"""E18 legacy replay of PV Future-O1 against a recorded controller output."""

from __future__ import annotations

import csv
import importlib.util
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
from ruckig import InputParameter, Ruckig, Trajectory

from otg_lab.confirmatory import (
    finish_compact_run,
    start_compact_run,
)
from otg_lab.experiment import ExperimentResult
from otg_lab.predictors import make_predictor
from otg_lab.runio import sha256_file, write_json, write_rows_csv
from otg_lab.types import TimedState

EXPERIMENT_ID = "E18"
SLUG = "pv_future_o1_recorded_replay_consistency"
DIRECTORY_NAME = f"{EXPERIMENT_ID}_{SLUG}"
TITLE = "PV Future-O1 legacy recorded-output replay"

RAW_INPUT_PATH = "data/raw/recorded_tasks/0801.csv"
RAW_FIELDS = ("elapsed time", "timestamp", "topic", "value")
INPUT_TOPIC = "/A/mc/ik/joint_states.position[$right_joint_id]"
OUTPUT_TOPIC = (
    "/A/mc/joint_controller/ruckig_joint_states."
    "interface_values[$right_joint_id].values[0]"
)
TARGET_ECHO_TOPIC = (
    "/A/mc/joint_controller/ruckig_joint_states."
    "interface_values[$right_joint_id].values[4]"
)
EXPECTED_TOPICS = (INPUT_TOPIC, OUTPUT_TOPIC, TARGET_ECHO_TOPIC)

CONTROL_DT_S = 0.001
SOURCE_NOMINAL_DT_S = 0.01
MAX_VELOCITY_RAD_S = 4.1
MAX_ACCELERATION_RAD_S2 = 16.2
MAX_JERK_RAD_S3 = 4000.0
NUMERICAL_IDENTITY_TOLERANCE_RAD = 1e-12
MAX_LAG_TICKS = 20


@dataclass(frozen=True)
class TopicSeries:
    elapsed_time_s: np.ndarray
    timestamp_s: np.ndarray
    position_rad: np.ndarray

    @property
    def count(self) -> int:
        return int(self.elapsed_time_s.size)


@dataclass(frozen=True)
class RecordedReplayData:
    source: TopicSeries
    output: TopicSeries
    target_echo: TopicSeries
    row_count: int


@dataclass(frozen=True)
class TickMapping:
    tick_index: np.ndarray
    lattice_elapsed_time_s: np.ndarray
    residual_s: np.ndarray
    max_tick: int


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


def load_recorded_replay_csv(path: str | Path) -> RecordedReplayData:
    """Load and strictly validate the three declared E18 topic streams."""

    source = Path(path)
    grouped: dict[str, list[tuple[float, float, float]]] = {
        topic: [] for topic in EXPECTED_TOPICS
    }
    observed_topics: set[str] = set()
    row_count = 0
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != RAW_FIELDS:
            raise ValueError(
                f"{source} must use header {','.join(RAW_FIELDS)}"
            )
        for row in reader:
            row_count += 1
            topic = str(row["topic"])
            observed_topics.add(topic)
            if topic not in grouped:
                raise ValueError(f"unexpected topic in {source}: {topic}")
            try:
                grouped[topic].append(
                    (
                        float(row["elapsed time"]),
                        float(row["timestamp"]),
                        float(row["value"]),
                    )
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid numeric row {row_count} in {source}") from error

    if observed_topics != set(EXPECTED_TOPICS):
        missing = sorted(set(EXPECTED_TOPICS) - observed_topics)
        raise ValueError(f"missing required topics: {', '.join(missing)}")
    output = _series(grouped[OUTPUT_TOPIC], OUTPUT_TOPIC)
    target_echo = _series(grouped[TARGET_ECHO_TOPIC], TARGET_ECHO_TOPIC)
    if output.count != target_echo.count:
        raise ValueError("output and target-echo topics must have equal row counts")
    if not (
        np.array_equal(output.elapsed_time_s, target_echo.elapsed_time_s)
        and np.array_equal(output.timestamp_s, target_echo.timestamp_s)
    ):
        raise ValueError("output and target-echo topics must have paired timestamps")
    return RecordedReplayData(
        source=_series(grouped[INPUT_TOPIC], INPUT_TOPIC),
        output=output,
        target_echo=target_echo,
        row_count=row_count,
    )


def map_output_ticks(
    elapsed_time_s: Sequence[float],
    *,
    dt_s: float = CONTROL_DT_S,
) -> TickMapping:
    """Map recorded output samples to unique ticks without filling missing ticks."""

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
        maximum = float(np.max(np.abs(residual)))
        raise ValueError(
            "output sample is more than half a control period from its tick: "
            f"{maximum} s"
        )
    return TickMapping(
        tick_index=ticks,
        lattice_elapsed_time_s=lattice,
        residual_s=residual,
        max_tick=int(ticks[-1]),
    )


def build_future_o1_target_events(data: RecordedReplayData) -> list[dict[str, Any]]:
    """Compute one nominal-10 ms PV Future-O1 target per source event."""

    predictor = make_predictor(
        "future_backward_fd_o1",
        nominal_dt=SOURCE_NOMINAL_DT_S,
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
                "source_index": index,
                "source_elapsed_time_s": float(data.source.elapsed_time_s[index]),
                "source_timestamp_s": float(data.source.timestamp_s[index]),
                "source_position_rad": float(position),
                "nominal_source_time_s": nominal_time,
                "target_time_s": float(prediction.state_time),
                "target_position_rad": float(prediction.position[0]),
                "target_velocity_rad_s": float(prediction.velocity[0]),
                "target_acceleration_rad_s2": 0.0,
                "prediction_status": prediction.status,
                "prediction_startup": prediction.startup,
            }
        )
    return events


