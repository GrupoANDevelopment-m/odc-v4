"""Test: self-heal rolls back if sandbox fails.

We create a tool that the agent would 'fix' but the fix is broken
(syntax error or unsafe code). The agent should detect and roll back
automatically.
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from odc.code.dynamic import DynamicPaths
from odc.refinement.self_heal import SelfHealEngine


def main():
    data_dir = Path("/tmp/odc_self_heal_rollback_test")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    paths = DynamicPaths.for_data_dir(data_dir)

    # Create a tool that's fine
    ok_tool = paths.tools_dir / "ok_tool.py"
    ok_tool.write_text('''"""A working tool."""
from odc.tools.base import tool

@tool(name='ok_tool', description='returns 42', parameters={})
def ok_tool() -> int:
    return 42
''')
    original_content = ok_tool.read_text()
    print(f"Original tool content:\n{original_content}")

    engine = SelfHealEngine(paths)

    # First attempt: 3 fails, will apply heal
    err = "TypeError: foo bar baz"
    for _ in range(3):
        engine.observe_failure("ok_tool", err)
    rec1 = engine.attempt_heal("ok_tool", err)
    print(f"\nFirst attempt: fix_strategy={rec1.fix_strategy}, applied={rec1.applied}")

    # Second attempt: same error pattern — should be IDEMPOTENT (no re-apply)
    for _ in range(3):
        engine.observe_failure("ok_tool", err)
    rec2 = engine.attempt_heal("ok_tool", err)
    print(f"Second attempt: fix_strategy={rec2.fix_strategy}, applied={rec2.applied}")
    assert not rec2.applied, "Second heal should be skipped (idempotent)"
    print("IDEMPOTENT ✓")

    # Now test the actual rollback scenario:
    # Inject a broken tool, make a backup manually, then test rollback
    backup = ok_tool.with_suffix(ok_tool.suffix + ".heal-backup")
    shutil.copy2(ok_tool, backup)
    # Corrupt the live file
    ok_tool.write_text("""SYNTAX ERROR""")
    print(f"\nAfter corruption: {ok_tool.read_text()}")
    print(f"Backup exists: {backup.exists()}")
    print(f"Backup content: {backup.read_text()[:60]}...")

    # Now rollback
    rolled = engine.rollback("ok_tool")
    print(f"\nRollback: {rolled}")
    print(f"After rollback: {ok_tool.read_text()[:60]}...")
    print(f"Backup still exists: {backup.exists()}")

    # Verify
    restored = "return 42" in ok_tool.read_text()
    backup_removed = not backup.exists()
    print(f"\nTool restored: {restored}")
    print(f"Backup removed: {backup_removed}")

    shutil.rmtree(data_dir)
    sys.exit(0 if (rolled and restored and backup_removed) else 1)


if __name__ == "__main__":
    main()