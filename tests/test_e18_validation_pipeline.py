from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest
from ruckig import (
    ControlInterface,
    DurationDiscretization,
    InputParameter,
    OutputParameter,
    Ruckig,
    Synchronization,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments/E18_pv_future_o1_recorded_replay_consistency/validation_pipeline.py"
)
SPEC = importlib.util.spec_from_file_location(
    "_e18_validation_pipeline_test", MODULE_PATH
)
assert SPEC is not None
assert SPEC.loader is not None
e18 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e18
SPEC.loader.exec_module(e18)


DOF = 3
DT_S = 0.001
AXIS_NAMES = ("left", "right", "wrist")
MAX_VELOCITY = (4.1, 3.2, 2.5)
MAX_ACCELERATION = (16.2, 12.7, 9.0)
MAX_JERK = (4000.0, 2500.0, 1500.0)
EVENT_CYCLES = (0, 10, 20)
EVENT_POSITIONS = (
    (0.020, -0.015, 0.010),
    (0.024, -0.012, 0.014),
    (0.029, -0.008, 0.019),
)
EVENT_TIME_OFFSETS_S = (-0.00020, -0.00040, -0.00010)
ANALYSIS_VALID_CYCLE = 21
LAST_CYCLE = 25

SYNCHRONIZATION_ENUMS = {
    "No": Synchronization.No,
    "Time": Synchronization.Time,
    "TimeIfNecessary": Synchronization.TimeIfNecessary,
    "Phase": Synchronization.Phase,
}


@dataclass(frozen=True)
class _FixtureInfo:
    root: Path
    call_seq_by_key: dict[tuple[str, int, str], int]
    event_call_seq: dict[tuple[str, int], int]


def _csv_scalar(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".17g")
    return value


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: tuple[str, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: _csv_scalar(row.get(field)) for field in fieldnames}
            )


