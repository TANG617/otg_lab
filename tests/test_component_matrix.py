from __future__ import annotations

import numpy as np
import pytest

from otg_lab.components import (
    ConfiguredLimitProjectionGovernor,
    configured_limit_project_target_state,
)
from otg_lab.constraints import ruckig_target_admissible
from otg_lab.estimators import TimestampError, make_estimator
from otg_lab.generators import generate_analytic_trajectory
from otg_lab.governors import MotionLimits as NumericalMotionLimits
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
)
from otg_lab.tracking import run_tracking
from otg_lab.types import Measurement as EstimatorMeasurement

ESTIMATORS = (
    "position_only",
    "raw_backward_difference",
    "delay_one_centered_difference",
    "local_poly",
    "alpha_beta_gamma",
    "ca_kf",
    "robust_ca_kf",
    "cj_kf",
    "jerk_limited_differentiator",
)
PREDICTORS = (
    "zero_order_hold",
    "constant_velocity",
    "constant_acceleration",
    "constant_jerk",
    "local_polynomial",
)


def _reference():
    return generate_analytic_trajectory(
        "sine",
        {"dt_s": 0.01, "duration_s": 0.3, "settle_duration_s": 0.1},
    )


def _config() -> RunConfig:
    return RunConfig(
        MotionLimits(4.1, 8.2, 4000.0),
        minimum_duration_s=0.01,
        prediction_horizon_s=0.01,
        dt_s=0.01,
    )


@pytest.mark.parametrize("estimator_id", ESTIMATORS)
def test_all_estimator_components_complete_single_axis_run(
    estimator_id: str,
) -> None:
    params = (
        {"window": 5, "degree": 3, "lag_samples": 0}
        if estimator_id == "local_poly"
        else {}
    )
    method = TrackingMethodSpec(
        method_id=f"test_{estimator_id}",
        estimator=ComponentSpec(estimator_id, params),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
    )
    run = run_tracking(_reference(), method, _config())
    assert run.status.completed
    assert run.command is not None
    assert run.command.sample_count == _reference().sample_count - 1


@pytest.mark.parametrize("predictor_id", PREDICTORS)
def test_all_causal_predictors_have_explicit_future_time(
    predictor_id: str,
) -> None:
    params = (
        {"window": 5, "degree": 3}
        if predictor_id == "local_polynomial"
        else {}
    )
    method = TrackingMethodSpec(
        method_id=f"test_{predictor_id}",
        estimator=ComponentSpec(
            "local_poly",
            {"window": 5, "degree": 3, "lag_samples": 0},
        ),
        predictor=ComponentSpec(predictor_id, params),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
    )
    run = run_tracking(_reference(), method, _config())
    assert run.status.completed
    assert all(
        row["prediction_time_s"]
        == pytest.approx(row["measurement_time_s"] + 0.01)
        for row in run.trace_rows
    )


@pytest.mark.parametrize("governor_id", ("one_step", "jerk_qp"))
def test_constrained_governors_feed_exact_direct_profiles(
    governor_id: str,
) -> None:
    method = TrackingMethodSpec(
        method_id=f"test_{governor_id}",
        estimator=ComponentSpec(
            "local_poly",
            {"window": 5, "degree": 3, "lag_samples": 0},
        ),
        predictor=ComponentSpec("constant_jerk"),
        target_builder=ComponentSpec("pva"),
        governor=ComponentSpec(governor_id),
        follower=ComponentSpec("direct"),
    )
    run = run_tracking(_reference(), method, _config())
    assert run.status.completed
    assert run.profile_rows
    assert all(row["exact"] for row in run.profile_rows)


def test_estimator_reset_causality_startup_and_timestamp_policy() -> None:
    estimator = make_estimator(
        "local_poly",
        nominal_dt=0.01,
        allow_variable_dt=False,
        window=5,
        degree=3,
        lag_samples=1,
    )
    posteriors = []
    for index in range(5):
        time_s = index * 0.01
        posterior = estimator.update(
            EstimatorMeasurement(
                position=np.sin(time_s),
                state_time=time_s,
                available_time=time_s,
            )
        )
        assert posterior.state_time <= time_s
        posteriors.append(posterior)
    assert posteriors[0].startup
    assert not posteriors[-1].startup

    with pytest.raises(TimestampError):
        estimator.update(
            EstimatorMeasurement(
                position=0.0,
                state_time=0.04,
                available_time=0.05,
            )
        )

    estimator.reset()
    restarted = estimator.update(
        EstimatorMeasurement(
            position=1.0,
            state_time=1.0,
            available_time=1.0,
        )
    )
    assert restarted.startup
    assert estimator.sample_count == 1


def test_numerical_components_reject_more_than_one_axis() -> None:
    with pytest.raises(ValueError, match="only one axis"):
        NumericalMotionLimits.broadcast(2, 4.1, 8.2, 4000.0)
    with pytest.raises(ValueError, match="exactly one axis"):
        EstimatorMeasurement(
            position=[0.0, 1.0],
            state_time=0.0,
            available_time=0.0,
        )


def test_configured_limit_projection_preserves_position_and_admissibility() -> None:
    limits = NumericalMotionLimits.broadcast(1, 4.1, 8.2, 41.0)
    raw = np.asarray([[2.5, 9.0, -20.0]])
    projected, changed = configured_limit_project_target_state(raw, limits)

    assert changed
    assert projected[0, 0] == pytest.approx(raw[0, 0])
    assert projected[0, 2] == pytest.approx(-8.2)
    assert projected[0, 1] == pytest.approx(4.1 - 8.2**2 / (2.0 * 41.0))
    assert ruckig_target_admissible(projected, limits)

    mirrored, mirrored_changed = configured_limit_project_target_state(
        np.asarray([[2.5, -9.0, 20.0]]),
        limits,
    )
    assert mirrored_changed
    assert mirrored[0, 1] == pytest.approx(
        -4.1 + 8.2**2 / (2.0 * 41.0)
    )
    assert mirrored[0, 2] == pytest.approx(8.2)
    assert ruckig_target_admissible(mirrored, limits)

    admissible = np.asarray([[1.0, 0.5, -0.25]])
    unchanged, unchanged_flag = configured_limit_project_target_state(
        admissible,
        limits,
    )
    np.testing.assert_array_equal(unchanged, admissible)
    assert not unchanged_flag

    governor = ConfiguredLimitProjectionGovernor(0.01, limits)
    governor.reset(np.zeros((1, 3)))
    result = governor.update(raw, control_time=0.0)
    assert result.target_projected
    assert not result.requested_target_feasible
    assert result.solver_status == "configured_limit_projection:projected"
    np.testing.assert_allclose(result.executable_state, projected)
    np.testing.assert_allclose(result.distortion, projected - raw)
