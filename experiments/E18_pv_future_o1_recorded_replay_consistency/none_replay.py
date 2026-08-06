"""E18 primary experiment: recorded Sync.No versus replayed PV Future-O1.

The currently available logger snapshot is sufficient for an exploratory
right-axis replay, but not for a formal parity claim.  Formal parity is
evaluated separately, in the same run, when a controller-internal full-axis
No-mode capture is available.
"""

from __future__ import annotations

import csv
import importlib.util
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
from ruckig import InputParameter, OutputParameter, Ruckig, Synchronization, Trajectory

from otg_lab.confirmatory import finish_compact_run, start_compact_run
from otg_lab.experiment import ExperimentResult
from otg_lab.predictors import make_predictor
from otg_lab.runio import sha256_file, write_json, write_rows_csv
from otg_lab.types import TimedState

EXPERIMENT_ID = "E18"
DIRECTORY_NAME = "E18_pv_future_o1_recorded_replay_consistency"
TITLE = "PV Future-O1 recorded/replay consistency"

RAW_INPUT_PATH = (
    "experiments/E18_pv_future_o1_recorded_replay_consistency/data/raw/none.csv"
)
FORMAL_CAPTURE_PATH = (
    "experiments/E18_pv_future_o1_recorded_replay_consistency/data/full_axis_capture"
)
RAW_FIELDS = ("elapsed time", "timestamp", "topic", "value")
INPUT_TOPIC = "/mc/ik/joint_states.position[$right_joint_id]"
OUTPUT_TOPIC = (
    "/mc/joint_controller/ruckig_joint_states."
    "interface_values[$right_joint_id].values[0]"
)
TARGET_ECHO_TOPIC = (
    "/mc/joint_controller/ruckig_joint_states."
    "interface_values[$right_joint_id].values[4]"
)
EXPECTED_TOPICS = (INPUT_TOPIC, OUTPUT_TOPIC, TARGET_ECHO_TOPIC)

CONTROL_DT_S = 0.001
SOURCE_NOMINAL_DT_S = 0.01
SEGMENT_GAP_S = 1.0
GARBAGE_EXCLUSION_S = 3.0
MAX_VELOCITY_RAD_S = 4.1
MAX_ACCELERATION_RAD_S2 = 16.2
MAX_JERK_RAD_S3 = 4000.0
NUMERICAL_IDENTITY_TOLERANCE_RAD = 1e-12
MAX_LAG_TICKS = 20

METHOD_ID = "pv_pred_backward_o1_kp1"
PRIMARY_EXECUTION_ID = "update_target_callback_and_control_loop"
EXECUTION_IDS = (
    PRIMARY_EXECUTION_ID,
    "update_control_loop_only",
    "calculate_control_loop_only",
)
EXECUTION_LABELS = {
    PRIMARY_EXECUTION_ID: "update: target callback + control loop",
    "update_control_loop_only": "update: control loop only",
    "calculate_control_loop_only": "calculate: control loop only",
}


@dataclass(frozen=True)
class TopicSeries:
    elapsed_time_s: np.ndarray
    timestamp_s: np.ndarray
    position_rad: np.ndarray

    @property
    def count(self) -> int:
        return int(self.elapsed_time_s.size)


@dataclass(frozen=True)
class NoneSnapshotData:
    source: TopicSeries
    output: TopicSeries
    target_echo: TopicSeries
    raw_row_count: int
    source_segment_count: int
    selected_segment_index: int
    segment_start_s: float
    segment_end_s: float
    observation_end_s: float
    analysis_valid_start_s: float


@dataclass(frozen=True)
class TickMapping:
    tick_index: np.ndarray
    lattice_elapsed_time_s: np.ndarray
    residual_s: np.ndarray
    max_tick: int


def _normalize_topic(topic: str) -> str:
    value = str(topic).strip()
    return value[2:] if value.startswith("/A/") else value


def _series(rows: Sequence[tuple[float, float, float]], topic: str) -> TopicSeries:
    if not rows:
        raise ValueError(f"required topic is empty: {topic}")
    values = np.asarray(rows, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3 or not np.all(np.isfinite(values)):
        raise ValueError(f"topic contains invalid numeric values: {topic}")
    if np.any(np.diff(values[:, 0]) <= 0.0):
        raise ValueError(f"elapsed time is not strictly increasing: {topic}")
    if np.any(np.diff(values[:, 1]) <= 0.0):
        raise ValueError(f"timestamp is not strictly increasing: {topic}")
    return TopicSeries(
        elapsed_time_s=values[:, 0],
        timestamp_s=values[:, 1],
        position_rad=values[:, 2],
    )


def load_none_snapshot(path: str | Path) -> NoneSnapshotData:
    """Load the final No-mode source segment and its paired observations."""

    source_path = Path(path).resolve()
    grouped: dict[str, list[tuple[float, float, float]]] = {
        topic: [] for topic in EXPECTED_TOPICS
    }
    row_count = 0
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != RAW_FIELDS:
            raise ValueError(f"{source_path} must use header {','.join(RAW_FIELDS)}")
        for row_count, row in enumerate(reader, start=1):
            topic = _normalize_topic(str(row["topic"]))
            if topic not in grouped:
                raise ValueError(f"unexpected topic in {source_path}: {topic}")
            try:
                values = (
                    float(row["elapsed time"]),
                    float(row["timestamp"]),
                    float(row["value"]),
                )
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"invalid numeric row {row_count} in {source_path}"
                ) from error
            if not all(math.isfinite(item) for item in values):
                raise ValueError(f"non-finite row {row_count} in {source_path}")
            grouped[topic].append(values)

    complete = {
        topic: _series(rows, topic) for topic, rows in grouped.items()
    }
    source = complete[INPUT_TOPIC]
    boundaries = np.flatnonzero(np.diff(source.elapsed_time_s) > SEGMENT_GAP_S) + 1
    segments = np.split(np.arange(source.count), boundaries)
    selected = segments[-1]
    selected_source = TopicSeries(
        elapsed_time_s=source.elapsed_time_s[selected],
        timestamp_s=source.timestamp_s[selected],
        position_rad=source.position_rad[selected],
    )
    source_dt = (
        float(np.median(np.diff(selected_source.elapsed_time_s)))
        if selected_source.count > 1
        else SOURCE_NOMINAL_DT_S
    )
    start = float(selected_source.elapsed_time_s[0])
    end = float(selected_source.elapsed_time_s[-1])
    observation_end = end + source_dt

    def select_window(series: TopicSeries) -> TopicSeries:
        mask = (series.elapsed_time_s >= start) & (
            series.elapsed_time_s <= observation_end
        )
        return TopicSeries(
            elapsed_time_s=series.elapsed_time_s[mask],
            timestamp_s=series.timestamp_s[mask],
            position_rad=series.position_rad[mask],
        )

    output = select_window(complete[OUTPUT_TOPIC])
    target_echo = select_window(complete[TARGET_ECHO_TOPIC])
    if output.count == 0:
        raise ValueError("selected No-mode segment has no recorded output observations")
    if output.count != target_echo.count:
        raise ValueError("output and target-echo topics must have equal row counts")
    if not (
        np.array_equal(output.elapsed_time_s, target_echo.elapsed_time_s)
        and np.array_equal(output.timestamp_s, target_echo.timestamp_s)
    ):
        raise ValueError("output and target-echo topics must have paired timestamps")
    return NoneSnapshotData(
        source=selected_source,
        output=output,
        target_echo=target_echo,
        raw_row_count=row_count,
        source_segment_count=len(segments),
        selected_segment_index=len(segments) - 1,
        segment_start_s=start,
        segment_end_s=end,
        observation_end_s=observation_end,
        analysis_valid_start_s=min(end, start + GARBAGE_EXCLUSION_S),
    )


