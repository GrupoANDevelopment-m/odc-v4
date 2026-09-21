"""Refinement tools — expose SelfRefinementEngine to the agent."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from odc.tools.base import tool
from odc.refinement.engine import SelfRefinementEngine
from odc.refinement.field_data import FieldDataStore, FieldOutcome

log = logging.getLogger(__name__)

_ENGINE: SelfRefinementEngine | None = None


def init_engine(data_dir: Path, current_config: dict | None = None,
                auto_apply: bool = False) -> SelfRefinementEngine:
    global _ENGINE
    store = FieldDataStore(data_dir / "refinement" / "field.db")
    _ENGINE = SelfRefinementEngine(
        store, current_config=current_config or {}, auto_apply=auto_apply,
    )
    return _ENGINE


def get_engine() -> SelfRefinementEngine | None:
    return _ENGINE


# ═══════════════════════════════════════════════════════════════════════
# 1. record_outcome — feed field data
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="refine.record_outcome",
    description=(
        "Record one empirical outcome of a task in the field-data store. "
        "Required inputs: task_id, task_type, hypothesis_id, success. "
        "Optional: error_class, failure_reason, evidence_tier, lenses_used."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "task_type": {"type": "string", "description": "Domain: security, osint, code, web, ..."},
            "hypothesis_id": {"type": "string", "description": "Which approach was tried"},
            "success": {"type": "boolean"},
            "duration_ms": {"type": "integer"},
            "error_class": {"type": "string"},
            "failure_reason": {"type": "string"},
            "evidence_tier": {"type": "string"},
            "lenses_used": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["task_id", "task_type", "hypothesis_id", "success"],
    },
)
async def record_outcome(
    task_id: str, task_type: str, hypothesis_id: str, success: bool,
    duration_ms: int = 0, error_class: str = "", failure_reason: str = "",
    evidence_tier: str = "DIRECT_OBSERVATION",
    lenses_used: list[str] | None = None,
) -> dict:
    eng = get_engine()
    if eng is None:
        return {"error": "engine not initialized — call init_engine(data_dir)"}
    o = FieldOutcome(
        task_id=task_id, task_type=task_type, hypothesis_id=hypothesis_id,
        success=success, duration_ms=duration_ms, error_class=error_class,
        failure_reason=failure_reason, evidence_tier=evidence_tier,
        lenses_used=lenses_used or [],
    )
    return {"id": eng.record_outcome(o)}


# ═══════════════════════════════════════════════════════════════════════
# 2. evaluate — run the cycle
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="refine.evaluate",
    description=(
        "Run the self-refinement cycle for a task_type. Returns the gate "
        "verdict, any proposal generated, sandbox result, and constitutional "
        "check. Will NOT auto-apply unless engine was initialized with "
        "auto_apply=True."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_type": {"type": "string"},
        },
        "required": ["task_type"],
    },
)
async def refine_evaluate(task_type: str) -> dict:
    eng = get_engine()
    if eng is None:
        return {"error": "engine not initialized"}
    result = eng.evaluate(task_type)
    return {
        "task_type": result.task_type,
        "verdict": {
            "is_exhausted": result.verdict.is_exhausted,
            "sample_count": result.verdict.sample_count,
            "hypothesis_count": result.verdict.hypothesis_count,
            "common_error_class": result.verdict.common_error_class,
            "confidence": result.verdict.confidence,
            "reasons": result.verdict.reasons,
        },
        "proposal": result.proposal.to_dict() if result.proposal else None,
        "sandbox": {
            "should_apply": result.sandbox.should_apply if result.sandbox else None,
            "baseline_rate": result.sandbox.baseline_failure_rate if result.sandbox else None,
            "simulated_rate": result.sandbox.simulated_failure_rate if result.sandbox else None,
            "delta": result.sandbox.delta if result.sandbox else None,
            "concerns": result.sandbox.concerns if result.sandbox else [],
        } if result.sandbox else None,
        "constitution": {
            "is_allowed": result.constitution.is_allowed if result.constitution else None,
            "violated": [v["id"] for v in result.constitution.violated] if result.constitution else [],
        } if result.constitution else None,
        "action_taken": result.action_taken,
        "explanation": result.explain(),
    }


# ═══════════════════════════════════════════════════════════════════════
# 3. apply_proposal — manually apply a verified proposal
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="refine.apply_proposal",
    description=(
        "Manually apply a proposal that has been generated + verified. "
        "Only works if the proposal has status='verified'. Stores snapshot "
        "for rollback. Returns the rollback_id."
    ),
    parameters={
        "type": "object",
        "properties": {
            "proposal_id": {"type": "string"},
        },
        "required": ["proposal_id"],
    },
)
async def apply_proposal(proposal_id: str) -> dict:
    eng = get_engine()
    if eng is None:
        return {"error": "engine not initialized"}
    # Find the proposal in the journal
    j = eng.store.journal(proposal_id=proposal_id, limit=100)
    for entry in j:
        if entry["event"] == "proposed" and entry["proposal_id"] == proposal_id:
            # Reconstruct & apply
            from odc.refinement.proposal import ModificationProposal, Target
            p = ModificationProposal(
                id=entry["proposal_id"],
                target=Target(entry["target"]),
                before=entry["before_state"],
                after=entry["after_state"],
                justification=entry["rationale"],
                field_evidence_count=0,
                common_error_class="",
                baseline_failure_rate=0.0,
                expected_improvement=0.0,
                rollback_plan={"restore": entry["before_state"]},
                status="verified",
            )
            r = eng.apply_proposal(p)
            return {"ok": True, "rollback_id": r.rollback_id, "action": r.action_taken}
    return {"error": "proposal not found or not in 'proposed' state"}


# ═══════════════════════════════════════════════════════════════════════
# 4. rollback — revert
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="refine.rollback",
    description=(
        "Roll back a previously applied modification by its proposal_id. "
        "Restores the config to its pre-modification snapshot."
    ),
    parameters={
        "type": "object",
        "properties": {
            "proposal_id": {"type": "string"},
        },
        "required": ["proposal_id"],
    },
)
async def rollback(proposal_id: str) -> dict:
    eng = get_engine()
    if eng is None:
        return {"error": "engine not initialized"}
    ok = eng.rollback(proposal_id)
    return {"ok": ok, "proposal_id": proposal_id}


# ═══════════════════════════════════════════════════════════════════════
# 5. journal — view audit trail
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="refine.journal",
    description=(
        "View the self-refinement journal (audit trail). Returns events "
        "with timestamp, target, rationale. Defaults to last 20 events."
    ),
    parameters={
        "type": "object",
        "properties": {
            "proposal_id": {"type": "string", "description": "Filter to one proposal"},
            "limit": {"type": "integer", "description": "Max events (default 20)"},
        },
    },
)
async def journal(proposal_id: str = "", limit: int = 20) -> dict:
    eng = get_engine()
    if eng is None:
        return {"error": "engine not initialized"}
    events = eng.store.journal(proposal_id=proposal_id or None, limit=limit)
    return {"count": len(events), "events": events}


# ═══════════════════════════════════════════════════════════════════════
# 6. status — engine state
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="refine.status",
    description=(
        "View current self-refinement engine state: number of applied "
        "proposals, current config snapshot, total field samples, "
        "rollback availability."
    ),
    parameters={"type": "object", "properties": {}},
)
async def status() -> dict:
    eng = get_engine()
    if eng is None:
        return {"error": "engine not initialized"}
    n_outcomes = len(eng.store.query(limit=10000))
    n_journal = len(eng.store.journal(limit=10000))
    return {
        "auto_apply": eng.auto_apply,
        "current_config_keys": list(eng.current_config.keys()),
        "applied_proposals": list(eng._applied.keys()),
        "total_field_outcomes": n_outcomes,
        "total_journal_events": n_journal,
    }


# ═══════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════
ALL_REFINEMENT_TOOLS = [record_outcome, refine_evaluate, apply_proposal, rollback, journal, status]


def register_all(registry, data_dir: Path) -> list[str]:
    init_engine(data_dir)
    names = []
    for fn in ALL_REFINEMENT_TOOLS:
        registry.register(fn)
        names.append(fn.name)
    return names
