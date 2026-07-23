"""Independent future-reference predictors.

Predictors consume estimator posteriors but never modify estimator state.
Every result is a :class:`~otg_lab.types.TimedState` explicitly labelled with
``source_state_time``, ``prediction_horizon``, and ``prediction_time``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable
from time import perf_counter_ns
from typing import Any, Callable

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .types import TimedState


class PredictorError(RuntimeError):
    """Base error raised by the predictor layer."""


def _horizon(value: float) -> float:
    result = float(value)
    if not np.isfinite(result) or result < 0.0:
        raise ValueError("horizon must be finite and non-negative")
    return result


def _as_axis_vector(
    value: ArrayLike,
    dof: int,
    name: str,
) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=float)
    if array.ndim == 0:
        array = np.full(dof, float(array))
    if array.shape != (dof,):
        raise ValueError(f"{name} must be scalar or have shape ({dof},)")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    return array


def propagate_constant_jerk(
    position: ArrayLike,
    velocity: ArrayLike,
    acceleration: ArrayLike,
    jerk: ArrayLike,
    horizon: float,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
]:
    """Analytically propagate vector state under constant jerk."""

    duration = _horizon(horizon)
    p = np.asarray(position, dtype=float)
    v = np.asarray(velocity, dtype=float)
    a = np.asarray(acceleration, dtype=float)
    j = np.asarray(jerk, dtype=float)
    try:
        p, v, a, j = np.broadcast_arrays(p, v, a, j)
    except ValueError as error:
        raise ValueError("p/v/a/j must have broadcast-compatible shapes") from error
    return (
        np.array(
            p + v * duration + 0.5 * a * duration**2 + j * duration**3 / 6.0,
            copy=True,
        ),
        np.array(
            v + a * duration + 0.5 * j * duration**2,
            copy=True,
        ),
        np.array(a + j * duration, copy=True),
    )


def predict_constant_jerk(
    state: ArrayLike,
    horizon: float,
    jerk: ArrayLike = 0.0,
) -> NDArray[np.float64]:
    """Compatibility helper returning propagated ``[p, v, a]`` values."""

    array = np.asarray(state, dtype=float)
    if array.ndim == 1 and array.shape == (3,):
        p, v, a = propagate_constant_jerk(
            array[0],
            array[1],
            array[2],
            jerk,
            horizon,
        )
        return np.asarray([float(p), float(v), float(a)])
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("state must have shape (3,) or (dof, 3)")
    jerk_vector = _as_axis_vector(jerk, array.shape[0], "jerk")
    p, v, a = propagate_constant_jerk(
        array[:, 0],
        array[:, 1],
        array[:, 2],
        jerk_vector,
        horizon,
    )
    return np.column_stack((p, v, a))


class Predictor(ABC):
    """Base predictor with uniform timestamp and sequence semantics."""

    name = "predictor"
    causal = True
    offline_only = False

    def __init__(self) -> None:
        self.compute_us: list[float] = []

    def reset(self) -> None:
        self.compute_us.clear()
        self._reset_impl()

    def _reset_impl(self) -> None:
        """Subclass hook for predictor history."""

        return None

    def predict(self, posterior: TimedState, horizon: float) -> TimedState:
        """Predict ``horizon`` seconds after the posterior's state time."""

        started = perf_counter_ns()
        duration = _horizon(horizon)
        self._validate_posterior(posterior)
        self._observe(posterior)
        values, jerk, status, metadata = self._predict_values(
            posterior,
            duration,
        )
        checked = []
        for label, value in zip(("position", "velocity", "acceleration"), values):
            array = np.asarray(value, dtype=float)
            if array.shape != (posterior.dof,):
                raise PredictorError(
                    f"{self.name} returned {label} shape {array.shape}; "
                    f"expected ({posterior.dof},)"
                )
            if not np.all(np.isfinite(array)):
                raise PredictorError(f"{self.name} returned non-finite {label}")
            checked.append(array)
        position, velocity, acceleration = checked
        if jerk is not None:
            jerk = np.asarray(jerk, dtype=float)
            if jerk.shape != (posterior.dof,) or not np.all(np.isfinite(jerk)):
                raise PredictorError(f"{self.name} returned invalid jerk shape/value")
        elapsed_us = (perf_counter_ns() - started) / 1000.0
        self.compute_us.append(elapsed_us)
        result_metadata = {
            **dict(posterior.metadata),
            **metadata,
            "predictor": self.name,
            "posterior_method": posterior.method,
            "offline_only": self.offline_only,
        }
        return TimedState(
            position=position,
            velocity=velocity,
            acceleration=acceleration,
            jerk=jerk,
            state_time=posterior.state_time + duration,
            available_time=posterior.available_time,
            method=self.name,
            status=status,
            valid=posterior.valid,
            startup=posterior.startup or status.startswith("startup"),
            compute_time_us=elapsed_us,
            source_state_time=posterior.state_time,
            prediction_horizon=duration,
            causal=self.causal,
            metadata=result_metadata,
        )

    def predict_sequence(
        self,
        posterior: TimedState,
        horizons: Iterable[float],
    ) -> list[TimedState]:
        """Predict multiple horizons without changing their order.

        Stateful predictors observe a posterior at most once even though
        ``predict`` is invoked repeatedly; duplicate-time observations replace
        the same history entry rather than adding future information.
        """

        durations = [_horizon(value) for value in horizons]
        return [self.predict(posterior, duration) for duration in durations]

    def _validate_posterior(self, posterior: TimedState) -> None:
        if not isinstance(posterior, TimedState):
            raise TypeError("predict expects an otg_lab.types.TimedState")
        if posterior.is_prediction:
            raise ValueError(
                "predict expects an estimator posterior, not another "
                "prediction; use its source posterior explicitly"
            )
        if not posterior.is_finite:
            raise ValueError("posterior contains non-finite state components")

    def _observe(self, posterior: TimedState) -> None:
        """Optional history hook called before prediction."""

        return None

    @abstractmethod
    def _predict_values(
        self,
        posterior: TimedState,
        horizon: float,
    ) -> tuple[
        tuple[
            NDArray[np.float64],
            NDArray[np.float64],
            NDArray[np.float64],
        ],
        NDArray[np.float64] | None,
        str,
        dict[str, Any],
    ]:
        raise NotImplementedError


