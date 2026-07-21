"""Ideal and delayed-servo plant models used after command generation."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter_ns

import numpy as np

from .governors import MotionLimits, as_state_matrix, integrate_constant_jerk


@dataclass(frozen=True)
class PlantResult:
    """One plant update with explicit command provenance.

    ``delayed_command_age`` is the realized age of the queue entry actually
    consumed by the plant.  It can be larger than ``configured_delay_s`` when
    the configured delay falls between control ticks.  The optional fields are
    appended for source compatibility with earlier callers that constructed a
    :class:`PlantResult` positionally.
    """

    true_state: np.ndarray
    measured_state: np.ndarray
    state_time: float
    available_time: float
    saturated: np.ndarray
    delayed_command_age: float
    compute_us: float
    status: str
    command_source_time: float | None = None
    configured_delay_s: float = 0.0


class IdealCommandPlant:
    """The command state is the plant state (historical pass-to-input mode)."""

    name = "ideal_command_state"

    def __init__(self, dof: int, dt: float) -> None:
        self.dof = int(dof)
        self.dt = float(dt)
        self.state: np.ndarray | None = None

    def reset(self, state: np.ndarray) -> None:
        self.state = as_state_matrix(state, self.dof)

    def update(self, command_state: np.ndarray, *, command_time: float) -> PlantResult:
        started = perf_counter_ns()
        self.state = as_state_matrix(command_state, self.dof)
        return PlantResult(
            true_state=self.state.copy(),
            measured_state=self.state.copy(),
            state_time=float(command_time),
            available_time=float(command_time),
            saturated=np.zeros(self.dof, dtype=bool),
            delayed_command_age=0.0,
            compute_us=(perf_counter_ns() - started) / 1000.0,
            status="ideal",
            command_source_time=float(command_time),
            configured_delay_s=0.0,
        )


class DelayedServoPlant:
    """Deterministic second-order servo with delay, saturation, and noise.

    The continuous servo acceleration request is integrated at ``substeps``
    per control interval.  Acceleration changes are jerk-limited, so true
    position/velocity/acceleration remain a physically explicit state rather
    than a filtered copy of the command.
    """

    name = "delayed_second_order_servo"

    def __init__(
        self,
        dof: int,
        dt: float,
        limits: MotionLimits,
        *,
        bandwidth_hz: float | Sequence[float] = 8.0,
        damping_ratio: float | Sequence[float] = 0.9,
        delay_s: float = 0.01,
        position_noise_sigma: float = 0.0,
        velocity_noise_sigma: float = 0.0,
        acceleration_noise_sigma: float = 0.0,
        substeps: int = 10,
        seed: int = 0,
    ) -> None:
        if dof != limits.dof:
            raise ValueError("limits dof mismatch")
        if dt <= 0.0 or substeps < 1 or delay_s < 0.0:
            raise ValueError("invalid plant timing")
        self.dof = int(dof)
        self.dt = float(dt)
        self.limits = limits
        self.bandwidth_hz = np.broadcast_to(
            np.asarray(bandwidth_hz, dtype=float), (dof,)
        ).copy()
        self.damping_ratio = np.broadcast_to(
            np.asarray(damping_ratio, dtype=float), (dof,)
        ).copy()
        if np.any(self.bandwidth_hz <= 0.0) or np.any(self.damping_ratio <= 0.0):
            raise ValueError("bandwidth and damping must be positive")
        self.delay_s = float(delay_s)
        self.position_noise_sigma = float(position_noise_sigma)
        self.velocity_noise_sigma = float(velocity_noise_sigma)
        self.acceleration_noise_sigma = float(acceleration_noise_sigma)
        self.substeps = int(substeps)
        self.rng = np.random.default_rng(seed)
        self.state: np.ndarray | None = None
        self._command_queue: deque[tuple[float, np.ndarray]] = deque()

    def reset(self, state: np.ndarray, *, state_time: float = 0.0) -> None:
        self.state = as_state_matrix(state, self.dof)
        self._command_queue.clear()
        self._command_queue.append((float(state_time), self.state.copy()))

    def _delayed_command(self, current_time: float) -> tuple[float, np.ndarray]:
        cutoff = current_time - self.delay_s
        selected = self._command_queue[0]
        for item in self._command_queue:
            if item[0] <= cutoff + 1e-15:
                selected = item
            else:
                break
        while len(self._command_queue) > 2 and self._command_queue[1][0] <= cutoff:
            self._command_queue.popleft()
        return selected

    def update(self, command_state: np.ndarray, *, command_time: float) -> PlantResult:
        started = perf_counter_ns()
        command = as_state_matrix(command_state, self.dof)
        if not np.all(np.isfinite(command)):
            raise ValueError("plant command must be finite")
        if self.state is None:
            self.reset(command, state_time=command_time - self.dt)
        self._command_queue.append((float(command_time), command.copy()))
        delayed_time, delayed = self._delayed_command(float(command_time))
        step_dt = self.dt / self.substeps
        saturated = np.zeros(self.dof, dtype=bool)
        omega = 2.0 * np.pi * self.bandwidth_hz
        for _ in range(self.substeps):
            requested_acceleration = omega**2 * (
                delayed[:, 0] - self.state[:, 0]
            ) + 2.0 * self.damping_ratio * omega * (delayed[:, 1] - self.state[:, 1])
            bounded_acceleration = np.clip(
                requested_acceleration,
                -self.limits.max_acceleration,
                self.limits.max_acceleration,
            )
            saturated |= bounded_acceleration != requested_acceleration
            requested_jerk = (bounded_acceleration - self.state[:, 2]) / step_dt
            jerk = np.clip(requested_jerk, -self.limits.max_jerk, self.limits.max_jerk)
            saturated |= jerk != requested_jerk
            next_state = integrate_constant_jerk(self.state, jerk, step_dt)
            velocity = np.clip(
                next_state[:, 1],
                -self.limits.max_velocity,
                self.limits.max_velocity,
            )
            saturated |= velocity != next_state[:, 1]
            next_state[:, 1] = velocity
            self.state = next_state
        noise = np.column_stack(
            (
                self.rng.normal(0.0, self.position_noise_sigma, self.dof),
                self.rng.normal(0.0, self.velocity_noise_sigma, self.dof),
                self.rng.normal(0.0, self.acceleration_noise_sigma, self.dof),
            )
        )
        measured = self.state + noise
        return PlantResult(
            true_state=self.state.copy(),
            measured_state=measured,
            state_time=float(command_time),
            available_time=float(command_time),
            saturated=saturated,
            delayed_command_age=max(0.0, float(command_time - delayed_time)),
            compute_us=(perf_counter_ns() - started) / 1000.0,
            status="saturated" if np.any(saturated) else "ok",
            command_source_time=float(delayed_time),
            configured_delay_s=self.delay_s,
        )
