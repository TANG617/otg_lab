from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from otg_lab.analysis import ComparisonSpec
from otg_lab.cli import load_experiment_spec
from otg_lab.trajectory_ablation import (
    BASELINE_METHOD_ID,
    CURRENT_ONLINE_BASELINE_CASE_ID,
    E01_INPUT_IDS,
    INPUT_IDS,
    RECORDED_BASELINE_INPUT_IDS,
)

ROOT = Path(__file__).resolve().parents[1]


def test_e01_is_the_standalone_scheduled_p_only_baseline() -> None:
    spec = load_experiment_spec(ROOT, "E01")

    assert spec.directory_name == "E01_p_only_baseline"
    assert spec.title == "E01 scheduled P-only trajectory baseline"
    assert tuple(item.input_id for item in spec.inputs) == E01_INPUT_IDS
    assert tuple(item.input_id for item in spec.inputs[-2:]) == (
        RECORDED_BASELINE_INPUT_IDS
    )
    assert spec.independent_variables == (
        "input_trajectory",
        "baseline_role",
        "runtime_limits",
    )
    assert spec.allowed_method_differences == ()
    assert spec.comparison_spec == ComparisonSpec()
    assert spec.artifact_writer is None

    assert len(spec.methods) == 1
    method = spec.methods[0]
    assert method.method_id == BASELINE_METHOD_ID
    assert method.estimator.component_id == "position_only"
    assert method.predictor.component_id == "zero_order_hold"
    assert method.target_builder.component_id == "scheduled_state"
    assert dict(method.target_builder.params) == {
        "components": "p",
        "time_source": "prediction_time",
    }
    assert method.governor.component_id == "none"
    assert method.follower.component_id == "ruckig"

    assert tuple(case.case_id for case in spec.cases) == (
        BASELINE_METHOD_ID,
        CURRENT_ONLINE_BASELINE_CASE_ID,
    )
    paired_case, current_online_case = spec.cases
    assert paired_case.method_id == BASELINE_METHOD_ID
    assert paired_case.run_config == spec.run_config
    assert current_online_case.method_id == BASELINE_METHOD_ID
    assert current_online_case.run_config.limits.max_velocity_rad_s == pytest.approx(
        4.2
    )
    assert (
        current_online_case.run_config.limits.max_acceleration_rad_s2
        == pytest.approx(8.2)
    )
    assert current_online_case.run_config.limits.max_jerk_rad_s3 == pytest.approx(
        41.0
    )

    config = spec.run_config
    assert config.dt_s == pytest.approx(0.01)
    assert config.prediction_horizon_s == pytest.approx(0.01)
    assert config.minimum_duration_s == pytest.approx(0.01)
    assert config.measurement_policy == "position_only"
    assert config.limits.max_velocity_rad_s == pytest.approx(4.1)
    assert config.limits.max_acceleration_rad_s2 == pytest.approx(8.2)
    assert config.limits.max_jerk_rad_s3 == pytest.approx(4000.0)
    assert (
        spec.controlled_variables[
            "scheduled_position_available_one_step_ahead"
        ]
        is True
    )
    assert (
        spec.controlled_variables["deployment_comparison_input"]
        == "recorded_tasks_simplified_with_velocity_limit"
    )
    assert (
        spec.controlled_variables["original_recorded_role"]
        == "current_online_p_only_report_baseline"
    )
    assert spec.controlled_variables["report_baseline"] == {
        "case_id": CURRENT_ONLINE_BASELINE_CASE_ID,
        "input_id": "recorded_tasks_original_no_velocity_limit",
        "target_components": "P",
        "max_velocity_rad_s": 4.2,
        "max_acceleration_rad_s2": 8.2,
        "max_jerk_rad_s3": 41.0,
        "role": "current_online_status_quo",
    }
    assert spec.controlled_variables["experimental_paired_baseline"] == {
        "case_id": BASELINE_METHOD_ID,
        "input_id": "recorded_tasks_simplified_with_velocity_limit",
        "max_velocity_rad_s": 4.1,
        "max_acceleration_rad_s2": 8.2,
        "max_jerk_rad_s3": 4000.0,
        "role": "unchanged_a04_a06_gain_denominator",
    }
    assert (
        spec.controlled_variables["analytic_role"]
        == "intermediate_method_correctness_only"
    )

    main = next(
        window for window in spec.windows if window.window_id == "main_evaluation"
    )
    assert main.start_time_s == pytest.approx(0.04)
    assert main.end_time_s is None
    assert spec.metric_roles["primary"] == ("position_rmse",)


@pytest.mark.parametrize("experiment_id", ("E03", "E04", "E05", "E06"))
def test_e01_matches_each_embedded_p_only_baseline(
    experiment_id: str,
) -> None:
    e01 = load_experiment_spec(ROOT, "E01")
    comparison = load_experiment_spec(ROOT, experiment_id)

    embedded = next(
        method
        for method in comparison.methods
        if method.method_id == BASELINE_METHOD_ID
    )
    assert embedded == e01.methods[0]
    assert comparison.run_config == e01.run_config
    assert comparison.inputs == e01.inputs[: len(INPUT_IDS)]
    assert tuple(item.input_id for item in comparison.inputs) == INPUT_IDS
    assert comparison.metric_roles == e01.metric_roles
    shared_e01_controls = {
        key: value
        for key, value in e01.controlled_variables.items()
        if key
        not in {
            "inputs",
            "deployment_comparison_input",
            "report_baseline",
            "experimental_paired_baseline",
            "original_recorded_role",
            "analytic_role",
        }
    }
    shared_comparison_controls = {
        key: value
        for key, value in comparison.controlled_variables.items()
        if key != "inputs"
    }
    assert shared_comparison_controls == shared_e01_controls


def test_method_differences_are_only_required_for_configured_comparisons() -> None:
    e01 = load_experiment_spec(ROOT, "E01")
    assert replace(e01, allowed_method_differences=()) == e01

    e03 = load_experiment_spec(ROOT, "E03")
    with pytest.raises(
        ValueError,
        match="allowed_method_differences must be declared",
    ):
        replace(e03, allowed_method_differences=())