class _RuckigStepper:
    """Minimal ordinary Ruckig replan-and-sample loop without extra policy."""

    def __init__(self, dt_s: float) -> None:
        self.dt_s = float(dt_s)
        self.otg = Ruckig(1, self.dt_s)

    def step(
        self,
        current: np.ndarray,
        target: np.ndarray,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if current.shape != (1, 3) or target.shape != (1, 3):
            raise ValueError("Ruckig states must have shape (1, 3)")
        if not np.all(np.isfinite(current)) or not np.all(np.isfinite(target)):
            raise ValueError("Ruckig states must be finite")
        if abs(float(target[0, 1])) > MAX_VELOCITY_RAD_S + 1e-12:
            raise ValueError("PV Future-O1 target exceeds max velocity")
        if abs(float(target[0, 2])) > MAX_ACCELERATION_RAD_S2 + 1e-12:
            raise ValueError("PV Future-O1 target exceeds max acceleration")

        inp = InputParameter(1)
        trajectory = Trajectory(1)
        inp.current_position = current[:, 0].tolist()
        inp.current_velocity = current[:, 1].tolist()
        inp.current_acceleration = current[:, 2].tolist()
        inp.target_position = target[:, 0].tolist()
        inp.target_velocity = target[:, 1].tolist()
        inp.target_acceleration = target[:, 2].tolist()
        inp.max_velocity = [MAX_VELOCITY_RAD_S]
        inp.max_acceleration = [MAX_ACCELERATION_RAD_S2]
        inp.max_jerk = [MAX_JERK_RAD_S3]
        result = self.otg.calculate(inp, trajectory)
        if int(result) < 0:
            raise RuntimeError(f"Ruckig calculate failed with result {int(result)}")
        duration = float(trajectory.duration)
        sample_time = min(self.dt_s, duration)
        if duration <= 1e-15:
            command = np.array(target, copy=True)
        else:
            position, velocity, acceleration = trajectory.at_time(sample_time)
            command = np.column_stack((position, velocity, acceleration))
        if duration < self.dt_s:
            command = np.array(target, copy=True)
        if not np.all(np.isfinite(command)):
            raise RuntimeError("Ruckig produced a non-finite command")
        return command, {
            "solver_status": str(result),
            "trajectory_duration_s": duration,
            "terminal_hold": duration < self.dt_s,
        }


def _target_array(event: dict[str, Any]) -> np.ndarray:
    return np.asarray(
        [
            [
                event["target_position_rad"],
                event["target_velocity_rad_s"],
                event["target_acceleration_rad_s2"],
            ]
        ],
        dtype=np.float64,
    )


def run_one_ms_replay(
    data: RecordedReplayData,
    events: Sequence[dict[str, Any]],
    mapping: TickMapping,
) -> list[dict[str, Any]]:
    """Run every 1 ms tick, including ticks absent from the recorded output."""

    stepper = _RuckigStepper(CONTROL_DT_S)
    current = np.zeros((1, 3), dtype=np.float64)
    event_index = 0
    active_event: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    anchor = float(data.output.elapsed_time_s[0])
    tolerance = max(1e-12, CONTROL_DT_S * 1e-9)
    for tick in range(1, mapping.max_tick + 1):
        command_elapsed = anchor + (tick - 1) * CONTROL_DT_S
        applied = 0
        while (
            event_index < len(events)
            and float(events[event_index]["source_elapsed_time_s"])
            <= command_elapsed + tolerance
        ):
            active_event = dict(events[event_index])
            event_index += 1
            applied += 1
        if active_event is None:
            raise ValueError("no source target is available at the first control tick")
        previous = np.array(current, copy=True)
        current, diagnostics = stepper.step(current, _target_array(active_event))
        average_jerk = float(
            (current[0, 2] - previous[0, 2]) / CONTROL_DT_S
        )
        violation_count = int(
            abs(float(current[0, 1])) > MAX_VELOCITY_RAD_S + 1e-9
        ) + int(abs(float(current[0, 2])) > MAX_ACCELERATION_RAD_S2 + 1e-9)
        rows.append(
            {
                "tick_index": tick,
                "command_time_s": tick * CONTROL_DT_S,
                "command_elapsed_time_s": command_elapsed,
                "source_events_applied": applied,
                "target_source_index": active_event["source_index"],
                "held_source_position_rad": active_event["source_position_rad"],
                "target_position_rad": active_event["target_position_rad"],
                "target_velocity_rad_s": active_event["target_velocity_rad_s"],
                "target_acceleration_rad_s2": 0.0,
                "target_startup": active_event["prediction_startup"],
                "command_position_rad": float(current[0, 0]),
                "command_velocity_rad_s": float(current[0, 1]),
                "command_acceleration_rad_s2": float(current[0, 2]),
                "command_average_jerk_rad_s3": average_jerk,
                "trajectory_duration_s": diagnostics["trajectory_duration_s"],
                "terminal_hold": diagnostics["terminal_hold"],
                "solver_status": diagnostics["solver_status"],
                "fallback_applied": False,
                "sampled_state_constraint_violation_count": violation_count,
            }
        )
    return rows


def build_observed_comparison(
    data: RecordedReplayData,
    mapping: TickMapping,
    replay_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for output_index, tick in enumerate(mapping.tick_index):
        replay = replay_rows[int(tick) - 1]
        real_position = float(data.output.position_rad[output_index])
        simulated_position = float(replay["command_position_rad"])
        error = simulated_position - real_position
        rows.append(
            {
                "output_index": output_index,
                "tick_index": int(tick),
                "recorded_elapsed_time_s": float(
                    data.output.elapsed_time_s[output_index]
                ),
                "lattice_elapsed_time_s": float(
                    mapping.lattice_elapsed_time_s[output_index]
                ),
                "tick_mapping_residual_s": float(mapping.residual_s[output_index]),
                "real_output_position_rad": real_position,
                "simulated_output_position_rad": simulated_position,
                "simulated_minus_real_rad": error,
                "abs_error_rad": abs(error),
                "target_source_index": replay["target_source_index"],
                "held_source_position_rad": replay["held_source_position_rad"],
                "target_position_rad": replay["target_position_rad"],
                "target_velocity_rad_s": replay["target_velocity_rad_s"],
            }
        )
    return rows


def build_target_echo_audit(
    data: RecordedReplayData,
    mapping: TickMapping,
    replay_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_by_value: dict[float, list[int]] = {}
    for index, value in enumerate(data.source.position_rad):
        source_by_value.setdefault(float(value), []).append(index)
    rows: list[dict[str, Any]] = []
    previous_echo: float | None = None
    for output_index, tick in enumerate(mapping.tick_index):
        replay = replay_rows[int(tick) - 1]
        elapsed = float(data.output.elapsed_time_s[output_index])
        echo = float(data.target_echo.position_rad[output_index])
        transition = previous_echo is None or echo != previous_echo
        transition_source_index: int | None = None
        transition_latency_s: float | None = None
        if transition:
            candidates = [
                index
                for index in source_by_value.get(echo, [])
                if float(data.source.elapsed_time_s[index]) <= elapsed + 1e-12
            ]
            if candidates:
                transition_source_index = candidates[-1]
                transition_latency_s = elapsed - float(
                    data.source.elapsed_time_s[transition_source_index]
                )
        scheduled_source_position = float(replay["held_source_position_rad"])
        rows.append(
            {
                "output_index": output_index,
                "tick_index": int(tick),
                "recorded_elapsed_time_s": elapsed,
                "target_echo_position_rad": echo,
                "scheduled_source_position_rad": scheduled_source_position,
                "exact_match": echo == scheduled_source_position,
                "echo_transition": transition,
                "transition_source_index": transition_source_index,
                "transition_latency_s": transition_latency_s,
            }
        )
        previous_echo = echo
    return rows


def _error_metrics(errors: np.ndarray) -> dict[str, float | bool | int]:
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
    }


def build_lag_scan(
    data: RecordedReplayData,
    mapping: TickMapping,
    replay_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    simulated = np.asarray(
        [row["command_position_rad"] for row in replay_rows], dtype=np.float64
    )
    rows: list[dict[str, Any]] = []
    for shift in range(-MAX_LAG_TICKS, MAX_LAG_TICKS + 1):
        shifted_ticks = mapping.tick_index + shift
        valid = (shifted_ticks >= 1) & (shifted_ticks <= simulated.size)
        errors = (
            simulated[shifted_ticks[valid] - 1]
            - data.output.position_rad[valid]
        )
        rows.append(
            {
                "simulation_shift_ticks": shift,
                "simulation_shift_s": shift * CONTROL_DT_S,
                "sample_count": int(errors.size),
                "position_rmse_rad": float(np.sqrt(np.mean(errors**2))),
            }
        )
    return rows


def run_rate_equivalence(
    events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare ten 1 ms replans with one 10 ms replan on common targets."""

    one_ms = _RuckigStepper(CONTROL_DT_S)
    ten_ms = _RuckigStepper(SOURCE_NOMINAL_DT_S)
    one_state = np.zeros((1, 3), dtype=np.float64)
    ten_state = np.zeros((1, 3), dtype=np.float64)
    rows: list[dict[str, Any]] = []
    for event_index, event in enumerate(events):
        target = _target_array(event)
        for _ in range(10):
            one_state, _ = one_ms.step(one_state, target)
        ten_state, _ = ten_ms.step(ten_state, target)
        difference = one_state - ten_state
        rows.append(
            {
                "source_index": event["source_index"],
                "boundary_time_s": (event_index + 1) * SOURCE_NOMINAL_DT_S,
                "target_position_rad": event["target_position_rad"],
                "target_velocity_rad_s": event["target_velocity_rad_s"],
                "one_ms_position_rad": float(one_state[0, 0]),
                "ten_ms_position_rad": float(ten_state[0, 0]),
                "position_difference_rad": float(difference[0, 0]),
                "one_ms_velocity_rad_s": float(one_state[0, 1]),
                "ten_ms_velocity_rad_s": float(ten_state[0, 1]),
                "velocity_difference_rad_s": float(difference[0, 1]),
                "one_ms_acceleration_rad_s2": float(one_state[0, 2]),
                "ten_ms_acceleration_rad_s2": float(ten_state[0, 2]),
                "acceleration_difference_rad_s2": float(difference[0, 2]),
            }
        )
    return rows


def _rate_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"boundary_count": len(rows)}
    for component, field, unit in (
        ("position", "position_difference_rad", "rad"),
        ("velocity", "velocity_difference_rad_s", "rad_s"),
        ("acceleration", "acceleration_difference_rad_s2", "rad_s2"),
    ):
        values = np.asarray([row[field] for row in rows], dtype=np.float64)
        result[f"{component}_rmse_{unit}"] = float(np.sqrt(np.mean(values**2)))
        result[f"{component}_max_abs_{unit}"] = float(np.max(np.abs(values)))
    result["position_numerically_identical"] = bool(
        result["position_max_abs_rad"] <= NUMERICAL_IDENTITY_TOLERANCE_RAD
    )
    return result


def _data_quality(
    data: RecordedReplayData,
    mapping: TickMapping,
    echo_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    output_gaps = np.diff(mapping.tick_index) - 1
    transition_latencies = np.asarray(
        [
            row["transition_latency_s"]
            for row in echo_rows
            if row["echo_transition"] and row["transition_latency_s"] is not None
        ],
        dtype=np.float64,
    )
    source_dt = np.diff(data.source.elapsed_time_s)
    echo_matches = np.asarray([row["exact_match"] for row in echo_rows], dtype=bool)
    return {
        "raw_row_count": data.row_count,
        "source_sample_count": data.source.count,
        "real_output_sample_count": data.output.count,
        "target_echo_sample_count": data.target_echo.count,
        "source_elapsed_start_s": float(data.source.elapsed_time_s[0]),
        "source_elapsed_end_s": float(data.source.elapsed_time_s[-1]),
        "source_dt_min_s": float(np.min(source_dt)),
        "source_dt_median_s": float(np.median(source_dt)),
        "source_dt_max_s": float(np.max(source_dt)),
        "mapped_control_tick_count": mapping.max_tick,
        "observed_tick_coverage_fraction": data.output.count / mapping.max_tick,
        "missing_control_tick_count": mapping.max_tick - data.output.count,
        "output_gap_count": int(np.sum(output_gaps > 0)),
        "largest_output_gap_ticks": int(np.max(output_gaps)),
        "tick_mapping_max_abs_residual_s": float(
            np.max(np.abs(mapping.residual_s))
        ),
        "target_echo_exact_match_count": int(np.sum(echo_matches)),
        "target_echo_exact_match_fraction": float(np.mean(echo_matches)),
        "target_echo_transition_count": int(
            sum(bool(row["echo_transition"]) for row in echo_rows)
        ),
        "target_echo_transition_latency_median_s": float(
            np.median(transition_latencies)
        ),
        "target_echo_transition_latency_p95_s": float(
            np.quantile(transition_latencies, 0.95, method="linear")
        ),
        "target_echo_transition_latency_max_s": float(
            np.max(transition_latencies)
        ),
    }


def _write_figures(
    run_directory: Path,
    comparison: Sequence[dict[str, Any]],
    rate_rows: Sequence[dict[str, Any]],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    time = np.asarray(
        [row["lattice_elapsed_time_s"] for row in comparison], dtype=float
    )
    time -= time[0]
    real = np.asarray(
        [row["real_output_position_rad"] for row in comparison], dtype=float
    )
    simulated = np.asarray(
        [row["simulated_output_position_rad"] for row in comparison], dtype=float
    )
    error = simulated - real

    figure, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, constrained_layout=True)
    axes[0].plot(time, real, color="#222222", linewidth=1.0, label="real values[0]")
    axes[0].plot(time, simulated, color="#4477AA", linewidth=0.9, label="1 ms replay")
    axes[0].set_ylabel("position [rad]")
    axes[0].legend(frameon=False)
    axes[1].plot(time, error, color="#D55E00", linewidth=0.8)
    axes[1].axhline(0.0, color="#777777", linewidth=0.7)
    axes[1].set(xlabel="elapsed from first output [s]", ylabel="replay - real [rad]")
    figure.savefig(figures / "real_output_comparison.png", dpi=200)
    figure.savefig(figures / "real_output_comparison.svg")
    plt.close(figure)

    worst = int(np.argmax(np.abs(error)))
    start_mask = time <= 0.05
    local_mask = np.abs(time - time[worst]) <= 0.05
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    for axis, mask, title in (
        (axes[0], start_mask, "Startup: first 50 ms"),
        (axes[1], local_mask, "Largest-error neighborhood"),
    ):
        axis.plot(time[mask], real[mask], color="#222222", label="real")
        axis.plot(time[mask], simulated[mask], color="#4477AA", label="1 ms replay")
        axis.set(title=title, xlabel="elapsed [s]", ylabel="position [rad]")
    axes[0].legend(frameon=False)
    figure.savefig(figures / "local_diagnostics.png", dpi=200)
    figure.savefig(figures / "local_diagnostics.svg")
    plt.close(figure)

    boundary_time = np.asarray([row["boundary_time_s"] for row in rate_rows])
    position_difference = np.asarray(
        [row["position_difference_rad"] for row in rate_rows]
    )
    velocity_difference = np.asarray(
        [row["velocity_difference_rad_s"] for row in rate_rows]
    )
    acceleration_difference = np.asarray(
        [row["acceleration_difference_rad_s2"] for row in rate_rows]
    )
    figure, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True, constrained_layout=True)
    for axis, values, label in (
        (axes[0], position_difference, "P(1 ms) - P(10 ms) [rad]"),
        (axes[1], velocity_difference, "V(1 ms) - V(10 ms) [rad/s]"),
        (axes[2], acceleration_difference, "A(1 ms) - A(10 ms) [rad/s²]"),
    ):
        axis.plot(boundary_time, values, color="#AA3377", linewidth=0.8)
        axis.axhline(0.0, color="#777777", linewidth=0.7)
        axis.set_ylabel(label)
    axes[2].set_xlabel("common 10 ms boundary [s]")
    figure.savefig(figures / "rate_equivalence.png", dpi=200)
    figure.savefig(figures / "rate_equivalence.svg")
    plt.close(figure)


def _output_hashes(run_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(run_directory).as_posix(): sha256_file(path)
        for path in sorted(run_directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def run_legacy_0801_replay(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
) -> ExperimentResult:
    root = Path(project_root).resolve()
    source_path = root / RAW_INPUT_PATH
    data = load_recorded_replay_csv(source_path)
    mapping = map_output_ticks(data.output.elapsed_time_s)
    events = build_future_o1_target_events(data)

    resolved_spec = {
        "raw_input_path": RAW_INPUT_PATH,
        "topics": {
            "source_position": INPUT_TOPIC,
            "real_output_position": OUTPUT_TOPIC,
            "target_echo_audit_only": TARGET_ECHO_TOPIC,
        },
        "source_update_policy": "compute Future-O1 once per source event and hold PV",
        "future_o1_nominal_dt_s": SOURCE_NOMINAL_DT_S,
        "control_dt_s": CONTROL_DT_S,
        "initial_state": {"position": 0.0, "velocity": 0.0, "acceleration": 0.0},
        "limits": {
            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
            "max_acceleration_rad_s2": MAX_ACCELERATION_RAD_S2,
            "max_jerk_rad_s3": MAX_JERK_RAD_S3,
        },
        "target_components": "PV with target acceleration fixed to zero",
        "governor": "none",
        "target_projection": False,
        "follower": "ordinary_ruckig_unshielded",
        "minimum_duration": None,
        "real_output_alignment": "nearest unique 1 ms tick; no interpolation",
        "numerical_identity_tolerance_rad": NUMERICAL_IDENTITY_TOLERANCE_RAD,
        "lag_scan_ticks": [-MAX_LAG_TICKS, MAX_LAG_TICKS],
        "rate_diagnostic": (
            "same nominal 10 ms Future-O1 targets; ten 1 ms replans versus one 10 ms replan"
        ),
    }
    run = start_compact_run(
        root,
        experiment_id=EXPERIMENT_ID,
        directory_name=DIRECTORY_NAME,
        title=TITLE,
        runs_root=runs_root,
        resolved_spec=resolved_spec,
    )
    replay_rows = run_one_ms_replay(data, events, mapping)
    comparison_rows = build_observed_comparison(data, mapping, replay_rows)
    echo_rows = build_target_echo_audit(data, mapping, replay_rows)
    lag_rows = build_lag_scan(data, mapping, replay_rows)
    rate_rows = run_rate_equivalence(events)

    errors = np.asarray(
        [row["simulated_minus_real_rad"] for row in comparison_rows], dtype=float
    )
    primary = _error_metrics(errors)
    best_lag = min(lag_rows, key=lambda row: float(row["position_rmse_rad"]))
    rate = _rate_metrics(rate_rows)
    quality = _data_quality(data, mapping, echo_rows)
    summary = {
        "scientific_result": (
            "numerically_identical"
            if primary["numerically_identical"]
            else "different"
        ),
        "real_vs_one_ms": primary,
        "lag_diagnostic": {
            "best_simulation_shift_ticks": best_lag["simulation_shift_ticks"],
            "best_simulation_shift_s": best_lag["simulation_shift_s"],
            "best_position_rmse_rad": best_lag["position_rmse_rad"],
            "primary_remains_zero_shift": True,
        },
        "one_ms_vs_ten_ms": rate,
        "data_quality": quality,
        "practical_equivalence_assessed": False,
    }

    write_rows_csv(run.run_directory / "target_events.csv", events)
    write_rows_csv(run.run_directory / "replay_1ms.csv", replay_rows)
    write_rows_csv(
        run.run_directory / "observed_comparison.csv", comparison_rows
    )
    write_rows_csv(run.run_directory / "target_echo_audit.csv", echo_rows)
    write_rows_csv(run.run_directory / "lag_scan.csv", lag_rows)
    write_rows_csv(run.run_directory / "rate_equivalence.csv", rate_rows)
    write_json(run.run_directory / "data_quality.json", quality)
    write_json(run.run_directory / "summary.json", summary)
    (run.run_directory / "acceptance_summary.md").write_text(
        "# E18 replay result\n\n"
        f"- Scientific result: **{summary['scientific_result']}**\n"
        f"- Real vs 1 ms RMSE: `{primary['position_rmse_rad']}` rad\n"
        f"- Real vs 1 ms max |error|: "
        f"`{primary['position_max_abs_error_rad']}` rad\n"
        f"- Best diagnostic simulation shift: "
        f"`{best_lag['simulation_shift_ticks']}` ticks\n"
        f"- 1 ms vs 10 ms position RMSE: "
        f"`{rate['position_rmse_rad']}` rad\n"
        f"- Observed 1 ms tick coverage: "
        f"`{quality['observed_tick_coverage_fraction']}`\n"
        f"- Target echo exact-match fraction: "
        f"`{quality['target_echo_exact_match_fraction']}`\n\n"
        "Numerical identity uses an absolute 1e-12 rad tolerance. This is not "
        "an engineering acceptance threshold. Missing real-output ticks are "
        "never interpolated.\n",
        encoding="utf-8",
    )
    if create_figures:
        _write_figures(run.run_directory, comparison_rows, rate_rows)
    else:
        (run.run_directory / "figures").mkdir(exist_ok=True)

    try:
        ruckig_version = version("ruckig")
    except PackageNotFoundError:
        ruckig_version = "unknown"
    run.manifest["inputs"] = {
        "recorded_environment_csv": {
            "path": RAW_INPUT_PATH,
            "sha256": sha256_file(source_path),
            "size_bytes": source_path.stat().st_size,
            "row_count": data.row_count,
            "topics": list(EXPECTED_TOPICS),
        }
    }
    run.manifest["real_environment"] = {
        "ruckig_version": "unknown",
        "known_limits": resolved_spec["limits"],
        "known_control_dt_s": CONTROL_DT_S,
    }
    run.manifest["local_replay"] = {
        "ruckig_version": ruckig_version,
        "resolved_assumptions": resolved_spec,
    }
    run.manifest["scientific_result"] = summary
    run.manifest["output_hashes"] = _output_hashes(run.run_directory)
    return finish_compact_run(
        run,
        outputs={
            "target_events": "target_events.csv",
            "replay_1ms": "replay_1ms.csv",
            "observed_comparison": "observed_comparison.csv",
            "target_echo_audit": "target_echo_audit.csv",
            "lag_scan": "lag_scan.csv",
            "rate_equivalence": "rate_equivalence.csv",
            "data_quality": "data_quality.json",
            "summary": "summary.json",
            "summary_markdown": "acceptance_summary.md",
            "figures": "figures",
        },
        failures=(),
        required_failure_count=0,
    )


def run_confirmatory(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
) -> ExperimentResult:
    """Run rebuilt E18: recorded Sync.No versus replayed PV Future-O1.

    The historical 0801 single-axis replay remains available through
    :func:`run_legacy_0801_replay`.
    """

    module_name = "_e18_none_replay_runtime"
    module = sys.modules.get(module_name)
    if module is None:
        path = Path(__file__).with_name("none_replay.py")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load rebuilt E18 runner: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.run_recorded_replay_consistency(
        project_root=project_root,
        runs_root=runs_root,
        create_figures=create_figures,
    )


if __name__ == "__main__":
    result = run_confirmatory(project_root=Path(__file__).resolve().parents[2])
    print(result.run_directory)


__all__ = [
    "CONTROL_DT_S",
    "DIRECTORY_NAME",
    "EXPECTED_TOPICS",
    "INPUT_TOPIC",
    "OUTPUT_TOPIC",
    "RAW_INPUT_PATH",
    "RecordedReplayData",
    "TARGET_ECHO_TOPIC",
    "TopicSeries",
    "build_future_o1_target_events",
    "build_observed_comparison",
    "build_target_echo_audit",
    "load_recorded_replay_csv",
    "map_output_ticks",
    "run_confirmatory",
    "run_legacy_0801_replay",
    "run_one_ms_replay",
    "run_rate_equivalence",
]
