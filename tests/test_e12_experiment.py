from __future__ import annotations

from pathlib import Path

import pytest

from otg_lab.cli import load_experiment_spec
from otg_lab.csvio import (
    load_trajectory_csv,
    load_trajectory_metadata,
    sha256_file,
)
from otg_lab.governors import MotionLimits as NumericalMotionLimits
from otg_lab.recorded_experiments import projection_audit
from otg_lab.tracking import run_tracking

ROOT = Path(__file__).resolve().parents[1]
INPUT_IDS = (
    "recorded_tasks_original_no_velocity_limit",
    "recorded_tasks_simplified_no_velocity_limit",
    "recorded_tasks_simplified_with_velocity_limit",
)
METHOD_IDS = (
    "p_kp1_baseline",
    "pva_est_backward_o1_k",
    "pva_est_backward_o2_k",
    "pva_est_centered_o2_km1",
    "pva_pred_backward_o1_kp1",
    "pva_pred_backward_o2_kp1",
)


def test_e12_declares_orthogonal_input_and_runtime_vmax_factors() -> None:
    spec = load_experiment_spec(ROOT, "E12")

    assert spec.directory_name == "E12_pva_recorded_vmax_ablation"
    assert tuple(item.input_id for item in spec.inputs) == INPUT_IDS
    assert tuple(method.method_id for method in spec.methods) == METHOD_IDS
    assert len(spec.cases) == 12
    assert len(spec.cases) * len(spec.inputs) == 36
    assert len(spec.comparison_spec.pairs) == 10
    assert {
        case.run_config.limits.max_velocity_rad_s for case in spec.cases
    } == {4.1, 10.0}
    assert {
        case.run_config.limits.max_acceleration_rad_s2 for case in spec.cases
    } == {8.2}
    assert {case.run_config.limits.max_jerk_rad_s3 for case in spec.cases} == {
        4000.0
    }
    assert all(
        method.governor.component_id == "configured_limit_projection"
        for method in spec.methods
    )


def test_simplified_unlimited_input_is_canonical_and_hash_linked() -> None:
    raw = ROOT / "data/raw/recorded_tasks/simplified_no_velocity_limit.csv"
    canonical = (
        ROOT
        / "data/trajectories/recorded_tasks_simplified_no_velocity_limit.csv"
    )
    trajectory = load_trajectory_csv(canonical, require_metadata=True)
    metadata = load_trajectory_metadata(canonical)

    assert sha256_file(raw) == (
        "777c9a4f0bcbffb52bfb3bd5d866e132bf2149a9db7835ccf59254825f564fa3"
    )
    assert sha256_file(canonical) == (
        "a4e400fb660dc04d63af57898e1b190bc173b8cb5d9692a1f61645c458cc4d97"
    )
    assert metadata.source_sha256 == sha256_file(raw)
    assert metadata.csv_sha256 == sha256_file(canonical)
    assert metadata.source["path"] == (
        "data/raw/recorded_tasks/simplified_no_velocity_limit.csv"
    )
    assert trajectory.sample_count == 1275
    assert trajectory.dt == pytest.approx(0.01)
    assert trajectory.velocity_rad_s is None


@pytest.mark.parametrize("vmax", (4.1, 10.0))
def test_e12_projection_decomposition_reconstructs_execution(vmax: float) -> None:
    spec = load_experiment_spec(ROOT, "E12")
    input_spec = next(
        item
        for item in spec.inputs
        if item.input_id == "recorded_tasks_simplified_no_velocity_limit"
    )
    csv_path, metadata_path = input_spec.resolve(ROOT)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    case = next(
        case
        for case in spec.cases
        if case.method_id == "pva_pred_backward_o2_kp1"
        and case.run_config.limits.max_velocity_rad_s == vmax
    )
    run = run_tracking(reference, spec.method_for_case(case), case.run_config)
    limits = NumericalMotionLimits.broadcast(1, vmax, 8.2, 4000.0)
    audit = projection_audit(run, limits)

    assert run.status.completed
    assert audit["projection_reconstruction_mismatch_count"] == 0
    assert audit["executable_target_inadmissible_count"] == 0
    assert audit["position_projection_max_abs_rad"] == pytest.approx(0.0)
    assert audit["projection_count"] >= audit["velocity_clip_count"]
    assert audit["projection_count"] >= audit["acceleration_clip_count"]
    assert audit["projection_count"] >= audit["stopping_envelope_count"]
