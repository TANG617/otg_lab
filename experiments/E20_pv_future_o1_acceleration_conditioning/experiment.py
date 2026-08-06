"""E20: acceleration conditioning of the E18 Future-O1 PV target."""

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
import osqp
from scipy import sparse

from otg_lab.confirmatory import finish_compact_run, start_compact_run
from otg_lab.experiment import ExperimentResult
from otg_lab.runio import sha256_file, write_json, write_rows_csv

EXPERIMENT_ID = "E20"
SLUG = "pv_future_o1_acceleration_conditioning"
DIRECTORY_NAME = f"{EXPERIMENT_ID}_{SLUG}"
TITLE = "PV Future-O1 acceleration-conditioned targets"

E18_DIRECTORY_NAME = "E18_pv_future_o1_recorded_replay_consistency"
RAW_TARGET_METHOD_ID = "pv_pred_backward_o1_kp1"
ACCEL_PROJECTED_METHOD_ID = "pv_pred_backward_o1_kp1_accel_projected"
NO_CONDITIONING_ID = "none"
ACCEL_PROJECTION_CONDITIONING_ID = "acceleration_projection"

POSITION_OBJECTIVE_SCALE_RAD = 4.1 * 0.01
VELOCITY_OBJECTIVE_SCALE_RAD_S = 4.1
PROJECTION_BOUND_MARGIN = 1e-9
PROJECTION_DYNAMICS_TOLERANCE = 1e-12
PROJECTION_SOLVER_TOLERANCE = 1e-10
NUMERICAL_DIP_TOLERANCE_RAD = 1e-12
ENGINEERING_DIP_TOLERANCE_RAD = 1e-4
NEGATIVE_VELOCITY_TOLERANCE_RAD_S = 1e-12
FOCAL_WINDOW_BEFORE_S = 0.030
FOCAL_WINDOW_AFTER_S = 0.040


def _load_e18_replay_module() -> Any:
    module_name = "_otg_lab_e18_none_replay_for_e20"
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


@dataclass(frozen=True)
class ProjectionResult:
    events: tuple[dict[str, Any], ...]
    intervals: tuple[dict[str, Any], ...]
    audit: Mapping[str, Any]


