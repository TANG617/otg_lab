"""Ruckig tracking loop and metrics used by the experiments."""

from __future__ import annotations

import numpy as np
from ruckig import InputParameter, OutputParameter, Ruckig


def target_state_is_feasible(
    velocity,
    acceleration,
    max_velocity,
    max_acceleration,
    max_jerk,
):
    """Check the necessary feasibility condition for a Ruckig target state."""
    velocity_limit = max_velocity * (1.0 - 1e-8)
    acceleration_limit_hard = max_acceleration * (1.0 - 1e-8)
    if abs(velocity) > velocity_limit or abs(acceleration) > acceleration_limit_hard:
        return False

    available_velocity = max(0.0, max_velocity - abs(velocity))
    acceleration_limit = np.sqrt(2.0 * max_jerk * available_velocity)
    acceleration_limit *= 1.0 - 1e-8
    return abs(acceleration) <= acceleration_limit


def project_target_state(
    state,
    max_velocity,
    max_acceleration,
    max_jerk,
):
    """Scale velocity and acceleration together into the feasible region."""
    state = np.asarray(state, dtype=float)
    if not np.all(np.isfinite(state)):
        position = state[0] if np.isfinite(state[0]) else 0.0
        return np.array([position, 0.0, 0.0]), True

    if target_state_is_feasible(
        state[1], state[2], max_velocity, max_acceleration, max_jerk
    ):
        return state, False

    velocity, acceleration = state[1:]
    low, high = 0.0, 1.0
    for _ in range(50):
        scale = 0.5 * (low + high)
        if target_state_is_feasible(
            scale * velocity,
            scale * acceleration,
            max_velocity,
            max_acceleration,
            max_jerk,
        ):
            low = scale
        else:
            high = scale

    projected = state.copy()
    projected[1:] *= low
    return projected, True


def run_tracking_experiment(
    position,
    estimator,
    dt,
    max_velocity,
    max_acceleration,
    max_jerk,
    minimum_duration=None,
    target_state_mode="full",
):
    """Run a causal position-only estimator followed by one Ruckig update."""
    if target_state_mode not in {"full", "position_only"}:
        raise ValueError(
            "target_state_mode must be 'full' or 'position_only', got "
            f"{target_state_mode!r}"
        )

    if minimum_duration is None:
        minimum_duration = max(dt, estimator.lookahead)
    minimum_duration = float(minimum_duration)
    if minimum_duration <= 0.0:
        raise ValueError("minimum_duration must be positive")

    otg = Ruckig(1, dt)
    inp = InputParameter(1)
    out = OutputParameter(1)
    inp.current_position = [float(position[0])]
    inp.current_velocity = [0.0]
    inp.current_acceleration = [0.0]
    inp.max_velocity = [max_velocity]
    inp.max_acceleration = [max_acceleration]
    inp.max_jerk = [max_jerk]
    inp.minimum_duration = max(dt, minimum_duration)

    count = position.size
    planned_position = np.empty(count)
    planned_velocity = np.empty(count)
    planned_acceleration = np.empty(count)
    target_states = np.empty((count, 3))
    raw_target_states = np.empty((count, 3))
    trajectory_durations = np.full(count, np.nan)
    ruckig_compute_us = []

    planned_position[0] = position[0]
    planned_velocity[0] = 0.0
    planned_acceleration[0] = 0.0
    target_states[0] = [position[0], 0.0, 0.0]
    raw_target_states[0] = [position[0], 0.0, 0.0]
    projection_count = 0

    for index in range(count - 1):
        raw_candidate = estimator.step(position[index])
        raw_target_states[index + 1] = raw_candidate
        candidate = raw_candidate.copy()
        if target_state_mode == "position_only":
            candidate[1:] = 0.0
        candidate, projected = project_target_state(
            candidate,
            max_velocity,
            max_acceleration,
            max_jerk,
        )
        projection_count += int(projected)
        target_states[index + 1] = candidate

        inp.target_position = [float(candidate[0])]
        inp.target_velocity = [float(candidate[1])]
        inp.target_acceleration = [float(candidate[2])]
        result = otg.update(inp, out)
        if int(result) < 0:
            time = index * dt
            raise RuntimeError(
                f"{estimator.name}: Ruckig error {result} "
                f"at index={index}, t={time:.3f}s"
            )

        planned_position[index + 1] = out.new_position[0]
        planned_velocity[index + 1] = out.new_velocity[0]
        planned_acceleration[index + 1] = out.new_acceleration[0]
        trajectory_durations[index + 1] = out.trajectory.duration
        ruckig_compute_us.append(out.calculation_duration)
        out.pass_to_input(inp)

    return {
        "position": planned_position,
        "velocity": planned_velocity,
        "acceleration": planned_acceleration,
        "target_states": target_states,
        "raw_target_states": raw_target_states,
        "trajectory_durations": trajectory_durations,
        "projection_rate": projection_count / max(1, count - 1),
        "estimator_compute_us": np.asarray(estimator.compute_us),
        "ruckig_compute_us": np.asarray(ruckig_compute_us),
        "delay_ms": estimator.delay_ms,
        "lookahead_ms": estimator.lookahead_ms,
        "minimum_duration_ms": 1000.0 * inp.minimum_duration,
        "target_state_mode": target_state_mode,
    }


