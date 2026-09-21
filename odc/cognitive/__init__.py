"""Cognitive tools: metacognition, hypothesis testing, self-improvement.

The agent loop is reactive by default (tool call → tool call). This
module adds the missing *intuitive* layer:

- think: explicit recorded thought (the "inner voice")
- simulate: run a safe Python expression to test a thought
- hypothesize: form a hypothesis, run a real tool to test it, return confirmation
- self_assess: review recent tool calls, identify capability gaps, write a new skill
- route: suggest the best approach for a new task based on past experience
- reflect: record what was learned after a task (updates the profile)
- strengthen: explicitly capture a lesson or rule that should persist
- observe: record a behavior pattern (sequence of tool calls) for later imitation
- imitate: find the best matching observed pattern for a target task

The tools are registered alongside the rest. They are read-only /
soft-side-effect, so they are safe by default (no confirm prompt).
"""
from __future__ import annotations

from odc.cognitive.tools import (
    cognitive_distill,
    cognitive_hypothesize,
    cognitive_imitate,
    cognitive_investigate,
    cognitive_observe,
    cognitive_reflect,
    cognitive_route,
    cognitive_self_assess,
    cognitive_simulate,
    cognitive_strengthen,
    cognitive_think,
)
from odc.cognitive.council_tool import cognitive_council as _council_v1  # alias kept
from odc.cognitive.deep_reason_tools import (
    cognitive_analogy,
    cognitive_council,
    cognitive_decompose,
    cognitive_hypothesize_v2,
    cognitive_investigate_deep,
    cognitive_revise,
)
from odc.cognitive.knowledge_tool import (
    knowledge_add,
    knowledge_list,
    knowledge_search,
)


def all_cognitive_tools() -> list:
    """Return all cognitive tools, ready to register."""
    return [
        cognitive_think,
        cognitive_simulate,
        cognitive_hypothesize,
        cognitive_self_assess,
        cognitive_route,
        cognitive_reflect,
        cognitive_strengthen,
        cognitive_observe,
        cognitive_imitate,
        cognitive_investigate,
        cognitive_distill,
        # DEEP-REASON System 2 layer
        cognitive_council,
        cognitive_revise,
        cognitive_analogy,
        cognitive_hypothesize_v2,
        cognitive_decompose,
        cognitive_investigate_deep,
        # Knowledge base
        knowledge_add,
        knowledge_search,
        knowledge_list,
    ]


__all__ = [
    "all_cognitive_tools",
    "cognitive_analogy",
    "cognitive_council",
    "cognitive_decompose",
    "cognitive_distill",
    "cognitive_hypothesize",
    "cognitive_hypothesize_v2",
    "cognitive_imitate",
    "cognitive_investigate",
    "cognitive_investigate_deep",
    "cognitive_observe",
    "cognitive_reflect",
    "cognitive_revise",
    "cognitive_route",
    "cognitive_self_assess",
    "cognitive_simulate",
    "cognitive_strengthen",
    "cognitive_think",
    "knowledge_add",
    "knowledge_list",
    "knowledge_search",
]
