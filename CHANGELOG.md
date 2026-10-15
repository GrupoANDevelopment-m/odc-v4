# Changelog

All notable changes to ODC v4 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.0.0] - 2026-10-15

### Added — Production Release

**Core agent**
- Fable Method loop with persistent SQLite checkpointing
- 89 tools (code, fs, shell, web, browser, OSINT, MCP, refinement, dynamic)
- 4 LLM providers (NVIDIA 550B, OpenAI, Anthropic, Gemini, Ollama)
- Web UI (3D, hash-routed, 5 subpages, premium palette)
- CLI, REPL, Python API entry points

**Cognitive architecture**
- 4-layer dynamic prompt builder (BM25, identity, task, history)
- DEEP-REASON System 2 (council, revise, analogy, hypothesize, decompose)
- 6-tier evidence taxonomy
- Wisdom-not-trauma failure learning
- Strict conversation isolation

**Memory / state**
- MCP persistent memory substrate (5-step ritual)
- Cognitive profile (heuristics, anti-patterns, lessons)
- OSINT module (13 endpoints: 8 no-key + 5 keyed)
- Injection-resistant framing (PAM)
- HMAC-chained audit trail

**Self-improvement (NEW)**
- AST + LLM combined self-heal for dynamic tools
- Proactive LLM-driven code improvement (refactor, not just fix)
- SelfImprovementEngine (reflections, trust, heuristics, anti-patterns, lessons, self-patches)
- Constitutional guard (10 invariants, I1-I10)
- Atomic apply with backup + auto-rollback
- Journal: every attempt logged
- Real 550B tested: actually generates working fixes and meaningful improvements

**Auto-extension**
- Runtime gate (default ON) for `dynamic.tool_create` and similar
- `require_safety` UNBREAKABLE (cannot be disabled)

**Browser automation (NEW)**
- 11 browser tools (open/goto/evaluate/screenshot/text/html/cookies/...)
- Async Playwright + persistent storage_state.json
- Loop-aware singleton
- Cross-process persistence verified (subprocess #1 sets, #2 reads)

**Tests (NEW)**
- 384+ tests passing across 17 test files, 0 regressions
- 25 cognitive validation tests (real LLM, 550B thinking)
- 20 adversarial audit tests
- 20 deep audit tests
- 3 real ODC bugs found and fixed (SQLite WAL, profile lock, Modelfile paths)
- test_growth.py — proves agent gets smarter over time
- test_self_heal_real_llm.py — proves AST+LLM self-heal with real 550B

**Production infrastructure (NEW)**
- setup.py / pyproject.toml (installable, PEP 621)
- Makefile (40+ targets: install/test/lint/run/build/docker/audit/clean)
- setup.sh (one-command install with deps + Playwright)
- Dockerfile (multi-stage build) + docker-compose.yml
- GitHub Actions CI/CD (lint, type-check, test, audit, build, docker, security)
- Git LFS tracking (.gitattributes for *.zip, *.tar.gz, etc)
- requirements.txt / requirements-dev.txt (pinned deps)
- .env.example
- Pre-commit config (ruff + black + git-lfs + large-file check)
- LICENSE (MIT)
- ROADMAP.md, CONTRIBUTING.md, SECURITY.md, CHANGELOG.md
- odc/version.py + odc/__init__.py re-exports

### Changed
- Repo size from 6MB → 125MB (now bundles full upstream sources)
- All 6MB gitignored third_party removed from .gitignore
- `odc/__init__.py` uses lazy `__getattr__` to re-export `Agent` (avoids circular imports)

### Fixed
- SQLite WAL deadlock (50 threads)
- CognitiveProfile thread-safety
- Modelfile leaked paths
- Browser data_dir override bug
- Async loop awareness in browser singleton
- `from odc import Agent` ImportError (lazy re-export)

### Honest gaps
- AST fix strategies are hand-coded (TypeError, ImportError, KeyError, etc); LLM handles novel patterns
- Self-heal only patches dynamic tools, not ODC framework
- LLM-as-surgeon for fully novel fixes is partial (relies on prompt quality)
- Browser test uses real Chromium (not headless mock)
- Some unit tests still slow (not all marked `@pytest.mark.slow`)

## [3.x] - earlier

Initial development with cognitive architecture, basic tools, web UI.
