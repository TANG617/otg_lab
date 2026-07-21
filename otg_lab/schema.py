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

SCHEMA_VERSION = "otg.sample.v1"
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
    _f("target_feasible", "bool", True, "after_feasibility_check"),
    _f("target_projected", "bool", True, "after_governor"),
    _f("fallback", "bool", True, "after_online_pipeline"),
    _f("fallback_reason", "string", True, "when_fallback"),
    _f("solver_status", "string", True, "after_solver"),
    _f("qp_iterations", "int64", True, "after_qp_solver"),
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
    _f("event_outlier", "bool", False),
    _f("outlier_kind", "string", True, "outlier_suite"),
    _f("outlier_realization", "float64", True, "finite_outlier_suite"),
    _f("event_nonfinite", "bool", False),
    _f("event_impossible_jump", "bool", False),
    _f("event_flags", "string", False),
)

FIELD_BY_NAME = {field.name: field for field in FIELD_SPECS}
FIELD_NAMES = tuple(FIELD_BY_NAME)
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
    for name in (
        "plant_command_age_s",
        "plant_delay_s",
        "command_measured_divergence",
    ):
        if row[name] is not None and row[name] < 0.0:
            raise SchemaValidationError(f"{where}.{name}: must be non-negative")
    if row["dt_control"] <= 0.0 or not math.isfinite(row["dt_control"]):
        raise SchemaValidationError(f"{where}.dt_control: must be finite and positive")
    if row["arrival_time"] < row["source_time"]:
        raise SchemaValidationError(f"{where}: arrival_time cannot precede source_time")
    if row["transport_delay_s"] is not None:
        if row["transport_delay_s"] < 0.0:
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
    if row["target_projected"] is True and row["target_feasible"] is None:
        raise SchemaValidationError(
            f"{where}: projection requires raw feasibility result"
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
            if source_delta == 0.0 and not (
                row["event_duplicate"] or row["event_held"]
            ):
                raise SchemaValidationError(
                    f"{key}, k={row['k']}: duplicate timestamp is unflagged"
                )
            if source_delta < 0.0 and not row["event_timestamp_regression"]:
                raise SchemaValidationError(
                    f"{key}, k={row['k']}: timestamp regression is unflagged"
                )
        previous[key] = row
    if require_nonempty and count == 0:
        raise SchemaValidationError("artifact contains no samples")
    return count


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


def read_parquet(path: str | Path, *, validate: bool = True) -> list[dict[str, Any]]:
    """Read a canonical Parquet artifact and optionally enforce its contract."""

    if pq is None:
        raise ImportError("pyarrow is required to read Parquet artifacts")
    table = pq.read_table(Path(path))
    validate_arrow_table(table, validate_rows=False)
    rows = table.to_pylist()
    if validate:
        validate_samples(rows)
    return rows


__all__ = [
    "COMPUTE_FIELDS",
    "FIELD_BY_NAME",
    "FIELD_NAMES",
    "FIELD_SPECS",
    "FieldSpec",
    "SCHEMA_VERSION",
    "SPLITS",
    "SchemaValidationError",
    "TRUTH_FIELDS",
    "arrow_schema",
    "empty_sample",
    "read_parquet",
    "rows_to_table",
    "validate_sample",
    "validate_samples",
    "validate_arrow_table",
    "write_parquet",
]
