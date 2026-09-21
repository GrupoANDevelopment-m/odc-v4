"""Implementation of the cognitive tools.

The four tools are:

- think(note, context?) — record a thought. Stored in a JSONL file
  with timestamp, task context, and the note. Lets the agent have
  an explicit "inner voice" that the loop can reference.

- simulate(expr) — eval a Python expression in a restricted namespace
  (no imports, no file ops, no network, no exec/compile of statements).
  Just pure expressions: arithmetic, string ops, list/dict comprehension,
  etc. Returns the repr of the result. Lets the agent test logic
  cheaply before committing to a side-effect tool.

- hypothesize(hypothesis, test_tool, test_args, expected?) — declare
  a hypothesis, then call the named tool with the given args, and
  return {hypothesis, test_result, confirmed, evidence}. The agent's
  hypothetico-deductive cycle in one call.

- self_assess() — read the last N entries from the JSONL log, find
  patterns of failure or inefficiency, and write a new skill to
  .odc_data/dynamic/skills/auto_<ts>/SKILL.md that targets the gap.
  Returns the path and a summary.

The data lives at <data_dir>/cognitive/. The path is discovered via
the same mechanism dynamic tools use: a module-level set_paths() call
from the agent at startup.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from odc.cognitive.profile import CognitiveProfile
from odc.tools.base import tool

# Module-level state. Agent.py calls set_paths() at startup so the
# tools know where to read/write.
_PATHS: dict[str, Path] = {}
_PROFILE: CognitiveProfile | None = None
# Module-level provider. Set by Agent.__init__ so the cognitive tools
# (council, revise, analogy, etc.) can call the LLM without having
# the provider threaded through every signature.
LLM_PROVIDER: Any = None


def set_paths(cognitive_dir: Path) -> None:
    """Tell the cognitive tools where to store their data."""
    _PATHS["dir"] = cognitive_dir
    cognitive_dir.mkdir(parents=True, exist_ok=True)
    global _PROFILE
    _PROFILE = CognitiveProfile(cognitive_dir / "profile.json")


def get_paths() -> Path:
    p = _PATHS.get("dir")
    if p is None:
        # Default: relative to cwd. The agent should set this.
        p = Path(".odc_data/cognitive")
        p.mkdir(parents=True, exist_ok=True)
    return p


def get_profile() -> CognitiveProfile:
    global _PROFILE
    if _PROFILE is None:
        p = get_paths()
        _PROFILE = CognitiveProfile(p / "profile.json")
    return _PROFILE


# ---------------------------------------------------------------------------
# think
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.think",
    description="Record an explicit thought in the agent's scratchpad. kind: hypothesis/observation/plan/doubt/insight.",
    parameters={
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "The thought. Be concrete and falsifiable."},
            "context": {"type": "string", "description": "Optional task context."},
            "kind": {"type": "string", "description": "hypothesis/observation/plan/doubt/insight", "enum": ["hypothesis", "observation", "plan", "doubt", "insight"], "default": "observation"},
        },
        "required": ["note"],
    },
)
async def cognitive_think(note: str, context: str = "", kind: str = "observation") -> dict[str, Any]:
    p = get_paths()
    log_file = p / "thinking.jsonl"
    entry = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": kind,
        "context": context[:500],
        "note": note[:2000],
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Count similar prior notes (rough: same first 30 chars of normalized note)
    norm = re.sub(r"\s+", " ", note.lower()).strip()[:30]
    similar = 0
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                prev = json.loads(line)
                prev_norm = re.sub(r"\s+", " ", prev.get("note", "").lower()).strip()[:30]
                if prev_norm == norm:
                    similar += 1
            except Exception:
                pass

    return {
        "ok": True,
        "id": f"t{int(entry['ts'] * 1000)}",
        "kind": kind,
        "logged_to": str(log_file),
        "similar_prior": similar,
        "hint": (
            "If this is a hypothesis, follow up with cognitive.hypothesize to test it. "
            "If it is a plan, follow up by calling the tool that executes it."
        ),
    }


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------


# Whitelist for simulate: pure expression subset. No imports, no names
# starting with underscore, no exec/compile/eval, no attribute access to
# dunder methods. Statements are not allowed (only `eval`).
_DANGEROUS_NAMES = {
    "exec", "compile", "eval", "__import__", "open", "input",
    "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr",
    "breakpoint", "memoryview",
}
_SAFE_BUILTINS = {
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "chr", "complex", "dict", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "hex", "int", "isinstance", "issubclass",
    "iter", "len", "list", "map", "max", "min", "next", "object", "oct",
    "ord", "pow", "print", "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "str", "sum", "tuple", "type", "zip", "True",
    "False", "None",
}


def _safe_eval(expr: str) -> tuple[bool, Any]:
    """Evaluate a Python expression in a restricted namespace.

    Returns (ok, result_or_error). The namespace contains only the
    safe builtins and a small set of pure stdlib helpers (math).
    """
    # AST sanity: reject anything that is not an Expression node
    import ast
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"

    # Walk the tree and reject dangerous nodes
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            return False, "imports are not allowed"
        if isinstance(node, ast.ImportFrom):
            return False, "imports are not allowed"
        if isinstance(node, ast.Call):
            # Block calls to dangerous functions
            func = node.func
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_NAMES:
                return False, f"call to {func.id!r} is not allowed"
        if isinstance(node, ast.Attribute):
            # Block dunder access
            if isinstance(node.attr, str) and node.attr.startswith("__"):
                return False, "dunder attribute access is not allowed"

    # Build a namespace with safe builtins and pure math
    import math
    ns = {"math": math, "__builtins__": {k: getattr(__builtins__, k) if hasattr(__builtins__, k) else __builtins__[k] for k in _SAFE_BUILTINS}}
    try:
        result = eval(compile(tree, "<cognitive.simulate>", "eval"), ns)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, result


@tool(
    name="cognitive.simulate",
    description="Evaluate a pure Python expression in a sandbox (no imports, no I/O). Test logic cheaply.",
    parameters={
        "type": "object",
        "properties": {
            "expr": {"type": "string", "description": "A Python expression, not a statement."},
        },
        "required": ["expr"],
    },
)
async def cognitive_simulate(expr: str) -> dict[str, Any]:
    ok, result = _safe_eval(expr)
    if not ok:
        return {"ok": False, "expr": expr, "error": str(result)}
    # Truncate large results
    rendered = repr(result)
    if len(rendered) > 2000:
        rendered = rendered[:2000] + "...(truncated)"
    return {
        "ok": True,
        "expr": expr,
        "result_type": type(result).__name__,
        "result": rendered,
    }


# ---------------------------------------------------------------------------
# hypothesize
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.hypothesize",
    description="Two-phase: log a hypothesis, run a test tool, get confirmed/refuted verdict.",
    parameters={
        "type": "object",
        "properties": {
            "hypothesis": {"type": "string", "description": "A falsifiable statement."},
            "test_tool": {"type": "string", "description": "Tool to call to test."},
            "test_args": {"type": "object", "description": "Args for the test tool."},
            "predicate": {"type": "string", "description": "Optional Python expr in terms of 'result' that yields True/False."},
            "test_result": {"type": "object", "description": "Optional: actual output from test_tool, to evaluate against predicate."},
        },
        "required": ["hypothesis", "test_tool", "test_args"],
    },
)
async def cognitive_hypothesize(
    hypothesis: str,
    test_tool: str,
    test_args: dict[str, Any],
    predicate: str = "",
    test_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p = get_paths()
    log_file = p / "thinking.jsonl"

    # Phase 1: just log the hypothesis
    if test_result is None:
        entry = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": "hypothesis",
            "note": hypothesis[:2000],
            "test_tool": test_tool,
            "test_args": test_args,
            "predicate": predicate,
            "phase": "logged",
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {
            "ok": True,
            "stage": "hypothesis_logged",
            "hypothesis": hypothesis,
            "test_tool": test_tool,
            "test_args": test_args,
            "predicate": predicate,
            "next_action": (
                f"now call the tool `{test_tool}` with the args above, "
                f"then call cognitive.hypothesize again with the SAME "
                f"hypothesis/test_tool/test_args/predicate and a "
                f"test_result=<the tool output dict>."
            ),
            "log_path": str(log_file),
        }

    # Phase 2: evaluate the predicate against the test_result
    confirmed = True
    evidence = "tool returned successfully (no predicate given)"
    if predicate:
        # Use the SAME safe-eval as simulate. 'result' is bound to test_result.
        try:
            ns = {"result": test_result, "__builtins__": {}}
            # Allow safe builtins here too
            for k in ("len", "str", "int", "float", "bool", "abs", "min", "max", "sum", "any", "all", "isinstance"):
                ns[k] = eval(k)  # noqa: S307 - safe builtins only
            ns["True"] = True
            ns["False"] = False
            ns["None"] = None
            confirmed = bool(eval(predicate, ns))  # noqa: S307
            evidence = f"predicate {predicate!r} evaluated to {confirmed}"
        except Exception as e:
            confirmed = False
            evidence = f"predicate error: {type(e).__name__}: {e}"
    else:
        # Without predicate, any successful tool call confirms.
        # success is indicated by success=True OR by output being non-error.
        if isinstance(test_result, dict) and test_result.get("success") is False:
            confirmed = False
            evidence = f"tool reported failure: {test_result.get('error', '?')}"

    entry = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": "hypothesis",
        "note": hypothesis[:2000],
        "test_tool": test_tool,
        "test_args": test_args,
        "predicate": predicate,
        "test_result": test_result,
        "phase": "evaluated",
        "confirmed": confirmed,
        "evidence": evidence,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "ok": True,
        "stage": "evaluated",
        "hypothesis": hypothesis,
        "test_tool": test_tool,
        "predicate": predicate,
        "test_result": test_result,
        "confirmed": confirmed,
        "evidence": evidence,
        "verdict": "CONFIRMED" if confirmed else "REFUTED",
        "log_path": str(log_file),
    }


# ---------------------------------------------------------------------------
# self_assess
# ---------------------------------------------------------------------------


_SKILL_TEMPLATE = """---
name: {name}
description: "{description}"
triggers:
  - {trigger}
