"""Tests for MCP memory substrate (asuramaya/Osiris 5-step ritual)."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from odc.mcp.osiris import OsirisMemory, Decision, PostalMessage
from odc.mcp import tools as mcp_tools
from odc.mcp.tools import (
    mount, status, graph_search, record_decision, settle, post,
    ALL_MCP_TOOLS,
)


def _run(coro):
    return asyncio.run(coro)


def _call(tool_obj, **kwargs):
    return _run(tool_obj.run(**kwargs))


@pytest.fixture
def mem(tmp_path):
    """Fresh memory substrate."""
    return OsirisMemory(tmp_path / "test.db")


# ──────────────────────────────────────────────────────────────────
# Direct substrate tests
# ──────────────────────────────────────────────────────────────────
def test_mount_returns_session_id_and_lineage(mem):
    r = mem.mount(cwd="/test")
    assert r["session_id"].startswith("s-")
    assert r["lineage"]
    assert r["cwd"] == "/test"


def test_mount_restores_lineage_from_same_cwd(mem):
    r1 = mem.mount(cwd="/test")
    first_lineage = r1["lineage"]
    mem.close()

    mem2 = OsirisMemory(mem.db_path.parent / "test.db")
    r2 = mem2.mount(cwd="/test")
    assert first_lineage in r2["lineage"] or r2["restored_from"] == r1["session_id"]


def test_get_status_under_400_chars(mem):
    mem.mount(cwd="/test")
    s = mem.get_status()
    assert len(s) < 400
    assert "sid=" in s
    assert "decisions_session=0" in s


def test_record_decision_returns_id_and_evidence(mem):
    mem.mount(cwd="/test")
    d = Decision(session_id="", decision="Use httpx",
                 category="tool_choice", rationale="modern async support",
                 evidence_tier="AUTHORITATIVE_API")
    did = mem.record_decision(d)
    assert did > 0


def test_record_duplicate_increments_seen_in_threads(mem):
    mem.mount(cwd="/test")
    d = Decision(session_id="", decision="Use httpx", evidence_tier="DIRECT_OBSERVATION")
    id1 = mem.record_decision(d)
    id2 = mem.record_decision(d)
    assert id1 == id2
    row = mem._conn.execute("SELECT seen_in_threads FROM decisions WHERE id=?", (id1,)).fetchone()
    assert row[0] >= 2


def test_record_outcome_updates_counts(mem):
    mem.mount(cwd="/test")
    did = mem.record_decision(Decision(session_id="", decision="X", evidence_tier="DIRECT_OBSERVATION"))
    mem.record_outcome(did, success=True)
    mem.record_outcome(did, success=True)
    mem.record_outcome(did, success=False)
    row = mem._conn.execute("SELECT success_count, failure_count FROM decisions WHERE id=?", (did,)).fetchone()
    assert row[0] == 2
    assert row[1] == 1


def test_graph_search_finds_matching_decisions(mem):
    mem.mount(cwd="/test")
    mem.record_decision(Decision(session_id="", decision="Use httpx for HTTP", category="tool_choice"))
    mem.record_decision(Decision(session_id="", decision="Use json for parsing", category="tool_choice"))
    mem.record_decision(Decision(session_id="", decision="Avoid deprecated requests", category="anti_pattern"))
    hits = mem.graph_search("httpx")
    assert any("httpx" in h["decision"] for h in hits)
    assert all("confidence" in h for h in hits)


def test_graph_search_filter_by_category(mem):
    mem.mount(cwd="/test")
    mem.record_decision(Decision(session_id="", decision="tool X", category="tool_choice"))
    mem.record_decision(Decision(session_id="", decision="fact Y", category="fact"))
    hits = mem.graph_search("X", category="tool_choice")
    assert all(h["category"] == "tool_choice" for h in hits)


def test_post_and_inbox(mem):
    mem.mount(cwd="/test")
    mid = mem.post(PostalMessage(sender="agent-a", recipient="agent-b",
                                  kind="note", body="hello"))
    assert mid > 0
    inbox = mem.inbox("agent-b")
    assert len(inbox) == 1
    assert inbox[0]["body"] == "hello"
    # Second call should be empty (marked read)
    inbox2 = mem.inbox("agent-b")
    assert inbox2 == []


def test_settle_marks_session_ended(mem):
    r = mem.mount(cwd="/test")
    result = mem.settle()
    assert result["session_id"] == r["session_id"]
    assert result["settled"] is True


def test_decision_confidence_uses_tier_prior():
    d = Decision(session_id="x", decision="x",
                  evidence_tier="DERIVED", success_count=10, failure_count=0)
    # DERIVED prior = 0.40, rate = 11/12 = 0.9167, conf = 0.367
    assert d.confidence() < 0.40
    d2 = Decision(session_id="x", decision="x",
                  evidence_tier="SELF_DECLARED", success_count=10, failure_count=0)
    assert d2.confidence() > d.confidence()


# ──────────────────────────────────────────────────────────────────
# Tool-level tests
# ──────────────────────────────────────────────────────────────────
def test_mcp_tools_register_six():
    assert len(ALL_MCP_TOOLS) == 6
    names = {t.name for t in ALL_MCP_TOOLS}
    assert {"osiris.mount", "osiris.status", "osiris.graph_search",
            "osiris.record_decision", "osiris.settle", "osiris.post"} == names


def test_tool_mount_status_settle_ritual(tmp_path):
    """End-to-end ritual via tools."""
    mcp_tools.init_memory(tmp_path / "memory.db")
    r = _call(mount, cwd="/sandbox")
    assert "session_id" in r

    s = _call(status)
    assert "sid=" in s
    assert "/sandbox" in s

    rd = _call(record_decision, decision="prefer httpx",
               category="tool_choice", rationale="modern",
               evidence_tier="AUTHORITATIVE_API")
    assert rd["id"] > 0

    gs = _call(graph_search, query="httpx")
    assert gs["count"] >= 1
    assert any("httpx" in d["decision"] for d in gs["decisions"])

    p = _call(post, sender="me", recipient="other", body="hi")
    assert p["id"] > 0

    res = _call(settle)
    assert res["settled"] is True


# ──────────────────────────────────────────────────────────────────
# Agent integration
# ──────────────────────────────────────────────────────────────────
def test_mcp_tools_in_agent_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ODC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ODC_LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-not-used")

    from odc.config import Config
    from odc.agent import Agent
    cfg = Config(data_dir=tmp_path / "data")
    a = Agent(config=cfg, with_memory=False)

    names = set(a.tools.names())
    expected = {"osiris.mount", "osiris.status", "osiris.graph_search",
                "osiris.record_decision", "osiris.settle", "osiris.post"}
    assert expected.issubset(names), f"missing: {expected - names}"


def test_mcp_persistence_across_agent_instances(tmp_path, monkeypatch):
    """Decisions survive Agent restart."""
    monkeypatch.setenv("ODC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ODC_LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-not-used")

    from odc.config import Config
    from odc.agent import Agent

    cfg = Config(data_dir=tmp_path / "data")
    a1 = Agent(config=cfg, with_memory=False)
    m1 = mcp_tools.get_memory()
    assert m1 is not None
    m1.mount(cwd="/sandbox")  # need to mount before recording
    did = m1.record_decision(Decision(session_id="", decision="persisted fact X"))

    # New agent instance — should see the decision
    a2 = Agent(config=cfg, with_memory=False)
    m2 = mcp_tools.get_memory()
    hits = m2.graph_search("persisted")
    assert any(h["id"] == did for h in hits)
