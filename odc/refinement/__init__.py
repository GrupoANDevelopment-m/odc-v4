"""ODC v4 — Level 9: Adaptive Self-Refinement (field-grounded).

The agent refines its own architecture. NOT blind optimization. It only
acts when ALL current hypotheses have empirically failed in field data,
based on accumulated experience, and never touches the constitutional
core.

The 5 sub-systems, in order:

  1. FieldDataStore        — persistent empirical record (SQLite)
  2. ExhaustionGate        — fires only when ALL hypotheses exhausted
  3. ModificationProposal  — generated with traceable justification
  4. SandboxVerifier       — test on historical tasks before applying
  5. ConstitutionalGuard   — rejects anything touching the core

Plus:
  - SelfRefinementEngine   — orchestrator (evaluate → propose → verify
                              → guard → apply with rollback)
  - JournalEntry           — every step is auditable
"""
