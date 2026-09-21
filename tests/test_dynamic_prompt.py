"""Tests for the dynamic prompt builder (3 phases + conversation isolation).

Phase 1: Layer 0/1/2/3 assembly + BM25 tool/skill filtering
Phase 2: tool.discover for on-demand expansion
Phase 3: extractive summarization of older messages
Hard constraint: NEVER mix conversations unless the user references past.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest

from odc.prompt.bm25 import BM25, build_corpus_from_tools
from odc.prompt.builder import (
    LAYER_0_CORE_BASE,
    TOOL_DISCOVER_NAME,
    build_dynamic_prompt,
    build_layer_2,
    build_layer_3,
    build_tool_discover_tool_description,
    estimate_tokens,
)
from odc.prompt.scope import (
    ThreadContext,
    heuristics_safe_to_inject,
    investigations_safe_to_inject,
    user_references_past,
)
from odc.prompt.summarize import summarize_messages


# ---------------------------------------------------------------------------
# Phase 1: BM25 layer
# ---------------------------------------------------------------------------


def test_bm25_ranks_relevant_doc_higher():
    corpus = [
        "python type hints mypy",
        "how to bake sourdough bread",
        "json parsing with python",
        "elephant migration patterns",
    ]
    ranker = BM25(corpus)
    ranked = ranker.rank("how to use python for json", top_k=2)
    assert 0 in ranked or 2 in ranked  # python hints or json parsing
    # "bake bread" should be last
    assert 1 not in ranked or 3 not in ranked


def test_bm25_empty_query_returns_top():
    ranker = BM25(["a", "b", "c"])
    r = ranker.rank("", top_k=2)
    assert r == [0, 1]


def test_layer_2_returns_subset_of_tools():
    class T:
        def __init__(self, name, desc):
            self.name = name
            self.description = desc
    tools = [
        T("web.search", "search the web for information"),
        T("fs.write", "write text to a file"),
        T("fs.read", "read content from a file"),
        T("code.run", "run python code"),
        T("git.log", "show git commit history"),
        T("shell.run", "run shell commands"),
    ]
    text, selected = build_layer_2(
        task="find the git history of this file",
        all_tools=tools,
        recent_tool_calls=[],
        initial_k=3,
    )
    # Should include git.log (best match) and recent continuity
    assert "git.log" in selected
    assert len(selected) <= 3 + 3
    assert TOOL_DISCOVER_NAME not in selected  # meta-tool, not in catalog


def test_layer_2_includes_recent_continuity():
    class T:
        def __init__(self, name, desc):
            self.name = name
            self.description = desc
    tools = [
        T("web.search", "search the web for information"),
        T("fs.write", "write text to a file"),
        T("fs.read", "read content from a file"),
        T("code.run", "run python code"),
        T("git.log", "show git commit history"),
        T("shell.run", "run shell commands"),
    ]
    text, selected = build_layer_2(
        task="write a python script",  # would naturally select code.run
        all_tools=tools,
        recent_tool_calls=["shell.run"],  # but we were just using shell
    )
    assert "code.run" in selected
    assert "shell.run" in selected  # continuity preserved


# ---------------------------------------------------------------------------
# Phase 1: Full builder
# ---------------------------------------------------------------------------


class _MockSkill:
    def __init__(self, name, body):
        self.name = name
        self.body = body


class _MockTool:
    def __init__(self, name, desc):
        self.name = name
        self.description = desc


def test_build_dynamic_prompt_all_layers(tmp_path):
    ctx = ThreadContext("t-1", tmp_path)
    ctx.add_fact("user wants a hash of /tmp/x")
    ctx.set_plan(["hash it", "verify", "report"])
    ctx.save()

    skills = {
        "method": _MockSkill("method", "Plan-Act-Verify loop"),
        "coding": _MockSkill("coding", "Use code.* tools for code"),
        "obstacle-breaker": _MockSkill("obstacle-breaker", "Build tools dynamically"),
    }
    tools = [
        _MockTool("web.search", "search the web"),
        _MockTool("fs.write", "write a file"),
        _MockTool("code.run", "run python code"),
        _MockTool("dynamic.tool_create", "build a new tool at runtime"),
        _MockTool("cognitive.think", "log a thought"),
    ]
    profile = {
        "heuristics": [
            {"rule": "For hash tasks, build the tool first",
             "seen_in_threads": ["t-1", "t-2"],
             "success_count": 3, "failure_count": 1},
        ],
        "failure_investigations": [],
        "anti_patterns": [],
    }
    r = build_dynamic_prompt(
        task="compute the SHA-256 hash of /tmp/x",
        thread_ctx=ctx, all_tools=tools, skills=skills, profile_data=profile,
        identity_block="## Your identity\nYou are cortana.\n\n",
    )
    sp = r["system_prompt"]
    # Layer 0 always there: identity + base rules
    assert "cortana" in sp, f"identity not in prompt: {sp[:300]}"
    assert "Core rules" in sp
    # Layer 1: matched skill (obstacle-breaker matches because of "build")
    assert "Active skills" in sp or "## Task" in sp.lower() or "TASK CONTEXT" in sp
    # Layer 2: tool subset header
    assert "[TOOL SUBSET]" in sp
    # selected tools (BM25 ranks by task)
    assert len(r["selected_tools"]) > 0
    assert len(r["selected_tools"]) <= len(tools)
    # Token estimate is way under the old 3-4K
    assert r["token_estimate"] < 1500
    # Heuristic surfaced (validated in 2 threads, matches "hash")
    assert "hash tasks, build the tool" in sp


def test_token_estimate_under_budget_for_normal_task(tmp_path):
    """Sanity: a normal task should fit in <1.5K tokens, vs the
    current 3-4K for the full static prompt."""
    ctx = ThreadContext("t-x", tmp_path)
    skills = {"method": _MockSkill("method", "plan-act-verify")}
    tools = [_MockTool(f"tool.{i}", f"desc {i}") for i in range(20)]
    profile = {"heuristics": [], "failure_investigations": [], "anti_patterns": []}
    r = build_dynamic_prompt(
        task="do something",
        thread_ctx=ctx, all_tools=tools, skills=skills, profile_data=profile,
    )
    # 20 tools but only 5 in the subset
    assert len(r["selected_tools"]) <= 8
    assert r["token_estimate"] < 1500


# ---------------------------------------------------------------------------
# Hard constraint: NEVER mix conversations
# ---------------------------------------------------------------------------


def test_no_cross_thread_mixing_without_explicit_reference():
    """Heuristics from OTHER threads do not surface unless the user
    references past. This is the user's hard constraint."""
    profile = {
        "heuristics": [
            # Validated in 1 thread (current task may or may not match)
            {"rule": "For hash tasks, build the tool first",
             "seen_in_threads": ["t-hash1"], "success_count": 5, "failure_count": 0},
        ],
        "failure_investigations": [],
    }
    # Task that doesn't match the heuristic
    hs = heuristics_safe_to_inject(profile, "do something unrelated", min_cross_threads=2)
    assert hs == []


