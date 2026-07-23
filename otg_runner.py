"""Ruckig tracking loop and metrics used by the experiments."""

from __future__ import annotations

import numpy as np
from ruckig import InputParameter, OutputParameter, Ruckig

PHASE_A_FIXED_GRID_DT = 0.01
PHASE_A_FIXED_GRID_LIMITS = {
    "max_velocity": 4.1,
    "max_acceleration": 8.2,
    "max_jerk": 4000.0,
}
PHASE_A_P_ONLY_REFERENCE_METRICS = {
    "rmse": 0.035187,
    "best_lag_s": 0.070,
    "max_error": 0.184528,
    "native_execution_rate": 1.0,
    "unexpected_fallback_rate": 0.0,
}
PHASE_A_P_ONLY_TOLERANCES = {
    "rmse": 1e-4,
    "best_lag_s": 0.01,
    "max_error": 1e-4,
    "native_execution_rate": 0.0,
    "unexpected_fallback_rate": 0.0,
}


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


def run_target_state_sequence(
    reference_position,
    raw_target_states,
    dt,
    max_velocity,
    max_acceleration,
    max_jerk,
    minimum_duration=None,
    project_targets=True,
    initial_state=None,
):
    """Run ordinary Ruckig against a preconstructed target-state sequence.

    At cycle ``k``, the target tagged with reference time ``t[k]`` is passed
    to ``update()``.  Ruckig's returned state is therefore stored at
    ``output[k + 1]``.  Keeping that convention explicit prevents the
    one-cycle plotting shift present in the historical experiment script.
    """
    reference_position = np.asarray(reference_position, dtype=float)
    raw_target_states = np.asarray(raw_target_states, dtype=float)
    if reference_position.ndim != 1:
        raise ValueError("reference_position must be one-dimensional")
    if raw_target_states.shape != (reference_position.size, 3):
        raise ValueError(
            "raw_target_states must have shape "
            f"({reference_position.size}, 3), got {raw_target_states.shape}"
        )
    if reference_position.size < 2:
        raise ValueError("at least two reference samples are required")
    if not np.all(np.isfinite(reference_position)):
        raise ValueError("reference_position must contain only finite values")
    if dt <= 0.0:
        raise ValueError("dt must be positive")
    for name, value in (
        ("max_velocity", max_velocity),
        ("max_acceleration", max_acceleration),
        ("max_jerk", max_jerk),
    ):
        if value <= 0.0:
            raise ValueError(f"{name} must be positive")

    if minimum_duration is None:
        minimum_duration = dt
    minimum_duration = float(minimum_duration)
    if minimum_duration <= 0.0:
        raise ValueError("minimum_duration must be positive")

    if initial_state is None:
        initial_state = np.array([reference_position[0], 0.0, 0.0])
    initial_state = np.asarray(initial_state, dtype=float)
    if initial_state.shape != (3,) or not np.all(np.isfinite(initial_state)):
        raise ValueError("initial_state must be a finite [p, v, a] vector")

    target_states = np.empty_like(raw_target_states)
    target_feasible = np.empty(reference_position.size, dtype=bool)
    projection_mask = np.zeros(reference_position.size, dtype=bool)
    for index, state in enumerate(raw_target_states):
        feasible = target_state_is_feasible(
            state[1],
            state[2],
            max_velocity,
            max_acceleration,
            max_jerk,
        )
        target_feasible[index] = feasible
        if project_targets:
            target_states[index], projection_mask[index] = project_target_state(
                state,
                max_velocity,
                max_acceleration,
                max_jerk,
            )
        elif feasible:
            target_states[index] = state
        else:
            raise ValueError(
                "raw target state is infeasible with projection disabled at "
                f"index={index}: {state.tolist()}"
            )

    otg = Ruckig(1, dt)
    inp = InputParameter(1)
    out = OutputParameter(1)
    inp.current_position = [float(initial_state[0])]
    inp.current_velocity = [float(initial_state[1])]
    inp.current_acceleration = [float(initial_state[2])]
    inp.max_velocity = [float(max_velocity)]
    inp.max_acceleration = [float(max_acceleration)]
    inp.max_jerk = [float(max_jerk)]
    inp.minimum_duration = max(float(dt), minimum_duration)

    count = reference_position.size
    output_states = np.empty((count, 3), dtype=float)
    output_new_jerk = np.zeros(count, dtype=float)
    trajectory_durations = np.full(count, np.nan, dtype=float)
    ruckig_compute_us = np.empty(count - 1, dtype=float)
    native_command_executed_mask = np.zeros(count - 1, dtype=bool)
    unexpected_fallback_mask = np.zeros(count - 1, dtype=bool)
    output_states[0] = initial_state

    for index in range(count - 1):
        candidate = target_states[index]
        inp.target_position = [float(candidate[0])]
        inp.target_velocity = [float(candidate[1])]
        inp.target_acceleration = [float(candidate[2])]
        result = otg.update(inp, out)
        if int(result) < 0:
            raise RuntimeError(
                f"Ruckig error {result} at index={index}, "
                f"t={index * dt:.3f}s"
            )

        output_states[index + 1] = [
            out.new_position[0],
            out.new_velocity[0],
            out.new_acceleration[0],
        ]
        output_new_jerk[index + 1] = out.new_jerk[0]
        trajectory_durations[index + 1] = out.trajectory.duration
        ruckig_compute_us[index] = out.calculation_duration
        native_command_executed_mask[index] = True
        out.pass_to_input(inp)

    return {
        "position": output_states[:, 0],
        "velocity": output_states[:, 1],
        "acceleration": output_states[:, 2],
        # This is OutputParameter.new_jerk at the executed sample, not the
        # maximum jerk anywhere in the remaining frozen trajectory.
        "new_jerk": output_new_jerk,
        "raw_target_states": raw_target_states.copy(),
        "target_states": target_states,
        "target_feasible_mask": target_feasible,
        "projection_mask": projection_mask,
        "trajectory_durations": trajectory_durations,
        "ruckig_compute_us": ruckig_compute_us,
        "minimum_duration_ms": 1000.0 * inp.minimum_duration,
        "target_timing": "target[k] -> output[k+1]",
        # This runner has no alternate controller. A failed native solve raises
        # above instead of silently changing the method being measured.
        "native_command_executed_mask": native_command_executed_mask,
        "unexpected_fallback_mask": unexpected_fallback_mask,
        "native_execution_rate": float(np.mean(native_command_executed_mask)),
        "unexpected_fallback_rate": float(np.mean(unexpected_fallback_mask)),
    }


