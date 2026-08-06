"""E19: Ruckig Amax sensitivity for the E18 PV Future-O1 replay."""

from __future__ import annotations

import importlib.util
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np

from otg_lab.confirmatory import finish_compact_run, start_compact_run
from otg_lab.experiment import ExperimentResult
from otg_lab.runio import sha256_file, write_json, write_rows_csv

EXPERIMENT_ID = "E19"
SLUG = "pv_future_o1_amax_sensitivity"
DIRECTORY_NAME = f"{EXPERIMENT_ID}_{SLUG}"
TITLE = "PV Future-O1 replay Amax sensitivity"

E18_DIRECTORY_NAME = "E18_pv_future_o1_recorded_replay_consistency"
METHOD_ID = "pv_pred_backward_o1_kp1"
REFERENCE_AMAX_RAD_S2 = 16.2
FINE_AMAX_START_RAD_S2 = 16.2
FINE_AMAX_END_RAD_S2 = 40.6
FINE_AMAX_STEP_RAD_S2 = 0.2
HIGH_AMAX_SENTINELS_RAD_S2 = (48.6, 64.8)
DEFAULT_AMAX_LEVELS_RAD_S2 = tuple(
    round(
        FINE_AMAX_START_RAD_S2
        + index * FINE_AMAX_STEP_RAD_S2,
        10,
    )
    for index in range(
        int(
            round(
                (
                    FINE_AMAX_END_RAD_S2
                    - FINE_AMAX_START_RAD_S2
                )
                / FINE_AMAX_STEP_RAD_S2
            )
        )
        + 1
    )
) + HIGH_AMAX_SENTINELS_RAD_S2

FOCAL_WINDOW_BEFORE_S = 0.030
FOCAL_WINDOW_AFTER_S = 0.040
NUMERICAL_DIP_TOLERANCE_RAD = 1e-12
ENGINEERING_DIP_TOLERANCE_RAD = 1e-4
NEGATIVE_VELOCITY_TOLERANCE_RAD_S = 1e-12


