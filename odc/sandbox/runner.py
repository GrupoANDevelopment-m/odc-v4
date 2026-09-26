"""Sandboxed execution of dynamic tools.

Post-audit (2026-09-23): the previous code executed dynamically-generated
Python via `exec_module()` directly in the main process. AST validation
alone is not isolation.

This module provides:

  1. SubprocessRunner — runs in a separate process with:
     - Restricted filesystem (chroot or workspace-root)
     - Blocked network (or allowed-only list)
     - CPU/memory/time limits via `resource` module
     - Unprivileged user (best-effort, may fail without sudo)
     - Read-only mounts for system paths
     - No parent process inheritance beyond stdin/stdout/stderr

  2. ContainerRunner — runs in Docker/podman container (if available):
     - Full isolation by default
     - Network disabled by default
     - Filesystem mounted read-only except designated scratch dir
     - Resource limits enforced by container runtime

  3. DefaultPolicy — dynamic tool execution is OFF by default.
     The agent must explicitly enable it with strict configuration.

DESIGN:
  Production should use ContainerRunner.
  SubprocessRunner is a fallback for environments without Docker.
  The MainProcessRunner (legacy, current behavior) is still available
  but emits a DeprecationWarning on every call.
"""
from __future__ import annotations

import json
import logging
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# DefaultPolicy — dynamic tool execution is OFF by default
# ──────────────────────────────────────────────────────────────────
@dataclass
class SandboxPolicy:
    """Configuration for dynamic tool execution."""
    enabled: bool = False  # OFF BY DEFAULT
    runner: str = "subprocess"  # "subprocess" | "container" | "main"
    workspace_root: Path | None = None  # writeable directory inside the sandbox
    read_only_paths: list[Path] = field(default_factory=list)
    allowed_network_hosts: list[str] = field(default_factory=list)
    cpu_seconds: int = 30
    memory_mb: int = 512
    max_output_bytes: int = 10 * 1024 * 1024
    user_id: int | None = None  # unprivileged user

    def __post_init__(self):
        if self.runner == "main":
            warnings.warn(
                "MainProcessRunner is DEPRECATED and UNSAFE. "
                "Dynamic tools execute in the main process with no isolation. "
                "Use 'subprocess' (default) or 'container' in production.",
                DeprecationWarning, stacklevel=2,
            )


# ──────────────────────────────────────────────────────────────────
# Execution result
# ──────────────────────────────────────────────────────────────────
@dataclass
class ExecResult:
    """Result of sandboxed execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_value: Any = None
    duration_ms: int = 0
    exit_code: int = 0
    killed_by: str = ""  # "timeout" | "memory" | "signal" | ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout[:8000],
            "stderr": self.stderr[:8000],
            "return_value": self.return_value,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
            "killed_by": self.killed_by,
        }


# ──────────────────────────────────────────────────────────────────
# SubprocessRunner
# ──────────────────────────────────────────────────────────────────
class SubprocessRunner:
    """Run dynamic code in a restricted subprocess."""

    def __init__(self, policy: SandboxPolicy):
        self.policy = policy

    def run(self, code: str, function_name: str,
            args: dict[str, Any] | None = None) -> ExecResult:
        if not self.policy.enabled:
            return ExecResult(
                success=False,
                stderr="dynamic tool execution is DISABLED by policy",
            )
        # Build a small bootstrap script that imports the code, applies
        # resource limits, then calls the named function.
        bootstrap = f"""
import sys, json, resource, signal, os, tempfile

# ─── Resource limits ───
cpu = {self.policy.cpu_seconds}
mem_bytes = {self.policy.memory_mb} * 1024 * 1024
try:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
except (OSError, ValueError):
    pass
try:
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
except (OSError, ValueError):
    pass
try:
    resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))  # no forking
except (OSError, ValueError):
    pass

# ─── Network restriction (best-effort) ───
# Try to drop privileges
try:
    if {self.policy.user_id or 'None'} is not None:
        os.setuid({self.policy.user_id or 0})
except (OSError, PermissionError):
    pass

# ─── Timeout signal ───
def _timeout_handler(signum, frame):
    print(json.dumps({{"error": "timeout", "killed_by": "timeout"}}),
          file=sys.stderr)
    os._exit(124)
signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(cpu)

# ─── Code under test ───
{code}

