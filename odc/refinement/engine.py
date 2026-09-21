"""SelfRefinementEngine — orchestrates evaluate → propose → verify → guard → apply."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from odc.refinement.constitution import (
    ConstitutionalGuard, ConstitutionalVerdict, CONSTITUTIONAL_INVARIANTS,
)
from odc.refinement.exhaustion import ExhaustionGate, ExhaustionVerdict
from odc.refinement.field_data import FieldDataStore, FieldOutcome
from odc.refinement.proposal import (
    ModificationProposal, Target, propose_from_exhaustion, hash_proposal,
)
from odc.refinement.sandbox import SandboxVerifier, SandboxVerdict

log = logging.getLogger(__name__)


@dataclass
class RefinementResult:
    """Result of one self-refinement evaluation."""
    task_type: str
    timestamp: float
    verdict: ExhaustionVerdict
    proposal: ModificationProposal | None = None
    sandbox: SandboxVerdict | None = None
    constitution: ConstitutionalVerdict | None = None
    action_taken: str = "none"   # none | proposed | verified | rejected | applied | rolled_back
    rollback_id: str = ""
    error: str = ""

    def explain(self) -> str:
        lines = [f"=== Self-Refinement Evaluation ==="]
        lines.append(f"task_type: {self.task_type}")
        lines.append(f"verdict: {'EXHAUSTED' if self.verdict.is_exhausted else 'NOT exhausted'}")
        if self.verdict.is_exhausted:
            lines.append(f"  confidence: {self.verdict.confidence:.0%}")
            lines.append(f"  common error: {self.verdict.common_error_class}")
        if self.proposal:
            lines.append(f"\nproposal: {self.proposal.target.value}")
            lines.append(f"  justification: {self.proposal.justification}")
            lines.append(f"  field_evidence: {self.proposal.field_evidence_count}")
        if self.sandbox:
            lines.append(f"\nsandbox:")
            lines.append(f"  {self.sandbox.explain()}")
        if self.constitution:
            lines.append(f"\nconstitution:")
            lines.append(f"  {self.constitution.explain()}")
        lines.append(f"\naction_taken: {self.action_taken}")
        if self.error:
            lines.append(f"error: {self.error}")
        return "\n".join(lines)


class SelfRefinementEngine:
    """The orchestrator. Public API:

      engine = SelfRefinementEngine(...)
      engine.record_outcome(outcome)         # field data
      result = engine.evaluate(task_type)     # run the cycle
      engine.apply(result.proposal)          # apply with rollback
      engine.rollback(rollback_id)           # revert
    """

    def __init__(
        self,
        store: FieldDataStore,
        *,
        current_config: dict[str, Any] | None = None,
        auto_apply: bool = False,    # if False (default), only PROPOSES
        custom_gate: ExhaustionGate | None = None,
        custom_guard: ConstitutionalGuard | None = None,
        custom_verifier: SandboxVerifier | None = None,
    ):
        self.store = store
        self.current_config = current_config or {}
        self.auto_apply = auto_apply
        self.gate = custom_gate or ExhaustionGate(store)
        self.guard = custom_guard or ConstitutionalGuard()
        self.verifier = custom_verifier or SandboxVerifier(store)
        # Track applied proposals for rollback
        self._applied: dict[str, dict] = {}  # proposal_id -> {before, after, applied_at}

    # ──────────────────────────────────────────
    # Field data interface
    # ──────────────────────────────────────────
    def record_outcome(self, outcome: FieldOutcome) -> int:
        return self.store.record(outcome)

    def record_outcomes_batch(self, outcomes: list[FieldOutcome]) -> list[int]:
        return [self.store.record(o) for o in outcomes]

    # ──────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────
    def evaluate(self, task_type: str) -> RefinementResult:
        """Run the full cycle: gate → propose → sandbox → constitutional check."""
        ts = time.time()
        verdict = self.gate.evaluate(task_type)
        result = RefinementResult(
            task_type=task_type, timestamp=ts, verdict=verdict,
        )

        if not verdict.is_exhausted:
            self.store.record_journal(
                event="evaluator_skipped",
                target="exhaustion_gate",
                rationale=f"not exhausted: {'; '.join(verdict.reasons)}",
                metadata={"confidence": verdict.confidence},
            )
            return result

        # Step 1: Generate proposal
        proposal = propose_from_exhaustion(verdict, self.current_config)
        # Add relevant task types to metadata for sandbox filtering
        proposal.metadata["relevant_task_types"] = [task_type]
        result.proposal = proposal
        self.store.record_journal(
            event="proposed",
            target=proposal.target.value,
            proposal_id=proposal.id,
            rationale=proposal.justification,
            before_state=proposal.before,
            after_state=proposal.after,
        )
        result.action_taken = "proposed"

        # Step 2: Constitutional check
        const = self.guard.check(proposal)
        result.constitution = const
        proposal.constitutional_check = {
            "is_allowed": const.is_allowed,
            "violated": [v["id"] for v in const.violated],
            "passed": const.passed,
        }
        self.store.record_journal(
            event="constitutional_check",
            target=proposal.target.value,
            proposal_id=proposal.id,
            rationale="; ".join(v["rule"] for v in const.violated) or "all checks passed",
        )
        if not const.is_allowed:
            proposal.status = "rejected"
            result.action_taken = "rejected"
            self.store.record_journal(
                event="rejected",
                target=proposal.target.value,
                proposal_id=proposal.id,
                rationale=f"constitution: {[v['id'] for v in const.violated]}",
            )
            return result

        # Step 3: Sandbox verification
        sandbox = self.verifier.verify(proposal)
        result.sandbox = sandbox
        proposal.sandbox_result = {
            "should_apply": sandbox.should_apply,
            "delta": sandbox.delta,
            "concerns": sandbox.concerns,
        }
        self.store.record_journal(
            event="sandbox_verified",
            target=proposal.target.value,
            proposal_id=proposal.id,
            rationale=f"delta={sandbox.delta:+.3f}, n={sandbox.sample_size}",
            success=sandbox.should_apply,
        )
        if not sandbox.should_apply:
            proposal.status = "rejected"
            result.action_taken = "rejected"
            self.store.record_journal(
                event="rejected_sandbox",
                target=proposal.target.value,
                proposal_id=proposal.id,
                rationale=f"sandbox concerns: {sandbox.concerns}",
            )
            return result

        proposal.status = "verified"
        result.action_taken = "verified"

        # Step 4: Apply (only if auto_apply=True)
        if self.auto_apply:
            return self._apply(result)

        return result

    def _apply(self, result: RefinementResult) -> RefinementResult:
        proposal = result.proposal
        if not proposal:
            return result
        try:
            # Snapshot current config
            snapshot_before = json.loads(json.dumps(self.current_config))
            # Apply (deep merge)
            for k, v in proposal.after.items():
                self.current_config[k] = v
            self._applied[proposal.id] = {
                "before": proposal.before,
                "after": proposal.after,
                "config_snapshot_before": snapshot_before,
                "applied_at": time.time(),
            }
            proposal.status = "applied"
            result.action_taken = "applied"
            result.rollback_id = proposal.id
            self.store.record_journal(
                event="applied",
                target=proposal.target.value,
                proposal_id=proposal.id,
                rationale=f"expected improvement: {proposal.expected_improvement:.1%}",
                before_state=proposal.before,
                after_state=proposal.after,
                success=True,
            )
        except Exception as e:
            result.action_taken = "rejected"
            result.error = str(e)
            self.store.record_journal(
                event="apply_failed",
                target=proposal.target.value,
                proposal_id=proposal.id,
                rationale=str(e),
            )
        return result

    def rollback(self, proposal_id: str) -> bool:
        """Revert a previously applied modification."""
        if proposal_id not in self._applied:
            return False
        entry = self._applied.pop(proposal_id)
        # Restore config
        self.current_config = entry["config_snapshot_before"]
        self.store.record_journal(
            event="rolled_back",
            target="(see proposal)",
            proposal_id=proposal_id,
            rationale="manual rollback",
            before_state=entry["after"],
            after_state=entry["before"],
        )
        return True

    def apply_proposal(self, proposal: ModificationProposal) -> RefinementResult:
        """Manually apply a verified proposal (bypasses auto_apply flag)."""
        result = RefinementResult(
            task_type="manual",
            timestamp=time.time(),
            verdict=ExhaustionVerdict(is_exhausted=True, task_type="manual", sample_count=0, hypothesis_count=0),
            proposal=proposal,
        )
        return self._apply(result)
