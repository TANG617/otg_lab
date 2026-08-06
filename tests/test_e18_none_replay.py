from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments/E18_pv_future_o1_recorded_replay_consistency/none_replay.py"
)
SPEC = importlib.util.spec_from_file_location("_e18_none_replay_test", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
e18 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e18
SPEC.loader.exec_module(e18)


def test_none_snapshot_selects_reset_segment_and_excludes_garbage_only_from_score() -> None:
    data = e18.load_none_snapshot(ROOT / e18.RAW_INPUT_PATH)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)

    assert data.raw_row_count == 101620
    assert data.source_segment_count == 1
    assert data.source.count == 1620
    assert data.output.count == 12244
    assert mapping.max_tick == 17329
    assert data.analysis_valid_start_s == pytest.approx(data.segment_start_s + 3.0)
    assert data.output.position_rad[0] == pytest.approx(
        e18.MAX_JERK_RAD_S3 * e18.CONTROL_DT_S**3 / 6.0,
        abs=2e-17,
    )


def test_none_replay_preserves_future_o1_startup_and_call_hypotheses() -> None:
    data = e18.load_none_snapshot(ROOT / e18.RAW_INPUT_PATH)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)
    events = e18.build_future_o1_target_events(data)

    assert events[0]["prediction_startup"]
    assert events[1]["prediction_startup"]
    assert not events[2]["prediction_startup"]
    assert events[2]["target_position_rad"] == pytest.approx(
        3.0 * data.source.position_rad[2]
        - 3.0 * data.source.position_rad[1]
        + data.source.position_rad[0]
    )

    primary, calls = e18.run_replay_execution(
        data, events, mapping, execution_id=e18.PRIMARY_EXECUTION_ID
    )
    control_only, _ = e18.run_replay_execution(
        data, events, mapping, execution_id="update_control_loop_only"
    )
    comparison = e18.build_recorded_replay_comparison(
        data,
        mapping,
        {
            e18.PRIMARY_EXECUTION_ID: primary,
            "update_control_loop_only": control_only,
        },
    )

    assert calls[0]["callback_source"] == "target_callback"
    assert primary[0]["replay_call_count_through_tick"] == 1
    assert control_only[0]["replay_call_count_through_tick"] == 1
    np.testing.assert_allclose(
        [
            row[f"{e18.PRIMARY_EXECUTION_ID}_minus_recorded_rad"]
            for row in comparison[:29]
        ],
        0.0,
        atol=2e-17,
        rtol=0.0,
    )
    assert (
        abs(comparison[29][f"{e18.PRIMARY_EXECUTION_ID}_minus_recorded_rad"])
        > 1e-4
    )


def test_none_replay_acceleration_parameter_is_backward_compatible() -> None:
    data = e18.load_none_snapshot(ROOT / e18.RAW_INPUT_PATH)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)
    events = e18.build_future_o1_target_events(data)

    default_replay, default_calls = e18.run_replay_execution(
        data, events, mapping, execution_id=e18.PRIMARY_EXECUTION_ID
    )
    explicit_replay, explicit_calls = e18.run_replay_execution(
        data,
        events,
        mapping,
        execution_id=e18.PRIMARY_EXECUTION_ID,
        max_acceleration_rad_s2=e18.MAX_ACCELERATION_RAD_S2,
    )

    assert default_replay == explicit_replay
    assert default_calls == explicit_calls


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_none_replay_rejects_invalid_acceleration(value: float) -> None:
    data = e18.load_none_snapshot(ROOT / e18.RAW_INPUT_PATH)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)
    events = e18.build_future_o1_target_events(data)

    with pytest.raises(ValueError, match="finite and positive"):
        e18.run_replay_execution(
            data,
            events,
            mapping,
            execution_id=e18.PRIMARY_EXECUTION_ID,
            max_acceleration_rad_s2=value,
        )


def test_rebuilt_e18_runs_exploration_and_no_only_formal_gate(tmp_path: Path) -> None:
    result = e18.run_recorded_replay_consistency(
        project_root=ROOT,
        runs_root=tmp_path / "runs",
        create_figures=True,
    )

    assert result.success
    summary = json.loads(
        (result.run_directory / "summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (result.run_directory / "manifest.json").read_text(encoding="utf-8")
    )
    comparison_lines = (
        result.run_directory / "recorded_replay_comparison.csv"
    ).read_text(encoding="utf-8").splitlines()
    replay_lines = (
        result.run_directory / "execution_output_trace.csv"
    ).read_text(encoding="utf-8").splitlines()

    assert summary["formal_no_parity_status"] == "not_evaluable"
    assert summary["exploratory_right_axis_result"] == "different"
    assert not summary["synchronization_ranking_generated"]
    assert not summary["p_only_pv_analysis_generated"]
    assert len(comparison_lines) == 12245
    assert len(replay_lines) == 3 * 17329 + 1
    assert manifest["status"] == "completed"
    assert manifest["inputs"]["recorded_sync_no_snapshot"]["sha256"] == (
        "9808c80ead58e315f79089a90d0bce599bf312ba3314a6882b48ff4f746654f0"
    )
    assert (result.run_directory / "formal_no_parity/gate_summary.csv").is_file()
    assert (result.run_directory / "figures").is_dir()
    assert (
        result.run_directory / "figures/target_recorded_replay_comparison.png"
    ).is_file()
    assert (
        result.run_directory / "figures/target_recorded_replay_comparison.svg"
    ).is_file()
    assert not (result.run_directory / "synchronization_ranking.csv").exists()
