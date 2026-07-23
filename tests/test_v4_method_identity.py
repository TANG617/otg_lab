from __future__ import annotations

import copy

import pytest

from otg_lab.followers import DirectExecutableFollower, RuckigFollower
from otg_lab.governors import MotionLimits, OneStepBoundedJerkGovernor
from otg_lab.predictors import OraclePredictor
from otg_lab.v4_methods import (
    ORACLE_METHOD_IDS,
    PRIMARY_METHOD_IDS,
    SECONDARY_METHOD_IDS,
    audit_oracle_rows,
    audit_primary_rows,
    audit_same_information_rows,
    build_v4_method_matrix,
    primary_purity_by_trajectory,
    validate_oracle_rows,
    validate_ordinary_rows,
    validate_primary_method_purity,
    validate_same_information_rows,
    validate_target_component_zeroing,
    validate_v4_method_matrix,
)


def _primary_row(method_id: str, *, k: int = 0) -> dict[str, object]:
    target_mode = {
        PRIMARY_METHOD_IDS[0]: "p",
        PRIMARY_METHOD_IDS[1]: "pv",
        PRIMARY_METHOD_IDS[2]: "pva",
    }[method_id]
    prediction_v = 0.4
    prediction_a = -0.3
    row: dict[str, object] = {
        "dataset_id": "synthetic-feasible-v4",
        "session_id": "session",
        "trajectory_id": "oscillatory__v4__test__000",
        "scenario_id": "clean",
        "joint_id": "joint_0",
        "k": k,
        "method_id": method_id,
        "split": "test",
        "seed": 123,
        "source_time": 0.01 * k,
        "arrival_time": 0.01 * k,
        "control_time": 0.01 * k,
        "dt_actual": 0.01,
        "dt_control": 0.01,
        "p_ref": 1.5,
        "v_ref_truth": 0.41,
        "a_ref_truth": -0.29,
        "j_ref_truth": 0.2,
        "p_meas": 1.49,
        "v_meas": None,
        "a_meas": None,
        "measurement_available": True,
        "measurement_valid": True,
        "invalid_input": False,
        "noise_realization": None,
        "quantization_error": None,
        "source_jitter_s": None,
        "transport_delay_s": 0.0,
        "event_dropped": False,
        "event_burst_drop": False,
        "event_held": False,
        "event_input_drop_count": 0,
        "event_arrivals_count": 1,
        "event_duplicate": False,
        "event_timestamp_regression": False,
        "event_future_source_time": False,
        "event_outlier": False,
        "outlier_kind": None,
        "outlier_realization": None,
        "event_nonfinite": False,
        "event_impossible_jump": False,
        "event_flags": "",
        "estimator_id": "local_poly",
        "posterior_p": 1.48,
        "posterior_v": 0.38,
        "posterior_a": -0.31,
        "posterior_state_time": 0.01 * k,
        "posterior_available_time": 0.01 * k,
        "posterior_axis_source_time": 0.01 * k,
        "posterior_axis_available_time": 0.01 * k,
        "measurement_sync_method": "causal_per_axis_latest_then_propagate",
        "predictor_id": "constant_jerk",
        "prediction_p": 1.5,
        "prediction_v": prediction_v,
        "prediction_a": prediction_a,
        "prediction_time": 0.01 * k,
        "prediction_horizon_ms": 0.0,
        "target_mode": target_mode,
        "raw_target_p": 1.5,
        "raw_target_v": 0.0 if target_mode == "p" else prediction_v,
        "raw_target_a": 0.0 if target_mode != "pva" else prediction_a,
        "raw_target_time": 0.01 * k,
        "governor_id": "one_step",
        "follower_id": "direct",
        "plant_id": "ideal",
        "limit_max_velocity": 4.1,
        "limit_max_acceleration": 8.2,
        "limit_max_jerk": 4000.0,
        "current_p": 1.0,
        "current_v": 0.0,
        "current_a": 0.0,
        "method_semantics": "direct_constant_jerk",
        "native_follower": "direct_executable",
        "actual_command_algorithm": "direct_executable",
        "native_command_executed": True,
        "safety_shield_requested": False,
        "safety_shield_applied": False,
        "fallback_requested": False,
        "fallback_applied": False,
        "fallback_changes_algorithm": False,
        "fallback_controller": "",
        "command_profile_kind": "constant_jerk",
        "command_profile_exact": True,
        "command_constant_jerk_exact": True,
        "command_endpoint_matches_profile": True,
        "command_profile_continuous_constraints_satisfied": True,
    }
    if k:
        row["current_p"] = {
            PRIMARY_METHOD_IDS[0]: 1.01,
            PRIMARY_METHOD_IDS[1]: 1.02,
            PRIMARY_METHOD_IDS[2]: 1.03,
        }[method_id]
    return row


def _primary_rows() -> list[dict[str, object]]:
    return [
        _primary_row(method_id, k=k) for k in (0, 1) for method_id in PRIMARY_METHOD_IDS
    ]


def test_matrix_contains_exact_v4_method_groups_and_primary_only_changes_target():
    matrix = build_v4_method_matrix()
    assert tuple(row["method_id"] for row in matrix["primary_methods"]) == (
        PRIMARY_METHOD_IDS
    )
    assert tuple(row["method_id"] for row in matrix["secondary_methods"]) == (
        SECONDARY_METHOD_IDS
    )
    assert tuple(row["method_id"] for row in matrix["oracle_methods"]) == (
        ORACLE_METHOD_IDS
    )

    pipelines = [row["pipeline"] for row in matrix["primary_methods"]]
    reference = {
        key: value for key, value in pipelines[0].items() if key != "target_mode"
    }
    assert [pipeline["target_mode"] for pipeline in pipelines] == ["p", "pv", "pva"]
    assert all(
        {key: value for key, value in pipeline.items() if key != "target_mode"}
        == reference
        for pipeline in pipelines[1:]
    )


