"""Real browser automation tool using Playwright + persistent Chromium.

Uses Playwright's ASYNC API (consistent with ODC's async tool runtime).

Provides:
  - browser.open(url)         — open URL in a persistent browser context
  - browser.goto(url)         — navigate to URL
  - browser.evaluate(js)      — run JS in page, return value
  - browser.screenshot(name)  — capture page as PNG
  - browser.text()            — extract rendered text (post-JS)
  - browser.html()            — get rendered HTML
  - browser.cookies()         — list all persistent cookies
  - browser.cookies_set(...)  — set a cookie
  - browser.cookies_clear()   — clear all
  - browser.close()           — close context
  - browser.status()          — current URL, title, cookies count

Single persistent context per data dir. Cookies and localStorage
survive across calls and across process restarts.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from odc.tools.base import tool

# ── Module-level singleton ─────────────────────────────────────────
# Async: keep one playwright instance per process. The context is
# pinned to a single asyncio loop (Playwright's async API requires this).

_BROWSER_STATE: dict[str, Any] = {
    "playwright": None,
    "context": None,
    "page": None,
    "loop": None,
    "lock": asyncio.Lock(),
    "start_count": 0,
    "screenshot_count": 0,
    "evaluate_count": 0,
}


def _browser_data_dir(data_dir: Path) -> Path:
    """Where the persistent browser state lives on disk."""
    p = data_dir / "browser"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_file(data_dir: Path) -> Path:
    """Path to the storage_state JSON (cookies + localStorage)."""
    return _browser_data_dir(data_dir) / "storage_state.json"


async def _save_state() -> None:
    """Persist cookies and localStorage to disk."""
    if _BROWSER_STATE["context"] is None:
        return
    sf = _state_file(_data_dir())
    state = await _BROWSER_STATE["context"].storage_state(path=str(sf))


async def _ensure_browser(data_dir: Path) -> tuple[Any, Any, Any]:
    """Lazy-start Chromium with persistent storage_state.

    CRITICAL: Playwright's async API is tied to the event loop it was
    started in. If a previous loop has been closed, we MUST create a
    fresh playwright instance. So we always check the loop identity.
    """
    current_loop = asyncio.get_event_loop()
    if (_BROWSER_STATE["page"] is not None
        and _BROWSER_STATE.get("loop") is current_loop):
        return _BROWSER_STATE["playwright"], _BROWSER_STATE["context"], _BROWSER_STATE["page"]
    # Loop changed or never started — clean up old state and start fresh
    if _BROWSER_STATE["page"] is not None:
        try:
            if _BROWSER_STATE["context"]:
                await _save_state()
                await _BROWSER_STATE["context"].close()
        except Exception:
            pass
        try:
            if _BROWSER_STATE["browser"]:
                await _BROWSER_STATE["browser"].close()
        except Exception:
            pass
        try:
            if _BROWSER_STATE["playwright"]:
                await _BROWSER_STATE["playwright"].stop()
        except Exception:
            pass
        _BROWSER_STATE["page"] = None
        _BROWSER_STATE["context"] = None
        _BROWSER_STATE["browser"] = None
        _BROWSER_STATE["playwright"] = None

    async with _BROWSER_STATE["lock"]:
        if (_BROWSER_STATE["page"] is not None
            and _BROWSER_STATE.get("loop") is current_loop):
            return _BROWSER_STATE["playwright"], _BROWSER_STATE["context"], _BROWSER_STATE["page"]
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--ignore-certificate-errors",
                ],
            )
        except Exception:
            try:
                await pw.stop()
            except Exception:
                pass
            raise
        sf = _state_file(data_dir)
        if sf.exists():
            ctx = await browser.new_context(
                storage_state=str(sf),
                ignore_https_errors=True,
            )
        else:
            ctx = await browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()
        _BROWSER_STATE["playwright"] = pw
        _BROWSER_STATE["browser"] = browser
        _BROWSER_STATE["context"] = ctx
        _BROWSER_STATE["page"] = page
        _BROWSER_STATE["loop"] = current_loop
        _BROWSER_STATE["start_count"] += 1
        return pw, ctx, page


def _data_dir() -> Path:
    from odc.config import Config
    cfg = Config()
    return Path(cfg.data_dir)


# ── Tool functions (all async) ────────────────────────────────────


@tool(
    name="browser.open",
    description=(
        "Open a URL in a real Chromium browser (headless, persistent). "
        "Returns the page title, current URL, and a snippet of the "
        "rendered text. Cookies and localStorage survive across calls. "
        "Use browser.screenshot to capture PNG, browser.evaluate to run JS, "
        "browser.text for full text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to open."},
            "wait_for": {
                "type": "string",
                "description": "Optional CSS selector to wait for before returning.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Navigation timeout in ms. Default 30000.",
                "default": 30000,
            },
        },
        "required": ["url"],
    },
    side_effect=True,
)
async def browser_open(url: str, wait_for: str = "", timeout_ms: int = 30000) -> dict:
    """Open URL in the persistent browser. Returns title, url, snippet."""
    _, _, page = await _ensure_browser(_data_dir())
    page.set_default_navigation_timeout(timeout_ms)
    await page.goto(url, wait_until="domcontentloaded")
    if wait_for:
        await page.wait_for_selector(wait_for, timeout=timeout_ms)
    title = await page.title()
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    snippet = (text or "")[:800]
    return {
        "url": page.url,
        "title": title,
        "snippet": snippet,
        "ok": True,
    }


@tool(
    name="browser.goto",
    description="Navigate the persistent browser to a new URL.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to navigate to."},
        },
        "required": ["url"],
    },
    side_effect=True,
)
async def browser_goto(url: str) -> dict:
    """Navigate (alias for open)."""
    return await browser_open(url)


@tool(
    name="browser.evaluate",
    description=(
        "Execute JavaScript in the current page and return the result. "
        "The expression is wrapped as () => <expr>. Use for clicking "
        "elements, extracting data, modifying the DOM, or reading "
        "runtime state. Result must be JSON-serializable."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "JS expression. Called as () => <expression>.",
            },
        },
        "required": ["expression"],
    },
)
async def browser_evaluate(expression: str) -> dict:
    """Run JS in current page, return JSON-serializable result."""
    if _BROWSER_STATE["page"] is None:
        return {"ok": False, "error": "no page open — call browser.open first"}
    page = _BROWSER_STATE["page"]
    result = await page.evaluate(f"() => {{ return ({expression}); }}")
    _BROWSER_STATE["evaluate_count"] += 1
    return {"ok": True, "result": result, "expression": expression}


@tool(
    name="browser.screenshot",
    description=(
        "Take a PNG screenshot of the current page. Saves to "
        "<data_dir>/browser/screenshots/<name>.png. Returns the path "
        "and byte size. Optional full_page=True captures the whole "
        "scrollable page."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Filename (no path). Default 'screenshot_<ts>.png'.",
            },
            "full_page": {
                "type": "boolean",
                "description": "Capture full scrollable page. Default false.",
                "default": False,
            },
        },
    },
)
async def browser_screenshot(name: str = "", full_page: bool = False) -> dict:
    """Save a PNG screenshot of the current page."""
    if _BROWSER_STATE["page"] is None:
        return {"ok": False, "error": "no page open — call browser.open first"}
    page = _BROWSER_STATE["page"]
    if not name:
        name = f"screenshot_{int(time.time())}.png"
    if not name.endswith(".png"):
        name += ".png"
    out_dir = _data_dir() / "browser" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / name
    await page.screenshot(path=str(out_path), full_page=full_page)
    size = out_path.stat().st_size
    _BROWSER_STATE["screenshot_count"] += 1
    return {
        "ok": True,
        "path": str(out_path),
        "size_bytes": size,
        "full_page": full_page,
    }


@tool(
    name="browser.text",
    description="Get the rendered text of the current page (post-JS). "
                "Useful for SPAs that web.fetch can't parse.",
    parameters={
        "type": "object",
        "properties": {
            "max_chars": {
                "type": "integer",
                "description": "Cap on text length. Default 50000.",
                "default": 50000,
            },
        },
    },
)
async def browser_text(max_chars: int = 50000) -> dict:
    """Extract rendered text from current page."""
    if _BROWSER_STATE["page"] is None:
        return {"ok": False, "error": "no page open — call browser.open first"}
    page = _BROWSER_STATE["page"]
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    truncated = (text or "")[:max_chars]
    return {
        "ok": True,
        "url": page.url,
        "text": truncated,
        "char_count": len(text or ""),
        "truncated": len(text or "") > max_chars,
    }


@tool(
    name="browser.html",
    description="Get the post-JS rendered HTML of the current page.",
    parameters={
        "type": "object",
        "properties": {
            "max_chars": {
                "type": "integer",
                "description": "Cap on HTML length. Default 100000.",
                "default": 100000,
            },
        },
    },
)
async def browser_html(max_chars: int = 100000) -> dict:
    """Extract rendered HTML from current page."""
    if _BROWSER_STATE["page"] is None:
        return {"ok": False, "error": "no page open — call browser.open first"}
    page = _BROWSER_STATE["page"]
    html = await page.content()
    truncated = (html or "")[:max_chars]
    return {
        "ok": True,
        "url": page.url,
        "html": truncated,
        "char_count": len(html or ""),
        "truncated": len(html or "") > max_chars,
    }


@tool(
    name="browser.cookies",
    description="List all cookies in the persistent browser context.",
    parameters={"type": "object", "properties": {}},
)
async def browser_cookies() -> dict:
    """Get all cookies in the persistent context."""
    if _BROWSER_STATE["context"] is None:
        return {"ok": False, "error": "no context — call browser.open first"}
    cookies = await _BROWSER_STATE["context"].cookies()
    return {"ok": True, "count": len(cookies), "cookies": cookies}


@tool(
    name="browser.cookies_set",
    description="Set a cookie in the persistent browser context.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Cookie name."},
            "value": {"type": "string", "description": "Cookie value."},
            "domain": {
                "type": "string",
                "description": "Cookie domain (e.g. '.example.com').",
            },
            "path": {
                "type": "string",
                "description": "Cookie path. Default '/'.",
                "default": "/",
            },
        },
        "required": ["name", "value", "domain"],
    },
)
async def browser_cookies_set(name: str, value: str, domain: str,
                                path: str = "/") -> dict:
    """Add a cookie to the persistent context."""
    if _BROWSER_STATE["context"] is None:
        return {"ok": False, "error": "no context — call browser.open first"}
    await _BROWSER_STATE["context"].add_cookies([{
        "name": name, "value": value,
        "domain": domain, "path": path,
    }])
    await _save_state()
    return {"ok": True, "name": name, "domain": domain}


@tool(
    name="browser.cookies_clear",
    description="Clear ALL cookies in the persistent browser context.",
    parameters={"type": "object", "properties": {}},
)
async def browser_cookies_clear() -> dict:
    """Wipe all cookies."""
    if _BROWSER_STATE["context"] is None:
        return {"ok": False, "error": "no context"}
    await _BROWSER_STATE["context"].clear_cookies()
    await _save_state()
    return {"ok": True, "cleared": True}


@tool(
    name="browser.close",
    description="Close the persistent browser. Cookies/localStorage persist on disk.",
    parameters={"type": "object", "properties": {}},
)
async def browser_close() -> dict:
    """Close the browser (cookies persist on disk)."""
    if _BROWSER_STATE["playwright"] is None:
        return {"ok": True, "already_closed": True}
    try:
        # Save state before closing
        if _BROWSER_STATE["context"]:
            await _save_state()
    except Exception:
        pass
    try:
        if _BROWSER_STATE["context"]:
            await _BROWSER_STATE["context"].close()
    except Exception:
        pass
    try:
        if _BROWSER_STATE["browser"]:
            await _BROWSER_STATE["browser"].close()
    except Exception:
        pass
    try:
        if _BROWSER_STATE["playwright"]:
            await _BROWSER_STATE["playwright"].stop()
    except Exception:
        pass
    _BROWSER_STATE["playwright"] = None
    _BROWSER_STATE["browser"] = None
    _BROWSER_STATE["context"] = None
    _BROWSER_STATE["page"] = None
    return {"ok": True, "closed": True}


@tool(
    name="browser.status",
    description=(
        "Get the current browser state: URL, title, cookie count, "
        "screenshot count, evaluate count, and whether the browser is open."
    ),
    parameters={"type": "object", "properties": {}},
)
async def browser_status() -> dict:
    """Report the browser's current state."""
    if _BROWSER_STATE["page"] is None:
        return {
            "ok": True,
            "open": False,
            "starts": _BROWSER_STATE["start_count"],
            "screenshots": _BROWSER_STATE["screenshot_count"],
            "evaluates": _BROWSER_STATE["evaluate_count"],
        }
    page = _BROWSER_STATE["page"]
    cookies = await _BROWSER_STATE["context"].cookies()
    return {
        "ok": True,
        "open": True,
        "url": page.url,
        "title": await page.title(),
        "cookie_count": len(cookies),
        "starts": _BROWSER_STATE["start_count"],
        "screenshots": _BROWSER_STATE["screenshot_count"],
        "evaluates": _BROWSER_STATE["evaluate_count"],
    }


ALL_BROWSER_TOOLS = [
    browser_open,
    browser_goto,
    browser_evaluate,
    browser_screenshot,
    browser_text,
    browser_html,
    browser_cookies,
    browser_cookies_set,
    browser_cookies_clear,
    browser_close,
    browser_status,
]


def register_browser_tools(registry) -> None:
    """Register all browser tools with a ToolRegistry."""
    for t in ALL_BROWSER_TOOLS:
        registry.register(t)