class ZeroOrderHoldPredictor(Predictor):
    """Hold all posterior components without dynamical propagation."""

    name = "zoh"

    def _predict_values(self, posterior: TimedState, horizon: float):
        return (
            (
                np.array(posterior.position, copy=True),
                np.array(posterior.velocity, copy=True),
                np.array(posterior.acceleration, copy=True),
            ),
            None if posterior.jerk is None else np.array(posterior.jerk, copy=True),
            "ok",
            {"model": "zero_order_hold"},
        )


class ConstantVelocityPredictor(Predictor):
    """Propagate position at constant posterior velocity."""

    name = "constant_velocity"

    def _predict_values(self, posterior: TimedState, horizon: float):
        zeros = np.zeros(posterior.dof)
        return (
            (
                posterior.position + posterior.velocity * horizon,
                np.array(posterior.velocity, copy=True),
                zeros,
            ),
            zeros,
            "ok",
            {"model": "constant_velocity"},
        )


class ConstantAccelerationPredictor(Predictor):
    """Propagate the posterior under zero jerk."""

    name = "constant_acceleration"

    def _predict_values(self, posterior: TimedState, horizon: float):
        zeros = np.zeros(posterior.dof)
        p, v, a = propagate_constant_jerk(
            posterior.position,
            posterior.velocity,
            posterior.acceleration,
            zeros,
            horizon,
        )
        return (p, v, a), zeros, "ok", {"model": "constant_acceleration"}


