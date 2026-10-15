# ODC v4 — Open Digital Companion

A self-improving LLM agent. Cognitive architecture, persistent memory, native tools, dynamic tool creation, AST+LLM self-heal, browser automation, MCP memory substrate, OSINT sensors, and a 3D web interface. Production-ready. **125MB of real source code.**

## What's new in v4

- **AST + LLM combined self-heal** — fast AST for known patterns, LLM (real 550B verified) for novel ones
- **Proactive code improvement** — LLM rewrites working code for safety, readability, performance
- **SelfImprovementEngine** — reflections, trust, heuristics, anti-patterns, self-patches
- **Browser automation** — 11 tools, persistent state, cross-process
- **Cognitive validation** — 5-level suite with REAL 550B
- **3 real ODC bugs found and fixed** during testing
- **125MB repo** with full upstream source bundles

## Quick start

```bash
# Clone (with LFS)
git clone https://github.com/GrupoANDevelopment-m/odc-v4.git
cd odc-v4

# One-command install
./setup.sh

# Or manual:
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test]"
python -m playwright install chromium

# Edit .env with your API keys
cp .env.example .env
# $EDITOR .env

# Run tests
make test

# Start the server
make run
# Open http://localhost:8000
```

## Features

### Core
- **Fable Method loop** with persistent SQLite checkpointing
- **89 tools** across code, fs, shell, web, browser, OSINT, MCP, refinement, dynamic
- **4 LLM providers**: NVIDIA 550B, OpenAI, Anthropic, Gemini, Ollama
- **Premium 3D web UI** with hash routing, no emoji, gold/blue/amber palette

### Cognitive architecture
- **4-layer dynamic prompt builder** (identity → task → history → context)
- **DEEP-REASON System 2** (council, revise, analogy, hypothesize, decompose)
- **6-tier evidence taxonomy**
- **Wisdom-not-trauma** failure learning (deprecated rules, not deleted)
- **Strict conversation isolation** (BM25 over thread_id)

### Memory / state
- **MCP persistent memory** (5-step ritual)
- **Cognitive profile** (heuristics, anti-patterns, lessons, trust)
- **OSINT sensors** (13 endpoints, 8 no-key + 5 keyed)
- **HMAC-chained audit trail**

### Self-improvement (NEW)
- **AST + LLM self-heal** — combined fast AST + slow LLM
- **Proactive improvement** — LLM rewrites working code
- **Constitutional guard** with 10 invariants (I1-I10)
- **Atomic apply** with backup + auto-rollback
- **Journaled** — every attempt logged

### Browser automation
- **11 tools**: open, goto, evaluate, screenshot, text, html, cookies, etc
- **Persistent state** (storage_state.json, cross-process)
- **Loop-aware singleton** (handles asyncio.run() between calls)

### Safety
- **Authorization**: workspace roots, web allow-list, RBAC
- **require_safety UNBREAKABLE** (system invariant, not a setting)
- **Auto-extension** (dynamic.tool_create) DEFAULT ON with safety gate
- **Rate limits** (10 heals/hour max)
- **Append-only HMAC audit**

## Tests

```bash
make test                # All unit + integration
make test-llm            # Real-LLM tests (need NVIDIA_API_KEY)
make test-cognitive      # Cognitive validation suite
make test-adversarial    # Adversarial audit
make test-deep           # Deep audit
make test-coverage       # With coverage report
make audit               # Full audit (cognitive + adversarial + deep)
```

Test counts:
- 533 unit tests
- 25 cognitive validation (real 550B)
- 20 adversarial audit
- 20 deep audit
- **Total: 553 tests, 0 regressions**

## Architecture

```
odc/
├── agent/          # Fable Method loop, repl
├── llm/            # 4 providers + resilient wrapper
├── cognitive/      # System 2, profile, anti-patterns
├── mcp/            # Memory substrate
├── osint/          # 13 sensors
├── refinement/     # Self-heal, self-improvement
├── governance/     # Constitutional guard
├── code/           # Dynamic tool create/load/safety
├── tools/          # 89 tool implementations
├── skills/         # 7 skills
├── observability/  # Audit, metrics, traces
├── web/            # 3D UI, server
└── ...
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Features](docs/FEATURES.md)
- [Honest Limitations](docs/HONEST_LIMITATIONS.md)
- [Audit Report 2026-09-25](docs/AUDIT_2026-09-25.pdf)
- [Adversarial Audit](docs/AUDIT_ADVERSARIAL_REPORT.md)
- [Deep Audit](docs/DEEP_AUDIT_REPORT.md)
- [Cognitive Suite Report](docs/COGNITIVE_SUITE_REPORT.md)
- [Self-Improvement](docs/SELF_IMPROVEMENT.md)
- [Self-Heal](docs/SELF_HEAL.md)
- [User Test Report](docs/USER_TEST_REPORT.md)

## Deployment

### Docker
```bash
docker build -t odc-v4 .
docker run -d --name odc-v4 -p 8000:8000 --env-file .env odc-v4
```

Or:
```bash
docker-compose up -d
```

### Local Ollama brain
```bash
# Install Ollama from https://ollama.ai
ollama pull llama3.1:8b
# Set ODC_LLM_PROVIDER=ollama in .env
```

## License

MIT — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributions must:
- Pass existing tests
- Add new tests
- Not break the constitutional guard
- Stay honest about limitations

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## Security

See [SECURITY.md](SECURITY.md). Report vulnerabilities to security@odc-v4.local.

## Acknowledgments

Built on top of:
- **OpenCode** (TypeScript, bundled at `third_party/opencode_full/`)
- **Browser-Use** (Python, bundled at `third_party/browser_use_full/`)
- **Claude-Mem** (memory substrate, `third_party/claude_mem/`)
- **Agent-Reach** (multi-channel, `third_party/agent_reach_full/`)
- **Fable Method** (metacognitive loop, `third_party/fable_method/`)
- **Self-Learning** (RL loop, `third_party/self_learning/`)

These are bundled for offline reference and provenance.
