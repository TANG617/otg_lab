"""Boundary, property, and long-sequence tests for the safety invariant."""

from __future__ import annotations

import numpy as np
import pytest

from otg_lab.config import load_config
from otg_lab.constraints import (
    integrate_constant_jerk,
    point_within_va_limits,
    segment_constant_jerk_feasible,
    terminal_has_viable_next_step,
    terminal_stopping_viable,
    viable_jerk_intervals,
)
from otg_lab.experiments import (
    run_pipeline_matrix,
    same_information_methods,
    synthetic_cases,
)
from otg_lab.governors import MotionLimits, OneStepBoundedJerkGovernor

DT = 0.01
LIMITS = MotionLimits.broadcast(1, 4.1, 8.2, 4000.0)


def _in_intervals(jerk: float, intervals: tuple[tuple[float, float], ...]) -> bool:
    return any(low <= jerk <= high for low, high in intervals)


@pytest.mark.parametrize("acceleration", [0.1, 2.0, 8.2])
def test_exact_directional_stopping_envelope_boundaries(acceleration):
    jmax = LIMITS.max_jerk[0]
    vmax = LIMITS.max_velocity[0]
    upper = np.array([0.0, vmax - acceleration**2 / (2.0 * jmax), acceleration])
    lower = np.array([0.0, -vmax + acceleration**2 / (2.0 * jmax), -acceleration])
    assert terminal_stopping_viable(upper, LIMITS, tolerance=0.0)
    assert terminal_stopping_viable(lower, LIMITS, tolerance=0.0)

    outside_upper = upper.copy()
    outside_upper[1] = np.nextafter(outside_upper[1], np.inf)
    outside_lower = lower.copy()
    outside_lower[1] = np.nextafter(outside_lower[1], -np.inf)
    assert not terminal_stopping_viable(outside_upper, LIMITS, tolerance=0.0)
    assert not terminal_stopping_viable(outside_lower, LIMITS, tolerance=0.0)


@pytest.mark.parametrize(
    ("velocity", "acceleration", "expected"),
    [
        (4.1, 0.1, False),
        (4.1, -0.1, True),
        (-4.1, 0.1, True),
        (-4.1, -0.1, False),
        (-4.099, 2.0, True),
        (4.099, -2.0, True),
        (0.0, 8.2, True),
        (0.0, -8.2, True),
    ],
)
def test_stopping_viability_is_directional(velocity, acceleration, expected):
    state = np.array([0.0, velocity, acceleration])
    assert point_within_va_limits(state, LIMITS)
    assert terminal_stopping_viable(state, LIMITS) is expected


def test_analytic_jerk_intervals_match_independent_random_audit():
    """A random oracle test may sample; the implementation under test does not."""

    rng = np.random.default_rng(20260722)
    checked = 0
    for _ in range(12_000):
        current = np.array([0.0, rng.uniform(-4.1, 4.1), rng.uniform(-8.2, 8.2)])
        if not terminal_stopping_viable(current, LIMITS, tolerance=0.0):
            continue
        jerk = rng.uniform(-4000.0, 4000.0)
        terminal = integrate_constant_jerk(current, jerk, DT)
        independently_feasible = segment_constant_jerk_feasible(
            current, jerk, DT, LIMITS, tolerance=0.0
        ) and terminal_stopping_viable(terminal, LIMITS, tolerance=2e-12)
        assert (
            _in_intervals(jerk, viable_jerk_intervals(current, DT, LIMITS))
            is independently_feasible
        )
        checked += 1
    assert checked > 11_500


def test_nonfinite_target_executes_verified_safety_action_not_impossible_hold():
    governor = OneStepBoundedJerkGovernor(1, DT, LIMITS)
    current = np.array([[0.3, 1.2, 2.0]])
    result = governor.update(
        np.array([[np.nan, np.inf, -np.inf]]),
        control_time=0.0,
        current_state=current,
    )
    reconstructed = integrate_constant_jerk(current, result.jerk, DT)
    np.testing.assert_allclose(result.executable_state, reconstructed, atol=1e-14)
    assert not np.array_equal(result.executable_state, current)
    assert result.fallback_requested and result.fallback_applied
    assert result.safety_guarantee and not result.emergency_mode
    assert result.command_segment_feasible
    assert result.command_terminal_viable
    assert result.command_next_step_exists


@pytest.mark.parametrize(
    "current",
    [
        np.array([[0.0, 4.2, 0.0]]),
        np.array([[0.0, 0.0, 8.3]]),
        np.array([[0.0, 4.1, 0.1]]),
        np.array([[0.0, -4.1, -0.1]]),
    ],
)
def test_outside_viability_uses_integrated_explicit_emergency(current):
    governor = OneStepBoundedJerkGovernor(1, DT, LIMITS, measured_state_mode="measured")
    result = governor.update(np.zeros((1, 3)), control_time=0.0, current_state=current)
    np.testing.assert_allclose(
        result.executable_state,
        integrate_constant_jerk(current, result.jerk, DT),
        rtol=0.0,
        atol=1e-14,
    )
    assert result.fallback_reason == "unrecoverable_current_state"
    assert result.fallback_requested and result.fallback_applied
    assert result.emergency_mode
    assert not result.safety_guarantee
    assert np.all(np.isfinite(result.executable_state))
    assert np.all(np.abs(result.jerk) <= LIMITS.max_jerk)