class ConstantJerkPredictor(Predictor):
    """Propagate with posterior jerk or an explicitly configured fallback."""

    name = "constant_jerk"

    def __init__(
        self,
        *,
        jerk: ArrayLike | None = None,
        missing_jerk: str = "zero",
    ) -> None:
        super().__init__()
        if missing_jerk not in {"zero", "raise"}:
            raise ValueError("missing_jerk must be 'zero' or 'raise'")
        self.configured_jerk = jerk
        self.missing_jerk = missing_jerk

    def _predict_values(self, posterior: TimedState, horizon: float):
        fallback = False
        if self.configured_jerk is not None:
            jerk = _as_axis_vector(
                self.configured_jerk,
                posterior.dof,
                "jerk",
            )
            source = "configured"
        elif posterior.jerk is not None:
            jerk = np.array(posterior.jerk, copy=True)
            source = "posterior"
        elif self.missing_jerk == "zero":
            jerk = np.zeros(posterior.dof)
            source = "zero_fallback"
            fallback = True
        else:
            raise PredictorError(
                "constant-jerk prediction requested but posterior has no "
                "jerk and missing_jerk='raise'"
            )
        p, v, a = propagate_constant_jerk(
            posterior.position,
            posterior.velocity,
            posterior.acceleration,
            jerk,
            horizon,
        )
        return (
            (p, v, a),
            jerk,
            "missing_jerk_zero" if fallback else "ok",
            {
                "model": "constant_jerk",
                "jerk_source": source,
                "fallback": fallback,
            },
        )


class LocalPolynomialPredictor(Predictor):
    """Causal polynomial extrapolation over recent posterior positions."""

    name = "local_polynomial_extrapolation"

    def __init__(
        self,
        *,
        window: int = 7,
        degree: int = 3,
        timestamp_policy: str = "reset",
    ) -> None:
        super().__init__()
        if int(window) != window or window < 3:
            raise ValueError("window must be an integer >= 3")
        if int(degree) != degree or degree not in {1, 2, 3}:
            raise ValueError("degree must be 1, 2, or 3")
        if timestamp_policy not in {"raise", "reset"}:
            raise ValueError("timestamp_policy must be 'raise' or 'reset'")
        self.window = int(window)
        self.degree = int(degree)
        self.timestamp_policy = timestamp_policy
        self._history: deque[tuple[float, NDArray[np.float64]]] = deque(
            maxlen=self.window
        )
        self._dof: int | None = None
        self._history_reset = False

    def _reset_impl(self) -> None:
        self._history.clear()
        self._dof = None
        self._history_reset = False

    def _observe(self, posterior: TimedState) -> None:
        self._history_reset = False
        if self._dof is None:
            self._dof = posterior.dof
        elif posterior.dof != self._dof:
            raise ValueError("posterior DoF changed; call predictor.reset()")
        if self._history:
            last_time = self._history[-1][0]
            if posterior.state_time < last_time:
                if self.timestamp_policy == "raise":
                    raise ValueError("posterior state_time regressed")
                self._history.clear()
                self._history_reset = True
            elif posterior.state_time == last_time:
                # A delayed estimator can revise the same startup timestamp as
                # newer measurements arrive.  Replace, do not double count.
                self._history[-1] = (
                    posterior.state_time,
                    np.array(posterior.position, copy=True),
                )
                return
        self._history.append(
            (posterior.state_time, np.array(posterior.position, copy=True))
        )

    def _predict_values(self, posterior: TimedState, horizon: float):
        times = np.array([item[0] for item in self._history])
        values = np.vstack([item[1] for item in self._history])
        offsets = times - posterior.state_time
        effective_degree = min(self.degree, len(times) - 1)
        if effective_degree == 0:
            position = np.array(posterior.position, copy=True)
            velocity = np.array(posterior.velocity, copy=True)
            acceleration = np.array(posterior.acceleration, copy=True)
            jerk = (
                np.zeros(posterior.dof)
                if posterior.jerk is None
                else np.array(posterior.jerk, copy=True)
            )
            p, v, a = propagate_constant_jerk(
                position,
                velocity,
                acceleration,
                jerk,
                horizon,
            )
            status = "startup_posterior_propagation"
        else:
            design = np.column_stack(
                [offsets**power for power in range(effective_degree + 1)]
            )
            coefficients, _, _, _ = np.linalg.lstsq(
                design,
                values,
                rcond=None,
            )
            powers = np.array([horizon**power for power in range(effective_degree + 1)])
            p = powers @ coefficients
            v = np.zeros(posterior.dof)
            a = np.zeros(posterior.dof)
            jerk = np.zeros(posterior.dof)
            if effective_degree >= 1:
                derivative_powers = np.array(
                    [
                        power * horizon ** (power - 1)
                        for power in range(1, effective_degree + 1)
                    ]
                )
                v = derivative_powers @ coefficients[1:]
            if effective_degree >= 2:
                second_powers = np.array(
                    [
                        power * (power - 1) * horizon ** (power - 2)
                        for power in range(2, effective_degree + 1)
                    ]
                )
                a = second_powers @ coefficients[2:]
            if effective_degree >= 3:
                third_powers = np.array(
                    [
                        power * (power - 1) * (power - 2) * horizon ** (power - 3)
                        for power in range(3, effective_degree + 1)
                    ]
                )
                jerk = third_powers @ coefficients[3:]
            status = "startup_fit" if len(times) < self.window else "ok"
        return (
            (p, v, a),
            jerk,
            status,
            {
                "model": "local_polynomial",
                "window": self.window,
                "degree": self.degree,
                "effective_degree": effective_degree,
                "samples_used": len(times),
                "history_reset": self._history_reset,
            },
        )


