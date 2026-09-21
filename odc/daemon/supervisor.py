"""Erlang OTP-style supervisor for the daemon's worker.

If the worker task crashes (LLM call hangs, exception, OOM, anything),
the supervisor restarts it. If too many restarts happen in a short
window, the supervisor itself gives up and propagates the failure
to the parent (the daemon main loop, which then exits cleanly).

The reference is Erlang's `one_for_one` strategy with MaxR/MaxT
intensity limits. We implement the same semantics in ~80 lines.

Also includes a `Watchdog` that kills a worker if it doesn't make
progress within a timeout. This catches the "LLM call hangs forever"
case that the regular exception path doesn't.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class Strategy(str, Enum):
    ONE_FOR_ONE = "one_for_one"  # restart only the failed child
    ONE_FOR_ALL = "one_for_all"  # restart all children (we have 1, so same)


@dataclass
class SupervisorConfig:
    strategy: Strategy = Strategy.ONE_FOR_ONE
    max_restarts: int = 5
    period: float = 60.0
    # If the worker doesn't tick the watchdog for this many seconds, kill it.
    watchdog_timeout: float = 300.0
    backoff_base: float = 1.0
    backoff_cap: float = 30.0


class SupervisorError(Exception):
    """Raised when the supervisor gives up (max restarts exceeded)."""


class WatchdogKilled(Exception):
    """Raised when the watchdog timer kills a stuck worker."""


# Set by the daemon right before running the supervised worker. The
# loop reads it to call heartbeat() at each turn. If no supervisor
# is set (e.g. running the loop directly), heartbeat is a no-op.
_current_supervisor: "Supervisor | None" = None


class Supervisor:
    """One_for_one supervisor for a single async worker."""

    def __init__(self, config: SupervisorConfig | None = None):
        self.config = config or SupervisorConfig()
        self.restart_times: deque[float] = deque()
        self.worker_task: asyncio.Task | None = None
        self.last_heartbeat: float = time.time()
        self._watchdog_task: asyncio.Task | None = None
        self._stopped = False
        self.last_error: BaseException | None = None
        self.restart_count: int = 0
        self._watchdog_killed: bool = False  # set by watchdog when it fires

    # ----------------------------------------------------------- public

    async def run(self, worker_factory: Callable[[], Awaitable[Any]]) -> Any:
        """Run the worker under supervision. Returns the worker's result
        (or raises SupervisorError if too many restarts)."""
        try:
            while not self._stopped:
                self._prune_restarts()
                if len(self.restart_times) >= self.config.max_restarts:
                    raise SupervisorError(
                        f"max_restarts={self.config.max_restarts} reached in "
                        f"{self.config.period}s; giving up. last error: "
                        f"{type(self.last_error).__name__}: {self.last_error}"
                    )
                # Start the worker
                self.last_heartbeat = time.time()
                self.worker_task = asyncio.create_task(worker_factory(), name="supervised-worker")
                # Start the watchdog in parallel
                self._watchdog_task = asyncio.create_task(self._watchdog_loop(), name="watchdog")
                try:
                    result = await self.worker_task
                    # Normal completion
                    return result
                except asyncio.CancelledError:
                    # If the watchdog killed the worker, this is a normal
                    # restart, not a fatal cancellation. Surface as
                    # WatchdogKilled and let the loop iterate.
                    if self._watchdog_killed:
                        e = self.last_error or WatchdogKilled("worker cancelled")
                        self._watchdog_killed = False
                        self.last_error = e
                        self._record_restart()
                        if len(self.restart_times) >= self.config.max_restarts:
                            raise SupervisorError(
                                f"max_restarts={self.config.max_restarts} reached"
                            ) from e
                        await self._sleep_backoff()
                        continue
                    # Not a watchdog kill: someone (us) cancelled the worker.
                    # Re-raise so the caller can decide.
                    raise
                except WatchdogKilled as e:
                    self.last_error = e
                    self._record_restart()
                    await self._sleep_backoff()
                    continue
                except BaseException as e:  # noqa: BLE001
                    # Non-retryable: KeyboardInterrupt / SystemExit must
                    # propagate immediately, without recording a restart.
                    if isinstance(e, (KeyboardInterrupt, SystemExit)):
                        self.last_error = e
                        raise
                    # If the watchdog killed the worker, surface a structured
                    # error instead of the raw CancelledError.
                    if self._watchdog_killed:
                        e = self.last_error or e
                        self._watchdog_killed = False
                    self.last_error = e
                    self._record_restart()
                    if len(self.restart_times) >= self.config.max_restarts:
                        # last attempt, give up
                        raise SupervisorError(
                            f"max_restarts={self.config.max_restarts} reached"
                        ) from e
                    await self._sleep_backoff()
                    continue
        finally:
            # Always stop the watchdog so the event loop can shut down.
            self._stopped = True
            self._stop_watchdog()
            # Cancel any in-flight worker too, to be safe
            if self.worker_task and not self.worker_task.done():
                self.worker_task.cancel()
        return None

    def heartbeat(self) -> None:
        """Call from inside the worker to prove it's alive."""
        self.last_heartbeat = time.time()

    def stop(self) -> None:
        """Stop the supervisor and its current worker."""
        self._stopped = True
        if self.worker_task and not self.worker_task.done():
            self.worker_task.cancel()
        self._stop_watchdog()

    # ----------------------------------------------------------- internal

    def _record_restart(self) -> None:
        now = time.time()
        self.restart_times.append(now)
        self.restart_count += 1
        self._prune_restarts()

    def _prune_restarts(self) -> None:
        cutoff = time.time() - self.config.period
        while self.restart_times and self.restart_times[0] < cutoff:
            self.restart_times.popleft()

    def _stop_watchdog(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None

    async def _sleep_backoff(self) -> None:
        delay = min(
            self.config.backoff_base * (2 ** max(0, len(self.restart_times) - 1)),
            self.config.backoff_cap,
        )
        await asyncio.sleep(delay)

    async def _watchdog_loop(self) -> None:
        """Periodically check heartbeat; kill worker if stuck.

        Does NOT raise. Sets `self._watchdog_killed` and cancels the
        worker. The outer run() loop sees the cancelled worker and
        treats it as a watchdog-kill restart.
        """
        try:
            while not self._stopped:
                await asyncio.sleep(min(10.0, self.config.watchdog_timeout / 3))
                if self._stopped:
                    return
                if self.worker_task is None or self.worker_task.done():
                    return
                elapsed = time.time() - self.last_heartbeat
                if elapsed >= self.config.watchdog_timeout:
                    # Mark first, then cancel. The outer loop will see
                    # the cancelled worker and `_watchdog_killed = True`.
                    self._watchdog_killed = True
                    self.last_error = WatchdogKilled(
                        f"worker stuck for {elapsed:.0f}s (no heartbeat)"
                    )
                    self.worker_task.cancel()
                    # Wait for the worker to actually die so the
                    # outer run() can re-raise cleanly.
                    try:
                        await self.worker_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    return
        except asyncio.CancelledError:
            return
