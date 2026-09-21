"""Tool wrapper for cognitive.council — exposes the council as a tool
the LLM can call explicitly. Also provides a sync wrapper for the
loop to call automatically when System 1 fails.
"""
from __future__ import annotations

import json
import time
from typing import Any

from odc.cognitive.council import (
    build_council_prompt,
    parse_council_response,
    render_council_for_prompt,
)
from odc.tools.base import tool


# Tiny in-process cache so the same (problem, context[:200]) within 60s
# doesn't re-invoke the LLM council. Cache is per-process; not persistent.
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 60.0


def _cache_key(problem: str, context: str) -> str:
    return f"{problem.strip()[:300]}|{context.strip()[:200]}"


def consult_council(
    problem: str,
    context: str = "",
    *,
    provider: Any = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Run the council and return a structured result.

    If `provider` is None, returns the parsed sample (only useful in
    tests). In production, the loop passes the live LLM provider.
    """
    key = _cache_key(problem, context)
    now = time.time()
    if use_cache and key in _CACHE:
        ts, cached = _CACHE[key]
        if now - ts < _CACHE_TTL:
            return cached
    prompt = build_council_prompt(problem, context)
    if provider is None:
        # Offline: return a placeholder so tests can still call this
        return {
            "offline": True,
            "prompt": prompt[:1000],
            "perspectives": {},
            "synthesis": "(no provider — would call LLM here)",
            "confidence": 0.0,
        }
    # Call the provider synchronously via asyncio
    import asyncio
    from odc.llm.provider import Message
    msg = [
        Message(role="system", content="You are a strict JSON generator. Output ONLY the JSON object requested. No prose, no markdown fences."),
        Message(role="user", content=prompt),
    ]
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already inside an event loop (loop.py case). Use a thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(asyncio.run, _async_consult(provider, msg))
                completion = fut.result(timeout=120)
        else:
            completion = asyncio.run(_async_consult(provider, msg))
    except RuntimeError:
        # No event loop at all
        completion = asyncio.run(_async_consult(provider, msg))
    text = completion.text if hasattr(completion, "text") else str(completion)
    result = parse_council_response(text)
    if use_cache:
        _CACHE[key] = (now, result)
    return result


async def _async_consult(provider: Any, messages: list) -> Any:
    return await provider.chat(messages, tools=None, temperature=0.3, max_tokens=1500)


@tool(
    name="cognitive.council",
    description=(
        "Call the Council of Lenses when you are stuck. The council "
        "consults 5 independent perspectives (expert, hacker, researcher, "
        "developer, investigator) and synthesizes a recommendation. "
        "Use this when: a tool failed multiple times, you don't know "
        "which approach to take, or you need creative alternatives. "
        "Returns structured JSON with per-lens answers and a synthesis."
    ),
    parameters={
        "type": "object",
        "properties": {
            "problem": {
                "type": "string",
                "description": "The problem you're stuck on. Be specific.",
            },
            "context": {
                "type": "string",
                "description": "What you've already tried and what failed.",
            },
        },
        "required": ["problem"],
    },
)
async def cognitive_council(problem: str, context: str = "") -> dict[str, Any]:
    from odc.cognitive.tools import LLM_PROVIDER  # module-level provider
    result = consult_council(
        problem=problem, context=context, provider=LLM_PROVIDER,
    )
    return result


__all__ = ["cognitive_council", "consult_council", "render_council_for_prompt"]
