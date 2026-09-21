"""Analogical reasoning — find parallels in human history and society.

When a problem is hard and the obvious approaches fail, the agent
can find a real-world analog (historical event, social dynamic,
biological process, business pattern) and use it as a lens.

This is NOT just decoration. Historical patterns are compressions
of thousands of cases — the patterns that survive are the ones
that work across many situations. They are a form of "compressed
experience" the LLM has been trained on.

Examples of analogical prompts:
  - "This is a classic 'tragedy of the commons' — each actor
     benefits from over-use, but the group loses"
  - "This is like the 'prisoner's dilemma' — cooperation requires
     trust that doesn't exist yet"
  - "This is a 'red queen' situation — adaptation is required
     just to maintain position"

The agent doesn't just name the analog; it extracts the
MECHANISM that made the historical case work, then proposes how
to apply that mechanism to the current problem.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


ANALOGY_PROMPT = """\
Find a real-world analog from human history, society, biology,
business, or any other domain that maps to the current problem.
The analog must illuminate SOMETHING the obvious approaches are
missing.

PROBLEM:
{problem}

WHAT HASN'T WORKED:
{failures}

The analog should help the agent see:
  - The hidden structure of the problem
  - Why current approaches are stuck
  - A mechanism from the analog that could be applied here

Choose ONE analog (or compare 2 if they illuminate different angles).
For each:
  - NAME the analog precisely (the specific event/pattern/case)
  - EXPLAIN the mechanism that made it work (1-2 sentences)
  - MAP the analog to THIS problem: what's the same, what's different
  - EXTRACT a concrete tactic the agent can try

OUTPUT FORMAT (strict JSON only, no prose outside):
{{
  "analogs": [
    {{
      "name": "<specific name, e.g. 'Darwin's finches on the Galapagos'>",
      "domain": "<history|biology|business|social|technology|other>",
      "mechanism": "<why it worked there>",
      "mapping": "<what's the same / different in our problem>",
      "tactic": "<a concrete action we can try>"
    }}
  ],
  "best_analog": "<name of the most illuminating one>",
  "extracted_strategy": "<a 1-sentence strategy derived from the analog>"
}}
"""


def build_analogy_prompt(problem: str, failures: str = "") -> str:
    return ANALOGY_PROMPT.format(
        problem=problem.strip() or "(no problem)",
        failures=(failures or "(no failures recorded)").strip(),
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_analogy_response(text: str) -> dict[str, Any]:
    if not text:
        return {"error": "empty", "analogs": []}
    m = _FENCE_RE.search(text)
    if m:
        try:
            return _normalize(json.loads(m.group(1)))
        except Exception:
            pass
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return _normalize(json.loads(text[start : i + 1]))
                except Exception:
                    start = -1
    return {"raw": text[:4000], "analogs": []}


def _normalize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"raw": str(data)[:4000], "analogs": []}
    analogs = []
    for a in data.get("analogs") or []:
        if not isinstance(a, dict):
            continue
        analogs.append({
            "name": (a.get("name") or "").strip()[:200],
            "domain": (a.get("domain") or "other").strip()[:50],
            "mechanism": (a.get("mechanism") or "").strip()[:500],
            "mapping": (a.get("mapping") or "").strip()[:500],
            "tactic": (a.get("tactic") or "").strip()[:500],
        })
    return {
        "analogs": analogs[:3],
        "best_analog": (data.get("best_analog") or "").strip()[:200],
        "extracted_strategy": (data.get("extracted_strategy") or "").strip()[:500],
    }


def analogy_prompt_for_injection(parsed: dict[str, Any]) -> str:
    """Render parsed analogs for inclusion in the LLM's context.
    Compact, max ~15 lines."""
    lines = ["[ANALOGICAL REASONING — patterns from human history/society]"]
    for a in parsed.get("analogs", [])[:2]:
        lines.append(f"\n- {a['name']} ({a['domain']})")
        lines.append(f"  mechanism: {a['mechanism']}")
        lines.append(f"  mapping: {a['mapping']}")
        lines.append(f"  tactic: {a['tactic']}")
    if parsed.get("extracted_strategy"):
        lines.append(f"\nEXTRACTED STRATEGY: {parsed['extracted_strategy']}")
    return "\n".join(lines)


__all__ = [
    "build_analogy_prompt",
    "parse_analogy_response",
    "analogy_prompt_for_injection",
]
