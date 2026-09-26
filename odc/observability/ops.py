"""Operations: latency, cost, SLOs, retry policies.

Post-audit additions (2026-09-23):
  - p50/p95/p99 latency tracking per tool and per LLM call
  - Cost tracking per task (tokens × model rate)
  - Hard cost budget per task (raise TaskBudgetExceeded)
  - Max LLM calls per task (raise TaskBudgetExceeded)
  - Tool failure rate tracking
  - Per-task hand-back rate
  - Retry policy by error class and provider

Storage: SQLite at <data_dir>/ops/metrics.db. Cheap, append-only.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# SLO / budget definitions
# ──────────────────────────────────────────────────────────────────
@dataclass
class TaskBudget:
    """Hard limits for a single task.

    The loop checks budget before each tool call. If exceeded,
    raises TaskBudgetExceeded → agent halts gracefully.
    """
    max_llm_calls: int = 30
    max_tool_calls: int = 50
    max_total_tokens: int = 200_000
    max_cost_usd: float = 1.00
    max_duration_s: float = 300.0
    max_consecutive_errors: int = 5
    max_retries_per_tool: int = 3


DEFAULT_TASK_BUDGET = TaskBudget()


class TaskBudgetExceeded(Exception):
    """Raised when a task exceeds its configured budget."""

    def __init__(self, reason: str, metric: str, value: float, limit: float):
        self.reason = reason
        self.metric = metric
        self.value = value
        self.limit = limit
        super().__init__(f"budget exceeded: {metric}={value} > {limit} ({reason})")


# ──────────────────────────────────────────────────────────────────
# Latency percentiles
# ──────────────────────────────────────────────────────────────────
class LatencyTracker:
    """Tracks p50/p95/p99 latency per category."""

    def __init__(self, max_samples: int = 1000):
        self._samples: dict[str, list[float]] = defaultdict(list)
        self._max = max_samples
        self._lock = threading.RLock()

    def record(self, category: str, duration_ms: float) -> None:
        with self._lock:
            arr = self._samples[category]
            arr.append(duration_ms)
            if len(arr) > self._max:
                arr.pop(0)  # simple sliding window

    def percentile(self, category: str, p: float) -> float:
        """Return the p-th percentile (0..1). 0 if no samples."""
        with self._lock:
            arr = sorted(self._samples[category])
        if not arr:
            return 0.0
        k = int(p * (len(arr) - 1))
        return arr[k]

    def stats(self, category: str) -> dict[str, float]:
        with self._lock:
            arr = sorted(self._samples[category])
        if not arr:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
        return {
            "count": len(arr),
            "p50": self.percentile(category, 0.50),
            "p95": self.percentile(category, 0.95),
            "p99": self.percentile(category, 0.99),
            "max": arr[-1],
        }


# ──────────────────────────────────────────────────────────────────
# Cost tracker
# ──────────────────────────────────────────────────────────────────
# Approximate cost per 1K tokens (USD) — verified 2026-09
# Source: NVIDIA pricing for hosted models
COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "default": {"input": 0.001, "output": 0.002},
    "mistralai/mistral-nemotron": {"input": 0.0001, "output": 0.0002},
    "nvidia/llama-3.1-nemotron-70b-instruct": {"input": 0.001, "output": 0.002},
    "meta/llama-3.1-70b-instruct": {"input": 0.001, "output": 0.002},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "claude-sonnet-4-5": {"input": 0.003, "output": 0.015},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single LLM call."""
    rates = COST_PER_1K_TOKENS.get(model, COST_PER_1K_TOKENS["default"])
    cost_in = (input_tokens / 1000.0) * rates["input"]
    cost_out = (output_tokens / 1000.0) * rates["output"]
    return round(cost_in + cost_out, 6)


