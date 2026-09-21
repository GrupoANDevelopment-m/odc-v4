"""Tool base + registry.

A tool is just a JSON-Schema spec + a callable. The agent reads the spec
to teach the LLM what the tool does, the loop calls run() with validated
arguments, the result goes back to the model.

Two safety rails by default:
- run() returns ToolResult(success, output, error) — never raises to the agent.
- side-effect tools (shell, write, browser) require explicit `confirm=True`
  unless allowlisted by config.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from odc.llm import ToolSpec
from odc.observability import get_logger, log_event

log = get_logger("odc.tools")


def _coerce_args(signature: inspect.Signature, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Coerce string args to declared types when possible.

    LLMs often serialize numbers as strings (especially with parallel
    tool calls or non-strict JSON parsers). This is a best-effort
    rescue: only the types declared in the signature get coerced, and
    only when the coercion is unambiguous.
    """
    out = dict(kwargs)
    for name, param in signature.parameters.items():
        if name not in out:
            continue
        value = out[name]
        if value is None:
            continue
        anno = param.annotation
        # Annotation is often a string under `from __future__ import annotations`.
        if isinstance(anno, str):
            anno_str = anno
        else:
            anno_str = getattr(anno, "__name__", "") if anno else ""
        try:
            if anno_str in ("int", "float") and isinstance(value, str):
                out[name] = int(value) if "." not in value else float(value)
            elif anno_str == "bool" and isinstance(value, str):
                out[name] = value.strip().lower() in ("1", "true", "yes", "on")
            elif anno_str in ("list", "List") and isinstance(value, str):
                # Best-effort: leave as-is. JSON parse failure is reported
                # by the tool itself, not here.
                pass
        except (ValueError, TypeError):
            # Leave as-is; the tool will surface a clearer error.
            pass
    return out


@dataclass
class ToolResult:
    """Standardized result returned by every tool."""

    success: bool
    output: Any = None
    error: str | None = None
    duration_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> str:
        """Format for the LLM. Keeps it compact and grep-able."""
        if self.success:
            return _truncate(repr(self.output))
        return f"ERROR: {self.error}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "meta": self.meta,
        }


def _truncate(s: str, limit: int = 8000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


class Tool:
    """Base class. Subclass and implement run(); the registry handles the rest."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    requires_confirm: bool = False
    side_effect: bool = False  # set True for shell/write/delete

    async def run(self, **kwargs) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    async def __call__(self, *, confirm: bool = False, **kwargs) -> ToolResult:
        if self.requires_confirm and not confirm:
            return ToolResult(
                success=False,
                error=(
                    f"Tool '{self.name}' requires explicit confirmation. "
                    f"Pass confirm=True from the agent after asking the user."
                ),
            )
        # Coerce stringly-typed args to declared types where unambiguous.
        # This is a defensive measure: some LLMs serialize numbers as strings.
        try:
            sig = inspect.signature(self.run)
            kwargs = _coerce_args(sig, kwargs)
        except (TypeError, ValueError):
            pass
        t0 = time.perf_counter()
        # Resilience wrapper: retry + circuit breaker + timeout. The
        # tool body runs inside `call_with_resilience`; on circuit open,
        # we return a structured error so the LLM can adapt.
        from odc.resilience import call_with_resilience, get_breaker

        async def _call() -> Any:
            if self.side_effect:
                log_event(
                    log, 20, "tool_call", tool=self.name, side_effect=True, args=_safe_args(kwargs)
                )
            return await self.run(**kwargs)

        out, err = await call_with_resilience(self.name, _call)
        dur = int((time.perf_counter() - t0) * 1000)
        if err is not None:
            log_event(log, 30, "tool_error", tool=self.name, error=str(err))
            return ToolResult(
                success=False,
                error=str(err),
                duration_ms=dur,
                meta={"circuit_state": get_breaker(self.name).state.value},
            )
        return ToolResult(success=True, output=out, duration_ms=dur)


def _safe_args(d: dict[str, Any]) -> dict[str, Any]:
    """Strip large / secret-looking fields from logs."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        s = str(v)
        if any(token in k.lower() for token in ("key", "token", "password", "secret")):
            out[k] = "***"
        elif len(s) > 200:
            out[k] = s[:200] + "..."
        else:
            out[k] = v
    return out


class ToolRegistry:
    """Holds the available tools, exposes them as ToolSpec for the LLM and
    a name→Tool map for execution.

    >>> r = ToolRegistry()
    >>> r.register(MyTool())
    >>> [t.name for t in r.specs()]
    ['my_tool']
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError(f"Tool {tool!r} has no name")
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove a tool by name. Used by dynamic.tool_repair."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}. Known: {sorted(self._tools)}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    async def run(self, tool_name: str, *, confirm: bool = False, **kwargs) -> ToolResult:
        try:
            tool = self.get(tool_name)
        except KeyError as e:
            return ToolResult(success=False, error=str(e))
        return await tool(confirm=confirm, **kwargs)


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    requires_confirm: bool = False,
    side_effect: bool = False,
) -> Callable[[Callable], Tool]:
    """Decorator: turn a plain function into a registered Tool.

    Example:
        @tool(name="add", description="add two ints", parameters={...})
        async def add(a: int, b: int) -> int:
            return a + b
    """

    def wrap(fn: Callable) -> Tool:
        if not inspect.iscoroutinefunction(fn):
            # Wrap sync functions in asyncio.to_thread
            async def _async(**kw):
                return await asyncio.to_thread(fn, **kw)

            _async.__name__ = fn.__name__
            run_fn = _async
        else:
            run_fn = fn

        tool_obj = Tool()
        tool_obj.name = name
        tool_obj.description = description
        tool_obj.parameters = parameters
        tool_obj.requires_confirm = requires_confirm
        tool_obj.side_effect = side_effect
        tool_obj.run = run_fn  # type: ignore[assignment]
        functools.update_wrapper(tool_obj, fn, updated=())
        return tool_obj

    return wrap
