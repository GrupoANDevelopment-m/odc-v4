"""Memory tools backed by the persistent store.

The agent saves and recalls through these — the heavy lifting (SQLite
+FTS5) lives in odc.memory.store. Keep the surface area tiny so the
LLM has fewer things to remember.
"""
from __future__ import annotations

from typing import Any

from odc.memory.store import MemoryStore
from odc.tools.base import tool

# A single store instance, lazily created. The CLI / agent sets it up
# and injects it. The tools look it up on first call.
_store: MemoryStore | None = None


def set_store(store: MemoryStore) -> None:
    global _store
    _store = store


def _get_store() -> MemoryStore:
    if _store is None:
        raise RuntimeError(
            "MemoryStore not configured. Call odc.tools.memory.set_store(store) "
            "before invoking memory tools."
        )
    return _store


@tool(
    name="memory.save",
    description=(
        "Save a fact, decision, or note to long-term memory. Stored with a "
        "category and optional tags. Future searches will find it via FTS5."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "What to remember. Plain text, one entry.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Short category: 'fact' | 'decision' | 'note' | 'error' | 'user' | 'task'."
                ),
                "default": "note",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for later grouping.",
                "default": [],
            },
            "source": {
                "type": "string",
                "description": "Where this came from, e.g. 'web.fetch:...', 'user', 'tool:shell.run'.",
                "default": "agent",
            },
        },
        "required": ["text"],
    },
)
async def memory_save(
    text: str, category: str = "note", tags: list[str] | None = None, source: str = "agent"
) -> str:
    store = _get_store()
    entry_id = store.save(text=text, category=category, tags=tags or [], source=source)
    return f"saved memory #{entry_id}"


@tool(
    name="memory.search",
    description=(
        "Full-text search over the memory store. Returns the top-K entries "
        "matching the query, newest first within relevance. Use it to recall "
        "prior decisions, user preferences, or known facts before guessing."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {
                "type": "integer",
                "description": "Max results. Default 5.",
                "default": 5,
            },
            "category": {
                "type": "string",
                "description": "Optional category filter.",
                "default": None,
            },
        },
        "required": ["query"],
    },
)
async def memory_search(query: str, limit: int = 5, category: str | None = None) -> list[dict[str, Any]]:
    store = _get_store()
    return store.search(query=query, limit=limit, category=category)


@tool(
    name="memory.recent",
    description=(
        "List the most recent N memory entries. Useful to glance at the "
        "tail of what you've been doing in this session."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of entries. Default 10.",
                "default": 10,
            },
        },
    },
)
async def memory_recent(limit: int = 10) -> list[dict[str, Any]]:
    store = _get_store()
    return store.recent(limit=limit)
