from __future__ import annotations

from pathlib import Path

import pytest

from otg_lab.cli import load_experiment_spec
from otg_lab.csvio import load_trajectory_csv
from otg_lab.models import Trajectory
from otg_lab.tracking import run_tracking
from otg_lab.trajectory_ablation import BASELINE_METHOD_ID

ROOT = Path(__file__).resolve().parents[1]
INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
METHOD_IDS = (
    "pv_est_backward_o1_k",
    "pv_est_backward_o2_k",
    "pv_est_centered_o2_km1",
    "pv_pred_backward_o1_kp1",
    "pv_pred_backward_o2_kp1",
)


def test_e11_declares_projected_e06_recorded_transfer_matrix() -> None:
    spec = load_experiment_spec(ROOT, "E11")
    e06 = load_experiment_spec(ROOT, "E06")

    assert spec.directory_name == "E11_pv_finite_difference_recorded_tracking"
    assert tuple(item.input_id for item in spec.inputs) == (INPUT_ID,)
    assert spec.inputs[0].required
    assert spec.inputs[0].csv_path == Path(f"data/trajectories/{INPUT_ID}.csv")

    methods = {method.method_id: method for method in spec.methods}
    assert set(methods) == {BASELINE_METHOD_ID, *METHOD_IDS}
    assert "pv_truth_kp1" not in methods
    assert all(method.required for method in spec.methods)
    assert all(method.predictor.component_id != "oracle" for method in spec.methods)

    e06_methods = {method.method_id: method for method in e06.methods}
    for method_id, method in methods.items():
        e06_method = e06_methods[method_id]
        for component_name in (
            "estimator",
            "predictor",
            "target_builder",
            "follower",
        ):
            assert (
                getattr(method, component_name).as_dict()
                == getattr(e06_method, component_name).as_dict()
            )
        assert method.governor.component_id == "configured_limit_projection"
        assert e06_method.governor.component_id == "none"

    assert methods[BASELINE_METHOD_ID].target_builder.params["components"] == "p"
    assert all(
        methods[method_id].target_builder.params["components"] == "pv"
        for method_id in METHOD_IDS
    )
    assert spec.controlled_variables["target_acceleration_policy"] == (
        "zero_by_target_builder"
    )
    assert spec.run_config.dt_s == pytest.approx(0.01)
    assert spec.run_config.prediction_horizon_s == pytest.approx(0.01)
    assert spec.run_config.minimum_duration_s == pytest.approx(0.01)
    assert spec.run_config.limits.max_velocity_rad_s == pytest.approx(4.1)
    assert spec.run_config.limits.max_acceleration_rad_s2 == pytest.approx(8.2)
    assert spec.run_config.limits.max_jerk_rad_s3 == pytest.approx(4000.0)

    windows = {window.window_id: window for window in spec.windows}
    assert windows["main_evaluation"].start_time_s == pytest.approx(0.04)
    assert windows["main_evaluation"].end_time_s is None
    assert len(spec.comparison_spec.pairs) == 5
    assert {pair.candidate_method_id for pair in spec.comparison_spec.pairs} == set(
        METHOD_IDS
    )
    assert spec.artifact_writer is not None


def test_e11_targets_have_no_acceleration_component() -> None:
    spec = load_experiment_spec(ROOT, "E11")
    input_spec = spec.inputs[0]
    csv_path, metadata_path = input_spec.resolve(ROOT)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    prefix_count = 512
    short_reference = Trajectory(
        sample_index=reference.sample_index[:prefix_count],
        time_s=reference.time_s[:prefix_count],
        position_rad=reference.position_rad[:prefix_count],
        nominal_dt_s=reference.dt,
    )

    for method in spec.methods:
        run = run_tracking(short_reference, method, spec.run_config)
        assert run.status.completed
        assert run.status.valid_cycles == prefix_count - 1
        assert all(
            float(row["raw_target_acceleration_rad_s2"]) == pytest.approx(0.0)
            and float(row["executable_target_acceleration_rad_s2"])
            == pytest.approx(0.0)
            for row in run.trace_rows
            if row["status"] == "ok"
        )
