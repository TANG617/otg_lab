"""Canonical, versioned per-sample schema used by the experiment artifacts.

The schema deliberately distinguishes missing information (``None`` / Arrow
null) from a numeric estimate.  In particular, importers must never manufacture
velocity, acceleration, or jerk *truth* by differentiating a position trace.

PyArrow is the canonical storage implementation.  The light-weight ``FieldSpec``
fallback keeps validation and data collection usable before optional artifact
dependencies are installed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral, Real
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "otg.sample.v2"
LEGACY_SCHEMA_VERSION = "otg.sample.v1"
SPLITS = frozenset({"train", "validation", "test", "development", "infeasible"})


try:  # Artifact writing requires PyArrow; validation itself does not.
    import pyarrow as pa  # type: ignore
    import pyarrow.parquet as pq  # type: ignore
except ImportError:  # pragma: no cover - exercised in dependency-minimal installs.
    pa = None
    pq = None


@dataclass(frozen=True)
class FieldSpec:
    """Portable description of one field in the canonical schema."""

    name: str
    kind: str
    nullable: bool = True
    availability: str = "always"


def _f(
    name: str,
    kind: str,
    nullable: bool = True,
    availability: str = "always",
) -> FieldSpec:
    return FieldSpec(name, kind, nullable, availability)


# Keep this tuple explicit and ordered.  Downstream tables use this stable order.
FIELD_SPECS: tuple[FieldSpec, ...] = (
    _f("run_id", "string", False),
    _f("dataset_id", "string", False),
    _f("session_id", "string", False),
    _f("trajectory_id", "string", False),
    _f("split", "string", False),
    _f("seed", "int64", False),
    _f("joint_id", "string", False),
    _f("k", "int64", False),
    _f("method_id", "string", True, "pipeline_configuration"),
    _f("estimator_id", "string", True, "pipeline_configuration"),
    _f("predictor_id", "string", True, "pipeline_configuration"),
    _f("target_mode", "string", True, "pipeline_configuration"),
    _f("governor_id", "string", True, "pipeline_configuration"),
    _f("follower_id", "string", True, "pipeline_configuration"),
    _f("plant_id", "string", True, "pipeline_configuration"),
    _f("source_time", "float64", False),
    _f("arrival_time", "float64", False),
    _f("control_time", "float64", False),
    _f("dt_actual", "float64", False),
    _f("dt_control", "float64", False),
    _f("p_ref", "float64", False),
    _f("v_ref_truth", "float64", True, "synthetic_truth_only"),
    _f("a_ref_truth", "float64", True, "synthetic_truth_only"),
    _f("j_ref_truth", "float64", True, "synthetic_truth_only"),
    _f("p_meas", "float64", True, "when_measurement_available"),
    _f("v_meas", "float64", True, "when_sensor_provides_velocity"),
    _f("a_meas", "float64", True, "when_sensor_provides_acceleration"),
    _f("posterior_p", "float64", True, "after_estimator"),
    _f("posterior_v", "float64", True, "after_estimator"),
    _f("posterior_a", "float64", True, "after_estimator"),
    _f("posterior_state_time", "float64", True, "after_estimator"),
    _f("posterior_available_time", "float64", True, "after_estimator"),
    _f(
        "posterior_axis_source_time",
        "float64",
        True,
        "after_per_axis_estimator_before_control_time_propagation",
    ),
    _f(
        "posterior_axis_available_time",
        "float64",
        True,
        "after_per_axis_estimator_before_control_time_propagation",
    ),
    _f(
        "measurement_sync_method",
        "string",
        True,
        "after_measurement_time_synchronization",
    ),
    _f("prediction_p", "float64", True, "after_predictor"),
    _f("prediction_v", "float64", True, "after_predictor"),
    _f("prediction_a", "float64", True, "after_predictor"),
    _f("prediction_time", "float64", True, "after_predictor"),
    _f("prediction_horizon_ms", "float64", True, "after_predictor"),
    _f("raw_target_p", "float64", True, "after_target_construction"),
    _f("raw_target_v", "float64", True, "after_target_construction"),
    _f("raw_target_a", "float64", True, "after_target_construction"),
    _f("raw_target_time", "float64", True, "after_target_construction"),
    _f("executable_target_p", "float64", True, "after_governor"),
    _f("executable_target_v", "float64", True, "after_governor"),
    _f("executable_target_a", "float64", True, "after_governor"),
    _f("executable_target_time", "float64", True, "after_governor"),
    _f("command_p", "float64", True, "after_follower"),
    _f("command_v", "float64", True, "after_follower"),
    _f("command_a", "float64", True, "after_follower"),
    _f("command_jerk", "float64", True, "after_follower"),
    _f("sampled_jerk", "float64", True, "sampled_command_a_difference"),
    _f("new_jerk", "float64", True, "follower_reported_jerk"),
    _f("internal_trajectory_jerk", "float64", True, "continuous_constraint_audit"),
    _f("command_time", "float64", True, "after_follower"),
    _f("plant_p", "float64", True, "when_plant_enabled"),
    _f("plant_v", "float64", True, "when_plant_enabled"),
    _f("plant_a", "float64", True, "when_plant_enabled"),
    _f("plant_measured_p", "float64", True, "when_plant_measurement_available"),
    _f("plant_measured_v", "float64", True, "when_plant_measurement_available"),
    _f("plant_measured_a", "float64", True, "when_plant_measurement_available"),
    _f("plant_saturated", "bool", True, "when_plant_enabled"),
    _f("plant_command_source_time", "float64", True, "when_plant_enabled"),
    _f("plant_command_age_s", "float64", True, "when_plant_enabled"),
    _f("plant_delay_s", "float64", True, "when_plant_enabled"),
    _f("plant_status", "string", True, "when_plant_enabled"),
    _f(
        "command_measured_delta_p",
        "float64",
        True,
        "when_feedback_state_comparison_available",
    ),
    _f(
        "command_measured_delta_v",
        "float64",
        True,
        "when_feedback_state_comparison_available",
    ),
    _f(
        "command_measured_delta_a",
        "float64",
        True,
        "when_feedback_state_comparison_available",
    ),
    _f(
        "command_measured_divergence",
        "float64",
        True,
        "when_feedback_state_comparison_available",
    ),
    _f("event_command_measured_divergence", "bool", False),
    _f("feedback_correction", "bool", False),
    _f(
        "feedback_correction_p",
        "float64",
        True,
        "after_replanning_state_selection",
    ),
    _f(
        "feedback_correction_v",
        "float64",
        True,
        "after_replanning_state_selection",
    ),
    _f(
        "feedback_correction_a",
        "float64",
        True,
        "after_replanning_state_selection",
    ),
    _f(
        "feedback_correction_reason",
        "string",
        True,
        "after_replanning_state_selection",
    ),
    _f("limit_max_velocity", "float64", True, "when_feasibility_is_audited"),
    _f("limit_max_acceleration", "float64", True, "when_feasibility_is_audited"),
    _f("limit_max_jerk", "float64", True, "when_feasibility_is_audited"),
    _f("current_p", "float64", True, "after_replanning_state_selection"),
    _f("current_v", "float64", True, "after_replanning_state_selection"),
    _f("current_a", "float64", True, "after_replanning_state_selection"),
    _f("raw_target_point_admissible", "bool", True, "after_raw_target_audit"),
    _f("raw_target_ruckig_admissible", "bool", True, "after_raw_target_audit"),
    _f("executable_target_available", "bool", True, "after_governor"),
    _f(
        "executable_target_point_admissible",
        "bool",
        True,
        "when_executable_target_available",
    ),
    _f(
        "executable_target_stopping_viable",
        "bool",
        True,
        "when_executable_target_available",
    ),
    _f(
        "executable_target_segment_feasible",
        "bool",
        True,
        "when_executable_target_available",
    ),
    _f(
        "executable_target_t_free_le_dt",
        "bool",
        True,
        "when_executable_target_available",
    ),
    _f(
        "executable_target_free_trajectory_duration",
        "float64",
        True,
        "when_executable_target_free_solve_succeeds",
    ),
    _f("command_t_free_le_dt", "bool", True, "after_follower_free_solve"),
    _f("command_segment_feasible", "bool", True, "after_follower"),
    _f("command_stopping_viable", "bool", True, "after_follower"),
    _f(
        "command_continuous_constraints_satisfied",
        "bool",
        True,
        "after_continuous_constraint_audit",
    ),
    _f(
        "command_max_abs_velocity",
        "float64",
        True,
        "after_continuous_constraint_audit",
    ),
    _f(
        "command_max_abs_acceleration",
        "float64",
        True,
        "after_continuous_constraint_audit",
    ),
    _f(
        "command_max_abs_jerk",
        "float64",
        True,
        "after_continuous_constraint_audit",
    ),
    _f("fallback_requested", "bool", True, "after_online_pipeline"),
    _f("fallback_applied", "bool", True, "after_online_pipeline"),
    _f("safety_guarantee", "bool", True, "after_online_pipeline"),
    _f("emergency_mode", "bool", True, "after_online_pipeline"),
    _f(
        "legacy_target_feasible_v1",
        "bool",
        True,
        "v1_migration_only_ambiguous_original_semantics",
    ),
    _f(
        "target_feasible",
        "bool",
        True,
        "deprecated_alias_for_raw_target_point_admissible",
    ),
    _f("target_projected", "bool", True, "after_governor"),
    _f("fallback", "bool", True, "after_online_pipeline"),
    _f("fallback_reason", "string", True, "when_fallback"),
    _f("solver_status", "string", True, "after_solver"),
    _f("qp_iterations", "int64", True, "after_qp_solver"),
    _f("qp_status_category", "string", True, "after_qp_solver"),
    _f("qp_solve_time_us", "float64", True, "after_qp_solver"),
    _f("qp_primal_residual", "float64", True, "after_qp_solver"),
    _f("qp_dual_residual", "float64", True, "after_qp_solver"),
    _f("qp_hessian_condition_number", "float64", True, "after_qp_setup"),
    _f("qp_constraint_condition_number", "float64", True, "after_qp_setup"),
    _f("deadline_miss", "bool", False),
    _f("state_reset", "bool", False),
    _f("invalid_input", "bool", False),
    _f("free_trajectory_duration", "float64", True, "when_solver_exposes_it"),
    _f("estimator_compute_us", "float64", True, "after_estimator"),
    _f("predictor_compute_us", "float64", True, "after_predictor"),
    _f("governor_compute_us", "float64", True, "after_governor"),
    _f("follower_compute_us", "float64", True, "after_follower"),
    _f("plant_compute_us", "float64", True, "when_plant_enabled"),
    _f("total_compute_us", "float64", True, "after_online_pipeline"),
    # Provenance and fault-realization fields.  These are additions to, not
    # replacements for, the required research fields above.
    _f("source_kind", "string", False),
    _f("reference_family", "string", True),
    _f("reference_variant", "string", True),
    _f(
        "reference_frequency_spec_json",
        "string",
        True,
        "synthetic_oscillatory_frequency_metadata",
    ),
    _f("scenario_id", "string", False),
    _f("stress_seed", "int64", True, "stress_suite"),
    _f("stress_parameters_json", "string", True, "stress_suite"),
    _f("truth_available", "bool", False),
    _f("measurement_available", "bool", False),
    _f("measurement_valid", "bool", False),
    _f("noise_realization", "float64", True, "noise_suite"),
    _f("quantization_error", "float64", True, "quantization_suite"),
    _f("source_jitter_s", "float64", True, "timing_suite"),
    _f("transport_delay_s", "float64", True, "arrival_simulation"),
    _f("event_dropped", "bool", False),
    _f("event_burst_drop", "bool", False),
    _f("event_held", "bool", False),
    _f("event_input_drop_count", "int64", False),
    _f("event_arrivals_count", "int64", False),
    _f("event_duplicate", "bool", False),
    _f("event_timestamp_regression", "bool", False),
    _f("event_future_source_time", "bool", False),
    _f("event_outlier", "bool", False),
    _f("outlier_kind", "string", True, "outlier_suite"),
    _f("outlier_realization", "float64", True, "finite_outlier_suite"),
    _f("event_nonfinite", "bool", False),
    _f("event_impossible_jump", "bool", False),
    _f("event_flags", "string", False),
)

FIELD_BY_NAME = {field.name: field for field in FIELD_SPECS}
FIELD_NAMES = tuple(FIELD_BY_NAME)
DEPRECATED_ALIASES = {"target_feasible": "raw_target_point_admissible"}
TRUTH_FIELDS = ("v_ref_truth", "a_ref_truth", "j_ref_truth")
COMPUTE_FIELDS = (
    "estimator_compute_us",
    "predictor_compute_us",
    "governor_compute_us",
    "follower_compute_us",
    "plant_compute_us",
    "total_compute_us",
)
NONFINITE_FAULT_FIELDS = frozenset({"p_meas", "v_meas", "a_meas"})
QP_STATUS_CATEGORIES = frozenset(
    {
        "qp_solved",
        "qp_time_limit_reached",
        "qp_max_iter_reached",
        "qp_primal_infeasible",
        "qp_dual_infeasible",
        "qp_numerical_failure",
        "qp_postcheck_failed",
        "qp_invalid_input",
        "qp_solver_unavailable",
    }
)


class SchemaValidationError(ValueError):
    """Raised when rows violate the canonical artifact contract."""


def _arrow_type(kind: str) -> Any:
    if pa is None:  # pragma: no cover - guarded by arrow_schema.
        raise ImportError("pyarrow is required for Parquet artifacts")
    return {
        "string": pa.string(),
        "int64": pa.int64(),
        "float64": pa.float64(),
        "bool": pa.bool_(),
    }[kind]


def arrow_schema() -> Any:
    """Return the canonical :class:`pyarrow.Schema` with availability metadata."""

    if pa is None:
        raise ImportError(
            "pyarrow is not installed; install the artifact dependencies to write Parquet"
        )
    fields = []
    for spec in FIELD_SPECS:
        metadata = {b"availability": spec.availability.encode("utf-8")}
        if spec.name in DEPRECATED_ALIASES:
            metadata.update(
                {
                    b"deprecated": b"true",
                    b"alias_for": DEPRECATED_ALIASES[spec.name].encode("utf-8"),
                }
            )
        fields.append(
            pa.field(
                spec.name,
                _arrow_type(spec.kind),
                nullable=spec.nullable,
                metadata=metadata,
            )
        )
    return pa.schema(fields, metadata={b"schema_version": SCHEMA_VERSION.encode()})


def empty_sample(**updates: Any) -> dict[str, Any]:
    """Create a complete null-initialized row, useful for pipeline stages."""

    updates = dict(updates)
    if "target_feasible" in updates and "raw_target_point_admissible" not in updates:
        updates["raw_target_point_admissible"] = updates["target_feasible"]
    elif "raw_target_point_admissible" in updates and "target_feasible" not in updates:
        updates["target_feasible"] = updates["raw_target_point_admissible"]
    if "fallback" in updates and "fallback_applied" not in updates:
        updates["fallback_applied"] = updates["fallback"]
    elif "fallback_applied" in updates and "fallback" not in updates:
        updates["fallback"] = updates["fallback_applied"]
    if "fallback" in updates and "fallback_requested" not in updates:
        updates["fallback_requested"] = updates["fallback"]

    row: dict[str, Any] = {name: None for name in FIELD_NAMES}
    row.update(
        {
            "source_kind": "unknown",
            "scenario_id": "clean",
            "truth_available": False,
            "measurement_available": False,
            "measurement_valid": False,
            "event_dropped": False,
            "event_burst_drop": False,
            "event_held": False,
            "event_input_drop_count": 0,
            "event_arrivals_count": 0,
            "event_duplicate": False,
            "event_timestamp_regression": False,
            "event_future_source_time": False,
            "event_outlier": False,
            "event_nonfinite": False,
            "event_impossible_jump": False,
            "event_flags": "",
            "deadline_miss": False,
            "state_reset": False,
            "event_command_measured_divergence": False,
            "feedback_correction": False,
            "invalid_input": False,
        }
    )
    unknown = set(updates) - set(FIELD_NAMES)
    if unknown:
        raise KeyError(f"unknown canonical fields: {sorted(unknown)}")
    row.update(updates)
    return row


def _is_real(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool) or (
        value.__class__.__module__.startswith("numpy")
        and value.__class__.__name__ in {"bool", "bool_"}
    )


def _check_scalar_type(spec: FieldSpec, value: Any, where: str) -> None:
    if value is None:
        if not spec.nullable:
            raise SchemaValidationError(f"{where}.{spec.name}: null is not allowed")
        return
    if spec.kind == "string" and not isinstance(value, str):
        raise SchemaValidationError(f"{where}.{spec.name}: expected string")
    if spec.kind == "bool" and not _is_bool(value):
        raise SchemaValidationError(f"{where}.{spec.name}: expected bool")
    if spec.kind == "int64" and (
        not isinstance(value, Integral) or isinstance(value, bool)
    ):
        raise SchemaValidationError(f"{where}.{spec.name}: expected int")
    if spec.kind == "float64" and not _is_real(value):
        raise SchemaValidationError(f"{where}.{spec.name}: expected real number")


def _complete_finite_state(
    row: Mapping[str, Any], prefix: str
) -> tuple[float, float, float] | None:
    values = tuple(row.get(f"{prefix}_{name}") for name in ("p", "v", "a"))
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise SchemaValidationError(f"{prefix} state is only partially available")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        return None
    return result  # type: ignore[return-value]


def _sample_limits(row: Mapping[str, Any]) -> tuple[float, float, float] | None:
    values = tuple(
        row.get(name)
        for name in ("limit_max_velocity", "limit_max_acceleration", "limit_max_jerk")
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise SchemaValidationError("motion limits are only partially available")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) and value > 0.0 for value in result):
        raise SchemaValidationError("motion limits must be finite and positive")
    return result  # type: ignore[return-value]


def _point_admissible(
    state: tuple[float, float, float], limits: tuple[float, float, float]
) -> bool:
    _, velocity, acceleration = state
    vmax, amax, _ = limits
    tolerance = 1e-8
    return abs(velocity) <= vmax + tolerance and abs(acceleration) <= amax + tolerance


def _stopping_viable(
    state: tuple[float, float, float], limits: tuple[float, float, float]
) -> bool:
    if not _point_admissible(state, limits):
        return False
    _, velocity, acceleration = state
    vmax, _, jmax = limits
    tolerance = 1e-8
    if acceleration > 0.0:
        return velocity + acceleration * acceleration / (2.0 * jmax) <= vmax + tolerance
    if acceleration < 0.0:
        return (
            velocity - acceleration * acceleration / (2.0 * jmax) >= -vmax - tolerance
        )
    return True


def _constant_jerk_segment_feasible(
    current: tuple[float, float, float],
    target: tuple[float, float, float],
    dt: float,
    limits: tuple[float, float, float],
) -> bool:
    if not math.isfinite(dt) or dt <= 0.0:
        return False
    p0, v0, a0 = current
    p1, v1, a1 = target
    vmax, amax, jmax = limits
    jerk = (a1 - a0) / dt
    tolerance = 2e-8
    reconstructed_p = p0 + v0 * dt + 0.5 * a0 * dt * dt + jerk * dt**3 / 6.0
    reconstructed_v = v0 + a0 * dt + 0.5 * jerk * dt * dt
    if (
        abs(reconstructed_p - p1) > tolerance
        or abs(reconstructed_v - v1) > tolerance
        or abs(jerk) > jmax + tolerance
        or max(abs(a0), abs(a1)) > amax + tolerance
    ):
        return False
    candidate_times = [0.0, dt]
    if jerk != 0.0:
        extremum_time = -a0 / jerk
        if 0.0 < extremum_time < dt:
            candidate_times.append(extremum_time)
    return all(
        abs(v0 + a0 * sample_time + 0.5 * jerk * sample_time**2) <= vmax + tolerance
        for sample_time in candidate_times
    )


def recompute_sample_feasibility(row: Mapping[str, Any]) -> dict[str, bool | None]:
    """Recompute every v2 feasibility flag from one long-form sample row.

    A result is ``None`` only when the supporting sample-level state is absent,
    as is allowed for raw datasets and explicitly migrated v1 artifacts.  New
    online v2 rows contain limits, replanning state, targets, command extrema,
    and therefore have no hidden dependency on a summary table or run config.
    """

    limits = _sample_limits(row)
    raw = _complete_finite_state(row, "raw_target")
    executable = _complete_finite_state(row, "executable_target")
    current = _complete_finite_state(row, "current")
    command = _complete_finite_state(row, "command")
    executable_available = executable is not None
    result: dict[str, bool | None] = {
        "raw_target_point_admissible": None,
        "raw_target_ruckig_admissible": None,
        "executable_target_available": executable_available,
        "executable_target_point_admissible": None,
        "executable_target_stopping_viable": None,
        "executable_target_segment_feasible": None,
        "executable_target_t_free_le_dt": None,
        "command_t_free_le_dt": None,
        "command_segment_feasible": None,
        "command_stopping_viable": None,
        "command_continuous_constraints_satisfied": None,
    }
    if limits is None:
        return result
    if raw is not None:
        result["raw_target_point_admissible"] = _point_admissible(raw, limits)
        result["raw_target_ruckig_admissible"] = _stopping_viable(raw, limits)
    if executable is not None:
        result["executable_target_point_admissible"] = _point_admissible(
            executable, limits
        )
        result["executable_target_stopping_viable"] = _stopping_viable(
            executable, limits
        )
        if current is not None:
            result["executable_target_segment_feasible"] = (
                _constant_jerk_segment_feasible(
                    current, executable, float(row["dt_control"]), limits
                )
            )
        duration = row.get("executable_target_free_trajectory_duration")
        result["executable_target_t_free_le_dt"] = bool(
            duration is not None
            and math.isfinite(float(duration))
            and float(duration) <= float(row["dt_control"]) + 1e-8
        )
    if command is not None:
        command_duration = row.get("free_trajectory_duration")
        result["command_t_free_le_dt"] = bool(
            command_duration is not None
            and math.isfinite(float(command_duration))
            and float(command_duration) <= float(row["dt_control"]) + 1e-8
        )
        result["command_stopping_viable"] = _stopping_viable(command, limits)
        if current is not None:
            result["command_segment_feasible"] = _constant_jerk_segment_feasible(
                current, command, float(row["dt_control"]), limits
            )
        maxima = tuple(
            row.get(name)
            for name in (
                "command_max_abs_velocity",
                "command_max_abs_acceleration",
                "command_max_abs_jerk",
            )
        )
        if all(value is not None for value in maxima):
            vmax, amax, jmax = limits
            result["command_continuous_constraints_satisfied"] = bool(
                float(maxima[0]) <= vmax + 1e-8
                and float(maxima[1]) <= amax + 1e-8
                and float(maxima[2]) <= jmax + 1e-8
            )
    return result


def validate_sample(row: Mapping[str, Any], *, strict: bool = True) -> None:
    """Validate one row, including null/truth/fault semantics.

    ``strict=True`` requires the exact canonical columns.  Non-finite sensor
    values are accepted only for a deliberately recorded non-finite fault; all
    truth, time, state, and runtime values must otherwise be finite.
    """

    keys = set(row)
    missing = set(FIELD_NAMES) - keys
    unknown = keys - set(FIELD_NAMES)
    if missing:
        raise SchemaValidationError(f"sample missing fields: {sorted(missing)}")
    if strict and unknown:
        raise SchemaValidationError(f"sample has unknown fields: {sorted(unknown)}")

    where = f"trajectory={row.get('trajectory_id')!r}, k={row.get('k')!r}"
    for spec in FIELD_SPECS:
        _check_scalar_type(spec, row[spec.name], where)

    if row["split"] not in SPLITS:
        raise SchemaValidationError(f"{where}.split: invalid split {row['split']!r}")
    if row["k"] < 0:
        raise SchemaValidationError(f"{where}.k: must be non-negative")
    if row["event_input_drop_count"] < 0 or row["event_arrivals_count"] < 0:
        raise SchemaValidationError(f"{where}: event counts must be non-negative")
    if row["qp_iterations"] is not None and row["qp_iterations"] < 0:
        raise SchemaValidationError(f"{where}.qp_iterations: must be non-negative")
    qp_category = row["qp_status_category"]
    if qp_category is not None and qp_category not in QP_STATUS_CATEGORIES:
        raise SchemaValidationError(
            f"{where}.qp_status_category: invalid category {qp_category!r}"
        )
    if qp_category is not None and row["qp_iterations"] is None:
        raise SchemaValidationError(
            f"{where}: qp_status_category requires qp_iterations"
        )
    if (
        qp_category is not None
        and qp_category != "qp_solved"
        and row["fallback_applied"] is False
    ):
        raise SchemaValidationError(
            f"{where}: failed QP status requires an applied safety fallback"
        )
    for name in (
        "qp_solve_time_us",
        "qp_primal_residual",
        "qp_dual_residual",
        "qp_hessian_condition_number",
        "qp_constraint_condition_number",
    ):
        if row[name] is not None and row[name] < 0.0:
            raise SchemaValidationError(f"{where}.{name}: must be non-negative")
    if qp_category is None:
        qp_details = (
            "qp_solve_time_us",
            "qp_primal_residual",
            "qp_dual_residual",
            "qp_hessian_condition_number",
            "qp_constraint_condition_number",
        )
        if any(row[name] is not None for name in qp_details):
            raise SchemaValidationError(
                f"{where}: QP observability values require qp_status_category"
            )
    for name in (
        "plant_command_age_s",
        "plant_delay_s",
        "command_measured_divergence",
    ):
        if row[name] is not None and row[name] < 0.0:
            raise SchemaValidationError(f"{where}.{name}: must be non-negative")
    if row["dt_control"] <= 0.0 or not math.isfinite(row["dt_control"]):
        raise SchemaValidationError(f"{where}.dt_control: must be finite and positive")
    future_clock_anomaly = bool(row["event_future_source_time"])
    if future_clock_anomaly:
        if not row["source_time"] > row["arrival_time"] + 1e-12:
            raise SchemaValidationError(
                f"{where}: future-source anomaly requires a source timestamp "
                "later than its availability timestamp"
            )
        if row["measurement_valid"] or not row["invalid_input"]:
            raise SchemaValidationError(
                f"{where}: future-source anomaly must be rejected as invalid input"
            )
    elif row["arrival_time"] < row["source_time"]:
        raise SchemaValidationError(f"{where}: arrival_time cannot precede source_time")
    if row["transport_delay_s"] is not None:
        if row["transport_delay_s"] < 0.0 and not future_clock_anomaly:
            raise SchemaValidationError(
                f"{where}.transport_delay_s: must be non-negative"
            )
        realized_delay = row["arrival_time"] - row["source_time"]
        if not math.isclose(row["transport_delay_s"], realized_delay, abs_tol=1e-9):
            raise SchemaValidationError(
                f"{where}.transport_delay_s: does not match arrival_time-source_time"
            )

    for name in FIELD_NAMES:
        value = row[name]
        if value is None or FIELD_BY_NAME[name].kind != "float64":
            continue
        if math.isfinite(float(value)):
            continue
        allowed_fault = (
            name in NONFINITE_FAULT_FIELDS
            and row["event_nonfinite"]
            and row["event_outlier"]
            and not row["measurement_valid"]
        )
        if not allowed_fault:
            raise SchemaValidationError(
                f"{where}.{name}: non-finite value is unflagged"
            )

    truth_values = tuple(row[name] for name in TRUTH_FIELDS)
    if row["truth_available"]:
        if any(value is None for value in truth_values):
            raise SchemaValidationError(
                f"{where}: truth_available requires complete v/a/j truth"
            )
    elif any(value is not None for value in truth_values):
        raise SchemaValidationError(
            f"{where}: unavailable derivative truth must be null, not estimated"
        )

    if row["measurement_available"] and row["p_meas"] is None:
        raise SchemaValidationError(f"{where}: available measurement needs p_meas")
    if not row["measurement_available"] and any(
        row[name] is not None for name in ("p_meas", "v_meas", "a_meas")
    ):
        raise SchemaValidationError(
            f"{where}: unavailable measurement fields must be null"
        )
    if row["measurement_valid"] and not row["measurement_available"]:
        raise SchemaValidationError(f"{where}: valid measurement cannot be unavailable")
    if row["event_dropped"] and row["measurement_available"]:
        raise SchemaValidationError(f"{where}: dropped measurement cannot be available")
    if row["event_burst_drop"] and not row["event_dropped"]:
        raise SchemaValidationError(f"{where}: burst-drop flag requires dropped flag")
    if row["event_outlier"] and (row["measurement_valid"] or not row["invalid_input"]):
        raise SchemaValidationError(
            f"{where}: outlier must be marked invalid and measurement_valid=false"
        )
    if row["event_nonfinite"] and not row["event_outlier"]:
        raise SchemaValidationError(f"{where}: non-finite event must be an outlier")
    if row["event_impossible_jump"] and not row["event_outlier"]:
        raise SchemaValidationError(f"{where}: impossible jump must be an outlier")
    if row["fallback"] is False and row["fallback_reason"] not in (None, ""):
        raise SchemaValidationError(f"{where}: fallback_reason set without fallback")
    if row["fallback"] is True and not row["fallback_reason"]:
        raise SchemaValidationError(f"{where}: fallback requires fallback_reason")
    if (
        row["target_projected"] is True
        and row["raw_target_point_admissible"] is None
        and row["legacy_target_feasible_v1"] is None
    ):
        raise SchemaValidationError(
            f"{where}: projection requires raw feasibility result"
        )
    if row["target_feasible"] != row["raw_target_point_admissible"]:
        raise SchemaValidationError(
            f"{where}: deprecated target_feasible must exactly alias "
            "raw_target_point_admissible"
        )
    if (
        row["fallback_applied"] is not None
        and row["fallback"] is not None
        and row["fallback"] != row["fallback_applied"]
    ):
        raise SchemaValidationError(
            f"{where}: deprecated fallback must alias fallback_applied on v2 rows"
        )
    if row["fallback_applied"] is False and row["fallback_reason"] not in (None, ""):
        raise SchemaValidationError(
            f"{where}: fallback_reason is set without an applied fallback"
        )
    if row["fallback_applied"] is True and not row["fallback_reason"]:
        raise SchemaValidationError(
            f"{where}: applied fallback requires fallback_reason"
        )
    if row["safety_guarantee"] is True and row["emergency_mode"] is True:
        raise SchemaValidationError(
            f"{where}: emergency mode cannot claim a safety guarantee"
        )
    if row["safety_guarantee"] is True:
        required_command_guarantees = (
            "command_segment_feasible",
            "command_stopping_viable",
            "command_continuous_constraints_satisfied",
        )
        failed = [
            name for name in required_command_guarantees if row[name] is not True
        ]
        if failed:
            raise SchemaValidationError(
                f"{where}: safety_guarantee requires verified command safety; "
                f"not true: {failed}"
            )
    axis_source_time = row["posterior_axis_source_time"]
    axis_available_time = row["posterior_axis_available_time"]
    if (axis_source_time is None) != (axis_available_time is None):
        raise SchemaValidationError(
            f"{where}: per-axis posterior source/availability times must be paired"
        )
    if axis_source_time is not None:
        if row["measurement_sync_method"] is None:
            raise SchemaValidationError(
                f"{where}: per-axis posterior timing requires a synchronization method"
            )
        if axis_source_time > row["control_time"] + 1e-12:
            raise SchemaValidationError(
                f"{where}: per-axis posterior source time is in the future"
            )
        if axis_available_time > row["control_time"] + 1e-12:
            raise SchemaValidationError(
                f"{where}: per-axis posterior was not available at control time"
            )
    expected_feasibility = recompute_sample_feasibility(row)
    for field, expected in expected_feasibility.items():
        recorded = row[field]
        if recorded is None:
            continue
        if expected is None:
            # Raw/source fixtures and explicit v1 migrations may not contain the
            # online audit state. Native v2 runner rows always do and are checked.
            continue
        if bool(recorded) != bool(expected):
            raise SchemaValidationError(
                f"{where}.{field}: recorded={recorded!r}, recomputed={expected!r}"
            )
    plant_measurement = tuple(
        row[name]
        for name in ("plant_measured_p", "plant_measured_v", "plant_measured_a")
    )
    if any(value is None for value in plant_measurement) and any(
        value is not None for value in plant_measurement
    ):
        raise SchemaValidationError(
            f"{where}: plant measured state must contain complete p/v/a components"
        )
    measured_delta = tuple(
        row[name]
        for name in (
            "command_measured_delta_p",
            "command_measured_delta_v",
            "command_measured_delta_a",
        )
    )
    if any(value is None for value in measured_delta) and any(
        value is not None for value in measured_delta
    ):
        raise SchemaValidationError(
            f"{where}: command-measured delta must contain complete p/v/a components"
        )
    correction = tuple(
        row[name]
        for name in (
            "feedback_correction_p",
            "feedback_correction_v",
            "feedback_correction_a",
        )
    )
    if any(value is None for value in correction) and any(
        value is not None for value in correction
    ):
        raise SchemaValidationError(
            f"{where}: feedback correction must contain complete p/v/a components"
        )
    if row["feedback_correction"] and (
        any(value is None for value in correction)
        or not row["feedback_correction_reason"]
    ):
        raise SchemaValidationError(
            f"{where}: feedback correction requires components and a reason"
        )
    if row["plant_command_source_time"] is not None:
        if row["command_time"] is None or row["plant_command_age_s"] is None:
            raise SchemaValidationError(
                f"{where}: plant command source time requires command time and age"
            )
        realized_age = row["command_time"] - row["plant_command_source_time"]
        if not math.isclose(realized_age, row["plant_command_age_s"], abs_tol=1e-9):
            raise SchemaValidationError(
                f"{where}.plant_command_age_s: does not match command time-source time"
            )
    for name in COMPUTE_FIELDS:
        value = row[name]
        if value is not None and value < 0.0:
            raise SchemaValidationError(f"{where}.{name}: runtime must be non-negative")


def validate_samples(
    rows: Iterable[Mapping[str, Any]],
    *,
    strict: bool = True,
    require_nonempty: bool = True,
) -> int:
    """Validate rows and trajectory-level ordering; return the row count."""

    count = 0
    previous: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    identity_split: dict[tuple[str, str], str] = {}
    for row in rows:
        validate_sample(row, strict=strict)
        count += 1
        # A canonical artifact may concatenate several runs and method-matrix
        # cells.  Ordering is local to one realized pipeline stream, while split
        # isolation below intentionally remains trajectory-wide across runs.
        key = (
            str(row["run_id"]),
            str(row["dataset_id"]),
            str(row["trajectory_id"]),
            str(row["joint_id"]),
            row["estimator_id"],
            row["predictor_id"],
            row["target_mode"],
            row["governor_id"],
            row["follower_id"],
            row["plant_id"],
            str(row["scenario_id"]),
        )
        identity_key = (str(row["dataset_id"]), str(row["trajectory_id"]))
        old_split = identity_split.setdefault(identity_key, str(row["split"]))
        if old_split != row["split"]:
            raise SchemaValidationError(
                f"trajectory {identity_key!r} appears in multiple splits"
            )
        prior = previous.get(key)
        if prior is not None:
            if row["k"] <= prior["k"]:
                raise SchemaValidationError(f"{key}: k must be strictly increasing")
            if row["control_time"] <= prior["control_time"]:
                raise SchemaValidationError(f"{key}: control_time must increase")
            source_delta = row["source_time"] - prior["source_time"]
            clock_comparable = not (
                prior["event_future_source_time"] or row["event_future_source_time"]
            )
            if (
                clock_comparable
                and source_delta == 0.0
                and not (row["event_duplicate"] or row["event_held"])
            ):
                raise SchemaValidationError(
                    f"{key}, k={row['k']}: duplicate timestamp is unflagged"
                )
            if (
                clock_comparable
                and source_delta < 0.0
                and not row["event_timestamp_regression"]
            ):
                raise SchemaValidationError(
                    f"{key}, k={row['k']}: timestamp regression is unflagged"
                )
        previous[key] = row
    if require_nonempty and count == 0:
        raise SchemaValidationError("artifact contains no samples")
    return count


def _migration_limit_value(
    limits: Mapping[str, Any], name: str, joint_id: str
) -> float:
    value = limits[name]
    if isinstance(value, Mapping):
        value = value[joint_id]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raise SchemaValidationError(
            "v1 migration limits must be scalars or joint_id mappings; positional "
            "sequences are ambiguous for long-form rows"
        )
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise SchemaValidationError(f"invalid migration limit {name}={value!r}")
    return result


def migrate_sample_v1_to_v2(
    row: Mapping[str, Any],
    *,
    limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Losslessly migrate one canonical v1 row to the v2 column contract.

    V1's ``target_feasible`` was semantically ambiguous.  Its original value is
    preserved in ``legacy_target_feasible_v1`` but is never copied into a v2
    feasibility field.  Supplying explicit motion limits lets the migration
    recompute the raw point/stopping predicates from the stored target state;
    otherwise those new fields and their deprecated alias remain null.
    """

    migrated = empty_sample()
    for name in FIELD_NAMES:
        if name in row and name not in {
            "target_feasible",
            "legacy_target_feasible_v1",
        }:
            migrated[name] = row[name]
    migrated["legacy_target_feasible_v1"] = row.get("target_feasible")
    migrated["target_feasible"] = None
    migrated["raw_target_point_admissible"] = None
    migrated["raw_target_ruckig_admissible"] = None
    old_fallback = row.get("fallback")
    migrated["fallback_requested"] = old_fallback
    # Historical status-only fallbacks cannot honestly be relabelled as applied.
    migrated["fallback_applied"] = None
    migrated["safety_guarantee"] = None
    migrated["emergency_mode"] = None
    if row.get("posterior_state_time") is not None:
        migrated["measurement_sync_method"] = "legacy_v1_unspecified"
    if limits is not None:
        required = {"max_velocity", "max_acceleration", "max_jerk"}
        missing = required - set(limits)
        if missing:
            raise SchemaValidationError(
                f"v1 migration limits are missing {sorted(missing)}"
            )
        joint_id = str(migrated.get("joint_id"))
        migrated["limit_max_velocity"] = _migration_limit_value(
            limits, "max_velocity", joint_id
        )
        migrated["limit_max_acceleration"] = _migration_limit_value(
            limits, "max_acceleration", joint_id
        )
        migrated["limit_max_jerk"] = _migration_limit_value(
            limits, "max_jerk", joint_id
        )
        recomputed = recompute_sample_feasibility(migrated)
        migrated["raw_target_point_admissible"] = recomputed[
            "raw_target_point_admissible"
        ]
        migrated["raw_target_ruckig_admissible"] = recomputed[
            "raw_target_ruckig_admissible"
        ]
        migrated["target_feasible"] = migrated["raw_target_point_admissible"]
        if migrated.get("command_p") is not None:
            migrated["command_stopping_viable"] = recomputed["command_stopping_viable"]
    return migrated