def test_cross_thread_reference_unlocks_past():
    """When the user says 'remember when' or 'last time', past
    heuristics with 2+ threads become available."""
    profile = {
        "heuristics": [
            {"rule": "For hash tasks, build the tool first",
             "seen_in_threads": ["t-hash1", "t-hash2"],
             "success_count": 5, "failure_count": 0},
        ],
        "failure_investigations": [],
    }
    # User references past
    task = "remember when we did the hash thing? try again"
    assert user_references_past(task) is True
    hs = heuristics_safe_to_inject(profile, task, min_cross_threads=2)
    assert len(hs) == 1
    assert "hash" in hs[0]["rule"]


def test_deterministic_thread_resume_uses_same_thread_ctx(tmp_path):
    """If the user calls agent.run() with the same task, the same
    thread_id is derived → same ThreadContext → past facts come
    back automatically. This is the legitimate cross-thread access."""
    # First run: record a fact
    ctx1 = ThreadContext("t-same", tmp_path)
    ctx1.add_fact("user wants reverse of 'hello'")
    ctx1.save()
    # Second run (same thread_id) reads it
    ctx2 = ThreadContext("t-same", tmp_path)
    brief = ctx2.to_brief()
    assert "reverse of 'hello'" in brief


def test_different_threads_do_not_share_facts(tmp_path):
    """Hard isolation: thread A's facts MUST NOT appear in thread B's
    ThreadContext, even when both exist on disk."""
    ctx_a = ThreadContext("t-a", tmp_path)
    ctx_a.add_fact("SECRET FACT FROM THREAD A: user password = 12345")
    ctx_a.save()
    ctx_b = ThreadContext("t-b", tmp_path)
    brief_b = ctx_b.to_brief()
    assert "SECRET FACT" not in brief_b
    assert "password" not in brief_b