def _truth_matrix(
    value: ArrayLike,
    sample_count: int,
    name: str,
) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=float)
    if array.ndim == 1:
        if array.shape[0] != sample_count:
            raise ValueError(f"{name} must have {sample_count} samples")
        array = array[:, None]
    if array.ndim != 2 or array.shape[0] != sample_count:
        raise ValueError(f"{name} must have shape (samples, dof)")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain finite truth values")
    return np.array(array, copy=True)


class OraclePredictor(Predictor):
    """Offline, noncausal lookup of synthetic ground-truth future state.

    This class is deliberately marked ``causal=False`` and
    ``offline_only=True`` in both object and output metadata.  It requires
    actual velocity and acceleration truth; it never fabricates derivatives
    from position samples.
    """

    name = "oracle_future_state_offline"
    causal = False
    offline_only = True

    def __init__(
        self,
        truth_times: ArrayLike | None = None,
        truth_position: ArrayLike | None = None,
        truth_velocity: ArrayLike | None = None,
        truth_acceleration: ArrayLike | None = None,
        truth_jerk: ArrayLike | None = None,
        *,
        lookup: Callable[[float], TimedState | ArrayLike] | None = None,
        out_of_range: str = "raise",
    ) -> None:
        super().__init__()
        if out_of_range not in {"raise", "clip"}:
            raise ValueError("out_of_range must be 'raise' or 'clip'")
        self.lookup = lookup
        self.out_of_range = out_of_range
        self.truth_times: NDArray[np.float64] | None = None
        self.truth_position: NDArray[np.float64] | None = None
        self.truth_velocity: NDArray[np.float64] | None = None
        self.truth_acceleration: NDArray[np.float64] | None = None
        self.truth_jerk: NDArray[np.float64] | None = None
        if lookup is not None:
            if any(
                value is not None
                for value in (
                    truth_times,
                    truth_position,
                    truth_velocity,
                    truth_acceleration,
                    truth_jerk,
                )
            ):
                raise ValueError("set lookup or truth arrays, not both")
            return
        if (
            truth_times is None
            or truth_position is None
            or truth_velocity is None
            or truth_acceleration is None
        ):
            raise ValueError(
                "oracle requires truth_times, position, velocity, and "
                "acceleration, or an explicit lookup callback"
            )
        times = np.asarray(truth_times, dtype=float)
        if times.ndim != 1 or times.size == 0 or not np.all(np.isfinite(times)):
            raise ValueError("truth_times must be a finite non-empty vector")
        if np.any(np.diff(times) <= 0.0):
            raise ValueError("truth_times must be strictly increasing")
        self.truth_times = np.array(times, copy=True)
        self.truth_position = _truth_matrix(
            truth_position,
            times.size,
            "truth_position",
        )
        self.truth_velocity = _truth_matrix(
            truth_velocity,
            times.size,
            "truth_velocity",
        )
        self.truth_acceleration = _truth_matrix(
            truth_acceleration,
            times.size,
            "truth_acceleration",
        )
        shape = self.truth_position.shape
        if self.truth_velocity.shape != shape or self.truth_acceleration.shape != shape:
            raise ValueError("oracle truth components must have equal shape")
        if truth_jerk is not None:
            self.truth_jerk = _truth_matrix(
                truth_jerk,
                times.size,
                "truth_jerk",
            )
            if self.truth_jerk.shape != shape:
                raise ValueError("truth_jerk shape differs from position truth")

    def _interpolate(
        self,
        values: NDArray[np.float64],
        target_time: float,
    ) -> NDArray[np.float64]:
        assert self.truth_times is not None
        return np.array(
            [
                np.interp(target_time, self.truth_times, values[:, axis])
                for axis in range(values.shape[1])
            ]
        )

    def _predict_values(self, posterior: TimedState, horizon: float):
        requested_target_time = posterior.state_time + horizon
        target_time = requested_target_time
        clipped = False
        if self.lookup is not None:
            truth = self.lookup(target_time)
            if isinstance(truth, TimedState):
                if truth.dof != posterior.dof:
                    raise PredictorError("oracle lookup returned wrong DoF")
                tolerance = (
                    32.0
                    * np.finfo(float).eps
                    * max(1.0, abs(target_time), abs(truth.state_time))
                )
                if abs(truth.state_time - target_time) > tolerance:
                    raise PredictorError(
                        "oracle lookup returned a state for the wrong "
                        f"physical time: {truth.state_time} != {target_time}"
                    )
                values = (
                    np.array(truth.position, copy=True),
                    np.array(truth.velocity, copy=True),
                    np.array(truth.acceleration, copy=True),
                )
                jerk = None if truth.jerk is None else np.array(truth.jerk, copy=True)
            else:
                array = np.asarray(truth, dtype=float)
                if array.shape == (3,) and posterior.dof == 1:
                    array = array.reshape(1, 3)
                if array.shape not in {
                    (posterior.dof, 3),
                    (posterior.dof, 4),
                }:
                    raise PredictorError(
                        "oracle lookup must return TimedState or shape (dof, 3/4)"
                    )
                values = (array[:, 0], array[:, 1], array[:, 2])
                jerk = None if array.shape[1] == 3 else array[:, 3]
        else:
            assert self.truth_times is not None
            assert self.truth_position is not None
            assert self.truth_velocity is not None
            assert self.truth_acceleration is not None
            if target_time < self.truth_times[0] or target_time > self.truth_times[-1]:
                if self.out_of_range == "raise":
                    raise PredictorError(
                        f"oracle target_time {target_time} is outside truth "
                        f"range [{self.truth_times[0]}, {self.truth_times[-1]}]"
                    )
                target_time = float(
                    np.clip(
                        target_time,
                        self.truth_times[0],
                        self.truth_times[-1],
                    )
                )
                clipped = True
            if self.truth_position.shape[1] != posterior.dof:
                raise PredictorError("oracle truth DoF differs from posterior DoF")
            values = (
                self._interpolate(self.truth_position, target_time),
                self._interpolate(self.truth_velocity, target_time),
                self._interpolate(self.truth_acceleration, target_time),
            )
            jerk = (
                None
                if self.truth_jerk is None
                else self._interpolate(self.truth_jerk, target_time)
            )
        return (
            values,
            jerk,
            "offline_oracle_clipped" if clipped else "offline_oracle",
            {
                "model": "oracle_truth_lookup",
                "offline_only": True,
                "noncausal": True,
                "oracle_query_time": requested_target_time,
                "oracle_effective_time": target_time,
                "oracle_clipped": clipped,
            },
        )


