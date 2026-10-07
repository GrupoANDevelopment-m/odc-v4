"""The agent's cognitive profile: persistent memory of strengths and gaps.

Stored at <data_dir>/cognitive/profile.json. Tracks:

- tool_success: per-tool {calls, successes, failures, last_used}
- task_patterns: detected patterns of tasks with their typical solution
- heuristics: explicit lessons the agent (or the user) has added
- meta_metrics: rolling averages of turns-per-task, tool-diversity, etc.

The profile is read by `cognitive.route` to suggest the best approach
for a new task, and updated by `cognitive.reflect` after each task.

The point: lessons compound. After 10 similar tasks, the agent KNOWS
which tool combo works, without re-deriving it.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any


class CognitiveProfile:
    """Persistent profile of what the agent has learned.

    Thread-safe for concurrent writes. Stored as pretty-printed JSON
    so humans can inspect it. Writes are atomic (write-to-temp +
    rename) so a crash mid-write doesn't corrupt the file.
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = self._load()
        self._lock = threading.RLock()

    # -- I/O -----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return self._empty()

    def _empty(self) -> dict[str, Any]:
        return {
            "version": 2,
            "created": time.time(),
            "updated": time.time(),
            "tool_success": {},
            "task_patterns": [],
            "heuristics": [],          # v2: {rule, source, created, hits, version, success_rate, last_validated}
            "anti_patterns": [],       # v2: {rule, context, occurrences, last_seen, last_validated, false_positives, source}
            "failure_investigations": [],  # v2: {tool, why, next_approach, mitigations, occurrences, last_seen, source}
            "distilled_skills": [],     # v2: {skill_name, distilled_rule, uses, success_count, source}
            "meta_metrics": {
                "total_tasks": 0,
                "total_tool_calls": 0,
                "avg_turns_per_task": 0.0,
                "successful_tasks": 0,
                "failed_tasks": 0,
            },
            "lessons": [],
        }

    def save(self) -> None:
        self.data["updated"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def _atomic_save(self) -> None:
        """Save with the lock held — atomic write + rename."""
        with self._lock:
            self.save()

    # -- mutation ------------------------------------------------------

    def record_tool_call(
        self, tool: str, success: bool, error: str | None = None
    ) -> None:
        with self._lock:
            s = self.data["tool_success"].setdefault(
                tool,
                {"calls": 0, "successes": 0, "failures": 0, "last_used": 0.0, "last_error": ""},
            )
            s["calls"] += 1
            s["last_used"] = time.time()
            if success:
                s["successes"] += 1
            else:
                s["failures"] += 1
                s["last_error"] = (error or "")[:200]
            self.data["meta_metrics"]["total_tool_calls"] += 1
            self._atomic_save()

    def record_task(self, success: bool, turns: int, tools_used: list[str]) -> None:
        with self._lock:
            m = self.data["meta_metrics"]
            m["total_tasks"] += 1
            if success:
                m["successful_tasks"] += 1
            else:
                m["failed_tasks"] += 1
            # Exponential moving average for avg_turns_per_task
            prev = m.get("avg_turns_per_task", 0.0)
            m["avg_turns_per_task"] = 0.7 * prev + 0.3 * turns
            self._atomic_save()

    def add_lesson(self, lesson: str, source: str = "agent") -> None:
        self.data["lessons"].append(
            {
                "ts": time.time(),
                "source": source,
                "lesson": lesson[:2000],
            }
        )
        # Keep at most 100 lessons
        self.data["lessons"] = self.data["lessons"][-100:]

    def add_heuristic(
        self, rule: str, source: str = "agent", *, distilled_from: str = ""
    ) -> None:
        """Add a v2 heuristic. Same key (rule) dedupes. Has version,
        hits, success_rate, last_validated. Re-evaluable — never permanent."""
        # Dedup by exact rule text
        for h in self.data["heuristics"]:
            if h["rule"].strip().lower() == rule.strip().lower():
                h["hits"] += 1
                h["last_validated"] = time.time()
                return
        self.data["heuristics"].append({
            "ts": time.time(),
            "source": source,
            "rule": rule[:500],
            "hits": 0,
            "version": 1,
            "success_count": 0,
            "failure_count": 0,
            "last_validated": time.time(),
            "distilled_from": distilled_from,
        })
        self.data["heuristics"] = self.data["heuristics"][-100:]

    def record_heuristic_outcome(self, rule: str, success: bool) -> None:
        """Record whether a heuristic's prediction was correct. Used to
        re-evaluate and deprecate bad heuristics — not permanent."""
        for h in self.data["heuristics"]:
            if h["rule"].strip().lower() == rule.strip().lower():
                h["hits"] += 1
                if success:
                    h["success_count"] += 1
                else:
                    h["failure_count"] += 1
                h["last_validated"] = time.time()
                return

    def add_anti_pattern(
        self,
        rule: str,
        context: str = "",
        *,
        source: str = "auto_distilled",
    ) -> None:
        """Add an anti-pattern with re-validation fields. NOT permanent:
        every entry has `last_validated` and `false_positives` to detect
        when the rule is wrong. Wisdom, not trauma."""
        for ap in self.data["anti_patterns"]:
            if ap["rule"].strip().lower() == rule.strip().lower():
                ap["occurrences"] += 1
                ap["last_seen"] = time.time()
                ap["last_validated"] = time.time()
                return
        self.data["anti_patterns"].append({
            "ts": time.time(),
            "source": source,
            "rule": rule[:500],
            "context": context[:300],
            "occurrences": 1,
            "last_seen": time.time(),
            "last_validated": time.time(),
            "false_positives": 0,  # times we warned but tool worked
            "deprecated": False,
        })
        self.data["anti_patterns"] = self.data["anti_patterns"][-50:]

    def record_anti_pattern_false_positive(self, rule: str) -> None:
        """The anti-pattern warned about a tool but the tool worked.
        Bumps false_positive. If FP > occurrences, deprecate."""
        for ap in self.data["anti_patterns"]:
            if ap["rule"].strip().lower() == rule.strip().lower():
                ap["false_positives"] += 1
                # Wisdom: if the rule is wrong more than right, retire it
                if ap["false_positives"] > ap["occurrences"]:
                    ap["deprecated"] = True
                return

    def add_failure_investigation(
        self,
        tool: str,
        why: str,
        next_approach: str,
        mitigations: list[str],
        *,
        source: str = "auto",
    ) -> None:
        """Distilled learning from a failure pattern.

        Unlike anti-patterns (which forbid), investigations document
        WHY something failed and HOW to try again. The agent can
        re-attempt with the suggested mitigations.
        """
        for inv in self.data["failure_investigations"]:
            if inv["tool"] == tool and inv["why"].strip() == why.strip():
                inv["occurrences"] += 1
                inv["last_seen"] = time.time()
                return
        self.data["failure_investigations"].append({
            "ts": time.time(),
            "source": source,
            "tool": tool,
            "why": why[:500],
            "next_approach": next_approach[:500],
            "mitigations": [m[:200] for m in mitigations[:5]],
            "occurrences": 1,
            "last_seen": time.time(),
        })
        self.data["failure_investigations"] = self.data["failure_investigations"][-50:]

    def get_mitigations_for(self, tool: str) -> list[str]:
        """Return all known mitigations for a tool that has failed before."""
        out: list[str] = []
        for inv in self.data["failure_investigations"]:
            if inv["tool"] == tool:
                out.extend(inv["mitigations"])
        return list(dict.fromkeys(out))[:5]

    def mark_heuristic_seen(self, rule: str, thread_id: str) -> None:
        """Mark that a heuristic was activated in the given thread.
        Used to track cross-thread validation: heuristics seen in
        2+ threads are 'promoted' and safe to inject cross-thread.
        """
        for h in self.data["heuristics"]:
            if h["rule"].strip().lower() == rule.strip().lower():
                seen = h.setdefault("seen_in_threads", [])
                if thread_id not in seen:
                    seen.append(thread_id)
                # Keep last 5 thread ids
                h["seen_in_threads"] = seen[-5:]
                return

    def promoted_heuristics(self) -> list[dict[str, Any]]:
        """Return heuristics validated in 2+ distinct threads."""
        out: list[dict[str, Any]] = []
        for h in self.data["heuristics"]:
            if len(h.get("seen_in_threads") or []) >= 2:
                out.append(h)
        return out

    def get_relevant_anti_patterns(self, task: str, *, max_age_days: float = 30) -> list[dict[str, Any]]:
        """Anti-patterns that:
        1. Are not deprecated
        2. Are within max_age_days of last validation
        3. Match the task keywords
        Anti-patterns older than max_age_days or marked deprecated are
        not surfaced — wisdom, not trauma. They can be re-validated.
        """
        cutoff = time.time() - max_age_days * 86400
        words = {w for w in task.lower().split() if len(w) > 3}
        out: list[dict[str, Any]] = []
        for ap in self.data["anti_patterns"]:
            if ap.get("deprecated"):
                continue
            if ap.get("last_validated", 0) < cutoff:
                # Stale — needs re-validation. Surface as "needs recheck" only.
                # For now, skip (don't be a trauma generator).
                continue
            ap_words = {w for w in ap["rule"].lower().split() if len(w) > 3}
            if words & ap_words:
                out.append(ap)
        return out[:3]

    def add_task_pattern(
        self, pattern: str, suggested_tools: list[str]
    ) -> None:
        """Store a generalized task pattern. CRITICAL: do NOT store the
        raw user text. We extract the *shape* of the task (verbs +
        nouns + tool names) and store only that. Raw text is the
        user's data and MUST NOT bleed across threads.

        This is part of the conversation-isolation contract: the
        profile grows (cross-thread learning) but it never carries
        user-specific content into other conversations.
        """
        # Normalize to shape only: drop punctuation, take 5 keywords
        # + 5 tool names. No secrets, no user data.
        words = re.findall(r"[a-z0-9]+", pattern.lower())
        stop = {"the", "a", "an", "to", "of", "for", "and", "or", "in", "on",
                "at", "by", "is", "are", "be", "this", "that", "it", "with",
                "as", "from", "into", "use", "using", "do", "make", "write",
                "read", "compute", "find", "get", "give", "say", "tell", "me",
                "you", "your", "i", "we", "my", "our", "want", "need"}
        keep = [w for w in words if w not in stop and len(w) > 2][:5]
        shape = " ".join(keep) if keep else "generic"
        key = shape
        for p in self.data["task_patterns"]:
            if p.get("shape") == key:
                p["occurrences"] += 1
                p["last_seen"] = time.time()
                merged = list(dict.fromkeys(p["suggested_tools"] + suggested_tools))
                p["suggested_tools"] = merged[:10]
                return
        self.data["task_patterns"].append(
            {
                "shape": key,                     # GENERALIZED only
                "pattern_summary": shape[:80],    # for human display
                "suggested_tools": list(dict.fromkeys(suggested_tools))[:10],
                "occurrences": 1,
                "created": time.time(),
                "last_seen": time.time(),
            }
        )
        self.data["task_patterns"] = self.data["task_patterns"][-100:]

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())[:100]

    # -- query ---------------------------------------------------------

    def find_similar_patterns(self, task: str, k: int = 3) -> list[dict[str, Any]]:
        """Return up to k task patterns whose text overlaps with the task.

        Cheap lexical overlap: counts common words (ignoring stopwords).
        """
        stop = {
            "the", "a", "an", "to", "of", "in", "on", "for", "and", "or",
            "is", "are", "be", "this", "that", "it", "as", "with", "by",
        }
        words = {w for w in self._normalize(task).split() if w not in stop and len(w) > 2}
        scored: list[tuple[int, dict[str, Any]]] = []
        for p in self.data["task_patterns"]:
            pwords = {w for w in self._normalize(p["pattern"]).split() if w not in stop and len(w) > 2}
            overlap = len(words & pwords)
            if overlap > 0:
                scored.append((overlap, p))
        scored.sort(key=lambda x: (-x[0], -x[1]["occurrences"]))
        return [p for _, p in scored[:k]]

    def best_tools_for(self, task: str, top: int = 5) -> list[tuple[str, float]]:
        """Return tool names with a score combining reliability and
        pattern-match evidence. Higher is better.
        """
        # Start with all tools' reliability (success rate, 0..1)
        reliability: dict[str, float] = {}
        for tool, s in self.data["tool_success"].items():
            if s["calls"] >= 1:
                reliability[tool] = s["successes"] / s["calls"]

        # Boost tools that appear in similar task patterns
        similar = self.find_similar_patterns(task, k=3)
        boosts: dict[str, float] = {}
        for p in similar:
            for t in p["suggested_tools"]:
                boosts[t] = boosts.get(t, 0) + 1.0 * p["occurrences"]

        scores = []
        seen = set()
        for t, s in reliability.items():
            seen.add(t)
            score = 0.6 * s + 0.4 * (boosts.get(t, 0) / 5.0)
            scores.append((t, score))
        # Also include boosted tools that have no reliability data
        for t, b in boosts.items():
            if t not in seen:
                scores.append((t, 0.4 * (b / 5.0)))
        scores.sort(key=lambda x: -x[1])
        return scores[:top]

    def summary(self) -> dict[str, Any]:
        """Return a short summary for the agent to read on demand."""
        m = self.data["meta_metrics"]
        return {
            "total_tasks": m["total_tasks"],
            "success_rate": (
                m["successful_tasks"] / m["total_tasks"] if m["total_tasks"] else 0
            ),
            "avg_turns": round(m["avg_turns_per_task"], 2),
            "total_tool_calls": m["total_tool_calls"],
            "top_tools": sorted(
                self.data["tool_success"].items(),
                key=lambda kv: -(kv[1]["successes"] / max(kv[1]["calls"], 1)),
            )[:5],
            "lessons_count": len(self.data["lessons"]),
            "heuristics_count": len(self.data["heuristics"]),
            "patterns_count": len(self.data["task_patterns"]),
        }
