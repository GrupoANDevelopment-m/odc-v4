"""Modification proposals — generated with traceable justification.

A proposal is NOT a mutation. It's a *description* of a change, with
the empirical evidence that motivated it, a snapshot of the current
state, and a rollback plan. The engine decides whether to verify +
apply it.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from odc.refinement.exhaustion import ExhaustionVerdict


class Target(str, Enum):
    """Which architectural component the proposal targets.

    Note: CONSTITUTIONAL_CORE is intentionally a target type but the
    ConstitutionalGuard rejects proposals that try to mutate it.
    """
    PROMPT_BUILDER = "prompt_builder"        # Layer 1 layout, BM25 weights
    COUNCIL_LENSES = "council_lenses"        # which lenses to use
    TOOL_CREATE_TEMPLATE = "tool_create_template"
    HEURISTIC_PROMOTION = "heuristic_promotion"   # seen_in_threads threshold
    ROUTING = "routing"                       # cognitive.route behavior
    ALWAYS_INCLUDED = "always_included"       # which tools are always-on
    MEMORY_SCHEMA = "memory_schema"
    CONSTITUTIONAL_CORE = "constitutional_core"  # REJECTED by guard


@dataclass
class ModificationProposal:
    """A proposed change with full provenance."""
    id: str
    target: Target
    before: dict[str, Any]
    after: dict[str, Any]
    justification: str
    field_evidence_count: int
    common_error_class: str
    baseline_failure_rate: float
    expected_improvement: float
    rollback_plan: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    sandbox_result: dict[str, Any] | None = None
    constitutional_check: dict[str, Any] | None = None
    status: str = "proposed"  # proposed | verified | rejected | applied | rolled_back
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["target"] = self.target.value
        return d


def _new_id() -> str:
    return f"prop-{uuid.uuid4().hex[:12]}"


# ──────────────────────────────────────────────────────────────────
# Proposal generators — one per target. Each consumes the
# ExhaustionVerdict + FieldDataStore and produces a specific proposal.
# ──────────────────────────────────────────────────────────────────
def _infer_target(verdict: ExhaustionVerdict) -> Target:
    """Pick a target based on common error class."""
    ec = verdict.common_error_class.lower()
    if "timeout" in ec or "rate" in ec:
        return Target.PROMPT_BUILDER     # reduce context size / fewer tools
    if "tool" in ec or "allowlist" in ec:
        return Target.COUNCIL_LENSES     # route via different expertise
    if "schema" in ec or "param" in ec:
        return Target.TOOL_CREATE_TEMPLATE
    if "prompt" in ec or "context" in ec:
        return Target.HEURISTIC_PROMOTION
    return Target.ROUTING  # default


def propose_from_exhaustion(
    verdict: ExhaustionVerdict,
    current_config: dict[str, Any],
) -> ModificationProposal:
    """Generate a proposal based on an exhaustion verdict + current config."""
    target = _infer_target(verdict)
    before = _snapshot_for(target, current_config)
    after = _derive_change(target, before, verdict)

    # Estimate expected improvement from baseline
    expected = round(min(0.5, max(0.05, verdict.confidence * 0.4)), 3)

    justification = (
        f"All {verdict.hypothesis_count} hypotheses exhausted for task_type='{verdict.task_type}' "
        f"(confidence {verdict.confidence:.0%}, common error: '{verdict.common_error_class}'). "
        f"Proposing change to {target.value} based on "
        f"{verdict.sample_count} field samples. Expected improvement: {expected:.0%}."
    )

    return ModificationProposal(
        id=_new_id(),
        target=target,
        before=before,
        after=after,
        justification=justification,
        field_evidence_count=verdict.sample_count,
        common_error_class=verdict.common_error_class,
        baseline_failure_rate=round(
            sum(s.failure_rate for s in verdict.failed_hypotheses) / max(1, len(verdict.failed_hypotheses)),
            3,
        ),
        expected_improvement=expected,
        rollback_plan={"restore": before, "snapshot_at": time.time()},
    )


def _snapshot_for(target: Target, config: dict) -> dict:
    """Pull the current value of the targeted config key."""
    keys = {
        Target.PROMPT_BUILDER: "prompt_builder",
        Target.COUNCIL_LENSES: "council_lenses",
        Target.TOOL_CREATE_TEMPLATE: "tool_create_template",
        Target.HEURISTIC_PROMOTION: "heuristic_promotion",
        Target.ROUTING: "routing",
        Target.ALWAYS_INCLUDED: "always_included",
        Target.MEMORY_SCHEMA: "memory_schema",
    }
    return {keys.get(target, "unknown"): config.get(keys.get(target, ""), {})}


def _derive_change(target: Target, before: dict, verdict: ExhaustionVerdict) -> dict:
    """Compute the proposed new state based on target + verdict."""
    cfg_key, cfg_val = next(iter(before.items()))
    ec = verdict.common_error_class.lower()

    if target == Target.PROMPT_BUILDER:
        # Reduce tool subset size if context errors
        cur = dict(cfg_val or {})
        if "timeout" in ec or "rate" in ec or "context" in ec:
            cur["initial_k"] = max(3, cur.get("initial_k", 5) - 1)
            cur["expand_k"] = max(1, cur.get("expand_k", 3) - 1)
        return {cfg_key: cur}

    if target == Target.COUNCIL_LENSES:
        cur = dict(cfg_val or {"always_run": ["expert", "hacker", "researcher", "developer", "investigator"]})
        # If timeout/rate, drop expert (most expensive)
        if "timeout" in ec or "rate" in ec:
            always = [l for l in cur.get("always_run", []) if l != "expert"]
            cur["always_run"] = always
        return {cfg_key: cur}

    if target == Target.TOOL_CREATE_TEMPLATE:
        cur = dict(cfg_val or {"require_test": False, "validate_schema": True})
        if "param" in ec or "schema" in ec:
            cur["require_test"] = True
        return {cfg_key: cur}

    if target == Target.HEURISTIC_PROMOTION:
        # Lower threshold if heuristics being over-filtered
        cur = dict(cfg_val or {"min_cross_threads": 2})
        if "context" in ec or "prompt" in ec:
            cur["min_cross_threads"] = max(1, cur.get("min_cross_threads", 2) - 1)
        return {cfg_key: cur}

    if target == Target.ROUTING:
        cur = dict(cfg_val or {})
        cur["fallback_on_all_hypothesis_fail"] = True
        return {cfg_key: cur}

    return {cfg_key: cfg_val}  # no change


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def proposal_to_json(p: ModificationProposal) -> str:
    return json.dumps(p.to_dict(), default=str, indent=2)


def hash_proposal(p: ModificationProposal) -> str:
    return hashlib.sha256(
        json.dumps({"target": p.target.value, "before": p.before, "after": p.after},
                   sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
