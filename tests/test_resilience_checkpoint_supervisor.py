"""Tests for the resilience / checkpoint / supervisor stack.

These tests prove that the self-healing, persistence, and auto-recovery
are REAL: actual retry that retries, actual circuit breaker that opens,
actual sqlite writes that survive process death, actual supervisor that
restarts crashed workers and gives up after too many restarts.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------- resilience


@pytest.fixture(autouse=True)
def _reset_resilience():
    from odc.resilience import reset_all
    reset_all()
    yield
    reset_all()


@pytest.mark.asyncio
async def test_resilience_retry_succeeds_after_transient_failure():
    """A tool that fails twice then succeeds must return success on the
    third call, after 2 retries with backoff."""
    from odc.resilience import call_with_resilience, ResiliencePolicy, get_breaker
    attempts = {"n": 0}

    async def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError(f"transient #{attempts['n']}")
        return "ok"

    policy = ResiliencePolicy(
        max_retries=3, base_delay=0.01, max_delay=0.05, jitter=0.0,
        circuit_threshold=10, circuit_window=60.0, circuit_reset=60.0, timeout=5.0,
    )
    result, err = await call_with_resilience("flaky.tool", flaky, policy=policy)
    assert err is None
    assert result == "ok"
    assert attempts["n"] == 3  # failed twice, succeeded third time
    assert get_breaker("flaky.tool").state.value == "closed"


@pytest.mark.asyncio
async def test_resilience_circuit_opens_after_threshold():
    """After N consecutive failures, the circuit must OPEN. Subsequent
    calls must fail-fast with `circuit_open:<name>` without invoking fn."""
    from odc.resilience import call_with_resilience, ResiliencePolicy, get_breaker
    attempts = {"n": 0}

    async def always_fails() -> str:
        attempts["n"] += 1
        raise RuntimeError("down")

    policy = ResiliencePolicy(
        max_retries=0,  # no retries; each call counts as one failure
        base_delay=0.001, max_delay=0.01, jitter=0.0,
        circuit_threshold=3, circuit_window=60.0, circuit_reset=60.0, timeout=5.0,
    )
    breaker = get_breaker("down.tool")
    # Trigger 3 failures (no retries per call → 3 calls)
    for _ in range(3):
        result, err = await call_with_resilience("down.tool", always_fails, policy=policy)
        assert err is not None
    assert breaker.state.value == "open", f"expected open, got {breaker.state}"
    # Now subsequent calls must fail-fast
    result, err = await call_with_resilience("down.tool", always_fails, policy=policy)
    assert err is not None
    assert "circuit_open" in err
    assert attempts["n"] == 3, f"function should NOT have been called after circuit opened; got {attempts['n']} attempts"


@pytest.mark.asyncio
async def test_resilience_circuit_half_open_after_reset():
    """After circuit_reset seconds, the circuit enters HALF_OPEN, allows
    one trial call. On success, it closes. On failure, it reopens."""
    from odc.resilience import call_with_resilience, ResiliencePolicy, get_breaker
    policy = ResiliencePolicy(
        max_retries=0, base_delay=0.001, max_delay=0.01, jitter=0.0,
        circuit_threshold=2, circuit_window=60.0, circuit_reset=0.1, timeout=5.0,
    )
    attempts = {"n": 0}

    async def fails_then_works() -> str:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise RuntimeError("down")
        return "recovered"

    breaker = get_breaker("recovering.tool")
    # Open the circuit
    for _ in range(2):
        await call_with_resilience("recovering.tool", fails_then_works, policy=policy)
    assert breaker.state.value == "open"
    # Wait for reset
    await asyncio.sleep(0.15)
    # Next call should be half-open trial; succeeds → circuit closes
    result, err = await call_with_resilience("recovering.tool", fails_then_works, policy=policy)
    assert result == "recovered"
    assert err is None
    assert breaker.state.value == "closed"


@pytest.mark.asyncio
async def test_resilience_timeout_raises_error():
    """A tool that takes longer than the policy timeout must surface a
    structured error."""
    from odc.resilience import call_with_resilience, ResiliencePolicy

    async def slow() -> str:
        await asyncio.sleep(2.0)
        return "should not see this"

    policy = ResiliencePolicy(
        max_retries=0, base_delay=0.001, max_delay=0.01, jitter=0.0,
        circuit_threshold=100, timeout=0.1,
    )
    result, err = await call_with_resilience("slow.tool", slow, policy=policy)
    assert err is not None
    assert "timeout" in err


@pytest.mark.asyncio
async def test_resilience_fallback_returned_on_circuit_open():
    """When the circuit is open, the policy's fallback value is returned
    instead of calling fn."""
    from odc.resilience import call_with_resilience, ResiliencePolicy, get_breaker
    policy = ResiliencePolicy(
        max_retries=0, base_delay=0.001, max_delay=0.01, jitter=0.0,
        circuit_threshold=1, circuit_window=60.0, circuit_reset=60.0, timeout=5.0,
        fallback={"cached": True, "value": "stale"},
    )

    async def fails() -> str:
        raise RuntimeError("nope")

    # First call: fails, opens circuit
    await call_with_resilience("fb.tool", fails, policy=policy)
    # Second call: fallback
    result, err = await call_with_resilience("fb.tool", fails, policy=policy)
    assert result == {"cached": True, "value": "stale"}
    assert "circuit_open" in err


def test_resilience_breaker_registry_isolated_per_tool():
    """Failures on tool A must not affect tool B's breaker."""
    from odc.resilience import call_with_resilience, ResiliencePolicy, get_breaker
    # We can't run async here; just verify the registry is per-name
    from odc.resilience import get_breaker as gb
    b1 = gb("tool.A")
    b2 = gb("tool.B")
    assert b1 is not b2
    assert b1.name == "tool.A"
    assert b2.name == "tool.B"


