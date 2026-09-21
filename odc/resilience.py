"""Resilience layer: retry + circuit breaker + timeout + fallback for tools.

Every tool call goes through this. The goal: when a downstream fails
(web search down, shell unavailable, etc), the agent does NOT thrash
or burn tokens. It backs off, opens the circuit, and returns a
structured failure that the loop can act on.

This is the pyresilience pattern in ~200 lines, zero external deps.

State per tool is held in a global registry keyed by tool name. The
registry is process-local; for multi-process resilience, write the
state to a small sqlite table (TODO: not in v4 single-machine).
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class CircuitState(str, Enum):
    CLOSED = "closed"      # calls pass through
    OPEN = "open"          # calls fail-fast
    HALF_OPEN = "half_open"  # one trial call allowed


@dataclass
class ResiliencePolicy:
    """Configuration for a tool's resilience behavior."""

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 10.0
    jitter: float = 0.3         # ±30% randomization
    circuit_threshold: int = 5  # failures in window before opening
    circuit_window: float = 30.0  # window in seconds
    circuit_reset: float = 60.0  # seconds before half-open trial
    timeout: float = 30.0       # per-call timeout
    fallback: Any = None        # static fallback value when circuit open


class CircuitBreaker:
    """Per-tool circuit breaker. Thread-safe under asyncio (single loop)."""

    def __init__(self, name: str, policy: ResiliencePolicy):
        self.name = name
        self.policy = policy
        self.state: CircuitState = CircuitState.CLOSED
        self.failures: list[float] = []  # timestamps within the window
        self.opened_at: float = 0.0
        self.half_open_trial_in_flight: bool = False

    def allow(self) -> tuple[bool, str | None]:
        """Decide whether a call may proceed. Returns (allowed, reason)."""
        now = time.time()
        if self.state == CircuitState.CLOSED:
            return True, None
        if self.state == CircuitState.OPEN:
            if now - self.opened_at >= self.policy.circuit_reset:
                self.state = CircuitState.HALF_OPEN
                self.half_open_trial_in_flight = False
                return True, None
            return False, f"circuit_open:{self.name}"
        # HALF_OPEN: allow exactly one trial at a time
        if self.half_open_trial_in_flight:
            return False, f"circuit_half_open_busy:{self.name}"
        return True, None

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failures = []
        elif self.state == CircuitState.CLOSED:
            self._prune_failures()
            # a single success doesn't reset failures; let the window expire

    def record_failure(self) -> None:
        now = time.time()
        self.failures.append(now)
        self._prune_failures()
        if self.state == CircuitState.HALF_OPEN:
            # trial failed → reopen
            self.state = CircuitState.OPEN
            self.opened_at = now
            self.half_open_trial_in_flight = False
        elif len(self.failures) >= self.policy.circuit_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = now

    def _prune_failures(self) -> None:
        cutoff = time.time() - self.policy.circuit_window
        self.failures = [f for f in self.failures if f >= cutoff]

    def mark_trial_started(self) -> None:
        self.half_open_trial_in_flight = True

    def mark_trial_finished(self) -> None:
        self.half_open_trial_in_flight = False


# Global registry, keyed by tool name
_BREAKERS: dict[str, CircuitBreaker] = {}
_POLICIES: dict[str, ResiliencePolicy] = {}


def get_breaker(tool_name: str) -> CircuitBreaker:
    if tool_name not in _BREAKERS:
        _BREAKERS[tool_name] = CircuitBreaker(tool_name, _POLICIES.get(tool_name, ResiliencePolicy()))
    return _BREAKERS[tool_name]


def set_policy(tool_name: str, policy: ResiliencePolicy) -> None:
    _POLICIES[tool_name] = policy
    if tool_name in _BREAKERS:
        _BREAKERS[tool_name].policy = policy


def get_policy(tool_name: str) -> ResiliencePolicy:
    return _POLICIES.get(tool_name, ResiliencePolicy())


def reset_all() -> None:
    """For tests: clear all breakers and policies."""
    _BREAKERS.clear()
    _POLICIES.clear()


def circuit_snapshot() -> dict[str, dict[str, Any]]:
    """For observability: snapshot of all breaker states."""
    return {
        name: {
            "state": b.state.value,
            "failures": len(b.failures),
            "opened_at": b.opened_at,
        }
        for name, b in _BREAKERS.items()
    }


# ---------------------------------------------------------------- execution


def _backoff_delay(attempt: int, policy: ResiliencePolicy) -> float:
    base = min(policy.base_delay * (2 ** attempt), policy.max_delay)
    if policy.jitter:
        base = base * (1.0 + random.uniform(-policy.jitter, policy.jitter))
    return max(0.0, base)


async def call_with_resilience(
    tool_name: str,
    fn: Callable[[], Awaitable[Any]],
    *,
    policy: ResiliencePolicy | None = None,
) -> tuple[Any, str | None]:
    """Run an async function with retry + circuit breaker + timeout.

    Returns (result, error_message). On circuit-open, returns
    (policy.fallback, error_message) without calling fn.
    """
    p = policy or get_policy(tool_name)
    # If a policy is explicitly passed, sync it onto the breaker so the
    # breaker's thresholds reflect the latest intent.
    breaker = get_breaker(tool_name)
    breaker.policy = p
    allowed, reason = breaker.allow()
    if not allowed:
        return p.fallback, reason
    if breaker.state == CircuitState.HALF_OPEN:
        breaker.mark_trial_started()
    last_exc: BaseException | None = None
    for attempt in range(p.max_retries + 1):
        try:
            result = await asyncio.wait_for(fn(), timeout=p.timeout)
        except asyncio.TimeoutError as e:
            last_exc = e
            breaker.record_failure()
            if attempt < p.max_retries:
                await asyncio.sleep(_backoff_delay(attempt, p))
                continue
            return p.fallback, f"timeout:{tool_name}"
        except Exception as e:  # noqa: BLE001
            last_exc = e
            breaker.record_failure()
            if attempt < p.max_retries:
                await asyncio.sleep(_backoff_delay(attempt, p))
                continue
            return p.fallback, f"{type(e).__name__}:{e}"
        # success
        breaker.record_success()
        if breaker.state == CircuitState.HALF_OPEN:
            breaker.mark_trial_finished()
        return result, None
    return p.fallback, f"exhausted:{last_exc}"
