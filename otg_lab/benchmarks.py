"""Pure research-benchmark orchestration for estimator and predictor studies.

The functions in this module deliberately return in-memory
:class:`pandas.DataFrame` objects (and, when requested, canonical sample rows).
They never choose an output directory or write an artifact.  This keeps split
selection, artifact provenance, and filesystem policy in the experiment CLI.

Two timing rules are central here:

* estimator errors are evaluated at ``posterior.state_time``;
* a configured prediction horizon is measured from ``control_time`` while the
  predictor's actual propagation interval starts at ``posterior.state_time``.

Consequently a delayed posterior can have a 20 ms propagation interval in a
10 ms configured-horizon cell.  Both values are reported instead of hiding
the estimator delay.
"""

from __future__ import annotations

import copy
import gc
import hashlib
import inspect
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from time import perf_counter_ns
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from ruckig import InputParameter, Ruckig, RuckigError, Trajectory

from .artifacts import runtime_environment
from .estimators import Estimator, make_estimator
from .pipeline import EstimatorPredictorPipeline
from .predictors import Predictor, make_predictor
from .schema import FIELD_NAMES, validate_samples
from .types import Measurement, TimedState

PRIMARY_HORIZONS_MS: tuple[float, ...] = (0.0, 10.0, 20.0, 40.0, 50.0, 60.0)
STRESS_HORIZONS_MS: tuple[float, ...] = (80.0, 100.0, 150.0)
SELECTION_SPLITS = frozenset({"train", "pilot", "validation"})

ACCELERATION_ACTIVE_PHASES: tuple[str, ...] = (
    "constant_acceleration",
    "acceleration_sign_reversal",
    "rapid_braking",
    "near_amax",
    "nonzero_acceleration_at_moving_target",
    "high_jerk_feasible",
    "stop_restart",
)
DEFAULT_RATIO_STRATA: Mapping[str, float] = {
    "low": 0.20,
    "medium": 0.50,
    "high": 0.75,
    "near_limit": 0.93,
}


class BenchmarkValidationError(ValueError):
    """Raised when benchmark inputs cannot support the requested inference."""


class SelectionLeakageError(BenchmarkValidationError):
    """Raised when locked-test data is presented to a selection operation."""


class FreeDurationUnavailable(BenchmarkValidationError):
    """Raised when a raw target has no valid unconstrained Ruckig duration."""


# Descriptive alias used by callers that prefer the term in the protocol.
SplitLeakageError = SelectionLeakageError


def ruckig_unconstrained_free_duration(
    prediction: TimedState,
    posterior: TimedState,
    row: Mapping[str, Any] | None = None,
    *,
    max_velocity: float | Sequence[float] = 4.1,
    max_acceleration: float | Sequence[float] = 8.2,
    max_jerk: float | Sequence[float] = 4000.0,
    control_dt: float = 0.01,
) -> float:
    """Return genuine Ruckig ``T_free`` without setting minimum duration.

    This frozen diagnostic connects the represented posterior to the raw
    predicted target.  It intentionally does not use ``prediction_horizon``
    as ``minimum_duration``.  A negative Ruckig result is an explicit
    benchmark failure because the raw target has no valid duration under the
    declared limits.
    """

    del row
    if prediction.dof != posterior.dof:
        raise BenchmarkValidationError("posterior/prediction DoF mismatch")
    if not math.isfinite(control_dt) or control_dt <= 0.0:
        raise BenchmarkValidationError("control_dt must be finite and positive")
    dof = prediction.dof

    def limit_vector(value: float | Sequence[float], name: str) -> list[float]:
        array = np.asarray(value, dtype=float)
        if array.ndim == 0:
            array = np.full(dof, float(array))
        if array.shape != (dof,) or not np.all(np.isfinite(array)) or np.any(array <= 0):
            raise BenchmarkValidationError(
                f"{name} must be a positive scalar or length-{dof} vector"
            )
        return array.tolist()

    inp = InputParameter(dof)
    inp.current_position = posterior.position.tolist()
    inp.current_velocity = posterior.velocity.tolist()
    inp.current_acceleration = posterior.acceleration.tolist()
    inp.target_position = prediction.position.tolist()
    inp.target_velocity = prediction.velocity.tolist()
    inp.target_acceleration = prediction.acceleration.tolist()
    inp.max_velocity = limit_vector(max_velocity, "max_velocity")
    inp.max_acceleration = limit_vector(max_acceleration, "max_acceleration")
    inp.max_jerk = limit_vector(max_jerk, "max_jerk")
    for state_name, values, bounds in (
        ("current velocity", posterior.velocity, inp.max_velocity),
        ("target velocity", prediction.velocity, inp.max_velocity),
        ("current acceleration", posterior.acceleration, inp.max_acceleration),
        ("target acceleration", prediction.acceleration, inp.max_acceleration),
    ):
        value = np.asarray(values, dtype=float)
        bound = np.asarray(bounds, dtype=float)
        violated = np.flatnonzero(np.abs(value) > bound + 1e-12)
        if violated.size:
            joint = int(violated[0])
            raise FreeDurationUnavailable(
                f"{state_name} {value[joint]:.9g} at DoF {joint} exceeds "
                f"limit {bound[joint]:.9g}"
            )
    trajectory = Trajectory(dof)
    try:
        result = Ruckig(dof, float(control_dt)).calculate(inp, trajectory)
    except RuckigError as error:
        raise FreeDurationUnavailable(
            f"unconstrained frozen Ruckig solve rejected the raw target: {error}"
        ) from error
    if int(result) < 0:
        raise FreeDurationUnavailable(
            f"unconstrained frozen Ruckig solve failed with result {int(result)}"
        )
    duration = float(trajectory.duration)
    if not math.isfinite(duration) or duration < 0.0:
        raise FreeDurationUnavailable("Ruckig returned an invalid free duration")
    return duration


@dataclass(frozen=True)
class _MethodSpec:
    method: str
    params: Mapping[str, Any]
    spec_id: str


@dataclass(frozen=True)
class _Trajectory:
    identity: Mapping[str, Any]
    ticks: tuple[tuple[Mapping[str, Any], ...], ...]
    joint_ids: tuple[str, ...]
    truth_times: NDArray[np.float64]
    truth_position: NDArray[np.float64]
    truth_velocity: NDArray[np.float64]
    truth_acceleration: NDArray[np.float64]
    truth_jerk: NDArray[np.float64]


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _records(rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(rows, pd.DataFrame):
        records = rows.to_dict(orient="records")
    else:
        records = [dict(row) for row in rows]
    if not records:
        raise BenchmarkValidationError("benchmark input contains no rows")
    return records


def _require_fields(
    records: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    context: str,
) -> None:
    for index, row in enumerate(records):
        missing = [field for field in fields if field not in row]
        if missing:
            raise BenchmarkValidationError(
                f"{context} row {index} is missing fields {missing}"
            )


def _guard_selection_splits(
    records: Sequence[Mapping[str, Any]],
    allowed_splits: Iterable[str],
    context: str,
) -> tuple[str, ...]:
    allowed = tuple(dict.fromkeys(str(value) for value in allowed_splits))
    if not allowed:
        raise BenchmarkValidationError("selection_splits cannot be empty")
    if "test" in allowed:
        raise SelectionLeakageError(
            f"{context}: test may not be declared as a selection split"
        )
    invalid_declared = set(allowed) - SELECTION_SPLITS
    if invalid_declared:
        raise BenchmarkValidationError(
            f"{context}: unsupported selection splits {sorted(invalid_declared)}; "
            "use train, pilot, and/or validation"
        )
    observed = {str(row.get("split", "")) for row in records}
    if "test" in observed:
        raise SelectionLeakageError(
            f"{context}: locked test trajectories cannot enter parameter or "
            "horizon selection"
        )
    unexpected = observed - set(allowed)
    if unexpected:
        raise BenchmarkValidationError(
            f"{context}: observed splits {sorted(unexpected)} are outside the "
            f"declared selection splits {list(allowed)}"
        )
    return allowed


def _is_option_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))


