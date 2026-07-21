"""Focused evidence tests for multi-DoF geometry and plant feedback semantics."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
import pytest

import otg_lab.runner as runner_module
from otg_lab.governors import MotionLimits
from otg_lab.multidof import (
    compute_multidof_tracking_diagnostics,
    generate_multidof_truth,
    multidof_to_rows,
)
from otg_lab.plants import DelayedServoPlant
from otg_lab.runner import run_pipeline_rows
from otg_lab.schema import FIELD_BY_NAME, empty_sample, validate_samples
from otg_lab.types import TimedState


def _pipeline_config(
    *,
    dof: int,
    plant: str = "delayed_servo",
    measured_state_mode: str = "measured",
    follower: str = "direct",
) -> dict[str, object]:
    del dof  # The runner infers DoF from the canonical long-form joint set.
    return {
        "seed": 17,
        "limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
        "control": {"dt": 0.01, "minimum_duration": 0.01},
        "pipeline": {
            "method_id": f"test::{plant}::{measured_state_mode}::{follower}",
            "estimator": "position_only",
            "estimator_parameters": {},
            "predictor": "zero_order_hold",
            "predictor_parameters": {},
            "prediction_horizon_ms": 10.0,
            "target_mode": "p",
            "governor": "one_step",
            "governor_parameters": {"divergence_threshold": 1e-6},
            "follower": follower,
            "plant": plant,
            "plant_parameters": {
                "bandwidth_hz": 4.0,
                "damping_ratio": 0.7,
                "delay_s": 0.02,
                "position_noise_sigma": 0.0,
                "velocity_noise_sigma": 0.0,
                "acceleration_noise_sigma": 0.0,
                "substeps": 10,
                "seed": 17,
            }
            if plant == "delayed_servo"
            else {},
            "measured_state_mode": measured_state_mode,
        },
    }


def _short_multidof_rows(dof: int = 3) -> list[dict[str, object]]:
    truth = generate_multidof_truth(
        dof,
        "different_frequency",
        seed=91,
        duration=0.12,
        internal_dt=0.001,
    )
    return multidof_to_rows(truth, sample_rate_hz=100.0, run_id="evidence-test")


def test_schema_additions_have_explicit_availability_and_safe_defaults() -> None:
    expected_availability = {
        "plant_measured_p": "when_plant_measurement_available",
        "plant_saturated": "when_plant_enabled",
        "plant_command_age_s": "when_plant_enabled",
        "plant_delay_s": "when_plant_enabled",
        "plant_status": "when_plant_enabled",
        "command_measured_divergence": "when_feedback_state_comparison_available",
        "feedback_correction_p": "after_replanning_state_selection",
    }
    for field, availability in expected_availability.items():
        assert FIELD_BY_NAME[field].availability == availability

    row = empty_sample()
    assert row["feedback_correction"] is False
    assert row["event_command_measured_divergence"] is False
    assert row["plant_saturated"] is None


def test_emergency_stop_keeps_command_provenance_and_exposes_divergence() -> None:
    dt = 0.01
    limits = MotionLimits.broadcast(1, 4.1, 8.2, 4000.0)
    initial = np.array([[0.0, 1.0, 0.0]])
    emergency_stop = np.zeros((1, 3))
    plant = DelayedServoPlant(
        1,
        dt,
        limits,
        bandwidth_hz=4.0,
        damping_ratio=0.7,
        delay_s=0.02,
        substeps=10,
        seed=4,
    )
    plant.reset(initial, state_time=0.0)

    plant.update(emergency_stop, command_time=0.01)
    result = plant.update(emergency_stop, command_time=0.02)

    assert result.command_source_time == pytest.approx(0.0)
    assert result.delayed_command_age == pytest.approx(0.02)
    assert result.configured_delay_s == pytest.approx(0.02)
    assert result.status in {"ok", "saturated"}
    assert np.max(np.abs(result.measured_state - emergency_stop)) > 0.5


def test_measured_feedback_is_correction_not_state_reset() -> None:
    rows = _short_multidof_rows(3)
    result = run_pipeline_rows(rows, _pipeline_config(dof=3))

    assert len(result.rows) == len(rows)
    assert any(row["feedback_correction"] for row in result.rows)
    assert any(row["event_command_measured_divergence"] for row in result.rows)
    assert not any(row["state_reset"] for row in result.rows)
    assert all(row["plant_command_age_s"] >= 0.0 for row in result.rows)
    assert all(row["plant_delay_s"] == pytest.approx(0.02) for row in result.rows)
    assert all(row["plant_status"] in {"ok", "saturated"} for row in result.rows)
    assert all(row["plant_measured_p"] is not None for row in result.rows)
    assert all(
        ("feedback_correction" in row["event_flags"])
        == bool(row["feedback_correction"])
        for row in result.rows
    )
    validate_samples(result.rows)


def test_real_estimator_reset_remains_separate_from_feedback() -> None:
    rows = _short_multidof_rows(1)
    regressing = copy.deepcopy(rows)
    regressing[3]["source_time"] = float(regressing[2]["source_time"]) - 0.001
    regressing[3]["transport_delay_s"] = (
        float(regressing[3]["arrival_time"]) - float(regressing[3]["source_time"])
    )
    regressing[3]["event_timestamp_regression"] = True
    config = _pipeline_config(
        dof=1,
        plant="ideal",
        measured_state_mode="previous_command",
    )
    config["pipeline"]["estimator_parameters"] = {"timestamp_policy": "reset"}
    result = run_pipeline_rows(regressing, config)

    reset_rows = [row for row in result.rows if row["state_reset"]]
    assert [row["k"] for row in reset_rows] == [3]
    assert "state_reset" in reset_rows[0]["event_flags"]
    assert not any(row["feedback_correction"] for row in result.rows)


def test_ndof_diagnostics_preserve_joint_inputs_and_audit_synchronization() -> None:
    rows = _short_multidof_rows(3)
    by_joint_and_k = {
        (str(row["joint_id"]), int(row["k"])): row for row in rows
    }
    maximum_k = max(int(row["k"]) for row in rows)
    for row in rows:
        k = int(row["k"])
        next_row = by_joint_and_k[(str(row["joint_id"]), min(k + 1, maximum_k))]
        row["method_id"] = "synchronized-perfect"
        row["command_time"] = float(row["control_time"]) + 0.01
        row["command_p"] = float(next_row["p_ref"])

    perfect = compute_multidof_tracking_diagnostics(rows)
    assert len(perfect.per_joint) == 3
    assert len(perfect.aligned_samples) == 3 * maximum_k
    assert all(record["joint_id"] for record in perfect.aligned_samples)
    assert perfect.summary[0]["dof"] == 3
    assert perfect.summary[0]["command_time_spread_max_s"] == pytest.approx(0.0)
    assert perfect.summary[0]["geometric_path_error_max"] < 1e-14
    assert perfect.summary[0]["synchronization_cross_track_error_max"] < 1e-14

    perturbed = copy.deepcopy(rows)
    for row in perturbed:
        if row["joint_id"] == "joint_2" and row["k"] == 0:
            row["command_time"] += 0.001
            row["command_p"] += 0.02
    diagnostic = compute_multidof_tracking_diagnostics(perturbed)
    assert diagnostic.summary[0]["command_time_spread_max_s"] == pytest.approx(0.001)
    assert diagnostic.summary[0]["geometric_path_error_max"] > 0.01
    assert diagnostic.summary[0]["synchronization_cross_track_error_max"] > 0.0


def test_runner_ndof_output_is_directly_diagnosable() -> None:
    result = run_pipeline_rows(
        _short_multidof_rows(3),
        _pipeline_config(
            dof=3,
            plant="ideal",
            measured_state_mode="previous_command",
            follower="ruckig",
        ),
    )
    diagnostic = compute_multidof_tracking_diagnostics(result.rows)

    assert diagnostic.summary[0]["dof"] == 3
    assert diagnostic.summary[0]["joint_count"] == 3
    assert diagnostic.summary[0]["command_time_spread_max_s"] == pytest.approx(0.0)
    assert len(diagnostic.per_joint) == 3
    assert all(record["sample_count"] > 0 for record in diagnostic.per_joint)


def test_qp_runner_accounts_for_every_predictor_horizon(monkeypatch) -> None:
    class FixedCostPredictor:
        def predict(self, posterior: TimedState, horizon: float) -> TimedState:
            return TimedState(
                position=posterior.position,
                velocity=posterior.velocity,
                acceleration=posterior.acceleration,
                state_time=posterior.state_time + float(horizon),
                available_time=posterior.available_time,
                method="fixed_cost",
                compute_time_us=7.0,
                source_state_time=posterior.state_time,
                prediction_horizon=float(horizon),
            )

        def predict_sequence(self, posterior, horizons):
            return [self.predict(posterior, horizon) for horizon in horizons]

    class FourStepGovernor:
        horizon_steps = 4

        def reset(self, state=None):
            return None

        def update(self, targets, *, control_time, current_state=None):
            target = np.asarray(targets, dtype=float)[0]
            return SimpleNamespace(
                executable_state=target,
                target_time=control_time + 0.01,
                compute_us=2.0,
                fallback=False,
                fallback_reason="",
                solver_status="solved",
                iterations=1,
            )

    monkeypatch.setattr(
        runner_module, "_build_predictor", lambda config, rows: FixedCostPredictor()
    )
    monkeypatch.setattr(
        runner_module,
        "_build_governor",
        lambda config, dof, dt, limits: FourStepGovernor(),
    )
    config = _pipeline_config(
        dof=1,
        plant="ideal",
        measured_state_mode="previous_command",
    )
    config["pipeline"]["governor"] = "jerk_qp"
    config["pipeline"]["governor_parameters"] = {"horizon_steps": 4}

    result = run_pipeline_rows(_short_multidof_rows(1), config)

    assert all(row["predictor_compute_us"] == pytest.approx(28.0) for row in result.rows)