def best_lag_metrics(reference, output, dt, max_lag_samples=100):
    """Return best global lag and RMSE; positive lag means output is late."""
    best = (np.inf, 0)
    for lag in range(-max_lag_samples, max_lag_samples + 1):
        if lag > 0:
            ref_part, out_part = reference[:-lag], output[lag:]
        elif lag < 0:
            ref_part, out_part = reference[-lag:], output[:lag]
        else:
            ref_part, out_part = reference, output

        rmse = float(np.sqrt(np.mean((out_part - ref_part) ** 2)))
        if rmse < best[0]:
            best = (rmse, lag)

    return best[1] * dt * 1000.0, best[0]


def compute_tracking_metrics(
    dataset_name,
    reference,
    original_count,
    estimator_name,
    result,
    dt,
):
    """Summarize tracking quality, constraints, and computation time."""
    ref = reference[:original_count]
    output = result["position"][:original_count]
    error = output - ref
    acceleration = result["acceleration"][:original_count]
    jerk = np.diff(acceleration) / dt
    target = result["target_states"][:original_count]
    target_jerk = np.diff(target[:, 2]) / dt
    trajectory_durations = result["trajectory_durations"][1:original_count]
    planning_horizon = max(dt, result["lookahead_ms"] / 1000.0)
    lag_ms, aligned_rmse = best_lag_metrics(ref, output, dt)
    estimate_us = result["estimator_compute_us"]
    ruckig_us = result["ruckig_compute_us"]

    return {
        "dataset": dataset_name,
        "method": estimator_name,
        "explicit_delay_ms": result["delay_ms"],
        "prediction_lookahead_ms": result["lookahead_ms"],
        "minimum_duration_ms": result["minimum_duration_ms"],
        "target_state_mode": result["target_state_mode"],
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(np.abs(error))),
        "max_error": float(np.max(np.abs(error))),
        "best_lag_ms": lag_ms,
        "lag_aligned_rmse": aligned_rmse,
        "target_projection_rate": result["projection_rate"],
        "target_max_velocity": float(np.max(np.abs(target[:, 1]))),
        "target_max_acceleration": float(np.max(np.abs(target[:, 2]))),
        "target_p99_jerk": float(np.percentile(np.abs(target_jerk), 99)),
        "trajectory_duration_p50_ms": float(
            1000.0 * np.percentile(trajectory_durations, 50)
        ),
        "trajectory_duration_p90_ms": float(
            1000.0 * np.percentile(trajectory_durations, 90)
        ),
        "trajectory_duration_p99_ms": float(
            1000.0 * np.percentile(trajectory_durations, 99)
        ),
        "trajectory_duration_max_ms": float(
            1000.0 * np.max(trajectory_durations)
        ),
        "reachable_within_lookahead_rate": float(
            np.mean(trajectory_durations <= planning_horizon + 1e-9)
        ),
        "output_max_velocity": float(
            np.max(np.abs(result["velocity"][:original_count]))
        ),
        "output_max_acceleration": float(np.max(np.abs(acceleration))),
        "output_max_jerk": float(np.max(np.abs(jerk))) if jerk.size else 0.0,
        "estimator_compute_p50_us": float(np.percentile(estimate_us, 50)),
        "estimator_compute_p99_us": float(np.percentile(estimate_us, 99)),
        "ruckig_compute_p99_us": float(np.percentile(ruckig_us, 99)),
    }
