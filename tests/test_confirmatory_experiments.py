from __future__ import annotations

from pathlib import Path

import pytest

from otg_lab.confirmatory import (
    build_measurement_schedule,
    constant_velocity_trajectory,
    critical_reference_velocity,
    observer_pv_method,
    oracle_pv_method,
    scheduled_p_method,
    summarize_tracking,
    tracking_config,
)
from otg_lab.models import ComponentSpec
from otg_lab.tracking import run_tracking


def _probe():
    dt_s = 0.01
    acceleration = 20.0
    jerk = 4000.0
    critical = critical_reference_velocity(acceleration, jerk, dt_s)
    reference = constant_velocity_trajectory(0.5 * critical, dt_s)
    config = tracking_config(
        dt_s=dt_s,
        acceleration_rad_s2=acceleration,
        jerk_rad_s3=jerk,
    )
    return reference, config


def test_measurement_schedule_is_causal_and_exposes_held_samples() -> None:
    reference, _ = _probe()
    measurements = build_measurement_schedule(
        reference,
        timestamp_jitter_std_s=0.001,
        delay_cycles=2,
        dropout_rate=0.1,
        seed=17,
    )

    assert len(measurements) == reference.sample_count - 1
    assert all(
        item.state_time <= item.available_time <= reference.time_s[index] + 1e-12
        for index, item in enumerate(measurements)
    )
    assert any(bool(item.metadata["held"]) for item in measurements[1:])


def test_variable_timestamp_measurements_run_with_timestamp_aware_observer() -> None:
    reference, config = _probe()
    measurements = build_measurement_schedule(
        reference,
        timestamp_jitter_std_s=0.001,
        seed=23,
    )
    method = observer_pv_method(
        "pv_timestamp_aware",
        ComponentSpec(
            "alpha_beta_gamma",
            {"allow_variable_dt": True, "timestamp_policy": "hold"},
        ),
        ComponentSpec("constant_acceleration"),
    )

    tracking_run = run_tracking(
        reference,
        method,
        config,
        measurements=measurements,
    )

    assert tracking_run.status.completed
    assert all(
        row["measurement_available_time_s"] is not None
        for row in tracking_run.trace_rows
    )


def test_no_minimum_duration_extends_short_native_profile_with_hold() -> None:
    reference, config = _probe()
    method = scheduled_p_method(
        "p_without_minimum_duration",
        use_minimum_duration=False,
    )

    tracking_run = run_tracking(reference, method, config)

    assert tracking_run.status.completed
    assert any(
        "terminal_hold:true" in str(row["follower_status"])
        for row in tracking_run.trace_rows
    )
    assert all(
        float(row["end_time_s"]) - float(row["start_time_s"])
        <= reference.dt + 1e-12
        for row in tracking_run.profile_rows
    )


def test_minimum_speed_metrics_distinguish_p_only_from_oracle_pv() -> None:
    reference, config = _probe()
    p_run = run_tracking(reference, scheduled_p_method(), config)
    pv_run = run_tracking(reference, oracle_pv_method(), config)

    p_metrics = summarize_tracking(
        reference,
        p_run,
        config.limits,
        start_time_s=0.2,
        end_time_s=0.8,
        input_id="p",
    )
    pv_metrics = summarize_tracking(
        reference,
        pv_run,
        config.limits,
        start_time_s=0.2,
        end_time_s=0.8,
        input_id="pv",
    )

    assert p_metrics["profile_min_abs_velocity_to_reference_p05"] == pytest.approx(0.0)
    assert p_metrics["profile_near_zero_cycle_fraction"] == pytest.approx(1.0)
    assert pv_metrics["profile_min_abs_velocity_to_reference_p05"] == pytest.approx(1.0)
    assert pv_metrics["profile_near_zero_cycle_fraction"] == pytest.approx(0.0)


def test_confirmatory_directories_are_present() -> None:
    root = Path(__file__).resolve().parents[1]
    for experiment in (
        "E15_dimensionless_stop_go_boundary",
        "E16_velocity_causal_ablation",
        "E17_causal_pv_robustness_holdout",
        "E18_pv_future_o1_recorded_replay_consistency",
        "E19_pv_future_o1_amax_sensitivity",
        "E20_pv_future_o1_acceleration_conditioning",
    ):
        assert (root / "experiments" / experiment / "experiment.py").is_file()
        assert (root / "experiments" / experiment / "README.md").is_file()
