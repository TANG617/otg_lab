"""Executable target governors for a jerk-limited triple integrator.

The governors in this module never mutate or clip the requested reference in
place.  They return a separately labelled executable state and an explicit
status/fallback record. States use the shape ``(1, 3)`` with columns
``position, velocity, acceleration``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from time import perf_counter_ns

import numpy as np

from .constraints import (
    InvariantViolationError,
    integrate_constant_jerk,
    point_within_va_limits,
    segment_constant_jerk_feasible,
    segment_feasible_jerk_intervals,
    terminal_has_viable_next_step,
    terminal_stopping_viable,
    velocity_extrema_constant_jerk,
    viable_jerk_intervals,
)

__all__ = [
    "GovernorResult",
    "InvariantViolationError",
    "JerkQPGovernor",
    "MotionLimits",
    "OneStepBoundedJerkGovernor",
    "as_state_matrix",
    "feasible_jerk_intervals",
    "integrate_constant_jerk",
    "point_is_admissible",
    "segment_is_feasible",
    "viable_jerk_intervals",
    "velocity_extrema_constant_jerk",
]


@dataclass(frozen=True)
class MotionLimits:
    """Single-axis velocity, acceleration, and jerk limits."""

    max_velocity: np.ndarray
    max_acceleration: np.ndarray
    max_jerk: np.ndarray

    def __post_init__(self) -> None:
        for name in ("max_velocity", "max_acceleration", "max_jerk"):
            value = np.asarray(getattr(self, name), dtype=float)
            if value.shape != (1,):
                raise ValueError(f"{name} must contain exactly one axis")
            if not np.all(np.isfinite(value)) or np.any(value <= 0.0):
                raise ValueError(f"{name} must contain one finite positive value")
            owned = np.array(value, copy=True)
            owned.setflags(write=False)
            object.__setattr__(self, name, owned)

    @classmethod
    def broadcast(
        cls,
        dof: int,
        max_velocity: float | Sequence[float] = 4.1,
        max_acceleration: float | Sequence[float] = 8.2,
        max_jerk: float | Sequence[float] = 4000.0,
    ) -> MotionLimits:
        if dof != 1:
            raise ValueError("only one axis is supported")

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
    requested_target_feasible: bool = False
    command_segment_feasible: bool = False
    command_terminal_viable: bool = False
    command_next_step_exists: bool = False
    safety_guarantee: bool = False
    emergency_mode: bool = False
    fallback_requested: bool = False
    fallback_applied: bool = False
    qp_status_category: str = ""
    qp_solve_time_us: float = np.nan
    qp_primal_residual: float = np.nan
    qp_dual_residual: float = np.nan
    qp_hessian_condition_number: float = np.nan
    qp_constraint_condition_number: float = np.nan


def as_state_matrix(state: np.ndarray | Sequence[float], dof: int) -> np.ndarray:
    """Normalize a scalar state to ``(1, 3)``."""

    if dof != 1:
        raise ValueError("only one axis is supported")
    value = np.asarray(state, dtype=float)
    if value.shape == (3,):
        value = value.reshape(1, 3)
    if value.shape != (1, 3):
        raise ValueError(f"state must have shape (1, 3), got {value.shape}")
    return value.copy()


def segment_is_feasible(
    state: Sequence[float],
    jerk: float,
    dt: float,
    limits: MotionLimits,
    joint: int = 0,
    *,
    tolerance: float = 1e-10,
) -> bool:
    """Compatibility scalar wrapper for the shared continuous audit."""

    one_joint_limits = MotionLimits.broadcast(
        1,
        limits.max_velocity[joint],
        limits.max_acceleration[joint],
        limits.max_jerk[joint],
    )
    return segment_constant_jerk_feasible(
        np.asarray(state, dtype=float),
        jerk,
        dt,
        one_joint_limits,
        tolerance=tolerance,
    )


def point_is_admissible(
    state: np.ndarray, limits: MotionLimits, *, tolerance: float = 1e-10
) -> bool:
    """Compatibility alias for point V/A admissibility."""

    return point_within_va_limits(state, limits, tolerance=tolerance)


def feasible_jerk_intervals(
    state: Sequence[float], dt: float, limits: MotionLimits, joint: int = 0
) -> tuple[tuple[float, float], ...]:
    """Compatibility name for continuous-segment feasible intervals."""

    return segment_feasible_jerk_intervals(state, dt, limits, joint)


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
        if self.command_state is not None and not np.all(
            np.isfinite(self.command_state)
        ):
            raise ValueError("reset state must be finite")
        self.last_jerk = np.zeros(self.dof, dtype=float)

    def _select_current(
        self, measured_state: np.ndarray | None
    ) -> tuple[np.ndarray, str]:
        if self.command_state is None:
            if measured_state is None:
                raise ValueError("first update requires measured/current state")
            measured = as_state_matrix(measured_state, self.dof)
            if not np.all(np.isfinite(measured)):
                raise InvariantViolationError(
                    "initial measured/current state is nonfinite and cannot be integrated"
                )
            self.command_state = measured
            return measured.copy(), "initialized_from_measurement"

        if measured_state is None or self.measured_state_mode == "previous_command":
            return self.command_state.copy(), "previous_command"
        measured = as_state_matrix(measured_state, self.dof)
        if not np.all(np.isfinite(measured)):
            return self.command_state.copy(), "nonfinite_measurement_ignored"
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
        intervals = viable_jerk_intervals(current, self.dt, self.limits, joint)
        if not intervals:
            raise InvariantViolationError("no_viable_one_step_jerk")
        quadratic, linear = self._objective_coefficients(current, raw_target, joint)
        requested = -linear / max(quadratic, 1e-30)
        candidate = _clip_to_intervals(requested, intervals)
        if self._joint_action_is_invariant(current, candidate, joint):
            return candidate, "analytic_optimum"

        # Analytic interval boundaries plus the zero-acceleration action are
        # deterministic recovery candidates, not a feasibility grid.
        candidates = [_clip_to_intervals(-current[2] / self.dt, intervals)]
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
            if self._joint_action_is_invariant(current, value, joint)
        ]
        if not feasible:
            raise InvariantViolationError("next_step_existence_verification_failed")

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

    def _joint_action_is_invariant(
        self, current: np.ndarray, jerk: float, joint: int
    ) -> bool:
        terminal = integrate_constant_jerk(current, jerk, self.dt)
        one_joint_limits = MotionLimits.broadcast(
            1,
            self.limits.max_velocity[joint],
            self.limits.max_acceleration[joint],
            self.limits.max_jerk[joint],
        )
        return bool(
            segment_is_feasible(current, jerk, self.dt, self.limits, joint)
            and terminal_stopping_viable(terminal, one_joint_limits)
            and viable_jerk_intervals(terminal, self.dt, one_joint_limits, 0)
        )

    def _safe_joint_jerk(self, current: np.ndarray, joint: int) -> float:
        intervals = viable_jerk_intervals(current, self.dt, self.limits, joint)
        if not intervals:
            raise InvariantViolationError("safe controller has no viable jerk")
        desired = -current[2] / self.dt
        candidates = [_clip_to_intervals(desired, intervals)]
        for low, high in intervals:
            candidates.extend(
                (
                    np.nextafter(low, high),
                    0.5 * (low + high),
                    np.nextafter(high, low),
                )
            )
        verified = [
            jerk
            for jerk in candidates
            if self._joint_action_is_invariant(current, jerk, joint)
        ]
        if not verified:
            raise InvariantViolationError(
                "safe controller could not preserve next-step existence"
            )
        return min(
            verified,
            key=lambda jerk: (
                abs(current[2] + jerk * self.dt),
                abs(integrate_constant_jerk(current, jerk, self.dt)[1]),
                abs(jerk),
            ),
        )

    def _current_is_viable(self, current: np.ndarray) -> bool:
        return bool(
            terminal_stopping_viable(current, self.limits)
            and all(
                viable_jerk_intervals(current[joint], self.dt, self.limits, joint)
                for joint in range(self.dof)
            )
        )

    def _emergency_jerk(self, current: np.ndarray) -> np.ndarray:
        """Return finite bounded best-effort recovery for an unsafe current state."""

        jerk = np.empty(self.dof, dtype=float)
        for joint in range(self.dof):
            _, velocity, acceleration = current[joint]
            vmax = self.limits.max_velocity[joint]
            amax = self.limits.max_acceleration[joint]
            jmax = self.limits.max_jerk[joint]
            upper_risk = velocity > vmax or (
                acceleration > 0.0 and velocity + acceleration**2 / (2.0 * jmax) > vmax
            )
            lower_risk = velocity < -vmax or (
                acceleration < 0.0 and velocity - acceleration**2 / (2.0 * jmax) < -vmax
            )
            if acceleration > amax or upper_risk:
                requested = -jmax
            elif acceleration < -amax or lower_risk:
                requested = jmax
            else:
                requested = -acceleration / self.dt
            jerk[joint] = float(np.clip(requested, -jmax, jmax))
        return jerk

    def _result(
        self,
        *,
        current: np.ndarray,
        executable: np.ndarray,
        jerk: np.ndarray,
        target: np.ndarray,
        control_time: float,
        started: int,
        requested_target_feasible: bool,
        target_projected: bool,
        fallback: bool,
        fallback_reason: str,
        solver_status: str,
        emergency_mode: bool,
    ) -> GovernorResult:
        segment_ok = segment_constant_jerk_feasible(current, jerk, self.dt, self.limits)
        terminal_ok = terminal_stopping_viable(executable, self.limits)
        next_ok = terminal_has_viable_next_step(executable, self.dt, self.limits)
        safety_guarantee = bool(
            not emergency_mode and segment_ok and terminal_ok and next_ok
        )
        self.command_state = executable.copy()
        self.last_jerk = jerk.copy()
        distortion = (
            executable - target
            if np.all(np.isfinite(target))
            else np.full_like(target, np.nan)
        )
        return GovernorResult(
            executable_state=executable.copy(),
            jerk=jerk.copy(),
            target_time=float(control_time + self.dt),
            target_feasible=safety_guarantee,
            target_projected=target_projected,
            fallback=fallback,
            fallback_reason=fallback_reason,
            solver_status=solver_status,
            iterations=0,
            compute_us=(perf_counter_ns() - started) / 1000.0,
            distortion=distortion,
            sequence=executable[None, :, :],
            requested_target_feasible=requested_target_feasible,
            command_segment_feasible=segment_ok,
            command_terminal_viable=terminal_ok,
            command_next_step_exists=next_ok,
            safety_guarantee=safety_guarantee,
            emergency_mode=emergency_mode,
            fallback_requested=fallback,
            fallback_applied=fallback,
        )

    def update(
        self,
        raw_target: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> GovernorResult:
        started = perf_counter_ns()
        target = as_state_matrix(raw_target, self.dof)
        current, current_status = self._select_current(current_state)
        if not self._current_is_viable(current):
            jerk = self._emergency_jerk(current)
            executable = integrate_constant_jerk(current, jerk, self.dt)
            return self._result(
                current=current,
                executable=executable,
                jerk=jerk,
                target=target,
                control_time=control_time,
                started=started,
                requested_target_feasible=False,
                target_projected=np.all(np.isfinite(target)),
                fallback=True,
                fallback_reason="unrecoverable_current_state",
                solver_status=f"emergency_recovery:{current_status}",
                emergency_mode=True,
            )

        if not np.all(np.isfinite(target)):
            safe_jerk = np.asarray(
                [
                    self._safe_joint_jerk(current[joint], joint)
                    for joint in range(self.dof)
                ]
            )
            executable = integrate_constant_jerk(current, safe_jerk, self.dt)
            return self._result(
                current=current,
                executable=executable,
                jerk=safe_jerk,
                target=target,
                control_time=control_time,
                started=started,
                requested_target_feasible=False,
                target_projected=False,
                fallback=True,
                fallback_reason="nonfinite_raw_target",
                solver_status=f"fallback_safe_brake:{current_status}",
                emergency_mode=False,
            )

        jerk = np.empty(self.dof, dtype=float)
        statuses = []
        for joint in range(self.dof):
            jerk[joint], status = self._choose_joint_jerk(
                current[joint], target[joint], joint
            )
            statuses.append(status)
        executable = integrate_constant_jerk(current, jerk, self.dt)
        if not (
            segment_constant_jerk_feasible(current, jerk, self.dt, self.limits)
            and terminal_stopping_viable(executable, self.limits)
            and terminal_has_viable_next_step(executable, self.dt, self.limits)
        ):
            raise InvariantViolationError(
                "analytic one-step action failed its invariant postcheck"
            )
        return self._result(
            current=current,
            executable=executable,
            jerk=jerk,
            target=target,
            control_time=control_time,
            started=started,
            requested_target_feasible=terminal_stopping_viable(target, self.limits),
            target_projected=not np.allclose(executable, target, rtol=0.0, atol=2e-12),
            fallback=False,
            fallback_reason="",
            solver_status=f"one_step:{current_status}:{'+'.join(statuses)}",
            emergency_mode=False,
        )


class JerkQPGovernor:
    """Dimensionless, deterministic jerk MPC with a verified first action."""

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
        time_limit_s: float = 0.0008,
        max_iter: int = 4000,
        terminal_acceleration_fraction: float = 0.01,
        fallback_governor: OneStepBoundedJerkGovernor | None = None,
    ) -> None:
        if horizon_steps < 1:
            raise ValueError("horizon_steps must be positive")
        if dof != limits.dof:
            raise ValueError("limits dof does not match governor dof")
        if not np.isfinite(dt) or dt <= 0.0:
            raise ValueError("dt must be finite and positive")
        if not np.isfinite(time_limit_s) or not 0.0 < time_limit_s < min(dt, 0.01):
            raise ValueError("time_limit_s must be positive and below the control budget")
        if max_iter < 1:
            raise ValueError("max_iter must be positive")
        weights = np.asarray(
            (position_weight, velocity_weight, acceleration_weight), dtype=float
        )
        if not np.all(np.isfinite(weights)) or np.any(weights < 0.0):
            raise ValueError("state weights must be finite and non-negative")
        if (
            not np.isfinite(jerk_weight)
            or not np.isfinite(delta_jerk_weight)
            or jerk_weight <= 0.0
            or delta_jerk_weight < 0.0
        ):
            raise ValueError("jerk weights must be finite and non-negative")
        if not 0.0 < terminal_acceleration_fraction < 1.0:
            raise ValueError("terminal_acceleration_fraction must be in (0, 1)")

        self.dof = dof
        self.dt = float(dt)
        self.limits = limits
        self.horizon_steps = int(horizon_steps)
        self.weights = weights
        self.jerk_weight = float(jerk_weight)
        self.delta_jerk_weight = float(delta_jerk_weight)
        self.time_limit_s = float(time_limit_s)
        self.max_iter = int(max_iter)
        self.terminal_acceleration_fraction = float(terminal_acceleration_fraction)
        self.command_state: np.ndarray | None = None
        self.last_jerk = np.zeros(dof)
        self.fallback_governor = fallback_governor or OneStepBoundedJerkGovernor(
            dof, dt, limits
        )

        self._base_powers, self._physical_maps = self._build_maps()
        self._state_scales = self._build_state_scales()
        self._normalized_maps = np.empty((dof, 3, horizon_steps, horizon_steps))
        for joint in range(dof):
            self._normalized_maps[joint] = (
                self._physical_maps
                * limits.max_jerk[joint]
                / self._state_scales[joint, :, None, None]
            )
        self._hessian = None
        self._constraints = None
        self._solver = None
        self._warm_x: np.ndarray | None = None
        self._warm_y: np.ndarray | None = None
        self.solver_setup_count = 0
        self.solver_update_count = 0
        self.qp_hessian_condition_number = np.nan
        self.qp_constraint_condition_number = np.nan

    def reset(self, state: np.ndarray | None = None) -> None:
        self.command_state = None if state is None else as_state_matrix(state, self.dof)
        self.last_jerk = np.zeros(self.dof)
        self.fallback_governor.reset(state)
        self._solver = None
        self._warm_x = None
        self._warm_y = None
        self.solver_setup_count = 0
        self.solver_update_count = 0

    def _build_maps(self) -> tuple[np.ndarray, np.ndarray]:
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
                maps[:, step, source] = (
                    np.linalg.matrix_power(transition, step - source) @ control
                )
        return base, maps

    def _build_state_scales(self) -> np.ndarray:
        horizon_time = self.horizon_steps * self.dt
        position = np.maximum.reduce(
            (
                self.limits.max_velocity * horizon_time,
                0.5 * self.limits.max_acceleration * horizon_time**2,
                self.limits.max_jerk * horizon_time**3 / 6.0,
                np.full(self.dof, 1e-6),
            )
        )
        return np.column_stack(
            (position, self.limits.max_velocity, self.limits.max_acceleration)
        )

    def _build_problem_structure(self):
        from scipy import sparse

        n = self.horizon_steps
        difference = np.eye(n) - np.eye(n, k=-1)
        hessian_blocks = []
        constraint_blocks = []
        for joint in range(self.dof):
            maps = self._normalized_maps[joint]
            hessian = self.jerk_weight * np.eye(n) + self.delta_jerk_weight * (
                difference.T @ difference
            )
            for component, weight in enumerate(self.weights):
                hessian += weight * (maps[component].T @ maps[component])
            # Jerk, every velocity/acceleration endpoint, then a conservative
            # terminal stopping-safe box.
            constraints = np.vstack(
                (
                    np.eye(n),
                    maps[1],
                    maps[2],
                    maps[1][-1:],
                    maps[2][-1:],
                )
            )
            hessian_blocks.append(sparse.csc_matrix(2.0 * hessian))
            constraint_blocks.append(sparse.csc_matrix(constraints))
        hessian = sparse.block_diag(hessian_blocks, format="csc")
        constraints = sparse.block_diag(constraint_blocks, format="csc")
        self.qp_hessian_condition_number = float(
            np.linalg.cond(hessian.toarray())
        )
        self.qp_constraint_condition_number = float(
            np.linalg.cond(constraints.toarray())
        )
        self._hessian = hessian
        self._constraints = constraints

    def _problem_vectors(
        self, current: np.ndarray, sequence: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        n = self.horizon_steps
        predicted_bases = np.empty((self.dof, n, 3))
        gradients = []
        lower_blocks = []
        upper_blocks = []
        for joint in range(self.dof):
            base = np.einsum("nij,j->ni", self._base_powers, current[joint])
            predicted_bases[joint] = base
            scales = self._state_scales[joint]
            base_normalized = base / scales
            reference_normalized = sequence[:, joint, :] / scales
            maps = self._normalized_maps[joint]
            gradient = np.zeros(n)
            for component, weight in enumerate(self.weights):
                residual = (
                    base_normalized[:, component]
                    - reference_normalized[:, component]
                )
                gradient += weight * (maps[component].T @ residual)
            gradient[0] -= self.delta_jerk_weight * (
                self.last_jerk[joint] / self.limits.max_jerk[joint]
            )
            gradients.append(2.0 * gradient)

            acceleration_bound = self.terminal_acceleration_fraction
            acceleration_bound_physical = (
                acceleration_bound * self.limits.max_acceleration[joint]
            )
            stopping_margin = acceleration_bound_physical**2 / (
                2.0 * self.limits.max_jerk[joint]
            )
            terminal_velocity_bound = (
                self.limits.max_velocity[joint] - stopping_margin
            ) / self.limits.max_velocity[joint]
            lower_blocks.append(
                np.concatenate(
                    (
                        np.full(n, -1.0),
                        -1.0 - base_normalized[:, 1],
                        -1.0 - base_normalized[:, 2],
                        [-terminal_velocity_bound - base_normalized[-1, 1]],
                        [-acceleration_bound - base_normalized[-1, 2]],
                    )
                )
            )
            upper_blocks.append(
                np.concatenate(
                    (
                        np.full(n, 1.0),
                        1.0 - base_normalized[:, 1],
                        1.0 - base_normalized[:, 2],
                        [terminal_velocity_bound - base_normalized[-1, 1]],
                        [acceleration_bound - base_normalized[-1, 2]],
                    )
                )
            )
        return (
            np.concatenate(gradients),
            np.concatenate(lower_blocks),
            np.concatenate(upper_blocks),
            predicted_bases,
        )

    @staticmethod
    def _status_category(info: object, normalized_status: str) -> str:
        status_value = getattr(info, "status_val", None)
        if status_value == 8 or "time_limit" in normalized_status:
            return "qp_time_limit_reached"
        if status_value == 7 or "maximum_iterations" in normalized_status:
            return "qp_max_iter_reached"
        if status_value in {3, 4} or "primal_infeasible" in normalized_status:
            return "qp_primal_infeasible"
        if status_value in {5, 6} or "dual_infeasible" in normalized_status:
            return "qp_dual_infeasible"
        if status_value in {1, 2} or normalized_status in {
            "solved",
            "solved_inaccurate",
        }:
            return "qp_solved"
        return "qp_numerical_failure"

    @staticmethod
    def _solver_metrics(info: object) -> tuple[float, float, float, int]:
        solve_time = float(getattr(info, "solve_time", np.nan)) * 1e6
        primal = float(getattr(info, "prim_res", np.nan))
        dual = float(getattr(info, "dual_res", np.nan))
        iterations = int(getattr(info, "iter", 0))
        return solve_time, primal, dual, iterations

    def _fallback(
        self,
        target: np.ndarray,
        current: np.ndarray,
        control_time: float,
        reason: str,
        started: int,
        status: str,
        iterations: int,
        *,
        solve_time_us: float = np.nan,
        primal_residual: float = np.nan,
        dual_residual: float = np.nan,
        status_category: str | None = None,
    ) -> GovernorResult:
        self.fallback_governor.command_state = current.copy()
        result = self.fallback_governor.update(
            target, control_time=control_time, current_state=current
        )
        self.command_state = result.executable_state.copy()
        self.last_jerk = result.jerk.copy()
        self.fallback_governor.command_state = result.executable_state.copy()
        self.fallback_governor.last_jerk = result.jerk.copy()
        return GovernorResult(
            executable_state=result.executable_state,
            jerk=result.jerk,
            target_time=result.target_time,
            target_feasible=result.target_feasible,
            target_projected=False,
            fallback=True,
            fallback_reason=reason,
            solver_status=f"{status}->one_step",
            iterations=iterations,
            compute_us=(perf_counter_ns() - started) / 1000.0,
            distortion=result.distortion,
            sequence=result.sequence,
            requested_target_feasible=result.requested_target_feasible,
            command_segment_feasible=result.command_segment_feasible,
            command_terminal_viable=result.command_terminal_viable,
            command_next_step_exists=result.command_next_step_exists,
            safety_guarantee=result.safety_guarantee,
            emergency_mode=result.emergency_mode,
            fallback_requested=True,
            fallback_applied=True,
            qp_status_category=reason if status_category is None else status_category,
            qp_solve_time_us=solve_time_us,
            qp_primal_residual=primal_residual,
            qp_dual_residual=dual_residual,
            qp_hessian_condition_number=self.qp_hessian_condition_number,
            qp_constraint_condition_number=self.qp_constraint_condition_number,
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
        if not np.all(np.isfinite(current)):
            raise InvariantViolationError("QP current state must be finite")
        if not np.all(np.isfinite(sequence)):
            return self._fallback(
                sequence[0],
                current,
                control_time,
                "nonfinite_reference_sequence",
                started,
                "invalid_input",
                0,
                status_category="qp_invalid_input",
            )

        try:
            import osqp

            if self._hessian is None or self._constraints is None:
                self._build_problem_structure()
            gradient, lower, upper, predicted_bases = self._problem_vectors(
                current, sequence
            )
            if self._solver is None:
                self._solver = osqp.OSQP()
                self._solver.setup(
                    P=self._hessian,
                    q=gradient,
                    A=self._constraints,
                    l=lower,
                    u=upper,
                    verbose=False,
                    polishing=False,
                    warm_starting=True,
                    adaptive_rho=False,
                    rho=0.1,
                    alpha=1.6,
                    scaling=0,
                    scaled_termination=False,
                    eps_abs=1e-5,
                    eps_rel=1e-5,
                    max_iter=self.max_iter,
                    time_limit=self.time_limit_s,
                    check_termination=1,
                )
                self.solver_setup_count += 1
            else:
                self._solver.update(q=gradient, l=lower, u=upper)
                self.solver_update_count += 1
            if self._warm_x is not None and self._warm_y is not None:
                self._solver.warm_start(x=self._warm_x, y=self._warm_y)
            solution = self._solver.solve(raise_error=False)
        except ImportError:
            return self._fallback(
                sequence[0],
                current,
                control_time,
                "osqp_unavailable",
                started,
                "unavailable",
                0,
                status_category="qp_solver_unavailable",
            )
        except Exception as error:  # OSQP exposes backend-specific errors.
            return self._fallback(
                sequence[0],
                current,
                control_time,
                "qp_numerical_failure",
                started,
                f"exception:{type(error).__name__}",
                0,
            )

        status = str(solution.info.status).lower().replace(" ", "_")
        category = self._status_category(solution.info, status)
        solve_time_us, primal_residual, dual_residual, iterations = (
            self._solver_metrics(solution.info)
        )
        if solution.x is None or category != "qp_solved":
            if solution.x is None and category == "qp_solved":
                category = "qp_numerical_failure"
            return self._fallback(
                sequence[0],
                current,
                control_time,
                category,
                started,
                status,
                iterations,
                solve_time_us=solve_time_us,
                primal_residual=primal_residual,
                dual_residual=dual_residual,
            )

        normalized_plan = np.asarray(solution.x, dtype=float)
        if (
            normalized_plan.shape != (self.dof * self.horizon_steps,)
            or not np.all(np.isfinite(normalized_plan))
        ):
            return self._fallback(
                sequence[0],
                current,
                control_time,
                "qp_numerical_failure",
                started,
                status,
                iterations,
                solve_time_us=solve_time_us,
                primal_residual=primal_residual,
                dual_residual=dual_residual,
            )
        if solution.y is not None and np.all(np.isfinite(solution.y)):
            self._warm_x = normalized_plan.copy()
            self._warm_y = np.asarray(solution.y, dtype=float).copy()

        n = self.horizon_steps
        jerk_plan = normalized_plan.reshape(self.dof, n).T
        jerk_plan = jerk_plan * self.limits.max_jerk[None, :]
        state_plan = np.empty((n, self.dof, 3))
        for joint in range(self.dof):
            for component in range(3):
                state_plan[:, joint, component] = (
                    predicted_bases[joint, :, component]
                    + self._physical_maps[component] @ jerk_plan[:, joint]
                )
        first_jerk = jerk_plan[0]
        executable = integrate_constant_jerk(current, first_jerk, self.dt)
        first_matches_plan = np.allclose(
            executable, state_plan[0], rtol=0.0, atol=2e-8
        )
        first_action_safe = bool(
            first_matches_plan
            and segment_constant_jerk_feasible(
                current, first_jerk, self.dt, self.limits
            )
            and terminal_stopping_viable(executable, self.limits)
            and terminal_has_viable_next_step(executable, self.dt, self.limits)
        )
        terminal_safe = terminal_stopping_viable(state_plan[-1], self.limits)
        if not first_action_safe or not terminal_safe:
            return self._fallback(
                sequence[0],
                current,
                control_time,
                "qp_postcheck_failed",
                started,
                status,
                iterations,
                solve_time_us=solve_time_us,
                primal_residual=primal_residual,
                dual_residual=dual_residual,
            )

        state_plan[0] = executable
        self.command_state = executable.copy()
        self.last_jerk = first_jerk.copy()
        self.fallback_governor.command_state = executable.copy()
        self.fallback_governor.last_jerk = first_jerk.copy()
        requested_feasible = terminal_stopping_viable(sequence[0], self.limits)
        return GovernorResult(
            executable_state=executable,
            jerk=first_jerk,
            target_time=float(control_time + self.dt),
            target_feasible=requested_feasible,
            target_projected=False,
            fallback=False,
            fallback_reason="",
            solver_status=status,
            iterations=iterations,
            compute_us=(perf_counter_ns() - started) / 1000.0,
            distortion=executable - sequence[0],
            sequence=state_plan,
            requested_target_feasible=requested_feasible,
            command_segment_feasible=True,
            command_terminal_viable=True,
            command_next_step_exists=True,
            safety_guarantee=True,
            emergency_mode=False,
            fallback_requested=False,
            fallback_applied=False,
            qp_status_category=category,
            qp_solve_time_us=solve_time_us,
            qp_primal_residual=primal_residual,
            qp_dual_residual=dual_residual,
            qp_hessian_condition_number=self.qp_hessian_condition_number,
            qp_constraint_condition_number=self.qp_constraint_condition_number,
        )
