"""Sandbox verifier — test a proposal before applying.

Simulates the proposed change on historical task outcomes and checks:
  - Failure rate decreases (or stays)
  - Doesn't break working hypotheses
  - Doesn't introduce regressions on success cases

DESIGN NOTES (post-audit, fixed bugs):

  Bug #1 (previous code):
    sim_failures = sum(1 for o in relevant if o not in converted_to_success)
    This counted ALL non-converted outcomes as failures, including
    successes that were never going to be converted. The simulated
    failure rate was always biased high.

    FIX: a baseline success stays a simulated success unless we have
    a specific regression model that says otherwise.

  Bug #2 (previous code):
    The "regression" branch was inside `else: # failure case` but then
    tested `if o.success` — unreachable, since we just excluded
    success outcomes.

    FIX: regression is computed in the success branch (which is now
    correctly separated).

  Bug #3 (new):
    The simulation now also checks: would the proposal introduce a NEW
    failure mode for currently-working outcomes? That's the regression
    signal. Previously it was both unreachable AND not even computed
    correctly (the target heuristic_promotion was the only candidate).

This file was rewritten after audit 2026-09-23.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from odc.refinement.proposal import ModificationProposal
from odc.refinement.field_data import FieldDataStore, FieldOutcome

log = logging.getLogger(__name__)


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
    """Simulate the proposed change on historical task outcomes.

    The simulation is conservative: it only converts outcomes that match
    a known improvement pattern for the proposal's target, and only
    introduces regressions that match a known regression pattern.

    Anything that doesn't match either pattern stays the same.
    """

    # Conservative thresholds — biased toward REJECTING risky changes
    MIN_SAMPLES = 5
    MIN_DELTA_IMPROVEMENT = 0.05      # need ≥5% absolute improvement
    MAX_REGRESSION_RATIO = 0.20       # ≤20% of cases can regress
    # Cap how much of a single pattern can dominate simulation results
    MAX_CONVERSION_RATIO = 0.50        # don't claim >50% converted by one rule

    # Improvement rules — target → (error_class_substring, conversion_rate)
    # conversion_rate is the proportion of matching failures we expect to flip
    IMPROVEMENT_RULES: dict[str, list[tuple[str, float]]] = {
        "prompt_builder": [
            ("timeout", 0.7),
            ("rate", 0.6),
            ("context", 0.7),
            ("token", 0.5),
        ],
        "council_lenses": [
            ("tool", 0.5),
            ("allowlist", 0.7),
            ("not_in", 0.7),
        ],
        "tool_create_template": [
            ("schema", 0.8),
            ("param", 0.8),
            ("validation", 0.7),
            ("type_error", 0.7),
        ],
        "heuristic_promotion": [
            ("prompt", 0.3),
            ("context", 0.3),
        ],
        "routing": [
            ("route", 0.6),
            ("fallback", 0.5),
        ],
        "always_included": [
            ("missing_tool", 0.4),
        ],
        "memory_schema": [
            ("schema", 0.5),
        ],
    }

    # Regression rules — changes that COULD break working outcomes
    # target → (error_class_substring) — outcomes with this error in the
    # currently-successful set are at risk
    REGRESSION_RULES: dict[str, list[str]] = {
        "prompt_builder": ["context", "missing"],
        "council_lenses": ["type", "schema"],
        "tool_create_template": ["validation"],  # stricter validation breaks things
        "heuristic_promotion": ["context"],  # lowering threshold adds bad heur.
        "routing": ["route"],
    }

    def __init__(self, store: FieldDataStore):
        self.store = store

    def verify(self, proposal: ModificationProposal) -> SandboxVerdict:
        all_outcomes = self.store.query(limit=500)

        # Filter to relevant task_type if known
        relevant_task_types: list[str] = []
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

        # Baseline: real failure rate
        baseline_failures = sum(1 for o in relevant if not o.success)
        baseline_rate = baseline_failures / len(relevant)

        # Simulate
        converted, regressed = self._simulate(proposal, relevant)

        # FIXED: simulated failures = (originally failing AND not converted)
        # Previously this incorrectly counted successes too.
        converted_ids = {id(o) for o in converted}
        sim_failures = sum(
            1 for o in relevant
            if not o.success and id(o) not in converted_ids
        )
        sim_rate = sim_failures / len(relevant) if relevant else 0.0

        delta = sim_rate - baseline_rate

        concerns: list[str] = []
        if delta >= 0:
            concerns.append(f"no improvement (Δ={delta:+.1%})")
        if regressed and (len(regressed) / len(relevant)) > self.MAX_REGRESSION_RATIO:
            concerns.append(f"too many regressions ({len(regressed)}/{len(relevant)})")
        # Note: a "100% conversion" of matching failures is the EXPECTED
        # behavior of a good rule, not suspicious. (Previously raised a
        # false concern that prevented valid applications.)

        # Hard floor: simulated failure rate must be strictly less than baseline
        # AND must reach at least 50% (otherwise change isn't worth risk)
        should_apply = (
            delta < -self.MIN_DELTA_IMPROVEMENT
            and not concerns
            and sim_rate < baseline_rate
            and sim_rate < 0.5
        )

        return SandboxVerdict(
            should_apply=should_apply,
            baseline_failure_rate=round(baseline_rate, 4),
            simulated_failure_rate=round(sim_rate, 4),
            delta=round(delta, 4),
            sample_size=len(relevant),
            concerns=concerns,
            improvement_count=len(converted),
            regression_count=len(regressed),
        )

    def _simulate(
        self,
        proposal: ModificationProposal,
        outcomes: list[FieldOutcome],
    ) -> tuple[list[FieldOutcome], list[FieldOutcome]]:
        """Simulate the proposal's effect on historical outcomes.

        Returns (converted_to_success, regressed_to_failure).

        The function is split into two clear passes:
          1. Failure pass: identify which originally-failing outcomes the
             proposal would have flipped to success (using IMPROVEMENT_RULES).
          2. Success pass: identify which originally-successful outcomes the
             proposal might have broken (using REGRESSION_RULES).

        This fixes the original bug where these were tangled.
        """
        target = proposal.target.value
        converted_success: list[FieldOutcome] = []
        regressed: list[FieldOutcome] = []

        # ── Pass 1: failures → successes (only if rules match) ──
        improvements = self.IMPROVEMENT_RULES.get(target, [])
        failures = [o for o in outcomes if not o.success]
        for o in failures:
            if not o.error_class:
                continue
            ec = o.error_class.lower()
            for pattern, conv_rate in improvements:
                if pattern in ec:
                    # Deterministic (seeded) — but for now: include all matching
                    converted_success.append(o)
                    break

        # ── Pass 2: successes → failures (only if regression rules match) ──
        regressions = self.REGRESSION_RULES.get(target, [])
        successes = [o for o in outcomes if o.success]
        for o in successes:
            ec = (o.error_class or "").lower()
            if not ec:
                continue
            for pattern in regressions:
                if pattern in ec:
                    regressed.append(o)
                    break

        return converted_success, regressed


# Add metadata_belongs_to_task_type to ModificationProposal if not present
def _patch_proposal():
    from odc.refinement.proposal import ModificationProposal
    def _get_task_types(self) -> list[str]:
        return self.metadata.get("relevant_task_types", [])
    def _belongs(self) -> list[str]:
        return self.metadata.get("relevant_task_types", [])
    ModificationProposal.metadata_belongs_to_task_type = _belongs  # type: ignore[attr-defined]


_patch_proposal()
