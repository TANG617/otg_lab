from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from otg_lab.cli import load_experiment_spec
from otg_lab.csvio import (
    load_trajectory_csv,
    load_trajectory_metadata,
    sha256_file,
)
from otg_lab.experiment import run_experiment
from otg_lab.trajectory_ablation import BASELINE_METHOD_ID

ROOT = Path(__file__).resolve().parents[1]
INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
RAW_PATH = ROOT / "data/raw/recorded_tasks/simplified_with_velocity_limit.csv"
CANONICAL_PATH = ROOT / f"data/trajectories/{INPUT_ID}.csv"
RAW_SHA256 = "7ca0480de2259ea033d5fa36197bf1ece2af67de88f82c78ab8114cb30afe7de"
CANONICAL_SHA256 = "2de870625e4dfe8304f959a6d4a589b00e24275caa84f3fa83d5ca33c2beb68d"

EXPECTED_FIRST_PROJECTION_CYCLES = {
    "pva_est_backward_o1_k": 309,
    "pva_est_backward_o2_k": 150,
    "pva_est_centered_o2_km1": 309,
    "pva_pred_backward_o1_kp1": 309,
    "pva_pred_backward_o2_kp1": 17,
}
EXPECTED_PROJECTION_COUNTS = {
    "pva_est_backward_o1_k": 64,
    "pva_est_backward_o2_k": 229,
    "pva_est_centered_o2_km1": 64,
    "pva_pred_backward_o1_kp1": 64,
    "pva_pred_backward_o2_kp1": 633,
}


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_e08_declares_projected_e04_recorded_transfer_matrix() -> None:
    spec = load_experiment_spec(ROOT, "E08")
    e04 = load_experiment_spec(ROOT, "E04")

    assert spec.directory_name == "E08_pva_finite_difference_recorded_tracking"
    assert tuple(item.input_id for item in spec.inputs) == (INPUT_ID,)
    assert spec.inputs[0].required
    assert spec.inputs[0].csv_path == Path(f"data/trajectories/{INPUT_ID}.csv")

    methods = {method.method_id: method for method in spec.methods}
    assert set(methods) == {
        BASELINE_METHOD_ID,
        *EXPECTED_FIRST_PROJECTION_CYCLES,
    }
    assert all(method.required for method in spec.methods)
    assert all(method.predictor.component_id != "oracle" for method in spec.methods)

    e04_methods = {method.method_id: method for method in e04.methods}
    for method_id, method in methods.items():
        e04_method = e04_methods[method_id]
        for component_name in (
            "estimator",
            "predictor",
            "target_builder",
            "follower",
        ):
            assert (
                getattr(method, component_name).as_dict()
                == getattr(
                    e04_method,
                    component_name,
                ).as_dict()
            )
        assert method.governor.component_id == ("configured_limit_projection")
        assert method.governor.factory is None
        assert e04_method.governor.component_id == "none"

    assert spec.run_config.dt_s == pytest.approx(0.01)
    assert spec.run_config.prediction_horizon_s == pytest.approx(0.01)
    assert spec.run_config.minimum_duration_s == pytest.approx(0.01)
    assert spec.run_config.measurement_policy == "position_only"
    assert spec.run_config.limits.max_velocity_rad_s == pytest.approx(4.1)
    assert spec.run_config.limits.max_acceleration_rad_s2 == pytest.approx(8.2)
    assert spec.run_config.limits.max_jerk_rad_s3 == pytest.approx(4000.0)
    assert not spec.input_gate.block_on_limit_violation

    windows = {window.window_id: window for window in spec.windows}
    assert windows["main_evaluation"].start_time_s == pytest.approx(0.04)
    assert windows["main_evaluation"].end_time_s is None
    assert len(spec.comparison_spec.pairs) == 5
    assert {pair.candidate_method_id for pair in spec.comparison_spec.pairs} == set(
        EXPECTED_FIRST_PROJECTION_CYCLES
    )
    assert {pair.baseline_method_id for pair in spec.comparison_spec.pairs} == {
        BASELINE_METHOD_ID
    }
    assert "pva_truth_kp1" not in methods
    assert "posterior_velocity_rmse" not in spec.metric_ids
    assert "raw_target_velocity_rmse" not in spec.metric_ids
    assert spec.artifact_writer is not None


