from __future__ import annotations

import numpy as np

from otg_lab.components import COMPONENT_REGISTRY, available_components
from otg_lab.estimators import PositionOnly
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    Trajectory,
)
from otg_lab.tracking import (
    PROFILE_FIELDS,
    TRACE_FIELDS,
    method_fingerprint,
    run_tracking,
)


def _reference(count: int = 8, dt: float = 0.01, *, truth: bool = True) -> Trajectory:
    time_s = np.arange(count, dtype=float) * dt
    position = 0.01 * np.sin(2.0 * time_s)
    if not truth:
        return Trajectory(
            sample_index=np.arange(count),
            time_s=time_s,
            position_rad=position,
        )
    velocity = 0.02 * np.cos(2.0 * time_s)
    acceleration = -0.04 * np.sin(2.0 * time_s)
    jerk = -0.08 * np.cos(2.0 * time_s)
    return Trajectory(
        sample_index=np.arange(count),
        time_s=time_s,
        position_rad=position,
        velocity_rad_s=velocity,
        acceleration_rad_s2=acceleration,
        jerk_rad_s3=jerk,
    )


def _config(**changes) -> RunConfig:
    values = {
        "limits": MotionLimits(
            max_velocity_rad_s=4.1,
            max_acceleration_rad_s2=8.2,
            max_jerk_rad_s3=4000.0,
        ),
        "minimum_duration_s": 0.01,
        "prediction_horizon_s": 0.01,
    }
    values.update(changes)
    return RunConfig(**values)


def _ruckig_method(
    *,
    estimator: ComponentSpec | None = None,
    follower: ComponentSpec | None = None,
) -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id="position_zoh_p_ruckig",
        estimator=estimator or ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=follower or ComponentSpec("ruckig"),
    )


def _direct_method() -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id="local_poly_cj_pva_direct",
        estimator=ComponentSpec(
            "local_poly",
            {"window": 5, "degree": 3, "lag_samples": 0},
        ),
        predictor=ComponentSpec("constant_jerk"),
        target_builder=ComponentSpec("pva"),
        governor=ComponentSpec("one_step"),
        follower=ComponentSpec("direct"),
    )


def test_registry_has_stable_e01_and_explicit_shield_ids() -> None:
    registry = available_components()
    assert set(COMPONENT_REGISTRY) == set(registry)
    assert "position_only" in registry["estimator"]
    assert "local_poly" in registry["estimator"]
    assert "zero_order_hold" in registry["predictor"]
    assert "constant_jerk" in registry["predictor"]
    assert {"p", "pv", "pva"} <= set(registry["target_builder"])
    assert "one_step" in registry["governor"]
    assert "direct" in registry["follower"]
    assert "ruckig" in registry["follower"]
    assert "ruckig_viability_shield" in registry["follower"]


def test_run_tracking_has_n_minus_one_commands_and_canonical_rows() -> None:
    reference = _reference()
    position_before = reference.position_rad.copy()
    result = run_tracking(reference, _ruckig_method(), _config())

    assert result.status.completed
    assert result.status.valid_cycles == reference.sample_count - 1
    assert result.command.sample_count == reference.sample_count - 1
    np.testing.assert_array_equal(result.command.sample_index, reference.sample_index[1:])
    np.testing.assert_allclose(result.command.time_s, reference.time_s[1:])
    assert len(result.trace_rows) == reference.sample_count - 1
    assert set(result.trace_rows[0]) == set(TRACE_FIELDS)
    assert all(
        row["command_time_s"] == reference.time_s[index + 1]
        for index, row in enumerate(result.trace_rows)
    )
    assert all(set(row) == set(PROFILE_FIELDS) for row in result.profile_rows)
    np.testing.assert_array_equal(reference.position_rad, position_before)


def test_position_only_policy_does_not_leak_reference_derivatives() -> None:
    result = run_tracking(_reference(truth=True), _ruckig_method(), _config())
    first = result.trace_rows[0]
    assert first["measurement_velocity_rad_s"] is None
    assert first["measurement_acceleration_rad_s2"] is None
    assert first["posterior_velocity_rad_s"] == 0.0
    assert first["posterior_acceleration_rad_s2"] == 0.0


def test_prediction_horizon_is_independent_and_time_explicit() -> None:
    config = _config(prediction_horizon_s=0.02, minimum_duration_s=0.03)
    result = run_tracking(_reference(), _ruckig_method(), config)
    assert result.status.completed
    first = result.trace_rows[0]
    assert first["prediction_horizon_s"] == 0.02
    assert first["prediction_time_s"] == 0.02
    assert first["raw_target_time_s"] == 0.02
    assert first["executable_target_time_s"] == 0.02
    assert first["command_time_s"] == 0.01


