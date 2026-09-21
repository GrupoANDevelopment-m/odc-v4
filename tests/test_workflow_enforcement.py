"""Test that the cognitive workflow is enforced by the loop, not by the LLM.

These tests verify that even if the LLM ignores the workflow (which it
will, on a small model), the system still routes the task through
cognitive.route, recalls past lessons, and nudges periodically.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from odc.loop import Loop
from odc.tools.base import ToolRegistry


def _make_loop(tmp_path: Path, max_turns: int = 4) -> tuple[Loop, list[dict]]:
    """Build a Loop with a mocked provider that records every chat call."""
    from odc.config import load_config
    cfg = load_config()
    cfg.data_dir = tmp_path / "odc"
    cfg.max_loop_turns = max_turns
    cfg.log_level = 20  # INFO

    calls: list[dict] = []

    class FakeProvider:
        async def chat(self, messages, tools=None):
            calls.append({
                "messages": list(messages),
                "n_messages": len(messages),
                "roles": [m.role for m in messages],
            })
            return MagicMock(
                text="done",
                tool_calls=[],
                usage={"prompt_tokens": 0, "completion_tokens": 0},
            )

    provider = FakeProvider()
    tools = ToolRegistry()
    loop = Loop(config=cfg, provider=provider, tools=tools)
    return loop, calls


@pytest.mark.asyncio
async def test_pre_task_brief_injects_system_message(tmp_path: Path):
    """The loop MUST inject the cognitive.route result as a system
    message before the user task, regardless of what the LLM does."""
    loop, calls = _make_loop(tmp_path)
    await loop.run("compute sha1 of .env")

    assert len(calls) == 1
    msgs = calls[0]["messages"]
    briefs = [
        m for m in msgs
        if m.role == "system" and "SYSTEM-GENERATED BRIEF" in m.content
    ]
    assert len(briefs) == 1, f"expected 1 brief, got {len(briefs)}: {[m.role for m in msgs]}"
    user_idx = next(i for i, m in enumerate(msgs) if m.role == "user")
    brief_idx = msgs.index(briefs[0])
    assert brief_idx < user_idx, "brief must be before the user task"


@pytest.mark.asyncio
async def test_brief_includes_route_recommendation(tmp_path: Path):
    """The brief must include the cognitive.route output."""
    loop, calls = _make_loop(tmp_path)
    await loop.run("encode hello world in rot13")
    msgs = calls[0]["messages"]
    brief = next(m for m in msgs if m.role == "system" and "SYSTEM-GENERATED BRIEF" in m.content)
    assert "cognitive.route()" in brief.content
    assert "recommendation" in brief.content.lower()


@pytest.mark.asyncio
async def test_periodic_nudge_actually_fires(tmp_path: Path):
    """Run 6 turns. Verify a system message containing 'SYSTEM NUDGE' is
    present in the 5th or 6th turn's messages."""
    from odc.config import load_config
    cfg = load_config()
    cfg.data_dir = tmp_path / "odc"
    cfg.max_loop_turns = 6
    cfg.log_level = 20

    messages_history: list[list] = []

    class FakeProvider:
        def __init__(self):
            self.turn = 0

        async def chat(self, messages, tools=None):
            self.turn += 1
            messages_history.append(
                [{"role": m.role, "content": m.content} for m in messages]
            )
            # Keep the loop running by always returning a tool call.
            # The fake tool just returns ok and the loop will call again.
            fake_tc = {
                "id": f"call_{self.turn}",
                "name": "fake.tool",
                "arguments": "{}",
            }
            return MagicMock(text="calling", tool_calls=[fake_tc], usage={})

    provider = FakeProvider()
    tools = ToolRegistry()
    from odc.tools.base import tool

    @tool(name="fake.tool", description="x", parameters={"type": "object", "properties": {}})
    async def fake_tool():
        return {"ok": True}

    tools.register(fake_tool)
    loop = Loop(config=cfg, provider=provider, tools=tools)
    await loop.run("anything")

    assert len(messages_history) >= 5, f"expected at least 5 turns, got {len(messages_history)}"
    nudge_found = False
    for snap in messages_history[3:]:  # from turn 4 onward
        for m in snap:
            if m["role"] == "system" and "SYSTEM NUDGE" in m["content"]:
                nudge_found = True
                break
        if nudge_found:
            break
    assert nudge_found, (
        f"expected a nudge by turn 5. got {len(messages_history)} turns. "
        f"Last turn roles: {[m['role'] for m in messages_history[-1]] if messages_history else 'none'}"
    )


@pytest.mark.asyncio
async def test_post_task_reflect_runs(tmp_path: Path):
    """After the loop ends, cognitive.reflect must be called to update
    the profile."""
    from odc.config import load_config
    from odc.cognitive.profile import CognitiveProfile

    cfg = load_config()
    cfg.data_dir = tmp_path / "odc"
    cfg.max_loop_turns = 2
    cfg.log_level = 20

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                # First turn: ask to call a tool
                fake_tc = {
                    "id": "call_1",
                    "name": "fake.tool",
                    "arguments": "{}",
                }
                return MagicMock(text="calling tool", tool_calls=[fake_tc], usage={})
            return MagicMock(text="done", tool_calls=[], usage={})

    provider = FakeProvider()
    tools = ToolRegistry()
    from odc.tools.base import tool

    @tool(name="fake.tool", description="x", parameters={"type": "object", "properties": {}})
    async def fake_tool():
        return {"ok": True}

    tools.register(fake_tool)
    loop = Loop(config=cfg, provider=provider, tools=tools)
    await loop.run("do a thing")

    # The profile must have been created and have at least 1 task
    profile_path = cfg.data_dir / "cognitive" / "profile.json"
    assert profile_path.exists(), f"profile not created at {profile_path}"
    prof = CognitiveProfile(profile_path)
    assert prof.data["meta_metrics"]["total_tasks"] >= 1
    # The tool should be tracked
    assert "fake.tool" in prof.data["tool_success"]


@pytest.mark.asyncio
async def test_brief_does_not_replace_user_task(tmp_path: Path):
    """The user task must still be present after the brief is injected.
    Sanity check that we don't drop messages."""
    loop, calls = _make_loop(tmp_path)
    await loop.run("this is the actual user task that must survive")
    msgs = calls[0]["messages"]
    user_msgs = [m for m in msgs if m.role == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0].content == "this is the actual user task that must survive"
