from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIRECTORY = (
    ROOT / "experiments/E08_pva_finite_difference_recorded_tracking"
)
sys.path.insert(0, str(DASHBOARD_DIRECTORY))

from dashboard_data import (  # noqa: E402
    BASELINE_METHOD_ID,
    INPUT_ID,
    METHOD_ORDER,
    PVA_METHODS,
    load_dashboard_data,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _build_fixture_run(tmp_path: Path) -> Path:
    run_directory = tmp_path / "20260729T010203.000000Z__dashboard"
    reference_rows = [
        {
            "sample_index": sample_index,
            "time_s": sample_index * 0.01,
            "position_rad": position,
        }
        for sample_index, position in enumerate((0.0, 0.1, 0.2))
    ]
    _write_csv(
        run_directory / "inputs" / INPUT_ID / "reference.csv",
        reference_rows,
    )

    acceptance_rows: list[dict[str, object]] = []
    feasibility_rows: list[dict[str, object]] = []
    for method_rank, method_id in enumerate(METHOD_ORDER):
        method_directory = run_directory / "methods" / method_id / INPUT_ID
        command_rows = [
            {
                "sample_index": sample_index,
                "time_s": sample_index * 0.01,
                "position_rad": sample_index * 0.1 + method_rank * 0.001,
            }
            for sample_index in (1, 2)
        ]
        _write_csv(method_directory / "command.csv", command_rows)
        if method_id != BASELINE_METHOD_ID:
            _write_csv(
                method_directory / "trace.csv",
                [
                    {
                        "cycle_index": 0,
                        "measurement_time_s": 0.0,
                        "command_time_s": 0.01,
                        "raw_target_position_rad": 0.1,
                        "raw_target_velocity_rad_s": 0.2,
                        "raw_target_acceleration_rad_s2": 0.3,
                        "executable_target_position_rad": 0.1,
                        "executable_target_velocity_rad_s": 0.2,
                        "executable_target_acceleration_rad_s2": 0.3,
                    },
                    {
                        "cycle_index": 1,
                        "measurement_time_s": 0.01,
                        "command_time_s": 0.02,
                        "raw_target_position_rad": 0.2,
                        "raw_target_velocity_rad_s": 0.4,
                        "raw_target_acceleration_rad_s2": 9.0 + method_rank,
                        "executable_target_position_rad": 0.2,
                        "executable_target_velocity_rad_s": 0.4,
                        "executable_target_acceleration_rad_s2": 8.2,
                    },
                ],
            )

        ratio = "" if method_id == BASELINE_METHOD_ID else 1.0 + method_rank / 10
        acceptance_rows.append(
            {
                "method_id": method_id,
                "completed": "true",
                "valid_cycles": 2,
                "total_cycles": 2,
                "position_rmse_rad": 0.01 + method_rank * 0.001,
                "rmse_ratio_vs_p": ratio,
                "projection_count": 0 if method_id == BASELINE_METHOD_ID else 1,
                "projection_rate": 0 if method_id == BASELINE_METHOD_ID else 0.5,
                "first_projection_cycle_index": (
                    "" if method_id == BASELINE_METHOD_ID else 1
                ),
                "guardrail_pass": "true",
                "scientific_status": (
                    "baseline_complete"
                    if method_id == BASELINE_METHOD_ID
                    else "complete_but_no_rmse_improvement"
                ),
            }
        )
        feasibility_rows.append(
            {
                "method_id": method_id,
                "target_velocity_max_abs_rad_s": 0.4,
                "target_acceleration_max_abs_rad_s2": (
                    0 if method_id == BASELINE_METHOD_ID else 9.0 + method_rank
                ),
                "target_acceleration_p95_abs_rad_s2": (
                    0 if method_id == BASELINE_METHOD_ID else 8.5
                ),
                "velocity_limit_violation_count": 0,
                "acceleration_limit_violation_count": (
                    0 if method_id == BASELINE_METHOD_ID else 1
                ),
                "ruckig_inadmissible_count": (
                    0 if method_id == BASELINE_METHOD_ID else 1
                ),
                "first_inadmissible_cycle_index": (
                    "" if method_id == BASELINE_METHOD_ID else 1
                ),
            }
        )

    _write_csv(run_directory / "analysis/acceptance.csv", acceptance_rows)
    _write_csv(
        run_directory / "analysis/raw_target_feasibility.csv",
        feasibility_rows,
    )
    return run_directory


def test_dashboard_data_preserves_full_series_and_exact_projection_events(
    tmp_path: Path,
) -> None:
    data = load_dashboard_data(_build_fixture_run(tmp_path))

    assert data.generated_at == "2026-07-29T01:02:03Z"
    assert len(data.reference_series) == 3
    assert len(data.position_series) == 3 + 6 * 2
    assert len(data.error_series) == 6 * 2
    assert len(data.target_audit) == 5 * 2
    assert len(data.projection_events) == 5
    assert {row["method_id"] for row in data.projection_events} == set(PVA_METHODS)
    assert {row["cycle_index"] for row in data.projection_events} == {1}
    assert {row["trigger"] for row in data.projection_events} == {
        "acceleration limit"
    }

    event = data.projection_events[0]
    assert event["command_position_rad"] == pytest.approx(0.201)
    assert event["reference_position_rad"] == pytest.approx(0.2)
    assert event["position_error_rad"] == pytest.approx(0.001)
    assert event["acceleration_projection_rad_s2"] < 0
    assert data.overview_metrics == {
        "duration_s": pytest.approx(0.02),
        "tracking_cycles": 2,
        "baseline_rmse_rad": pytest.approx(0.01),
        "best_pva_ratio": pytest.approx(1.1),
        "best_pva_method": "PVA est O1 [k]",
        "projection_event_count": 5,
        "completed_method_count": 6,
    }


def test_portable_artifact_builder_uses_shared_dashboard_data(
    tmp_path: Path,
) -> None:
    run_directory = _build_fixture_run(tmp_path)
    output = tmp_path / "artifact.json"
    subprocess.run(
        [
            sys.executable,
            str(DASHBOARD_DIRECTORY / "build_interactive_dashboard.py"),
            str(run_directory),
            "--output",
            str(output),
        ],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    artifact = json.loads(output.read_text(encoding="utf-8"))
    datasets = artifact["snapshot"]["datasets"]
    assert len(datasets["position_overview"]) == 15
    assert len(datasets["error_overview"]) == 12
    assert len(datasets["projection_events"]) == 5
    assert datasets["overview_metrics"][0]["projection_event_count"] == 5