def test_user_references_past_phrases():
    """The list of phrases that unlock cross-thread memory."""
    for phrase in [
        "remember when we did this?",
        "last time we tried",
        "yesterday we discussed this",
        "as you said before",
        "from our last session",
        "previously we found",
    ]:
        assert user_references_past(phrase) is True, phrase
    for phrase in [
        "compute the hash",
        "what's the weather",
        "tell me a joke",
        "open the file",
    ]:
        assert user_references_past(phrase) is False, phrase


def test_bad_heuristic_deprecated_not_injected():
    """Wisdom, not trauma: a heuristic with more failures than
    successes is not surfaced even if cross-validated."""
    profile = {
        "heuristics": [
            {"rule": "Always use shell.run for everything",
             "seen_in_threads": ["t-1", "t-2", "t-3"],
             "success_count": 1, "failure_count": 9},
        ],
    }
    hs = heuristics_safe_to_inject(profile, "use shell.run for hashing", min_cross_threads=2)
    assert hs == []


# ---------------------------------------------------------------------------
# Phase 2: tool.discover
# ---------------------------------------------------------------------------


def test_tool_discover_is_in_catalog():
    desc = build_tool_discover_tool_description()
    assert desc["name"] == "tool.discover"
    assert "query" in desc["parameters"]["properties"]


# ---------------------------------------------------------------------------
# Phase 3: extractive summarization
# ---------------------------------------------------------------------------


class _MockMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


def test_summarize_messages_picks_decisions():
    msgs = [
        _MockMessage("user", "Compute the SHA-256 of /tmp/file"),
        _MockMessage("assistant", "I will use the dynamic.tool_create to build a sha256 tool. Then I will run it. Then I will verify."),
        _MockMessage("user", "ok proceed"),
        _MockMessage("assistant", "I built the tool. Hash computed. Verification done. The hash is 0xabc123."),
    ]
    s = summarize_messages(msgs, max_sentences=3)
    assert "sha256" in s.lower() or "hash" in s.lower()
    # Should not include the trivial "ok proceed"
    assert "ok proceed" not in s.lower()


def test_summarize_messages_empty():
    assert summarize_messages([]) == ""
    assert summarize_messages([_MockMessage("user", "")]) == ""


def test_layer_3_truncates_long_messages():
    msg = _MockMessage("user", "x" * 2000)
    text = build_layer_3([msg], keep_last=1, summary_tail="")
    # Should be truncated (not full 2000 chars verbatim)
    assert "x" * 1500 not in text


def test_layer_3_includes_summary_tail():
    msg = _MockMessage("user", "short")
    text = build_layer_3([msg], keep_last=1, summary_tail="earlier: we decided X")
    assert "earlier: we decided X" in text


# ---------------------------------------------------------------------------
# E2E: dynamic prompt actually reduces token count vs static
# ---------------------------------------------------------------------------


def test_dynamic_prompt_smaller_than_static():
    """Compare dynamic (matched) vs naive (all skills + all tools).
    Dynamic must be smaller."""
    ctx = ThreadContext("t-cmp", Path("/tmp/dyn_cmp"))
    ctx.save()
    skills = {
        "method": _MockSkill("method", "x" * 200),
        "coding": _MockSkill("coding", "y" * 200),
        "obstacle-breaker": _MockSkill("obstacle-breaker", "z" * 200),
    }
    tools = [_MockTool(f"tool.{i}", "d" * 50) for i in range(15)]
    profile = {"heuristics": [], "failure_investigations": [], "anti_patterns": []}
    r = build_dynamic_prompt(
        task="do something",
        thread_ctx=ctx, all_tools=tools, skills=skills, profile_data=profile,
    )
    # Static equivalent would be: 3 skills * 200 + 15 tools * 60 = 600+900 = 1500
    # Plus the static system prompt. Should be way more.
    static_estimate = (
        300  # LAYER_0_CORE_BASE
        + 3 * 200 / 4  # all 3 skills
        + 15 * 60 / 4  # all 15 tools
        + 200  # misc
    )
    # Dynamic should be at least 30% smaller
    assert r["token_estimate"] < static_estimate * 0.7
