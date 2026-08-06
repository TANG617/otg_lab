from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments/E19_pv_future_o1_amax_sensitivity/experiment.py"
)
SPEC = importlib.util.spec_from_file_location("_e19_experiment_test", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
e19 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e19
SPEC.loader.exec_module(e19)


def test_default_amax_grid_is_predeclared_and_dense() -> None:
    levels = e19.DEFAULT_AMAX_LEVELS_RAD_S2

    assert len(levels) == 125
    assert levels[:3] == (16.2, 16.4, 16.6)
    assert levels[-3:] == (40.6, 48.6, 64.8)
    assert all(right > left for left, right in zip(levels, levels[1:]))


def test_drawdown_and_classification_detect_transfer() -> None:
    elapsed = [0.0, 0.001, 0.002, 0.003, 0.004]
    monotonic = e19.measure_position_drawdown(
        elapsed,
        [0.0, 0.1, 0.2, 0.3, 0.4],
        start_s=0.0,
        end_s=0.004,
    )
    rollback = e19.measure_position_drawdown(
        elapsed,
        [0.0, 0.2, 0.4, 0.1, 0.3],
        start_s=0.0,
        end_s=0.004,
    )

    assert monotonic["max_drawdown_rad"] == 0.0
    assert monotonic["numerically_eliminated"]
    assert rollback["max_drawdown_rad"] == pytest.approx(0.3)
    assert rollback["peak_elapsed_time_s"] == pytest.approx(0.002)
    assert rollback["trough_elapsed_time_s"] == pytest.approx(0.003)
    assert (
        e19.classify_dip(monotonic, rollback, criterion="numerical")
        == "focal_eliminated_but_transferred"
    )


def test_replay_window_counts_negative_velocity_duration() -> None:
    replay = [
        {
            "command_elapsed_time_s": index * 0.001,
            "command_position_rad": position,
            "command_velocity_rad_s": velocity,
        }
        for index, (position, velocity) in enumerate(
            ((0.0, 0.1), (0.1, -0.2), (0.05, -0.1), (0.2, 0.3))
        )
    ]

    result = e19.measure_replay_window(replay, start_s=0.0, end_s=0.003)

    assert result["max_drawdown_rad"] == pytest.approx(0.05)
    assert result["minimum_velocity_rad_s"] == pytest.approx(-0.2)
    assert result["negative_velocity_sample_count"] == 2
    assert result["negative_velocity_duration_s"] == pytest.approx(0.002)


def test_e19_locates_current_e18_focal_and_rising_windows() -> None:
    data = e19.e18.load_none_snapshot(ROOT / e19.e18.RAW_INPUT_PATH)

    windows = e19.locate_analysis_windows(data)

    assert windows.anchor_source_index == 1191
    assert windows.rising_start_source_index == 1167
    assert windows.rising_end_source_index == 1201
    assert windows.anchor_elapsed_time_s - data.segment_start_s == pytest.approx(
        12.669195149006555
    )
    assert windows.focal_start_s == pytest.approx(
        windows.anchor_elapsed_time_s - 0.030
    )
    assert windows.focal_end_s == pytest.approx(
        windows.anchor_elapsed_time_s + 0.040
    )


def test_e19_control_cases_show_elimination_and_reappearance(tmp_path: Path) -> None:
    result = e19.run_amax_sensitivity(
        project_root=ROOT,
        runs_root=tmp_path / "runs",
        create_figures=True,
        amax_levels_rad_s2=(16.2, 21.8, 32.4),
    )

    assert result.success
    with (result.run_directory / "amax_sweep_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        metrics = {
            float(row["max_acceleration_rad_s2"]): row
            for row in csv.DictReader(handle)
        }
    summary = json.loads(
        (result.run_directory / "summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (result.run_directory / "manifest.json").read_text(encoding="utf-8")
    )

    assert float(metrics[16.2]["focal_max_drawdown_mrad"]) > 5.0
    assert metrics[21.8]["focal_numerically_eliminated"] == "true"
    assert float(metrics[21.8]["rising_max_drawdown_mrad"]) > 0.1
    assert float(metrics[32.4]["focal_max_drawdown_mrad"]) > 0.1
    assert summary["scientific_result"] == "focal_eliminated_but_transferred"
    assert summary["engineering_result"] == "focal_eliminated_but_transferred"
    assert summary["tested_case_count"] == 3
    assert summary["method_id"] == "pv_pred_backward_o1_kp1"
    assert summary["reference_case"]["case_id"] == "amax_16p2"
    assert summary["all_output_constraint_audits_passed"]
    assert manifest["status"] == "completed"
    assert len(
        (result.run_directory / "output_constraint_audit.csv")
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 4
    assert len(
        (result.run_directory / "amax_output_trace.csv")
        .read_text(encoding="utf-8")
        .splitlines()
    ) > 900
    for figure_name in (
        "drawdown_vs_amax",
        "minimum_velocity_vs_amax",
        "focal_output_comparison",
        "rising_episode_output_comparison",
    ):
        assert (result.run_directory / f"figures/{figure_name}.png").is_file()
        assert (result.run_directory / f"figures/{figure_name}.svg").is_file()