def _future_target(
    history: list[np.ndarray], raw_position: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    history.append(np.array(raw_position, copy=True))
    if len(history) < 3:
        return np.array(raw_position, copy=True), np.zeros(DOF)
    p2, p1, p0 = history[-3:]
    return (
        3.0 * p0 - 3.0 * p1 + p2,
        (2.0 * p0 - 3.0 * p1 + p2) / 0.01,
    )


def _write_synthetic_capture(
    root: Path,
    *,
    state_injection: tuple[str, int, str, int, float] | None = None,
) -> _FixtureInfo:
    root.mkdir(parents=True, exist_ok=True)
    calls: list[dict[str, Any]] = []
    axes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    call_seq_by_key: dict[tuple[str, int, str], int] = {}
    event_call_seq: dict[tuple[str, int], int] = {}
    base_time = 100.0

    for mode in e18.REQUIRED_MODES:
        run_id = f"run_{e18.MODE_SLUGS[mode]}"
        otg = Ruckig(DOF, DT_S)
        inp = InputParameter(DOF)
        out = OutputParameter(DOF)
        inp.current_position = [0.0] * DOF
        inp.current_velocity = [0.0] * DOF
        inp.current_acceleration = [0.0] * DOF
        inp.max_velocity = list(MAX_VELOCITY)
        inp.min_velocity = None
        inp.max_acceleration = list(MAX_ACCELERATION)
        inp.min_acceleration = None
        inp.max_jerk = list(MAX_JERK)
        inp.enabled = [True] * DOF
        inp.synchronization = SYNCHRONIZATION_ENUMS[mode]
        inp.per_dof_synchronization = None
        inp.control_interface = ControlInterface.Position
        inp.per_dof_control_interface = None
        inp.duration_discretization = DurationDiscretization.Continuous
        inp.minimum_duration = None

        call_seq = 0
        active_event_seq = -1
        target_position = np.zeros(DOF)
        target_velocity = np.zeros(DOF)
        history: list[np.ndarray] = []
        previous_call_time = base_time - DT_S

        for cycle_seq in range(LAST_CYCLE + 1):
            callback_sources = ["control_loop"]
            if cycle_seq in EVENT_CYCLES:
                event_seq = EVENT_CYCLES.index(cycle_seq)
                active_event_seq = event_seq
                raw_position = np.asarray(
                    EVENT_POSITIONS[event_seq], dtype=np.float64
                )
                target_position, target_velocity = _future_target(
                    history, raw_position
                )
                callback_sources = ["target_callback", "control_loop"]
                event_call_seq[(mode, event_seq)] = call_seq
                event_time = (
                    base_time
                    + cycle_seq * DT_S
                    + EVENT_TIME_OFFSETS_S[event_seq]
                )
                for axis_index, position in enumerate(raw_position):
                    events.append(
                        {
                            "run_id": run_id,
                            "event_seq": event_seq,
                            "applied_call_seq": call_seq,
                            "axis_index": axis_index,
                            "axis_name": AXIS_NAMES[axis_index],
                            "monotonic_time_s": event_time,
                            "position_rad": float(position),
                        }
                    )

            for subindex, callback_source in enumerate(callback_sources):
                call_time = base_time + cycle_seq * DT_S + subindex * 0.0002
                wall_delta_time = call_time - previous_call_time
                previous_call_time = call_time
                call_seq_by_key[(mode, cycle_seq, callback_source)] = call_seq

                inp.target_position = target_position.tolist()
                inp.target_velocity = target_velocity.tolist()
                inp.target_acceleration = [0.0] * DOF
                inp.max_velocity = list(MAX_VELOCITY)
                inp.min_velocity = None
                inp.max_acceleration = list(MAX_ACCELERATION)
                inp.min_acceleration = None
                inp.max_jerk = list(MAX_JERK)
                inp.enabled = [True] * DOF
                inp.synchronization = SYNCHRONIZATION_ENUMS[mode]
                inp.per_dof_synchronization = None
                inp.control_interface = ControlInterface.Position
                inp.per_dof_control_interface = None
                inp.duration_discretization = DurationDiscretization.Continuous
                inp.minimum_duration = None

                if state_injection is not None:
                    (
                        injection_mode,
                        injection_cycle,
                        injection_source,
                        injection_axis,
                        injection_delta,
                    ) = state_injection
                    if (
                        mode == injection_mode
                        and cycle_seq == injection_cycle
                        and callback_source == injection_source
                    ):
                        injected_position = list(inp.current_position)
                        injected_position[injection_axis] += injection_delta
                        inp.current_position = injected_position

                current_position = list(inp.current_position)
                current_velocity = list(inp.current_velocity)
                current_acceleration = list(inp.current_acceleration)
                recorded_target_position = list(inp.target_position)
                recorded_target_velocity = list(inp.target_velocity)
                recorded_target_acceleration = list(inp.target_acceleration)
                result = otg.update(inp, out)
                independent = list(out.trajectory.independent_min_durations)
                result_name = str(result).split(".")[-1]
                analysis_valid = cycle_seq >= ANALYSIS_VALID_CYCLE
                calls.append(
                    {
                        "run_id": run_id,
                        "mode": mode,
                        "cycle_seq": cycle_seq,
                        "call_seq": call_seq,
                        "callback_source": callback_source,
                        "active_event_seq": active_event_seq,
                        "monotonic_time_s": call_time,
                        "wall_delta_time_s": wall_delta_time,
                        "ruckig_delta_time_s": DT_S,
                        "run_reset": call_seq == 0,
                        "analysis_valid": analysis_valid,
                        "result_code": int(result),
                        "result_name": result_name,
                        "trajectory_duration_s": float(out.trajectory.duration),
                        "trajectory_time_s": float(out.time),
                        "new_calculation": bool(out.new_calculation),
                        "did_section_change": bool(out.did_section_change),
                        "new_section": int(out.new_section),
                        "was_calculation_interrupted": bool(
                            out.was_calculation_interrupted
                        ),
                        "calculation_duration_us": float(
                            out.calculation_duration
                        ),
                        "synchronization": mode,
                        "control_interface": "Position",
                        "duration_discretization": "Continuous",
                        "minimum_duration_s": None,
                    }
                )
                for axis_index in range(DOF):
                    axes.append(
                        {
                            "run_id": run_id,
                            "call_seq": call_seq,
                            "axis_index": axis_index,
                            "axis_name": AXIS_NAMES[axis_index],
                            "current_position_rad": current_position[axis_index],
                            "current_velocity_rad_s": current_velocity[axis_index],
                            "current_acceleration_rad_s2": current_acceleration[
                                axis_index
                            ],
                            "target_position_rad": recorded_target_position[
                                axis_index
                            ],
                            "target_velocity_rad_s": recorded_target_velocity[
                                axis_index
                            ],
                            "target_acceleration_rad_s2": recorded_target_acceleration[
                                axis_index
                            ],
                            "output_position_rad": float(
                                out.new_position[axis_index]
                            ),
                            "output_velocity_rad_s": float(
                                out.new_velocity[axis_index]
                            ),
                            "output_acceleration_rad_s2": float(
                                out.new_acceleration[axis_index]
                            ),
                            "output_jerk_rad_s3": float(out.new_jerk[axis_index]),
                            "max_velocity_rad_s": MAX_VELOCITY[axis_index],
                            "min_velocity_rad_s": None,
                            "max_acceleration_rad_s2": MAX_ACCELERATION[
                                axis_index
                            ],
                            "min_acceleration_rad_s2": None,
                            "max_jerk_rad_s3": MAX_JERK[axis_index],
                            "min_jerk_rad_s3": -MAX_JERK[axis_index],
                            "enabled": True,
                            "per_dof_synchronization": None,
                            "per_dof_control_interface": None,
                            "independent_min_duration_s": float(
                                independent[axis_index]
                            ),
                        }
                    )
                out.pass_to_input(inp)
                call_seq += 1

    manifest = {
        "schema_version": e18.CAPTURE_SCHEMA_VERSION,
        "capture_kind": "controller_internal_full_axis",
        "dof": DOF,
        "axis_names": list(AXIS_NAMES),
        "right_axis_index": 1,
        "future_o1_h_s": 0.01,
        "nominal_control_dt_s": DT_S,
        "ruckig": {
            "version": version("ruckig"),
            "commit": "synthetic-fixture-same-runtime",
        },
        "build": {
            "platform": sys.platform,
            "compiler": "pytest-fixture",
            "floating_point_options": "default IEEE-754 binary64",
        },
        "runs": [
            {
                "run_id": f"run_{e18.MODE_SLUGS[mode]}",
                "mode": mode,
            }
            for mode in e18.REQUIRED_MODES
        ],
    }
    (root / "capture_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(root / "calls.csv", calls, e18.CALL_FIELDS)
    _write_csv(root / "axis_states.csv", axes, e18.AXIS_STATE_FIELDS)
    _write_csv(
        root / "raw_position_events.csv",
        events,
        e18.RAW_POSITION_EVENT_FIELDS,
    )
    return _FixtureInfo(
        root=root,
        call_seq_by_key=call_seq_by_key,
        event_call_seq=event_call_seq,
    )


@pytest.fixture
def synthetic_capture(tmp_path: Path) -> _FixtureInfo:
    return _write_synthetic_capture(tmp_path / "capture")


def _rewrite_rows(
    path: Path,
    fieldnames: tuple[str, ...],
    transform: Callable[[list[dict[str, str]]], list[dict[str, Any]]],
) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    _write_csv(path, transform(rows), fieldnames)


def _change_one_numeric_row(
    path: Path,
    fieldnames: tuple[str, ...],
    *,
    run_id: str,
    call_seq: int,
    axis_index: int,
    field: str,
    delta: float,
) -> None:
    changed = 0

    def transform(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        nonlocal changed
        for row in rows:
            if (
                row["run_id"] == run_id
                and int(row["call_seq"]) == call_seq
                and int(row["axis_index"]) == axis_index
            ):
                row[field] = format(float(row[field]) + delta, ".17g")
                changed += 1
        return rows

    _rewrite_rows(path, fieldnames, transform)
    assert changed == 1


def _mode_result(report: Any, mode: str) -> Any:
    return next(item for item in report.modes if item.mode == mode)


def test_full_axis_loader_preserves_heterogeneous_limits_and_extra_call(
    synthetic_capture: _FixtureInfo,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)

    assert capture.manifest.dof == DOF
    assert capture.manifest.axis_names == AXIS_NAMES
    assert capture.manifest.right_axis_index == 1
    assert len(capture.run_ids) == 4
    calls = capture.calls_for_run("run_time_if_necessary")
    cycle_ten = [call for call in calls if call.cycle_seq == 10]
    assert [call.callback_source for call in cycle_ten] == [
        "target_callback",
        "control_loop",
    ]
    assert cycle_ten[1].call_seq == cycle_ten[0].call_seq + 1

    first_axes = capture.axes_for_call("run_time_if_necessary", 0)
    assert tuple(axis.max_velocity_rad_s for axis in first_axes) == MAX_VELOCITY
    assert tuple(axis.max_acceleration_rad_s2 for axis in first_axes) == (
        MAX_ACCELERATION
    )
    assert tuple(axis.max_jerk_rad_s3 for axis in first_axes) == MAX_JERK
    assert all(axis.min_velocity_rad_s is None for axis in first_axes)
    assert all(axis.min_acceleration_rad_s2 is None for axis in first_axes)
    assert len(capture.axis_states) == len(capture.calls) * DOF


def test_all_four_modes_pass_all_three_pointwise_gates(
    synthetic_capture: _FixtureInfo,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)
    sufficiency = e18.validate_pipeline_data_sufficiency(capture)
    report = e18.run_parity(capture)

    assert len(sufficiency) == 4
    assert all(row["status"] == e18.GATE_PASS for row in sufficiency)
    assert report.all_passed
    for mode in e18.REQUIRED_MODES:
        result = _mode_result(report, mode)
        assert result.target_builder.status == e18.GATE_PASS
        assert result.solver_step.status == e18.GATE_PASS
        assert result.closed_loop.status == e18.GATE_PASS
        assert result.target_builder.evaluated_point_count > 0
        assert result.solver_step.evaluated_point_count == (
            result.closed_loop.evaluated_point_count
        )


def test_rebuilt_e18_no_only_sufficiency_does_not_require_other_modes(
    synthetic_capture: _FixtureInfo,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)
    sufficiency = e18.validate_no_data_sufficiency(capture)
    report = e18.run_parity(capture, modes=("No",))

    assert len(sufficiency) == 1
    assert sufficiency[0]["mode"] == "No"
    assert sufficiency[0]["status"] == e18.GATE_PASS
    assert len(report.modes) == 1
    assert report.modes[0].passed


def test_all_pass_pipeline_unlocks_controlled_sync_and_local_p_pv(
    synthetic_capture: _FixtureInfo,
    tmp_path: Path,
) -> None:
    result = e18.run_e18_validation_pipeline(
        project_root=ROOT,
        capture_root=synthetic_capture.root,
        runs_root=tmp_path / "passing_runs",
        create_figures=False,
    )

    downstream = json.loads(
        (result.run_directory / "downstream_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.success
    assert downstream["downstream_status"] == e18.DOWNSTREAM_ALLOWED
    assert downstream["synchronization_analysis_generated"]
    assert downstream["p_only_pv_analysis_generated"]
    assert not (result.run_directory / "first_mismatch.json").exists()
    for name in (
        "synchronization_counterfactual.csv",
        "synchronization_metrics.csv",
        "synchronization_lag_scan.csv",
        "target_transition_diagnostics.csv",
        "real_mode_observation_metrics.csv",
        "p_only_pv_outputs.csv",
        "p_only_pv_metrics.csv",
    ):
        assert (result.run_directory / "post_parity" / name).is_file()

    with (result.run_directory / "post_parity/synchronization_metrics.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        synchronization_metrics = list(csv.DictReader(handle))
    assert {row["synchronization"] for row in synchronization_metrics} == set(
        e18.REQUIRED_MODES
    )
    assert len({row["controlled_input_hash"] for row in synchronization_metrics}) == 1

    with (result.run_directory / "post_parity/p_only_pv_outputs.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        ablation_rows = list(csv.DictReader(handle))
    p_only = [row for row in ablation_rows if row["arm_id"] == "p_only"]
    pv_live = [
        row for row in ablation_rows if row["arm_id"] == "pv_future_o1_live"
    ]
    legacy = [
        row for row in ablation_rows if row["arm_id"] == "predictor_p_legacy"
    ]
    assert p_only and pv_live and legacy
    assert all(
        float(row["target_position_rad"])
        == float(row["reference_raw_position_rad"])
        and float(row["target_velocity_rad_s"]) == 0.0
        and float(row["target_acceleration_rad_s2"]) == 0.0
        for row in p_only
    )
    assert any(abs(float(row["target_velocity_rad_s"])) > 0.0 for row in pv_live)
    assert all(float(row["target_acceleration_rad_s2"]) == 0.0 for row in pv_live)
    assert all(float(row["target_velocity_rad_s"]) == 0.0 for row in legacy)
    assert all(row["method_role"] == "sensitivity_only" for row in legacy)


def test_target_builder_uses_fixed_h_startup_jitter_and_hold(
    synthetic_capture: _FixtureInfo,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)
    run_id = "run_time_if_necessary"
    events = capture.events_for_run(run_id)
    event_times = np.asarray(
        [
            next(row.monotonic_time_s for row in events if row.event_seq == index)
            for index in range(3)
        ]
    )
    assert not np.allclose(np.diff(event_times), 0.01, rtol=0.0, atol=1e-12)

    targets = e18.build_local_target_sequence(capture, run_id)
    first_call = synthetic_capture.event_call_seq[("TimeIfNecessary", 0)]
    second_call = synthetic_capture.event_call_seq[("TimeIfNecessary", 1)]
    mature_call = synthetic_capture.event_call_seq[("TimeIfNecessary", 2)]
    assert targets[first_call]["startup"]
    assert targets[second_call]["startup"]
    assert not targets[mature_call]["startup"]
    np.testing.assert_allclose(
        targets[first_call]["target_position"], EVENT_POSITIONS[0], atol=0.0
    )
    np.testing.assert_allclose(
        targets[second_call]["target_velocity"], 0.0, atol=0.0
    )

    p2, p1, p0 = (np.asarray(row) for row in EVENT_POSITIONS)
    np.testing.assert_allclose(
        targets[mature_call]["target_position"],
        3.0 * p0 - 3.0 * p1 + p2,
        rtol=0.0,
        atol=1e-15,
    )
    np.testing.assert_allclose(
        targets[mature_call]["target_velocity"],
        (2.0 * p0 - 3.0 * p1 + p2) / 0.01,
        rtol=0.0,
        atol=1e-14,
    )
    held = targets[mature_call + 2]
    assert held["held"]
    assert held["events_applied"] == ()
    np.testing.assert_array_equal(
        held["target_position"], targets[mature_call]["target_position"]
    )
    np.testing.assert_array_equal(
        held["target_velocity"], targets[mature_call]["target_velocity"]
    )


def test_analysis_valid_scores_after_warmup_without_reinitializing(
    synthetic_capture: _FixtureInfo,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)
    result = _mode_result(e18.run_parity(capture, modes=("Time",)), "Time")
    rows = list(result.closed_loop.rows)
    first_valid_call = min(
        int(row["call_seq"]) for row in rows if bool(row["analysis_valid"])
    )
    first_valid = next(
        row
        for row in rows
        if int(row["call_seq"]) == first_valid_call
        and int(row["axis_index"]) == 0
    )
    previous = next(
        row
        for row in rows
        if int(row["call_seq"]) == first_valid_call - 1
        and int(row["axis_index"]) == 0
    )

    assert not previous["evaluated"]
    assert first_valid["evaluated"]
    assert abs(first_valid["local_values"]["current_position_rad"]) > 0.0
    assert first_valid["local_values"]["current_position_rad"] == pytest.approx(
        previous["local_values"]["output_position_rad"], abs=0.0
    )


def test_target_mismatch_stops_later_gates_and_blocks_downstream(
    synthetic_capture: _FixtureInfo,
    tmp_path: Path,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)
    last_call = capture.calls_for_run("run_no")[-1].call_seq
    _change_one_numeric_row(
        synthetic_capture.root / "axis_states.csv",
        e18.AXIS_STATE_FIELDS,
        run_id="run_no",
        call_seq=last_call,
        axis_index=1,
        field="target_position_rad",
        delta=2.0e-12,
    )

    report = e18.run_parity(synthetic_capture.root, modes=("No",))
    no_mode = _mode_result(report, "No")
    assert no_mode.target_builder.status == e18.GATE_FAIL
    assert no_mode.solver_step.status == e18.GATE_NOT_RUN
    assert no_mode.closed_loop.status == e18.GATE_NOT_RUN
    assert no_mode.target_builder.first_mismatch["call_seq"] == last_call
    assert "target_position_rad" in no_mode.target_builder.first_mismatch[
        "mismatch_components"
    ]

    pipeline = e18.run_e18_validation_pipeline(
        project_root=ROOT,
        capture_root=synthetic_capture.root,
        runs_root=tmp_path / "runs",
        create_figures=False,
    )
    downstream = json.loads(
        (pipeline.run_directory / "downstream_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert pipeline.success
    assert downstream["downstream_status"] == e18.DOWNSTREAM_BLOCKED
    assert not downstream["synchronization_analysis_generated"]
    assert not downstream["p_only_pv_analysis_generated"]
    assert not (pipeline.run_directory / "post_parity").exists()


def test_solver_gate_uses_pointwise_threshold_not_low_rmse(
    synthetic_capture: _FixtureInfo,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)
    last_call = capture.calls_for_run("run_no")[-1].call_seq
    _change_one_numeric_row(
        synthetic_capture.root / "axis_states.csv",
        e18.AXIS_STATE_FIELDS,
        run_id="run_no",
        call_seq=last_call,
        axis_index=0,
        field="output_position_rad",
        delta=1.1e-12,
    )

    result = _mode_result(
        e18.run_parity(synthetic_capture.root, modes=("No",)), "No"
    )
    assert result.target_builder.status == e18.GATE_PASS
    assert result.solver_step.status == e18.GATE_FAIL
    assert result.closed_loop.status == e18.GATE_NOT_RUN
    errors = np.asarray(
        [
            float(row["errors"]["output_position_rad"])
            for row in result.solver_step.rows
            if row["evaluated"]
        ]
    )
    rmse = float(np.sqrt(np.mean(errors**2)))
    assert rmse < e18.DEFAULT_THRESHOLDS.position_rad
    assert (
        result.solver_step.max_abs_errors["output_position_rad"]
        > e18.DEFAULT_THRESHOLDS.position_rad
    )
    assert result.solver_step.first_mismatch["call_seq"] == last_call


@pytest.mark.parametrize(
    ("table", "field", "delta", "component"),
    (
        ("axis", "output_velocity_rad_s", 1.1e-10, "output_velocity_rad_s"),
        (
            "axis",
            "output_acceleration_rad_s2",
            1.1e-8,
            "output_acceleration_rad_s2",
        ),
        ("call", "trajectory_duration_s", 1.1e-12, "trajectory_duration_s"),
    ),
)
def test_each_numeric_gate_uses_its_declared_pointwise_tolerance(
    tmp_path: Path,
    table: str,
    field: str,
    delta: float,
    component: str,
) -> None:
    fixture = _write_synthetic_capture(tmp_path / field)
    capture = e18.load_full_axis_capture(fixture.root)
    last_call = capture.calls_for_run("run_no")[-1].call_seq
    if table == "axis":
        _change_one_numeric_row(
            fixture.root / "axis_states.csv",
            e18.AXIS_STATE_FIELDS,
            run_id="run_no",
            call_seq=last_call,
            axis_index=0,
            field=field,
            delta=delta,
        )
    else:

        def alter_call(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
            changed = 0
            for row in rows:
                if row["run_id"] == "run_no" and int(row["call_seq"]) == last_call:
                    row[field] = format(float(row[field]) + delta, ".17g")
                    changed += 1
            assert changed == 1
            return rows

        _rewrite_rows(fixture.root / "calls.csv", e18.CALL_FIELDS, alter_call)

    result = _mode_result(e18.run_parity(fixture.root, modes=("No",)), "No")

    assert result.solver_step.status == e18.GATE_FAIL
    assert component in result.solver_step.first_mismatch["mismatch_components"]


def test_within_tolerance_ulp_difference_passes_but_is_not_bitwise_equal(
    synthetic_capture: _FixtureInfo,
) -> None:
    capture = e18.load_full_axis_capture(synthetic_capture.root)
    last_call = capture.calls_for_run("run_no")[-1].call_seq

    def change_by_one_ulp(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        changed = 0
        for row in rows:
            if (
                row["run_id"] == "run_no"
                and int(row["call_seq"]) == last_call
                and int(row["axis_index"]) == 0
            ):
                original = float(row["output_position_rad"])
                row["output_position_rad"] = format(
                    float(np.nextafter(original, math.inf)), ".17g"
                )
                changed += 1
        assert changed == 1
        return rows

    _rewrite_rows(
        synthetic_capture.root / "axis_states.csv",
        e18.AXIS_STATE_FIELDS,
        change_by_one_ulp,
    )
    result = _mode_result(
        e18.run_parity(synthetic_capture.root, modes=("No",)), "No"
    )

    assert result.solver_step.status == e18.GATE_PASS
    assert result.closed_loop.status == e18.GATE_PASS
    assert result.solver_step.bitwise_equal is False


@pytest.mark.parametrize(
    ("field", "replacement", "component"),
    (
        ("new_calculation", "false", "new_calculation"),
        ("new_section", "7", "new_section"),
        ("result_name", "Finished", "result_name"),
    ),
)
def test_solver_discrete_state_must_match_exactly(
    tmp_path: Path,
    field: str,
    replacement: str,
    component: str,
) -> None:
    fixture = _write_synthetic_capture(tmp_path / field)
    capture = e18.load_full_axis_capture(fixture.root)
    last_call = capture.calls_for_run("run_no")[-1].call_seq

    def alter_call(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        changed = 0
        for row in rows:
            if row["run_id"] == "run_no" and int(row["call_seq"]) == last_call:
                if field == "new_calculation":
                    row[field] = "false" if row[field] == "true" else "true"
                else:
                    row[field] = replacement
                changed += 1
        assert changed == 1
        return rows

    _rewrite_rows(fixture.root / "calls.csv", e18.CALL_FIELDS, alter_call)
    result = _mode_result(e18.run_parity(fixture.root, modes=("No",)), "No")

    assert result.target_builder.status == e18.GATE_PASS
    assert result.solver_step.status == e18.GATE_FAIL
    assert result.closed_loop.status == e18.GATE_NOT_RUN
    assert component in result.solver_step.first_mismatch["mismatch_components"]
    assert result.solver_step.first_mismatch["constraints"]
    assert len(result.solver_step.first_mismatch["context_calls"]) >= 2


def test_cross_mode_constraint_drift_is_not_evaluable(
    synthetic_capture: _FixtureInfo,
) -> None:
    _change_one_numeric_row(
        synthetic_capture.root / "axis_states.csv",
        e18.AXIS_STATE_FIELDS,
        run_id="run_time",
        call_seq=5,
        axis_index=2,
        field="max_velocity_rad_s",
        delta=0.01,
    )
    capture = e18.load_full_axis_capture(synthetic_capture.root)

    with pytest.raises(e18.CaptureValidationError) as caught:
        e18.validate_pipeline_data_sufficiency(capture)

    assert caught.value.code == "cross_mode_control_mismatch"


def test_recorded_state_injection_passes_step_but_fails_closed_loop(
    tmp_path: Path,
) -> None:
    fixture = _write_synthetic_capture(
        tmp_path / "injected",
        state_injection=("Time", 23, "control_loop", 0, 1.0e-5),
    )
    capture = e18.load_full_axis_capture(fixture.root)
    result = _mode_result(e18.run_parity(capture, modes=("Time",)), "Time")
    injected_call = fixture.call_seq_by_key[("Time", 23, "control_loop")]

    assert result.target_builder.status == e18.GATE_PASS
    assert result.solver_step.status == e18.GATE_PASS
    assert result.closed_loop.status == e18.GATE_FAIL
    assert result.closed_loop.first_mismatch["call_seq"] == injected_call
    assert any(
        name.startswith("current_") or name.startswith("output_")
        for name in result.closed_loop.first_mismatch["mismatch_components"]
    )


@pytest.mark.parametrize(
    ("damage", "expected_code"),
    (
        ("axis", "incomplete_axis_set"),
        ("call", "missing_call_seq"),
        ("version", "ruckig_version_mismatch"),
    ),
)
def test_incomplete_or_wrong_version_capture_is_not_evaluable(
    tmp_path: Path,
    damage: str,
    expected_code: str,
) -> None:
    fixture = _write_synthetic_capture(tmp_path / damage)
    if damage == "axis":

        def remove_axis(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
            return [
                row
                for row in rows
                if not (
                    row["run_id"] == "run_no"
                    and int(row["call_seq"]) == 5
                    and int(row["axis_index"]) == 2
                )
            ]

        _rewrite_rows(
            fixture.root / "axis_states.csv",
            e18.AXIS_STATE_FIELDS,
            remove_axis,
        )
    elif damage == "call":

        def remove_call(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
            return [
                row
                for row in rows
                if not (
                    row["run_id"] == "run_no" and int(row["call_seq"]) == 5
                )
            ]

        _rewrite_rows(
            fixture.root / "calls.csv", e18.CALL_FIELDS, remove_call
        )
    else:
        manifest_path = fixture.root / "capture_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["ruckig"]["version"] = "0.0.deliberately-wrong"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if damage == "version":
        capture = e18.load_full_axis_capture(fixture.root)
        with pytest.raises(e18.CaptureValidationError) as caught:
            e18.validate_pipeline_data_sufficiency(capture)
    else:
        with pytest.raises(e18.CaptureValidationError) as caught:
            e18.load_full_axis_capture(fixture.root)
    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("damage", "expected_code"),
    (("nonfinite", "nonfinite_number"), ("duplicate_call", "duplicate_call")),
)
def test_capture_schema_rejects_nonfinite_and_duplicate_keys(
    tmp_path: Path,
    damage: str,
    expected_code: str,
) -> None:
    fixture = _write_synthetic_capture(tmp_path / damage)
    if damage == "nonfinite":

        def inject_nan(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
            rows[0]["current_position_rad"] = "nan"
            return rows

        _rewrite_rows(
            fixture.root / "axis_states.csv",
            e18.AXIS_STATE_FIELDS,
            inject_nan,
        )
    else:

        def duplicate(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
            return [*rows, dict(rows[0])]

        _rewrite_rows(fixture.root / "calls.csv", e18.CALL_FIELDS, duplicate)

    with pytest.raises(e18.CaptureValidationError) as caught:
        e18.load_full_axis_capture(fixture.root)

    assert caught.value.code == expected_code


@pytest.mark.parametrize("case", ("conflict", "tie"))
def test_sync_winner_requires_one_untied_winner_across_all_views(
    case: str,
) -> None:
    if case == "conflict":
        values = {
            "No": (1.0, 4.0, 4.0, 4.0),
            "Time": (4.0, 1.0, 4.0, 4.0),
            "TimeIfNecessary": (4.0, 4.0, 4.0, 1.0),
            "Phase": (4.0, 4.0, 1.0, 4.0),
        }
    else:
        values = {mode: (1.0, 1.0, 1.0, 1.0) for mode in e18.REQUIRED_MODES}
    rows = [
        {
            "synchronization": mode,
            "right_position_rmse_rad": metrics[0],
            "aggregate_position_rmse_rad": metrics[1],
            "worst_axis_position_rmse_rad": metrics[2],
            "right_position_max_abs_error_rad": metrics[3],
        }
        for mode, metrics in values.items()
    ]

    selection = e18.select_robust_sync_winner(rows)

    assert selection["status"] == "no_robust_single_best"
    assert selection["winner"] is None
    assert selection["criterion_winners"]


def test_current_snapshots_select_final_segment_and_block_pipeline(
    tmp_path: Path,
) -> None:
    snapshot_root = (
        ROOT / "experiments/E18_pv_future_o1_recorded_replay_consistency/data/raw"
    )
    observations = {
        row.mode: row for row in e18.inspect_snapshot_directory(snapshot_root)
    }
    expected = {
        "No": (1, 1620, 12244, 1362, 11307),
        "Time": (2, 1625, 12032, 1366, 11224),
        "TimeIfNecessary": (3, 1644, 11975, 1382, 11197),
        "Phase": (4, 1596, 11791, 1345, 11049),
    }
    for mode, values in expected.items():
        observation = observations[mode]
        assert (
            observation.source_segment_count,
            observation.selected_source_count,
            observation.selected_output_count,
            observation.analysis_valid_source_count,
            observation.analysis_valid_output_count,
        ) == values
        assert observation.analysis_valid_start_s == pytest.approx(
            observation.segment_start_s + 3.0
        )
        assert not observation.formal_gate_eligible

    result = e18.run_e18_validation_pipeline(
        project_root=ROOT,
        capture_root=snapshot_root,
        runs_root=tmp_path / "snapshot_runs",
        create_figures=False,
    )
    summary = json.loads(
        (result.run_directory / "summary.json").read_text(encoding="utf-8")
    )
    downstream = json.loads(
        (result.run_directory / "downstream_status.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.success
    assert summary["data_sufficiency_status"] == e18.GATE_NOT_EVALUABLE
    assert not summary["all_modes_all_gates_passed"]
    assert downstream["downstream_status"] == e18.DOWNSTREAM_BLOCKED
    assert not downstream["synchronization_analysis_generated"]
    assert not downstream["p_only_pv_analysis_generated"]
    assert not (result.run_directory / "post_parity").exists()
    assert (result.run_directory / "first_mismatch.json").is_file()
    assert all(
        row["status"] == e18.GATE_NOT_EVALUABLE
        for row in summary["gate_summary"]
    )
