"""Frozen V4 method identities and sample-level identity audits.

The functions in this module are deliberately side-effect free except for the
explicit JSON loader.  They do not construct trajectories, controllers, or
predictors.  This keeps method-matrix validation usable before any fresh V4
test trajectory is generated.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

METHOD_MATRIX_SCHEMA_VERSION = "otg.method-matrix.v4"

PRIMARY_METHOD_IDS = (
    "one_step_governed_p_direct",
    "one_step_governed_pv_direct",
    "one_step_governed_pva_direct",
)
SECONDARY_METHOD_IDS = (
    "deployed_p_only_ordinary_ruckig",
    "predicted_p_ordinary_ruckig",
    "raw_predicted_pv_ordinary_ruckig",
    "raw_predicted_pva_ordinary_ruckig",
)
ORACLE_METHOD_IDS = (
    "oracle_one_step_p_direct",
    "oracle_one_step_pv_direct",
    "oracle_one_step_pva_direct",
)

TARGET_MODE_BY_METHOD = {
    PRIMARY_METHOD_IDS[0]: "p",
    PRIMARY_METHOD_IDS[1]: "pv",
    PRIMARY_METHOD_IDS[2]: "pva",
    SECONDARY_METHOD_IDS[0]: "p",
    SECONDARY_METHOD_IDS[1]: "p",
    SECONDARY_METHOD_IDS[2]: "pv",
    SECONDARY_METHOD_IDS[3]: "pva",
    ORACLE_METHOD_IDS[0]: "p",
    ORACLE_METHOD_IDS[1]: "pv",
    ORACLE_METHOD_IDS[2]: "pva",
}

_METHOD_GROUPS = {
    "primary_methods": PRIMARY_METHOD_IDS,
    "secondary_methods": SECONDARY_METHOD_IDS,
    "oracle_methods": ORACLE_METHOD_IDS,
}

_PRIMARY_REQUIRED_PIPELINE_FIELDS = frozenset(
    {
        "estimator",
        "estimator_parameters",
        "predictor",
        "predictor_parameters",
        "prediction_horizon_ms",
        "target_mode",
        "governor",
        "governor_parameters",
        "follower",
        "follower_parameters",
        "plant",
        "plant_parameters",
        "measured_state_mode",
        "motion_limits",
        "control_dt_s",
        "minimum_duration_s",
        "target_alignment",
        "initial_state_policy",
        "sample_population",
        "fallback_policy",
        "runtime_measurement_policy",
        "method_family",
    }
)

_PRIMARY_PURITY_EXPECTATIONS = {
    "method_semantics": "direct_constant_jerk",
    "native_follower": "direct_executable",
    "actual_command_algorithm": "direct_executable",
    "native_command_executed": True,
    "safety_shield_requested": False,
    "safety_shield_applied": False,
    "fallback_requested": False,
    "fallback_applied": False,
    "fallback_changes_algorithm": False,
    "fallback_controller": "",
    "command_profile_kind": "constant_jerk",
    "command_profile_exact": True,
    "command_constant_jerk_exact": True,
    "command_endpoint_matches_profile": True,
    "command_profile_continuous_constraints_satisfied": True,
}

_ORDINARY_ROW_EXPECTATIONS = {
    "method_semantics": "ordinary_ruckig_unshielded",
    "native_follower": "ordinary_ruckig",
    "actual_command_algorithm": "ordinary_ruckig",
    "native_command_executed": True,
    "safety_shield_requested": False,
    "safety_shield_applied": False,
    "fallback_requested": False,
    "fallback_applied": False,
    "fallback_changes_algorithm": False,
    "fallback_controller": "",
}

_SAME_INFORMATION_FIELDS = (
    # The position input stream and its timing/validity.
    "split",
    "seed",
    "source_time",
    "arrival_time",
    "control_time",
    "dt_actual",
    "dt_control",
    "p_ref",
    "v_ref_truth",
    "a_ref_truth",
    "j_ref_truth",
    "p_meas",
    "v_meas",
    "a_meas",
    "measurement_available",
    "measurement_valid",
    "invalid_input",
    "noise_realization",
    "quantization_error",
    "source_jitter_s",
    "transport_delay_s",
    "event_dropped",
    "event_burst_drop",
    "event_held",
    "event_input_drop_count",
    "event_arrivals_count",
    "event_duplicate",
    "event_timestamp_regression",
    "event_future_source_time",
    "event_outlier",
    "outlier_kind",
    "outlier_realization",
    "event_nonfinite",
    "event_impossible_jump",
    "event_flags",
    # Estimator identity and complete synchronized posterior.
    "estimator_id",
    "posterior_p",
    "posterior_v",
    "posterior_a",
    "posterior_state_time",
    "posterior_available_time",
    "posterior_axis_source_time",
    "posterior_axis_available_time",
    "measurement_sync_method",
    # Predictor identity and complete prediction.
    "predictor_id",
    "prediction_p",
    "prediction_v",
    "prediction_a",
    "prediction_time",
    "prediction_horizon_ms",
    # Downstream declared identity, clock, and limits.
    "governor_id",
    "follower_id",
    "plant_id",
    "raw_target_time",
    "limit_max_velocity",
    "limit_max_acceleration",
    "limit_max_jerk",
)

_INITIAL_STATE_FIELDS = ("current_p", "current_v", "current_a")


def _default_matrix_path() -> Path:
    return Path(__file__).resolve().parents[1] / "V4_METHOD_MATRIX.json"


def load_v4_method_matrix(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the canonical V4 method matrix."""

    matrix_path = _default_matrix_path() if path is None else Path(path)
    with matrix_path.open(encoding="utf-8") as stream:
        matrix = json.load(stream)
    validate_v4_method_matrix(matrix)
    return matrix


