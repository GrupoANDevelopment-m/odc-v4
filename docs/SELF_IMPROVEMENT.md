# ODC v4 — Self-Improvement Engine

**Module:** `odc/refinement/auto_heal.py`
**Test:** `test_growth.py`
**Date:** 2026-10-13

The user demanded that ODC v4 should "increase in IQ with practice" —
get smarter over time, learn to use flows, auto-correct bugs, and
self-apply patches. This is the implementation, verified end-to-end.

---

## The user's exact ask

> "It has the capacity for IQ increase — this means getting smarter
> with time. Should be able to improve the ability to use flows and
> learn to use flows with practice, and increase the ability to
> auto-correct its bugs in the system, self-applying paths."

---

## What was built

`SelfImprovementEngine` (odc/refinement/auto_heal.py) — a closed loop
that observes, learns, and applies. Hooks into CognitiveProfile
(lessons, heuristics, anti-patterns) and tracks growth metrics
persistently.

### Six learning mechanisms

1. **Reflection per task** — every `reflect_on_task()` call updates
   `meta_metrics.total_tasks`, `successful_tasks`, `failed_tasks`,
   plus confidence-per-task-type (exponential moving average).

2. **Tool trust** — after 5+ calls with ≥80% success rate, a tool
   is marked `trusted: True` in `tool_success[tool]`. Future
   `cognitive.route` can prefer trusted tools.

3. **Heuristic promotion** — when a heuristic is `threads_seen` in
   2+ distinct threads, it's marked `promoted: True`. The system
   has learned this rule generalizes beyond one context.

4. **Anti-pattern extraction** — every 3rd failure of a task type
   adds an anti-pattern with context, occurrences, last_seen,
   `false_positives` (so bad rules can be deprecated, not deleted —
   wisdom, not trauma).

5. **Lessons** — every failure with an error message adds a
   lesson via `profile.add_lesson()`. Persistent across process
   restarts.

6. **Self-patches** — `record_self_patch(patch_id, target, before,
   after)` records a self-applied modification. Counter increments,
   lesson added. Real self-correction requires a human in the loop
   to approve, but the bookkeeping is closed.

---

## What the test proves (`test_growth.py`)

| Phase | What happens | Result |
|---|---|---|
| 0 | Baseline (no learning) | reflections=0, no heuristics, no anti-patterns |
| 1 | 20 successful tasks across 3 threads | 6 tools granted trust |
| 2 | 2 heuristics added, marked seen in 2+ threads | **2 heuristics promoted** |
| 3 | 9 distinct failures | **3 anti-patterns added** + 9 lessons |
| 4 | 2 self-patches recorded | **2 self-patches applied** |
| 5 | Process death (new SelfImprovementEngine instance) | All state preserved |
| 6 | Final report | **30 reflections, 2 promoted, 3 anti-patterns, 2 patches, 6 trusted** |

**`✓ AGENT GREW (IQ increased)`** — assertion held.

---

## What is honest, what is not

### Verified ✓
- Reflections accumulate (30 from 30 task reflections)
- Trust is granted after 5+ successful uses (6 tools hit threshold)
- Heuristics are promoted when seen in 2+ threads (2 promoted)
- Anti-patterns are added at the 3-failure cadence (3 added)
- Self-patches are recorded (2 recorded)
- **All state persists across process restart** (proven via new
  engine instance reading same `self_improvement.json`)

### Not verified ✗
- **Real self-correction in production** — the test records that a
  patch was applied, but it does not write a real code change to
  ODC itself. That requires a human-approved `ModificationProposal`
  flowing through the constitutional guard + sandbox + apply.
- **Heuristic-driven routing** — the trust system records which
  tools are trusted, but `cognitive.route` does not yet prioritize
  trusted tools over untrusted. Wiring needed.
- **Anti-pattern avoidance** — anti-patterns are stored, but the
  agent does not yet actively avoid them at planning time.
- **Multi-day learning** — test simulates 30 reflections in 1 second.
  Real-world compounding over days/weeks is unmeasured.

---

## Files

- `odc/refinement/auto_heal.py` — engine (8.1KB)
- `test_growth.py` — proof of growth (5.5KB, 6 phases, all green)

---

## What this enables (not yet wired, but built)

1. **Tool confidence** — future `cognitive.route` can use
   `profile.data["tool_success"][tool]["trusted"]` to skip
   alternatives in routing.

2. **Auto-patch candidate selection** — `record_self_patch` is the
   seam where real self-modification would integrate with the
   existing `SelfRefinementEngine.apply_proposal()`.

3. **Cross-task learning** — `confidence_by_task` lets the agent
   say "I'm 1.0 confident on hash_file, 0.0 on bad_task" — useful
   for the agent to ask for help on low-confidence tasks.

4. **Anti-pattern warnings** — `profile.get_relevant_anti_patterns()`
   already exists in the cognitive layer; just needs to be called
   at planning time.
