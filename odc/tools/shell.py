"""Sandboxed shell.

Two layers of safety:
1. An allow-list of command prefixes (e.g. ['git', 'pytest', 'python']).
   If empty, the shell only runs a tiny safe set.
2. Each call is sandboxed to ODC_DATA_DIR by default; pass `cwd` to override.
3. Requires confirm=True for any command.

This is *not* a security boundary. The point is to make accidental
destructive commands harder, not impossible.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from odc.config import Config
from odc.tools.base import tool

# Minimum baseline — what the agent can do with empty allowlist.
_DEFAULT_SAFE = {"ls", "cat", "head", "tail", "wc", "echo", "pwd", "date", "which", "rg", "grep"}


def _resolve_allowlist(config: Config) -> set[str]:
    base = set(_DEFAULT_SAFE)
    base.update(config.shell_allowlist or [])
    return base


@tool(
    name="shell.run",
    description=(
        "Run a shell command and return stdout (and stderr if exit != 0). "
        "The command must start with one of the allow-listed prefixes. "
        "Side-effect: requires confirm=True."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command. First token must be in the allow-list.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory. Defaults to ODC_DATA_DIR.",
                "default": None,
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default 30.",
                "default": 30,
            },
        },
        "required": ["command"],
    },
    requires_confirm=True,
    side_effect=True,
)
async def shell_run(command: str, cwd: str | None = None, timeout: int = 30) -> str:
    from odc.config import Config as _C  # local import to avoid cycle at module load

    cfg = _C()
    allow = _resolve_allowlist(cfg)

    try:
        first = shlex.split(command)[0]
    except ValueError as e:
        raise ValueError(f"could not parse command: {e}") from e

    if first not in allow:
        raise PermissionError(
            f"command '{first}' is not in the shell allow-list. "
            f"Allowed: {sorted(allow)}. Set ODC_SHELL_ALLOWLIST to extend."
        )

    workdir = Path(cwd).expanduser() if cwd else cfg.data_dir
    if not workdir.exists():
        raise FileNotFoundError(f"cwd does not exist: {workdir}")

    proc = subprocess.run(
        command,
        shell=True,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "ODC_SHELL": "1"},
    )
    out = proc.stdout
    if proc.returncode != 0:
        out = (
            f"EXIT={proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    return out.strip()
