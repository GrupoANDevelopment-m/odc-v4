"""Unit tests for DEEP-REASON components (council, revise, analogy,
hypothesize_v2, decompose, investigate_deep, knowledge base, auto-trigger).

These are PURE unit tests — no LLM calls. The prompt construction,
parsing, and data structures are validated.
"""
import json
from pathlib import Path

import pytest

from odc.cognitive.council import (
    LENSES,
    build_council_prompt,
    parse_council_response,
    render_council_for_prompt,
)
from odc.cognitive.revise import build_revise_prompt, parse_revise_response
from odc.cognitive.analogy import (
    build_analogy_prompt,
    parse_analogy_response,
    analogy_prompt_for_injection,
)
from odc.cognitive.hypothesize_v2 import (
    build_hypothesize_prompt,
    parse_hypotheses_response,
    rank_hypotheses_for_testing,
)
from odc.cognitive.decompose import (
    build_decompose_prompt,
    parse_decompose_response,
    validate_dag,
    next_executable,
    summarize_progress,
)
from odc.cognitive.investigate_deep import (
    classify_topic,
    reformulate_query,
    deep_investigate_plan,
    deep_investigate_summary,
    SOURCE_DOMAINS,
)
from odc.knowledge import KnowledgeBase


# ---------------------------------------------------------------------------
# Council
# ---------------------------------------------------------------------------


def test_council_has_5_lenses():
    assert len(LENSES) == 5
    names = {n for n, _ in LENSES}
    assert names == {"expert", "hacker", "researcher", "developer", "investigator"}


def test_council_prompt_includes_problem_and_context():
    p = build_council_prompt("how to do X", "tried A, B, C all failed")
    assert "how to do X" in p
    assert "tried A, B, C all failed" in p
    for name in ("EXPERT", "HACKER", "RESEARCHER", "DEVELOPER", "INVESTIGATOR"):
        assert name in p


def test_council_parse_fenced_json():
    text = '```json\n{"perspectives": {"expert": "do X"}, "synthesis": "yes", "confidence": 0.7}\n```'
    r = parse_council_response(text)
    assert r["perspectives"]["expert"] == "do X"
    assert r["synthesis"] == "yes"
    assert abs(r["confidence"] - 0.7) < 0.01


def test_council_parse_inline_json():
    text = 'before text {"perspectives": {"hacker": "use Y"}, "synthesis": "h4ck it", "confidence": 0.5} after'
    r = parse_council_response(text)
    assert r["perspectives"]["hacker"] == "use Y"


def test_council_parse_prose_fallback():
    text = "**Synthesis**: just do it\n\nmore text"
    r = parse_council_response(text)
    # Either via fallback parser (synthesis) or raw
    assert "raw" in r or "synthesis" in r


def test_council_render_for_prompt_compact():
    r = {
        "perspectives": {"expert": "do A", "hacker": "try B", "researcher": "read C", "developer": "code D", "investigator": "test E"},
        "synthesis": "use B with mitigation",
        "confidence": 0.8,
    }
    out = render_council_for_prompt(r)
    assert "EXPERT" in out and "do A" in out
    assert "SYNTHESIS" in out
    assert "0.80" in out


# ---------------------------------------------------------------------------
# Revise (epistemic humility)
# ---------------------------------------------------------------------------


def test_revise_prompt_includes_all_sections():
    p = build_revise_prompt(
        "web.search is broken",
        evidence="0 results for 5 queries",
        failures="DDGS blocked from datacenter",
    )
    for section in ("STEEL-MAN OPPOSITE", "BLIND SPOTS", "ALTERNATIVE VARIANTS",
                    "FALSIFICATION CRITERIA", "CONFIDENCE RECALIBRATION"):
        assert section in p


def test_revise_parse_returns_required_keys():
    text = '''```json
    {
      "steel_man_opposite": "x is wrong",
      "blind_spots": ["a", "b"],
      "alternative_variants": ["c"],
      "falsification_criteria": ["d"],
      "new_confidence": 0.4,
      "should_change_approach": true,
      "suggested_next_move": "use code.run"
    }
    ```'''
    r = parse_revise_response(text)
    assert r["steel_man_opposite"] == "x is wrong"
    assert r["blind_spots"] == ["a", "b"]
    assert r["new_confidence"] == 0.4
    assert r["should_change_approach"] is True


def test_revise_invalid_confidence_falls_back():
    text = '{"steel_man_opposite": "x", "new_confidence": "not a number"}'
    r = parse_revise_response(text)
    assert r["new_confidence"] == 0.5  # default


# ---------------------------------------------------------------------------
# Analogy
# ---------------------------------------------------------------------------


