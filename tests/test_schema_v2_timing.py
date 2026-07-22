"""Schema-v2 feasibility and asynchronous multi-axis timing regressions."""

from __future__ import annotations

import copy

import pytest

from otg_lab.multidof import generate_multidof_truth, multidof_to_rows
from otg_lab.pipeline import PER_AXIS_CAUSAL_SYNC, synchronize_axis_posteriors
from otg_lab.runner import run_pipeline_rows
from otg_lab.schema import (
    FIELD_NAMES,
    SchemaValidationError,
    empty_sample,
    migrate_sample_v1_to_v2,
    recompute_sample_feasibility,
    validate_sample,
    validate_samples,
)
from otg_lab.types import TimedState


def _config() -> dict[str, object]:
    return {
        "seed": 31,
        "limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
        "control": {"dt": 0.01, "minimum_duration": 0.01},
        "pipeline": {
            "method_id": "schema-v2-async-test",
            "estimator": "raw_backward_difference",
            "estimator_parameters": {"timestamp_policy": "hold"},
            "predictor": "constant_acceleration",
            "predictor_parameters": {},
            "prediction_horizon_ms": 10.0,
            "target_mode": "pva",
            "governor": "one_step",
            "governor_parameters": {},
            "follower": "direct",
            "plant": "ideal",
            "plant_parameters": {},
            "measured_state_mode": "previous_command",
        },
    }


def _rows(cycles: int = 6) -> list[dict[str, object]]:
    truth = generate_multidof_truth(
        2,
        "different_frequency",
        seed=301,
        duration=(cycles - 1) * 0.01,
        internal_dt=0.001,
    )
    return multidof_to_rows(truth, sample_rate_hz=100.0, run_id="schema-v2-timing-test")


def _by_axis_and_k(rows: list[dict[str, object]]) -> dict[tuple[str, int], dict]:
    return {(str(row["joint_id"]), int(row["k"])): row for row in rows}


def test_per_axis_jitter_drop_duplicate_and_regression_remain_causal() -> None:
    rows = _rows()
    source = _by_axis_and_k(rows)
    # Independent jitter/delay: neither axis is relabelled with the newer time.
    source[("joint_0", 1)]["source_time"] = 0.006
    source[("joint_0", 1)]["arrival_time"] = 0.009
    source[("joint_1", 1)]["source_time"] = 0.009
    source[("joint_1", 1)]["arrival_time"] = 0.009
    # Independent drop: axis 0 must causally hold while axis 1 advances.
    dropped = source[("joint_0", 2)]
    dropped.update(
        p_meas=None,
        measurement_available=False,
        measurement_valid=False,
        event_dropped=True,
    )
    # Independent duplicate on axis 1.
    duplicate = source[("joint_1", 3)]
    duplicate["source_time"] = source[("joint_1", 2)]["source_time"]
    duplicate["event_duplicate"] = True
    # Independent regression on axis 0 is held by that estimator only.
    regression = source[("joint_0", 4)]
    regression["source_time"] = 0.025
    regression["event_timestamp_regression"] = True

    result = run_pipeline_rows(rows, _config())
    output = _by_axis_and_k(result.rows)

    assert output[("joint_0", 1)]["posterior_axis_source_time"] == pytest.approx(0.006)
    assert output[("joint_1", 1)]["posterior_axis_source_time"] == pytest.approx(0.009)
    assert output[("joint_0", 2)]["posterior_axis_source_time"] == pytest.approx(0.006)
    assert output[("joint_1", 3)]["posterior_axis_source_time"] == pytest.approx(
        float(source[("joint_1", 2)]["source_time"])
    )
    assert output[("joint_0", 4)]["posterior_axis_source_time"] == pytest.approx(
        float(source[("joint_0", 3)]["source_time"])
    )
    for row in result.rows:
        assert row["measurement_sync_method"] == PER_AXIS_CAUSAL_SYNC
        assert row["posterior_state_time"] == pytest.approx(row["control_time"])
        assert row["posterior_axis_source_time"] <= row["control_time"] + 1e-12
        assert row["posterior_axis_available_time"] <= row["control_time"] + 1e-12
    validate_samples(result.rows)


def test_future_source_clock_anomaly_is_rejected_not_consumed() -> None:
    rows = _rows(4)
    source = _by_axis_and_k(rows)
    anomaly = source[("joint_0", 2)]
    anomaly["source_time"] = 0.03
    anomaly["arrival_time"] = 0.02
    anomaly["transport_delay_s"] = None

    result = run_pipeline_rows(rows, _config())
    output = _by_axis_and_k(result.rows)
    rejected = output[("joint_0", 2)]
    assert rejected["event_future_source_time"] is True
    assert rejected["measurement_valid"] is False
    assert rejected["invalid_input"] is True
    assert "future_source_time_rejected" in rejected["event_flags"]
    assert rejected["posterior_axis_source_time"] < 0.03
    assert all(
        row["posterior_axis_source_time"] <= row["control_time"] + 1e-12
        for row in result.rows
    )
    validate_samples(result.rows)


def test_synchronizer_rejects_future_or_not_yet_available_axis() -> None:
    safe = TimedState([0.0], [1.0], [0.0], state_time=0.0, available_time=0.0)
    synchronized = synchronize_axis_posteriors([safe], control_time=0.01)
    assert synchronized.position[0] == pytest.approx(0.01)
    assert synchronized.state_time == pytest.approx(0.01)
    future = TimedState(
        [0.0],
        [0.0],
        [0.0],
        state_time=0.02,
        available_time=0.02,
    )
    with pytest.raises(ValueError, match="future"):
        synchronize_axis_posteriors([future], control_time=0.01)


