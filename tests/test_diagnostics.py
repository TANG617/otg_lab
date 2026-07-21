"""Focused tests for strict formal post-run diagnostics."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from otg_lab.diagnostics import (
    DiagnosticValidationError,
    governor_invariant_summaries,
    real_replay_diagnostics,
    robustness_fault_events,
    robustness_recovery_summaries,
    synthetic_chirp_frequency_response,
    synthetic_frequency_response,
    synthetic_local_delay,
)
from otg_lab.schema import empty_sample


def _row(
    k: int,
    *,
    dt: float = 0.01,
    trajectory_id: str = "trajectory-001",
    truth: bool = False,
    p_ref: float = 0.0,
    v_ref: float = 0.0,
    a_ref: float = 0.0,
    j_ref: float = 0.0,
    command_p: float = 0.0,
    command_v: float = 0.0,
    command_a: float = 0.0,
    command_jerk: float = 0.0,
) -> dict:
    control_time = k * dt
    command_time = control_time + dt
    return empty_sample(
        run_id="run-001",
        dataset_id="diagnostic-data-v1",
        session_id="session-001",
        trajectory_id=trajectory_id,
        split="test",
        seed=17,
        joint_id="joint-0",
        k=k,
        method_id="locked-method",
        estimator_id="local_polynomial",
        predictor_id="constant_acceleration",
        target_mode="pva",
        governor_id="one_step",
        follower_id="direct",
        plant_id="ideal",
        source_time=control_time,
        arrival_time=control_time,
        control_time=control_time,
        dt_actual=dt,
        dt_control=dt,
        p_ref=p_ref,
        v_ref_truth=v_ref if truth else None,
        a_ref_truth=a_ref if truth else None,
        j_ref_truth=j_ref if truth else None,
        p_meas=p_ref,
        v_meas=None,
        a_meas=None,
        posterior_p=p_ref,
        posterior_v=v_ref,
        posterior_a=a_ref,
        posterior_state_time=control_time,
        posterior_available_time=control_time,
        prediction_p=p_ref,
        prediction_v=v_ref,
        prediction_a=a_ref,
        prediction_time=command_time,
        prediction_horizon_ms=dt * 1000.0,
        raw_target_p=command_p,
        raw_target_v=command_v,
        raw_target_a=command_a,
        raw_target_time=command_time,
        executable_target_p=command_p,
        executable_target_v=command_v,
        executable_target_a=command_a,
        executable_target_time=command_time,
        command_p=command_p,
        command_v=command_v,
        command_a=command_a,
        command_jerk=command_jerk,
        sampled_jerk=command_jerk,
        new_jerk=command_jerk,
        internal_trajectory_jerk=command_jerk,
        command_time=command_time,
        plant_p=command_p,
        plant_v=command_v,
        plant_a=command_a,
        target_feasible=True,
        target_projected=False,
        fallback=False,
        fallback_reason="",
        solver_status="solved",
        qp_iterations=0,
        deadline_miss=False,
        state_reset=False,
        invalid_input=False,
        free_trajectory_duration=0.5 * dt,
        estimator_compute_us=1.0,
        predictor_compute_us=1.0,
        governor_compute_us=1.0,
        follower_compute_us=1.0,
        plant_compute_us=1.0,
        total_compute_us=5.0,
        source_kind="synthetic" if truth else "real_csv",
        reference_family="diagnostic",
        scenario_id="clean",
        truth_available=truth,
        measurement_available=True,
        measurement_valid=True,
    )


def _assert_rectangular_finite(rows: list[dict]) -> None:
    assert rows
    keys = set(rows[0])
    for row in rows:
        assert set(row) == keys
        for value in row.values():
            assert value is not None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                assert math.isfinite(float(value))


def _governor_rows(count: int = 7, dt: float = 0.01) -> list[dict]:
    jerk = 2.0
    rows = []
    for k in range(count):
        target_time = (k + 1) * dt
        acceleration = jerk * target_time
        velocity = 0.5 * jerk * target_time**2
        position = jerk * target_time**3 / 6.0
        rows.append(
            _row(
                k,
                dt=dt,
                command_p=position,
                command_v=velocity,
                command_a=acceleration,
                command_jerk=jerk,
            )
        )
    return rows


def test_governor_invariants_are_exact_and_rates_are_explicit() -> None:
    rows = _governor_rows()
    rows[2]["fallback"] = True
    rows[2]["fallback_requested"] = True
    rows[2]["fallback_applied"] = True
    rows[2]["fallback_reason"] = "forced_test_fallback"
    rows[2]["free_trajectory_duration"] = None
    rows[4]["target_projected"] = True
    result = governor_invariant_summaries(
        rows,
        motion_limits={
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
    )
    _assert_rectangular_finite(result)
    summary = result[0]
    assert summary["adjacent_consistency_rate"] == pytest.approx(1.0)
    assert summary["adjacent_unexplained_inconsistent_count"] == 0
    assert summary["executable_point_admissible_rate"] == pytest.approx(1.0)
    assert summary["nonfallback_point_admissible_rate"] == pytest.approx(1.0)
    assert summary["nonfallback_sample_count"] == len(rows) - 1
    assert summary["nonfallback_one_step_reachable_rate"] == pytest.approx(1.0)
    assert summary["nonfallback_one_step_target_time_rate"] == pytest.approx(1.0)
    assert summary["nonfallback_one_step_invariant_rate"] == pytest.approx(1.0)
    assert summary["continuous_limit_violation_rate"] == pytest.approx(0.0)
    assert summary["nonfallback_sequence_rate_defined"] is True
    assert summary["nonfallback_sequence_consistency_rate"] == pytest.approx(1.0)
    assert summary["fallback_count"] == 1
    assert summary["projection_count"] == 1


def test_governor_diagnostics_count_all_qp_failure_categories_separately() -> None:
    rows = _governor_rows()
    failures = (
        "qp_time_limit_reached",
        "qp_max_iter_reached",
        "qp_primal_infeasible",
        "qp_dual_infeasible",
        "qp_numerical_failure",
        "qp_postcheck_failed",
    )
    for row in rows:
        row["qp_status_category"] = "qp_solved"
        row["qp_iterations"] = 10
    for row, category in zip(rows, failures):
        row["qp_status_category"] = category
        row["fallback"] = True
        row["fallback_requested"] = True
        row["fallback_applied"] = True
        row["fallback_reason"] = category
        row["free_trajectory_duration"] = None

    summary = governor_invariant_summaries(
        rows,
        motion_limits={
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
    )[0]

    for category in failures:
        assert summary[f"{category}_count"] == 1
        assert summary[f"{category}_rate"] == pytest.approx(1 / len(rows))
    assert summary["qp_solved_count"] == 1


def test_governor_invariant_flags_unexplained_break_and_missing_t_free() -> None:
    rows = _governor_rows()
    rows[3]["executable_target_p"] += 0.1
    rows[3]["command_p"] += 0.1
    rows[3]["plant_p"] += 0.1
    result = governor_invariant_summaries(
        rows,
        motion_limits={
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
    )[0]
    assert result["adjacent_unexplained_inconsistent_count"] >= 1
    assert result["continuous_invariant_or_limit_violation_count"] >= 1

    missing = _governor_rows()
    missing[1]["free_trajectory_duration"] = None
    with pytest.raises(DiagnosticValidationError, match="non-fallback"):
        governor_invariant_summaries(
            missing,
            motion_limits={
                "max_velocity": 4.1,
                "max_acceleration": 8.2,
                "max_jerk": 4000.0,
            },
        )


def test_governor_invariant_all_fallback_denominator_is_explicit() -> None:
    rows = _governor_rows()
    for row in rows:
        row["fallback"] = True
        row["fallback_requested"] = True
        row["fallback_applied"] = True
        row["fallback_reason"] = "deliberate_negative_suite"
        row["free_trajectory_duration"] = None
    summary = governor_invariant_summaries(
        rows,
        motion_limits={
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
    )[0]
    assert summary["nonfallback_sample_count"] == 0
    assert summary["nonfallback_rate_defined"] is False
    assert summary["nonfallback_one_step_reachable_count"] == 0
    assert summary["nonfallback_one_step_reachable_rate"] == 0.0
    assert summary["nonfallback_point_admissible_rate"] == 0.0
    assert summary["fallback_rate"] == 1.0


def _real_rows() -> list[dict]:
    rows = []
    for k in range(8):
        row = _row(
            k,
            p_ref=0.1 * k,
            command_p=0.1 * (k + 1),
            command_v=0.1 * k,
            command_a=0.02 * k,
            command_jerk=0.01 * k,
        )
        row["posterior_p"] = 0.1 * k - 0.01
        row["posterior_v"] = 0.12 * k
        row["posterior_a"] = 0.03 * k
        row["p_meas"] = 0.1 * k
        rows.append(row)

    rows[2]["source_time"] = rows[1]["source_time"]
    rows[2]["arrival_time"] = rows[1]["arrival_time"]
    rows[2]["event_held"] = True
    rows[3]["event_dropped"] = True
    rows[3]["event_input_drop_count"] = 1
    rows[3]["measurement_available"] = False
    rows[3]["measurement_valid"] = False
    rows[3]["p_meas"] = None
    rows[4]["event_outlier"] = True
    rows[4]["event_impossible_jump"] = True
    rows[4]["invalid_input"] = True
    rows[4]["measurement_valid"] = False
    rows[4]["p_meas"] = 99.0
    rows[5]["fallback"] = True
    rows[5]["fallback_requested"] = True
    rows[5]["fallback_applied"] = True
    rows[5]["fallback_reason"] = "forced_test_fallback"
    rows[6]["state_reset"] = True
    rows[6]["deadline_miss"] = True
    return rows


def test_real_replay_diagnostics_need_no_derivative_truth() -> None:
    result = real_replay_diagnostics(_real_rows())
    _assert_rectangular_finite(result)
    summary = result[0]
    assert summary["derivative_truth_used"] is False
    # Dropped and explicitly invalid measurements never enter innovations.
    assert summary["posterior_innovation_finite_count"] == 6
    assert summary["event_dropped_count"] == 1
    assert summary["event_input_drop_count"] == 1
    assert summary["event_held_count"] == 1
    assert summary["event_source_repeat_observed_count"] == 1
    assert summary["event_outlier_count"] == 1
    assert summary["event_impossible_jump_count"] == 1
    assert summary["state_reset_count"] == 1
    assert summary["fallback_count"] == 1
    assert summary["deadline_miss_count"] == 1
    assert summary["arrival_to_command_latency_s_max_abs"] > 0.0
    assert summary["command_jerk_smoothness_delta_count"] == 7


def _robustness_rows(*, recover: bool) -> list[dict]:
    error = [0.0, 0.01, 0.01, 0.5, 0.4, 0.2, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]
    if not recover:
        error[5:] = [0.2] * (len(error) - 5)
    rows = [
        _row(
            k,
            truth=True,
            p_ref=0.0,
            command_p=value,
            command_v=0.0,
            command_a=0.0,
            command_jerk=0.0,
        )
        for k, value in enumerate(error)
    ]
    rows[3]["source_time"] = rows[2]["source_time"]
    rows[3]["arrival_time"] = rows[2]["arrival_time"]
    rows[3]["event_held"] = True
    rows[4]["source_time"] = rows[3]["source_time"]
    rows[4]["arrival_time"] = rows[3]["arrival_time"]
    rows[4]["event_held"] = True
    return rows


def test_robustness_fault_recovery_and_censoring_are_explicit() -> None:
    rows = _robustness_rows(recover=True)
    events = robustness_fault_events(
        rows,
        output_field="plant_p",
        recovery_tolerance=0.05,
        recovery_hold_samples=2,
    )
    summaries = robustness_recovery_summaries(
        rows,
        output_field="plant_p",
        recovery_tolerance=0.05,
        recovery_hold_samples=2,
    )
    _assert_rectangular_finite(events)
    _assert_rectangular_finite(summaries)
    assert len(events) == 1
    assert events[0]["fault_kinds"] == "held"
    assert events[0]["fault_window_max_abs_error"] == pytest.approx(0.5)
    assert events[0]["recovered"] is True
    assert events[0]["recovery_time_censored"] is False
    assert events[0]["recovery_observed_time_s"] == pytest.approx(0.03)
    assert summaries[0]["fault_episode_count"] == 1
    assert summaries[0]["fault_recovery_censored_count"] == 0

    censored = robustness_recovery_summaries(
        _robustness_rows(recover=False),
        output_field="plant_p",
        recovery_tolerance=0.05,
        recovery_hold_samples=2,
    )[0]
    assert censored["fault_recovery_censored_count"] == 1
    assert censored["fault_recovery_complete_rate"] == pytest.approx(0.0)
    assert math.isfinite(censored["recovery_observed_max_s"])


def test_terminal_fault_is_retained_as_zero_window_right_censoring() -> None:
    rows = [_row(k, truth=True, p_ref=0.0, command_p=0.0) for k in range(8)]
    rows[-1]["source_time"] = rows[-2]["source_time"]
    rows[-1]["arrival_time"] = rows[-2]["arrival_time"]
    rows[-1]["event_held"] = True
    events = robustness_fault_events(
        rows,
        output_field="plant_p",
        recovery_tolerance=0.05,
        recovery_hold_samples=2,
    )
    assert len(events) == 1
    terminal = events[0]
    assert terminal["fault_clock"] == "control_time"
    assert terminal["fault_start_k"] == 7
    assert terminal["fault_window_evaluated_sample_count"] == 0
    assert terminal["fault_window_error_observed"] is False
    assert terminal["recovered"] is False
    assert terminal["recovery_time_censored"] is True
    assert terminal["recovery_observed_time_s"] == 0.0


def _frequency_rows() -> list[dict]:
    dt = 0.002
    frequencies = (2.0, 5.0)
    rows = []
    for k in range(1001):
        time = k * dt
        command_time = time + dt
        p_ref = sum(np.sin(2.0 * np.pi * f * time) for f in frequencies)
        v_ref = sum(
            2.0 * np.pi * f * np.cos(2.0 * np.pi * f * time) for f in frequencies
        )
        a_ref = sum(
            -((2.0 * np.pi * f) ** 2) * np.sin(2.0 * np.pi * f * time)
            for f in frequencies
        )
        j_ref = sum(
            -((2.0 * np.pi * f) ** 3) * np.cos(2.0 * np.pi * f * time)
            for f in frequencies
        )
        command = sum(np.sin(2.0 * np.pi * f * command_time) for f in frequencies)
        rows.append(
            _row(
                k,
                dt=dt,
                truth=True,
                p_ref=float(p_ref),
                v_ref=float(v_ref),
                a_ref=float(a_ref),
                j_ref=float(j_ref),
                command_p=float(command),
            )
        )
    return rows


def test_synthetic_frequency_wrapper_aligns_command_physical_time() -> None:
    rows = synthetic_frequency_response(_frequency_rows(), frequencies_hz=[2.0, 5.0])
    _assert_rectangular_finite(rows)
    assert len(rows) == 2
    for row in rows:
        assert row["gain"] == pytest.approx(1.0, abs=1e-12)
        assert row["phase_delay_s"] == pytest.approx(0.0, abs=1e-12)
        assert row["group_delay_s"] == pytest.approx(0.0, abs=1e-12)


def _chirp_rows() -> list[dict]:
    dt = 0.002
    duration = 4.0
    start_hz = 1.0
    end_hz = 5.0
    sweep_rate = (end_hz - start_hz) / duration
    alpha = 2.0 * np.pi * sweep_rate
    specification = json.dumps(
        {
            "kind": "linear_chirp",
            "start_hz": start_hz,
            "end_hz": end_hz,
            "duration_s": duration,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    rows = []
    for k in range(int(duration / dt) + 1):
        time = k * dt
        command_time = time + dt
        phase = 2.0 * np.pi * (
            start_hz * time + 0.5 * sweep_rate * time**2
        )
        angular_frequency = 2.0 * np.pi * (start_hz + sweep_rate * time)
        command_phase = 2.0 * np.pi * (
            start_hz * command_time + 0.5 * sweep_rate * command_time**2
        )
        row = _row(
            k,
            dt=dt,
            truth=True,
            trajectory_id="chirp-001",
            p_ref=float(np.sin(phase)),
            v_ref=float(np.cos(phase) * angular_frequency),
            a_ref=float(
                -np.sin(phase) * angular_frequency**2 + np.cos(phase) * alpha
            ),
            j_ref=float(
                -np.cos(phase) * angular_frequency**3
                - 3.0 * np.sin(phase) * angular_frequency * alpha
            ),
            command_p=float(np.sin(command_phase)),
        )
        row["reference_family"] = "oscillatory"
        row["reference_variant"] = "chirp"
        row["reference_frequency_spec_json"] = specification
        rows.append(row)
    return rows


def test_chirp_response_uses_predeclared_windows_and_explicit_denominators() -> None:
    rows = synthetic_chirp_frequency_response(
        _chirp_rows(),
        band_count=4,
        minimum_samples_per_band=20,
        max_local_lag_s=0.02,
    )
    _assert_rectangular_finite(rows)
    assert len(rows) == 4
    assert [row["frequency_band_index"] for row in rows] == [0, 1, 2, 3]
    assert [row["frequency_low_hz"] for row in rows] == pytest.approx(
        [1.0, 2.0, 3.0, 4.0]
    )
    for row in rows:
        assert row["gain"] == pytest.approx(1.0, abs=1e-12)
        assert row["phase_rad"] == pytest.approx(0.0, abs=1e-12)
        assert row["phase_delay_s"] == pytest.approx(0.0, abs=1e-12)
        assert row["group_delay_s"] == pytest.approx(0.0, abs=1e-12)
        assert row["local_delay_samples"] == 0
        assert row["local_delay_s"] == pytest.approx(0.0, abs=1e-12)
        assert row["window_truth_sample_denominator"] > 0
        assert row["evaluated_sample_count"] >= 20
        assert row["local_delay_overlap_denominator"] == row["evaluated_sample_count"]
        assert row["local_delay_overlap_count"] == row["evaluated_sample_count"]
        assert row["local_delay_candidate_count"] > 0
        assert row["reference_projection_magnitude"] > 0.0
        assert row["reference_projection_normalized"] > 0.0
        assert row["future_tail_excluded_sample_count"] == 1
        assert 0.0 < row["evaluated_time_coverage_fraction"] <= 1.0


def test_chirp_response_reports_known_local_delay_for_kind_chirp() -> None:
    rows = _chirp_rows()
    specification = json.loads(rows[0]["reference_frequency_spec_json"])
    specification["kind"] = "chirp"
    encoded = json.dumps(specification, sort_keys=True, separators=(",", ":"))
    for row in rows:
        # The command represents t + dt but contains the reference value at t:
        # an exact one-sample physical delay on this uniform test grid.
        row["command_p"] = row["p_ref"]
        row["reference_frequency_spec_json"] = encoded
    result = synthetic_chirp_frequency_response(
        rows,
        band_count=4,
        minimum_samples_per_band=20,
        max_local_lag_s=0.02,
    )
    for row in result:
        assert row["chirp_metadata_kind"] == "chirp"
        assert row["local_delay_samples"] == 1
        assert row["local_delay_s"] == pytest.approx(0.002, abs=1e-12)
        assert row["phase_delay_s"] == pytest.approx(0.002, abs=1e-4)


@pytest.mark.parametrize(
    "specification",
    [
        {"kind": "discrete_tones", "frequencies_hz": [1.0, 2.0]},
        {"kind": "chirp", "start_hz": 1.0, "end_hz": 5.0},
        {
            "kind": "chirp",
            "start_hz": 1.0,
            "end_hz": 5.0,
            "duration_s": 7.0,
        },
        {
            "kind": "chirp",
            "start_hz": 5.0,
            "end_hz": 1.0,
            "duration_s": 4.0,
        },
    ],
)
def test_chirp_response_rejects_wrong_persisted_metadata(
    specification: dict,
) -> None:
    rows = _chirp_rows()
    encoded = json.dumps(specification, sort_keys=True, separators=(",", ":"))
    for row in rows:
        row["reference_frequency_spec_json"] = encoded
    with pytest.raises(DiagnosticValidationError, match="chirp|metadata|duration"):
        synthetic_chirp_frequency_response(rows, band_count=4)


def test_chirp_response_rejects_inconsistent_metadata_and_future_timestamp() -> None:
    inconsistent = _chirp_rows()
    inconsistent[100]["reference_frequency_spec_json"] = json.dumps(
        {
            "kind": "chirp",
            "start_hz": 1.0,
            "end_hz": 4.0,
            "duration_s": 4.0,
        }
    )
    with pytest.raises(DiagnosticValidationError, match="one persisted"):
        synthetic_chirp_frequency_response(inconsistent, band_count=4)

    future = _chirp_rows()
    future[100]["command_time"] += 0.25
    with pytest.raises(DiagnosticValidationError, match="future command timestamp"):
        synthetic_chirp_frequency_response(future, band_count=4)


def _reversal_rows() -> list[dict]:
    dt = 0.01
    rows = []
    for k in range(201):
        time = k * dt
        command_time = time + dt
        p_ref = time if time < 1.0 else 2.0 - time
        v_ref = 1.0 if time < 1.0 else -1.0
        command_p = command_time if command_time < 1.0 else 2.0 - command_time
        rows.append(
            _row(
                k,
                dt=dt,
                truth=True,
                p_ref=p_ref,
                v_ref=v_ref,
                a_ref=0.0,
                j_ref=0.0,
                command_p=command_p,
                command_v=v_ref,
            )
        )
    return rows


def test_synthetic_local_delay_wrapper_uses_truth_defined_reversal() -> None:
    rows = synthetic_local_delay(
        _reversal_rows(),
        event_types=("reversal",),
        window_before_s=0.2,
        window_after_s=0.2,
        max_lag_s=0.05,
    )
    _assert_rectangular_finite(rows)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "reversal"
    assert rows[0]["lag_samples"] == 0
    assert rows[0]["lag_s"] == pytest.approx(0.0)
    assert rows[0]["lag_aligned_rmse"] == pytest.approx(0.0, abs=1e-15)


def test_frequency_and_local_delay_fail_closed_without_synthetic_truth() -> None:
    real_rows = _real_rows()
    with pytest.raises(DiagnosticValidationError, match="derivative truth"):
        synthetic_frequency_response(real_rows, frequencies_hz=[2.0, 5.0])
    with pytest.raises(DiagnosticValidationError, match="derivative truth"):
        synthetic_local_delay(real_rows)
