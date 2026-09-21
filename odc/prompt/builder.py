"""Dynamic prompt builder — the ITR pattern (Instruction-Tool Retrieval).

Composes the system prompt in 4 layers, each independently sized:

  Layer 0: Core (always, ~300 tokens) — identity + 5 rules + safety
  Layer 1: Task-context (~500 tokens) — matched skills + thread facts
  Layer 2: Turn-context (~400 tokens) — tool subset (5-8 of 41)
  Layer 3: Conversation tail — last messages verbatim, older summarized

Result: ~1.2K tokens vs the current 3-4K. The 8B Nemotron stops
losing the thread.

Conversation isolation: by default, the builder only injects data
from the CURRENT thread (ThreadContext). The global profile is
read ONLY for cross-thread-promoted heuristics (validated in 2+
threads) AND only when the user's task references a past conversation
explicitly, OR when the heuristic has been seen in this exact task's
deterministic thread_id before.
"""
from __future__ import annotations

import re
from typing import Any

from odc.prompt.bm25 import BM25, build_corpus_from_skills, build_corpus_from_tools
from odc.prompt.osiris_frame import frame_recalled_memory, detect_pam_breach
from odc.prompt.scope import (
    ThreadContext,
    heuristics_safe_to_inject,
    investigations_safe_to_inject,
    user_references_past,
)


# Layer 0 — Core. Always present. ~300 tokens base.
# Stable prefix → friendly to prompt caching if the provider supports it.
# Identity (name, voice, mannerisms, user_nickname) is INJECTED HERE
# by build_dynamic_prompt, not hardcoded — because the agent's
# identity is a per-deployment concern. The base rules are constant.
LAYER_0_CORE_BASE = """\
Core rules:
1. Use the tools that the current layer exposed — do not invent tools.
2. If a tool fails, read the failure carefully; you may try once with
   a different approach, but do not loop on the same call.
3. When writing code, prefer dynamic.tool_create over shell hacks.
4. When researching, cite the URL you actually read.
5. Stay within scope. If the task is done, say it's done.

Safety:
- You do not exfiltrate secrets, do not run destructive commands, and
  do not pretend to do things you did not do.
"""


# Layer 0b — Tool catalog header. Brief, points to Layer 2 for the full
# schemas. ~30 tokens.
LAYER_0B_TOOL_HEADER = """\
Tools available for THIS turn are listed in the [TOOL SUBSET] section
below. Use ONLY those tools. To ask for a tool not in the subset, call
tool.discover(query=...) to expand the set.
"""


# Per-turn "discover" tool — the LLM can ask for more tools on demand
TOOL_DISCOVER_NAME = "tool.discover"


def build_tool_discover_tool_description() -> dict[str, Any]:
    """The discover tool: lets the LLM request new tools by query."""
    return {
        "name": TOOL_DISCOVER_NAME,
        "description": (
            "Expand the current tool subset. Pass a keyword query and "
            "the most relevant tools (up to 5) will be added to the "
            "active set for this turn and the next. Use this when the "
            "task needs a tool that isn't currently in your subset."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword query describing the tool you need, e.g. 'git history' or 'json parse'.",
                },
                "max_tools": {
                    "type": "integer",
                    "description": "Maximum number of tools to add. Default 3.",
                },
            },
            "required": ["query"],
        },
    }


# ---------------------------------------------------------------------------
# Layer 1 — task-context assembly
# ---------------------------------------------------------------------------


