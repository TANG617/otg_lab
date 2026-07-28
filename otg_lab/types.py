"""Time-explicit scalar-state structures for the numerical components.

The two clocks in this module deliberately mean different things:

``state_time``
    Physical time at which the represented state belongs.
``available_time``
    Physical time at which an online algorithm may first use the value.

A delayed posterior therefore has ``available_time > state_time``.  A future
prediction normally has ``available_time < state_time`` and additionally
carries ``source_state_time`` and ``prediction_horizon``.  Keeping those
fields separate prevents a predicted target from being silently treated as a
current-state estimate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatVector = NDArray[np.float64]


@dataclass(frozen=True)
class CommandProfile:
    """Executable jerk profile over one command interval.

    ``segment_boundaries`` are profile-relative times.  They always include
    zero and ``duration`` and have one more entry than ``segment_jerks``.
    Each jerk row applies on the corresponding half-open interval (with the
    final interval closed at ``duration``). Exact profiles independently
    reconstruct every state. When a binding does not expose its segments, an
    explicitly inexact sampled state grid supports evaluation without
    fabricating internal jerk values.
    """

    profile_kind: str
    start_time: float
    duration: float
    initial_state: np.ndarray
    terminal_state: np.ndarray
    segment_boundaries: np.ndarray
    segment_jerks: np.ndarray
    source: str
    exact: bool
    sample_times: np.ndarray | None = None
    sample_states: np.ndarray | None = None

    def __post_init__(self) -> None:
        start_time = _finite_time(self.start_time, "start_time")
        duration = _finite_time(self.duration, "duration")
        if duration <= 0.0:
            raise ValueError("command profile duration must be positive")
        initial = np.asarray(self.initial_state, dtype=np.float64)
        terminal = np.asarray(self.terminal_state, dtype=np.float64)
        if initial.ndim == 1:
            initial = initial.reshape(1, -1)
        if terminal.ndim == 1:
            terminal = terminal.reshape(1, -1)
        if initial.shape != (1, 3):
            raise ValueError("initial_state must have shape (1, 3)")
        if terminal.shape != initial.shape:
            raise ValueError("terminal_state must match initial_state shape")
        boundaries = np.asarray(self.segment_boundaries, dtype=np.float64)
        jerks = np.asarray(self.segment_jerks, dtype=np.float64)
        if boundaries.ndim != 1 or boundaries.size < 2:
            raise ValueError("segment_boundaries must be one-dimensional")
        if jerks.ndim == 1:
            jerks = jerks.reshape(-1, initial.shape[0])
        has_segment_model = jerks.shape == (
            boundaries.size - 1,
            initial.shape[0],
        )
        if jerks.ndim != 2 or jerks.shape[1:] != (1,):
            raise ValueError("segment_jerks must have shape (segment_count, 1)")
        sample_times = (
            None
            if self.sample_times is None
            else np.asarray(self.sample_times, dtype=np.float64)
        )
        sample_states = (
            None
            if self.sample_states is None
            else np.asarray(self.sample_states, dtype=np.float64)
        )
        if (sample_times is None) != (sample_states is None):
            raise ValueError("sample_times and sample_states must be provided together")
        has_sample_model = sample_times is not None
        if self.exact and not has_segment_model:
            raise ValueError("exact profile jerks must cover every segment")
        if not has_segment_model and not has_sample_model:
            raise ValueError("profile requires either segment jerks or sampled states")
        if not (
            np.all(np.isfinite(initial))
            and np.all(np.isfinite(terminal))
            and np.all(np.isfinite(boundaries))
            and np.all(np.isfinite(jerks))
        ):
            raise ValueError("command profile values must be finite")
        if abs(float(boundaries[0])) > 1e-12:
            raise ValueError("segment_boundaries must start at zero")
        if abs(float(boundaries[-1]) - duration) > 1e-12:
            raise ValueError("segment_boundaries must end at duration")
        if np.any(np.diff(boundaries) <= 0.0):
            raise ValueError("segment_boundaries must be strictly increasing")
        if has_sample_model:
            assert sample_times is not None and sample_states is not None
            if sample_times.ndim != 1 or sample_times.size < 2:
                raise ValueError("sample_times must be a one-dimensional grid")
            if sample_states.shape != (sample_times.size, *initial.shape):
                raise ValueError("sample_states must have shape (sample_count, 1, 3)")
            if not (
                np.all(np.isfinite(sample_times))
                and np.all(np.isfinite(sample_states))
            ):
                raise ValueError("sampled command profile values must be finite")
            if abs(float(sample_times[0])) > 1e-12:
                raise ValueError("sample_times must start at zero")
            if abs(float(sample_times[-1]) - duration) > 1e-12:
                raise ValueError("sample_times must end at duration")
            if np.any(np.diff(sample_times) <= 0.0):
                raise ValueError("sample_times must be strictly increasing")
            if not np.allclose(sample_states[0], initial, rtol=0.0, atol=2e-8):
                raise ValueError("first sampled state must match initial_state")
            if not np.allclose(sample_states[-1], terminal, rtol=0.0, atol=2e-8):
                raise ValueError("last sampled state must match terminal_state")
        initial = np.array(initial, copy=True)
        terminal = np.array(terminal, copy=True)
        boundaries = np.array(boundaries, copy=True)
        jerks = np.array(jerks, copy=True)
        if sample_times is not None:
            sample_times = np.array(sample_times, copy=True)
            sample_states = np.array(sample_states, copy=True)
        for value in (
            initial,
            terminal,
            boundaries,
            jerks,
            sample_times,
            sample_states,
        ):
            if value is None:
                continue
            value.setflags(write=False)
        object.__setattr__(self, "start_time", start_time)
        object.__setattr__(self, "duration", duration)
        object.__setattr__(self, "initial_state", initial)
        object.__setattr__(self, "terminal_state", terminal)
        object.__setattr__(self, "segment_boundaries", boundaries)
        object.__setattr__(self, "segment_jerks", jerks)
        object.__setattr__(self, "sample_times", sample_times)
        object.__setattr__(self, "sample_states", sample_states)
        object.__setattr__(self, "profile_kind", str(self.profile_kind))
        object.__setattr__(self, "source", str(self.source))
        object.__setattr__(self, "exact", bool(self.exact))

    @property
    def dof(self) -> int:
        return int(self.initial_state.shape[0])

    @property
    def segment_count(self) -> int:
        return int(self.segment_jerks.shape[0])

    @property
    def boundary_count(self) -> int:
        """Number of accessible internal profile boundaries."""

        return max(0, int(self.segment_boundaries.size) - 2)

    @property
    def first_jerk(self) -> FloatVector | None:
        return None if self.segment_count == 0 else self.segment_jerks[0]

    @property
    def last_jerk(self) -> FloatVector | None:
        return None if self.segment_count == 0 else self.segment_jerks[-1]

    @property
    def internal_max_abs_jerk(self) -> FloatVector | None:
        if self.segment_count == 0:
            return None
        return np.max(np.abs(self.segment_jerks), axis=0)

    def evaluate(self, profile_time: float) -> np.ndarray:
        """Evaluate exact segments analytically or an inexact sampled grid."""

        value = float(profile_time)
        if not np.isfinite(value) or value < -1e-12 or value > self.duration + 1e-12:
            raise ValueError("profile_time must lie in [0, duration]")
        value = min(max(value, 0.0), self.duration)
        if self.segment_count == 0:
            if self.sample_times is None or self.sample_states is None:
                raise ValueError("sampled profile evaluator is unavailable")
            right = int(np.searchsorted(self.sample_times, value, side="left"))
            if right == 0:
                return np.array(self.sample_states[0], copy=True)
            if right >= self.sample_times.size:
                return np.array(self.sample_states[-1], copy=True)
            if self.sample_times[right] == value:
                return np.array(self.sample_states[right], copy=True)
            left = right - 1
            alpha = (value - self.sample_times[left]) / (
                self.sample_times[right] - self.sample_times[left]
            )
            return np.array(
                self.sample_states[left]
                + alpha * (self.sample_states[right] - self.sample_states[left]),
                copy=True,
            )
        state = np.array(self.initial_state, dtype=np.float64, copy=True)
        for index, jerk in enumerate(self.segment_jerks):
            left = float(self.segment_boundaries[index])
            right = float(self.segment_boundaries[index + 1])
            step = min(value, right) - left
            if step <= 0.0:
                break
            position = (
                state[:, 0]
                + state[:, 1] * step
                + 0.5 * state[:, 2] * step**2
                + jerk * step**3 / 6.0
            )
            velocity = state[:, 1] + state[:, 2] * step + 0.5 * jerk * step**2
            acceleration = state[:, 2] + jerk * step
            state = np.column_stack((position, velocity, acceleration))
            if value <= right:
                break
        return state

    @property
    def endpoint_matches_profile(self) -> bool:
        return bool(
            np.allclose(
                self.evaluate(self.duration),
                self.terminal_state,
                rtol=0.0,
                atol=2e-8,
            )
        )

    @property
    def constant_jerk_exact(self) -> bool | None:
        if self.profile_kind not in {"constant_jerk", "emergency_constant_jerk"}:
            return None
        return bool(self.exact and self.endpoint_matches_profile)


@dataclass(frozen=True)
class FollowerResult:
    """One-cycle follower outcome with explicit request/commit semantics.

    ``fallback`` remains available as a read-only compatibility alias, but new
    code should distinguish a requested fallback from one that was actually
    committed using ``fallback_requested`` and ``fallback_applied``.

    ``requested_target_free_trajectory_duration`` belongs to the solve from
    the current state to the requested follower target.
    ``free_trajectory_duration`` and ``command_t_free_le_dt`` belong only to
    the separate solve from the same current state to ``command_state``.  The
    requested-target duration must never be reused as a command diagnostic.
    """

    command_state: np.ndarray
    command_jerk: np.ndarray
    command_time: float
    solver_status: str
    fallback_reason: str
    target_projected: bool
    requested_target_free_trajectory_duration: float
    free_trajectory_duration: float
    frozen_trajectory_duration: float
    compute_us: float
    continuous_audit: Mapping[str, np.ndarray | float | int | str]
    requested_target_feasible: bool
    command_segment_feasible: bool
    command_terminal_viable: bool
    command_next_step_exists: bool
    command_t_free_le_dt: bool
    fallback_requested: bool
    fallback_applied: bool
    safety_guarantee: bool
    emergency_mode: bool
    command_profile: CommandProfile | None = None
    native_follower: str = ""
    native_command_executed: bool = True
    safety_shield_requested: bool = False
    safety_shield_applied: bool = False
    safety_shield_reason: str = ""
    fallback_controller: str = ""
    fallback_changes_algorithm: bool = False

    @property
    def fallback(self) -> bool:
        """Deprecated alias for whether the safety fallback was committed."""

        return self.fallback_applied

    @property
    def command_profile_kind(self) -> str:
        return (
            "unspecified"
            if self.command_profile is None
            else self.command_profile.profile_kind
        )

    @property
    def command_profile_segment_count(self) -> int:
        return 0 if self.command_profile is None else self.command_profile.segment_count

    @property
    def command_profile_boundary_count(self) -> int:
        return (
            0 if self.command_profile is None else self.command_profile.boundary_count
        )

    @property
    def command_profile_exact(self) -> bool:
        return bool(self.command_profile is not None and self.command_profile.exact)

    @property
    def command_endpoint_matches_profile(self) -> bool:
        return bool(
            self.command_profile is not None
            and self.command_profile.endpoint_matches_profile
        )

    @property
    def command_first_jerk(self) -> np.ndarray | None:
        return None if self.command_profile is None else self.command_profile.first_jerk

    @property
    def command_last_jerk(self) -> np.ndarray | None:
        return None if self.command_profile is None else self.command_profile.last_jerk

    @property
    def command_internal_max_abs_jerk(self) -> np.ndarray | None:
        if self.command_profile is None:
            return None
        return self.command_profile.internal_max_abs_jerk

    @property
    def command_constant_jerk_exact(self) -> bool | None:
        if self.command_profile is None:
            return None
        return self.command_profile.constant_jerk_exact

    @property
    def command_profile_continuous_constraints_satisfied(self) -> bool:
        violations = np.asarray(
            self.continuous_audit.get("violation_count", []), dtype=int
        )
        return bool(violations.size and np.all(violations == 0))


def _finite_time(value: float, name: str) -> float:
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return result


def _vector(
    value: ArrayLike,
    name: str,
    *,
    shape: tuple[int, ...] | None = None,
) -> FloatVector:
    """Return an owned, immutable one-dimensional float64 vector.

    Non-finite component values are intentionally permitted here.  Their
    handling is an estimator policy decision and is tested at that layer.
    Times, in contrast, are always required to be finite.
    """

    array = np.asarray(value, dtype=np.float64)
    if array.ndim == 0:
        array = array.reshape(1)
    if array.ndim != 1 or array.size == 0:
        raise ValueError(
            f"{name} must be a non-empty scalar or one-dimensional array; "
            f"got shape {array.shape}"
        )
    if shape is not None and array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {array.shape}")
    result = np.array(array, dtype=np.float64, copy=True)
    result.setflags(write=False)
    return result


def _metadata(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    # Keep an owned, serialization-friendly mapping. The dataclass itself is
    # frozen; artifact writers can use dataclasses.asdict without a
    # MappingProxyType/pickle incompatibility.
    return dict(value or {})


@dataclass(frozen=True)
class Measurement:
    """A measurement delivered to an online estimator.

    Scalars are normalized to shape ``(1,)`` for numerical routines. Optional
    measured derivatives are carried without implying that an estimator must
    use them.
    """

    position: FloatVector
    state_time: float
    available_time: float
    velocity: FloatVector | None = None
    acceleration: FloatVector | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        position = _vector(self.position, "position")
        if position.shape != (1,):
            raise ValueError("position must contain exactly one axis")
        state_time = _finite_time(self.state_time, "state_time")
        available_time = _finite_time(self.available_time, "available_time")
        if available_time < state_time:
            raise ValueError(
                "measurement available_time cannot precede state_time: "
                f"{available_time} < {state_time}"
            )
        velocity = (
            None
            if self.velocity is None
            else _vector(self.velocity, "velocity", shape=position.shape)
        )
        acceleration = (
            None
            if self.acceleration is None
            else _vector(
                self.acceleration,
                "acceleration",
                shape=position.shape,
            )
        )
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "velocity", velocity)
        object.__setattr__(self, "acceleration", acceleration)
        object.__setattr__(self, "state_time", state_time)
        object.__setattr__(self, "available_time", available_time)
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @property
    def dof(self) -> int:
        return int(self.position.size)

    @property
    def source_time(self) -> float:
        """Schema-friendly alias for :attr:`state_time`."""

        return self.state_time

    @property
    def arrival_time(self) -> float:
        """Schema-friendly alias for :attr:`available_time`."""

        return self.available_time

    @property
    def is_finite(self) -> bool:
        arrays = (self.position, self.velocity, self.acceleration)
        return all(
            component is None or bool(np.all(np.isfinite(component)))
            for component in arrays
        )


@dataclass(frozen=True)
class TimedState:
    """A posterior or explicitly labelled future state.

    Estimators return instances with ``source_state_time=None`` and
    ``prediction_horizon=None``.  Predictors set both fields.  The same vector
    representation lets governors and followers consume either object while
    preserving the semantic distinction in machine-readable fields.
    """

    position: FloatVector
    velocity: FloatVector
    acceleration: FloatVector
    state_time: float
    available_time: float
    jerk: FloatVector | None = None
    method: str = ""
    status: str = "ok"
    valid: bool = True
    startup: bool = False
    compute_time_us: float = 0.0
    source_state_time: float | None = None
    prediction_horizon: float | None = None
    causal: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        position = _vector(self.position, "position")
        if position.shape != (1,):
            raise ValueError("state must contain exactly one axis")
        velocity = _vector(self.velocity, "velocity", shape=position.shape)
        acceleration = _vector(
            self.acceleration,
            "acceleration",
            shape=position.shape,
        )
        jerk = (
            None
            if self.jerk is None
            else _vector(self.jerk, "jerk", shape=position.shape)
        )
        state_time = _finite_time(self.state_time, "state_time")
        available_time = _finite_time(self.available_time, "available_time")
        compute_time_us = float(self.compute_time_us)
        if not np.isfinite(compute_time_us) or compute_time_us < 0.0:
            raise ValueError("compute_time_us must be finite and non-negative")

        source_state_time = self.source_state_time
        prediction_horizon = self.prediction_horizon
        if (source_state_time is None) != (prediction_horizon is None):
            raise ValueError(
                "source_state_time and prediction_horizon must either both "
                "be set (prediction) or both be None (posterior)"
            )
        if source_state_time is None:
            # A posterior cannot be available before the physical state it
            # estimates.  Delayed estimators naturally satisfy a strict >.
            if available_time < state_time:
                raise ValueError(
                    "posterior available_time cannot precede state_time: "
                    f"{available_time} < {state_time}"
                )
        else:
            source_state_time = _finite_time(
                source_state_time,
                "source_state_time",
            )
            prediction_horizon = float(prediction_horizon)
            if not np.isfinite(prediction_horizon) or prediction_horizon < 0.0:
                raise ValueError("prediction_horizon must be finite and non-negative")
            expected = source_state_time + prediction_horizon
            tolerance = (
                32.0
                * np.finfo(float).eps
                * max(
                    1.0,
                    abs(state_time),
                    abs(expected),
                )
            )
            if abs(state_time - expected) > tolerance:
                raise ValueError(
                    "prediction timestamp mismatch: state_time must equal "
                    "source_state_time + prediction_horizon; got "
                    f"{state_time} != {source_state_time} + "
                    f"{prediction_horizon}"
                )

        object.__setattr__(self, "position", position)
        object.__setattr__(self, "velocity", velocity)
        object.__setattr__(self, "acceleration", acceleration)
        object.__setattr__(self, "jerk", jerk)
        object.__setattr__(self, "state_time", state_time)
        object.__setattr__(self, "available_time", available_time)
        object.__setattr__(self, "compute_time_us", compute_time_us)
        object.__setattr__(self, "source_state_time", source_state_time)
        object.__setattr__(self, "prediction_horizon", prediction_horizon)
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @property
    def dof(self) -> int:
        return int(self.position.size)

    @property
    def is_prediction(self) -> bool:
        return self.prediction_horizon is not None

    @property
    def prediction_time(self) -> float | None:
        """Physical target time, or ``None`` for an estimator posterior."""

        return self.state_time if self.is_prediction else None

    @property
    def lag(self) -> float:
        """Availability lag for posteriors (negative for future predictions)."""

        return self.available_time - self.state_time

    @property
    def is_finite(self) -> bool:
        arrays = (self.position, self.velocity, self.acceleration, self.jerk)
        return all(
            component is None or bool(np.all(np.isfinite(component)))
            for component in arrays
        )

    def with_updates(self, **changes: Any) -> TimedState:
        """Immutable convenience wrapper around :func:`dataclasses.replace`."""

        return replace(self, **changes)

    def as_array(self, *, include_jerk: bool = False) -> FloatVector:
        """Return one ``[p, v, a]`` (or ``[p, v, a, j]``) row.

        The returned layout is ``(1, components)``. A copy is returned so
        callers cannot mutate the time-stamped state.
        """

        components = [self.position, self.velocity, self.acceleration]
        if include_jerk:
            if self.jerk is None:
                raise ValueError("state does not carry a jerk estimate")
            components.append(self.jerk)
        return np.column_stack(components)


def state_from_array(
    values: ArrayLike,
    *,
    state_time: float,
    available_time: float,
    method: str = "",
    **kwargs: Any,
) -> TimedState:
    """Construct a :class:`TimedState` from shape ``(1, 3 or 4)``."""

    array = np.asarray(values, dtype=float)
    if array.ndim == 1 and array.size in (3, 4):
        array = array.reshape(1, -1)
    if array.ndim != 2 or array.shape[0] != 1 or array.shape[1] not in (3, 4):
        raise ValueError("values must have shape (1, 3) or (1, 4)")
    return TimedState(
        position=array[:, 0],
        velocity=array[:, 1],
        acceleration=array[:, 2],
        jerk=None if array.shape[1] == 3 else array[:, 3],
        state_time=state_time,
        available_time=available_time,
        method=method,
        **kwargs,
    )