def test_e08_input_is_hash_linked_fixed_grid_position_only() -> None:
    trajectory = load_trajectory_csv(CANONICAL_PATH, require_metadata=True)
    metadata = load_trajectory_metadata(CANONICAL_PATH)

    assert sha256_file(RAW_PATH) == RAW_SHA256
    assert sha256_file(CANONICAL_PATH) == CANONICAL_SHA256
    assert metadata.source_sha256 == RAW_SHA256
    assert metadata.csv_sha256 == CANONICAL_SHA256
    assert metadata.source["path"] == (
        "data/raw/recorded_tasks/simplified_with_velocity_limit.csv"
    )
    assert metadata.source["value_column"] == "value"
    assert metadata.source["other_columns_ignored"] is True

    assert trajectory.sample_count == 7673
    assert trajectory.dt == pytest.approx(0.01)
    assert trajectory.duration_s == pytest.approx(76.72)
    assert trajectory.velocity_rad_s is None
    assert trajectory.acceleration_rad_s2 is None
    assert trajectory.jerk_rad_s3 is None

    raw_values = np.asarray(
        [float(row["value"]) for row in _csv_rows(RAW_PATH)],
        dtype=float,
    )
    np.testing.assert_array_equal(trajectory.position_rad, raw_values)


def test_e08_run_projects_targets_and_completes_full_comparisons(
    tmp_path: Path,
) -> None:
    spec = load_experiment_spec(ROOT, "E08")
    result = run_experiment(
        spec,
        project_root=ROOT,
        runs_root=tmp_path / "runs",
        create_figures=True,
    )

    assert result.success
    assert result.failure_count == 0
    assert result.required_failure_count == 0
    run_directory = result.run_directory

    manifest = json.loads((run_directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["failure_count"] == 0
    assert manifest["required_failure_count"] == 0
    baseline_manifest = manifest["methods"][BASELINE_METHOD_ID][INPUT_ID]
    assert baseline_manifest["completed"] is True
    assert baseline_manifest["valid_cycles"] == 7672
    assert baseline_manifest["total_cycles"] == 7672

    for method_id in EXPECTED_FIRST_PROJECTION_CYCLES:
        status = json.loads(
            (
                run_directory / "methods" / method_id / INPUT_ID / "status.json"
            ).read_text(encoding="utf-8")
        )
        assert status["completed"] is True
        assert status["valid_cycles"] == 7672
        assert status["total_cycles"] == 7672
        assert status["failure_layer"] is None
        assert status["failure_reason"] is None

    analysis = run_directory / "analysis"
    acceptance = {
        row["method_id"]: row for row in _csv_rows(analysis / "acceptance.csv")
    }
    assert acceptance[BASELINE_METHOD_ID]["position_rmse_rad"]
    assert acceptance[BASELINE_METHOD_ID]["overall_pass"] == "true"
    assert int(acceptance[BASELINE_METHOD_ID]["projection_count"]) == 0
    for method_id, projection_cycle in EXPECTED_FIRST_PROJECTION_CYCLES.items():
        row = acceptance[method_id]
        assert row["completed"] == "true"
        assert row["failure_cycle_index"] == ""
        assert int(row["first_projection_cycle_index"]) == projection_cycle
        assert int(row["projection_count"]) == EXPECTED_PROJECTION_COUNTS[method_id]
        assert row["position_rmse_rad"]
        assert row["rmse_ratio_vs_p"]
        assert row["prefix_rmse_used"] == "false"
        assert row["scientific_status"] in {
            "transfer_pass",
            "complete_but_no_rmse_improvement",
            "complete_but_guardrail_regression",
        }

    comparisons = _csv_rows(analysis / "comparisons.csv")
    assert comparisons
    position_rmse_comparisons = [
        row for row in comparisons if row["metric_id"] == "position_rmse"
    ]
    assert len(position_rmse_comparisons) == 10
    assert {row["status"] for row in position_rmse_comparisons} == {"available"}

    feasibility = {
        row["method_id"]: row
        for row in _csv_rows(analysis / "raw_target_feasibility.csv")
    }
    assert (
        int(feasibility["pva_est_backward_o1_k"]["acceleration_limit_violation_count"])
        == 64
    )
    assert (
        int(feasibility["pva_est_backward_o2_k"]["first_inadmissible_cycle_index"])
        == 150
    )
    assert (
        int(feasibility["pva_pred_backward_o1_kp1"]["velocity_limit_violation_count"])
        == 0
    )
    assert (
        int(feasibility["pva_pred_backward_o2_kp1"]["velocity_limit_violation_count"])
        == 0
    )
    assert (
        float(
            feasibility["pva_pred_backward_o2_kp1"][
                "target_acceleration_max_abs_rad_s2"
            ]
        )
        > 500.0
    )

    scan = _csv_rows(analysis / "raw_target_scan.csv")
    assert len(scan) == 6 * 7672
    assert {row["method_id"] for row in scan} == {
        BASELINE_METHOD_ID,
        *EXPECTED_FIRST_PROJECTION_CYCLES,
    }
    summary = (analysis / "acceptance_summary.md").read_text(encoding="utf-8")
    assert "Experiment execution: `complete`" in summary
    assert "Scientific transfer: `fail`" in summary
    assert "projected into the configured Ruckig-admissible limits" in summary

    figures = analysis / "figures"
    for stem in (
        "recorded_position_tracking",
        "raw_target_feasibility",
    ):
        assert (figures / f"{stem}.png").is_file()
        assert (figures / f"{stem}.svg").is_file()
