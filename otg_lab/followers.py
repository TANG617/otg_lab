"""Follower implementations and frozen-trajectory constraint audits."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns

import numpy as np
from ruckig import InputParameter, Ruckig, Trajectory

from .governors import (
    MotionLimits,
    OneStepBoundedJerkGovernor,
    as_state_matrix,
    integrate_constant_jerk,
    point_is_admissible,
    segment_is_feasible,
    velocity_extrema_constant_jerk,
)


@dataclass(frozen=True)
class FollowerResult:
    command_state: np.ndarray
    command_jerk: np.ndarray
    command_time: float
    solver_status: str
    fallback: bool
    fallback_reason: str
    target_projected: bool
    free_trajectory_duration: float
    frozen_trajectory_duration: float
    compute_us: float
    continuous_audit: dict[str, np.ndarray | float | int]


def _configure_input(
    inp: InputParameter,
    current: np.ndarray,
    target: np.ndarray,
    limits: MotionLimits,
    minimum_duration: float | None,
) -> None:
    inp.current_position = current[:, 0].tolist()
    inp.current_velocity = current[:, 1].tolist()
    inp.current_acceleration = current[:, 2].tolist()
    inp.target_position = target[:, 0].tolist()
    inp.target_velocity = target[:, 1].tolist()
    inp.target_acceleration = target[:, 2].tolist()
    inp.max_velocity = limits.max_velocity.tolist()
    inp.max_acceleration = limits.max_acceleration.tolist()
    inp.max_jerk = limits.max_jerk.tolist()
    if minimum_duration is not None:
        inp.minimum_duration = float(minimum_duration)


def _trajectory_boundaries(trajectory: Trajectory) -> np.ndarray:
    """Extract accessible Ruckig profile boundaries for audit sampling."""

    boundaries = {0.0, float(trajectory.duration)}
    try:
        for section in trajectory.profiles:
            for profile in section:
                offset = 0.0
                brake = getattr(profile, "brake", None)
                if brake is not None:
                    for duration in getattr(brake, "t", []):
                        offset += float(duration)
                        boundaries.add(offset)
                for duration in getattr(profile, "t", []):
                    offset += float(duration)
                    boundaries.add(offset)
                accel = getattr(profile, "accel", None)
                if accel is not None:
                    for duration in getattr(accel, "t", []):
                        offset += float(duration)
                        boundaries.add(offset)
    except (AttributeError, TypeError):
        pass
    duration = float(trajectory.duration)
    return np.asarray(sorted(t for t in boundaries if 0.0 <= t <= duration))


def _profile_internal_jerk(trajectory: Trajectory, dof: int) -> np.ndarray:
    maximum = np.zeros(dof, dtype=float)
    try:
        for section in trajectory.profiles:
            for joint, profile in enumerate(section[:dof]):
                values = list(getattr(getattr(profile, "brake", None), "j", []))
                values += list(getattr(profile, "j", []))
                values += list(getattr(getattr(profile, "accel", None), "j", []))
                if values:
                    maximum[joint] = max(maximum[joint], np.max(np.abs(values)))
    except (AttributeError, TypeError):
        maximum[:] = np.nan
    return maximum


def _analytic_profile_audit(
    trajectory: Trajectory, limits: MotionLimits, tolerance: float
) -> dict[str, np.ndarray | float | int] | None:
    """Use exposed constant-jerk profiles for an exact extrema audit."""

    max_velocity = np.zeros(limits.dof)
    max_acceleration = np.zeros(limits.dof)
    max_jerk = np.zeros(limits.dof)
    velocity_time = np.zeros(limits.dof)
    acceleration_time = np.zeros(limits.dof)
    jerk_time = np.zeros(limits.dof)
    violations = np.zeros(limits.dof, dtype=int)
    parsed = np.zeros(limits.dof, dtype=bool)
    try:
        for section in trajectory.profiles:
            for joint, profile in enumerate(section[: limits.dof]):
                offset = 0.0
                sequences = (
                    getattr(profile, "brake", None),
                    profile,
                    getattr(profile, "accel", None),
                )
                for sequence in sequences:
                    if sequence is None:
                        continue
                    durations = list(getattr(sequence, "t", []))
                    jerks = list(getattr(sequence, "j", []))
                    accelerations = list(getattr(sequence, "a", []))
                    velocities = list(getattr(sequence, "v", []))
                    if not durations:
                        continue
                    for segment, duration_value in enumerate(durations):
                        duration = float(duration_value)
                        if duration < 0.0:
                            return None
                        if segment >= len(jerks):
                            return None
                        jerk = float(jerks[segment])
                        if segment < len(accelerations):
                            acceleration = float(accelerations[segment])
                        elif accelerations:
                            acceleration = float(accelerations[-1])
                        else:
                            return None
                        if segment < len(velocities):
                            velocity = float(velocities[segment])
                        elif velocities:
                            velocity = float(velocities[-1])
                        else:
                            return None
                        next_acceleration = acceleration + jerk * duration
                        candidate_accelerations = (
                            (abs(acceleration), offset),
                            (abs(next_acceleration), offset + duration),
                        )
                        for magnitude, occurrence in candidate_accelerations:
                            if magnitude > max_acceleration[joint]:
                                max_acceleration[joint] = magnitude
                                acceleration_time[joint] = occurrence
                        candidate_velocities = [
                            (abs(velocity), offset),
                            (
                                abs(
                                    velocity
                                    + acceleration * duration
                                    + 0.5 * jerk * duration**2
                                ),
                                offset + duration,
                            ),
                        ]
                        if jerk != 0.0:
                            extremum_time = -acceleration / jerk
                            if 0.0 < extremum_time < duration:
                                extremum_velocity = (
                                    velocity
                                    + acceleration * extremum_time
                                    + 0.5 * jerk * extremum_time**2
                                )
                                candidate_velocities.append(
                                    (abs(extremum_velocity), offset + extremum_time)
                                )
                        for magnitude, occurrence in candidate_velocities:
                            if magnitude > max_velocity[joint]:
                                max_velocity[joint] = magnitude
                                velocity_time[joint] = occurrence
                        if abs(jerk) > max_jerk[joint]:
                            max_jerk[joint] = abs(jerk)
                            jerk_time[joint] = offset
                        if (
                            max(value[0] for value in candidate_velocities)
                            > limits.max_velocity[joint] + tolerance
                            or max(value[0] for value in candidate_accelerations)
                            > limits.max_acceleration[joint] + tolerance
                            or abs(jerk) > limits.max_jerk[joint] + tolerance
                        ):
                            violations[joint] += 1
                        offset += duration
                        parsed[joint] = True
        if not np.all(parsed):
            return None
    except (AttributeError, TypeError, ValueError, IndexError):
        return None
    return {
        "duration": float(trajectory.duration),
        "sample_count": 0,
        "audit_method": "analytic_profile_extrema",
        "max_velocity": max_velocity,
        "max_acceleration": max_acceleration,
        "max_internal_jerk": max_jerk,
        "max_sampled_jerk": np.full(limits.dof, np.nan),
        "velocity_max_time": velocity_time,
        "acceleration_max_time": acceleration_time,
        "jerk_max_time": jerk_time,
        "velocity_margin": limits.max_velocity - max_velocity,
        "acceleration_margin": limits.max_acceleration - max_acceleration,
        "jerk_margin": limits.max_jerk - max_jerk,
        "violation_count": violations,
        "worst_excess": float(
            max(
                0.0,
                np.max(max_velocity - limits.max_velocity),
                np.max(max_acceleration - limits.max_acceleration),
                np.max(max_jerk - limits.max_jerk),
            )
        ),
    }


def audit_frozen_trajectory(
    trajectory: Trajectory,
    limits: MotionLimits,
    *,
    grid_dt: float = 0.0001,
    tolerance: float = 1e-8,
) -> dict[str, np.ndarray | float | int]:
    """Audit all frozen trajectory time with <=0.1 ms grid plus boundaries."""

    if grid_dt <= 0.0 or grid_dt > 0.0001 + 1e-15:
        raise ValueError("grid_dt must be in (0, 0.1 ms]")
    analytic = _analytic_profile_audit(trajectory, limits, tolerance)
    if analytic is not None:
        return analytic
    duration = float(trajectory.duration)
    regular = np.arange(0.0, duration + grid_dt * 0.5, grid_dt)
    times = np.unique(
        np.concatenate((regular, _trajectory_boundaries(trajectory), [duration]))
    )
    positions = np.empty((times.size, limits.dof))
    velocities = np.empty_like(positions)
    accelerations = np.empty_like(positions)
    for index, sample_time in enumerate(times):
        position, velocity, acceleration = trajectory.at_time(float(sample_time))
        positions[index] = position
        velocities[index] = velocity
        accelerations[index] = acceleration
    max_velocity = np.max(np.abs(velocities), axis=0)
    max_acceleration = np.max(np.abs(accelerations), axis=0)
    internal_jerk = _profile_internal_jerk(trajectory, limits.dof)
    if times.size > 1:
        sampled_jerk = np.diff(accelerations, axis=0) / np.diff(times)[:, None]
        max_sampled_jerk = np.max(np.abs(sampled_jerk), axis=0)
    else:
        max_sampled_jerk = np.zeros(limits.dof)
    velocity_excess = max_velocity - limits.max_velocity
    acceleration_excess = max_acceleration - limits.max_acceleration
    jerk_excess = internal_jerk - limits.max_jerk
    violations = (np.abs(velocities) > limits.max_velocity + tolerance).sum(axis=0) + (
        np.abs(accelerations) > limits.max_acceleration + tolerance
    ).sum(axis=0)
    if np.all(np.isfinite(internal_jerk)):
        violations += (internal_jerk > limits.max_jerk + tolerance).astype(int)
    return {
        "duration": duration,
        "sample_count": int(times.size),
        "audit_method": "sampled_grid_with_boundaries",
        "max_velocity": max_velocity,
        "max_acceleration": max_acceleration,
        "max_internal_jerk": internal_jerk,
        "max_sampled_jerk": max_sampled_jerk,
        "velocity_max_time": times[np.argmax(np.abs(velocities), axis=0)],
        "acceleration_max_time": times[np.argmax(np.abs(accelerations), axis=0)],
        "jerk_max_time": np.full(limits.dof, np.nan),
        "velocity_margin": limits.max_velocity - max_velocity,
        "acceleration_margin": limits.max_acceleration - max_acceleration,
        "jerk_margin": limits.max_jerk - internal_jerk,
        "violation_count": violations,
        "worst_excess": float(
            max(
                0.0,
                np.max(velocity_excess),
                np.max(acceleration_excess),
                np.nanmax(jerk_excess),
            )
        ),
    }


def target_state_is_ruckig_admissible(
    target: np.ndarray, limits: MotionLimits, *, tolerance: float = 1e-8
) -> bool:
    """Ruckig target-state stopping-feasibility condition, per joint."""

    value = as_state_matrix(target, limits.dof)
    if not point_is_admissible(value, limits, tolerance=tolerance):
        return False
    available_velocity = np.maximum(0.0, limits.max_velocity - np.abs(value[:, 1]))
    acceleration_bound = np.sqrt(2.0 * limits.max_jerk * available_velocity)
    return bool(np.all(np.abs(value[:, 2]) <= acceleration_bound + tolerance))


def scalar_project_target_state(
    target: np.ndarray, limits: MotionLimits
) -> tuple[np.ndarray, bool]:
    """Historical scalar v/a projection baseline (explicitly not a governor)."""

    value = as_state_matrix(target, limits.dof)
    if not np.all(np.isfinite(value)):
        raise ValueError("scalar projection does not accept non-finite targets")
    projected = value.copy()
    changed = False
    for joint in range(limits.dof):
        one_joint_limits = MotionLimits.broadcast(
            1,
            limits.max_velocity[joint],
            limits.max_acceleration[joint],
            limits.max_jerk[joint],
        )
        if target_state_is_ruckig_admissible(value[joint], one_joint_limits):
            continue
        low, high = 0.0, 1.0
        for _ in range(60):
            scale = 0.5 * (low + high)
            candidate = value[joint].copy()
            candidate[1:] *= scale
            if target_state_is_ruckig_admissible(candidate, one_joint_limits):
                low = scale
            else:
                high = scale
        projected[joint, 1:] *= low
        changed = True
    return projected, changed


def _audit_constant_jerk_segment(
    current: np.ndarray,
    command: np.ndarray,
    jerk: np.ndarray,
    duration: float,
    limits: MotionLimits,
) -> dict[str, np.ndarray | float | int]:
    """Return exact extrema for a synchronized direct/fallback segment."""

    max_velocity = np.empty(limits.dof)
    velocity_max_time = np.empty(limits.dof)
    violations = np.zeros(limits.dof, dtype=int)
    for joint in range(limits.dof):
        velocity_min, velocity_max, candidate_times = velocity_extrema_constant_jerk(
            current[joint], float(jerk[joint]), duration
        )
        candidate_values = np.asarray(
            [
                current[joint, 1]
                + current[joint, 2] * sample_time
                + 0.5 * jerk[joint] * sample_time**2
                for sample_time in candidate_times
            ]
        )
        max_velocity[joint] = max(abs(velocity_min), abs(velocity_max))
        velocity_max_time[joint] = candidate_times[
            int(np.argmax(np.abs(candidate_values)))
        ]
        violations[joint] = int(
            not segment_is_feasible(current[joint], jerk[joint], duration, limits, joint)
        )
    acceleration_values = np.column_stack((current[:, 2], command[:, 2]))
    acceleration_indices = np.argmax(np.abs(acceleration_values), axis=1)
    max_acceleration = np.max(np.abs(acceleration_values), axis=1)
    maximum_jerk = np.abs(jerk)
    return {
        "duration": float(duration),
        "sample_count": 2,
        "audit_method": "analytic_constant_jerk",
        "max_velocity": max_velocity,
        "max_acceleration": max_acceleration,
        "max_internal_jerk": maximum_jerk,
        "max_sampled_jerk": maximum_jerk,
        "velocity_max_time": velocity_max_time,
        "acceleration_max_time": acceleration_indices.astype(float) * duration,
        "jerk_max_time": np.zeros(limits.dof),
        "velocity_margin": limits.max_velocity - max_velocity,
        "acceleration_margin": limits.max_acceleration - max_acceleration,
        "jerk_margin": limits.max_jerk - maximum_jerk,
        "violation_count": violations,
        "worst_excess": float(
            max(
                0.0,
                np.max(max_velocity - limits.max_velocity),
                np.max(max_acceleration - limits.max_acceleration),
                np.max(maximum_jerk - limits.max_jerk),
            )
        ),
    }


class DirectExecutableFollower:
    """Execute an already one-step-reachable target without a second planner."""

    name = "direct_executable"

    def __init__(self, dof: int, dt: float, limits: MotionLimits) -> None:
        self.dof = dof
        self.dt = float(dt)
        self.limits = limits
        self.command_state: np.ndarray | None = None
        self._fallback = OneStepBoundedJerkGovernor(dof, dt, limits)

    def reset(self, state: np.ndarray) -> None:
        self.command_state = as_state_matrix(state, self.dof)
        self._fallback.reset(self.command_state)

    def update(
        self,
        target: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> FollowerResult:
        started = perf_counter_ns()
        target_value = as_state_matrix(target, self.dof)
        if current_state is not None:
            current = as_state_matrix(current_state, self.dof)
        elif self.command_state is not None:
            current = self.command_state.copy()
        else:
            raise ValueError("first update requires current_state")
        jerk = (target_value[:, 2] - current[:, 2]) / self.dt
        reconstructed = integrate_constant_jerk(current, jerk, self.dt)
        reachable = np.allclose(reconstructed, target_value, rtol=0.0, atol=2e-8)
        feasible = reachable and all(
            segment_is_feasible(current[j], jerk[j], self.dt, self.limits, j)
            for j in range(self.dof)
        )
        if not feasible:
            fallback = self._fallback.update(
                target_value, control_time=control_time, current_state=current
            )
            command = fallback.executable_state
            jerk = fallback.jerk
            fallback_reason = (
                "target_not_one_step_reachable"
                if not reachable
                else "target_segment_infeasible"
            )
            status = "direct_fallback_one_step"
        else:
            command = target_value
            fallback_reason = ""
            status = "direct"
        free_duration = np.nan
        try:
            inp = InputParameter(self.dof)
            trajectory = Trajectory(self.dof)
            _configure_input(inp, current, command, self.limits, None)
            result = Ruckig(self.dof, self.dt).calculate(inp, trajectory)
            if int(result) >= 0:
                free_duration = float(trajectory.duration)
                if free_duration > self.dt + 1e-8:
                    feasible = False
                    fallback_reason = fallback_reason or "free_duration_exceeds_dt"
            else:
                feasible = False
                fallback_reason = fallback_reason or "free_duration_solver_failure"
        except Exception:
            feasible = False
            fallback_reason = fallback_reason or "free_duration_solver_exception"
        self.command_state = command.copy()
        self._fallback.command_state = command.copy()
        audit = _audit_constant_jerk_segment(
            current, command, jerk, self.dt, self.limits
        )
        return FollowerResult(
            command,
            jerk,
            control_time + self.dt,
            status,
            not feasible,
            fallback_reason,
            False,
            free_duration,
            self.dt,
            (perf_counter_ns() - started) / 1000.0,
            audit,
        )


class RuckigFollower:
    """Ordinary Community Ruckig follower using one synchronized n-DoF solve."""

    name = "ordinary_ruckig"

    def __init__(
        self,
        dof: int,
        dt: float,
        limits: MotionLimits,
        *,
        minimum_duration: float | None = None,
        project_targets: bool = False,
        audit_grid_dt: float = 0.0001,
    ) -> None:
        if dof != limits.dof:
            raise ValueError("limits dof mismatch")
        self.dof = dof
        self.dt = float(dt)
        self.minimum_duration = (
            self.dt if minimum_duration is None else float(minimum_duration)
        )
        if abs(self.minimum_duration - self.dt) > 1e-15:
            # Non-formal runs may opt in explicitly, but horizon is never accepted
            # by this API and can therefore never alter minimum duration.
            if self.minimum_duration <= 0.0:
                raise ValueError("minimum_duration must be positive")
        self.limits = limits
        self.project_targets = bool(project_targets)
        self.audit_grid_dt = float(audit_grid_dt)
        self.command_state: np.ndarray | None = None
        self._otg = Ruckig(dof, dt)
        self._fallback = OneStepBoundedJerkGovernor(dof, dt, limits)

    def reset(self, state: np.ndarray) -> None:
        self.command_state = as_state_matrix(state, self.dof)
        self._otg.reset()
        self._fallback.reset(self.command_state)

    def _calculate(self, current: np.ndarray, target: np.ndarray, minimum_duration):
        inp = InputParameter(self.dof)
        trajectory = Trajectory(self.dof)
        _configure_input(inp, current, target, self.limits, minimum_duration)
        result = self._otg.calculate(inp, trajectory)
        return result, trajectory

    def update(
        self,
        target: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> FollowerResult:
        started = perf_counter_ns()
        raw_target = as_state_matrix(target, self.dof)
        if current_state is not None:
            current = as_state_matrix(current_state, self.dof)
        elif self.command_state is not None:
            current = self.command_state.copy()
        else:
            raise ValueError("first update requires current_state")
        target_value = raw_target
        target_projected = False
        if self.project_targets:
            try:
                target_value, target_projected = scalar_project_target_state(
                    raw_target, self.limits
                )
            except ValueError:
                target_projected = False
        if not np.all(np.isfinite(target_value)):
            fallback = self._fallback.update(
                np.nan_to_num(target_value),
                control_time=control_time,
                current_state=current,
            )
            self.command_state = fallback.executable_state.copy()
            return FollowerResult(
                fallback.executable_state,
                fallback.jerk,
                control_time + self.dt,
                "invalid_target_fallback",
                True,
                "nonfinite_target",
                target_projected,
                np.nan,
                self.dt,
                (perf_counter_ns() - started) / 1000.0,
                _audit_constant_jerk_segment(
                    current,
                    fallback.executable_state,
                    fallback.jerk,
                    self.dt,
                    self.limits,
                ),
            )

        try:
            free_result, free_trajectory = self._calculate(current, target_value, None)
            free_duration = (
                float(free_trajectory.duration) if int(free_result) >= 0 else np.nan
            )
            frozen_result, frozen_trajectory = self._calculate(
                current, target_value, self.minimum_duration
            )
        except Exception as error:
            fallback = self._fallback.update(
                target_value, control_time=control_time, current_state=current
            )
            self.command_state = fallback.executable_state.copy()
            return FollowerResult(
                fallback.executable_state,
                fallback.jerk,
                control_time + self.dt,
                f"ruckig_exception:{type(error).__name__}",
                True,
                "ruckig_exception",
                target_projected,
                np.nan,
                self.dt,
                (perf_counter_ns() - started) / 1000.0,
                _audit_constant_jerk_segment(
                    current,
                    fallback.executable_state,
                    fallback.jerk,
                    self.dt,
                    self.limits,
                ),
            )
        if int(frozen_result) < 0:
            fallback = self._fallback.update(
                target_value, control_time=control_time, current_state=current
            )
            self.command_state = fallback.executable_state.copy()
            return FollowerResult(
                fallback.executable_state,
                fallback.jerk,
                control_time + self.dt,
                f"ruckig_error_{int(frozen_result)}",
                True,
                "ruckig_solver_failure",
                target_projected,
                free_duration,
                np.nan,
                (perf_counter_ns() - started) / 1000.0,
                _audit_constant_jerk_segment(
                    current,
                    fallback.executable_state,
                    fallback.jerk,
                    self.dt,
                    self.limits,
                ),
            )
        position, velocity, acceleration = frozen_trajectory.at_time(self.dt)
        command = np.column_stack((position, velocity, acceleration))
        jerk = (command[:, 2] - current[:, 2]) / self.dt
        audit = audit_frozen_trajectory(
            frozen_trajectory, self.limits, grid_dt=self.audit_grid_dt
        )
        self.command_state = command.copy()
        self._fallback.command_state = command.copy()
        return FollowerResult(
            command,
            jerk,
            control_time + self.dt,
            str(frozen_result),
            False,
            "",
            target_projected,
            free_duration,
            float(frozen_trajectory.duration),
            (perf_counter_ns() - started) / 1000.0,
            audit,
        )