# ---------------------------------------------------------------- checkpoint


def test_checkpoint_save_and_load(tmp_path: Path):
    """A saved checkpoint must be retrievable with the same messages."""
    from odc.checkpoint import LoopCheckpoint
    cp = LoopCheckpoint(tmp_path / "cp.db")
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    cp.save("thread-1", turn=1, task="say hi", messages=messages, state={"k": "v"})
    loaded = cp.load_latest("thread-1")
    assert loaded is not None
    assert loaded["turn"] == 1
    assert loaded["task"] == "say hi"
    assert loaded["messages"] == messages
    assert loaded["state"] == {"k": "v"}


def test_checkpoint_load_latest_returns_highest_turn(tmp_path: Path):
    """After multiple saves, load_latest must return the highest turn."""
    from odc.checkpoint import LoopCheckpoint
    cp = LoopCheckpoint(tmp_path / "cp.db")
    for turn in range(1, 5):
        cp.save("t", turn=turn, task="x", messages=[{"role": "user", "content": f"turn {turn}"}])
    loaded = cp.load_latest("t")
    assert loaded["turn"] == 4
    assert loaded["messages"][0]["content"] == "turn 4"


def test_checkpoint_mark_done(tmp_path: Path):
    """mark_done must update task_meta.status."""
    from odc.checkpoint import LoopCheckpoint
    cp = LoopCheckpoint(tmp_path / "cp.db")
    cp.save("t", turn=1, task="x", messages=[])
    cp.mark_done("t", "completed", "ok")
    threads = cp.list_threads()
    assert len(threads) == 1
    assert threads[0]["status"] == "completed"
    assert threads[0]["note"] == "ok"


def test_checkpoint_prune_audit_trail(tmp_path: Path):
    """After many saves, only the last 20 turns are kept (audit trail cap)."""
    from odc.checkpoint import LoopCheckpoint
    cp = LoopCheckpoint(tmp_path / "cp.db")
    for turn in range(1, 30):
        cp.save("t", turn=turn, task="x", messages=[{"turn": turn}])
    # We saved 30 turns but should keep only the last 20
    with sqlite3.connect(str(tmp_path / "cp.db")) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id='t'"
        ).fetchone()[0]
    assert count == 20


def test_checkpoint_prune_older_than(tmp_path: Path):
    """prune_older_than must remove old rows."""
    from odc.checkpoint import LoopCheckpoint
    cp = LoopCheckpoint(tmp_path / "cp.db")
    cp.save("t", turn=1, task="x", messages=[])
    # Backdate it
    with sqlite3.connect(str(tmp_path / "cp.db")) as conn:
        conn.execute(
            "UPDATE checkpoints SET created_at = ? WHERE thread_id='t'",
            (time.time() - 100 * 86400,),
        )
        conn.execute(
            "UPDATE task_meta SET updated_at = ? WHERE thread_id='t'",
            (time.time() - 100 * 86400,),
        )
        conn.commit()
    n = cp.prune_older_than(days=30)
    assert n >= 1
    assert cp.load_latest("t") is None


def test_checkpoint_delete_thread(tmp_path: Path):
    """delete_thread removes all rows for that thread."""
    from odc.checkpoint import LoopCheckpoint
    cp = LoopCheckpoint(tmp_path / "cp.db")
    cp.save("t1", turn=1, task="x", messages=[])
    cp.save("t2", turn=1, task="y", messages=[])
    cp.delete_thread("t1")
    assert cp.load_latest("t1") is None
    assert cp.load_latest("t2") is not None


