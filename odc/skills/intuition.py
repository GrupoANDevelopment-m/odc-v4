"""The Intuition skill — metacognition and hypothesis testing.

Tells the agent to use the cognitive.* tools when facing uncertainty,
and explains when each one is appropriate. The agent loads this skill
on every run because it is in builtin_skills().
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from odc.skills.loader import Skill

INTUITION = """\
# Intuition — the metacognitive layer

You have seven cognitive tools. They form a learning loop:

  ROUTE -> ACT -> REFLECT -> (profile grows) -> next ROUTE is smarter

This is how you get smarter over time. The profile at
.odc_data/cognitive/profile.json is your persistent memory of
strengths, gaps, and rules.

## cognitive.think

Use it for any **explicit inner thought** you want to leave a trail
of. The note is logged with a timestamp and retrievable later.
- Before you act on uncertainty: "I think the user wants X, not Y"
- When you form a plan: "step 1: read X, step 2: try Y"
- When you doubt your own work: "maybe I am misreading the schema"

## cognitive.simulate

Use it to **test pure logic cheaply**. Arithmetic, string ops, list
comprehensions, conditionals, JSON-shape checks. No side effects, no
imports, no file ops. Examples:

- simulate("2 ** 64")  -> 18446744073709551616
- simulate("len([x for x in range(100) if x % 7 == 0])")  -> 15
- simulate("{'a': 1, 'b': 2}.get('c', 0) + 1")  -> 1

If a task involves deterministic computation, **simulate first** to
sanity-check before you call a real side-effect tool.

## cognitive.hypothesize

The hypothetico-deductive cycle in two calls. First call with
hypothesis + test_tool + test_args + optional predicate — the tool
logs the hypothesis. Then call the test_tool. Then call
cognitive.hypothesize again with the SAME fields plus test_result
(the tool output dict) — the tool evaluates the predicate and
returns confirmed/refuted. Use it when:

- You suspect a fact about the world ("the file is bigger than 1MB")
- You want to validate a plan before committing ("if I run tool X
  with Y, I should get Z")
- You want to disprove a wrong assumption fast

## cognitive.self_assess

Use it after a long task or whenever you feel stuck. It reads your
recent log, finds the **most common failure pattern**, and writes a
new skill to `.odc_data/dynamic/skills/auto_<ts>/SKILL.md` targeting
the gap. Next time the agent starts, that skill auto-loads. This is
how you augment your own capabilities.

## The reflex

When you face a non-trivial task, the reflex is:

1. `cognitive.think` — what do I know, what am I unsure of?
2. `cognitive.simulate` — test any pure-logic step cheaply
3. `cognitive.hypothesize` — pick a real tool, run it, get a verdict
4. If you keep failing or notice a gap, `cognitive.self_assess`
5. Then act on the strongest hypothesis

The user sees only the final report, but the cognitive calls are
auditable in the JSONL log.
"""

_TRIGGERS = [
    "hypothesize", "hypothesis", "intuition", "intuitive", "metacog",
    "self-improve", "self assess", "self_assess", "self assess",
    "what if", "verify", "double check", "sanity check", "are you sure",
]


def intuition_skill() -> Skill:
    return Skill(
        name="intuition",
        description=(
            "Metacognitive layer. Teaches the agent to use cognitive.think, "
            "cognitive.simulate, cognitive.hypothesize, and cognitive.self_assess "
            "for hypothesis-driven reasoning and self-improvement."
        ),
        body=INTUITION,
        triggers=_TRIGGERS,
        path=Path(__file__),
        meta={"builtin": True, "category": "metacognition"},
    )
