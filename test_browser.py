"""Real browser automation test with persistent Chromium.

Run all steps in a single event loop (Playwright is loop-bound).
"""
import asyncio
import time
from pathlib import Path

from odc import Agent
from odc.config import Config


def step(label: str):
    print(f"\n→ {label}")


async def amain():
    cfg = Config()
    cfg.data_dir = Path("/tmp/odc_browser_test")
    cfg.ensure_dirs()
    print(f"Data dir: {cfg.data_dir}")
    agent = Agent(config=cfg, auto_approve=True, interactive=False)
    browser_tools = [n for n in agent.tool_names() if n.startswith("browser.")]
    print(f"Browser tools loaded: {browser_tools}")
    print(f"Total tools: {len(agent.tool_names())}")

    reg = agent.tools

    step("1. browser.status — initial state")
    r = await reg.run("browser.status", confirm=False)
    print(f"   result: {r.output}")

    step("2. browser.open — about:blank")
    t0 = time.time()
    r = await reg.run("browser.open", confirm=False, url="about:blank")
    print(f"   result: {str(r.output)[:200]}")
    print(f"   latency: {time.time()-t0:.2f}s")

    step("3. browser.evaluate — JS 2+2")
    r = await reg.run("browser.evaluate", confirm=False, expression="2 + 2")
    print(f"   result: {r.output}")

    step("4. browser.open — httpbin.org/get (real HTTP)")
    t0 = time.time()
    r = await reg.run("browser.open", confirm=False,
                        url="https://httpbin.org/get?test=odc_browser",
                        timeout_ms=15000)
    print(f"   url: {r.output.get('url', '?')[:80]}")
    print(f"   title: {r.output.get('title', '?')}")
    print(f"   snippet[:150]: {r.output.get('snippet', '?')[:150]}")
    print(f"   latency: {time.time()-t0:.2f}s")

    step("5. browser.text — full rendered text")
    r = await reg.run("browser.text", confirm=False, max_chars=400)
    print(f"   url: {r.output.get('url', '?')[:80]}")
    print(f"   text[:200]: {r.output.get('text', '?')[:200]}")

    step("6. browser.evaluate — extract JSON from page")
    r = await reg.run("browser.evaluate", confirm=False,
                        expression="document.body.innerText.substring(0, 100)")
    print(f"   extracted: {r.output}")

    step("7. browser.screenshot — real PNG")
    t0 = time.time()
    r = await reg.run("browser.screenshot", confirm=False, name="httpbin.png")
    print(f"   path: {r.output.get('path', '?')}")
    print(f"   size_bytes: {r.output.get('size_bytes', 0)}")
    print(f"   latency: {time.time()-t0:.2f}s")

    step("8. browser.cookies — list persistent cookies")
    r = await reg.run("browser.cookies", confirm=False)
    print(f"   cookie_count: {r.output.get('count', 0)}")
    for c in r.output.get('cookies', [])[:3]:
        print(f"     {c.get('name', '?')} @ {c.get('domain', '?')}")

    step("9. browser.cookies_set — set a fake cookie")
    r = await reg.run("browser.cookies_set", confirm=False,
                        name="odc_test", value="browser_persistent_works",
                        domain=".httpbin.org")
    print(f"   result: {r.output}")

    step("10. browser.cookies — verify cookie persists in session")
    r = await reg.run("browser.cookies", confirm=False)
    print(f"   cookie_count after set: {r.output.get('count', 0)}")
    found = any(c.get('name') == 'odc_test' for c in r.output.get('cookies', []))
    print(f"   odc_test cookie present: {found}")

    step("11. browser.status — full state")
    r = await reg.run("browser.status", confirm=False)
    print(f"   state: {r.output}")

    step("12. browser.close")
    r = await reg.run("browser.close", confirm=False)
    print(f"   result: {r.output}")

    step("13. Reopen and verify cookie persistence on disk")
    r = await reg.run("browser.open", confirm=False,
                        url="https://httpbin.org/cookies",
                        timeout_ms=15000)
    r2 = await reg.run("browser.cookies", confirm=False)
    print(f"   cookie_count after reopen: {r2.output.get('count', 0)}")
    found2 = any(c.get('name') == 'odc_test' for c in r2.output.get('cookies', []))
    print(f"   odc_test cookie still present: {found2}")

    step("14. List screenshots on disk")
    ss_dir = cfg.data_dir / "browser" / "screenshots"
    if ss_dir.exists():
        for p in sorted(ss_dir.glob("*.png")):
            print(f"   {p.name} ({p.stat().st_size} bytes)")

    step("15. List profile state on disk")
    state_file = cfg.data_dir / "browser" / "storage_state.json"
    if state_file.exists():
        import json
        state = json.loads(state_file.read_text())
        print(f"   storage_state.json: {state_file.stat().st_size} bytes")
        print(f"   cookies: {len(state.get('cookies', []))}")
        for c in state.get('cookies', []):
            print(f"     {c.get('name')} @ {c.get('domain')}")

    await reg.run("browser.close", confirm=False)
    print("\n=== ALL DONE — REAL CHROMIUM, REAL PERSISTENCE ===")


if __name__ == "__main__":
    asyncio.run(amain())