def _cartesian_parameters(parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand a compact parameter grid in deterministic insertion order."""

    if not parameters:
        return [{}]
    keys = list(parameters)
    options: list[list[Any]] = []
    for key in keys:
        value = parameters[key]
        if _is_option_sequence(value):
            choices = list(value)
            if not choices:
                raise BenchmarkValidationError(
                    f"parameter grid option {key!r} is empty"
                )
        else:
            choices = [value]
        options.append(choices)
    return [dict(zip(keys, values)) for values in product(*options)]


def _spec_from_mapping(
    raw: Mapping[str, Any],
    *,
    kind: str,
    method_hint: str | None = None,
) -> list[_MethodSpec]:
    method_keys = ("method", kind, "name")
    method = method_hint
    for key in method_keys:
        if raw.get(key) is not None:
            method = str(raw[key])
            break
    if method is None:
        raise BenchmarkValidationError(f"{kind} specification has no method name")

    identifier = raw.get("id") or raw.get("spec_id")
    explicit_identifier = raw.get(f"{kind}_id")
    # When a separate method field exists, estimator_id/predictor_id is an
    # explicit configuration label.  Otherwise it may itself be the method.
    if identifier is None and any(key in raw for key in method_keys):
        identifier = explicit_identifier

    reserved = {
        "method",
        kind,
        "name",
        "id",
        "spec_id",
        f"{kind}_id",
        "params",
        "parameters",
        f"{kind}_parameters",
        "rank",
        "selected",
        "selection_rank",
        "selection_score",
        "selection_splits",
        "params_json",
    }
    nested = raw.get("params")
    if nested is None:
        nested = raw.get("parameters")
    if nested is None:
        nested = raw.get(f"{kind}_parameters")
    if nested is None and "params_json" in raw:
        nested = json.loads(str(raw["params_json"]))
    if nested is None:
        nested = {key: value for key, value in raw.items() if key not in reserved}
    if not isinstance(nested, Mapping):
        raise BenchmarkValidationError(f"{kind} parameters must be a mapping")

    expanded = _cartesian_parameters(nested)
    output: list[_MethodSpec] = []
    for index, params in enumerate(expanded):
        serialized = _stable_json(params)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:10]
        generated = str(method) if not params else f"{method}:{digest}"
        spec_id = str(identifier or generated)
        if len(expanded) > 1 and identifier is not None:
            spec_id = f"{spec_id}:{index:03d}"
        output.append(_MethodSpec(str(method), dict(params), spec_id))
    return output


def _expand_method_grid(parameter_grid: Any, *, kind: str) -> list[_MethodSpec]:
    """Normalize explicit lists and ``method -> parameter grid`` mappings."""

    specs: list[_MethodSpec] = []
    if isinstance(parameter_grid, str):
        specs = [_MethodSpec(parameter_grid, {}, parameter_grid)]
    elif isinstance(parameter_grid, Mapping):
        if any(key in parameter_grid for key in ("method", kind, "name")):
            specs.extend(_spec_from_mapping(parameter_grid, kind=kind))
        elif f"locked_{kind}s" in parameter_grid:
            return _expand_method_grid(parameter_grid[f"locked_{kind}s"], kind=kind)
        else:
            for method, raw_values in parameter_grid.items():
                if raw_values is None:
                    specs.append(_MethodSpec(str(method), {}, str(method)))
                elif isinstance(raw_values, Mapping):
                    specs.extend(
                        _spec_from_mapping(
                            raw_values, kind=kind, method_hint=str(method)
                        )
                    )
                elif isinstance(raw_values, Sequence) and not isinstance(
                    raw_values, (str, bytes)
                ):
                    if not raw_values:
                        raise BenchmarkValidationError(
                            f"{kind} grid for {method!r} is empty"
                        )
                    for raw in raw_values:
                        if not isinstance(raw, Mapping):
                            raise BenchmarkValidationError(
                                f"explicit {kind} configurations must be mappings"
                            )
                        specs.extend(
                            _spec_from_mapping(raw, kind=kind, method_hint=str(method))
                        )
                else:
                    raise BenchmarkValidationError(
                        f"unsupported {kind} grid value for {method!r}"
                    )
    elif isinstance(parameter_grid, Sequence):
        for raw in parameter_grid:
            if isinstance(raw, str):
                specs.append(_MethodSpec(raw, {}, raw))
            elif isinstance(raw, Mapping):
                specs.extend(_spec_from_mapping(raw, kind=kind))
            else:
                raise BenchmarkValidationError(
                    f"{kind} grid entries must be strings or mappings"
                )
    else:
        raise BenchmarkValidationError(
            f"{kind} parameter_grid must be a mapping or sequence"
        )

    if not specs:
        raise BenchmarkValidationError(f"{kind} parameter grid is empty")
    identifiers = [spec.spec_id for spec in specs]
    if len(set(identifiers)) != len(identifiers):
        duplicates = sorted(
            identifier
            for identifier in set(identifiers)
            if identifiers.count(identifier) > 1
        )
        raise BenchmarkValidationError(
            f"{kind} configuration IDs are not unique: {duplicates}"
        )
    return specs


def expand_estimator_grid(parameter_grid: Any) -> pd.DataFrame:
    """Return the fully expanded estimator grid without running data."""

    specs = _expand_method_grid(parameter_grid, kind="estimator")
    return pd.DataFrame(
        [
            {
                "estimator_id": spec.spec_id,
                "estimator": spec.method,
                "params_json": _stable_json(spec.params),
                "estimator_parameters": dict(spec.params),
            }
            for spec in specs
        ]
    )


def _trajectory_identity_fields(
    records: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    preferred = (
        "run_id",
        "dataset_id",
        "session_id",
        "trajectory_id",
        "split",
        "scenario_id",
        "seed",
        "reference_family",
        "reference_variant",
    )
    return tuple(field for field in preferred if all(field in row for row in records))


def _trajectory_bundles(records: Sequence[Mapping[str, Any]]) -> list[_Trajectory]:
    required = (
        "trajectory_id",
        "split",
        "joint_id",
        "k",
        "source_time",
        "arrival_time",
        "control_time",
        "p_ref",
        "v_ref_truth",
        "a_ref_truth",
        "j_ref_truth",
        "p_meas",
        "measurement_available",
    )
    _require_fields(records, required, "synthetic benchmark")
    identity_fields = _trajectory_identity_fields(records)
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        groups[tuple(row[field] for field in identity_fields)].append(row)

    trajectories: list[_Trajectory] = []
    for group_key in sorted(
        groups, key=lambda value: tuple(str(item) for item in value)
    ):
        group = groups[group_key]
        identity = dict(zip(identity_fields, group_key))
        by_k: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for row in group:
            by_k[int(row["k"])].append(row)
        ticks: list[tuple[Mapping[str, Any], ...]] = []
        expected_joints: tuple[str, ...] | None = None
        for k in sorted(by_k):
            tick = tuple(sorted(by_k[k], key=lambda row: str(row["joint_id"])))
            joints = tuple(str(row["joint_id"]) for row in tick)
            if len(set(joints)) != len(joints):
                raise BenchmarkValidationError(
                    f"trajectory {identity.get('trajectory_id')!r}, k={k}: "
                    "duplicate joint rows"
                )
            if expected_joints is None:
                expected_joints = joints
            elif joints != expected_joints:
                raise BenchmarkValidationError(
                    f"trajectory {identity.get('trajectory_id')!r}: joint set/order changed"
                )
            source_times = np.asarray([float(row["source_time"]) for row in tick])
            control_times = np.asarray([float(row["control_time"]) for row in tick])
            if not np.allclose(source_times, source_times[0], rtol=0.0, atol=1e-12):
                raise BenchmarkValidationError(
                    "estimator/predictor truth evaluation requires synchronized "
                    "source times across joints"
                )
            if not np.allclose(control_times, control_times[0], rtol=0.0, atol=1e-12):
                raise BenchmarkValidationError(
                    "estimator/predictor benchmark requires synchronized control times"
                )
            ticks.append(tick)
        if expected_joints is None or not ticks:
            raise BenchmarkValidationError("trajectory contains no synchronized ticks")

        truth_times = np.asarray([float(tick[0]["source_time"]) for tick in ticks])
        if not np.all(np.isfinite(truth_times)) or np.any(np.diff(truth_times) <= 0.0):
            raise BenchmarkValidationError(
                f"trajectory {identity.get('trajectory_id')!r}: synthetic truth "
                "times must be finite and strictly increasing"
            )

        bound_trajectory_id = identity.get("trajectory_id")

        def truth_matrix(
            field: str,
            tick_rows: tuple[tuple[Mapping[str, Any], ...], ...] = tuple(ticks),
            trajectory_id: Any = bound_trajectory_id,
        ) -> NDArray[np.float64]:
            if any(row[field] is None for tick in tick_rows for row in tick):
                raise BenchmarkValidationError(
                    f"trajectory {trajectory_id!r}: {field} is "
                    "required genuine synthetic truth"
                )
            value = np.asarray(
                [[float(row[field]) for row in tick] for tick in tick_rows], dtype=float
            )
            if not np.all(np.isfinite(value)):
                raise BenchmarkValidationError(f"{field} contains NaN or infinity")
            return value

        trajectories.append(
            _Trajectory(
                identity=identity,
                ticks=tuple(ticks),
                joint_ids=expected_joints,
                truth_times=truth_times,
                truth_position=truth_matrix("p_ref"),
                truth_velocity=truth_matrix("v_ref_truth"),
                truth_acceleration=truth_matrix("a_ref_truth"),
                truth_jerk=truth_matrix("j_ref_truth"),
            )
        )
    return trajectories


def _infer_nominal_dt(trajectories: Sequence[_Trajectory]) -> float:
    differences = np.concatenate(
        [np.diff(trajectory.truth_times) for trajectory in trajectories]
    )
    if differences.size == 0:
        raise BenchmarkValidationError("at least two samples are required to infer dt")
    nominal_dt = float(np.median(differences))
    if not math.isfinite(nominal_dt) or nominal_dt <= 0.0:
        raise BenchmarkValidationError("could not infer a positive nominal_dt")
    return nominal_dt


def _measurement(tick: Sequence[Mapping[str, Any]]) -> tuple[Measurement, float] | None:
    available = [
        bool(row.get("measurement_available", row.get("p_meas") is not None))
        and row.get("p_meas") is not None
        for row in tick
    ]
    if not all(available):
        return None
    source_times = [float(row["source_time"]) for row in tick]
    arrival_times = [float(row["arrival_time"]) for row in tick]
    control_times = [float(row["control_time"]) for row in tick]
    control_time = max(control_times)
    if max(arrival_times) > control_time + 1e-12:
        # The measurement is not yet visible at this control tick.  Selection
        # datasets are normally clean; skipping is explicit and measurable via
        # evaluated_time_fraction.
        return None
    measurement = Measurement(
        position=[float(row["p_meas"]) for row in tick],
        state_time=max(source_times),
        available_time=max(max(arrival_times), max(source_times)),
        velocity=(
            None
            if any(row.get("v_meas") is None for row in tick)
            else [float(row["v_meas"]) for row in tick]
        ),
        acceleration=(
            None
            if any(row.get("a_meas") is None for row in tick)
            else [float(row["a_meas"]) for row in tick]
        ),
        metadata={
            "control_time": control_time,
            "joint_ids": [str(row["joint_id"]) for row in tick],
            "sample_index": int(tick[0]["k"]),
        },
    )
    return measurement, control_time


def _interpolate_truth(
    truth_times: NDArray[np.float64],
    truth_values: NDArray[np.float64],
    query_times: NDArray[np.float64],
) -> NDArray[np.float64]:
    if query_times.ndim != 1 or not np.all(np.isfinite(query_times)):
        raise BenchmarkValidationError("state query times must be a finite vector")
    tolerance = (
        32.0
        * np.finfo(float).eps
        * max(
            1.0,
            float(np.max(np.abs(truth_times))),
            float(np.max(np.abs(query_times))) if query_times.size else 1.0,
        )
    )
    if np.any(query_times < truth_times[0] - tolerance) or np.any(
        query_times > truth_times[-1] + tolerance
    ):
        raise BenchmarkValidationError(
            "truth interpolation would extrapolate beyond the synthetic trajectory"
        )
    clipped = np.clip(query_times, truth_times[0], truth_times[-1])
    return np.column_stack(
        [
            np.interp(clipped, truth_times, truth_values[:, joint])
            for joint in range(truth_values.shape[1])
        ]
    )


def _error_summary(
    estimate: NDArray[np.float64],
    truth: NDArray[np.float64],
    prefix: str,
) -> dict[str, float]:
    if estimate.shape != truth.shape or estimate.size == 0:
        raise BenchmarkValidationError(
            f"{prefix} estimate/truth arrays must be equal and non-empty"
        )
    error = estimate - truth
    absolute = np.abs(error)
    return {
        f"{prefix}_rmse": float(np.sqrt(np.mean(np.square(error)))),
        f"{prefix}_mae": float(np.mean(absolute)),
        f"{prefix}_bias": float(np.mean(error)),
        f"{prefix}_max_abs_error": float(np.max(absolute)),
    }


def _quantile(
    values: Sequence[float] | NDArray[np.float64], probability: float
) -> float:
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or array.size == 0 or not np.all(np.isfinite(array)):
        raise BenchmarkValidationError(
            "runtime/ratio values must be finite and non-empty"
        )
    return float(np.quantile(array, probability, method="linear"))


def _timing_summary(values: Sequence[float], prefix: str) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if (
        array.ndim != 1
        or array.size == 0
        or np.any(array < 0.0)
        or not np.all(np.isfinite(array))
    ):
        raise BenchmarkValidationError(f"{prefix} timing samples are invalid")
    p999 = _quantile(array, 0.999)
    return {
        f"{prefix}_mean_us": float(np.mean(array)),
        f"{prefix}_p50_us": _quantile(array, 0.50),
        f"{prefix}_p90_us": _quantile(array, 0.90),
        f"{prefix}_p99_us": _quantile(array, 0.99),
        f"{prefix}_p999_us": p999,
        f"{prefix}_p99_9_us": p999,
        f"{prefix}_max_us": float(np.max(array)),
    }


def _construct_estimator(
    factory: Callable[..., Estimator], spec: _MethodSpec, nominal_dt: float
) -> tuple[Estimator, dict[str, Any]]:
    params = dict(spec.params)
    if "dt" not in params and "nominal_dt" not in params:
        params["nominal_dt"] = nominal_dt
    estimator = factory(spec.method, **params)
    if not isinstance(estimator, Estimator):
        raise BenchmarkValidationError("estimator_factory must return Estimator")
    return estimator, params


def _canonical_input(records: Sequence[Mapping[str, Any]]) -> bool:
    return all(set(FIELD_NAMES) == set(row) for row in records)


def _validate_optional_canonical(rows: Sequence[Mapping[str, Any]]) -> None:
    if rows and _canonical_input(rows):
        validate_samples(rows)


def _estimator_trajectory(
    trajectory: _Trajectory,
    spec: _MethodSpec,
    *,
    nominal_dt: float,
    estimator_factory: Callable[..., Estimator],
    include_rows: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    estimator, resolved_params = _construct_estimator(
        estimator_factory, spec, nominal_dt
    )
    posterior_states: list[TimedState] = []
    controls: list[float] = []
    canonical: list[dict[str, Any]] = []
    startup_control_times: list[float] = []

    for tick in trajectory.ticks:
        measurement_cycle = _measurement(tick)
        posterior: TimedState | None = None
        control_time = float(tick[0]["control_time"])
        if measurement_cycle is not None:
            measurement, control_time = measurement_cycle
            posterior = estimator.update(measurement)
            posterior_states.append(posterior)
            controls.append(control_time)
            if posterior.startup:
                startup_control_times.append(control_time)
        if include_rows:
            for joint, original in enumerate(tick):
                row = copy.deepcopy(dict(original))
                row["estimator_id"] = spec.spec_id
                if posterior is not None:
                    row["posterior_p"] = float(posterior.position[joint])
                    row["posterior_v"] = float(posterior.velocity[joint])
                    row["posterior_a"] = float(posterior.acceleration[joint])
                    row["posterior_state_time"] = float(posterior.state_time)
                    row["posterior_available_time"] = float(posterior.available_time)
                    row["estimator_compute_us"] = float(posterior.compute_time_us)
                    if "state_reset" in row:
                        row["state_reset"] = bool(
                            row.get("state_reset", False) or "reset" in posterior.status
                        )
                    if "invalid_input" in row:
                        row["invalid_input"] = bool(
                            row.get("invalid_input", False) or not posterior.valid
                        )
                canonical.append(row)

    if not posterior_states:
        raise BenchmarkValidationError(
            f"trajectory {trajectory.identity.get('trajectory_id')!r} produced "
            "no estimator posterior"
        )
    query_times = np.asarray([state.state_time for state in posterior_states])
    estimates = {
        "p": np.vstack([state.position for state in posterior_states]),
        "v": np.vstack([state.velocity for state in posterior_states]),
        "a": np.vstack([state.acceleration for state in posterior_states]),
    }
    truths = {
        "p": _interpolate_truth(
            trajectory.truth_times, trajectory.truth_position, query_times
        ),
        "v": _interpolate_truth(
            trajectory.truth_times, trajectory.truth_velocity, query_times
        ),
        "a": _interpolate_truth(
            trajectory.truth_times, trajectory.truth_acceleration, query_times
        ),
    }
    metric: dict[str, Any] = {
        **dict(trajectory.identity),
        "method": spec.spec_id,
        "estimator_id": spec.spec_id,
        "estimator": spec.method,
        "params_json": _stable_json(resolved_params),
        "estimator_parameters": dict(resolved_params),
        "n_samples": len(trajectory.ticks),
        "evaluated_samples": len(posterior_states),
        "evaluated_time_fraction": len(posterior_states) / len(trajectory.ticks),
    }
    for component in ("p", "v", "a"):
        summary = _error_summary(estimates[component], truths[component], component)
        metric.update(summary)
        for key, value in summary.items():
            metric[f"estimator_{key}"] = value

    control = np.asarray(controls, dtype=float)
    state_time = np.asarray([state.state_time for state in posterior_states])
    available_time = np.asarray([state.available_time for state in posterior_states])
    startup = np.asarray([state.startup for state in posterior_states], dtype=bool)
    recovered_indices = np.flatnonzero(~startup)
    startup_recovered = bool(recovered_indices.size)
    if startup_recovered:
        recovery_time = float(control[int(recovered_indices[0])] - control[0])
    else:
        recovery_time = float(control[-1] - control[0])
    metric.update(
        {
            "posterior_lag_s": float(np.mean(control - state_time)),
            "posterior_max_lag_s": float(np.max(control - state_time)),
            "posterior_availability_lag_s": float(np.mean(available_time - state_time)),
            "startup_samples": int(np.sum(startup)),
            "startup_fraction": float(np.mean(startup)),
            "startup_recovered": startup_recovered,
            "startup_recovery_time_s": recovery_time,
        }
    )
    timing = _timing_summary(
        [state.compute_time_us for state in posterior_states], "estimator"
    )
    metric.update(timing)
    metric.update(
        {
            "compute_p50_us": timing["estimator_p50_us"],
            "compute_p90_us": timing["estimator_p90_us"],
            "compute_p99_us": timing["estimator_p99_us"],
            "compute_p999_us": timing["estimator_p999_us"],
            "compute_max_us": timing["estimator_max_us"],
        }
    )
    return metric, canonical, resolved_params


def _run_estimator_core(
    records: Sequence[Mapping[str, Any]],
    specs: Sequence[_MethodSpec],
    *,
    nominal_dt: float | None,
    estimator_factory: Callable[..., Estimator],
    return_canonical_rows: bool,
) -> pd.DataFrame | tuple[pd.DataFrame, list[dict[str, Any]]]:
    trajectories = _trajectory_bundles(records)
    actual_dt = (
        _infer_nominal_dt(trajectories) if nominal_dt is None else float(nominal_dt)
    )
    if not math.isfinite(actual_dt) or actual_dt <= 0.0:
        raise BenchmarkValidationError("nominal_dt must be finite and positive")
    metrics: list[dict[str, Any]] = []
    canonical: list[dict[str, Any]] = []
    for spec in specs:
        for trajectory in trajectories:
            metric, output_rows, _ = _estimator_trajectory(
                trajectory,
                spec,
                nominal_dt=actual_dt,
                estimator_factory=estimator_factory,
                include_rows=return_canonical_rows,
            )
            metrics.append(metric)
            canonical.extend(output_rows)
    frame = (
        pd.DataFrame(metrics)
        .sort_values(["estimator_id", "split", "trajectory_id"], kind="stable")
        .reset_index(drop=True)
    )
    if not return_canonical_rows:
        return frame
    _validate_optional_canonical(canonical)
    return frame, canonical


def run_estimator_grid(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    parameter_grid: Any,
    *,
    nominal_dt: float | None = None,
    selection_splits: Iterable[str] = ("train", "pilot", "validation"),
    estimator_factory: Callable[..., Estimator] = make_estimator,
    return_canonical_rows: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Evaluate a complete estimator parameter grid on selection data only.

    ``parameter_grid`` accepts either explicit specifications, for example
    ``[{"method": "ca_kf", "params": {...}}]``, or a compact mapping such as
    ``{"local_poly": {"window": [5, 7], "degree": [2, 3],
    "lag_samples": [1]}}``.  One output row is produced per complete
    trajectory and parameter cell.

    Locked-test rows are rejected unconditionally.  Use
    :func:`evaluate_locked_estimator` after parameters have been frozen.
    """

    records = _records(rows)
    _guard_selection_splits(records, selection_splits, "estimator grid")
    specs = _expand_method_grid(parameter_grid, kind="estimator")
    return _run_estimator_core(
        records,
        specs,
        nominal_dt=nominal_dt,
        estimator_factory=estimator_factory,
        return_canonical_rows=return_canonical_rows,
    )


def evaluate_locked_estimator(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    locked_estimator: Mapping[str, Any] | Sequence[Mapping[str, Any]] | str,
    *,
    nominal_dt: float | None = None,
    estimator_factory: Callable[..., Estimator] = make_estimator,
    return_canonical_rows: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Evaluate already locked estimator specifications, including on test."""

    records = _records(rows)
    specs = _expand_method_grid(locked_estimator, kind="estimator")
    return _run_estimator_core(
        records,
        specs,
        nominal_dt=nominal_dt,
        estimator_factory=estimator_factory,
        return_canonical_rows=return_canonical_rows,
    )


def _selection_frame(
    metrics: pd.DataFrame | Iterable[Mapping[str, Any]],
    selection_splits: Iterable[str],
    context: str,
) -> pd.DataFrame:
    frame = (
        metrics.copy(deep=True)
        if isinstance(metrics, pd.DataFrame)
        else pd.DataFrame([dict(row) for row in metrics])
    )
    if frame.empty:
        raise BenchmarkValidationError(f"{context} metrics are empty")
    if "split" not in frame:
        raise BenchmarkValidationError(f"{context} metrics have no split column")
    records = frame[["split"]].to_dict(orient="records")
    allowed = _guard_selection_splits(records, selection_splits, context)
    selected = frame[frame["split"].astype(str).isin(allowed)].copy()
    if selected.empty:
        raise BenchmarkValidationError(f"{context} has no rows in selection splits")
    return selected


def rank_estimator_grid(
    trajectory_metrics: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    selection_splits: Iterable[str] = ("validation",),
    objective_metrics: Sequence[str] = (
        "estimator_v_rmse",
        "estimator_a_rmse",
        "estimator_p_rmse",
    ),
    objective_weights: Sequence[float] | None = None,
) -> pd.DataFrame:
    """Rank every grid cell using trajectory-level validation means.

    Metrics are converted to within-grid fractional ranks before weighting, so
    position, velocity, and acceleration units are not added directly.  The
    returned table retains every grid cell and its resolved parameter JSON.
    """

    selection_splits = tuple(selection_splits)
    frame = _selection_frame(trajectory_metrics, selection_splits, "estimator ranking")
    required = {"estimator_id", "estimator", "params_json", *objective_metrics}
    missing = required - set(frame)
    if missing:
        raise BenchmarkValidationError(
            f"estimator ranking is missing columns {sorted(missing)}"
        )
    weights = (
        np.ones(len(objective_metrics), dtype=float)
        if objective_weights is None
        else np.asarray(objective_weights, dtype=float)
    )
    if weights.shape != (len(objective_metrics),) or not np.all(np.isfinite(weights)):
        raise BenchmarkValidationError("objective_weights has the wrong shape/value")
    if np.any(weights < 0.0) or not np.any(weights > 0.0):
        raise BenchmarkValidationError(
            "objective_weights must be non-negative and nonzero"
        )
    weights = weights / np.sum(weights)

    unit_fields = [
        field
        for field in ("dataset_id", "session_id", "trajectory_id", "scenario_id")
        if field in frame
    ]
    if "trajectory_id" not in unit_fields:
        raise BenchmarkValidationError("estimator ranking requires trajectory_id")
    units_by_estimator = {
        str(estimator_id): set(map(tuple, group[unit_fields].to_numpy().tolist()))
        for estimator_id, group in frame.groupby(
            "estimator_id", sort=True, dropna=False
        )
    }
    reference_units = next(iter(units_by_estimator.values()))
    if any(units != reference_units for units in units_by_estimator.values()):
        raise BenchmarkValidationError(
            "estimator grid cells do not cover the same trajectory units"
        )
    for estimator_id, group in frame.groupby("estimator_id", sort=True, dropna=False):
        if (
            group["estimator"].astype(str).nunique() != 1
            or group["params_json"].astype(str).nunique() != 1
        ):
            raise BenchmarkValidationError(
                f"estimator ID {estimator_id!r} maps to conflicting methods/parameters"
            )

    aggregations: dict[str, tuple[str, str]] = {
        "estimator": ("estimator", "first"),
        "params_json": ("params_json", "first"),
        "n_trajectories": ("trajectory_id", "nunique"),
    }
    for metric in objective_metrics:
        values = pd.to_numeric(frame[metric], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy()).all():
            raise BenchmarkValidationError(f"objective metric {metric} is non-finite")
        aggregations[f"mean_{metric}"] = (metric, "mean")
        aggregations[f"median_{metric}"] = (metric, "median")
    ranked = frame.groupby("estimator_id", as_index=False, sort=True).agg(
        **aggregations
    )
    candidate_count = len(ranked)
    score = np.zeros(candidate_count, dtype=float)
    for metric, weight in zip(objective_metrics, weights):
        column = f"mean_{metric}"
        raw_rank = (
            ranked[column].rank(method="min", ascending=True).to_numpy(dtype=float)
        )
        denominator = max(1, candidate_count - 1)
        fractional = (raw_rank - 1.0) / denominator
        ranked[f"rank_{metric}"] = raw_rank.astype(int)
        score += weight * fractional
    ranked["selection_score"] = score
    ranked["selection_splits"] = ";".join(selection_splits)
    ranked["objective_metrics"] = ";".join(objective_metrics)
    ranked["objective_weights"] = _stable_json(weights.tolist())
    ranked = ranked.sort_values(
        [
            "selection_score",
            *[f"mean_{metric}" for metric in objective_metrics],
            "estimator_id",
        ],
        kind="stable",
    ).reset_index(drop=True)
    ranked.insert(0, "rank", np.arange(1, len(ranked) + 1, dtype=int))
    ranked["selected"] = ranked["rank"] == 1
    return ranked


def lock_estimator_parameters(
    ranking: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    top_k: int = 1,
) -> dict[str, Any]:
    """Create a JSON-serializable locked-estimator block from a ranking."""

    if int(top_k) != top_k or top_k < 1:
        raise BenchmarkValidationError("top_k must be a positive integer")
    frame = (
        ranking.copy(deep=True)
        if isinstance(ranking, pd.DataFrame)
        else pd.DataFrame([dict(row) for row in ranking])
    )
    required = {"rank", "estimator_id", "estimator", "params_json"}
    missing = required - set(frame)
    if frame.empty or missing:
        raise BenchmarkValidationError(
            f"estimator ranking is empty or missing {sorted(missing)}"
        )
    if (
        "selection_splits" in frame
        and frame["selection_splits"]
        .astype(str)
        .str.contains(r"(?:^|;)test(?:;|$)", regex=True)
        .any()
    ):
        raise SelectionLeakageError("cannot lock parameters from a test ranking")
    frame = frame.sort_values(["rank", "estimator_id"], kind="stable")
    if frame["rank"].duplicated().any():
        raise BenchmarkValidationError("estimator ranking contains duplicate ranks")
    if top_k > len(frame):
        raise BenchmarkValidationError(
            f"top_k={top_k} exceeds the {len(frame)} ranked estimator cells"
        )
    selected = frame.head(int(top_k))
    locked = []
    for row in selected.to_dict(orient="records"):
        params = json.loads(str(row["params_json"]))
        locked.append(
            {
                "estimator_id": str(row["estimator_id"]),
                "method": str(row["estimator"]),
                "estimator": str(row["estimator"]),
                "params": params,
                "estimator_parameters": params,
                "selection_rank": int(row["rank"]),
                "selection_score": float(row.get("selection_score", 0.0)),
            }
        )
    return {
        "locked": True,
        "selection_splits": (
            str(selected.iloc[0].get("selection_splits", "validation"))
        ),
        "objective_metrics": str(selected.iloc[0].get("objective_metrics", "")),
        "locked_estimators": locked,
    }


def _construct_predictor(
    factory: Callable[..., Predictor],
    spec: _MethodSpec,
    trajectory: _Trajectory,
) -> Predictor:
    params = dict(spec.params)
    normalized = spec.method.strip().lower().replace("-", "_")
    if normalized in {
        "oracle",
        "oracle_future_state",
        "oracle_future_state_offline",
    }:
        truth_arguments = {
            "truth_times": trajectory.truth_times,
            "truth_position": trajectory.truth_position,
            "truth_velocity": trajectory.truth_velocity,
            "truth_acceleration": trajectory.truth_acceleration,
            "truth_jerk": trajectory.truth_jerk,
            "out_of_range": "clip",
        }
        for key, value in truth_arguments.items():
            params.setdefault(key, value)
    predictor = factory(spec.method, **params)
    if not isinstance(predictor, Predictor):
        raise BenchmarkValidationError("predictor_factory must return Predictor")
    return predictor


def _truth_event_masks(
    trajectory: _Trajectory,
    query_times: NDArray[np.float64],
    *,
    event_window_s: float,
    stop_velocity_fraction: float,
    stop_velocity_absolute: float,
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    if event_window_s < 0.0 or not math.isfinite(event_window_s):
        raise BenchmarkValidationError("event_window_s must be finite and non-negative")
    if stop_velocity_fraction < 0.0 or stop_velocity_absolute < 0.0:
        raise BenchmarkValidationError("stop velocity thresholds must be non-negative")
    velocity = trajectory.truth_velocity
    peak = float(np.max(np.abs(velocity)))
    sign_threshold = max(stop_velocity_absolute, stop_velocity_fraction * peak)
    reversal_times: list[float] = []
    # Find sign transitions independently by joint.  Zeros are bridged by the
    # nearest non-zero signs, preventing a stop plateau from creating dozens of
    # artificial reversals.
    for joint in range(velocity.shape[1]):
        values = velocity[:, joint]
        signs = np.sign(np.where(np.abs(values) <= sign_threshold, 0.0, values))
        nonzero = np.flatnonzero(signs)
        for left, right in zip(nonzero[:-1], nonzero[1:]):
            if signs[left] * signs[right] >= 0.0:
                continue
            t0 = trajectory.truth_times[left]
            t1 = trajectory.truth_times[right]
            v0 = abs(values[left])
            v1 = abs(values[right])
            fraction = 0.5 if v0 + v1 == 0.0 else v0 / (v0 + v1)
            reversal_times.append(float(t0 + fraction * (t1 - t0)))
    if reversal_times:
        distances = np.min(
            np.abs(query_times[:, None] - np.asarray(reversal_times)[None, :]), axis=1
        )
        reversal = distances <= event_window_s + 1e-15
    else:
        reversal = np.zeros(query_times.size, dtype=bool)

    query_velocity = _interpolate_truth(
        trajectory.truth_times, trajectory.truth_velocity, query_times
    )
    stop = np.any(np.abs(query_velocity) <= sign_threshold + 1e-15, axis=1)
    return reversal, stop


def _subset_error_metrics(
    estimates: Mapping[str, NDArray[np.float64]],
    truths: Mapping[str, NDArray[np.float64]],
    mask: NDArray[np.bool_],
    label: str,
) -> dict[str, Any]:
    output: dict[str, Any] = {f"{label}_sample_count": int(np.sum(mask))}
    for component in ("p", "v", "a"):
        if np.any(mask):
            values = _error_summary(
                estimates[component][mask],
                truths[component][mask],
                f"{label}_{component}",
            )
            output.update(values)
            # Figure/report friendly aliases retaining the layer prefix.
            for suffix in ("rmse", "mae", "bias", "max_abs_error"):
                output[f"prediction_{label}_{component}_{suffix}"] = values[
                    f"{label}_{component}_{suffix}"
                ]
        else:
            for suffix in ("rmse", "mae", "bias", "max_abs_error"):
                output[f"{label}_{component}_{suffix}"] = math.nan
                output[f"prediction_{label}_{component}_{suffix}"] = math.nan
    return output


def _invoke_free_duration(
    function: Callable[..., float],
    prediction: TimedState,
    posterior: TimedState,
    row: Mapping[str, Any],
) -> float:
    try:
        parameter_count = len(
            [
                parameter
                for parameter in inspect.signature(function).parameters.values()
                if parameter.kind
                in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                and parameter.default is parameter.empty
            ]
        )
    except (TypeError, ValueError):
        parameter_count = 3
    if parameter_count <= 1:
        value = function(prediction)
    elif parameter_count == 2:
        value = function(prediction, posterior)
    else:
        value = function(prediction, posterior, row)
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise BenchmarkValidationError(
            "free_duration_fn must return a finite non-negative duration in seconds"
        )
    return result


def _predictor_trajectory(
    trajectory: _Trajectory,
    estimator_spec: _MethodSpec,
    predictor_spec: _MethodSpec,
    *,
    horizon_ms: float,
    horizon_set: str,
    nominal_dt: float,
    estimator_factory: Callable[..., Estimator],
    predictor_factory: Callable[..., Predictor],
    event_window_s: float,
    stop_velocity_fraction: float,
    stop_velocity_absolute: float,
    free_duration_fn: Callable[..., float] | None,
    include_rows: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    estimator, resolved_estimator_params = _construct_estimator(
        estimator_factory, estimator_spec, nominal_dt
    )
    predictor = _construct_predictor(predictor_factory, predictor_spec, trajectory)
    pipeline = EstimatorPredictorPipeline(
        estimator,
        predictor,
        prediction_horizon=horizon_ms / 1000.0,
        target_components="pva",
    )
    posteriors: list[TimedState] = []
    predictions: list[TimedState] = []
    controls: list[float] = []
    propagation_ms: list[float] = []
    canonical: list[dict[str, Any]] = []
    free_samples: list[dict[str, Any]] = []
    condition_predictor_id = f"{predictor_spec.spec_id}@{horizon_ms:g}ms"

    for tick in trajectory.ticks:
        measurement_cycle = _measurement(tick)
        cycle = None
        free_duration: float | None = None
        free_duration_status: str | None = None
        if measurement_cycle is not None:
            measurement, control_time = measurement_cycle
            cycle = pipeline.process(measurement, control_time=control_time)
            posterior = cycle.posterior
            prediction = cycle.prediction
            posteriors.append(posterior)
            predictions.append(prediction)
            controls.append(control_time)
            propagation_ms.append(cycle.propagation_horizon * 1000.0)
            if free_duration_fn is not None:
                try:
                    free_duration = _invoke_free_duration(
                        free_duration_fn, prediction, posterior, tick[0]
                    )
                    free_duration_status = "available"
                except FreeDurationUnavailable as error:
                    free_duration_status = f"unavailable: {error}"
                sample = {
                    **dict(trajectory.identity),
                    "k": int(tick[0]["k"]),
                    "control_time": control_time,
                    "estimator_id": estimator_spec.spec_id,
                    "predictor_id": predictor_spec.spec_id,
                    "configured_horizon_ms": horizon_ms,
                    "actual_propagation_horizon_ms": cycle.propagation_horizon * 1000.0,
                    "free_trajectory_duration": free_duration,
                    "free_trajectory_duration_available": free_duration is not None,
                    "free_trajectory_duration_status": free_duration_status,
                }
                free_samples.append(sample)
        if include_rows:
            for joint, original in enumerate(tick):
                row = copy.deepcopy(dict(original))
                row["estimator_id"] = estimator_spec.spec_id
                row["predictor_id"] = condition_predictor_id
                if cycle is not None:
                    posterior = cycle.posterior
                    prediction = cycle.prediction
                    row["posterior_p"] = float(posterior.position[joint])
                    row["posterior_v"] = float(posterior.velocity[joint])
                    row["posterior_a"] = float(posterior.acceleration[joint])
                    row["posterior_state_time"] = float(posterior.state_time)
                    row["posterior_available_time"] = float(posterior.available_time)
                    row["prediction_p"] = float(prediction.position[joint])
                    row["prediction_v"] = float(prediction.velocity[joint])
                    row["prediction_a"] = float(prediction.acceleration[joint])
                    row["prediction_time"] = float(prediction.state_time)
                    # Canonical prediction_horizon is propagation from the
                    # represented posterior; configured H remains in the
                    # trajectory-level benchmark result.
                    row["prediction_horizon_ms"] = float(
                        cycle.propagation_horizon * 1000.0
                    )
                    row["estimator_compute_us"] = float(posterior.compute_time_us)
                    row["predictor_compute_us"] = float(prediction.compute_time_us)
                    if free_duration_fn is not None:
                        row["free_trajectory_duration"] = free_duration
                        row["target_feasible"] = free_duration is not None
                        row["solver_status"] = free_duration_status
                canonical.append(row)

    if not predictions:
        raise BenchmarkValidationError(
            f"trajectory {trajectory.identity.get('trajectory_id')!r} produced "
            "no predictions"
        )
    prediction_times = np.asarray([state.state_time for state in predictions])
    in_range = (prediction_times >= trajectory.truth_times[0] - 1e-12) & (
        prediction_times <= trajectory.truth_times[-1] + 1e-12
    )
    if not np.any(in_range):
        raise BenchmarkValidationError("no prediction time overlaps synthetic truth")
    evaluated_times = prediction_times[in_range]
    estimates = {
        "p": np.vstack([state.position for state in predictions])[in_range],
        "v": np.vstack([state.velocity for state in predictions])[in_range],
        "a": np.vstack([state.acceleration for state in predictions])[in_range],
    }
    truths = {
        "p": _interpolate_truth(
            trajectory.truth_times, trajectory.truth_position, evaluated_times
        ),
        "v": _interpolate_truth(
            trajectory.truth_times, trajectory.truth_velocity, evaluated_times
        ),
        "a": _interpolate_truth(
            trajectory.truth_times, trajectory.truth_acceleration, evaluated_times
        ),
    }
    reversal, stop = _truth_event_masks(
        trajectory,
        evaluated_times,
        event_window_s=event_window_s,
        stop_velocity_fraction=stop_velocity_fraction,
        stop_velocity_absolute=stop_velocity_absolute,
    )
    metric: dict[str, Any] = {
        **dict(trajectory.identity),
        "method": predictor_spec.spec_id,
        "estimator_id": estimator_spec.spec_id,
        "estimator": estimator_spec.method,
        "estimator_params_json": _stable_json(resolved_estimator_params),
        "predictor_id": predictor_spec.spec_id,
        "predictor": predictor_spec.method,
        "predictor_params_json": _stable_json(predictor_spec.params),
        "configured_horizon_ms": horizon_ms,
        "prediction_horizon_ms": horizon_ms,
        "horizon_set": horizon_set,
        "actual_propagation_horizon_ms": float(np.mean(propagation_ms)),
        "actual_propagation_horizon_min_ms": float(np.min(propagation_ms)),
        "actual_propagation_horizon_p50_ms": _quantile(propagation_ms, 0.50),
        "actual_propagation_horizon_p90_ms": _quantile(propagation_ms, 0.90),
        "actual_propagation_horizon_max_ms": float(np.max(propagation_ms)),
        "posterior_lag_s": float(
            np.mean(
                np.asarray(controls)
                - np.asarray([state.state_time for state in posteriors])
            )
        ),
        "n_samples": len(trajectory.ticks),
        "predicted_samples": len(predictions),
        "evaluated_samples": int(np.sum(in_range)),
        "prediction_evaluated_time_fraction": float(np.mean(in_range)),
    }
    for component in ("p", "v", "a"):
        summary = _error_summary(
            estimates[component], truths[component], f"prediction_{component}"
        )
        metric.update(summary)
        for suffix in ("rmse", "mae", "bias", "max_abs_error"):
            metric[f"{component}_{suffix}"] = summary[
                f"prediction_{component}_{suffix}"
            ]
    metric.update(_subset_error_metrics(estimates, truths, reversal, "reversal"))
    metric.update(_subset_error_metrics(estimates, truths, stop, "stop"))
    metric.update(
        _timing_summary([state.compute_time_us for state in predictions], "predictor")
    )
    available_free_samples = [
        row for row in free_samples if row["free_trajectory_duration"] is not None
    ]
    metric.update(
        {
            "t_free_requested_samples": len(free_samples),
            "t_free_unavailable_samples": len(free_samples)
            - len(available_free_samples),
            "t_free_available_fraction": (
                len(available_free_samples) / len(free_samples)
                if free_samples
                else math.nan
            ),
        }
    )
    if available_free_samples and horizon_ms > 0.0:
        durations = np.asarray(
            [row["free_trajectory_duration"] for row in available_free_samples],
            dtype=float,
        )
        rho = durations / (horizon_ms / 1000.0)
        metric.update(
            {
                "t_free_samples": int(rho.size),
                "t_free_rho_p50": _quantile(rho, 0.50),
                "t_free_rho_p90": _quantile(rho, 0.90),
                "t_free_rho_p99": _quantile(rho, 0.99),
                "t_free_rho_max": float(np.max(rho)),
                "t_free_rho_le_one_fraction": float(np.mean(rho <= 1.0)),
            }
        )
    else:
        metric.update(
            {
                "t_free_samples": 0,
                "t_free_rho_p50": math.nan,
                "t_free_rho_p90": math.nan,
                "t_free_rho_p99": math.nan,
                "t_free_rho_max": math.nan,
                "t_free_rho_le_one_fraction": math.nan,
            }
        )
    return metric, canonical, free_samples


def _predictor_specs(parameter_grid: Any | None) -> list[_MethodSpec]:
    if parameter_grid is None:
        parameter_grid = (
            "zero_order_hold",
            "constant_velocity",
            "constant_acceleration",
            "constant_jerk",
            "local_polynomial",
        )
    return _expand_method_grid(parameter_grid, kind="predictor")


def _horizon_cells(
    horizons_ms: Sequence[float], stress_horizons_ms: Sequence[float]
) -> list[tuple[float, str]]:
    primary = [float(value) for value in horizons_ms]
    stress = [float(value) for value in stress_horizons_ms]
    all_values = primary + stress
    if not all_values or any(
        not math.isfinite(value) or value < 0.0 for value in all_values
    ):
        raise BenchmarkValidationError("horizons must be finite and non-negative")
    if len(set(all_values)) != len(all_values):
        raise BenchmarkValidationError("primary and stress horizons must be unique")
    return [(value, "primary") for value in primary] + [
        (value, "stress") for value in stress
    ]


def _run_predictor_core(
    records: Sequence[Mapping[str, Any]],
    estimator_specs: Sequence[_MethodSpec],
    predictor_specs: Sequence[_MethodSpec],
    horizon_cells: Sequence[tuple[float, str]],
    *,
    nominal_dt: float | None,
    estimator_factory: Callable[..., Estimator],
    predictor_factory: Callable[..., Predictor],
    event_window_s: float,
    stop_velocity_fraction: float,
    stop_velocity_absolute: float,
    free_duration_fn: Callable[..., float] | None,
    return_canonical_rows: bool,
) -> pd.DataFrame | tuple[pd.DataFrame, list[dict[str, Any]]]:
    trajectories = _trajectory_bundles(records)
    actual_dt = (
        _infer_nominal_dt(trajectories) if nominal_dt is None else float(nominal_dt)
    )
    if not math.isfinite(actual_dt) or actual_dt <= 0.0:
        raise BenchmarkValidationError("nominal_dt must be finite and positive")
    metrics: list[dict[str, Any]] = []
    canonical: list[dict[str, Any]] = []
    for estimator_spec in estimator_specs:
        for predictor_spec in predictor_specs:
            for horizon_ms, horizon_set in horizon_cells:
                for trajectory in trajectories:
                    metric, output_rows, _ = _predictor_trajectory(
                        trajectory,
                        estimator_spec,
                        predictor_spec,
                        horizon_ms=horizon_ms,
                        horizon_set=horizon_set,
                        nominal_dt=actual_dt,
                        estimator_factory=estimator_factory,
                        predictor_factory=predictor_factory,
                        event_window_s=event_window_s,
                        stop_velocity_fraction=stop_velocity_fraction,
                        stop_velocity_absolute=stop_velocity_absolute,
                        free_duration_fn=free_duration_fn,
                        include_rows=return_canonical_rows,
                    )
                    metrics.append(metric)
                    canonical.extend(output_rows)
    frame = (
        pd.DataFrame(metrics)
        .sort_values(
            [
                "estimator_id",
                "predictor_id",
                "configured_horizon_ms",
                "split",
                "trajectory_id",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )
    if not return_canonical_rows:
        return frame
    _validate_optional_canonical(canonical)
    return frame, canonical


def run_predictor_horizon_sweep(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    locked_estimators: Any,
    predictor_grid: Any | None = None,
    *,
    horizons_ms: Sequence[float] = PRIMARY_HORIZONS_MS,
    stress_horizons_ms: Sequence[float] = STRESS_HORIZONS_MS,
    nominal_dt: float | None = None,
    selection_splits: Iterable[str] = ("validation",),
    estimator_factory: Callable[..., Estimator] = make_estimator,
    predictor_factory: Callable[..., Predictor] = make_predictor,
    event_window_s: float = 0.03,
    stop_velocity_fraction: float = 0.02,
    stop_velocity_absolute: float = 1e-6,
    free_duration_fn: Callable[..., float] | None = None,
    return_canonical_rows: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Run the predeclared primary and stress prediction-horizon sweep.

    Estimators must already be locked.  The function rejects test rows because
    comparing several horizons is a selection operation.  Predictions are
    scored against truth at ``prediction_time``; terminal predictions outside
    the available truth interval remain observable through
    ``prediction_evaluated_time_fraction`` but are not extrapolated.
    """

    records = _records(rows)
    _guard_selection_splits(records, selection_splits, "predictor horizon sweep")
    estimator_specs = _expand_method_grid(locked_estimators, kind="estimator")
    predictor_specs = _predictor_specs(predictor_grid)
    cells = _horizon_cells(horizons_ms, stress_horizons_ms)
    return _run_predictor_core(
        records,
        estimator_specs,
        predictor_specs,
        cells,
        nominal_dt=nominal_dt,
        estimator_factory=estimator_factory,
        predictor_factory=predictor_factory,
        event_window_s=event_window_s,
        stop_velocity_fraction=stop_velocity_fraction,
        stop_velocity_absolute=stop_velocity_absolute,
        free_duration_fn=free_duration_fn,
        return_canonical_rows=return_canonical_rows,
    )


def evaluate_locked_predictor(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    locked_estimators: Any,
    locked_predictors: Any,
    *,
    horizons_ms: Sequence[float],
    nominal_dt: float | None = None,
    estimator_factory: Callable[..., Estimator] = make_estimator,
    predictor_factory: Callable[..., Predictor] = make_predictor,
    event_window_s: float = 0.03,
    stop_velocity_fraction: float = 0.02,
    stop_velocity_absolute: float = 1e-6,
    free_duration_fn: Callable[..., float] | None = None,
    return_canonical_rows: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Evaluate frozen estimator/predictor/horizon cells, including on test."""

    records = _records(rows)
    estimator_specs = _expand_method_grid(locked_estimators, kind="estimator")
    predictor_specs = _expand_method_grid(locked_predictors, kind="predictor")
    cells = _horizon_cells(horizons_ms, ())
    return _run_predictor_core(
        records,
        estimator_specs,
        predictor_specs,
        cells,
        nominal_dt=nominal_dt,
        estimator_factory=estimator_factory,
        predictor_factory=predictor_factory,
        event_window_s=event_window_s,
        stop_velocity_fraction=stop_velocity_fraction,
        stop_velocity_absolute=stop_velocity_absolute,
        free_duration_fn=free_duration_fn,
        return_canonical_rows=return_canonical_rows,
    )


def rank_prediction_horizons(
    trajectory_metrics: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    selection_splits: Iterable[str] = ("validation",),
    objective_metric: str = "prediction_p_rmse",
    include_stress_in_selection: bool = False,
    group_fields: Sequence[str] = ("estimator_id", "predictor_id"),
) -> pd.DataFrame:
    """Rank horizons within each locked estimator/predictor condition.

    Stress horizons are retained in the returned complete ranking but are
    ineligible for locking unless ``include_stress_in_selection`` is explicit.
    """

    selection_splits = tuple(selection_splits)
    if not group_fields:
        raise BenchmarkValidationError("group_fields cannot be empty")
    frame = _selection_frame(
        trajectory_metrics, selection_splits, "prediction horizon ranking"
    )
    required = {
        "trajectory_id",
        "configured_horizon_ms",
        "horizon_set",
        objective_metric,
        *group_fields,
    }
    missing = required - set(frame)
    if missing:
        raise BenchmarkValidationError(
            f"prediction horizon ranking is missing columns {sorted(missing)}"
        )
    numeric = pd.to_numeric(frame[objective_metric], errors="coerce")
    if numeric.isna().any() or not np.isfinite(numeric.to_numpy()).all():
        raise BenchmarkValidationError(
            f"objective metric {objective_metric} is non-finite"
        )
    frame[objective_metric] = numeric

    unit_fields = [
        field
        for field in ("dataset_id", "session_id", "trajectory_id", "scenario_id")
        if field in frame
    ]
    condition_units: dict[tuple[Any, ...], dict[float, set[tuple[Any, ...]]]] = (
        defaultdict(dict)
    )
    for key, group in frame.groupby(
        [*group_fields, "configured_horizon_ms"], sort=True, dropna=False
    ):
        key_tuple = tuple(key) if isinstance(key, tuple) else (key,)
        condition = key_tuple[:-1]
        horizon = float(key_tuple[-1])
        condition_units[condition][horizon] = set(
            map(tuple, group[unit_fields].to_numpy().tolist())
        )
    for condition, by_horizon in condition_units.items():
        reference_units = next(iter(by_horizon.values()))
        if any(units != reference_units for units in by_horizon.values()):
            raise BenchmarkValidationError(
                f"horizon cells for {condition} do not cover the same trajectory units"
            )

    grouping = [*group_fields, "configured_horizon_ms", "horizon_set"]
    aggregate = (
        frame.groupby(grouping, as_index=False, sort=True)
        .agg(
            n_trajectories=("trajectory_id", "nunique"),
            objective_mean=(objective_metric, "mean"),
            objective_median=(objective_metric, "median"),
            objective_q25=(objective_metric, lambda x: x.quantile(0.25)),
            objective_q75=(objective_metric, lambda x: x.quantile(0.75)),
            actual_propagation_horizon_ms=(
                "actual_propagation_horizon_ms",
                "mean",
            ),
        )
        .sort_values([*group_fields, "configured_horizon_ms"], kind="stable")
        .reset_index(drop=True)
    )
    aggregate["objective_metric"] = objective_metric
    aggregate["selection_splits"] = ";".join(selection_splits)
    aggregate["eligible_for_selection"] = (
        True
        if include_stress_in_selection
        else aggregate["horizon_set"].astype(str).eq("primary")
    )
    aggregate["diagnostic_rank"] = (
        aggregate.groupby(list(group_fields))["objective_mean"]
        .rank(method="min", ascending=True)
        .astype(int)
    )
    aggregate["rank"] = pd.array([pd.NA] * len(aggregate), dtype="Int64")
    for _, indices in aggregate.groupby(list(group_fields), sort=True).groups.items():
        eligible_indices = [
            index
            for index in indices
            if bool(aggregate.at[index, "eligible_for_selection"])
        ]
        ordered = sorted(
            eligible_indices,
            key=lambda index: (
                float(aggregate.at[index, "objective_mean"]),
                float(aggregate.at[index, "configured_horizon_ms"]),
            ),
        )
        for rank, index in enumerate(ordered, start=1):
            aggregate.at[index, "rank"] = rank
    aggregate["selected"] = aggregate["rank"].eq(1).fillna(False)
    return aggregate


def lock_prediction_horizons(
    ranking: pd.DataFrame | Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze the rank-one horizon for every estimator/predictor group."""

    frame = (
        ranking.copy(deep=True)
        if isinstance(ranking, pd.DataFrame)
        else pd.DataFrame([dict(row) for row in ranking])
    )
    required = {
        "estimator_id",
        "predictor_id",
        "configured_horizon_ms",
        "rank",
        "eligible_for_selection",
    }
    missing = required - set(frame)
    if frame.empty or missing:
        raise BenchmarkValidationError(
            f"horizon ranking is empty or missing {sorted(missing)}"
        )
    if (
        "selection_splits" in frame
        and frame["selection_splits"]
        .astype(str)
        .str.contains(r"(?:^|;)test(?:;|$)", regex=True)
        .any()
    ):
        raise SelectionLeakageError("cannot lock a prediction horizon from test")
    selected = frame[frame["eligible_for_selection"].astype(bool) & frame["rank"].eq(1)]
    expected_groups = frame[["estimator_id", "predictor_id"]].drop_duplicates()
    if len(selected) != len(expected_groups):
        raise BenchmarkValidationError(
            "each estimator/predictor condition must have exactly one rank-one horizon"
        )
    cells = [
        {
            "estimator_id": str(row["estimator_id"]),
            "predictor_id": str(row["predictor_id"]),
            "prediction_horizon_ms": float(row["configured_horizon_ms"]),
            "selection_objective": str(
                row.get("objective_metric", "prediction_p_rmse")
            ),
            "selection_objective_mean": float(row["objective_mean"]),
        }
        for row in selected.sort_values(
            ["estimator_id", "predictor_id"], kind="stable"
        ).to_dict(orient="records")
    ]
    return {
        "locked": True,
        "selection_splits": str(selected.iloc[0].get("selection_splits", "validation")),
        "locked_prediction_cells": cells,
    }


def _default_rho_group_fields(frame: pd.DataFrame, horizon_col: str) -> list[str]:
    preferred = (
        "split",
        "estimator_id",
        "predictor_id",
        "target_mode",
        "governor_id",
        "follower_id",
        horizon_col,
    )
    fields = [field for field in preferred if field in frame]
    return fields or [horizon_col]


def _segment_statistics(group: pd.DataFrame) -> tuple[int, int, float]:
    trajectory_fields = [
        field
        for field in ("dataset_id", "session_id", "trajectory_id", "scenario_id")
        if field in group
    ]
    if "trajectory_id" not in trajectory_fields:
        trajectory_fields = []
    segment_count = 0
    longest_samples = 0
    longest_duration = 0.0
    iterator: Iterable[tuple[Any, pd.DataFrame]]
    if trajectory_fields:
        grouper: Any = (
            trajectory_fields[0] if len(trajectory_fields) == 1 else trajectory_fields
        )
        iterator = group.groupby(grouper, sort=False, dropna=False)
    else:
        iterator = [("all", group)]
    for _, trajectory in iterator:
        order_field = (
            "k"
            if "k" in trajectory
            else "control_time"
            if "control_time" in trajectory
            else None
        )
        ordered = (
            trajectory.sort_values(order_field, kind="stable")
            if order_field
            else trajectory
        )
        exceed = ordered["rho"].to_numpy(dtype=float) > 1.0
        times = (
            ordered["control_time"].to_numpy(dtype=float)
            if "control_time" in ordered
            else np.arange(len(ordered), dtype=float)
        )
        start: int | None = None
        for index, flag in enumerate(np.append(exceed, False)):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                stop = index
                length = stop - start
                segment_count += 1
                longest_samples = max(longest_samples, length)
                if length <= 1:
                    duration = 0.0
                else:
                    duration = float(times[stop - 1] - times[start])
                longest_duration = max(longest_duration, duration)
                start = None
    return segment_count, longest_samples, longest_duration


def summarize_t_free_rho(
    samples: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    horizon_col: str = "configured_horizon_ms",
    duration_col: str = "free_trajectory_duration",
    group_fields: Sequence[str] | None = None,
    return_samples: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
    """Summarize the genuine unconstrained-duration ratio ``T_free / H``.

    Rows with ``H == 0`` have no defined ratio and are excluded, never coerced
    to zero or infinity.  Consecutive ``rho > 1`` runs are counted within each
    complete trajectory rather than across concatenated trajectories.
    """

    frame = (
        samples.copy(deep=True)
        if isinstance(samples, pd.DataFrame)
        else pd.DataFrame([dict(row) for row in samples])
    )
    required = {horizon_col, duration_col}
    missing = required - set(frame)
    if frame.empty or missing:
        raise BenchmarkValidationError(
            f"T_free input is empty or missing {sorted(missing)}"
        )
    horizon_series = pd.to_numeric(frame[horizon_col], errors="coerce")
    duration_series = pd.to_numeric(frame[duration_col], errors="coerce")
    horizon = horizon_series.to_numpy(dtype=float)
    duration = duration_series.to_numpy(dtype=float)
    if not np.all(np.isfinite(horizon)) or np.any(horizon < 0.0):
        raise BenchmarkValidationError(
            "configured horizons must be finite and non-negative"
        )
    invalid_duration = frame[duration_col].notna().to_numpy() & ~np.isfinite(duration)
    if np.any(invalid_duration) or np.any(duration[np.isfinite(duration)] < 0.0):
        raise BenchmarkValidationError(
            "available T_free durations must be finite and non-negative"
        )
    valid = np.isfinite(duration) & (horizon > 0.0)
    frame = frame.loc[valid].copy()
    if frame.empty:
        raise BenchmarkValidationError("T_free/H has no rows with finite H > 0")
    frame["rho"] = pd.to_numeric(frame[duration_col], errors="raise") / (
        pd.to_numeric(frame[horizon_col], errors="raise") / 1000.0
    )
    groups = (
        list(group_fields)
        if group_fields is not None
        else _default_rho_group_fields(frame, horizon_col)
    )
    missing_groups = set(groups) - set(frame)
    if missing_groups:
        raise BenchmarkValidationError(
            f"T_free group fields are missing {sorted(missing_groups)}"
        )
    # Canonical long-format artifacts repeat one synchronized multi-DoF solve
    # once per joint.  Collapse those identical copies before percentiles and
    # consecutive-run counting; unequal copies indicate a corrupt artifact.
    if "joint_id" in frame and "k" in frame:
        dedup_fields = list(
            dict.fromkeys(
                [
                    *groups,
                    *[
                        field
                        for field in (
                            "run_id",
                            "dataset_id",
                            "session_id",
                            "trajectory_id",
                            "scenario_id",
                        )
                        if field in frame
                    ],
                    "k",
                ]
            )
        )
        for _, duplicates in frame.groupby(dedup_fields, sort=False, dropna=False):
            if (
                duplicates[horizon_col].nunique(dropna=False) != 1
                or duplicates[duration_col].nunique(dropna=False) != 1
            ):
                raise BenchmarkValidationError(
                    "synchronized joint rows disagree on H or T_free"
                )
        frame = frame.drop_duplicates(dedup_fields, keep="first").copy()
    output: list[dict[str, Any]] = []
    grouper: Any = groups[0] if len(groups) == 1 else groups
    for key, group in frame.groupby(grouper, sort=True, dropna=False):
        key_values = (key,) if len(groups) == 1 else tuple(key)
        rho = group["rho"].to_numpy(dtype=float)
        segment_count, longest_samples, longest_duration = _segment_statistics(group)
        row: dict[str, Any] = dict(zip(groups, key_values))
        row.update(
            {
                "n_samples": int(rho.size),
                "n_trajectories": int(
                    group["trajectory_id"].nunique() if "trajectory_id" in group else 1
                ),
                "rho_p50": _quantile(rho, 0.50),
                "rho_p90": _quantile(rho, 0.90),
                "rho_p99": _quantile(rho, 0.99),
                "rho_max": float(np.max(rho)),
                "rho_le_one_fraction": float(np.mean(rho <= 1.0)),
                "rho_exceedance_fraction": float(np.mean(rho > 1.0)),
                "rho_exceedance_segment_count": segment_count,
                "rho_longest_exceedance_samples": longest_samples,
                "rho_longest_exceedance_duration_s": longest_duration,
            }
        )
        output.append(row)
    summary = (
        pd.DataFrame(output).sort_values(groups, kind="stable").reset_index(drop=True)
    )
    if return_samples:
        return summary, frame.reset_index(drop=True)
    return summary


def acceleration_phase_design(
    *,
    r_a_strata: Mapping[str, float] | Sequence[float] = DEFAULT_RATIO_STRATA,
    r_j_strata: Mapping[str, float] | Sequence[float] = DEFAULT_RATIO_STRATA,
    phases: Sequence[str] = ACCELERATION_ACTIVE_PHASES,
    directions: Sequence[int] = (-1, 1),
    dt: float = 0.01,
    include_conditions: bool = True,
) -> pd.DataFrame:
    """Build the complete, predeclared acceleration-active oracle matrix.

    Every physical cell is represented in both motion directions.  With
    ``include_conditions=True`` each case is expanded to current P/PV/PVA and
    next-cycle P/PV/PVA, all sharing a ``future_position_key`` within a timing
    condition so a CLI can enforce the same-position comparison.
    """

    if not math.isfinite(dt) or dt <= 0.0:
        raise BenchmarkValidationError("dt must be finite and positive")

    def strata(value: Mapping[str, float] | Sequence[float], name: str):
        items = (
            list(value.items())
            if isinstance(value, Mapping)
            else [(f"{float(item):g}", float(item)) for item in value]
        )
        if not items:
            raise BenchmarkValidationError(f"{name} strata cannot be empty")
        normalized = []
        for label, raw in items:
            ratio = float(raw)
            if not math.isfinite(ratio) or not 0.0 < ratio <= 1.0:
                raise BenchmarkValidationError(f"{name} ratios must lie in (0, 1]")
            normalized.append((str(label), ratio))
        if len({label for label, _ in normalized}) != len(normalized):
            raise BenchmarkValidationError(f"{name} stratum labels are duplicated")
        return normalized

    a_values = strata(r_a_strata, "r_a")
    j_values = strata(r_j_strata, "r_j")
    if set(phases) != set(ACCELERATION_ACTIVE_PHASES):
        missing = set(ACCELERATION_ACTIVE_PHASES) - set(phases)
        extra = set(phases) - set(ACCELERATION_ACTIVE_PHASES)
        raise BenchmarkValidationError(
            f"acceleration phase suite must be complete; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    normalized_directions = [int(value) for value in directions]
    if set(normalized_directions) != {-1, 1}:
        raise BenchmarkValidationError(
            "directions must include the complete {-1, +1} pair"
        )
    conditions = (
        (
            ("current", 0.0, "p"),
            ("current", 0.0, "pv"),
            ("current", 0.0, "pva"),
            ("next_cycle", dt * 1000.0, "p"),
            ("next_cycle", dt * 1000.0, "pv"),
            ("next_cycle", dt * 1000.0, "pva"),
        )
        if include_conditions
        else (("design", 0.0, "all"),)
    )
    rows = []
    for phase, (a_label, r_a), (j_label, r_j), direction in product(
        phases, a_values, j_values, normalized_directions
    ):
        case_id = (
            f"accel-{phase}-ra-{a_label}-rj-{j_label}-"
            f"{'pos' if direction > 0 else 'neg'}"
        )
        for target_time_mode, horizon_ms, target_mode in conditions:
            future_position_key = f"{case_id}:{target_time_mode}:{horizon_ms:g}ms"
            rows.append(
                {
                    "case_id": case_id,
                    "trajectory_id": case_id,
                    "phase": phase,
                    "r_a_stratum": a_label,
                    "r_j_stratum": j_label,
                    "r_a": r_a,
                    "r_j": r_j,
                    "direction": direction,
                    "target_time_mode": target_time_mode,
                    "configured_horizon_ms": horizon_ms,
                    "target_mode": target_mode,
                    "predictor_id": "oracle_future_state_offline",
                    "oracle": True,
                    "causal": False,
                    "future_position_key": future_position_key,
                    "condition_id": f"{future_position_key}:{target_mode}",
                }
            )
    return (
        pd.DataFrame(rows)
        .sort_values(
            [
                "phase",
                "r_a",
                "r_j",
                "direction",
                "configured_horizon_ms",
                "target_mode",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )


def build_acceleration_phase_map(
    trajectory_metrics: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    rmse_field: str = "position_rmse",
    lag_field: str = "lag_s",
    mode_field: str = "target_mode",
    pv_label: str = "pv",
    pva_label: str = "pva",
    required_phases: Sequence[str] = ACCELERATION_ACTIVE_PHASES,
    required_directions: Sequence[int] | None = (-1, 1),
    expected_r_a: Sequence[float] | None = None,
    expected_r_j: Sequence[float] | None = None,
    require_oracle: bool = True,
    return_pairs: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
    """Pair oracle PV/PVA trajectory results and build a complete phase map.

    Improvement is ``(PV - PVA) / PV`` for lower-is-better RMSE.  It is never
    clipped: negative cells and individual harmful trajectories remain in the
    output.  Completeness is checked for every phase, ``r_a``, ``r_j``, motion
    direction, and current/next-cycle condition represented by the input.
    """

    frame = (
        trajectory_metrics.copy(deep=True)
        if isinstance(trajectory_metrics, pd.DataFrame)
        else pd.DataFrame([dict(row) for row in trajectory_metrics])
    )
    required = {
        "trajectory_id",
        "phase",
        "r_a",
        "r_j",
        mode_field,
        rmse_field,
        lag_field,
    }
    missing = required - set(frame)
    if frame.empty or missing:
        raise BenchmarkValidationError(
            f"acceleration phase metrics are empty or missing {sorted(missing)}"
        )
    frame = frame[frame[mode_field].astype(str).isin((pv_label, pva_label))].copy()
    if frame.empty:
        raise BenchmarkValidationError("phase map contains no PV/PVA rows")
    if require_oracle:
        if (
            "predictor_id" not in frame
            or not frame["predictor_id"]
            .astype(str)
            .str.lower()
            .str.contains("oracle")
            .all()
        ):
            raise BenchmarkValidationError(
                "acceleration phase map must use the explicitly labelled offline oracle"
            )
    for field in ("r_a", "r_j", rmse_field, lag_field):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
        if frame[field].isna().any() or not np.isfinite(frame[field]).all():
            raise BenchmarkValidationError(f"phase-map field {field} is non-finite")
    if (frame[rmse_field] < 0.0).any():
        raise BenchmarkValidationError("RMSE values cannot be negative")

    unit_fields = [
        field
        for field in ("dataset_id", "session_id", "trajectory_id", "scenario_id")
        if field in frame
    ]
    if "trajectory_id" not in unit_fields:
        unit_fields.append("trajectory_id")
    condition_fields = [
        field
        for field in (
            "phase",
            "r_a_stratum",
            "r_j_stratum",
            "r_a",
            "r_j",
            "direction",
            "target_time_mode",
            "configured_horizon_ms",
            "future_position_key",
            "estimator_id",
            "predictor_id",
        )
        if field in frame and field not in unit_fields
    ]
    pair_fields = [*unit_fields, *condition_fields]
    duplicates = frame.duplicated([*pair_fields, mode_field], keep=False)
    if duplicates.any():
        raise BenchmarkValidationError(
            "phase map has duplicate trajectory/mode rows; samples may not be "
            "used as statistical units"
        )
    pv = frame[frame[mode_field].astype(str) == pv_label].set_index(pair_fields)
    pva = frame[frame[mode_field].astype(str) == pva_label].set_index(pair_fields)
    if set(pv.index) != set(pva.index):
        missing_pv = len(set(pva.index) - set(pv.index))
        missing_pva = len(set(pv.index) - set(pva.index))
        raise BenchmarkValidationError(
            f"PV/PVA trajectory pairing is incomplete: missing PV={missing_pv}, "
            f"missing PVA={missing_pva}"
        )
    pv = pv.sort_index()
    pva = pva.reindex(pv.index)
    pv_rmse = pv[rmse_field].to_numpy(dtype=float)
    pva_rmse = pva[rmse_field].to_numpy(dtype=float)
    if np.any(pv_rmse <= np.finfo(float).tiny):
        raise BenchmarkValidationError(
            "relative PVA/PV improvement is undefined for zero PV RMSE"
        )
    pairs = pv.reset_index()[pair_fields].copy()
    pairs["pv_position_rmse"] = pv_rmse
    pairs["pva_position_rmse"] = pva_rmse
    pairs["pva_vs_pv_rmse_difference"] = pv_rmse - pva_rmse
    pairs["pva_vs_pv_rmse_improvement"] = (pv_rmse - pva_rmse) / pv_rmse
    pv_lag = np.abs(pv[lag_field].to_numpy(dtype=float))
    pva_lag = np.abs(pva[lag_field].to_numpy(dtype=float))
    pairs["pv_abs_lag_s"] = pv_lag
    pairs["pva_abs_lag_s"] = pva_lag
    pairs["pva_vs_pv_lag_improvement_s"] = pv_lag - pva_lag
    pairs["pva_beneficial"] = pairs["pva_vs_pv_rmse_improvement"] > 0.0
    pairs["pva_harmful"] = pairs["pva_vs_pv_rmse_improvement"] < 0.0

    phases = tuple(str(value) for value in required_phases)
    observed_phases = set(pairs["phase"].astype(str))
    if set(phases) - observed_phases:
        raise BenchmarkValidationError(
            f"phase map is missing phases {sorted(set(phases) - observed_phases)}"
        )
    selected_r_a = pairs["r_a"].unique() if expected_r_a is None else expected_r_a
    selected_r_j = pairs["r_j"].unique() if expected_r_j is None else expected_r_j
    a_values = sorted(set(float(value) for value in selected_r_a))
    j_values = sorted(set(float(value) for value in selected_r_j))
    if not a_values or not j_values:
        raise BenchmarkValidationError("expected r_a/r_j strata cannot be empty")
    directions = (
        tuple(int(value) for value in required_directions)
        if required_directions is not None and "direction" in pairs
        else tuple(sorted(set(int(value) for value in pairs.get("direction", [1]))))
    )
    map_conditions = [
        field
        for field in (
            "target_time_mode",
            "configured_horizon_ms",
            "estimator_id",
            "predictor_id",
        )
        if field in pairs
    ]
    condition_values = (
        pairs[map_conditions].drop_duplicates().to_dict(orient="records")
        if map_conditions
        else [{}]
    )
    for condition in condition_values:
        subset = pairs
        for field, value in condition.items():
            subset = subset[subset[field] == value]
        observed = {
            (
                str(row.phase),
                float(row.r_a),
                float(row.r_j),
                int(getattr(row, "direction", 1)),
            )
            for row in subset.itertuples(index=False)
        }
        expected = set(product(phases, a_values, j_values, directions))
        missing_cells = expected - observed
        if missing_cells:
            first = sorted(missing_cells, key=lambda value: tuple(map(str, value)))[0]
            raise BenchmarkValidationError(
                f"acceleration phase matrix has missing cells under {condition}; "
                f"first missing={first}"
            )

    cell_fields = [*map_conditions, "r_a", "r_j"]
    output: list[dict[str, Any]] = []
    grouper: Any = cell_fields[0] if len(cell_fields) == 1 else cell_fields
    for key, group in pairs.groupby(grouper, sort=True, dropna=False):
        key_values = (key,) if len(cell_fields) == 1 else tuple(key)
        improvement = group["pva_vs_pv_rmse_improvement"].to_numpy(dtype=float)
        difference = group["pva_vs_pv_rmse_difference"].to_numpy(dtype=float)
        lag_improvement = group["pva_vs_pv_lag_improvement_s"].to_numpy(dtype=float)
        row = dict(zip(cell_fields, key_values))
        row.update(
            {
                "n_trajectory_pairs": int(len(group)),
                "phase_coverage": ";".join(sorted(set(group["phase"].astype(str)))),
                "direction_coverage": ";".join(
                    str(value) for value in sorted(set(group.get("direction", [1])))
                ),
                "pva_vs_pv_rmse_improvement": float(np.mean(improvement)),
                "pva_vs_pv_rmse_improvement_median": float(np.median(improvement)),
                "pva_vs_pv_rmse_improvement_min": float(np.min(improvement)),
                "pva_vs_pv_rmse_improvement_max": float(np.max(improvement)),
                "pva_vs_pv_rmse_difference": float(np.mean(difference)),
                "pva_vs_pv_lag_improvement_s": float(np.mean(lag_improvement)),
                "positive_pair_count": int(np.sum(improvement > 0.0)),
                "negative_pair_count": int(np.sum(improvement < 0.0)),
                "tie_pair_count": int(np.sum(improvement == 0.0)),
                "positive_pair_fraction": float(np.mean(improvement > 0.0)),
                "negative_pair_fraction": float(np.mean(improvement < 0.0)),
            }
        )
        output.append(row)
    phase_map = (
        pd.DataFrame(output)
        .sort_values(cell_fields, kind="stable")
        .reset_index(drop=True)
    )
    if return_pairs:
        return phase_map, pairs.reset_index(drop=True)
    return phase_map


# Explicit long name used by experiment CLIs and manifests.
acceleration_active_oracle_phase_map = build_acceleration_phase_map


def sampling_rate_dimensionless(
    sample_rates_hz: Sequence[float] = (50.0, 100.0, 200.0, 500.0),
    *,
    max_velocity: float | Sequence[float] = 4.1,
    max_acceleration: float | Sequence[float] = 8.2,
    max_jerk: float | Sequence[float] = 4000.0,
    primary_rate_hz: float = 100.0,
) -> pd.DataFrame:
    """Return ``chi_j`` and ``chi_a`` for each independently sampled rate."""

    rates = np.asarray(sample_rates_hz, dtype=float)
    if (
        rates.ndim != 1
        or rates.size == 0
        or not np.all(np.isfinite(rates))
        or np.any(rates <= 0.0)
    ):
        raise BenchmarkValidationError("sample rates must be finite and positive")
    if len(np.unique(rates)) != rates.size:
        raise BenchmarkValidationError("sample rates must be unique")
    if not math.isfinite(float(primary_rate_hz)) or primary_rate_hz <= 0.0:
        raise BenchmarkValidationError("primary_rate_hz must be finite and positive")
    limits = [
        np.asarray(value, dtype=float)
        for value in (max_velocity, max_acceleration, max_jerk)
    ]
    dof = max(array.size for array in limits)
    broadcast: list[NDArray[np.float64]] = []
    for array, label in zip(limits, ("max_velocity", "max_acceleration", "max_jerk")):
        if array.ndim == 0:
            array = np.full(dof, float(array))
        if (
            array.shape != (dof,)
            or not np.all(np.isfinite(array))
            or np.any(array <= 0.0)
        ):
            raise BenchmarkValidationError(
                f"{label} must be positive scalar or a common-length vector"
            )
        broadcast.append(array)
    velocity, acceleration, jerk = broadcast
    rows = []
    for rate in rates:
        dt = 1.0 / float(rate)
        for joint in range(dof):
            rows.append(
                {
                    "sample_rate_hz": float(rate),
                    "dt_s": dt,
                    "dt_ms": dt * 1000.0,
                    "deadline_us": dt * 1e6,
                    "joint_index": joint,
                    "max_velocity": float(velocity[joint]),
                    "max_acceleration": float(acceleration[joint]),
                    "max_jerk": float(jerk[joint]),
                    "chi_j": float(acceleration[joint] / (jerk[joint] * dt)),
                    "chi_a": float(velocity[joint] / (acceleration[joint] * dt)),
                    "primary_condition": bool(
                        math.isclose(
                            float(rate), primary_rate_hz, rel_tol=0.0, abs_tol=1e-12
                        )
                    ),
                }
            )
    return (
        pd.DataFrame(rows)
        .sort_values(["sample_rate_hz", "joint_index"], kind="stable")
        .reset_index(drop=True)
    )


def _runtime_tasks(
    tasks: Mapping[str, Callable[[], Any]] | Callable[[], Any],
    name: str | None,
) -> list[tuple[str, Callable[[], Any]]]:
    if callable(tasks):
        label = name or getattr(tasks, "__name__", "callable")
        return [(str(label), tasks)]
    if not isinstance(tasks, Mapping) or not tasks:
        raise BenchmarkValidationError(
            "runtime tasks must be a callable or non-empty mapping"
        )
    output = []
    for label, function in tasks.items():
        if not callable(function):
            raise BenchmarkValidationError(f"runtime task {label!r} is not callable")
        output.append((str(label), function))
    if len({label for label, _ in output}) != len(output):
        raise BenchmarkValidationError("runtime task names must be unique")
    return output


def benchmark_runtime(
    tasks: Mapping[str, Callable[[], Any]] | Callable[[], Any],
    *,
    name: str | None = None,
    warmup: int = 20,
    repetitions: int = 200,
    calls_per_repetition: int = 1,
    deadline_us: float | Mapping[str, float] | None = None,
    before_repetition: Callable[[str, int, bool], None] | None = None,
    disable_gc: bool = True,
    return_samples: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
    """Warm up and repeatedly time one or more in-memory operations.

    Plotting and disk logging are outside this helper by construction.  Each
    timed value is the average cost per call in one repetition.  Warm-up calls
    are executed but never included in percentiles.
    """

    for value, label, allow_zero in (
        (warmup, "warmup", True),
        (repetitions, "repetitions", False),
        (calls_per_repetition, "calls_per_repetition", False),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, np.integer))
            or value < (0 if allow_zero else 1)
        ):
            qualifier = "non-negative" if allow_zero else "positive"
            raise BenchmarkValidationError(f"{label} must be a {qualifier} integer")
    task_list = _runtime_tasks(tasks, name)
    if deadline_us is not None and not isinstance(deadline_us, Mapping):
        scalar_deadline = float(deadline_us)
        if not math.isfinite(scalar_deadline) or scalar_deadline <= 0.0:
            raise BenchmarkValidationError("deadline_us must be finite and positive")
    else:
        scalar_deadline = None

    environment = runtime_environment()
    environment_json = _stable_json(environment)
    samples: list[dict[str, Any]] = []
    gc_was_enabled = gc.isenabled()
    try:
        if disable_gc and gc_was_enabled:
            gc.disable()
        for label, function in task_list:
            for repetition in range(int(warmup)):
                if before_repetition is not None:
                    before_repetition(label, repetition, True)
                for _ in range(int(calls_per_repetition)):
                    function()
            for repetition in range(int(repetitions)):
                if before_repetition is not None:
                    before_repetition(label, repetition, False)
                started = perf_counter_ns()
                for _ in range(int(calls_per_repetition)):
                    function()
                elapsed_us = (perf_counter_ns() - started) / 1000.0
                per_call_us = elapsed_us / int(calls_per_repetition)
                if isinstance(deadline_us, Mapping):
                    if label not in deadline_us:
                        task_deadline = None
                    else:
                        task_deadline = float(deadline_us[label])
                        if not math.isfinite(task_deadline) or task_deadline <= 0.0:
                            raise BenchmarkValidationError(
                                f"deadline for task {label!r} must be finite and positive"
                            )
                else:
                    task_deadline = scalar_deadline
                samples.append(
                    {
                        "method": label,
                        "repetition": repetition,
                        "calls_per_repetition": int(calls_per_repetition),
                        "elapsed_us": elapsed_us,
                        "per_call_us": per_call_us,
                        "compute_us": per_call_us,
                        "total_compute_us": per_call_us,
                        "deadline_us": task_deadline,
                        "deadline_miss": bool(
                            task_deadline is not None and per_call_us > task_deadline
                        ),
                    }
                )
    finally:
        if disable_gc and gc_was_enabled:
            gc.enable()

    sample_frame = pd.DataFrame(samples)
    summaries = []
    packages = environment.get("packages", {})
    for label, group in sample_frame.groupby("method", sort=False):
        values = group["per_call_us"].to_numpy(dtype=float)
        deadline_values = group["deadline_us"].dropna()
        task_deadline = (
            None if deadline_values.empty else float(deadline_values.iloc[0])
        )
        p999 = _quantile(values, 0.999)
        summaries.append(
            {
                "method": label,
                "warmup_repetitions": int(warmup),
                "repetitions": int(repetitions),
                "calls_per_repetition": int(calls_per_repetition),
                "runtime_mean_us": float(np.mean(values)),
                "runtime_p50_us": _quantile(values, 0.50),
                "runtime_p90_us": _quantile(values, 0.90),
                "runtime_p99_us": _quantile(values, 0.99),
                "runtime_p999_us": p999,
                "runtime_p99_9_us": p999,
                "runtime_max_us": float(np.max(values)),
                "deadline_us": task_deadline,
                "deadline_miss_rate": float(group["deadline_miss"].mean()),
                "python_version": environment.get("python"),
                "platform": environment.get("platform"),
                "machine": environment.get("machine"),
                "processor": environment.get("processor"),
                "cpu_count": environment.get("cpu_count"),
                "numpy_version": packages.get("numpy"),
                "ruckig_version": packages.get("ruckig"),
                "osqp_version": packages.get("osqp"),
                "environment_json": environment_json,
            }
        )
    summary = pd.DataFrame(summaries)
    if return_samples:
        return summary, sample_frame
    return summary


# Concise aliases retained for config-driven runners.
dimensionless_rate_table = sampling_rate_dimensionless
dimensionless_chi = sampling_rate_dimensionless
runtime_benchmark = benchmark_runtime
t_free_rho_summary = summarize_t_free_rho


__all__ = [
    "ACCELERATION_ACTIVE_PHASES",
    "BenchmarkValidationError",
    "DEFAULT_RATIO_STRATA",
    "FreeDurationUnavailable",
    "PRIMARY_HORIZONS_MS",
    "SELECTION_SPLITS",
    "STRESS_HORIZONS_MS",
    "SelectionLeakageError",
    "SplitLeakageError",
    "acceleration_active_oracle_phase_map",
    "acceleration_phase_design",
    "benchmark_runtime",
    "build_acceleration_phase_map",
    "dimensionless_rate_table",
    "dimensionless_chi",
    "evaluate_locked_estimator",
    "evaluate_locked_predictor",
    "expand_estimator_grid",
    "lock_estimator_parameters",
    "lock_prediction_horizons",
    "rank_estimator_grid",
    "rank_prediction_horizons",
    "run_estimator_grid",
    "run_predictor_horizon_sweep",
    "ruckig_unconstrained_free_duration",
    "runtime_benchmark",
    "sampling_rate_dimensionless",
    "summarize_t_free_rho",
    "t_free_rho_summary",
]