def _load_e18_replay_module() -> Any:
    module_name = "_otg_lab_e18_none_replay_for_e19"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    path = (
        Path(__file__).resolve().parents[1]
        / E18_DIRECTORY_NAME
        / "none_replay.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load E18 replay module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


e18 = _load_e18_replay_module()


@dataclass(frozen=True)
class AnalysisWindows:
    anchor_source_index: int
    anchor_elapsed_time_s: float
    focal_start_s: float
    focal_end_s: float
    rising_start_source_index: int
    rising_end_source_index: int
    rising_start_s: float
    rising_end_s: float


def _validated_amax_levels(
    levels: Sequence[float],
) -> tuple[float, ...]:
    values = tuple(float(value) for value in levels)
    if not values:
        raise ValueError("amax_levels_rad_s2 must not be empty")
    if any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise ValueError("acceleration levels must be finite and positive")
    if tuple(sorted(set(values))) != values:
        raise ValueError("acceleration levels must be unique and strictly increasing")
    if REFERENCE_AMAX_RAD_S2 not in values:
        raise ValueError("Amax levels must include the 16.2 rad/s^2 reference")
    return values


def amax_case_id(acceleration_rad_s2: float) -> str:
    token = f"{float(acceleration_rad_s2):.10g}".replace(".", "p")
    return f"amax_{token}"


def locate_analysis_windows(data: Any) -> AnalysisWindows:
    """Locate the largest positive source jump and its rising episode."""

    positions = np.asarray(data.source.position_rad, dtype=np.float64)
    elapsed = np.asarray(data.source.elapsed_time_s, dtype=np.float64)
    if positions.size < 3 or elapsed.shape != positions.shape:
        raise ValueError("source data must contain at least three paired samples")
    differences = np.diff(positions)
    eligible = elapsed[1:] >= float(data.analysis_valid_start_s)
    scores = np.where(eligible, differences, -np.inf)
    anchor = int(np.argmax(scores)) + 1
    if not math.isfinite(float(scores[anchor - 1])) or differences[anchor - 1] <= 0.0:
        raise ValueError("no positive source jump exists in the scored segment")

    rising_start = anchor
    while (
        rising_start > 0
        and positions[rising_start] >= positions[rising_start - 1]
    ):
        rising_start -= 1
    rising_end = anchor
    while (
        rising_end + 1 < positions.size
        and positions[rising_end + 1] >= positions[rising_end]
    ):
        rising_end += 1

    anchor_time = float(elapsed[anchor])
    return AnalysisWindows(
        anchor_source_index=anchor,
        anchor_elapsed_time_s=anchor_time,
        focal_start_s=anchor_time - FOCAL_WINDOW_BEFORE_S,
        focal_end_s=anchor_time + FOCAL_WINDOW_AFTER_S,
        rising_start_source_index=rising_start,
        rising_end_source_index=rising_end,
        rising_start_s=float(elapsed[rising_start]),
        rising_end_s=float(elapsed[rising_end]),
    )


def measure_position_drawdown(
    elapsed_time_s: Sequence[float],
    position_rad: Sequence[float],
    *,
    start_s: float,
    end_s: float,
) -> dict[str, Any]:
    """Measure the largest earlier-peak to later-trough position rollback."""

    elapsed = np.asarray(elapsed_time_s, dtype=np.float64)
    position = np.asarray(position_rad, dtype=np.float64)
    if elapsed.ndim != 1 or position.shape != elapsed.shape or elapsed.size == 0:
        raise ValueError("elapsed time and position must be paired non-empty vectors")
    if not np.all(np.isfinite(elapsed)) or not np.all(np.isfinite(position)):
        raise ValueError("elapsed time and position must be finite")
    if np.any(np.diff(elapsed) <= 0.0):
        raise ValueError("elapsed time must be strictly increasing")
    if not math.isfinite(float(start_s)) or not math.isfinite(float(end_s)):
        raise ValueError("window bounds must be finite")
    if float(end_s) < float(start_s):
        raise ValueError("window end must not precede its start")

    selected = np.flatnonzero(
        (elapsed >= float(start_s) - 1e-12)
        & (elapsed <= float(end_s) + 1e-12)
    )
    if selected.size == 0:
        raise ValueError("analysis window contains no samples")
    values = position[selected]
    running_peak = np.maximum.accumulate(values)
    drawdown = running_peak - values
    local_trough = int(np.argmax(drawdown))
    local_peak = int(np.argmax(values[: local_trough + 1]))
    peak_index = int(selected[local_peak])
    trough_index = int(selected[local_trough])
    maximum = float(drawdown[local_trough])
    return {
        "sample_count": int(selected.size),
        "max_drawdown_rad": maximum,
        "max_drawdown_mrad": maximum * 1000.0,
        "peak_index": peak_index,
        "peak_elapsed_time_s": float(elapsed[peak_index]),
        "peak_position_rad": float(position[peak_index]),
        "trough_index": trough_index,
        "trough_elapsed_time_s": float(elapsed[trough_index]),
        "trough_position_rad": float(position[trough_index]),
        "numerically_eliminated": maximum <= NUMERICAL_DIP_TOLERANCE_RAD,
        "engineering_eliminated": maximum <= ENGINEERING_DIP_TOLERANCE_RAD,
    }


def measure_replay_window(
    replay: Sequence[Mapping[str, Any]],
    *,
    start_s: float,
    end_s: float,
) -> dict[str, Any]:
    elapsed = np.asarray(
        [float(row["command_elapsed_time_s"]) for row in replay],
        dtype=np.float64,
    )
    position = np.asarray(
        [float(row["command_position_rad"]) for row in replay],
        dtype=np.float64,
    )
    velocity = np.asarray(
        [float(row["command_velocity_rad_s"]) for row in replay],
        dtype=np.float64,
    )
    result = measure_position_drawdown(
        elapsed,
        position,
        start_s=start_s,
        end_s=end_s,
    )
    selected = (elapsed >= start_s - 1e-12) & (elapsed <= end_s + 1e-12)
    negative = velocity[selected] < -NEGATIVE_VELOCITY_TOLERANCE_RAD_S
    result.update(
        {
            "minimum_velocity_rad_s": float(np.min(velocity[selected])),
            "negative_velocity_sample_count": int(np.sum(negative)),
            "negative_velocity_duration_s": (
                int(np.sum(negative)) * float(e18.CONTROL_DT_S)
            ),
        }
    )
    return result


def classify_dip(
    focal: Mapping[str, Any],
    rising: Mapping[str, Any],
    *,
    criterion: str,
) -> str:
    if criterion not in {"numerical", "engineering"}:
        raise ValueError("criterion must be numerical or engineering")
    key = (
        "numerically_eliminated"
        if criterion == "numerical"
        else "engineering_eliminated"
    )
    if bool(rising[key]):
        return "globally_eliminated"
    if bool(focal[key]):
        return "focal_eliminated_but_transferred"
    return "not_eliminated"


def _prefix_metrics(prefix: str, metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {f"{prefix}_{key}": value for key, value in metrics.items()}


def _output_constraint_audit(
    calls: Sequence[Mapping[str, Any]],
    *,
    acceleration_rad_s2: float,
) -> dict[str, Any]:
    output_velocity = np.asarray(
        [float(row["output_velocity_rad_s"]) for row in calls], dtype=np.float64
    )
    output_acceleration = np.asarray(
        [float(row["output_acceleration_rad_s2"]) for row in calls],
        dtype=np.float64,
    )
    current_acceleration = np.asarray(
        [float(row["current_acceleration_rad_s2"]) for row in calls],
        dtype=np.float64,
    )
    output_jerk = (
        output_acceleration - current_acceleration
    ) / float(e18.CONTROL_DT_S)
    velocity_violation = (
        np.abs(output_velocity) > float(e18.MAX_VELOCITY_RAD_S) + 1e-10
    )
    acceleration_violation = (
        np.abs(output_acceleration) > float(acceleration_rad_s2) + 1e-8
    )
    jerk_violation = np.abs(output_jerk) > float(e18.MAX_JERK_RAD_S3) + 1e-6
    return {
        "call_count": len(calls),
        "max_abs_output_velocity_rad_s": float(np.max(np.abs(output_velocity))),
        "max_abs_output_acceleration_rad_s2": float(
            np.max(np.abs(output_acceleration))
        ),
        "max_abs_output_jerk_rad_s3": float(np.max(np.abs(output_jerk))),
        "velocity_violation_count": int(np.sum(velocity_violation)),
        "acceleration_violation_count": int(np.sum(acceleration_violation)),
        "jerk_violation_count": int(np.sum(jerk_violation)),
        "constraint_audit_passed": not bool(
            np.any(velocity_violation)
            or np.any(acceleration_violation)
            or np.any(jerk_violation)
        ),
    }


def _recorded_output_reference_metrics(data: Any, windows: AnalysisWindows) -> dict[str, Any]:
    elapsed = np.asarray(data.output.elapsed_time_s, dtype=np.float64)
    position = np.asarray(data.output.position_rad, dtype=np.float64)
    return {
        "focal": measure_position_drawdown(
            elapsed,
            position,
            start_s=windows.focal_start_s,
            end_s=windows.focal_end_s,
        ),
        "rising_episode": measure_position_drawdown(
            elapsed,
            position,
            start_s=windows.rising_start_s,
            end_s=windows.rising_end_s,
        ),
        "interpretation": (
            "recorded A=16.2 reference only; not a counterfactual for higher A"
        ),
    }


def _passing_bands(
    rows: Sequence[Mapping[str, Any]], key: str
) -> list[dict[str, Any]]:
    passing = [
        float(row["max_acceleration_rad_s2"])
        for row in rows
        if bool(row[key])
    ]
    if not passing:
        return []
    all_levels = [float(row["max_acceleration_rad_s2"]) for row in rows]
    differences = np.diff(np.asarray(all_levels, dtype=np.float64))
    nominal_step = float(np.min(differences)) if differences.size else math.inf
    groups: list[list[float]] = [[passing[0]]]
    for value in passing[1:]:
        if value - groups[-1][-1] <= nominal_step + 1e-9:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [
        {
            "minimum_tested_acceleration_rad_s2": group[0],
            "maximum_tested_acceleration_rad_s2": group[-1],
            "tested_level_count": len(group),
        }
        for group in groups
    ]


def select_representatives(
    rows: Sequence[Mapping[str, Any]],
) -> dict[float, tuple[str, ...]]:
    if not rows:
        return {}
    selected: dict[float, list[str]] = {}

    def add(row: Mapping[str, Any] | None, role: str) -> None:
        if row is None:
            return
        acceleration = float(row["max_acceleration_rad_s2"])
        selected.setdefault(acceleration, []).append(role)

    baseline = next(
        (
            row
            for row in rows
            if float(row["max_acceleration_rad_s2"])
            == REFERENCE_AMAX_RAD_S2
        ),
        None,
    )
    first_zero = next(
        (row for row in rows if bool(row["focal_numerically_eliminated"])),
        None,
    )
    best_rising = min(
        rows,
        key=lambda row: (
            float(row["rising_max_drawdown_rad"]),
            float(row["max_acceleration_rad_s2"]),
        ),
    )
    first_reappearance = None
    if first_zero is not None:
        zero_acceleration = float(first_zero["max_acceleration_rad_s2"])
        first_reappearance = next(
            (
                row
                for row in rows
                if float(row["max_acceleration_rad_s2"]) > zero_acceleration
                and not bool(row["focal_numerically_eliminated"])
            ),
            None,
        )
    add(baseline, "reference_amax")
    add(first_zero, "first_focal_numerical_elimination")
    add(best_rising, "best_rising_episode")
    add(first_reappearance, "first_focal_reappearance")
    add(rows[-1], "high_acceleration_boundary")
    return {
        acceleration: tuple(roles)
        for acceleration, roles in sorted(selected.items())
    }


def _trace_rows(
    replay: Sequence[Mapping[str, Any]],
    *,
    acceleration_rad_s2: float,
    windows: AnalysisWindows,
    segment_start_s: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    case_id = amax_case_id(acceleration_rad_s2)
    for replay_row in replay:
        elapsed = float(replay_row["command_elapsed_time_s"])
        if windows.rising_start_s - 1e-12 <= elapsed <= windows.rising_end_s + 1e-12:
            rows.append(
                {
                    "method_id": METHOD_ID,
                    "execution_id": e18.PRIMARY_EXECUTION_ID,
                    "case_id": case_id,
                    "max_acceleration_rad_s2": acceleration_rad_s2,
                    "elapsed_from_segment_start_s": elapsed - segment_start_s,
                    "in_focal_window": (
                        windows.focal_start_s - 1e-12
                        <= elapsed
                        <= windows.focal_end_s + 1e-12
                    ),
                    **dict(replay_row),
                }
            )
    return rows


def _representative_trace_map(
    trace_rows: Sequence[Mapping[str, Any]],
    representatives: Mapping[float, Sequence[str]],
) -> dict[float, list[Mapping[str, Any]]]:
    selected = set(representatives)
    grouped = {acceleration: [] for acceleration in representatives}
    for row in trace_rows:
        acceleration = float(row["max_acceleration_rad_s2"])
        if acceleration in selected:
            grouped[acceleration].append(row)
    return grouped


def _write_figures(
    run_directory: Path,
    data: Any,
    windows: AnalysisWindows,
    metric_rows: Sequence[Mapping[str, Any]],
    trace_rows: Sequence[Mapping[str, Any]],
    representatives: Mapping[float, Sequence[str]],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    acceleration = np.asarray(
        [float(row["max_acceleration_rad_s2"]) for row in metric_rows]
    )
    focal_drawdown = np.asarray(
        [float(row["focal_max_drawdown_mrad"]) for row in metric_rows]
    )
    rising_drawdown = np.asarray(
        [float(row["rising_max_drawdown_mrad"]) for row in metric_rows]
    )

    figure, axis = plt.subplots(figsize=(10.5, 5.2), constrained_layout=True)
    axis.plot(
        acceleration,
        focal_drawdown,
        marker="o",
        markersize=2.8,
        linewidth=1.0,
        label="original focal window",
    )
    axis.plot(
        acceleration,
        rising_drawdown,
        marker="o",
        markersize=2.8,
        linewidth=1.0,
        label="complete monotonic-input episode",
    )
    axis.axhline(
        ENGINEERING_DIP_TOLERANCE_RAD * 1000.0,
        color="#777777",
        linestyle="--",
        linewidth=0.8,
        label="0.1 mrad engineering threshold",
    )
    axis.set(
        title="Position rollback versus Ruckig Amax",
        xlabel="Ruckig Amax [rad/s²]",
        ylabel="maximum drawdown [mrad]",
    )
    axis.grid(color="#DDDDDD", linewidth=0.5, alpha=0.65)
    axis.legend(frameon=False)
    figure.savefig(figures / "drawdown_vs_amax.png", dpi=200)
    figure.savefig(figures / "drawdown_vs_amax.svg")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(10.5, 5.2), constrained_layout=True)
    axis.plot(
        acceleration,
        [float(row["focal_minimum_velocity_rad_s"]) for row in metric_rows],
        marker="o",
        markersize=2.8,
        linewidth=1.0,
        label="original focal window",
    )
    axis.plot(
        acceleration,
        [float(row["rising_minimum_velocity_rad_s"]) for row in metric_rows],
        marker="o",
        markersize=2.8,
        linewidth=1.0,
        label="complete monotonic-input episode",
    )
    axis.axhline(0.0, color="#777777", linewidth=0.8)
    axis.set(
        title="Minimum replay output velocity versus Ruckig Amax",
        xlabel="Ruckig Amax [rad/s²]",
        ylabel="minimum velocity [rad/s]",
    )
    axis.grid(color="#DDDDDD", linewidth=0.5, alpha=0.65)
    axis.legend(frameon=False)
    figure.savefig(figures / "minimum_velocity_vs_amax.png", dpi=200)
    figure.savefig(figures / "minimum_velocity_vs_amax.svg")
    plt.close(figure)

    grouped = _representative_trace_map(trace_rows, representatives)
    source_time = np.asarray(data.source.elapsed_time_s) - data.segment_start_s
    source_position = np.asarray(data.source.position_rad)
    output_time = np.asarray(data.output.elapsed_time_s) - data.segment_start_s
    output_position = np.asarray(data.output.position_rad)
    event_rows = e18.build_future_o1_target_events(data)
    event_time = np.asarray(
        [float(row["source_elapsed_time_s"]) for row in event_rows]
    ) - data.segment_start_s
    future_position = np.asarray(
        [float(row["target_position_rad"]) for row in event_rows]
    )

    def overlay(
        *,
        start_s: float,
        end_s: float,
        title: str,
        output_name: str,
    ) -> None:
        relative_start = start_s - data.segment_start_s
        relative_end = end_s - data.segment_start_s
        source_mask = (source_time >= relative_start) & (source_time <= relative_end)
        output_mask = (output_time >= relative_start) & (output_time <= relative_end)
        event_mask = (event_time >= relative_start) & (event_time <= relative_end)
        figure, axis = plt.subplots(figsize=(11, 5.8), constrained_layout=True)
        axis.step(
            source_time[source_mask],
            source_position[source_mask],
            where="post",
            color="#8A9A20",
            linewidth=1.15,
            label="source position",
        )
        axis.plot(
            event_time[event_mask],
            future_position[event_mask],
            color="#E69F00",
            linestyle=":",
            marker=".",
            markersize=3,
            linewidth=0.9,
            label="raw target P — PV Future-O1",
        )
        axis.scatter(
            output_time[output_mask],
            output_position[output_mask],
            s=5,
            color="#7B3294",
            alpha=0.7,
            label="recorded output — Amax=16.2 reference",
            zorder=4,
        )
        colors = ("#4477AA", "#009E73", "#D55E00", "#CC79A7", "#56B4E9")
        for (acceleration_value, rows), color in zip(grouped.items(), colors):
            replay_time = np.asarray(
                [float(row["elapsed_from_segment_start_s"]) for row in rows]
            )
            replay_position = np.asarray(
                [float(row["command_position_rad"]) for row in rows]
            )
            mask = (replay_time >= relative_start) & (replay_time <= relative_end)
            roles = ", ".join(representatives[acceleration_value])
            axis.plot(
                replay_time[mask],
                replay_position[mask],
                color=color,
                linewidth=1.0,
                label=f"replay output — Amax={acceleration_value:g} ({roles})",
            )
        axis.set(
            title=title,
            xlabel="elapsed from reset segment [s]",
            ylabel="position [rad]",
        )
        axis.grid(axis="y", color="#DDDDDD", linewidth=0.5, alpha=0.65)
        axis.legend(frameon=False, fontsize=7.5, ncol=2)
        figure.savefig(figures / f"{output_name}.png", dpi=200)
        figure.savefig(figures / f"{output_name}.svg")
        plt.close(figure)

    overlay(
        start_s=windows.focal_start_s,
        end_s=windows.focal_end_s,
        title="Focal window: replay output by Ruckig Amax",
        output_name="focal_output_comparison",
    )
    overlay(
        start_s=windows.rising_start_s,
        end_s=windows.rising_end_s,
        title="Rising episode: replay output by Ruckig Amax",
        output_name="rising_episode_output_comparison",
    )


def _output_hashes(run_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(run_directory).as_posix(): sha256_file(path)
        for path in sorted(run_directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def _row_summary(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = (
        "case_id",
        "max_acceleration_rad_s2",
        "focal_max_drawdown_rad",
        "focal_max_drawdown_mrad",
        "focal_numerically_eliminated",
        "focal_engineering_eliminated",
        "rising_max_drawdown_rad",
        "rising_max_drawdown_mrad",
        "rising_numerically_eliminated",
        "rising_engineering_eliminated",
        "numerical_classification",
        "engineering_classification",
    )
    return {key: row[key] for key in keys}


def run_amax_sensitivity(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
    amax_levels_rad_s2: Sequence[float] = (
        DEFAULT_AMAX_LEVELS_RAD_S2
    ),
) -> ExperimentResult:
    root = Path(project_root).resolve()
    levels = _validated_amax_levels(amax_levels_rad_s2)
    source_path = root / e18.RAW_INPUT_PATH
    data = e18.load_none_snapshot(source_path)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)
    events = e18.build_future_o1_target_events(data)
    windows = locate_analysis_windows(data)
    resolved_spec = {
        "question": (
            "Does increasing only replay Ruckig Amax eliminate the "
            "E18 PV Future-O1 position dip, or merely move the rollback?"
        ),
        "method_id": METHOD_ID,
        "input_path": e18.RAW_INPUT_PATH,
        "input_selection": "E18 last source segment separated by gaps > 1 s",
        "independent_variable": "max_acceleration_rad_s2",
        "amax_levels_rad_s2": list(levels),
        "controlled_limits": {
            "max_velocity_rad_s": e18.MAX_VELOCITY_RAD_S,
            "max_jerk_rad_s3": e18.MAX_JERK_RAD_S3,
        },
        "target_method": METHOD_ID,
        "target_description": "PV Future-O1; nominal h=10 ms; target A=0",
        "execution_id": e18.PRIMARY_EXECUTION_ID,
        "synchronization": "No",
        "control_dt_s": e18.CONTROL_DT_S,
        "initial_state": {"position": 0.0, "velocity": 0.0, "acceleration": 0.0},
        "full_history_replay_per_case": True,
        "focal_window_relative_to_largest_positive_source_jump_s": [
            -FOCAL_WINDOW_BEFORE_S,
            FOCAL_WINDOW_AFTER_S,
        ],
        "rising_episode_policy": (
            "maximal contiguous nondecreasing raw-position interval around anchor"
        ),
        "numerical_dip_tolerance_rad": NUMERICAL_DIP_TOLERANCE_RAD,
        "engineering_dip_tolerance_rad": ENGINEERING_DIP_TOLERANCE_RAD,
        "recorded_output_role": (
            "A=16.2 observational reference only; no higher-A counterfactual claim"
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

    metric_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for acceleration in levels:
        case_id = amax_case_id(acceleration)
        try:
            replay, calls = e18.run_replay_execution(
                data,
                events,
                mapping,
                execution_id=e18.PRIMARY_EXECUTION_ID,
                max_acceleration_rad_s2=acceleration,
            )
            focal = measure_replay_window(
                replay,
                start_s=windows.focal_start_s,
                end_s=windows.focal_end_s,
            )
            rising = measure_replay_window(
                replay,
                start_s=windows.rising_start_s,
                end_s=windows.rising_end_s,
            )
            audit = _output_constraint_audit(
                calls,
                acceleration_rad_s2=acceleration,
            )
            metric_rows.append(
                {
                    "method_id": METHOD_ID,
                    "execution_id": e18.PRIMARY_EXECUTION_ID,
                    "case_id": case_id,
                    "max_acceleration_rad_s2": acceleration,
                    **_prefix_metrics("focal", focal),
                    **_prefix_metrics("rising", rising),
                    "numerical_classification": classify_dip(
                        focal, rising, criterion="numerical"
                    ),
                    "engineering_classification": classify_dip(
                        focal, rising, criterion="engineering"
                    ),
                    "constraint_audit_passed": audit["constraint_audit_passed"],
                }
            )
            audit_rows.append(
                {
                    "method_id": METHOD_ID,
                    "execution_id": e18.PRIMARY_EXECUTION_ID,
                    "case_id": case_id,
                    "max_acceleration_rad_s2": acceleration,
                    **audit,
                }
            )
            trace_rows.extend(
                _trace_rows(
                    replay,
                    acceleration_rad_s2=acceleration,
                    windows=windows,
                    segment_start_s=data.segment_start_s,
                )
            )
        except (RuntimeError, ValueError) as error:
            failures.append(
                {
                    "method_id": METHOD_ID,
                    "execution_id": e18.PRIMARY_EXECUTION_ID,
                    "case_id": case_id,
                    "max_acceleration_rad_s2": acceleration,
                    "required": True,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )

    representatives = select_representatives(metric_rows)
    first_numerical = next(
        (
            row
            for row in metric_rows
            if bool(row["focal_numerically_eliminated"])
        ),
        None,
    )
    first_engineering = next(
        (
            row
            for row in metric_rows
            if bool(row["focal_engineering_eliminated"])
        ),
        None,
    )
    best_rising = (
        min(
            metric_rows,
            key=lambda row: (
                float(row["rising_max_drawdown_rad"]),
                float(row["max_acceleration_rad_s2"]),
            ),
        )
        if metric_rows
        else None
    )
    reference_case = next(
        (
            row
            for row in metric_rows
            if float(row["max_acceleration_rad_s2"])
            == REFERENCE_AMAX_RAD_S2
        ),
        None,
    )
    any_global_numerical = any(
        bool(row["rising_numerically_eliminated"]) for row in metric_rows
    )
    any_global_engineering = any(
        bool(row["rising_engineering_eliminated"]) for row in metric_rows
    )
    any_focal_numerical = first_numerical is not None
    any_focal_engineering = first_engineering is not None

    def overall_result(*, global_pass: bool, focal_pass: bool) -> str:
        if global_pass:
            return "globally_eliminated_at_tested_level"
        if focal_pass:
            return "focal_eliminated_but_transferred"
        return "not_eliminated"

    summary = {
        "operational_status": "completed" if not failures else "failed",
        "method_id": METHOD_ID,
        "execution_id": e18.PRIMARY_EXECUTION_ID,
        "scientific_result": overall_result(
            global_pass=any_global_numerical,
            focal_pass=any_focal_numerical,
        ),
        "engineering_result": overall_result(
            global_pass=any_global_engineering,
            focal_pass=any_focal_engineering,
        ),
        "tested_case_count": len(metric_rows),
        "declared_case_count": len(levels),
        "failed_case_count": len(failures),
        "analysis_windows": {
            "anchor_source_index": windows.anchor_source_index,
            "anchor_elapsed_from_segment_start_s": (
                windows.anchor_elapsed_time_s - data.segment_start_s
            ),
            "focal_start_from_segment_start_s": (
                windows.focal_start_s - data.segment_start_s
            ),
            "focal_end_from_segment_start_s": (
                windows.focal_end_s - data.segment_start_s
            ),
            "rising_start_source_index": windows.rising_start_source_index,
            "rising_end_source_index": windows.rising_end_source_index,
            "rising_start_from_segment_start_s": (
                windows.rising_start_s - data.segment_start_s
            ),
            "rising_end_from_segment_start_s": (
                windows.rising_end_s - data.segment_start_s
            ),
        },
        "reference_case": _row_summary(reference_case),
        "first_focal_numerical_elimination": _row_summary(first_numerical),
        "first_focal_engineering_elimination": _row_summary(first_engineering),
        "best_rising_episode_case": _row_summary(best_rising),
        "focal_numerical_elimination_bands": _passing_bands(
            metric_rows, "focal_numerically_eliminated"
        ),
        "focal_engineering_elimination_bands": _passing_bands(
            metric_rows, "focal_engineering_eliminated"
        ),
        "rising_numerical_elimination_bands": _passing_bands(
            metric_rows, "rising_numerically_eliminated"
        ),
        "rising_engineering_elimination_bands": _passing_bands(
            metric_rows, "rising_engineering_eliminated"
        ),
        "representative_accelerations": {
            f"{acceleration:g}": list(roles)
            for acceleration, roles in representatives.items()
        },
        "all_output_constraint_audits_passed": bool(audit_rows)
        and all(bool(row["constraint_audit_passed"]) for row in audit_rows),
        "recorded_output_reference": _recorded_output_reference_metrics(
            data,
            windows,
        ),
        "claim_boundary": (
            "Bands describe tested grid points only; recorded output is not "
            "a counterfactual observation for changed acceleration."
        ),
    }

    write_rows_csv(run.run_directory / "raw_target_events.csv", events)
    write_rows_csv(
        run.run_directory / "amax_sweep_metrics.csv",
        metric_rows,
    )
    write_rows_csv(run.run_directory / "amax_output_trace.csv", trace_rows)
    write_rows_csv(run.run_directory / "output_constraint_audit.csv", audit_rows)
    write_json(run.run_directory / "summary.json", summary)
    (run.run_directory / "acceptance_summary.md").write_text(
        "# E19 PV Future-O1 replay Amax sensitivity result\n\n"
        f"- Method: `{METHOD_ID}`\n"
        f"- Scientific result: **{summary['scientific_result']}**\n"
        f"- Engineering result: **{summary['engineering_result']}**\n"
        f"- Completed cases: `{len(metric_rows)}/{len(levels)}`\n"
        f"- Reference Amax focal rollback: "
        f"`{None if reference_case is None else reference_case['focal_max_drawdown_mrad']}` mrad\n"
        f"- First numerical focal elimination Amax: "
        f"`{None if first_numerical is None else first_numerical['max_acceleration_rad_s2']}` rad/s²\n"
        f"- Best complete-rising-episode Amax: "
        f"`{None if best_rising is None else best_rising['max_acceleration_rad_s2']}` rad/s²\n"
        f"- Best complete-rising-episode rollback: "
        f"`{None if best_rising is None else best_rising['rising_max_drawdown_mrad']}` mrad\n\n"
        "A focal pass combined with a complete-rising-episode failure is reported "
        "as dip transfer, not global elimination. The recorded output curve is an "
        "A=16.2 reference only.\n",
        encoding="utf-8",
    )
    if create_figures and metric_rows:
        _write_figures(
            run.run_directory,
            data,
            windows,
            metric_rows,
            trace_rows,
            representatives,
        )
    else:
        (run.run_directory / "figures").mkdir(exist_ok=True)

    try:
        ruckig_version = version("ruckig")
    except PackageNotFoundError:
        ruckig_version = "unknown"
    run.manifest["inputs"] = {
        "e18_sync_no_snapshot": {
            "path": e18.RAW_INPUT_PATH,
            "sha256": sha256_file(source_path),
            "size_bytes": source_path.stat().st_size,
            "selected_source_segment_index": data.selected_segment_index,
            "source_event_count": data.source.count,
        }
    }
    run.manifest["methods"] = {
        METHOD_ID: {
            "method_id": METHOD_ID,
            "conditioning_id": "none",
            "ruckig_version": ruckig_version,
            "execution_id": e18.PRIMARY_EXECUTION_ID,
            "synchronization": "No",
            "target_stage": "raw_target",
            "varied_parameter": "max_acceleration_rad_s2",
        }
    }
    run.manifest["scientific_result"] = summary
    run.manifest["output_hashes"] = _output_hashes(run.run_directory)
    return finish_compact_run(
        run,
        outputs={
            "raw_target_events": "raw_target_events.csv",
            "amax_sweep_metrics": "amax_sweep_metrics.csv",
            "amax_output_trace": "amax_output_trace.csv",
            "output_constraint_audit": "output_constraint_audit.csv",
            "summary": "summary.json",
            "acceptance_summary": "acceptance_summary.md",
            "figures": "figures",
        },
        failures=failures,
        required_failure_count=len(failures),
    )


def run_confirmatory(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
) -> ExperimentResult:
    return run_amax_sensitivity(
        project_root=project_root,
        runs_root=runs_root,
        create_figures=create_figures,
    )


if __name__ == "__main__":
    result = run_confirmatory(project_root=Path(__file__).resolve().parents[2])
    print(result.run_directory)


__all__ = [
    "AnalysisWindows",
    "REFERENCE_AMAX_RAD_S2",
    "DEFAULT_AMAX_LEVELS_RAD_S2",
    "DIRECTORY_NAME",
    "ENGINEERING_DIP_TOLERANCE_RAD",
    "EXPERIMENT_ID",
    "NUMERICAL_DIP_TOLERANCE_RAD",
    "amax_case_id",
    "classify_dip",
    "locate_analysis_windows",
    "measure_position_drawdown",
    "measure_replay_window",
    "run_amax_sensitivity",
    "run_confirmatory",
    "select_representatives",
]
