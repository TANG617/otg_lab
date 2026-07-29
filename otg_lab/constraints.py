"""Exact safety constraints for the jerk-limited triple integrator.

The formal algorithms in this module do not sample jerk or time on a grid.
They use the affine endpoint constraints, the sole possible interior velocity
extremum of a constant-jerk segment, and analytic roots of the terminal
stopping-envelope quadratics.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class MotionLimitsLike(Protocol):
    """Structural type used to avoid coupling constraints to a governor."""

    max_velocity: np.ndarray
    max_acceleration: np.ndarray
    max_jerk: np.ndarray

    @property
    def dof(self) -> int: ...


class InvariantViolationError(RuntimeError):
    """A state claimed to be safe has no verified invariant-preserving action."""


Interval = tuple[float, float]
Intervals = tuple[Interval, ...]


def _state_matrix(
    state: np.ndarray | Sequence[float], limits: MotionLimitsLike
) -> np.ndarray | None:
    value = np.asarray(state, dtype=float)
    if value.shape == (3,) and limits.dof == 1:
        value = value.reshape(1, 3)
    if value.shape != (limits.dof, 3):
        return None
    return value


def _scalar_state(state: np.ndarray | Sequence[float]) -> np.ndarray | None:
    value = np.asarray(state, dtype=float)
    if value.shape != (3,):
        return None
    return value


def integrate_constant_jerk(
    state: np.ndarray | Sequence[float],
    jerk: np.ndarray | Sequence[float] | float,
    dt: float,
) -> np.ndarray:
    """Integrate the exact constant-jerk dynamics over one interval."""

    if not np.isfinite(dt) or dt <= 0.0:
        raise ValueError("dt must be finite and positive")
    value = np.asarray(state, dtype=float)
    scalar_state = value.shape == (3,)
    if scalar_state:
        value = value.reshape(1, 3)
    if value.ndim != 2 or value.shape[1] != 3:
        raise ValueError("state must have shape (3,) or (dof, 3)")
    jerk_value = np.broadcast_to(np.asarray(jerk, dtype=float), (value.shape[0],))
    position, velocity, acceleration = value.T
    result = np.column_stack(
        (
            position
            + velocity * dt
            + 0.5 * acceleration * dt**2
            + jerk_value * dt**3 / 6.0,
            velocity + acceleration * dt + 0.5 * jerk_value * dt**2,
            acceleration + jerk_value * dt,
        )
    )
    return result[0] if scalar_state else result


def velocity_extrema_constant_jerk(
    state: Sequence[float], jerk: float, dt: float
) -> tuple[float, float, tuple[float, ...]]:
    """Return the exact velocity extrema and their candidate times."""

    value = _scalar_state(state)
    if value is None or not np.all(np.isfinite(value)):
        return np.nan, np.nan, ()
    if not np.isfinite(jerk) or not np.isfinite(dt) or dt <= 0.0:
        return np.nan, np.nan, ()
    _, velocity, acceleration = value
    times = [0.0, float(dt)]
    if (
        jerk != 0.0
        and np.signbit(acceleration) != np.signbit(jerk)
        and abs(acceleration) < abs(jerk) * dt
    ):
        stationary_time = -acceleration / jerk
        times.append(float(stationary_time))
    values = [velocity + acceleration * time + 0.5 * jerk * time**2 for time in times]
    return float(min(values)), float(max(values)), tuple(times)


def point_within_va_limits(
    state: np.ndarray | Sequence[float],
    limits: MotionLimitsLike,
    *,
    tolerance: float = 1e-10,
) -> bool:
    """Return whether every finite state is within point velocity/accel limits."""

    value = _state_matrix(state, limits)
    return bool(
        value is not None
        and np.all(np.isfinite(value))
        and np.all(np.abs(value[:, 1]) <= limits.max_velocity + tolerance)
        and np.all(np.abs(value[:, 2]) <= limits.max_acceleration + tolerance)
    )


def terminal_stopping_viable(
    state: np.ndarray | Sequence[float],
    limits: MotionLimitsLike,
    *,
    tolerance: float = 1e-10,
) -> bool:
    """Audit the direction-dependent jerk-limited stopping envelope.

    For positive acceleration only the upper velocity boundary is approached;
    for negative acceleration only the lower boundary is approached.  Using
    ``abs(velocity)`` here would incorrectly reject safe opposite-sign states.
    """

    value = _state_matrix(state, limits)
    if value is None or not point_within_va_limits(value, limits, tolerance=tolerance):
        return False
    velocity = value[:, 1]
    acceleration = value[:, 2]
    positive = acceleration > 0.0
    negative = acceleration < 0.0
    upper_stop = velocity + acceleration**2 / (2.0 * limits.max_jerk)
    lower_stop = velocity - acceleration**2 / (2.0 * limits.max_jerk)
    return bool(
        np.all(~positive | (upper_stop <= limits.max_velocity + tolerance))
        and np.all(~negative | (lower_stop >= -limits.max_velocity - tolerance))
    )


def ruckig_target_admissible(
    state: np.ndarray | Sequence[float],
    limits: MotionLimitsLike,
    *,
    tolerance: float = 1e-10,
) -> bool:
    """Return the exact reverse-time target condition used by Ruckig.

    Ruckig validates a target by asking whether its acceleration can be
    brought to zero while integrating backward from the target state. This is
    the sign-reversed counterpart of forward terminal stopping viability and
    must not be aliased to :func:`terminal_stopping_viable`.
    """

    value = _state_matrix(state, limits)
    if value is None or not point_within_va_limits(
        value,
        limits,
        tolerance=tolerance,
    ):
        return False
    velocity = value[:, 1]
    acceleration = value[:, 2]
    positive = acceleration > 0.0
    negative = acceleration < 0.0
    reverse_lower = velocity - acceleration**2 / (2.0 * limits.max_jerk)
    reverse_upper = velocity + acceleration**2 / (2.0 * limits.max_jerk)
    return bool(
        np.all(~positive | (reverse_lower >= -limits.max_velocity - tolerance))
        and np.all(~negative | (reverse_upper <= limits.max_velocity + tolerance))
    )


def segment_constant_jerk_feasible(
    current: np.ndarray | Sequence[float],
    jerk: np.ndarray | Sequence[float] | float,
    dt: float,
    limits: MotionLimitsLike,
    *,
    tolerance: float = 1e-10,
) -> bool:
    """Audit continuous V/A/J constraints for all joints exactly."""

    value = _state_matrix(current, limits)
    if value is None or not np.all(np.isfinite(value)):
        return False
    try:
        jerk_value = np.broadcast_to(np.asarray(jerk, dtype=float), (limits.dof,))
    except ValueError:
        return False
    if not np.all(np.isfinite(jerk_value)) or not np.isfinite(dt) or dt <= 0.0:
        return False
    terminal = integrate_constant_jerk(value, jerk_value, dt)
    for joint in range(limits.dof):
        velocity_min, velocity_max, _ = velocity_extrema_constant_jerk(
            value[joint], float(jerk_value[joint]), dt
        )
        acceleration_min = min(value[joint, 2], terminal[joint, 2])
        acceleration_max = max(value[joint, 2], terminal[joint, 2])
        if (
            abs(jerk_value[joint]) > limits.max_jerk[joint] + tolerance
            or acceleration_min < -limits.max_acceleration[joint] - tolerance
            or acceleration_max > limits.max_acceleration[joint] + tolerance
            or velocity_min < -limits.max_velocity[joint] - tolerance
            or velocity_max > limits.max_velocity[joint] + tolerance
        ):
            return False
    return True


def _intersect(intervals: Intervals, low: float, high: float) -> Intervals:
    if low > high:
        return ()
    result = []
    for interval_low, interval_high in intervals:
        clipped_low = max(interval_low, low)
        clipped_high = min(interval_high, high)
        if clipped_low <= clipped_high:
            result.append((float(clipped_low), float(clipped_high)))
    return tuple(result)


def _union(*groups: Intervals) -> Intervals:
    ordered = sorted(interval for group in groups for interval in group)
    if not ordered:
        return ()
    merged = [ordered[0]]
    for low, high in ordered[1:]:
        previous_low, previous_high = merged[-1]
        if low <= previous_high:
            merged[-1] = (previous_low, max(previous_high, high))
        else:
            merged.append((low, high))
    return tuple((float(low), float(high)) for low, high in merged)


def _quadratic_leq(
    intervals: Intervals, quadratic: float, linear: float, constant: float
) -> Intervals:
    """Intersect intervals with ``quadratic*x**2 + linear*x + constant <= 0``."""

    coefficient_scale = max(abs(quadratic), abs(linear), abs(constant), 1.0)
    epsilon = 32.0 * np.finfo(float).eps * coefficient_scale
    if abs(quadratic) <= epsilon:
        if abs(linear) <= epsilon:
            return intervals if constant <= epsilon else ()
        root = -constant / linear
        return (
            _intersect(intervals, -np.inf, root)
            if linear > 0.0
            else _intersect(intervals, root, np.inf)
        )

    discriminant = linear * linear - 4.0 * quadratic * constant
    discriminant_tolerance = (
        64.0
        * np.finfo(float).eps
        * max(linear * linear, abs(4.0 * quadratic * constant), 1.0)
    )
    if discriminant < -discriminant_tolerance:
        return intervals if quadratic < 0.0 else ()
    discriminant = max(0.0, discriminant)
    square_root = float(np.sqrt(discriminant))
    # The q-form avoids cancellation when one root is much smaller than the other.
    q_value = -0.5 * (linear + np.copysign(square_root, linear))
    if q_value == 0.0:
        roots = (-linear / (2.0 * quadratic),) * 2
    else:
        roots = (q_value / quadratic, constant / q_value)
    low_root, high_root = sorted(roots)
    if quadratic > 0.0:
        return _intersect(intervals, low_root, high_root)
    return _union(
        _intersect(intervals, -np.inf, low_root),
        _intersect(intervals, high_root, np.inf),
    )


def segment_feasible_jerk_intervals(
    current: Sequence[float],
    dt: float,
    limits: MotionLimitsLike,
    joint: int = 0,
) -> Intervals:
    """Return exact jerk intervals satisfying continuous V/A/J bounds."""

    value = _scalar_state(current)
    if (
        value is None
        or not np.all(np.isfinite(value))
        or not np.isfinite(dt)
        or dt <= 0.0
        or not 0 <= joint < limits.dof
    ):
        return ()
    _, velocity, acceleration = value
    vmax = float(limits.max_velocity[joint])
    amax = float(limits.max_acceleration[joint])
    jmax = float(limits.max_jerk[joint])
    if abs(velocity) > vmax or abs(acceleration) > amax:
        return ()

    intervals: Intervals = ((-jmax, jmax),)
    intervals = _intersect(
        intervals,
        (-amax - acceleration) / dt,
        (amax - acceleration) / dt,
    )
    intervals = _intersect(
        intervals,
        2.0 * (-vmax - velocity - acceleration * dt) / dt**2,
        2.0 * (vmax - velocity - acceleration * dt) / dt**2,
    )
    if not intervals:
        return ()

    # v(t) has at most one interior extremum, at t=-a/j.  The following
    # rational inequalities are rearranged only after fixing the sign of j.
    crossing = -acceleration / dt
    if acceleration > 0.0:
        margin = vmax - velocity
        crossing_part = _intersect(intervals, -np.inf, crossing)
        noncrossing_part = _intersect(intervals, crossing, np.inf)
        if margin <= 0.0:
            crossing_part = ()
        else:
            threshold = -(acceleration**2) / (2.0 * margin)
            crossing_part = _intersect(crossing_part, -np.inf, threshold)
        intervals = _union(crossing_part, noncrossing_part)
    elif acceleration < 0.0:
        margin = velocity + vmax
        noncrossing_part = _intersect(intervals, -np.inf, crossing)
        crossing_part = _intersect(intervals, crossing, np.inf)
        if margin <= 0.0:
            crossing_part = ()
        else:
            threshold = acceleration**2 / (2.0 * margin)
            crossing_part = _intersect(crossing_part, threshold, np.inf)
        intervals = _union(noncrossing_part, crossing_part)
    return intervals


def viable_jerk_intervals(
    current: Sequence[float],
    dt: float,
    limits: MotionLimitsLike,
    joint: int = 0,
) -> Intervals:
    """Return analytic jerks whose segment ends in the stopping viability set.

    The terminal acceleration sign splits the envelope into two quadratic
    inequalities.  No jerk grid or sampled feasibility oracle is used.
    """

    value = _scalar_state(current)
    if value is None or not np.all(np.isfinite(value)) or not 0 <= joint < limits.dof:
        return ()
    one_joint = _SingleJointLimits(limits, joint)
    if not terminal_stopping_viable(value, one_joint, tolerance=0.0):
        return ()
    intervals = segment_feasible_jerk_intervals(value, dt, limits, joint)
    if not intervals:
        return ()

    _, velocity, acceleration = value
    vmax = float(limits.max_velocity[joint])
    amax = float(limits.max_acceleration[joint])
    jmax = float(limits.max_jerk[joint])
    sign_boundary = -acceleration / dt

    # a1 >= 0: v1 + a1^2/(2*jmax) <= vmax, plus v1 >= -vmax.
    positive = _intersect(intervals, sign_boundary, (amax - acceleration) / dt)
    positive = _intersect(
        positive,
        2.0 * (-vmax - velocity - acceleration * dt) / dt**2,
        np.inf,
    )
    positive = _quadratic_leq(
        positive,
        dt**2 / (2.0 * jmax),
        0.5 * dt**2 + acceleration * dt / jmax,
        velocity + acceleration * dt + acceleration**2 / (2.0 * jmax) - vmax,
    )

    # a1 <= 0: v1 - a1^2/(2*jmax) >= -vmax, plus v1 <= vmax.
    negative = _intersect(intervals, (-amax - acceleration) / dt, sign_boundary)
    negative = _intersect(
        negative,
        -np.inf,
        2.0 * (vmax - velocity - acceleration * dt) / dt**2,
    )
    negative = _quadratic_leq(
        negative,
        dt**2 / (2.0 * jmax),
        -0.5 * dt**2 + acceleration * dt / jmax,
        -velocity - acceleration * dt + acceleration**2 / (2.0 * jmax) - vmax,
    )
    return _union(negative, positive)


class _SingleJointLimits:
    """A zero-copy one-joint view used by scalar interval functions."""

    def __init__(self, limits: MotionLimitsLike, joint: int) -> None:
        self.max_velocity = np.asarray([limits.max_velocity[joint]], dtype=float)
        self.max_acceleration = np.asarray(
            [limits.max_acceleration[joint]], dtype=float
        )
        self.max_jerk = np.asarray([limits.max_jerk[joint]], dtype=float)

    @property
    def dof(self) -> int:
        return 1


def terminal_has_viable_next_step(
    state: np.ndarray | Sequence[float],
    dt: float,
    limits: MotionLimitsLike,
) -> bool:
    """Return whether each terminal joint has a subsequent viable action."""

    value = _state_matrix(state, limits)
    return bool(
        value is not None
        and all(
            viable_jerk_intervals(value[j], dt, limits, j) for j in range(limits.dof)
        )
    )