def test_hybrid_measurement_correction_near_boundary_enters_emergency():
    governor = OneStepBoundedJerkGovernor(
        1,
        DT,
        LIMITS,
        measured_state_mode="hybrid",
        divergence_threshold=1e-9,
    )
    first = governor.update(
        np.zeros((1, 3)), control_time=0.0, current_state=np.zeros((1, 3))
    )
    measured = np.array([[first.executable_state[0, 0], 4.1, 0.1]])
    recovered = governor.update(
        np.zeros((1, 3)), control_time=DT, current_state=measured
    )
    assert recovered.emergency_mode
    np.testing.assert_allclose(
        recovered.executable_state,
        integrate_constant_jerk(measured, recovered.jerk, DT),
        rtol=0.0,
        atol=1e-14,
    )


def test_oscillatory_test_004_boundary_regression_does_not_freeze():
    """Reproduce the v1 transition that created the impossible-hold cascade."""

    governor = OneStepBoundedJerkGovernor(1, DT, LIMITS, measured_state_mode="measured")
    current = np.array([[0.8147513157648854, -4.048799, -8.2]])
    targets = (
        np.array([[-0.576821, -2.905800, 3.253637]]),
        np.array([[-0.605713, -2.872475, 3.416625]]),
        np.array([[-0.634265, -2.837531, 3.577687]]),
    )
    for index, target in enumerate(targets):
        result = governor.update(
            target,
            control_time=(170 + index) * DT,
            current_state=current,
        )
        np.testing.assert_allclose(
            result.executable_state,
            integrate_constant_jerk(current, result.jerk, DT),
            rtol=0.0,
            atol=2e-12,
        )
        assert result.safety_guarantee
        assert result.command_segment_feasible
        assert result.command_terminal_viable
        assert result.command_next_step_exists
        assert terminal_has_viable_next_step(result.executable_state, DT, LIMITS)
        current = result.executable_state


def test_full_exposed_oscillatory_test_004_pipeline_regression() -> None:
    """Replay all 447 exposed v1 cycles; this is regression, never inference."""

    config = load_config("configs/locked_test_v1.yaml")
    selection = config["locked_selection"]
    method_ids = {
        "one_step_governed_pva_direct",
        "one_step_governed_pva_ruckig",
    }
    methods = [
        method
        for method in same_information_methods(
            estimator=selection["estimator"],
            estimator_parameters=selection["estimator_parameters"],
            predictor=selection["predictor"],
            horizon_ms=selection["prediction_horizon_ms"],
            qp_horizon_steps=selection["qp_horizon_steps"],
        )
        if method["method_id"] in method_ids
    ]
    cases = [
        case
        for case in synthetic_cases(
            "test",
            sample_rate_hz=100.0,
            manifest_path="split_manifest.json",
            run_id="v1-exposed-regression",
        )
        if case[0] == "oscillatory__test__004"
    ]
    assert len(cases) == 1
    assert len(cases[0][1]) == 447
    outcome = run_pipeline_matrix(cases, config, methods)
    assert not outcome.failures

    for method_id in method_ids:
        rows = [row for row in outcome.samples if row["method_id"] == method_id]
        audits = [
            audit
            for audit in outcome.constraint_audits
            if audit["method_id"] == method_id
        ]
        assert len(rows) == 447
        assert sum(int(audit["violation_count"]) for audit in audits) == 0
        for row in rows:
            current = np.array([row["current_p"], row["current_v"], row["current_a"]])
            expected = integrate_constant_jerk(
                current, float(row["command_jerk"]), float(row["dt_control"])
            )
            np.testing.assert_allclose(
                expected,
                [row["command_p"], row["command_v"], row["command_a"]],
                rtol=0.0,
                atol=2e-12,
            )
            assert row["safety_guarantee"] is True
            assert row["command_segment_feasible"] is True
            assert row["command_stopping_viable"] is True
            assert row["command_continuous_constraints_satisfied"] is True
            assert row["emergency_mode"] is False
            if row["fallback_applied"]:
                assert row["fallback_requested"] is True
                assert row["fallback_reason"]


def test_10001_sequential_adversarial_updates_preserve_invariant():
    governor = OneStepBoundedJerkGovernor(1, DT, LIMITS)
    current = np.zeros((1, 3))
    rng = np.random.default_rng(617)
    for index in range(10_001):
        if index and index % 997 == 0:
            target = np.array([[np.nan, np.inf, -np.inf]])
        elif index % 4 == 0:
            sign = -1.0 if (index // 4) % 2 else 1.0
            target = np.array([[100.0 * sign, 100.0 * sign, 10_000.0 * sign]])
        elif index % 4 == 1:
            target = np.array([[0.0, 4.1, 8.2]])
        elif index % 4 == 2:
            target = np.array([[0.0, -4.1, -8.2]])
        else:
            target = rng.normal(size=(1, 3)) * np.array([[5.0, 8.0, 80.0]])
        result = governor.update(
            target,
            control_time=index * DT,
            current_state=current if index == 0 else None,
        )
        np.testing.assert_allclose(
            result.executable_state,
            integrate_constant_jerk(current, result.jerk, DT),
            rtol=0.0,
            atol=2e-12,
        )
        assert result.safety_guarantee
        assert result.command_segment_feasible
        assert result.command_terminal_viable
        assert result.command_next_step_exists
        current = result.executable_state
