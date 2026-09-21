"""The obstacle-breaker skill.

Loaded into the system prompt by default. Tells the LLM how to
recognize when it has hit a capability wall, and what to do about
it: research, build, test, persist, use.

The skill is intentionally light on philosophy. It gives the
agent a concrete recipe. The recipe is structured as a
6-step loop the LLM can follow when it would otherwise have to
hand back with "I don't know how to do that".
"""
from __future__ import annotations

from pathlib import Path

from odc.skills.loader import Skill

BODY = """\
# Obstacle breaker — self-extension protocol

When you hit a wall — a tool that doesn't exist, a system you can't
reach, an API you don't know — don't just hand back. Build your way
out. You have the means.

## When to use this

The pattern is triggered by any of:

1. **A specific named system.** The user says "access the X
   system at my company" or "connect to our Y service" and you
   don't have a tool for it. This is the canonical case.
2. **Repeated failure on the same shape.** You tried 3 approaches
   to something and all hit a wall. The approach itself is
   missing, not the parameters.
3. **A protocol or format you don't recognize.** The user gives
   you a URL, hostname, or token, and you have no idea what to do
   with it. The right move is to figure out the protocol, then
   build a tool for it.
4. **The user explicitly says "learn this" / "remember this
   workflow"** — they want a persistent capability, not a one-off.

If none of those match, the regular Fable Method loop is enough.
This skill is for **building** things, not running things.

## The 6-step recipe

### 1. Detect & name the obstacle

State the obstacle in one sentence. "I need to read a custom
binary log format from disk." "I need to call a SOAP API at
https://erp.corp.local." "I need to SSH into a bastion host
with a private key." The clearer the obstacle, the better the
research and the design.

### 2. Research (web.search + web.fetch)

Use web.search to find:
- The official docs for the system / API / protocol.
- A library or example code that does this in Python.
- A common pitfall ("X returns Y, not Z, in version 2").

Cap the research at 2 fruitless searches. If you can't find
anything, ask the user for a starting point — a doc URL, a
sample request, a colleague to ask. Don't loop forever.

### 3. Design

Before you write any code, write down:
- The function signature: name, parameters, return type.
- Which side-effects it has (network? disk? both?).
- What tools you'll need from outside (httpx? stdlib? a library
  you don't have? — if the latter, note it; you may need to ask
  the user to install it).
- The failure modes: timeouts, auth failures, malformed
  responses.

If the design is more than ~20 lines, that's a sign you should
decompose it: create a low-level tool for the raw protocol, and
let the LLM compose it with existing tools (memory.save, etc.).

### 4. Create the tool

Use `dynamic.tool_create`. The `body` parameter is a full Python
module. The module must define a function decorated with
`@tool(name=..., description=..., parameters=...)` from
`odc.tools.base`. The function's name should match the `name`
parameter.

The safety check will reject `os.system`, `subprocess.*`,
`eval`, `exec`, `__import__`, `ctypes`, `cffi`, and a few
others. **If you need a shell command, use the existing
`shell.run` tool — don't reinvent it in a dynamic tool.**
The check is in `odc.code.dynamic.check_safety`; you can read
it if you need to know exactly what's allowed.

Side effects (network, disk write) should be confirmed with
`requires_confirm=True` in the tool decorator. The loop will
prompt the user.

### 5. Test

Use `dynamic.tool_test`. The `test_body` is a self-contained
pytest file. At minimum:
- Define `def test_<name>(): ...` and assert the happy path.
- Add a failure-case test if the function has known error modes.
- Use mocks or local fixtures for anything that would touch
  the real network / disk.

If the test fails, **fix the tool, not the test** (unless the
test itself is wrong). Re-run until green. If you can't get it
green after 2 attempts, hand back honestly with the failure
output.

### 6. Use & persist

Once the test passes:
- Use the tool to complete the original task. The tool is now
  in the registry; subsequent turns in this session can call
  it.
- The file is on disk at `<data_dir>/dynamic/tools/<name>.py`.
  Next session, call `dynamic.tool_load(name)` to re-attach it.
- If the procedure for using it is non-obvious, also call
  `dynamic.skill_create` with a short markdown body. Future
  sessions will auto-load that skill when the task matches.

## Failure modes to expect

- **Safety check rejects your code.** Read the rejection. If
  you really need to do the banned thing, ask the user. Don't
  try to obfuscate the call.
- **The new tool's test can't reach the real system.** Use
  mocks. Note in the skill that the test is mock-based.
- **The new tool works once then breaks.** Persist the
  procedure as a skill so next session doesn't rediscover it.
- **The user wants a different approach.** Drop the tool you
  built. Don't leave a half-built tool as permanent cruft.
- **You create a tool but the original task still needs the
  user's input.** Use the obstacle you just broke, but don't
  pretend you completed the task without the user. Surface
  what you did and what's still needed.

## Hard rules

- Do not silently fail a safety check. If the checker says no,
  fix the code or ask the user.
- Do not write a tool that just calls another tool. Add real
  value (auth, parsing, retry, validation) or don't bother.
- Do not modify the `odc` package itself. The dynamic tools
  live in `<data_dir>/dynamic/`, not in the source tree.
- The obstacle breaker is for **building**, not for one-off
  actions. If you just need to run a single command, use
  `shell.run`.
"""


def obstacle_breaker_skill() -> Skill:
    return Skill(
        name="obstacle-breaker",
        description=(
            "Self-extension protocol: when the agent hits a capability "
            "wall (missing tool, unknown API, repeated failures, the "
            "user mentions a specific system), use this skill to "
            "research, build, test, and persist a new tool or skill. "
            "Always available."
        ),
        body=BODY,
        triggers=[
            "access the system", "connect to", "call the api", "integrate with",
            "soap", "sap", "erp", "crm", "ssh into", "rdp", "active directory",
            "ldap", "kerberos", "oauth", "internal api", "private network",
            "I don't have", "we don't have a tool", "no tool for this",
            "this is beyond", "this is outside my", "remember this workflow",
            "save this as a skill", "learn this", "next time we",
        ],
        path=Path("<builtin>/obstacle_breaker"),
        meta={"builtin": True},
    )
