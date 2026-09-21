# ODC v4 — Open Distributed Cognitive architecture

LLM agent with **ACAMR-9 (Adaptive Self-Refinement)** classification.

## What it does

- **Persistent memory** (SQLite-backed) with cross-session lineage (5-step ritual)
- **Real-time OSINT sensors** (13 endpoints): flights, earthquakes, CVE, bitcoin, crypto, weather, satellites, fires, news, sanctions, space weather, Wikipedia
- **Injection-resistant framing** (PAM memory blocks, content escaping)
- **6-tier Evidence Taxonomy** for calibrated confidence
- **DEEP-REASON System 2**: council of 5 lenses, multi-hypothesis, decompose, analogical reasoning
- **Self-refinement engine** that ONLY modifies itself when ALL current hypotheses have failed in field data — and never touches the constitutional core

## Architecture layers

```
Level 9: Adaptive Self-Refinement (Field-Grounded, Constitutional)
Level 8: Embodied Persistent Reasoning (real OSINT data)
Level 7: Multi-perspective Reasoning (DEEP-REASON)
Level 6: Autopoietic (self-extension, self-repair)
Level 5: Self-improving (wisdom-not-trauma)
Level 4: Self-healing (resilience layer)
Level 3: Self-extending (dynamic tools + skills)
Level 2: Reflective (loop checks itself)
Level 1: Reactive agent (LLM + tools)
```

## Quick start

```bash
# 1. install
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. configure LLM
export NVIDIA_API_KEY="your-key"
export ODC_LLM_PROVIDER=nvidia
export NVIDIA_MODEL="mistralai/mistral-nemotron"

# 3. run tests
.venv/bin/python -m pytest tests/ --ignore=tests/test_e2e_*.py -v

# 4. real LLM benchmark (needs API key)
export NVIDIA_API_KEY="..."  # and run
.venv/bin/python -m pytest tests/test_real_llm_benchmark.py -v

# 5. REPL
.venv/bin/python -m odc.cli
```

## Verified with real data

- 310 unit tests passing
- 4 real-LLM tests with `mistralai/mistral-nemotron`
- 36 solid-benchmark tests against live public APIs (OpenSky, USGS, NVD, blockstream, CoinGecko, NOAA, Open-Meteo, Wikipedia)
- Real Bitcoin block height returned via `osint.bitcoin`
- Real weather data via `osint.weather`
- Real-time aircraft count via `osint.flights`

## Structure

```
odc/
├── agent.py             # Agent class
├── loop.py              # Main loop with dynamic prompt
├── prompt/              # 4-layer dynamic prompt builder
│   ├── builder.py       # Core assembly logic
│   ├── bm25.py          # BM25 ranking (zero deps)
│   ├── scope.py         # Thread isolation
│   ├── osiris_frame.py  # Injection-resistant framing
│   └── summarize.py     # Conversation compression
├── cognitive/           # Multi-perspective reasoning
│   ├── council.py       # 5-lens council
│   ├── revise.py        # Epistemic humility
│   ├── analogy.py       # Real-world analogy patterns
│   ├── decompose.py     # DAG of sub-tasks
│   ├── evidence.py      # 6-tier Evidence Taxonomy
│   └── ...
├── osint/               # Real-time planet sensors
│   ├── tools.py         # 8 no-key endpoints
│   └── tools_keyed.py   # 5 keyed endpoints (degrade gracefully)
├── mcp/                 # Persistent memory substrate
│   ├── osiris.py        # 5-step ritual, SQLite backend
│   └── tools.py         # 6 ODC tool wrappers
├── refinement/          # Level 9 self-refinement
│   ├── field_data.py    # Field outcomes, audit journal
│   ├── exhaustion.py    # Gate (fires only when all exhausted)
│   ├── proposal.py      # Modification proposals with justification
│   ├── constitution.py  # 10 untouchable invariants
│   ├── sandbox.py       # Test before applying
│   ├── engine.py        # Orchestrator
│   └── tools.py         # 6 ODC tool wrappers
├── code/                # Native code tools (no subprocess)
├── llm/                 # 4 LLM providers (nvidia, openai, anthropic, ollama)
└── tests/               # 350+ tests
```

## License

MIT
