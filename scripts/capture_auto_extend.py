"""Capture screenshot of Settings scrolled to Auto-extension."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

PORT = int(open("/tmp/odc_port").read().strip())
BASE = f"http://127.0.0.1:{PORT}"
OUTDIR = Path("/workspace/odc-v4/docs/screenshots")


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
        await page.goto(f"{BASE}/#/settings", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2500)
        # Scroll to the auto-extend section
        await page.evaluate("""
          const ae = document.querySelector('[id*="set-ae-tool-create"]');
          if (ae) ae.closest('.card').scrollIntoView({block: 'start'});
        """)
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUTDIR / "06-auto-extension.png"), full_page=False)
        print(f"saved {OUTDIR / '06-auto-extension.png'}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
