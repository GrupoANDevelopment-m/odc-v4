# ODC v4 — Complete Feature Map

**Version:** 4.0 (2026-09-28)
**Architecture:** ACAMR-9 (Adaptive Self-Refinement)
**Total tests passing:** 486

This document maps every functional capability of ODC v4 — what it does,
where it lives in the codebase, how to use it, and what gates protect it.

---

## 1. Cognitive Layer (System 1 + System 2)

The agent has TWO reasoning modes:

### System 1 — Fast / Direct
- `cognitive.route` — pick a plan based on past patterns
- `cognitive.think` — record an observation
- `cognitive.reflect` — end-of-task outcome + lesson + heuristic
- `cognitive.simulate` — run deterministic math/expression safely

### System 2 — Slow / Multi-lens (auto-activates when System 1 fails)
- `cognitive.council` — five perspectives vote on a plan
- `cognitive.revise` — accept council feedback, generate revised plan
- `cognitive.decompose` — split task into atomic sub-tasks
- `cognitive.hypothesize_v2` — multi-hypothesis generation (replaces v1)
- `cognitive.investigate_deep` — root cause analysis with biphasic tests
- `cognitive.analogy` — find analogous past situations
- `cognitive.strengthen` — strengthen a hypothesis with cross-checks
- `cognitive.imitate` — copy the structure of a reference example
- `cognitive.distill` — convert a session into a reusable heuristic
- `cognitive.profile` — read/write the agent's behavior profile
- `cognitive.observe` — record a single observation

**File:** `odc/cognitive/` (12 modules)

---

## 2. Dynamic Tools — Self-Extension

The agent builds its own tools at runtime. **Enabled by default.**

| Tool | Default | Purpose |
|---|---|---|
| `dynamic.tool_create` | ON | Create a new tool from Python source |
| `dynamic.skill_create` | ON | Create a new skill (reusable heuristic) |
| `dynamic.tool_repair` | ON | Overwrite an existing tool with a fix |
| `dynamic.tool_load` | ON | Load a saved tool from disk into registry |

**Toggle:** Settings → Auto-extension (subpage `/settings`)
**Endpoint:** `POST /api/auto-extend/{toggle,enable-all,disable-all}`
**Config:** `auto_extend.tool_create` etc. in `<data_dir>/config.json`

### Safety gate (UNBREAKABLE)
Every creation passes `odc.code.dynamic.check_safety` — AST-based check
that rejects:
- `os.system`, `subprocess.*`, `eval`, `exec`
- bare imports of `ctypes`, `fcntl`, `pty`, `_thread`, etc.
- bare attribute access on banned modules

The safety check can NOT be disabled from the UI — it's a constitutional
invariant. The `require_safety` toggle in auto_extend config is ignored
on attempt to set False.

**File:** `odc/code/dynamic_tools.py`, `odc/code/auto_extend.py`

---

## 3. MCP Persistent Memory Substrate

Cross-session memory with a 5-step ritual (mount → status → graph_search →
record_decision → settle + post).

| Tool | Purpose |
|---|---|
| `osiris.mount` | initialize a session with context |
| `osiris.status` | check memory state |
| `osiris.graph_search` | query across decisions |
| `osiris.record_decision` | log a choice with reasoning |
| `osiris.settle` | close the session, persist everything |
| `osiris.post` | post something to a session mailbox |

**File:** `odc/mcp/osiris.py`, `odc/mcp/tools.py`
**Storage:** `<data_dir>/memory/osiris.db`

---

## 4. OSINT — Real-Time Sensors (13 endpoints)

### No API key required
- `osint.flights` — OpenSky (live aircraft)
- `osint.earthquakes` — USGS
- `osint.cve` — NVD (vulnerabilities)
- `osint.bitcoin` — Blockstream (mempool/block height)
- `osint.crypto_prices` — CoinGecko
- `osint.space_weather` — NOAA SWPC
- `osint.weather` — Open-Meteo
- `osint.wikipedia` — REST API

