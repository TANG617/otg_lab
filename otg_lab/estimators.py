"""Strictly causal, time-explicit state estimators.

Unlike the historical top-level :mod:`estimators` module, classes here never
extrapolate a posterior to a command or target time.  Every ``update`` consumes
one :class:`~otg_lab.types.Measurement` and returns a posterior whose
``state_time`` is no later than that measurement's time.  Future propagation
lives exclusively in :mod:`otg_lab.predictors`.
"""

from __future__ import annotations

import copy
import re
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable
from math import factorial
from time import perf_counter_ns
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .types import Measurement, TimedState


class EstimatorError(RuntimeError):
    """Base error raised by the estimator layer."""


class TimestampError(EstimatorError, ValueError):
    """Raised for duplicated, regressing, or unsupported timestamps."""


class NonFiniteMeasurementError(EstimatorError, ValueError):
    """Raised when a configured non-finite policy cannot produce a state."""


def _positive_float(value: float, name: str) -> float:
    result = float(value)
    if not np.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _limit_vector(value: ArrayLike, dof: int, name: str) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=float)
    if array.ndim == 0:
        array = np.full(dof, float(array))
    if array.shape != (dof,):
        raise ValueError(f"{name} must be scalar or have shape ({dof},)")
    if not np.all(np.isfinite(array)) or np.any(array <= 0.0):
        raise ValueError(f"{name} must contain finite positive values")
    return array


