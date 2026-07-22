"""Time-explicit, vector-valued data structures for the OTG pipeline.

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
class FollowerResult:
    """One-cycle follower outcome with explicit request/commit semantics.

    ``fallback`` remains available as a read-only compatibility alias, but new
    code should distinguish a requested fallback from one that was actually
    committed using ``fallback_requested`` and ``fallback_applied``.
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

    @property
    def fallback(self) -> bool:
        """Deprecated alias for whether the safety fallback was committed."""

        return self.fallback_applied


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

    Parameters are vector-valued even for one degree of freedom; a scalar is
    accepted and normalized to shape ``(1,)``.  Optional measured derivatives
    are carried without implying that an estimator must use them.
    """

    position: FloatVector
    state_time: float
    available_time: float
    velocity: FloatVector | None = None
    acceleration: FloatVector | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        position = _vector(self.position, "position")
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
        """Return ``[p, v, a]`` (or ``[p, v, a, j]``) by DoF.

        The returned layout is ``(dof, components)``.  A copy is returned so
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
    """Construct a :class:`TimedState` from shape ``(dof, 3 or 4)``."""

    array = np.asarray(values, dtype=float)
    if array.ndim == 1 and array.size in (3, 4):
        array = array.reshape(1, -1)
    if array.ndim != 2 or array.shape[0] == 0 or array.shape[1] not in (3, 4):
        raise ValueError("values must have shape (dof, 3) or (dof, 4)")
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
