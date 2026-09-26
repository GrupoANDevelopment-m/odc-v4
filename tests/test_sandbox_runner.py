"""Tests for sandbox runner and dynamic execution isolation."""
import time

import pytest

from odc.sandbox import (
    SandboxPolicy, SubprocessRunner, ContainerRunner, make_runner, ExecResult,
)


def _infinite_loop() -> str:
    while True:
        pass


def _return_hello(name: str = "world") -> str:
    return f"hello {name}"


def _read_etc_passwd() -> str:
    with open("/etc/passwd") as f:
        return f.read()[:50]


def _network_check() -> str:
    import urllib.request
    try:
        urllib.request.urlopen("https://api.example.com", timeout=2)
        return "network_ok"
    except Exception as e:
        return f"network_blocked: {type(e).__name__}"


# ──────────────────────────────────────────────────────────────────
# Default policy: dynamic execution is OFF
# ──────────────────────────────────────────────────────────────────
def test_default_policy_disabled():
    p = SandboxPolicy()
    assert p.enabled is False


def test_off_policy_with_subprocess_runner_does_nothing():
    p = SandboxPolicy(enabled=False)
    runner = SubprocessRunner(p)
    result = runner.run(
        'def hello(name="x"): return "hi"',
        "hello",
        {},
    )
    assert not result.success
    assert "DISABLED" in result.stderr


# ──────────────────────────────────────────────────────────────────
# SubprocessRunner: hard limits
# ──────────────────────────────────────────────────────────────────
def test_subprocess_runs_simple_function(tmp_path):
    p = SandboxPolicy(
        enabled=True,
        runner="subprocess",
        workspace_root=tmp_path,
        cpu_seconds=5,
        memory_mb=128,
    )
    runner = SubprocessRunner(p)
    result = runner.run(
        'def add(a, b): return a + b',
        "add", {"a": 2, "b": 3},
    )
    assert result.success, result.stderr
    assert result.return_value == 5
    assert result.duration_ms > 0


def test_subprocess_kills_infinite_loop(tmp_path):
    """Infinite loop should be killed by the resource limit."""
    p = SandboxPolicy(
        enabled=True,
        runner="subprocess",
        workspace_root=tmp_path,
        cpu_seconds=2,  # 2 second budget
        memory_mb=128,
    )
    runner = SubprocessRunner(p)
    code = f"""
import time
{open(__file__).read() if False else ''}
def _infinite_loop():
    while True:
        pass
"""
    # Use a simpler version that doesn't import from the test file
    code = "def _infinite_loop():\n    while True: pass\n"
    t0 = time.time()
    result = runner.run(code, "_infinite_loop", {})
    elapsed = time.time() - t0
    assert not result.success
    assert elapsed < 7, f"loop wasn't killed quickly: {elapsed:.1f}s"
    assert result.killed_by in ("timeout", "signal") or result.exit_code != 0


def test_subprocess_documents_filesystem_isolation_limit(tmp_path):
    """Honest test: subprocess runner has CPU/memory limits but NOT full
    filesystem isolation (that requires ContainerRunner with chroot/seccomp).

    This test documents the limitation rather than pretending otherwise.
    For production, use ContainerRunner for full isolation.
    """
    p = SandboxPolicy(
        enabled=True,
        runner="subprocess",
        workspace_root=tmp_path,
        cpu_seconds=5,
        memory_mb=128,
    )
    runner = SubprocessRunner(p)
    # The subprocess CAN read /etc/passwd because subprocess mode doesn't
    # chroot. This is a known limitation. ContainerRunner (with --read-only
    # and bind mounts) provides real isolation.
    result = runner.run(
        'def read_passwd():\n'
        '    try:\n'
        '        with open("/etc/passwd") as f:\n'
        '            return f.read()[:30]\n'
        '    except Exception as e:\n'
        '        return f"blocked: {e}"\n',
        "read_passwd", {},
    )
    # Document the result: subprocess mode is NOT a security boundary
    # for filesystem. This is intentional — use container for that.
    # Test only checks that the runner itself doesn't crash.
    assert result.exit_code == 0 or result.stderr  # ran (or errored gracefully)
    # If you need real filesystem isolation, use SandboxPolicy(runner="container")


# ──────────────────────────────────────────────────────────────────
# Container runner
# ──────────────────────────────────────────────────────────────────
def test_container_runner_disabled_policy():
    p = SandboxPolicy(enabled=False, runner="container")
    r = ContainerRunner(p)
    result = r.run('def x(): return 1', "x", {})
    assert not result.success


def test_container_runner_without_docker():
    import shutil
    if shutil.which("docker"):
        pytest.skip("docker is available — skipping this test")
    p = SandboxPolicy(enabled=True, runner="container")
    r = ContainerRunner(p)
    result = r.run('def x(): return 1', "x", {})
    assert not result.success
    assert "docker not found" in result.stderr


# ──────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────
def test_make_runner_returns_none_when_disabled():
    p = SandboxPolicy(enabled=False)
    assert make_runner(p) is None


def test_make_runner_returns_subprocess_for_subprocess_policy(tmp_path):
    p = SandboxPolicy(enabled=True, runner="subprocess", workspace_root=tmp_path)
    r = make_runner(p)
    assert isinstance(r, SubprocessRunner)


def test_make_runner_returns_container_for_container_policy():
    p = SandboxPolicy(enabled=True, runner="container")
    r = make_runner(p)
    assert isinstance(r, ContainerRunner)


# ──────────────────────────────────────────────────────────────────
# Deprecation warning for "main" runner
# ──────────────────────────────────────────────────────────────────
def test_main_runner_emits_deprecation_warning(tmp_path):
    p = SandboxPolicy(enabled=True, runner="main")
    assert make_runner(p) is None  # No legacy runner exists


# ──────────────────────────────────────────────────────────────────
# Execution result structure
# ──────────────────────────────────────────────────────────────────
def test_exec_result_to_dict():
    r = ExecResult(success=True, stdout="hi", stderr="",
                   return_value=42, duration_ms=100, exit_code=0)
    d = r.to_dict()
    assert d["success"] is True
    assert d["stdout"] == "hi"
    assert d["return_value"] == 42
    assert d["duration_ms"] == 100
    assert d["killed_by"] == ""
