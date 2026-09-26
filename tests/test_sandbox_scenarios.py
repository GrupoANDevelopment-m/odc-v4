"""Known-scenario tests for the SandboxVerifier.

These tests verify the verifier behaves correctly on synthetic scenarios
with KNOWN ground-truth outcomes — no LLM, no live network. They were
written after the audit found the simulation bugs:

  Bug #1: sim_failures counted successes as failures
  Bug #2: regression branch was unreachable (inside failure-else)

Each test specifies the expected baseline rate, expected simulation
result, and expected verdict. If any of these break, the engine would
take wrong actions in production — so the cases are documented.
"""
import pytest

from odc.refinement.field_data import FieldDataStore, FieldOutcome
from odc.refinement.proposal import ModificationProposal, Target
from odc.refinement.sandbox import SandboxVerifier


def _make_store(outcomes: list[FieldOutcome]) -> FieldDataStore:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = FieldDataStore(path)
    for o in outcomes:
        store.record(o)
    return store


def _proposal(target: Target = Target.PROMPT_BUILDER,
              metadata: dict | None = None) -> ModificationProposal:
    return ModificationProposal(
        id="prop-test", target=target,
        before={}, after={"x": 1}, justification="test",
        field_evidence_count=10, common_error_class="timeout",
        baseline_failure_rate=0.6, expected_improvement=0.2,
        rollback_plan={"restore": {}},
        metadata=metadata or {"relevant_task_types": ["test"]},
    )


# ──────────────────────────────────────────────────────────────────
# SCENARIO 1: All failures timeout → prompt_builder reduces tools
# Expected: improvement, NO regression (no successes to break)
# ──────────────────────────────────────────────────────────────────
def test_scenario_all_timeouts_prompt_builder_helps():
    outcomes = [
        FieldOutcome(task_id=f"t-{i}", task_type="test",
                     hypothesis_id=f"h{i}", success=False, error_class="timeout")
        for i in range(10)
    ]
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.PROMPT_BUILDER))
    assert v.baseline_failure_rate == 1.0
    assert v.simulated_failure_rate < 1.0, "should improve"
    assert v.delta < 0
    assert v.improvement_count == 10, "all 10 timeouts should be flipped"
    assert v.regression_count == 0, "no successes existed, so no regressions"
    # Decision: apply (improvement + no regressions)
    assert v.should_apply, v.explain()


# ──────────────────────────────────────────────────────────────────
# SCENARIO 2: Mixed failures + successes, proposal affects successes
# Expected: regression risk detected, should NOT apply
# ──────────────────────────────────────────────────────────────────
def test_scenario_mixed_with_regression_risk():
    outcomes = []
    # 5 failures (timeout)
    for i in range(5):
        outcomes.append(FieldOutcome(
            task_id=f"f-{i}", task_type="test", hypothesis_id="h1",
            success=False, error_class="timeout",
        ))
    # 5 successes that COULD regress under prompt_builder change
    # (their error_class contains "context" — REGRESSION_RULES pattern)
    for i in range(5):
        outcomes.append(FieldOutcome(
            task_id=f"s-{i}", task_type="test", hypothesis_id="h1",
            success=True, error_class="context_window_issue",
        ))
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.PROMPT_BUILDER))
    assert v.baseline_failure_rate == 0.5
    assert v.improvement_count == 5  # 5 timeouts converted
    assert v.regression_count == 5  # 5 successes at risk
    # Regression ratio = 5/10 = 50% > MAX_REGRESSION_RATIO (20%)
    assert not v.should_apply
    assert any("regression" in c for c in v.concerns)


# ──────────────────────────────────────────────────────────────────
# SCENARIO 3: All successes → no improvements possible
# Expected: NO improvement, do NOT apply
# ──────────────────────────────────────────────────────────────────
def test_scenario_all_success_no_improvement():
    outcomes = [
        FieldOutcome(task_id=f"s-{i}", task_type="test", hypothesis_id=f"h{i}",
                     success=True) for i in range(10)
    ]
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.PROMPT_BUILDER))
    assert v.baseline_failure_rate == 0.0
    assert v.simulated_failure_rate == 0.0
    assert v.delta == 0.0
    assert not v.should_apply


# ──────────────────────────────────────────────────────────────────
# SCENARIO 4: Target mismatch — failures not matching pattern
# Expected: no conversion (target doesn't help), no improvement
# ──────────────────────────────────────────────────────────────────
def test_scenario_target_mismatch_no_help():
    """Failures are 'permission_denied', proposal is for prompt_builder (timeout)."""
    outcomes = [
        FieldOutcome(task_id=f"t-{i}", task_type="test", hypothesis_id="h",
                     success=False, error_class="permission_denied")
        for i in range(10)
    ]
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.PROMPT_BUILDER))
    assert v.baseline_failure_rate == 1.0
    assert v.improvement_count == 0, "permission_denied doesn't match timeout pattern"
    assert not v.should_apply


