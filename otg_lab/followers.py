"""Follower implementations and frozen-trajectory constraint audits."""

from __future__ import annotations

from time import perf_counter_ns

import numpy as np
from ruckig import InputParameter, Ruckig, Trajectory

from .constraints import (
    InvariantViolationError,
    integrate_constant_jerk,
    ruckig_target_admissible,
    segment_constant_jerk_feasible,
    terminal_has_viable_next_step,
    terminal_stopping_viable,
)
from .governors import (
    MotionLimits,
    OneStepBoundedJerkGovernor,
    as_state_matrix,
    segment_is_feasible,
    velocity_extrema_constant_jerk,
)
from .types import CommandProfile, FollowerResult


def _coalesce_times(
    values: list[float] | np.ndarray,
    *,
    start: float,
    end: float,
    tolerance: float | None = None,
) -> np.ndarray:
    """Remove only floating-point duplicates, preserving real short segments."""

    if tolerance is None:
        scale = max(abs(float(start)), abs(float(end)), np.finfo(float).tiny)
        tolerance = 32.0 * abs(float(np.spacing(scale)))
    ordered = sorted(float(value) for value in values if start <= value <= end)
    merged = [float(start)]
    for value in ordered:
        if value <= start + tolerance or value >= end - tolerance:
            continue
        if value - merged[-1] > tolerance:
            merged.append(value)
    merged.append(float(end))
    return np.asarray(merged, dtype=float)


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


def _trajectory_axis_segments(
    trajectory: Trajectory, dof: int
) -> list[list[tuple[float, float, float]]] | None:
    """Parse exposed brake/main/accel profiles into absolute-time segments."""

    axis_segments: list[list[tuple[float, float, float]]] = [[] for _ in range(dof)]
    section_start = 0.0
    try:
        sections = list(trajectory.profiles)
        if not sections:
            return None
        for section in sections:
            if len(section) < dof:
                return None
            section_durations = np.zeros(dof)
            pending: list[list[tuple[float, float, float]]] = [[] for _ in range(dof)]
            for joint, profile in enumerate(section[:dof]):
                cursor = section_start
                for sequence in (
                    getattr(profile, "brake", None),
                    profile,
                    getattr(profile, "accel", None),
                ):
                    if sequence is None:
                        continue
                    durations = list(getattr(sequence, "t", []))
                    jerks = list(getattr(sequence, "j", []))
                    if len(jerks) < len(durations):
                        return None
                    for duration_value, jerk_value in zip(durations, jerks):
                        duration = float(duration_value)
                        jerk = float(jerk_value)
                        if not np.isfinite(duration) or duration < 0.0:
                            return None
                        if not np.isfinite(jerk):
                            return None
                        if duration > 1e-15:
                            pending[joint].append((cursor, cursor + duration, jerk))
                        cursor += duration
                section_durations[joint] = cursor - section_start
            section_duration = float(np.max(section_durations))
            if section_duration <= 0.0 or not np.allclose(
                section_durations, section_duration, rtol=0.0, atol=2e-8
            ):
                return None
            for joint in range(dof):
                axis_segments[joint].extend(pending[joint])
            section_start += section_duration
        if not np.isclose(
            section_start, float(trajectory.duration), rtol=0.0, atol=2e-8
        ):
            return None
    except (AttributeError, TypeError, ValueError, IndexError):
        return None
    return axis_segments


def _extract_ruckig_command_profile(
    trajectory: Trajectory,
    *,
    start: float,
    end: float,
    dof: int,
) -> CommandProfile | None:
    """Return an exact, independently evaluable prefix from exposed profiles."""

    axis_segments = _trajectory_axis_segments(trajectory, dof)
    if axis_segments is None:
        return None
    boundaries = [float(start), float(end)]
    for segments in axis_segments:
        for left, right, _jerk in segments:
            if start < left < end:
                boundaries.append(left)
            if start < right < end:
                boundaries.append(right)
    absolute_boundaries = _coalesce_times(boundaries, start=start, end=end)
    segment_jerks = np.empty((absolute_boundaries.size - 1, dof), dtype=float)
    for index, (left, right) in enumerate(
        zip(absolute_boundaries[:-1], absolute_boundaries[1:])
    ):
        midpoint = 0.5 * (left + right)
        for joint, segments in enumerate(axis_segments):
            matches = [
                jerk
                for segment_start, segment_end, jerk in segments
                if segment_start <= midpoint < segment_end
            ]
            if not matches:
                return None
            segment_jerks[index, joint] = matches[0]
    try:
        initial = np.column_stack(trajectory.at_time(float(start)))
        terminal = np.column_stack(trajectory.at_time(float(end)))
        profile = CommandProfile(
            profile_kind="ruckig_piecewise_constant_jerk",
            start_time=float(start),
            duration=float(end - start),
            initial_state=initial,
            terminal_state=terminal,
            segment_boundaries=absolute_boundaries - start,
            segment_jerks=segment_jerks,
            source="ruckig_frozen_trajectory_prefix",
            exact=True,
        )
    except (TypeError, ValueError):
        return None
    if not profile.endpoint_matches_profile:
        return None
    return profile


