"""Time-explicit composition of estimator, predictor, governor, follower, plant.

This module contains no rule that derives a follower's ``minimum_duration``
from a prediction horizon.  A horizon selects a physical reference target
time; trajectory duration remains an independently configured follower
property.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from .estimators import Estimator
from .predictors import Predictor, select_target_components
from .types import Measurement, TimedState

PER_AXIS_CAUSAL_SYNC = "per_axis_estimator_ca_propagation_to_control_time"


@runtime_checkable
class GovernorProtocol(Protocol):
    """Structural interface used by :class:`TrackingPipeline`."""

    dof: int
    dt: float

    def reset(self, state: NDArray[np.float64] | None = None) -> None: ...

    def update(
        self,
        raw_target: NDArray[np.float64],
        *,
        control_time: float,
        current_state: NDArray[np.float64] | None = None,
    ) -> Any: ...


@runtime_checkable
class FollowerProtocol(Protocol):
    dof: int
    dt: float

    def reset(self, state: NDArray[np.float64]) -> None: ...

    def update(
        self,
        target: NDArray[np.float64],
        *,
        control_time: float,
        current_state: NDArray[np.float64] | None = None,
    ) -> Any: ...


@runtime_checkable
class PlantProtocol(Protocol):
    dof: int
    dt: float

    def reset(self, state: NDArray[np.float64]) -> None: ...

    def update(
        self,
        command_state: NDArray[np.float64],
        *,
        command_time: float,
    ) -> Any: ...


def _time(value: float, name: str) -> float:
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _state_matrix(
    state: TimedState | NDArray[np.float64], dof: int
) -> NDArray[np.float64]:
    if isinstance(state, TimedState):
        value = state.as_array()
    else:
        value = np.asarray(state, dtype=float)
        if value.shape == (3,) and dof == 1:
            value = value.reshape(1, 3)
    if value.shape != (dof, 3):
        raise ValueError(f"state must have shape ({dof}, 3), got {value.shape}")
    if not np.all(np.isfinite(value)):
        raise ValueError("current state must contain only finite values")
    return np.array(value, copy=True)


def _prediction_state(
    values: NDArray[np.float64],
    *,
    state_time: float,
    available_time: float,
    source_state_time: float,
    method: str,
    status: str,
    valid: bool,
    causal: bool,
    metadata: dict[str, Any],
) -> TimedState:
    horizon = state_time - source_state_time
    tolerance = (
        32.0
        * np.finfo(float).eps
        * max(
            1.0,
            abs(state_time),
            abs(source_state_time),
        )
    )
    if horizon < -tolerance:
        raise ValueError("pipeline component returned a state in the past")
    horizon = max(0.0, horizon)
    return TimedState(
        values[:, 0],
        values[:, 1],
        values[:, 2],
        state_time=state_time,
        available_time=available_time,
        method=method,
        status=status,
        valid=valid,
        causal=causal,
        source_state_time=source_state_time,
        prediction_horizon=horizon,
        metadata=metadata,
    )


def synchronize_axis_posteriors(
    posteriors: list[TimedState] | tuple[TimedState, ...],
    *,
    control_time: float,
) -> TimedState:
    """Causally propagate scalar posteriors to one common controller time.

    Each input must be a one-axis posterior that was available no later than
    ``control_time`` and represents no future physical state.  Constant-
    acceleration propagation is explicit; source timestamps remain in metadata
    and are never overwritten with the newest axis timestamp.
    """

    target_time = _time(control_time, "control_time")
    if not posteriors:
        raise ValueError("cannot synchronize an empty posterior set")
    position: list[float] = []
    velocity: list[float] = []
    acceleration: list[float] = []
    axis_source_times: list[float] = []
    axis_available_times: list[float] = []
    for axis, posterior in enumerate(posteriors):
        if posterior.dof != 1 or posterior.is_prediction:
            raise ValueError(f"axis {axis} must be a scalar estimator posterior")
        if posterior.state_time > target_time + 1e-12:
            raise ValueError(f"axis {axis} posterior is in the future")
        if posterior.available_time > target_time + 1e-12:
            raise ValueError(f"axis {axis} posterior was not yet available")
        propagation = max(0.0, target_time - posterior.state_time)
        p0 = float(posterior.position[0])
        v0 = float(posterior.velocity[0])
        a0 = float(posterior.acceleration[0])
        position.append(p0 + v0 * propagation + 0.5 * a0 * propagation**2)
        velocity.append(v0 + a0 * propagation)
        acceleration.append(a0)
        axis_source_times.append(float(posterior.state_time))
        axis_available_times.append(float(posterior.available_time))
    return TimedState(
        np.asarray(position),
        np.asarray(velocity),
        np.asarray(acceleration),
        state_time=target_time,
        available_time=target_time,
        method=PER_AXIS_CAUSAL_SYNC,
        status="synchronized"
        if all(item.valid for item in posteriors)
        else "held_axis",
        valid=all(item.valid for item in posteriors),
        startup=any(item.startup for item in posteriors),
        causal=all(item.causal for item in posteriors),
        compute_time_us=sum(float(item.compute_time_us) for item in posteriors),
        metadata={
            "measurement_sync_method": PER_AXIS_CAUSAL_SYNC,
            "axis_posterior_source_times": axis_source_times,
            "axis_posterior_available_times": axis_available_times,
            "control_time": target_time,
        },
    )


@dataclass(frozen=True)
class FrontEndCycle:
    """Estimator/predictor result with both requested and actual horizon."""

    measurement: Measurement
    control_time: float
    target_time: float
    requested_horizon: float
    posterior: TimedState
    prediction: TimedState
    raw_target: TimedState

    @property
    def posterior_lag(self) -> float:
        return self.control_time - self.posterior.state_time

    @property
    def propagation_horizon(self) -> float:
        assert self.prediction.prediction_horizon is not None
        return self.prediction.prediction_horizon


class EstimatorPredictorPipeline:
    """Strict causal front end, usable independently of Ruckig.

    ``requested_horizon`` is measured from ``control_time``.  If an estimator
    posterior is delayed, the predictor receives the longer, explicit
    ``target_time - posterior.state_time`` propagation interval.  No plotting
    shift or timestamp rewrite is used to conceal the delay.
    """

    def __init__(
        self,
        estimator: Estimator,
        predictor: Predictor,
        *,
        prediction_horizon: float = 0.0,
        target_components: str = "pva",
    ) -> None:
        if not isinstance(estimator, Estimator):
            raise TypeError("estimator must implement otg_lab.estimators.Estimator")
        if not isinstance(predictor, Predictor):
            raise TypeError("predictor must implement otg_lab.predictors.Predictor")
        self.estimator = estimator
        self.predictor = predictor
        self.prediction_horizon = float(prediction_horizon)
        if not np.isfinite(self.prediction_horizon) or self.prediction_horizon < 0.0:
            raise ValueError("prediction_horizon must be finite and non-negative")
        normalized = target_components.lower()
        if normalized not in {"p", "pv", "pva"}:
            raise ValueError("target_components must be p, pv, or pva")
        self.target_components = normalized

    def reset(self) -> None:
        self.estimator.reset()
        self.predictor.reset()

    def process(
        self,
        measurement: Measurement,
        *,
        control_time: float | None = None,
        prediction_horizon: float | None = None,
        target_time: float | None = None,
        target_components: str | None = None,
    ) -> FrontEndCycle:
        """Produce one posterior and a prediction at an exact physical time.

        Set either ``target_time`` or ``prediction_horizon``.  If both are
        omitted the configured horizon is used.  Supplying both is rejected so
        contradictory timestamp semantics cannot be silently resolved.
        """

        axis_source_times = measurement.metadata.get("axis_source_times")
        if axis_source_times is not None:
            times = np.asarray(axis_source_times, dtype=float)
            if times.shape != (measurement.dof,) or not np.all(np.isfinite(times)):
                raise ValueError(
                    "axis_source_times must contain one finite time per axis"
                )
            if not np.allclose(times, times[0], rtol=0.0, atol=1e-12):
                raise ValueError(
                    "asynchronous vector Measurement is forbidden; estimate each axis "
                    "independently and call synchronize_axis_posteriors"
                )
        if control_time is None:
            control_time = measurement.available_time
        control_time = _time(control_time, "control_time")
        if control_time < measurement.available_time:
            raise ValueError("control_time cannot precede measurement available_time")
        if target_time is not None and prediction_horizon is not None:
            raise ValueError("set target_time or prediction_horizon, not both")
        if target_time is None:
            requested = (
                self.prediction_horizon
                if prediction_horizon is None
                else float(prediction_horizon)
            )
            if not np.isfinite(requested) or requested < 0.0:
                raise ValueError("prediction_horizon must be finite and non-negative")
            target_time = control_time + requested
        else:
            target_time = _time(target_time, "target_time")
            requested = target_time - control_time
            if requested < 0.0:
                raise ValueError("target_time cannot precede control_time")

        posterior = self.estimator.update(measurement)
        propagation_horizon = target_time - posterior.state_time
        tolerance = (
            32.0
            * np.finfo(float).eps
            * max(
                1.0,
                abs(target_time),
                abs(posterior.state_time),
            )
        )
        if propagation_horizon < -tolerance:
            raise ValueError(
                "target_time precedes posterior state_time; cannot perform a "
                "future prediction"
            )
        propagation_horizon = max(0.0, propagation_horizon)
        prediction = self.predictor.predict(posterior, propagation_horizon)
        if abs(prediction.state_time - target_time) > tolerance:
            raise RuntimeError(
                "predictor violated timestamp contract: prediction_time "
                f"{prediction.state_time}, requested {target_time}"
            )
        prediction = prediction.with_updates(
            available_time=control_time,
            metadata={
                **dict(prediction.metadata),
                "control_time": control_time,
                "target_time": target_time,
                "requested_horizon": requested,
                "posterior_lag_at_control": control_time - posterior.state_time,
                "propagation_horizon": propagation_horizon,
            },
        )
        components = (
            self.target_components if target_components is None else target_components
        )
        raw_target = select_target_components(prediction, components)
        return FrontEndCycle(
            measurement=measurement,
            control_time=control_time,
            target_time=target_time,
            requested_horizon=requested,
            posterior=posterior,
            prediction=prediction,
            raw_target=raw_target,
        )


@dataclass(frozen=True)
class PipelineCycle:
    """One fully time-stamped end-to-end control cycle."""

    front_end: FrontEndCycle
    executable_target: TimedState | None
    command: TimedState
    plant_state: TimedState
    governor_result: Any | None
    follower_result: Any
    plant_result: Any | None
    estimator_compute_us: float
    predictor_compute_us: float
    governor_compute_us: float
    follower_compute_us: float
    plant_compute_us: float
    total_compute_us: float

    @property
    def posterior(self) -> TimedState:
        return self.front_end.posterior

    @property
    def prediction(self) -> TimedState:
        return self.front_end.prediction

    @property
    def raw_target(self) -> TimedState:
        return self.front_end.raw_target


@dataclass(frozen=True)
class ReplanningStateSelection:
    """Authoritative previous-command/measured/hybrid feedback decision."""

    state: NDArray[np.float64]
    measured_delta: NDArray[np.float64]
    correction: NDArray[np.float64]
    correction_applied: NDArray[np.bool_]
    divergence: NDArray[np.float64]
    divergence_exceeded: NDArray[np.bool_]
    reason: str


def select_replanning_state(
    mode: str,
    command_state: NDArray[np.float64],
    measured_state: NDArray[np.float64],
    threshold: float,
) -> ReplanningStateSelection:
    """Select controller state without conflating feedback with a reset.

    Both :class:`TrackingPipeline` and the canonical batch runner call this
    implementation so their plant/current-state semantics cannot drift.
    """

    if not np.isfinite(threshold) or threshold < 0.0:
        raise ValueError(
            "feedback divergence threshold must be finite and non-negative"
        )
    measured_delta = measured_state - command_state
    divergence = np.max(np.abs(measured_delta), axis=1)
    divergence_exceeded = divergence > threshold
    if mode == "previous_command":
        selected = command_state.copy()
        reason = "previous_command"
    elif mode == "measured":
        selected = measured_state.copy()
        reason = "measured_state"
    elif mode == "hybrid":
        if np.any(divergence_exceeded):
            selected = measured_state.copy()
            reason = "hybrid_threshold_exceeded"
        else:
            selected = command_state.copy()
            reason = "hybrid_below_threshold"
    else:
        raise ValueError(f"unknown measured_state_mode {mode!r}")
    correction = selected - command_state
    correction_applied = np.any(correction != 0.0, axis=1)
    return ReplanningStateSelection(
        state=selected,
        measured_delta=measured_delta,
        correction=correction,
        correction_applied=correction_applied,
        divergence=divergence,
        divergence_exceeded=divergence_exceeded,
        reason=reason,
    )


class TrackingPipeline:
    """Thin single-cycle facade over the authoritative shared semantics.

    Formal artifact execution remains :func:`otg_lab.runner.run_pipeline_rows`.
    This facade shares its replanning-state rule and v2 layer meanings; it does
    not maintain an alternative interpretation of executable targets or safe
    fallbacks.
    """

    def __init__(
        self,
        estimator: Estimator,
        predictor: Predictor,
        follower: FollowerProtocol,
        *,
        dof: int,
        dt: float,
        prediction_horizon: float = 0.0,
        target_components: str = "pva",
        governor: GovernorProtocol | None = None,
        plant: PlantProtocol | None = None,
        measured_state_mode: str = "previous_command",
        divergence_threshold: float = 0.05,
    ) -> None:
        if int(dof) != dof or dof < 1:
            raise ValueError("dof must be a positive integer")
        if not np.isfinite(dt) or dt <= 0.0:
            raise ValueError("dt must be finite and positive")
        self.dof = int(dof)
        self.dt = float(dt)
        self.front_end = EstimatorPredictorPipeline(
            estimator,
            predictor,
            prediction_horizon=prediction_horizon,
            target_components=target_components,
        )
        self.estimator = estimator
        self.predictor = predictor
        self.governor = governor
        self.follower = follower
        self.plant = plant
        if measured_state_mode not in {"previous_command", "measured", "hybrid"}:
            raise ValueError(
                "measured_state_mode must be previous_command, measured, or hybrid"
            )
        if not np.isfinite(divergence_threshold) or divergence_threshold < 0.0:
            raise ValueError("divergence_threshold must be finite and non-negative")
        self.measured_state_mode = measured_state_mode
        self.divergence_threshold = float(divergence_threshold)
        for label, component in (
            ("governor", governor),
            ("follower", follower),
            ("plant", plant),
        ):
            if component is None:
                continue
            component_dof = getattr(component, "dof", self.dof)
            component_dt = getattr(component, "dt", self.dt)
            if int(component_dof) != self.dof:
                raise ValueError(f"{label} DoF differs from pipeline DoF")
            if not np.isclose(component_dt, self.dt, rtol=0.0, atol=1e-15):
                raise ValueError(f"{label} dt differs from pipeline dt")
        self._current_state: NDArray[np.float64] | None = None
        self._command_state: NDArray[np.float64] | None = None
        self._measured_state: NDArray[np.float64] | None = None

    def reset(
        self,
        current_state: TimedState | NDArray[np.float64] | None = None,
        *,
        state_time: float = 0.0,
    ) -> None:
        self.front_end.reset()
        self._current_state = (
            None if current_state is None else _state_matrix(current_state, self.dof)
        )
        self._command_state = (
            None if self._current_state is None else self._current_state.copy()
        )
        self._measured_state = (
            None if self._current_state is None else self._current_state.copy()
        )
        if self.governor is not None:
            self.governor.reset(self._current_state)
        if self._current_state is not None:
            self.follower.reset(self._current_state)
            if self.plant is not None:
                # Ideal and delayed plants intentionally have slightly
                # different reset signatures. Select by the public signature,
                # without depending on implementation-private attributes.
                reset_parameters = inspect.signature(self.plant.reset).parameters
                if "state_time" in reset_parameters:
                    self.plant.reset(self._current_state, state_time=state_time)
                else:
                    self.plant.reset(self._current_state)

    def _qp_reference_sequence(
        self,
        posterior: TimedState,
        first_prediction: TimedState,
        first_target_time: float,
        steps: int,
        components: str,
    ) -> tuple[NDArray[np.float64], float]:
        target_times = first_target_time + np.arange(steps) * self.dt
        horizons = target_times - posterior.state_time
        if np.any(horizons < -1e-15):
            raise ValueError("QP reference sequence asks predictor for the past")
        predictions = [first_prediction]
        if steps > 1:
            predictions.extend(
                self.predictor.predict_sequence(
                    posterior,
                    np.maximum(horizons[1:], 0.0),
                )
            )
        selected = [select_target_components(item, components) for item in predictions]
        additional_compute_us = sum(item.compute_time_us for item in predictions[1:])
        return (
            np.stack([item.as_array() for item in selected]),
            additional_compute_us,
        )

    def step(
        self,
        measurement: Measurement,
        *,
        control_time: float | None = None,
        current_state: TimedState | NDArray[np.float64] | None = None,
        prediction_horizon: float | None = None,
        target_time: float | None = None,
        target_components: str | None = None,
    ) -> PipelineCycle:
        if measurement.dof != self.dof:
            raise ValueError("measurement DoF differs from pipeline DoF")
        if current_state is not None:
            current = _state_matrix(current_state, self.dof)
        elif self._command_state is not None and self._measured_state is not None:
            current = select_replanning_state(
                self.measured_state_mode,
                self._command_state,
                self._measured_state,
                self.divergence_threshold,
            ).state
        else:
            raise RuntimeError(
                "TrackingPipeline requires reset(current_state) or an explicit "
                "current_state on the first step; measurement position is not a "
                "valid implicit p/v/a replanning state"
            )

        front = self.front_end.process(
            measurement,
            control_time=control_time,
            prediction_horizon=prediction_horizon,
            target_time=target_time,
            target_components=target_components,
        )
        control_time_value = front.control_time
        components = (
            self.front_end.target_components
            if target_components is None
            else target_components.lower()
        )

        governor_result = None
        governor_compute_us = 0.0
        predictor_compute_us = front.prediction.compute_time_us
        if self.governor is None:
            # No governor means no separately materialized executable target.
            # The raw request is passed directly to the follower, matching the
            # canonical runner and otg.sample.v3 availability semantics.
            executable_target = None
            executable_values = front.raw_target.as_array()
        else:
            if hasattr(self.governor, "horizon_steps"):
                sequence, sequence_predictor_us = self._qp_reference_sequence(
                    front.posterior,
                    front.prediction,
                    front.target_time,
                    int(self.governor.horizon_steps),
                    components,
                )
                predictor_compute_us += sequence_predictor_us
                governor_result = self.governor.update(
                    sequence,
                    control_time=control_time_value,
                    current_state=current,
                )
            else:
                governor_result = self.governor.update(
                    front.raw_target.as_array(),
                    control_time=control_time_value,
                    current_state=current,
                )
            governor_compute_us = float(governor_result.compute_us)
            executable_values = np.asarray(
                governor_result.executable_state,
                dtype=float,
            )
            executable_target = _prediction_state(
                executable_values,
                state_time=float(governor_result.target_time),
                available_time=control_time_value,
                source_state_time=control_time_value,
                method=str(getattr(self.governor, "name", "governor")),
                status=str(governor_result.solver_status),
                valid=bool(
                    getattr(
                        governor_result,
                        "safety_guarantee",
                        getattr(governor_result, "target_feasible", False),
                    )
                    and getattr(governor_result, "command_segment_feasible", True)
                    and getattr(governor_result, "command_terminal_viable", True)
                ),
                causal=front.raw_target.causal,
                metadata={
                    "target_projected": bool(governor_result.target_projected),
                    "fallback": bool(governor_result.fallback),
                    "fallback_reason": str(governor_result.fallback_reason),
                    "iterations": int(governor_result.iterations),
                    "raw_target_time": front.raw_target.state_time,
                },
            )

        follower_result = self.follower.update(
            executable_values,
            control_time=control_time_value,
            current_state=current,
        )
        command_values = np.asarray(follower_result.command_state, dtype=float)
        follower_fallback = bool(getattr(follower_result, "fallback", False))
        fallback_requested = bool(
            getattr(follower_result, "fallback_requested", follower_fallback)
        )
        fallback_applied = bool(
            getattr(follower_result, "fallback_applied", follower_fallback)
        )
        follower_safety = bool(
            getattr(follower_result, "safety_guarantee", not follower_fallback)
        )
        command = _prediction_state(
            command_values,
            state_time=float(follower_result.command_time),
            available_time=control_time_value,
            source_state_time=control_time_value,
            method=str(getattr(self.follower, "name", "follower")),
            status=str(follower_result.solver_status),
            valid=follower_safety,
            causal=(
                front.raw_target.causal
                if executable_target is None
                else executable_target.causal
            ),
            metadata={
                "fallback": fallback_applied,
                "fallback_requested": fallback_requested,
                "fallback_applied": fallback_applied,
                "fallback_reason": str(follower_result.fallback_reason),
                "safety_guarantee": follower_safety,
                "emergency_mode": bool(
                    getattr(follower_result, "emergency_mode", False)
                ),
                "command_segment_feasible": bool(
                    getattr(follower_result, "command_segment_feasible", follower_safety)
                ),
                "command_terminal_viable": bool(
                    getattr(follower_result, "command_terminal_viable", follower_safety)
                ),
                "target_projected": bool(follower_result.target_projected),
                "free_trajectory_duration": float(
                    follower_result.free_trajectory_duration
                ),
                "frozen_trajectory_duration": float(
                    follower_result.frozen_trajectory_duration
                ),
            },
        )

        plant_result = None
        plant_compute_us = 0.0
        if self.plant is None:
            plant_values = command_values
            plant_state = TimedState(
                plant_values[:, 0],
                plant_values[:, 1],
                plant_values[:, 2],
                state_time=command.state_time,
                available_time=command.state_time,
                method="implicit_ideal_plant",
                causal=command.causal,
            )
        else:
            plant_result = self.plant.update(
                command_values,
                command_time=command.state_time,
            )
            plant_compute_us = float(plant_result.compute_us)
            plant_values = np.asarray(plant_result.measured_state, dtype=float)
            plant_state = TimedState(
                plant_values[:, 0],
                plant_values[:, 1],
                plant_values[:, 2],
                state_time=float(plant_result.state_time),
                available_time=float(plant_result.available_time),
                method=str(getattr(self.plant, "name", "plant")),
                status=str(plant_result.status),
                causal=command.causal,
                metadata={
                    "saturated": np.asarray(plant_result.saturated).tolist(),
                    "delayed_command_age": float(plant_result.delayed_command_age),
                },
            )
        self._command_state = command_values.copy()
        self._measured_state = plant_values.copy()
        self._current_state = current.copy()

        estimator_us = front.posterior.compute_time_us
        follower_us = float(follower_result.compute_us)
        total_us = (
            estimator_us
            + predictor_compute_us
            + governor_compute_us
            + follower_us
            + plant_compute_us
        )
        return PipelineCycle(
            front_end=front,
            executable_target=executable_target,
            command=command,
            plant_state=plant_state,
            governor_result=governor_result,
            follower_result=follower_result,
            plant_result=plant_result,
            estimator_compute_us=estimator_us,
            predictor_compute_us=predictor_compute_us,
            governor_compute_us=governor_compute_us,
            follower_compute_us=follower_us,
            plant_compute_us=plant_compute_us,
            total_compute_us=total_us,
        )


__all__ = [
    "PER_AXIS_CAUSAL_SYNC",
    "EstimatorPredictorPipeline",
    "FollowerProtocol",
    "FrontEndCycle",
    "GovernorProtocol",
    "PipelineCycle",
    "PlantProtocol",
    "ReplanningStateSelection",
    "TrackingPipeline",
    "select_replanning_state",
    "synchronize_axis_posteriors",
]
