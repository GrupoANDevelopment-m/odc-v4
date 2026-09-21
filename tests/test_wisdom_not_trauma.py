"""Test the v2 'wisdom not trauma' learning: failure investigations,
heuristic versioning, anti-pattern re-validation.

The agent must NOT be afraid of failures — it must investigate, learn,
and try again with mitigations.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest

from odc.cognitive.profile import CognitiveProfile
from odc.cognitive.tools import (
    cognitive_distill,
    cognitive_investigate,
)


# ---------------------------------------------------------------------------
# Failure investigations
# ---------------------------------------------------------------------------


async def test_investigate_records_why_and_mitigations(tmp_path: Path):
    from odc.cognitive import tools as cogtools
    cogtools.set_paths(tmp_path)
    cogtools._PROFILE = None
    try:
        r = await cognitive_investigate(
            tool="web.search",
            why="DDGS library blocked from datacenter IP",
            next_approach="Use DDG Instant Answer API via httpx",
            mitigations=[
                "Try Instant Answer first",
                "Fall back to HTML scrape",
                "Use web.fetch with known URL",
            ],
        )
        out = r.output if hasattr(r, "output") else r
        assert out.get("ok") is True
        prof = CognitiveProfile(tmp_path / "profile.json")
        invs = prof.data["failure_investigations"]
        assert len(invs) == 1
        assert invs[0]["tool"] == "web.search"
        assert "datacenter" in invs[0]["why"]
        assert len(invs[0]["mitigations"]) == 3
        mits = prof.get_mitigations_for("web.search")
        assert "Try Instant Answer first" in mits
    finally:
        cogtools._PROFILE = None


async def test_investigation_dedup_on_same_tool_and_why(tmp_path: Path):
    from odc.cognitive import tools as cogtools
    cogtools.set_paths(tmp_path)
    cogtools._PROFILE = None
    try:
        for _ in range(3):
            await cognitive_investigate(
                tool="web.search",
                why="DDGS blocked",
                next_approach="Use API",
                mitigations=["x"],
            )
        prof = CognitiveProfile(tmp_path / "profile.json")
        invs = prof.data["failure_investigations"]
        assert len(invs) == 1
        assert invs[0]["occurrences"] == 3
    finally:
        cogtools._PROFILE = None


async def test_distill_creates_heuristic_with_distilled_from(tmp_path: Path):
    from odc.cognitive import tools as cogtools
    cogtools.set_paths(tmp_path)
    cogtools._PROFILE = None
    try:
        r = await cognitive_distill(
            skill_name="obstacle-breaker",
            distilled_rule="When a tool is missing, build it with dynamic.tool_create",
        )
        out = r.output if hasattr(r, "output") else r
        assert out.get("ok") is True
        prof = CognitiveProfile(tmp_path / "profile.json")
        hs = prof.data["heuristics"]
        assert len(hs) == 1
        assert hs[0]["source"] == "distilled:obstacle-breaker"
        assert hs[0]["distilled_from"] == "obstacle-breaker"
        assert "dynamic.tool_create" in hs[0]["rule"]
        assert "version" in hs[0]
        assert "success_count" in hs[0]
        assert "last_validated" in hs[0]
    finally:
        cogtools._PROFILE = None


# ---------------------------------------------------------------------------
# Wisdom: anti-pattern with false_positive deprecation
# ---------------------------------------------------------------------------


def test_anti_pattern_deprecates_after_too_many_false_positives(tmp_path: Path):
    p = tmp_path / "profile.json"
    p.unlink(missing_ok=True)
    prof = CognitiveProfile(p)
    prof.add_anti_pattern("Avoid web.search in sandbox")
    # Once: 1 occurrence
    ap = prof.data["anti_patterns"][0]
    assert ap["occurrences"] == 1
    assert ap["false_positives"] == 0
    assert not ap["deprecated"]
    # Bump false_positives beyond occurrences → deprecate
    prof.record_anti_pattern_false_positive("Avoid web.search in sandbox")
    prof.record_anti_pattern_false_positive("Avoid web.search in sandbox")
    ap = prof.data["anti_patterns"][0]
    assert ap["false_positives"] == 2
    assert ap["occurrences"] == 1
    assert ap["deprecated"] is True  # FPs > occurrences


def test_get_relevant_anti_patterns_skips_deprecated_and_stale(tmp_path: Path):
    p = tmp_path / "profile.json"
    p.unlink(missing_ok=True)
    prof = CognitiveProfile(p)
    prof.add_anti_pattern("Avoid the broken tool", context="test")
    # Make it stale (older than 30 days)
    prof.data["anti_patterns"][0]["last_validated"] = time.time() - 40 * 86400
    prof.save()
    prof2 = CognitiveProfile(p)
    rel = prof2.get_relevant_anti_patterns("use the broken tool", max_age_days=30)
    assert rel == []  # stale → not surfaced (wisdom, not trauma)
    # But with a wider window, it surfaces
    rel = prof2.get_relevant_anti_patterns("use the broken tool", max_age_days=60)
    assert len(rel) == 1


# ---------------------------------------------------------------------------
# Wisdom: heuristic success/failure tracking for re-evaluation
# ---------------------------------------------------------------------------


def test_heuristic_outcome_tracking(tmp_path: Path):
    p = tmp_path / "profile.json"
    p.unlink(missing_ok=True)
    prof = CognitiveProfile(p)
    prof.add_heuristic("For hash tasks, build the tool first")
    for _ in range(10):
        prof.record_heuristic_outcome("For hash tasks, build the tool first", success=True)
    prof.record_heuristic_outcome("For hash tasks, build the tool first", success=False)
    h = prof.data["heuristics"][0]
    assert h["hits"] == 11
    assert h["success_count"] == 10
    assert h["failure_count"] == 1
    # Agent system can read these to decide: is this heuristic reliable?


# ---------------------------------------------------------------------------
# Wisdom: investigations expose mitigations, not just "don't"
# ---------------------------------------------------------------------------


def test_mitigations_are_actionable_suggestions(tmp_path: Path):
    """The point of an investigation is that it suggests a NEXT ACTION,
    not just a prohibition. Verify this in the data shape."""
    p = tmp_path / "profile.json"
    p.unlink(missing_ok=True)
    prof = CognitiveProfile(p)
    prof.add_failure_investigation(
        tool="shell.run",
        why="binary 'git' not in allowlist",
        next_approach="build a dynamic tool wrapping the subprocess call",
        mitigations=[
            "use dynamic.tool_create with @tool wrapping subprocess.run",
            "or add 'git' to ODC_SHELL_ALLOWLIST env",
            "or use code.run with python: prefix",
        ],
    )
    inv = prof.data["failure_investigations"][0]
    # Wisdom, not trauma: the record says what to DO, not what to avoid
    assert "next_approach" in inv
    assert len(inv["mitigations"]) >= 2
    # The mitigations are concrete actions
    for m in inv["mitigations"]:
        assert len(m) > 5


# ---------------------------------------------------------------------------
# end-to-end: real NVIDIA test
# ---------------------------------------------------------------------------


async def test_real_nvidia_distill_and_investigate(tmp_path: Path):
    """Real test against the LLM. The distill/investigate tools are
    mechanical (no LLM) so this just exercises the full path."""
    from odc.cognitive import tools as cogtools
    cogtools.set_paths(tmp_path)
    cogtools._PROFILE = None
    try:
        r1 = await cognitive_distill(
            skill_name="method",
            distilled_rule="Plan, then act, then verify; do not skip verify",
        )
        out1 = r1.output if hasattr(r1, "output") else r1
        assert out1.get("ok") is True
        r2 = await cognitive_investigate(
            tool="web.search",
            why="datacenter blocks DDGS; tested 3 endpoints, all return 0",
            next_approach="Use DDG Instant Answer API as primary; only fall back if 202s",
            mitigations=[
                "Try Instant Answer first via httpx",
                "Fall back to html.duckduckgo.com scrape",
                "Use web.fetch with known URL as last resort",
            ],
        )
        out2 = r2.output if hasattr(r2, "output") else r2
        assert out2.get("ok") is True
        prof = CognitiveProfile(tmp_path / "profile.json")
        assert len(prof.data["heuristics"]) >= 1
        assert len(prof.data["failure_investigations"]) >= 1
    finally:
        cogtools._PROFILE = None
