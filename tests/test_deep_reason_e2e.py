"""End-to-end REAL tests for DEEP-REASON — runs against the live LLM.

Tests the System 2 cascade (council → revise → investigate → analogy)
in real conditions. The point is to verify:
  1. The council tool actually invokes the LLM and returns 5 perspectives
  2. Revise returns falsification criteria + confidence
  3. Analogy returns a real-world pattern
  4. Hypothesize_v2 returns a tree of competing hypotheses
  5. Knowledge base can store and recall facts
  6. The auto-trigger fires when System 1 fails
"""
import asyncio
import json
import shutil
from pathlib import Path

import pytest

from odc.cognitive.deep_reason_tools import (
    cognitive_analogy,
    cognitive_council,
    cognitive_decompose,
    cognitive_hypothesize_v2,
    cognitive_investigate_deep,
    cognitive_revise,
)
from odc.cognitive.knowledge_tool import (
    knowledge_add,
    knowledge_list,
    knowledge_search,
)
from odc.cognitive.tools import LLM_PROVIDER, get_paths, set_paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _have_provider() -> bool:
    return LLM_PROVIDER is not None


def _setup_kb_paths(tmp_path: Path) -> None:
    """Point the cognitive tools at this tmp dir so knowledge.* writes there."""
    cog_dir = tmp_path / "cognitive"
    cog_dir.mkdir(exist_ok=True)
    set_paths(cog_dir)


def _unwrap(result) -> dict:
    """Cognitive tools return ToolResult (with .output). Tests want the dict."""
    if hasattr(result, "output"):
        out = result.output
        if isinstance(out, dict):
            return out
        return {"raw": str(out)[:4000]}
    if isinstance(result, dict):
        return result
    return {"raw": str(result)[:4000]}


# ---------------------------------------------------------------------------
# cognitive.council — real LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_council_returns_5_perspectives(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    _setup_kb_paths(tmp_path)
    raw = await cognitive_council(
        problem="I need to POST JSON to httpbin.org/post but curl is not in the shell allowlist and web.fetch is GET-only",
        context="Already tried shell.run with curl (allowlist error), web.fetch with method=POST (unknown param), and dynamic.tool_create hit verify_hard_cap. 3 failures.",
    )
    r = _unwrap(raw)
    print(f"\n[council] synthesis: {(r.get('synthesis') or '')[:200]}")
    assert "perspectives" in r, f"no perspectives: {r}"
    persp = r["perspectives"]
    # Should have at least 3 of the 5 lenses
    n_lenses = sum(1 for k in ("expert", "hacker", "researcher", "developer", "investigator") if persp.get(k))
    assert n_lenses >= 3, f"only {n_lenses} lenses: {persp}"
    assert r.get("synthesis"), "no synthesis"


# ---------------------------------------------------------------------------
# cognitive.revise — epistemic humility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_revise_returns_falsification(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    _setup_kb_paths(tmp_path)
    raw = await cognitive_revise(
        current_hypothesis="web.search is broken in this sandbox",
        evidence="Got 0 results for 5 different queries",
        failures="DDGS library is blocked from datacenter IPs",
    )
    r = _unwrap(raw)
    print(f"\n[revise] new_confidence: {r.get('new_confidence')}, should_change: {r.get('should_change_approach')}")
    assert r.get("new_confidence") is not None
    assert isinstance(r.get("blind_spots"), list)
    assert isinstance(r.get("falsification_criteria"), list)
    assert r.get("should_change_approach") in (True, False)


# ---------------------------------------------------------------------------
# cognitive.analogy — real-world pattern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_analogy_returns_named_pattern(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    _setup_kb_paths(tmp_path)
    raw = await cognitive_analogy(
        problem="Agent needs to perform an action that no existing tool supports, and creating a new tool is hitting the safety check timeout",
        failures="dynamic.tool_create failed twice, fallback to shell.run blocked by allowlist",
    )
    r = _unwrap(raw)
    print(f"\n[analogy] analogs: {len(r.get('analogs', []))}, best: {r.get('best_analog')}")
    assert r.get("analogs"), f"no analogs: {r}"
    a = r["analogs"][0]
    assert a.get("name"), "analog has no name"
    assert a.get("tactic"), "analog has no tactic"
    print(f"[analogy] first: {a['name']} -> {a['tactic'][:120]}")