def migrate_samples_v1_to_v2(
    rows: Iterable[Mapping[str, Any]],
    *,
    limits: Mapping[str, Any] | None = None,
    validate: bool = True,
) -> list[dict[str, Any]]:
    """Migrate v1 rows in memory, optionally validating the complete v2 result."""

    migrated = [migrate_sample_v1_to_v2(row, limits=limits) for row in rows]
    if validate:
        validate_samples(migrated)
    return migrated


def rows_to_table(rows: Sequence[Mapping[str, Any]], *, validate: bool = True) -> Any:
    """Build a canonical Arrow table without allowing implicit extra columns."""

    if pa is None:
        raise ImportError("pyarrow is required to build an artifact table")
    if validate:
        validate_samples(rows)
    columns = {name: [row[name] for row in rows] for name in FIELD_NAMES}
    return pa.Table.from_pydict(columns, schema=arrow_schema())


def validate_arrow_table(table: Any, *, validate_rows: bool = True) -> int:
    """Strictly validate Arrow types, nullability/metadata, and row semantics."""

    if pa is None:
        raise ImportError("pyarrow is required to validate an artifact table")
    if not isinstance(table, pa.Table):
        raise SchemaValidationError("artifact is not a pyarrow.Table")
    expected = arrow_schema()
    if not table.schema.equals(expected, check_metadata=True):
        raise SchemaValidationError(
            "Arrow schema (including availability/version metadata) is not canonical"
        )
    if validate_rows:
        return validate_samples(table.to_pylist())
    return table.num_rows


