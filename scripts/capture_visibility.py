"""Capture screenshots of the new Visibility subpage (5 tabs)."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

PORT = int(open("/tmp/odc_port").read().strip())
BASE = f"http://127.0.0.1:{PORT}"
OUTDIR = Path("/workspace/odc-v4/docs/screenshots")
OUTDIR.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            ignore_https_errors=True,
        )
        page = await ctx.new_page()

        # Load the Visibility subpage via dispatchEvent
        await page.goto(f"{BASE}/#/visibility", wait_until="load", timeout=15000)
        await page.wait_for_timeout(2000)
        # Use document.querySelector to mutate the active subpages
        await page.evaluate("""() => {
          // The HTML has chat-subpage with class active by default
          // Force show visibility, hide chat
          document.querySelectorAll('.subpage, .chat-subpage').forEach(s => s.classList.remove('active'));
          const sub = document.getElementById('subpage-visibility');
          if (sub) sub.classList.add('active');
          document.querySelectorAll('.topbar-nav a').forEach(a => {
            a.classList.toggle('active', a.dataset.route === '/visibility');
          });
          // Hide chat sidebar on non-chat pages
          document.getElementById('sidebar').style.display = 'none';
          // Trigger the loadVisibility function manually
          if (typeof loadVisibility === 'function') loadVisibility();
        }""")
        await page.wait_for_timeout(3500)
        await page.screenshot(path=str(OUTDIR / "07-visibility-tools.png"), full_page=False)
        print(f"saved 07-visibility-tools.png")

        # Click MCPs tab via JS (force=True didn't work; navigating via JS directly)
        await page.evaluate("switchVTab && switchVTab('mcps'); "
                            "if (!window.switchVTab) { "
                            "  document.querySelectorAll('#subpage-visibility .btn[data-vtab]').forEach(b => b.classList.toggle('active', b.dataset.vtab==='mcps')); "
                            "  document.querySelectorAll('#subpage-visibility .vtab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'vtab-mcps')); "
                            "}")
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUTDIR / "08-visibility-mcps.png"), full_page=False)
        print(f"saved 08-visibility-mcps.png")

        # Live Cognition
        await page.evaluate("document.querySelectorAll('#subpage-visibility .btn[data-vtab]').forEach(b => b.classList.toggle('active', b.dataset.vtab==='cog')); "
                            "document.querySelectorAll('#subpage-visibility .vtab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'vtab-cog'));")
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUTDIR / "09-visibility-cognition.png"), full_page=False)
        print(f"saved 09-visibility-cognition.png")

        # Decisions
        await page.evaluate("document.querySelectorAll('#subpage-visibility .btn[data-vtab]').forEach(b => b.classList.toggle('active', b.dataset.vtab==='decisions')); "
                            "document.querySelectorAll('#subpage-visibility .vtab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'vtab-decisions'));")
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUTDIR / "10-visibility-decisions.png"), full_page=False)
        print(f"saved 10-visibility-decisions.png")

        # Upload
        await page.evaluate("document.querySelectorAll('#subpage-visibility .btn[data-vtab]').forEach(b => b.classList.toggle('active', b.dataset.vtab==='upload')); "
                            "document.querySelectorAll('#subpage-visibility .vtab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'vtab-upload'));")
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUTDIR / "11-visibility-upload.png"), full_page=False)
        print(f"saved 11-visibility-upload.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
