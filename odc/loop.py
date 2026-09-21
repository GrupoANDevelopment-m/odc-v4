"""The closed loop.

This is the core: a think-act-verify loop with hard caps and an
explicit report at the end. It is deliberately small. Anything that
sounds like agent philosophy lives in the skills, not here.

Stages per turn:
  1. LLM thinks + emits tool calls.
  2. Tools run (with confirm gates).
  3. Tool results go back to the LLM.
  4. After `verify_hard_cap` consecutive failed verifies, hand back.
  5. After `max_loop_turns`, hand back with a partial report.

The LLM is told, via the active skills, what to do at each turn. We
just give it the mechanics to do it.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from odc.config import Config
from odc.llm import Completion, Message, Provider, ToolSpec
from odc.observability import get_logger, log_event
from odc.skills import format_skill_block
from odc.skills.loader import Skill
from odc.tools.base import Tool, ToolRegistry, ToolResult

log = get_logger("odc.loop")


SYSTEM_PROMPT = """\
You are ODC, an honest agent. You follow the active skills literally.
You use the provided tools. You never claim a tool was called or a \
file was read without showing the result. You surface uncertainty; you \
do not fake rigor. When the user is silent and an outward action is \
required, you stop and ask. The fallback is always an honest hand-back.

CRITICAL — AUTONOMY AND SELF-EXTENSION
You are empowered to extend your own capabilities at runtime. If a task \
requires a tool you do not have, you MUST build it instead of refusing \
or falling back to shell tricks. The workflow is:

1. Analyze the task. Identify what you need that you don't already have.
2. Call `dynamic.tool_create` with a Python source body that defines the \
   new tool using the `@tool` decorator from `odc.tools.base`. The \
   decorator MUST include `parameters=...` as a JSON Schema dict.
3. The new tool is AUTO-LOADED into your registry on the next call. \
   You do NOT need to call `dynamic.tool_load` — it happens for you.
4. On the very next turn, call the new tool by its name.
5. Verify the result with the appropriate read or test tool.

Do not use `shell.run` for tasks a tool can do. Do not try to cd, run \
python, or invoke subprocesses — `shell.run` is allowlist-restricted. \
Use `fs.read`, `code.read`, `dynamic.tool_list`, `dynamic.tool_test`, \
etc. instead.

NEVER ANSWER FROM MEMORY FOR COMPUTATION.
If the task involves encoding, hashing, math, conversion, parsing, or \
any deterministic operation, you MUST call a tool to compute the answer. \
Do not guess, do not estimate, do not answer from your training data. \
The user expects the EXACT result from a real computation, not a likely \
answer. If you don't have a tool, build one with `dynamic.tool_create` \
and call it.

When you finish, write a short report describing what you created, what \
you called, and what the result was. If you failed, say so honestly.

WORKED EXAMPLE
User: "compute SHA-256 of .env"
You: dynamic.tool_create(name="sha256_file", description="...", \
  body=<valid @tool source>). Then on next turn: \
  sha256_file(path=".env"). Report the hex digest.

THE LEARNING LOOP (mandatory on non-trivial tasks)
- Start: call `cognitive.route(task)` to load the recommended plan
- End: call `cognitive.reflect(task, success, turns, tools_used,
  lesson=..., heuristic=...)` to update your profile