def write_parquet(
    rows: Sequence[Mapping[str, Any]],
    path: str | Path,
    *,
    compression: str = "zstd",
) -> Path:
    """Validate and atomically write a compressed canonical Parquet artifact."""

    if pq is None:
        raise ImportError("pyarrow is required to write Parquet artifacts")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    pq.write_table(rows_to_table(rows), temporary, compression=compression)
    temporary.replace(output)
    return output


def read_parquet(
    path: str | Path,
    *,
    validate: bool = True,
    migrate_v1: bool = False,
    migration_limits: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read v2 Parquet, or explicitly migrate a v1 table in memory.

    Legacy migration is opt-in so an old artifact can never silently masquerade
    as a native v2 run.  Call :func:`write_parquet` on the returned rows to emit
    canonical v2 Arrow metadata.
    """

    if pq is None:
        raise ImportError("pyarrow is required to read Parquet artifacts")
    table = pq.read_table(Path(path))
    version = (table.schema.metadata or {}).get(b"schema_version", b"").decode()
    rows = table.to_pylist()
    if version == LEGACY_SCHEMA_VERSION:
        if not migrate_v1:
            raise SchemaValidationError(
                "v1 Parquet requires migrate_v1=True; implicit schema migration "
                "is forbidden"
            )
        return migrate_samples_v1_to_v2(
            rows, limits=migration_limits, validate=validate
        )
    validate_arrow_table(table, validate_rows=False)
    if validate:
        validate_samples(rows)
    return rows


__all__ = [
    "COMPUTE_FIELDS",
    "FIELD_BY_NAME",
    "FIELD_NAMES",
    "FIELD_SPECS",
    "FieldSpec",
    "DEPRECATED_ALIASES",
    "LEGACY_SCHEMA_VERSION",
    "QP_STATUS_CATEGORIES",
    "SCHEMA_VERSION",
    "SPLITS",
    "SchemaValidationError",
    "TRUTH_FIELDS",
    "arrow_schema",
    "empty_sample",
    "migrate_sample_v1_to_v2",
    "migrate_samples_v1_to_v2",
    "read_parquet",
    "recompute_sample_feasibility",
    "rows_to_table",
    "validate_sample",
    "validate_samples",
    "validate_arrow_table",
    "write_parquet",
]
