from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pytest

from otg_lab.analysis import (
    ANALYSIS_ESTIMATE,
    AVAILABLE,
    UNAVAILABLE_INCOMPLETE_PAIR,
    AnalysisSpec,
    ComparisonSpec,
    MethodPair,
    MetricRow,
    MetricSet,
    MetricTable,
    analyze_reference,
    analyze_tracking,
    compare_methods,
    get_metric_spec,
    metric_registry,
)
from otg_lab.models import MotionLimits, TrackingRun, TrackingStatus, Trajectory


def _trajectory(
    position: np.ndarray,
    *,
    first_index: int = 0,
    first_time: float = 0.0,
    dt: float = 0.1,
    velocity: np.ndarray | None = None,
    acceleration: np.ndarray | None = None,
    jerk: np.ndarray | None = None,
) -> Trajectory:
    count = position.size
    return Trajectory(
        sample_index=np.arange(first_index, first_index + count),
        time_s=first_time + dt * np.arange(count),
        position_rad=position,
        velocity_rad_s=velocity,
        acceleration_rad_s2=acceleration,
        jerk_rad_s3=jerk,
        nominal_dt_s=dt,
    )


def _status(count: int) -> TrackingStatus:
    return TrackingStatus(
        completed=True,
        valid_cycles=count,
        total_cycles=count,
        method_fingerprint="test",
    )


def _row(table: MetricTable, metric_id: str) -> MetricRow:
    selected = table.select(metric_id=metric_id)
    assert len(selected) == 1
    return selected[0]


def test_metric_registry_is_versioned_and_read_only() -> None:
    registry = metric_registry()
    assert isinstance(registry, MappingProxyType)
    metric = get_metric_spec("position_rmse")
    assert metric.version == "otg.metric.v1"
    assert metric.alignment == "raw_time"
    assert get_metric_spec("lag_aligned_rmse").alignment == "lag_diagnostic"
    with pytest.raises(TypeError):
        registry["new"] = metric  # type: ignore[index]


def test_position_only_reference_uses_offline_second_order_derivatives() -> None:
    time = 0.1 * np.arange(7)
    reference = _trajectory(np.square(time))

    result = analyze_reference(
        reference,
        AnalysisSpec(input_id="quadratic", jump_threshold_rad=1.0),
    )

    assert reference.velocity_rad_s is None
    assert reference.acceleration_rad_s2 is None
    assert reference.jerk_rad_s3 is None
    np.testing.assert_allclose(
        result.derived_trajectory.velocity_rad_s,
        2.0 * time,
        rtol=0.0,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        result.derived_trajectory.acceleration_rad_s2,
        2.0,
        rtol=0.0,
        atol=2e-12,
    )
    np.testing.assert_allclose(
        result.derived_trajectory.jerk_rad_s3,
        0.0,
        rtol=0.0,
        atol=4e-11,
    )
    velocity_peak = _row(result.metrics, "reference_velocity_max_abs")
    assert velocity_peak.source_semantics == ANALYSIS_ESTIMATE
    assert velocity_peak.value == pytest.approx(1.2)
    assert result.derivative_semantics == ANALYSIS_ESTIMATE


def test_reference_truth_consistency_and_limit_metrics() -> None:
    time = 0.1 * np.arange(6)
    position = np.square(time)
    velocity = 2.0 * time
    acceleration = np.full(time.shape, 2.0)
    jerk = np.zeros(time.shape)
    reference = _trajectory(
        position,
        velocity=velocity,
        acceleration=acceleration,
        jerk=jerk,
    )
    limits = MotionLimits(
        max_velocity_rad_s=0.7,
        max_acceleration_rad_s2=3.0,
        max_jerk_rad_s3=1.0,
    )

    result = analyze_reference(
        reference,
        AnalysisSpec(input_id="truth", limits=limits),
    )

    assert result.metrics.value("position_velocity_consistency_rmse") == pytest.approx(
        0.0, abs=1e-12
    )
    assert result.metrics.value("velocity_acceleration_consistency_rmse") == (
        pytest.approx(0.0, abs=1e-12)
    )
    assert result.metrics.value("reference_velocity_violation_count") == 2
    assert result.metrics.value("reference_acceleration_limit_margin") == (
        pytest.approx(1.0)
    )