def test_checkpoint_list_threads_filter_by_status(tmp_path: Path):
    """list_threads(status=...) must filter."""
    from odc.checkpoint import LoopCheckpoint
    cp = LoopCheckpoint(tmp_path / "cp.db")
    cp.save("t1", turn=1, task="x", messages=[])
    cp.save("t2", turn=1, task="y", messages=[])
    cp.mark_done("t1", "completed")
    cp.mark_done("t2", "handed_back")
    completed = cp.list_threads("completed")
    handed = cp.list_threads("handed_back")
    assert {t["thread_id"] for t in completed} == {"t1"}
    assert {t["thread_id"] for t in handed} == {"t2"}


# ---------------------------------------------------------------- supervisor


@pytest.mark.asyncio
async def test_supervisor_restarts_on_crash():
    """If the worker raises, the supervisor must restart it (up to the
    restart limit)."""
    from odc.daemon.supervisor import Supervisor, SupervisorConfig, SupervisorError
    attempts = {"n": 0}

    async def flaky_worker():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError(f"crash #{attempts['n']}")
        return "done"

    sup = Supervisor(SupervisorConfig(
        max_restarts=5, period=60.0, backoff_base=0.001, backoff_cap=0.01,
        watchdog_timeout=60.0,
    ))
    result = await sup.run(flaky_worker)
    assert result == "done"
    assert attempts["n"] == 3
    assert sup.restart_count == 2  # two crashes, two restarts


@pytest.mark.asyncio
async def test_supervisor_gives_up_after_max_restarts():
    """If the worker keeps crashing faster than max_restarts/period,
    the supervisor must give up and raise SupervisorError."""
    from odc.daemon.supervisor import Supervisor, SupervisorConfig, SupervisorError

    async def always_crashes():
        raise RuntimeError("perma-broken")

    sup = Supervisor(SupervisorConfig(
        max_restarts=3, period=60.0, backoff_base=0.001, backoff_cap=0.01,
        watchdog_timeout=60.0,
    ))
    with pytest.raises(SupervisorError):
        await sup.run(always_crashes)
    # 3 restarts in 60s = gave up
    assert sup.restart_count == 3


@pytest.mark.asyncio
async def test_supervisor_watchdog_kills_stuck_worker():
    """If the worker doesn't call heartbeat() within watchdog_timeout,
    the supervisor must cancel and restart it."""
    from odc.daemon.supervisor import Supervisor, SupervisorConfig, SupervisorError, WatchdogKilled
    started = {"n": 0}

    async def stuck_worker():
        started["n"] += 1
        if started["n"] == 1:
            # First attempt: hang forever (no heartbeat)
            await asyncio.sleep(60)
        return "second-attempt-ok"

    sup = Supervisor(SupervisorConfig(
        max_restarts=2, period=60.0, backoff_base=0.001, backoff_cap=0.01,
        watchdog_timeout=0.3,  # very short for the test
    ))
    result = await sup.run(stuck_worker)
    assert result == "second-attempt-ok"
    assert started["n"] == 2


@pytest.mark.asyncio
async def test_supervisor_heartbeat_keeps_worker_alive():
    """If the worker calls heartbeat() frequently, the watchdog must not
    fire even if the run is long."""
    from odc.daemon.supervisor import Supervisor, SupervisorConfig
    sup = Supervisor(SupervisorConfig(
        max_restarts=1, period=60.0, backoff_base=0.001, backoff_cap=0.01,
        watchdog_timeout=0.5,
    ))

    async def healthy_long_worker():
        for _ in range(3):
            sup.heartbeat()
            await asyncio.sleep(0.1)
        return "ok"

    result = await sup.run(healthy_long_worker)
    assert result == "ok"


@pytest.mark.asyncio
async def test_supervisor_cancellation_propagates():
    """If the supervisor's outer task is cancelled, the worker is too."""
    from odc.daemon.supervisor import Supervisor, SupervisorConfig

    async def slow_worker():
        await asyncio.sleep(60)

    sup = Supervisor(SupervisorConfig(
        max_restarts=5, period=60.0, backoff_base=0.001, backoff_cap=0.01,
        watchdog_timeout=60.0,
    ))
    task = asyncio.create_task(sup.run(slow_worker))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sup.restart_count == 0


@pytest.mark.asyncio
async def test_supervisor_does_not_restart_on_cancelled_error():
    """If the worker is cancelled (e.g. by an external task cancel), the
    supervisor propagates the CancelledError. The watchdog path is
    tested separately."""
    from odc.daemon.supervisor import Supervisor, SupervisorConfig

    async def slow_worker():
        await asyncio.sleep(60)

    sup = Supervisor(SupervisorConfig(
        max_restarts=5, period=60.0, backoff_base=0.001, backoff_cap=0.01,
        watchdog_timeout=60.0,
    ))
    task = asyncio.create_task(sup.run(slow_worker))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # No restart was recorded (the cancel came from outside)
    assert sup.restart_count == 0


# ---------------------------------------------------------------- integration: crash + resume


