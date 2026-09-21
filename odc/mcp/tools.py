"""MCP ritual tools — exposes the OsirisMemory 5-step ritual as ODC tools."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from odc.tools.base import tool
from odc.mcp.osiris import OsirisMemory, Decision, PostalMessage

log = logging.getLogger(__name__)

# Singleton — set on Agent init
_MEMORY: OsirisMemory | None = None


def init_memory(data_dir: Path) -> OsirisMemory:
    """Initialize the singleton memory substrate at <data_dir>/mcp/memory.db."""
    global _MEMORY
    _MEMORY = OsirisMemory(data_dir / "mcp" / "memory.db")
    return _MEMORY


def get_memory() -> OsirisMemory | None:
    return _MEMORY


# ═══════════════════════════════════════════════════════════════════════
# 1. mount — bind session, restore lineage
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osiris.mount",
    description=(
        "Step 1 of the 5-step ritual: bind the current session to a working "
        "directory. Returns session_id and lineage. Auto-restore from previous "
        "sessions on the same cwd."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Working directory to bind to"},
        },
    },
)
async def mount(cwd: str = "") -> dict:
    mem = get_memory()
    if mem is None:
        return {"error": "OsirisMemory not initialized — call init_memory(data_dir) first"}
    return mem.mount(cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
# 2. status — identity + unread + fleet pulse
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osiris.status",
    description=(
        "Step 2 of the ritual: one-line summary of session, lineage, unread "
        "messages, and decision counts. Compact (<400 chars) for context "
        "efficiency."
    ),
    parameters={"type": "object", "properties": {}},
)
async def status() -> str:
    mem = get_memory()
    if mem is None:
        return "OsirisMemory not initialized"
    return mem.get_status()


# ═══════════════════════════════════════════════════════════════════════
# 3. graph_search — check existing rulings
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osiris.graph_search",
    description=(
        "Step 3 of the ritual: search persisted decisions by query. Returns "
        "matching architectural choices, tool choices, facts, and anti-patterns "
        "with their evidence tier and empirical confidence. Use BEFORE "
        "re-deriving a solution — if it's been decided, reuse it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (substring)"},
            "category": {"type": "string", "description": "Filter by category (architectural|tool_choice|fact|anti_pattern)"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
        },
    },
)
async def graph_search(query: str, category: str | None = None, limit: int = 5) -> dict:
    mem = get_memory()
    if mem is None:
        return {"error": "OsirisMemory not initialized"}
    hits = mem.graph_search(query, category=category, limit=limit)
    return {"query": query, "count": len(hits), "decisions": hits}


# ═══════════════════════════════════════════════════════════════════════
# 4. record_decision — persist with provenance
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osiris.record_decision",
    description=(
        "Step 4 of the ritual: persist an architectural or behavioral decision "
        "with evidence tier (SELF_DECLARED, AUTHORITATIVE_API, DIRECT_OBSERVATION, "
        "CORROBORATED, CO_OCCURRENCE, DERIVED). Returns decision id. Duplicate "
        "decisions increment seen_in_threads (cross-thread promotion)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "decision": {"type": "string", "description": "The choice made"},
            "category": {"type": "string", "enum": ["architectural", "tool_choice", "fact", "anti_pattern"]},
            "rationale": {"type": "string", "description": "Why this choice"},
            "evidence_tier": {"type": "string", "enum": ["SELF_DECLARED", "AUTHORITATIVE_API", "DIRECT_OBSERVATION", "CORROBORATED", "CO_OCCURRENCE", "DERIVED"]},
            "parent_id": {"type": "integer", "description": "Parent decision id if this derives from another"},
        },
        "required": ["decision"],
    },
)
async def record_decision(
    decision: str,
    category: str = "architectural",
    rationale: str = "",
    evidence_tier: str = "DIRECT_OBSERVATION",
    parent_id: int | None = None,
) -> dict:
    mem = get_memory()
    if mem is None:
        return {"error": "OsirisMemory not initialized"}
    d = Decision(
        session_id="",  # filled by record_decision
        decision=decision,
        category=category,
        rationale=rationale,
        evidence_tier=evidence_tier,
        parent_id=parent_id,
    )
    did = mem.record_decision(d)
    return {"id": did, "decision": decision, "evidence_tier": evidence_tier}


# ═══════════════════════════════════════════════════════════════════════
# 5. settle — verify durable, mark session ended
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osiris.settle",
    description=(
        "Step 5 of the ritual: mark the current session as ended. Verifies "
        "all writes are committed (WAL checkpoint). Call before context "
        "compaction or session end."
    ),
    parameters={"type": "object", "properties": {}},
)
async def settle() -> dict:
    mem = get_memory()
    if mem is None:
        return {"error": "OsirisMemory not initialized"}
    return mem.settle()


# ═══════════════════════════════════════════════════════════════════════
# Bonus: post / inbox for forward-compat with multi-agent
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osiris.post",
    description=(
        "Send a message via the postal channel. Currently single-agent; "
        "designed to support multi-agent broadcast/DM in the future."
    ),
    parameters={
        "type": "object",
        "properties": {
            "sender": {"type": "string"},
            "recipient": {"type": "string"},
            "kind": {"type": "string", "enum": ["note", "decision_ref", "request"]},
            "body": {"type": "string"},
            "thread": {"type": "string"},
        },
        "required": ["sender", "recipient", "body"],
    },
)
async def post(sender: str, recipient: str, body: str,
               kind: str = "note", thread: str = "") -> dict:
    mem = get_memory()
    if mem is None:
        return {"error": "OsirisMemory not initialized"}
    msg = PostalMessage(sender=sender, recipient=recipient, kind=kind,
                        body=body, thread=thread)
    return {"id": mem.post(msg)}


# ═══════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════
ALL_MCP_TOOLS = [mount, status, graph_search, record_decision, settle, post]


def register_all(registry, data_dir: Path) -> list[str]:
    """Initialize memory and register all 5 ritual tools."""
    init_memory(data_dir)
    names = []
    for fn in ALL_MCP_TOOLS:
        registry.register(fn)
        names.append(fn.name)
    return names