def map_output_ticks(
    elapsed_time_s: Sequence[float], *, dt_s: float = CONTROL_DT_S
) -> TickMapping:
    """Map observations to unique nearest ticks without interpolation."""

    elapsed = np.asarray(elapsed_time_s, dtype=np.float64)
    if elapsed.ndim != 1 or elapsed.size == 0 or not np.all(np.isfinite(elapsed)):
        raise ValueError("output elapsed times must be a non-empty finite vector")
    if np.any(np.diff(elapsed) <= 0.0):
        raise ValueError("output elapsed times must be strictly increasing")
    dt = float(dt_s)
    if not math.isfinite(dt) or dt <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    zero_based = np.rint((elapsed - elapsed[0]) / dt).astype(np.int64)
    ticks = zero_based + 1
    if np.any(np.diff(ticks) <= 0):
        raise ValueError("output samples do not map to unique increasing ticks")
    lattice = elapsed[0] + zero_based.astype(np.float64) * dt
    residual = elapsed - lattice
    tolerance = 0.5 * dt + max(1e-12, dt * 1e-9)
    if np.any(np.abs(residual) > tolerance):
        raise ValueError("output sample is more than half a control period from its tick")
    return TickMapping(ticks, lattice, residual, int(ticks[-1]))


def build_future_o1_target_events(data: NoneSnapshotData) -> list[dict[str, Any]]:
    predictor = make_predictor(
        "future_backward_fd_o1", nominal_dt=SOURCE_NOMINAL_DT_S
    )
    predictor.reset()
    events: list[dict[str, Any]] = []
    for index, position in enumerate(data.source.position_rad):
        nominal_time = index * SOURCE_NOMINAL_DT_S
        posterior = TimedState(
            position=[float(position)],
            velocity=[0.0],
            acceleration=[0.0],
            state_time=nominal_time,
            available_time=nominal_time,
            method="position_only",
        )
        prediction = predictor.predict(posterior, SOURCE_NOMINAL_DT_S)
        events.append(
            {
                "method_id": METHOD_ID,
                "source_index": index,
                "source_elapsed_time_s": float(data.source.elapsed_time_s[index]),
                "source_timestamp_s": float(data.source.timestamp_s[index]),
                "source_position_rad": float(position),
                "target_position_rad": float(prediction.position[0]),
                "target_velocity_rad_s": float(prediction.velocity[0]),
                "target_acceleration_rad_s2": 0.0,
                "prediction_status": prediction.status,
                "prediction_startup": prediction.startup,
            }
        )
    return events


def _target_array(event: Mapping[str, Any]) -> np.ndarray:
    return np.asarray(
        [[
            event["target_position_rad"],
            event["target_velocity_rad_s"],
            event["target_acceleration_rad_s2"],
        ]],
        dtype=np.float64,
    )


def _validate_states(current: np.ndarray, target: np.ndarray) -> None:
    if current.shape != (1, 3) or target.shape != (1, 3):
        raise ValueError("Ruckig states must have shape (1, 3)")
    if not np.all(np.isfinite(current)) or not np.all(np.isfinite(target)):
        raise ValueError("Ruckig states must be finite")
    if abs(float(target[0, 1])) > MAX_VELOCITY_RAD_S + 1e-12:
        raise ValueError("PV Future-O1 target exceeds max velocity")


class _UpdateStepper:
    def __init__(self, max_acceleration_rad_s2: float) -> None:
        self.otg = Ruckig(1, CONTROL_DT_S)
        self.inp = InputParameter(1)
        self.out = OutputParameter(1)
        self.max_acceleration_rad_s2 = float(max_acceleration_rad_s2)

    def step(
        self, current: np.ndarray, target: np.ndarray
    ) -> tuple[np.ndarray, dict[str, Any]]:
        _validate_states(current, target)
        self.inp.current_position = current[:, 0].tolist()
        self.inp.current_velocity = current[:, 1].tolist()
        self.inp.current_acceleration = current[:, 2].tolist()
        self.inp.target_position = target[:, 0].tolist()
        self.inp.target_velocity = target[:, 1].tolist()
        self.inp.target_acceleration = target[:, 2].tolist()
        self.inp.max_velocity = [MAX_VELOCITY_RAD_S]
        self.inp.max_acceleration = [self.max_acceleration_rad_s2]
        self.inp.max_jerk = [MAX_JERK_RAD_S3]
        self.inp.synchronization = Synchronization.No
        result = self.otg.update(self.inp, self.out)
        if int(result) < 0:
            raise RuntimeError(f"Ruckig update failed with result {int(result)}")
        command = np.asarray(
            [[
                self.out.new_position[0],
                self.out.new_velocity[0],
                self.out.new_acceleration[0],
            ]],
            dtype=np.float64,
        )
        return command, {
            "solver_status": str(result),
            "trajectory_duration_s": float(self.out.trajectory.duration),
            "new_calculation": bool(self.out.new_calculation),
        }


