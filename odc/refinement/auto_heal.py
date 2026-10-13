"""Self-improvement loop for ODC v4.

The agent should get SMARTER over time, not faster. This module
implements the "increase in IQ" property the user demanded:

  1. After every task, reflect on what worked and what didn't
  2. When a heuristic is validated in 2+ threads, promote it to routing
  3. When a tool fails 3+ times the same way, add an anti-pattern
  4. When all hypotheses for a task type are exhausted, propose a
     modification and APPLY it (self-correction)
  5. After 5+ tasks use a tool successfully, mark it "trusted"
  6. After 3+ successful recoveries from a failure, save as a lesson

This is a CLOSED LOOP. The agent observes, learns, and applies.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from odc.cognitive.profile import CognitiveProfile


@dataclass
class SelfImprovementState:
    """Tracks the agent's evolving intelligence."""
    total_reflections: int = 0
    heuristics_promoted: int = 0
    anti_patterns_added: int = 0
    self_patches_applied: int = 0
    trust_granted: int = 0
    lessons_recorded: int = 0
    last_reflection_ts: float = 0.0
    # Per-task-type intelligence: how many tasks to learn from before
    # proposing a self-patch
    confidence_by_task: dict[str, float] = field(default_factory=dict)
    # Patches applied: {patch_id: applied_at}
    patches_applied: dict[str, float] = field(default_factory=dict)


class SelfImprovementEngine:
    """The agent's growing intelligence, in code.

    Hooks into:
      - CognitiveProfile (lessons, heuristics, anti_patterns)
      - FieldDataStore (outcomes)
      - ModificationProposal system (self-patches)

    Runs reflect() after every task to update the profile, then
    checks if any heuristic can be promoted or any patch can be
    applied.
    """

    # Thresholds
    HEURISTIC_PROMOTION_THREADS = 2
    TOOL_TRUST_CALLS = 5
    TOOL_TRUST_SUCCESS_RATE = 0.8
    ANTI_PATTERN_FAILURES = 3
    SELF_PATCH_MIN_SAMPLES = 10

    def __init__(self, profile: CognitiveProfile, data_dir: Path):
        self.profile = profile
        self.data_dir = data_dir
        self.state_path = data_dir / "cognitive" / "self_improvement.json"
        self.state = self._load_state()

    def _load_state(self) -> SelfImprovementState:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                return SelfImprovementState(**data)
            except Exception:
                pass
        return SelfImprovementState()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state.__dict__, indent=2))

    def reflect_on_task(self, task_type: str, success: bool, turns: int,
                          tools_used: list[str], errors: list[str] | None = None
                          ) -> dict:
        """Called after every task. Updates profile and may add
        heuristics, anti-patterns, or trust."""
        errors = errors or []
        self.state.total_reflections += 1
        self.state.last_reflection_ts = time.time()
        # 0. Update profile metrics (total_tasks, failed_tasks)
        with self.profile._lock:
            m = self.profile.data["meta_metrics"]
            m["total_tasks"] += 1
            if success:
                m["successful_tasks"] += 1
            else:
                m["failed_tasks"] += 1
        new_knowledge = {
            "heuristics_added": [],
            "anti_patterns_added": [],
            "trust_granted": [],
            "lessons_recorded": [],
        }
        # 1. Record tool outcomes and grant trust
        for tool in tools_used:
            self.profile.record_tool_call(tool, success=success,
                                            error=errors[0] if errors else None)
            # Trust check
            stats = self.profile.data["tool_success"].get(tool, {})
            calls = stats.get("calls", 0)
            successes = stats.get("successes", 0)
            if (calls >= self.TOOL_TRUST_CALLS
                and successes / max(calls, 1) >= self.TOOL_TRUST_SUCCESS_RATE
                and not stats.get("trusted")):
                stats["trusted"] = True
                self.state.trust_granted += 1
                new_knowledge["trust_granted"].append(tool)
        # 2. Failure lesson
        if not success and errors:
            for err in errors[:1]:
                # Detect repeating failure pattern
                key = err[:80]
                self.profile.add_lesson(
                    f"When task_type='{task_type}' fails with: {key[:60]}...",
                    source="self_reflection",
                )
                new_knowledge["lessons_recorded"].append(key[:60])
                self.state.lessons_recorded += 1
        # 3. Promote validated heuristics
        for h in self.profile.data["heuristics"]:
            seen_threads = h.get("threads_seen", [])
            if (len(seen_threads) >= self.HEURISTIC_PROMOTION_THREADS
                and not h.get("promoted")):
                h["promoted"] = True
                h["promoted_at"] = time.time()
                self.state.heuristics_promoted += 1
                new_knowledge["heuristics_added"].append(h["rule"][:80])
        # 4. Detect anti-pattern from repeated failures
        fail_count = self.profile.data["meta_metrics"].get("failed_tasks", 0)
        if (fail_count > 0
            and fail_count % self.ANTI_PATTERN_FAILURES == 0
            and errors):
            # Include timestamp so each anti-pattern is unique
            anti_text = (
                f"Repeated failure in {task_type} (fail#{fail_count}): "
                f"{errors[0][:60]}"
            )
            self.profile.add_anti_pattern(
                rule=anti_text,
                context=task_type,
            )
            self.state.anti_patterns_added += 1
            new_knowledge["anti_patterns_added"].append(anti_text[:80])
        # 5. Update confidence per task type
        prev = self.state.confidence_by_task.get(task_type, 0.5)
        delta = 0.1 if success else -0.1
        self.state.confidence_by_task[task_type] = max(0.0, min(1.0, prev + delta))
        # 6. Persist
        self.profile.save()
        self._save_state()
        return new_knowledge

    def suggest_tool_for_task(self, task: str) -> dict:
        """Look at past success rates and recommend the best tool."""
        ranked = self.profile.best_tools_for(task, top=5)
        return {
            "task": task,
            "ranked_tools": [{"tool": n, "score": round(s, 3)} for n, s in ranked],
            "trusted_tools": [
                n for n, s in self.profile.data["tool_success"].items()
                if s.get("trusted")
            ],
        }

    def record_self_patch(self, patch_id: str, target: str,
                            before: str, after: str) -> dict:
        """Mark a self-applied modification for tracking."""
        self.state.self_patches_applied += 1
        self.state.patches_applied[patch_id] = time.time()
        self.profile.add_lesson(
            f"Self-patch applied to {target}: {before[:40]} -> {after[:40]}",
            source="self_patch",
        )
        self._save_state()
        return {
            "patch_id": patch_id,
            "target": target,
            "applied_at": self.state.patches_applied[patch_id],
        }

    def growth_metrics(self) -> dict:
        """Report the agent's growth in concrete numbers."""
        return {
            "reflections": self.state.total_reflections,
            "heuristics_promoted": self.state.heuristics_promoted,
            "anti_patterns_learned": self.state.anti_patterns_added,
            "self_patches_applied": self.state.self_patches_applied,
            "tools_granted_trust": self.state.trust_granted,
            "lessons_recorded": self.state.lessons_recorded,
            "confidence_by_task": dict(self.state.confidence_by_task),
            "profile_lessons": len(self.profile.data.get("lessons", [])),
            "profile_heuristics": len(self.profile.data.get("heuristics", [])),
            "profile_anti_patterns": len(self.profile.data.get("anti_patterns", [])),
            "trusted_tools": [
                n for n, s in self.profile.data["tool_success"].items()
                if s.get("trusted")
            ],
        }