def test_one_step_direct_produces_exact_constant_jerk_profiles() -> None:
    result = run_tracking(_reference(), _direct_method(), _config())
    assert result.status.completed
    assert result.profile_rows
    assert all(row["exact"] for row in result.profile_rows)
    assert all(row["jerk_rad_s3"] is not None for row in result.profile_rows)
    assert result.command.jerk_rad_s3 is not None


def test_ruckig_exact_profile_tolerates_terminal_roundoff_at_threshold() -> None:
    count = 100
    time_s = np.arange(count, dtype=float) * 0.01
    critical_velocity = 0.012095
    reference = Trajectory(
        sample_index=np.arange(count),
        time_s=time_s,
        position_rad=critical_velocity * time_s,
        velocity_rad_s=np.full(count, critical_velocity),
        acceleration_rad_s2=np.zeros(count),
        jerk_rad_s3=np.zeros(count),
    )

    result = run_tracking(reference, _ruckig_method(), _config())

    assert result.status.completed
    assert result.profile_rows
    assert all(row["exact"] for row in result.profile_rows)
    assert all(row["jerk_rad_s3"] is not None for row in result.profile_rows)
    assert all(
        row["requested_target_free_duration_s"] is not None
        and row["frozen_trajectory_duration_s"] is not None
        for row in result.trace_rows
    )


class _FailOnSecondEstimator(PositionOnly):
    def __init__(self, dt_s: float) -> None:
        super().__init__(nominal_dt=dt_s, allow_variable_dt=False)
        self.calls = 0

    def update(self, measurement):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("intentional estimator failure")
        return super().update(measurement)


def test_custom_factory_and_partial_failure_preserve_raw_output() -> None:
    method = _ruckig_method(
        estimator=ComponentSpec(
            "test_fail_second",
            factory=lambda dt_s: _FailOnSecondEstimator(dt_s),
        )
    )
    result = run_tracking(_reference(), method, _config())

    assert not result.status.completed
    assert result.status.failure_layer == "estimator"
    assert "intentional estimator failure" in result.status.failure_reason
    assert result.status.valid_cycles == 1
    assert result.status.total_cycles == 7
    assert result.command.sample_count == 1
    assert len(result.trace_rows) == 2
    assert result.trace_rows[-1]["status"] == "failed"
    assert result.trace_rows[-1]["error_layer"] == "estimator"


def test_method_fingerprint_is_stable_and_parameter_sensitive() -> None:
    reference = _reference()
    config = _config()
    first = method_fingerprint(_ruckig_method(), config, dt_s=reference.dt)
    second = method_fingerprint(_ruckig_method(), config, dt_s=reference.dt)
    changed = method_fingerprint(
        _ruckig_method(
            follower=ComponentSpec("ruckig", {"audit_grid_dt": 0.0002})
        ),
        config,
        dt_s=reference.dt,
    )
    assert first == second
    assert len(first) == 64
    assert first != changed


def test_ordinary_ruckig_cannot_hide_a_viability_shield() -> None:
    result = run_tracking(
        _reference(),
        _ruckig_method(
            follower=ComponentSpec("ruckig", {"safety_shield": True})
        ),
        _config(),
    )
    assert not result.status.completed
    assert result.status.failure_layer == "follower"
    assert "stable follower ID" in result.status.failure_reason


def test_oracle_requires_explicit_noncausal_diagnostic_marker() -> None:
    base = _ruckig_method()
    unmarked = TrackingMethodSpec(
        method_id="oracle_unmarked",
        estimator=base.estimator,
        predictor=ComponentSpec("oracle"),
        target_builder=base.target_builder,
        governor=base.governor,
        follower=base.follower,
    )
    rejected = run_tracking(_reference(), unmarked, _config())
    assert not rejected.status.completed
    assert rejected.status.failure_layer == "predictor"
    assert "noncausal_diagnostic=True" in rejected.status.failure_reason

    marked = TrackingMethodSpec(
        method_id="oracle_marked",
        estimator=base.estimator,
        predictor=ComponentSpec(
            "oracle", {"noncausal_diagnostic": True}
        ),
        target_builder=base.target_builder,
        governor=base.governor,
        follower=base.follower,
    )
    accepted = run_tracking(_reference(), marked, _config())
    assert accepted.status.completed
