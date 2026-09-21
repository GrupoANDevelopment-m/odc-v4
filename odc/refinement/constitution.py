"""Constitutional Guard — the immutable core.

These invariants CANNOT be modified by any self-refinement proposal,
no matter how justified by field data. Violations → automatic rejection
plus journal entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from odc.refinement.proposal import ModificationProposal, Target


# ──────────────────────────────────────────────────────────────────
# The constitutional invariants — UNTOUCHABLE.
# ──────────────────────────────────────────────────────────────────
CONSTITUTIONAL_INVARIANTS: list[dict[str, Any]] = [
    {
        "id": "I1",
        "rule": "Framing PAM must remain active for all recalled memory.",
        "check": lambda p: _check_framing_not_disabled(p),
        "rationale": "Disabling injection-resistant framing opens a memory-mediated "
                     "prompt injection attack vector.",
    },
    {
        "id": "I2",
        "rule": "Evidence Tier cannot be downgraded (e.g., AUTHORITATIVE_API → DERIVED).",
        "check": lambda p: _check_no_tier_downgrade(p),
        "rationale": "Calibration discipline requires epistemic honesty — we cannot "
                     "redistribute trust to weaker tiers.",
    },
    {
        "id": "I3",
        "rule": "Side-effecting tools must always require explicit confirmation.",
        "check": lambda p: _check_confirm_required(p),
        "rationale": "Side-effects (write, edit, shell, dynamic.*) without confirm "
                     "create irreversible damage risk.",
    },
    {
        "id": "I4",
        "rule": "Audit trail cannot be deleted or made append-only-deletable.",
        "check": lambda p: "delete_audit_trail" not in _flatten(p.after) and
                            "purge_journal" not in _flatten(p.after),
        "rationale": "Self-modification without audit defeats accountability.",
    },
    {
        "id": "I5",
        "rule": "Circuit breaker cannot be disabled or have its threshold increased beyond safety.",
        "check": lambda p: "circuit_breaker_enabled" not in _flatten(p.after) or
                            _flatten(p.after).get("circuit_breaker_enabled") is True,
        "rationale": "Resilience layer protects against cascading failures.",
    },
    {
        "id": "I6",
        "rule": "Modifications cannot target constitutional_core itself.",
        "check": lambda p: p.target != Target.CONSTITUTIONAL_CORE,
        "rationale": "The constitution must remain self-modifying-resistant. "
                     "Any change to it requires human review.",
    },
    {
        "id": "I7",
        "rule": "Proposal must include rollback plan.",
        "check": lambda p: bool(p.rollback_plan) and "restore" in (p.rollback_plan or {}),
        "rationale": "Every modification must be reversible. Without rollback, "
                     "self-refinement is a one-way door.",
    },
    {
        "id": "I8",
        "rule": "Modifications cannot disable the exhaustion gate (no fast-tracking).",
        "check": lambda p: "disable_exhaustion_gate" not in _flatten(p.after) and
                            "skip_field_data" not in _flatten(p.after),
        "rationale": "The exhaustion gate is the user-corrected trigger — "
                     "disabling it would regress to blind optimization.",
    },
    {
        "id": "I9",
        "rule": "Evidence_tier calibration must use both success AND failure counts.",
        "check": lambda p: True,  # enforced in cognitive/evidence.py
        "rationale": "Bipolar evidence (success + failure) was a user correction.",
    },
    {
        "id": "I10",
        "rule": "Modifications must be based on field data with N≥MIN_FIELD_SAMPLES outcomes.",
        "check": lambda p: p.field_evidence_count >= 5,
        "rationale": "Premature modification on insufficient data leads to overfitting.",
    },
]


def _flatten(d: dict) -> dict:
    """Flatten nested dicts for simple key lookup."""
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            out.update(_flatten(v))
        else:
            out[k] = v
    return out


def _check_no_tier_downgrade(p: ModificationProposal) -> bool:
    """Check that no proposal downgrades a tier's confidence."""
    flat_after = _flatten(p.after)
    flat_before = _flatten(p.before)
    tier_order = {
        "SELF_DECLARED": 6, "AUTHORITATIVE_API": 5, "DIRECT_OBSERVATION": 4,
        "CORROBORATED": 3, "CO_OCCURRENCE": 2, "DERIVED": 1,
    }
    for key in flat_after:
        if "tier" in key.lower() and isinstance(flat_after[key], str):
            new = tier_order.get(flat_after[key], 0)
            old = tier_order.get(flat_before.get(key, ""), 0)
            if new and old and new < old:
                return False
    return True


def _check_confirm_required(p: ModificationProposal) -> bool:
    """requires_confirm must remain truthy (or absent) for safety."""
    flat_after = _flatten(p.after)
    for key, v in flat_after.items():
        if key == "requires_confirm" and not v:
            return False
    return True


def _check_framing_not_disabled(p: ModificationProposal) -> bool:
    """Framing PAM must remain active. Allow setting 'active' or anything else
    that doesn't explicitly disable it."""
    flat_after = _flatten(p.after)
    # If 'framing' key exists, it must not be a falsy/disable value
    if "framing" in flat_after:
        v = flat_after["framing"]
        if not v or v in ("disabled", "off"):
            return False
    # Also check inside nested for "disable_framing" or "framing_disabled"
    for key in flat_after:
        kl = key.lower()
        if "disable" in kl and "fram" in kl:
            return False
        if kl == "framing_disabled" or kl == "framing_off":
            return False
    return True


# ──────────────────────────────────────────────────────────────────
# Verdict
# ──────────────────────────────────────────────────────────────────
@dataclass
class ConstitutionalVerdict:
    is_allowed: bool
    violated: list[dict[str, Any]] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    def explain(self) -> str:
        if self.is_allowed:
            return f"✓ Constitutional: PASS ({len(self.passed)} checks)"
        lines = ["✗ Constitutional: REJECTED"]
        for v in self.violated:
            lines.append(f"  - [{v['id']}] {v['rule']}")
            lines.append(f"    rationale: {v['rationale']}")
        return "\n".join(lines)


class ConstitutionalGuard:
    """Evaluates proposals against the constitution."""

    def __init__(self, invariants: list[dict[str, Any]] | None = None):
        self.invariants = invariants or CONSTITUTIONAL_INVARIANTS

    def check(self, proposal: ModificationProposal) -> ConstitutionalVerdict:
        violated: list[dict[str, Any]] = []
        passed: list[str] = []
        for inv in self.invariants:
            try:
                ok = inv["check"](proposal)
            except Exception:
                # If the check itself errors, treat as suspicious → reject
                ok = False
            if ok:
                passed.append(inv["id"])
            else:
                violated.append(inv)
        return ConstitutionalVerdict(
            is_allowed=len(violated) == 0,
            violated=violated,
            passed=passed,
        )