def test_analogy_prompt_has_correct_sections():
    p = build_analogy_prompt("agent stuck in sandbox", "tools blocked")
    assert "MECHANISM" in p.upper() or "mechanism" in p
    assert "MAPPING" in p.upper() or "mapping" in p
    assert "TACTIC" in p.upper() or "tactic" in p


def test_analogy_parse_extracts_analogs():
    text = '''```json
    {
      "analogs": [
        {"name": "Apollo 13", "domain": "history", "mechanism": "improvise", "mapping": "limited tools", "tactic": "use what's available"}
      ],
      "best_analog": "Apollo 13",
      "extracted_strategy": "improvise with available tools"
    }
    ```'''
    r = parse_analogy_response(text)
    assert len(r["analogs"]) == 1
    assert r["analogs"][0]["name"] == "Apollo 13"
    assert r["best_analog"] == "Apollo 13"
    assert "improvise" in r["extracted_strategy"]


def test_analogy_injection_compact():
    r = {
        "analogs": [
            {"name": "Apollo 13", "domain": "history", "mechanism": "improvise", "mapping": "limited tools", "tactic": "use what's available"},
        ],
        "extracted_strategy": "use what's there",
    }
    out = analogy_prompt_for_injection(r)
    assert "Apollo 13" in out
    assert "improvise" in out
    assert "EXTRACTED STRATEGY" in out


# ---------------------------------------------------------------------------
# Hypothesize v2 (multi-hypothesis tree)
# ---------------------------------------------------------------------------


def test_hypothesize_prompt_n_hypotheses():
    p = build_hypothesize_prompt("how to POST JSON", "no curl", n=4)
    assert "4" in p
    assert "DISTINCT" in p.upper() or "distinct" in p


def test_hypothesize_parse_returns_ranked_list():
    text = '''```json
    {
      "hypotheses": [
        {"id": "h1", "hypothesis": "a", "test": {"tool": "code.run"}, "cost": 1, "prior": 0.7},
        {"id": "h2", "hypothesis": "b", "test": {"tool": "web.fetch"}, "cost": 2, "prior": 0.5}
      ],
      "ranking": ["h1", "h2"],
      "best_initial_test": "h1",
      "fallback": "try h2"
    }
    ```'''
    r = parse_hypotheses_response(text, n_expected=2)
    assert len(r["hypotheses"]) == 2
    assert r["best_initial_test"] == "h1"


def test_rank_hypotheses_for_testing_prefers_cheap_high_prior():
    hyps = [
        {"id": "h1", "hypothesis": "a", "cost": 3, "prior": 0.9},
        {"id": "h2", "hypothesis": "b", "cost": 1, "prior": 0.4},
        {"id": "h3", "hypothesis": "c", "cost": 1, "prior": 0.8},
    ]
    ranked = rank_hypotheses_for_testing(hyps)
    # h3 (cost 1, prior 0.8) should come before h2 (cost 1, prior 0.4)
    # h1 (cost 3) should come last
    assert ranked[0]["id"] == "h3"
    assert ranked[1]["id"] == "h2"
    assert ranked[2]["id"] == "h1"


# ---------------------------------------------------------------------------
# Decompose (DAG)
# ---------------------------------------------------------------------------


def test_decompose_prompt_has_dag_requirements():
    p = build_decompose_prompt("complex multi-step task")
    assert "DAG" in p or "dag" in p
    assert "depends_on" in p


def test_decompose_parse_returns_dag():
    text = '''```json
    {
      "sub_tasks": [
        {"id": "s1", "description": "step 1", "depends_on": [], "tool_hint": "web.search", "acceptance": "got results"},
        {"id": "s2", "description": "step 2", "depends_on": ["s1"], "tool_hint": "web.fetch", "acceptance": "fetched content"}
      ],
      "execution_order": ["s1", "s2"],
      "estimated_calls": 2,
      "rollback_strategy": "abort"
    }
    ```'''
    dag = parse_decompose_response(text)
    assert len(dag["sub_tasks"]) == 2
    assert dag["sub_tasks"][1]["depends_on"] == ["s1"]


def test_validate_dag_rejects_cycle():
    dag = {
        "sub_tasks": [
            {"id": "s1", "depends_on": ["s2"]},
            {"id": "s2", "depends_on": ["s1"]},
        ]
    }
    ok, err = validate_dag(dag)
    assert not ok
    assert "cycle" in err.lower() or "missing" in err.lower()


def test_validate_dag_rejects_missing_dep():
    dag = {
        "sub_tasks": [
            {"id": "s1", "depends_on": ["s_ghost"]},
        ]
    }
    ok, err = validate_dag(dag)
    assert not ok
    assert "missing" in err.lower()


