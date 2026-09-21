"""Unified diff for the ODC code tools.

A tiny wrapper over difflib so we get a consistent, well-shaped
output for both display and machine consumption. No external
dependencies.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


def unified_diff(
    before: str,
    after: str,
    *,
    fromfile: str = "before",
    tofile: str = "after",
    n: int = 3,
) -> str:
    """Return a unified diff string."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
            n=n,
        )
    )


def file_diff(path: str | Path, *, against: str | None = None) -> dict[str, Any]:
    """Compute the diff of a file against a saved snapshot.

    Parameters
    ----------
    path: the current file path.
    against: optional path to a saved version (e.g. the original
             before editing). If None, this is a no-op that just
             returns the file's current text.

    Returns
    -------
    dict with keys: path, against, has_changes (bool), diff (str),
    before (str | None), after (str | None).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    after = p.read_text(encoding="utf-8", errors="replace")
    if against is None:
        return {
            "path": str(p),
            "against": None,
            "has_changes": False,
            "diff": "",
            "before": None,
            "after": after,
        }
    a = Path(against)
    before = a.read_text(encoding="utf-8", errors="replace") if a.exists() else ""
    d = unified_diff(before, after, fromfile=str(a), tofile=str(p))
    return {
        "path": str(p),
        "against": str(a),
        "has_changes": before != after,
        "diff": d,
        "before": before,
        "after": after,
    }
