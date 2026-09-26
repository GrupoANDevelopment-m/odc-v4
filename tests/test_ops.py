"""Tests for operations, SLOs, and budget enforcement."""
import time

import pytest

from odc.observability.ops import (
    LatencyTracker, CostTracker, ToolFailureTracker,
    TaskBudget, TaskMetrics, TaskBudgetExceeded,
    estimate_cost, policy_for, RetryPolicy, OpsStore, DEFAULT_RETRY_POLICIES,
)


# ──────────────────────────────────────────────────────────────────
# Latency
# ──────────────────────────────────────────────────────────────────
def test_latency_tracker_percentiles():
    t = LatencyTracker()
    for ms in range(1, 101):
        t.record("api", float(ms))
    s = t.stats("api")
    assert s["count"] == 100
    assert 49 <= s["p50"] <= 51
    assert 94 <= s["p95"] <= 96
    assert s["max"] == 100


def test_latency_tracker_sliding_window():
    t = LatencyTracker(max_samples=10)
    for i in range(20):
        t.record("api", float(i))
    s = t.stats("api")
    assert s["count"] == 10  # capped
    assert s["max"] == 19


def test_latency_tracker_empty():
    t = LatencyTracker()
    assert t.percentile("nope", 0.95) == 0.0


# ──────────────────────────────────────────────────────────────────
# Cost
# ──────────────────────────────────────────────────────────────────
def test_estimate_cost_default():
    # Default: 0.001/1k input, 0.002/1k output
    cost = estimate_cost("unknown-model", 1000, 500)
    assert cost == 0.001 + 0.001  # 0.002


def test_estimate_cost_specific_model():
    cost = estimate_cost("mistralai/mistral-nemotron", 1000, 1000)
    # 0.0001 + 0.0002 = 0.0003
    assert cost == pytest.approx(0.0003, abs=1e-6)


def test_cost_tracker_per_task():
    t = CostTracker()
    t.record("task-1", "mistralai/mistral-nemotron", 1000, 500)
    t.record("task-1", "mistralai/mistral-nemotron", 2000, 1000)
    assert t.total("task-1") > 0
    assert t.tokens("task-1")["input"] == 3000
    assert t.tokens("task-1")["output"] == 1500


# ──────────────────────────────────────────────────────────────────
# Tool failure tracker
# ──────────────────────────────────────────────────────────────────
def test_tool_failure_rate():
    t = ToolFailureTracker()
    t.record("osint.bitcoin", True)
    t.record("osint.bitcoin", True)
    t.record("osint.bitcoin", False, "timeout")
    assert t.failure_rate("osint.bitcoin") == pytest.approx(1/3)
    top = t.top_failing(n=5)
    assert top[0][0] == "osint.bitcoin"


def test_tool_failure_zero_calls():
    t = ToolFailureTracker()
    assert t.failure_rate("never-called") == 0.0


# ──────────────────────────────────────────────────────────────────
# Task budget — hard limits
# ──────────────────────────────────────────────────────────────────
def test_task_budget_enforces_llm_calls():
    m = TaskMetrics(task_id="t", started_at=time.time(),
                    budget=TaskBudget(max_llm_calls=3))
    for i in range(3):
        m.record_llm(100, 100)
    with pytest.raises(TaskBudgetExceeded) as exc_info:
        m.record_llm(100, 100)  # 4th call
    assert exc_info.value.metric == "llm_calls"


def test_task_budget_enforces_cost():
    m = TaskMetrics(task_id="t", started_at=time.time(),
                    budget=TaskBudget(max_cost_usd=0.0001))
    # Each call to gpt-4o with 1000+1000 tokens costs 0.020 — way over
    with pytest.raises(TaskBudgetExceeded) as exc_info:
        m.record_llm(1000, 1000, model="gpt-4o")
    assert exc_info.value.metric == "cost_usd"


def test_task_budget_enforces_duration():
    m = TaskMetrics(task_id="t", started_at=time.time() - 1000,
                    budget=TaskBudget(max_duration_s=10))
    with pytest.raises(TaskBudgetExceeded) as exc_info:
        m.check_budget()
    assert exc_info.value.metric == "duration_s"


def test_task_budget_consecutive_errors():
    m = TaskMetrics(task_id="t", started_at=time.time(),
                    budget=TaskBudget(max_consecutive_errors=3))
    for i in range(3):
        m.record_tool(success=False)
    with pytest.raises(TaskBudgetExceeded) as exc_info:
        m.record_tool(success=False)  # 4th consecutive
    assert exc_info.value.metric == "consecutive_errors"


def test_task_budget_resets_consecutive_on_success():
    m = TaskMetrics(task_id="t", started_at=time.time(),
                    budget=TaskBudget(max_consecutive_errors=3))
    m.record_tool(False)
    m.record_tool(False)
    m.record_tool(True)  # resets
    m.record_tool(False)
    m.record_tool(False)
    # Should not raise — 2 consecutive, not 3+
    m.check_budget()


def test_task_metrics_summary():
    m = TaskMetrics(task_id="t", started_at=time.time())
    m.record_llm(100, 50, model="mistralai/mistral-nemotron")
    m.record_tool(True)
    m.record_tool(False, )  # doesn't trigger budget
    s = m.summary()
    assert s["llm_calls"] == 1
    assert s["tool_calls"] == 2
    assert s["tool_failures"] == 1
    assert s["input_tokens"] == 100
    assert s["cost_usd"] > 0


# ──────────────────────────────────────────────────────────────────
# Retry policies
# ──────────────────────────────────────────────────────────────────
def test_retry_policy_backoff():
    p = RetryPolicy(max_attempts=3, backoff_base_s=1.0,
                    backoff_factor=2.0, backoff_jitter=False)
    assert p.backoff_delay(1) == 1.0
    assert p.backoff_delay(2) == 2.0
    assert p.backoff_delay(3) == 4.0


def test_retry_policy_per_error_class():
    assert policy_for("timeout").max_attempts >= 1
    assert policy_for("rate_limit").max_attempts >= 3
    assert policy_for("auth_error").max_attempts == 0  # don't retry
    assert policy_for("schema").max_attempts == 0


# ──────────────────────────────────────────────────────────────────
# OpsStore
# ──────────────────────────────────────────────────────────────────
def test_ops_store_record_and_query(tmp_path):
    store = OpsStore(tmp_path / "ops.db")
    store.record("t1", "latency", "p50", 50.0)
    store.record("t1", "cost", "usd", 0.05)
    rows = store.query(task_id="t1")
    assert len(rows) == 2
    metrics = {r["metric"]: r["value"] for r in rows}
    assert metrics["p50"] == 50.0
    assert metrics["usd"] == 0.05


def test_ops_store_filter_by_category(tmp_path):
    store = OpsStore(tmp_path / "ops.db")
    store.record("t1", "latency", "p50", 50.0)
    store.record("t1", "cost", "usd", 0.05)
    rows = store.query(category="cost")
    assert len(rows) == 1
    assert rows[0]["metric"] == "usd"