def _event_vectors(
    events: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(events) < 2:
        raise ValueError("offline projection requires at least two target events")
    elapsed = np.asarray(
        [float(event["source_elapsed_time_s"]) for event in events],
        dtype=np.float64,
    )
    position = np.asarray(
        [float(event["target_position_rad"]) for event in events],
        dtype=np.float64,
    )
    velocity = np.asarray(
        [float(event["target_velocity_rad_s"]) for event in events],
        dtype=np.float64,
    )
    if not (
        np.all(np.isfinite(elapsed))
        and np.all(np.isfinite(position))
        and np.all(np.isfinite(velocity))
    ):
        raise ValueError("target events must contain finite time, position, and velocity")
    if np.any(np.diff(elapsed) <= 0.0):
        raise ValueError("target event times must be strictly increasing")
    return elapsed, position, velocity


def _solve_projection_qp(
    elapsed: np.ndarray,
    raw_position: np.ndarray,
    raw_velocity: np.ndarray,
    *,
    max_velocity_rad_s: float,
    max_acceleration_rad_s2: float,
) -> tuple[np.ndarray, Mapping[str, Any]]:
    """Solve the sparse closest-curve QP before deterministic reconstruction."""

    count = int(elapsed.size)
    interval_count = count - 1
    dt = np.diff(elapsed)
    position_offset = 0
    velocity_offset = count
    acceleration_offset = 2 * count
    variable_count = 3 * count - 1

    position_weight = 1.0 / POSITION_OBJECTIVE_SCALE_RAD**2
    velocity_weight = 1.0 / VELOCITY_OBJECTIVE_SCALE_RAD_S**2
    quadratic = sparse.diags(
        np.concatenate(
            (
                np.full(count, position_weight),
                np.full(count, velocity_weight),
                np.zeros(interval_count),
            )
        ),
        format="csc",
    )
    linear = np.concatenate(
        (
            -position_weight * raw_position,
            -velocity_weight * raw_velocity,
            np.zeros(interval_count),
        )
    )

    row_indices: list[int] = []
    column_indices: list[int] = []
    values: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    row = 0

    def add_constraint(
        columns: Sequence[int],
        coefficients: Sequence[float],
        low: float,
        high: float,
    ) -> None:
        nonlocal row
        row_indices.extend([row] * len(columns))
        column_indices.extend(columns)
        values.extend(coefficients)
        lower.append(float(low))
        upper.append(float(high))
        row += 1

    for index, duration in enumerate(dt):
        add_constraint(
            (
                position_offset + index,
                position_offset + index + 1,
                velocity_offset + index,
                velocity_offset + index + 1,
            ),
            (-1.0, 1.0, -0.5 * duration, -0.5 * duration),
            0.0,
            0.0,
        )
        add_constraint(
            (
                velocity_offset + index,
                velocity_offset + index + 1,
                acceleration_offset + index,
            ),
            (-1.0, 1.0, -duration),
            0.0,
            0.0,
        )

    add_constraint((position_offset,), (1.0,), raw_position[0], raw_position[0])
    add_constraint((velocity_offset,), (1.0,), raw_velocity[0], raw_velocity[0])
    for index in range(count):
        add_constraint(
            (velocity_offset + index,),
            (1.0,),
            -max_velocity_rad_s,
            max_velocity_rad_s,
        )
    for index in range(interval_count):
        add_constraint(
            (acceleration_offset + index,),
            (1.0,),
            -max_acceleration_rad_s2,
            max_acceleration_rad_s2,
        )

    constraint_matrix = sparse.csc_matrix(
        (values, (row_indices, column_indices)),
        shape=(row, variable_count),
    )
    solver = osqp.OSQP()
    solver.setup(
        P=quadratic,
        q=linear,
        A=constraint_matrix,
        l=np.asarray(lower, dtype=np.float64),
        u=np.asarray(upper, dtype=np.float64),
        eps_abs=PROJECTION_SOLVER_TOLERANCE,
        eps_rel=PROJECTION_SOLVER_TOLERANCE,
        max_iter=200_000,
        polishing=True,
        adaptive_rho=True,
        verbose=False,
    )
    solved = solver.solve(raise_error=True)
    status = str(solved.info.status).lower()
    if status != "solved" or solved.x is None:
        raise RuntimeError(f"offline projection QP failed with status {status}")
    return np.asarray(solved.x, dtype=np.float64), {
        "solver": "OSQP",
        "solver_status": status,
        "solver_iterations": int(solved.info.iter),
        "solver_primal_residual": float(solved.info.prim_res),
        "solver_dual_residual": float(solved.info.dual_res),
        "solver_objective": float(solved.info.obj_val),
    }


def condition_future_o1_acceleration(
    events: Sequence[Mapping[str, Any]],
    *,
    max_velocity_rad_s: float = 4.1,
    max_acceleration_rad_s2: float = 16.2,
) -> ProjectionResult:
    """Project a complete Future-O1 PV sequence before any replay calls.

    The projected curve uses constant acceleration between event timestamps.
    Position and velocity obey exact discrete double-integrator recurrences.
    No operation in this function is called from the 1 ms replay loop.
    """

    velocity_limit = float(max_velocity_rad_s)
    acceleration_limit = float(max_acceleration_rad_s2)
    if not math.isfinite(velocity_limit) or velocity_limit <= 0.0:
        raise ValueError("max_velocity_rad_s must be finite and positive")
    if not math.isfinite(acceleration_limit) or acceleration_limit <= 0.0:
        raise ValueError("max_acceleration_rad_s2 must be finite and positive")
    elapsed, raw_position, raw_velocity = _event_vectors(events)
    if abs(float(raw_velocity[0])) > velocity_limit:
        raise ValueError("fixed initial target velocity exceeds max velocity")

    qp_state, solver_audit = _solve_projection_qp(
        elapsed,
        raw_position,
        raw_velocity,
        max_velocity_rad_s=velocity_limit,
        max_acceleration_rad_s2=acceleration_limit,
    )
    count = int(elapsed.size)
    qp_position = qp_state[:count]
    qp_velocity = qp_state[count : 2 * count]
    qp_acceleration = qp_state[2 * count :]
    dt = np.diff(elapsed)

    safe_velocity_limit = velocity_limit - min(
        PROJECTION_BOUND_MARGIN,
        0.5 * velocity_limit,
    )
    safe_acceleration_limit = acceleration_limit - min(
        PROJECTION_BOUND_MARGIN,
        0.5 * acceleration_limit,
    )
    projected_position = np.empty(count, dtype=np.float64)
    projected_velocity = np.empty(count, dtype=np.float64)
    projected_acceleration = np.empty(count - 1, dtype=np.float64)
    projected_position[0] = raw_position[0]
    projected_velocity[0] = raw_velocity[0]

    for index, duration in enumerate(dt):
        velocity_low = (
            -safe_velocity_limit - projected_velocity[index]
        ) / duration
        velocity_high = (
            safe_velocity_limit - projected_velocity[index]
        ) / duration
        acceleration_low = max(-safe_acceleration_limit, velocity_low)
        acceleration_high = min(safe_acceleration_limit, velocity_high)
        if acceleration_low > acceleration_high:
            raise RuntimeError("deterministic projection reconstruction is infeasible")
        acceleration = float(
            np.clip(
                qp_acceleration[index],
                acceleration_low,
                acceleration_high,
            )
        )
        next_velocity = projected_velocity[index] + acceleration * duration
        projected_velocity[index + 1] = next_velocity
        projected_position[index + 1] = (
            projected_position[index]
            + 0.5
            * (projected_velocity[index] + next_velocity)
            * duration
        )
        projected_acceleration[index] = (
            next_velocity - projected_velocity[index]
        ) / duration

    position_dynamics_residual = np.diff(projected_position) - (
        0.5 * (projected_velocity[:-1] + projected_velocity[1:]) * dt
    )
    velocity_dynamics_residual = np.diff(projected_velocity) - (
        projected_acceleration * dt
    )
    raw_acceleration = np.diff(raw_velocity) / dt
    strict_velocity_violation = np.abs(projected_velocity) > velocity_limit
    strict_acceleration_violation = (
        np.abs(projected_acceleration) > acceleration_limit
    )
    initial_state_preserved = bool(
        projected_position[0] == raw_position[0]
        and projected_velocity[0] == raw_velocity[0]
    )
    maximum_dynamics_residual = float(
        max(
            np.max(np.abs(position_dynamics_residual)),
            np.max(np.abs(velocity_dynamics_residual)),
        )
    )
    strict_compliance = bool(
        not np.any(strict_velocity_violation)
        and not np.any(strict_acceleration_violation)
        and maximum_dynamics_residual <= PROJECTION_DYNAMICS_TOLERANCE
        and initial_state_preserved
    )
    if not strict_compliance:
        raise RuntimeError("offline projection failed its strict post-solve audit")

    projected_events: list[dict[str, Any]] = []
    for index, original in enumerate(events):
        event = dict(original)
        event.update(
            {
                "method_id": ACCEL_PROJECTED_METHOD_ID,
                "conditioning_id": ACCEL_PROJECTION_CONDITIONING_ID,
                "raw_target_position_rad": float(raw_position[index]),
                "raw_target_velocity_rad_s": float(raw_velocity[index]),
                "target_position_rad": float(projected_position[index]),
                "target_velocity_rad_s": float(projected_velocity[index]),
                "target_acceleration_rad_s2": 0.0,
                "conditioned_curve_acceleration_rad_s2": (
                    float(projected_acceleration[index])
                    if index < count - 1
                    else None
                ),
            }
        )
        projected_events.append(event)

    interval_rows: list[dict[str, Any]] = []
    for index, duration in enumerate(dt):
        raw_value = float(raw_acceleration[index])
        projected_value = float(projected_acceleration[index])
        interval_rows.append(
            {
                "interval_index": index,
                "source_index_start": events[index]["source_index"],
                "source_index_end": events[index + 1]["source_index"],
                "start_elapsed_time_s": float(elapsed[index]),
                "end_elapsed_time_s": float(elapsed[index + 1]),
                "duration_s": float(duration),
                "raw_start_position_rad": float(raw_position[index]),
                "raw_end_position_rad": float(raw_position[index + 1]),
                "projected_start_position_rad": float(projected_position[index]),
                "projected_end_position_rad": float(projected_position[index + 1]),
                "raw_start_velocity_rad_s": float(raw_velocity[index]),
                "raw_end_velocity_rad_s": float(raw_velocity[index + 1]),
                "projected_start_velocity_rad_s": float(projected_velocity[index]),
                "projected_end_velocity_rad_s": float(projected_velocity[index + 1]),
                "raw_implied_acceleration_rad_s2": raw_value,
                "projected_acceleration_rad_s2": projected_value,
                "raw_acceleration_violation": abs(raw_value) > acceleration_limit,
                "projected_acceleration_violation": (
                    abs(projected_value) > acceleration_limit
                ),
                "position_dynamics_residual_rad": float(
                    position_dynamics_residual[index]
                ),
                "velocity_dynamics_residual_rad_s": float(
                    velocity_dynamics_residual[index]
                ),
            }
        )

    position_error = projected_position - raw_position
    velocity_error = projected_velocity - raw_velocity
    audit = {
        **solver_audit,
        "conditioning_stage": "offline_before_replay",
        "runtime_projection_or_governor": False,
        "curve_model": "piecewise_constant_acceleration_at_actual_event_times",
        "objective": "normalized_l2_distance_to_raw_target_pv",
        "position_objective_scale_rad": POSITION_OBJECTIVE_SCALE_RAD,
        "velocity_objective_scale_rad_s": VELOCITY_OBJECTIVE_SCALE_RAD_S,
        "event_count": count,
        "interval_count": count - 1,
        "max_velocity_rad_s": velocity_limit,
        "max_acceleration_rad_s2": acceleration_limit,
        "raw_acceleration_violation_count": int(
            np.sum(np.abs(raw_acceleration) > acceleration_limit)
        ),
        "raw_min_acceleration_rad_s2": float(np.min(raw_acceleration)),
        "raw_max_acceleration_rad_s2": float(np.max(raw_acceleration)),
        "raw_max_abs_acceleration_rad_s2": float(
            np.max(np.abs(raw_acceleration))
        ),
        "projected_acceleration_violation_count": int(
            np.sum(strict_acceleration_violation)
        ),
        "projected_velocity_violation_count": int(
            np.sum(strict_velocity_violation)
        ),
        "projected_min_acceleration_rad_s2": float(
            np.min(projected_acceleration)
        ),
        "projected_max_acceleration_rad_s2": float(
            np.max(projected_acceleration)
        ),
        "projected_max_abs_acceleration_rad_s2": float(
            np.max(np.abs(projected_acceleration))
        ),
        "projected_max_abs_velocity_rad_s": float(
            np.max(np.abs(projected_velocity))
        ),
        "position_projection_rmse_rad": float(
            np.sqrt(np.mean(position_error**2))
        ),
        "position_projection_max_abs_rad": float(
            np.max(np.abs(position_error))
        ),
        "velocity_projection_rmse_rad_s": float(
            np.sqrt(np.mean(velocity_error**2))
        ),
        "velocity_projection_max_abs_rad_s": float(
            np.max(np.abs(velocity_error))
        ),
        "maximum_position_dynamics_residual_rad": float(
            np.max(np.abs(position_dynamics_residual))
        ),
        "maximum_velocity_dynamics_residual_rad_s": float(
            np.max(np.abs(velocity_dynamics_residual))
        ),
        "maximum_dynamics_residual": maximum_dynamics_residual,
        "initial_state_preserved": initial_state_preserved,
        "deterministic_reconstruction_max_position_adjustment_rad": float(
            np.max(np.abs(projected_position - qp_position))
        ),
        "deterministic_reconstruction_max_velocity_adjustment_rad_s": float(
            np.max(np.abs(projected_velocity - qp_velocity))
        ),
        "deterministic_reconstruction_max_acceleration_adjustment_rad_s2": float(
            np.max(np.abs(projected_acceleration - qp_acceleration))
        ),
        "strict_acceleration_compliance": strict_compliance,
    }
    return ProjectionResult(
        events=tuple(projected_events),
        intervals=tuple(interval_rows),
        audit=audit,
    )


def locate_analysis_windows(data: Any) -> AnalysisWindows:
    positions = np.asarray(data.source.position_rad, dtype=np.float64)
    elapsed = np.asarray(data.source.elapsed_time_s, dtype=np.float64)
    differences = np.diff(positions)
    eligible = elapsed[1:] >= float(data.analysis_valid_start_s)
    scores = np.where(eligible, differences, -np.inf)
    anchor = int(np.argmax(scores)) + 1
    if not math.isfinite(float(scores[anchor - 1])) or differences[anchor - 1] <= 0.0:
        raise ValueError("no positive source jump exists in the scored segment")
    rising_start = anchor
    while rising_start > 0 and positions[rising_start] >= positions[rising_start - 1]:
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
    elapsed = np.asarray(elapsed_time_s, dtype=np.float64)
    position = np.asarray(position_rad, dtype=np.float64)
    if elapsed.ndim != 1 or position.shape != elapsed.shape or elapsed.size == 0:
        raise ValueError("elapsed time and position must be paired non-empty vectors")
    if np.any(np.diff(elapsed) <= 0.0):
        raise ValueError("elapsed time must be strictly increasing")
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
        "peak_elapsed_time_s": float(elapsed[peak_index]),
        "peak_position_rad": float(position[peak_index]),
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


def _output_constraint_audit(
    calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    velocity = np.asarray(
        [float(row["output_velocity_rad_s"]) for row in calls],
        dtype=np.float64,
    )
    acceleration = np.asarray(
        [float(row["output_acceleration_rad_s2"]) for row in calls],
        dtype=np.float64,
    )
    current_acceleration = np.asarray(
        [float(row["current_acceleration_rad_s2"]) for row in calls],
        dtype=np.float64,
    )
    jerk = (acceleration - current_acceleration) / float(e18.CONTROL_DT_S)
    velocity_violation = np.abs(velocity) > float(e18.MAX_VELOCITY_RAD_S) + 1e-10
    acceleration_violation = (
        np.abs(acceleration) > float(e18.MAX_ACCELERATION_RAD_S2) + 1e-8
    )
    jerk_violation = np.abs(jerk) > float(e18.MAX_JERK_RAD_S3) + 1e-6
    return {
        "call_count": len(calls),
        "max_abs_output_velocity_rad_s": float(np.max(np.abs(velocity))),
        "max_abs_output_acceleration_rad_s2": float(
            np.max(np.abs(acceleration))
        ),
        "max_abs_output_jerk_rad_s3": float(np.max(np.abs(jerk))),
        "velocity_violation_count": int(np.sum(velocity_violation)),
        "acceleration_violation_count": int(np.sum(acceleration_violation)),
        "jerk_violation_count": int(np.sum(jerk_violation)),
        "constraint_audit_passed": not bool(
            np.any(velocity_violation)
            or np.any(acceleration_violation)
            or np.any(jerk_violation)
        ),
    }


def _metric_row(
    method_id: str,
    conditioning_id: str,
    replay: Sequence[Mapping[str, Any]],
    windows: AnalysisWindows,
) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
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
    row: dict[str, Any] = {
        "method_id": method_id,
        "conditioning_id": conditioning_id,
        "execution_id": e18.PRIMARY_EXECUTION_ID,
    }
    row.update({f"focal_{key}": value for key, value in focal.items()})
    row.update({f"rising_{key}": value for key, value in rising.items()})
    return row, {"focal": focal, "rising_episode": rising}


def _classification(
    raw_target: Mapping[str, Any],
    conditioned_target: Mapping[str, Any],
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
    if bool(conditioned_target["rising_episode"][key]):
        return "globally_eliminated"
    if bool(conditioned_target["focal"][key]):
        return "focal_eliminated_but_transferred"
    raw_drawdown = float(raw_target["focal"]["max_drawdown_rad"])
    conditioned_drawdown = float(
        conditioned_target["focal"]["max_drawdown_rad"]
    )
    if conditioned_drawdown < raw_drawdown - NUMERICAL_DIP_TOLERANCE_RAD:
        return "improved_not_eliminated"
    return "not_improved"


def _reduction(raw_target: float, conditioned_target: float) -> dict[str, float | None]:
    reduction = float(raw_target) - float(conditioned_target)
    return {
        "absolute_reduction_rad": reduction,
        "relative_reduction_fraction": (
            reduction / float(raw_target) if float(raw_target) > 0.0 else None
        ),
    }


def _window_trace_rows(
    output_by_method: Mapping[str, Sequence[Mapping[str, Any]]],
    windows: AnalysisWindows,
    *,
    segment_start_s: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method_id, replay in output_by_method.items():
        conditioning_id = (
            NO_CONDITIONING_ID
            if method_id == RAW_TARGET_METHOD_ID
            else ACCEL_PROJECTION_CONDITIONING_ID
        )
        for item in replay:
            elapsed = float(item["command_elapsed_time_s"])
            if windows.rising_start_s - 1e-12 <= elapsed <= windows.rising_end_s + 1e-12:
                rows.append(
                    {
                        "method_id": method_id,
                        "conditioning_id": conditioning_id,
                        "elapsed_from_segment_start_s": elapsed - segment_start_s,
                        "in_focal_window": (
                            windows.focal_start_s - 1e-12
                            <= elapsed
                            <= windows.focal_end_s + 1e-12
                        ),
                        **dict(item),
                    }
                )
    return rows


def _write_figures(
    run_directory: Path,
    data: Any,
    raw_target_events: Sequence[Mapping[str, Any]],
    projection: ProjectionResult,
    output_by_method: Mapping[str, Sequence[Mapping[str, Any]]],
    windows: AnalysisWindows,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    source_time = np.asarray(data.source.elapsed_time_s) - data.segment_start_s
    source_position = np.asarray(data.source.position_rad)
    event_time = np.asarray(
        [float(row["source_elapsed_time_s"]) for row in raw_target_events]
    ) - data.segment_start_s
    raw_target_position = np.asarray(
        [float(row["target_position_rad"]) for row in raw_target_events]
    )
    raw_target_velocity = np.asarray(
        [float(row["target_velocity_rad_s"]) for row in raw_target_events]
    )
    projected_position = np.asarray(
        [float(row["target_position_rad"]) for row in projection.events]
    )
    projected_velocity = np.asarray(
        [float(row["target_velocity_rad_s"]) for row in projection.events]
    )
    interval_time = np.asarray(
        [float(row["end_elapsed_time_s"]) for row in projection.intervals]
    ) - data.segment_start_s
    raw_acceleration = np.asarray(
        [float(row["raw_implied_acceleration_rad_s2"]) for row in projection.intervals]
    )
    projected_acceleration = np.asarray(
        [float(row["projected_acceleration_rad_s2"]) for row in projection.intervals]
    )
    relative_start = windows.rising_start_s - data.segment_start_s
    relative_end = windows.rising_end_s - data.segment_start_s
    source_mask = (source_time >= relative_start) & (source_time <= relative_end)
    event_mask = (event_time >= relative_start) & (event_time <= relative_end)
    interval_mask = (interval_time >= relative_start) & (interval_time <= relative_end)

    figure, axes = plt.subplots(
        3,
        1,
        figsize=(11, 8.5),
        sharex=True,
        constrained_layout=True,
    )
    axes[0].step(
        source_time[source_mask],
        source_position[source_mask],
        where="post",
        color="#777777",
        linewidth=1.0,
        label="source position",
    )
    axes[0].plot(
        event_time[event_mask],
        raw_target_position[event_mask],
        color="#D55E00",
        marker=".",
        linewidth=0.9,
        label="raw target P — PV Future-O1",
    )
    axes[0].plot(
        event_time[event_mask],
        projected_position[event_mask],
        color="#0072B2",
        marker=".",
        linewidth=1.0,
        label="A-projected target P — PV Future-O1",
    )
    axes[0].set_ylabel("position [rad]")
    axes[0].legend(frameon=False, fontsize=8, ncol=3)
    axes[1].plot(
        event_time[event_mask],
        raw_target_velocity[event_mask],
        color="#D55E00",
        marker=".",
        linewidth=0.9,
        label="raw target V — PV Future-O1",
    )
    axes[1].plot(
        event_time[event_mask],
        projected_velocity[event_mask],
        color="#0072B2",
        marker=".",
        linewidth=1.0,
        label="A-projected target V — PV Future-O1",
    )
    axes[1].axhline(0.0, color="#888888", linewidth=0.6)
    axes[1].set_ylabel("velocity [rad/s]")
    axes[1].legend(frameon=False, fontsize=8)
    axes[2].step(
        interval_time[interval_mask],
        raw_acceleration[interval_mask],
        where="post",
        color="#D55E00",
        linewidth=0.9,
        label="raw target implied A",
    )
    axes[2].step(
        interval_time[interval_mask],
        projected_acceleration[interval_mask],
        where="post",
        color="#0072B2",
        linewidth=1.0,
        label="A-projected target A",
    )
    axes[2].axhline(
        e18.MAX_ACCELERATION_RAD_S2,
        color="#222222",
        linestyle="--",
        linewidth=0.75,
    )
    axes[2].axhline(
        -e18.MAX_ACCELERATION_RAD_S2,
        color="#222222",
        linestyle="--",
        linewidth=0.75,
        label="±A limit",
    )
    axes[2].set(
        xlabel="elapsed from reset segment [s]",
        ylabel="interval A [rad/s²]",
    )
    axes[2].legend(frameon=False, fontsize=8, ncol=3)
    figure.suptitle("E20 target acceleration conditioning — rising episode")
    figure.savefig(figures / "target_conditioning_rising_episode.png", dpi=200)
    figure.savefig(figures / "target_conditioning_rising_episode.svg")
    plt.close(figure)

    recorded_time = np.asarray(data.output.elapsed_time_s) - data.segment_start_s
    recorded_position = np.asarray(data.output.position_rad)
    colors = {
        RAW_TARGET_METHOD_ID: "#D55E00",
        ACCEL_PROJECTED_METHOD_ID: "#0072B2",
    }
    labels = {
        RAW_TARGET_METHOD_ID: "replay output — raw target",
        ACCEL_PROJECTED_METHOD_ID: "replay output — A-projected target",
    }

    mapping = e18.map_output_ticks(data.output.elapsed_time_s)
    replay_arrays: dict[str, dict[str, np.ndarray]] = {}
    for method_id, replay in output_by_method.items():
        replay_arrays[method_id] = {
            "time": np.asarray(
                [float(row["command_elapsed_time_s"]) for row in replay]
            )
            - data.segment_start_s,
            "position": np.asarray(
                [float(row["command_position_rad"]) for row in replay]
            ),
            "velocity": np.asarray(
                [float(row["command_velocity_rad_s"]) for row in replay]
            ),
            "source_position": np.asarray(
                [float(row["held_source_position_rad"]) for row in replay]
            ),
        }
    raw_replay = output_by_method[RAW_TARGET_METHOD_ID]
    recorded_source_position = np.asarray(
        [
            float(raw_replay[int(tick) - 1]["held_source_position_rad"])
            for tick in mapping.tick_index
        ]
    )

    figure, axes = plt.subplots(
        3,
        1,
        figsize=(12, 9),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": (1.35, 1.0, 1.0)},
    )
    axes[0].step(
        source_time,
        source_position,
        where="post",
        color="#666666",
        linestyle="--",
        linewidth=0.9,
        label="source position",
    )
    axes[0].plot(
        event_time,
        raw_target_position,
        color="#E69F00",
        linestyle=":",
        linewidth=1.0,
        label="raw target P — PV Future-O1",
    )
    axes[0].plot(
        event_time,
        projected_position,
        color="#009E73",
        linestyle=":",
        linewidth=1.0,
        label="A-projected target P — PV Future-O1",
    )
    axes[0].scatter(
        recorded_time,
        recorded_position,
        s=2.2,
        color="#222222",
        label="recorded output — Amax=16.2 reference",
        zorder=4,
    )
    for method_id, arrays in replay_arrays.items():
        axes[0].plot(
            arrays["time"],
            arrays["position"],
            color=colors[method_id],
            linewidth=0.8,
            label=labels[method_id],
            zorder=3,
        )
    axes[0].set_ylabel("position [rad]")
    axes[0].set_title(
        "Full reset segment: source, targets, recorded output, and replay outputs"
    )
    axes[0].legend(frameon=False, fontsize=7.5, ncol=3, loc="lower right")

    axes[1].plot(
        recorded_time,
        recorded_position - recorded_source_position,
        color="#222222",
        linewidth=0.7,
        label="recorded output - source position",
    )
    axes[1].plot(
        event_time,
        raw_target_position - source_position,
        color="#E69F00",
        linestyle=":",
        linewidth=0.8,
        label="raw target P - source position",
    )
    axes[1].plot(
        event_time,
        projected_position - source_position,
        color="#009E73",
        linestyle=":",
        linewidth=0.8,
        label="A-projected target P - source position",
    )
    for method_id, arrays in replay_arrays.items():
        axes[1].plot(
            arrays["time"],
            arrays["position"] - arrays["source_position"],
            color=colors[method_id],
            linewidth=0.7,
            label=f"{labels[method_id]} - source position",
        )
    axes[1].axhline(0.0, color="#888888", linewidth=0.65)
    axes[1].set_ylabel("relative to source [rad]")
    axes[1].legend(frameon=False, fontsize=7, ncol=3, loc="lower right")

    for method_id, replay in output_by_method.items():
        replay_at_observations = np.asarray(
            [
                float(replay[int(tick) - 1]["command_position_rad"])
                for tick in mapping.tick_index
            ]
        )
        axes[2].plot(
            recorded_time,
            replay_at_observations - recorded_position,
            color=colors[method_id],
            linewidth=0.75,
            label=f"{labels[method_id]} - recorded output",
        )
    axes[2].axhline(0.0, color="#777777", linewidth=0.7)
    axes[2].set(
        xlabel="elapsed from reset segment [s]",
        ylabel="replay - recorded [rad]",
    )
    axes[2].legend(frameon=False, fontsize=8, loc="lower right")
    for axis in axes:
        axis.axvspan(0.0, e18.GARBAGE_EXCLUSION_S, color="#BBBBBB", alpha=0.2)
        axis.grid(axis="y", color="#DDDDDD", linewidth=0.5, alpha=0.55)
    figure.savefig(figures / "target_recorded_replay_comparison.png", dpi=200)
    figure.savefig(figures / "target_recorded_replay_comparison.svg")
    plt.close(figure)

    focal_start = windows.focal_start_s - data.segment_start_s
    focal_end = windows.focal_end_s - data.segment_start_s
    recorded_mask = (recorded_time >= focal_start) & (recorded_time <= focal_end)
    source_focal = (source_time >= focal_start) & (source_time <= focal_end)
    event_focal = (event_time >= focal_start) & (event_time <= focal_end)
    figure, axes = plt.subplots(
        2,
        1,
        figsize=(11, 7),
        sharex=True,
        constrained_layout=True,
    )
    axes[0].step(
        source_time[source_focal],
        source_position[source_focal],
        where="post",
        color="#777777",
        linewidth=1.0,
        label="source position",
    )
    axes[0].plot(
        event_time[event_focal],
        raw_target_position[event_focal],
        color="#E69F00",
        linestyle=":",
        marker=".",
        label="raw target P — PV Future-O1",
    )
    axes[0].plot(
        event_time[event_focal],
        projected_position[event_focal],
        color="#009E73",
        linestyle=":",
        marker=".",
        label="A-projected target P — PV Future-O1",
    )
    axes[0].scatter(
        recorded_time[recorded_mask],
        recorded_position[recorded_mask],
        s=8,
        color="#7B3294",
        alpha=0.7,
        label="recorded output — Amax=16.2 reference",
        zorder=4,
    )
    for method_id in output_by_method:
        replay_time = replay_arrays[method_id]["time"]
        replay_position = replay_arrays[method_id]["position"]
        replay_velocity = replay_arrays[method_id]["velocity"]
        mask = (replay_time >= focal_start) & (replay_time <= focal_end)
        axes[0].plot(
            replay_time[mask],
            replay_position[mask],
            color=colors[method_id],
            linewidth=1.15,
            label=labels[method_id],
        )
        axes[1].plot(
            replay_time[mask],
            replay_velocity[mask],
            color=colors[method_id],
            linewidth=1.1,
            label=labels[method_id],
        )
    axes[0].set(title="Target and replay output comparison", ylabel="position [rad]")
    axes[0].legend(frameon=False, fontsize=7.5, ncol=2)
    axes[1].axhline(0.0, color="#777777", linewidth=0.7)
    axes[1].set(
        xlabel="elapsed from reset segment [s]",
        ylabel="output velocity [rad/s]",
    )
    axes[1].legend(frameon=False, fontsize=8)
    figure.savefig(figures / "target_and_output_comparison.png", dpi=200)
    figure.savefig(figures / "target_and_output_comparison.svg")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(11, 5.8), constrained_layout=True)
    axis.step(
        source_time[source_focal],
        source_position[source_focal],
        where="post",
        color="#777777",
        linewidth=1.0,
        label="source position",
    )
    axis.scatter(
        recorded_time[recorded_mask],
        recorded_position[recorded_mask],
        s=14,
        color="#7B3294",
        alpha=0.65,
        label="recorded output — Amax=16.2 reference",
        zorder=4,
    )
    annotation_offsets = {
        RAW_TARGET_METHOD_ID: (-125, 20),
        ACCEL_PROJECTED_METHOD_ID: (14, 18),
    }
    for method_id in output_by_method:
        replay_time = replay_arrays[method_id]["time"]
        replay_position = replay_arrays[method_id]["position"]
        mask = (replay_time >= focal_start) & (replay_time <= focal_end)
        metric = measure_position_drawdown(
            replay_time,
            replay_position,
            start_s=focal_start,
            end_s=focal_end,
        )
        axis.plot(
            replay_time[mask],
            replay_position[mask],
            color=colors[method_id],
            linewidth=2.1,
            label=labels[method_id],
            zorder=3,
        )
        peak_time = float(metric["peak_elapsed_time_s"])
        trough_time = float(metric["trough_elapsed_time_s"])
        peak_position = float(metric["peak_position_rad"])
        trough_position = float(metric["trough_position_rad"])
        axis.plot(
            [peak_time, trough_time],
            [peak_position, trough_position],
            color=colors[method_id],
            linestyle="--",
            linewidth=1.0,
            marker="o",
            markersize=4,
        )
        axis.annotate(
            f"{float(metric['max_drawdown_mrad']):.3f} mrad drawdown",
            xy=(trough_time, trough_position),
            xytext=annotation_offsets[method_id],
            textcoords="offset points",
            color=colors[method_id],
            fontsize=8.5,
            arrowprops={
                "arrowstyle": "->",
                "color": colors[method_id],
                "linewidth": 0.8,
            },
        )
    axis.set(
        title="E20 dip window — output position only",
        xlabel="elapsed from reset segment [s]",
        ylabel="position [rad]",
        xlim=(focal_start, focal_end),
    )
    axis.grid(color="#DDDDDD", linewidth=0.55, alpha=0.7)
    axis.legend(frameon=False, fontsize=8, ncol=2, loc="upper left")
    figure.savefig(figures / "dip_position_comparison.png", dpi=200)
    figure.savefig(figures / "dip_position_comparison.svg")
    plt.close(figure)


def _output_hashes(run_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(run_directory).as_posix(): sha256_file(path)
        for path in sorted(run_directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def run_acceleration_conditioning(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
) -> ExperimentResult:
    root = Path(project_root).resolve()
    source_path = root / e18.RAW_INPUT_PATH
    data = e18.load_none_snapshot(source_path)
    mapping = e18.map_output_ticks(data.output.elapsed_time_s)
    raw_target_events = e18.build_future_o1_target_events(data)
    projection = condition_future_o1_acceleration(
        raw_target_events,
        max_velocity_rad_s=e18.MAX_VELOCITY_RAD_S,
        max_acceleration_rad_s2=e18.MAX_ACCELERATION_RAD_S2,
    )
    windows = locate_analysis_windows(data)
    resolved_spec = {
        "question": (
            "Does strict offline acceleration compliance of the complete "
            "Future-O1 PV input reduce the E18 position dip?"
        ),
        "input_path": e18.RAW_INPUT_PATH,
        "methods": {
            RAW_TARGET_METHOD_ID: {"conditioning_id": NO_CONDITIONING_ID},
            ACCEL_PROJECTED_METHOD_ID: {
                "conditioning_id": ACCEL_PROJECTION_CONDITIONING_ID
            },
        },
        "projection_timing": "before replay; never called from the 1 ms data flow",
        "projection_curve_model": (
            "piecewise constant acceleration over actual source event intervals"
        ),
        "projection_equalities": (
            "v[i+1]=v[i]+a[i]*dt[i]; "
            "p[i+1]=p[i]+0.5*(v[i]+v[i+1])*dt[i]"
        ),
        "projection_limits": {
            "max_velocity_rad_s": e18.MAX_VELOCITY_RAD_S,
            "max_acceleration_rad_s2": e18.MAX_ACCELERATION_RAD_S2,
        },
        "projection_jerk_constraint": None,
        "ruckig_limits": {
            "max_velocity_rad_s": e18.MAX_VELOCITY_RAD_S,
            "max_acceleration_rad_s2": e18.MAX_ACCELERATION_RAD_S2,
            "max_jerk_rad_s3": e18.MAX_JERK_RAD_S3,
        },
        "target_acceleration_rad_s2": 0.0,
        "target_interface": "PV unchanged from E18",
        "execution_id": e18.PRIMARY_EXECUTION_ID,
        "synchronization": "No",
        "control_dt_s": e18.CONTROL_DT_S,
        "initial_state": {"position": 0.0, "velocity": 0.0, "acceleration": 0.0},
        "numerical_dip_tolerance_rad": NUMERICAL_DIP_TOLERANCE_RAD,
        "engineering_dip_tolerance_rad": ENGINEERING_DIP_TOLERANCE_RAD,
    }
    run = start_compact_run(
        root,
        experiment_id=EXPERIMENT_ID,
        directory_name=DIRECTORY_NAME,
        title=TITLE,
        runs_root=runs_root,
        resolved_spec=resolved_spec,
    )

    raw_target_output, raw_target_calls = e18.run_replay_execution(
        data,
        raw_target_events,
        mapping,
        execution_id=e18.PRIMARY_EXECUTION_ID,
    )
    conditioned_target_output, conditioned_target_calls = e18.run_replay_execution(
        data,
        projection.events,
        mapping,
        execution_id=e18.PRIMARY_EXECUTION_ID,
    )
    output_by_method = {
        RAW_TARGET_METHOD_ID: raw_target_output,
        ACCEL_PROJECTED_METHOD_ID: conditioned_target_output,
    }
    raw_target_row, raw_target_metrics = _metric_row(
        RAW_TARGET_METHOD_ID,
        NO_CONDITIONING_ID,
        raw_target_output,
        windows,
    )
    conditioned_target_row, conditioned_target_metrics = _metric_row(
        ACCEL_PROJECTED_METHOD_ID,
        ACCEL_PROJECTION_CONDITIONING_ID,
        conditioned_target_output,
        windows,
    )
    metric_rows = [raw_target_row, conditioned_target_row]
    output_audits = [
        {
            "method_id": RAW_TARGET_METHOD_ID,
            "conditioning_id": NO_CONDITIONING_ID,
            "execution_id": e18.PRIMARY_EXECUTION_ID,
            **_output_constraint_audit(raw_target_calls),
        },
        {
            "method_id": ACCEL_PROJECTED_METHOD_ID,
            "conditioning_id": ACCEL_PROJECTION_CONDITIONING_ID,
            "execution_id": e18.PRIMARY_EXECUTION_ID,
            **_output_constraint_audit(conditioned_target_calls),
        },
    ]
    focal_reduction = _reduction(
        float(raw_target_metrics["focal"]["max_drawdown_rad"]),
        float(conditioned_target_metrics["focal"]["max_drawdown_rad"]),
    )
    rising_reduction = _reduction(
        float(raw_target_metrics["rising_episode"]["max_drawdown_rad"]),
        float(conditioned_target_metrics["rising_episode"]["max_drawdown_rad"]),
    )
    recorded_output_reference = {
        "focal": measure_position_drawdown(
            data.output.elapsed_time_s,
            data.output.position_rad,
            start_s=windows.focal_start_s,
            end_s=windows.focal_end_s,
        ),
        "rising_episode": measure_position_drawdown(
            data.output.elapsed_time_s,
            data.output.position_rad,
            start_s=windows.rising_start_s,
            end_s=windows.rising_end_s,
        ),
        "role": "recorded output at Amax=16.2; not an E20 counterfactual",
    }
    summary = {
        "operational_status": "completed",
        "scientific_result": _classification(
            raw_target_metrics,
            conditioned_target_metrics,
            criterion="numerical",
        ),
        "engineering_result": _classification(
            raw_target_metrics,
            conditioned_target_metrics,
            criterion="engineering",
        ),
        "strict_target_conditioning_passed": bool(
            projection.audit["strict_acceleration_compliance"]
        ),
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
        "method_metrics": {
            RAW_TARGET_METHOD_ID: {
                "conditioning_id": NO_CONDITIONING_ID,
                **raw_target_metrics,
            },
            ACCEL_PROJECTED_METHOD_ID: {
                "conditioning_id": ACCEL_PROJECTION_CONDITIONING_ID,
                **conditioned_target_metrics,
            },
        },
        "focal_drawdown_change": focal_reduction,
        "rising_episode_drawdown_change": rising_reduction,
        "acceleration_projection_audit": dict(projection.audit),
        "all_output_constraint_audits_passed": all(
            bool(row["constraint_audit_passed"]) for row in output_audits
        ),
        "recorded_output_reference": recorded_output_reference,
        "claim_boundary": (
            "E20 isolates offline acceleration compliance only. It does not "
            "constrain target-curve jerk and does not modify runtime planning."
        ),
    }

    trace_rows = _window_trace_rows(
        output_by_method,
        windows,
        segment_start_s=data.segment_start_s,
    )
    write_rows_csv(run.run_directory / "raw_target_events.csv", raw_target_events)
    write_rows_csv(
        run.run_directory / "acceleration_projected_target_events.csv",
        projection.events,
    )
    write_rows_csv(
        run.run_directory / "acceleration_projection_audit.csv",
        projection.intervals,
    )
    write_json(
        run.run_directory / "acceleration_projection_summary.json",
        projection.audit,
    )
    write_rows_csv(run.run_directory / "method_metrics.csv", metric_rows)
    write_rows_csv(run.run_directory / "method_output_trace.csv", trace_rows)
    write_rows_csv(
        run.run_directory / "output_constraint_audit.csv",
        output_audits,
    )
    write_json(run.run_directory / "summary.json", summary)
    (run.run_directory / "acceptance_summary.md").write_text(
        "# E20 PV Future-O1 acceleration conditioning result\n\n"
        f"- Raw-target method: `{RAW_TARGET_METHOD_ID}`\n"
        f"- A-projected method: `{ACCEL_PROJECTED_METHOD_ID}`\n"
        f"- Strict target conditioning: "
        f"**{summary['strict_target_conditioning_passed']}**\n"
        f"- Scientific result: **{summary['scientific_result']}**\n"
        f"- Engineering result: **{summary['engineering_result']}**\n"
        f"- Raw acceleration violations: "
        f"`{projection.audit['raw_acceleration_violation_count']}`\n"
        f"- Projected acceleration violations: "
        f"`{projection.audit['projected_acceleration_violation_count']}`\n"
        f"- Raw-target replay focal drawdown: "
        f"`{raw_target_metrics['focal']['max_drawdown_mrad']}` mrad\n"
        f"- A-projected-target replay focal drawdown: "
        f"`{conditioned_target_metrics['focal']['max_drawdown_mrad']}` mrad\n"
        f"- Focal reduction: "
        f"`{focal_reduction['relative_reduction_fraction']}`\n"
        f"- Raw-target replay minimum focal velocity: "
        f"`{raw_target_metrics['focal']['minimum_velocity_rad_s']}` rad/s\n"
        f"- A-projected-target replay minimum focal velocity: "
        f"`{conditioned_target_metrics['focal']['minimum_velocity_rad_s']}` rad/s\n\n"
        "The target curve is conditioned once before replay. Runtime Ruckig "
        "settings and target A=0 remain unchanged; target-curve jerk is not "
        "constrained in E20.\n",
        encoding="utf-8",
    )
    if create_figures:
        _write_figures(
            run.run_directory,
            data,
            raw_target_events,
            projection,
            output_by_method,
            windows,
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
        RAW_TARGET_METHOD_ID: {
            "method_id": RAW_TARGET_METHOD_ID,
            "conditioning_id": NO_CONDITIONING_ID,
            "execution_id": e18.PRIMARY_EXECUTION_ID,
        },
        ACCEL_PROJECTED_METHOD_ID: {
            "method_id": ACCEL_PROJECTED_METHOD_ID,
            "conditioning_id": ACCEL_PROJECTION_CONDITIONING_ID,
            "projection_solver": "OSQP followed by deterministic reconstruction",
            "runtime_projection": False,
            "execution_id": e18.PRIMARY_EXECUTION_ID,
        },
    }
    run.manifest["replay"] = {
        "ruckig_version": ruckig_version,
        "synchronization": "No",
    }
    run.manifest["scientific_result"] = summary
    run.manifest["output_hashes"] = _output_hashes(run.run_directory)
    return finish_compact_run(
        run,
        outputs={
            "raw_target_events": "raw_target_events.csv",
            "acceleration_projected_target_events": (
                "acceleration_projected_target_events.csv"
            ),
            "acceleration_projection_audit": "acceleration_projection_audit.csv",
            "acceleration_projection_summary": (
                "acceleration_projection_summary.json"
            ),
            "method_metrics": "method_metrics.csv",
            "method_output_trace": "method_output_trace.csv",
            "output_constraint_audit": "output_constraint_audit.csv",
            "summary": "summary.json",
            "acceptance_summary": "acceptance_summary.md",
            "figures": "figures",
        },
        failures=[],
        required_failure_count=0,
    )


def run_confirmatory(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
) -> ExperimentResult:
    return run_acceleration_conditioning(
        project_root=project_root,
        runs_root=runs_root,
        create_figures=create_figures,
    )


if __name__ == "__main__":
    result = run_confirmatory(project_root=Path(__file__).resolve().parents[2])
    print(result.run_directory)


__all__ = [
    "AnalysisWindows",
    "RAW_TARGET_METHOD_ID",
    "DIRECTORY_NAME",
    "ENGINEERING_DIP_TOLERANCE_RAD",
    "EXPERIMENT_ID",
    "NUMERICAL_DIP_TOLERANCE_RAD",
    "ACCEL_PROJECTED_METHOD_ID",
    "ProjectionResult",
    "locate_analysis_windows",
    "measure_position_drawdown",
    "measure_replay_window",
    "condition_future_o1_acceleration",
    "run_confirmatory",
    "run_acceleration_conditioning",
]