def test_tracking_raw_time_dynamics_runtime_fallback_and_profile_metrics() -> None:
    time = 0.1 * np.arange(5)
    reference = _trajectory(
        time,
        velocity=np.ones(5),
        acceleration=np.zeros(5),
        jerk=np.zeros(5),
    )
    command = _trajectory(
        time[1:] + 0.1,
        first_index=1,
        first_time=0.1,
        velocity=np.ones(4),
        acceleration=np.zeros(4),
        jerk=np.zeros(4),
    )
    trace_rows = []
    profile_rows = []
    for cycle in range(4):
        trace_rows.append(
            {
                "cycle_index": cycle,
                "posterior_time_s": float(time[cycle]),
                "posterior_position_rad": float(time[cycle]),
                "prediction_time_s": float(time[cycle + 1]),
                "prediction_position_rad": float(time[cycle + 1]),
                "raw_target_position_rad": float(time[cycle + 1]),
                "executable_target_position_rad": float(time[cycle + 1]),
                "command_start_velocity_rad_s": 1.0,
                "command_start_acceleration_rad_s2": 0.0,
                "fallback_applied": cycle == 2,
                "solver_status": "success",
                "runtime_estimator_us": 10.0,
                "runtime_predictor_us": 20.0,
                "runtime_target_builder_us": 5.0,
                "runtime_governor_us": 15.0,
                "runtime_follower_us": 50.0,
                "runtime_total_us": 100.0,
            }
        )
        profile_rows.append(
            {
                "profile_id": f"m:{cycle}",
                "cycle_index": cycle,
                "segment_index": 0,
                "start_time_s": float(time[cycle]),
                "end_time_s": float(time[cycle + 1]),
                "jerk_rad_s3": 0.0,
                "exact": True,
            }
        )
    run = TrackingRun(
        method_id="method",
        command=command,
        trace_rows=trace_rows,
        profile_rows=profile_rows,
        status=_status(4),
    )
    limits = MotionLimits(
        max_velocity_rad_s=2.0,
        max_acceleration_rad_s2=3.0,
        max_jerk_rad_s3=4.0,
    )
    requested = (
        "position_rmse",
        "position_mae",
        "position_bias",
        "position_p95_abs_error",
        "position_max_abs_error",
        "position_iae",
        "lag_s",
        "lag_aligned_rmse",
        "output_velocity_limit_margin",
        "fallback_count",
        "fallback_rate",
        "runtime_total_p95_s",
        "deadline_miss_count",
        "profile_exact_fraction",
        "profile_jerk_max_abs",
        "profile_constraint_violation_count",
        "posterior_position_rmse",
        "prediction_position_rmse",
        "target_position_distortion_rmse",
    )

    table = analyze_tracking(
        reference,
        run,
        MetricSet(metric_ids=requested, input_id="input", limits=limits),
    )

    assert table.value("position_rmse") == pytest.approx(0.1)
    assert table.value("position_mae") == pytest.approx(0.1)
    assert table.value("position_bias") == pytest.approx(0.1)
    assert table.value("position_p95_abs_error") == pytest.approx(0.1)
    assert table.value("position_max_abs_error") == pytest.approx(0.1)
    assert table.value("position_iae") == pytest.approx(0.03)
    assert table.value("output_velocity_limit_margin") == pytest.approx(1.0)
    assert table.value("fallback_count") == 1
    assert table.value("fallback_rate") == pytest.approx(0.25)
    assert table.value("runtime_total_p95_s") == pytest.approx(100e-6)
    assert table.value("deadline_miss_count") == 0
    assert table.value("profile_exact_fraction") == pytest.approx(1.0)
    assert table.value("profile_jerk_max_abs") == pytest.approx(0.0)
    assert table.value("profile_constraint_violation_count") == 0
    assert table.value("posterior_position_rmse") == pytest.approx(0.0)
    assert table.value("prediction_position_rmse") == pytest.approx(0.0)
    assert table.value("target_position_distortion_rmse") == pytest.approx(0.0)
    assert _row(table, "lag_s").notes.startswith("diagnostic only")