def _trajectory_boundaries(trajectory: Trajectory) -> np.ndarray:
    """Extract accessible Ruckig profile boundaries for audit sampling."""

    boundaries = {0.0, float(trajectory.duration)}
    try:
        section_start = 0.0
        for section in trajectory.profiles:
            section_ends: list[float] = []
            for profile in section:
                offset = section_start
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
                section_ends.append(offset)
            if section_ends:
                section_start = max(section_ends)
    except (AttributeError, TypeError, ValueError):
        pass
    duration = float(trajectory.duration)
    return _coalesce_times(list(boundaries), start=0.0, end=duration)


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


def _audit_command_profile(
    profile: CommandProfile,
    limits: MotionLimits,
    tolerance: float,
) -> dict[str, np.ndarray | float | int | str | bool]:
    """Compute exact V/A/J extrema for a parsed piecewise-jerk profile."""

    max_velocity = np.abs(profile.initial_state[:, 1]).copy()
    max_acceleration = np.abs(profile.initial_state[:, 2]).copy()
    max_jerk = np.zeros(limits.dof)
    velocity_time = np.zeros(limits.dof)
    acceleration_time = np.zeros(limits.dof)
    jerk_time = np.zeros(limits.dof)
    violations = np.zeros(limits.dof, dtype=int)
    state = np.array(profile.initial_state, dtype=float, copy=True)
    for index, jerk in enumerate(profile.segment_jerks):
        left = float(profile.segment_boundaries[index])
        right = float(profile.segment_boundaries[index + 1])
        duration = right - left
        terminal = integrate_constant_jerk(state, jerk, duration)
        for joint in range(limits.dof):
            acceleration_candidates = (
                (abs(float(state[joint, 2])), left),
                (abs(float(terminal[joint, 2])), right),
            )
            for magnitude, occurrence in acceleration_candidates:
                if magnitude > max_acceleration[joint]:
                    max_acceleration[joint] = magnitude
                    acceleration_time[joint] = occurrence
            velocity_candidates = [
                (abs(float(state[joint, 1])), left),
                (abs(float(terminal[joint, 1])), right),
            ]
            if jerk[joint] != 0.0:
                extremum = -state[joint, 2] / jerk[joint]
                if 0.0 < extremum < duration:
                    velocity = (
                        state[joint, 1]
                        + state[joint, 2] * extremum
                        + 0.5 * jerk[joint] * extremum**2
                    )
                    velocity_candidates.append((abs(float(velocity)), left + extremum))
            for magnitude, occurrence in velocity_candidates:
                if magnitude > max_velocity[joint]:
                    max_velocity[joint] = magnitude
                    velocity_time[joint] = occurrence
            if abs(jerk[joint]) > max_jerk[joint]:
                max_jerk[joint] = abs(jerk[joint])
                jerk_time[joint] = left
            if (
                max(value[0] for value in velocity_candidates)
                > limits.max_velocity[joint] + tolerance
                or max(value[0] for value in acceleration_candidates)
                > limits.max_acceleration[joint] + tolerance
                or abs(jerk[joint]) > limits.max_jerk[joint] + tolerance
            ):
                violations[joint] += 1
        state = terminal
    acceleration_difference_jerk = (
        profile.terminal_state[:, 2] - profile.initial_state[:, 2]
    ) / profile.duration
    return {
        "prefix_start_time": profile.start_time,
        "duration": profile.duration,
        "sample_count": 0,
        "audit_method": "analytic_ruckig_piecewise_constant_jerk",
        "profile_exact": profile.exact,
        "endpoint_matches_profile": profile.endpoint_matches_profile,
        "segment_boundaries": profile.segment_boundaries,
        "segment_count": profile.segment_count,
        "boundary_count": profile.boundary_count,
        "max_velocity": max_velocity,
        "max_acceleration": max_acceleration,
        "max_internal_jerk": max_jerk,
        "max_sampled_jerk": np.full(limits.dof, np.nan),
        "acceleration_difference_jerk": acceleration_difference_jerk,
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


def audit_ruckig_prefix(
    trajectory: Trajectory,
    limits: MotionLimits,
    *,
    start: float = 0.0,
    end: float | None = None,
    grid_dt: float = 0.0001,
    tolerance: float = 1e-8,
) -> dict[str, np.ndarray | float | int | str | bool | CommandProfile | None]:
    """Audit a Ruckig trajectory prefix without assuming one constant jerk.

    Exposed brake/main/accel segments are preferred and yield analytic extrema.
    If a binding does not expose those profiles, velocity and acceleration are
    sampled on a <=0.1 ms grid that also includes every accessible boundary.
    The sampled fallback deliberately leaves internal jerk unavailable instead
    of relabeling acceleration finite differences as solver-internal jerk.
    """

    if grid_dt <= 0.0 or grid_dt > 0.0001 + 1e-15:
        raise ValueError("grid_dt must be in (0, 0.1 ms]")
    duration = float(trajectory.duration)
    start_value = float(start)
    end_value = duration if end is None else float(end)
    if not (
        np.isfinite(start_value)
        and np.isfinite(end_value)
        and 0.0 <= start_value < end_value <= duration + 1e-12
    ):
        raise ValueError("prefix must satisfy 0 <= start < end <= duration")
    end_value = min(end_value, duration)
    profile = _extract_ruckig_command_profile(
        trajectory, start=start_value, end=end_value, dof=limits.dof
    )
    if profile is not None:
        audit = _audit_command_profile(profile, limits, tolerance)
        audit["command_profile"] = profile
        return audit

    regular = np.arange(start_value, end_value + grid_dt * 0.5, grid_dt)
    accessible = _trajectory_boundaries(trajectory)
    accessible = accessible[(accessible >= start_value) & (accessible <= end_value)]
    times = _coalesce_times(
        np.concatenate((regular, accessible, [start_value, end_value])),
        start=start_value,
        end=end_value,
    )
    positions = np.empty((times.size, limits.dof))
    velocities = np.empty_like(positions)
    accelerations = np.empty_like(velocities)
    for index, sample_time in enumerate(times):
        position, velocity, acceleration = trajectory.at_time(float(sample_time))
        positions[index] = position
        velocities[index] = velocity
        accelerations[index] = acceleration
    max_velocity = np.max(np.abs(velocities), axis=0)
    max_acceleration = np.max(np.abs(accelerations), axis=0)
    acceleration_difference_jerk = (accelerations[-1] - accelerations[0]) / (
        end_value - start_value
    )
    sampled_jerks = np.diff(accelerations, axis=0) / np.diff(times)[:, None]
    sampled_jerk_indices = np.argmax(np.abs(sampled_jerks), axis=0)
    max_sampled_jerk = np.max(np.abs(sampled_jerks), axis=0)
    sampled_jerk_times = 0.5 * (times[:-1] + times[1:])
    violations = (np.abs(velocities) > limits.max_velocity + tolerance).sum(axis=0)
    violations += (np.abs(accelerations) > limits.max_acceleration + tolerance).sum(
        axis=0
    )
    violations += (max_sampled_jerk > limits.max_jerk + tolerance).astype(int)
    unknown_jerk = np.full(limits.dof, np.nan)
    relative_boundaries = np.asarray(
        sorted(
            set((accessible - start_value).tolist() + [0.0, end_value - start_value])
        )
    )
    profile = CommandProfile(
        profile_kind="ruckig_piecewise_constant_jerk",
        start_time=start_value,
        duration=end_value - start_value,
        initial_state=np.column_stack(
            (positions[0], velocities[0], accelerations[0])
        ),
        terminal_state=np.column_stack(
            (positions[-1], velocities[-1], accelerations[-1])
        ),
        segment_boundaries=relative_boundaries,
        segment_jerks=np.empty((0, limits.dof)),
        source="ruckig_sampled_trajectory_prefix",
        exact=False,
        sample_times=times - start_value,
        sample_states=np.stack((positions, velocities, accelerations), axis=2),
    )
    return {
        "prefix_start_time": start_value,
        "duration": end_value - start_value,
        "sample_count": int(times.size),
        "audit_method": "sampled_ruckig_grid_with_boundaries",
        "profile_exact": False,
        "endpoint_matches_profile": profile.endpoint_matches_profile,
        "segment_boundaries": relative_boundaries,
        "segment_count": 0,
        "boundary_count": profile.boundary_count,
        "max_velocity": max_velocity,
        "max_acceleration": max_acceleration,
        "max_internal_jerk": unknown_jerk,
        "max_sampled_jerk": max_sampled_jerk,
        "acceleration_difference_jerk": acceleration_difference_jerk,
        "velocity_max_time": times[np.argmax(np.abs(velocities), axis=0)] - start_value,
        "acceleration_max_time": times[np.argmax(np.abs(accelerations), axis=0)]
        - start_value,
        "jerk_max_time": sampled_jerk_times[sampled_jerk_indices] - start_value,
        "velocity_margin": limits.max_velocity - max_velocity,
        "acceleration_margin": limits.max_acceleration - max_acceleration,
        "jerk_margin": limits.max_jerk - max_sampled_jerk,
        "violation_count": violations,
        "worst_excess": float(
            max(
                0.0,
                np.max(max_velocity - limits.max_velocity),
                np.max(max_acceleration - limits.max_acceleration),
                np.max(max_sampled_jerk - limits.max_jerk),
            )
        ),
        "sampled_last_jerk": sampled_jerks[-1],
        "command_profile": profile,
    }


def audit_frozen_trajectory(
    trajectory: Trajectory,
    limits: MotionLimits,
    *,
    grid_dt: float = 0.0001,
    tolerance: float = 1e-8,
) -> dict[str, np.ndarray | float | int]:
    """Audit all frozen trajectory time with <=0.1 ms grid plus boundaries."""

    if float(trajectory.duration) > 0.0:
        audit = audit_ruckig_prefix(
            trajectory,
            limits,
            start=0.0,
            end=float(trajectory.duration),
            grid_dt=grid_dt,
            tolerance=tolerance,
        )
        audit.pop("command_profile", None)
        return audit
    if grid_dt <= 0.0 or grid_dt > 0.0001 + 1e-15:
        raise ValueError("grid_dt must be in (0, 0.1 ms]")
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
    """Compatibility wrapper around the shared target admissibility rule."""

    return bool(ruckig_target_admissible(target, limits, tolerance=tolerance))


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
            not segment_is_feasible(
                current[joint], jerk[joint], duration, limits, joint
            )
        )
    acceleration_values = np.column_stack((current[:, 2], command[:, 2]))
    acceleration_indices = np.argmax(np.abs(acceleration_values), axis=1)
    max_acceleration = np.max(np.abs(acceleration_values), axis=1)
    maximum_jerk = np.abs(jerk)
    acceleration_difference_jerk = (command[:, 2] - current[:, 2]) / duration
    return {
        "duration": float(duration),
        "sample_count": 2,
        "audit_method": "analytic_constant_jerk",
        "max_velocity": max_velocity,
        "max_acceleration": max_acceleration,
        "max_internal_jerk": maximum_jerk,
        "max_sampled_jerk": maximum_jerk,
        "acceleration_difference_jerk": acceleration_difference_jerk,
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


def _constant_jerk_profile(
    current: np.ndarray,
    command: np.ndarray,
    jerk: np.ndarray,
    duration: float,
    *,
    emergency: bool,
    source: str,
) -> CommandProfile:
    return CommandProfile(
        profile_kind=("emergency_constant_jerk" if emergency else "constant_jerk"),
        start_time=0.0,
        duration=duration,
        initial_state=current,
        terminal_state=command,
        segment_boundaries=np.asarray([0.0, duration]),
        segment_jerks=np.asarray(jerk, dtype=float).reshape(1, -1),
        source=source,
        exact=True,
    )


def _all_true(value: object) -> bool:
    """Normalize scalar or per-axis constraint predicates."""

    return bool(np.all(np.asarray(value, dtype=bool)))


def _command_checks(
    current: np.ndarray,
    command: np.ndarray,
    jerk: np.ndarray,
    dt: float,
    limits: MotionLimits,
) -> tuple[bool, bool, bool, bool]:
    """Return reachability, segment, stopping, and next-action checks."""

    if not (
        np.all(np.isfinite(current))
        and np.all(np.isfinite(command))
        and np.all(np.isfinite(jerk))
    ):
        return False, False, False, False
    reconstructed = integrate_constant_jerk(current, jerk, dt)
    reachable = bool(np.allclose(reconstructed, command, rtol=0.0, atol=2e-8))
    segment_ok = reachable and _all_true(
        segment_constant_jerk_feasible(current, jerk, dt, limits)
    )
    terminal_ok = reachable and _all_true(terminal_stopping_viable(command, limits))
    next_step_ok = terminal_ok and terminal_has_viable_next_step(command, dt, limits)
    return reachable, segment_ok, terminal_ok, next_step_ok


def _result_succeeded(result: object) -> bool:
    try:
        return int(result) >= 0
    except (TypeError, ValueError):
        return False


def _failure_reason(
    *,
    reachable: bool,
    segment_ok: bool,
    terminal_ok: bool,
    next_step_ok: bool,
    t_free_ok: bool,
) -> str:
    if not reachable:
        return "command_not_one_step_reachable"
    if not segment_ok:
        return "command_segment_infeasible"
    if not terminal_ok:
        return "command_terminal_not_viable"
    if not next_step_ok:
        return "command_no_viable_next_step"
    if not t_free_ok:
        return "command_t_free_exceeds_dt"
    return ""


class DirectExecutableFollower:
    """Execute an already one-step-reachable target without a second planner."""

    name = "direct_executable"

    def __init__(
        self,
        dof: int,
        dt: float,
        limits: MotionLimits,
        *,
        require_t_free_le_dt: bool = True,
        formal: bool = False,
    ) -> None:
        if dof != limits.dof:
            raise ValueError("limits dof mismatch")
        self.dof = dof
        self.dt = float(dt)
        self.limits = limits
        self.require_t_free_le_dt = bool(require_t_free_le_dt)
        self.formal = bool(formal)
        self.command_state: np.ndarray | None = None
        self._fallback = OneStepBoundedJerkGovernor(dof, dt, limits)

    def reset(self, state: np.ndarray) -> None:
        self.command_state = as_state_matrix(state, self.dof)
        self._fallback.reset(self.command_state)

    def _free_duration(
        self, current: np.ndarray, target: np.ndarray
    ) -> tuple[float, bool, str]:
        try:
            inp = InputParameter(self.dof)
            trajectory = Trajectory(self.dof)
            _configure_input(inp, current, target, self.limits, None)
            result = Ruckig(self.dof, self.dt).calculate(inp, trajectory)
        except Exception as error:
            return np.nan, False, f"free_duration_exception:{type(error).__name__}"
        if not _result_succeeded(result):
            return np.nan, False, f"free_duration_error_{int(result)}"
        duration = float(trajectory.duration)
        valid = bool(
            np.isfinite(duration) and duration >= 0.0 and duration <= self.dt + 1e-8
        )
        return duration, valid, str(result)

    def _commit(self, command: np.ndarray, jerk: np.ndarray) -> None:
        self.command_state = command.copy()
        self._fallback.command_state = command.copy()
        self._fallback.last_jerk = jerk.copy()

    def _apply_fallback(
        self,
        target: np.ndarray,
        current: np.ndarray,
        *,
        control_time: float,
        reason: str,
        status: str,
        requested_target_feasible: bool,
        requested_free_duration: float,
        started: int,
    ) -> FollowerResult:
        # The fallback must be generated from the actual current state; merely
        # changing a status flag while committing the failed candidate is not a
        # fallback.
        self._fallback.command_state = current.copy()
        fallback = self._fallback.update(
            target, control_time=control_time, current_state=current
        )
        command = np.asarray(fallback.executable_state, dtype=float)
        jerk = np.asarray(fallback.jerk, dtype=float)
        reachable, segment_ok, terminal_ok, next_step_ok = _command_checks(
            current, command, jerk, self.dt, self.limits
        )
        free_duration, t_free_ok, fallback_solver_status = self._free_duration(
            current, command
        )
        physical_safety = bool(segment_ok and terminal_ok and next_step_ok)
        current_viable = bool(
            _all_true(terminal_stopping_viable(current, self.limits))
            and terminal_has_viable_next_step(current, self.dt, self.limits)
        )
        emergency_mode = bool(
            getattr(fallback, "emergency_mode", False)
            or not current_viable
            or not physical_safety
        )
        full_check = physical_safety and (t_free_ok or not self.require_t_free_le_dt)
        if self.formal and current_viable and not full_check:
            detail = _failure_reason(
                reachable=reachable,
                segment_ok=segment_ok,
                terminal_ok=terminal_ok,
                next_step_ok=next_step_ok,
                t_free_ok=t_free_ok,
            )
            raise InvariantViolationError(
                "direct follower safety fallback failed validation: "
                f"{detail or fallback_solver_status}"
            )
        self._commit(command, jerk)
        audit = _audit_constant_jerk_segment(
            current, command, jerk, self.dt, self.limits
        )
        profile = _constant_jerk_profile(
            current,
            command,
            jerk,
            self.dt,
            emergency=emergency_mode,
            source="direct_follower_one_step_fallback",
        )
        return FollowerResult(
            command_state=command,
            command_jerk=jerk,
            command_time=control_time + self.dt,
            solver_status=f"{status}->one_step:{fallback_solver_status}",
            fallback_reason=reason,
            target_projected=False,
            requested_target_free_trajectory_duration=requested_free_duration,
            free_trajectory_duration=free_duration,
            frozen_trajectory_duration=self.dt,
            compute_us=(perf_counter_ns() - started) / 1000.0,
            continuous_audit=audit,
            requested_target_feasible=requested_target_feasible,
            command_segment_feasible=segment_ok,
            command_terminal_viable=terminal_ok,
            command_next_step_exists=next_step_ok,
            command_t_free_le_dt=t_free_ok,
            fallback_requested=True,
            fallback_applied=True,
            safety_guarantee=physical_safety,
            emergency_mode=emergency_mode,
            command_profile=profile,
            native_follower=self.name,
            native_command_executed=False,
            safety_shield_requested=True,
            safety_shield_applied=True,
            safety_shield_reason=reason,
            fallback_controller="one_step_bounded_jerk",
            fallback_changes_algorithm=True,
        )

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
        if not np.all(np.isfinite(current)):
            raise InvariantViolationError("current state must be finite")
        if not np.all(np.isfinite(target_value)):
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="nonfinite_target",
                status="invalid_target",
                requested_target_feasible=False,
                requested_free_duration=np.nan,
                started=started,
            )
        jerk = (target_value[:, 2] - current[:, 2]) / self.dt
        reachable, segment_ok, terminal_ok, next_step_ok = _command_checks(
            current, target_value, jerk, self.dt, self.limits
        )
        if not (reachable and segment_ok and terminal_ok and next_step_ok):
            reason = _failure_reason(
                reachable=reachable,
                segment_ok=segment_ok,
                terminal_ok=terminal_ok,
                next_step_ok=next_step_ok,
                t_free_ok=True,
            )
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason=reason,
                status="direct_postcheck_failed",
                requested_target_feasible=False,
                requested_free_duration=np.nan,
                started=started,
            )

        free_duration, t_free_ok, free_status = self._free_duration(
            current, target_value
        )
        if self.require_t_free_le_dt and not t_free_ok:
            reason = (
                "free_duration_exceeds_dt"
                if np.isfinite(free_duration)
                else (
                    "free_duration_solver_exception"
                    if "exception" in free_status
                    else "free_duration_solver_failure"
                )
            )
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason=reason,
                status=f"direct:{free_status}",
                requested_target_feasible=False,
                requested_free_duration=free_duration,
                started=started,
            )

        command = target_value.copy()
        self._commit(command, jerk)
        audit = _audit_constant_jerk_segment(
            current, command, jerk, self.dt, self.limits
        )
        profile = _constant_jerk_profile(
            current,
            command,
            jerk,
            self.dt,
            emergency=False,
            source="direct_executable_follower",
        )
        return FollowerResult(
            command_state=command,
            command_jerk=jerk,
            command_time=control_time + self.dt,
            solver_status=f"direct:{free_status}",
            fallback_reason="",
            target_projected=False,
            requested_target_free_trajectory_duration=free_duration,
            free_trajectory_duration=free_duration,
            frozen_trajectory_duration=self.dt,
            compute_us=(perf_counter_ns() - started) / 1000.0,
            continuous_audit=audit,
            requested_target_feasible=True,
            command_segment_feasible=True,
            command_terminal_viable=True,
            command_next_step_exists=True,
            command_t_free_le_dt=t_free_ok,
            fallback_requested=False,
            fallback_applied=False,
            safety_guarantee=True,
            emergency_mode=False,
            command_profile=profile,
            native_follower=self.name,
            native_command_executed=True,
        )