class _CalculateStepper:
    def __init__(self, max_acceleration_rad_s2: float) -> None:
        self.otg = Ruckig(1, CONTROL_DT_S)
        self.max_acceleration_rad_s2 = float(max_acceleration_rad_s2)

    def step(
        self, current: np.ndarray, target: np.ndarray
    ) -> tuple[np.ndarray, dict[str, Any]]:
        _validate_states(current, target)
        inp = InputParameter(1)
        trajectory = Trajectory(1)
        inp.current_position = current[:, 0].tolist()
        inp.current_velocity = current[:, 1].tolist()
        inp.current_acceleration = current[:, 2].tolist()
        inp.target_position = target[:, 0].tolist()
        inp.target_velocity = target[:, 1].tolist()
        inp.target_acceleration = target[:, 2].tolist()
        inp.max_velocity = [MAX_VELOCITY_RAD_S]
        inp.max_acceleration = [self.max_acceleration_rad_s2]
        inp.max_jerk = [MAX_JERK_RAD_S3]
        inp.synchronization = Synchronization.No
        result = self.otg.calculate(inp, trajectory)
        if int(result) < 0:
            raise RuntimeError(f"Ruckig calculate failed with result {int(result)}")
        duration = float(trajectory.duration)
        if duration <= 1e-15:
            command = np.array(target, copy=True)
        else:
            position, velocity, acceleration = trajectory.at_time(
                min(CONTROL_DT_S, duration)
            )
            command = np.column_stack((position, velocity, acceleration))
        if duration < CONTROL_DT_S:
            command = np.array(target, copy=True)
        return command, {
            "solver_status": str(result),
            "trajectory_duration_s": duration,
            "new_calculation": True,
        }


def _call_row(
    *,
    execution_id: str,
    call_seq: int,
    tick_index: int,
    call_elapsed_time_s: float,
    callback_source: str,
    event: Mapping[str, Any],
    current_before: np.ndarray,
    current_after: np.ndarray,
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "method_id": event.get("method_id", METHOD_ID),
        "execution_id": execution_id,
        "call_seq": call_seq,
        "tick_index": tick_index,
        "call_elapsed_time_s": call_elapsed_time_s,
        "callback_source": callback_source,
        "active_source_index": event["source_index"],
        "target_position_rad": event["target_position_rad"],
        "target_velocity_rad_s": event["target_velocity_rad_s"],
        "current_position_rad": float(current_before[0, 0]),
        "current_velocity_rad_s": float(current_before[0, 1]),
        "current_acceleration_rad_s2": float(current_before[0, 2]),
        "output_position_rad": float(current_after[0, 0]),
        "output_velocity_rad_s": float(current_after[0, 1]),
        "output_acceleration_rad_s2": float(current_after[0, 2]),
        "solver_status": diagnostics["solver_status"],
        "trajectory_duration_s": diagnostics["trajectory_duration_s"],
        "new_calculation": diagnostics["new_calculation"],
    }


