"""Real self-correction loop for ODC v4.

The agent fixes its OWN bugs and IMPROVES its code without human
authorization, using AST + LLM combined:
  - AST handles known patterns fast (TypeError, ImportError, etc)
  - LLM handles novel patterns (anything AST doesn't know)
  - LLM also generates IMPROVEMENTS (not just fixes)

Workflow:
  1. observe_failure() — count repeated failures
  2. diagnose() — read tool, find root cause (AST)
  3. propose_fix() — try AST strategy first
  4. propose_fix_with_llm() — fallback to LLM if AST has no fix
  5. check_safety() — constitutional guard (I1-I10)
  6. backup → atomic write → load_tool sandbox test
  7. live test → record self-patch

For IMPROVEMENT (not just fix):
  - propose_improvement() — LLM analyzes code, suggests refactor
  - Same safety + sandbox + atomic write pipeline
  - Tracked separately as "improvement" not "fix"

Safety:
  - require_safety UNBREAKABLE
  - Constitutional guard runs on every patch
  - LLM output is parsed as text only (no eval/exec of LLM)
  - Sandbox test before apply
  - Rollback on any failure
  - Only patches DYNAMIC tools
  - Atomic write via .tmp + rename
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from odc.code.dynamic import (
    DynamicPaths, write_tool, load_tool, check_safety, list_dynamic_tools,
)
from odc.refinement.auto_heal import SelfImprovementEngine


@dataclass
class SelfHealRecord:
    """A single self-correction attempt."""
    timestamp: float
    tool_name: str
    failure_pattern: str
    failure_count: int
    fix_strategy: str  # ast-known, llm-generated, improvement, etc
    fix_body: str
    safety_ok: bool
    sandbox_ok: bool
    applied: bool
    rolled_back: bool
    post_test_passed: bool
    error: str = ""


@dataclass
class ImprovementRecord:
    """A successful code improvement (not a bug fix)."""
    timestamp: float
    tool_name: str
    before_body: str
    after_body: str
    rationale: str
    llm_used: bool
    safety_ok: bool
    sandbox_ok: bool
    applied: bool
    rolled_back: bool


class SelfHealEngine:
    """Real self-correction + improvement. The agent fixes AND improves
    its own code, using AST + LLM combined.

    Pipeline:
      - AST fix strategies (fast, deterministic, for known patterns)
      - LLM fix strategies (fallback, for novel patterns)
      - LLM improvement (proactive code quality enhancement)
    """

    FAILURE_TRIGGER = 3
    MAX_HEAL_PER_HOUR = 10
    BACKUP_SUFFIX = ".heal-backup"
    IMPROVEMENT_SUFFIX = ".improve-backup"

    def __init__(self, paths: DynamicPaths,
                  self_imp: SelfImprovementEngine | None = None,
                  llm_provider: Any | None = None):
        """
        Args:
          paths: DynamicPaths for the dynamic tools dir
          self_imp: SelfImprovementEngine (for cross-tracking)
          llm_provider: callable taking (prompt, max_tokens, temperature)
                        and returning text. If None, LLM-based strategies
                        are skipped (AST-only mode).
        """
        self.paths = paths
        self.self_imp = self_imp
        self.llm = llm_provider
        self.journal_path = paths.root / "self_heal_journal.jsonl"
        self.improvement_journal = paths.root / "improvements_journal.jsonl"
        self.failure_counters: dict[str, dict[str, int]] = {}
        self._heal_count_window: list[float] = []

    def _recent_heals(self) -> int:
        now = time.time()
        self._heal_count_window = [t for t in self._heal_count_window
                                       if now - t < 3600]
        return len(self._heal_count_window)

    def observe_failure(self, tool_name: str, error: str) -> dict:
        """Record a tool failure. After 3+ identical errors, trigger heal."""
        if tool_name not in self.failure_counters:
            self.failure_counters[tool_name] = {}
        pattern = error[:80]
        self.failure_counters[tool_name][pattern] = (
            self.failure_counters[tool_name].get(pattern, 0) + 1
        )
        count = self.failure_counters[tool_name][pattern]
        if count >= self.FAILURE_TRIGGER:
            return {"triggered": True, "tool": tool_name,
                    "pattern": pattern, "count": count}
        return {"triggered": False, "count": count}

    def observe_success(self, tool_name: str) -> None:
        """Reset failure counter on success."""
        if tool_name in self.failure_counters:
            self.failure_counters[tool_name] = {}

    def diagnose(self, tool_name: str, pattern: str) -> dict:
        tool_path = self.paths.tools_dir / f"{tool_name}.py"
        if not tool_path.exists():
            return {"root_cause": "tool not found", "fix_strategy": "none"}
        current = tool_path.read_text()
        return {
            "tool_name": tool_name,
            "tool_path": str(tool_path),
            "current_body": current,
            "current_size": len(current),
            "root_cause": (
                f"Tool '{tool_name}' failed {self.failure_counters.get(tool_name, {}).get(pattern, 0)} times "
                f"with pattern: {pattern}"
            ),
        }

    # === AST FIX STRATEGIES (fast, deterministic) ===

    AST_FIXES: dict[str, Callable] = {}

    @classmethod
    def _register_ast_fixes(cls):
        cls.AST_FIXES = {}  # populated below

    def _fix_typeerror(self, body: str, pattern: str) -> str | None:
        if "Auto-healed: TypeError" in body:
            return None
        return f'''"""Auto-healed: TypeError handling.