def build_layer_1(
    task: str,
    thread_ctx: ThreadContext,
    skills: dict[str, Any],
    profile_data: dict[str, Any],
    *,
    max_skills: int = 2,
    max_heuristics: int = 2,
    max_investigations: int = 2,
) -> str:
    """Assemble the per-task context layer.

    - Matched skills via BM25 (max N)
    - Thread-scoped facts/plan (always)
    - Heuristics (only from current thread OR promoted cross-thread)
    - Investigations (from current thread + matching tools)
    """
    parts: list[str] = ["[TASK CONTEXT]"]

    # 1. Skills (BM25-matched)
    if skills:
        corpus, names = build_corpus_from_skills(skills)
        ranker = BM25(corpus)
        ranked = ranker.rank(task, top_k=max_skills, min_score=0.0)
        if ranked:
            parts.append("\n### Active skills (matched to this task)")
            for idx in ranked:
                name = names[idx]
                skill = skills[name]
                body = getattr(skill, "body", "") or ""
                if not isinstance(body, str):
                    body = str(body)
                # Compact: first 600 chars
                parts.append(f"- **{name}**: {body[:600].strip()}")

    # 2. Thread facts (always thread-scoped)
    brief = thread_ctx.to_brief()
    if brief:
        parts.append("\n### Current thread state (this conversation only)")
        parts.append(brief)

    # 3. Heuristics — gated by scope rules AND wrapped in PAM framing
    hs = heuristics_safe_to_inject(profile_data, task, min_cross_threads=1)
    if hs:
        parts.append("\n### Past heuristics (validated, framed as data)")
        framed_h = frame_recalled_memory(
            [{"rule": h["rule"], "approach": h.get("approach", "")} for h in hs[:max_heuristics]],
            block_types=["heuristic"] * min(max_heuristics, len(hs)),
        )
        parts.append(framed_h)

    # 4. Investigations — only matching tools, recent first, framed
    invs = investigations_safe_to_inject(profile_data, task)
    if invs:
        parts.append("\n### Wisdom from past failures (framed as data)")
        inv_dicts = []
        for inv in invs[:max_investigations]:
            inv_dicts.append({
                "why": inv.get("why", ""),
                "next_approach": inv.get("next_approach", ""),
                "mitigations": " | ".join(inv.get("mitigations", [])[:3]),
            })
        framed_inv = frame_recalled_memory(inv_dicts, block_types=["failure"] * len(inv_dicts))
        parts.append(framed_inv)

    return "\n".join(parts) if len(parts) > 1 else ""


# ---------------------------------------------------------------------------
# Layer 2 — turn-context: tool subset
# ---------------------------------------------------------------------------