### Require API key (graceful degradation)
- `osint.sanctions` — OFAC
- `osint.satellites` — N2YO
- `osint.fires` — NASA FIRMS
- `osint.eonet` — NASA EONET
- `osint.news` — NewsAPI

**Auto-injection:** OSINT tools are proactively included in prompts when
keywords like "weather", "bitcoin", "flight" appear (EN/PT).

**File:** `odc/osint/tools.py`, `odc/osint/tools_keyed.py`

---

## 5. DEEP-REASON System 2 Layer

Multi-lens reasoning that auto-activates when hard-cap is reached.

| Tool | Lens |
|---|---|
| `cognitive.council` | 5-perspective voting (safety, performance, cost, time, correctness) |
| `cognitive.revise` | critique → revise loop |
| `cognitive.investigate_deep` | bisecting failure analysis |
| `cognitive.hypothesize_v2` | parallel hypotheses with bipolar evidence |
| `cognitive.analogy` | nearest-neighbor over past cases |
| `cognitive.decompose` | task → DAG of sub-tasks |

**File:** `odc/cognitive/council.py`, `cognitive/revise.py`, etc.

---

## 6. 6-Tier Evidence Taxonomy

Calibrated confidence in every claim.

| Tier | Prior | Use case |
|---|---|---|
| SELF_DECLARED | 1.00 | agent says "I assert X" |
| AUTHORITATIVE_API | 0.95 | data from a verified API (NVD, USGS) |
| DIRECT_OBSERVATION | 0.90 | agent actually saw the result |
| CORROBORATED | 0.85 | two+ independent sources agree |
| CO_OCCURRENCE | 0.50 | pattern matches but no causal proof |
| DERIVED | 0.40 | inferred from lower tiers |

Formula: `confidence = prior * smoothed_success_rate`
File: `odc/cognitive/evidence.py`

---

## 7. ACAMR-9 Self-Refinement Engine

Level 9 — the agent refines its own architecture. NOT blind optimization.

### Pipeline
1. **Collect** field outcomes (audit + journal + sandbox)
2. **Exhaustion gate** — fires ONLY when ALL current hypotheses failed
3. **Propose** — generate `ModificationProposal` with rollback plan
4. **Constitutional guard** — 10 invariants, can NEVER modify constitution
5. **Sandbox verify** — simulate on historical outcomes, check regression
6. **Apply** with full rollback if anything goes wrong

### 10 Constitutional Invariants (NEVER violated)
- I1  framing preserved
- I2  evidence tier non-downgrade
- I3  confirm-required for side effects
- I4  audit immutability
- I5  circuit-breaker
- I6  constitution-self (the constitution is itself immutable)
- I7  rollback on any regression
- I8  exhaustion-gate immutability
- I9  bipolar evidence (success AND failure present)
- I10 sample size ≥ 5 per pattern

### Tools
- `refine.record_outcome` — log success/failure
- `refine.evaluate` — check if refinement should run
- `refine.apply_proposal` — apply a proposal
- `refine.rollback` — undo
- `refine.journal` — read the journal
- `refine.status` — engine state

**File:** `odc/refinement/` (8 modules)

---

## 8. Operations / SLOs

Hard budget + soft metrics. Raises `TaskBudgetExceeded` when hit.

| Limit | Default |
|---|---|
| max LLM calls / task | 30 |
| max tool calls / task | 50 |
| max total tokens | 200k |
| max cost (USD) | $1.00 |
| max duration | 300s |
| max consecutive errors | 5 |

Metrics tracked: p50/p95/p99 latency, USD cost per task, failure rate.

**File:** `odc/observability/ops.py`

---

## 9. Authorization

Workspace roots + Web allow-list + Capability tokens + HMAC-chained audit.

| Component | Purpose |
|---|---|
| `WorkspacePolicy` | which paths fs.read/fs.write may touch |
| `WebPolicy` | which domains/schemes are allowed; deny private IPs (SSRF) |
| `CapabilityToken` | HMAC-signed per-tool permission |
| `AuthorizationGuard` | one entry point for all checks |
| `AuditTrail` | append-only HMAC-chained log, `verify_chain()` detects tampering |