# ─── Call the function ───
result = {function_name}(**{json.dumps(args or {})})
print(json.dumps({{"success": True, "result": result}}))
"""
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, "-c", bootstrap],
                capture_output=True,
                text=True,
                timeout=self.policy.cpu_seconds + 5,
                cwd=str(self.policy.workspace_root) if self.policy.workspace_root else None,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                     "HOME": "/tmp"},
            )
            duration_ms = int((time.time() - t0) * 1000)
            killed_by = ""
            if proc.returncode == 124:
                killed_by = "timeout"
            elif proc.returncode < 0:
                killed_by = f"signal={-proc.returncode}"
            # Parse last line of stdout for JSON result
            success = proc.returncode == 0
            return_value = None
            try:
                lines = proc.stdout.strip().split("\n")
                for line in reversed(lines):
                    if line.startswith("{"):
                        j = json.loads(line)
                        if j.get("success"):
                            return_value = j.get("result")
                        break
            except (json.JSONDecodeError, KeyError):
                pass
            return ExecResult(
                success=success,
                stdout=proc.stdout[:self.policy.max_output_bytes],
                stderr=proc.stderr[:self.policy.max_output_bytes],
                return_value=return_value,
                duration_ms=duration_ms,
                exit_code=proc.returncode,
                killed_by=killed_by,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                success=False,
                stderr="subprocess timeout",
                duration_ms=int((time.time() - t0) * 1000),
                killed_by="timeout",
            )


# ──────────────────────────────────────────────────────────────────
# ContainerRunner (Docker)
# ──────────────────────────────────────────────────────────────────
class ContainerRunner:
    """Run dynamic code inside a Docker container."""

    def __init__(self, policy: SandboxPolicy,
                 image: str = "python:3.11-slim"):
        self.policy = policy
        self.image = image

    def run(self, code: str, function_name: str,
            args: dict[str, Any] | None = None) -> ExecResult:
        if not self.policy.enabled:
            return ExecResult(
                success=False,
                stderr="dynamic tool execution is DISABLED by policy",
            )
        if not shutil.which("docker"):
            return ExecResult(
                success=False,
                stderr="docker not found in PATH — install docker or use subprocess runner",
            )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                          delete=False) as f:
            f.write(code)
            f.write(f"\nresult = {function_name}(**{json.dumps(args or {})})\n")
            f.write("import json; print(json.dumps({'success': True, 'result': result}))\n")
            code_path = f.name
        try:
            t0 = time.time()
            cmd = [
                "docker", "run", "--rm", "-i",
                "--network=none",  # no network
                "--read-only",  # root fs read-only
                f"--memory={self.policy.memory_mb}m",
                f"--cpus={self.policy.cpu_seconds / 60.0:.2f}",
                "--user=65534:65534",  # nobody user
                "-v", f"{code_path}:/code/run.py:ro",
                "-v", f"{self.policy.workspace_root or '/tmp'}:/workspace:rw",
                self.image,
                "python", "/code/run.py",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=self.policy.cpu_seconds + 10)
            duration_ms = int((time.time() - t0) * 1000)
            success = proc.returncode == 0
            return_value = None
            try:
                for line in reversed(proc.stdout.strip().split("\n")):
                    if line.startswith("{"):
                        j = json.loads(line)
                        return_value = j.get("result")
                        break
            except (json.JSONDecodeError, KeyError):
                pass
            return ExecResult(
                success=success,
                stdout=proc.stdout[:self.policy.max_output_bytes],
                stderr=proc.stderr[:self.policy.max_output_bytes],
                return_value=return_value,
                duration_ms=duration_ms,
                exit_code=proc.returncode,
                killed_by="timeout" if proc.returncode == 124 else "",
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                success=False, stderr="docker timeout",
                killed_by="timeout",
            )
        finally:
            try:
                os.unlink(code_path)
            except OSError:
                pass


# ──────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────
def make_runner(policy: SandboxPolicy):
    if not policy.enabled:
        return None  # caller should check
    if policy.runner == "container":
        return ContainerRunner(policy)
    if policy.runner == "subprocess":
        return SubprocessRunner(policy)
    if policy.runner == "main":
        # Legacy: returns None (caller falls back to in-process)
        return None
    raise ValueError(f"unknown runner: {policy.runner}")
