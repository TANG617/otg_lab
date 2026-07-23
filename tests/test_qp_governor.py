from types import SimpleNamespace

import numpy as np
import pytest

from otg_lab.constraints import (
    integrate_constant_jerk,
    segment_constant_jerk_feasible,
    terminal_stopping_viable,
)
from otg_lab.governors import JerkQPGovernor, MotionLimits
from otg_lab.runner import run_pipeline_rows
from otg_lab.schema import empty_sample

DT = 0.01
LIMITS = MotionLimits.broadcast(1, 4.1, 8.2, 4000.0)
ZERO = np.zeros((1, 3))


def _target(steps=10):
    return np.repeat(np.array([[[0.02, 0.4, 0.0]]]), steps, axis=0)


class _FakeSolver:
    def __init__(self, solution=None, error=None):
        self.solution = solution
        self.error = error
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def warm_start(self, **_kwargs):
        pass

    def solve(self):
        if self.error is not None:
            raise self.error
        return self.solution


def _solution(status, status_val, *, steps=10, x=None, y=None):
    return SimpleNamespace(
        info=SimpleNamespace(
            status=status,
            status_val=status_val,
            iter=17,
            solve_time=0.000123,
            prim_res=2e-6,
            dual_res=3e-6,
        ),
        x=x,
        y=y,
    )


def _assert_safe_fallback(result):
    assert result.fallback_applied
    np.testing.assert_allclose(
        result.executable_state,
        integrate_constant_jerk(ZERO, result.jerk, DT),
        rtol=0.0,
        atol=2e-10,
    )
    assert segment_constant_jerk_feasible(ZERO, result.jerk, DT, LIMITS)
    assert terminal_stopping_viable(result.executable_state, LIMITS)


def test_qp_is_dimensionless_and_reuses_fixed_problem_with_primal_dual_warm_start():
    governor = JerkQPGovernor(1, DT, LIMITS, horizon_steps=10)
    assert governor.time_limit_s < DT
    assert governor.time_limit_s < 0.01

    first = governor.update(_target(), control_time=0.0, current_state=ZERO)
    hessian = governor._hessian
    constraints = governor._constraints
    second = governor.update(_target(), control_time=DT)

    assert not first.fallback_applied
    assert not second.fallback_applied
    assert governor.solver_setup_count == 1
    assert governor.solver_update_count == 1
    assert governor._hessian is hessian
    assert governor._constraints is constraints
    assert governor._warm_x is not None
    assert governor._warm_y is not None
    assert np.max(np.abs(governor._warm_x)) <= 1.0 + 1e-5
    assert np.isfinite(second.qp_solve_time_us)
    assert np.isfinite(second.qp_primal_residual)
    assert np.isfinite(second.qp_dual_residual)
    assert np.isfinite(second.qp_hessian_condition_number)
    assert np.isfinite(second.qp_constraint_condition_number)


@pytest.mark.parametrize(
    ("status", "status_val", "reason"),
    [
        ("run time limit reached", 8, "qp_time_limit_reached"),
        ("maximum iterations reached", 7, "qp_max_iter_reached"),
        ("primal infeasible", 3, "qp_primal_infeasible"),
        ("dual infeasible", 5, "qp_dual_infeasible"),
        ("problem non convex", 9, "qp_numerical_failure"),
    ],
)
def test_qp_statuses_are_not_collapsed(status, status_val, reason):
    governor = JerkQPGovernor(1, DT, LIMITS, horizon_steps=10)
    governor._solver = _FakeSolver(_solution(status, status_val))

    result = governor.update(_target(), control_time=0.0, current_state=ZERO)

    assert result.fallback_reason == reason
    assert result.qp_status_category == reason
    assert result.iterations == 17
    assert result.qp_solve_time_us == pytest.approx(123.0)
    assert result.qp_primal_residual == pytest.approx(2e-6)
    assert result.qp_dual_residual == pytest.approx(3e-6)
    _assert_safe_fallback(result)


def test_qp_solver_exception_is_numerical_failure_with_executed_fallback():
    governor = JerkQPGovernor(1, DT, LIMITS, horizon_steps=10)
    governor._solver = _FakeSolver(error=RuntimeError("backend failed"))

    result = governor.update(_target(), control_time=0.0, current_state=ZERO)

    assert result.fallback_reason == "qp_numerical_failure"
    assert "RuntimeError" in result.solver_status
    _assert_safe_fallback(result)


def test_qp_first_action_and_terminal_set_share_invariant_postcheck():
    steps = 10
    governor = JerkQPGovernor(1, DT, LIMITS, horizon_steps=steps)
    # A nominally "solved" but unsafe all-max-jerk vector must never commit.
    unsafe = np.ones(steps)
    dual = np.zeros(3 * steps + 2)
    governor._solver = _FakeSolver(
        _solution("solved", 1, steps=steps, x=unsafe, y=dual)
    )

    result = governor.update(_target(steps), control_time=0.0, current_state=ZERO)

    assert result.fallback_reason == "qp_postcheck_failed"
    _assert_safe_fallback(result)


def test_qp_terminal_plan_is_in_conservative_stopping_safe_box():
    governor = JerkQPGovernor(1, DT, LIMITS, horizon_steps=10)
    result = governor.update(_target(), control_time=0.0, current_state=ZERO)

    assert not result.fallback_applied
    assert terminal_stopping_viable(result.sequence[-1], LIMITS)
    assert abs(result.sequence[-1, 0, 2]) <= (
        governor.terminal_acceleration_fraction * LIMITS.max_acceleration[0] + 1e-4
    )
    np.testing.assert_allclose(
        result.executable_state,
        integrate_constant_jerk(ZERO, result.jerk, DT),
        rtol=0.0,
        atol=2e-10,
    )


def test_runner_persists_every_qp_observability_field_in_schema_v2():
    rows = []
    for k in range(8):
        time = DT * k
        position = 0.01 * np.sin(time)
        rows.append(
            empty_sample(
                run_id="qp-runner",
                dataset_id="qp-data",
                session_id="qp-session",
                trajectory_id="qp-trajectory",
                split="development",
                seed=1,
                joint_id="joint-0",
                k=k,
                source_time=time,
                arrival_time=time,
                control_time=time,
                dt_actual=DT,
                dt_control=DT,
                p_ref=position,
                p_meas=position,
                source_kind="synthetic",
                reference_family="unit_test",
                scenario_id="clean",
                truth_available=False,
                measurement_available=True,
                measurement_valid=True,
            )
        )
    config = {
        "control": {"dt": DT, "minimum_duration": DT},
        "limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
        "pipeline": {
            "estimator": "position_only",
            "estimator_parameters": {},
            "predictor": "constant_acceleration",
            "predictor_parameters": {},
            "prediction_horizon_ms": 10.0,
            "target_mode": "p",
            "governor": "jerk_qp",
            "governor_parameters": {"horizon_steps": 10},
            "follower": "direct",
            "plant": "ideal",
            "plant_parameters": {},
            "measured_state_mode": "previous_command",
        },
    }

    result = run_pipeline_rows(rows, config)

    assert {row["qp_status_category"] for row in result.rows} == {"qp_solved"}
    for row in result.rows:
        for field in (
            "qp_iterations",
            "qp_solve_time_us",
            "qp_primal_residual",
            "qp_dual_residual",
            "qp_hessian_condition_number",
            "qp_constraint_condition_number",
        ):
            assert row[field] is not None
            assert np.isfinite(row[field])
