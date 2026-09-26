"""Sandboxed execution for dynamic tools.

Post-audit (2026-09-23): dynamic tool execution was unsafe.
This package provides isolated runners with resource limits.

Default: dynamic tool execution is DISABLED.
"""
from odc.sandbox.runner import (
    SandboxPolicy,
    ExecResult,
    SubprocessRunner,
    ContainerRunner,
    make_runner,
)

__all__ = [
    "SandboxPolicy",
    "ExecResult",
    "SubprocessRunner",
    "ContainerRunner",
    "make_runner",
]