def build_v4_method_matrix(path: str | Path | None = None) -> dict[str, Any]:
    """Return an owned validated copy of the canonical method matrix."""

    return copy.deepcopy(load_v4_method_matrix(path))


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be a mapping")
    return value


def _method_map(
    matrix: Mapping[str, Any], group_name: str
) -> dict[str, Mapping[str, Any]]:
    methods = matrix.get(group_name)
    if not isinstance(methods, Sequence) or isinstance(methods, (str, bytes)):
        raise ValueError(f"{group_name} must be a sequence")
    result: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(methods):
        method = _require_mapping(item, f"{group_name}[{index}]")
        method_id = str(method.get("method_id", ""))
        if not method_id:
            raise ValueError(f"{group_name}[{index}] lacks method_id")
        if method_id in result:
            raise ValueError(f"duplicate method_id {method_id!r}")
        result[method_id] = method
    return result


def _pipeline(method: Mapping[str, Any], method_id: str) -> Mapping[str, Any]:
    return _require_mapping(method.get("pipeline"), f"{method_id}.pipeline")


def _validate_exact_ids(
    by_group: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    all_ids: list[str] = []
    for group_name, expected in _METHOD_GROUPS.items():
        observed = tuple(by_group[group_name])
        if observed != expected:
            raise ValueError(
                f"{group_name} must contain exactly {expected!r} in that order; "
                f"observed={observed!r}"
            )
        all_ids.extend(observed)
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("method_id values must be unique across the V4 matrix")


def _validate_primary_identity(
    primary: Mapping[str, Mapping[str, Any]],
) -> None:
    pipelines: list[Mapping[str, Any]] = []
    for method_id in PRIMARY_METHOD_IDS:
        method = primary[method_id]
        pipeline = _pipeline(method, method_id)
        missing = sorted(_PRIMARY_REQUIRED_PIPELINE_FIELDS - set(pipeline))
        if missing:
            raise ValueError(
                f"primary method {method_id!r} lacks required fields {missing!r}"
            )
        expected_mode = TARGET_MODE_BY_METHOD[method_id]
        if pipeline["target_mode"] != expected_mode:
            raise ValueError(f"{method_id!r} must use target_mode={expected_mode!r}")
        if method.get("causal") is not True or method.get("deployable") is not True:
            raise ValueError(f"{method_id!r} must be causal and deployable")
        if method.get("included_in_primary") is not True:
            raise ValueError(f"{method_id!r} must be included in primary")
        observed_identity = (
            pipeline["estimator"],
            pipeline["predictor"],
            pipeline["prediction_horizon_ms"],
            pipeline["governor"],
            pipeline["follower"],
            pipeline["plant"],
            pipeline["measured_state_mode"],
            pipeline["method_family"],
        )
        expected_identity = (
            "local_poly",
            "constant_jerk",
            0.0,
            "one_step",
            "direct",
            "ideal",
            "previous_command",
            "one_step_governed_direct",
        )
        if observed_identity != expected_identity:
            raise ValueError(
                f"{method_id!r} does not map to the locked one-step/direct "
                f"implementation: observed={observed_identity!r}"
            )
        pipelines.append(pipeline)

    reference = {
        key: value for key, value in pipelines[0].items() if key != "target_mode"
    }
    for method_id, pipeline in zip(PRIMARY_METHOD_IDS[1:], pipelines[1:]):
        comparable = {
            key: value for key, value in pipeline.items() if key != "target_mode"
        }
        if comparable != reference:
            raise ValueError(
                "primary P/PV/PVA pipeline declarations may differ only in "
                f"target_mode; mismatch at {method_id!r}"
            )


def _validate_secondary_identity(
    secondary: Mapping[str, Mapping[str, Any]],
) -> None:
    for method_id in SECONDARY_METHOD_IDS:
        method = secondary[method_id]
        pipeline = _pipeline(method, method_id)
        expected_mode = TARGET_MODE_BY_METHOD[method_id]
        if pipeline.get("target_mode") != expected_mode:
            raise ValueError(f"{method_id!r} must use target_mode={expected_mode!r}")
        if method.get("included_in_primary") is not False:
            raise ValueError(f"{method_id!r} cannot be included in primary")
        if method.get("causal") is not True or method.get("deployable") is not True:
            raise ValueError(f"{method_id!r} must be causal and deployable")
        identity = (
            pipeline.get("governor"),
            pipeline.get("follower"),
            pipeline.get("method_family"),
            pipeline.get("target_projection"),
        )
        if identity != (
            "none",
            "ruckig",
            "ordinary_ruckig_unshielded",
            False,
        ):
            raise ValueError(f"{method_id!r} is not native unshielded Community Ruckig")
        follower_parameters = _require_mapping(
            pipeline.get("follower_parameters"),
            f"{method_id}.pipeline.follower_parameters",
        )
        if follower_parameters.get("safety_shield") is not False:
            raise ValueError(f"{method_id!r} must explicitly disable its shield")
        fallback_policy = _require_mapping(
            pipeline.get("fallback_policy"),
            f"{method_id}.pipeline.fallback_policy",
        )
        if (
            fallback_policy.get("hidden_fallback_allowed") is not False
            or fallback_policy.get("native_failure_disposition") != "failed_unit"
            or fallback_policy.get("algorithm_replacement_allowed") is not False
        ):
            raise ValueError(
                f"{method_id!r} must preserve native failure without fallback"
            )

    deployed = _pipeline(secondary[SECONDARY_METHOD_IDS[0]], SECONDARY_METHOD_IDS[0])
    if (
        deployed.get("estimator") != "position_only"
        or deployed.get("predictor") != "zero_order_hold"
        or deployed.get("prediction_horizon_ms") != 0.0
    ):
        raise ValueError("deployed P-only ordinary Ruckig identity is incorrect")

    corrected = [
        _pipeline(secondary[method_id], method_id)
        for method_id in SECONDARY_METHOD_IDS[1:]
    ]
    for method_id, pipeline in zip(SECONDARY_METHOD_IDS[1:], corrected):
        if (
            pipeline.get("estimator") != "local_poly"
            or pipeline.get("predictor") != "constant_jerk"
            or pipeline.get("prediction_horizon_ms") != 0.0
        ):
            raise ValueError(
                f"{method_id!r} does not use the locked estimated prediction"
            )
    reference = {
        key: value for key, value in corrected[0].items() if key != "target_mode"
    }
    for method_id, pipeline in zip(SECONDARY_METHOD_IDS[2:], corrected[1:]):
        comparable = {
            key: value for key, value in pipeline.items() if key != "target_mode"
        }
        if comparable != reference:
            raise ValueError(
                "corrected ordinary-Ruckig P/PV/PVA declarations may differ "
                f"only in target_mode; mismatch at {method_id!r}"
            )


def _validate_oracle_identity(
    oracle: Mapping[str, Mapping[str, Any]],
    primary: Mapping[str, Mapping[str, Any]],
) -> None:
    for oracle_id, primary_id in zip(ORACLE_METHOD_IDS, PRIMARY_METHOD_IDS):
        method = oracle[oracle_id]
        pipeline = _pipeline(method, oracle_id)
        primary_pipeline = _pipeline(primary[primary_id], primary_id)
        if method.get("causal") is not False:
            raise ValueError(f"{oracle_id!r} must be explicitly noncausal")
        if method.get("offline_only") is not True:
            raise ValueError(f"{oracle_id!r} must be offline_only")
        if method.get("deployable") is not False:
            raise ValueError(f"{oracle_id!r} cannot be deployable")
        if method.get("included_in_primary") is not False:
            raise ValueError(f"{oracle_id!r} cannot be included in primary")
        if method.get("eligible_for_parameter_selection") is not False:
            raise ValueError(f"{oracle_id!r} cannot select parameters")
        if pipeline.get("target_mode") != TARGET_MODE_BY_METHOD[oracle_id]:
            raise ValueError(f"{oracle_id!r} has the wrong target_mode")
        if pipeline.get("predictor") != "oracle":
            raise ValueError(f"{oracle_id!r} must use the existing oracle predictor")
        if pipeline.get("prediction_source") != "synthetic_truth":
            raise ValueError(f"{oracle_id!r} must source predictions from truth")
        shared_fields = (
            "prediction_horizon_ms",
            "target_mode",
            "governor",
            "governor_parameters",
            "follower",
            "follower_parameters",
            "plant",
            "plant_parameters",
            "measured_state_mode",
            "motion_limits",
            "control_dt_s",
            "minimum_duration_s",
            "target_alignment",
            "initial_state_policy",
            "sample_population",
            "fallback_policy",
            "runtime_measurement_policy",
            "method_family",
        )
        mismatches = [
            field
            for field in shared_fields
            if pipeline.get(field) != primary_pipeline.get(field)
        ]
        if mismatches:
            raise ValueError(
                f"{oracle_id!r} differs from {primary_id!r} in downstream or "
                f"timing fields {mismatches!r}"
            )


def validate_v4_method_matrix(matrix: Mapping[str, Any]) -> None:
    """Fail closed unless ``matrix`` is exactly the preregistered V4 identity."""

    matrix = _require_mapping(matrix, "V4 method matrix")
    if matrix.get("schema_version") != METHOD_MATRIX_SCHEMA_VERSION:
        raise ValueError("unexpected V4 method-matrix schema_version")
    if matrix.get("protocol_version") != "v4":
        raise ValueError("method matrix must declare protocol_version='v4'")
    if matrix.get("community_ruckig_only") is not True:
        raise ValueError("V4 method matrix must be Community-Ruckig-only")

    by_group = {
        group_name: _method_map(matrix, group_name) for group_name in _METHOD_GROUPS
    }
    _validate_exact_ids(by_group)
    _validate_primary_identity(by_group["primary_methods"])
    _validate_secondary_identity(by_group["secondary_methods"])
    _validate_oracle_identity(by_group["oracle_methods"], by_group["primary_methods"])

    primary_comparison = _require_mapping(
        matrix.get("primary_comparison"), "primary_comparison"
    )
    if (
        primary_comparison.get("baseline_method") != PRIMARY_METHOD_IDS[0]
        or primary_comparison.get("candidate_method") != PRIMARY_METHOD_IDS[2]
    ):
        raise ValueError("the V4 primary comparison must be PVA-direct versus P-direct")
    if any(
        method_id in ORACLE_METHOD_IDS
        for method_id in (
            primary_comparison.get("baseline_method"),
            primary_comparison.get("candidate_method"),
        )
    ):
        raise ValueError("oracle methods cannot enter the primary comparison")


def primary_method_specs(
    matrix: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return runner-ready owned primary method specifications."""

    source = load_v4_method_matrix() if matrix is None else matrix
    validate_v4_method_matrix(source)
    return copy.deepcopy(list(source["primary_methods"]))


def secondary_method_specs(
    matrix: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return runner-ready owned ordinary-Ruckig secondary specifications."""

    source = load_v4_method_matrix() if matrix is None else matrix
    validate_v4_method_matrix(source)
    return copy.deepcopy(list(source["secondary_methods"]))


def oracle_method_specs(
    matrix: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return runner-ready owned offline oracle method specifications."""

    source = load_v4_method_matrix() if matrix is None else matrix
    validate_v4_method_matrix(source)
    return copy.deepcopy(list(source["oracle_methods"]))


def _identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: row.get(field)
        for field in (
            "dataset_id",
            "session_id",
            "trajectory_id",
            "scenario_id",
            "joint_id",
            "k",
            "control_time",
            "method_id",
        )
    }


def _values_equal(left: Any, right: Any, *, tolerance: float) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        left_float = float(left)
        right_float = float(right)
        if math.isnan(left_float) or math.isnan(right_float):
            return math.isnan(left_float) and math.isnan(right_float)
        return math.isclose(left_float, right_float, rel_tol=0.0, abs_tol=tolerance)
    return left == right


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def audit_executed_primary_configuration(
    executed_method_matrix: Sequence[Mapping[str, Any]],
    *,
    canonical_matrix: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove that the executed primary parameters and policies were locked.

    The expanded method matrix is emitted by the execution engine after all
    base-config merges.  This audit compares that effective pipeline against
    the preregistered pipeline byte-for-byte (apart from the injected
    ``method_id``), and proves that control, limits, and data policies are
    identical across the three primary methods.
    """

    source = load_v4_method_matrix() if canonical_matrix is None else canonical_matrix
    validate_v4_method_matrix(source)
    canonical = _method_map(source, "primary_methods")
    observed: dict[str, Mapping[str, Any]] = {}
    failures: list[str] = []
    for index, raw in enumerate(executed_method_matrix):
        if not isinstance(raw, Mapping):
            failures.append(f"executed[{index}]:not_mapping")
            continue
        method_id = str(raw.get("method_id", ""))
        if method_id not in PRIMARY_METHOD_IDS:
            continue
        if method_id in observed:
            failures.append(f"{method_id}:duplicate")
            continue
        observed[method_id] = raw
    if set(observed) != set(PRIMARY_METHOD_IDS):
        failures.append("complete_primary_executed_matrix")

    shared_effective: dict[str, Any] | None = None
    for method_id in PRIMARY_METHOD_IDS:
        if method_id not in observed:
            continue
        row = observed[method_id]
        pipeline = row.get("pipeline")
        if not isinstance(pipeline, Mapping):
            failures.append(f"{method_id}:pipeline_not_mapping")
            continue
        effective_pipeline = copy.deepcopy(dict(pipeline))
        injected_method_id = effective_pipeline.pop("method_id", None)
        if injected_method_id not in {None, method_id}:
            failures.append(f"{method_id}:pipeline_method_id")
        expected_pipeline = canonical[method_id]["pipeline"]
        if effective_pipeline != expected_pipeline:
            differing = sorted(
                key
                for key in set(effective_pipeline) | set(expected_pipeline)
                if effective_pipeline.get(key) != expected_pipeline.get(key)
            )
            failures.extend(f"{method_id}:pipeline:{field}" for field in differing)

        effective_shared = {
            field: copy.deepcopy(row.get(field))
            for field in ("control", "limits", "data")
        }
        if any(not isinstance(effective_shared[field], Mapping) for field in effective_shared):
            failures.append(f"{method_id}:effective_policy_not_mapping")
        elif shared_effective is None:
            shared_effective = effective_shared
        elif effective_shared != shared_effective:
            failures.append(f"{method_id}:effective_policy_differs")

        control = row.get("control")
        limits = row.get("limits")
        if isinstance(control, Mapping):
            if control.get("dt") != expected_pipeline["control_dt_s"]:
                failures.append(f"{method_id}:control:dt")
            if control.get("minimum_duration") != expected_pipeline["minimum_duration_s"]:
                failures.append(f"{method_id}:control:minimum_duration")
        if isinstance(limits, Mapping):
            expected_limits = expected_pipeline["motion_limits"]
            if any(
                limits.get(field) != expected_limits[field]
                for field in ("max_velocity", "max_acceleration", "max_jerk")
            ):
                failures.append(f"{method_id}:limits")

    observed_projection = {
        method_id: copy.deepcopy(dict(observed[method_id]))
        for method_id in PRIMARY_METHOD_IDS
        if method_id in observed
    }
    canonical_projection = {
        method_id: copy.deepcopy(dict(canonical[method_id]["pipeline"]))
        for method_id in PRIMARY_METHOD_IDS
    }
    return {
        "configuration_identity_passed": not failures,
        "failed_configuration_fields": "|".join(dict.fromkeys(failures)),
        "executed_configuration_sha256": _canonical_sha256(observed_projection),
        "canonical_primary_pipeline_sha256": _canonical_sha256(canonical_projection),
        "effective_shared_policy_sha256": (
            _canonical_sha256(shared_effective) if shared_effective is not None else ""
        ),
        "executed_primary_method_count": len(observed),
    }


def _failed_expectations(
    row: Mapping[str, Any], expectations: Mapping[str, Any]
) -> list[str]:
    return [
        field
        for field, expected in expectations.items()
        if field not in row or row[field] != expected
    ]


def audit_primary_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Audit every primary sample row against the direct-follower purity gate."""

    audit: list[dict[str, Any]] = []
    for row in rows:
        method_id = str(row.get("method_id", ""))
        if method_id not in PRIMARY_METHOD_IDS:
            continue
        failed = _failed_expectations(row, _PRIMARY_PURITY_EXPECTATIONS)
        audit.append(
            {
                **_identity(row),
                "method_pure": not failed,
                "failed_fields": "|".join(failed),
            }
        )
    return audit


def primary_purity_by_trajectory(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize primary method purity at the whole-trajectory unit."""

    row_audit = audit_primary_rows(rows)
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    fields = (
        "dataset_id",
        "session_id",
        "trajectory_id",
        "scenario_id",
        "method_id",
    )
    for audit_row in row_audit:
        grouped[tuple(audit_row.get(field) for field in fields)].append(audit_row)
    result: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: tuple(str(value) for value in item)):
        group = grouped[key]
        pure_count = sum(bool(row["method_pure"]) for row in group)
        result.append(
            {
                **dict(zip(fields, key)),
                "sample_row_count": len(group),
                "pure_sample_row_count": pure_count,
                "method_purity_rate": pure_count / len(group),
                "passed": pure_count == len(group),
            }
        )
    return result


def validate_primary_method_purity(rows: Sequence[Mapping[str, Any]]) -> None:
    audit = audit_primary_rows(rows)
    if not audit:
        raise ValueError("no primary sample rows were supplied")
    failed = [row for row in audit if not row["method_pure"]]
    if failed:
        first = failed[0]
        raise ValueError(
            "primary method-purity gate failed at "
            f"{first['trajectory_id']!r}, k={first['k']!r}, "
            f"method={first['method_id']!r}: {first['failed_fields']}"
        )
    summaries = primary_purity_by_trajectory(rows)
    if any(summary["method_purity_rate"] != 1.0 for summary in summaries):
        raise ValueError("every primary trajectory must have method_purity_rate=1.0")


def audit_ordinary_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expose any hidden algorithm replacement in completed ordinary rows."""

    audit: list[dict[str, Any]] = []
    for row in rows:
        method_id = str(row.get("method_id", ""))
        if method_id not in SECONDARY_METHOD_IDS:
            continue
        failed = _failed_expectations(row, _ORDINARY_ROW_EXPECTATIONS)
        audit.append(
            {
                **_identity(row),
                "native_unshielded": not failed,
                "failed_fields": "|".join(failed),
            }
        )
    return audit


def validate_ordinary_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    failed = [row for row in audit_ordinary_rows(rows) if not row["native_unshielded"]]
    if failed:
        first = failed[0]
        raise ValueError(
            "ordinary-Ruckig row contains a shield or algorithm replacement at "
            f"{first['trajectory_id']!r}, k={first['k']!r}, "
            f"method={first['method_id']!r}: {first['failed_fields']}"
        )


def audit_oracle_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Create explicit offline/noncausal labels tied to actual oracle rows."""

    audit: list[dict[str, Any]] = []
    for row in rows:
        method_id = str(row.get("method_id", ""))
        if method_id not in ORACLE_METHOD_IDS:
            continue
        failed = []
        if row.get("predictor_id") not in {
            "oracle",
            "oracle_future_state",
            "oracle_future_state_offline",
        }:
            failed.append("predictor_id")
        if row.get("method_semantics") != "direct_constant_jerk":
            failed.append("method_semantics")
        if row.get("native_follower") != "direct_executable":
            failed.append("native_follower")
        audit.append(
            {
                **_identity(row),
                "causal": False,
                "offline_only": True,
                "deployable": False,
                "included_in_primary": False,
                "eligible_for_parameter_selection": False,
                "oracle_identity_valid": not failed,
                "failed_fields": "|".join(failed),
            }
        )
    return audit


def validate_oracle_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    audit = audit_oracle_rows(rows)
    if not audit:
        raise ValueError("no oracle sample rows were supplied")
    failed = [row for row in audit if not row["oracle_identity_valid"]]
    if failed:
        first = failed[0]
        raise ValueError(
            "oracle identity gate failed at "
            f"{first['trajectory_id']!r}, k={first['k']!r}, "
            f"method={first['method_id']!r}: {first['failed_fields']}"
        )


def _cycle_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("dataset_id"),
        row.get("session_id"),
        row.get("trajectory_id"),
        row.get("scenario_id"),
        row.get("joint_id"),
        row.get("k"),
    )


def _group_primary_cycles(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[Any, ...], dict[str, Mapping[str, Any]]]:
    grouped: dict[tuple[Any, ...], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        method_id = str(row.get("method_id", ""))
        if method_id not in PRIMARY_METHOD_IDS:
            continue
        key = _cycle_key(row)
        if method_id in grouped[key]:
            raise ValueError(
                f"duplicate primary row for cycle {key!r}, method={method_id!r}"
            )
        grouped[key][method_id] = row
    return grouped


def _first_k_by_trajectory_joint(
    grouped: Mapping[tuple[Any, ...], Mapping[str, Mapping[str, Any]]],
) -> dict[tuple[Any, ...], Any]:
    result: dict[tuple[Any, ...], Any] = {}
    for key in grouped:
        trajectory_joint = key[:5]
        k = key[5]
        if trajectory_joint not in result or k < result[trajectory_joint]:
            result[trajectory_joint] = k
    return result


def _target_component_failures(
    methods: Mapping[str, Mapping[str, Any]], *, tolerance: float
) -> list[str]:
    if set(methods) != set(PRIMARY_METHOD_IDS):
        return ["complete_primary_triplet"]
    p_row = methods[PRIMARY_METHOD_IDS[0]]
    pv_row = methods[PRIMARY_METHOD_IDS[1]]
    pva_row = methods[PRIMARY_METHOD_IDS[2]]
    failed: list[str] = []
    expected_modes = ("p", "pv", "pva")
    for method_id, expected_mode in zip(PRIMARY_METHOD_IDS, expected_modes):
        if methods[method_id].get("target_mode") != expected_mode:
            failed.append(f"{method_id}:target_mode")
    if not (
        _values_equal(
            p_row.get("raw_target_p"),
            pv_row.get("raw_target_p"),
            tolerance=tolerance,
        )
        and _values_equal(
            p_row.get("raw_target_p"),
            pva_row.get("raw_target_p"),
            tolerance=tolerance,
        )
    ):
        failed.append("raw_target_p")
    if not _values_equal(p_row.get("raw_target_v"), 0.0, tolerance=tolerance):
        failed.append("p_target_v_zero")
    if not _values_equal(p_row.get("raw_target_a"), 0.0, tolerance=tolerance):
        failed.append("p_target_a_zero")
    if not _values_equal(
        pv_row.get("raw_target_v"),
        pv_row.get("prediction_v"),
        tolerance=tolerance,
    ):
        failed.append("pv_target_v_from_prediction")
    if not _values_equal(pv_row.get("raw_target_a"), 0.0, tolerance=tolerance):
        failed.append("pv_target_a_zero")
    if not _values_equal(
        pva_row.get("raw_target_v"),
        pva_row.get("prediction_v"),
        tolerance=tolerance,
    ):
        failed.append("pva_target_v_from_prediction")
    if not _values_equal(
        pva_row.get("raw_target_a"),
        pva_row.get("prediction_a"),
        tolerance=tolerance,
    ):
        failed.append("pva_target_a_from_prediction")
    if not _values_equal(
        pv_row.get("raw_target_v"),
        pva_row.get("raw_target_v"),
        tolerance=tolerance,
    ):
        failed.append("pv_pva_target_v")
    return failed


def audit_target_component_zeroing(
    rows: Sequence[Mapping[str, Any]], *, tolerance: float = 1e-12
) -> list[dict[str, Any]]:
    """Audit the exact P/PV/PVA component construction at every input cycle."""

    if tolerance < 0.0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be finite and nonnegative")
    grouped = _group_primary_cycles(rows)
    audit: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: tuple(str(value) for value in item)):
        methods = grouped[key]
        failed = _target_component_failures(methods, tolerance=tolerance)
        audit.append(
            {
                "dataset_id": key[0],
                "session_id": key[1],
                "trajectory_id": key[2],
                "scenario_id": key[3],
                "joint_id": key[4],
                "k": key[5],
                "control_time": next(iter(methods.values())).get("control_time"),
                "target_component_zeroing_passed": not failed,
                "failed_fields": "|".join(failed),
            }
        )
    return audit


def audit_same_information_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    tolerance: float = 1e-12,
    executed_method_matrix: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Audit same upstream information for each primary cycle and joint.

    Endogenous current state is compared only at the initial cycle.  Later
    current states are intentionally allowed to differ because the three
    controllers may have executed different earlier commands.
    """

    if tolerance < 0.0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be finite and nonnegative")
    grouped = _group_primary_cycles(rows)
    first_k = _first_k_by_trajectory_joint(grouped)
    configuration = (
        audit_executed_primary_configuration(executed_method_matrix)
        if executed_method_matrix is not None
        else {
            "configuration_identity_passed": True,
            "failed_configuration_fields": "",
            "executed_configuration_sha256": "",
            "canonical_primary_pipeline_sha256": "",
            "effective_shared_policy_sha256": "",
            "executed_primary_method_count": 0,
        }
    )
    audit: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: tuple(str(value) for value in item)):
        methods = grouped[key]
        failed: list[str] = []
        if set(methods) != set(PRIMARY_METHOD_IDS):
            failed.append("complete_primary_triplet")
        else:
            reference = methods[PRIMARY_METHOD_IDS[0]]
            fields = list(_SAME_INFORMATION_FIELDS)
            if key[5] == first_k[key[:5]]:
                fields.extend(_INITIAL_STATE_FIELDS)
            for field in fields:
                if field not in reference:
                    failed.append(f"missing:{field}")
                    continue
                for method_id in PRIMARY_METHOD_IDS[1:]:
                    candidate = methods[method_id]
                    if field not in candidate:
                        failed.append(f"{method_id}:missing:{field}")
                    elif not _values_equal(
                        reference[field], candidate[field], tolerance=tolerance
                    ):
                        failed.append(f"{method_id}:{field}")
            failed.extend(_target_component_failures(methods, tolerance=tolerance))
        audit.append(
            {
                "dataset_id": key[0],
                "session_id": key[1],
                "trajectory_id": key[2],
                "scenario_id": key[3],
                "joint_id": key[4],
                "k": key[5],
                "control_time": next(iter(methods.values())).get("control_time"),
                **configuration,
                "audit_passed": not failed
                and bool(configuration["configuration_identity_passed"]),
                "failed_fields": "|".join(
                    dict.fromkeys(
                        [
                            *failed,
                            *(
                                [str(configuration["failed_configuration_fields"])]
                                if configuration["failed_configuration_fields"]
                                else []
                            ),
                        ]
                    )
                ),
            }
        )
    return audit


def validate_same_information_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    tolerance: float = 1e-12,
    executed_method_matrix: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    audit = audit_same_information_rows(
        rows,
        tolerance=tolerance,
        executed_method_matrix=executed_method_matrix,
    )
    if not audit:
        raise ValueError("no primary sample rows were supplied")
    failed = [row for row in audit if not row["audit_passed"]]
    if failed:
        first = failed[0]
        raise ValueError(
            "same-information gate failed at "
            f"{first['trajectory_id']!r}, joint={first['joint_id']!r}, "
            f"k={first['k']!r}: {first['failed_fields']}"
        )


def validate_target_component_zeroing(
    rows: Sequence[Mapping[str, Any]], *, tolerance: float = 1e-12
) -> None:
    audit = audit_target_component_zeroing(rows, tolerance=tolerance)
    if not audit:
        raise ValueError("no primary sample rows were supplied")
    failed = [row for row in audit if not row["target_component_zeroing_passed"]]
    if failed:
        first = failed[0]
        raise ValueError(
            "target-component zeroing failed at "
            f"{first['trajectory_id']!r}, joint={first['joint_id']!r}, "
            f"k={first['k']!r}: {first['failed_fields']}"
        )


# Stable V4-facing name requested by the preregistered method-identity gate.
validate_method_matrix_identity = validate_v4_method_matrix


__all__ = [
    "METHOD_MATRIX_SCHEMA_VERSION",
    "ORACLE_METHOD_IDS",
    "PRIMARY_METHOD_IDS",
    "SECONDARY_METHOD_IDS",
    "TARGET_MODE_BY_METHOD",
    "audit_executed_primary_configuration",
    "audit_oracle_rows",
    "audit_ordinary_rows",
    "audit_primary_rows",
    "audit_same_information_rows",
    "audit_target_component_zeroing",
    "build_v4_method_matrix",
    "load_v4_method_matrix",
    "oracle_method_specs",
    "primary_method_specs",
    "primary_purity_by_trajectory",
    "secondary_method_specs",
    "validate_method_matrix_identity",
    "validate_oracle_rows",
    "validate_ordinary_rows",
    "validate_primary_method_purity",
    "validate_same_information_rows",
    "validate_target_component_zeroing",
    "validate_v4_method_matrix",
]