def test_missing_command_jerk_is_explicit_and_estimate_has_distinct_name() -> None:
    reference = _trajectory(np.array([0.0, 0.0, 0.0, 0.0]))
    command = _trajectory(
        np.array([0.0, 0.0, 0.0]),
        first_index=1,
        first_time=0.1,
        velocity=np.zeros(3),
        acceleration=np.array([0.0, 0.1, 0.2]),
    )
    run = TrackingRun(
        method_id="method",
        command=command,
        status=_status(3),
    )

    table = analyze_tracking(
        reference,
        run,
        (
            "output_jerk_rms",
            "sampled_jerk_estimate_rms",
        ),
    )

    truth_jerk = _row(table, "output_jerk_rms")
    assert truth_jerk.value is None
    assert truth_jerk.status == "unavailable_missing_command_jerk"
    estimate = _row(table, "sampled_jerk_estimate_rms")
    assert estimate.status == AVAILABLE
    assert estimate.source_semantics == ANALYSIS_ESTIMATE
    assert estimate.value == pytest.approx(1.0)


def _metric_table(method: str, values: dict[str, float | None]) -> MetricTable:
    rows = []
    metric = get_metric_spec("position_rmse")
    for input_id, value in values.items():
        rows.append(
            MetricRow(
                input_id=input_id,
                method_id=method,
                window_id="full_overlap",
                metric_id="position_rmse",
                value=value,
                unit=metric.unit,
                direction=metric.direction,
                status=AVAILABLE if value is not None else "unavailable_failed",
            )
        )
    return MetricTable(tuple(rows))


def test_compare_methods_requires_complete_pairs_and_bootstrap_is_deterministic() -> None:
    baseline = _metric_table("baseline", {"a": 2.0, "b": 4.0})
    candidate = _metric_table("candidate", {"a": 1.0, "b": 3.0})
    spec = ComparisonSpec(
        pairs=(MethodPair("baseline", "candidate"),),
        metric_ids=("position_rmse",),
        input_ids=("a", "b"),
        window_ids=("full_overlap",),
        bootstrap_seed=19,
        bootstrap_repetitions=100,
    )

    first = compare_methods([baseline, candidate], spec)
    second = compare_methods([baseline, candidate], spec)

    assert first == second
    row = first.rows[0]
    assert row.status == AVAILABLE
    assert row.paired_input_count == 2
    assert row.baseline_mean == pytest.approx(3.0)
    assert row.candidate_mean == pytest.approx(2.0)
    assert row.difference == pytest.approx(-1.0)
    assert row.improvement == pytest.approx(1.0)
    assert row.ci_lower == pytest.approx(-1.0)
    assert row.ci_upper == pytest.approx(-1.0)

    incomplete = compare_methods(
        [
            baseline,
            _metric_table("candidate", {"a": 1.0}),
        ],
        spec,
    )
    unavailable = incomplete.rows[0]
    assert unavailable.status == UNAVAILABLE_INCOMPLETE_PAIR
    assert unavailable.paired_input_count == 1
    assert unavailable.expected_input_count == 2
    assert unavailable.difference is None


def test_empty_command_returns_unavailable_rows_instead_of_raising() -> None:
    reference = _trajectory(np.array([0.0, 1.0, 2.0]))
    empty = Trajectory(
        sample_index=np.array([], dtype=np.int64),
        time_s=np.array([], dtype=float),
        position_rad=np.array([], dtype=float),
        nominal_dt_s=0.1,
    )
    run = TrackingRun(
        method_id="failed",
        command=empty,
        status=TrackingStatus(
            completed=False,
            failure_layer="follower",
            failure_reason="test failure",
            valid_cycles=0,
            total_cycles=2,
        ),
    )

    table = analyze_tracking(reference, run, ("position_rmse", "fallback_rate"))

    assert {row.status for row in table.rows} == {"unavailable_empty_command"}
    assert all(row.value is None for row in table.rows)