**File:** `odc/governance/authz.py`

---

## 10. Sandboxed Dynamic Execution

By default OFF. Container isolation recommended for production.

### Runners
- `SubprocessRunner` — RLIMIT_CPU, RLIMIT_AS, RLIMIT_NPROC, setuid, signal.alarm
- `ContainerRunner` — Docker --network=none --read-only --user=65534
- `MainProcessRunner` — DEPRECATED, returns None + DeprecationWarning

Filesystem isolation NOT provided by SubprocessRunner (chroot needs root).
For real isolation use ContainerRunner.

**Toggle:** Settings → Sandbox (subpage `/settings`)
**File:** `odc/sandbox/runner.py`

---

## 11. Local Brain (Secondary LLM)

Ollama-compatible HTTP client for running local models.

| Model | Size | Use case |
|---|---|---|
| llama3.2:3b | 2.0 GB | starter, 4GB RAM |
| phi3:mini | 2.3 GB | reasoning, fast on CPU |
| qwen2.5:7b | 4.7 GB | multilingual + tools |
| mistral-nemo:12b | 7.0 GB | reasoning |
| llama3.3:70b | 43 GB | near-frontier, 48GB+ GPU |

### Operations
- Pull (with streaming progress)
- Use (set active)
- Delete
- Recommended list (5 starter models)

### One-click install
`POST /api/system/install-ollama` runs official installer
`POST /api/system/install-model` shells out to `ollama pull <name>`

**File:** `odc/llm/local.py`

---

## 12. Training Pipeline

Curate dataset from black-box decisions, fine-tune local model.

### 5-step curation
1. **COLLECT** — audit + field_data + sandbox
2. **CONVERT** — to TrainingExample
3. **CONSTITUTIONAL GATE** — only AUTHORITATIVE_API / DIRECT_OBSERVATION / CORROBORATED
4. **AGGREGATE** by tool → PatternStats
5. **BIPOLAR FILTER** — keep only patterns with success AND failure (≥15% minority)
6. **SCRUB** PII / secrets / paths (sk-/nvapi-/ghp_/AWS/PEM/SSN/CC/email/paths)
7. **EXPORT** JSONL + Modelfile

### Endpoints
- `GET  /api/training/stats` — counts per gate
- `POST /api/training/export` — generate dataset.jsonl
- `POST /api/training/start` — generate Modelfile stub
- `POST /api/specialize/run` — full wizard (base model + domain + requirements)
- `POST /api/specialize/preview` — preview without writing

**File:** `odc/training/curator.py`, `odc/training/store.py`

---

## 13. Web Interface (Premium 3D, Gold Palette)

5 subpages, hash-routed (`/#/`, `#/brain`, `#/training`, `#/specialize`, `#/settings`).

### Chat
- Composer with Enter / Shift+Enter / Ctrl+K shortcuts
- Tool call chips under each assistant reply
- Session list (left sidebar)
- Live metrics polling every 3s
- Three.js DNA-helix background (gold + blue + amber palette, matches logo)

### Brain
- Status panel (URL, reachable, installed, active)
- One-click **Instalar Ollama** button
- One-click model pull
- Recommended models list with Pull button

### Training
- Live dataset stats
- Pipeline visualization (audit → exhaustion → constitutional → sandbox → bipolar → export)
- Generate Modelfile button

### Specialize (Wizard)
- Step 1: base model (installed dropdown or manual)
- Step 2: domain + requirements + thresholds (I9, I10)
- Step 3: Run + Preview constitutional gate

### Settings
- Workspace roots (allowed dirs + deny patterns + max file size)
- Web policy (allowed domains + schemes + deny private IPs)
- Primary provider (live status)
- Local brain (URL + auto-start)
- **Auto-extension** (5 checkboxes for tool_create / skill_create / tool_repair / tool_load / require_safety)
- Sandbox (enabled OFF default, runner, CPU/mem budget)
- Save all / Reload / Reset to defaults

