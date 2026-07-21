"""Executable target governors for a jerk-limited triple integrator.

The governors in this module never mutate or clip the requested reference in
place.  They return a separately labelled executable state and an explicit
status/fallback record.  States use the shape ``(dof, 3)`` with columns
``position, velocity, acceleration``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from time import perf_counter_ns

import numpy as np


@dataclass(frozen=True)
class MotionLimits:
    """Per-joint velocity, acceleration, and jerk limits."""

    max_velocity: np.ndarray
    max_acceleration: np.ndarray
    max_jerk: np.ndarray

    @classmethod
    def broadcast(
        cls,
        dof: int,
        max_velocity: float | Sequence[float] = 4.1,
        max_acceleration: float | Sequence[float] = 8.2,
        max_jerk: float | Sequence[float] = 4000.0,
    ) -> MotionLimits:
        if dof < 1:
            raise ValueError("dof must be positive")

        def array(value: float | Sequence[float], name: str) -> np.ndarray:
            result = np.broadcast_to(np.asarray(value, dtype=float), (dof,)).copy()
            if not np.all(np.isfinite(result)) or np.any(result <= 0.0):
                raise ValueError(f"{name} must contain {dof} finite positive values")
            return result

        return cls(
            array(max_velocity, "max_velocity"),
            array(max_acceleration, "max_acceleration"),
            array(max_jerk, "max_jerk"),
        )

    @property
    def dof(self) -> int:
        return int(self.max_velocity.size)


@dataclass(frozen=True)
class GovernorResult:
    executable_state: np.ndarray
    jerk: np.ndarray
    target_time: float
    target_feasible: bool
    target_projected: bool
    fallback: bool
    fallback_reason: str
    solver_status: str
    iterations: int
    compute_us: float
    distortion: np.ndarray
    sequence: np.ndarray | None = None


def as_state_matrix(state: np.ndarray | Sequence[float], dof: int) -> np.ndarray:
    """Normalize a state to ``(dof, 3)`` without silently changing values."""

    value = np.asarray(state, dtype=float)
    if value.shape == (3,) and dof == 1:
        value = value.reshape(1, 3)
    if value.shape != (dof, 3):
        raise ValueError(f"state must have shape ({dof}, 3), got {value.shape}")
    return value.copy()


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
    """Return exact velocity min/max and the times examined in ``[0, dt]``."""

    position, velocity, acceleration = np.asarray(state, dtype=float)
    del position
    times = [0.0, float(dt)]
    if jerk != 0.0:
        stationary_time = -acceleration / jerk
        if 0.0 < stationary_time < dt:
            times.append(float(stationary_time))
    values = [velocity + acceleration * t + 0.5 * jerk * t**2 for t in times]
    return float(min(values)), float(max(values)), tuple(times)


def segment_is_feasible(
    state: Sequence[float],
    jerk: float,
    dt: float,
    limits: MotionLimits,
    joint: int = 0,
    *,
    tolerance: float = 1e-10,
) -> bool:
    """Audit exact continuous V/A/J bounds for one constant-jerk segment."""

    state_value = np.asarray(state, dtype=float)
    if state_value.shape != (3,) or not np.all(np.isfinite(state_value)):
        return False
    if not np.isfinite(jerk):
        return False
    vmax = limits.max_velocity[joint]
    amax = limits.max_acceleration[joint]
    jmax = limits.max_jerk[joint]
    next_state = integrate_constant_jerk(state_value, jerk, dt)
    velocity_min, velocity_max, _ = velocity_extrema_constant_jerk(
        state_value, jerk, dt
    )
    acceleration_min = min(state_value[2], next_state[2])
    acceleration_max = max(state_value[2], next_state[2])
    return bool(
        abs(jerk) <= jmax + tolerance
        and acceleration_min >= -amax - tolerance
        and acceleration_max <= amax + tolerance
        and velocity_min >= -vmax - tolerance
        and velocity_max <= vmax + tolerance
    )


def point_is_admissible(
    state: np.ndarray, limits: MotionLimits, *, tolerance: float = 1e-10
) -> bool:
    value = as_state_matrix(state, limits.dof)
    return bool(
        np.all(np.isfinite(value))
        and np.all(np.abs(value[:, 1]) <= limits.max_velocity + tolerance)
        and np.all(np.abs(value[:, 2]) <= limits.max_acceleration + tolerance)
    )


def _intersect_interval(
    interval: tuple[float, float], low: float, high: float
) -> tuple[float, float] | None:
    result = (max(interval[0], low), min(interval[1], high))
    return result if result[0] <= result[1] else None


def feasible_jerk_intervals(
    state: Sequence[float], dt: float, limits: MotionLimits, joint: int = 0
) -> tuple[tuple[float, float], ...]:
    """Exact feasible jerk intervals for continuous V/A/J over one period.

    Endpoint acceleration and velocity constraints are affine in jerk.  A
    possible interior velocity extremum produces at most one excluded interval,
    which is split explicitly here.
    """

    _, velocity, acceleration = np.asarray(state, dtype=float)
    vmax = float(limits.max_velocity[joint])
    amax = float(limits.max_acceleration[joint])
    jmax = float(limits.max_jerk[joint])
    if not np.all(np.isfinite([velocity, acceleration])):
        return ()
    if abs(velocity) > vmax + 1e-12 or abs(acceleration) > amax + 1e-12:
        return ()

    low = -jmax
    high = jmax
    low = max(low, (-amax - acceleration) / dt)
    high = min(high, (amax - acceleration) / dt)
    velocity_factor = 2.0 / dt**2
    low = max(low, velocity_factor * (-vmax - velocity - acceleration * dt))
    high = min(high, velocity_factor * (vmax - velocity - acceleration * dt))
    if low > high:
        return ()
    intervals = [(low, high)]

    # If positive acceleration crosses zero, velocity has an interior maximum.
    if acceleration > 0.0:
        crossing = -acceleration / dt
        margin = vmax - velocity
        if margin <= 0.0:
            allowed_crossing_high = -np.inf
        else:
            allowed_crossing_high = -(acceleration**2) / (2.0 * margin)
        # Crossing jerks are j < crossing.  When the required magnitude is
        # larger, (allowed_crossing_high, crossing) is infeasible.
        if allowed_crossing_high < crossing:
            split = []
            for interval in intervals:
                left = _intersect_interval(interval, -np.inf, allowed_crossing_high)
                right = _intersect_interval(interval, crossing, np.inf)
                if left is not None:
                    split.append(left)
                if right is not None:
                    split.append(right)
            intervals = split

    # Negative acceleration crossing zero gives an interior minimum.
    elif acceleration < 0.0:
        crossing = -acceleration / dt
        margin = velocity + vmax
        if margin <= 0.0:
            allowed_crossing_low = np.inf
        else:
            allowed_crossing_low = (acceleration**2) / (2.0 * margin)
        if allowed_crossing_low > crossing:
            split = []
            for interval in intervals:
                left = _intersect_interval(interval, -np.inf, crossing)
                right = _intersect_interval(interval, allowed_crossing_low, np.inf)
                if left is not None:
                    split.append(left)
                if right is not None:
                    split.append(right)
            intervals = split

    verified = []
    for interval in intervals:
        if interval[1] - interval[0] < 1e-12:
            candidates = [0.5 * (interval[0] + interval[1])]
        else:
            candidates = [
                interval[0],
                0.5 * (interval[0] + interval[1]),
                interval[1],
            ]
        if any(segment_is_feasible(state, j, dt, limits, joint) for j in candidates):
            verified.append((float(interval[0]), float(interval[1])))
    return tuple(verified)


def _clip_to_intervals(value: float, intervals: Iterable[tuple[float, float]]) -> float:
    candidates = [float(np.clip(value, low, high)) for low, high in intervals]
    if not candidates:
        raise ValueError("no feasible jerk interval")
    return min(candidates, key=lambda candidate: abs(candidate - value))


class OneStepBoundedJerkGovernor:
    """Stateful one-step governor with exact constant-jerk reachability."""

    name = "one_step_bounded_jerk"

    def __init__(
        self,
        dof: int,
        dt: float,
        limits: MotionLimits,
        *,
        position_weight: float = 1.0,
        velocity_weight: float = 0.25,
        acceleration_weight: float = 0.05,
        jerk_weight: float = 1e-5,
        delta_jerk_weight: float = 2e-5,
        measured_state_mode: str = "previous_command",
        divergence_threshold: float = 0.05,
    ) -> None:
        if dof != limits.dof:
            raise ValueError("limits dof does not match governor dof")
        if not np.isfinite(dt) or dt <= 0.0:
            raise ValueError("dt must be finite and positive")
        if measured_state_mode not in {"previous_command", "measured", "hybrid"}:
            raise ValueError("invalid measured_state_mode")
        self.dof = dof
        self.dt = float(dt)
        self.limits = limits
        self.weights = np.asarray(
            [position_weight, velocity_weight, acceleration_weight], dtype=float
        )
        if np.any(self.weights < 0.0):
            raise ValueError("tracking weights must be nonnegative")
        self.jerk_weight = float(jerk_weight)
        self.delta_jerk_weight = float(delta_jerk_weight)
        self.measured_state_mode = measured_state_mode
        self.divergence_threshold = float(divergence_threshold)
        self.command_state: np.ndarray | None = None
        self.last_jerk = np.zeros(dof, dtype=float)

    def reset(self, state: np.ndarray | None = None) -> None:
        self.command_state = None if state is None else as_state_matrix(state, self.dof)
        if self.command_state is not None and not point_is_admissible(
            self.command_state, self.limits
        ):
            raise ValueError("reset state is not admissible")
        self.last_jerk = np.zeros(self.dof, dtype=float)

    def _select_current(
        self, measured_state: np.ndarray | None
    ) -> tuple[np.ndarray, str]:
        if self.command_state is None:
            if measured_state is None:
                raise ValueError("first update requires measured/current state")
            measured = as_state_matrix(measured_state, self.dof)
            if not point_is_admissible(measured, self.limits):
                raise ValueError("initial measured/current state is not admissible")
            self.command_state = measured
            return measured.copy(), "initialized_from_measurement"

        if measured_state is None or self.measured_state_mode == "previous_command":
            return self.command_state.copy(), "previous_command"
        measured = as_state_matrix(measured_state, self.dof)
        if not point_is_admissible(measured, self.limits):
            return self.command_state.copy(), "invalid_measurement_ignored"
        if self.measured_state_mode == "measured":
            return measured, "measured_state"
        divergence = np.max(np.abs(measured - self.command_state), axis=1)
        if np.any(divergence > self.divergence_threshold):
            return measured, "hybrid_measurement_reset"
        return self.command_state.copy(), "hybrid_previous_command"

    def _objective_coefficients(
        self, current: np.ndarray, raw_target: np.ndarray, joint: int
    ) -> tuple[float, float]:
        dt = self.dt
        base = integrate_constant_jerk(current, 0.0, dt)
        sensitivity = np.asarray([dt**3 / 6.0, 0.5 * dt**2, dt])
        scales = np.asarray(
            [
                max(self.limits.max_velocity[joint] * dt, 1e-12),
                self.limits.max_velocity[joint],
                self.limits.max_acceleration[joint],
            ]
        )
        weights = self.weights / scales**2
        residual = base - raw_target
        quadratic = float(np.sum(weights * sensitivity**2))
        linear = float(np.sum(weights * sensitivity * residual))
        jerk_scale = self.limits.max_jerk[joint]
        quadratic += (self.jerk_weight + self.delta_jerk_weight) / jerk_scale**2
        linear -= self.delta_jerk_weight * self.last_jerk[joint] / jerk_scale**2
        return quadratic, linear

    def _choose_joint_jerk(
        self, current: np.ndarray, raw_target: np.ndarray, joint: int
    ) -> tuple[float, str]:
        intervals = feasible_jerk_intervals(current, self.dt, self.limits, joint)
        if not intervals:
            raise RuntimeError("no_feasible_one_step_jerk")
        quadratic, linear = self._objective_coefficients(current, raw_target, joint)
        requested = -linear / max(quadratic, 1e-30)
        candidate = _clip_to_intervals(requested, intervals)
        if segment_is_feasible(current, candidate, self.dt, self.limits, joint):
            return candidate, "analytic_optimum"

        # Numerical roundoff at a split boundary: evaluate deterministic
        # interior candidates and preserve an explicit status.
        candidates = []
        for low, high in intervals:
            candidates.extend(
                [
                    low,
                    np.nextafter(low, high),
                    0.5 * (low + high),
                    np.nextafter(high, low),
                    high,
                ]
            )
        feasible = [
            value
            for value in candidates
            if segment_is_feasible(current, value, self.dt, self.limits, joint)
        ]
        if not feasible:
            raise RuntimeError("interval_verification_failed")

        def objective(jerk: float) -> float:
            state = integrate_constant_jerk(current, jerk, self.dt)
            scale = np.asarray(
                [
                    max(self.limits.max_velocity[joint] * self.dt, 1e-12),
                    self.limits.max_velocity[joint],
                    self.limits.max_acceleration[joint],
                ]
            )
            tracking = np.sum(self.weights * ((state - raw_target) / scale) ** 2)
            regularization = (
                self.jerk_weight * (jerk / self.limits.max_jerk[joint]) ** 2
            )
            delta = (
                self.delta_jerk_weight
                * ((jerk - self.last_jerk[joint]) / self.limits.max_jerk[joint]) ** 2
            )
            return float(tracking + regularization + delta)

        return min(feasible, key=objective), "verified_boundary"

    def update(
        self,
        raw_target: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> GovernorResult:
        started = perf_counter_ns()
        target = as_state_matrix(raw_target, self.dof)
        try:
            current, current_status = self._select_current(current_state)
        except ValueError as error:
            compute_us = (perf_counter_ns() - started) / 1000.0
            if self.command_state is None:
                raise
            return GovernorResult(
                self.command_state.copy(),
                np.zeros(self.dof),
                control_time + self.dt,
                False,
                False,
                True,
                str(error),
                "invalid_current_state",
                0,
                compute_us,
                self.command_state - np.nan_to_num(target),
            )

        if not np.all(np.isfinite(target)):
            safe_jerk = np.empty(self.dof)
            for joint in range(self.dof):
                intervals = feasible_jerk_intervals(
                    current[joint], self.dt, self.limits, joint
                )
                if not intervals:
                    safe_jerk[joint] = 0.0
                else:
                    safe_jerk[joint] = _clip_to_intervals(
                        -current[joint, 2] / self.dt, intervals
                    )
            executable = integrate_constant_jerk(current, safe_jerk, self.dt)
            self.command_state = executable
            self.last_jerk = safe_jerk
            compute_us = (perf_counter_ns() - started) / 1000.0
            return GovernorResult(
                executable.copy(),
                safe_jerk.copy(),
                control_time + self.dt,
                False,
                False,
                True,
                "nonfinite_raw_target",
                f"fallback_safe_brake:{current_status}",
                0,
                compute_us,
                np.full_like(target, np.nan),
            )

        jerk = np.empty(self.dof, dtype=float)
        statuses = []
        fallback = False
        fallback_reason = ""
        for joint in range(self.dof):
            try:
                jerk[joint], status = self._choose_joint_jerk(
                    current[joint], target[joint], joint
                )
                statuses.append(status)
            except RuntimeError as error:
                fallback = True
                fallback_reason = str(error)
                intervals = feasible_jerk_intervals(
                    current[joint], self.dt, self.limits, joint
                )
                jerk[joint] = (
                    _clip_to_intervals(-current[joint, 2] / self.dt, intervals)
                    if intervals
                    else 0.0
                )
                statuses.append("safe_brake")
        executable = integrate_constant_jerk(current, jerk, self.dt)
        feasible = point_is_admissible(executable, self.limits) and all(
            segment_is_feasible(current[j], jerk[j], self.dt, self.limits, j)
            for j in range(self.dof)
        )
        if not feasible:
            fallback = True
            fallback_reason = (
                fallback_reason or "postcheck_failed_hold_previous_command"
            )
            executable = self.command_state.copy()
            jerk = np.zeros(self.dof)
        self.command_state = executable.copy()
        self.last_jerk = jerk.copy()
        compute_us = (perf_counter_ns() - started) / 1000.0
        return GovernorResult(
            executable,
            jerk,
            float(control_time + self.dt),
            bool(feasible),
            False,
            fallback,
            fallback_reason,
            f"one_step:{current_status}:{'+'.join(statuses)}",
            0,
            compute_us,
            executable - target,
            executable[None, :, :],
        )


class JerkQPGovernor:
    """Short-horizon deterministic jerk QP/MPC with one-step execution."""

    name = "jerk_qp_mpc"

    def __init__(
        self,
        dof: int,
        dt: float,
        limits: MotionLimits,
        horizon_steps: int = 20,
        *,
        position_weight: float = 1.0,
        velocity_weight: float = 0.2,
        acceleration_weight: float = 0.03,
        jerk_weight: float = 2e-5,
        delta_jerk_weight: float = 5e-5,
        time_limit_s: float = 0.020,
        max_iter: int = 4000,
        fallback_governor: OneStepBoundedJerkGovernor | None = None,
    ) -> None:
        if horizon_steps < 1:
            raise ValueError("horizon_steps must be positive")
        if dof != limits.dof:
            raise ValueError("limits dof does not match governor dof")
        self.dof = dof
        self.dt = float(dt)
        self.limits = limits
        self.horizon_steps = int(horizon_steps)
        self.weights = (position_weight, velocity_weight, acceleration_weight)
        self.jerk_weight = float(jerk_weight)
        self.delta_jerk_weight = float(delta_jerk_weight)
        self.time_limit_s = float(time_limit_s)
        self.max_iter = int(max_iter)
        self.command_state: np.ndarray | None = None
        self.last_jerk = np.zeros(dof)
        self.fallback_governor = fallback_governor or OneStepBoundedJerkGovernor(
            dof, dt, limits
        )
        self._solver = None
        self._maps = self._build_maps()

    def reset(self, state: np.ndarray | None = None) -> None:
        self.command_state = None if state is None else as_state_matrix(state, self.dof)
        self.last_jerk = np.zeros(self.dof)
        self.fallback_governor.reset(state)
        self._solver = None

    def _build_maps(self):
        n = self.horizon_steps
        dt = self.dt
        transition = np.asarray(
            [[1.0, dt, 0.5 * dt**2], [0.0, 1.0, dt], [0.0, 0.0, 1.0]]
        )
        control = np.asarray([dt**3 / 6.0, 0.5 * dt**2, dt])
        base = np.empty((n, 3, 3))
        maps = np.zeros((3, n, n))
        for step in range(n):
            power = np.linalg.matrix_power(transition, step + 1)
            base[step] = power
            for source in range(step + 1):
                coefficient = (
                    np.linalg.matrix_power(transition, step - source) @ control
                )
                maps[:, step, source] = coefficient
        return base, maps

    def _fallback(
        self,
        target: np.ndarray,
        current: np.ndarray,
        control_time: float,
        reason: str,
        started: int,
        status: str,
        iterations: int,
    ) -> GovernorResult:
        self.fallback_governor.command_state = current.copy()
        result = self.fallback_governor.update(
            target, control_time=control_time, current_state=current
        )
        self.command_state = result.executable_state.copy()
        self.last_jerk = result.jerk.copy()
        return GovernorResult(
            result.executable_state,
            result.jerk,
            result.target_time,
            result.target_feasible,
            False,
            True,
            reason,
            f"{status}->one_step",
            iterations,
            (perf_counter_ns() - started) / 1000.0,
            result.distortion,
            result.sequence,
        )

    def update(
        self,
        reference_sequence: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> GovernorResult:
        started = perf_counter_ns()
        sequence = np.asarray(reference_sequence, dtype=float)
        if sequence.shape == (self.dof, 3):
            sequence = np.repeat(sequence[None, :, :], self.horizon_steps, axis=0)
        if sequence.ndim != 3 or sequence.shape[1:] != (self.dof, 3):
            raise ValueError(
                "reference_sequence must have shape (steps, dof, 3) or (dof, 3)"
            )
        if sequence.shape[0] < self.horizon_steps:
            sequence = np.concatenate(
                (
                    sequence,
                    np.repeat(
                        sequence[-1:, :, :],
                        self.horizon_steps - sequence.shape[0],
                        axis=0,
                    ),
                ),
                axis=0,
            )
        sequence = sequence[: self.horizon_steps]
        if current_state is not None:
            current = as_state_matrix(current_state, self.dof)
        elif self.command_state is not None:
            current = self.command_state.copy()
        else:
            raise ValueError("first update requires current_state")
        if not point_is_admissible(current, self.limits):
            if self.command_state is None:
                raise ValueError("current_state is not admissible")
            current = self.command_state.copy()
        if not np.all(np.isfinite(sequence)):
            return self._fallback(
                np.nan_to_num(sequence[0], nan=current[:, 0:1]),
                current,
                control_time,
                "nonfinite_reference_sequence",
                started,
                "invalid_input",
                0,
            )

        try:
            import osqp
            from scipy import sparse
        except ImportError:
            return self._fallback(
                sequence[0],
                current,
                control_time,
                "osqp_unavailable",
                started,
                "unavailable",
                0,
            )

        base_powers, state_maps = self._maps
        n = self.horizon_steps
        p_blocks = []
        q_blocks = []
        constraint_blocks = []
        lower_blocks = []
        upper_blocks = []
        predicted_bases = np.empty((self.dof, n, 3))
        difference = np.eye(n) - np.eye(n, k=-1)
        for joint in range(self.dof):
            base = np.einsum("nij,j->ni", base_powers, current[joint])
            predicted_bases[joint] = base
            hessian = self.jerk_weight * np.eye(n) + self.delta_jerk_weight * (
                difference.T @ difference
            )
            gradient = np.zeros(n)
            scales = (
                max(self.limits.max_velocity[joint] * self.dt, 1e-12),
                self.limits.max_velocity[joint],
                self.limits.max_acceleration[joint],
            )
            for component, weight in enumerate(self.weights):
                mapping = state_maps[component]
                normalized_weight = weight / scales[component] ** 2
                residual = base[:, component] - sequence[:, joint, component]
                hessian += normalized_weight * (mapping.T @ mapping)
                gradient += normalized_weight * (mapping.T @ residual)
            # First delta-jerk row is j0-last_jerk, not j0-0.
            gradient[0] -= self.delta_jerk_weight * self.last_jerk[joint]
            p_blocks.append(sparse.csc_matrix(2.0 * hessian))
            q_blocks.append(2.0 * gradient)

            identity = np.eye(n)
            velocity_map = state_maps[1]
            acceleration_map = state_maps[2]
            constraint_blocks.append(
                sparse.csc_matrix(np.vstack((identity, velocity_map, acceleration_map)))
            )
            lower_blocks.append(
                np.concatenate(
                    (
                        np.full(n, -self.limits.max_jerk[joint]),
                        -self.limits.max_velocity[joint] - base[:, 1],
                        -self.limits.max_acceleration[joint] - base[:, 2],
                    )
                )
            )
            upper_blocks.append(
                np.concatenate(
                    (
                        np.full(n, self.limits.max_jerk[joint]),
                        self.limits.max_velocity[joint] - base[:, 1],
                        self.limits.max_acceleration[joint] - base[:, 2],
                    )
                )
            )

        hessian = sparse.block_diag(p_blocks, format="csc")
        constraints = sparse.block_diag(constraint_blocks, format="csc")
        gradient = np.concatenate(q_blocks)
        lower = np.concatenate(lower_blocks)
        upper = np.concatenate(upper_blocks)
        try:
            if self._solver is None:
                self._solver = osqp.OSQP()
                self._solver.setup(
                    P=hessian,
                    q=gradient,
                    A=constraints,
                    l=lower,
                    u=upper,
                    verbose=False,
                    polishing=False,
                    warm_starting=True,
                    adaptive_rho=False,
                    rho=0.1,
                    eps_abs=1e-5,
                    eps_rel=1e-5,
                    max_iter=self.max_iter,
                    time_limit=self.time_limit_s,
                    check_termination=25,
                )
            else:
                self._solver.update(q=gradient, l=lower, u=upper)
            solution = self._solver.solve()
        except Exception as error:  # OSQP exposes backend-specific errors.
            return self._fallback(
                sequence[0],
                current,
                control_time,
                f"solver_exception:{type(error).__name__}",
                started,
                "exception",
                0,
            )
        status = str(solution.info.status).lower().replace(" ", "_")
        iterations = int(solution.info.iter)
        if solution.x is None or status not in {"solved", "solved_inaccurate"}:
            reason = (
                "qp_timeout"
                if "time" in status or "maximum" in status
                else "qp_infeasible_or_failed"
            )
            return self._fallback(
                sequence[0], current, control_time, reason, started, status, iterations
            )
        jerk_plan = np.asarray(solution.x).reshape(self.dof, n).T
        state_plan = np.empty((n, self.dof, 3))
        for joint in range(self.dof):
            for component in range(3):
                state_plan[:, joint, component] = (
                    predicted_bases[joint, :, component]
                    + state_maps[component] @ jerk_plan[:, joint]
                )
        executable = state_plan[0]
        first_jerk = jerk_plan[0]
        continuous_ok = all(
            segment_is_feasible(current[j], first_jerk[j], self.dt, self.limits, j)
            for j in range(self.dof)
        )
        if not continuous_ok or not point_is_admissible(executable, self.limits):
            return self._fallback(
                sequence[0],
                current,
                control_time,
                "qp_continuous_postcheck_failed",
                started,
                status,
                iterations,
            )
        self.command_state = executable.copy()
        self.last_jerk = first_jerk.copy()
        self.fallback_governor.command_state = executable.copy()
        self.fallback_governor.last_jerk = first_jerk.copy()
        return GovernorResult(
            executable,
            first_jerk,
            float(control_time + self.dt),
            True,
            False,
            False,
            "",
            status,
            iterations,
            (perf_counter_ns() - started) / 1000.0,
            executable - sequence[0],
            state_plan,
        )
