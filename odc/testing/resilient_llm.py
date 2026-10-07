"""Resilient LLM helper for tests.

LLM outputs are stochastic — even at temperature=0, different
deployments, timeouts, or thinking-budget exhaustion can produce
empty text or inconsistent answers.

This module provides production-grade retry logic for test
infrastructure. Based on patterns from real LLM testing suites
(see pytest-rerunfailures, tenacity).

Usage:
    llm = ResilientLLM(cache_path=Path(".odc_data/test_llm_cache.json"))
    result = llm.ask_sync(
        "What's 2+2?",
        system="Be brief.",
        max_retries=3,
    )
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMResult:
    text: str = ""
    reasoning: str = ""
    latency_s: float = 0.0
    model: str = ""
    attempts: int = 1
    error: str = ""


class ResilientLLM:
    """Test-friendly LLM wrapper with cache + retry.

    Behavior:
      - First call hits real API and caches result
      - Subsequent calls with same (prompt, system, max_tokens, temperature)
        replay the cached result
      - Empty results (text=="" and reasoning=="") trigger retry with
        exponential backoff
      - Errors from API trigger retry
    """

    def __init__(self, cache_path: Path | None = None,
                 *, max_retries: int = 3, base_delay: float = 1.0):
        self.cache_path = cache_path
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._cache: dict[str, dict[str, Any]] = {}
        if cache_path and cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except Exception:
                self._cache = {}

    def _cache_key(self, prompt: str, system: str,
                    max_tokens: int, temperature: float) -> str:
        h = hashlib.sha256()
        h.update(prompt.encode())
        h.update(b"|")
        h.update(system.encode())
        h.update(f"|{max_tokens}|{temperature}".encode())
        return h.hexdigest()

    def _save_cache(self):
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=2))

    async def ask(self, prompt: str, *, system: str = "",
                   max_tokens: int = 2000, temperature: float = 0.0,
                   enable_thinking: bool = True) -> LLMResult:
        key = self._cache_key(prompt, system, max_tokens, temperature)
        if key in self._cache:
            entry = self._cache[key]
            return LLMResult(
                text=entry.get("text", ""),
                reasoning=entry.get("reasoning", ""),
                latency_s=entry.get("latency_s", 0.0),
                model=entry.get("model", ""),
                attempts=0,  # cached
            )

        from odc.llm.provider import NvidiaProvider, Message
        from odc.config import Config

        last_err = ""
        for attempt in range(1, self.max_retries + 1):
            cfg = Config()
            provider = NvidiaProvider(cfg)  # fresh per attempt
            model = cfg.nvidia_model
            msgs = []
            if system:
                msgs.append(Message(role="system", content=system))
            msgs.append(Message(role="user", content=prompt))
            t0 = time.time()
            try:
                extra_body = (
                    {"chat_template_kwargs": {"enable_thinking": True}}
                    if enable_thinking else None
                )
                r = await provider.chat(
                    msgs, max_tokens=max_tokens, temperature=temperature,
                    extra_body=extra_body,
                )
                msg = r.raw.choices[0].message
                text = r.text or ""
                reasoning = getattr(msg, "reasoning_content", "") or ""
                # If both empty, treat as failure (thinking exhausted)
                if not text.strip() and not reasoning.strip():
                    last_err = "empty response (thinking exhausted?)"
                    if attempt < self.max_retries:
                        await asyncio.sleep(self.base_delay * (2 ** (attempt - 1)))
                    continue
                result = LLMResult(
                    text=text,
                    reasoning=reasoning,
                    latency_s=round(time.time() - t0, 2),
                    model=model,
                    attempts=attempt,
                )
                # Cache successful results
                self._cache[key] = {
                    "text": result.text,
                    "reasoning": result.reasoning,
                    "latency_s": result.latency_s,
                    "model": result.model,
                }
                self._save_cache()
                return result
            except Exception as e:
                last_err = str(e)[:200]
                if attempt < self.max_retries:
                    await asyncio.sleep(self.base_delay * (2 ** (attempt - 1)))
                continue
        return LLMResult(
            text="", reasoning="", latency_s=0.0, model="",
            attempts=self.max_retries, error=last_err,
        )

    def ask_sync(self, prompt: str, **kwargs) -> LLMResult:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.ask(prompt, **kwargs))
        finally:
            loop.close()


def majority_vote(results: list[LLMResult], field: str = "text") -> str:
    """Return the most common value across multiple LLMResult samples."""
    from collections import Counter
    values = [getattr(r, field, "") for r in results if getattr(r, field, "")]
    if not values:
        return ""
    return Counter(values).most_common(1)[0][0]