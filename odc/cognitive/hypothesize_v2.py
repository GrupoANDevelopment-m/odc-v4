"""Multi-hypothesis reasoning — the hypothesis tree.

Instead of one hypothesis at a time (the old `cognitive.hypothesize`),
generate N competing hypotheses, each with:
  - description
  - the test that would discriminate it from the others
  - expected outcome
  - cost estimate

Then pick the most testable + highest expected-value hypothesis and
return the tree. The agent can then call `cognitive.test_hypothesis`
later to update which one survives.

The tree is stored in the profile so cross-task learning can
identify which hypothesis TYPES tend to survive (learning to
reason better, not just to act better).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


HYPOTHESIZE_PROMPT = """\
You are generating {n} competing hypotheses for a problem.
Each hypothesis must be DISTINCT (not variations of the same idea)
and TESTABLE (a concrete tool call or check would discriminate it).

PROBLEM:
{problem}

OBSERVED FACTS:
{context}

For each hypothesis, output a JSON object with:
- "hypothesis": one-line claim
- "rationale": why this is plausible (1 sentence)
- "test": the EXACT tool call (name + args) that would confirm or refute it
- "expected_outcome": what you expect to see if the hypothesis is true
- "cost": 1-3 (1 = cheap single tool call, 3 = expensive multi-step)
- "prior": 0.0-1.0 (how likely you think this is BEFORE testing)

Then return a "ranking" array listing the hypotheses in the order
they should be tested (most testable + highest expected information
gain first).

OUTPUT FORMAT (strict JSON only, no prose):
{{
  "hypotheses": [
    {{"id": "h1", "hypothesis": "...", "rationale": "...", "test": {{"tool": "name", "args": {{...}}}}, "expected_outcome": "...", "cost": 1, "prior": 0.3}},
    ...
  ],
  "ranking": ["h2", "h1", "h3", ...],
  "best_initial_test": "h2",
  "fallback": "If the best test fails, what should we try next?"
}}
"""


def build_hypothesize_prompt(problem: str, context: str, n: int = 3) -> str:
    return HYPOTHESIZE_PROMPT.format(
        n=n,
        problem=problem.strip() or "(no problem)",
        context=(context or "(no observations)").strip(),
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_hypotheses_response(text: str, n_expected: int = 3) -> dict[str, Any]:
    """Parse the LLM's hypothesis tree response."""
    if not text:
        return {"error": "empty", "hypotheses": [], "ranking": []}
    # 1) fenced
    m = _FENCE_RE.search(text)
    if m:
        try:
            return _normalize(json.loads(m.group(1)), n_expected)
        except Exception:
            pass
    # 2) balanced braces
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
                    return _normalize(json.loads(text[start : i + 1]), n_expected)
                except Exception:
                    start = -1
    # 3) fallback: try to split by numbered list
    return {
        "raw": text[:4000],
        "hypotheses": _extract_hypotheses_from_prose(text),
        "ranking": [],
        "best_initial_test": None,
        "fallback": "",
    }


def _normalize(data: Any, n_expected: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"raw": str(data)[:4000], "hypotheses": [], "ranking": []}
    hyps = data.get("hypotheses") or []
    norm = []
    for i, h in enumerate(hyps[:max(n_expected, 1)]):
        if not isinstance(h, dict):
            continue
        norm.append({
            "id": h.get("id") or f"h{i+1}",
            "hypothesis": (h.get("hypothesis") or "").strip()[:500],
            "rationale": (h.get("rationale") or "").strip()[:500],
            "test": h.get("test") or {},
            "expected_outcome": (h.get("expected_outcome") or "").strip()[:500],
            "cost": int(h.get("cost") or 1),
            "prior": float(h.get("prior") or 0.3),
        })
    return {
        "hypotheses": norm,
        "ranking": data.get("ranking") or [h["id"] for h in norm],
        "best_initial_test": data.get("best_initial_test") or (norm[0]["id"] if norm else None),
        "fallback": (data.get("fallback") or "").strip()[:500],
    }


def _extract_hypotheses_from_prose(text: str) -> list[dict[str, Any]]:
    out = []
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"\s*(?:\d+[.)]|\*|-)\s*(.+)", line)
        if m and 20 < len(m.group(1)) < 500:
            out.append({
                "id": f"h{len(out)+1}",
                "hypothesis": m.group(1)[:500],
                "rationale": "",
                "test": {},
                "expected_outcome": "",
                "cost": 1,
                "prior": 0.3,
            })
    return out[:5]


def rank_hypotheses_for_testing(
    hyps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-rank hypotheses by testability + expected info gain.
    Lower cost first, then by prior (higher = more likely true)."""
    return sorted(
        hyps,
        key=lambda h: (
            h.get("cost", 2),                # cheap first
            -float(h.get("prior", 0.3)),     # higher prior first
        ),
    )


__all__ = [
    "build_hypothesize_prompt",
    "parse_hypotheses_response",
    "rank_hypotheses_for_testing",
]