def build_layer_2(
    task: str,
    all_tools: list[Any],
    recent_tool_calls: list[str] | None = None,
    *,
    initial_k: int = 5,
    expand_k: int = 3,
) -> tuple[str, list[str]]:
    """Build the per-turn tool subset.

    Returns (prompt_text, list_of_included_tool_names).

    The subset is BM25-ranked against the task. If recent tool calls
    suggest the agent is going down a different path, those tools
    are also included (continuity).

    IMPORTANT: core self-extension tools (dynamic.tool_create,
    tool.discover, cognitive.*) are always included so the agent
    can never get STUCK without the ability to build new tools or
    reflect. The 8B model would otherwise get trapped when the
    initial subset doesn't match its actual need.

    OSINT proactive injection: when the task is about real-world data
    (flights, earthquakes, news, weather, BTC, etc.), inject OSINT
    tools proactively — don't wait for the agent to discover them.
    """
    if not all_tools:
        return "[TOOL SUBSET]\n(no tools available)\n", []

    # Tools that must ALWAYS be in the subset — these are the
    # self-extension and reflection primitives. Without them the
    # agent can't escape a bad initial subset.
    ALWAYS_INCLUDED = {
        "dynamic.tool_create",
        "dynamic.tool_repair",
        "dynamic.tool_list",
        "tool.discover",
        "cognitive.think",
        "cognitive.route",
        "cognitive.reflect",
        "fs.read",        # most tasks need to read at some point
    }

    # OSINT tools that get INJECTED when the task is about real-world
    # data. The agent shouldn't have to call tool.discover() for these.
    OSINT_TASK_PATTERNS: dict[str, list[str]] = {
        "osint.flights":        ["flight", "aircraft", "plane", "aviao", "voo", "avioes", "airport"],
        "osint.earthquakes":    ["earthquake", "quake", "sismo", "terremoto", "seism"],
        "osint.cve":            ["cve", "vulnerability", "vulnerabilidade", "exploit", "security flaw"],
        "osint.bitcoin":        ["bitcoin", "btc", "mempool", "block", "miner"],
        "osint.crypto_prices":  ["crypto", "price", "preco", "bitcoin price", "ethereum price",
                                 "btc price", "eth price", "sol price", "criptomoeda"],
        "osint.space_weather":  ["solar", "sunspot", "geomagnetic", "solar flare", "kp index"],
        "osint.weather":        ["weather", "tempo", "clima", "temperature", "temperatura",
                                 "humidity", "wind", "rain", "forecast"],
        "osint.wikipedia":      ["wikipedia", "wiki", "encyclopedia", "enciclopedia"],
        "osint.news":           ["news", "noticias", "notícia", "headline", "article", "artigo"],
        "osint.sanctions":      ["sanctions", "sancoes", "ofac", "sdn", "designated"],
        "osint.satellites":     ["satellite", "satelite", "iss", "starlink"],
        "osint.fires":          ["fire", "fogo", "wildfire", "incendio", "hotspot", "firms"],
        "osint.eonet":          ["eonet", "natural event", "desastre", "disaster", "volcano"],
    }

    corpus, names = build_corpus_from_tools(all_tools)
    ranker = BM25(corpus)
    # Initial: top-K by task match
    initial_idx = ranker.rank(task, top_k=initial_k, min_score=0.0)
    selected: list[int] = list(initial_idx)
    # Always-on tools (self-extension + reflection)
    for i, n in enumerate(names):
        if n in ALWAYS_INCLUDED and i not in selected:
            # Don't push out task-matched tools, just append
            selected.append(i)
    # OSINT proactive injection — when task is about real-world data,
    # inject the relevant OSINT tools without requiring tool.discover().
    task_lower = task.lower()
    for tool_name, keywords in OSINT_TASK_PATTERNS.items():
        if any(kw in task_lower for kw in keywords):
            # find tool by name and add
            for i, n in enumerate(names):
                if n == tool_name and i not in selected:
                    selected.append(i)
                    break
    # Continuity: if recent tool calls include tools not yet selected,
    # add the top expand_k of them. This prevents thrashing when the
    # agent is mid-execution.
    if recent_tool_calls:
        recent_idx = [i for i, n in enumerate(names) if n in recent_tool_calls]
        for i in recent_idx:
            if i not in selected:
                selected.append(i)
                if len(selected) >= initial_k + expand_k + len(ALWAYS_INCLUDED):
                    break

    # Cap at reasonable size
    cap = initial_k + expand_k + len(ALWAYS_INCLUDED)
    selected = selected[:cap]

    selected_names = [names[i] for i in selected]
    parts = ["[TOOL SUBSET]", f"Active tools ({len(selected_names)} of {len(all_tools)}):"]
    for n in selected_names:
        parts.append(f"- {n}")
    parts.append("")
    parts.append(
        "To request tools not listed: call "
        f"{TOOL_DISCOVER_NAME}(query=...) to expand the set."
    )
    return "\n".join(parts), selected_names


# ---------------------------------------------------------------------------
# Layer 3 — conversation tail
# ---------------------------------------------------------------------------