def test_matrix_validator_rejects_any_other_primary_difference():
    matrix = build_v4_method_matrix()
    matrix["primary_methods"][1]["pipeline"]["governor_parameters"][
        "velocity_weight"
    ] = 0.5
    with pytest.raises(ValueError, match="only in target_mode"):
        validate_v4_method_matrix(matrix)


def test_matrix_maps_to_current_community_implementations():
    limits = MotionLimits.broadcast(1)
    assert OneStepBoundedJerkGovernor(1, 0.01, limits).name == ("one_step_bounded_jerk")
    assert DirectExecutableFollower(1, 0.01, limits).name == "direct_executable"
    assert (
        RuckigFollower(1, 0.01, limits, safety_shield=False).name
        == "ordinary_ruckig_unshielded"
    )
    assert OraclePredictor.causal is False
    assert OraclePredictor.offline_only is True


def test_primary_purity_requires_every_declared_identity_field():
    rows = _primary_rows()
    validate_primary_method_purity(rows)
    assert all(row["method_pure"] for row in audit_primary_rows(rows))
    assert all(
        row["method_purity_rate"] == 1.0 for row in primary_purity_by_trajectory(rows)
    )

    rows[0]["fallback_applied"] = True
    rows[0]["actual_command_algorithm"] = "one_step_bounded_jerk"
    rows[0]["native_command_executed"] = False
    with pytest.raises(ValueError, match="method-purity"):
        validate_primary_method_purity(rows)


def test_same_information_and_target_zeroing_allow_only_endogenous_divergence():
    rows = _primary_rows()
    validate_same_information_rows(rows)
    validate_target_component_zeroing(rows)
    assert all(
        row["same_information_passed"] for row in audit_same_information_rows(rows)
    )

    changed = copy.deepcopy(rows)
    changed[-1]["posterior_a"] = 99.0
    with pytest.raises(ValueError, match="posterior_a"):
        validate_same_information_rows(changed)

    changed = copy.deepcopy(rows)
    changed[1]["current_p"] = 99.0
    with pytest.raises(ValueError, match="current_p"):
        validate_same_information_rows(changed)


@pytest.mark.parametrize(
    ("method_id", "field", "value", "message"),
    [
        (PRIMARY_METHOD_IDS[0], "raw_target_v", 0.1, "p_target_v_zero"),
        (PRIMARY_METHOD_IDS[1], "raw_target_a", 0.1, "pv_target_a_zero"),
        (
            PRIMARY_METHOD_IDS[2],
            "raw_target_a",
            0.1,
            "pva_target_a_from_prediction",
        ),
    ],
)
def test_target_component_zeroing_fails_closed(method_id, field, value, message):
    rows = _primary_rows()
    row = next(row for row in rows if row["method_id"] == method_id and row["k"] == 0)
    row[field] = value
    with pytest.raises(ValueError, match=message):
        validate_target_component_zeroing(rows)


def test_same_information_rejects_incomplete_primary_triplet():
    rows = [
        row
        for row in _primary_rows()
        if not (row["method_id"] == PRIMARY_METHOD_IDS[2] and row["k"] == 1)
    ]
    with pytest.raises(ValueError, match="complete_primary_triplet"):
        validate_same_information_rows(rows)


def test_ordinary_matrix_and_rows_forbid_hidden_fallback():
    matrix = build_v4_method_matrix()
    for method in matrix["secondary_methods"]:
        assert method["pipeline"]["follower_parameters"]["safety_shield"] is False
        assert method["pipeline"]["fallback_policy"]["hidden_fallback_allowed"] is False
        assert (
            method["pipeline"]["fallback_policy"]["native_failure_disposition"]
            == "failed_unit"
        )

    row = _primary_row(PRIMARY_METHOD_IDS[0])
    row.update(
        method_id=SECONDARY_METHOD_IDS[1],
        method_semantics="ordinary_ruckig_unshielded",
        native_follower="ordinary_ruckig",
        actual_command_algorithm="ordinary_ruckig",
    )
    validate_ordinary_rows([row])
    row["safety_shield_applied"] = True
    row["fallback_changes_algorithm"] = True
    with pytest.raises(ValueError, match="shield or algorithm replacement"):
        validate_ordinary_rows([row])


def test_oracle_rows_are_noncausal_and_excluded_from_primary():
    matrix = build_v4_method_matrix()
    primary_ids = {row["method_id"] for row in matrix["primary_methods"]}
    for method in matrix["oracle_methods"]:
        assert method["causal"] is False
        assert method["offline_only"] is True
        assert method["deployable"] is False
        assert method["included_in_primary"] is False
        assert method["eligible_for_parameter_selection"] is False
        assert method["method_id"] not in primary_ids

    row = _primary_row(PRIMARY_METHOD_IDS[0])
    row.update(method_id=ORACLE_METHOD_IDS[0], predictor_id="oracle")
    validate_oracle_rows([row])
    audit = audit_oracle_rows([row])
    assert audit[0]["causal"] is False
    assert audit[0]["deployable"] is False
    assert audit[0]["included_in_primary"] is False

    row["predictor_id"] = "constant_jerk"
    with pytest.raises(ValueError, match="oracle identity"):
        validate_oracle_rows([row])