def _auditable_v2_row() -> dict:
    dt = 0.1
    jerk = 1.0
    terminal = (jerk * dt**3 / 6.0, 0.5 * jerk * dt**2, jerk * dt)
    row = empty_sample(
        run_id="v2",
        dataset_id="v2",
        session_id="v2",
        trajectory_id="v2",
        split="development",
        seed=1,
        joint_id="joint_0",
        k=0,
        source_time=0.0,
        arrival_time=0.0,
        control_time=0.0,
        dt_actual=dt,
        dt_control=dt,
        p_ref=0.0,
        p_meas=0.0,
        raw_target_p=terminal[0],
        raw_target_v=terminal[1],
        raw_target_a=terminal[2],
        raw_target_time=dt,
        executable_target_p=terminal[0],
        executable_target_v=terminal[1],
        executable_target_a=terminal[2],
        executable_target_time=dt,
        executable_target_free_trajectory_duration=0.05,
        free_trajectory_duration=0.05,
        command_p=terminal[0],
        command_v=terminal[1],
        command_a=terminal[2],
        command_jerk=jerk,
        command_time=dt,
        current_p=0.0,
        current_v=0.0,
        current_a=0.0,
        limit_max_velocity=1.0,
        limit_max_acceleration=1.0,
        limit_max_jerk=1.0,
        command_max_abs_velocity=terminal[1],
        command_max_abs_acceleration=terminal[2],
        command_max_abs_jerk=jerk,
        target_projected=False,
        fallback_requested=False,
        fallback_applied=False,
        fallback=False,
        fallback_reason="",
        safety_guarantee=True,
        emergency_mode=False,
        source_kind="unit_test",
        scenario_id="clean",
        truth_available=False,
        measurement_available=True,
        measurement_valid=True,
    )
    row.update(recompute_sample_feasibility(row))
    row["target_feasible"] = row["raw_target_point_admissible"]
    return row


def test_all_feasibility_fields_recompute_from_sample_state() -> None:
    row = _auditable_v2_row()
    expected = recompute_sample_feasibility(row)
    assert all(value is True for value in expected.values())
    validate_sample(row)
    corrupted = copy.deepcopy(row)
    corrupted["command_segment_feasible"] = False
    with pytest.raises(
        SchemaValidationError, match="recomputed|verified command safety"
    ):
        validate_sample(corrupted)


def test_stopping_envelope_does_not_alias_discrete_next_step_viability() -> None:
    row = _auditable_v2_row()
    row.update(
        dt_control=0.01,
        command_p=1.9422009,
        command_v=-4.08496081,
        command_a=-8.2,
        limit_max_velocity=4.1,
        limit_max_acceleration=8.2,
        limit_max_jerk=4000.0,
    )

    recomputed = recompute_sample_feasibility(row)

    assert recomputed["command_stopping_viable"] is True
    assert recomputed["command_next_step_exists"] is False


@pytest.mark.parametrize(
    "field",
    (
        "command_segment_feasible",
        "command_stopping_viable",
        "command_next_step_exists",
        "command_continuous_constraints_satisfied",
    ),
)
def test_safety_guarantee_cannot_contradict_command_audit(field: str) -> None:
    row = _auditable_v2_row()
    row[field] = False
    with pytest.raises(SchemaValidationError, match="verified command safety"):
        validate_sample(row)


def test_v1_migration_preserves_ambiguous_value_but_recomputes_v2_meanings() -> None:
    v1 = _auditable_v2_row()
    v1_only = {
        "posterior_axis_source_time",
        "posterior_axis_available_time",
        "measurement_sync_method",
        "limit_max_velocity",
        "limit_max_acceleration",
        "limit_max_jerk",
        "current_p",
        "current_v",
        "current_a",
        "raw_target_point_admissible",
        "raw_target_ruckig_admissible",
        "executable_target_available",
        "executable_target_point_admissible",
        "executable_target_stopping_viable",
        "executable_target_segment_feasible",
        "executable_target_t_free_le_dt",
        "executable_target_free_trajectory_duration",
        "command_t_free_le_dt",
        "command_segment_feasible",
        "command_stopping_viable",
        "command_next_step_exists",
        "command_continuous_constraints_satisfied",
        "command_max_abs_velocity",
        "command_max_abs_acceleration",
        "command_max_abs_jerk",
        "fallback_requested",
        "fallback_applied",
        "safety_guarantee",
        "emergency_mode",
        "legacy_target_feasible_v1",
        "event_future_source_time",
    }
    legacy = {name: value for name, value in v1.items() if name not in v1_only}
    legacy["target_feasible"] = False  # Deliberately ambiguous historical value.
    migrated = migrate_sample_v1_to_v2(
        legacy,
        limits={"max_velocity": 1.0, "max_acceleration": 1.0, "max_jerk": 1.0},
    )
    assert set(migrated) == set(FIELD_NAMES)
    assert migrated["legacy_target_feasible_v1"] is False
    assert migrated["raw_target_point_admissible"] is True
    assert migrated["target_feasible"] is True
    validate_sample(migrated)