def build_layer_3(
    messages: list[Any],
    *,
    keep_last: int = 6,
    summary_tail: str = "",
) -> str:
    """Render the conversation tail. Older messages are summarized;
    the most recent keep_last messages are kept verbatim.

    For Phase 3, the summarization will use LLMLingua-2 or extractive
    compression. For now, we use a simple 'earlier summary' field
    stored in ThreadContext.
    """
    parts: list[str] = ["[CONVERSATION]"]
    if summary_tail:
        parts.append("Earlier in this conversation (summarized):")
        parts.append(summary_tail)
        parts.append("---")
    # Keep last N messages verbatim
    tail = messages[-keep_last:] if len(messages) > keep_last else messages
    for m in tail:
        role = getattr(m, "role", "?")
        content = getattr(m, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        if len(content) > 800:
            content = content[:400] + " … " + content[-200:]
        parts.append(f"[{role}] {content}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Top-level assembler
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token. Good enough for budget."""
    return max(1, len(text) // 4)


def build_dynamic_prompt(
    task: str,
    thread_ctx: ThreadContext,
    all_tools: list[Any],
    skills: dict[str, Any],
    profile_data: dict[str, Any],
    messages: list[Any] | None = None,
    recent_tool_calls: list[str] | None = None,
    *,
    identity_block: str = "",
    max_total_tokens: int = 4000,
) -> dict[str, Any]:
    """Assemble the full dynamic system prompt.

    Returns dict with:
        - system_prompt: str (the assembled prompt)
        - layers: dict (per-layer sizes and content for debugging)
        - selected_tools: list[str] (the tools currently in scope)
        - token_estimate: int
        - referenced_past: bool (whether the user referenced a past conversation)
    """
    # Layer 0 — always: identity (if any) + base rules + tool header
    layer0 = (identity_block or "") + LAYER_0_CORE_BASE + "\n" + LAYER_0B_TOOL_HEADER
    # Filter the 'identity' skill out of Layer 1 because it's already
    # in Layer 0 (avoid duplication).
    skills_for_layer1 = {
        n: s for n, s in skills.items() if n != "identity"
    }
    # Layer 1 — task context
    layer1 = build_layer_1(
        task, thread_ctx, skills_for_layer1, profile_data,
    )
    # Layer 2 — tool subset
    layer2, selected = build_layer_2(
        task, all_tools, recent_tool_calls or [],
    )
    # Layer 3 — conversation tail
    layer3 = build_layer_3(
        messages or [],
        summary_tail=thread_ctx.data.get("summary_tail", ""),
    )

    # Assemble
    full = "\n\n".join(
        part for part in [layer0, layer1, layer2, layer3] if part
    )

    # Track per-layer for observability
    layers = {
        "0_core": estimate_tokens(layer0),
        "1_task": estimate_tokens(layer1),
        "2_tools": estimate_tokens(layer2),
        "3_conv": estimate_tokens(layer3),
    }
    token_est = sum(layers.values())

    # If over budget, trim Layer 3 (older messages), then Layer 1 (heuristics)
    if token_est > max_total_tokens:
        # Phase 3: re-build Layer 3 with summarization of older messages
        from odc.prompt.summarize import summarize_messages
        if messages and len(messages) > 6:
            tail = messages[-4:]
            older = messages[:-4]
            summary = summarize_messages(older, max_sentences=6)
            # Persist the summary into the thread context
            thread_ctx.set_summary_tail(summary)
            try:
                thread_ctx.save()
            except Exception:
                pass
            layer3 = build_layer_3(
                tail, keep_last=4, summary_tail=summary,
            )
        else:
            layer3 = build_layer_3(
                messages or [], keep_last=4,
                summary_tail=thread_ctx.data.get("summary_tail", ""),
            )
        full = "\n\n".join(
            p for p in [layer0, layer1, layer2, layer3] if p
        )
        layers["3_conv"] = estimate_tokens(layer3)
        token_est = sum(layers.values())

    return {
        "system_prompt": full,
        "layers": layers,
        "selected_tools": selected,
        "token_estimate": token_est,
        "referenced_past": user_references_past(task),
    }


__all__ = [
    "build_dynamic_prompt",
    "build_layer_1",
    "build_layer_2",
    "build_layer_3",
    "build_tool_discover_tool_description",
    "TOOL_DISCOVER_NAME",
    "estimate_tokens",
    "LAYER_0_CORE_BASE",
]
