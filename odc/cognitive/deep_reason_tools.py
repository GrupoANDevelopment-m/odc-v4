"""Tool wrappers for the DEEP-REASON System 2 layer.

Five tools the LLM can call (or the loop can call automatically):
  - cognitive.council     : 5-perspective reasoning
  - cognitive.revise      : steel-man / epistemic humility
  - cognitive.analogy     : real-world analog
  - cognitive.hypothesize_v2 : multi-hypothesis tree
  - cognitive.decompose   : task DAG

Plus a sync `system2_fallback()` that the loop can call when
System 1 fails repeatedly. It runs the cascade: council →
revise → investigate_deep → analogy.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from odc.tools.base import tool


# ---------------------------------------------------------------------------
# Async LLM call helper (works inside or outside an event loop)
# ---------------------------------------------------------------------------


async def _llm_chat(provider, messages, *, max_tokens=1500, temperature=0.3):
    return await provider.chat(
        messages, tools=None, max_tokens=max_tokens, temperature=temperature
    )


def _call_llm_sync(provider, messages, **kwargs):
    """Call the LLM from a sync context. Creates a one-shot loop if needed."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(asyncio.run, _llm_chat(provider, messages, **kwargs))
                return fut.result(timeout=180)
    except RuntimeError:
        pass
    return asyncio.run(_llm_chat(provider, messages, **kwargs))


def _get_provider():
    from odc.cognitive.tools import LLM_PROVIDER
    return LLM_PROVIDER


# ---------------------------------------------------------------------------
# cognitive.council
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.council",
    description=(
        "Call the Council of Lenses — 5 independent perspectives "
        "(expert, hacker, researcher, developer, investigator) "
        "consulted in parallel. Use when: tool failed multiple times, "
        "you don't know which approach to take, or you need creative "
        "alternatives. Returns structured JSON with per-lens answers "
        "and a synthesis. System 2 fallback for hard problems."
    ),
    parameters={
        "type": "object",
        "properties": {
            "problem": {"type": "string", "description": "The specific problem you're stuck on."},
            "context": {"type": "string", "description": "What you've already tried and what failed."},
        },
        "required": ["problem"],
    },
)
async def cognitive_council(problem: str, context: str = "") -> dict[str, Any]:
    from odc.cognitive.council import build_council_prompt, parse_council_response
    provider = _get_provider()
    if provider is None:
        return {"offline": True, "hint": "no provider configured"}
    prompt = build_council_prompt(problem, context)
    from odc.llm.provider import Message
    msgs = [
        Message(role="system", content="You are a strict JSON generator. Output ONLY the JSON object requested. No prose, no markdown fences."),
        Message(role="user", content=prompt),
    ]
    completion = await _llm_chat(provider, msgs, max_tokens=1500, temperature=0.3)
    text = completion.text if hasattr(completion, "text") else str(completion)
    return parse_council_response(text)


# ---------------------------------------------------------------------------
# cognitive.revise — epistemic humility
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.revise",
    description=(
        "Steel-man exercise. Force yourself to attack your own "
        "hypothesis, list blind spots, propose alternative variants, "
        "state falsification criteria, and recalibrate confidence. "
        "Use when: you've been retrying the same approach and it's "
        "still failing. The output tells you whether to change "
        "approach entirely."
    ),
    parameters={
        "type": "object",
        "properties": {
            "current_hypothesis": {"type": "string", "description": "What you currently believe."},
            "evidence": {"type": "string", "description": "What you've observed so far."},
            "failures": {"type": "string", "description": "What has failed (specifics help)."},
        },
        "required": ["current_hypothesis"],
    },
)
async def cognitive_revise(
    current_hypothesis: str, evidence: str = "", failures: str = "",
) -> dict[str, Any]:
    from odc.cognitive.revise import build_revise_prompt, parse_revise_response
    provider = _get_provider()
    if provider is None:
        return {"offline": True}
    prompt = build_revise_prompt(current_hypothesis, evidence, failures)
    from odc.llm.provider import Message
    msgs = [
        Message(role="system", content="You are a strict JSON generator. Output ONLY the JSON object requested. No prose, no markdown fences."),
        Message(role="user", content=prompt),
    ]
    completion = await _llm_chat(provider, msgs, max_tokens=1500, temperature=0.4)
    text = completion.text if hasattr(completion, "text") else str(completion)
    return parse_revise_response(text)


