"""Training pipeline — curate dataset from ODC's black-box decisions.

Public API:
  - DatasetCurator: low-level curation (collect → filter → bipolar → export)
  - TrainingStore: facade for web/CLI (stats, export, start_finetune)
  - TrainingExample: dataclass for one chat-format training example
  - scrub: PII / secrets / paths scrubber
  - PatternStats: per-pattern aggregator with bipolar check

Safety gates (10 invariants applied):
  I1  framing preserved
  I2  evidence tier (only AUTHORITATIVE_API / DIRECT_OBSERVATION / CORROBORATED)
  I3  no auto-train for side-effecting decisions
  I4  audit source read-only
  I5  training pauses on excessive failures (circuit-breaker)
  I6  cannot modify the constitution
  I7  every checkpoint has rollback
  I8  only patterns past exhaustion gate
  I9  bipolar evidence (success AND failure present)
  I10 minimum sample size (≥5 per pattern)
"""
from odc.training.curator import (
    DatasetCurator,
    TrainingExample,
    PatternStats,
    scrub,
    MIN_SAMPLES_PER_PATTERN,
    MIN_BIPOLAR_RATIO,
)
from odc.training.store import TrainingStore

__all__ = [
    "DatasetCurator",
    "TrainingExample",
    "PatternStats",
    "scrub",
    "TrainingStore",
    "MIN_SAMPLES_PER_PATTERN",
    "MIN_BIPOLAR_RATIO",
]