# ──────────────────────────────────────────────────────────────────
# SCENARIO 5: Tool-creation proposal matches schema errors
# Expected: high improvement, no regression
# ──────────────────────────────────────────────────────────────────
def test_scenario_schema_errors_tool_template_helps():
    outcomes = [
        FieldOutcome(task_id=f"t-{i}", task_type="test", hypothesis_id="h",
                     success=False, error_class="schema_validation")
        for i in range(8)
    ]
    # 2 successes unrelated
    for i in range(2):
        outcomes.append(FieldOutcome(
            task_id=f"s-{i}", task_type="test", hypothesis_id="h",
            success=True,
        ))
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.TOOL_CREATE_TEMPLATE))
    assert v.baseline_failure_rate == pytest.approx(0.8, abs=0.01)
    assert v.improvement_count == 8
    assert v.regression_count == 0
    assert v.should_apply, v.explain()


# ──────────────────────────────────────────────────────────────────
# SCENARIO 6: Heuristic promotion with 50%+ successes → high regression risk
# Expected: regression detected → do NOT apply
# ──────────────────────────────────────────────────────────────────
def test_scenario_heuristic_promotion_dangerous():
    """Lowering heuristic threshold can break working heuristic matches."""
    outcomes = []
    # 4 failures (prompt-related) — would benefit from heuristic changes
    for i in range(4):
        outcomes.append(FieldOutcome(
            task_id=f"f-{i}", task_type="test", hypothesis_id="h",
            success=False, error_class="prompt_mismatch",
        ))
    # 6 successes — risk regression because context-error-class successes
    # would now match a more permissive heuristic
    for i in range(6):
        outcomes.append(FieldOutcome(
            task_id=f"s-{i}", task_type="test", hypothesis_id="h",
            success=True, error_class="context_aware_match",
        ))
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.HEURISTIC_PROMOTION))
    # 4 failures converted + 6 regressions
    assert v.improvement_count == 4
    assert v.regression_count == 6
    # Regression ratio 6/10 = 60% > MAX_REGRESSION_RATIO
    assert not v.should_apply


# ──────────────────────────────────────────────────────────────────
# SCENARIO 7: Real-world Edge — Bitcoin timing pattern
# Expected: improvement on rate-limited failures
# ──────────────────────────────────────────────────────────────────
def test_scenario_real_world_bitcoin_rate_limit():
    """Realistic: 12 failures due to rate_limit, 8 successes."""
    outcomes = []
    for i in range(12):
        outcomes.append(FieldOutcome(
            task_id=f"btc-{i}", task_type="bitcoin_query",
            hypothesis_id="httpx_default",
            success=False, error_class="rate_limit_exceeded",
        ))
    for i in range(8):
        outcomes.append(FieldOutcome(
            task_id=f"btc-ok-{i}", task_type="bitcoin_query",
            hypothesis_id="httpx_default", success=True,
        ))
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(
        Target.PROMPT_BUILDER,
        metadata={"relevant_task_types": ["bitcoin_query"]},
    ))
    assert v.baseline_failure_rate == pytest.approx(0.6, abs=0.01)
    # All 12 rate_limit failures flipped → 0 simulated failures
    assert v.simulated_failure_rate == 0.0
    assert v.should_apply


# ──────────────────────────────────────────────────────────────────
# SCENARIO 8: REGRESSION OF BUG #1
# Make sure we DON'T count originally-successful outcomes as failures
# This is the exact regression scenario from the audit.
# ──────────────────────────────────────────────────────────────────
def test_scenario_no_success_counted_as_failure():
    """100% success rate should remain 0% in simulation (was previously inflated)."""
    outcomes = [
        FieldOutcome(task_id=f"s-{i}", task_type="test", hypothesis_id="h",
                     success=True) for i in range(20)
    ]
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.PROMPT_BUILDER))
    # Critical: simulated rate MUST equal baseline rate (0.0)
    assert v.simulated_failure_rate == 0.0, \
        f"BUG REGRESSION: success counted as failure! rate={v.simulated_failure_rate}"
    assert v.delta == 0.0


# ──────────────────────────────────────────────────────────────────
# SCENARIO 9: REGRESSION OF BUG #2
# The old "regressed.append(o)" was unreachable — verify it now actually
# runs and computes correctly when successes have risky error classes.
# ──────────────────────────────────────────────────────────────────
def test_scenario_regression_branch_now_reachable():
    """Successes with risky error classes should now be flagged."""
    successes = [
        FieldOutcome(task_id=f"s-{i}", task_type="test", hypothesis_id="h",
                     success=True, error_class="context_aware_match")
        for i in range(9)
    ]
    outcomes = [
        FieldOutcome(task_id="f-1", task_type="test", hypothesis_id="h",
                     success=False, error_class="timeout"),
    ] + successes
    store = _make_store(outcomes)
    v = SandboxVerifier(store).verify(_proposal(Target.PROMPT_BUILDER))
    # 1 improvement + 9 regressions
    assert v.improvement_count == 1
    assert v.regression_count == 9, \
        f"BUG REGRESSION: regression branch not detecting risky successes! count={v.regression_count}"
    # 9/10 = 90% regression rate, way above threshold
    assert not v.should_apply