class CostTracker:
    """Track cost per task, alert on budget breach."""

    def __init__(self):
        self._lock = threading.RLock()
        self._per_task: dict[str, float] = defaultdict(float)
        self._per_task_tokens: dict[str, dict[str, int]] = defaultdict(
            lambda: {"input": 0, "output": 0}
        )

    def record(self, task_id: str, model: str,
               input_tokens: int, output_tokens: int) -> float:
        cost = estimate_cost(model, input_tokens, output_tokens)
        with self._lock:
            self._per_task[task_id] += cost
            self._per_task_tokens[task_id]["input"] += input_tokens
            self._per_task_tokens[task_id]["output"] += output_tokens
        return cost

    def total(self, task_id: str) -> float:
        with self._lock:
            return self._per_task[task_id]

    def tokens(self, task_id: str) -> dict[str, int]:
        with self._lock:
            return dict(self._per_task_tokens[task_id])


# ──────────────────────────────────────────────────────────────────
# Tool failure tracker
# ──────────────────────────────────────────────────────────────────
class ToolFailureTracker:
    """Per-tool failure rate."""

    def __init__(self):
        self._lock = threading.RLock()
        self._calls: dict[str, int] = defaultdict(int)
        self._failures: dict[str, int] = defaultdict(int)
        self._error_classes: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    def record(self, tool_name: str, success: bool,
               error_class: str = "") -> None:
        with self._lock:
            self._calls[tool_name] += 1
            if not success:
                self._failures[tool_name] += 1
                if error_class:
                    self._error_classes[tool_name][error_class] += 1

    def failure_rate(self, tool_name: str) -> float:
        with self._lock:
            n = self._calls[tool_name]
            return self._failures[tool_name] / n if n else 0.0

    def top_failing(self, n: int = 5) -> list[tuple[str, float, int]]:
        with self._lock:
            stats = [
                (name, self._failures[name] / self._calls[name], self._calls[name])
                for name in self._calls if self._calls[name] > 0
            ]
        stats.sort(key=lambda x: (-x[1], -x[2]))
        return stats[:n]


# ──────────────────────────────────────────────────────────────────
# Retry policy
# ──────────────────────────────────────────────────────────────────
@dataclass
class RetryPolicy:
    """Per-error-class retry policy."""
    max_attempts: int = 3
    backoff_base_s: float = 1.0
    backoff_factor: float = 2.0
    backoff_jitter: bool = True

    def backoff_delay(self, attempt: int) -> float:
        delay = self.backoff_base_s * (self.backoff_factor ** (attempt - 1))
        if self.backoff_jitter:
            import random
            delay *= 0.5 + random.random()
        return delay


# Default policies per error class
DEFAULT_RETRY_POLICIES: dict[str, RetryPolicy] = {
    "timeout":         RetryPolicy(max_attempts=3, backoff_base_s=2.0),
    "rate_limit":      RetryPolicy(max_attempts=5, backoff_base_s=5.0, backoff_factor=2.5),
    "service_unavailable": RetryPolicy(max_attempts=3, backoff_base_s=2.0),
    "internal_error":  RetryPolicy(max_attempts=2, backoff_base_s=3.0),
    "auth_error":      RetryPolicy(max_attempts=0),  # don't retry
    "validation":      RetryPolicy(max_attempts=0),  # don't retry
    "schema":          RetryPolicy(max_attempts=0),
    "permission_denied": RetryPolicy(max_attempts=0),
}


def policy_for(error_class: str) -> RetryPolicy:
    return DEFAULT_RETRY_POLICIES.get(error_class, RetryPolicy())


