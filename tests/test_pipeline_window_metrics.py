from __future__ import annotations

import numpy as np
import pytest

from otg_lab.analysis import EvaluationWindow, MetricSet, analyze_tracking
from otg_lab.models import TrackingRun, TrackingStatus, Trajectory


def test_pipeline_truth_metrics_are_windowed_and_exclude_startup() -> None:
    times = 0.1 * np.arange(6)
    reference = Trajectory(
        sample_index=np.arange(6),
        time_s=times,
        position_rad=np.square(times),
        velocity_rad_s=2.0 * times,
        acceleration_rad_s2=np.full(6, 2.0),
        jerk_rad_s3=np.zeros(6),
        nominal_dt_s=0.1,
    )
    command = Trajectory(
        sample_index=np.arange(1, 6),
        time_s=times[1:],
        position_rad=np.square(times[1:]),
        velocity_rad_s=2.0 * times[1:],
        acceleration_rad_s2=np.full(5, 2.0),
        jerk_rad_s3=np.zeros(5),
        nominal_dt_s=0.1,
    )
    rows = []
    for cycle in range(5):
        target_time = times[cycle + 1]
        startup = cycle < 2
        rows.append(
            {
                "cycle_index": cycle,
                "raw_target_time_s": target_time,
                "raw_target_startup": startup,
                "raw_target_age_samples": 0.0,
                "prediction_time_s": target_time,
                "prediction_velocity_rad_s": (
                    100.0 if startup else 2.0 * target_time
                ),
                "prediction_acceleration_rad_s2": 2.0,
                "prediction_position_rad": target_time**2,
                "prediction_startup": startup,
                "posterior_time_s": times[cycle],
                "posterior_velocity_rad_s": 2.0 * times[cycle],
                "posterior_acceleration_rad_s2": 2.0,
                "posterior_position_rad": times[cycle] ** 2,
                "posterior_startup": startup,
                "raw_target_position_rad": target_time**2,
                "executable_target_position_rad": target_time**2,
                "raw_target_velocity_rad_s": (
                    100.0 if startup else 2.0 * target_time
                ),
                "executable_target_velocity_rad_s": (
                    100.0 if startup else 2.0 * target_time
                ),
                "raw_target_acceleration_rad_s2": 2.0,
                "executable_target_acceleration_rad_s2": 2.0,
            }
        )
    run = TrackingRun(
        method_id="method",
        command=command,
        trace_rows=rows,
        status=TrackingStatus(
            completed=True,
            valid_cycles=5,
            total_cycles=5,
            method_fingerprint="test",
        ),
    )
    table = analyze_tracking(
        reference,
        run,
        MetricSet(
            metric_ids=(
                "raw_target_velocity_rmse",
                "raw_target_acceleration_rmse",
                "prediction_velocity_rmse",
                "raw_target_age_samples_mean",
            ),
            windows=(
                EvaluationWindow("full_overlap"),
                EvaluationWindow(
                    "main_evaluation",
                    start_time_s=0.3,
                    end_time_s=0.5,
                ),
            ),
        ),
    )
    for window_id in ("full_overlap", "main_evaluation"):
        assert table.value(
            "raw_target_velocity_rmse",
            window_id=window_id,
        ) == pytest.approx(0.0)
        assert table.value(
            "raw_target_acceleration_rmse",
            window_id=window_id,
        ) == pytest.approx(0.0)
        assert table.value(
            "prediction_velocity_rmse",
            window_id=window_id,
        ) == pytest.approx(0.0)
        assert table.value(
            "raw_target_age_samples_mean",
            window_id=window_id,
        ) == pytest.approx(0.0)
