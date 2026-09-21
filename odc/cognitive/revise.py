"""Epistemic humility — open mind, ready to revise.

DEEP-REASON's biggest risk is the LLM getting stuck in a wrong
hypothesis with high confidence. The `revise` (steel-man) tool
forces the LLM to:

  1. STATE its current best hypothesis
  2. Generate the STRONGEST POSSIBLE argument AGAINST it
  3. List alternative variants it may have NOT considered
  4. State what evidence would change its mind
  5. If a previous hypothesis was falsified, mark it and move on

This is the opposite of "confident assertion" — it's deliberate
uncertainty calibration. Designed for situations where the agent
keeps retrying the same failed approach.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


REVISE_PROMPT = """\
You are about to deliberately attack your own reasoning. This is
the "steel-man" exercise: find the strongest possible argument
AGAINST what you currently believe, and consider alternatives you
may have missed.

YOUR CURRENT BEST HYPOTHESIS:
{current_hypothesis}

EVIDENCE YOU'VE SEEN SO FAR:
{evidence}

WHAT HAS FAILED:
{failures}

For each of the 5 sections below, write a concrete answer (no
hedging, no "well, it depends"):

1. STEEL-MAN OPPOSITE — what is the STRONGEST argument that your
   hypothesis is WRONG? Take the opposing view seriously. If the
   opposite is actually better, say so.

2. BLIND SPOTS — what are 2-3 things you may have NOT considered?
   Think outside the box. What perspectives are you missing?
   What if the problem is fundamentally different from how you
   framed it?

3. ALTERNATIVE VARIANTS — list 2-3 ways your hypothesis might be
   ALMOST right but with a key correction. E.g. "X is true EXCEPT
   when Y"; "the right answer is X not because of A but because of B".

4. FALSIFICATION CRITERIA — what specific evidence would prove your
   hypothesis wrong? Be concrete. If you can't state what would
   falsify it, you don't actually have a hypothesis — you have a
   belief.

5. CONFIDENCE RECALIBRATION — given everything, what is your NEW
   confidence (0.0-1.0)? If it's < 0.3, you should change approach.
   If it's > 0.7, you have good reason to keep going.

OUTPUT FORMAT (strict JSON only, no prose outside):
{{
  "steel_man_opposite": "<1-2 sentences>",
  "blind_spots": ["...", "...", "..."],
  "alternative_variants": ["...", "...", "..."],
  "falsification_criteria": ["...", "...", "..."],
  "new_confidence": <float>,
  "should_change_approach": <bool>,
  "suggested_next_move": "<if should_change_approach, what>"
}}
"""


def build_revise_prompt(
    current_hypothesis: str,
    evidence: str = "",
    failures: str = "",
) -> str:
    return REVISE_PROMPT.format(
        current_hypothesis=current_hypothesis.strip() or "(no hypothesis stated)",
        evidence=(evidence or "(no evidence)").strip(),
        failures=(failures or "(no failures)").strip(),
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_revise_response(text: str) -> dict[str, Any]:
    if not text:
        return {"error": "empty"}
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
    return {"raw": text[:4000]}


def _normalize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"raw": str(data)[:4000]}
    out = {
        "steel_man_opposite": (data.get("steel_man_opposite") or "").strip()[:1000],
        "blind_spots": list(data.get("blind_spots") or [])[:5],
        "alternative_variants": list(data.get("alternative_variants") or [])[:5],
        "falsification_criteria": list(data.get("falsification_criteria") or [])[:5],
        "should_change_approach": bool(data.get("should_change_approach", False)),
        "suggested_next_move": (data.get("suggested_next_move") or "").strip()[:500],
    }
    try:
        out["new_confidence"] = float(data.get("new_confidence", 0.5))
    except (TypeError, ValueError):
        out["new_confidence"] = 0.5
    return out


__all__ = ["build_revise_prompt", "parse_revise_response"]
