"""Source-backed contextual and audit tables for the V4 reporting boundary.

The confirmation statistics intentionally exclude oracle outcomes.  This
module produces their separate diagnostic tables, and the ordinary-Ruckig
context tables, from already-written immutable raw bundles.  It has no access
to experiment execution or trajectory generation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

PRIMARY_METHODS = (
    "one_step_governed_p_direct",
    "one_step_governed_pv_direct",
    "one_step_governed_pva_direct",
)
ORDINARY_METHODS = (
    "deployed_p_only_ordinary_ruckig",
    "predicted_p_ordinary_ruckig",
    "raw_predicted_pv_ordinary_ruckig",
    "raw_predicted_pva_ordinary_ruckig",
)
ORACLE_METHODS = (
    "oracle_one_step_p_direct",
    "oracle_one_step_pv_direct",
    "oracle_one_step_pva_direct",
)
ACCELERATION_ACTIVE_FAMILIES = frozenset(
    {
        "piecewise_constant_jerk",
        "stop_and_go",
        "rapid_reversal",
        "boundary_grazing",
    }
)
DERIVED_TABLES = (
    "oracle_target_component_metrics.csv",
    "oracle_pv_vs_p.csv",
    "oracle_pva_vs_pv.csv",
    "oracle_acceleration_active_effect.csv",
    "ordinary_ruckig_metrics.csv",
    "ordinary_ruckig_method_identity.csv",
    "ordinary_ruckig_completion.csv",
    "ordinary_ruckig_profile_audit.csv",
    "method_identity_summary.csv",
    "method_identity_by_trajectory.csv",
    "same_information_audit.csv",
    "constraint_audit.csv",
    "runtime_benchmark.csv",
    "failures.csv",
    "fallback_events.csv",
    "completion_summary.csv",
)


class V4ContextualError(ValueError):
    """Immutable raw evidence cannot produce the required contextual tables."""


def _parse(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        result = float(text)
        return int(result) if result.is_integer() and "e" not in text.lower() else result
    except ValueError:
        return text


def _read_csv(path: Path, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not path.is_file():
        raise V4ContextualError(f"required immutable source is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise V4ContextualError(f"CSV source has no header: {path}")
        rows = [{key: _parse(value or "") for key, value in row.items()} for row in reader]
    if not rows and not allow_empty:
        raise V4ContextualError(f"CSV source has no rows: {path}")
    return rows


def _method(row: Mapping[str, Any]) -> str:
    value = row.get("method", row.get("method_id"))
    if value is None:
        run_id = str(row.get("run_id", ""))
        value = run_id.rsplit("::", 1)[-1] if "::" in run_id else None
    if value is None:
        raise V4ContextualError("source row has no method identity")
    return str(value)


def _number(row: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        if row.get(key) is not None:
            value = float(row[key])
            if not math.isfinite(value):
                raise V4ContextualError(f"{key} is non-finite")
            return value
    raise V4ContextualError(f"required numeric source field is absent: {keys}")


def _bool(row: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
    raise V4ContextualError(f"required boolean source field is absent: {keys}")


def _index_metrics(
    rows: Sequence[Mapping[str, Any]], methods: Sequence[str]
) -> dict[str, dict[str, Mapping[str, Any]]]:
    result: dict[str, dict[str, Mapping[str, Any]]] = {method: {} for method in methods}
    for row in rows:
        method = _method(row)
        if method not in result:
            continue
        trajectory_id = str(row.get("trajectory_id", ""))
        if not trajectory_id or trajectory_id in result[method]:
            raise V4ContextualError(
                f"duplicate or missing whole-trajectory metric for {method}"
            )
        result[method][trajectory_id] = row
    return result


def _pair(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    baseline: str,
    candidate: str,
    trajectory_ids: Sequence[str],
    comparison_id: str,
) -> dict[str, Any]:
    baseline_rows = indexed[baseline]
    candidate_rows = indexed[candidate]
    ids = sorted(trajectory_ids)
    paired = [
        identifier
        for identifier in ids
        if identifier in baseline_rows and identifier in candidate_rows
    ]
    missing_baseline = [identifier for identifier in ids if identifier not in baseline_rows]
    missing_candidate = [identifier for identifier in ids if identifier not in candidate_rows]
    if missing_baseline or missing_candidate:
        return {
            "comparison_id": comparison_id,
            "baseline_method": baseline,
            "candidate_method": candidate,
            "n_trajectories": len(paired),
            "required_trajectories": len(ids),
            "status": "unavailable_incomplete_denominator",
            "effect": None,
            "absolute_improvement": None,
            "relative_improvement": None,
            "harmful_count": None,
            "harmful_rate": None,
            "missing_baseline_ids_json": json.dumps(missing_baseline),
            "missing_candidate_ids_json": json.dumps(missing_candidate),
            "information_condition": "offline_analytic_truth",
            "causal": False,
            "deployable": False,
            "diagnostic_only": True,
        }
    baseline_values = np.asarray(
        [_number(baseline_rows[identifier], "position_rmse") for identifier in paired]
    )
    candidate_values = np.asarray(
        [_number(candidate_rows[identifier], "position_rmse") for identifier in paired]
    )
    absolute = float(np.mean(baseline_values) - np.mean(candidate_values))
    relative = float(absolute / np.mean(baseline_values))
    return {
        "comparison_id": comparison_id,
        "baseline_method": baseline,
        "candidate_method": candidate,
        "n_trajectories": len(ids),
        "required_trajectories": len(ids),
        "status": "available",
        "effect": relative,
        "absolute_improvement": absolute,
        "relative_improvement": relative,
        "harmful_count": int(np.count_nonzero(candidate_values > baseline_values)),
        "harmful_rate": float(np.mean(candidate_values > baseline_values)),
        "information_condition": "offline_analytic_truth",
        "causal": False,
        "deployable": False,
        "diagnostic_only": True,
    }


def _manifest_metadata(oracle_root: Path) -> dict[str, dict[str, str]]:
    path = oracle_root / "split_manifest.json"
    if not path.is_file():
        path = oracle_root / "data_manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V4ContextualError(f"cannot read oracle split metadata: {exc}") from exc
    rows = value.get("trajectories", value.get("population"))
    if not isinstance(rows, list):
        raise V4ContextualError("oracle split metadata lacks trajectory population")
    result = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("split") != "test":
            continue
        identifier = str(row.get("trajectory_id", ""))
        family = row.get("family", row.get("reference_family"))
        demand = row.get("demand_stratum")
        if not identifier or family is None or demand is None:
            raise V4ContextualError("oracle test metadata is incomplete")
        result[identifier] = {"family": str(family), "demand_stratum": str(demand)}
    if len(result) != 120:
        raise V4ContextualError("oracle split metadata must contain 120 test trajectories")
    return result


def _ordinary_identity(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        method = _method(row)
        if method in ORDINARY_METHODS:
            grouped[method].append(row)
    output = []
    for method in ORDINARY_METHODS:
        method_rows = grouped.get(method, [])
        if not method_rows:
            output.append(
                {
                    "method": method,
                    "role": "contextual_secondary",
                    "status": "unavailable_no_completed_native_rows",
                    "audit_row_count": 0,
                    "native_execution_count": 0,
                    "native_execution_rate": None,
                    "native_unshielded": None,
                    "hidden_shield_or_replacement_count": None,
                }
            )
            continue
        native = [
            _bool(row, "native_unshielded")
            if row.get("native_unshielded") is not None
            else (
                _number(row, "native_execution_rate") == 1.0
                and _number(row, "shield_application_rate") == 0.0
                and _number(row, "fallback_changes_algorithm_rate") == 0.0
            )
            for row in method_rows
        ]
        output.append(
            {
                "method": method,
                "role": "contextual_secondary",
                "status": "available",
                "audit_row_count": len(method_rows),
                "native_execution_count": sum(native),
                "native_execution_rate": float(np.mean(native)),
                "native_unshielded": all(native),
                "hidden_shield_or_replacement_count": len(native) - sum(native),
            }
        )
    return output


def _ordinary_completion(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_method = {_method(row): row for row in rows if _method(row) in ORDINARY_METHODS}
    output = []
    for method in ORDINARY_METHODS:
        row = by_method.get(method)
        if row is None:
            raise V4ContextualError(f"ordinary completion row is absent for {method}")
        attempted = int(
            _number(row, "attempted_trajectories", "attempted_trajectory_runs")
        )
        completed = int(
            _number(row, "completed_trajectories", "successful_trajectory_runs")
        )
        failed = int(
            row.get("failed_trajectories", row.get("failed_trajectory_runs", attempted - completed))
        )
        if attempted != 120 or completed + failed != attempted:
            raise V4ContextualError(f"ordinary completion denominator is invalid for {method}")
        output.append(
            {
                "method": method,
                "role": "contextual_secondary",
                "attempted_trajectories": attempted,
                "completed_trajectories": completed,
                "failed_trajectories": failed,
                "completion_rate": completed / attempted,
                "complete_paired_inference_permitted": completed == attempted,
            }
        )
    return output


def _ordinary_profile(
    rows: Sequence[Mapping[str, Any]],
    metrics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    metric_ids = {
        (_method(row), str(row["trajectory_id"]))
        for row in metrics
        if _method(row) in ORDINARY_METHODS
    }
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        method = _method(row)
        if method in ORDINARY_METHODS:
            grouped[(method, str(row.get("trajectory_id", "")))].append(row)
    output = []
    for method, trajectory_id in sorted(metric_ids | set(grouped)):
        audit_rows = grouped.get((method, trajectory_id), [])
        if not audit_rows:
            output.append(
                {
                    "trajectory_id": trajectory_id,
                    "method": method,
                    "role": "contextual_secondary",
                    "status": "unavailable_missing_profile_audit",
                    "audit_cycle_joint_count": 0,
                    "audit_methods": "",
                    "profile_aware": False,
                    "acceleration_difference_used_as_internal_jerk": None,
                    "violation_count": None,
                }
            )
            continue
        audit_methods = {str(row.get("audit_method", "")) for row in audit_rows}
        profile_aware = bool(audit_methods) and all(
            name
            in {
                "analytic_profile_extrema",
                "analytic_ruckig_piecewise_constant_jerk",
            }
            for name in audit_methods
        )
        output.append(
            {
                "trajectory_id": trajectory_id,
                "method": method,
                "role": "contextual_secondary",
                "status": "available" if profile_aware else "invalid_profile_audit",
                "audit_cycle_joint_count": len(audit_rows),
                "audit_methods": "|".join(sorted(audit_methods)),
                "profile_aware": profile_aware,
                "acceleration_difference_used_as_internal_jerk": False,
                "violation_count": sum(
                    int(_number(row, "violation_count")) for row in audit_rows
                ),
            }
        )
    for method in ORDINARY_METHODS:
        if not any(row["method"] == method for row in output):
            output.append(
                {
                    "trajectory_id": "__unavailable__",
                    "method": method,
                    "role": "contextual_secondary",
                    "status": "unavailable_no_completed_trajectories",
                    "audit_cycle_joint_count": 0,
                    "audit_methods": "",
                    "profile_aware": None,
                    "acceleration_difference_used_as_internal_jerk": None,
                    "violation_count": None,
                }
            )
    return output


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], *, fields: Sequence[str] | None = None) -> None:
    if not rows and fields is None:
        raise V4ContextualError(f"cannot infer header for empty table {path.name}")
    columns = list(fields or rows[0])
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_v4_contextual_tables(
    *,
    results_root: str | Path,
    locked_test_root: str | Path,
    oracle_root: str | Path,
    report_only: bool = False,
) -> Mapping[str, Any]:
    """Atomically derive contextual/audit reporting tables from raw bundles.

    Fresh confirmation refuses pre-existing derived paths.  A constrained
    report-only resume may atomically replace only the named derived tables;
    raw bundles and the eleven confirmatory statistics are never modified.
    """

    results = Path(results_root).resolve()
    locked = Path(locked_test_root).resolve()
    oracle = Path(oracle_root).resolve()
    for bundle in (locked, oracle):
        try:
            bundle.relative_to(results)
        except ValueError as exc:
            raise V4ContextualError("raw bundles must be inside results_root") from exc
    statistics = results / "statistics"
    if not statistics.is_dir():
        raise V4ContextualError("confirmatory statistics directory is missing")
    targets = [statistics / name for name in DERIVED_TABLES]
    if not report_only and any(path.exists() for path in targets):
        raise V4ContextualError("refusing to overwrite contextual/audit statistics")

    locked_metrics = _read_csv(locked / "metrics_by_trajectory.csv")
    oracle_metrics = _read_csv(oracle / "metrics_by_trajectory.csv")
    oracle_identity_rows = _read_csv(oracle / "oracle_method_identity.csv")
    oracle_identity_valid = {}
    for method in ORACLE_METHODS:
        rows = [
            row for row in oracle_identity_rows if _method(row) == method
        ]
        oracle_identity_valid[method] = bool(rows) and all(
            _bool(row, "oracle_identity_valid") for row in rows
        )
    metadata = _manifest_metadata(oracle)
    oracle_index = _index_metrics(oracle_metrics, ORACLE_METHODS)
    expected_ids = sorted(metadata)
    labelled_oracle = []
    for row in oracle_metrics:
        method = _method(row)
        if method not in ORACLE_METHODS:
            continue
        identifier = str(row["trajectory_id"])
        labelled_oracle.append(
            {
                **dict(row),
                "method": method,
                **metadata[identifier],
                "information_condition": "offline_analytic_truth",
                "causal": False,
                "deployable": False,
                "diagnostic_only": True,
                "excluded_from_primary": True,
                "oracle_identity_valid": oracle_identity_valid[method],
                "status": (
                    "available"
                    if oracle_identity_valid[method]
                    else "invalid_oracle_identity"
                ),
            }
        )
    for method in ORACLE_METHODS:
        if not any(row["method"] == method for row in labelled_oracle):
            labelled_oracle.append(
                {
                    "trajectory_id": "__unavailable__",
                    "method": method,
                    "family": None,
                    "demand_stratum": None,
                    "position_rmse": None,
                    "status": "unavailable_no_completed_trajectories",
                    "information_condition": "offline_analytic_truth",
                    "causal": False,
                    "deployable": False,
                    "diagnostic_only": True,
                    "excluded_from_primary": True,
                    "oracle_identity_valid": None,
                }
            )
    oracle_pv = _pair(
        oracle_index,
        ORACLE_METHODS[0],
        ORACLE_METHODS[1],
        expected_ids,
        "oracle_PV_vs_P_position_RMSE",
    )
    oracle_pva = _pair(
        oracle_index,
        ORACLE_METHODS[1],
        ORACLE_METHODS[2],
        expected_ids,
        "oracle_PVA_vs_PV_position_RMSE",
    )
    active_ids = [
        identifier
        for identifier, fields in metadata.items()
        if fields["family"] in ACCELERATION_ACTIVE_FAMILIES
        and fields["demand_stratum"] in {"high", "near_limit"}
    ]
    if len(active_ids) != 40:
        raise V4ContextualError("acceleration-active oracle denominator must equal 40")
    oracle_active = _pair(
        oracle_index,
        ORACLE_METHODS[1],
        ORACLE_METHODS[2],
        active_ids,
        "oracle_PVA_vs_PV_acceleration_active_position_RMSE",
    )

    ordinary_metrics_actual = [
        {**dict(row), "method": _method(row), "role": "contextual_secondary"}
        for row in locked_metrics
        if _method(row) in ORDINARY_METHODS
    ]
    ordinary_index = _index_metrics(ordinary_metrics_actual, ORDINARY_METHODS)
    ordinary_metrics = list(ordinary_metrics_actual)
    for method in ORDINARY_METHODS:
        if not ordinary_index[method]:
            ordinary_metrics.append(
                {
                    "trajectory_id": "__unavailable__",
                    "method": method,
                    "position_rmse": None,
                    "role": "contextual_secondary",
                    "status": "unavailable_no_completed_trajectories",
                }
            )
    completion_source = _read_csv(locked / "completion_summary.csv")
    ordinary_completion = _ordinary_completion(completion_source)
    for row in ordinary_completion:
        method = str(row["method"])
        numeric_count = len(ordinary_index[method])
        completed = int(row["completed_trajectories"])
        row["numeric_metric_trajectory_count"] = numeric_count
        row["numeric_denominator_matches_completion"] = numeric_count == completed
        if numeric_count != completed:
            row["status"] = "invalid_numeric_completion_denominator"
            row["complete_paired_inference_permitted"] = False
        else:
            row["status"] = (
                "available"
                if completed == int(row["attempted_trajectories"])
                else "unavailable_incomplete_denominator"
            )
    identity_source_path = locked / "ordinary_ruckig_method_identity.csv"
    identity_source = _read_csv(identity_source_path)
    ordinary_identity = _ordinary_identity(identity_source)
    constraint_source = _read_csv(locked / "constraint_audit.csv")
    ordinary_profile = _ordinary_profile(
        constraint_source, ordinary_metrics_actual
    )

    runtime_source = locked / "runtime_repeated_summary.csv"
    runtime_rows = _read_csv(runtime_source)
    runtime_failures = _read_csv(
        locked / "runtime_repeated_failures.csv", allow_empty=True
    )
    repetitions: dict[str, set[int]] = defaultdict(set)
    for row in runtime_rows:
        method = _method(row)
        if method not in PRIMARY_METHODS:
            continue
        repetitions[method].add(int(_number(row, "repetition")))
        if not _bool(row, "timing_population_complete"):
            row["formal_runtime_valid"] = False
        else:
            row["formal_runtime_valid"] = True
        row["runtime_deadline_miss_rate"] = _number(row, "deadline_miss_rate")
    for row in runtime_rows:
        method = _method(row)
        row["formal_repetitions_complete"] = (
            method in PRIMARY_METHODS
            and repetitions[method] == set(range(5))
        )
        row["runtime_failure_count"] = sum(
            _method(failure) == method for failure in runtime_failures
        )

    passthrough: dict[str, tuple[list[dict[str, Any]], Sequence[str] | None]] = {
        "method_identity_summary.csv": (
            _read_csv(locked / "method_identity_summary.csv"),
            None,
        ),
        "method_identity_by_trajectory.csv": (
            _read_csv(locked / "method_identity_by_trajectory.csv"),
            None,
        ),
        "same_information_audit.csv": (
            _read_csv(locked / "same_information_audit.csv"),
            None,
        ),
        "constraint_audit.csv": (constraint_source, None),
        "runtime_benchmark.csv": (runtime_rows, None),
        "failures.csv": (
            _read_csv(locked / "failures.csv", allow_empty=True),
            ("run_id", "trajectory_id", "failure_type", "reason"),
        ),
        "fallback_events.csv": (
            _read_csv(locked / "fallback_events.csv", allow_empty=True),
            ("run_id", "trajectory_id", "k", "fallback_reason"),
        ),
        "completion_summary.csv": (completion_source, None),
    }
    produced: dict[str, tuple[Sequence[Mapping[str, Any]], Sequence[str] | None]] = {
        "oracle_target_component_metrics.csv": (labelled_oracle, None),
        "oracle_pv_vs_p.csv": ([oracle_pv], None),
        "oracle_pva_vs_pv.csv": ([oracle_pva], None),
        "oracle_acceleration_active_effect.csv": ([oracle_active], None),
        "ordinary_ruckig_metrics.csv": (ordinary_metrics, None),
        "ordinary_ruckig_method_identity.csv": (ordinary_identity, None),
        "ordinary_ruckig_completion.csv": (ordinary_completion, None),
        "ordinary_ruckig_profile_audit.csv": (ordinary_profile, None),
        **passthrough,
    }
    staging = Path(tempfile.mkdtemp(prefix=".v4-contextual-", dir=statistics))
    promoted: list[Path] = []
    try:
        for name in DERIVED_TABLES:
            rows, fields = produced[name]
            _write_csv(staging / name, rows, fields=fields)
        for name in DERIVED_TABLES:
            target = statistics / name
            staged = staging / name
            if target.exists():
                if not report_only:
                    raise V4ContextualError(f"refusing to overwrite {target}")
                if _sha256(target) != _sha256(staged):
                    raise V4ContextualError(
                        f"report-only derived evidence differs from existing {name}"
                    )
                staged.unlink()
            else:
                os.replace(staged, target)
                promoted.append(target)
    except BaseException:
        for path in promoted:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in staging.glob("*"):
            path.unlink()
        staging.rmdir()
    sources = (
        results / "statistics" / "metrics_by_trajectory.csv",
        results / "statistics" / "primary_comparison.csv",
        results / "statistics" / "secondary_comparisons.csv",
        results / "statistics" / "family_effects.csv",
        results / "statistics" / "demand_stratum_effects.csv",
        results / "statistics" / "acceleration_active_effect.csv",
        results / "statistics" / "harmful_trajectory_rate.csv",
        results / "statistics" / "worst_five_trajectories.csv",
        locked / "metrics_by_trajectory.csv",
        locked / "samples.parquet",
        locked / "ordinary_ruckig_method_identity.csv",
        locked / "completion_summary.csv",
        locked / "constraint_audit.csv",
        locked / "runtime_repeated_summary.csv",
        locked / "runtime_repeated_samples.csv",
        locked / "runtime_repeated_failures.csv",
        locked / "method_identity_summary.csv",
        locked / "method_identity_by_trajectory.csv",
        locked / "same_information_audit.csv",
        locked / "failures.csv",
        locked / "fallback_events.csv",
        oracle / "metrics_by_trajectory.csv",
        oracle / "oracle_method_identity.csv",
        oracle / "split_manifest.json",
    )
    return {
        "schema_version": "otg.v4-contextual-tables.v1",
        "report_only": report_only,
        "source_hashes": {
            path.relative_to(results).as_posix(): _sha256(path) for path in sources
        },
        "table_hashes": {
            f"statistics/{name}": _sha256(statistics / name)
            for name in DERIVED_TABLES
        },
        "ordinary_role": "contextual_secondary",
        "oracle_information_condition": "offline_analytic_truth",
        "oracle_causal": False,
        "oracle_deployable": False,
    }


__all__ = [
    "DERIVED_TABLES",
    "V4ContextualError",
    "generate_v4_contextual_tables",
]