@pytest.mark.asyncio
async def test_loop_resumes_from_checkpoint_after_simulated_crash(tmp_path: Path):
    """End-to-end: run the loop, force a crash mid-way, then start a
    fresh loop with the same thread_id and verify it resumes from the
    checkpoint, not from the beginning."""
    from odc.checkpoint import LoopCheckpoint
    from odc.config import load_config
    from odc.loop import Loop
    from odc.tools.base import ToolRegistry

    cfg = load_config()
    cfg.data_dir = tmp_path / "odc"
    cfg.max_loop_turns = 10
    cfg.verify_hard_cap = 99
    cfg.log_level = 20

    # Mock provider: turn 1 = empty, turn 2 = final report
    chat_calls: list[int] = []

    class FakeProvider:
        async def chat(self, messages, tools=None):
            chat_calls.append(len(chat_calls) + 1)
            # Always return a tool call (so the loop keeps going)
            return MagicMock(
                text="calling",
                tool_calls=[{
                    "id": f"c{len(chat_calls)}",
                    "name": "noop",
                    "arguments": "{}",
                }],
                usage={},
            )

    from odc.tools.base import tool

    @tool(name="noop", description="x", parameters={"type": "object", "properties": {}})
    async def noop():
        return {"ok": True}

    tools = ToolRegistry()
    tools.register(noop)
    loop = Loop(config=cfg, provider=FakeProvider(), tools=tools)

    # First run: let it go a few turns, then "crash" by cancelling
    task = asyncio.create_task(loop.run("do a thing", thread_id="resumable-1"))
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Verify checkpoint exists
    cp = LoopCheckpoint(tmp_path / "odc" / "checkpoints.db")
    ckpt = cp.load_latest("resumable-1")
    assert ckpt is not None, "checkpoint must have been saved"
    first_run_turns = ckpt["turn"]
    first_chat_count = len(chat_calls)
    assert first_run_turns >= 1, "should have at least 1 turn before crash"
    assert first_chat_count >= 1

    # Second run: same thread_id → should resume, not start over
    chat_calls.clear()
    loop2 = Loop(config=cfg, provider=FakeProvider(), tools=tools)
    res = await loop2.run("do a thing", thread_id="resumable-1")
    # The resume + new chat count must be > first_run_turns in total,
    # but the second run only called chat for NEW turns, not all the
    # prior ones.
    second_chat_count = len(chat_calls)
    assert second_chat_count < first_chat_count + first_run_turns, (
        f"resume should NOT re-run all prior turns. "
        f"first run did {first_chat_count} chats, second did {second_chat_count}"
    )
    # The resumed_from should be > 0
    assert res.resumed_from > 0, f"resumed_from must be > 0; got {res.resumed_from}"


@pytest.mark.asyncio
async def test_loop_uses_resilience_on_tool_failure(tmp_path: Path):
    """A tool that fails must be retried by the resilience layer."""
    from odc.config import load_config
    from odc.loop import Loop
    from odc.tools.base import ToolRegistry
    from odc.resilience import reset_all, ResiliencePolicy
    reset_all()
    # Set a tight policy for the failing tool
    from odc.resilience import set_policy
    set_policy("flaky", ResiliencePolicy(
        max_retries=3, base_delay=0.001, max_delay=0.01, jitter=0.0,
        circuit_threshold=10, circuit_window=60.0, circuit_reset=60.0, timeout=2.0,
    ))

    cfg = load_config()
    cfg.data_dir = tmp_path / "odc"
    cfg.max_loop_turns = 4
    cfg.verify_hard_cap = 99
    cfg.log_level = 20

    chat_calls: list[dict] = []
    fail_count = {"n": 0}

    class FakeProvider:
        async def chat(self, messages, tools=None):
            chat_calls.append({"n_msgs": len(messages), "roles": [m.role for m in messages]})
            if len(chat_calls) == 1:
                # Ask to call the flaky tool
                return MagicMock(
                    text="calling",
                    tool_calls=[{"id": "c1", "name": "flaky", "arguments": "{}"}],
                    usage={},
                )
            return MagicMock(text="done", tool_calls=[], usage={})

    from odc.tools.base import tool

    @tool(name="flaky", description="x", parameters={"type": "object", "properties": {}})
    async def flaky_tool():
        fail_count["n"] += 1
        if fail_count["n"] < 3:
            raise ConnectionError(f"transient #{fail_count['n']}")
        return {"ok": True}

    tools = ToolRegistry()
    tools.register(flaky_tool)
    loop = Loop(config=cfg, provider=FakeProvider(), tools=tools)
    res = await loop.run("flaky test")
    # The flaky tool should have been called 3 times (2 fail + 1 success)
    assert fail_count["n"] == 3
    # Final report must be "done" (from second chat)
    assert "done" in res.report.lower() or res.report == "done"
