"""Web tools: fetch + search.

Two real tools:
- web.fetch: GET a URL, return cleaned text. Uses trafilatura if installed
  (best for articles), else BeautifulSoup4, else raw text. Falls back to
  raw httpx if neither is available.
- web.search: DuckDuckGo results with title/url/snippet. No key required.

Heavy imports are lazy so the base install stays light.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from odc.tools.base import tool

USER_AGENT = "Mozilla/5.0 (compatible; ODC/4.0; +https://github.com/local/odc)"


@tool(
    name="web.fetch",
    description=(
        "Fetch a URL and return its main text content (HTML stripped, "
        "scripts/styles removed). Best for articles and docs. Returns up "
        "to ~16KB of text. Use web.search first to find URLs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to fetch."},
            "max_bytes": {
                "type": "integer",
                "description": "Cap on response size. Default 2_000_000.",
                "default": 2_000_000,
            },
        },
        "required": ["url"],
    },
)
async def fetch(url: str, max_bytes: int = 2_000_000) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        if len(r.content) > max_bytes:
            r._content = r.content[:max_bytes]  # type: ignore[attr-defined]
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype.lower() and "xml" not in ctype.lower():
            return {
                "url": str(r.url),
                "status": r.status_code,
                "content_type": ctype,
                "text": r.text[:16000],
            }

    text = _extract_text(r.text, str(r.url))
    return {
        "url": str(r.url),
        "status": r.status_code,
        "title": _extract_title(r.text),
        "text": text[:16000],
    }


def _extract_title(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip() or None


def _extract_text(html: str, url: str) -> str:
    """Best-effort article extraction. Tries trafilatura, then BS4, then regex."""
    try:
        import trafilatura  # type: ignore

        extracted = trafilatura.extract(
            html, include_comments=False, include_tables=False, url=url
        )
        if extracted and len(extracted) > 200:
            return extracted
    except Exception:  # noqa: BLE001
        pass

    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text
    except Exception:  # noqa: BLE001
        pass

    # Last-ditch: strip tags crudely.
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@tool(
    name="web.search",
    description=(
        "Search DuckDuckGo (no API key) and return top results: title, "
        "url, snippet. Use web.fetch on a chosen url to read the page."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {
                "type": "integer",
                "description": "Number of results. Default 8, max 30.",
                "default": 8,
            },
            "region": {
                "type": "string",
                "description": "Region code (e.g. 'wt-wt', 'us-en', 'br-pt'). Default 'wt-wt'.",
                "default": "wt-wt",
            },
        },
        "required": ["query"],
    },
)
async def search(query: str, max_results: int = 8, region: str = "wt-wt") -> list[dict[str, str]]:
    max_results = max(1, min(30, max_results))

    # Primary: try the duckduckgo-search library. Often blocked from
    # datacenters; if it returns 0, fall through.
    ddgs_hits: list[dict[str, str]] = []
    try:
        from duckduckgo_search import DDGS  # type: ignore

        def _run_sync() -> list[dict[str, str]]:
            with DDGS() as ddgs:
                hits = list(
                    ddgs.text(
                        query,
                        region=region,
                        max_results=max_results,
                        safesearch="moderate",
                    )
                )
            return [
                {
                    "title": h.get("title", ""),
                    "url": h.get("href", h.get("url", "")),
                    "snippet": h.get("body", h.get("snippet", "")),
                }
                for h in hits
            ]

        ddgs_hits = await asyncio.to_thread(_run_sync)
    except Exception:  # noqa: BLE001
        ddgs_hits = []

    if ddgs_hits:
        return ddgs_hits

    # Fallback A: DuckDuckGo Instant Answer API. Works from datacenters
    # where the HTML scraper is 503'd. Returns the Wikipedia abstract
    # plus RelatedTopics — enough for most research queries.
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ODC/4.0)"},
        ) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )
            if r.status_code == 200:
                data = r.json()
                hits: list[dict[str, str]] = []
                if data.get("Abstract") and data.get("AbstractURL"):
                    hits.append({
                        "title": data.get("Heading") or query,
                        "url": data["AbstractURL"],
                        "snippet": data["Abstract"],
                    })
                for t in data.get("RelatedTopics", []) or []:
                    if "Text" in t and "FirstURL" in t:
                        hits.append({
                            "title": (t.get("Text", "") or "")[:80].split(" - ")[0] or query,
                            "url": t["FirstURL"],
                            "snippet": t["Text"],
                        })
                    elif "Topics" in t:
                        for sub in t["Topics"] or []:
                            if "Text" in sub and "FirstURL" in sub:
                                hits.append({
                                    "title": (sub.get("Text", "") or "")[:80].split(" - ")[0] or query,
                                    "url": sub["FirstURL"],
                                    "snippet": sub["Text"],
                                })
                    if len(hits) >= max_results:
                        break
                if hits:
                    return hits[:max_results]
    except Exception:  # noqa: BLE001
        pass

    # Fallback B: scrape the html.duckduckgo.com endpoint. Last resort.
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            r = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": region},
            )
            if r.status_code == 200:
                return _parse_ddg_html(r.text, max_results)
    except Exception:  # noqa: BLE001
        pass

    # Nothing worked. Return a structured empty result with a hint so
    # the agent can see the search is unavailable and self-repair if
    # it wants.
    return [{
        "title": "(no results — all search backends blocked)",
        "url": "",
        "snippet": (
            "DDGS library returned 0 results, DDG Instant Answer API "
            "returned no abstract, and html.duckduckgo.com returned a "
            "non-200 status. This usually means the sandbox blocks "
            "search backends. Use web.fetch against a known URL instead, "
            "or call dynamic.tool_repair to rewrite web.search with a "
            "different backend."
        ),
    }]


def _parse_ddg_html(html: str, limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    # Very lightweight parser — good enough as a fallback.
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        flags=re.DOTALL,
    ):
        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out
