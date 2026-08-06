from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = (
    ROOT
    / "experiments/E18_pv_future_o1_recorded_replay_consistency/experiment.py"
)
SPEC = importlib.util.spec_from_file_location("_e18_experiment_test", EXPERIMENT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
e18 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e18
SPEC.loader.exec_module(e18)


def _write_fixture(path: Path) -> None:
    rows = []
    for topic, values, times in (
        (e18.INPUT_TOPIC, (0.1, 0.2, 0.4), (0.0, 0.01, 0.02)),
        (e18.OUTPUT_TOPIC, (0.0, 0.001, 0.003), (0.0004, 0.0014, 0.0034)),
        (e18.TARGET_ECHO_TOPIC, (0.1, 0.1, 0.2), (0.0004, 0.0014, 0.0034)),
    ):
        rows.extend(
            {
                "elapsed time": time,
                "timestamp": 100.0 + time,
                "topic": topic,
                "value": value,
            }
            for time, value in zip(times, values)
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=e18.RAW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_e18_parser_and_tick_mapping_preserve_missing_ticks(tmp_path: Path) -> None:
    path = tmp_path / "fixture.csv"
    _write_fixture(path)

    data = e18.load_recorded_replay_csv(path)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)

    assert data.row_count == 9
    assert data.source.count == 3
    np.testing.assert_array_equal(mapping.tick_index, [1, 2, 4])
    assert mapping.max_tick == 4
    assert data.output.count < mapping.max_tick


def test_e18_tick_mapping_rejects_two_samples_on_one_tick() -> None:
    with pytest.raises(ValueError, match="unique increasing ticks"):
        e18.map_output_ticks([0.0, 0.0004])


def test_e18_reuses_nominal_future_o1_startup_and_stencil(tmp_path: Path) -> None:
    path = tmp_path / "fixture.csv"
    _write_fixture(path)
    data = e18.load_recorded_replay_csv(path)

    events = e18.build_future_o1_target_events(data)

    assert events[0]["prediction_startup"]
    assert events[1]["prediction_startup"]
    assert not events[2]["prediction_startup"]
    assert events[0]["target_position_rad"] == pytest.approx(0.1)
    assert events[0]["target_velocity_rad_s"] == pytest.approx(0.0)
    assert events[2]["target_position_rad"] == pytest.approx(
        3.0 * 0.4 - 3.0 * 0.2 + 0.1
    )
    assert events[2]["target_velocity_rad_s"] == pytest.approx(
        (2.0 * 0.4 - 3.0 * 0.2 + 0.1) / 0.01
    )
    assert all(event["target_acceleration_rad_s2"] == 0.0 for event in events)


def test_e18_real_input_contract_and_startup_reproduction() -> None:
    data = e18.load_recorded_replay_csv(ROOT / e18.RAW_INPUT_PATH)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)
    events = e18.build_future_o1_target_events(data)
    replay = e18.run_one_ms_replay(data, events, mapping)
    comparison = e18.build_observed_comparison(data, mapping, replay)

    assert data.row_count == 25124
    assert data.source.count == 1582
    assert data.output.count == 11771
    assert mapping.max_tick == 17339
    assert [row["tick_index"] for row in comparison[:9]] == list(range(1, 10))
    assert all(float(row["abs_error_rad"]) <= 1e-18 for row in comparison[:9])
    assert len(comparison) == data.output.count


def test_e18_one_ms_and_ten_ms_are_not_assumed_equivalent() -> None:
    data = e18.load_recorded_replay_csv(ROOT / e18.RAW_INPUT_PATH)
    events = e18.build_future_o1_target_events(data)

    rows = e18.run_rate_equivalence(events)
    position_difference = np.asarray(
        [row["position_difference_rad"] for row in rows], dtype=float
    )

    assert len(rows) == data.source.count
    assert np.max(np.abs(position_difference)) > 1e-4


def test_e18_end_to_end_writes_complete_non_interpolated_result(
    tmp_path: Path,
) -> None:
    result = e18.run_legacy_0801_replay(
        project_root=ROOT,
        runs_root=tmp_path / "runs",
        create_figures=False,
    )

    assert result.success
    summary = json.loads(
        (result.run_directory / "summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (result.run_directory / "manifest.json").read_text(encoding="utf-8")
    )
    comparison_lines = (
        result.run_directory / "observed_comparison.csv"
    ).read_text(encoding="utf-8").splitlines()
    replay_lines = (
        result.run_directory / "replay_1ms.csv"
    ).read_text(encoding="utf-8").splitlines()

    assert summary["scientific_result"] == "different"
    assert summary["practical_equivalence_assessed"] is False
    assert len(comparison_lines) == 11772
    assert len(replay_lines) == 17340
    assert manifest["status"] == "completed"
    assert manifest["real_environment"]["ruckig_version"] == "unknown"
    assert manifest["local_replay"]["ruckig_version"] == "0.17.3"
    assert manifest["inputs"]["recorded_environment_csv"]["sha256"] == (
        "eb304992daa300d9c08c2479fa9cb939c3b7b4d0973e5d20e977fa27848a135b"
    )
    assert "observed_comparison.csv" in manifest["output_hashes"]