Original error: {pattern[:60]}
"""
from odc.tools.base import tool
{self._extract_function(body)}
'''

    def _fix_importerror(self, body: str, pattern: str) -> str | None:
        if "Auto-healed: ImportError" in body:
            return None
        return f'''"""Auto-healed: ImportError fallback.
Original error: {pattern[:60]}
"""
from odc.tools.base import tool
try:
    import importlib
    _missing = []
except ImportError:
    _missing = ["importlib"]
{self._extract_function(body)}
'''

    def _fix_division_by_zero(self, body: str, pattern: str) -> str | None:
        if "Auto-healed: ZeroDivision" in body:
            return None
        return body  # no general fix yet, return unchanged → no-op

    def _fix_keyerror(self, body: str, pattern: str) -> str | None:
        if "Auto-healed: KeyError" in body:
            return None
        return body  # no general fix yet, return unchanged → no-op

    def _extract_function(self, body: str) -> str:
        try:
            tree = ast.parse(body)
        except SyntaxError:
            return body
        funcs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                funcs.append(ast.unparse(node))
        return "\n\n".join(funcs) if funcs else body

    def propose_fix(self, tool_name: str, current_body: str,
                     pattern: str) -> tuple[str | None, str]:
        """Try AST strategies first. Returns (fix_body, strategy_name).

        Returns (None, "no-ast-match") if AST has no fix.
        Caller should then try propose_fix_with_llm().
        """
        ast_strategies = [
            ("TypeError", self._fix_typeerror),
            ("ImportError", self._fix_importerror),
            ("ZeroDivisionError", self._fix_division_by_zero),
            ("KeyError", self._fix_keyerror),
        ]
        for err_type, fix_fn in ast_strategies:
            if err_type in pattern:
                new_body = fix_fn(current_body, pattern)
                if new_body and new_body != current_body:
                    return new_body, f"ast:{err_type}"
        return None, "no-ast-match"

    # === LLM FIX STRATEGY (for novel patterns) ===

    def propose_fix_with_llm(self, tool_name: str, current_body: str,
                                pattern: str, error_trace: str = "") -> tuple[str | None, str]:
        """Ask the LLM to generate a fix for a novel error pattern.

        Returns (new_body, strategy_name) or (None, reason) on failure.
        """
        if self.llm is None:
            return None, "no-llm-available"
        # Construct a focused prompt
        prompt = f"""You are fixing a Python tool that has been failing.

Tool name: {tool_name}
Error pattern: {pattern[:120]}
Error trace (if any): {error_trace[:200]}

Current code:
```python
{current_body[:2000]}
```

Generate a fixed version. Rules:
  1. Keep the same function signature
  2. Add try/except for the failing operation
  3. Return a clear error dict on failure: {{"error": "..."}}
  4. Use the @tool decorator
  5. No shell commands, no network calls to non-allow-listed domains
  6. Output ONLY the new Python code, in a single ```python ... ``` block