# ──────────────────────────────────────────────────────────────────
# Persistent metrics storage
# ──────────────────────────────────────────────────────────────────
class OpsStore:
    """Append-only SQLite storage for ops metrics."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT,
        category TEXT,
        metric TEXT,
        value REAL,
        ts REAL
    );
    CREATE INDEX IF NOT EXISTS idx_metrics_task ON metrics(task_id);
    CREATE INDEX IF NOT EXISTS idx_metrics_cat ON metrics(category);
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def record(self, task_id: str, category: str,
               metric: str, value: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO metrics(task_id, category, metric, value, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, category, metric, value, time.time()),
            )
            self._conn.commit()

    def query(self, category: str = "",
              metric: str = "", task_id: str = "",
              since: float = 0, limit: int = 1000) -> list[dict]:
        with self._lock:
            sql = "SELECT task_id, category, metric, value, ts FROM metrics WHERE 1=1"
            params: list[Any] = []
            if category:
                sql += " AND category=?"
                params.append(category)
            if metric:
                sql += " AND metric=?"
                params.append(metric)
            if task_id:
                sql += " AND task_id=?"
                params.append(task_id)
            if since:
                sql += " AND ts>=?"
                params.append(since)
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [
                {"task_id": r[0], "category": r[1], "metric": r[2],
                 "value": r[3], "ts": r[4]}
                for r in rows
            ]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────
# Task runner — enforces budget
# ──────────────────────────────────────────────────────────────────
@dataclass
class TaskMetrics:
    """Live metrics for one running task."""
    task_id: str
    started_at: float
    llm_calls: int = 0
    tool_calls: int = 0
    consecutive_errors: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    tool_failures: int = 0
    hand_backs: int = 0
    budget: TaskBudget = field(default_factory=TaskBudget)
    finished: bool = False

    def check_budget(self) -> None:
        """Raise TaskBudgetExceeded if any limit is hit."""
        elapsed = time.time() - self.started_at
        if elapsed > self.budget.max_duration_s:
            raise TaskBudgetExceeded(
                "task took too long", "duration_s",
                elapsed, self.budget.max_duration_s,
            )
        if self.llm_calls > self.budget.max_llm_calls:
            raise TaskBudgetExceeded(
                "too many LLM calls", "llm_calls",
                float(self.llm_calls), float(self.budget.max_llm_calls),
            )
        if self.tool_calls > self.budget.max_tool_calls:
            raise TaskBudgetExceeded(
                "too many tool calls", "tool_calls",
                float(self.tool_calls), float(self.budget.max_tool_calls),
            )
        total_tokens = self.total_input_tokens + self.total_output_tokens
        if total_tokens > self.budget.max_total_tokens:
            raise TaskBudgetExceeded(
                "token budget exceeded", "tokens",
                float(total_tokens), float(self.budget.max_total_tokens),
            )
        if self.total_cost_usd > self.budget.max_cost_usd:
            raise TaskBudgetExceeded(
                "cost budget exceeded", "cost_usd",
                self.total_cost_usd, self.budget.max_cost_usd,
            )
        if self.consecutive_errors > self.budget.max_consecutive_errors:
            raise TaskBudgetExceeded(
                "too many consecutive errors", "consecutive_errors",
                float(self.consecutive_errors),
                float(self.budget.max_consecutive_errors),
            )

    def record_llm(self, input_tokens: int, output_tokens: int,
                   model: str = "default") -> None:
        self.llm_calls += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += estimate_cost(model, input_tokens, output_tokens)
        self.consecutive_errors = 0
        self.check_budget()

    def record_tool(self, success: bool) -> None:
        self.tool_calls += 1
        if success:
            self.consecutive_errors = 0
        else:
            self.tool_failures += 1
            self.consecutive_errors += 1
        self.check_budget()

    def record_hand_back(self) -> None:
        self.hand_backs += 1

    def summary(self) -> dict[str, Any]:
        elapsed = time.time() - self.started_at
        return {
            "task_id": self.task_id,
            "duration_s": round(elapsed, 2),
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "hand_backs": self.hand_backs,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cost_usd": round(self.total_cost_usd, 6),
            "tool_failure_rate": (
                self.tool_failures / self.tool_calls
                if self.tool_calls else 0.0
            ),
            "finished": self.finished,
        }
