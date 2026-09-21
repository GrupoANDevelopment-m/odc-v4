# Honest limitations of ODC v4

A v4 you can install and run in two minutes is great. It is not great
because it solves every problem. Here is what it does not do, written
down so you don't have to guess.

## What it is

- An LLM with hands (tools), a notebook (memory), a procedure (skills),
  and a budget (closed loop).
- Tested. The loop, the tool contract, the memory store, the skill
  loader, and the file tools have automated tests.
- Honest. It stops after 25 turns or 3 failed verifies and tells you
  so. It surfaces unverified claims as caveats. It does not fake rigor.

## What it is not

### Not a "conscious digital organism"

There is no qualia, no self-model, no feelings. The word "ODC" used
to mean that in the projects this one grew out of; v4 keeps the
acronym for file-name continuity and **renames** the concept to
"honest agent." When you read code, prefer "agent." When you read
docs that mention consciousness, treat as marketing.

### Not safe to run unattended

`shell.run` is allow-listed. The default safe set is small (`ls`,
`cat`, `grep`, etc.). You can extend it via `ODC_SHELL_ALLOWLIST`.
Even so, the shell runs as you. Don't point it at production.

### Not a search engine

`web.search` uses DuckDuckGo and `web.fetch` does a plain HTTP GET.
If a site requires JS rendering, logins, or has aggressive bot
detection, you'll get a 403 or empty HTML. For those cases, install
the `[browser]` extra (wraps `browser-use`) — or just use a
human.

### Not a vector store

Memory is SQLite + FTS5 (keyword + Porter stemmer). For 10k–100k
entries this is fast enough. For semantic recall ("things like
this") it is the wrong tool. Adding FAISS or Qdrant is a small,
deliberate change — the `MemoryStore` interface is the place.

### Not multi-tenant

There is one agent per process. The memory store is one SQLite file.
If you want N agents collaborating, run N processes with a shared
volume, or fork the design.

### Not proven in adversarial settings

The Fable Method is the *operating procedure* for the agent. The
author of the Fable Method measured it against traps (spec-vs-test
conflicts, fraudulent "work complete" reports, unauthorized deploys)
and reported the lifts in the original repo. v4 inherits the
procedure, not the eval. We have not rerun the eval here.

### Not a deployment platform

There is no built-in `odc deploy`. If you want the agent to deploy
something, write a `deploy` skill that calls your existing tool
(`kubectl`, `aws`, etc.) and put the auth gate around it.

### Not "let the agent fix itself"

`self-learn` captures golden paths as new skill files. It does not
edit its own code. Treat captured skills as a log of how the agent
solved hard things — review them before they accumulate.

## What will not improve without your help

- **The model.** Whatever you plug in is what you get. A weak model
  following the loop is more reliable than a strong model
  free-styling, but no loop makes a model have an insight it lacks.
- **The tools.** If the tool isn't there, the agent can't use it.
  Write a `@tool(...)` function and register it.
- **The skills.** The Fable Method, Judge, and Self-Learning are a
  starting point. Domain-specific skills (e.g. a `postgres-debug`
  skill) live with you, the user, in `./skills/<name>/SKILL.md`.
- **The memory.** Search is FTS5. You can save anything, but the
  retrieval will not surprise you. If you want semantic recall, add
  embeddings.

## What to do when it lies

It will, sometimes. The loop is honest about not knowing; the
*model* may not be. If a report looks too clean, run `odc repl` and
ask the agent to **judge** its own last run:

> use the judge skill on the last report

`judge` re-runs every claim and surfaces unverified ones. That's the
intended use — the loop is the writer, the judge is the auditor.

## Reporting issues

Open an issue with: the command, the `.env` (with keys redacted), and
the last 50 lines of `<data_dir>/logs/odc.jsonl`. That is enough to
reproduce.