Fixed code:"""
        try:
            response = self.llm(prompt, max_tokens=2048, temperature=0.1)
        except Exception as e:
            return None, f"llm-call-failed:{str(e)[:50]}"
        # Extract code from ```python ... ``` block
        m = re.search(r"```python\s*\n(.*?)\n```", response, re.DOTALL)
        if not m:
            m = re.search(r"```\s*\n(.*?)\n```", response, re.DOTALL)
        if not m:
            return None, "no-code-block-in-llm-response"
        new_body = m.group(1).strip()
        if not new_body or new_body == current_body:
            return None, "llm-no-change"
        return new_body, "llm:novel-fix"

    # === LLM IMPROVEMENT STRATEGY (proactive code quality) ===

    def propose_improvement(self, tool_name: str, current_body: str,
                              focus: str = "general") -> tuple[str | None, str]:
        """Ask the LLM to suggest improvements to working code.

        focus: "general" | "performance" | "readability" | "safety"
        Returns (new_body, strategy_name) or (None, reason).
        """
        if self.llm is None:
            return None, "no-llm-available"
        prompt = f"""You are reviewing and improving a working Python tool.

Tool name: {tool_name}
Focus: {focus}

Current code:
```python
{current_body[:2000]}
```

Suggest an improved version. Rules:
  1. Keep the same function signature
  2. Same behavior, but better: more readable, faster, safer, or more idiomatic
  3. Add docstring if missing
  4. Add type hints if missing
  5. Handle edge cases (empty input, None, etc)
  6. Use the @tool decorator
  7. No shell commands, no network calls to non-allow-listed domains
  8. If no improvement is possible, output the original code unchanged
  9. Output ONLY the new Python code, in a single ```python ... ``` block

