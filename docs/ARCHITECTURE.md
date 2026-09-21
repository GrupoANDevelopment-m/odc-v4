# Architecture

A one-page tour. Read this if you want to extend the agent.

## Layering

```
┌──────────────────────────────────────────────────────────────┐
│ cli.py        typer commands: run, repl, doctor, memory     │
├──────────────────────────────────────────────────────────────┤
│ agent.py     Agent — wires provider, tools, skills, memory  │
├──────────────────────────────────────────────────────────────┤
│ loop.py      Loop — the think-act-verify closed loop         │
├────────────┬─────────────────┬───────────────────────────────┤
│ llm/        │ tools/          │ skills/                       │
│ provider.py │ base.py, web.py │ loader.py, builtin.py         │
│             │ file.py, etc.   │                               │
├─────────────┴─────────────────┴──────────────────────────────┤
│ memory/store.py        governance/auth.py                    │
│ observability/logs.py  config.py                             │
└──────────────────────────────────────────────────────────────┘
```

## Data flow for one task

```
user ──> cli.run / Agent.run
            │
            ├─> load config + memory + tools + skills
            │
            └─> Loop.run(task)
                 │
                 ├─> for each turn:
                 │     provider.chat(messages, tool_specs)
                 │     │
                 │     ├─ no tool calls → break
                 │     │
                 │     └─ for each tool call:
                 │           confirm? ─no──> return error
                 │           tool.run(**args) ─> ToolResult
                 │           append tool message
                 │           update verify counter
                 │           if cap → hand back
                 │
                 └─> final report (forced if model didn't speak)
                      │
                      └─> save task memory
                          return LoopResult → AgentRun
```

## Extending

### Add a tool

```python
# odc/tools/my_tool.py
from odc.tools.base import tool

@tool(
    name="my.do_thing",
    description="what it does, in plain English",
    parameters={"type": "object", "properties": {...}, "required": [...]},
    side_effect=False,
    requires_confirm=False,
)
async def do_thing(arg: str) -> str:
    return f"got {arg}"
```

Then register it in `odc/tools/__init__.py:build_default_registry()`.

### Add a skill

```bash
mkdir -p skills/postgres-debug
cat > skills/postgres-debug/SKILL.md <<'EOF'
---
name: postgres-debug
description: How to debug a slow query on the prod replica.
triggers:
  - slow query
  - postgres
  - replica
---

# Postgres Debug

1. ...
EOF
```

The agent discovers this at startup and adds it to the loaded skills
list. When the task matches a trigger, the skill body is injected
into the system prompt.

### Add an LLM provider

```python
# odc/llm/my_provider.py
from odc.llm.provider import Provider, Completion, Message, ToolSpec

class MyProvider(Provider):
    name = "my"
    async def chat(self, messages, tools=None, **kw) -> Completion:
        # call the API
        return Completion(text=..., tool_calls=...)
```

Register in `odc/llm/provider.py:_REGISTRY`.

## Why each choice

- **SQLite + FTS5 for memory** — no install footprint, deterministic,
  grep-able. We can add embeddings later without changing the API.
- **Markdown skills, not code plugins** — easier to author, easier to
  review, easier to delete. The cost is no programmatic behavior
  from skills, but the loop already does that.
- **Closed loop in one file (`loop.py`)** — small surface, easy to
  read end-to-end, easy to test. Anything that sounds like
  philosophy lives in skills.
- **Side-effect confirmation at the tool level** — the tool knows if
  it needs confirmation; the loop just passes through the
  `confirm=True` flag. No central policy table to keep in sync.
- **Three providers, one interface** — swapping is a config change,
  not a rewrite.

## What's not in v4 (and how to add it)

| Feature | Where to add |
|---|---|
| Vector memory | new `memory/vector.py` with the same `save/search/recent` API |
| Browser automation | new `tools/browser.py` wrapping `browser-use` |
| YouTube / RSS / GitHub | new `tools/reach.py` wrapping `agent-reach` channels |
| Multi-agent (federation) | new `federation/` package, shared volume, CRDT for state |
| Observability (OTEL) | swap `observability/logs.py` for OTEL exporters |
| Cost budgets | add to `loop.py:_run_once` — already counts `usage` |
