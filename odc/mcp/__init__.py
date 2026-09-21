"""ODC's mini-MCP: persistent memory + coordination substrate.

Inspired by asuramaya/Osiris's "5-step ritual" and the
Portable Agent Memory paper. We don't run a full MCP server (no
PostgreSQL/Redis needed), but we expose the same primitives so the
agent can do:

  mount(cwd)         → bind session, restore lineage
  get_status()       → identity + unread decisions + fleet pulse
  graph_search(...)  → check existing decisions before re-deriving
  record_decision()  → persist architectural choice with provenance
  settle()           → verify all writes durable before compaction

Storage: SQLite at <data_dir>/mcp/memory.db. No external deps.

This is single-agent by design — the "fleet pulse" is just the agent's
own activity. The interface is forward-compatible with multi-agent
(postal channels) when we need them.
"""
