"""In-repo text/code search using ripgrep if available, else pure Python.

Respects .gitignore. Returns matching lines with file:line:content.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from odc.tools.base import tool


@tool(
    name="search.grep",
    description=(
        "Search a directory for a regex pattern. Uses ripgrep (rg) when "
        "available, else a Python fallback. Returns a list of matches in "
        "'path:line:content' format. Respects .gitignore."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern."},
            "path": {
                "type": "string",
                "description": "Directory to search (default '.').",
                "default": ".",
            },
            "max_results": {
                "type": "integer",
                "description": "Cap on number of matches. Default 50.",
                "default": 50,
            },
            "glob": {
                "type": "string",
                "description": "Optional file glob, e.g. '*.py'.",
                "default": None,
            },
        },
        "required": ["pattern"],
    },
)
async def grep(
    pattern: str,
    path: str = ".",
    max_results: int = 50,
    glob: str | None = None,
) -> list[str]:
    base = Path(path).expanduser()
    if not base.exists():
        raise FileNotFoundError(base)

    rg = _which("rg")
    if rg:
        cmd = [
            rg,
            "--no-heading",
            "--line-number",
            "--color=never",
            "--max-columns=300",
            f"--max-columns-preview",
        ]
        if glob:
            cmd += ["--glob", glob]
        cmd += [pattern, str(base)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, check=False
            )
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(f"rg timeout after {e.timeout}s") from e
        if proc.returncode not in (0, 1):  # 1 = no matches
            raise RuntimeError(f"rg error: {proc.stderr.strip()}")
        out = proc.stdout.splitlines()[:max_results]
        return out

    # Fallback: walk + regex.
    rx = re.compile(pattern)
    results: list[str] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if glob and not p.match(glob):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                results.append(f"{p}:{i}:{line[:300]}")
                if len(results) >= max_results:
                    return results
    return results


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)