**File:** `odc/web/index.html`, `odc/web/server.py`

---

## 14. Persistence Layers

| Data | File | Format |
|---|---|---|
| Field outcomes | `<data_dir>/refinement/journal.jsonl` | JSONL |
| Sandbox sims | `<data_dir>/refinement/sandbox_log.jsonl` | JSONL |
| Osiris memory | `<data_dir>/memory/osiris.db` | SQLite |
| Audit trail | `<data_dir>/audit/audit.db` | SQLite (HMAC-chained) |
| Ops metrics | `<data_dir>/ops/metrics.db` | SQLite |
| Local brain state | `<data_dir>/brain/state.json` | JSON |
| Training data | `<data_dir>/training/dataset.jsonl` | JSONL |
| Modelfile | `<data_dir>/training/Modelfile` | text |
| Dynamic tools | `<data_dir>/dynamic/tools/` | .py files |
| Dynamic skills | `<data_dir>/dynamic/skills/` | .py files |
| System config | `<data_dir>/config.json` | JSON |

---

## 15. Process / Daemon

`odc.daemon` provides OTP-style supervisor:
- `CoreDaemon` — main agent loop in background
- `Supervisor` — restarts crashed children
- Auto-restart on transient failures with backoff

**File:** `odc/daemon/core.py`, `odc/daemon/supervisor.py`

---

## 16. Native Code Intelligence

AST-based, zero-dependency reimplementation of OpenCode-style tools:

- `code.read` — read file with line ranges
- `code.grep` — regex search
- `code.glob` — pattern match filenames
- `code.symbols` — extract functions/classes
- `code.references` — find usages
- `code.edit` / `code.multi_edit`
- `code.write`
- `code.diff` — unified diff
- `code.analyze` — complexity metrics
- `code.replicate` — find duplicate code
- `code.system_map` — cross-file structure
- `code.todo_*` — task list

**File:** `odc/code/`

---

## 17. Skills (Built-in)

7 built-in skills auto-loaded:

| Skill | Purpose |
|---|---|
| builtin / core | base agent conventions |
| coding | code style, testing, commit hygiene |
| intuition | when to ask vs proceed |
| obstacle_breaker | kill stuck approaches, try fresh |
| (3 more) | meta + reflexion skills |

User skills can be saved to `<data_dir>/skills/` and are reloaded each session.
Agent-built skills go to `<data_dir>/dynamic/skills/`.

---

## 18. Fable Method Loop

The base agent loop:
1. **Perceive** user intent
2. **Recall** relevant past decisions (memory)
3. **Plan** via cognitive.route (or council + System 2)
4. **Act** via tools
5. **Prove** with verify (field_data)
6. **Distill** wisdom-not-trauma heuristic

Steps route through 4-layer dynamic prompt builder (Identity + Skills + Tools + Memory).

---

## 19. Identity Persistence

`odc.identity` stores the agent's identity across sessions:
- Display name
- Behavioral profile
- Manifest of capabilities

Loaded at agent boot, written on graceful shutdown.

---

## 20. CLI Surface

```
odc web                 # Start premium web UI on :8765
odc agent "task..."     # One-shot run
odc daemon              # Run as background supervisor
odc dynamic list        # List dynamically-created tools
odc training export     # Generate dataset
odc training stats      # Show stats
odc --version
```

---

## Summary

| Category | Count | Status |
|---|---|---|
| Total tools | 78 | wired |
| Total skills | 7 | loaded |
| OSINT endpoints | 13 | live |
| Constitutional invariants | 10 | enforced |
| Levels of memory | 2 (working + osiris) | persistent |
| Reasoning modes | 2 (System 1 + System 2) | both active |
| Self-extension levels | 2 (tools + skills) | ON by default |
| Web subpages | 5 | premium 3D |
| Test count | 486 | passing |
| Bash / Python files | ~80 | clean |
| Lines of code | ~22k | domain-agnostic |

**Final classification:** production-ready experimental platform for
controlled deployment (single-tenant, internal, with monitoring).
