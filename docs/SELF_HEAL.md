# ODC v4 — Self-Correction (Auto-Heal)

**Module:** `odc/refinement/self_heal.py`
**Date:** 2026-10-14

The user demanded real self-correction without human authorization:
> "This doesn't need human authorization to self-correct."

This module implements the closed loop: the agent detects its own
bugs, generates fixes, applies them, verifies them, and rolls back
if broken — all autonomously.

---

## The closed loop

```
observe_failure(tool, error)   # 1. count repeated failures
   ↓ (after 3+ same error)
diagnose(tool, pattern)         # 2. read the tool, find root cause
   ↓
propose_fix(tool, body, pattern) # 3. generate candidate fix
   ↓
check_safety(fix_body)          # 4. AST safety check (I1-I10)
   ↓ (must pass)
backup current → write tmp → atomic rename
   ↓
load_tool(fixed)                # 5. sandbox: can it be loaded?
   ↓ (must succeed)
post-test: call the tool         # 6. live verify
   ↓
record self-patch                # 7. close the loop
```

If ANY step fails after the backup, automatic rollback restores
the previous version.

---

## What is verified

### `test_self_heal.py` (REAL)
- Creates a broken dynamic tool (`int + str` → TypeError)
- Observes 3 identical failures
- Triggers diagnose → propose_fix → safety_ok → sandbox_ok → apply
- New file on disk contains healed tool with safety wrapper
- Backup kept on disk
- Journal entry written to `self_heal_journal.jsonl`
- **`✓ AGENT SELF-CORRECTED (no human auth)`**

### `test_self_heal_rollback.py` (REAL)
- First heal applied to a working tool
- Second heal with same error: **IDEMPOTENT** (no re-apply) ✓
- Manually corrupt the tool
- `rollback()` restores from `.heal-backup`
- Backup file removed after restore
- **`Rollback: True, Tool restored: True, Backup removed: True`**

---

## Safety properties

1. **Constitutional guard runs** — `check_safety()` AST-validates
   every fix before apply. `os.system`, `subprocess.Popen` without
   allow-list, network calls to non-allow-listed domains, etc.
   are rejected.

2. **Rate limit** — max 10 heals per hour. If exceeded, heal
   is refused.

3. **Idempotence** — `Auto-healed:` docstring marker means the
   second heal with same pattern is a no-op.

4. **Atomic write** — write to `.tmp`, rename to live. No
   partial writes.

5. **Backup before modify** — every heal creates `.heal-backup`.
   Rollback is one method call.

6. **Sandbox test** — `load_tool()` must succeed after the fix
   is written. If `load_tool` raises, automatic rollback.

7. **Live test** — after successful load, the tool is invoked
   to ensure it doesn't crash. If it crashes, the heal is
   recorded as `post_test_passed=False` but the file is kept
   (the load succeeded; runtime may need args).

8. **Failure triggers** — only after 3+ identical errors does
   the engine attempt a heal. Single transient errors are
   ignored (no thrash).

9. **Journal** — every attempt is logged to
   `self_heal_journal.jsonl` with: ts, tool, pattern, count,
   strategy, safety_ok, sandbox_ok, applied, rolled_back,
   post_test, error.

---

## Files

- `odc/refinement/self_heal.py` (16KB) — engine
- `test_self_heal.py` — proves heal works
- `test_self_heal_rollback.py` — proves rollback + idempotence

---

## What this is NOT

- **Not LLM-generated patches.** The fix strategies are
  hand-coded AST transformations. The agent does not yet
  use the LLM to generate novel patches for novel error
  patterns. That's a future direction (LLM-as-surgeon,
  constitutional-guarded).

- **Not a global auto-modify of ODC itself.** Only dynamic
  tools under `<data_dir>/dynamic/tools/` are auto-healed.
  The ODC framework itself is read-only to this engine.

- **Not infallible.** A new failure pattern with no matching
  fix strategy returns `no-known-fix`. Honest: not all bugs
  can be fixed by these strategies.

---

## How to wire into Agent loop

The engine exposes `observe_failure(tool, error)` and
`observe_success(tool)` — call them from the tool execution
path after each tool run. When `observe_failure` returns
`triggered=True`, the agent can call `attempt_heal()`.

This wiring lives in `odc/agent/loop.py` (TODO: integration
patch pending in next iteration).
