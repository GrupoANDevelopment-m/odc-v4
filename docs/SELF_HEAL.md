# ODC v4 — Self-Correction (Auto-Heal)

**Module:** `odc/refinement/self_heal.py`
**Date:** 2026-10-14

The user demanded:
1. Real self-correction without human authorization
2. AST + LLM combined for fix strategies
3. The agent should also IMPROVE its code, not just fix it

This module implements both: the agent detects its own bugs,
generates fixes (AST or LLM), applies them, verifies them, and
rolls back if broken — all autonomously. AND it can proactively
improve working code via LLM.

---

## The closed loop

```
observe_failure(tool, error)   # 1. count repeated failures
   ↓ (after 3+ same error)
diagnose(tool, pattern)         # 2. read the tool
   ↓
propose_fix() [AST first]       # 3a. try AST strategy
   ↓ (no AST match)
propose_fix_with_llm()          # 3b. fallback to LLM
   ↓
check_safety(fix_body)          # 4. constitutional guard
   ↓
backup → atomic write → load_tool sandbox → live test
   ↓
record self-patch                # 7. close the loop
```

For IMPROVEMENT (proactive, not just reactive):
```
propose_improvement(tool, focus) # LLM analyzes, suggests refactor
   ↓
check_safety → backup → atomic write → load → live test
   ↓
record improvement
```

---

## Strategy priority (3-tier)

1. **AST strategy** (fast, deterministic)
   - TypeError, ImportError, KeyError, ZeroDivisionError, etc.
   - No LLM call → no latency, no cost.
   - Works even when LLM is offline.

2. **LLM strategy** (fallback for novel patterns)
   - Prompt: "Fix this tool that's failing with <error>"
   - LLM returns Python code in ```python ... ``` block
   - Code is parsed, safety-checked, sandbox-tested
   - Used when AST has no match.

3. **LLM improvement** (proactive, not reactive)
   - No failure required
   - Focus: "general" | "performance" | "readability" | "safety"
   - LLM suggests refactor: better edge cases, type hints,
     docstrings, error handling
   - Same safety + sandbox pipeline

---

## What is verified (test_self_heal_llm.py)

Six sub-tests, all REAL (mock LLM, not real API to keep test fast):

| # | Test | Verifies | Result |
|---|------|----------|--------|
| 1 | AST strategy for TypeError | AST works without LLM | ✓ |
| 2 | LLM strategy for novel pattern | LLM fallback works | ✓ |
| 3 | Unsafe LLM output rejected | Constitutional guard blocks | ✓ |
| 4 | IMPROVEMENT path | Proactive code quality | ✓ |
| 5 | Idempotence | Second heal is no-op | ✓ |
| 6 | Rollback both heal+improve | Recovery works | ✓ |

Final journal: 4 entries showing mixed AST+LLM+improvement.

---

## Code extraction from LLM

The LLM is prompted to return code in `python ... ` blocks. The
parser:
1. Tries `r"```python\s*\n(.*?)\n```"` (DOTALL)
2. Falls back to `r"```\s*\n(.*?)\n```"`
3. Strips whitespace
4. Rejects empty / same-as-input

No `eval()` or `exec()` of LLM output — code is only written
to a file, then loaded via Python's import system. The safety
guard AST-validates before the load.

---

## What this is NOT

- **Not a global auto-modify of ODC itself.** Only dynamic
  tools under `<data_dir>/dynamic/tools/` are auto-healed.
  The ODC framework is read-only to this engine.

- **Not infallible.** If both AST and LLM fail to generate a
  fix, the engine returns `no-known-fix` honestly. A
  `manual` patch via the agent's normal tool-creation path
  is then the recommended next step.

---

## Files

- `odc/refinement/self_heal.py` (~24KB) — engine
- `test_self_heal.py` — original heal test (no LLM)
- `test_self_heal_rollback.py` — rollback + idempotence
- `test_self_heal_llm.py` — 6 sub-tests for AST+LLM+IMPROVEMENT

---

## How to wire into Agent loop

The engine exposes:
- `observe_failure(tool, error) → dict` — call after every tool run
- `observe_success(tool) → None` — call on success
- `attempt_heal(tool, pattern) → SelfHealRecord` — when triggered
- `attempt_improvement(tool, focus) → ImprovementRecord` — periodic

To enable LLM-based strategies, pass an `llm_provider` callable:
```python
engine = SelfHealEngine(
    paths, self_imp=self_imp,
    llm_provider=lambda prompt, **kw: my_llm.ask_sync(prompt, **kw).text,
)
```

The agent loop should:
1. Call `observe_failure` after every tool execution
2. If `triggered=True`, call `attempt_heal`
3. Periodically (e.g. nightly) call `attempt_improvement` on
   frequently-used tools
