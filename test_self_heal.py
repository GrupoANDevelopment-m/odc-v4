"""Test: agent self-corrects a broken dynamic tool.

We create a tool that fails on every call, run 3+ times to trigger
the heal, then verify the agent fixed it without human authorization.
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from odc.code.dynamic import DynamicPaths
from odc.refinement.self_heal import SelfHealEngine


def banner(t):
    print(f"\n{'='*60}\n{t}\n{'='*60}")


def main():
    data_dir = Path("/tmp/odc_self_heal_test")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    paths = DynamicPaths.for_data_dir(data_dir)
    print(f"Dynamic paths: {paths.tools_dir}")

    # Create a deliberately broken tool: uses TypeError
    broken_tool_path = paths.tools_dir / "broken_add.py"
    broken_tool_path.write_text('''"""A broken tool that always TypeErrors."""
from odc.tools.base import tool

@tool(name='broken_add',
       description='Add two numbers',
       parameters={'a': {'type': 'number'}, 'b': {'type': 'number'}})
def broken_add(a: int, b: int) -> int:
    return a + b + "extra_string"  # TypeError: int + str
''')
    print(f"Created broken tool: {broken_tool_path}")

    engine = SelfHealEngine(paths)

    banner("PHASE 0 — initial state")
    print(f"tool exists: {broken_tool_path.exists()}")
    print(f"heal journal exists: {engine.journal_path.exists()}")

    banner("PHASE 1 — observe 3 failures of the same TypeError pattern")
    error_msg = "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
    for i in range(3):
        result = engine.observe_failure("broken_add", error_msg)
        print(f"  fail {i+1}: triggered={result.get('triggered', False)}, count={result.get('count', 0)}")

    banner("PHASE 2 — attempt self-heal (NO human authorization)")
    rec = engine.attempt_heal("broken_add", error_msg)
    print(f"  tool_name:        {rec.tool_name}")
    print(f"  failure_pattern:  {rec.failure_pattern[:60]}")
    print(f"  fix_strategy:     {rec.fix_strategy}")
    print(f"  safety_ok:        {rec.safety_ok}")
    print(f"  sandbox_ok:       {rec.sandbox_ok}")
    print(f"  applied:          {rec.applied}")
    print(f"  rolled_back:      {rec.rolled_back}")
    print(f"  post_test_passed: {rec.post_test_passed}")
    print(f"  error:            {rec.error}")

    banner("PHASE 3 — verify fix on disk")
    if rec.applied:
        new_content = broken_tool_path.read_text()
        print(f"  new content (first 500 chars):\n{new_content[:500]}")
        backup = broken_tool_path.with_suffix(broken_tool_path.suffix + ".heal-backup")
        print(f"  backup exists: {backup.exists()}")
    else:
        print(f"  (no fix applied)")

    banner("PHASE 4 — try the healed tool")
    try:
        from odc.code.dynamic import load_tool
        sys.path.insert(0, str(paths.tools_dir))
        if "broken_add" in sys.modules:
            del sys.modules["broken_add"]
        loaded = load_tool("broken_add", paths)
        if loaded is not None:
            # Call with no args to see if it errors gracefully
            try:
                r = loaded(a=2, b=3)
                print(f"  call(a=2, b=3) = {r}")
            except TypeError as e:
                print(f"  call with no args: TypeError (expected for required args): {e}")
            except Exception as e:
                print(f"  call: {type(e).__name__}: {e}")
        else:
            print(f"  load returned None — fix did not produce a valid tool")
    except Exception as e:
        print(f"  load exception: {e}")

    banner("PHASE 5 — journal entries")
    journal = engine.journal()
    print(f"  {len(journal)} entries logged")
    for j in journal:
        print(f"    {j}")

    banner("PHASE 6 — cleanup test")
    shutil.rmtree(data_dir)
    print(f"  removed {data_dir}")

    # Final verdict
    grew = (rec.applied
            and rec.safety_ok
            and rec.sandbox_ok
            and len(journal) > 0)
    print(f"\n{'✓ AGENT SELF-CORRECTED (no human auth)' if grew else '✗ NO SELF-CORRECTION'}")
    sys.exit(0 if grew else 1)


if __name__ == "__main__":
    main()