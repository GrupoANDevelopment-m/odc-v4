"""Local knowledge base — facts the agent has learned, persisted on disk.

The KB is a JSONL file at <data_dir>/knowledge/facts.jsonl. Each fact
is a small dict: {ts, topic, content, source, confidence}. Search
is BM25-style by default (no model deps). When a `knowledge.search`
returns nothing, the agent knows the topic is novel and falls back
to web research.

This is the agent's LONG-TERM semantic memory, separate from:
  - profile.json (learning state)
  - threads/<id>/context.json (per-conversation state)
  - checkpoints.db (turn-by-turn state)

KB is *cross-thread* (like profile) but holds *facts*, not meta-learning.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from odc.prompt.bm25 import BM25


_FACT_MAX = 5000
_FACT_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t for t in _FACT_RE.findall(text.lower()) if len(t) > 2]


class KnowledgeBase:
    """Simple JSONL-backed KB with BM25 search."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "knowledge"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "facts.jsonl"
        self._facts: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out[-_FACT_MAX:]

    def _persist(self) -> None:
        with self.path.open("w") as f:
            for f_ in self._facts:
                f.write(json.dumps(f_, ensure_ascii=False) + "\n")

    def add(
        self,
        topic: str,
        content: str,
        *,
        source: str = "agent",
        confidence: float = 0.7,
    ) -> dict[str, Any]:
        """Add a fact. Deduplicates by (topic, content) prefix."""
        norm = (topic.strip().lower(), content.strip()[:200].lower())
        for existing in self._facts:
            if (existing["topic"].lower(), existing["content"][:200].lower()) == norm:
                existing["hits"] = existing.get("hits", 0) + 1
                existing["ts"] = time.time()
                self._persist()
                return {"ok": True, "deduped": True, "fact": existing}
        fact = {
            "ts": time.time(),
            "topic": topic.strip()[:200],
            "content": content.strip()[:2000],
            "source": source,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "hits": 0,
        }
        self._facts.append(fact)
        if len(self._facts) > _FACT_MAX:
            self._facts = self._facts[-_FACT_MAX:]
        self._persist()
        return {"ok": True, "deduped": False, "fact": fact}

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        topic_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 search over facts. Returns top-k with their scores."""
        if not self._facts:
            return []
        corpus: list[str] = []
        names: list[str] = []
        for i, f in enumerate(self._facts):
            if topic_filter and topic_filter.lower() not in f["topic"].lower():
                continue
            corpus.append(f"{f['topic']} {f['content']}")
            names.append(str(i))
        if not corpus:
            return []
        ranker = BM25(corpus)
        ranked = ranker.rank_with_scores(query, top_k=top_k)
        out = []
        for idx, score in ranked:
            if score <= min_score:
                break
            f = self._facts[int(names[idx])]
            out.append({
                "score": round(float(score), 3),
                "topic": f["topic"],
                "content": f["content"],
                "source": f.get("source", "?"),
                "confidence": f.get("confidence", 0.5),
                "ts": f.get("ts", 0),
            })
        return out

    def stats(self) -> dict[str, Any]:
        topics: dict[str, int] = {}
        for f in self._facts:
            t = f.get("topic", "?").split("/")[0].strip()
            topics[t] = topics.get(t, 0) + 1
        return {
            "total_facts": len(self._facts),
            "topics": dict(sorted(topics.items(), key=lambda x: -x[1])[:20]),
            "file_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    def list_recent(self, n: int = 20) -> list[dict[str, Any]]:
        return self._facts[-n:][::-1]

    def delete(self, topic: str, content_prefix: str = "") -> int:
        before = len(self._facts)
        self._facts = [
            f for f in self._facts
            if f["topic"].lower() != topic.lower()
            or (content_prefix and not f["content"].lower().startswith(content_prefix.lower()))
        ]
        removed = before - len(self._facts)
        if removed:
            self._persist()
        return removed


__all__ = ["KnowledgeBase"]
