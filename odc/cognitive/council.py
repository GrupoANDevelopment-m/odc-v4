"""Cognitive Council — multi-perspective reasoning (System 2 layer 1).

When System 1 (reactive tool use) hits a wall, the Council forces
the LLM to switch lenses. Five perspectives, each independent:

  1. EXPERT      — domain specialist
  2. HACKER      — unconventional, boundary-pushing
  3. RESEARCHER  — what to learn / read first
  4. DEVELOPER   — what code/tool to write or use
  5. INVESTIGATOR — what evidence would prove/refute

Each lens returns a 1-2 sentence answer. The Council then synthesizes
a recommended next action. This is *deliberate* reasoning (Kahneman
System 2), as opposed to the fast reactive System 1 the LLM defaults
to.

The Council is implemented purely as a prompt scaffold + LLM call.
No external deps. The result is cached briefly so the same task
doesn't re-run the council within a turn.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


LENSES = [
    ("expert", "If I were the world's leading expert in this domain, what would I do first?"),
    ("hacker", "What's the unconventional, clever, or boundary-pushing approach that conventional thinking would miss?"),
    ("researcher", "What knowledge do I need to learn first? What paper, doc, book, or repo would change my approach?"),
    ("developer", "What is the simplest code, tool, or library I could write or use to advance this task?"),
    ("investigator", "What concrete evidence would prove or refute my current hypothesis? How would I gather it?"),
]


COUNCIL_PROMPT_TEMPLATE = """\
You are consulting a council of 5 perspectives on a hard problem.
Each lens is independent. Do not let one perspective's answer
contaminate another's.

PROBLEM:
{problem}

CURRENT CONTEXT (tools tried, errors seen, prior attempts):
{context}

For EACH of the 5 lenses below, give a concrete 1-2 sentence answer
that the agent can act on. Be specific, not generic. No "I would
think about it" — say what you would DO.

{lens_questions}

After the 5 lenses, write a SYNTHESIS line (one paragraph) that
picks the most promising next move and explains why. If multiple
lenses converge on the same approach, name it explicitly.

OUTPUT FORMAT (strict JSON, no prose outside the JSON):
{{
  "perspectives": {{
    "expert":      "<1-2 sentences>",
    "hacker":      "<1-2 sentences>",
    "researcher":  "<1-2 sentences>",
    "developer":   "<1-2 sentences>",
    "investigator":"<1-2 sentences>"
  }},
  "synthesis": "<one paragraph naming the best next move and why>",
  "confidence": <float 0.0-1.0>
}}
"""


def _build_lens_questions() -> str:
    return "\n".join(
        f"{i+1}. {name.upper()}: {q}"
        for i, (name, q) in enumerate(LENSES)
    )


def build_council_prompt(problem: str, context: str = "") -> str:
    """Build the council prompt. Returns the user-side message body
    the LLM should respond to."""
    return COUNCIL_PROMPT_TEMPLATE.format(
        problem=problem.strip() or "(no problem specified)",
        context=(context or "(no prior context)").strip(),
        lens_questions=_build_lens_questions(),
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_ANY_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_council_response(text: str) -> dict[str, Any]:
    """Parse the LLM's council response into structured form.
    Tolerant: handles JSON in fences, raw JSON, or JSON embedded in prose.
    Returns a dict with keys: perspectives (dict), synthesis (str),
    confidence (float). On parse failure, returns the raw text under
    a 'raw' key so the caller can still use it.
    """
    if not text:
        return {"raw": "", "error": "empty response"}
    # 1) Try fenced JSON
    m = _FENCE_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(1))
            return _normalize_council(data)
        except Exception:
            pass
    # 2) Try to find the first balanced JSON object
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
                snippet = text[start : i + 1]
                try:
                    data = json.loads(snippet)
                    return _normalize_council(data)
                except Exception:
                    start = -1
                    continue
    # 3) Fallback: return raw text under a 'raw' key
    return {
        "raw": text.strip()[:4000],
        "synthesis": _extract_synthesis_fallback(text),
        "perspectives": {},
        "confidence": 0.0,
    }


def _normalize_council(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"raw": str(data)[:4000]}
    out: dict[str, Any] = {}
    out["perspectives"] = data.get("perspectives") or {}
    out["synthesis"] = (data.get("synthesis") or "").strip()
    try:
        out["confidence"] = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        out["confidence"] = 0.5
    if not out["perspectives"] and "raw" not in out:
        # If the LLM returned a flat dict, treat keys as perspectives
        for k, v in data.items():
            if k in ("synthesis", "confidence"):
                continue
            if isinstance(v, str):
                out["perspectives"][k] = v[:500]
    return out


def _extract_synthesis_fallback(text: str) -> str:
    """Best-effort extract a 'synthesis' line from prose that
    the LLM wrote outside the JSON."""
    for line in text.splitlines():
        ll = line.lower()
        if ll.startswith("synthesis") or ll.startswith("**synthesis"):
            return line.split(":", 1)[-1].strip()[:1000]
    # Last paragraph as a fallback
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paras[-1][:1000] if paras else text[:1000]


def render_council_for_prompt(result: dict[str, Any]) -> str:
    """Render a council result for inclusion in a system prompt.
    Compact, max ~30 lines."""
    lines = ["[COUNCIL OF LENSES — multi-perspective reasoning]"]
    for name, _ in LENSES:
        v = result.get("perspectives", {}).get(name, "")
        if v:
            lines.append(f"- {name.upper()}: {v}")
    syn = result.get("synthesis") or ""
    if syn:
        lines.append(f"\nSYNTHESIS: {syn}")
    conf = result.get("confidence")
    if isinstance(conf, (int, float)):
        lines.append(f"(confidence: {conf:.2f})")
    return "\n".join(lines)


__all__ = [
    "LENSES",
    "build_council_prompt",
    "parse_council_response",
    "render_council_for_prompt",
]
