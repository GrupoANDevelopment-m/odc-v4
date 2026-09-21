"""Dynamic prompt assembly for ODC v4.

The agent's context window is precious. Instead of dumping the entire
catalog (41 tools, 6 skills, full profile) into every prompt, we
assemble the system prompt in 4 layers, each independently sized:

- Layer 0: Core (always, ~300 tokens) — identity + 5 rules
- Layer 1: Task-context (~500 tokens) — matched skills + thread facts
- Layer 2: Turn-context (~400 tokens) — tool subset (5-8 of 41)
- Layer 3: Conversation tail — last messages verbatim, older summarized

Conversation isolation: by default, the builder only injects data
from the CURRENT thread. The global profile is read ONLY for cross-
thread-promoted heuristics AND only when the user's task references a
past conversation explicitly, OR when the heuristic has been seen
in this exact task's deterministic thread_id before.
"""
from odc.prompt.builder import (
    LAYER_0_CORE_BASE,
    TOOL_DISCOVER_NAME,
    build_dynamic_prompt,
    build_layer_1,
    build_layer_2,
    build_layer_3,
    build_tool_discover_tool_description,
    estimate_tokens,
)
from odc.prompt.bm25 import BM25, build_corpus_from_skills, build_corpus_from_tools
from odc.prompt.scope import (
    ThreadContext,
    heuristics_safe_to_inject,
    investigations_safe_to_inject,
    user_references_past,
)


__all__ = [
    "BM25",
    "LAYER_0_CORE_BASE",
    "ThreadContext",
    "TOOL_DISCOVER_NAME",
    "build_corpus_from_skills",
    "build_corpus_from_tools",
    "build_dynamic_prompt",
    "build_layer_1",
    "build_layer_2",
    "build_layer_3",
    "build_tool_discover_tool_description",
    "estimate_tokens",
    "heuristics_safe_to_inject",
    "investigations_safe_to_inject",
    "user_references_past",
]