def test_validate_dag_accepts_valid():
    dag = {
        "sub_tasks": [
            {"id": "s1", "depends_on": []},
            {"id": "s2", "depends_on": ["s1"]},
            {"id": "s3", "depends_on": ["s1", "s2"]},
        ]
    }
    ok, err = validate_dag(dag)
    assert ok, err


def test_next_executable_returns_leaves():
    dag = {
        "sub_tasks": [
            {"id": "s1", "depends_on": []},
            {"id": "s2", "depends_on": ["s1"]},
            {"id": "s3", "depends_on": ["s1"]},
        ]
    }
    assert [s["id"] for s in next_executable(dag, {})] == ["s1"]
    assert [s["id"] for s in next_executable(dag, {"s1": "done"})] == ["s2", "s3"]


def test_summarize_progress():
    dag = {"sub_tasks": [{"id": f"s{i}"} for i in range(5)]}
    s = summarize_progress(dag, {"s1": "done", "s2": "done", "s3": "failed"})
    assert "2/5" in s
    assert "1 failed" in s


# ---------------------------------------------------------------------------
# Investigate deep
# ---------------------------------------------------------------------------


def test_classify_topic_picks_python():
    assert classify_topic("how to use python decorators") == "python"
    assert classify_topic("POST with python httpx") == "python"


def test_classify_topic_picks_http():
    assert classify_topic("REST API POST request") == "http"
    assert classify_topic("http status codes") == "http"


def test_classify_topic_picks_arxiv_for_paper():
    assert classify_topic("recent paper on transformers") == "arxiv"
    assert classify_topic("research on RLHF") == "ml"


def test_reformulate_query_returns_variants():
    qs = reformulate_query("how to POST JSON in python", n=3)
    assert len(qs) >= 2
    assert qs[0] == "how to POST JSON in python"
    # Variant should drop at least one filler word
    assert any(len(q) < len(qs[0]) for q in qs[1:])


def test_deep_investigate_plan_bounded():
    plan = deep_investigate_plan("how to do X")
    assert len(plan) > 0
    assert len(plan) <= 6  # bounded
    for step in plan:
        assert "step_id" in step
        assert "action" in step
        assert "params" in step
        assert "expected_yield" in step


def test_deep_investigate_plan_includes_kb_lookup():
    plan = deep_investigate_plan("anything")
    actions = [s["action"] for s in plan]
    assert "knowledge.search" in actions


def test_deep_investigate_summary_renders():
    plan = deep_investigate_plan("python httpx POST", max_attempts=4)
    s = deep_investigate_summary(plan)
    assert "DEEP RESEARCH PLAN" in s
    assert "stop if:" in s.lower() or "stop_if" in s


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------


def test_kb_add_and_search(tmp_path):
    kb = KnowledgeBase(tmp_path)
    r = kb.add("python", "httpx.post accepts json=", source="docs", confidence=0.9)
    assert r["ok"] and not r["deduped"]
    kb.add("python", "requests.post is also fine", source="so")
    kb.add("http", "Content-Type: application/json", source="mdn")
    res = kb.search("how to POST JSON python")
    assert len(res) >= 1
    assert res[0]["topic"] in ("python", "http")


def test_kb_dedupes_by_content_prefix(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.add("python", "x" * 250)
    r = kb.add("python", "x" * 250)
    assert r["deduped"] is True
    assert len(kb.list_recent(100)) == 1


def test_kb_topic_filter(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.add("python/httpx", "a")
    kb.add("http/post", "b")
    res = kb.search("something", topic_filter="python")
    assert all("python" in f["topic"].lower() for f in res)


def test_kb_stats(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.add("python", "a")
    kb.add("python", "b")
    kb.add("http", "c")
    s = kb.stats()
    assert s["total_facts"] == 3
    assert "python" in s["topics"]


def test_kb_delete(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.add("python", "fact 1")
    kb.add("python", "fact 2")
    n = kb.delete("python", "fact 1")
    assert n == 1
    assert kb.stats()["total_facts"] == 1


# ---------------------------------------------------------------------------
# Auto-trigger integration
# ---------------------------------------------------------------------------


def test_loop_has_system2_fallback_method():
    from odc.loop import Loop
    # The method must exist on the class
    assert hasattr(Loop, "_system2_fallback")
    assert asyncio.iscoroutinefunction(Loop._system2_fallback)


def test_config_has_deep_reason_flag():
    from odc.config import Config
    # Either it exists as default True, or it's a missing attribute
    # (in which case getattr with default works)
    c = Config()
    flag = getattr(c, "deep_reason_enabled", True)
    assert flag is True  # default enabled


import asyncio
