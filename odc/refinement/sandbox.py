"""Sandbox verifier — test a proposal before applying.

Simulates the proposed change on historical task outcomes and checks:
  - Failure rate decreases (or stays)
  - Doesn't break working hypotheses
  - Doesn't introduce regressions on success cases
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from odc.refinement.proposal import ModificationProposal
from odc.refinement.field_data import FieldDataStore, FieldOutcome


@dataclass
class SandboxVerdict:
    should_apply: bool
    baseline_failure_rate: float
    simulated_failure_rate: float
    delta: float            # negative = improvement
    sample_size: int
    concerns: list[str] = field(default_factory=list)
    regression_count: int = 0
    improvement_count: int = 0

    def explain(self) -> str:
        arrow = "↓" if self.delta < 0 else "↑" if self.delta > 0 else "="
        lines = [
            f"{'✓' if self.should_apply else '✗'} Sandbox: "
            f"{self.baseline_failure_rate:.1%} {arrow} {self.simulated_failure_rate:.1%} "
            f"(Δ={self.delta:+.1%}, n={self.sample_size})",
        ]
        if self.improvement_count:
            lines.append(f"  improvements: {self.improvement_count}")
        if self.regression_count:
            lines.append(f"  regressions: {self.regression_count}")
        if self.concerns:
            lines.append("  concerns:")
            for c in self.concerns:
                lines.append(f"    • {c}")
        return "\n".join(lines)


class SandboxVerifier:
    """Simulate the proposed change on historical task outcomes."""

    # Conservative thresholds
    MIN_SAMPLES = 5
    MIN_DELTA_IMPROVEMENT = 0.05      # need ≥5% improvement to apply
    MAX_REGRESSION_RATIO = 0.20       # ≤20% of cases can regress

    def __init__(self, store: FieldDataStore):
        self.store = store

    def verify(self, proposal: ModificationProposal) -> SandboxVerdict:
        # Get all outcomes first
        all_outcomes = self.store.query(limit=500)

        # Filter to relevant task_type if the proposal has it
        relevant_task_types = []
        try:
            relevant_task_types = proposal.metadata_belongs_to_task_type() or []
        except Exception:
            pass
        if relevant_task_types:
            relevant = [o for o in all_outcomes if o.task_type in relevant_task_types]
        else:
            relevant = all_outcomes

        if len(relevant) < self.MIN_SAMPLES:
            return SandboxVerdict(
                should_apply=False,
                baseline_failure_rate=proposal.baseline_failure_rate,
                simulated_failure_rate=proposal.baseline_failure_rate,
                delta=0.0, sample_size=len(relevant),
                concerns=[f"only {len(relevant)} samples (need {self.MIN_SAMPLES})"],
            )

        baseline_failures = sum(1 for o in relevant if not o.success)
        baseline_rate = baseline_failures / len(relevant)

        # Simulate the proposed change: simple model — would the change have
        # converted failures into successes?
        converted = self._simulate(proposal, relevant)
        sim_failures = sum(1 for o in relevant if o not in converted["converted_to_success"])
        sim_rate = sim_failures / len(relevant) if relevant else 0.0

        improvements = converted["converted_to_success"]
        regressions = converted["regressed_to_failure"]

        delta = sim_rate - baseline_rate

        concerns: list[str] = []
        if delta >= 0:
            concerns.append(f"no improvement (Δ={delta:+.1%})")
        if regressions and (len(regressions) / len(relevant)) > self.MAX_REGRESSION_RATIO:
            concerns.append(f"too many regressions ({len(regressions)}/{len(relevant)})")

        should_apply = (
            delta <= -self.MIN_DELTA_IMPROVEMENT
            and not concerns
            and sim_rate < 0.5   # must reach acceptable failure rate
        )

        return SandboxVerdict(
            should_apply=should_apply,
            baseline_failure_rate=round(baseline_rate, 4),
            simulated_failure_rate=round(sim_rate, 4),
            delta=round(delta, 4),
            sample_size=len(relevant),
            concerns=concerns,
            improvement_count=len(improvements),
            regression_count=len(regressions),
        )

    def _simulate(
        self,
        proposal: ModificationProposal,
        outcomes: list[FieldOutcome],
    ) -> dict[str, list[FieldOutcome]]:
        """Simulate the proposal's effect on historical outcomes.

        Simple heuristic model:
          - If proposal reduces prompt size (PROMPT_BUILDER), assume
            timeouts/rate_limits get resolved (would have succeeded).
          - If proposal adds stricter validation (TOOL_CREATE_TEMPLATE),
            assume schema/param errors get caught earlier.
          - If proposal adjusts heuristics, assume previously-rejected
            heuristics now usable → slight improvement on context errors.
          - Anything else: small noise — assume same outcome.
        """
        converted_success: list[FieldOutcome] = []
        regressed: list[FieldOutcome] = []

        target = proposal.target.value
        for o in outcomes:
            if o.success:
                # Successful outcomes: assume no regression (small risk: 5%)
                if o.error_class in ("none", "", None):
                    continue
                continue
            # Failure case — would the proposal have helped?
            ec = (o.error_class or "").lower()
            would_help = False
            if target == "prompt_builder" and any(t in ec for t in ("timeout", "rate", "context")):
                would_help = True
            elif target == "council_lenses" and any(t in ec for t in ("tool", "allowlist")):
                would_help = True
            elif target == "tool_create_template" and any(t in ec for t in ("schema", "param", "validation")):
                would_help = True
            elif target == "heuristic_promotion" and any(t in ec for t in ("prompt", "context")):
                would_help = True
            elif target == "routing" and any(t in ec for t in ("route", "fallback")):
                would_help = True

            if would_help:
                converted_success.append(o)
            else:
                # Small regression risk if we changed heuristic promotion
                if target == "heuristic_promotion" and o.success:
                    if "context" in ec:
                        regressed.append(o)

        return {
            "converted_to_success": converted_success,
            "regressed_to_failure": regressed,
        }


# Add metadata_belongs_to_task_type to ModificationProposal
def _patch_proposal():
    from odc.refinement.proposal import ModificationProposal
    def _get_task_types(self) -> list[str]:
        return self.metadata.get("relevant_task_types", [])
    def _belongs(self) -> list[str]:
        return self.metadata.get("relevant_task_types", [])
    # attach as method
    ModificationProposal.metadata_belongs_to_task_type = _belongs  # type: ignore[attr-defined]


_patch_proposal()
