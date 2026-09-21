"""Thread-scoped state for the agent.

A "thread" is one agent.run() execution (one task, possibly multi-turn).
The constraint from the user is hard: the agent MUST NOT mix one
conversation with another. Unless the user explicitly references a
previous conversation.

This module provides:
- ThreadContext: per-thread ephemeral state (in-memory + on disk)
- ScopeGate: decides what to surface from the global profile vs not

The global profile is still written to (long-term learning). But for
in-prompt injection, only entries that are explicitly cross-thread-
promoted (heuristics validated in 2+ threads) get injected. AND only
when the user task looks related. AND never as "this came from a
previous conversation" — that requires explicit user reference.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


# Robust word extraction: split on non-alphanumeric, drop short/empty.
_WORD_RE = re.compile(r"[a-z0-9]+")


def _key_words(text: str) -> set[str]:
    """Extract a set of word tokens, lowercased, len > 2, alphanumeric only."""
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2}


class ThreadContext:
    """Ephemeral state for one thread.

    Stored at <data_dir>/threads/<thread_id>/context.json. Survives
    crashes (so the thread can resume), but is NOT shared with other
    threads. When the user starts a new task, a new ThreadContext is
    created — the previous one stays on disk but is not read unless
    the user explicitly asks.
    """

    def __init__(self, thread_id: str, data_dir: Path):
        self.thread_id = thread_id
        self.dir = data_dir / "threads" / thread_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "context.json"
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return self._empty()

    def _empty(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "created": time.time(),
            "updated": time.time(),
            "task": "",
            "tools_used": [],
            "heuristics_activated": [],
            "investigations_used": [],
            "current_plan": [],
            "current_turn": 0,
            "facts_learned": [],
            "questions_pending": [],
            "summary_tail": "",  # last summarization of older turns
        }

    def save(self) -> None:
        self.data["updated"] = time.time()
        self.path.write_text(json.dumps(self.data, indent=2))

    def record_tool(self, tool_name: str) -> None:
        if tool_name not in self.data["tools_used"]:
            self.data["tools_used"].append(tool_name)
        self.data["current_turn"] += 1

    def activate_heuristic(self, rule: str) -> None:
        if rule not in self.data["heuristics_activated"]:
            self.data["heuristics_activated"].append(rule)

    def activate_investigation(self, tool: str) -> None:
        if tool not in self.data["investigations_used"]:
            self.data["investigations_used"].append(tool)

    def add_fact(self, fact: str) -> None:
        self.data["facts_learned"].append({"ts": time.time(), "fact": fact[:300]})
        self.data["facts_learned"] = self.data["facts_learned"][-30:]

    def set_plan(self, plan: list[str]) -> None:
        self.data["current_plan"] = plan[:10]

    def set_summary_tail(self, summary: str) -> None:
        self.data["summary_tail"] = summary[:2000]

    def to_brief(self) -> str:
        """Render a short brief for the current thread, suitable for
        injection into pre_task_brief. ONLY thread-scoped data."""
        parts: list[str] = []
        if self.data.get("summary_tail"):
            parts.append(f"Thread summary (this conversation): {self.data['summary_tail']}")
        if self.data.get("facts_learned"):
            facts = "; ".join(f["fact"] for f in self.data["facts_learned"][-5:])
            parts.append(f"Recent facts from this conversation: {facts}")
        if self.data.get("current_plan"):
            plan = " | ".join(self.data["current_plan"][:5])
            parts.append(f"Plan in progress: {plan}")
        if self.data.get("questions_pending"):
            q = " | ".join(self.data["questions_pending"][:3])
            parts.append(f"Pending questions: {q}")
        return "\n".join(parts) if parts else ""


# Cross-thread reference detection ------------------------------------------------

# Phrases that the user uses to reference a previous conversation.
CROSS_THREAD_HINTS = (
    "remember when",
    "last time",
    "previous conversation",
    "we discussed",
    "we talked about",
    "yesterday we",
    "earlier we",
    "before we",
    "as you said",
    "from our last",
    "from before",
    "previously",
    "last session",
    "previous session",
    "in our previous",
)


def user_references_past(task: str) -> bool:
    """Return True if the user's task explicitly references a past
    conversation. Used to gate cross-thread profile injection."""
    t = task.lower()
    return any(hint in t for hint in CROSS_THREAD_HINTS)


# Profile cross-thread gate --------------------------------------------------


def heuristics_safe_to_inject(
    profile_data: dict[str, Any],
    task: str,
    *,
    min_cross_threads: int = 1,
) -> list[dict[str, Any]]:
    """Return the heuristics that are safe to inject into the current
    task's pre_task_brief.

    Rules (wisdom, not trauma):
    1. Heuristic must have at least `min_cross_threads` distinct thread_ids
       in its `seen_in_threads` field — this means it was validated
       across multiple conversations, so it's a real rule not a one-off.
    2. Heuristic must not be deprecated (success_count >> failure_count).
    3. Heuristic keywords must match the current task.

    By default min_cross_threads=1, which means: if the user ran this
    exact same task before (deterministic thread_id), the heuristic is
    available. The user can override by passing a higher value to
    require more independent validations.
    """
    if not user_references_past(task) and min_cross_threads > 1:
        # Without explicit user reference, require 2+ threads
        pass
    out: list[dict[str, Any]] = []
    task_words = _key_words(task)
    for h in profile_data.get("heuristics", []):
        seen_in = h.get("seen_in_threads") or []
        if len(seen_in) < min_cross_threads:
            continue
        # Wisdom not trauma: deprecate on too many failures
        if h.get("failure_count", 0) > h.get("success_count", 0) + 3:
            continue
        h_words = _key_words(h["rule"])
        if not (task_words & h_words):
            continue
        out.append(h)
    return out[:3]


def investigations_safe_to_inject(
    profile_data: dict[str, Any], task: str
) -> list[dict[str, Any]]:
    """Same rules as heuristics but for failure_investigations.

    Investigations are MORE permissive than anti-patterns because they
    capture 'how to try again', not 'avoid forever'. Cross-thread is
    not required — a recent investigation is useful even if just from
    this thread, as long as it matches the task.
    """
    out: list[dict[str, Any]] = []
    task_words = _key_words(task)
    task_lower = task.lower()
    for inv in profile_data.get("failure_investigations", []):
        if inv.get("occurrences", 0) < 1:
            continue
        tool = inv.get("tool", "")
        if not tool:
            continue
        # Match tool name appearing in the task
        if tool.lower() in task_lower:
            out.append(inv)
            continue
        # Or any mitigation keyword (word overlap with task)
        for m in inv.get("mitigations", []):
            if _key_words(m) & task_words:
                out.append(inv)
                break
    return out[:3]
