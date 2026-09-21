"""Tests for the 6-tier Evidence Taxonomy."""
import pytest

from odc.cognitive.evidence import (
    EvidenceTier, TIER_PRIOR, EvidencedClaim, infer_tier,
    weighted_score, rank_claims, resolve_conflict,
)


# ──────────────────────────────────────────────────────────────────
# Tier priors
# ──────────────────────────────────────────────────────────────────
def test_tier_priors_in_expected_order():
    priors = [TIER_PRIOR[t] for t in EvidenceTier]
    assert priors == sorted(priors, reverse=True), "tiers should be ordered"


def test_all_six_tiers_present():
    assert len(EvidenceTier) == 6
    assert EvidenceTier("SELF_DECLARED")
    assert EvidenceTier("AUTHORITATIVE_API")
    assert EvidenceTier("DIRECT_OBSERVATION")
    assert EvidenceTier("CORROBORATED")
    assert EvidenceTier("CO_OCCURRENCE")
    assert EvidenceTier("DERIVED")


# ──────────────────────────────────────────────────────────────────
# EvidencedClaim confidence formula
# ──────────────────────────────────────────────────────────────────
def test_confidence_with_no_evidence_is_prior_times_smoothing():
    c = EvidencedClaim(claim="x", tier=EvidenceTier.DIRECT_OBSERVATION)
    # alpha=1: rate = (0+1)/(0+0+2) = 0.5, prior = 0.90, conf = 0.45
    assert c.confidence() == 0.45


def test_confidence_improves_with_successes():
    c1 = EvidencedClaim(claim="x", tier=EvidenceTier.DIRECT_OBSERVATION,
                        success_count=10, failure_count=0)
    c2 = EvidencedClaim(claim="x", tier=EvidenceTier.DIRECT_OBSERVATION,
                        success_count=1, failure_count=1)
    assert c1.confidence() > c2.confidence()


def test_confidence_capped_by_prior():
    """Even with infinite successes, can't exceed tier prior."""
    c = EvidencedClaim(claim="x", tier=EvidenceTier.CO_OCCURRENCE,
                       success_count=10000, failure_count=0)
    assert c.confidence() <= TIER_PRIOR[EvidenceTier.CO_OCCURRENCE]


def test_self_declared_has_higher_confidence_cap_than_derived():
    sd = EvidencedClaim(claim="x", tier=EvidenceTier.SELF_DECLARED,
                        success_count=100, failure_count=0)
    dv = EvidencedClaim(claim="x", tier=EvidenceTier.DERIVED,
                        success_count=100, failure_count=0)
    assert sd.confidence() > dv.confidence()


# ──────────────────────────────────────────────────────────────────
# Tier inference
# ──────────────────────────────────────────────────────────────────
def test_infer_tier_for_osint_tools():
    assert infer_tier("osint.cve") == EvidenceTier.AUTHORITATIVE_API
    assert infer_tier("osint.bitcoin") == EvidenceTier.AUTHORITATIVE_API
    assert infer_tier("osint.flights") == EvidenceTier.AUTHORITATIVE_API


def test_infer_tier_for_code_tools():
    assert infer_tier("code.read") == EvidenceTier.DIRECT_OBSERVATION
    assert infer_tier("code.grep") == EvidenceTier.DIRECT_OBSERVATION


def test_infer_tier_for_kb():
    assert infer_tier("knowledge.search") == EvidenceTier.CORROBORATED
    assert infer_tier("wikipedia") == EvidenceTier.CORROBORATED


def test_infer_tier_keyword_sniffing():
    assert infer_tier("official_api_response") == EvidenceTier.AUTHORITATIVE_API
    assert infer_tier("observed_in_transcript") == EvidenceTier.DIRECT_OBSERVATION
    assert infer_tier("inferred_from_history") == EvidenceTier.DERIVED


def test_infer_tier_explicit_override():
    assert infer_tier("anything", {"tier": "AUTHORITATIVE_API"}) == EvidenceTier.AUTHORITATIVE_API


def test_infer_tier_default_is_derived():
    assert infer_tier("unknown_source") == EvidenceTier.DERIVED


def test_infer_tier_identity_context_is_self_declared():
    assert infer_tier("preference", {"is_identity": True}) == EvidenceTier.SELF_DECLARED


# ──────────────────────────────────────────────────────────────────
# Weighted scoring
# ──────────────────────────────────────────────────────────────────
def test_weighted_score_empty_returns_zero():
    assert weighted_score([]) == 0.0


def test_weighted_score_higher_tier_dominates():
    claims = [
        EvidencedClaim("a", EvidenceTier.DERIVED, success_count=10, failure_count=0),
        EvidencedClaim("b", EvidenceTier.SELF_DECLARED, success_count=1, failure_count=0),
    ]
    score = weighted_score(claims)
    # Self-declared (prior 1.0, conf ~0.5) should pull score up despite low success
    assert score > 0.4


# ──────────────────────────────────────────────────────────────────
# Ranking
# ──────────────────────────────────────────────────────────────────
def test_rank_claims_orders_by_confidence_then_prior():
    claims = [
        EvidencedClaim("low tier high succ", EvidenceTier.CO_OCCURRENCE, success_count=20, failure_count=0),
        EvidencedClaim("high tier low succ", EvidenceTier.SELF_DECLARED, success_count=1, failure_count=0),
    ]
    ranked = rank_claims(claims)
    assert ranked[0].claim == "high tier low succ"


# ──────────────────────────────────────────────────────────────────
# Conflict resolution
# ──────────────────────────────────────────────────────────────────
def test_resolve_conflict_clear_winner():
    winner = EvidencedClaim("yes", EvidenceTier.AUTHORITATIVE_API, success_count=5, failure_count=0)
    loser = EvidencedClaim("no", EvidenceTier.DERIVED, success_count=5, failure_count=0)
    assert resolve_conflict([loser, winner]) is winner


def test_resolve_conflict_returns_none_when_ambiguous():
    a = EvidencedClaim("a", EvidenceTier.DIRECT_OBSERVATION, success_count=5, failure_count=0)
    b = EvidencedClaim("b", EvidenceTier.DIRECT_OBSERVATION, success_count=5, failure_count=0)
    assert resolve_conflict([a, b]) is None


def test_resolve_conflict_single_returns_it():
    only = EvidencedClaim("alone", EvidenceTier.DERIVED)
    assert resolve_conflict([only]) is only


def test_resolve_conflict_empty_returns_none():
    assert resolve_conflict([]) is None
