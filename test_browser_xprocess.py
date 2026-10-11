"""Honest test: does browser state survive PROCESS RESTART?

This is the real test. If I close the browser, kill the process,
restart Python, and the cookie/state is still there — that's real
persistence. If not, it was just session caching.
"""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

# Same data dir as the earlier test
DATA_DIR = "/tmp/odc_browser_xprocess"


def run_in_subprocess(script: str) -> tuple[int, str, str]:
    """Run a small Python script in a separate process."""
    env = os.environ.copy()
    env["ODC_TEST_DATA_DIR"] = DATA_DIR
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, '.'); {script}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode, result.stdout, result.stderr


PHASE1 = f'''
import asyncio, os
from pathlib import Path
os.environ["ODC_DATA_DIR"] = "{DATA_DIR}"
from odc import Agent
from odc.config import Config

async def main():
    cfg = Config()
    cfg.data_dir = Path("{DATA_DIR}")
    cfg.ensure_dirs()
    agent = Agent(config=cfg, auto_approve=True, interactive=False)
    reg = agent.tools

    # Open
    r = await reg.run("browser.open", confirm=False, url="https://httpbin.org/get")
    print(f"OPEN_URL={{r.output.get('url', '?')}}")

    # Set cookie
    r = await reg.run("browser.cookies_set", confirm=False,
                        name="cross_process_test",
                        value="persistence_verified",
                        domain=".httpbin.org")
    print(f"SET_COOKIE={{r.output}}")

    # Verify present
    r = await reg.run("browser.cookies", confirm=False)
    n = r.output.get("count", 0)
    print(f"COOKIE_COUNT_AFTER_SET={{n}}")

    # Close
    r = await reg.run("browser.close", confirm=False)
    print(f"CLOSE={{r.output}}")

asyncio.run(main())
'''


PHASE2 = '''
import asyncio, os, json
from pathlib import Path
os.environ["ODC_DATA_DIR"] = "{DATA_DIR}"
from odc import Agent
from odc.config import Config

async def main():
    cfg = Config()
    cfg.data_dir = Path("{DATA_DIR}")
    cfg.ensure_dirs()
    agent = Agent(config=cfg, auto_approve=True, interactive=False)
    reg = agent.tools

    # Open (this should load saved state from disk)
    r = await reg.run("browser.open", confirm=False,
                        url="https://httpbin.org/cookies")
    print(f"REOPEN_URL={{r.output.get('url', '?')}}")

    # List cookies — cross_process_test should be there
    r = await reg.run("browser.cookies", confirm=False)
    n = r.output.get("count", 0)
    print(f"COOKIE_COUNT_AFTER_RESTART={{n}}")
    found = any(c.get("name") == "cross_process_test" for c in r.output.get("cookies", []))
    print(f"COOKIE_FOUND_AFTER_RESTART={{found}}")
    for c in r.output.get("cookies", []):
        if c.get("name") == "cross_process_test":
            print(f"COOKIE_VALUE={{c.get('value', '?')}}")

    # Verify state file exists on disk
    state_file = Path("{DATA_DIR}") / "browser" / "storage_state.json"
    print(f"STATE_FILE_EXISTS={{state_file.exists()}}")
    if state_file.exists():
        print(f"STATE_FILE_SIZE={{state_file.stat().st_size}}")
        state = json.loads(state_file.read_text())
        print(f"STATE_FILE_COOKIES={{len(state.get('cookies', []))}}")

    # Close
    r = await reg.run("browser.close", confirm=False)
    print(f"CLOSE={{r.output}}")

asyncio.run(main())
'''


def main():
    # Cleanup
    import shutil
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR)
    print(f"=== PHASE 1: Set cookie, close browser, exit process ===\n")
    rc, out, err = run_in_subprocess(PHASE1)
    print(out)
    if err:
        print("STDERR:", err[:500])
    if rc != 0:
        print(f"PHASE 1 FAILED with exit code {rc}")
        return
    print(f"\n=== Process 1 exited. State file should be on disk. ===\n")
    # Show state file BEFORE phase 2
    sf = Path(DATA_DIR) / "browser" / "storage_state.json"
    if sf.exists():
        print(f"State file present: {sf} ({sf.stat().st_size} bytes)")
        print(f"Contents:\n{sf.read_text()[:300]}")
    else:
        print(f"NO state file at {sf}")
    # Wait a beat (proves it's disk, not memory)
    time.sleep(1)
    print(f"\n=== PHASE 2: New process. Open browser. Check cookie. ===\n")
    rc2, out2, err2 = run_in_subprocess(PHASE2.format(DATA_DIR=DATA_DIR))
    print(out2)
    if err2:
        print("STDERR:", err2[:500])

    # Verdict
    print("\n=== VERDICT ===")
    if "COOKIE_FOUND_AFTER_RESTART=True" in out2:
        print("✓ Cookie survived process restart. Cross-process persistence REAL.")
    else:
        print("✗ Cookie LOST across process restart. Persistence is IN-PROCESS ONLY.")


if __name__ == "__main__":
    main()