def run_replay_execution(
    data: NoneSnapshotData,
    events: Sequence[Mapping[str, Any]],
    mapping: TickMapping,
    *,
    execution_id: str,
    max_acceleration_rad_s2: float = MAX_ACCELERATION_RAD_S2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay one declared Ruckig execution-semantics hypothesis."""

    if execution_id not in EXECUTION_IDS:
        raise ValueError(f"unknown replay execution: {execution_id}")
    acceleration_limit = float(max_acceleration_rad_s2)
    if not math.isfinite(acceleration_limit) or acceleration_limit <= 0.0:
        raise ValueError("max_acceleration_rad_s2 must be finite and positive")
    callback_calls = execution_id == PRIMARY_EXECUTION_ID
    stepper: _UpdateStepper | _CalculateStepper
    stepper = (
        _CalculateStepper(acceleration_limit)
        if execution_id == "calculate_control_loop_only"
        else _UpdateStepper(acceleration_limit)
    )
    current = np.zeros((1, 3), dtype=np.float64)
    active_event: Mapping[str, Any] | None = None
    event_index = 0
    call_seq = 0
    replay_rows: list[dict[str, Any]] = []
    call_rows: list[dict[str, Any]] = []
    anchor = float(data.output.elapsed_time_s[0])
    tolerance = max(1e-12, CONTROL_DT_S * 1e-9)

    def take_step(
        callback_source: str,
        tick_index: int,
        call_elapsed_time_s: float,
    ) -> None:
        nonlocal call_seq, current
        if active_event is None:
            raise ValueError("no source target is available for a replay Ruckig call")
        before = np.array(current, copy=True)
        current, diagnostics = stepper.step(current, _target_array(active_event))
        call_rows.append(
            _call_row(
                execution_id=execution_id,
                call_seq=call_seq,
                tick_index=tick_index,
                call_elapsed_time_s=call_elapsed_time_s,
                callback_source=callback_source,
                event=active_event,
                current_before=before,
                current_after=current,
                diagnostics=diagnostics,
            )
        )
        call_seq += 1

    for tick in range(1, mapping.max_tick + 1):
        command_elapsed = anchor + (tick - 1) * CONTROL_DT_S
        applied_events: list[Mapping[str, Any]] = []
        while (
            event_index < len(events)
            and float(events[event_index]["source_elapsed_time_s"])
            <= command_elapsed + tolerance
        ):
            active_event = events[event_index]
            applied_events.append(active_event)
            event_index += 1
            if callback_calls:
                take_step(
                    "target_callback",
                    tick,
                    float(active_event["source_elapsed_time_s"]),
                )
        if active_event is None:
            raise ValueError("no source target is available at the first control tick")

        # At reset, the first target callback produces the first exposed state.
        # Thereafter each target callback and each 1 ms loop invocation are kept
        # as distinct calls.  This is an explicit deployment hypothesis until a
        # call-by-call controller capture is supplied.
        skip_first_control_call = tick == 1 and callback_calls and applied_events
        if not skip_first_control_call:
            take_step("control_loop", tick, command_elapsed)

        replay_rows.append(
            {
                "method_id": active_event.get("method_id", METHOD_ID),
                "execution_id": execution_id,
                "tick_index": tick,
                "command_elapsed_time_s": command_elapsed,
                "source_events_applied": len(applied_events),
                "target_source_index": active_event["source_index"],
                "held_source_position_rad": active_event["source_position_rad"],
                "target_position_rad": active_event["target_position_rad"],
                "target_velocity_rad_s": active_event["target_velocity_rad_s"],
                "target_acceleration_rad_s2": 0.0,
                "target_startup": active_event["prediction_startup"],
                "command_position_rad": float(current[0, 0]),
                "command_velocity_rad_s": float(current[0, 1]),
                "command_acceleration_rad_s2": float(current[0, 2]),
                "replay_call_count_through_tick": call_seq,
            }
        )
    return replay_rows, call_rows


def build_recorded_replay_comparison(
    data: NoneSnapshotData,
    mapping: TickMapping,
    replays: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_elapsed = data.source.elapsed_time_s
    for output_index, tick in enumerate(mapping.tick_index):
        elapsed = float(data.output.elapsed_time_s[output_index])
        recorded_position = float(data.output.position_rad[output_index])
        row: dict[str, Any] = {
            "output_index": output_index,
            "tick_index": int(tick),
            "recorded_elapsed_time_s": elapsed,
            "elapsed_from_segment_start_s": elapsed - data.segment_start_s,
            "lattice_elapsed_time_s": float(mapping.lattice_elapsed_time_s[output_index]),
            "tick_mapping_residual_s": float(mapping.residual_s[output_index]),
            "analysis_valid": elapsed >= data.analysis_valid_start_s,
            "recorded_output_position_rad": recorded_position,
            "target_echo_position_rad": float(data.target_echo.position_rad[output_index]),
            "nearest_source_event_distance_s": float(np.min(np.abs(source_elapsed - elapsed))),
        }
        for execution_id, replay_rows in replays.items():
            replay = replay_rows[int(tick) - 1]
            simulated = float(replay["command_position_rad"])
            error = simulated - recorded_position
            row[f"{execution_id}_position_rad"] = simulated
            row[f"{execution_id}_minus_recorded_rad"] = error
            row[f"{execution_id}_abs_error_rad"] = abs(error)
        primary = replays[PRIMARY_EXECUTION_ID][int(tick) - 1]
        row["target_source_index"] = primary["target_source_index"]
        row["held_source_position_rad"] = primary["held_source_position_rad"]
        row["target_position_rad"] = primary["target_position_rad"]
        row["target_velocity_rad_s"] = primary["target_velocity_rad_s"]
        rows.append(row)
    return rows


def build_target_echo_audit(
    data: NoneSnapshotData,
    mapping: TickMapping,
    primary_replay: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source_by_value: dict[float, list[int]] = {}
    for index, value in enumerate(data.source.position_rad):
        source_by_value.setdefault(float(value), []).append(index)
    rows: list[dict[str, Any]] = []
    previous_echo: float | None = None
    for output_index, tick in enumerate(mapping.tick_index):
        replay = primary_replay[int(tick) - 1]
        elapsed = float(data.output.elapsed_time_s[output_index])
        echo = float(data.target_echo.position_rad[output_index])
        transition = previous_echo is None or echo != previous_echo
        source_index: int | None = None
        latency: float | None = None
        if transition:
            candidates = [
                index
                for index in source_by_value.get(echo, [])
                if float(data.source.elapsed_time_s[index]) <= elapsed + 1e-12
            ]
            if candidates:
                source_index = candidates[-1]
                latency = elapsed - float(data.source.elapsed_time_s[source_index])
        scheduled = float(replay["held_source_position_rad"])
        rows.append(
            {
                "output_index": output_index,
                "tick_index": int(tick),
                "recorded_elapsed_time_s": elapsed,
                "analysis_valid": elapsed >= data.analysis_valid_start_s,
                "target_echo_position_rad": echo,
                "scheduled_source_position_rad": scheduled,
                "exact_match": echo == scheduled,
                "echo_transition": transition,
                "transition_source_index": source_index,
                "transition_latency_s": latency,
            }
        )
        previous_echo = echo
    return rows


def _error_metrics(errors: np.ndarray) -> dict[str, Any]:
    if errors.size == 0:
        raise ValueError("cannot score an empty error vector")
    absolute = np.abs(errors)
    return {
        "sample_count": int(errors.size),
        "position_rmse_rad": float(np.sqrt(np.mean(errors**2))),
        "position_mae_rad": float(np.mean(absolute)),
        "position_bias_rad": float(np.mean(errors)),
        "position_p95_abs_error_rad": float(
            np.quantile(absolute, 0.95, method="linear")
        ),
        "position_max_abs_error_rad": float(np.max(absolute)),
        "numerically_identical": bool(
            np.max(absolute) <= NUMERICAL_IDENTITY_TOLERANCE_RAD
        ),
        "within_identity_tolerance_count": int(
            np.sum(absolute <= NUMERICAL_IDENTITY_TOLERANCE_RAD)
        ),
        "within_identity_tolerance_fraction": float(
            np.mean(absolute <= NUMERICAL_IDENTITY_TOLERANCE_RAD)
        ),
    }


def build_execution_metrics(
    comparison: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    for execution_id in EXECUTION_IDS:
        execution_summary: dict[str, Any] = {}
        for scope in ("all_observations", "analysis_valid"):
            selected = [
                row
                for row in comparison
                if scope == "all_observations" or bool(row["analysis_valid"])
            ]
            errors = np.asarray(
                [row[f"{execution_id}_minus_recorded_rad"] for row in selected],
                dtype=np.float64,
            )
            metrics = _error_metrics(errors)
            rows.append(
                {
                    "method_id": METHOD_ID,
                    "execution_id": execution_id,
                    "scope": scope,
                    **metrics,
                }
            )
            execution_summary[scope] = metrics
        summary[execution_id] = execution_summary
    return rows, summary


def build_lag_scan(
    data: NoneSnapshotData,
    mapping: TickMapping,
    replay: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    simulated = np.asarray(
        [row["command_position_rad"] for row in replay], dtype=np.float64
    )
    valid_observation = data.output.elapsed_time_s >= data.analysis_valid_start_s
    rows: list[dict[str, Any]] = []
    for shift in range(-MAX_LAG_TICKS, MAX_LAG_TICKS + 1):
        shifted_ticks = mapping.tick_index + shift
        usable = (
            valid_observation
            & (shifted_ticks >= 1)
            & (shifted_ticks <= simulated.size)
        )
        errors = simulated[shifted_ticks[usable] - 1] - data.output.position_rad[usable]
        rows.append(
            {
                "simulation_shift_ticks": shift,
                "simulation_shift_s": shift * CONTROL_DT_S,
                "sample_count": int(errors.size),
                "position_rmse_rad": float(np.sqrt(np.mean(errors**2))),
            }
        )
    return rows


def _data_quality(
    data: NoneSnapshotData,
    mapping: TickMapping,
    echo_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    output_gaps = np.diff(mapping.tick_index) - 1
    source_dt = np.diff(data.source.elapsed_time_s)
    latencies = np.asarray(
        [
            row["transition_latency_s"]
            for row in echo_rows
            if row["echo_transition"] and row["transition_latency_s"] is not None
        ],
        dtype=np.float64,
    )
    exact = np.asarray([row["exact_match"] for row in echo_rows], dtype=bool)
    expected_first_position = MAX_JERK_RAD_S3 * CONTROL_DT_S**3 / 6.0
    return {
        "formal_parity_eligible": False,
        "raw_row_count": data.raw_row_count,
        "source_segment_count": data.source_segment_count,
        "selected_segment_index": data.selected_segment_index,
        "selected_source_count": data.source.count,
        "selected_output_count": data.output.count,
        "analysis_valid_output_count": int(
            np.sum(data.output.elapsed_time_s >= data.analysis_valid_start_s)
        ),
        "segment_start_s": data.segment_start_s,
        "segment_end_s": data.segment_end_s,
        "analysis_valid_start_s": data.analysis_valid_start_s,
        "garbage_exclusion_s": GARBAGE_EXCLUSION_S,
        "source_dt_min_s": float(np.min(source_dt)),
        "source_dt_median_s": float(np.median(source_dt)),
        "source_dt_max_s": float(np.max(source_dt)),
        "mapped_control_tick_count": mapping.max_tick,
        "observed_tick_coverage_fraction": data.output.count / mapping.max_tick,
        "missing_control_tick_count": mapping.max_tick - data.output.count,
        "output_gap_count": int(np.sum(output_gaps > 0)),
        "largest_output_gap_ticks": int(np.max(output_gaps)),
        "tick_mapping_max_abs_residual_s": float(np.max(np.abs(mapping.residual_s))),
        "target_echo_exact_match_fraction": float(np.mean(exact)),
        "target_echo_transition_count": int(
            sum(bool(row["echo_transition"]) for row in echo_rows)
        ),
        "target_echo_transition_latency_median_s": (
            float(np.median(latencies)) if latencies.size else None
        ),
        "target_echo_transition_latency_p95_s": (
            float(np.quantile(latencies, 0.95, method="linear"))
            if latencies.size
            else None
        ),
        "first_recorded_output_position_rad": float(data.output.position_rad[0]),
        "zero_state_jerk_limited_first_position_rad": expected_first_position,
        "first_position_minus_zero_state_expectation_rad": float(
            data.output.position_rad[0] - expected_first_position
        ),
    }


def _load_validation_module(experiment_root: Path) -> Any:
    name = "_e18_validation_pipeline_for_none_replay"
    if name in sys.modules:
        return sys.modules[name]
    path = experiment_root / "validation_pipeline.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load E18 validation module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run_formal_no_parity(
    *,
    experiment_root: Path,
    capture_root: Path,
    run_directory: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    formal_directory = run_directory / "formal_no_parity"
    formal_directory.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    mismatch: Mapping[str, Any] | None = None
    validation = _load_validation_module(experiment_root)

    if not (capture_root / "capture_manifest.json").is_file():
        reason = (
            "No-mode controller-internal full-axis capture is absent; the current "
            "right-axis position snapshot cannot establish formal parity"
        )
        data_rows = [{
            "mode": "No",
            "status": "not_evaluable",
            "formal_gate_eligible": False,
            "capture_root": capture_root.as_posix(),
            "reason": reason,
            "missing_requirements": (
                "full-axis current/target/output PVA",
                "complete call_seq and callback_source",
                "per-axis constraints and options",
                "analysis_valid and run_reset",
                "deployed Ruckig build identity",
            ),
        }]
        gate_rows = [
            {
                "mode": "No",
                "gate": gate,
                "status": "not_evaluable",
                "evaluated_point_count": 0,
                "reason": reason,
            }
            for gate in ("target_builder", "solver_step", "closed_loop")
        ]
        mismatch = {
            "classification": "data_sufficiency",
            "code": "right_axis_snapshot_only",
            "message": reason,
        }
        status = "not_evaluable"
    else:
        try:
            capture = validation.load_full_axis_capture(capture_root)
            local_build = validation.collect_local_ruckig_build()
            data_rows = list(
                validation.validate_no_data_sufficiency(
                    capture, local_build=local_build
                )
            )
            report = validation.run_parity(capture, modes=("No",))
            result = report.modes[0]
            gates = (
                result.target_builder,
                result.solver_step,
                result.closed_loop,
            )
            gate_rows = [
                {
                    "run_id": result.run_id,
                    "mode": result.mode,
                    "gate": gate.gate,
                    "status": gate.status,
                    "evaluated_point_count": gate.evaluated_point_count,
                    "bitwise_equal": gate.bitwise_equal,
                    "max_abs_errors": gate.max_abs_errors,
                    "reason": gate.reason,
                }
                for gate in gates
            ]
            for gate in gates:
                path = formal_directory / f"{gate.gate}_parity.csv"
                write_rows_csv(path, gate.rows)
                outputs[f"formal_no_{gate.gate}"] = path.relative_to(
                    run_directory
                ).as_posix()
            mismatch = report.first_mismatch
            status = "pass" if result.passed else "fail"
        except (validation.CaptureValidationError, ValueError, OSError) as error:
            if isinstance(error, validation.CaptureValidationError):
                mismatch = error.as_dict()
            else:
                mismatch = {
                    "classification": "data_sufficiency",
                    "code": "capture_read_error",
                    "message": f"{type(error).__name__}: {error}",
                }
            data_rows = [{
                "mode": "No",
                "status": "not_evaluable",
                "formal_gate_eligible": False,
                "capture_root": capture_root.as_posix(),
                "reason": mismatch["message"],
            }]
            gate_rows = [
                {
                    "mode": "No",
                    "gate": gate,
                    "status": "not_evaluable",
                    "evaluated_point_count": 0,
                    "reason": mismatch["message"],
                }
                for gate in ("target_builder", "solver_step", "closed_loop")
            ]
            status = "not_evaluable"

    data_path = formal_directory / "data_sufficiency.csv"
    gate_path = formal_directory / "gate_summary.csv"
    write_rows_csv(data_path, data_rows)
    write_rows_csv(gate_path, gate_rows)
    outputs["formal_no_data_sufficiency"] = data_path.relative_to(
        run_directory
    ).as_posix()
    outputs["formal_no_gate_summary"] = gate_path.relative_to(
        run_directory
    ).as_posix()
    if mismatch is not None:
        mismatch_path = formal_directory / "first_mismatch.json"
        write_json(mismatch_path, mismatch)
        outputs["formal_no_first_mismatch"] = mismatch_path.relative_to(
            run_directory
        ).as_posix()
    return {
        "status": status,
        "capture_root": capture_root.as_posix(),
        "gate_summary": gate_rows,
        "first_mismatch": mismatch,
        "synchronization_ranking_generated": False,
        "p_only_pv_analysis_generated": False,
    }, outputs


def _write_figures(
    run_directory: Path,
    data: NoneSnapshotData,
    comparison: Sequence[Mapping[str, Any]],
    replays: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    observed_time = np.asarray(
        [row["elapsed_from_segment_start_s"] for row in comparison], dtype=float
    )
    recorded = np.asarray(
        [row["recorded_output_position_rad"] for row in comparison], dtype=float
    )
    valid = np.asarray([row["analysis_valid"] for row in comparison], dtype=bool)
    replay_time = np.asarray(
        [
            row["command_elapsed_time_s"] - data.segment_start_s
            for row in replays[PRIMARY_EXECUTION_ID]
        ],
        dtype=float,
    )
    primary = np.asarray(
        [row["command_position_rad"] for row in replays[PRIMARY_EXECUTION_ID]],
        dtype=float,
    )
    raw_target = np.asarray(
        [row["held_source_position_rad"] for row in replays[PRIMARY_EXECUTION_ID]],
        dtype=float,
    )
    future_target = np.asarray(
        [row["target_position_rad"] for row in replays[PRIMARY_EXECUTION_ID]],
        dtype=float,
    )
    primary_at_observations = np.asarray(
        [row[f"{PRIMARY_EXECUTION_ID}_position_rad"] for row in comparison], dtype=float
    )
    primary_error = primary_at_observations - recorded

    observed_raw_target = np.asarray(
        [row["held_source_position_rad"] for row in comparison], dtype=float
    )
    observed_future_target = np.asarray(
        [row["target_position_rad"] for row in comparison], dtype=float
    )
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(12, 9),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": (1.35, 1.0, 1.0)},
    )
    axes[0].plot(
        replay_time,
        raw_target,
        color="#666666",
        linestyle="--",
        linewidth=0.9,
        label="source position",
    )
    axes[0].plot(
        replay_time,
        future_target,
        color="#E69F00",
        linestyle=":",
        linewidth=1.0,
        label="raw target P — PV Future-O1",
    )
    axes[0].scatter(
        observed_time,
        recorded,
        s=2.2,
        color="#222222",
        label="recorded output — Sync.No",
        zorder=4,
    )
    axes[0].plot(
        replay_time,
        primary,
        color="#4477AA",
        linewidth=0.85,
        label="replay output — primary execution",
        zorder=3,
    )
    axes[0].set_ylabel("position [rad]")
    axes[0].set_title("Source, raw target, recorded output, and replay output")
    axes[0].legend(frameon=False, fontsize=8, ncol=2, loc="lower right")

    axes[1].plot(
        observed_time,
        recorded - observed_raw_target,
        color="#222222",
        linewidth=0.75,
        label="recorded output - source position",
    )
    axes[1].plot(
        observed_time,
        primary_at_observations - observed_raw_target,
        color="#4477AA",
        linewidth=0.75,
        label="replay output - source position",
    )
    axes[1].plot(
        observed_time,
        observed_future_target - observed_raw_target,
        color="#E69F00",
        linestyle=":",
        linewidth=0.8,
        label="raw target P - source position",
    )
    axes[1].axhline(0.0, color="#888888", linewidth=0.65)
    axes[1].set_ylabel("relative to raw target [rad]")
    axes[1].legend(frameon=False, fontsize=8, ncol=3, loc="lower right")

    axes[2].scatter(
        observed_time,
        primary_error,
        s=2.2,
        color="#D55E00",
        label="replay output - recorded output",
    )
    axes[2].axhline(0.0, color="#777777", linewidth=0.7)
    axes[2].set(
        xlabel="elapsed from reset segment [s]",
        ylabel="replay - recorded [rad]",
    )
    axes[2].legend(frameon=False, fontsize=8, loc="lower right")
    for axis in axes:
        axis.axvspan(0.0, GARBAGE_EXCLUSION_S, color="#BBBBBB", alpha=0.2)
        axis.grid(axis="y", color="#DDDDDD", linewidth=0.5, alpha=0.55)
    figure.savefig(figures / "target_recorded_replay_comparison.png", dpi=200)
    figure.savefig(figures / "target_recorded_replay_comparison.svg")
    plt.close(figure)

    figure, axes = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True, constrained_layout=True
    )
    axes[0].scatter(
        observed_time,
        recorded,
        s=2.0,
        color="#222222",
        label="recorded output — Sync.No",
    )
    axes[0].plot(
        replay_time,
        primary,
        color="#4477AA",
        linewidth=0.8,
        label="replay output — primary execution",
    )
    axes[0].set_ylabel("position [rad]")
    axes[0].legend(frameon=False)
    axes[1].scatter(observed_time, primary_error, s=2.0, color="#D55E00")
    axes[1].axhline(0.0, color="#777777", linewidth=0.7)
    axes[1].set(
        xlabel="elapsed from reset segment [s]",
        ylabel="replay - recorded [rad]",
    )
    for axis in axes:
        axis.axvspan(0.0, GARBAGE_EXCLUSION_S, color="#BBBBBB", alpha=0.2)
    figure.savefig(figures / "recorded_vs_replay_position.png", dpi=200)
    figure.savefig(figures / "recorded_vs_replay_position.svg")
    plt.close(figure)

    startup_mask = observed_time <= 0.08
    figure, axis = plt.subplots(figsize=(11, 4.8), constrained_layout=True)
    axis.scatter(
        observed_time[startup_mask],
        recorded[startup_mask],
        s=14,
        color="#222222",
        label="recorded output",
    )
    colors = ("#4477AA", "#009E73", "#AA3377")
    for execution_id, color in zip(EXECUTION_IDS, colors):
        values = np.asarray(
            [row[f"{execution_id}_position_rad"] for row in comparison], dtype=float
        )
        axis.plot(
            observed_time[startup_mask],
            values[startup_mask],
            linewidth=1.0,
            color=color,
            label=EXECUTION_LABELS[execution_id],
        )
    for event_time in data.source.elapsed_time_s:
        relative = float(event_time - data.segment_start_s)
        if 0.0 <= relative <= 0.08:
            axis.axvline(relative, color="#999999", linewidth=0.5, alpha=0.45)
    axis.set(
        title="Startup and target-callback timing",
        xlabel="elapsed from reset segment [s]",
        ylabel="position [rad]",
    )
    axis.legend(frameon=False, fontsize=8)
    figure.savefig(figures / "execution_startup.png", dpi=200)
    figure.savefig(figures / "execution_startup.svg")
    plt.close(figure)

    valid_indices = np.flatnonzero(valid)
    worst = int(valid_indices[np.argmax(np.abs(primary_error[valid]))])
    worst_time = observed_time[worst]
    neighborhood_mask = np.abs(observed_time - worst_time) <= 0.06
    figure, axes = plt.subplots(
        2, 1, figsize=(11, 6.5), sharex=True, constrained_layout=True
    )
    axes[0].plot(
        observed_time[neighborhood_mask],
        observed_raw_target[neighborhood_mask],
        color="#666666",
        linestyle="--",
        linewidth=1.0,
        label="source position",
    )
    axes[0].plot(
        observed_time[neighborhood_mask],
        observed_future_target[neighborhood_mask],
        color="#E69F00",
        linestyle=":",
        linewidth=1.0,
        label="raw target P — PV Future-O1",
    )
    axes[0].scatter(
        observed_time[neighborhood_mask],
        recorded[neighborhood_mask],
        s=10,
        color="#222222",
        label="recorded output — Sync.No",
        zorder=4,
    )
    axes[0].plot(
        observed_time[neighborhood_mask],
        primary_at_observations[neighborhood_mask],
        color="#4477AA",
        label="replay output — primary execution",
        zorder=3,
    )
    axes[0].axvline(worst_time, color="#777777", linestyle="--", linewidth=0.7)
    axes[0].set_title("Largest replay-recorded error neighborhood (±60 ms)")
    axes[0].legend(frameon=False, fontsize=8, ncol=2)
    axes[0].set_ylabel("position [rad]")
    axes[1].plot(
        observed_time[neighborhood_mask],
        primary_error[neighborhood_mask],
        color="#D55E00",
        label="replay output - recorded output",
    )
    axes[1].axhline(0.0, color="#777777", linewidth=0.7)
    axes[1].axvline(worst_time, color="#777777", linestyle="--", linewidth=0.7)
    axes[1].set(
        xlabel="elapsed from reset segment [s]",
        ylabel="replay - recorded [rad]",
    )
    axes[1].legend(frameon=False, fontsize=8)
    figure.savefig(figures / "largest_recorded_replay_error.png", dpi=200)
    figure.savefig(figures / "largest_recorded_replay_error.svg")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(11, 5.2), constrained_layout=True)
    for execution_id, color in zip(EXECUTION_IDS, colors):
        errors = np.asarray(
            [row[f"{execution_id}_minus_recorded_rad"] for row in comparison],
            dtype=float,
        )
        axis.scatter(
            observed_time[valid],
            errors[valid],
            s=2.0,
            color=color,
            alpha=0.65,
            label=EXECUTION_LABELS[execution_id],
        )
    axis.axhline(0.0, color="#777777", linewidth=0.7)
    axis.set(
        title="Replay execution-semantics diagnostic (scored window)",
        xlabel="elapsed from reset segment [s]",
        ylabel="replay - recorded [rad]",
    )
    axis.legend(frameon=False, fontsize=8)
    figure.savefig(figures / "execution_semantics_error.png", dpi=200)
    figure.savefig(figures / "execution_semantics_error.svg")
    plt.close(figure)


def _output_hashes(run_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(run_directory).as_posix(): sha256_file(path)
        for path in sorted(run_directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def run_recorded_replay_consistency(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
    capture_root: str | Path | None = None,
) -> ExperimentResult:
    root = Path(project_root).resolve()
    experiment_root = root / "experiments" / DIRECTORY_NAME
    source_path = root / RAW_INPUT_PATH
    formal_capture = (
        root / FORMAL_CAPTURE_PATH
        if capture_root is None
        else Path(capture_root)
    )
    if not formal_capture.is_absolute():
        formal_capture = root / formal_capture
    formal_capture = formal_capture.resolve()

    data = load_none_snapshot(source_path)
    mapping = map_output_ticks(data.output.elapsed_time_s)
    events = build_future_o1_target_events(data)
    resolved_spec = {
        "primary_question": (
            "recorded Synchronization.No PV Future-O1 output versus same-method replay"
        ),
        "method_id": METHOD_ID,
        "raw_input_path": RAW_INPUT_PATH,
        "selected_source_segment": "last segment separated by source gaps > 1 s",
        "garbage_policy": (
            "simulate from zero-state reset; exclude first 3 s from scoring only"
        ),
        "initial_state": {"position": 0.0, "velocity": 0.0, "acceleration": 0.0},
        "future_o1_nominal_dt_s": SOURCE_NOMINAL_DT_S,
        "control_dt_s": CONTROL_DT_S,
        "limits": {
            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
            "max_acceleration_rad_s2": MAX_ACCELERATION_RAD_S2,
            "max_jerk_rad_s3": MAX_JERK_RAD_S3,
        },
        "synchronization": "No",
        "minimum_duration": None,
        "target_acceleration_rad_s2": 0.0,
        "primary_execution_id": PRIMARY_EXECUTION_ID,
        "diagnostic_executions": EXECUTION_IDS[1:],
        "recorded_output_alignment": "nearest unique 1 ms tick; no interpolation",
        "identity_tolerance_rad": NUMERICAL_IDENTITY_TOLERANCE_RAD,
        "formal_parity_scope": "No mode only; full axis and every Ruckig call",
        "post_parity_method_ranking": "out of scope for rebuilt E18",
    }
    run = start_compact_run(
        root,
        experiment_id=EXPERIMENT_ID,
        directory_name=DIRECTORY_NAME,
        title=TITLE,
        runs_root=runs_root,
        resolved_spec=resolved_spec,
    )

    replay_by_execution: dict[str, list[dict[str, Any]]] = {}
    call_rows: list[dict[str, Any]] = []
    for execution_id in EXECUTION_IDS:
        replay, calls = run_replay_execution(
            data, events, mapping, execution_id=execution_id
        )
        replay_by_execution[execution_id] = replay
        call_rows.extend(calls)
    comparison = build_recorded_replay_comparison(
        data,
        mapping,
        replay_by_execution,
    )
    echo_audit = build_target_echo_audit(
        data, mapping, replay_by_execution[PRIMARY_EXECUTION_ID]
    )
    metric_rows, metric_summary = build_execution_metrics(comparison)
    lag_rows = build_lag_scan(
        data, mapping, replay_by_execution[PRIMARY_EXECUTION_ID]
    )
    quality = _data_quality(data, mapping, echo_audit)
    formal, formal_outputs = _run_formal_no_parity(
        experiment_root=experiment_root,
        capture_root=formal_capture,
        run_directory=run.run_directory,
    )
    primary_metrics = metric_summary[PRIMARY_EXECUTION_ID]["analysis_valid"]
    valid_rows = [row for row in comparison if row["analysis_valid"]]
    first_mismatch = next(
        (
            {
                "output_index": row["output_index"],
                "tick_index": row["tick_index"],
                "recorded_elapsed_time_s": row["recorded_elapsed_time_s"],
                "recorded_output_position_rad": row[
                    "recorded_output_position_rad"
                ],
                "replay_output_position_rad": row[
                    f"{PRIMARY_EXECUTION_ID}_position_rad"
                ],
                "error_rad": row[
                    f"{PRIMARY_EXECUTION_ID}_minus_recorded_rad"
                ],
                "target_source_index": row["target_source_index"],
            }
            for row in valid_rows
            if row[f"{PRIMARY_EXECUTION_ID}_abs_error_rad"]
            > NUMERICAL_IDENTITY_TOLERANCE_RAD
        ),
        None,
    )
    best_lag = min(lag_rows, key=lambda row: float(row["position_rmse_rad"]))
    summary = {
        "operational_status": "completed",
        "formal_no_parity_status": formal["status"],
        "scientific_status": (
            "formal_parity_passed"
            if formal["status"] == "pass"
            else (
                "formal_parity_failed"
                if formal["status"] == "fail"
                else "formal_parity_not_evaluable"
            )
        ),
        "exploratory_right_axis_result": (
            "numerically_identical"
            if primary_metrics["numerically_identical"]
            else "different"
        ),
        "exploratory_primary_execution": PRIMARY_EXECUTION_ID,
        "exploratory_primary_metrics": primary_metrics,
        "exploratory_first_scored_mismatch": first_mismatch,
        "diagnostic_execution_metrics": metric_summary,
        "lag_diagnostic": {
            "best_simulation_shift_ticks": best_lag["simulation_shift_ticks"],
            "best_position_rmse_rad": best_lag["position_rmse_rad"],
            "primary_remains_zero_shift": True,
        },
        "data_quality": quality,
        "formal_no_parity": formal,
        "engineering_equivalence_assessed": False,
        "synchronization_ranking_generated": False,
        "p_only_pv_analysis_generated": False,
    }

    replay_rows = [
        row
        for execution_id in EXECUTION_IDS
        for row in replay_by_execution[execution_id]
    ]
    write_rows_csv(run.run_directory / "raw_target_events.csv", events)
    write_rows_csv(run.run_directory / "execution_output_trace.csv", replay_rows)
    write_rows_csv(run.run_directory / "execution_call_trace.csv", call_rows)
    write_rows_csv(
        run.run_directory / "recorded_replay_comparison.csv",
        comparison,
    )
    write_rows_csv(run.run_directory / "target_echo_audit.csv", echo_audit)
    write_rows_csv(run.run_directory / "execution_metrics.csv", metric_rows)
    write_rows_csv(run.run_directory / "replay_lag_scan.csv", lag_rows)
    write_json(run.run_directory / "data_quality.json", quality)
    write_json(run.run_directory / "summary.json", summary)
    (run.run_directory / "acceptance_summary.md").write_text(
        "# E18 Sync.No recorded/replay result\n\n"
        f"- Formal No parity: **{formal['status']}**\n"
        f"- Exploratory right-axis result: **{summary['exploratory_right_axis_result']}**\n"
        f"- Method: `{METHOD_ID}`\n"
        f"- Primary execution: `{PRIMARY_EXECUTION_ID}`\n"
        f"- Scored position RMSE: `{primary_metrics['position_rmse_rad']}` rad\n"
        f"- Scored max |error|: `{primary_metrics['position_max_abs_error_rad']}` rad\n"
        f"- Scored samples: `{primary_metrics['sample_count']}`\n"
        f"- Observed 1 ms tick coverage: `{quality['observed_tick_coverage_fraction']}`\n"
        f"- First output minus zero-state jerk expectation: "
        f"`{quality['first_position_minus_zero_state_expectation_rad']}` rad\n\n"
        "The first 3 s are excluded from scoring but are still simulated from "
        "the zero-state reset. Current right-axis data are exploratory only. "
        "A formal identity claim requires the No-mode full-axis, call-by-call "
        "capture to pass target-builder, solver-step, and closed-loop gates.\n",
        encoding="utf-8",
    )
    if create_figures:
        _write_figures(run.run_directory, data, comparison, replay_by_execution)
    else:
        (run.run_directory / "figures").mkdir(exist_ok=True)

    try:
        replay_ruckig_version = version("ruckig")
    except PackageNotFoundError:
        replay_ruckig_version = "unknown"
    run.manifest["inputs"] = {
        "recorded_sync_no_snapshot": {
            "path": RAW_INPUT_PATH,
            "sha256": sha256_file(source_path),
            "size_bytes": source_path.stat().st_size,
            "raw_row_count": data.raw_row_count,
            "topics": list(EXPECTED_TOPICS),
        },
        "formal_capture_root": formal_capture.as_posix(),
    }
    run.manifest["methods"] = {
        METHOD_ID: {
            "method_id": METHOD_ID,
            "conditioning_id": "none",
            "primary_execution_id": PRIMARY_EXECUTION_ID,
            "execution_ids": list(EXECUTION_IDS),
        }
    }
    run.manifest["replay"] = {
        "ruckig_version": replay_ruckig_version,
        "synchronization": "No",
        "resolved_assumptions": resolved_spec,
    }
    run.manifest["scientific_result"] = summary
    run.manifest["output_hashes"] = _output_hashes(run.run_directory)
    outputs = {
        "raw_target_events": "raw_target_events.csv",
        "execution_output_trace": "execution_output_trace.csv",
        "execution_call_trace": "execution_call_trace.csv",
        "recorded_replay_comparison": "recorded_replay_comparison.csv",
        "target_echo_audit": "target_echo_audit.csv",
        "execution_metrics": "execution_metrics.csv",
        "replay_lag_scan": "replay_lag_scan.csv",
        "data_quality": "data_quality.json",
        "summary": "summary.json",
        "acceptance_summary": "acceptance_summary.md",
        "figures": "figures",
        **formal_outputs,
    }
    return finish_compact_run(
        run,
        outputs=outputs,
        failures=(),
        required_failure_count=0,
    )


__all__ = [
    "EXPECTED_TOPICS",
    "INPUT_TOPIC",
    "NoneSnapshotData",
    "OUTPUT_TOPIC",
    "METHOD_ID",
    "PRIMARY_EXECUTION_ID",
    "RAW_INPUT_PATH",
    "TARGET_ECHO_TOPIC",
    "EXECUTION_IDS",
    "build_future_o1_target_events",
    "build_recorded_replay_comparison",
    "load_none_snapshot",
    "map_output_ticks",
    "run_recorded_replay_consistency",
    "run_replay_execution",
]