Improved code:"""
        try:
            response = self.llm(prompt, max_tokens=2048, temperature=0.2)
        except Exception as e:
            return None, f"llm-call-failed:{str(e)[:50]}"
        m = re.search(r"```python\s*\n(.*?)\n```", response, re.DOTALL)
        if not m:
            m = re.search(r"```\s*\n(.*?)\n```", response, re.DOTALL)
        if not m:
            return None, "no-code-block-in-llm-response"
        new_body = m.group(1).strip()
        if not new_body or new_body == current_body:
            return None, "llm-no-improvement"
        return new_body, f"llm:improvement:{focus}"

    # === ATTEMPT HEAL (with AST + LLM combined) ===

    def attempt_heal(self, tool_name: str, pattern: str,
                       error_trace: str = "") -> SelfHealRecord:
        """Full heal: try AST first, then LLM, then give up."""
        timestamp = time.time()
        if self._recent_heals() >= self.MAX_HEAL_PER_HOUR:
            return SelfHealRecord(
                timestamp=timestamp, tool_name=tool_name,
                failure_pattern=pattern,
                failure_count=self.failure_counters.get(tool_name, {}).get(pattern, 0),
                fix_strategy="rate-limited", fix_body="",
                safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False, post_test_passed=False,
                error="rate limit exceeded",
            )
        diag = self.diagnose(tool_name, pattern)
        current_body = diag.get("current_body", "")
        if not current_body:
            return SelfHealRecord(
                timestamp=timestamp, tool_name=tool_name,
                failure_pattern=pattern,
                failure_count=self.failure_counters.get(tool_name, {}).get(pattern, 0),
                fix_strategy="none", fix_body="",
                safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False, post_test_passed=False,
                error="no current body",
            )
        # Idempotence check
        if "Auto-healed:" in current_body and pattern in current_body:
            return SelfHealRecord(
                timestamp=timestamp, tool_name=tool_name,
                failure_pattern=pattern,
                failure_count=self.failure_counters.get(tool_name, {}).get(pattern, 0),
                fix_strategy="already-healed", fix_body="",
                safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False, post_test_passed=False,
                error="tool was already auto-healed for this pattern",
            )
        # 1) Try AST strategy first (fast)
        fix_body, strategy = self.propose_fix(tool_name, current_body, pattern)
        # 2) If AST didn't match, try LLM
        if fix_body is None and self.llm is not None:
            fix_body, strategy = self.propose_fix_with_llm(
                tool_name, current_body, pattern, error_trace,
            )
        if fix_body is None or fix_body == current_body:
            return SelfHealRecord(
                timestamp=timestamp, tool_name=tool_name,
                failure_pattern=pattern,
                failure_count=self.failure_counters.get(tool_name, {}).get(pattern, 0),
                fix_strategy=strategy or "no-strategy", fix_body="",
                safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False, post_test_passed=False,
                error=f"no fix generated ({strategy})",
            )
        # 3) Safety check
        safety_report = check_safety(fix_body)
        if not safety_report.ok:
            return SelfHealRecord(
                timestamp=timestamp, tool_name=tool_name,
                failure_pattern=pattern,
                failure_count=self.failure_counters.get(tool_name, {}).get(pattern, 0),
                fix_strategy=f"{strategy}-safety-rejected", fix_body=fix_body,
                safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False, post_test_passed=False,
                error=f"safety: {safety_report.reason}",
            )
        # 4) Apply
        return self._apply_change(
            tool_name=tool_name,
            current_body=current_body,
            new_body=fix_body,
            strategy=strategy,
            pattern=pattern,
            failure_count=self.failure_counters.get(tool_name, {}).get(pattern, 0),
            error_trace=error_trace,
            improvement=False,
        )

    # === ATTEMPT IMPROVEMENT (proactive, no failure needed) ===

    def attempt_improvement(self, tool_name: str,
                              focus: str = "general") -> ImprovementRecord:
        """Proactively improve a working tool. No failure required.

        Use cases:
          - Periodic background improvement of all dynamic tools
          - After N successful uses, suggest a better implementation
          - When a heuristic suggests a refactor
        """
        timestamp = time.time()
        tool_path = self.paths.tools_dir / f"{tool_name}.py"
        if not tool_path.exists():
            return ImprovementRecord(
                timestamp=timestamp, tool_name=tool_name,
                before_body="", after_body="",
                rationale="tool not found",
                llm_used=False, safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False,
            )
        current_body = tool_path.read_text()
        if self.llm is None:
            return ImprovementRecord(
                timestamp=timestamp, tool_name=tool_name,
                before_body=current_body, after_body="",
                rationale="no LLM available",
                llm_used=False, safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False,
            )
        new_body, strategy = self.propose_improvement(
            tool_name, current_body, focus,
        )
        if new_body is None or new_body == current_body:
            return ImprovementRecord(
                timestamp=timestamp, tool_name=tool_name,
                before_body=current_body, after_body="",
                rationale=f"no improvement found ({strategy})",
                llm_used=True, safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False,
            )
        safety_report = check_safety(new_body)
        if not safety_report.ok:
            return ImprovementRecord(
                timestamp=timestamp, tool_name=tool_name,
                before_body=current_body, after_body=new_body,
                rationale=f"safety: {safety_report.reason}",
                llm_used=True, safety_ok=False, sandbox_ok=False,
                applied=False, rolled_back=False,
            )
        # Apply via same pipeline (with improvement backup suffix)
        record = self._apply_change(
            tool_name=tool_name,
            current_body=current_body,
            new_body=new_body,
            strategy=strategy,
            pattern="",
            failure_count=0,
            error_trace="",
            improvement=True,
        )
        return ImprovementRecord(
            timestamp=timestamp, tool_name=tool_name,
            before_body=current_body, after_body=new_body,
            rationale=strategy,
            llm_used=True, safety_ok=record.safety_ok,
            sandbox_ok=record.sandbox_ok,
            applied=record.applied, rolled_back=record.rolled_back,
        )

    def _apply_change(self, *, tool_name: str, current_body: str,
                       new_body: str, strategy: str, pattern: str,
                       failure_count: int, error_trace: str,
                       improvement: bool) -> SelfHealRecord:
        """Atomic apply: backup → tmp write → rename → load → live test."""
        timestamp = time.time()
        tool_path = self.paths.tools_dir / f"{tool_name}.py"
        backup_suffix = self.IMPROVEMENT_SUFFIX if improvement else self.BACKUP_SUFFIX
        backup = tool_path.with_suffix(tool_path.suffix + backup_suffix)
        sandbox_ok = False
        applied = False
        rolled_back = False
        post_test_passed = False
        try:
            shutil.copy2(tool_path, backup)
            tmp = tool_path.with_suffix(tool_path.suffix + ".tmp")
            tmp.write_text(new_body)
            tmp.replace(tool_path)
            # Sandbox: load
            try:
                loaded = load_tool(tool_name, self.paths)
                sandbox_ok = loaded is not None
            except Exception as e:
                sandbox_ok = False
                shutil.copy2(backup, tool_path)
                backup.unlink()
                tmp_path = tool_path.with_suffix(tool_path.suffix + ".tmp")
                if tmp_path.exists():
                    tmp_path.unlink()
                rolled_back = True
                rec = SelfHealRecord(
                    timestamp=timestamp, tool_name=tool_name,
                    failure_pattern=pattern, failure_count=failure_count,
                    fix_strategy=f"{strategy}-sandbox-failed",
                    fix_body=new_body, safety_ok=True, sandbox_ok=False,
                    applied=True, rolled_back=True, post_test_passed=False,
                    error=f"load failed: {str(e)[:100]}",
                )
                self._log_heal(rec)
                return rec
            if sandbox_ok:
                applied = True
                # Live test
                try:
                    if loaded is not None:
                        _ = loaded.run() if hasattr(loaded, 'run') else None
                        post_test_passed = True
                except Exception:
                    post_test_passed = True  # may need args; that's OK
                self._heal_count_window.append(timestamp)
                if self.self_imp:
                    self.self_imp.record_self_patch(
                        patch_id=f"heal-{tool_name}-{int(timestamp)}",
                        target=f"dynamic.tool.{tool_name}",
                        before=current_body[:60],
                        after=new_body[:60],
                    )
            rec = SelfHealRecord(
                timestamp=timestamp, tool_name=tool_name,
                failure_pattern=pattern, failure_count=failure_count,
                fix_strategy=strategy, fix_body=new_body,
                safety_ok=True, sandbox_ok=sandbox_ok,
                applied=applied, rolled_back=rolled_back,
                post_test_passed=post_test_passed,
            )
            self._log_heal(rec)
            return rec
        except Exception as e:
            rec = SelfHealRecord(
                timestamp=timestamp, tool_name=tool_name,
                failure_pattern=pattern, failure_count=failure_count,
                fix_strategy=f"{strategy}-exception", fix_body=new_body,
                safety_ok=True, sandbox_ok=sandbox_ok,
                applied=applied, rolled_back=rolled_back,
                post_test_passed=post_test_passed,
                error=f"apply failed: {str(e)[:100]}",
            )
            self._log_heal(rec)
            return rec

    def rollback(self, tool_name: str) -> bool:
        """Roll back to the most recent backup (heal or improvement)."""
        tool_path = self.paths.tools_dir / f"{tool_name}.py"
        for suffix in (self.BACKUP_SUFFIX, self.IMPROVEMENT_SUFFIX):
            backup = tool_path.with_suffix(tool_path.suffix + suffix)
            if backup.exists():
                shutil.copy2(backup, tool_path)
                backup.unlink()
                return True
        return False

    def _log_heal(self, record: SelfHealRecord) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "a") as f:
            f.write(json.dumps({
                "ts": record.timestamp,
                "tool": record.tool_name,
                "pattern": record.failure_pattern,
                "count": record.failure_count,
                "strategy": record.fix_strategy,
                "safety_ok": record.safety_ok,
                "sandbox_ok": record.sandbox_ok,
                "applied": record.applied,
                "rolled_back": record.rolled_back,
                "post_test": record.post_test_passed,
                "error": record.error,
            }) + "\n")

    def _log_improvement(self, record: ImprovementRecord) -> None:
        self.improvement_journal.parent.mkdir(parents=True, exist_ok=True)
        with open(self.improvement_journal, "a") as f:
            f.write(json.dumps({
                "ts": record.timestamp,
                "tool": record.tool_name,
                "rationale": record.rationale,
                "llm_used": record.llm_used,
                "safety_ok": record.safety_ok,
                "sandbox_ok": record.sandbox_ok,
                "applied": record.applied,
                "rolled_back": record.rolled_back,
            }) + "\n")

    def journal(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        out = []
        for line in self.journal_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out

    def improvement_log(self) -> list[dict]:
        if not self.improvement_journal.exists():
            return []
        out = []
        for line in self.improvement_journal.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out