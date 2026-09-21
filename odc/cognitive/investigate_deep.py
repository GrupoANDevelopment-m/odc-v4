"""Deep research loop — when System 1 + council fail, go DEEPER.

The cascade is:
  1. web.search with multiple formulations of the question
  2. web.fetch against known authoritative sources
     (github, stackoverflow, official docs, blogs)
  3. arXiv / papers via the open access endpoints
  4. Local knowledge base (knowledge.add / knowledge.search)

The loop stops as soon as one step yields actionable evidence.
Each step is bounded (max_attempts, max_seconds) so the agent
doesn't burn the entire task budget on research.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any


# Authoritative sources per topic. We try them in order; first hit wins.
SOURCE_DOMAINS = {
    "python":      ["docs.python.org", "stackoverflow.com", "realpython.com"],
    "http":        ["httpbin.org", "developer.mozilla.org", "stackoverflow.com"],
    "github":      ["github.com", "raw.githubusercontent.com"],
    "arxiv":       ["arxiv.org", "export.arxiv.org"],
    "ml":          ["arxiv.org", "paperswithcode.com", "distill.pub"],
    "general":     ["wikipedia.org", "github.com", "stackoverflow.com"],
}


def classify_topic(query: str) -> str:
    """Best-effort topic classification to pick a source list."""
    q = query.lower()
    if any(w in q for w in ("python", "import ", "pip ", "venv ")):
        return "python"
    if any(w in q for w in ("http", "post ", "get ", "api ", "rest ")):
        return "http"
    if "arxiv" in q or "paper" in q:
        return "arxiv"
    # ML signals before generic "research" — RLHF, transformer, etc. are
    # more specific than a generic "research" query.
    if any(w in q for w in ("model", "training", "fine-tun", "transformer", "llm", "rlhf")):
        return "ml"
    if "research" in q:
        return "arxiv"
    if "github" in q or "repo" in q or "repository" in q:
        return "github"
    return "general"


def reformulate_query(query: str, n: int = 3) -> list[str]:
    """Generate n search query variants to widen the net.
    Cheap heuristic: add site: filter, swap synonyms, drop filler."""
    out = [query.strip()]
    if n >= 2:
        # Drop common filler words
        out.append(re.sub(r"\b(como|how|to|the|a|an|please|me|diga|qual)\b", "", query, flags=re.I).strip())
    if n >= 3:
        # Quote the most specific term (capitalized or quoted)
        m = re.search(r'"([^"]+)"', query)
        if m:
            out.append(f"{m.group(1)} example")
    if n >= 4:
        # Add "site:stackoverflow.com" for code-y queries
        if any(w in query.lower() for w in ("erro", "error", "exception", "fail")):
            out.append(f"{query} site:stackoverflow.com")
    return out[:n]


def build_arxiv_url(query: str) -> str:
    """Return an arXiv search URL (no API key needed)."""
    return "https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(
        "all:" + query
    ) + "&max_results=5"


def deep_investigate_plan(
    query: str,
    *,
    max_attempts: int = 6,
    max_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Return a *plan* of investigation steps the loop can execute.
    Each step is a dict with: step_id, action, params, expected_yield.

    The plan is structured so the loop can stop as soon as any step
    produces actionable evidence. This keeps the research bounded.
    """
    topic = classify_topic(query)
    sources = SOURCE_DOMAINS.get(topic, SOURCE_DOMAINS["general"])
    queries = reformulate_query(query, n=min(3, max_attempts))
    plan: list[dict[str, Any]] = []
    # 1) web.search variants
    for i, q in enumerate(queries):
        plan.append({
            "step_id": f"search_{i+1}",
            "action": "web.search",
            "params": {"query": q, "max_results": 5},
            "expected_yield": "URLs and snippets matching the query",
            "stop_if": "found >= 1 result with code snippet or official doc URL",
        })
    # 2) web.fetch against known authoritative sources
    for i, dom in enumerate(sources[:3]):
        plan.append({
            "step_id": f"fetch_{dom.replace('.', '_')}",
            "action": "web.fetch",
            "params": {"url": f"https://www.google.com/search?q=site:{dom}+{urllib.parse.quote(query)}"},
            "expected_yield": f"results from {dom}",
            "stop_if": "fetched 200 with relevant content",
        })
    # 3) arXiv (if the topic is research-y)
    if topic in ("arxiv", "ml") or "paper" in query.lower() or "research" in query.lower():
        plan.append({
            "step_id": "arxiv_search",
            "action": "web.fetch",
            "params": {"url": build_arxiv_url(query)},
            "expected_yield": "XML feed of arXiv papers matching the query",
            "stop_if": "found at least 1 paper title + abstract",
        })
    # 4) knowledge base lookup
    plan.append({
        "step_id": "kb_lookup",
        "action": "knowledge.search",
        "params": {"query": query, "max_results": 5},
        "expected_yield": "facts previously learned about this topic",
        "stop_if": "found >= 1 high-relevance fact",
    })
    return plan


def deep_investigate_summary(plan: list[dict[str, Any]]) -> str:
    """Render the plan as a compact instruction block for the LLM."""
    lines = ["[DEEP RESEARCH PLAN — System 2 fallback]"]
    lines.append(
        "System 1 has failed or is insufficient. Follow this plan "
        "in order, stopping as soon as a step produces actionable "
        "evidence (per the 'stop_if' clause):"
    )
    for step in plan:
        lines.append(
            f"\n{step['step_id']}. {step['action']}({json.dumps(step['params'])})"
        )
        lines.append(f"   expected: {step['expected_yield']}")
        if step.get("stop_if"):
            lines.append(f"   stop if: {step['stop_if']}")
    return "\n".join(lines)


__all__ = [
    "classify_topic",
    "reformulate_query",
    "SOURCE_DOMAINS",
    "build_arxiv_url",
    "deep_investigate_plan",
    "deep_investigate_summary",
]