def evaluate_phase_a_p_only_compatibility_metrics(metrics):
    """Evaluate the declared post-review ordinary-Ruckig regression criteria."""
    observed = {
        name: float(metrics[name]) for name in PHASE_A_P_ONLY_REFERENCE_METRICS
    }
    criteria = {}
    for name, expected in PHASE_A_P_ONLY_REFERENCE_METRICS.items():
        tolerance = PHASE_A_P_ONLY_TOLERANCES[name]
        criterion = {
            "rmse": "ordinary_ruckig_phase_a_rmse_regression",
            "best_lag_s": "ordinary_ruckig_phase_a_lag_regression",
            "max_error": "ordinary_ruckig_phase_a_max_error_regression",
            "native_execution_rate": "ordinary_ruckig_native_execution_rate",
            "unexpected_fallback_rate": (
                "ordinary_ruckig_unexpected_fallback_rate"
            ),
        }[name]
        criteria[criterion] = bool(abs(observed[name] - expected) <= tolerance)
    return criteria


def run_phase_a_p_only_compatibility(reference_position, *, original_count=None):
    """Reproduce the value-only fixed-grid Phase A ordinary-Ruckig baseline.

    The configuration is intentionally closed: 10 ms control/minimum duration,
    limits 4.1/8.2/4000, target ``[p[k], 0, 0]``, target-at-k producing
    output-at-k+1, and native output fed back as the next current state.
    There is no safety shield or replacement controller in this runner.
    """
    position = np.asarray(reference_position, dtype=float)
    if position.ndim != 1:
        raise ValueError("reference_position must be one-dimensional")
    stop = position.size if original_count is None else int(original_count)
    if stop > position.size or stop <= 3:
        raise ValueError("original_count must be in [4, reference_position.size]")

    targets = np.column_stack((position, np.zeros((position.size, 2), dtype=float)))
    result = run_target_state_sequence(
        position,
        targets,
        PHASE_A_FIXED_GRID_DT,
        **PHASE_A_FIXED_GRID_LIMITS,
        minimum_duration=PHASE_A_FIXED_GRID_DT,
        project_targets=False,
    )
    evaluation_start = 3
    reference = position[evaluation_start:stop]
    output = result["position"][evaluation_start:stop]
    error = output - reference
    lag_ms, lag_aligned_rmse = best_lag_metrics(
        reference,
        output,
        PHASE_A_FIXED_GRID_DT,
        max_lag_samples=min(100, (reference.size - 1) // 2),
    )
    metrics = {
        "rmse": float(np.sqrt(np.mean(error**2))),
        "best_lag_s": float(lag_ms / 1000.0),
        "max_error": float(np.max(np.abs(error))),
        "lag_aligned_rmse": float(lag_aligned_rmse),
        "native_execution_rate": float(result["native_execution_rate"]),
        "unexpected_fallback_rate": float(result["unexpected_fallback_rate"]),
        "evaluation_start_index": evaluation_start,
        "evaluation_stop_index_exclusive": stop,
    }
    result["compatibility_metrics"] = metrics
    result["acceptance_criteria"] = evaluate_phase_a_p_only_compatibility_metrics(
        metrics
    )
    return result


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
