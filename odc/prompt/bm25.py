"""Minimal BM25 ranker — zero dependencies.

Used to filter the tool catalog and skill catalog down to only what's
relevant for the current task. Standard parameters (k1=1.5, b=0.75).
Designed for small corpora (41 tools, 6 skills) — fits in memory easily.

This is the same algorithm used by Elasticsearch's BM25 default and by
Microsoft's LLMLingua for token ranking. We don't need a vector store
or a transformer model — for ODC's scale, plain BM25 with TF-IDF
weighting outperforms them.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanum. Drops tokens of length 1."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1]


class BM25:
    """Minimal BM25Okapi implementation."""

    def __init__(self, corpus: list[str], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [_tokenize(d) for d in corpus]
        self.n_docs = len(self.docs)
        self.doc_lens = [len(d) for d in self.docs]
        self.avg_dl = sum(self.doc_lens) / max(1, self.n_docs)
        # Document frequency: how many docs each term appears in
        df: Counter = Counter()
        for d in self.docs:
            for term in set(d):
                df[term] += 1
        self.df = dict(df)
        # Pre-compute IDF for each term (BM25+ smoothing)
        self.idf: dict[str, float] = {}
        for term, freq in df.items():
            # Standard BM25 IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            self.idf[term] = math.log(
                (self.n_docs - freq + 0.5) / (freq + 0.5) + 1.0
            )

    def score(self, query: str, doc_idx: int) -> float:
        q_tokens = _tokenize(query)
        if not q_tokens or doc_idx >= self.n_docs:
            return 0.0
        doc = self.docs[doc_idx]
        dl = self.doc_lens[doc_idx]
        # Term frequencies in the doc
        tf = Counter(doc)
        score = 0.0
        for qt in q_tokens:
            if qt not in tf:
                continue
            idf = self.idf.get(qt, 0.0)
            num = tf[qt] * (self.k1 + 1)
            denom = tf[qt] + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
            score += idf * (num / denom)
        return score

    def rank(self, query: str, *, top_k: int = 5, min_score: float = 0.0) -> list[int]:
        """Return indices of the top-k documents matching the query,
        ordered by descending BM25 score."""
        if not query.strip() or self.n_docs == 0:
            return list(range(min(top_k, self.n_docs)))
        scored = [(i, self.score(query, i)) for i in range(self.n_docs)]
        scored.sort(key=lambda x: -x[1])
        out: list[int] = []
        for i, s in scored:
            if s <= min_score:
                break
            out.append(i)
            if len(out) >= top_k:
                break
        return out

    def rank_with_scores(
        self, query: str, *, top_k: int = 5
    ) -> list[tuple[int, float]]:
        scored = [(i, self.score(query, i)) for i in range(self.n_docs)]
        scored.sort(key=lambda x: -x[1])
        return [(i, s) for i, s in scored[:top_k] if s > 0]


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def build_corpus_from_tools(tools: list[Any]) -> tuple[list[str], list[str]]:
    """Build a (corpus, names) pair from a list of tools.
    Each tool's text is name + description + parameter names + sample values."""
    names: list[str] = []
    corpus: list[str] = []
    for t in tools:
        name = getattr(t, "name", None) or t.__class__.__name__
        # Tool object may have .description (str) or .parameters (dict)
        desc = getattr(t, "description", "") or ""
        params = getattr(t, "parameters", {}) or {}
        param_text = ""
        if isinstance(params, dict):
            for pname, pinfo in (params.get("properties") or {}).items():
                param_text += " " + pname
                if isinstance(pinfo, dict) and pinfo.get("description"):
                    param_text += " " + pinfo["description"]
        text = f"{name} {desc} {param_text}".strip()
        names.append(name)
        corpus.append(text)
    return corpus, names


def build_corpus_from_skills(skills: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Build a (corpus, names) pair from a skills dict.
    Each skill is rendered as name + body (truncated to 500 chars)."""
    names: list[str] = []
    corpus: list[str] = []
    for name, skill in skills.items():
        body = ""
        if hasattr(skill, "body"):
            body = skill.body if isinstance(skill.body, str) else str(skill.body)
        elif isinstance(skill, str):
            body = skill
        text = f"{name} {body[:500]}"
        names.append(name)
        corpus.append(text)
    return corpus, names
