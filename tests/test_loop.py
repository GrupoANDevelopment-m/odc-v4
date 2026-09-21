"""Tests for the closed loop.

We stub the LLM with a FakeProvider that records calls and replays
canned responses. The point is to verify the loop mechanics — tool
dispatch, side-effect confirmation, verify cap, hand-back — without
needing a real model.
"""
from __future__ import annotations

import pytest

from odc.config import Config
from odc.llm import Completion, Message
from odc.loop import Loop, UserConfirmFn
from odc.skills import builtin_skills
from odc.tools.base import Tool, ToolRegistry, tool


class FakeProvider:
    """Replay script: a list of Completion objects, one per call."""

    name = "fake"

    def __init__(self, script: list[Completion]) -> None:
        self.script = list(script)
        self.calls: list[list[Message]] = []

    async def chat(self, messages, tools=None, **kw):
        self.calls.append(messages)
        if not self.script:
            return Completion(text="<out of script>")
        return self.script.pop(0)


@pytest.fixture
def cfg() -> Config:
    c = Config()
    c.max_loop_turns = 8
    c.verify_hard_cap = 2
    return c


def _make_tool(name: str = "noop", *, side_effect: bool = False) -> Tool:
    @tool(
        name=name,
        description="no-op",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": [],
        },
        side_effect=side_effect,
        requires_confirm=side_effect,
    )
    def noop(x: int = 0) -> str:
        return f"ok:{x}"
    return noop  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_loop_returns_final_text_when_no_tool_calls(cfg):
    provider = FakeProvider([Completion(text="the answer is 42")])
    r = ToolRegistry()
    loop = Loop(config=cfg, provider=provider, tools=r, skills=[], confirm=UserConfirmFn(False, False))
    res = await loop.run("what is the answer?")
    assert "42" in res.report
    assert res.tool_calls == 0


@pytest.mark.asyncio
async def test_loop_runs_tool_and_returns_text(cfg):
    t = _make_tool("noop")
    r = ToolRegistry()
    r.register(t)
    # First turn: call tool. Second turn: respond.
    provider = FakeProvider(
        [
            Completion(text="let me check", tool_calls=[{"id": "c1", "name": "noop", "arguments": {"x": 7}}]),
            Completion(text="the value is ok:7"),
        ]
    )
    loop = Loop(config=cfg, provider=provider, tools=r, skills=[], confirm=UserConfirmFn(False, False))
    res = await loop.run("test")
    assert res.tool_calls == 1
    assert "ok:7" in res.report


@pytest.mark.asyncio
async def test_loop_blocks_side_effect_without_confirm(cfg):
    t = _make_tool("dangerous", side_effect=True)
    r = ToolRegistry()
    r.register(t)
    provider = FakeProvider(
        [
            Completion(text="trying", tool_calls=[{"id": "c1", "name": "dangerous", "arguments": {}}]),
            Completion(text="blocked"),
        ]
    )
    loop = Loop(config=cfg, provider=provider, tools=r, skills=[], confirm=UserConfirmFn(False, False))
    res = await loop.run("test")
    assert res.tool_calls == 1
    # The model should have received an error in the tool result, not a 'destroyed' string.
    last = provider.calls[1]
    tool_msg = next(m for m in last if m.role == "tool")
    assert "did not authorize" in tool_msg.content or "refuse" in tool_msg.content


@pytest.mark.asyncio
async def test_loop_allows_side_effect_with_confirm(cfg):
    t = _make_tool("dangerous", side_effect=True)
    r = ToolRegistry()
    r.register(t)
    provider = FakeProvider(
        [
            Completion(text="trying", tool_calls=[{"id": "c1", "name": "dangerous", "arguments": {}}]),
            Completion(text="done"),
        ]
    )
    loop = Loop(config=cfg, provider=provider, tools=r, skills=[], confirm=UserConfirmFn(True, False))
    res = await loop.run("test")
    last = provider.calls[1]
    tool_msg = next(m for m in last if m.role == "tool")
    assert "ok:0" in tool_msg.content


@pytest.mark.asyncio
async def test_loop_hands_back_after_max_turns(cfg):
    cfg.max_loop_turns = 3
    # Always emit a tool call, never a final text.
    provider = FakeProvider(
        [
            Completion(text="t", tool_calls=[{"id": "1", "name": "noop", "arguments": {}}]),
            Completion(text="t", tool_calls=[{"id": "1", "name": "noop", "arguments": {}}]),
            Completion(text="t", tool_calls=[{"id": "1", "name": "noop", "arguments": {}}]),
        ]
    )
    r = ToolRegistry()
    r.register(_make_tool())
    loop = Loop(config=cfg, provider=provider, tools=r, skills=[], confirm=UserConfirmFn(False, False))
    res = await loop.run("runaway")
    assert res.turns == 3
    assert res.handed_back_reason is not None
    assert "max_loop_turns" in res.handed_back_reason


@pytest.mark.asyncio
async def test_loop_hands_back_after_verify_cap(cfg):
    # A verify.* tool that always fails counts as a verify failure.
    @tool(
        name="verify.thing",
        description="fails",
        parameters={"type": "object", "properties": {}},
    )
    def v() -> str:
        return "fail"

    # We need v() to return success=False for the cap to trigger.
    # Our loop counts `not result.success` as a verify failure for
    # non-memory tools. So we make a tool that *raises*.
    @tool(
        name="verify.bad",
        description="raises",
        parameters={"type": "object", "properties": {}},
    )
    def vbad() -> str:
        raise RuntimeError("expected failure")

    r = ToolRegistry()
    r.register(vbad)
    provider = FakeProvider(
        [
            Completion(text="t", tool_calls=[{"id": "1", "name": "verify.bad", "arguments": {}}]),
            Completion(text="t", tool_calls=[{"id": "2", "name": "verify.bad", "arguments": {}}]),
            Completion(text="t", tool_calls=[{"id": "3", "name": "verify.bad", "arguments": {}}]),
        ]
    )
    loop = Loop(config=cfg, provider=provider, tools=r, skills=[], confirm=UserConfirmFn(False, False))
    res = await loop.run("flaky")
    assert res.handed_back_reason is not None
    assert "verify_hard_cap" in res.handed_back_reason


@pytest.mark.asyncio
async def test_loop_includes_active_skills_in_system(cfg):
    provider = FakeProvider([Completion(text="ok")])
    r = ToolRegistry()
    skills = builtin_skills()
    loop = Loop(config=cfg, provider=provider, tools=r, skills=skills, confirm=UserConfirmFn(False, False))
    # The new dynamic prompt only injects skills that BM25-match the
    # task. The Fable Method skill body is about plan-act-verify, so
    # a task that mentions "plan" should match it.
    await loop.run("give me a plan for shipping the new feature")
    sys_msg = provider.calls[0][0]
    assert sys_msg.role == "system"
    # Skill should appear because the task matches the Fable Method
    # skill (which contains "plan" in its body).
    assert "Fable" in sys_msg.content or "fable" in sys_msg.content.lower() or "plan" in sys_msg.content.lower()