---

# Auto-generated skill: {name}

This skill was written by `cognitive.self_assess` after observing a gap
in the agent's recent tool-call history. The agent struggled with:

{struggle}

## When to use

{when_to_use}

## Procedure

{procedure}
"""


def _find_gaps(log_path: Path, limit: int = 200) -> list[dict[str, Any]]:
    """Read the JSONL log and find patterns of failure / inefficiency."""
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()[-limit:]
    errors: dict[str, int] = {}
    missing_tools: dict[str, int] = {}
    total = 0
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        total += 1
        if d.get("level") in ("WARNING", "ERROR") or d.get("msg") == "tool_error":
            tool = d.get("odc_tool") or d.get("tool") or "?"
            err = (d.get("error") or "")[:120]
            key = f"{tool}: {err}"
            errors[key] = errors.get(key, 0) + 1
        # Look for tool-not-found errors
        err = d.get("error") or ""
        if "not found" in err.lower() or "unknown tool" in err.lower():
            tool = (d.get("odc_tool") or d.get("tool") or "?")
            missing_tools[tool] = missing_tools.get(tool, 0) + 1
    gaps = []
    for k, v in sorted(errors.items(), key=lambda x: -x[1])[:5]:
        gaps.append({"type": "error", "detail": k, "count": v})
    for k, v in sorted(missing_tools.items(), key=lambda x: -x[1])[:3]:
        gaps.append({"type": "missing_tool", "detail": k, "count": v})
    return gaps


def _write_skill(gaps: list[dict[str, Any]], target_dir: Path) -> Path | None:
    """Write a new skill that addresses the most common gap."""
    if not gaps:
        return None
    top = gaps[0]
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"auto_{ts}"
    desc_parts = [f"Auto-generated after observing: {g['detail'][:80]} (x{g['count']})" for g in gaps[:2]]
    # YAML-quote the description to escape any colons
    description = " | ".join(desc_parts).replace('"', "'")
    trigger = top["detail"][:60].replace(":", "").strip() or "auto-detected gap"
    struggle = "\n".join(f"- {g['type']}: {g['detail']} (count={g['count']})" for g in gaps)
    if top["type"] == "missing_tool":
        when = "When the agent repeatedly tries to call a tool that does not exist."
        proc = (
            "1. Recognize that the tool is missing from the registry.\n"
            "2. Use `dynamic.tool_create` to build the missing tool.\n"
            "3. Reload and call it."
        )
    else:
        when = "When the agent sees the same error repeatedly in recent turns."
        proc = (
            "1. Read the error message carefully.\n"
            "2. Identify the root cause (wrong arg, wrong tool, wrong state).\n"
            "3. Form a hypothesis with `cognitive.hypothesize` and test it.\n"
            "4. Apply the fix."
        )

    skill_dir = target_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        _SKILL_TEMPLATE.format(
            name=name,
            description=description,
            trigger=trigger,
            struggle=struggle,
            when_to_use=when,
            procedure=proc,
        ),
        encoding="utf-8",
    )
    return skill_path


@tool(
    name="cognitive.self_assess",
    description="Analyze recent failures, write a new skill to dynamic/skills/auto_<ts>/SKILL.md.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Recent log entries to analyze.", "default": 200},
        },
    },
)
async def cognitive_self_assess(limit: int = 200) -> dict[str, Any]:
    p = get_paths()
    # The JSONL log lives at <data_dir>/logs/odc.jsonl
    log_path = p.parent / "logs" / "odc.jsonl"
    gaps = _find_gaps(log_path, limit=limit)
    if not gaps:
        return {
            "ok": True,
            "gaps": [],
            "message": "no failure patterns found in the recent log; nothing to write",
        }
    skill_path = _write_skill(gaps, p.parent / "dynamic" / "skills")
    return {
        "ok": True,
        "gaps": gaps,
        "skill_written": str(skill_path) if skill_path else None,
        "summary": f"found {len(gaps)} gap(s); wrote skill '{skill_path.parent.name}'" if skill_path else f"found {len(gaps)} gap(s); no skill written",
    }


# ---------------------------------------------------------------------------
# route — suggest the best approach for a new task
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.route",
    description="Given a new task, return the recommended tool sequence based on past experience. Call FIRST.",
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task you are about to perform."},
        },
        "required": ["task"],
    },
)
async def cognitive_route(task: str) -> dict[str, Any]:
    """Suggest a plan based on past experience. CRITICAL: never echo
    user task text from past patterns. Patterns are stored in
    *generalized shape* (verbs + nouns) and the route only surfaces
    the TOOL list, not the pattern text. This is part of the
    conversation-isolation contract.
    """
    prof = get_profile()
    similar = prof.find_similar_patterns(task, k=3)
    best_tools = prof.best_tools_for(task, top=5)
    matched_heuristics = []
    task_words = {w for w in task.lower().split() if len(w) > 3}
    for h in prof.data["heuristics"][-20:]:
        rule_words = {w for w in h["rule"].lower().split() if len(w) > 3}
        if task_words & rule_words:
            matched_heuristics.append(h)
    matched_heuristics = matched_heuristics[:3]
    plan_steps: list[str] = []
    if similar:
        plan_steps.append("This looks like a task pattern seen before. Suggested approach:")
        for s in similar:
            # Only show the shape (e.g. "hash file") — NEVER the raw pattern.
            shape = s.get("pattern_summary") or s.get("shape") or "similar task"
            plan_steps.append(
                f"- shape '{shape[:40]}' (seen {s['occurrences']}x) — try: "
                + ", ".join(s["suggested_tools"][:4])
            )
    else:
        plan_steps.append("No similar past task found. Use general workflow:")
        plan_steps.append("- cognitive.think to state the problem")
        plan_steps.append("- cognitive.simulate for any pure logic")
        plan_steps.append("- cognitive.hypothesize to test key assumptions")
        plan_steps.append("- call the right tool")
    if best_tools:
        plan_steps.append("Tools ranked by past success for this kind of task:")
        for name, score in best_tools:
            s = prof.data["tool_success"].get(name, {})
            rate = (
                f"{s['successes']}/{s['calls']}"
                if s.get("calls")
                else "no data"
            )
            plan_steps.append(f"- {name} (score {score:.2f}, success {rate})")
    if matched_heuristics:
        plan_steps.append("Heuristics to keep in mind:")
        for h in matched_heuristics:
            plan_steps.append(f"- {h['rule'][:200]}")
    return {
        "ok": True,
        "task": task,
        "similar_patterns": [
            {"pattern": s["pattern"], "occurrences": s["occurrences"], "tools": s["suggested_tools"]}
            for s in similar
        ],
        "best_tools": [{"tool": n, "score": round(s, 3)} for n, s in best_tools],
        "heuristics": [h["rule"] for h in matched_heuristics],
        "plan": plan_steps,
    }


# ---------------------------------------------------------------------------
# reflect — record what was learned after a task
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.reflect",
    description="Record what was learned after a task. Updates the profile (tool success, patterns, lessons).",
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task performed."},
            "success": {"type": "boolean", "description": "Did it complete successfully?"},
            "turns": {"type": "integer", "description": "How many turns."},
            "tools_used": {"type": "array", "items": {"type": "string"}, "description": "Tool names in order."},
            "lesson": {"type": "string", "description": "Optional one-line takeaway."},
            "heuristic": {"type": "string", "description": "Optional general rule for next time."},
        },
        "required": ["task", "success", "tools_used"],
    },
)
async def cognitive_reflect(
    task: str,
    success: bool,
    tools_used: list[str],
    turns: int = 0,
    lesson: str = "",
    heuristic: str = "",
) -> dict[str, Any]:
    prof = get_profile()
    # Mark tools as used (success/failure unknown individually, but the
    # call itself is recorded; we treat them as successful for the
    # metric — explicit failure tracking happens in self_assess).
    for t in tools_used:
        prof.record_tool_call(t, success=success)
    prof.record_task(success=success, turns=turns, tools_used=tools_used)
    prof.add_task_pattern(task, tools_used)
    if lesson:
        prof.add_lesson(lesson, source="agent")
    if heuristic:
        prof.add_heuristic(heuristic, source="agent")
    prof.save()
    return {
        "ok": True,
        "task": task[:200],
        "success": success,
        "turns": turns,
        "tools_used": tools_used,
        "lesson_recorded": bool(lesson),
        "heuristic_recorded": bool(heuristic),
        "summary": prof.summary(),
    }


# ---------------------------------------------------------------------------
# strengthen — explicit skill/heuristic capture from the user or agent
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.strengthen",
    description="Capture a lesson/heuristic/anti-pattern. kind: heuristic, lesson, anti_pattern.",
    parameters={
        "type": "object",
        "properties": {
            "rule": {"type": "string", "description": "The rule to remember."},
            "kind": {"type": "string", "enum": ["heuristic", "lesson", "anti_pattern"], "default": "heuristic"},
        },
        "required": ["rule"],
    },
)
async def cognitive_strengthen(rule: str, kind: str = "heuristic") -> dict[str, Any]:
    prof = get_profile()
    if kind == "lesson":
        prof.add_lesson(rule, source="user_or_agent")
    elif kind == "anti_pattern":
        prof.add_heuristic(f"NEVER: {rule}", source="user_or_agent")
    else:
        prof.add_heuristic(rule, source="user_or_agent")
    prof.save()
    return {
        "ok": True,
        "kind": kind,
        "rule": rule[:500],
        "total_heuristics": len(prof.data["heuristics"]),
        "total_lessons": len(prof.data["lessons"]),
    }


# ---------------------------------------------------------------------------
# observe — record a sequence of tool calls as a behavior pattern
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.observe",
    description="Record a sequence of tool calls as a behavior pattern for later imitation.",
    parameters={
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "Short pattern name."},
            "description": {"type": "string", "description": "What it achieves."},
            "sequence": {"type": "array", "items": {"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}}}, "description": "Ordered tool calls."},
        },
        "required": ["label", "description", "sequence"],
    },
)
async def cognitive_observe(
    label: str, description: str, sequence: list[dict[str, Any]]
) -> dict[str, Any]:
    prof = get_profile()
    patterns = prof.data.setdefault("observed_patterns", [])
    # De-duplicate by label: if same label exists, replace
    for p in patterns:
        if p["label"] == label:
            p["sequence"] = sequence
            p["description"] = description
            p["updated"] = time.time()
            p["uses"] = p.get("uses", 0)
            prof.save()
            return {
                "ok": True,
                "label": label,
                "action": "updated",
                "steps": len(sequence),
                "total_patterns": len(patterns),
            }
    patterns.append(
        {
            "label": label,
            "description": description,
            "sequence": sequence,
            "created": time.time(),
            "updated": time.time(),
            "uses": 0,
        }
    )
    prof.data["observed_patterns"] = patterns[-50:]
    prof.save()
    return {
        "ok": True,
        "label": label,
        "action": "created",
        "steps": len(sequence),
        "total_patterns": len(patterns),
    }


# ---------------------------------------------------------------------------
# imitate — replay the best matching observed pattern
# ---------------------------------------------------------------------------


def _pattern_match_score(obs: dict, target_text: str) -> float:
    """Lexical overlap between pattern label/description and target."""
    obs_text = " ".join([obs.get("label", ""), obs.get("description", "")]).lower()
    target = target_text.lower()
    obs_words = {w for w in re.findall(r"\w+", obs_text) if len(w) > 3}
    target_words = {w for w in re.findall(r"\w+", target) if len(w) > 3}
    if not obs_words or not target_words:
        return 0.0
    return len(obs_words & target_words) / len(obs_words | target_words)


@tool(
    name="cognitive.imitate",
    description="Find the best-matching observed pattern for a target task and return its tool sequence.",
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "What you want to do."},
        },
        "required": ["target"],
    },
)
async def cognitive_imitate(target: str) -> dict[str, Any]:
    prof = get_profile()
    patterns = prof.data.get("observed_patterns", [])
    if not patterns:
        return {
            "ok": True,
            "target": target,
            "matched": None,
            "fallback": "no observed patterns yet. Use cognitive.observe to record one first.",
        }
    scored = []
    for obs in patterns:
        score = _pattern_match_score(obs, target)
        # Boost by past uses (each use = 0.05)
        score += 0.05 * obs.get("uses", 0)
        scored.append((score, obs))
    scored.sort(key=lambda x: -x[0])
    best_score, best = scored[0]
    if best_score == 0:
        return {
            "ok": True,
            "target": target,
            "matched": None,
            "fallback": f"no strong match. closest: {best['label']!r} (score 0)",
            "all_patterns": [p["label"] for _, p in scored],
        }
    # Record that this pattern was used (drives popularity)
    best["uses"] = best.get("uses", 0) + 1
    prof.save()
    return {
        "ok": True,
        "target": target,
        "matched": {
            "label": best["label"],
            "description": best["description"],
            "score": round(best_score, 3),
            "uses": best["uses"],
            "steps": best["sequence"],
        },
        "instruction": (
            f"Follow the sequence below. Each step is a tool call; call "
            f"them in order with the same args. If a step fails, adapt "
            f"and continue — imitation is a starting point, not a rigid script."
        ),
    }


# ---------------------------------------------------------------------------
# investigate — record a failure with WHY and HOW TO TRY AGAIN
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.investigate",
    description=(
        "Record a failure pattern with root cause + suggested next "
        "approach + mitigations. The agent should call this whenever a "
        "tool fails in a way that suggests a systematic issue (not a "
        "transient blip). The investigation is stored in the cognitive "
        "profile; future tasks of similar shape will surface the "
        "mitigations so the agent can try again with a smarter strategy "
        "instead of avoiding the tool forever."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Name of the tool that failed.",
            },
            "why": {
                "type": "string",
                "description": "Root-cause analysis: WHY did this fail?",
            },
            "next_approach": {
                "type": "string",
                "description": "What to try next time, in plain English.",
            },
            "mitigations": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "1-5 concrete steps to make the next attempt safer. "
                    "Each one is a candidate retry strategy."
                ),
            },
        },
        "required": ["tool", "why", "next_approach", "mitigations"],
    },
)
async def cognitive_investigate(
    tool: str, why: str, next_approach: str, mitigations: list[str]
) -> dict[str, Any]:
    prof = get_profile()
    prof.add_failure_investigation(
        tool=tool, why=why, next_approach=next_approach, mitigations=mitigations
    )
    prof.save()
    return {
        "ok": True,
        "tool": tool,
        "why": why[:200],
        "next_approach": next_approach[:200],
        "mitigations": mitigations[:5],
        "hint": (
            "Recorded. The next time this tool is called, the loop will "
            "surface these mitigations in the pre_task_brief so the LLM "
            "can try again with a smarter strategy. The tool is NOT "
            "blacklisted — wisdom, not trauma."
        ),
    }


# ---------------------------------------------------------------------------
# distill — turn a successful skill into a 1-line heuristic
# ---------------------------------------------------------------------------


@tool(
    name="cognitive.distill",
    description=(
        "After a successful task, distill the most useful pattern from "
        "the active skill into a 1-line heuristic that the profile will "
        "carry forward. The agent should call this when a task using a "
        "specific skill succeeded. The distilled heuristic will be "
        "matched against future tasks via cognitive.route."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Name of the skill that was used (e.g. 'method', 'coding', 'obstacle-breaker').",
            },
            "distilled_rule": {
                "type": "string",
                "description": "A short, general rule learned from this task. Max 200 chars.",
            },
        },
        "required": ["skill_name", "distilled_rule"],
    },
)
async def cognitive_distill(skill_name: str, distilled_rule: str) -> dict[str, Any]:
    prof = get_profile()
    prof.add_heuristic(distilled_rule, source=f"distilled:{skill_name}", distilled_from=skill_name)
    prof.save()
    return {
        "ok": True,
        "skill_name": skill_name,
        "distilled_rule": distilled_rule[:200],
        "stored": True,
        "hint": "Heuristic added to the profile. Future tasks of similar shape will be routed with this rule.",
    }