def select_target_components(
    prediction: TimedState,
    components: str,
) -> TimedState:
    """Apply an explicit P/PV/PVA target-state ablation.

    Position is always retained.  Components omitted by the requested mode are
    set to zero and the choice is recorded in metadata.
    """

    normalized = components.lower()
    if normalized not in {"p", "pv", "pva"}:
        raise ValueError("components must be 'p', 'pv', or 'pva'")
    if not prediction.is_prediction:
        raise ValueError("component selection expects a prediction")
    velocity = (
        np.zeros(prediction.dof)
        if normalized == "p"
        else np.array(prediction.velocity, copy=True)
    )
    acceleration = (
        np.zeros(prediction.dof)
        if normalized in {"p", "pv"}
        else np.array(prediction.acceleration, copy=True)
    )
    return prediction.with_updates(
        velocity=velocity,
        acceleration=acceleration,
        jerk=None,
        method=f"{prediction.method}:{normalized}",
        metadata={
            **dict(prediction.metadata),
            "target_components": normalized,
        },
    )


# Compact aliases suitable for config factories.
ZOHPredictor = ZeroOrderHoldPredictor
CVPredictor = ConstantVelocityPredictor
CAPredictor = ConstantAccelerationPredictor
CJPredictor = ConstantJerkPredictor
LocalPolyPredictor = LocalPolynomialPredictor


