"""Phase 3: simple extractive summarization for older messages.

For ODC's scale (single-agent, 8B model, < 25 turn conversations),
LLMLingua-2 would be overkill (50MB model just to compress text).
A simple extractive approach works:

1. Score each message by:
   - Length (longer = more content)
   - Question marks / colons (decision points)
   - Tool names mentioned
   - Recent facts we recorded
2. Pick the top-N sentences from the oldest messages
3. Concatenate with sentence-level dedup

This is a placeholder. When the conversation gets really long (>20
turns) and the LLM is a paid model with strong extraction, we can
swap in LLMLingua-2 by calling a method on the same interface.
"""
from __future__ import annotations

import re
from typing import Any


_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|[\n]{2,}")


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENT_RE.split(text) if s.strip()]


def _score_sentence(
    sent: str,
    *,
    keywords: set[str],
) -> float:
    """Score a sentence by keyword overlap and structural features."""
    s = sent.lower()
    score = 0.0
    # Keyword overlap
    sent_words = set(re.findall(r"[a-z0-9]+", s))
    overlap = sent_words & keywords
    score += len(overlap) * 1.0
    # Decision / question markers
    if "?" in sent:
        score += 1.0
    if any(w in sent.lower() for w in ("decided", "because", "therefore", "so we", "thus")):
        score += 0.8
    if any(w in sent.lower() for w in ("error", "failed", "success", "completed")):
        score += 0.5
    # Length penalty for very short sentences
    if len(sent) < 30:
        score -= 0.5
    if len(sent) > 500:
        score -= 0.3
    return score


def summarize_messages(
    messages: list[Any],
    *,
    max_sentences: int = 8,
    keywords: set[str] | None = None,
) -> str:
    """Extract the most important sentences from a list of messages.

    `keywords` is a hint set (e.g. tool names, recent facts) that
    biases scoring toward them. If None, we use a generic English
    stopword filter and prioritize decision markers.
    """
    if not messages:
        return ""
    # Default: pull keywords from the messages themselves (names of tools,
    # numbers, capitalized entities) — no model needed.
    if keywords is None:
        keywords = set()
        for m in messages:
            content = getattr(m, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
            # Tool names: dot-separated words (e.g. web.search)
            for match in re.findall(r"\b([a-z][a-z0-9]*\.[a-z][a-z0-9_]+)\b", content):
                keywords.add(match.split(".")[0])
            # Capitalized entities
            for match in re.findall(r"\b([A-Z][a-z]+)\b", content):
                keywords.add(match.lower())

    all_sentences: list[tuple[float, str]] = []
    seen: set[str] = set()
    for m in messages:
        content = getattr(m, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        for sent in _split_sentences(content):
            # Dedup on first 80 chars
            key = sent[:80].lower()
            if key in seen:
                continue
            seen.add(key)
            all_sentences.append((_score_sentence(sent, keywords=keywords), sent))
    all_sentences.sort(key=lambda x: -x[0])
    picked = [s for _, s in all_sentences[:max_sentences]]
    return " | ".join(picked)


__all__ = ["summarize_messages"]