# ---------------------------------------------------------------------------
# cognitive.analogy
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.analogy",
    description=(
        "Find a real-world analog from human history, biology, "
        "business, or social dynamics. The analog should illuminate "
        "something the obvious approaches are missing. Returns 1-3 "
        "analogs with mechanism + mapping + concrete tactic. Use "
        "when: you're stuck on a problem that feels like it has a "
        "known shape from elsewhere."
    ),
    parameters={
        "type": "object",
        "properties": {
            "problem": {"type": "string", "description": "The problem to find an analog for."},
            "failures": {"type": "string", "description": "What hasn't worked."},
        },
        "required": ["problem"],
    },
)
async def cognitive_analogy(problem: str, failures: str = "") -> dict[str, Any]:
    from odc.cognitive.analogy import build_analogy_prompt, parse_analogy_response
    provider = _get_provider()
    if provider is None:
        return {"offline": True, "analogs": []}
    prompt = build_analogy_prompt(problem, failures)
    from odc.llm.provider import Message
    msgs = [
        Message(role="system", content="You are a strict JSON generator. Output ONLY the JSON object requested. No prose, no markdown fences."),
        Message(role="user", content=prompt),
    ]
    completion = await _llm_chat(provider, msgs, max_tokens=1500, temperature=0.5)
    text = completion.text if hasattr(completion, "text") else str(completion)
    return parse_analogy_response(text)


# ---------------------------------------------------------------------------
# cognitive.hypothesize_v2 — multi-hypothesis tree
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.hypothesize_v2",
    description=(
        "Generate N competing hypotheses (default 3) for a problem. "
        "Each hypothesis has a test (concrete tool call that would "
        "confirm/refute it) and a prior. Returns a ranked list to "
        "test in order. Use instead of single-shot hypothesis when "
        "the problem is ambiguous or the LLM might be wrong."
    ),
    parameters={
        "type": "object",
        "properties": {
            "problem": {"type": "string"},
            "context": {"type": "string", "description": "What you've observed so far."},
            "n": {"type": "integer", "description": "How many hypotheses. Default 3, max 5."},
        },
        "required": ["problem"],
    },
)
async def cognitive_hypothesize_v2(
    problem: str, context: str = "", n: int = 3,
) -> dict[str, Any]:
    from odc.cognitive.hypothesize_v2 import build_hypothesize_prompt, parse_hypotheses_response
    provider = _get_provider()
    if provider is None:
        return {"offline": True, "hypotheses": []}
    prompt = build_hypothesize_prompt(problem, context, n=n)
    from odc.llm.provider import Message
    msgs = [
        Message(role="system", content="You are a strict JSON generator. Output ONLY the JSON object requested. No prose, no markdown fences."),
        Message(role="user", content=prompt),
    ]
    completion = await _llm_chat(provider, msgs, max_tokens=2000, temperature=0.4)
    text = completion.text if hasattr(completion, "text") else str(completion)
    return parse_hypotheses_response(text, n_expected=n)


# ---------------------------------------------------------------------------
# cognitive.decompose — task DAG
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.decompose",
    description=(
        "Break a complex task into a DAG of sub-tasks (3-8). Each "
        "sub-task has an acceptance criterion. Returns a topo-sorted "
        "execution order. Use for tasks that need more than 5-6 "
        "tool calls."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string"},
        },
        "required": ["task"],
    },
)
async def cognitive_decompose(task: str) -> dict[str, Any]:
    from odc.cognitive.decompose import build_decompose_prompt, parse_decompose_response, validate_dag
    provider = _get_provider()
    if provider is None:
        return {"offline": True, "sub_tasks": []}
    prompt = build_decompose_prompt(task)
    from odc.llm.provider import Message
    msgs = [
        Message(role="system", content="You are a strict JSON generator. Output ONLY the JSON object requested. No prose, no markdown fences."),
        Message(role="user", content=prompt),
    ]
    completion = await _llm_chat(provider, msgs, max_tokens=2000, temperature=0.3)
    text = completion.text if hasattr(completion, "text") else str(completion)
    parsed = parse_decompose_response(text)
    ok, err = validate_dag(parsed)
    if not ok:
        parsed["validation_error"] = err
    return parsed


# ---------------------------------------------------------------------------
# cognitive.investigate_deep — research plan
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.investigate_deep",
    description=(
        "Returns a multi-step deep-research plan: web.search with "
        "multiple query formulations, web.fetch against authoritative "
        "sources, arXiv, local KB. Use when System 1 web.search "
        "didn't yield enough. Plan is bounded (max 6 steps) so it "
        "stays inside the turn budget."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_attempts": {"type": "integer", "description": "Max steps. Default 6."},
        },
        "required": ["query"],
    },
)
async def cognitive_investigate_deep(query: str, max_attempts: int = 6) -> dict[str, Any]:
    from odc.cognitive.investigate_deep import deep_investigate_plan, deep_investigate_summary
    plan = deep_investigate_plan(query, max_attempts=max_attempts)
    return {
        "ok": True,
        "query": query,
        "n_steps": len(plan),
        "plan": plan,
        "summary": deep_investigate_summary(plan),
        "hint": (
            "Execute the steps in order. Stop as soon as a step "
            "produces actionable evidence (per the 'stop_if' clause). "
            "Store any useful findings with knowledge.add."
        ),
    }


__all__ = [
    "cognitive_council",
    "cognitive_revise",
    "cognitive_analogy",
    "cognitive_hypothesize_v2",
    "cognitive_decompose",
    "cognitive_investigate_deep",
]