If a future task looks similar to a past one, `cognitive.route` will
already suggest the right approach. Use that — do not re-derive.
"""


@dataclass
class LoopResult:
    """The final result of running the loop on a task."""

    task: str
    report: str
    turns: int = 0
    tool_calls: int = 0
    verify_failures: int = 0
    handed_back_reason: str | None = None
    final_messages: list[Message] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    thread_id: str | None = None
    resumed_from: int = 0  # turn we resumed from (0 = fresh)
    tools_used: list[str] = field(default_factory=list)  # tool names called
    elapsed_sec: float = 0.0  # wall-clock time


@dataclass
class UserConfirmFn:
    """Pluggable user-confirmation strategy.

    - default: refuse side-effect tools unless `auto_approve=True`.
    - The CLI overrides this with a real prompt.
    """

    auto_approve: bool = False
    interactive: bool = True

    def __call__(self, prompt: str) -> bool:
        if self.auto_approve:
            return True
        if not self.interactive:
            return False
        # The CLI swaps this for a real input(). The default
        # here is the safe refusal: unattended = no.
        return False


class Loop:
    """The closed loop. Drive it with `await loop.run(task)`."""

    def __init__(
        self,
        *,
        config: Config,
        provider: Provider,
        tools: ToolRegistry,
        skills: list[Skill] | None = None,
        confirm: UserConfirmFn | None = None,
        on_event: Any = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tools = tools
        self.skills = skills or []
        self.confirm = confirm or UserConfirmFn(auto_approve=False, interactive=False)
        self.on_event = on_event  # callback for streaming UI (optional)
        # Per-run state for the dynamic prompt builder
        self._thread_id: str | None = None
        self._recent_tool_calls: list[str] = []
        self.data_dir = config.data_dir  # for thread context persistence

    # ------------------------------------------------------------------ public

    async def run(self, task: str, *, thread_id: str | None = None) -> LoopResult:
        t0 = time.perf_counter()
        # Thread id: if not provided, derive deterministically from the task
        # so retries of the same task can resume from a checkpoint.
        import hashlib
        if thread_id is None:
            thread_id = "t-" + hashlib.sha1(task.encode("utf-8")).hexdigest()[:16]
        self._thread_id = thread_id  # for the dynamic prompt builder
        log_event(
            log, 20, "loop_start",
            task=task[:200], turns_cap=self.config.max_loop_turns, thread_id=thread_id,
        )

        # Check for an existing checkpoint to resume from
        from odc.checkpoint import LoopCheckpoint
        cp = LoopCheckpoint(self.config.data_dir / "checkpoints.db")
        existing = cp.load_latest(thread_id)
        resumed_from = 0
        if existing and existing["task"] == task:
            # Resume: load the saved messages
            messages = [Message(**m) for m in existing["messages"]]
            resumed_from = existing["turn"]
            log_event(
                log, 20, "loop_resumed",
                thread_id=thread_id, turn=resumed_from,
            )
        else:
            messages = self._initial_messages(task)
        # SYSTEM-LEVEL WORKFLOW ENFORCEMENT
        # The LLM cannot be trusted to remember the workflow on its own.
        # Before the LLM sees the task, we run cognitive.route() and
        # cognitive.recall() to load past experience, and inject the
        # results as a system message. This makes the workflow a
        # *property of the system*, not a suggestion in the prompt.
        try:
            messages = await self._pre_task_brief(task, messages)
        except Exception as e:
            log_event(log, 30, "pre_task_brief_failed", error=str(e)[:200])
        # Counters for the periodic nudge
        last_nudge_turn = 0
        NUDGE_EVERY = 4  # turns

        verify_failures = 0
        tool_calls = 0
        last_text = ""
        last_assistant_message: Message | None = None
        handed_back: str | None = None

        for turn in range(1, self.config.max_loop_turns + 1):
            # Periodic nudge: if the LLM hasn't used a cognitive tool in
            # N turns, re-inject the workflow reminder.
            if turn - last_nudge_turn >= NUDGE_EVERY and turn > 1:
                messages = self._inject_nudge(messages, turn)
                last_nudge_turn = turn
            self._emit("turn_start", {"turn": turn})
            # Heartbeat for the supervisor (no-op when not supervised)
            try:
                from odc.daemon.supervisor import _current_supervisor  # type: ignore
                _current_supervisor.heartbeat()
            except Exception:
                pass

            completion = await self.provider.chat(
                messages,
                tools=self.tools.specs() or None,
            )
            self._emit(
                "llm_response",
                {
                    "turn": turn,
                    "text_len": len(completion.text),
                    "tool_calls": len(completion.tool_calls),
                    "usage": completion.usage,
                },
            )
            last_text = completion.text

            # Persist assistant turn.
            assistant_msg = Message(
                role="assistant",
                content=completion.text,
                tool_calls=completion.tool_calls,
            )
            messages.append(assistant_msg)
            last_assistant_message = assistant_msg
            self._record({"role": "assistant", "turn": turn, **assistant_msg.to_dict()})

            # If no tool calls: model is done. Append the final report.
            if not completion.tool_calls:
                break

            # Run each tool call.
            any_verify_failed = False
            for tc in completion.tool_calls:
                tool_calls += 1
                name = tc.get("name", "")
                args = tc.get("arguments") or {}
                # The OpenAI wire format sends arguments as a JSON string.
                # Some providers (and tests) send a dict. Handle both.
                if isinstance(args, str):
                    import json as _json
                    try:
                        args = _json.loads(args) if args.strip() else {}
                    except Exception:
                        args = {}
                tool: Tool | None = None
                try:
                    tool = self.tools.get(name)
                except KeyError:
                    result = ToolResult(success=False, error=f"unknown tool: {name}")
                else:
                    # Auth gate: confirm side-effect tools.
                    if tool.requires_confirm:
                        authorized = self.confirm(
                            f"Run {name}({json.dumps(args)[:200]})?"
                        )
                        if not authorized:
                            result = ToolResult(
                                success=False,
                                error=(
                                    f"user did not authorize '{name}'. "
                                    f"If unattended, refuse side-effecting tools."
                                ),
                            )
                        else:
                            # Use tool_name= to avoid collision with tools
                            # whose first arg is named `name`.
                            result = await self.tools.run(
                                tool_name=name, confirm=True, **args
                            )
                    else:
                        result = await self.tools.run(tool_name=name, **args)
                    # Track for dynamic prompt Layer 2 (continuity)
                    if name not in self._recent_tool_calls:
                        self._recent_tool_calls.append(name)
                    # Keep last 4 only
                    self._recent_tool_calls = self._recent_tool_calls[-4:]

                # Record the tool result.
                tool_msg = Message(
                    role="tool",
                    content=result.to_message(),
                    tool_call_id=tc.get("id", ""),
                    name=name,
                )
                messages.append(tool_msg)
                self._record(
                    {
                        "role": "tool",
                        "turn": turn,
                        "tool": name,
                        "args": _truncate_dict(args),
                        "result": result.to_dict(),
                    }
                )
                self._emit(
                    "tool_result",
                    {"turn": turn, "tool": name, "ok": result.success, "ms": result.duration_ms},
                )

                # Verify accounting: a tool call whose name starts with
                # "verify." or returns success=False is a verify failure.
                if name.startswith("verify.") or (not result.success and name != "memory.save"):
                    any_verify_failed = True

            if any_verify_failed:
                verify_failures += 1
            else:
                verify_failures = 0  # any success resets

            if verify_failures >= self.config.verify_hard_cap:
                # System 2 auto-trigger: before giving up, try the
                # Council of Lenses + deep research plan. If they
                # produce actionable advice, we retry ONE more time
                # with the system-2 brief injected. Otherwise we
                # hand back gracefully with a system-2-informed
                # report.
                sys2 = await self._system2_fallback(
                    task=task,
                    context=f"verify_hard_cap hit after {verify_failures} failures in turn {turn}",
                    messages=messages,
                )
                if sys2 and sys2.get("should_retry"):
                    log_event(log, 20, "system2_triggered", reason="verify_hard_cap")
                    # Inject the system-2 brief as a system message
                    # and let the loop continue one more turn.
                    messages.append(Message(
                        role="system",
                        content=sys2.get("brief", ""),
                    ))
                    verify_failures = max(0, verify_failures - 1)  # give it a second chance
                    continue
                handed_back = (
                    f"hit verify_hard_cap={self.config.verify_hard_cap}; "
                    f"system2_tried={sys2 is not None}; "
                    f"stopping to hand back rather than thrash"
                )
                self._emit("hand_back", {"reason": handed_back})
                break

            # Save checkpoint after each successful (or attempted) turn.
            # If the daemon crashes here, we resume from this turn.
            try:
                cp.save(
                    thread_id=thread_id,
                    turn=turn,
                    task=task,
                    messages=[m.__dict__ for m in messages],
                    state={
                        "tool_calls": tool_calls,
                        "verify_failures": verify_failures,
                    },
                )
            except Exception as e:
                log_event(log, 30, "checkpoint_save_failed", error=str(e)[:200])

        if handed_back is None and turn == self.config.max_loop_turns:
            handed_back = f"hit max_loop_turns={self.config.max_loop_turns}"

        # Force a final report if the model didn't produce one.
        report_text = last_text.strip()
        if not report_text and last_assistant_message is not None:
            # Ask the model once for a summary.
            messages.append(
                Message(
                    role="user",
                    content=(
                        "The loop is ending. Produce a final report: outcome first, "
                        "then what you did, what you observed, and any caveats."
                    ),
                )
            )
            completion = await self.provider.chat(messages)
            report_text = completion.text.strip()
            messages.append(Message(role="assistant", content=report_text))

        # Trim very long transcripts to keep memory bounded.
        messages = _trim(messages, max_messages=60)

        result = LoopResult(
            task=task,
            report=report_text or "(no report produced — loop ended without assistant text)",
            turns=turn,
            tool_calls=tool_calls,
            verify_failures=verify_failures,
            handed_back_reason=handed_back,
            final_messages=messages,
            thread_id=thread_id,
            resumed_from=resumed_from,
            tools_used=list(self._recent_tool_calls),
            elapsed_sec=time.perf_counter() - t0,
        )
        # Mark thread as completed so the daemon can show status
        try:
            if handed_back is None and verify_failures == 0:
                cp.mark_done(thread_id, "completed", f"turns={turn}")
            elif handed_back:
                cp.mark_done(thread_id, "handed_back", handed_back)
        except Exception:
            pass
        log_event(
            log,
            20,
            "loop_end",
            turns=result.turns,
            tool_calls=result.tool_calls,
            verify_failures=result.verify_failures,
            handed_back=handed_back,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        # Automatic reflection: every completed task updates the
        # cognitive profile so the next task can route on past experience.
        try:
            await self._auto_reflect(result)
        except Exception as e:
            log_event(log, 30, "auto_reflect_outer_fail", error=str(e)[:200])
        return result

    async def _auto_reflect(self, result: "LoopResult") -> None:
        """Best-effort: record this task in the cognitive profile."""
        import traceback as _tb
        try:
            from odc.cognitive.tools import cognitive_reflect, set_paths as _set_cog
        except Exception as e:
            log_event(log, 30, "reflect_import_failed", error=str(e)[:200])
            return
        # Make sure the cognitive module knows where to write.
        try:
            _set_cog(self.config.data_dir / "cognitive")
        except Exception:
            pass
        # Collect tool names from the final_messages. Each tool-role
        # message has its `name` attribute set to the tool name when the
        # loop recorded the call.
        tools_used: list[str] = []
        for msg in result.final_messages:
            if msg.role == "tool" and msg.name:
                if msg.name not in tools_used:
                    tools_used.append(msg.name)
        if not tools_used:
            # Fallback: also try transcript
            for entry in result.transcript:
                t = entry.get("tool")
                if t and t not in tools_used:
                    tools_used.append(t)
        if not tools_used:
            log_event(log, 20, "reflect_skipped_no_tools")
            return
        success = (result.handed_back_reason is None) and (
            result.verify_failures == 0
        )
        try:
            r = await cognitive_reflect(
                task=result.task,
                success=success,
                turns=result.turns,
                tools_used=tools_used,
            )
            log_event(log, 20, "reflect_done", tools=len(tools_used), success=success)
            # Wisdom, not trauma: when a tool failed, record an
            # investigation (root cause + next approach + mitigations)
            # instead of a hard anti-pattern. The LLM can re-attempt
            # with the mitigations.
            await self._auto_investigate_failures(result, tools_used)
        except Exception as e:
            log_event(log, 30, "reflect_failed", error=str(e)[:200], traceback=_tb.format_exc()[:500])

    async def _auto_investigate_failures(self, result, tools_used: list[str]) -> None:
        """For each tool that errored, record an investigation if we
        haven't already. The investigation gives WHY + HOW TO TRY AGAIN.
        """
        try:
            from odc.cognitive.profile import CognitiveProfile
            from odc.cognitive.tools import get_paths as _cog_paths
        except Exception:
            return
        prof = CognitiveProfile(_cog_paths() / "profile.json")
        # Look at the transcript for tool_error events
        for entry in result.transcript:
            err = entry.get("error") or ""
            tool = entry.get("tool") or ""
            if not tool or not err:
                continue
            # Heuristic: if the error mentions a specific known issue,
            # record a tailored investigation
            el = err.lower()
            why = ""
            next_approach = ""
            mitigations: list[str] = []
            if "no such tool" in el or "tool not found" in el or "unknown tool" in el:
                why = f"Tool {tool!r} not in registry. The LLM tried to call a tool that doesn't exist — possibly a typo or a not-yet-created dynamic tool."
                next_approach = f"Use dynamic.tool_list to see available tools, or dynamic.tool_create to build {tool!r}."
                mitigations = [
                    f"call dynamic.tool_list first to confirm {tool!r} exists",
                    "if missing, call dynamic.tool_create to build it",
                    "verify the @tool decorator's name= matches the call site",
                ]
            elif "shell" in el and "not in" in el:
                why = f"shell.run doesn't allow {tool!r}. Allowlist is restrictive."
                next_approach = f"Don't use shell.run for {tool!r}. Either build a dynamic tool or use code.run with python."
                mitigations = [
                    "build a dynamic tool with @tool wrapping the python implementation",
                    "use code.run with the python: prefix",
                    "if the binary is essential, add it to ODC_SHELL_ALLOWLIST",
                ]
            elif "timeout" in el or "timed out" in el:
                why = f"{tool!r} timed out. Probably network or slow downstream."
                next_approach = f"Retry with shorter scope, or fall back to a cached/static result."
                mitigations = [
                    "reduce the request size (fewer results, smaller page)",
                    "use the policy.fallback if available",
                    "check the resilience layer's circuit-breaker state",
                ]
            elif "rate" in el or "429" in el:
                why = f"{tool!r} hit a rate limit."
                next_approach = "Back off and retry after the Retry-After window."
                mitigations = [
                    "wait Retry-After seconds before retry",
                    "use exponential backoff with jitter",
                    "switch to a lower-volume alternative tool",
                ]
            else:
                continue  # uninteresting errors don't get an investigation
            prof.add_failure_investigation(
                tool=tool, why=why, next_approach=next_approach, mitigations=mitigations,
            )
        prof.save()

    # ----------------------------------------------------------------- helpers

    async def _system2_fallback(
        self,
        task: str,
        context: str,
        messages: list[Message],
    ) -> dict[str, Any] | None:
        """System 2 auto-trigger: when System 1 fails (verify_hard_cap,
        repeated tool errors, etc), run the Council of Lenses + revise
        + investigate_deep cascade. If the cascade produces actionable
        advice, return a brief the loop can inject; otherwise None.

        This is the central DEEP-REASON hook. It is opt-in: the
        default config has it enabled. To disable, set
        `config.deep_reason_enabled = False`.
        """
        # Respect the config flag
        if not getattr(self.config, "deep_reason_enabled", True):
            return None
        try:
            from odc.cognitive.deep_reason_tools import (
                cognitive_council,
                cognitive_revise,
                cognitive_investigate_deep,
                cognitive_analogy,
            )
            # 1) Council
            council_out = await cognitive_council(
                problem=task, context=context,
            )
            # 2) Revise (steel-man) — what is the LLM possibly wrong about?
            revise_out = await cognitive_revise(
                current_hypothesis=(council_out.get("synthesis") or task)[:500],
                evidence=context,
                failures=context,
            )
            should_change = bool(revise_out.get("should_change_approach"))
            new_conf = float(revise_out.get("new_confidence", 0.5))
            suggested = (revise_out.get("suggested_next_move") or "").strip()
            # 3) Investigate deep (research plan, no LLM call needed)
            deep_out = await cognitive_investigate_deep(
                query=task, max_attempts=4,
            )
            # 4) Analogy (real-world pattern)
            analogy_out = await cognitive_analogy(
                problem=task, failures=context,
            )
            # Compose the brief
            brief_lines = [
                "[SYSTEM 2 FALLBACK — your previous approach is failing.]",
                "",
                "## Council of Lenses (5 perspectives)",
                council_out.get("synthesis", ""),
            ]
            perspectives = council_out.get("perspectives") or {}
            for name in ("expert", "hacker", "researcher", "developer", "investigator"):
                v = perspectives.get(name) or ""
                if v:
                    brief_lines.append(f"- {name.upper()}: {v}")
            if revise_out.get("steel_man_opposite"):
                brief_lines.append("")
                brief_lines.append("## Steel-man (adversarial)")
                brief_lines.append(f"Opposite view: {revise_out['steel_man_opposite']}")
                if revise_out.get("blind_spots"):
                    brief_lines.append("Blind spots: " + " | ".join(revise_out["blind_spots"]))
                if revise_out.get("alternative_variants"):
                    brief_lines.append("Variants: " + " | ".join(revise_out["alternative_variants"]))
                if revise_out.get("falsification_criteria"):
                    brief_lines.append("Would falsify if: " + " | ".join(revise_out["falsification_criteria"]))
            if analogy_out.get("analogs"):
                brief_lines.append("")
                brief_lines.append("## Analogical lens")
                for a in analogy_out["analogs"][:2]:
                    brief_lines.append(f"- {a['name']} ({a['domain']}): {a['tactic']}")
                if analogy_out.get("extracted_strategy"):
                    brief_lines.append(f"Strategy: {analogy_out['extracted_strategy']}")
            if deep_out.get("plan"):
                brief_lines.append("")
                brief_lines.append("## Deep research plan (try in order, stop on first hit)")
                for step in deep_out["plan"][:5]:
                    brief_lines.append(
                        f"- {step['step_id']}: {step['action']} "
                        f"({step.get('stop_if', 'no stop condition')})"
                    )
            brief_lines.append("")
            brief_lines.append(
                f"Confidence after revision: {new_conf:.2f}. "
                f"{'CHANGE APPROACH.' if should_change else 'Keep current direction but apply the council/analogy tactics above.'} "
                f"{suggested}"
            )
            return {
                "brief": "\n".join(brief_lines),
                "should_retry": new_conf >= 0.3 or should_change,
                "should_change_approach": should_change,
                "new_confidence": new_conf,
                "council": council_out,
                "revise": revise_out,
                "analogy": analogy_out,
                "deep_plan": deep_out,
            }
        except Exception as e:
            log_event(log, 30, "system2_fallback_failed", error=str(e)[:200])
            return None

    def _initial_messages(self, task: str) -> list[Message]:
        """Assemble the dynamic system prompt (Phase 1+2 of the
        dynamic prompt builder).

        The full SYSTEM_PROMPT + all skills is replaced by a 4-layer
        build:
          Layer 0: core identity + rules
          Layer 1: task-matched skills + thread facts
          Layer 2: tool subset (5-8 of N) by BM25
          Layer 3: conversation tail
        """
        # Build a skill dict for the BM25 ranker
        skills_dict: dict[str, Any] = {}
        for s in self.skills:
            skills_dict[s.name] = s
        # Build the tool list for Layer 2
        all_tools: list[Any] = []
        for n in self.tools.names():
            t = self.tools.get(n)
            if t is not None:
                all_tools.append(t)
        # Profile data
        prof_data: dict[str, Any] = {}
        try:
            from odc.cognitive.profile import CognitiveProfile
            from odc.cognitive.tools import get_paths as _cog_paths
            prof_data = CognitiveProfile(_cog_paths() / "profile.json").data
        except Exception:
            pass
        # Thread context
        from odc.prompt.scope import ThreadContext
        thread_id = self._thread_id or "default"
        thread_ctx = ThreadContext(thread_id, self.data_dir)
        thread_ctx.data["task"] = task[:500]
        thread_ctx.save()
        # Build the identity block (if any). The Agent loaded an
        # identity skill; pull its body to inject as Layer 0 base
        # so the LLM always knows who it is — even when BM25 filters
        # the 'identity' skill out of Layer 1.
        identity_block = ""
        for s in self.skills:
            if s.name == "identity":
                body = getattr(s, "body", "") or ""
                if isinstance(body, str) and body.strip():
                    identity_block = body + "\n\n"
                break
        # Build
        from odc.prompt.builder import build_dynamic_prompt
        built = build_dynamic_prompt(
            task=task,
            thread_ctx=thread_ctx,
            all_tools=all_tools,
            skills=skills_dict,
            profile_data=prof_data,
            recent_tool_calls=self._recent_tool_calls,
            identity_block=identity_block,
        )
        # Log for observability
        log_event(
            log, 20, "dynamic_prompt_built",
            tokens=built["token_estimate"],
            layers=built["layers"],
            tools=len(built["selected_tools"]),
            referenced_past=built["referenced_past"],
        )
        sys = built["system_prompt"]
        return [
            Message(role="system", content=sys),
            Message(role="user", content=task),
        ]

    async def _pre_task_brief(
        self, task: str, messages: list[Message]
    ) -> list[Message]:
        """Run cognitive.route + cognitive.recall before the LLM sees the
        task. Inject the result as a system message so the LLM cannot
        ignore it (it's part of the context).

        The LLM is unreliable at following workflows from prompts
        alone. By making the brief part of the *message sequence*,
        we turn a prompt suggestion into a system-level guarantee.
        """
        from odc.cognitive.tools import cognitive_route, cognitive_imitate
        from odc.cognitive.profile import CognitiveProfile
        from odc.cognitive.tools import get_paths as _cog_paths

        brief_parts: list[str] = ["## SYSTEM-GENERATED BRIEF (pre-task)"]
        brief_parts.append(
            "The following is auto-generated by the loop before you "
            "see the task. Treat it as authoritative context."
        )
        # 1. Route
        try:
            plan = await cognitive_route(task=task)
            plan_d = plan.output if hasattr(plan, "output") else plan
            brief_parts.append("\n### cognitive.route() recommendation")
            for line in plan_d.get("plan", [])[:8]:
                brief_parts.append(f"- {line}")
        except Exception as e:
            brief_parts.append(f"\n(route failed: {e})")

        # 2. Recall: surface relevant lessons and heuristics
        try:
            prof = CognitiveProfile(_cog_paths() / "profile.json")
            relevant_lessons: list[str] = []
            relevant_heuristics: list[str] = []
            words = {w for w in task.lower().split() if len(w) > 3}
            for h in prof.data.get("heuristics", [])[-20:]:
                if words & {w for w in h["rule"].lower().split() if len(w) > 3}:
                    relevant_heuristics.append(h["rule"])
            for l in prof.data.get("lessons", [])[-20:]:
                if words & {w for w in l["lesson"].lower().split() if len(w) > 3}:
                    relevant_lessons.append(l["lesson"][:200])
            if relevant_heuristics or relevant_lessons:
                brief_parts.append("\n### Past lessons & heuristics for this kind of task")
                for h in relevant_heuristics[:3]:
                    brief_parts.append(f"- HEURISTIC: {h}")
                for l in relevant_lessons[:3]:
                    brief_parts.append(f"- LESSON: {l}")
        except Exception as e:
            brief_parts.append(f"\n(recall failed: {e})")

        # 2b. Wisdom not trauma: surface mitigations from past failure
        # investigations for tools likely to be called for this task.
        try:
            prof = CognitiveProfile(_cog_paths() / "profile.json")
            investigations = prof.data.get("failure_investigations", [])
            if investigations:
                # Match investigations by tool-name keywords in the task
                task_lower = task.lower()
                relevant: list[str] = []
                seen: set[str] = set()
                for inv in investigations[-10:]:
                    tool = inv.get("tool", "")
                    if not tool or tool in seen:
                        continue
                    # Match if the tool name or any mitigation is mentioned
                    if (
                        tool.lower() in task_lower
                        or any(m.lower().split()[0] in task_lower for m in inv.get("mitigations", []))
                    ):
                        relevant.append(tool)
                        seen.add(tool)
                if relevant:
                    brief_parts.append(
                        "\n### Wisdom from past failures — TRY AGAIN, but smarter"
                    )
                    for tool in relevant[:3]:
                        mits = prof.get_mitigations_for(tool)
                        brief_parts.append(
                            f"- {tool}: previously failed, but these mitigations are known: "
                            + " | ".join(mits[:3])
                        )
        except Exception:
            pass

        # 3. Imitation: if a matching pattern exists, surface it
        try:
            match = await cognitive_imitate(target=task)
            match_d = match.output if hasattr(match, "output") else match
            if match_d.get("matched"):
                m = match_d["matched"]
                brief_parts.append(
                    f"\n### cognitive.imitate() found a pattern: '{m['label']}'"
                )
                brief_parts.append(f"Description: {m['description']}")
                brief_parts.append(
                    f"Suggested steps ({len(m['steps'])}): "
                    + ", ".join(s["tool"] for s in m["steps"][:6])
                )
        except Exception as e:
            brief_parts.append(f"\n(imitate failed: {e})")

        brief = "\n".join(brief_parts)
        log_event(log, 20, "pre_task_brief", len=len(brief), parts=len(brief_parts) - 1)

        # Insert the brief as a system message right BEFORE the user task.
        # This is the key: the LLM cannot miss it.
        return messages[:-1] + [
            Message(role="system", content=brief),
            messages[-1],
        ]

    def _inject_nudge(self, messages: list[Message], turn: int) -> list[Message]:
        """Re-inject a workflow reminder at periodic intervals.

        The LLM may 'forget' the cognitive tools exist as the context
        grows. This puts the reminder back in the conversation.
        """
        nudge = (
            f"## SYSTEM NUDGE (turn {turn})\n"
            f"Reminder: you have cognitive tools available — "
            f"cognitive.think, cognitive.simulate, cognitive.hypothesize, "
            f"cognitive.imitate, cognitive.observe, cognitive.route, "
            f"cognitive.reflect. If you are uncertain, use them. "
            f"If you need a tool that does not exist, call "
            f"dynamic.tool_create to build it. The system will auto-load "
            f"and auto-apply it. You do not need to ask permission."
        )
        return messages + [Message(role="system", content=nudge)]

    def _record(self, entry: dict[str, Any]) -> None:
        # Placeholder hook — the Agent wraps the loop and collects
        # the full transcript. Kept here so the loop is independently
        # runnable for tests.
        pass

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event, payload)
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------------------

def _truncate_dict(d: dict[str, Any], limit: int = 200) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        s = str(v)
        if len(s) > limit:
            out[k] = s[:limit] + "..."
        else:
            out[k] = v
    return out


def _trim(messages: list[Message], max_messages: int) -> list[Message]:
    """Keep system + first user + last N messages. Drops middle when long."""
    if len(messages) <= max_messages:
        return messages
    head = messages[:2]  # system + first user
    tail = messages[-(max_messages - 2):]
    return head + tail