def _ca_propagate(
    position: NDArray[np.float64],
    velocity: NDArray[np.float64],
    acceleration: NDArray[np.float64],
    dt: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    return (
        position + velocity * dt + 0.5 * acceleration * dt * dt,
        velocity + acceleration * dt,
        acceleration.copy(),
    )


class Estimator(ABC):
    """Base class implementing timing, reset, and invalid-input policies.

    Parameters
    ----------
    nominal_dt:
        Optional expected period.  Variable time steps are still used unless
        ``allow_variable_dt`` is false.
    nonfinite_policy:
        ``"raise"``; ``"hold"``/``"drop"`` (return a stale posterior);
        ``"component_hold"`` (replace only invalid positions); or ``"reset"``
        (clear algorithm memory and return the previous state once).
    timestamp_policy:
        ``"raise"``; ``"hold"``/``"ignore"``; or ``"reset"`` for a source
        timestamp that does not strictly increase.
    """

    name = "estimator"
    causal = True

    def __init__(
        self,
        nominal_dt: float | None = None,
        *,
        dt: float | None = None,
        allow_variable_dt: bool = True,
        dt_tolerance: float = 1e-6,
        nonfinite_policy: str = "hold",
        timestamp_policy: str = "raise",
    ) -> None:
        if dt is not None:
            if nominal_dt is not None:
                raise ValueError("set only one of dt or nominal_dt")
            nominal_dt = dt
        self.nominal_dt = (
            None if nominal_dt is None else _positive_float(nominal_dt, "nominal_dt")
        )
        self.allow_variable_dt = bool(allow_variable_dt)
        self.dt_tolerance = _positive_float(dt_tolerance, "dt_tolerance")
        if nonfinite_policy == "drop":
            nonfinite_policy = "hold"
        if nonfinite_policy not in {
            "raise",
            "hold",
            "component_hold",
            "reset",
        }:
            raise ValueError(
                "nonfinite_policy must be one of raise, hold, component_hold, reset"
            )
        if timestamp_policy == "ignore":
            timestamp_policy = "hold"
        if timestamp_policy not in {"raise", "hold", "reset"}:
            raise ValueError("timestamp_policy must be one of raise, hold, reset")
        self.nonfinite_policy = nonfinite_policy
        self.timestamp_policy = timestamp_policy
        self.compute_us: list[float] = []
        self._last_measurement: Measurement | None = None
        self._last_posterior: TimedState | None = None
        self._dof: int | None = None
        self._sample_count = 0

    @property
    def dof(self) -> int | None:
        return self._dof

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def ready(self) -> bool:
        return self._last_posterior is not None and not self._last_posterior.startup

    def reset(self) -> None:
        """Clear all estimator memory and timing samples."""

        self.compute_us.clear()
        self._last_measurement = None
        self._last_posterior = None
        self._dof = None
        self._sample_count = 0
        self._reset_impl()

    def _reset_impl(self) -> None:
        """Subclass hook for clearing algorithm-specific state."""

        return None

    def update(self, measurement: Measurement) -> TimedState:
        """Consume one available measurement and return a causal posterior."""

        started = perf_counter_ns()
        if not isinstance(measurement, Measurement):
            raise TypeError("update expects an otg_lab.types.Measurement")

        reset_due_to_timestamp = False
        if self._last_measurement is not None:
            source_dt = measurement.state_time - self._last_measurement.state_time
            arrival_dt = (
                measurement.available_time - self._last_measurement.available_time
            )
            if source_dt <= 0.0 or arrival_dt < 0.0:
                reason = (
                    "non-increasing state_time"
                    if source_dt <= 0.0
                    else "regressing available_time"
                )
                if self.timestamp_policy == "raise":
                    raise TimestampError(
                        f"{reason}: previous=({self._last_measurement.state_time}, "
                        f"{self._last_measurement.available_time}), current="
                        f"({measurement.state_time}, {measurement.available_time})"
                    )
                if self.timestamp_policy == "hold":
                    posterior = self._held_posterior(
                        measurement,
                        status="timestamp_ignored",
                    )
                    return self._finish(posterior, started, remember=False)
                self.reset()
                reset_due_to_timestamp = True

        if self._dof is None:
            self._dof = measurement.dof
        elif measurement.dof != self._dof:
            raise ValueError(
                f"measurement DoF changed from {self._dof} to "
                f"{measurement.dof}; call reset() first"
            )

        finite_position = bool(np.all(np.isfinite(measurement.position)))
        if not finite_position:
            if self.nonfinite_policy == "raise":
                raise NonFiniteMeasurementError(
                    "measurement position contains NaN or infinity"
                )
            if self._last_posterior is None:
                raise NonFiniteMeasurementError(
                    "cannot apply non-finite hold/reset policy before the "
                    "first valid measurement"
                )
            if self.nonfinite_policy == "reset":
                previous = self._last_posterior
                self.reset()
                held = previous.with_updates(
                    available_time=measurement.available_time,
                    status="nonfinite_reset",
                    valid=False,
                    compute_time_us=0.0,
                    metadata={
                        **dict(previous.metadata),
                        "invalid_position_mask": (
                            ~np.isfinite(measurement.position)
                        ).tolist(),
                    },
                )
                return self._finish(held, started, remember=False)
            if self.nonfinite_policy == "hold":
                posterior = self._held_posterior(
                    measurement,
                    status="nonfinite_hold",
                )
                return self._finish(posterior, started, remember=False)

            # component_hold advances time using the last valid value for only
            # the corrupt axes.  The substitution is explicit in metadata.
            assert self._last_measurement is not None
            repaired = np.where(
                np.isfinite(measurement.position),
                measurement.position,
                self._last_measurement.position,
            )
            measurement = Measurement(
                position=repaired,
                state_time=measurement.state_time,
                available_time=measurement.available_time,
                velocity=measurement.velocity,
                acceleration=measurement.acceleration,
                metadata={
                    **dict(measurement.metadata),
                    "invalid_position_mask": (
                        ~np.isfinite(measurement.position)
                    ).tolist(),
                },
            )

        dt = None
        if self._last_measurement is not None:
            dt = measurement.state_time - self._last_measurement.state_time
            if (
                self.nominal_dt is not None
                and not self.allow_variable_dt
                and not np.isclose(
                    dt,
                    self.nominal_dt,
                    rtol=self.dt_tolerance,
                    atol=self.dt_tolerance * self.nominal_dt,
                )
            ):
                raise TimestampError(
                    f"{self.name} is fixed-dt: expected {self.nominal_dt}, got {dt}"
                )

        posterior = self._update_valid(measurement, dt)
        if not isinstance(posterior, TimedState):
            raise TypeError("estimator implementation did not return TimedState")
        if posterior.dof != measurement.dof:
            raise EstimatorError("posterior DoF differs from measurement DoF")
        if posterior.is_prediction:
            raise EstimatorError(
                "an Estimator must return a posterior, not a prediction"
            )
        tolerance = (
            32.0
            * np.finfo(float).eps
            * max(
                1.0,
                abs(posterior.state_time),
                abs(measurement.state_time),
            )
        )
        if posterior.state_time > measurement.state_time + tolerance:
            raise EstimatorError(
                "causal estimator returned a future state: "
                f"{posterior.state_time} > {measurement.state_time}"
            )
        if posterior.available_time != measurement.available_time:
            raise EstimatorError(
                "posterior available_time must equal the triggering "
                "measurement available_time"
            )

        if reset_due_to_timestamp:
            posterior = posterior.with_updates(
                status="timestamp_reset",
                metadata={
                    **dict(posterior.metadata),
                    "timestamp_reset": True,
                },
            )
        elif measurement.metadata.get("invalid_position_mask") is not None:
            posterior = posterior.with_updates(
                status="nonfinite_component_hold",
                valid=False,
                metadata={
                    **dict(posterior.metadata),
                    "invalid_position_mask": measurement.metadata[
                        "invalid_position_mask"
                    ],
                },
            )

        self._last_measurement = measurement
        self._sample_count += 1
        return self._finish(posterior, started, remember=True)

    def _held_posterior(
        self,
        measurement: Measurement,
        *,
        status: str,
    ) -> TimedState:
        if self._last_posterior is None:
            raise EstimatorError(f"cannot {status} before a valid posterior")
        metadata = dict(self._last_posterior.metadata)
        metadata.update(
            {
                "held_for_measurement_state_time": measurement.state_time,
                "invalid_position_mask": (~np.isfinite(measurement.position)).tolist(),
            }
        )
        return self._last_posterior.with_updates(
            available_time=measurement.available_time,
            status=status,
            valid=False,
            compute_time_us=0.0,
            metadata=metadata,
        )

    def _finish(
        self,
        posterior: TimedState,
        started_ns: int,
        *,
        remember: bool,
    ) -> TimedState:
        elapsed_us = (perf_counter_ns() - started_ns) / 1000.0
        result = posterior.with_updates(
            method=posterior.method or self.name,
            compute_time_us=elapsed_us,
        )
        self.compute_us.append(elapsed_us)
        if remember:
            self._last_posterior = result
        return result

    @abstractmethod
    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        raise NotImplementedError


class PositionOnly(Estimator):
    """Current measured position with zero derivative posterior."""

    name = "position_only"

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        zeros = np.zeros(measurement.dof)
        return TimedState(
            measurement.position,
            zeros,
            zeros,
            state_time=measurement.state_time,
            available_time=measurement.available_time,
            method=self.name,
            startup=False,
        )


class RawBackwardDifference(Estimator):
    """Raw first/second backward differences at the newest position sample.

    This intentionally retains the classical derivative time misalignment as
    a baseline.  ``metadata['derivative_alignment']`` makes that limitation
    explicit instead of compensating with hidden prediction.
    """

    name = "raw_backward_difference"

    def __init__(self, nominal_dt: float | None = None, **kwargs: Any) -> None:
        super().__init__(nominal_dt, **kwargs)
        self._history: deque[tuple[float, NDArray[np.float64]]] = deque(maxlen=3)

    def _reset_impl(self) -> None:
        self._history.clear()

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        self._history.append(
            (measurement.state_time, np.array(measurement.position, copy=True))
        )
        count = len(self._history)
        velocity = np.zeros(measurement.dof)
        acceleration = np.zeros(measurement.dof)
        velocity_time = measurement.state_time
        acceleration_time = measurement.state_time
        if count >= 2:
            t1, p1 = self._history[-2]
            t2, p2 = self._history[-1]
            h2 = t2 - t1
            velocity = (p2 - p1) / h2
            velocity_time = 0.5 * (t1 + t2)
        if count >= 3:
            t0, p0 = self._history[-3]
            t1, p1 = self._history[-2]
            t2, p2 = self._history[-1]
            h1 = t1 - t0
            h2 = t2 - t1
            acceleration = 2.0 * ((p2 - p1) / h2 - (p1 - p0) / h1) / (h1 + h2)
            acceleration_time = t1
        return TimedState(
            measurement.position,
            velocity,
            acceleration,
            state_time=measurement.state_time,
            available_time=measurement.available_time,
            method=self.name,
            status="startup" if count < 3 else "ok",
            startup=count < 3,
            metadata={
                "derivative_alignment": "raw_backward_misaligned",
                "velocity_time": velocity_time,
                "acceleration_time": acceleration_time,
                "samples_used": count,
            },
        )


class DelayOneCenteredDifference(Estimator):
    """Three-point quadratic derivative posterior delayed by one sample.

    At update ``k`` (once initialized), the posterior belongs to sample
    ``k-1`` and is only available when sample ``k`` arrives.  No forward
    extrapolation is performed.
    """

    name = "delay_one_centered_difference"
    lag_samples = 1

    def __init__(self, nominal_dt: float | None = None, **kwargs: Any) -> None:
        super().__init__(nominal_dt, **kwargs)
        self._history: deque[tuple[float, NDArray[np.float64]]] = deque(maxlen=3)

    def _reset_impl(self) -> None:
        self._history.clear()

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        self._history.append(
            (measurement.state_time, np.array(measurement.position, copy=True))
        )
        count = len(self._history)
        if count < 3:
            state_time, position = self._history[0]
            velocity = np.zeros(measurement.dof)
            acceleration = np.zeros(measurement.dof)
            return TimedState(
                position,
                velocity,
                acceleration,
                state_time=state_time,
                available_time=measurement.available_time,
                method=self.name,
                status="startup",
                startup=True,
                metadata={
                    "lag_samples": min(self.lag_samples, count - 1),
                    "lag_seconds": measurement.state_time - state_time,
                    "samples_used": count,
                },
            )

        times = np.array([item[0] for item in self._history])
        values = np.vstack([item[1] for item in self._history])
        state_time = float(times[1])
        offsets = times - state_time
        design = np.column_stack((np.ones(3), offsets, offsets**2))
        coefficients = np.linalg.solve(design, values)
        return TimedState(
            coefficients[0],
            coefficients[1],
            2.0 * coefficients[2],
            state_time=state_time,
            available_time=measurement.available_time,
            method=self.name,
            metadata={
                "lag_samples": self.lag_samples,
                "lag_seconds": measurement.state_time - state_time,
                "samples_used": 3,
            },
        )


class CausalLocalPolynomial(Estimator):
    """Causal fixed-window local polynomial posterior with explicit lag."""

    name = "causal_local_polynomial"

    def __init__(
        self,
        nominal_dt: float | None = None,
        *,
        window: int = 5,
        degree: int = 2,
        lag_samples: int = 0,
        lag: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(nominal_dt, **kwargs)
        if lag is not None:
            if lag_samples != 0:
                raise ValueError("set only one of lag or lag_samples")
            lag_samples = lag
        if int(window) not in {5, 7, 9, 11}:
            raise ValueError("window must be one of 5, 7, 9, 11")
        if int(degree) not in {2, 3}:
            raise ValueError("degree must be 2 or 3")
        if int(lag_samples) != lag_samples or not 0 <= lag_samples < window:
            raise ValueError("lag_samples must be an integer in [0, window)")
        self.window = int(window)
        self.degree = int(degree)
        self.lag_samples = int(lag_samples)
        self.name = f"local_poly_w{self.window}_d{self.degree}_lag{self.lag_samples}"
        self._history: deque[tuple[float, NDArray[np.float64]]] = deque(
            maxlen=self.window
        )

    def _reset_impl(self) -> None:
        self._history.clear()

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        self._history.append(
            (measurement.state_time, np.array(measurement.position, copy=True))
        )
        times = np.array([item[0] for item in self._history], dtype=float)
        values = np.vstack([item[1] for item in self._history])
        effective_lag = min(self.lag_samples, len(self._history) - 1)
        evaluation_index = len(self._history) - 1 - effective_lag
        state_time = float(times[evaluation_index])
        offsets = times - state_time
        effective_degree = min(self.degree, len(times) - 1)
        design = np.column_stack(
            [offsets**power for power in range(effective_degree + 1)]
        )
        coefficients, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
        position = coefficients[0]
        velocity = (
            coefficients[1] if effective_degree >= 1 else np.zeros(measurement.dof)
        )
        acceleration = (
            2.0 * coefficients[2]
            if effective_degree >= 2
            else np.zeros(measurement.dof)
        )
        jerk = (
            6.0 * coefficients[3]
            if effective_degree >= 3
            else np.zeros(measurement.dof)
        )
        startup = len(self._history) < self.window
        return TimedState(
            position,
            velocity,
            acceleration,
            jerk=jerk,
            state_time=state_time,
            available_time=measurement.available_time,
            method=self.name,
            status="startup" if startup else "ok",
            startup=startup,
            metadata={
                "window": self.window,
                "degree": self.degree,
                "effective_degree": effective_degree,
                "lag_samples": effective_lag,
                "configured_lag_samples": self.lag_samples,
                "lag_seconds": measurement.state_time - state_time,
                "samples_used": len(self._history),
            },
        )


class AlphaBetaGamma(Estimator):
    """Causal alpha-beta-gamma filter evaluated at measurement time."""

    name = "alpha_beta_gamma"

    def __init__(
        self,
        nominal_dt: float | None = None,
        *,
        alpha: float = 0.401,
        beta: float = 0.11528,
        gamma: float = 0.009504,
        **kwargs: Any,
    ) -> None:
        super().__init__(nominal_dt, **kwargs)
        for value, label in (
            (alpha, "alpha"),
            (beta, "beta"),
            (gamma, "gamma"),
        ):
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{label} must be finite and non-negative")
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.gamma = float(gamma)
        self._state: NDArray[np.float64] | None = None

    def _reset_impl(self) -> None:
        self._state = None

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        if self._state is None:
            self._state = np.column_stack(
                (
                    measurement.position,
                    np.zeros(measurement.dof),
                    np.zeros(measurement.dof),
                )
            )
        else:
            assert dt is not None
            p, v, a = _ca_propagate(
                self._state[:, 0],
                self._state[:, 1],
                self._state[:, 2],
                dt,
            )
            residual = measurement.position - p
            p = p + self.alpha * residual
            v = v + self.beta * residual / dt
            a = a + 2.0 * self.gamma * residual / (dt * dt)
            self._state = np.column_stack((p, v, a))
        startup = self._sample_count < 2
        return TimedState(
            self._state[:, 0],
            self._state[:, 1],
            self._state[:, 2],
            state_time=measurement.state_time,
            available_time=measurement.available_time,
            method=self.name,
            status="startup" if startup else "ok",
            startup=startup,
            metadata={"dt_used": dt},
        )


def _ca_transition(dt: float) -> NDArray[np.float64]:
    return np.array(
        [
            [1.0, dt, 0.5 * dt**2],
            [0.0, 1.0, dt],
            [0.0, 0.0, 1.0],
        ]
    )


def _ca_process_noise(dt: float, spectral_density: float) -> NDArray[np.float64]:
    return spectral_density * np.array(
        [
            [dt**5 / 20.0, dt**4 / 8.0, dt**3 / 6.0],
            [dt**4 / 8.0, dt**3 / 3.0, dt**2 / 2.0],
            [dt**3 / 6.0, dt**2 / 2.0, dt],
        ]
    )


class ConstantAccelerationKalmanFilter(Estimator):
    """Independent per-DoF constant-acceleration Kalman filter."""

    name = "ca_kalman_filter"

    def __init__(
        self,
        nominal_dt: float | None = None,
        *,
        measurement_sigma: ArrayLike = 0.01,
        jerk_spectral_density: float = 1000.0,
        initial_velocity_sigma: float = 0.05,
        initial_acceleration_sigma: float = 0.5,
        **kwargs: Any,
    ) -> None:
        super().__init__(nominal_dt, **kwargs)
        self._measurement_sigma_input = np.asarray(
            measurement_sigma,
            dtype=float,
        )
        if not np.all(np.isfinite(self._measurement_sigma_input)) or np.any(
            self._measurement_sigma_input <= 0.0
        ):
            raise ValueError("measurement_sigma must be finite and positive")
        self.jerk_spectral_density = _positive_float(
            jerk_spectral_density,
            "jerk_spectral_density",
        )
        self.initial_velocity_sigma = _positive_float(
            initial_velocity_sigma,
            "initial_velocity_sigma",
        )
        self.initial_acceleration_sigma = _positive_float(
            initial_acceleration_sigma,
            "initial_acceleration_sigma",
        )
        self._state: NDArray[np.float64] | None = None
        self._covariance: NDArray[np.float64] | None = None
        self._measurement_variance: NDArray[np.float64] | None = None

    def _reset_impl(self) -> None:
        self._state = None
        self._covariance = None
        self._measurement_variance = None

    def _initialize(self, measurement: Measurement) -> None:
        sigma = self._measurement_sigma_input
        if sigma.ndim == 0:
            sigma = np.full(measurement.dof, float(sigma))
        if sigma.shape != (measurement.dof,):
            raise ValueError(
                "measurement_sigma must be scalar or match measurement DoF"
            )
        self._measurement_variance = sigma**2
        self._state = np.column_stack(
            (
                measurement.position,
                np.zeros(measurement.dof),
                np.zeros(measurement.dof),
            )
        )
        self._covariance = np.zeros((measurement.dof, 3, 3))
        self._covariance[:, 0, 0] = self._measurement_variance
        self._covariance[:, 1, 1] = self.initial_velocity_sigma**2
        self._covariance[:, 2, 2] = self.initial_acceleration_sigma**2

    def _clip_innovation(
        self,
        innovation: NDArray[np.float64],
        innovation_variance: NDArray[np.float64],
    ) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
        return innovation, np.zeros(innovation.shape, dtype=bool)

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        if self._state is None:
            self._initialize(measurement)
            outlier_mask = np.zeros(measurement.dof, dtype=bool)
        else:
            assert dt is not None
            assert self._covariance is not None
            assert self._measurement_variance is not None
            transition = _ca_transition(dt)
            process_noise = _ca_process_noise(
                dt,
                self.jerk_spectral_density,
            )
            predicted_state = self._state @ transition.T
            predicted_covariance = (
                np.einsum(
                    "ij,njk,lk->nil",
                    transition,
                    self._covariance,
                    transition,
                )
                + process_noise
            )
            innovation = measurement.position - predicted_state[:, 0]
            innovation_variance = (
                predicted_covariance[:, 0, 0] + self._measurement_variance
            )
            innovation, outlier_mask = self._clip_innovation(
                innovation,
                innovation_variance,
            )
            gain = predicted_covariance[:, :, 0] / innovation_variance[:, None]
            self._state = predicted_state + gain * innovation[:, None]

            identity = np.eye(3)
            covariance = np.empty_like(predicted_covariance)
            for axis in range(measurement.dof):
                correction = identity.copy()
                correction[:, 0] -= gain[axis]
                covariance[axis] = (
                    correction @ predicted_covariance[axis] @ correction.T
                    + np.outer(gain[axis], gain[axis])
                    * self._measurement_variance[axis]
                )
                covariance[axis] = 0.5 * (covariance[axis] + covariance[axis].T)
            self._covariance = covariance

        assert self._state is not None
        startup = self._sample_count < 2
        return TimedState(
            self._state[:, 0],
            self._state[:, 1],
            self._state[:, 2],
            state_time=measurement.state_time,
            available_time=measurement.available_time,
            method=self.name,
            status="startup" if startup else "ok",
            startup=startup,
            metadata={
                "dt_used": dt,
                "outlier_mask": outlier_mask.tolist(),
            },
        )


class RobustCAKalmanFilter(ConstantAccelerationKalmanFilter):
    """CA-KF with a Huber-style normalized-innovation cap."""

    name = "robust_ca_kalman_filter"

    def __init__(
        self,
        nominal_dt: float | None = None,
        *,
        innovation_sigma_limit: float = 3.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(nominal_dt, **kwargs)
        self.innovation_sigma_limit = _positive_float(
            innovation_sigma_limit,
            "innovation_sigma_limit",
        )
        self.outlier_count: NDArray[np.int64] | None = None

    def _reset_impl(self) -> None:
        super()._reset_impl()
        self.outlier_count = None

    def _initialize(self, measurement: Measurement) -> None:
        super()._initialize(measurement)
        self.outlier_count = np.zeros(measurement.dof, dtype=np.int64)

    def _clip_innovation(
        self,
        innovation: NDArray[np.float64],
        innovation_variance: NDArray[np.float64],
    ) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
        limit = self.innovation_sigma_limit * np.sqrt(innovation_variance)
        mask = np.abs(innovation) > limit
        clipped = np.clip(innovation, -limit, limit)
        assert self.outlier_count is not None
        self.outlier_count += mask.astype(np.int64)
        return clipped, mask


def _cj_transition(dt: float) -> NDArray[np.float64]:
    return np.array(
        [
            [1.0, dt, dt**2 / 2.0, dt**3 / 6.0],
            [0.0, 1.0, dt, dt**2 / 2.0],
            [0.0, 0.0, 1.0, dt],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def _cj_process_noise(dt: float, spectral_density: float) -> NDArray[np.float64]:
    # Continuous white snap integrated into [p, v, a, j].
    result = np.empty((4, 4), dtype=float)
    for row in range(4):
        for column in range(4):
            exponent = 7 - row - column
            denominator = factorial(3 - row) * factorial(3 - column) * exponent
            result[row, column] = dt**exponent / denominator
    return spectral_density * result


class ConstantJerkKalmanFilter(Estimator):
    """Position-observed constant-jerk KF with continuous white snap noise."""

    name = "cj_kalman_filter"

    def __init__(
        self,
        nominal_dt: float | None = None,
        *,
        measurement_sigma: ArrayLike = 0.01,
        snap_spectral_density: float = 10000.0,
        initial_velocity_sigma: float = 0.05,
        initial_acceleration_sigma: float = 0.5,
        initial_jerk_sigma: float = 5.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(nominal_dt, **kwargs)
        self._measurement_sigma_input = np.asarray(
            measurement_sigma,
            dtype=float,
        )
        if not np.all(np.isfinite(self._measurement_sigma_input)) or np.any(
            self._measurement_sigma_input <= 0.0
        ):
            raise ValueError("measurement_sigma must be finite and positive")
        self.snap_spectral_density = _positive_float(
            snap_spectral_density,
            "snap_spectral_density",
        )
        self.initial_sigmas = np.array(
            [
                1.0,
                _positive_float(initial_velocity_sigma, "initial_velocity_sigma"),
                _positive_float(
                    initial_acceleration_sigma,
                    "initial_acceleration_sigma",
                ),
                _positive_float(initial_jerk_sigma, "initial_jerk_sigma"),
            ]
        )
        self._state: NDArray[np.float64] | None = None
        self._covariance: NDArray[np.float64] | None = None
        self._measurement_variance: NDArray[np.float64] | None = None

    def _reset_impl(self) -> None:
        self._state = None
        self._covariance = None
        self._measurement_variance = None

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        if self._state is None:
            sigma = self._measurement_sigma_input
            if sigma.ndim == 0:
                sigma = np.full(measurement.dof, float(sigma))
            if sigma.shape != (measurement.dof,):
                raise ValueError(
                    "measurement_sigma must be scalar or match measurement DoF"
                )
            self._measurement_variance = sigma**2
            self._state = np.column_stack(
                (
                    measurement.position,
                    np.zeros((measurement.dof, 3)),
                )
            )
            self._covariance = np.zeros((measurement.dof, 4, 4))
            diagonal = np.array(
                [
                    0.0,
                    self.initial_sigmas[1] ** 2,
                    self.initial_sigmas[2] ** 2,
                    self.initial_sigmas[3] ** 2,
                ]
            )
            self._covariance[:] = np.diag(diagonal)
            self._covariance[:, 0, 0] = self._measurement_variance
        else:
            assert dt is not None
            assert self._covariance is not None
            assert self._measurement_variance is not None
            transition = _cj_transition(dt)
            process_noise = _cj_process_noise(
                dt,
                self.snap_spectral_density,
            )
            predicted_state = self._state @ transition.T
            predicted_covariance = (
                np.einsum(
                    "ij,njk,lk->nil",
                    transition,
                    self._covariance,
                    transition,
                )
                + process_noise
            )
            innovation = measurement.position - predicted_state[:, 0]
            innovation_variance = (
                predicted_covariance[:, 0, 0] + self._measurement_variance
            )
            gain = predicted_covariance[:, :, 0] / innovation_variance[:, None]
            self._state = predicted_state + gain * innovation[:, None]
            identity = np.eye(4)
            covariance = np.empty_like(predicted_covariance)
            for axis in range(measurement.dof):
                correction = identity.copy()
                correction[:, 0] -= gain[axis]
                covariance[axis] = (
                    correction @ predicted_covariance[axis] @ correction.T
                    + np.outer(gain[axis], gain[axis])
                    * self._measurement_variance[axis]
                )
                covariance[axis] = 0.5 * (covariance[axis] + covariance[axis].T)
            self._covariance = covariance

        assert self._state is not None
        startup = self._sample_count < 3
        return TimedState(
            self._state[:, 0],
            self._state[:, 1],
            self._state[:, 2],
            jerk=self._state[:, 3],
            state_time=measurement.state_time,
            available_time=measurement.available_time,
            method=self.name,
            status="startup" if startup else "ok",
            startup=startup,
            metadata={"dt_used": dt},
        )


class JerkLimitedDifferentiator(Estimator):
    """Third-order tracking differentiator with per-axis V/A/J bounds."""

    name = "jerk_limited_differentiator"

    def __init__(
        self,
        nominal_dt: float | None = None,
        *,
        max_velocity: ArrayLike = 4.1,
        max_acceleration: ArrayLike = 8.2,
        max_jerk: ArrayLike = 4000.0,
        frequency_hz: float = 2.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(nominal_dt, **kwargs)
        self._max_velocity_input = max_velocity
        self._max_acceleration_input = max_acceleration
        self._max_jerk_input = max_jerk
        self.frequency_hz = _positive_float(frequency_hz, "frequency_hz")
        self.omega = 2.0 * np.pi * self.frequency_hz
        self._state: NDArray[np.float64] | None = None
        self._last_jerk: NDArray[np.float64] | None = None
        self._max_velocity: NDArray[np.float64] | None = None
        self._max_acceleration: NDArray[np.float64] | None = None
        self._max_jerk: NDArray[np.float64] | None = None

    def _reset_impl(self) -> None:
        self._state = None
        self._last_jerk = None
        self._max_velocity = None
        self._max_acceleration = None
        self._max_jerk = None

    def _initialize(self, measurement: Measurement) -> None:
        self._max_velocity = _limit_vector(
            self._max_velocity_input,
            measurement.dof,
            "max_velocity",
        )
        self._max_acceleration = _limit_vector(
            self._max_acceleration_input,
            measurement.dof,
            "max_acceleration",
        )
        self._max_jerk = _limit_vector(
            self._max_jerk_input,
            measurement.dof,
            "max_jerk",
        )
        self._state = np.column_stack(
            (
                measurement.position,
                np.zeros(measurement.dof),
                np.zeros(measurement.dof),
            )
        )
        self._last_jerk = np.zeros(measurement.dof)

    def _bounded_jerk(
        self,
        requested: NDArray[np.float64],
        dt: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
        assert self._state is not None
        assert self._max_velocity is not None
        assert self._max_acceleration is not None
        assert self._max_jerk is not None
        velocity = self._state[:, 1]
        acceleration = self._state[:, 2]
        lower = -self._max_jerk.copy()
        upper = self._max_jerk.copy()
        lower = np.maximum(
            lower,
            (-self._max_acceleration - acceleration) / dt,
        )
        upper = np.minimum(
            upper,
            (self._max_acceleration - acceleration) / dt,
        )
        factor = 2.0 / (dt * dt)
        lower = np.maximum(
            lower,
            factor * (-self._max_velocity - velocity - acceleration * dt),
        )
        upper = np.minimum(
            upper,
            factor * (self._max_velocity - velocity - acceleration * dt),
        )
        infeasible = lower > upper
        result = np.clip(requested, lower, upper)
        # A state already outside the bounds can make the interval empty.
        # Zero jerk is an explicit, observable fallback rather than NaN.
        result[infeasible] = 0.0
        return result, infeasible

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        if self._state is None:
            self._initialize(measurement)
            jerk = np.zeros(measurement.dof)
            infeasible = np.zeros(measurement.dof, dtype=bool)
        else:
            assert dt is not None
            error = measurement.position - self._state[:, 0]
            requested = (
                self.omega**3 * error
                - 3.0 * self.omega**2 * self._state[:, 1]
                - 3.0 * self.omega * self._state[:, 2]
            )
            jerk, infeasible = self._bounded_jerk(requested, dt)
            position = (
                self._state[:, 0]
                + self._state[:, 1] * dt
                + 0.5 * self._state[:, 2] * dt**2
                + jerk * dt**3 / 6.0
            )
            velocity = self._state[:, 1] + self._state[:, 2] * dt + 0.5 * jerk * dt**2
            acceleration = self._state[:, 2] + jerk * dt
            self._state = np.column_stack((position, velocity, acceleration))
            self._last_jerk = jerk
        assert self._state is not None
        startup = self._sample_count == 0
        return TimedState(
            self._state[:, 0],
            self._state[:, 1],
            self._state[:, 2],
            jerk=jerk,
            state_time=measurement.state_time,
            available_time=measurement.available_time,
            method=self.name,
            status=(
                "limit_interval_empty"
                if np.any(infeasible)
                else ("startup" if startup else "ok")
            ),
            valid=not bool(np.any(infeasible)),
            startup=startup,
            metadata={
                "dt_used": dt,
                "limit_interval_empty_mask": infeasible.tolist(),
                "fallback": bool(np.any(infeasible)),
            },
        )


class LegacyEstimatorAdapter(Estimator):
    """Adapt a zero-lookahead legacy ``step(position)`` implementation.

    A legacy object's ``lookahead`` is inspected.  Any positive value is
    rejected because it means ``step`` already returns a future prediction;
    accepting it as a posterior would recreate the architecture defect this
    package removes.  Set the legacy estimator's lookahead to zero and attach
    an explicit :class:`otg_lab.predictors.Predictor` instead.
    """

    name = "legacy_zero_lookahead_adapter"

    def __init__(
        self,
        legacy_estimator: Any,
        nominal_dt: float | None = None,
        *,
        output_lag: float = 0.0,
        **kwargs: Any,
    ) -> None:
        if not hasattr(legacy_estimator, "step"):
            raise TypeError("legacy_estimator must provide step(position)")
        detected_lookahead = float(getattr(legacy_estimator, "lookahead", 0.0))
        if not np.isfinite(detected_lookahead) or detected_lookahead < 0.0:
            raise ValueError("legacy lookahead must be finite and non-negative")
        if detected_lookahead > 1e-15:
            raise ValueError(
                "legacy estimator includes future prediction "
                f"(lookahead={detected_lookahead}); refusing to hide it in "
                "an estimator posterior"
            )
        if not np.isfinite(output_lag) or output_lag < 0.0:
            raise ValueError("output_lag must be finite and non-negative")
        if nominal_dt is None:
            nominal_dt = getattr(legacy_estimator, "dt", None)
        super().__init__(nominal_dt, **kwargs)
        self.legacy_estimator = legacy_estimator
        try:
            self._legacy_template = copy.deepcopy(legacy_estimator)
        except Exception:
            self._legacy_template = None
        self.output_lag = float(output_lag)
        legacy_name = str(
            getattr(legacy_estimator, "name", type(legacy_estimator).__name__)
        )
        self.name = f"legacy_adapter:{legacy_name}"

    def _reset_impl(self) -> None:
        reset = getattr(self.legacy_estimator, "reset", None)
        if callable(reset):
            reset()
        elif self._legacy_template is not None:
            self.legacy_estimator = copy.deepcopy(self._legacy_template)
        elif hasattr(self, "legacy_estimator"):
            raise EstimatorError(
                "legacy estimator has no reset() and could not be copied; "
                "construct a new adapter for a fresh trajectory"
            )

    def _update_valid(
        self,
        measurement: Measurement,
        dt: float | None,
    ) -> TimedState:
        argument: float | NDArray[np.float64]
        argument = (
            float(measurement.position[0])
            if measurement.dof == 1
            else np.array(measurement.position, copy=True)
        )
        values = np.asarray(self.legacy_estimator.step(argument), dtype=float)
        if values.shape == (3,) and measurement.dof == 1:
            values = values.reshape(1, 3)
        if values.shape != (measurement.dof, 3):
            raise EstimatorError(
                "legacy step output must have shape (3,) for 1 DoF or "
                f"(dof, 3); got {values.shape}"
            )
        state_time = measurement.state_time - self.output_lag
        return TimedState(
            values[:, 0],
            values[:, 1],
            values[:, 2],
            state_time=state_time,
            available_time=measurement.available_time,
            method=self.name,
            startup=self._sample_count == 0,
            status="startup" if self._sample_count == 0 else "ok",
            metadata={
                "legacy_adapter": True,
                "legacy_lookahead": 0.0,
                "output_lag": self.output_lag,
            },
        )


def local_polynomial_grid(
    nominal_dt: float,
    *,
    lag_samples: int = 0,
    windows: Iterable[int] = (5, 7, 9, 11),
    degrees: Iterable[int] = (2, 3),
    **kwargs: Any,
) -> list[CausalLocalPolynomial]:
    """Construct the formal window/degree local-polynomial grid."""

    return [
        CausalLocalPolynomial(
            nominal_dt,
            window=window,
            degree=degree,
            lag_samples=lag_samples,
            **kwargs,
        )
        for window in windows
        for degree in degrees
    ]


def default_estimator_suite(
    nominal_dt: float,
    *,
    max_velocity: ArrayLike = 4.1,
    max_acceleration: ArrayLike = 8.2,
    max_jerk: ArrayLike = 4000.0,
    local_poly_lag_samples: int = 0,
    **kwargs: Any,
) -> list[Estimator]:
    """Return all required estimator families without a hidden predictor."""

    estimators: list[Estimator] = [
        PositionOnly(nominal_dt, **kwargs),
        RawBackwardDifference(nominal_dt, **kwargs),
        DelayOneCenteredDifference(nominal_dt, **kwargs),
    ]
    estimators.extend(
        local_polynomial_grid(
            nominal_dt,
            lag_samples=local_poly_lag_samples,
            **kwargs,
        )
    )
    estimators.extend(
        [
            AlphaBetaGamma(nominal_dt, **kwargs),
            ConstantAccelerationKalmanFilter(nominal_dt, **kwargs),
            RobustCAKalmanFilter(nominal_dt, **kwargs),
            ConstantJerkKalmanFilter(nominal_dt, **kwargs),
            JerkLimitedDifferentiator(
                nominal_dt,
                max_velocity=max_velocity,
                max_acceleration=max_acceleration,
                max_jerk=max_jerk,
                **kwargs,
            ),
        ]
    )
    return estimators


ESTIMATOR_METHOD_IDS = (
    "position_only",
    "raw_backward_difference",
    "delay_one_centered_difference",
    "local_poly",
    "alpha_beta_gamma",
    "ca_kf",
    "robust_ca_kf",
    "cj_kf",
    "jerk_limited_differentiator",
)


def make_estimator(name: str, **params: Any) -> Estimator:
    """Create an estimator from a stable configuration method ID.

    ``dt`` is accepted as a configuration alias for ``nominal_dt``.  Fully
    resolved local-polynomial IDs such as ``local_poly_w7_d3_lag1`` are also
    accepted and override the corresponding grid parameters.
    """

    if "lookahead" in params:
        legacy_lookahead = float(params.pop("lookahead"))
        if legacy_lookahead != 0.0:
            raise ValueError(
                "estimator lookahead is forbidden in the timed architecture; "
                "configure a Predictor and prediction_horizon instead"
            )
    if "prediction_horizon" in params:
        raise ValueError(
            "prediction_horizon belongs to a Predictor/pipeline, not an Estimator"
        )
    if "dt" in params:
        if "nominal_dt" in params:
            raise ValueError("set only one of dt or nominal_dt")
        params["nominal_dt"] = params.pop("dt")
    normalized = str(name).strip().lower().replace("-", "_")
    aliases = {
        "position": "position_only",
        "p": "position_only",
        "raw_backward": "raw_backward_difference",
        "backward_fd": "raw_backward_difference",
        "delay_one_centered": "delay_one_centered_difference",
        "centered_causal": "delay_one_centered_difference",
        "abg": "alpha_beta_gamma",
        "constant_acceleration_kf": "ca_kf",
        "ca_kalman_filter": "ca_kf",
        "robust_ca_kalman_filter": "robust_ca_kf",
        "robust_kf": "robust_ca_kf",
        "constant_jerk_kf": "cj_kf",
        "cj_kalman_filter": "cj_kf",
        "jerk_limited": "jerk_limited_differentiator",
    }
    normalized = aliases.get(normalized, normalized)
    match = re.fullmatch(
        r"local_(?:poly|polynomial)_w(5|7|9|11)_d(2|3)_lag(\d+)",
        normalized,
    )
    if match:
        params = {
            **params,
            "window": int(match.group(1)),
            "degree": int(match.group(2)),
            "lag_samples": int(match.group(3)),
        }
        normalized = "local_poly"
    factories: dict[str, type[Estimator]] = {
        "position_only": PositionOnly,
        "raw_backward_difference": RawBackwardDifference,
        "delay_one_centered_difference": DelayOneCenteredDifference,
        "local_poly": CausalLocalPolynomial,
        "alpha_beta_gamma": AlphaBetaGamma,
        "ca_kf": ConstantAccelerationKalmanFilter,
        "robust_ca_kf": RobustCAKalmanFilter,
        "cj_kf": ConstantJerkKalmanFilter,
        "jerk_limited_differentiator": JerkLimitedDifferentiator,
    }
    try:
        factory = factories[normalized]
    except KeyError as error:
        raise KeyError(
            f"unknown estimator {name!r}; stable IDs are "
            f"{', '.join(ESTIMATOR_METHOD_IDS)}"
        ) from error
    return factory(**params)


# Descriptive aliases used by configs and migration code.
StateEstimator = Estimator
CentralDifference = DelayOneCenteredDifference
CentralDifference10 = DelayOneCenteredDifference
LocalPolynomialEstimator = CausalLocalPolynomial
LocalPolynomial = CausalLocalPolynomial
CAKalmanFilter = ConstantAccelerationKalmanFilter
RobustKalmanFilter = RobustCAKalmanFilter
RobustKalman = RobustCAKalmanFilter
CJKalmanFilter = ConstantJerkKalmanFilter
JerkLimitedTrackingDifferentiator = JerkLimitedDifferentiator
JerkLimitedTracker = JerkLimitedDifferentiator


__all__ = [
    "AlphaBetaGamma",
    "CAKalmanFilter",
    "CJKalmanFilter",
    "CausalLocalPolynomial",
    "CentralDifference",
    "CentralDifference10",
    "ConstantAccelerationKalmanFilter",
    "ConstantJerkKalmanFilter",
    "DelayOneCenteredDifference",
    "Estimator",
    "EstimatorError",
    "ESTIMATOR_METHOD_IDS",
    "JerkLimitedDifferentiator",
    "JerkLimitedTracker",
    "JerkLimitedTrackingDifferentiator",
    "LegacyEstimatorAdapter",
    "LocalPolynomial",
    "LocalPolynomialEstimator",
    "NonFiniteMeasurementError",
    "PositionOnly",
    "RawBackwardDifference",
    "RobustCAKalmanFilter",
    "RobustKalman",
    "RobustKalmanFilter",
    "StateEstimator",
    "TimestampError",
    "default_estimator_suite",
    "local_polynomial_grid",
    "make_estimator",
]
