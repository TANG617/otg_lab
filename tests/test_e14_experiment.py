from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from otg_lab.cli import load_experiment_spec
from otg_lab.constraints import ruckig_target_admissible
from otg_lab.csvio import load_trajectory_csv
from otg_lab.experiment import run_experiment
from otg_lab.governors import MotionLimits as NumericalMotionLimits
from otg_lab.tracking import run_tracking

ROOT = Path(__file__).resolve().parents[1]


def test_e14_declares_fine_three_dimensional_pv_pva_grid() -> None:
    spec = load_experiment_spec(ROOT, "E14")

    assert spec.directory_name == "E14_pv_pva_vaj_fine_sensitivity"
    assert tuple(method.method_id for method in spec.methods) == (
        "pv_pred_backward_o1_kp1",
        "pva_pred_backward_o1_kp1",
    )
    assert not any(method.required for method in spec.methods)
    assert len(spec.inputs) == 1
    assert len(spec.cases) == 1280
    assert {
        case.run_config.limits.max_velocity_rad_s for case in spec.cases
    } == {0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.1}
    assert {
        case.run_config.limits.max_acceleration_rad_s2 for case in spec.cases
    } == {2.0, 3.0, 4.1, 5.0, 6.0, 7.0, 7.5, 8.2}
    assert {case.run_config.limits.max_jerk_rad_s3 for case in spec.cases} == {
        41.0,
        100.0,
        200.0,
        400.0,
        800.0,
        1200.0,
        1600.0,
        2400.0,
        3200.0,
        4000.0,
    }
    assert spec.artifact_writer is not None


@pytest.mark.parametrize(
    ("method_id", "velocity", "acceleration", "jerk"),
    (
        ("pv_pred_backward_o1_kp1", 0.5, 2.0, 41.0),
        ("pva_pred_backward_o1_kp1", 4.1, 8.2, 4000.0),
    ),
)
def test_e14_representative_boundary_and_vendor_cases_complete(
    method_id: str,
    velocity: float,
    acceleration: float,
    jerk: float,
) -> None:
    spec = load_experiment_spec(ROOT, "E14")
    input_spec = spec.inputs[0]
    csv_path, metadata_path = input_spec.resolve(ROOT)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    case = next(
        case
        for case in spec.cases
        if case.method_id == method_id
        and case.run_config.limits.max_velocity_rad_s == velocity
        and case.run_config.limits.max_acceleration_rad_s2 == acceleration
        and case.run_config.limits.max_jerk_rad_s3 == jerk
    )
    run = run_tracking(reference, spec.method_for_case(case), case.run_config)
    limits = NumericalMotionLimits.broadcast(1, velocity, acceleration, jerk)

    assert run.status.completed
    assert all(
        ruckig_target_admissible(
            [
                float(row["executable_target_position_rad"]),
                float(row["executable_target_velocity_rad_s"]),
                float(row["executable_target_acceleration_rad_s2"]),
            ],
            limits,
        )
        for row in run.trace_rows
        if row.get("executable_target_position_rad") is not None
    )


def test_e14_artifact_writer_accepts_a_bounded_memory_shard(
    tmp_path: Path,
) -> None:
    full = load_experiment_spec(ROOT, "E14")
    shard = replace(full, cases=(full.cases[0],))
    result = run_experiment(
        shard,
        project_root=ROOT,
        runs_root=tmp_path / "runs",
        create_figures=False,
    )

    assert result.success
    surface = result.run_directory / "analysis" / "vaj_sensitivity.csv"
    assert surface.is_file()
    assert len(surface.read_text(encoding="utf-8").splitlines()) == 2