class RuckigFollower:
    """Execute the actual piecewise-jerk prefix of one synchronized solve."""

    name = "ordinary_ruckig_unshielded"

    def __init__(
        self,
        dof: int,
        dt: float,
        limits: MotionLimits,
        *,
        minimum_duration: float | None = None,
        project_targets: bool = False,
        audit_grid_dt: float = 0.0001,
        formal: bool = False,
        safety_shield: bool = False,
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
        self.formal = bool(formal)
        self.safety_shield = bool(safety_shield)
        self.name = (
            "ordinary_ruckig_with_viability_shield"
            if self.safety_shield
            else "ordinary_ruckig_unshielded"
        )
        self.command_state: np.ndarray | None = None
        self.frozen_trajectory: Trajectory | None = None
        self._otg = Ruckig(dof, dt)
        self._fallback = OneStepBoundedJerkGovernor(dof, dt, limits)

    def reset(self, state: np.ndarray) -> None:
        self.command_state = as_state_matrix(state, self.dof)
        self.frozen_trajectory = None
        self._otg.reset()
        self._fallback.reset(self.command_state)

    def _calculate(self, current: np.ndarray, target: np.ndarray, minimum_duration):
        inp = InputParameter(self.dof)
        trajectory = Trajectory(self.dof)
        _configure_input(inp, current, target, self.limits, minimum_duration)
        result = self._otg.calculate(inp, trajectory)
        return result, trajectory

    def _free_duration(
        self, current: np.ndarray, target: np.ndarray
    ) -> tuple[float, bool, str]:
        try:
            result, trajectory = self._calculate(current, target, None)
        except Exception as error:
            return np.nan, False, f"free_duration_exception:{type(error).__name__}"
        if not _result_succeeded(result):
            return np.nan, False, f"free_duration_error_{int(result)}"
        duration = float(trajectory.duration)
        valid = bool(
            np.isfinite(duration) and duration >= 0.0 and duration <= self.dt + 1e-8
        )
        return duration, valid, str(result)

    def _commit(self, command: np.ndarray, jerk: np.ndarray) -> None:
        self.command_state = command.copy()
        self._fallback.command_state = command.copy()
        self._fallback.last_jerk = jerk.copy()

    def _apply_fallback(
        self,
        target: np.ndarray,
        current: np.ndarray,
        *,
        control_time: float,
        reason: str,
        native_status: str,
        requested_solver_status: str,
        requested_target_feasible: bool,
        target_projected: bool,
        requested_free_duration: float,
        started: int,
    ) -> FollowerResult:
        if not self.safety_shield:
            raise InvariantViolationError(
                "ordinary Ruckig unshielded method failure: "
                f"{reason} ({native_status})"
            )
        self._fallback.command_state = current.copy()
        fallback = self._fallback.update(
            target, control_time=control_time, current_state=current
        )
        command = np.asarray(fallback.executable_state, dtype=float)
        jerk = np.asarray(fallback.jerk, dtype=float)
        reachable, segment_ok, terminal_ok, next_step_ok = _command_checks(
            current, command, jerk, self.dt, self.limits
        )
        command_duration, t_free_ok, command_solver_status = self._free_duration(
            current, command
        )
        physical_safety = bool(segment_ok and terminal_ok and next_step_ok)
        current_viable = bool(
            _all_true(terminal_stopping_viable(current, self.limits))
            and terminal_has_viable_next_step(current, self.dt, self.limits)
        )
        emergency_mode = bool(
            getattr(fallback, "emergency_mode", False)
            or not current_viable
            or not physical_safety
        )
        if self.formal and current_viable and not (physical_safety and t_free_ok):
            detail = _failure_reason(
                reachable=reachable,
                segment_ok=segment_ok,
                terminal_ok=terminal_ok,
                next_step_ok=next_step_ok,
                t_free_ok=t_free_ok,
            )
            raise InvariantViolationError(
                "Ruckig follower safety fallback failed validation: "
                f"{detail or command_solver_status}"
            )
        self._commit(command, jerk)
        audit = _audit_constant_jerk_segment(
            current, command, jerk, self.dt, self.limits
        )
        profile = _constant_jerk_profile(
            current,
            command,
            jerk,
            self.dt,
            emergency=emergency_mode,
            source="ruckig_viability_shield_one_step_fallback",
        )
        return FollowerResult(
            command_state=command,
            command_jerk=jerk,
            command_time=control_time + self.dt,
            solver_status=(
                f"requested_target_free:{requested_solver_status}"
                f"|native_prefix:{native_status}"
                f"|committed_command_free:{command_solver_status}"
                "|shield:one_step_bounded_jerk"
            ),
            fallback_reason=reason,
            target_projected=target_projected,
            requested_target_free_trajectory_duration=requested_free_duration,
            free_trajectory_duration=command_duration,
            frozen_trajectory_duration=self.dt,
            compute_us=(perf_counter_ns() - started) / 1000.0,
            continuous_audit=audit,
            requested_target_feasible=requested_target_feasible,
            command_segment_feasible=segment_ok,
            command_terminal_viable=terminal_ok,
            command_next_step_exists=next_step_ok,
            command_t_free_le_dt=t_free_ok,
            fallback_requested=True,
            fallback_applied=True,
            safety_guarantee=physical_safety,
            emergency_mode=emergency_mode,
            command_profile=profile,
            native_follower="ordinary_ruckig",
            native_command_executed=False,
            safety_shield_requested=True,
            safety_shield_applied=True,
            safety_shield_reason=reason,
            fallback_controller="one_step_bounded_jerk",
            fallback_changes_algorithm=True,
        )

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
        if not np.all(np.isfinite(current)):
            raise InvariantViolationError("current state must be finite")
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
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="nonfinite_target",
                native_status="not_run:invalid_target",
                requested_solver_status="not_run:nonfinite_target",
                requested_target_feasible=False,
                target_projected=target_projected,
                requested_free_duration=np.nan,
                started=started,
            )

        target_admissible = _all_true(
            ruckig_target_admissible(target_value, self.limits)
        )
        if not target_admissible:
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="requested_target_not_ruckig_admissible",
                native_status="not_run:target_postcheck_failed",
                requested_solver_status="not_run:target_postcheck_failed",
                requested_target_feasible=False,
                target_projected=target_projected,
                requested_free_duration=np.nan,
                started=started,
            )

        try:
            free_result, free_trajectory = self._calculate(current, target_value, None)
        except Exception as error:
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="ruckig_exception",
                native_status="not_run",
                requested_solver_status=(
                    f"free_duration_exception:{type(error).__name__}"
                ),
                requested_target_feasible=False,
                target_projected=target_projected,
                requested_free_duration=np.nan,
                started=started,
            )
        requested_solver_status = str(free_result)
        requested_free_duration = (
            float(free_trajectory.duration) if _result_succeeded(free_result) else np.nan
        )
        if not _result_succeeded(free_result):
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="free_duration_solver_failure",
                native_status="not_run",
                requested_solver_status=f"ruckig_error_{int(free_result)}",
                requested_target_feasible=False,
                target_projected=target_projected,
                requested_free_duration=np.nan,
                started=started,
            )

        try:
            frozen_result, frozen_trajectory = self._calculate(
                current, target_value, self.minimum_duration
            )
        except Exception as error:
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="ruckig_exception",
                native_status=f"ruckig_exception:{type(error).__name__}",
                requested_solver_status=requested_solver_status,
                requested_target_feasible=False,
                target_projected=target_projected,
                requested_free_duration=requested_free_duration,
                started=started,
            )
        if not _result_succeeded(frozen_result):
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="ruckig_solver_failure",
                native_status=f"ruckig_error_{int(frozen_result)}",
                requested_solver_status=requested_solver_status,
                requested_target_feasible=False,
                target_projected=target_projected,
                requested_free_duration=requested_free_duration,
                started=started,
            )

        self.frozen_trajectory = frozen_trajectory
        if float(frozen_trajectory.duration) + 1e-12 < self.dt:
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="ruckig_prefix_shorter_than_control_period",
                native_status=str(frozen_result),
                requested_solver_status=requested_solver_status,
                requested_target_feasible=False,
                target_projected=target_projected,
                requested_free_duration=requested_free_duration,
                started=started,
            )
        try:
            audit = audit_ruckig_prefix(
                frozen_trajectory,
                self.limits,
                start=0.0,
                end=self.dt,
                grid_dt=self.audit_grid_dt,
            )
            profile = audit.pop("command_profile")
            position, velocity, acceleration = frozen_trajectory.at_time(self.dt)
            command = np.column_stack((position, velocity, acceleration))
        except Exception as error:
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason="ruckig_profile_audit_exception",
                native_status=(
                    f"ruckig_profile_audit_exception:{type(error).__name__}"
                ),
                requested_solver_status=requested_solver_status,
                requested_target_feasible=True,
                target_projected=target_projected,
                requested_free_duration=requested_free_duration,
                started=started,
            )
        endpoint_ok = bool(
            profile is not None
            and profile.endpoint_matches_profile
            and np.allclose(command, profile.terminal_state, rtol=0.0, atol=2e-8)
        )
        jerk_audit = np.asarray(
            audit[
                "max_internal_jerk"
                if profile is not None and profile.exact
                else "max_sampled_jerk"
            ],
            dtype=float,
        )
        segment_ok = bool(
            endpoint_ok
            and np.all(np.isfinite(command))
            and np.all(np.asarray(audit["violation_count"], dtype=int) == 0)
            and np.all(np.isfinite(jerk_audit))
        )
        if not segment_ok:
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason=(
                    "ruckig_profile_endpoint_mismatch"
                    if not endpoint_ok
                    else "ruckig_prefix_constraint_audit_failed"
                ),
                native_status=str(frozen_result),
                requested_solver_status=requested_solver_status,
                requested_target_feasible=True,
                target_projected=target_projected,
                requested_free_duration=requested_free_duration,
                started=started,
            )

        assert profile is not None
        command_duration, command_t_free_ok, command_solver_status = (
            self._free_duration(current, command)
        )
        terminal_ok = _all_true(terminal_stopping_viable(command, self.limits))
        next_step_ok = bool(
            terminal_ok and terminal_has_viable_next_step(command, self.dt, self.limits)
        )
        if self.safety_shield and not (terminal_ok and next_step_ok):
            reason = (
                "ruckig_command_terminal_not_viable"
                if not terminal_ok
                else "ruckig_command_no_viable_next_step"
            )
            return self._apply_fallback(
                target_value,
                current,
                control_time=control_time,
                reason=reason,
                native_status=str(frozen_result),
                requested_solver_status=requested_solver_status,
                requested_target_feasible=True,
                target_projected=target_projected,
                requested_free_duration=requested_free_duration,
                started=started,
            )
        # The compatibility field is the first actual Ruckig jerk, never the
        # acceleration-difference/average jerk.  The complete profile is the
        # authoritative executable command.
        if profile.first_jerk is None or profile.last_jerk is None:
            first_jerk = np.full(self.dof, np.nan)
            fallback_last_jerk = np.asarray(audit["sampled_last_jerk"], dtype=float)
        else:
            first_jerk = np.asarray(profile.first_jerk, dtype=float)
            fallback_last_jerk = np.asarray(profile.last_jerk, dtype=float)
        self._commit(command, fallback_last_jerk)
        return FollowerResult(
            command_state=command,
            command_jerk=first_jerk,
            command_time=control_time + self.dt,
            solver_status=(
                f"requested_target_free:{requested_solver_status}"
                f"|native_prefix:{str(frozen_result)}"
                f"|committed_command_free:{command_solver_status}"
            ),
            fallback_reason="",
            target_projected=target_projected,
            requested_target_free_trajectory_duration=requested_free_duration,
            free_trajectory_duration=command_duration,
            frozen_trajectory_duration=float(frozen_trajectory.duration),
            compute_us=(perf_counter_ns() - started) / 1000.0,
            continuous_audit=audit,
            requested_target_feasible=True,
            command_segment_feasible=segment_ok,
            command_terminal_viable=terminal_ok,
            command_next_step_exists=next_step_ok,
            command_t_free_le_dt=command_t_free_ok,
            fallback_requested=False,
            fallback_applied=False,
            safety_guarantee=True,
            emergency_mode=False,
            command_profile=profile,
            native_follower="ordinary_ruckig",
            native_command_executed=True,
            safety_shield_requested=self.safety_shield,
            safety_shield_applied=False,
        )
