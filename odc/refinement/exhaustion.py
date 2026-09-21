"""Exhaustion gate — fires only when ALL current hypotheses have failed.

The user-corrected trigger condition: NOT after 1 failure, NOT after
success-rate dips, ONLY when every hypothesis in the active set has
empirically failed in field data with sufficient sample size.

Decision rules (from the corrected Level 9 design):

  1. Need ≥ MIN_FIELD_SAMPLES outcomes per task_type (not premature)
  2. Each hypothesis in the active set has attempts ≥ MIN_ATTEMPTS
  3. Each hypothesis has failure_rate ≥ EXHAUSTION_THRESHOLD (0.6 default)
  4. Failure pattern is consistent (not scattered / noisy)
  5. At least one hypothesis has a *common* error class (signal of root cause)
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from odc.refinement.field_data import FieldDataStore, HypothesisStats

log = logging.getLogger(__name__)


# Knobs — calibrated conservatively (won't fire prematurely)
MIN_FIELD_SAMPLES = 5          # need ≥ 5 outcomes for any claim
MIN_ATTEMPTS_PER_HYPOTHESIS = 3   # each hypothesis tried ≥ 3 times
EXHAUSTION_THRESHOLD = 0.60     # failure_rate ≥ 60% counts as "this approach failed"
PATTERN_CONSISTENCY = 0.50      # ≥ 50% of failures share an error class
MIN_ACTIVE_HYPOTHESES = 2       # need ≥ 2 hypotheses tried (otherwise just one bad attempt)


@dataclass
class ExhaustionVerdict:
    """Result of the exhaustion gate evaluation."""
    is_exhausted: bool
    task_type: str
    sample_count: int
    hypothesis_count: int
    reasons: list[str] = field(default_factory=list)
    failed_hypotheses: list[HypothesisStats] = field(default_factory=list)
    common_error_class: str = ""
    confidence: float = 0.0  # 0..1, how confident we are in exhaustion

    def explain(self) -> str:
        if self.is_exhausted:
            lines = [f"✓ EXHAUSTED: {self.task_type}"]
            lines.append(f"  - {self.hypothesis_count} hypotheses tried, all failed")
            lines.append(f"  - {self.sample_count} field samples")
            lines.append(f"  - common error: {self.common_error_class or '(mixed)'}")
            lines.append(f"  - confidence: {self.confidence:.0%}")
            lines.append("  reasons:")
            for r in self.reasons:
                lines.append(f"    • {r}")
            return "\n".join(lines)
        return f"✗ NOT exhausted ({self.task_type}): {'; '.join(self.reasons)}"


class ExhaustionGate:
    """Decides if all current hypotheses have failed (in field data)."""

    def __init__(
        self,
        store: FieldDataStore,
        *,
        min_field_samples: int = MIN_FIELD_SAMPLES,
        min_attempts: int = MIN_ATTEMPTS_PER_HYPOTHESIS,
        threshold: float = EXHAUSTION_THRESHOLD,
        pattern_consistency: float = PATTERN_CONSISTENCY,
        min_hypotheses: int = MIN_ACTIVE_HYPOTHESES,
    ):
        self.store = store
        self.min_field_samples = min_field_samples
        self.min_attempts = min_attempts
        self.threshold = threshold
        self.pattern_consistency = pattern_consistency
        self.min_hypotheses = min_hypotheses

    def evaluate(self, task_type: str) -> ExhaustionVerdict:
        """Check if all current hypotheses have failed in field data."""
        reasons: list[str] = []
        outcomes = self.store.query(task_type=task_type, limit=500)
        n_samples = len(outcomes)
        if n_samples < self.min_field_samples:
            return ExhaustionVerdict(
                is_exhausted=False, task_type=task_type,
                sample_count=n_samples, hypothesis_count=0,
                reasons=[f"only {n_samples} field samples (need {self.min_field_samples})"],
            )

        stats = self.store.stats_for(task_type=task_type, min_attempts=self.min_attempts)
        if len(stats) < self.min_hypotheses:
            return ExhaustionVerdict(
                is_exhausted=False, task_type=task_type,
                sample_count=n_samples, hypothesis_count=len(stats),
                reasons=[f"only {len(stats)} hypotheses tried (need {self.min_hypotheses})"],
            )

        # Each hypothesis must have failure_rate >= threshold
        failed = [s for s in stats if s.failure_rate >= self.threshold]
        if len(failed) != len(stats):
            working = [s for s in stats if s.failure_rate < self.threshold]
            return ExhaustionVerdict(
                is_exhausted=False, task_type=task_type,
                sample_count=n_samples, hypothesis_count=len(stats),
                reasons=[f"{len(working)} hypothesis(es) still working "
                         f"(e.g., {working[0].hypothesis_id} at "
                         f"{1 - working[0].failure_rate:.0%} success)"],
            )

        # Failure pattern consistency check — based on ACTUAL outcome error classes,
        # not the per-hypothesis aggregate (which loses information when each
        # hypothesis sees a mix).
        failure_outcomes = self.store.query(task_type=task_type, failure_only=True, limit=500)
        ec_counter = Counter(o.error_class for o in failure_outcomes if o.error_class)
        total_failures = sum(ec_counter.values())
        common_class, common_count = "", 0
        if ec_counter:
            common_class, common_count = ec_counter.most_common(1)[0]
        consistency = common_count / total_failures if total_failures else 0.0
        if consistency < self.pattern_consistency:
            return ExhaustionVerdict(
                is_exhausted=False, task_type=task_type,
                sample_count=n_samples, hypothesis_count=len(stats),
                reasons=[f"failures scattered across error classes "
                         f"(consistency {consistency:.0%} < {self.pattern_consistency:.0%})"],
                failed_hypotheses=failed,
            )

        # Compute confidence based on sample size + consistency + coverage
        sample_factor = min(1.0, n_samples / (self.min_field_samples * 4))
        coverage_factor = min(1.0, len(failed) / 3.0)
        confidence = round(
            sample_factor * 0.4 + consistency * 0.4 + coverage_factor * 0.2,
            3,
        )

        reasons.extend([
            f"tried {len(stats)} hypotheses, each with {self.min_attempts}+ attempts",
            f"all failed (failure_rate ≥ {self.threshold:.0%})",
            f"failure pattern consistent ({consistency:.0%} share class '{common_class}')",
        ])

        return ExhaustionVerdict(
            is_exhausted=True, task_type=task_type,
            sample_count=n_samples, hypothesis_count=len(stats),
            reasons=reasons, failed_hypotheses=failed,
            common_error_class=common_class, confidence=confidence,
        )