PREDICTOR_METHOD_IDS = (
    "zero_order_hold",
    "constant_velocity",
    "constant_acceleration",
    "constant_jerk",
    "local_polynomial",
    "oracle",
)


def make_predictor(name: str, **params: Any) -> Predictor:
    """Create a predictor from a stable configuration method ID."""

    normalized = str(name).strip().lower().replace("-", "_")
    aliases = {
        "none": "zoh",
        "no_prediction": "zoh",
        "zero_order_hold": "zoh",
        "constant_velocity": "cv",
        "constant_acceleration": "ca",
        "constant_jerk": "cj",
        "local_polynomial": "local_poly",
        "local_polynomial_extrapolation": "local_poly",
        "oracle_future_state": "oracle",
        "oracle_future_state_offline": "oracle",
    }
    normalized = aliases.get(normalized, normalized)
    factories: dict[str, type[Predictor]] = {
        "zoh": ZeroOrderHoldPredictor,
        "cv": ConstantVelocityPredictor,
        "ca": ConstantAccelerationPredictor,
        "cj": ConstantJerkPredictor,
        "local_poly": LocalPolynomialPredictor,
        "oracle": OraclePredictor,
    }
    try:
        factory = factories[normalized]
    except KeyError as error:
        raise KeyError(
            f"unknown predictor {name!r}; stable IDs are "
            f"{', '.join(PREDICTOR_METHOD_IDS)}"
        ) from error
    return factory(**params)


__all__ = [
    "CAPredictor",
    "CJPredictor",
    "CVPredictor",
    "ConstantAccelerationPredictor",
    "ConstantJerkPredictor",
    "ConstantVelocityPredictor",
    "LocalPolyPredictor",
    "LocalPolynomialPredictor",
    "OraclePredictor",
    "PREDICTOR_METHOD_IDS",
    "Predictor",
    "PredictorError",
    "ZOHPredictor",
    "ZeroOrderHoldPredictor",
    "predict_constant_jerk",
    "propagate_constant_jerk",
    "make_predictor",
    "select_target_components",
]
