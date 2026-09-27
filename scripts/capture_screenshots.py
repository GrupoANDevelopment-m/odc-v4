"""Capture screenshots of all ODC v4 web subpages.

Renders the 3D UI in a headless Chromium and saves one PNG per subpage.
Uses Playwright (already installed with chromium).
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PORT = int(__import__("os").environ.get("ODC_PORT", open("/tmp/odc_port").read().strip()))
BASE = f"http://127.0.0.1:{PORT}"
OUTDIR = Path("/workspace/odc-v4/docs/screenshots")
OUTDIR.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("/", "01-chat.png"),
    ("/#/brain", "02-brain.png"),
    ("/#/training", "03-training.png"),
    ("/#/specialize", "04-specialize.png"),
    ("/#/settings", "05-settings.png"),
]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            ignore_https_errors=True,
        )
        page = await context.new_page()

        for path, fname in PAGES:
            url = BASE + path
            print(f"  -> {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3500)  # wait for module + Three.js to load
            out = OUTDIR / fname
            await page.screenshot(path=str(out), full_page=False)
            print(f"     saved {out}")

        await browser.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