# ---------------------------------------------------------------------------
# cognitive.hypothesize_v2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_hypothesize_v2_returns_tree(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    _setup_kb_paths(tmp_path)
    raw = await cognitive_hypothesize_v2(
        problem="How to POST JSON when no POST tool exists and shell is restricted",
        context="web.fetch GET only. shell.run allowlist rejects curl. dynamic.tool_create hit safety timeout.",
        n=3,
    )
    r = _unwrap(raw)
    print(f"\n[hyp] n: {len(r.get('hypotheses', []))}, best: {r.get('best_initial_test')}")
    assert len(r.get("hypotheses", [])) == 3, f"got {len(r.get('hypotheses', []))}: {r}"
    for h in r["hypotheses"]:
        assert h.get("hypothesis"), "hypothesis missing claim"
        assert h.get("test"), "hypothesis missing test"
        assert "tool" in h["test"]


# ---------------------------------------------------------------------------
# cognitive.decompose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_decompose_returns_dag(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    _setup_kb_paths(tmp_path)
    raw = await cognitive_decompose(
        task="Build a tool that posts JSON to httpbin.org/post, register it, and test it end-to-end",
    )
    r = _unwrap(raw)
    print(f"\n[decompose] sub_tasks: {len(r.get('sub_tasks', []))}, order: {r.get('execution_order')}")
    assert r.get("sub_tasks"), f"no sub-tasks: {r}"
    assert len(r["sub_tasks"]) >= 2
    if r.get("validation_error"):
        pytest.fail(f"invalid DAG: {r['validation_error']}")
    for s in r["sub_tasks"]:
        assert s.get("acceptance"), f"sub-task {s['id']} missing acceptance"


# ---------------------------------------------------------------------------
# cognitive.investigate_deep — research plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_investigate_deep_returns_plan(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    _setup_kb_paths(tmp_path)
    raw = await cognitive_investigate_deep(
        query="how to POST JSON in python httpx",
        max_attempts=4,
    )
    r = _unwrap(raw)
    print(f"\n[deep] n_steps: {r['n_steps']}, actions: {[s['action'] for s in r['plan']]}")
    assert r["n_steps"] >= 3
    assert r["n_steps"] <= 4
    actions = [s["action"] for s in r["plan"]]
    assert "web.search" in actions


# ---------------------------------------------------------------------------
# Knowledge base — full flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_knowledge_add_search_flow(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    _setup_kb_paths(tmp_path)
    r1 = _unwrap(await knowledge_add(
        topic="python/httpx",
        content="httpx.post(url, json=payload) sends a JSON POST and returns a Response",
        source="https://www.python-httpx.org/api/#post",
        confidence=0.95,
    ))
    r2 = _unwrap(await knowledge_add(
        topic="python/httpx",
        content="httpx is in the ODC v4 dependency list",
        source="pyproject.toml",
        confidence=0.9,
    ))
    assert r1.get("ok")
    assert r2.get("ok")
    rs = _unwrap(await knowledge_search(query="how to POST JSON with httpx", top_k=3))
    print(f"\n[kb] results: {rs['n_results']}")
    assert rs["n_results"] >= 1
    assert rs["results"][0]["topic"].startswith("python")
    stats = _unwrap(await knowledge_list(n=10))
    assert stats["stats"]["total_facts"] == 2


# ---------------------------------------------------------------------------
# System 2 auto-trigger — verify it fires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_system2_auto_trigger(tmp_path):
    if not _have_provider():
        pytest.skip("no LLM provider configured")
    from odc.config import load_config
    from odc.loop import Loop
    from odc.tools.base import ToolRegistry

    cfg = load_config()
    cfg.data_dir = tmp_path
    cfg.max_loop_turns = 2
    cfg.verify_hard_cap = 2
    cfg.deep_reason_enabled = True
    set_paths(tmp_path / "cognitive")

    provider = LLM_PROVIDER
    if provider is None:
        pytest.skip("no LLM provider")
    tools = ToolRegistry()
    loop = Loop(config=cfg, provider=provider, tools=tools, skills=[])

    out = await loop._system2_fallback(
        task="POST JSON to a server with no curl and no POST tool",
        context="verify_hard_cap=2 hit. web.fetch GET only. shell.run blocks curl.",
        messages=[],
    )
    assert out is not None
    assert "brief" in out
    assert "Council" in out["brief"] or "council" in out["brief"].lower()
    assert "should_retry" in out
    print(f"\n[auto-trigger] brief length: {len(out['brief'])} chars, should_retry: {out['should_retry']}")
    print(f"[auto-trigger] brief head: {out['brief'][:300]}")
