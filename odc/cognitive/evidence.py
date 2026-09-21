"""6-tier Evidence Taxonomy — epistemic discipline for stored knowledge.

Borrowed from asuramaya/Osiris (Section "The Evidence Taxonomy"). Each
piece of knowledge the agent stores or recalls carries an explicit
confidence band tied to the *kind* of evidence, not just a freeform
number. This prevents three failure modes:

  1. Treating "I think so" the same as "I just observed it"
  2. Overweighting old knowledge (recency isn't enough — provenance is)
  3. Hallucinated confidence (numeric 0.95 with no real backing)

The 6 tiers:

  SELF_DECLARED         1.00  — Direct first-party statement by agent
                                 ("I am cortana", "I prefer httpx")
  AUTHORITATIVE_API     0.95  — Verified response from canonical API
                                 (WHOIS, NVD, CourtListener, OpenSanctions)
  DIRECT_OBSERVATION    0.90  — Observed runtime telemetry / transcript
                                 (tool returned this; we used it)
  CORROBORATED          0.85  — Multi-source independent agreement
                                 (3 sources say the same thing)
  CO_OCCURRENCE         0.50  — Statistical co-occurrence / proximity
                                 (often appears together)
  DERIVED               0.40  — Inferred background / backfill
                                 (transcript mining, automated inference)

Final confidence is tier × success_rate (Bayesian-ish weighting). The
tier sets the *prior*; success/failure counts shift it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvidenceTier(str, Enum):
    """The 6 tiers, ordered from highest to lowest prior confidence."""
    SELF_DECLARED = "SELF_DECLARED"
    AUTHORITATIVE_API = "AUTHORITATIVE_API"
    DIRECT_OBSERVATION = "DIRECT_OBSERVATION"
    CORROBORATED = "CORROBORATED"
    CO_OCCURRENCE = "CO_OCCURRENCE"
    DERIVED = "DERIVED"


# Tier → numeric prior. Frozen: changing these values invalidates
# every stored confidence.
TIER_PRIOR: dict[EvidenceTier, float] = {
    EvidenceTier.SELF_DECLARED:      1.00,
    EvidenceTier.AUTHORITATIVE_API:  0.95,
    EvidenceTier.DIRECT_OBSERVATION: 0.90,
    EvidenceTier.CORROBORATED:       0.85,
    EvidenceTier.CO_OCCURRENCE:      0.50,
    EvidenceTier.DERIVED:            0.40,
}


@dataclass
class EvidencedClaim:
    """A claim with explicit evidence tier and empirical counts.

    confidence = prior(tier) × (success / (success + failure + alpha))
    where alpha is a smoothing factor (Laplace) so a single observation
    doesn't dominate.
    """
    claim: str
    tier: EvidenceTier
    success_count: int = 0
    failure_count: int = 0
    source: str = ""  # optional provenance pointer
    meta: dict[str, Any] | None = None

    @property
    def prior(self) -> float:
        return TIER_PRIOR[self.tier]

    def confidence(self, alpha: float = 1.0) -> float:
        """Final confidence = prior × smoothed empirical rate.

        alpha=1 is Laplace smoothing (uniform prior over outcomes).
        """
        s, f = self.success_count, self.failure_count
        rate = (s + alpha) / (s + f + 2 * alpha)
        return round(self.prior * rate, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "tier": self.tier.value,
            "prior": self.prior,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence": self.confidence(),
            "source": self.source,
            "meta": self.meta or {},
        }


# ──────────────────────────────────────────────────────────────────
# Inference: pick the most likely tier for a context
# ──────────────────────────────────────────────────────────────────
_TOOL_RESULT_TIERS: dict[str, EvidenceTier] = {
    # Tools that hit canonical APIs → AUTHORITATIVE_API
    "code.read":      EvidenceTier.DIRECT_OBSERVATION,
    "code.glob":      EvidenceTier.DIRECT_OBSERVATION,
    "code.grep":      EvidenceTier.DIRECT_OBSERVATION,
    "osint.cve":      EvidenceTier.AUTHORITATIVE_API,
    "osint.bitcoin":  EvidenceTier.AUTHORITATIVE_API,
    "osint.flights":  EvidenceTier.AUTHORITATIVE_API,
    "osint.earthquakes": EvidenceTier.AUTHORITATIVE_API,
    "osint.crypto_prices": EvidenceTier.AUTHORITATIVE_API,
    "osint.space_weather": EvidenceTier.AUTHORITATIVE_API,
    "osint.weather":  EvidenceTier.AUTHORITATIVE_API,
    "osint.wikipedia": EvidenceTier.CORROBORATED,  # editable but canonical
    "web.fetch":      EvidenceTier.DIRECT_OBSERVATION,
    "knowledge.search": EvidenceTier.CORROBORATED,
    "cognitive.route": EvidenceTier.DERIVED,
    "cognitive.reflect": EvidenceTier.DERIVED,
}


def infer_tier(source: str, context: dict[str, Any] | None = None) -> EvidenceTier:
    """Infer the appropriate Evidence Tier for a claim based on source.

    `source` is a tool name, an API endpoint, or a free-form descriptor.
    """
    s = (source or "").lower()
    ctx = context or {}

    # Explicit override wins
    if "tier" in ctx:
        try:
            return EvidenceTier(ctx["tier"])
        except ValueError:
            pass

    # Tool result tier lookup
    if s in _TOOL_RESULT_TIERS:
        return _TOOL_RESULT_TIERS[s]

    # Heuristic keyword sniffing
    if any(k in s for k in ("api", "official", "authoritative", "canon")):
        return EvidenceTier.AUTHORITATIVE_API
    if any(k in s for k in ("observed", "transcript", "telemetry", "ran", "executed")):
        return EvidenceTier.DIRECT_OBSERVATION
    if any(k in s for k in ("wikipedia", "kb", "knowledge_base", "consensus")):
        return EvidenceTier.CORROBORATED
    if any(k in s for k in ("near", "co-occur", "proximity", "vector")):
        return EvidenceTier.CO_OCCURRENCE
    if any(k in s for k in ("inferred", "derived", "backfill", "guessed")):
        return EvidenceTier.DERIVED

    # Default: SELF_DECLARED for identity-like, DERIVED otherwise
    if ctx.get("is_identity"):
        return EvidenceTier.SELF_DECLARED
    return EvidenceTier.DERIVED


# ──────────────────────────────────────────────────────────────────
# Calibration: reweight a list of claims by tier
# ──────────────────────────────────────────────────────────────────
def weighted_score(claims: list[EvidencedClaim]) -> float:
    """Average confidence across claims, weighted by tier prior.

    Higher-prior claims have more influence on the final score.
    """
    if not claims:
        return 0.0
    num = sum(c.confidence() * c.prior for c in claims)
    den = sum(c.prior for c in claims)
    return round(num / den, 4) if den else 0.0


def rank_claims(claims: list[EvidencedClaim]) -> list[EvidencedClaim]:
    """Sort by confidence × prior (so high-tier claims surface first)."""
    return sorted(claims, key=lambda c: (c.confidence(), c.prior), reverse=True)


# ──────────────────────────────────────────────────────────────────
# Conflict resolution: when two claims disagree
# ──────────────────────────────────────────────────────────────────
def resolve_conflict(
    claims: list[EvidencedClaim],
    *,
    threshold: float = 0.10,
) -> EvidencedClaim | None:
    """Pick the winning claim among conflicting ones.

    Uses confidence as primary, tier as tiebreaker. Returns None if no
    claim dominates by ≥ threshold.
    """
    if not claims:
        return None
    ranked = rank_claims(claims)
    if len(ranked) == 1:
        return ranked[0]
    top = ranked[0]
    second = ranked[1]
    if top.confidence() - second.confidence() >= threshold:
        return top
    return None  # ambiguous — escalate to system 2
