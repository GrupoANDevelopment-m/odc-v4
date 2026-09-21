"""Task decomposition for long or complex tasks.

When a task exceeds a single tool budget, decompose it into a DAG
of sub-tasks. Each sub-task has:
  - id
  - description
  - depends_on (list of sub-task ids that must complete first)
  - tool_hint (suggested tool to use, if any)
  - acceptance (how to verify it's done)

The DAG is stored in ThreadContext so it survives across turns.
The loop processes the DAG, executing leaves (no outstanding deps)
in topological order, persisting state to checkpoint.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


DECOMPOSE_PROMPT = """\
Decompose a complex task into a directed acyclic graph (DAG) of
sub-tasks. Each sub-task is small enough to complete in 1-3 tool
calls. Sub-tasks may depend on others.

TASK:
{task}

CONSTRAINTS:
- Each sub-task: 1-3 tool calls
- Total sub-tasks: 3-8 (don't over-decompose a small task)
- Acceptance criteria must be concrete and verifiable
- Dependencies form a DAG (no cycles)

OUTPUT FORMAT (strict JSON only):
{{
  "sub_tasks": [
    {{
      "id": "s1",
      "description": "<one sentence>",
      "depends_on": [],
      "tool_hint": "<tool name or empty>",
      "acceptance": "<how to verify done>"
    }},
    ...
  ],
  "execution_order": ["s1", "s2", ...],
  "estimated_calls": <total tool calls across all sub-tasks>,
  "rollback_strategy": "<what to do if any sub-task fails>"
}}
"""


def build_decompose_prompt(task: str) -> str:
    return DECOMPOSE_PROMPT.format(task=task.strip() or "(no task)")


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_decompose_response(text: str) -> dict[str, Any]:
    if not text:
        return {"error": "empty", "sub_tasks": [], "execution_order": []}
    m = _FENCE_RE.search(text)
    if m:
        try:
            return _normalize(json.loads(m.group(1)))
        except Exception:
            pass
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return _normalize(json.loads(text[start : i + 1]))
                except Exception:
                    start = -1
    return {"raw": text[:4000], "sub_tasks": [], "execution_order": []}


def _normalize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"raw": str(data)[:4000], "sub_tasks": []}
    subs = data.get("sub_tasks") or []
    norm = []
    for i, s in enumerate(subs):
        if not isinstance(s, dict):
            continue
        norm.append({
            "id": s.get("id") or f"s{i+1}",
            "description": (s.get("description") or "").strip()[:500],
            "depends_on": list(s.get("depends_on") or []),
            "tool_hint": (s.get("tool_hint") or "").strip(),
            "acceptance": (s.get("acceptance") or "").strip()[:500],
        })
    return {
        "sub_tasks": norm,
        "execution_order": data.get("execution_order") or [s["id"] for s in norm],
        "estimated_calls": int(data.get("estimated_calls") or len(norm) * 2),
        "rollback_strategy": (data.get("rollback_strategy") or "").strip()[:500],
    }


def validate_dag(dag: dict[str, Any]) -> tuple[bool, str]:
    """Check that the DAG is acyclic and dependencies are valid.
    Returns (is_valid, error_message)."""
    subs = {s["id"]: s for s in dag.get("sub_tasks", [])}
    if not subs:
        return False, "no sub-tasks"
    # All deps must reference existing sub-tasks
    for s in dag.get("sub_tasks", []):
        for dep in s.get("depends_on", []):
            if dep not in subs:
                return False, f"sub-task {s['id']} depends on missing {dep}"
    # Topological sort + cycle detection
    visited: set[str] = set()
    in_stack: set[str] = set()
    def dfs(node: str) -> bool:
        if node in in_stack:
            return True  # cycle
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in subs[node].get("depends_on", []):
            if dfs(dep):
                return True
        in_stack.discard(node)
        return False
    for s in subs:
        if dfs(s):
            return False, f"cycle detected involving {s}"
    return True, ""


def next_executable(dag: dict[str, Any], status: dict[str, str]) -> list[dict[str, Any]]:
    """Return sub-tasks that are ready to execute (deps all done)."""
    done = {k for k, v in status.items() if v == "done"}
    return [
        s for s in dag.get("sub_tasks", [])
        if s["id"] not in status
        and s["id"] not in done
        and all(d in done for d in s.get("depends_on", []))
    ]


def summarize_progress(dag: dict[str, Any], status: dict[str, str]) -> str:
    """Compact one-liner for the system prompt."""
    total = len(dag.get("sub_tasks", []))
    done = sum(1 for v in status.values() if v == "done")
    failed = sum(1 for v in status.values() if v == "failed")
    return f"[DAG progress: {done}/{total} done, {failed} failed]"


__all__ = [
    "build_decompose_prompt",
    "parse_decompose_response",
    "validate_dag",
    "next_executable",
    "summarize_progress",
]
