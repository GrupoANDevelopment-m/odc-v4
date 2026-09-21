"""Tool wrapper for the local knowledge base.

Three tools:
  - knowledge.add: store a fact with topic + content + source + confidence
  - knowledge.search: BM25 search over stored facts
  - knowledge.list: list recent facts (debug)
"""
from __future__ import annotations

from typing import Any

from odc.tools.base import tool


def _get_kb():
    """Lazy-resolve the KB based on the cognitive paths config."""
    from odc.cognitive.tools import get_paths
    from odc.knowledge import KnowledgeBase
    return KnowledgeBase(get_paths().parent)


@tool(
    name="knowledge.add",
    description=(
        "Store a fact the agent has learned (from a web page, a paper, "
        "an error message, an experiment). Facts are deduplicated by "
        "(topic, content prefix). The KB is cross-thread, so facts added "
        "in one conversation can be searched in another. Use this "
        "WHEN: you read something useful, discovered a workaround, "
        "or solved a non-obvious bug."
    ),
    parameters={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Short topic tag (e.g. 'python/httpx', 'http/post', 'odc/identity').",
            },
            "content": {
                "type": "string",
                "description": "The fact itself. Concise, factual, no hedging.",
            },
            "source": {
                "type": "string",
                "description": "Where this came from (URL, doc, error msg).",
            },
            "confidence": {
                "type": "number",
                "description": "0.0-1.0; how confident the agent is in this fact.",
            },
        },
        "required": ["topic", "content"],
    },
)
async def knowledge_add(
    topic: str, content: str, source: str = "agent", confidence: float = 0.7,
) -> dict[str, Any]:
    kb = _get_kb()
    return kb.add(topic, content, source=source, confidence=confidence)


@tool(
    name="knowledge.search",
    description=(
        "BM25 search the local knowledge base. Returns facts previously "
        "learned that match the query. Use this BEFORE web.search when "
        "the topic might have been encountered before — the KB is local "
        "and instant, web is slow and uncertain."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "top_k": {"type": "integer", "description": "Max results. Default 5."},
            "topic_filter": {
                "type": "string",
                "description": "Optional: only return facts whose topic contains this substring.",
            },
        },
        "required": ["query"],
    },
)
async def knowledge_search(
    query: str, top_k: int = 5, topic_filter: str | None = None,
) -> dict[str, Any]:
    kb = _get_kb()
    results = kb.search(query, top_k=top_k, topic_filter=topic_filter)
    return {
        "ok": True,
        "query": query,
        "n_results": len(results),
        "results": results,
        "hint": (
            "If results are empty, the topic is novel — fall back to "
            "cognitive.investigate_deep or web.search."
        ),
    }


@tool(
    name="knowledge.list",
    description="List recent facts in the KB (for debugging / audit).",
    parameters={
        "type": "object",
        "properties": {
            "n": {"type": "integer", "description": "How many. Default 20."},
        },
    },
)
async def knowledge_list(n: int = 20) -> dict[str, Any]:
    kb = _get_kb()
    return {
        "ok": True,
        "stats": kb.stats(),
        "recent": kb.list_recent(n),
    }


__all__ = ["knowledge_add", "knowledge_search", "knowledge_list"]
