"""The single CSV-first trajectory tracking engine.

``run_tracking`` is deliberately the only control loop in the refactored
package.  It consumes one validated, fixed-grid :class:`Trajectory`, composes
fresh stateful components, and returns raw command/trace/profile data without
performing any statistical analysis.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from time import perf_counter_ns
from typing import Any

import numpy as np

from .components import (
    build_estimator,
    build_follower,
    build_governor,
    build_predictor,
    build_target_builder,
    component_context,
)
from .models import (
    ComponentSpec,
    RunConfig,
    State,
    TrackingMethodSpec,
    TrackingRun,
    TrackingStatus,
    Trajectory,
)
from .types import Measurement as TimedMeasurement
from .types import TimedState

TRACE_FIELDS = (
    "cycle_index",
    "measurement_time_s",
    "measurement_available_time_s",
    "measurement_held",
    "measurement_dropped",
    "measurement_position_rad",
    "measurement_velocity_rad_s",
    "measurement_acceleration_rad_s2",
    "posterior_time_s",
    "posterior_available_time_s",
    "posterior_position_rad",
    "posterior_velocity_rad_s",
    "posterior_acceleration_rad_s2",
    "posterior_jerk_rad_s3",
    "posterior_status",
    "posterior_startup",
    "prediction_time_s",
    "prediction_available_time_s",
    "prediction_source_time_s",
    "prediction_horizon_s",
    "prediction_position_rad",
    "prediction_velocity_rad_s",
    "prediction_acceleration_rad_s2",
    "prediction_jerk_rad_s3",
    "prediction_status",
    "prediction_startup",
    "prediction_causal",
    "prediction_offline_only",
    "raw_target_time_s",
    "raw_target_available_time_s",
    "raw_target_position_rad",
    "raw_target_velocity_rad_s",
    "raw_target_acceleration_rad_s2",
    "raw_target_status",
    "raw_target_startup",
    "raw_target_causal",
    "raw_target_position_source",
    "raw_target_derivative_source",
    "raw_target_latest_input_time_s",
    "raw_target_age_samples",
    "executable_target_time_s",
    "executable_target_position_rad",
    "executable_target_velocity_rad_s",
    "executable_target_acceleration_rad_s2",
    "command_start_position_rad",
    "command_start_velocity_rad_s",
    "command_start_acceleration_rad_s2",
    "command_time_s",
    "command_position_rad",
    "command_velocity_rad_s",
    "command_acceleration_rad_s2",
    "command_jerk_rad_s3",
    "requested_target_free_duration_s",
    "frozen_trajectory_duration_s",
    "estimator_id",
    "predictor_id",
    "target_builder_id",
    "governor_id",
    "follower_id",
    "governor_status",
    "follower_status",
    "solver_status",
    "fallback_requested",
    "fallback_applied",
    "fallback_reason",
    "safety_guarantee",
    "emergency_mode",
    "component_reset",
    "runtime_estimator_us",
    "runtime_predictor_us",
    "runtime_target_builder_us",
    "runtime_governor_us",
    "runtime_follower_us",
    "runtime_total_us",
    "deadline_miss",
    "status",
    "error_layer",
    "error_reason",
)

PROFILE_FIELDS = (
    "profile_id",
    "cycle_index",
    "segment_index",
    "start_time_s",
    "end_time_s",
    "jerk_rad_s3",
    "exact",
)


class TrackingExecutionError(RuntimeError):
    """Raised for a failed layer when ``failure_policy='fail_fast'``."""

    def __init__(self, message: str, tracking_run: TrackingRun) -> None:
        super().__init__(message)
        self.tracking_run = tracking_run


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not np.isfinite(value):
            return str(value)
        return value
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if callable(value):
        return (
            f"{getattr(value, '__module__', type(value).__module__)}."
            f"{getattr(value, '__qualname__', type(value).__qualname__)}"
        )
    return repr(value)


def _component_identity(spec: ComponentSpec) -> dict[str, Any]:
    return {
        "component_id": spec.component_id,
        "params": _jsonable(dict(spec.params)),
        "factory": None if spec.factory is None else _jsonable(spec.factory),
    }


def method_fingerprint(
    method_spec: TrackingMethodSpec,
    run_config: RunConfig,
    *,
    dt_s: float,
) -> str:
    """Return a stable SHA-256 identity for executable method semantics."""

    payload = {
        "schema_version": "otg.method.v1",
        "method_id": method_spec.method_id,
        "components": {
            "estimator": _component_identity(method_spec.estimator),
            "predictor": _component_identity(method_spec.predictor),
            "target_builder": _component_identity(method_spec.target_builder),
            "governor": _component_identity(method_spec.governor),
            "follower": _component_identity(method_spec.follower),
        },
        "run": {
            "dt_s": float(dt_s),
            "limits": _jsonable(run_config.limits),
            "minimum_duration_s": float(run_config.minimum_duration_s),
            "prediction_horizon_s": float(run_config.prediction_horizon_s),
            "measurement_policy": run_config.measurement_policy,
            "initial_state": _jsonable(run_config.initial_state),
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scalar(value: Any, *, default: float | None = None) -> float | None:
    if value is None:
        return default
    array = np.asarray(value, dtype=float)
    if array.size != 1:
        raise ValueError(f"single-axis value has shape {array.shape}")
    result = float(array.reshape(-1)[0])
    return result if np.isfinite(result) else default


def _state_matrix(value: Any) -> np.ndarray:
    if isinstance(value, TimedState):
        array = value.as_array()
    elif hasattr(value, "as_array"):
        array = np.asarray(value.as_array(), dtype=float)
    elif all(
        hasattr(value, field)
        for field in ("position_rad", "velocity_rad_s", "acceleration_rad_s2")
    ):
        array = np.asarray(
            [
                [
                    value.position_rad,
                    value.velocity_rad_s,
                    value.acceleration_rad_s2,
                ]
            ],
            dtype=float,
        )
    else:
        array = np.asarray(value, dtype=float)
    if array.shape == (3,):
        array = array.reshape(1, 3)
    if array.shape != (1, 3) or not np.all(np.isfinite(array)):
        raise ValueError(
            "component state must be a finite single-axis [position, velocity, "
            "acceleration] value"
        )
    return np.array(array, copy=True)


def _state_time(value: Any, default: float) -> float:
    for field in ("state_time", "time_s", "target_time"):
        if hasattr(value, field):
            result = float(getattr(value, field))
            if np.isfinite(result):
                return result
    return float(default)


def _fresh_trace_row(
    cycle_index: int,
    method_spec: TrackingMethodSpec,
) -> dict[str, Any]:
    row = {field: None for field in TRACE_FIELDS}
    row.update(
        {
            "cycle_index": int(cycle_index),
            "estimator_id": method_spec.estimator.component_id,
            "predictor_id": method_spec.predictor.component_id,
            "target_builder_id": method_spec.target_builder.component_id,
            "governor_id": method_spec.governor.component_id,
            "follower_id": method_spec.follower.component_id,
            "fallback_requested": False,
            "fallback_applied": False,
            "safety_guarantee": False,
            "emergency_mode": False,
            "component_reset": False,
            "deadline_miss": False,
            "status": "running",
        }
    )
    return row


def _initial_state(reference: Trajectory, config: RunConfig) -> State:
    if config.initial_state is not None:
        initial = config.initial_state
        if not np.isclose(
            float(initial.time_s),
            float(reference.time_s[0]),
            rtol=0.0,
            atol=max(1e-12, 1e-9 * reference.dt),
        ):
            raise ValueError("initial_state.time_s must equal the first reference time")
        return initial
    # The default deliberately does not consume derivative truth.
    return State(
        time_s=float(reference.time_s[0]),
        position_rad=float(reference.position_rad[0]),
        velocity_rad_s=0.0,
        acceleration_rad_s2=0.0,
    )


def _resolve_dt(reference: Trajectory, config: RunConfig) -> float:
    dt_s = float(reference.dt)
    if config.dt_s is not None and not np.isclose(
        float(config.dt_s),
        dt_s,
        rtol=1e-10,
        atol=max(1e-12, dt_s * 1e-10),
    ):
        raise ValueError(
            f"RunConfig dt_s={config.dt_s} differs from trajectory dt_s={dt_s}"
        )
    return dt_s


def _measurement(
    reference: Trajectory,
    cycle_index: int,
    policy: str,
) -> tuple[TimedMeasurement, float | None, float | None]:
    normalized = str(policy).strip().lower()
    if normalized == "position_only":
        velocity = None
        acceleration = None
    elif normalized in {
        "available_truth",
        "oracle_noncausal",
        "available_channels",
        "reference_channels",
        "truth",
    }:
        velocity = (
            None
            if reference.velocity_rad_s is None
            else float(reference.velocity_rad_s[cycle_index])
        )
        acceleration = (
            None
            if reference.acceleration_rad_s2 is None
            else float(reference.acceleration_rad_s2[cycle_index])
        )
    else:
        raise ValueError(
            "measurement_policy must be position_only, available_truth, or "
            "oracle_noncausal"
        )
    time_s = float(reference.time_s[cycle_index])
    measurement = TimedMeasurement(
        position=[float(reference.position_rad[cycle_index])],
        velocity=None if velocity is None else [velocity],
        acceleration=None if acceleration is None else [acceleration],
        state_time=time_s,
        available_time=time_s,
        metadata={
            "sample_index": int(reference.sample_index[cycle_index]),
            "measurement_policy": normalized,
        },
    )
    return measurement, velocity, acceleration


def _reset_component(component: Any, layer: str, initial: np.ndarray) -> None:
    reset = getattr(component, "reset", None)
    if not callable(reset):
        raise TypeError(f"{layer} component must provide reset()")
    if layer in {"governor", "follower"}:
        reset(initial)
    else:
        reset()


def _target_build(builder: Any, prediction: Any) -> Any:
    build = getattr(builder, "build", None)
    if callable(build):
        return build(prediction)
    if callable(builder):
        return builder(prediction)
    raise TypeError("target_builder component must provide build() or be callable")


def _empty_command(dt_s: float) -> Trajectory:
    return Trajectory(
        sample_index=np.asarray([], dtype=np.int64),
        time_s=np.asarray([], dtype=np.float64),
        position_rad=np.asarray([], dtype=np.float64),
        velocity_rad_s=np.asarray([], dtype=np.float64),
        acceleration_rad_s2=np.asarray([], dtype=np.float64),
        jerk_rad_s3=None,
        nominal_dt_s=float(dt_s),
    )


def _command_trajectory(
    reference: Trajectory,
    dt_s: float,
    command_states: Sequence[np.ndarray],
    command_jerks: Sequence[float | None],
) -> Trajectory:
    count = len(command_states)
    if count == 0:
        return _empty_command(dt_s)
    values = np.asarray(command_states, dtype=float).reshape(count, 3)
    jerk = None
    if command_jerks and all(value is not None for value in command_jerks):
        jerk = np.asarray(command_jerks, dtype=float)
    return Trajectory(
        sample_index=np.asarray(reference.sample_index[1 : count + 1], dtype=np.int64),
        time_s=np.asarray(reference.time_s[1 : count + 1], dtype=np.float64),
        position_rad=values[:, 0],
        velocity_rad_s=values[:, 1],
        acceleration_rad_s2=values[:, 2],
        jerk_rad_s3=jerk,
        nominal_dt_s=float(dt_s),
    )


def _status(
    *,
    completed: bool,
    fingerprint: str,
    valid_cycles: int,
    total_cycles: int,
    failure_layer: str | None = None,
    failure_reason: str | None = None,
) -> TrackingStatus:
    return TrackingStatus(
        completed=bool(completed),
        failure_layer=failure_layer,
        failure_reason=failure_reason,
        valid_cycles=int(valid_cycles),
        total_cycles=int(total_cycles),
        method_fingerprint=fingerprint,
    )


def _failure_reason(error: BaseException) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _finish_failure(
    *,
    reference: Trajectory,
    method_spec: TrackingMethodSpec,
    run_config: RunConfig,
    dt_s: float,
    fingerprint: str,
    command_states: Sequence[np.ndarray],
    command_jerks: Sequence[float | None],
    trace_rows: list[dict[str, Any]],
    profile_rows: list[dict[str, Any]],
    total_cycles: int,
    failure_layer: str,
    error: BaseException,
) -> TrackingRun:
    reason = _failure_reason(error)
    status = _status(
        completed=False,
        fingerprint=fingerprint,
        valid_cycles=len(command_states),
        total_cycles=total_cycles,
        failure_layer=failure_layer,
        failure_reason=reason,
    )
    result = TrackingRun(
        method_id=method_spec.method_id,
        command=_command_trajectory(reference, dt_s, command_states, command_jerks),
        trace_rows=tuple(trace_rows),
        profile_rows=tuple(profile_rows),
        status=status,
    )
    if str(run_config.failure_policy).strip().lower() in {"raise", "fail_fast"}:
        raise TrackingExecutionError(
            f"{method_spec.method_id} failed in {failure_layer}: {reason}",
            result,
        ) from error
    return result


def _profile_rows(
    profile: Any,
    *,
    method_id: str,
    cycle_index: int,
    control_time: float,
    command_time: float,
) -> tuple[list[dict[str, Any]], float | None]:
    if profile is None:
        return [], None
    boundaries = np.asarray(profile.segment_boundaries, dtype=float)
    jerks = np.asarray(profile.segment_jerks, dtype=float)
    exact = bool(profile.exact)
    profile_id = f"{method_id}.c{cycle_index:06d}"
    rows: list[dict[str, Any]] = []
    if jerks.ndim == 1:
        jerks = jerks.reshape(-1, 1)
    if jerks.shape == (boundaries.size - 1, 1):
        for segment_index, jerk in enumerate(jerks[:, 0]):
            rows.append(
                {
                    "profile_id": profile_id,
                    "cycle_index": int(cycle_index),
                    "segment_index": int(segment_index),
                    "start_time_s": float(control_time + boundaries[segment_index]),
                    "end_time_s": float(control_time + boundaries[segment_index + 1]),
                    "jerk_rad_s3": float(jerk),
                    "exact": exact,
                }
            )
    elif boundaries.size >= 2:
        rows.append(
            {
                "profile_id": profile_id,
                "cycle_index": int(cycle_index),
                "segment_index": 0,
                "start_time_s": float(control_time),
                "end_time_s": float(command_time),
                "jerk_rad_s3": None,
                "exact": False,
            }
        )

    # A command CSV jerk is populated only when its full interval has one
    # unambiguous constant jerk. Piecewise Ruckig jerk remains in profiles.
    terminal_jerk = None
    if (
        exact
        and len(rows) == 1
        and str(getattr(profile, "profile_kind", ""))
        in {"constant_jerk", "emergency_constant_jerk"}
    ):
        terminal_jerk = float(rows[0]["jerk_rad_s3"])
    return rows, terminal_jerk


def _prediction(
    predictor: Any,
    posterior: TimedState,
    target_time_s: float,
) -> Any:
    duration = max(0.0, float(target_time_s) - float(posterior.state_time))
    predict = getattr(predictor, "predict", None)
    if not callable(predict):
        raise TypeError("predictor component must provide predict()")
    return predict(posterior, duration)


def run_tracking(
    reference: Trajectory,
    method_spec: TrackingMethodSpec,
    run_config: RunConfig,
    *,
    measurements: Sequence[TimedMeasurement] | None = None,
) -> TrackingRun:
    """Track one reference with one independently constructed method.

    Cycle ``k`` observes reference sample ``k`` by default and commits exactly
    one command at reference time ``t[k+1]``.  A caller may instead supply one
    time-explicit measurement per control cycle.  This keeps the command grid
    fixed while allowing source timestamp jitter, transport delay, and held
    samples to be tested without pretending that the reference itself is an
    irregular trajectory.
    """

    if not isinstance(reference, Trajectory):
        raise TypeError("reference must be an otg_lab.models.Trajectory")
    if not isinstance(method_spec, TrackingMethodSpec):
        raise TypeError("method_spec must be a TrackingMethodSpec")
    if not isinstance(run_config, RunConfig):
        raise TypeError("run_config must be a RunConfig")
    if reference.sample_count < 2:
        raise ValueError("tracking requires at least two reference samples")
    failure_policy = str(run_config.failure_policy).strip().lower()
    if failure_policy not in {
        "record_and_continue",
        "record",
        "raise",
        "fail_fast",
    }:
        raise ValueError(
            "failure_policy must be record_and_continue or fail_fast"
        )

    dt_s = _resolve_dt(reference, run_config)
    total_cycles = reference.sample_count - 1
    supplied_measurements = (
        None if measurements is None else tuple(measurements)
    )
    if supplied_measurements is not None:
        if len(supplied_measurements) != total_cycles:
            raise ValueError(
                "measurements must contain exactly one item per control cycle: "
                f"expected {total_cycles}, got {len(supplied_measurements)}"
            )
        if not all(
            isinstance(item, TimedMeasurement)
            for item in supplied_measurements
        ):
            raise TypeError("measurements must contain Measurement values")
    fingerprint = method_fingerprint(method_spec, run_config, dt_s=dt_s)
    trace_rows: list[dict[str, Any]] = []
    profile_rows: list[dict[str, Any]] = []
    command_states: list[np.ndarray] = []
    command_jerks: list[float | None] = []

    try:
        initial_model_state = _initial_state(reference, run_config)
        current_state = _state_matrix(initial_model_state)
    except Exception as error:
        return _finish_failure(
            reference=reference,
            method_spec=method_spec,
            run_config=run_config,
            dt_s=dt_s,
            fingerprint=fingerprint,
            command_states=command_states,
            command_jerks=command_jerks,
            trace_rows=trace_rows,
            profile_rows=profile_rows,
            total_cycles=total_cycles,
            failure_layer="initial_state",
            error=error,
        )

    context = component_context(reference, run_config, dt_s)
    components: dict[str, Any] = {}
    builders = (
        ("estimator", build_estimator, method_spec.estimator),
        ("predictor", build_predictor, method_spec.predictor),
        ("target_builder", build_target_builder, method_spec.target_builder),
        ("governor", build_governor, method_spec.governor),
        ("follower", build_follower, method_spec.follower),
    )
    for layer, builder, spec in builders:
        try:
            components[layer] = builder(spec, context)
            _reset_component(components[layer], layer, current_state)
        except Exception as error:
            return _finish_failure(
                reference=reference,
                method_spec=method_spec,
                run_config=run_config,
                dt_s=dt_s,
                fingerprint=fingerprint,
                command_states=command_states,
                command_jerks=command_jerks,
                trace_rows=trace_rows,
                profile_rows=profile_rows,
                total_cycles=total_cycles,
                failure_layer=layer,
                error=error,
            )

    estimator = components["estimator"]
    predictor = components["predictor"]
    target_builder = components["target_builder"]
    governor = components["governor"]
    follower = components["follower"]

    for cycle_index in range(total_cycles):
        cycle_started = perf_counter_ns()
        control_time = float(reference.time_s[cycle_index])
        expected_command_time = float(reference.time_s[cycle_index + 1])
        row = _fresh_trace_row(cycle_index, method_spec)
        active_layer = "measurement"
        try:
            if supplied_measurements is None:
                measurement, measured_velocity, measured_acceleration = _measurement(
                    reference,
                    cycle_index,
                    run_config.measurement_policy,
                )
            else:
                measurement = supplied_measurements[cycle_index]
                tolerance = max(1e-12, dt_s * 1e-9)
                if measurement.available_time > control_time + tolerance:
                    raise ValueError(
                        "measurement is not available at the control cycle: "
                        f"{measurement.available_time} > {control_time}"
                    )
                measured_velocity = _scalar(measurement.velocity)
                measured_acceleration = _scalar(measurement.acceleration)
            row.update(
                {
                    "measurement_time_s": measurement.state_time,
                    "measurement_available_time_s": measurement.available_time,
                    "measurement_held": bool(
                        measurement.metadata.get("held", False)
                    ),
                    "measurement_dropped": bool(
                        measurement.metadata.get("dropped", False)
                    ),
                    "measurement_position_rad": _scalar(measurement.position),
                    "measurement_velocity_rad_s": measured_velocity,
                    "measurement_acceleration_rad_s2": measured_acceleration,
                }
            )

            active_layer = "estimator"
            started = perf_counter_ns()
            posterior = estimator.update(measurement)
            row["runtime_estimator_us"] = (perf_counter_ns() - started) / 1000.0
            if not isinstance(posterior, TimedState) or posterior.dof != 1:
                raise TypeError("estimator must return a single-axis TimedState")
            row.update(
                {
                    "posterior_time_s": posterior.state_time,
                    "posterior_available_time_s": posterior.available_time,
                    "posterior_position_rad": _scalar(posterior.position),
                    "posterior_velocity_rad_s": _scalar(posterior.velocity),
                    "posterior_acceleration_rad_s2": _scalar(
                        posterior.acceleration
                    ),
                    "posterior_jerk_rad_s3": _scalar(posterior.jerk),
                    "posterior_status": posterior.status,
                    "posterior_startup": posterior.startup,
                    "component_reset": (
                        "reset" in str(posterior.status).lower()
                        or bool(posterior.metadata.get("reset", False))
                    ),
                }
            )

            active_layer = "predictor"
            prediction_target_time = (
                control_time + float(run_config.prediction_horizon_s)
            )
            started = perf_counter_ns()
            prediction = _prediction(predictor, posterior, prediction_target_time)
            row["runtime_predictor_us"] = (perf_counter_ns() - started) / 1000.0
            if not isinstance(prediction, TimedState) or prediction.dof != 1:
                raise TypeError("predictor must return a single-axis TimedState")
            row.update(
                {
                    "prediction_time_s": prediction.state_time,
                    "prediction_available_time_s": prediction.available_time,
                    "prediction_source_time_s": prediction.source_state_time,
                    "prediction_horizon_s": prediction.prediction_horizon,
                    "prediction_position_rad": _scalar(prediction.position),
                    "prediction_velocity_rad_s": _scalar(prediction.velocity),
                    "prediction_acceleration_rad_s2": _scalar(
                        prediction.acceleration
                    ),
                    "prediction_jerk_rad_s3": _scalar(prediction.jerk),
                    "prediction_status": prediction.status,
                    "prediction_startup": prediction.startup,
                    "prediction_causal": prediction.causal,
                    "prediction_offline_only": bool(
                        prediction.metadata.get("offline_only", False)
                    ),
                }
            )

            active_layer = "target_builder"
            started = perf_counter_ns()
            raw_target_object = _target_build(target_builder, prediction)
            raw_target = _state_matrix(raw_target_object)
            target_builder_runtime = (perf_counter_ns() - started) / 1000.0
            raw_target_time = _state_time(
                raw_target_object,
                float(prediction.state_time),
            )
            raw_target_metadata = (
                dict(raw_target_object.metadata)
                if isinstance(raw_target_object, TimedState)
                else {}
            )
            raw_target_available_time = float(
                getattr(
                    raw_target_object,
                    "available_time",
                    prediction.available_time,
                )
            )
            raw_target_age_samples = (
                expected_command_time - raw_target_time
            ) / dt_s
            row.update(
                {
                    "runtime_target_builder_us": target_builder_runtime,
                    "raw_target_time_s": raw_target_time,
                    "raw_target_available_time_s": raw_target_available_time,
                    "raw_target_position_rad": float(raw_target[0, 0]),
                    "raw_target_velocity_rad_s": float(raw_target[0, 1]),
                    "raw_target_acceleration_rad_s2": float(raw_target[0, 2]),
                    "raw_target_status": str(
                        getattr(
                            raw_target_object,
                            "status",
                            prediction.status,
                        )
                    ),
                    "raw_target_startup": bool(
                        getattr(
                            raw_target_object,
                            "startup",
                            prediction.startup,
                        )
                    ),
                    "raw_target_causal": bool(
                        getattr(
                            raw_target_object,
                            "causal",
                            prediction.causal,
                        )
                    ),
                    "raw_target_position_source": str(
                        raw_target_metadata.get("position_source", "")
                    ),
                    "raw_target_derivative_source": str(
                        raw_target_metadata.get("derivative_source", "")
                    ),
                    "raw_target_latest_input_time_s": _scalar(
                        raw_target_metadata.get(
                            "latest_position_input_time_s"
                        )
                    ),
                    "raw_target_age_samples": float(raw_target_age_samples),
                }
            )

            active_layer = "governor"
            row.update(
                {
                    "command_start_position_rad": float(current_state[0, 0]),
                    "command_start_velocity_rad_s": float(current_state[0, 1]),
                    "command_start_acceleration_rad_s2": float(
                        current_state[0, 2]
                    ),
                }
            )
            governor_input: np.ndarray = raw_target
            if hasattr(governor, "horizon_steps"):
                steps = int(governor.horizon_steps)
                target_sequence = [raw_target]
                for step in range(1, steps):
                    sequence_time = prediction_target_time + step * dt_s
                    predictor_started = perf_counter_ns()
                    item_prediction = _prediction(
                        predictor,
                        posterior,
                        sequence_time,
                    )
                    row["runtime_predictor_us"] += (
                        perf_counter_ns() - predictor_started
                    ) / 1000.0
                    builder_started = perf_counter_ns()
                    item_target = _target_build(target_builder, item_prediction)
                    target_sequence.append(_state_matrix(item_target))
                    row["runtime_target_builder_us"] += (
                        perf_counter_ns() - builder_started
                    ) / 1000.0
                governor_input = np.asarray(target_sequence, dtype=float)
            started = perf_counter_ns()
            governed = governor.update(
                governor_input,
                control_time=control_time,
                current_state=current_state,
            )
            row["runtime_governor_us"] = (perf_counter_ns() - started) / 1000.0
            executable = _state_matrix(governed.executable_state)
            if str(getattr(governor, "name", "")) in {
                "none",
                "scalar_projection",
            }:
                executable_time = raw_target_time
            else:
                executable_time = _state_time(governed, expected_command_time)
            governor_fallback_requested = bool(
                getattr(
                    governed,
                    "fallback_requested",
                    getattr(governed, "fallback", False),
                )
            )
            governor_fallback_applied = bool(
                getattr(
                    governed,
                    "fallback_applied",
                    getattr(governed, "fallback", False),
                )
            )
            row.update(
                {
                    "executable_target_time_s": executable_time,
                    "executable_target_position_rad": float(executable[0, 0]),
                    "executable_target_velocity_rad_s": float(executable[0, 1]),
                    "executable_target_acceleration_rad_s2": float(
                        executable[0, 2]
                    ),
                    "governor_status": str(
                        getattr(governed, "solver_status", "ok")
                    ),
                }
            )

            active_layer = "follower"
            started = perf_counter_ns()
            followed = follower.update(
                executable,
                control_time=control_time,
                current_state=current_state,
            )
            row["runtime_follower_us"] = (perf_counter_ns() - started) / 1000.0
            command = _state_matrix(followed.command_state)
            command_time = float(followed.command_time)
            if not np.isclose(
                command_time,
                expected_command_time,
                rtol=0.0,
                atol=max(1e-12, dt_s * 1e-9),
            ):
                raise ValueError(
                    "follower command_time must equal reference t[k+1]: "
                    f"{command_time} != {expected_command_time}"
                )
            # Persist the canonical reference-grid value rather than an
            # arithmetically equivalent ``t[k] + dt`` rounding variant.
            command_time = expected_command_time
            follower_fallback_requested = bool(
                getattr(
                    followed,
                    "fallback_requested",
                    getattr(followed, "fallback", False),
                )
            )
            follower_fallback_applied = bool(
                getattr(
                    followed,
                    "fallback_applied",
                    getattr(followed, "fallback", False),
                )
            )
            fallback_requested = (
                governor_fallback_requested or follower_fallback_requested
            )
            fallback_applied = (
                governor_fallback_applied or follower_fallback_applied
            )
            fallback_reasons = [
                str(value)
                for value in (
                    getattr(governed, "fallback_reason", ""),
                    getattr(followed, "fallback_reason", ""),
                )
                if value
            ]
            cycle_profile_rows, command_jerk = _profile_rows(
                getattr(followed, "command_profile", None),
                method_id=method_spec.method_id,
                cycle_index=cycle_index,
                control_time=control_time,
                command_time=command_time,
            )
            profile_rows.extend(cycle_profile_rows)
            row.update(
                {
                    "command_time_s": command_time,
                    "command_position_rad": float(command[0, 0]),
                    "command_velocity_rad_s": float(command[0, 1]),
                    "command_acceleration_rad_s2": float(command[0, 2]),
                    "command_jerk_rad_s3": _scalar(
                        getattr(followed, "command_jerk", None)
                    ),
                    "requested_target_free_duration_s": _scalar(
                        getattr(
                            followed,
                            "requested_target_free_trajectory_duration",
                            None,
                        )
                    ),
                    "frozen_trajectory_duration_s": _scalar(
                        getattr(followed, "frozen_trajectory_duration", None)
                    ),
                    "follower_status": str(
                        getattr(followed, "solver_status", "ok")
                    ),
                    # Detailed native statuses remain in the two layer fields.
                    # Reaching a committed command is the pipeline-level
                    # success condition consumed by generic metric analysis.
                    "solver_status": "ok",
                    "fallback_requested": fallback_requested,
                    "fallback_applied": fallback_applied,
                    "fallback_reason": ";".join(fallback_reasons),
                    "safety_guarantee": bool(
                        getattr(governed, "safety_guarantee", True)
                        and getattr(followed, "safety_guarantee", True)
                    ),
                    "emergency_mode": bool(
                        getattr(governed, "emergency_mode", False)
                        or getattr(followed, "emergency_mode", False)
                    ),
                    "status": "ok",
                }
            )
            current_state = command
            command_states.append(command[0].copy())
            command_jerks.append(command_jerk)
        except Exception as error:
            row["runtime_total_us"] = (perf_counter_ns() - cycle_started) / 1000.0
            row["deadline_miss"] = row["runtime_total_us"] > dt_s * 1e6
            row["status"] = "failed"
            row["error_layer"] = active_layer
            row["error_reason"] = _failure_reason(error)
            trace_rows.append(row)
            return _finish_failure(
                reference=reference,
                method_spec=method_spec,
                run_config=run_config,
                dt_s=dt_s,
                fingerprint=fingerprint,
                command_states=command_states,
                command_jerks=command_jerks,
                trace_rows=trace_rows,
                profile_rows=profile_rows,
                total_cycles=total_cycles,
                failure_layer=active_layer,
                error=error,
            )

        row["runtime_total_us"] = (perf_counter_ns() - cycle_started) / 1000.0
        row["deadline_miss"] = row["runtime_total_us"] > dt_s * 1e6
        trace_rows.append(row)

    return TrackingRun(
        method_id=method_spec.method_id,
        command=_command_trajectory(reference, dt_s, command_states, command_jerks),
        trace_rows=tuple(trace_rows),
        profile_rows=tuple(profile_rows),
        status=_status(
            completed=True,
            fingerprint=fingerprint,
            valid_cycles=total_cycles,
            total_cycles=total_cycles,
        ),
    )


__all__ = [
    "PROFILE_FIELDS",
    "TRACE_FIELDS",
    "TrackingExecutionError",
    "method_fingerprint",
    "run_tracking",
]
