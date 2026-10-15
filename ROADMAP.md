# ODC v4 — Roadmap

## Current: v4.0.0 (Production)

The current release is feature-complete as a self-improving agent:
- 89 tools, 7 skills, 4 LLM providers
- 553 tests, 0 regressions
- AST+LLM self-heal (real 550B verified)
- Proactive code improvement
- Browser automation (11 tools, persistent)
- MCP memory substrate
- DEEP-REASON System 2
- Cognitive validation suite
- 3D web UI

## v4.1 — Near-term (next 4-6 weeks)

### Self-modification of ODC framework itself
Currently self-heal only patches dynamic tools. The constitutional
guard + sandbox verifier exist for ODC code; need to:
- Wire `record_self_patch` to `refine.apply_proposal` for ODC source
- Add a `propose_odc_patch` flow: LLM proposes diff → guard → sandbox
- Add a `canary_test` step: run full test suite on proposed patch
- Rollback if any test fails

### LLM-as-surgeon for novel fixes
Currently AST handles known patterns, LLM handles unknown. Want:
- LLM reviews failing code + error → proposes targeted diff
- Diff applied via AST node replacement (not full file rewrite)
- LLM-generated regression test included in proposal
- Sandbox runs the new test before apply

### Federated memory
Multiple ODC instances share learnings via:
- Heuristic promotion broadcast (anonymized, opt-in)
- Anti-pattern sharing
- Tool usage statistics aggregation

## v4.2 — Mid-term (3-6 months)

### Voice / audio interface
- WebRTC for browser-based voice
- Whisper transcription (local)
- TTS via local model (Piper, Coqui)

### Vision / multimodal
- LLaVA-style local vision model
- Screenshot understanding for browser automation
- Image generation (Stable Diffusion XL local)

### Plugin system
- First-class plugin API
- Plugins in any language (WASM)
- Plugin marketplace / registry

### Distributed deployment
- Multi-node ODC cluster
- Shared state via Raft consensus
- Load balancing

## v5.0 — Long-term (6-12 months)

### AGI-grade metacognition
- Real-time theory of mind
- Self-modifying goals
- Autonomous research direction
- Cross-domain transfer learning

### Embodied ODC
- ROS integration for robot control
- Drone / vehicle interfaces
- Sensor fusion

### Full code-owning self-modification
- Agent rewrites its own LLM prompts at runtime
- Agent trains its own fine-tunes (LoRA on successful patches)
- Agent evolves its own architecture

## Non-goals

- Compete with frontier model labs (we run on top of them)
- Replace human developers (we augment)
- Closed-source commercialization (we stay MIT)
- Centralized control (decentralized by design)

## Contributing

See CONTRIBUTING.md. All contributions must:
- Pass existing tests
- Add new tests for new features
- Not break the constitutional guard (I1-I10)
- Not disable `require_safety`
- Be honest about limitations (no fake PASS)
