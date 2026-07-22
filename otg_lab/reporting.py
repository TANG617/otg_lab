"""Assemble bounded, independently verified paper-evidence artifacts.

The raw run bundles remain the canonical, uncommitted experiment outputs.  This
module verifies every supplied bundle (manifest, schemas, SHA-256 registries,
and an independent metric recomputation), then derives a bounded technical
result tree containing statistical tables, deterministic figures, provenance
manifests, and representative trace rows.

Inference is deliberately fail closed.  The exact locked-test trajectory IDs
come from ``split_manifest.json``; a missing trajectory for either method makes
the predeclared paired family unavailable instead of triggering silent
complete-case deletion.  Ten-thousand-resample trajectory bootstraps are the
fixed default for both paired comparisons and method confidence intervals.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

import numpy as np

from .artifacts import (
    ArtifactValidationError,
    assert_records_close,
    read_csv,
    read_json,
    sha256_file,
    validate_artifact_bundle,
    validate_artifact_schema,
    write_csv,
    write_json,
)
from .diagnostics import (
    DiagnosticValidationError,
    governor_invariant_summaries,
)
from .figures import (
    REQUIRED_FIGURE_CATEGORIES,
    FigureValidationError,
    generate_required_figures,
    select_representative_trajectories,
)
from .schema import read_parquet
from .statistics import (
    StatisticalValidationError,
    bootstrap_confidence_intervals,
    holm_adjust,
    paired_comparison_from_records,
    stratified_paired_trajectory_bootstrap,
)

REPORT_SCHEMA_VERSION = "otg.paper-evidence-report.v2"
ROOT_INDEX_SCHEMA_VERSION = "otg.paper-evidence-root-index.v1"
STATISTICAL_DESIGN_SCHEMA_VERSION = "otg.statistical-design.v1"
RAW_VALIDATION_SCHEMA_VERSION = "otg.raw-bundle-validation.v1"
CHART_MAP_SCHEMA_VERSION = "otg.chart-map.v1"
ACCEPTANCE_SCHEMA_VERSION = "otg.section16-acceptance.v1"

CORE_DIAGNOSTIC_PUBLICATIONS = {
    "locked_test/frequency_response.csv": "summaries/frequency_response.csv",
    "locked_test/chirp_frequency_response.csv": (
        "summaries/chirp_frequency_response.csv"
    ),
    "locked_test/local_event_delay.csv": "summaries/local_event_delay.csv",
}

_CORE_DIAGNOSTIC_REQUIRED_FIELDS = {
    "locked_test/frequency_response.csv": {
        "trajectory_id",
        "scenario_id",
        "method_id",
        "joint_id",
        "frequency_hz",
        "gain",
        "phase_rad",
        "phase_delay_s",
        "group_delay_s",
    },
    "locked_test/chirp_frequency_response.csv": {
        "trajectory_id",
        "scenario_id",
        "method_id",
        "joint_id",
        "frequency_band_index",
        "frequency_band_count",
        "gain",
        "phase_rad",
        "phase_delay_s",
        "group_delay_s",
        "local_delay_s",
        "window_truth_sample_denominator",
        "evaluated_sample_count",
        "local_delay_overlap_count",
        "local_delay_overlap_denominator",
    },
    "locked_test/local_event_delay.csv": {
        "trajectory_id",
        "scenario_id",
        "method_id",
        "joint_id",
        "event_id",
        "event_type",
        "lag_s",
        "lag_aligned_rmse",
    },
}

PRIMARY_LIMITS = {
    "max_velocity": 4.1,
    "max_acceleration": 8.2,
    "max_jerk": 4000.0,
}

DEFAULT_RAW_BUNDLES = (
    "validation",
    "locked_test",
    "acceleration",
    "governor_infeasible",
    "robustness",
    "rate_study",
    "multidof",
    "plant",
    "real_replay",
    "phase_a",
)

DEFAULT_COMPARISONS: tuple[dict[str, Any], ...] = (
    {
        "comparison_id": "primary_position:one_step_pva-vs-predicted_p",
        "metric": "position_rmse",
        "baseline_method": "predicted_p",
        "candidate_method": "one_step_governed_pva_direct",
        "lower_is_better": True,
        "secondary": False,
    },
    {
        "comparison_id": "max_error:one_step_pva-vs-predicted_p",
        "metric": "position_max_abs_error",
        "baseline_method": "predicted_p",
        "candidate_method": "one_step_governed_pva_direct",
        "lower_is_better": True,
        "secondary": True,
    },
    {
        "comparison_id": "lag:one_step_pva-vs-predicted_p",
        "metric": "lag_s",
        "baseline_method": "predicted_p",
        "candidate_method": "one_step_governed_pva_direct",
        "lower_is_better": True,
        "secondary": True,
    },
    {
        "comparison_id": "position:one_step_pva-vs-deployed_p_only",
        "metric": "position_rmse",
        "baseline_method": "deployed_p_only",
        "candidate_method": "one_step_governed_pva_direct",
        "lower_is_better": True,
        "secondary": True,
    },
    {
        "comparison_id": "position:one_step_pva-vs-raw_predicted_pv",
        "metric": "position_rmse",
        "baseline_method": "raw_predicted_pv",
        "candidate_method": "one_step_governed_pva_direct",
        "lower_is_better": True,
        "secondary": True,
    },
    {
        "comparison_id": "position:one_step_direct-vs-one_step_ruckig",
        "metric": "position_rmse",
        "baseline_method": "one_step_governed_pva_ruckig",
        "candidate_method": "one_step_governed_pva_direct",
        "lower_is_better": True,
        "secondary": True,
    },
    {
        "comparison_id": "position:jerk_qp_direct-vs-one_step_direct",
        "metric": "position_rmse",
        "baseline_method": "one_step_governed_pva_direct",
        "candidate_method": "jerk_qp_pva_direct",
        "lower_is_better": True,
        "secondary": True,
    },
    {
        "comparison_id": "position:jerk_qp_direct-vs-jerk_qp_ruckig",
        "metric": "position_rmse",
        "baseline_method": "jerk_qp_pva_ruckig",
        "candidate_method": "jerk_qp_pva_direct",
        "lower_is_better": True,
        "secondary": True,
    },
)

DEFAULT_CI_METRICS = (
    "position_rmse",
    "position_mae",
    "position_p95_abs_error",
    "total_p99_us",
)

DEFAULT_STRATIFICATION_FIELDS = (
    "reference_family",
    "demand_stratum",
    "sample_rate_hz",
)

PRIMARY_METHOD_IDS = (
    "deployed_p_only",
    "predicted_p",
    "raw_predicted_pv",
    "scalar_projected_pva",
    "one_step_governed_pva_direct",
    "one_step_governed_pva_ruckig",
    "jerk_qp_pva_direct",
    "jerk_qp_pva_ruckig",
)
_PRIMARY_METHOD_SET = frozenset(PRIMARY_METHOD_IDS)

# These are exact producer contracts, rather than a loose list of columns that
# plotting happens to inspect today.  Keeping the chart inputs narrow prevents
# accidental publication of raw sample tables.
FIGURE_TABLE_SCHEMAS: dict[str, tuple[str, ...]] = {
    "estimator": (
        "method",
        "estimator_p_rmse",
        "posterior_lag_s",
        "estimator_p99_us",
    ),
    "prediction": ("method", "prediction_horizon_ms", "prediction_p_rmse"),
    "ablation": ("method", "position_rmse"),
    "acceleration_phase": ("r_j", "r_a", "pva_vs_pv_rmse_improvement"),
    "governor": (
        "method",
        "governor_position_distortion_rmse",
        "one_step_reachable_rate",
    ),
    "follower": ("trajectory_id", "follower", "position_rmse"),
    "robustness": ("scenario_id", "method", "position_rmse"),
    "sampling_rate": ("sampling_rate_hz", "method", "position_rmse"),
    "constraints": ("method", "jerk_semantic", "max_abs_jerk"),
    "scalability": ("dof", "method", "total_p99_us"),
    "plant": ("plant", "method", "position_rmse"),
    "runtime_samples": ("method", "total_compute_us"),
    "paired": (
        "comparison_id",
        "relative_improvement",
        "relative_improvement_ci_low",
        "relative_improvement_ci_high",
    ),
    "trajectory_metrics": ("trajectory_id", "method", "position_rmse"),
    "trace_samples": (
        "trajectory_id",
        "joint_id",
        "method_id",
        "control_time",
        "command_time",
        "p_ref",
        "command_p",
    ),
}

_NUMERIC_FIGURE_FIELDS = frozenset(
    {
        "estimator_p_rmse",
        "posterior_lag_s",
        "estimator_p99_us",
        "prediction_horizon_ms",
        "prediction_p_rmse",
        "position_rmse",
        "r_j",
        "r_a",
        "pva_vs_pv_rmse_improvement",
        "governor_position_distortion_rmse",
        "one_step_reachable_rate",
        "sampling_rate_hz",
        "max_abs_jerk",
        "dof",
        "total_p99_us",
        "total_compute_us",
        "relative_improvement",
        "relative_improvement_ci_low",
        "relative_improvement_ci_high",
        "control_time",
        "command_time",
        "p_ref",
        "command_p",
    }
)

_MANAGED_OUTPUTS = (
    "summaries",
    "statistics",
    "figures",
    "manifests",
    "README.md",
    "FAILURE_ANALYSIS.md",
    "protocol_hash.txt",
    "artifact_index.json",
    "artifact_index.sha256",
)


class ReportingValidationError(ValueError):
    """Raised when a final report would weaken the evidence contract."""


@dataclass(frozen=True)
class ValidatedBundle:
    """A raw bundle whose hashes, schemas, and recomputation all passed."""

    name: str
    root: Path
    validation: Mapping[str, Any]
    run_manifest: Mapping[str, Any]
    artifact_index: Mapping[str, Any]
    data_manifest: Mapping[str, Any]
    split_manifest: Mapping[str, Any]


@dataclass(frozen=True)
class StatisticalTables:
    """Published inference plus explicit denominator/completeness status."""

    paired_comparisons: list[dict[str, Any]]
    stratified_comparisons: list[dict[str, Any]]
    stratum_effects: list[dict[str, Any]]
    heterogeneity: list[dict[str, Any]]
    trajectory_outcome_summary: list[dict[str, Any]]
    worst_trajectories: list[dict[str, Any]]
    confidence_intervals: list[dict[str, Any]]
    completeness: list[dict[str, Any]]
    inference_status: list[dict[str, Any]]
    expected_trajectory_ids: tuple[str, ...]


@dataclass(frozen=True)
class FailureAnalysis:
    """Bounded failure/fallback counts and technical Markdown."""

    summary: list[dict[str, Any]]
    failure_types: list[dict[str, Any]]
    markdown: str


@dataclass(frozen=True)
class AcceptanceAnalysis:
    """Fail-closed Section 16 decisions and their bounded supporting tables."""

    criteria: list[dict[str, Any]]
    fallback_summary: list[dict[str, Any]]
    evidence_ledger: list[dict[str, Any]]
    core_diagnostics: dict[str, list[dict[str, Any]]] = dataclass_field(
        default_factory=dict
    )


def _safe_relative_path(relative_path: str | Path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ReportingValidationError(f"unsafe artifact path {relative_path!r}")
    return relative


def _bundle_file(bundle: ValidatedBundle, relative_path: str | Path) -> Path:
    relative = _safe_relative_path(relative_path)
    target = (bundle.root / relative).resolve()
    try:
        target.relative_to(bundle.root.resolve())
    except ValueError as error:
        raise ReportingValidationError(
            f"artifact path escapes bundle {bundle.name}: {relative}"
        ) from error
    if not target.is_file():
        raise ReportingValidationError(
            f"bundle {bundle.name!r} lacks artifact {relative.as_posix()!r}"
        )
    return target


def _decode_csv_scalar(value: str) -> Any:
    stripped = value.strip()
    if stripped == "":
        return None
    if stripped == "True":
        return True
    if stripped == "False":
        return False
    if re.fullmatch(r"[-+]?\d+", stripped):
        try:
            return int(stripped)
        except ValueError:
            pass
    try:
        number = float(stripped)
    except ValueError:
        return value
    if not math.isfinite(number):
        raise ReportingValidationError("CSV contains NaN or infinity")
    return number


def load_bundle_csv(
    bundle: ValidatedBundle,
    relative_path: str | Path,
    *,
    required_fields: Iterable[str] = (),
    allow_empty: bool = False,
) -> list[dict[str, Any]]:
    """Load a validated CSV artifact with deterministic scalar decoding."""

    target = _bundle_file(bundle, relative_path)
    validate_artifact_schema(target)
    with target.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ReportingValidationError(f"CSV has no header: {target}")
        fields = tuple(reader.fieldnames)
    raw_rows = read_csv(
        target,
        required_fields=required_fields,
        allowed_missing_fields=fields,
        allow_empty=allow_empty,
    )
    return [
        {field: _decode_csv_scalar(value) for field, value in row.items()}
        for row in raw_rows
    ]


def load_bundle_parquet(
    bundle: ValidatedBundle, relative_path: str | Path = "samples.parquet"
) -> list[dict[str, Any]]:
    """Load a canonical Parquet artifact after bundle-level verification."""

    target = _bundle_file(bundle, relative_path)
    if target.suffix.lower() != ".parquet":
        raise ReportingValidationError(f"not a Parquet artifact: {target.name}")
    return read_parquet(target, validate=True)


def validate_raw_bundles(
    raw_root: str | Path,
    *,
    required_bundles: Sequence[str] = DEFAULT_RAW_BUNDLES,
    expected_commit: str | None = None,
    require_clean: bool = True,
    require_single_commit: bool = True,
) -> dict[str, ValidatedBundle]:
    """Independently verify every requested raw bundle.

    No bundle is trusted merely because another run from the same command
    passed.  Each invocation checks that bundle's own manifest, artifact index,
    SHA-256 registry, schemas, and independent summary recomputation.
    """

    root = Path(raw_root).resolve()
    if not required_bundles:
        raise ReportingValidationError("required_bundles cannot be empty")
    if len(set(required_bundles)) != len(required_bundles):
        raise ReportingValidationError("required_bundles contains duplicates")
    output: dict[str, ValidatedBundle] = {}
    commits: set[str] = set()
    for name in required_bundles:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", str(name)):
            raise ReportingValidationError(f"unsafe bundle name {name!r}")
        bundle_root = (root / str(name)).resolve()
        if bundle_root.parent != root or not bundle_root.is_dir():
            raise ReportingValidationError(f"raw bundle is missing: {name}")
        try:
            validation = validate_artifact_bundle(
                bundle_root,
                require_standard_artifacts=True,
                require_clean=require_clean,
                expected_commit=expected_commit,
                verify_recomputation=True,
                require_complete_feasibility=name == "locked_test",
                recompute_arguments={
                    "max_lag_s": 1.0,
                    "motion_limits": PRIMARY_LIMITS,
                },
            )
        except (ArtifactValidationError, OSError, ValueError) as error:
            raise ReportingValidationError(
                f"raw bundle {name!r} failed independent validation: {error}"
            ) from error
        run_manifest = read_json(bundle_root / "run.json")
        artifact_index = read_json(bundle_root / "artifact_index.json")
        data_manifest = read_json(bundle_root / "data_manifest.json")
        split_manifest = read_json(bundle_root / "split_manifest.json")
        commit = str(validation["git_commit"])
        commits.add(commit)
        output[str(name)] = ValidatedBundle(
            name=str(name),
            root=bundle_root,
            validation=dict(validation),
            run_manifest=run_manifest,
            artifact_index=artifact_index,
            data_manifest=data_manifest,
            split_manifest=split_manifest,
        )
    if require_single_commit and len(commits) != 1:
        raise ReportingValidationError(
            f"raw bundles were produced from different commits: {sorted(commits)}"
        )
    return output


def expected_trajectory_ids(
    split_manifest: Mapping[str, Any],
    *,
    split: str = "test",
    expected_count: int | None = None,
) -> tuple[str, ...]:
    """Extract the exact predeclared whole-trajectory denominator."""

    trajectories = split_manifest.get("trajectories")
    identifiers: list[str]
    if isinstance(trajectories, list):
        identifiers = []
        for index, entry in enumerate(trajectories):
            if not isinstance(entry, Mapping):
                raise ReportingValidationError(
                    f"split manifest trajectory {index} is not an object"
                )
            if str(entry.get("split")) != split:
                continue
            trajectory_id = entry.get("trajectory_id")
            if not isinstance(trajectory_id, str) or not trajectory_id:
                raise ReportingValidationError(
                    f"split manifest trajectory {index} lacks trajectory_id"
                )
            identifiers.append(trajectory_id)
    else:
        compact = split_manifest.get(split)
        if not isinstance(compact, list):
            raise ReportingValidationError(
                f"split manifest has no trajectory list for split {split!r}"
            )
        identifiers = []
        for index, entry in enumerate(compact):
            if isinstance(entry, str):
                trajectory_id = entry
            elif isinstance(entry, Mapping):
                trajectory_id = entry.get("trajectory_id")
            else:
                trajectory_id = None
            if not isinstance(trajectory_id, str) or not trajectory_id:
                raise ReportingValidationError(
                    f"split manifest {split}[{index}] lacks trajectory_id"
                )
            identifiers.append(trajectory_id)
    if not identifiers:
        raise ReportingValidationError(f"split {split!r} has no trajectories")
    duplicates = sorted(
        identifier for identifier, count in Counter(identifiers).items() if count > 1
    )
    if duplicates:
        raise ReportingValidationError(
            f"split {split!r} contains duplicate trajectory IDs: {duplicates[:5]}"
        )
    if expected_count is not None and len(identifiers) != expected_count:
        raise ReportingValidationError(
            f"split {split!r} must contain {expected_count} trajectories, "
            f"observed {len(identifiers)}"
        )
    return tuple(sorted(identifiers))


def _finite_metric(row: Mapping[str, Any], metric: str) -> bool:
    if metric not in row or row[metric] is None:
        return False
    try:
        value = float(row[metric])
    except (TypeError, ValueError):
        return False
    return math.isfinite(value)


def _completeness_row(
    records: Sequence[Mapping[str, Any]],
    *,
    analysis_kind: str,
    analysis_id: str,
    metric: str,
    method: str,
    role: str,
    expected_ids: Sequence[str],
) -> dict[str, Any]:
    matching = [
        row
        for row in records
        if str(row.get("method")) == method and _finite_metric(row, metric)
    ]
    observed = [str(row.get("trajectory_id")) for row in matching]
    counts = Counter(observed)
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    observed_set = set(observed)
    expected_set = set(expected_ids)
    missing = sorted(expected_set - observed_set)
    unexpected = sorted(observed_set - expected_set)
    status = (
        "complete"
        if not missing and not unexpected and not duplicates
        else "incomplete"
    )
    return {
        "analysis_kind": analysis_kind,
        "analysis_id": analysis_id,
        "metric": metric,
        "method": method,
        "role": role,
        "expected_trajectory_count": len(expected_ids),
        "observed_unique_trajectory_count": len(observed_set),
        "row_count": len(observed),
        "missing_trajectory_count": len(missing),
        "unexpected_trajectory_count": len(unexpected),
        "duplicate_trajectory_count": len(duplicates),
        "status": status,
        "missing_trajectory_ids_json": json.dumps(missing, separators=(",", ":")),
        "unexpected_trajectory_ids_json": json.dumps(unexpected, separators=(",", ":")),
        "duplicate_trajectory_ids_json": json.dumps(duplicates, separators=(",", ":")),
    }


def _incomplete_message(rows: Sequence[Mapping[str, Any]]) -> str:
    descriptions = []
    for row in rows:
        if row["status"] == "complete":
            continue
        descriptions.append(
            f"{row['analysis_id']}:{row['method']} "
            f"missing={row['missing_trajectory_count']} "
            f"unexpected={row['unexpected_trajectory_count']} "
            f"duplicates={row['duplicate_trajectory_count']}"
        )
    return "; ".join(descriptions)


def _locked_test_strata(
    test_rows: Sequence[Mapping[str, Any]],
    split_manifest: Mapping[str, Any],
    expected_ids: Sequence[str],
    *,
    fields: Sequence[str],
    default_sample_rate_hz: float | None,
    require_all: bool,
) -> dict[str, dict[str, Any]]:
    """Resolve predeclared trajectory strata without using metric outcomes."""

    if not fields or len(set(fields)) != len(fields):
        raise ReportingValidationError("stratification fields are empty or duplicated")
    entries = split_manifest.get("trajectories", [])
    manifest_rows = {
        str(entry["trajectory_id"]): entry
        for entry in entries
        if isinstance(entry, Mapping)
        and str(entry.get("split")) == "test"
        and isinstance(entry.get("trajectory_id"), str)
    }
    top_level_rate = split_manifest.get("sample_rate_hz")
    if top_level_rate is None:
        top_level_rate = default_sample_rate_hz
    result: dict[str, dict[str, Any]] = {}
    for field in fields:
        mapping: dict[str, Any] = {}
        missing: list[str] = []
        for trajectory_id in expected_ids:
            candidates: list[Any] = []
            entry = manifest_rows.get(trajectory_id, {})
            manifest_field = "family" if field == "reference_family" else field
            for value in (entry.get(field), entry.get(manifest_field)):
                if value is not None and value != "":
                    candidates.append(value)
            for row in test_rows:
                if str(row.get("trajectory_id")) != trajectory_id:
                    continue
                value = row.get(field)
                if value is not None and value != "":
                    candidates.append(value)
            if field == "sample_rate_hz" and not candidates:
                if top_level_rate is not None:
                    candidates.append(top_level_rate)
            try:
                normalized = {
                    (
                        f"{float(value):.12g}"
                        if field == "sample_rate_hz"
                        else str(value)
                    )
                    for value in candidates
                }
            except (TypeError, ValueError) as error:
                raise ReportingValidationError(
                    f"trajectory {trajectory_id!r} has invalid {field} metadata"
                ) from error
            if field == "sample_rate_hz" and any(
                not math.isfinite(float(value)) or float(value) <= 0.0
                for value in normalized
            ):
                raise ReportingValidationError(
                    f"trajectory {trajectory_id!r} has invalid sample_rate_hz metadata"
                )
            if len(normalized) > 1:
                raise ReportingValidationError(
                    f"trajectory {trajectory_id!r} has conflicting {field} labels: "
                    f"{sorted(normalized)}"
                )
            if not normalized:
                missing.append(trajectory_id)
                continue
            mapping[trajectory_id] = next(iter(normalized))
        if missing:
            if require_all:
                raise ReportingValidationError(
                    f"locked-test stratification {field!r} is incomplete; "
                    f"missing={missing[:5]}"
                )
            continue
        result[field] = mapping
    if require_all and set(result) != set(fields):
        raise ReportingValidationError(
            "locked-test stratification dimensions are incomplete"
        )
    return result


def build_statistical_tables(
    trajectory_metrics: Sequence[Mapping[str, Any]],
    split_manifest: Mapping[str, Any],
    *,
    comparisons: Sequence[Mapping[str, Any]] = DEFAULT_COMPARISONS,
    ci_metrics: Sequence[str] = DEFAULT_CI_METRICS,
    ci_methods: Sequence[str] | None = None,
    resamples: int = 10_000,
    confidence_level: float = 0.95,
    seed: int = 20260721,
    alpha: float = 0.05,
    expected_test_count: int | None = 120,
    incomplete_policy: str = "reject",
    stratification_fields: Sequence[str] = DEFAULT_STRATIFICATION_FIELDS,
    default_sample_rate_hz: float | None = None,
    require_stratification: bool = False,
) -> StatisticalTables:
    """Compute locked-test trajectory inference with exact denominators.

    ``incomplete_policy='reject'`` is the formal default.  ``'report'`` is a QA
    mode: incomplete comparisons are explicitly marked unavailable and the
    entire predeclared Holm family is withheld, so the multiplicity family is
    never silently changed after seeing failures.
    """

    if incomplete_policy not in {"reject", "report"}:
        raise ReportingValidationError("incomplete_policy must be 'reject' or 'report'")
    if not trajectory_metrics:
        raise ReportingValidationError("trajectory_metrics is empty")
    if not comparisons:
        raise ReportingValidationError("comparisons is empty")
    if not ci_metrics:
        raise ReportingValidationError("ci_metrics is empty")
    expected_ids = expected_trajectory_ids(
        split_manifest, split="test", expected_count=expected_test_count
    )
    test_rows = [
        dict(row)
        for row in trajectory_metrics
        if str(row.get("split", "test")) == "test"
        and str(row.get("scenario_id", "clean")) == "clean"
    ]
    if not test_rows:
        raise ReportingValidationError("no clean locked-test trajectory metrics")
    strata = _locked_test_strata(
        test_rows,
        split_manifest,
        expected_ids,
        fields=stratification_fields,
        default_sample_rate_hz=default_sample_rate_hz,
        require_all=require_stratification,
    )

    completeness: list[dict[str, Any]] = []
    status: list[dict[str, Any]] = []
    normalized_comparisons: list[dict[str, Any]] = []
    comparison_ids: set[str] = set()
    for index, comparison in enumerate(comparisons):
        required = {"metric", "baseline_method", "candidate_method"}
        missing_fields = required - set(comparison)
        if missing_fields:
            raise ReportingValidationError(
                f"comparison {index} is missing {sorted(missing_fields)}"
            )
        normalized = dict(comparison)
        normalized["comparison_id"] = str(
            comparison.get(
                "comparison_id",
                f"{comparison['metric']}:{comparison['candidate_method']}"
                f"-vs-{comparison['baseline_method']}",
            )
        )
        if normalized["comparison_id"] in comparison_ids:
            raise ReportingValidationError(
                f"duplicate comparison_id {normalized['comparison_id']!r}"
            )
        comparison_ids.add(normalized["comparison_id"])
        normalized_comparisons.append(normalized)
        for role, method_field in (
            ("baseline", "baseline_method"),
            ("candidate", "candidate_method"),
        ):
            completeness.append(
                _completeness_row(
                    test_rows,
                    analysis_kind="paired_comparison",
                    analysis_id=normalized["comparison_id"],
                    metric=str(normalized["metric"]),
                    method=str(normalized[method_field]),
                    role=role,
                    expected_ids=expected_ids,
                )
            )

    incomplete_comparisons = [
        row
        for row in completeness
        if row["analysis_kind"] == "paired_comparison" and row["status"] != "complete"
    ]
    if incomplete_comparisons and incomplete_policy == "reject":
        raise ReportingValidationError(
            "locked-test paired denominator is incomplete; "
            + _incomplete_message(incomplete_comparisons)
        )

    paired_rows: list[dict[str, Any]] = []
    stratified_rows: list[dict[str, Any]] = []
    stratum_effect_rows: list[dict[str, Any]] = []
    heterogeneity_rows: list[dict[str, Any]] = []
    trajectory_summary_rows: list[dict[str, Any]] = []
    worst_trajectory_rows: list[dict[str, Any]] = []
    if incomplete_comparisons:
        for comparison in normalized_comparisons:
            status.append(
                {
                    "analysis_kind": "paired_comparison",
                    "analysis_id": comparison["comparison_id"],
                    "metric": str(comparison["metric"]),
                    "method": (
                        f"{comparison['candidate_method']} vs "
                        f"{comparison['baseline_method']}"
                    ),
                    "status": "unavailable_incomplete_predeclared_family",
                    "reason": (
                        "at least one method/trajectory cell is missing, unexpected, "
                        "or duplicated; no complete-case inference was run"
                    ),
                }
            )
    else:
        for index, comparison in enumerate(normalized_comparisons):
            metric = str(comparison["metric"])
            baseline = str(comparison["baseline_method"])
            candidate = str(comparison["candidate_method"])
            relevant = [
                row
                for row in test_rows
                if str(row.get("method")) in {baseline, candidate}
                and _finite_metric(row, metric)
            ]
            try:
                result = paired_comparison_from_records(
                    relevant,
                    metric=metric,
                    baseline_method=baseline,
                    candidate_method=candidate,
                    method_field="method",
                    unit_fields=("trajectory_id",),
                    resamples=resamples,
                    confidence_level=confidence_level,
                    seed=seed + index,
                    lower_is_better=bool(comparison.get("lower_is_better", True)),
                    expected_units=expected_ids,
                )
            except StatisticalValidationError as error:
                raise ReportingValidationError(
                    f"paired comparison {comparison['comparison_id']!r} failed: {error}"
                ) from error
            paired_rows.append(
                {
                    "comparison_id": comparison["comparison_id"],
                    "secondary": bool(comparison.get("secondary", True)),
                    **result.to_dict(),
                }
            )
            baseline_values = {
                str(row["trajectory_id"]): float(row[metric])
                for row in relevant
                if str(row.get("method")) == baseline
            }
            candidate_values = {
                str(row["trajectory_id"]): float(row[metric])
                for row in relevant
                if str(row.get("method")) == candidate
            }
            first_stratum_result: dict[str, Any] | None = None
            for stratum_index, (stratum_field, stratum_map) in enumerate(
                strata.items()
            ):
                try:
                    stratified = stratified_paired_trajectory_bootstrap(
                        baseline_values,
                        candidate_values,
                        stratum_map,
                        metric=metric,
                        baseline_method=baseline,
                        candidate_method=candidate,
                        stratum_name=stratum_field,
                        resamples=resamples,
                        confidence_level=confidence_level,
                        seed=seed + 100_000 + index * 100 + stratum_index,
                        lower_is_better=bool(comparison.get("lower_is_better", True)),
                        expected_units=expected_ids,
                    )
                except StatisticalValidationError as error:
                    raise ReportingValidationError(
                        f"stratified comparison {comparison['comparison_id']!r} "
                        f"by {stratum_field!r} failed: {error}"
                    ) from error
                if first_stratum_result is None:
                    first_stratum_result = stratified
                stratified_rows.append(
                    {
                        "comparison_id": comparison["comparison_id"],
                        "stratum_dimension": stratum_field,
                        **stratified["stratified"],
                    }
                )
                for effect in stratified["strata"]:
                    stratum_effect_rows.append(
                        {
                            "comparison_id": comparison["comparison_id"],
                            "stratum_dimension": stratum_field,
                            "stratum_value": effect[stratum_field],
                            **{
                                key: value
                                for key, value in effect.items()
                                if key != stratum_field
                            },
                        }
                    )
                heterogeneity_rows.append(
                    {
                        "comparison_id": comparison["comparison_id"],
                        "stratum_dimension": stratum_field,
                        **stratified["heterogeneity"],
                    }
                )
            if first_stratum_result is not None:
                trajectory_summary_rows.append(
                    {
                        "comparison_id": comparison["comparison_id"],
                        "metric": metric,
                        "baseline_method": baseline,
                        "candidate_method": candidate,
                        **{
                            key: value
                            for key, value in first_stratum_result[
                                "trajectory_summary"
                            ].items()
                            if key != "worst_5"
                        },
                    }
                )
                for rank, row in enumerate(
                    first_stratum_result["trajectory_summary"]["worst_5"], start=1
                ):
                    worst_trajectory_rows.append(
                        {
                            "comparison_id": comparison["comparison_id"],
                            "rank": rank,
                            "trajectory_id": row["unit"],
                            "baseline": row["baseline"],
                            "candidate": row["candidate"],
                            "improvement": row["improvement"],
                        }
                    )
        secondary_indices = [
            index for index, row in enumerate(paired_rows) if row["secondary"]
        ]
        if secondary_indices:
            adjusted = holm_adjust(
                [
                    float(paired_rows[index]["unadjusted_p_value"])
                    for index in secondary_indices
                ]
            )
            for index, value in zip(secondary_indices, adjusted):
                paired_rows[index]["holm_adjusted_p_value"] = value
                paired_rows[index]["reject_holm"] = bool(value < alpha)
        for row in paired_rows:
            if not row["secondary"]:
                value = float(row["unadjusted_p_value"])
                row["holm_adjusted_p_value"] = value
                row["reject_holm"] = bool(value < alpha)
            status.append(
                {
                    "analysis_kind": "paired_comparison",
                    "analysis_id": row["comparison_id"],
                    "metric": row["metric"],
                    "method": (
                        f"{row['candidate_method']} vs {row['baseline_method']}"
                    ),
                    "status": "available_complete_pairs",
                    "reason": "exact split-manifest denominator verified",
                }
            )

    methods = (
        sorted({str(row.get("method")) for row in test_rows})
        if ci_methods is None
        else [str(method) for method in ci_methods]
    )
    if not methods or len(set(methods)) != len(methods):
        raise ReportingValidationError("ci_methods is empty or duplicated")
    confidence_rows: list[dict[str, Any]] = []
    ci_incomplete: list[dict[str, Any]] = []
    for metric_index, metric in enumerate(ci_metrics):
        for method_index, method in enumerate(methods):
            audit = _completeness_row(
                test_rows,
                analysis_kind="method_confidence_interval",
                analysis_id=f"{metric}:{method}",
                metric=str(metric),
                method=method,
                role="method",
                expected_ids=expected_ids,
            )
            completeness.append(audit)
            if audit["status"] != "complete":
                ci_incomplete.append(audit)
                status.append(
                    {
                        "analysis_kind": "method_confidence_interval",
                        "analysis_id": audit["analysis_id"],
                        "metric": str(metric),
                        "method": method,
                        "status": "unavailable_incomplete_denominator",
                        "reason": (
                            "method rows differ from exact split-manifest denominator; "
                            "no complete-case interval was run"
                        ),
                    }
                )
                continue
            relevant = [
                row
                for row in test_rows
                if str(row.get("method")) == method and _finite_metric(row, str(metric))
            ]
            try:
                rows = bootstrap_confidence_intervals(
                    relevant,
                    metric=str(metric),
                    method_field="method",
                    unit_fields=("trajectory_id",),
                    resamples=resamples,
                    confidence_level=confidence_level,
                    seed=seed + 10_000 + metric_index * 1_000 + method_index,
                )
            except StatisticalValidationError as error:
                raise ReportingValidationError(
                    f"confidence interval {metric}:{method} failed: {error}"
                ) from error
            if len(rows) != 1 or int(rows[0]["n_trajectories"]) != len(expected_ids):
                raise ReportingValidationError(
                    f"confidence interval {metric}:{method} changed its denominator"
                )
            confidence_rows.extend(rows)
            status.append(
                {
                    "analysis_kind": "method_confidence_interval",
                    "analysis_id": audit["analysis_id"],
                    "metric": str(metric),
                    "method": method,
                    "status": "available_complete_denominator",
                    "reason": "exact split-manifest denominator verified",
                }
            )
    if ci_incomplete and incomplete_policy == "reject":
        raise ReportingValidationError(
            "locked-test CI denominator is incomplete; "
            + _incomplete_message(ci_incomplete)
        )
    return StatisticalTables(
        paired_comparisons=paired_rows,
        stratified_comparisons=stratified_rows,
        stratum_effects=stratum_effect_rows,
        heterogeneity=heterogeneity_rows,
        trajectory_outcome_summary=trajectory_summary_rows,
        worst_trajectories=worst_trajectory_rows,
        confidence_intervals=confidence_rows,
        completeness=sorted(
            completeness,
            key=lambda row: (
                str(row["analysis_kind"]),
                str(row["analysis_id"]),
                str(row["role"]),
                str(row["method"]),
            ),
        ),
        inference_status=sorted(
            status,
            key=lambda row: (
                str(row["analysis_kind"]),
                str(row["analysis_id"]),
                str(row["method"]),
            ),
        ),
        expected_trajectory_ids=expected_ids,
    )


def _require_bundle(
    bundles: Mapping[str, ValidatedBundle], name: str
) -> ValidatedBundle:
    try:
        return bundles[name]
    except KeyError as error:
        raise ReportingValidationError(
            f"figure assembly requires raw bundle {name!r}"
        ) from error


def _configured_sample_rate_hz(bundle: ValidatedBundle) -> float:
    config = bundle.run_manifest.get("resolved_config")
    if not isinstance(config, Mapping):
        raise ReportingValidationError(
            "locked-test run manifest has no resolved config"
        )
    data = config.get("data")
    value = data.get("sample_rate_hz") if isinstance(data, Mapping) else None
    if value is None:
        value = config.get("sample_rate_hz")
    try:
        rate = float(value)
    except (TypeError, ValueError) as error:
        raise ReportingValidationError(
            "locked-test resolved config has no finite sample_rate_hz"
        ) from error
    if not math.isfinite(rate) or rate <= 0.0:
        raise ReportingValidationError(
            "locked-test resolved config has no finite sample_rate_hz"
        )
    return rate


def build_core_diagnostic_publications(
    bundles: Mapping[str, ValidatedBundle],
    *,
    maximum_rows_per_table: int = 100_000,
) -> dict[str, list[dict[str, Any]]]:
    """Load the bounded locked-test diagnostics that the final layer publishes."""

    if maximum_rows_per_table < 1:
        raise ReportingValidationError(
            "maximum core-diagnostic row count must be positive"
        )
    output: dict[str, list[dict[str, Any]]] = {}
    for source, destination in CORE_DIAGNOSTIC_PUBLICATIONS.items():
        bundle_name, relative_path = source.split("/", 1)
        rows = load_bundle_csv(
            _require_bundle(bundles, bundle_name),
            relative_path,
            required_fields=_CORE_DIAGNOSTIC_REQUIRED_FIELDS[source],
        )
        if len(rows) > maximum_rows_per_table:
            raise ReportingValidationError(
                f"core diagnostic {source!r} has {len(rows)} rows, exceeding "
                f"the bounded publication limit {maximum_rows_per_table}"
            )
        if destination in output:
            raise ReportingValidationError(
                f"duplicate core-diagnostic destination {destination!r}"
            )
        output[destination] = rows
    if set(output) != set(CORE_DIAGNOSTIC_PUBLICATIONS.values()):
        raise ReportingValidationError(
            "core-diagnostic publication mapping is incomplete"
        )
    return output


def _project_row(row: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    missing = [field for field in fields if field not in row or row[field] is None]
    if missing:
        raise ReportingValidationError(f"row is missing required values {missing}")
    return {field: row[field] for field in fields}


def _finite_value(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ReportingValidationError(f"field {field} is not numeric") from error
    if not math.isfinite(number):
        raise ReportingValidationError(f"field {field} is NaN or infinity")
    return number


def _bounded_group_sample(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_field: str,
    maximum_per_group: int,
    sort_fields: Sequence[str],
) -> list[Mapping[str, Any]]:
    if maximum_per_group < 1:
        raise ReportingValidationError("maximum_per_group must be positive")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[group_field])].append(row)
    selected: list[Mapping[str, Any]] = []
    for group in sorted(grouped):
        ordered = sorted(
            grouped[group],
            key=lambda row: tuple(str(row.get(field, "")) for field in sort_fields),
        )
        if len(ordered) <= maximum_per_group:
            selected.extend(ordered)
            continue
        indices = np.linspace(0, len(ordered) - 1, maximum_per_group, dtype=np.int64)
        selected.extend(ordered[int(index)] for index in indices)
    return selected


def filter_primary_method_rows(
    records: Sequence[Mapping[str, Any]], *, require_all: bool = False
) -> list[dict[str, Any]]:
    """Keep only the eight preregistered primary method IDs.

    Exact membership, rather than a negative prefix match, prevents an
    estimator-rank diagnostic or another later-added method from silently
    entering a primary figure.
    """

    selected = [
        dict(row) for row in records if str(row.get("method")) in _PRIMARY_METHOD_SET
    ]
    if not selected:
        raise ReportingValidationError("no primary-method rows are available")
    if require_all:
        observed = {str(row["method"]) for row in selected}
        missing = _PRIMARY_METHOD_SET - observed
        if missing:
            raise ReportingValidationError(
                f"primary method table is missing {sorted(missing)}"
            )
    return selected


def build_constraint_jerk_table(
    trajectory_metrics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Map the three independent trajectory-level jerk semantics for figures.

    The sampled-output, follower-reported ``new_jerk``, and continuous internal
    profile jerk fields are independently recomputed from canonical samples.
    Constraint-audit endpoint availability is deliberately irrelevant here;
    in particular, a Ruckig analytic audit may omit ``max_sampled_jerk`` while
    the three trajectory metric columns remain valid.
    """

    source_fields = {
        "sampled_output": "sampled_output_max_sampled_jerk",
        "direct_new_jerk": "sampled_output_max_new_jerk",
        "internal_profile": "sampled_output_max_internal_jerk",
    }
    rows = []
    for row in filter_primary_method_rows(trajectory_metrics):
        for semantic, source in source_fields.items():
            if row.get(source) is None:
                continue
            rows.append(
                {
                    "method": str(row["method"]),
                    "jerk_semantic": semantic,
                    "max_abs_jerk": _finite_value(row[source], field=source),
                }
            )
    if not rows:
        raise ReportingValidationError(
            "no primary method has an available independent jerk metric"
        )
    return rows


def _sampling_rate_from_scenario(scenario_id: Any) -> float:
    match = re.fullmatch(r"rate_([0-9]+(?:\.[0-9]+)?)hz", str(scenario_id))
    if match is None:
        raise ReportingValidationError(
            f"cannot recover sampling rate from scenario_id {scenario_id!r}"
        )
    return float(match.group(1))


def select_acceleration_phase_condition(
    records: Sequence[Mapping[str, Any]],
    *,
    target_time_mode: str = "next_cycle",
    configured_horizon_ms: float = 10.0,
) -> list[dict[str, Any]]:
    """Select one physical acceleration-study condition without averaging modes."""

    if not records:
        raise ReportingValidationError("acceleration phase table is empty")
    selected = []
    for index, row in enumerate(records):
        missing = {
            *FIGURE_TABLE_SCHEMAS["acceleration_phase"],
            "target_time_mode",
            "configured_horizon_ms",
        } - set(row)
        if missing:
            raise ReportingValidationError(
                f"acceleration phase row {index} is missing {sorted(missing)}"
            )
        if str(row["target_time_mode"]) != target_time_mode:
            continue
        if not math.isclose(
            _finite_value(row["configured_horizon_ms"], field="configured_horizon_ms"),
            configured_horizon_ms,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            continue
        selected.append(_project_row(row, FIGURE_TABLE_SCHEMAS["acceleration_phase"]))
    if not selected:
        raise ReportingValidationError(
            "acceleration phase table lacks requested physical condition "
            f"{target_time_mode}/H={configured_horizon_ms:g} ms"
        )
    cells = [
        (
            _finite_value(row["r_j"], field="r_j"),
            _finite_value(row["r_a"], field="r_a"),
        )
        for row in selected
    ]
    if len(set(cells)) != len(cells):
        raise ReportingValidationError(
            "acceleration phase condition has duplicate r_j/r_a cells"
        )
    r_j_values = {cell[0] for cell in cells}
    r_a_values = {cell[1] for cell in cells}
    expected_cells = {(r_j, r_a) for r_j in r_j_values for r_a in r_a_values}
    if set(cells) != expected_cells:
        raise ReportingValidationError(
            "acceleration phase condition is not a complete r_j/r_a matrix"
        )
    return sorted(selected, key=lambda row: (float(row["r_j"]), float(row["r_a"])))


def validate_figure_tables(tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    """Enforce the exact, narrow schema for every required figure input."""

    if set(tables) != set(FIGURE_TABLE_SCHEMAS):
        raise ReportingValidationError(
            "figure table set differs from contract: "
            f"missing={sorted(set(FIGURE_TABLE_SCHEMAS) - set(tables))}, "
            f"extra={sorted(set(tables) - set(FIGURE_TABLE_SCHEMAS))}"
        )
    for name, fields in FIGURE_TABLE_SCHEMAS.items():
        rows = list(tables[name])
        if not rows:
            raise ReportingValidationError(f"figure table {name!r} is empty")
        expected = set(fields)
        for index, row in enumerate(rows):
            if set(row) != expected:
                raise ReportingValidationError(
                    f"figure table {name!r} row {index} schema differs: "
                    f"missing={sorted(expected - set(row))}, "
                    f"extra={sorted(set(row) - expected)}"
                )
            for field in fields:
                value = row[field]
                if value is None or (isinstance(value, str) and not value):
                    raise ReportingValidationError(
                        f"figure table {name!r} row {index}.{field} is missing"
                    )
                if field in _NUMERIC_FIGURE_FIELDS:
                    _finite_value(value, field=field)


def assemble_figure_tables(
    bundles: Mapping[str, ValidatedBundle],
    statistical_tables: StatisticalTables,
    *,
    ranking_method: str = "one_step_governed_pva_direct",
    predefined_trace_ids: Sequence[str] = (),
    maximum_runtime_rows_per_method: int = 2_000,
    maximum_trace_rows_per_joint: int = 2_000,
) -> dict[str, list[dict[str, Any]]]:
    """Derive exact, bounded inputs for all required figure categories."""

    validation = _require_bundle(bundles, "validation")
    locked = _require_bundle(bundles, "locked_test")
    acceleration = _require_bundle(bundles, "acceleration")
    robustness = _require_bundle(bundles, "robustness")
    rate_study = _require_bundle(bundles, "rate_study")
    multidof = _require_bundle(bundles, "multidof")
    plant_bundle = _require_bundle(bundles, "plant")

    estimator_source = load_bundle_csv(
        validation,
        "estimator_grid_metrics.csv",
        required_fields=FIGURE_TABLE_SCHEMAS["estimator"],
    )
    estimator = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["estimator"])
        for row in estimator_source
        if str(row.get("split")) == "validation"
    ]
    prediction_source = load_bundle_csv(
        validation,
        "predictor_horizon_metrics.csv",
        required_fields={"method", "prediction_horizon_ms", "prediction_p_rmse"},
    )
    prediction = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["prediction"])
        for row in prediction_source
        if str(row.get("split")) == "validation"
    ]

    locked_metrics = load_bundle_csv(
        locked,
        "metrics_by_trajectory.csv",
        required_fields={"trajectory_id", "method", "position_rmse"},
    )
    locked_test = [
        row
        for row in locked_metrics
        if str(row.get("split")) == "test" and str(row.get("scenario_id")) == "clean"
    ]
    locked_primary = filter_primary_method_rows(locked_test, require_all=True)
    ablation = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["ablation"]) for row in locked_primary
    ]
    governor = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["governor"])
        for row in locked_primary
        if all(row.get(field) is not None for field in FIGURE_TABLE_SCHEMAS["governor"])
    ]
    follower_methods = {
        "one_step_governed_pva_direct": "direct",
        "one_step_governed_pva_ruckig": "ruckig",
    }
    follower = [
        {
            "trajectory_id": row["trajectory_id"],
            "follower": follower_methods[str(row["method"])],
            "position_rmse": row["position_rmse"],
        }
        for row in locked_primary
        if str(row.get("method")) in follower_methods
    ]
    trajectory_metrics = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["trajectory_metrics"])
        for row in locked_primary
        if str(row.get("method")) == ranking_method
    ]
    selection = select_representative_trajectories(
        trajectory_metrics,
        ranking_method=ranking_method,
        predefined_ids=predefined_trace_ids,
    )
    selected_ids = {str(row["trajectory_id"]) for row in selection}
    trace_source = [
        row
        for row in load_bundle_parquet(locked)
        if str(row.get("method_id")) == ranking_method
        and str(row.get("trajectory_id")) in selected_ids
        and row.get("command_p") is not None
        and row.get("command_time") is not None
    ]
    trace_bounded = _bounded_group_sample(
        trace_source,
        group_field="trajectory_id",
        maximum_per_group=max(
            maximum_trace_rows_per_joint,
            maximum_trace_rows_per_joint
            * len({str(row.get("joint_id")) for row in trace_source}),
        ),
        sort_fields=("trajectory_id", "joint_id", "k"),
    )
    trace_samples = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["trace_samples"])
        for row in trace_bounded
    ]

    acceleration_phase_source = load_bundle_csv(
        acceleration,
        "acceleration_phase_map.csv",
        required_fields={
            *FIGURE_TABLE_SCHEMAS["acceleration_phase"],
            "target_time_mode",
            "configured_horizon_ms",
        },
    )
    # The raw phase map contains two legitimate but different experiments:
    # current-state/H=0 and next-cycle/H=10 ms.  A single heatmap must not
    # average those conditions together; the paper-evidence figure is the
    # deployable next-cycle condition and the raw table retains both.
    acceleration_phase = select_acceleration_phase_condition(
        acceleration_phase_source,
        target_time_mode="next_cycle",
        configured_horizon_ms=10.0,
    )
    robustness_rows = load_bundle_csv(
        robustness,
        "metrics_by_trajectory.csv",
        required_fields=FIGURE_TABLE_SCHEMAS["robustness"],
    )
    robustness_table = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["robustness"]) for row in robustness_rows
    ]
    rate_rows = load_bundle_csv(
        rate_study,
        "metrics_by_trajectory.csv",
        required_fields={"scenario_id", "method", "position_rmse"},
    )
    sampling_rate = [
        {
            "sampling_rate_hz": _sampling_rate_from_scenario(row["scenario_id"]),
            "method": row["method"],
            "position_rmse": row["position_rmse"],
        }
        for row in rate_rows
    ]
    constraints = build_constraint_jerk_table(locked_primary)
    multidof_rows = load_bundle_csv(
        multidof,
        "metrics_by_trajectory.csv",
        required_fields={"n_joints", "method", "total_p99_us"},
    )
    scalability = [
        {
            "dof": int(row["n_joints"]),
            "method": row["method"],
            "total_p99_us": row["total_p99_us"],
        }
        for row in multidof_rows
    ]
    plant_rows = load_bundle_csv(
        plant_bundle,
        "plant_reference_per_joint_metrics.csv",
        required_fields={"method_id", "position_rmse", "output_field"},
    )
    plant: list[dict[str, Any]] = []
    for row in plant_rows:
        if str(row["output_field"]) != "plant_p":
            raise ReportingValidationError(
                "plant comparison must use plant-to-reference tracking metrics"
            )
        pieces = str(row["method_id"]).split("::", 1)
        if len(pieces) != 2 or not all(pieces):
            raise ReportingValidationError(
                f"plant method_id lacks plant::feedback encoding: {row['method_id']!r}"
            )
        plant.append(
            {
                "plant": pieces[0],
                "method": pieces[1],
                "position_rmse": row["position_rmse"],
            }
        )
    runtime_rows = load_bundle_csv(
        locked,
        "runtime_repetition_samples.csv",
        required_fields=FIGURE_TABLE_SCHEMAS["runtime_samples"],
    )
    runtime_bounded = _bounded_group_sample(
        runtime_rows,
        group_field="method",
        maximum_per_group=maximum_runtime_rows_per_method,
        sort_fields=("method", "repetition", "trajectory_id", "k"),
    )
    runtime_samples = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["runtime_samples"])
        for row in runtime_bounded
    ]
    paired = [
        _project_row(row, FIGURE_TABLE_SCHEMAS["paired"])
        for row in statistical_tables.paired_comparisons
    ]
    tables = {
        "estimator": estimator,
        "prediction": prediction,
        "ablation": ablation,
        "acceleration_phase": acceleration_phase,
        "governor": governor,
        "follower": follower,
        "robustness": robustness_table,
        "sampling_rate": sampling_rate,
        "constraints": constraints,
        "scalability": scalability,
        "plant": plant,
        "runtime_samples": runtime_samples,
        "paired": paired,
        "trajectory_metrics": trajectory_metrics,
        "trace_samples": trace_samples,
    }
    for name, rows in tables.items():
        tables[name] = sorted(
            rows,
            key=lambda row: tuple(
                str(row[field]) for field in FIGURE_TABLE_SCHEMAS[name]
            ),
        )
    validate_figure_tables(tables)
    return tables


def generate_final_figures(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    output_directory: str | Path,
    *,
    ranking_method: str,
    predefined_trace_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Generate and verify all deterministic required figure categories."""

    validate_figure_tables(tables)
    try:
        manifest = generate_required_figures(
            tables,
            output_directory,
            ranking_method=ranking_method,
            predefined_trace_ids=predefined_trace_ids,
        )
    except FigureValidationError as error:
        raise ReportingValidationError(
            f"required figure generation failed: {error}"
        ) from error
    if tuple(manifest.get("categories", {})) != REQUIRED_FIGURE_CATEGORIES:
        raise ReportingValidationError(
            "required figure category manifest is incomplete"
        )
    output = Path(output_directory)
    for category in REQUIRED_FIGURE_CATEGORIES:
        declared = manifest["categories"][category]
        if declared != [f"{category}.png", f"{category}.svg"]:
            raise ReportingValidationError(
                f"figure category {category} has unexpected artifacts {declared}"
            )
        for name in declared:
            target = output / name
            if not target.is_file() or target.stat().st_size == 0:
                raise ReportingValidationError(f"figure artifact is missing: {target}")
    return manifest


_ACCEPTANCE_FIELDS = (
    "schema_version",
    "criterion_id",
    "family",
    "scope",
    "method",
    "metric",
    "source_artifact",
    "observed_value",
    "operator",
    "threshold_value",
    "threshold_defined",
    "numerator",
    "denominator",
    "denominator_defined",
    "status",
    "required",
    "failure_stage",
    "notes",
)

_CANDIDATE_METHOD = "one_step_governed_pva_direct"
_PAIRED_BASELINE_METHOD = "predicted_p"
_CSV_BASELINE_METHOD = "deployed_p_only"


def _acceptance_record(
    criterion_id: str,
    *,
    family: str,
    scope: str,
    method: str,
    metric: str,
    source_artifact: str,
    observed_value: float,
    operator: str,
    threshold_value: float,
    numerator: float = 0.0,
    denominator: float = 0.0,
    denominator_defined: bool = False,
    threshold_defined: bool = True,
    required: bool = True,
    failure_stage: str,
    notes: str,
    status: str | None = None,
) -> dict[str, Any]:
    """Create one finite, rectangular, machine-evaluable criterion row."""

    observed = _finite_value(observed_value, field=f"{criterion_id}.observed_value")
    threshold = _finite_value(threshold_value, field=f"{criterion_id}.threshold_value")
    numerator_value = _finite_value(numerator, field=f"{criterion_id}.numerator")
    denominator_value = _finite_value(denominator, field=f"{criterion_id}.denominator")
    if denominator_value < 0.0 or numerator_value < 0.0:
        raise ReportingValidationError(
            f"acceptance criterion {criterion_id!r} has negative counts"
        )
    if status is None:
        if not denominator_defined and operator in {"rate==", "rate<=", "rate>="}:
            status = "unavailable_zero_denominator"
        elif not threshold_defined or operator == "report_only":
            status = "reported"
        else:
            comparisons = {
                ">=": observed >= threshold,
                ">": observed > threshold,
                "<=": observed <= threshold,
                "<": observed < threshold,
                "==": math.isclose(observed, threshold, rel_tol=0.0, abs_tol=1e-12),
                "rate==": math.isclose(observed, threshold, rel_tol=0.0, abs_tol=1e-12),
                "rate<=": observed <= threshold,
                "rate>=": observed >= threshold,
            }
            if operator not in comparisons:
                raise ReportingValidationError(
                    f"acceptance criterion {criterion_id!r} has unknown operator {operator!r}"
                )
            status = "pass" if comparisons[operator] else "fail"
    if status not in {
        "pass",
        "fail",
        "reported",
        "unavailable_zero_denominator",
    }:
        raise ReportingValidationError(
            f"acceptance criterion {criterion_id!r} has invalid status {status!r}"
        )
    record = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "criterion_id": criterion_id,
        "family": family,
        "scope": scope,
        "method": method,
        "metric": metric,
        "source_artifact": source_artifact,
        "observed_value": observed,
        "operator": operator,
        "threshold_value": threshold,
        "threshold_defined": bool(threshold_defined),
        "numerator": numerator_value,
        "denominator": denominator_value,
        "denominator_defined": bool(denominator_defined),
        "status": status,
        "required": bool(required),
        "failure_stage": failure_stage,
        "notes": notes,
    }
    if tuple(record) != _ACCEPTANCE_FIELDS:
        raise ReportingValidationError("internal acceptance schema order differs")
    return record


def _unique_row(
    records: Sequence[Mapping[str, Any]],
    *,
    field: str,
    value: str,
    label: str,
) -> Mapping[str, Any]:
    matches = [row for row in records if str(row.get(field)) == value]
    if len(matches) != 1:
        raise ReportingValidationError(
            f"{label} requires exactly one {field}={value!r} row; observed {len(matches)}"
        )
    return matches[0]


def _exact_method_metric(
    records: Sequence[Mapping[str, Any]],
    *,
    method: str,
    metric: str,
    expected_ids: Sequence[str],
    absolute: bool = False,
) -> dict[str, float]:
    indexed: dict[str, float] = {}
    for row in records:
        if str(row.get("method")) != method:
            continue
        if (
            str(row.get("split", "test")) != "test"
            or str(row.get("scenario_id", "clean")) != "clean"
        ):
            continue
        trajectory_id = str(row.get("trajectory_id", ""))
        if trajectory_id in indexed:
            raise ReportingValidationError(
                f"duplicate locked metric row for {method}/{trajectory_id}"
            )
        value = _finite_value(row.get(metric), field=f"{method}.{metric}")
        indexed[trajectory_id] = abs(value) if absolute else value
    expected = set(expected_ids)
    if set(indexed) != expected:
        raise ReportingValidationError(
            f"locked metric denominator differs for {method}/{metric}: "
            f"missing={sorted(expected - set(indexed))[:5]}, "
            f"unexpected={sorted(set(indexed) - expected)[:5]}"
        )
    return indexed


def _absolute_lag_difference(
    records: Sequence[Mapping[str, Any]],
    *,
    expected_ids: Sequence[str],
    baseline_method: str = _PAIRED_BASELINE_METHOD,
    candidate_method: str = _CANDIDATE_METHOD,
) -> tuple[float, float, float]:
    baseline = _exact_method_metric(
        records,
        method=baseline_method,
        metric="lag_s",
        expected_ids=expected_ids,
        absolute=True,
    )
    candidate = _exact_method_metric(
        records,
        method=candidate_method,
        metric="lag_s",
        expected_ids=expected_ids,
        absolute=True,
    )
    ordered = sorted(expected_ids)
    baseline_mean = float(np.mean([baseline[item] for item in ordered]))
    candidate_mean = float(np.mean([candidate[item] for item in ordered]))
    return baseline_mean, candidate_mean, candidate_mean - baseline_mean


def aggregate_governor_acceptance(
    invariant_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate exact count denominators from trajectory invariant rows."""

    if not invariant_rows:
        raise ReportingValidationError("candidate governor invariant table is empty")
    required = {
        "n_samples",
        "nonfallback_sample_count",
        "nonfallback_point_admissible_count",
        "nonfallback_one_step_reachable_count",
        "nonfallback_t_free_recorded_count",
        "nonfallback_transition_count",
        "nonfallback_sequence_consistent_count",
        "projection_count",
        "fallback_count",
    }
    for index, row in enumerate(invariant_rows):
        missing = required - set(row)
        if missing:
            raise ReportingValidationError(
                f"governor invariant row {index} is missing {sorted(missing)}"
            )

    def total(field: str) -> int:
        values = []
        for row in invariant_rows:
            raw = row[field]
            if isinstance(raw, bool):
                raise ReportingValidationError(f"{field} is boolean, not a count")
            value = int(raw)
            if value < 0 or float(raw) != value:
                raise ReportingValidationError(f"{field} is not a non-negative integer")
            values.append(value)
        return sum(values)

    samples = total("n_samples")
    nonfallback = total("nonfallback_sample_count")
    point = total("nonfallback_point_admissible_count")
    reachable = total("nonfallback_one_step_reachable_count")
    recorded = total("nonfallback_t_free_recorded_count")
    transitions = total("nonfallback_transition_count")
    consistent = total("nonfallback_sequence_consistent_count")
    projections = total("projection_count")
    fallbacks = total("fallback_count")
    if samples <= 0 or fallbacks + nonfallback != samples:
        raise ReportingValidationError(
            "governor invariant sample/fallback denominators are inconsistent"
        )
    for name, numerator, denominator in (
        ("point admissibility", point, nonfallback),
        ("T_free recorded", recorded, nonfallback),
        ("T_free reachability", reachable, nonfallback),
        ("sequence consistency", consistent, transitions),
        ("projection", projections, samples),
    ):
        if numerator > denominator:
            raise ReportingValidationError(
                f"governor {name} numerator exceeds its denominator"
            )
    if recorded != nonfallback:
        raise ReportingValidationError(
            "non-fallback T_free coverage differs from its exact denominator"
        )
    return {
        "sample_count": samples,
        "nonfallback_sample_count": nonfallback,
        "point_admissible_count": point,
        "point_admissible_rate": point / nonfallback if nonfallback else 0.0,
        "point_rate_defined": nonfallback > 0,
        "t_free_reachable_count": reachable,
        "t_free_reachable_rate": reachable / nonfallback if nonfallback else 0.0,
        "t_free_rate_defined": nonfallback > 0,
        "sequence_transition_count": transitions,
        "sequence_consistent_count": consistent,
        "sequence_consistency_rate": consistent / transitions if transitions else 0.0,
        "sequence_rate_defined": transitions > 0,
        "projection_count": projections,
        "projection_rate": projections / samples,
        "fallback_count": fallbacks,
        "fallback_rate": fallbacks / samples,
    }


def summarize_repeated_runtime(
    records: Sequence[Mapping[str, Any]],
    *,
    method: str = _CANDIDATE_METHOD,
    expected_repetitions: int | None = None,
    expected_warmup_cycles: int | None = None,
    expected_deadline_us: float = 10_000.0,
) -> dict[str, Any]:
    """Summarize the complete repeated, post-warm-up runtime population."""

    rows = [row for row in records if str(row.get("method")) == method]
    if not rows:
        raise ReportingValidationError(f"runtime table has no rows for {method!r}")
    required = {
        "repetition",
        "warmup_cycles_per_trajectory",
        "dataset_id",
        "session_id",
        "trajectory_id",
        "scenario_id",
        "k",
        "dof",
        "deadline_us",
        "deadline_miss",
        "total_compute_us",
    }
    keys: set[tuple[Any, ...]] = set()
    repetitions: set[int] = set()
    warmups: set[int] = set()
    totals: list[float] = []
    misses: list[bool] = []
    by_repetition: dict[int, list[float]] = defaultdict(list)
    for index, row in enumerate(rows):
        missing = required - set(row)
        if missing:
            raise ReportingValidationError(
                f"runtime row {index} is missing {sorted(missing)}"
            )
        repetition = int(row["repetition"])
        warmup = int(row["warmup_cycles_per_trajectory"])
        if repetition < 0 or warmup < 1:
            raise ReportingValidationError("runtime repetition/warmup is invalid")
        key = (
            repetition,
            str(row["dataset_id"]),
            str(row["session_id"]),
            str(row["trajectory_id"]),
            str(row["scenario_id"]),
            int(row["k"]),
            int(row["dof"]),
        )
        if key in keys:
            raise ReportingValidationError(f"duplicate runtime cycle key {key}")
        keys.add(key)
        deadline = _finite_value(row["deadline_us"], field="runtime.deadline_us")
        if not math.isclose(deadline, expected_deadline_us, rel_tol=0.0, abs_tol=1e-9):
            raise ReportingValidationError(
                f"runtime row is not a 100 Hz deadline: {deadline} us"
            )
        total = _finite_value(row["total_compute_us"], field="runtime.total_compute_us")
        if total < 0.0:
            raise ReportingValidationError("runtime total is negative")
        miss = row["deadline_miss"]
        if not isinstance(miss, bool):
            raise ReportingValidationError("runtime deadline_miss must be boolean")
        if miss != (total > deadline):
            raise ReportingValidationError(
                "runtime deadline_miss disagrees with total_compute_us"
            )
        repetitions.add(repetition)
        warmups.add(warmup)
        totals.append(total)
        misses.append(miss)
        by_repetition[repetition].append(total)
    if expected_repetitions is not None and repetitions != set(
        range(expected_repetitions)
    ):
        raise ReportingValidationError(
            f"runtime repetitions differ: expected {expected_repetitions}, "
            f"observed {sorted(repetitions)}"
        )
    if len(repetitions) < 2:
        raise ReportingValidationError("formal runtime requires repeated runs")
    if len(warmups) != 1:
        raise ReportingValidationError("runtime rows use different warm-up counts")
    warmup = next(iter(warmups))
    if expected_warmup_cycles is not None and warmup != expected_warmup_cycles:
        raise ReportingValidationError(
            f"runtime warm-up differs: expected {expected_warmup_cycles}, got {warmup}"
        )
    values = np.asarray(totals, dtype=float)
    miss_count = int(np.count_nonzero(misses))
    return {
        "method": method,
        "timed_cycle_count": int(values.size),
        "repetition_count": len(repetitions),
        "warmup_cycles_per_trajectory": warmup,
        "deadline_us": expected_deadline_us,
        "total_p99_us": float(np.quantile(values, 0.99, method="linear")),
        "total_max_us": float(np.max(values)),
        "deadline_miss_count": miss_count,
        "deadline_miss_rate": miss_count / values.size,
        "worst_repetition_p99_us": float(
            max(
                np.quantile(group, 0.99, method="linear")
                for group in by_repetition.values()
            )
        ),
    }


def csv_regression_criteria(
    records: Sequence[Mapping[str, Any]],
    *,
    candidate_method: str = _CANDIDATE_METHOD,
) -> list[dict[str, Any]]:
    """Evaluate only the isolated legacy-fixed-grid development regression."""

    legacy = [
        row for row in records if str(row.get("scenario_id")) == "legacy_fixed_grid"
    ]
    if not legacy:
        raise ReportingValidationError("real replay lacks legacy_fixed_grid metrics")
    for row in legacy:
        source_kind = row.get("source_kind")
        if source_kind is not None and str(source_kind) != "real_csv_legacy_fixed_grid":
            raise ReportingValidationError(
                "legacy_fixed_grid regression has a different source_kind"
            )
    baseline = _unique_row(
        legacy,
        field="method",
        value=_CSV_BASELINE_METHOD,
        label="CSV baseline regression",
    )
    candidate = _unique_row(
        legacy,
        field="method",
        value=candidate_method,
        label="CSV candidate regression",
    )
    metric_specs = (
        ("rmse", "position_rmse", 0.035187, 0.02991, "rad", False),
        ("lag", "lag_s", 0.070, 0.030, "s", True),
        (
            "max_error",
            "position_max_abs_error",
            0.184528,
            0.184528,
            "rad",
            False,
        ),
    )
    output = []
    for (
        label,
        metric,
        baseline_reference,
        candidate_limit,
        unit,
        absolute,
    ) in metric_specs:
        baseline_value = _finite_value(
            baseline.get(metric), field=f"CSV baseline {metric}"
        )
        candidate_value = _finite_value(
            candidate.get(metric), field=f"CSV candidate {metric}"
        )
        if absolute:
            baseline_value = abs(baseline_value)
            candidate_value = abs(candidate_value)
        output.append(
            _acceptance_record(
                f"csv_p_only_{label}_reference",
                family="csv_development_regression",
                scope="legacy_fixed_grid_only",
                method=_CSV_BASELINE_METHOD,
                metric=metric,
                source_artifact="real_replay/metrics_by_trajectory.csv",
                observed_value=baseline_value,
                operator="report_only",
                threshold_value=baseline_reference,
                threshold_defined=False,
                required=False,
                failure_stage="information_condition",
                notes=(
                    f"Reference is approximate ({baseline_reference:g} {unit}); "
                    "the protocol declares no pass/fail tolerance."
                ),
            )
        )
        output.append(
            _acceptance_record(
                f"csv_candidate_{label}_target",
                family="csv_development_regression",
                scope="legacy_fixed_grid_only",
                method=candidate_method,
                metric=metric,
                source_artifact="real_replay/metrics_by_trajectory.csv",
                observed_value=candidate_value,
                operator="<=",
                threshold_value=candidate_limit,
                required=True,
                failure_stage="information_condition",
                notes=(
                    f"Strict development regression target in {unit}; this single trace "
                    "is not used for parameter selection."
                ),
            )
        )
    return output


def _verify_candidate_governor_invariants(
    locked: ValidatedBundle,
    *,
    expected_ids: Sequence[str],
    candidate_method: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Independently recompute candidate invariants and compare the saved CSV."""

    all_samples = load_bundle_parquet(locked)
    candidate_samples = [
        row
        for row in all_samples
        if str(row.get("method_id")) == candidate_method
        and str(row.get("split")) == "test"
        and str(row.get("scenario_id")) == "clean"
    ]
    if not candidate_samples:
        raise ReportingValidationError("locked candidate samples are empty")
    try:
        recomputed = governor_invariant_summaries(
            candidate_samples,
            motion_limits=PRIMARY_LIMITS,
        )
    except (DiagnosticValidationError, ValueError) as error:
        raise ReportingValidationError(
            f"candidate governor invariant recomputation failed: {error}"
        ) from error
    saved = load_bundle_csv(
        locked,
        "governor_invariants.csv",
        required_fields={
            "run_id",
            "dataset_id",
            "session_id",
            "trajectory_id",
            "scenario_id",
            "method_id",
            "nonfallback_point_admissible_count",
            "nonfallback_one_step_reachable_count",
            "nonfallback_sequence_consistent_count",
        },
    )
    saved_candidate = [
        row for row in saved if str(row.get("method_id")) == candidate_method
    ]
    try:
        assert_records_close(
            recomputed,
            saved_candidate,
            key_fields=(
                "run_id",
                "dataset_id",
                "session_id",
                "trajectory_id",
                "scenario_id",
                "method_id",
            ),
        )
    except ArtifactValidationError as error:
        raise ReportingValidationError(
            f"saved governor invariants differ from independent recomputation: {error}"
        ) from error
    observed = [str(row["trajectory_id"]) for row in recomputed]
    counts = Counter(observed)
    expected = set(expected_ids)
    if set(observed) != expected or any(count != 1 for count in counts.values()):
        raise ReportingValidationError(
            "candidate governor invariant denominator differs from locked test IDs"
        )
    return [dict(row) for row in all_samples], recomputed


def _constraint_acceptance_summary(
    audit_rows: Sequence[Mapping[str, Any]],
    candidate_samples: Sequence[Mapping[str, Any]],
    *,
    candidate_method: str,
) -> dict[str, Any]:
    candidate_audits = [
        row
        for row in audit_rows
        if str(row.get("method_id")) == candidate_method
        and str(row.get("scenario_id")) == "clean"
    ]
    if not candidate_audits:
        raise ReportingValidationError("candidate continuous constraint audit is empty")

    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            str(row.get("run_id")),
            str(row.get("dataset_id")),
            str(row.get("trajectory_id")),
            str(row.get("scenario_id")),
            str(row.get("joint_id")),
            int(row.get("k", -1)),
        )

    sample_keys = {key(row) for row in candidate_samples}
    audit_keys = [key(row) for row in candidate_audits]
    if len(audit_keys) != len(set(audit_keys)):
        raise ReportingValidationError(
            "candidate constraint audit has duplicate cycles"
        )
    if set(audit_keys) != sample_keys:
        raise ReportingValidationError(
            "candidate constraint audit coverage differs from canonical samples"
        )
    velocity_margins: list[float] = []
    acceleration_margins: list[float] = []
    jerk_margins: list[float] = []
    violation_count = 0
    for index, row in enumerate(candidate_audits):
        velocity_margins.append(
            _finite_value(row.get("velocity_margin"), field=f"audit[{index}].velocity")
        )
        acceleration_margins.append(
            _finite_value(
                row.get("acceleration_margin"),
                field=f"audit[{index}].acceleration",
            )
        )
        jerk_margins.append(
            _finite_value(row.get("jerk_margin"), field=f"audit[{index}].jerk")
        )
        raw_count = row.get("violation_count")
        if (
            isinstance(raw_count, bool)
            or int(raw_count) < 0
            or float(raw_count) != int(raw_count)
        ):
            raise ReportingValidationError("constraint violation_count is invalid")
        violation_count += int(raw_count)
    return {
        "audit_row_count": len(candidate_audits),
        "violation_count": violation_count,
        "velocity_margin_min": min(velocity_margins),
        "acceleration_margin_min": min(acceleration_margins),
        "jerk_margin_min": min(jerk_margins),
    }


def build_fallback_summary(
    samples: Sequence[Mapping[str, Any]],
    event_rows: Sequence[Mapping[str, Any]],
    *,
    methods: Sequence[str] = PRIMARY_METHOD_IDS,
) -> list[dict[str, Any]]:
    """Deduplicate per-joint fallback rows into exact synchronized cycle rates."""

    selected = set(methods)
    if not selected:
        raise ReportingValidationError("fallback summary methods cannot be empty")

    def cycle_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            str(row.get("method_id")),
            str(row.get("run_id")),
            str(row.get("dataset_id")),
            str(row.get("session_id")),
            str(row.get("trajectory_id")),
            str(row.get("scenario_id")),
            int(row.get("k", -1)),
        )

    sample_cycles: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in samples:
        method = str(row.get("method_id"))
        if method in selected and str(row.get("scenario_id")) == "clean":
            sample_cycles[cycle_key(row)].append(row)
    observed_methods = {key[0] for key in sample_cycles}
    if observed_methods != selected:
        raise ReportingValidationError(
            f"fallback sample methods differ: missing={sorted(selected - observed_methods)}"
        )

    fallback_cycles: dict[tuple[Any, ...], str] = {}
    for key, rows in sample_cycles.items():
        flags = {row.get("fallback") for row in rows}
        if flags - {True, False} or len(flags) != 1:
            raise ReportingValidationError(
                f"fallback flag is not synchronized for cycle {key}"
            )
        reasons = {str(row.get("fallback_reason", "")) for row in rows}
        is_fallback = bool(next(iter(flags)))
        if is_fallback:
            if "" in reasons or not reasons:
                raise ReportingValidationError(
                    f"fallback cycle {key} has no explicit reason"
                )
            fallback_cycles[key] = ";".join(sorted(reasons))
        elif reasons != {""}:
            raise ReportingValidationError(
                f"non-fallback cycle {key} carries a fallback reason"
            )

    event_reasons: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for row in event_rows:
        if str(row.get("method_id")) not in selected:
            continue
        key = cycle_key(row)
        reason = str(row.get("fallback_reason", ""))
        if not reason:
            raise ReportingValidationError("fallback event has an empty reason")
        event_reasons[key].add(reason)
    if set(event_reasons) != set(fallback_cycles):
        raise ReportingValidationError(
            "fallback event cycles differ from canonical sample fallback cycles"
        )
    for key, reasons in event_reasons.items():
        if ";".join(sorted(reasons)) != fallback_cycles[key]:
            raise ReportingValidationError(
                f"fallback event reason differs from samples for cycle {key}"
            )

    output = []
    for method in methods:
        method_cycles = [key for key in sample_cycles if key[0] == method]
        total = len(method_cycles)
        if total == 0:
            raise ReportingValidationError(f"fallback denominator is zero for {method}")
        local = {
            key: reason for key, reason in fallback_cycles.items() if key[0] == method
        }
        output.append(
            {
                "method": method,
                "reason": "__all__",
                "fallback_cycle_count": len(local),
                "total_cycle_count": total,
                "fallback_rate": len(local) / total,
                "deduplication_unit": (
                    "run_id,dataset_id,session_id,trajectory_id,scenario_id,k"
                ),
            }
        )
        reason_counts = Counter(local.values())
        for reason, count in sorted(reason_counts.items()):
            output.append(
                {
                    "method": method,
                    "reason": reason,
                    "fallback_cycle_count": count,
                    "total_cycle_count": total,
                    "fallback_rate": count / total,
                    "deduplication_unit": (
                        "run_id,dataset_id,session_id,trajectory_id,scenario_id,k"
                    ),
                }
            )
    return output


def _estimated_acceleration_harmful_summary(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not records:
        raise ReportingValidationError("estimated acceleration PVA/PV table is empty")
    harmful_count = 0
    relative_defined_count = 0
    identities: set[tuple[str, str, str]] = set()
    for index, row in enumerate(records):
        harmful = row.get("acceleration_target_harmful")
        relative_defined = row.get("relative_improvement_defined")
        if not isinstance(harmful, bool) or not isinstance(relative_defined, bool):
            raise ReportingValidationError(
                f"estimated acceleration row {index} has invalid boolean flags"
            )
        identity = (
            str(row.get("dataset_id")),
            str(row.get("session_id")),
            str(row.get("trajectory_id")),
        )
        if identity in identities:
            raise ReportingValidationError(
                f"estimated acceleration table duplicates {identity}"
            )
        identities.add(identity)
        harmful_count += int(harmful)
        relative_defined_count += int(relative_defined)
        _finite_value(
            row.get("pva_vs_pv_absolute_rmse_difference"),
            field="estimated acceleration absolute difference",
        )
    count = len(records)
    return {
        "trajectory_count": count,
        "harmful_count": harmful_count,
        "harmful_rate": harmful_count / count,
        "relative_defined_count": relative_defined_count,
    }


def _candidate_metric_mean(
    records: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    expected_ids: Sequence[str],
    candidate_method: str,
) -> float:
    values = _exact_method_metric(
        records,
        method=candidate_method,
        metric=metric,
        expected_ids=expected_ids,
    )
    return float(np.mean([values[item] for item in sorted(values)]))


def _chirp_evidence_rows(
    records: Sequence[Mapping[str, Any]],
    *,
    candidate_method: str,
) -> list[dict[str, Any]]:
    selected = [
        row
        for row in records
        if str(row.get("method_id")) == candidate_method
        and str(row.get("scenario_id")) == "clean"
    ]
    if not selected:
        raise ReportingValidationError(
            "chirp frequency response has no clean candidate rows"
        )
    identities: set[tuple[str, ...]] = set()
    bands_by_group: dict[tuple[str, ...], tuple[int, set[int]]] = {}
    metric_values = {
        "chirp_max_abs_gain_error": [],
        "chirp_max_abs_phase_delay_s": [],
        "chirp_max_abs_group_delay_s": [],
        "chirp_max_abs_local_delay_s": [],
    }
    trajectories: set[str] = set()
    for index, row in enumerate(selected):
        band_index = int(row["frequency_band_index"])
        band_count = int(row["frequency_band_count"])
        if band_count < 2 or band_index < 0 or band_index >= band_count:
            raise ReportingValidationError(
                f"chirp response row {index} has invalid band identity"
            )
        identity = (
            str(row.get("run_id", "")),
            str(row.get("dataset_id", "")),
            str(row.get("session_id", "")),
            str(row["trajectory_id"]),
            str(row["scenario_id"]),
            str(row["method_id"]),
            str(row["joint_id"]),
            str(band_index),
        )
        if identity in identities:
            raise ReportingValidationError(
                f"chirp frequency response duplicates row identity {identity}"
            )
        identities.add(identity)
        group = identity[:-1]
        observed_count, observed_bands = bands_by_group.setdefault(
            group, (band_count, set())
        )
        if observed_count != band_count:
            raise ReportingValidationError(
                f"chirp frequency response changes band count for {group}"
            )
        observed_bands.add(band_index)
        for field_name in (
            "window_truth_sample_denominator",
            "evaluated_sample_count",
            "local_delay_overlap_denominator",
        ):
            value = int(row[field_name])
            if value <= 0 or float(row[field_name]) != value:
                raise ReportingValidationError(
                    f"chirp response row {index}.{field_name} is not positive"
                )
        overlap = int(row["local_delay_overlap_count"])
        overlap_denominator = int(row["local_delay_overlap_denominator"])
        if overlap <= 0 or overlap > overlap_denominator:
            raise ReportingValidationError(
                f"chirp response row {index} has invalid lag overlap"
            )
        gain = _finite_value(row["gain"], field=f"chirp[{index}].gain")
        if gain < 0.0:
            raise ReportingValidationError("chirp response gain is negative")
        metric_values["chirp_max_abs_gain_error"].append(abs(gain - 1.0))
        for metric, source_field in (
            ("chirp_max_abs_phase_delay_s", "phase_delay_s"),
            ("chirp_max_abs_group_delay_s", "group_delay_s"),
            ("chirp_max_abs_local_delay_s", "local_delay_s"),
        ):
            metric_values[metric].append(
                abs(
                    _finite_value(
                        row[source_field], field=f"chirp[{index}].{source_field}"
                    )
                )
            )
        trajectories.add(str(row["trajectory_id"]))
    for group, (band_count, observed_bands) in bands_by_group.items():
        if observed_bands != set(range(band_count)):
            raise ReportingValidationError(
                f"chirp frequency response has incomplete band coverage for {group}"
            )

    interpretation = {
        "chirp_max_abs_gain_error": "Maximum |gain-1| across candidate chirp bands and joints.",
        "chirp_max_abs_phase_delay_s": "Maximum absolute chirp phase delay; no retrospective threshold.",
        "chirp_max_abs_group_delay_s": "Maximum absolute chirp group delay; no retrospective threshold.",
        "chirp_max_abs_local_delay_s": "Maximum absolute metadata-windowed local delay; no retrospective threshold.",
    }
    return [
        {
            "evidence_id": metric,
            "stage": "follower",
            "source_artifact": "locked_test/chirp_frequency_response.csv",
            "metric": metric,
            "observed_value": float(max(values)),
            "negative_observation": False,
            "trajectory_or_cycle_count": len(trajectories),
            "interpretation": (
                f"{interpretation[metric]} {len(selected)} band-joint rows retained "
                "in summaries/chirp_frequency_response.csv."
            ),
        }
        for metric, values in metric_values.items()
    ]


def _evidence_ledger(
    locked_metrics: Sequence[Mapping[str, Any]],
    *,
    expected_ids: Sequence[str],
    candidate_method: str,
    governor_summary: Mapping[str, Any],
    constraint_summary: Mapping[str, Any],
    acceleration_summary: Mapping[str, Any],
    chirp_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    specifications = (
        (
            "estimator_accuracy",
            "estimator",
            "estimator_p_rmse",
            _candidate_metric_mean(
                locked_metrics,
                metric="estimator_p_rmse",
                expected_ids=expected_ids,
                candidate_method=candidate_method,
            ),
            False,
            "Magnitude is diagnostic; the protocol predeclares no estimator RMSE threshold.",
        ),
        (
            "prediction_accuracy",
            "prediction",
            "prediction_p_rmse",
            _candidate_metric_mean(
                locked_metrics,
                metric="prediction_p_rmse",
                expected_ids=expected_ids,
                candidate_method=candidate_method,
            ),
            False,
            "Correct-future-time prediction magnitude; no retrospective threshold.",
        ),
        (
            "governor_projection",
            "governor",
            "projection_rate",
            float(governor_summary["projection_rate"]),
            bool(governor_summary["projection_count"]),
            "Any projection is negative evidence against the zero-projection criterion.",
        ),
        (
            "follower_continuous_constraints",
            "follower",
            "continuous_violation_count",
            float(constraint_summary["violation_count"]),
            bool(constraint_summary["violation_count"]),
            "Direct-follower continuous audit over every canonical command cycle.",
        ),
        (
            "ideal_plant_command_divergence",
            "plant",
            "plant_position_rmse",
            _candidate_metric_mean(
                locked_metrics,
                metric="plant_position_rmse",
                expected_ids=expected_ids,
                candidate_method=candidate_method,
            ),
            False,
            "Ideal clean-test plant diagnostic; delayed-plant evidence remains in its raw bundle.",
        ),
        (
            "estimated_acceleration_harmful",
            "information_condition",
            "estimated_pva_harmful_rate",
            float(acceleration_summary["harmful_rate"]),
            bool(acceleration_summary["harmful_count"]),
            "Same-future estimated PVA worse than PV; all harmful trajectories are retained.",
        ),
    )
    rows = []
    for evidence_id, stage, metric, value, negative, interpretation in specifications:
        rows.append(
            {
                "evidence_id": evidence_id,
                "stage": stage,
                "source_artifact": (
                    "acceleration/acceleration_estimated_pv_pva_pairs.csv"
                    if stage == "information_condition"
                    else "locked_test/metrics_by_trajectory.csv"
                    if stage in {"estimator", "prediction", "plant"}
                    else "locked_test/governor_invariants.csv"
                    if stage == "governor"
                    else "locked_test/constraint_audit.csv"
                ),
                "metric": metric,
                "observed_value": _finite_value(value, field=evidence_id),
                "negative_observation": bool(negative),
                "trajectory_or_cycle_count": int(
                    acceleration_summary["trajectory_count"]
                    if stage == "information_condition"
                    else len(expected_ids)
                ),
                "interpretation": interpretation,
            }
        )
    rows.extend(_chirp_evidence_rows(chirp_rows, candidate_method=candidate_method))
    if {row["stage"] for row in rows} != {
        "estimator",
        "prediction",
        "governor",
        "follower",
        "plant",
        "information_condition",
    }:
        raise ReportingValidationError("layer evidence ledger is incomplete")
    return rows


def build_acceptance_analysis(
    bundles: Mapping[str, ValidatedBundle],
    statistical_tables: StatisticalTables,
    *,
    candidate_method: str = _CANDIDATE_METHOD,
) -> AcceptanceAnalysis:
    """Build the complete Section 16 acceptance layer from verified raw bundles."""

    locked = _require_bundle(bundles, "locked_test")
    real_replay = _require_bundle(bundles, "real_replay")
    acceleration = _require_bundle(bundles, "acceleration")
    core_diagnostics = build_core_diagnostic_publications(bundles)
    chirp_rows = core_diagnostics["summaries/chirp_frequency_response.csv"]
    expected_ids = statistical_tables.expected_trajectory_ids
    locked_metrics = load_bundle_csv(
        locked,
        "metrics_by_trajectory.csv",
        required_fields={
            "trajectory_id",
            "split",
            "scenario_id",
            "method",
            "position_rmse",
            "position_max_abs_error",
            "lag_s",
        },
    )
    all_locked_samples, recomputed_invariants = _verify_candidate_governor_invariants(
        locked,
        expected_ids=expected_ids,
        candidate_method=candidate_method,
    )
    candidate_samples = [
        row
        for row in all_locked_samples
        if str(row.get("method_id")) == candidate_method
        and str(row.get("split")) == "test"
        and str(row.get("scenario_id")) == "clean"
    ]
    governor = aggregate_governor_acceptance(recomputed_invariants)

    constraint_rows = load_bundle_csv(
        locked,
        "constraint_audit.csv",
        required_fields={
            "run_id",
            "dataset_id",
            "trajectory_id",
            "scenario_id",
            "joint_id",
            "k",
            "method_id",
            "velocity_margin",
            "acceleration_margin",
            "jerk_margin",
            "violation_count",
        },
    )
    constraints = _constraint_acceptance_summary(
        constraint_rows,
        candidate_samples,
        candidate_method=candidate_method,
    )

    fallback_events = load_bundle_csv(
        locked,
        "fallback_events.csv",
        required_fields={
            "run_id",
            "method_id",
            "dataset_id",
            "session_id",
            "trajectory_id",
            "scenario_id",
            "k",
            "fallback_reason",
        },
        allow_empty=True,
    )
    fallback_summary = build_fallback_summary(all_locked_samples, fallback_events)
    candidate_overall = [
        row
        for row in fallback_summary
        if str(row["method"]) == candidate_method and str(row["reason"]) == "__all__"
    ]
    if len(candidate_overall) != 1:
        raise ReportingValidationError("candidate fallback overall row is not unique")
    candidate_fallback = candidate_overall[0]
    if int(candidate_fallback["fallback_cycle_count"]) != int(
        governor["fallback_count"]
    ) or int(candidate_fallback["total_cycle_count"]) != int(governor["sample_count"]):
        raise ReportingValidationError(
            "fallback summary differs from independently recomputed governor counts"
        )

    runtime_rows = load_bundle_csv(
        locked,
        "runtime_repetition_samples.csv",
        required_fields={
            "method",
            "repetition",
            "warmup_cycles_per_trajectory",
            "deadline_us",
            "deadline_miss",
            "total_compute_us",
        },
    )
    resolved = locked.run_manifest.get("resolved_config")
    if not isinstance(resolved, Mapping) or not isinstance(
        resolved.get("runtime"), Mapping
    ):
        raise ReportingValidationError(
            "locked run manifest lacks runtime configuration"
        )
    runtime_config = resolved["runtime"]
    expected_repetitions = int(runtime_config["repetitions"])
    expected_warmup = int(runtime_config["warmup_cycles"])
    runtime = summarize_repeated_runtime(
        runtime_rows,
        method=candidate_method,
        expected_repetitions=expected_repetitions,
        expected_warmup_cycles=expected_warmup,
    )

    real_metrics = load_bundle_csv(
        real_replay,
        "metrics_by_trajectory.csv",
        required_fields={
            "scenario_id",
            "method",
            "position_rmse",
            "lag_s",
            "position_max_abs_error",
        },
    )
    acceleration_pairs = load_bundle_csv(
        acceleration,
        "acceleration_estimated_pv_pva_pairs.csv",
        required_fields={
            "dataset_id",
            "session_id",
            "trajectory_id",
            "pva_vs_pv_absolute_rmse_difference",
            "relative_improvement_defined",
            "acceleration_target_harmful",
        },
    )
    acceleration_summary = _estimated_acceleration_harmful_summary(acceleration_pairs)

    primary = _unique_row(
        statistical_tables.paired_comparisons,
        field="comparison_id",
        value="primary_position:one_step_pva-vs-predicted_p",
        label="primary paired RMSE comparison",
    )
    maximum = _unique_row(
        statistical_tables.paired_comparisons,
        field="comparison_id",
        value="max_error:one_step_pva-vs-predicted_p",
        label="paired maximum-error comparison",
    )
    if int(primary.get("resamples", 0)) != 10_000 or not math.isclose(
        float(primary.get("confidence_level", 0.0)), 0.95, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ReportingValidationError("primary paired inference is not 10k/95%")
    baseline_lag, candidate_lag, lag_difference = _absolute_lag_difference(
        locked_metrics,
        expected_ids=expected_ids,
        candidate_method=candidate_method,
    )

    criteria = [
        _acceptance_record(
            "paired_rmse_relative_improvement_at_least_5pct",
            family="governed_pva_core",
            scope="clean_locked_test_whole_trajectories",
            method=candidate_method,
            metric="relative_improvement",
            source_artifact="statistics/paired_comparisons.csv",
            observed_value=float(primary["relative_improvement"]),
            operator=">=",
            threshold_value=0.05,
            numerator=float(primary["n_trajectories"]),
            denominator=float(primary["n_expected_trajectories"]),
            denominator_defined=True,
            failure_stage="estimator|prediction|information_condition",
            notes="Paired trajectory RMSE; candidate and baseline share estimator, predictor, and H.",
        ),
        _acceptance_record(
            "paired_rmse_95pct_ci_supports_improvement",
            family="governed_pva_core",
            scope="clean_locked_test_whole_trajectories",
            method=candidate_method,
            metric="relative_improvement_ci_low",
            source_artifact="statistics/paired_comparisons.csv",
            observed_value=float(primary["relative_improvement_ci_low"]),
            operator=">",
            threshold_value=0.0,
            numerator=float(primary["n_trajectories"]),
            denominator=float(primary["n_expected_trajectories"]),
            denominator_defined=True,
            failure_stage="estimator|prediction|information_condition",
            notes="Lower endpoint of the predeclared 10,000-resample paired 95% interval.",
        ),
        _acceptance_record(
            "paired_absolute_lag_not_worse",
            family="governed_pva_core",
            scope="clean_locked_test_whole_trajectories",
            method=candidate_method,
            metric="mean_abs_lag_candidate_minus_baseline_s",
            source_artifact="locked_test/metrics_by_trajectory.csv",
            observed_value=lag_difference,
            operator="<=",
            threshold_value=0.0,
            numerator=float(len(expected_ids)),
            denominator=float(len(expected_ids)),
            denominator_defined=True,
            failure_stage="estimator|prediction|information_condition",
            notes=(
                f"Uses absolute signed lag before pairing; baseline mean={baseline_lag:.12g}s, "
                f"candidate mean={candidate_lag:.12g}s."
            ),
        ),
        _acceptance_record(
            "paired_max_error_not_worse",
            family="governed_pva_core",
            scope="clean_locked_test_whole_trajectories",
            method=candidate_method,
            metric="position_max_abs_error_candidate_minus_baseline",
            source_artifact="statistics/paired_comparisons.csv",
            observed_value=float(maximum["absolute_difference"]),
            operator="<=",
            threshold_value=0.0,
            numerator=float(maximum["n_trajectories"]),
            denominator=float(maximum["n_expected_trajectories"]),
            denominator_defined=True,
            failure_stage="estimator|prediction|information_condition",
            notes="Trajectory-paired candidate-minus-baseline maximum absolute error.",
        ),
        _acceptance_record(
            "continuous_velocity_margin_nonnegative",
            family="continuous_constraints",
            scope="every_clean_candidate_command_cycle_and_joint",
            method=candidate_method,
            metric="velocity_margin_min",
            source_artifact="locked_test/constraint_audit.csv",
            observed_value=float(constraints["velocity_margin_min"]),
            operator=">=",
            threshold_value=-1e-8,
            numerator=float(constraints["audit_row_count"]),
            denominator=float(constraints["audit_row_count"]),
            denominator_defined=True,
            failure_stage="governor|follower",
            notes="Exact direct-follower continuous velocity audit; threshold is audit tolerance.",
        ),
        _acceptance_record(
            "continuous_acceleration_margin_nonnegative",
            family="continuous_constraints",
            scope="every_clean_candidate_command_cycle_and_joint",
            method=candidate_method,
            metric="acceleration_margin_min",
            source_artifact="locked_test/constraint_audit.csv",
            observed_value=float(constraints["acceleration_margin_min"]),
            operator=">=",
            threshold_value=-1e-8,
            numerator=float(constraints["audit_row_count"]),
            denominator=float(constraints["audit_row_count"]),
            denominator_defined=True,
            failure_stage="governor|follower",
            notes="Exact direct-follower continuous acceleration audit.",
        ),
        _acceptance_record(
            "continuous_jerk_margin_nonnegative",
            family="continuous_constraints",
            scope="every_clean_candidate_command_cycle_and_joint",
            method=candidate_method,
            metric="jerk_margin_min",
            source_artifact="locked_test/constraint_audit.csv",
            observed_value=float(constraints["jerk_margin_min"]),
            operator=">=",
            threshold_value=-1e-8,
            numerator=float(constraints["audit_row_count"]),
            denominator=float(constraints["audit_row_count"]),
            denominator_defined=True,
            failure_stage="governor|follower",
            notes="Exact direct-follower continuous internal jerk audit.",
        ),
        _acceptance_record(
            "continuous_vaj_violation_count_zero",
            family="continuous_constraints",
            scope="every_clean_candidate_command_cycle_and_joint",
            method=candidate_method,
            metric="violation_count",
            source_artifact="locked_test/constraint_audit.csv",
            observed_value=float(constraints["violation_count"]),
            operator="==",
            threshold_value=0.0,
            numerator=float(constraints["violation_count"]),
            denominator=float(constraints["audit_row_count"]),
            denominator_defined=True,
            failure_stage="governor|follower",
            notes="Combined V/A/internal-J violation count; individual margins are separate rows.",
        ),
        _acceptance_record(
            "nonfallback_point_admissibility_100pct",
            family="executable_target_invariants",
            scope="nonfallback_clean_candidate_targets",
            method=candidate_method,
            metric="nonfallback_point_admissible_rate",
            source_artifact="locked_test/governor_invariants.csv",
            observed_value=float(governor["point_admissible_rate"]),
            operator="rate==",
            threshold_value=1.0,
            numerator=float(governor["point_admissible_count"]),
            denominator=float(governor["nonfallback_sample_count"]),
            denominator_defined=bool(governor["point_rate_defined"]),
            failure_stage="governor",
            notes="Independently recomputed from canonical Parquet and compared with saved CSV.",
        ),
        _acceptance_record(
            "nonfallback_t_free_le_dt_100pct",
            family="executable_target_invariants",
            scope="nonfallback_clean_candidate_targets",
            method=candidate_method,
            metric="nonfallback_one_step_reachable_rate",
            source_artifact="locked_test/governor_invariants.csv",
            observed_value=float(governor["t_free_reachable_rate"]),
            operator="rate==",
            threshold_value=1.0,
            numerator=float(governor["t_free_reachable_count"]),
            denominator=float(governor["nonfallback_sample_count"]),
            denominator_defined=bool(governor["t_free_rate_defined"]),
            failure_stage="governor|follower",
            notes="T_free is the unconstrained frozen solve; every nonfallback value is recorded.",
        ),
        _acceptance_record(
            "nonfallback_sequence_consistency_100pct",
            family="executable_target_invariants",
            scope="adjacent_nonfallback_clean_candidate_targets",
            method=candidate_method,
            metric="nonfallback_sequence_consistency_rate",
            source_artifact="locked_test/governor_invariants.csv",
            observed_value=float(governor["sequence_consistency_rate"]),
            operator="rate==",
            threshold_value=1.0,
            numerator=float(governor["sequence_consistent_count"]),
            denominator=float(governor["sequence_transition_count"]),
            denominator_defined=bool(governor["sequence_rate_defined"]),
            failure_stage="governor",
            notes="Exact adjacent constant-jerk state consistency on nonfallback transitions.",
        ),
        _acceptance_record(
            "projection_rate_zero",
            family="executable_target_invariants",
            scope="all_clean_candidate_cycles",
            method=candidate_method,
            metric="projection_rate",
            source_artifact="locked_test/governor_invariants.csv",
            observed_value=float(governor["projection_rate"]),
            operator="rate==",
            threshold_value=0.0,
            numerator=float(governor["projection_count"]),
            denominator=float(governor["sample_count"]),
            denominator_defined=True,
            failure_stage="governor|follower",
            notes="Projection remains distinct from the proposed governor and from fallback.",
        ),
        _acceptance_record(
            "fallback_rate_reported_separately",
            family="fallback",
            scope="deduplicated_clean_candidate_cycles",
            method=candidate_method,
            metric="fallback_rate",
            source_artifact="summaries/fallback_summary.csv",
            observed_value=float(candidate_fallback["fallback_rate"]),
            operator="report_only",
            threshold_value=0.0,
            threshold_defined=False,
            numerator=float(candidate_fallback["fallback_cycle_count"]),
            denominator=float(candidate_fallback["total_cycle_count"]),
            denominator_defined=True,
            required=False,
            failure_stage="governor|follower",
            notes="Reasons are preserved in per-reason rows; fallback is not silently treated as success.",
        ),
        _acceptance_record(
            "runtime_total_p99_below_1ms",
            family="runtime",
            scope="complete_repeated_post_warmup_100hz_cycles",
            method=candidate_method,
            metric="total_p99_us",
            source_artifact="locked_test/runtime_repetition_samples.csv",
            observed_value=float(runtime["total_p99_us"]),
            operator="<",
            threshold_value=1_000.0,
            numerator=float(runtime["timed_cycle_count"]),
            denominator=float(runtime["timed_cycle_count"]),
            denominator_defined=True,
            failure_stage="estimator|prediction|governor|follower|plant",
            notes=(
                f"Pooled {runtime['repetition_count']} repetitions after "
                f"{runtime['warmup_cycles_per_trajectory']} warm-up cycles per trajectory."
            ),
        ),
        _acceptance_record(
            "runtime_total_max_below_5ms",
            family="runtime",
            scope="complete_repeated_post_warmup_100hz_cycles",
            method=candidate_method,
            metric="total_max_us",
            source_artifact="locked_test/runtime_repetition_samples.csv",
            observed_value=float(runtime["total_max_us"]),
            operator="<",
            threshold_value=5_000.0,
            numerator=float(runtime["timed_cycle_count"]),
            denominator=float(runtime["timed_cycle_count"]),
            denominator_defined=True,
            failure_stage="estimator|prediction|governor|follower|plant",
            notes="Global maximum over the full repeated post-warm-up population.",
        ),
        _acceptance_record(
            "runtime_100hz_deadline_miss_rate_zero",
            family="runtime",
            scope="complete_repeated_post_warmup_100hz_cycles",
            method=candidate_method,
            metric="deadline_miss_rate",
            source_artifact="locked_test/runtime_repetition_samples.csv",
            observed_value=float(runtime["deadline_miss_rate"]),
            operator="rate==",
            threshold_value=0.0,
            numerator=float(runtime["deadline_miss_count"]),
            denominator=float(runtime["timed_cycle_count"]),
            denominator_defined=True,
            failure_stage="estimator|prediction|governor|follower|plant",
            notes="Each stored deadline flag is independently checked against its 10 ms deadline.",
        ),
    ]
    criteria.extend(
        csv_regression_criteria(real_metrics, candidate_method=candidate_method)
    )

    required_rows = [row for row in criteria if bool(row["required"])]
    passed = sum(row["status"] == "pass" for row in required_rows)
    unavailable = any(
        str(row["status"]).startswith("unavailable") for row in required_rows
    )
    failures = any(row["status"] == "fail" for row in required_rows)
    overall_status = "pass" if passed == len(required_rows) else "fail"
    if unavailable and not failures:
        overall_status = "unavailable_zero_denominator"
    criteria.append(
        _acceptance_record(
            "section16_overall_required_pass_rate",
            family="overall",
            scope="all_predeclared_strict_section16_criteria",
            method=candidate_method,
            metric="required_criteria_pass_rate",
            source_artifact="summaries/acceptance_criteria.csv",
            observed_value=passed / len(required_rows),
            operator="rate==",
            threshold_value=1.0,
            numerator=float(passed),
            denominator=float(len(required_rows)),
            denominator_defined=True,
            failure_stage="see_failed_rows",
            notes="Informational roll-up; every component row remains authoritative.",
            status=overall_status,
        )
    )
    if len({str(row["criterion_id"]) for row in criteria}) != len(criteria):
        raise ReportingValidationError("acceptance criteria contain duplicate IDs")

    evidence = _evidence_ledger(
        locked_metrics,
        expected_ids=expected_ids,
        candidate_method=candidate_method,
        governor_summary=governor,
        constraint_summary=constraints,
        acceleration_summary=acceleration_summary,
        chirp_rows=chirp_rows,
    )
    return AcceptanceAnalysis(
        criteria,
        fallback_summary,
        evidence,
        core_diagnostics=core_diagnostics,
    )


def build_failure_analysis(
    bundles: Mapping[str, ValidatedBundle],
    *,
    acceptance: AcceptanceAnalysis | None = None,
) -> FailureAnalysis:
    """Summarize failures/fallbacks without copying unbounded event logs."""

    summary: list[dict[str, Any]] = []
    failure_types: list[dict[str, Any]] = []
    for name, bundle in sorted(bundles.items()):
        failures = load_bundle_csv(bundle, "failures.csv", allow_empty=True)
        fallback_path = bundle.root / "fallback_events.csv"
        fallbacks = (
            load_bundle_csv(bundle, "fallback_events.csv", allow_empty=True)
            if fallback_path.is_file()
            else []
        )
        attempted = bundle.data_manifest.get("attempted_trajectory_runs")
        successful = bundle.data_manifest.get("successful_trajectory_runs")
        if isinstance(attempted, bool) or not isinstance(attempted, int):
            attempted_value: int | str = "unavailable"
            successful_value: int | str = "unavailable"
            completion: float | str = "unavailable"
        else:
            attempted_value = attempted
            successful_value = int(successful) if isinstance(successful, int) else 0
            completion = successful_value / attempted_value if attempted_value else 0.0
        summary.append(
            {
                "bundle": name,
                "run_id": str(bundle.run_manifest["run_id"]),
                "attempted_trajectory_runs": attempted_value,
                "successful_trajectory_runs": successful_value,
                "failure_event_count": len(failures),
                "fallback_event_count": len(fallbacks),
                "completion_rate": completion,
                "status": "no_failures" if not failures else "failures_observed",
            }
        )
        counts = Counter(
            (
                str(row.get("failure_type", "unclassified")),
                str(row.get("reason", "unavailable")),
            )
            for row in failures
        )
        if not counts:
            failure_types.append(
                {
                    "bundle": name,
                    "failure_type": "none",
                    "reason": "none",
                    "count": 0,
                }
            )
        else:
            for (failure_type, reason), count in sorted(counts.items()):
                failure_types.append(
                    {
                        "bundle": name,
                        "failure_type": failure_type,
                        "reason": reason,
                        "count": count,
                    }
                )
    lines = [
        "# Failure and fallback analysis",
        "",
        "This file is a generated technical status artifact. Failed trajectory runs "
        "remain in the completion denominator and are excluded from numeric metric "
        "tables; locked-test paired inference requires the complete predeclared set.",
        "",
        "| bundle | failures | fallbacks | completion | status |",
        "|---|---:|---:|---:|---|",
    ]
    for row in summary:
        completion = row["completion_rate"]
        completion_text = (
            f"{100.0 * float(completion):.3f}%"
            if isinstance(completion, (int, float))
            else str(completion)
        )
        lines.append(
            f"| {row['bundle']} | {row['failure_event_count']} | "
            f"{row['fallback_event_count']} | {completion_text} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "Detailed bounded counts are in `summaries/failure_type_counts.csv`; raw "
            "event rows remain in their independently hashed run bundles.",
            "",
        ]
    )
    if acceptance is not None:
        component_rows = [
            row
            for row in acceptance.criteria
            if bool(row["required"])
            and row["criterion_id"] != "section16_overall_required_pass_rate"
        ]
        failed = [row for row in component_rows if row["status"] != "pass"]
        lines.extend(
            [
                "## Scientific acceptance failures",
                "",
                (
                    f"Required Section 16 component criteria: {len(component_rows)}; "
                    f"passed: {len(component_rows) - len(failed)}; "
                    f"failed or unavailable: {len(failed)}."
                ),
                "",
            ]
        )
        if failed:
            lines.extend(
                [
                    "| criterion | observed | operator | threshold | status | attribution |",
                    "|---|---:|---|---:|---|---|",
                ]
            )
            for row in failed:
                attribution = str(row["failure_stage"]).replace("|", "/")
                lines.append(
                    f"| {row['criterion_id']} | {float(row['observed_value']):.12g} | "
                    f"{row['operator']} | {float(row['threshold_value']):.12g} | "
                    f"{row['status']} | {attribution} |"
                )
        else:
            lines.append("All strict Section 16 component criteria passed.")
        lines.extend(
            [
                "",
                "The attribution column is a bounded technical localization, not a causal "
                "claim. Layer evidence below preserves competing explanations.",
                "",
                "## Deduplicated fallback results",
                "",
                "| method | reason | fallback cycles | total cycles | rate |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for row in acceptance.fallback_summary:
            if row["reason"] != "__all__" and not row["fallback_cycle_count"]:
                continue
            reason = str(row["reason"]).replace("|", "/")
            lines.append(
                f"| {row['method']} | {reason} | {row['fallback_cycle_count']} | "
                f"{row['total_cycle_count']} | {float(row['fallback_rate']):.12g} |"
            )
        lines.extend(
            [
                "",
                "## Layered evidence ledger",
                "",
                "| stage | metric | observed | negative evidence | interpretation |",
                "|---|---|---:|---|---|",
            ]
        )
        for row in acceptance.evidence_ledger:
            interpretation = str(row["interpretation"]).replace("|", "/")
            lines.append(
                f"| {row['stage']} | {row['metric']} | "
                f"{float(row['observed_value']):.12g} | "
                f"{row['negative_observation']} | {interpretation} |"
            )
        lines.extend(
            [
                "",
                "Machine-readable sources are `summaries/acceptance_criteria.csv`, "
                "`summaries/fallback_summary.csv`, and "
                "`summaries/evidence_ledger.csv`.",
                "",
            ]
        )
    return FailureAnalysis(summary, failure_types, "\n".join(lines))


def technical_readme(
    *,
    protocol_version: str = "v1",
    bundle_count: int,
    expected_test_trajectory_count: int,
    ranking_method: str,
    comparison_count: int,
    ci_count: int,
    acceptance_required_count: int,
    acceptance_failure_count: int,
) -> str:
    """Return a concise technical index, not manuscript narrative."""

    if re.fullmatch(r"v[1-9][0-9]*", protocol_version) is None:
        raise ReportingValidationError(
            f"invalid evidence protocol version {protocol_version!r}"
        )

    return "\n".join(
        [
            f"# Paper evidence {protocol_version}: technical artifact index",
            "",
            "This directory contains bounded, generated evidence artifacts. Raw run "
            "bundles are intentionally external to the committed result layer and are "
            "referenced by SHA-256 through `artifact_index.json`.",
            "",
            "## Validation contract",
            "",
            f"- Independently verified raw bundles: {bundle_count}",
            "- Bundle checks: run manifest, schema hooks, artifact index coverage, "
            "SHA-256 registry, CSV row counts, and independent metric recomputation",
            f"- Locked-test denominator: {expected_test_trajectory_count} whole trajectories",
            "- Paired bootstrap: 10,000 trajectory resamples; candidate minus baseline; "
            "Holm adjustment over the predeclared secondary family",
            "- Incomplete pairs: rejected in formal mode; no complete-case deletion",
            f"- Representative trace ranking method: `{ranking_method}`",
            f"- Strict Section 16 criteria: {acceptance_required_count}; "
            f"failed or unavailable: {acceptance_failure_count}",
            "",
            "## Artifact layout",
            "",
            "- `summaries/`: bounded figure inputs, acceptance, fallback, layer evidence, "
            "frequency/event diagnostics, and QA",
            f"- `statistics/`: {comparison_count} paired comparisons and {ci_count} method intervals",
            f"- `figures/`: {len(REQUIRED_FIGURE_CATEGORIES)} deterministic PNG/SVG categories",
            "- `manifests/`: raw validation inventory, chart map, and statistical design",
            "- `FAILURE_ANALYSIS.md`: completion/failure/fallback status",
            "- `artifact_index.json`: SHA-256 inventory and raw-bundle roots of trust",
            "- `artifact_index.sha256`: digest of the root index",
            "",
            "All physical tracking plots preserve `target[k] -> output[k+1]` timing; "
            "offline oracle studies remain separately labelled in their raw bundles.",
            "",
        ]
    )


def protocol_hash_text(protocol_path: str | Path) -> str:
    """Return the exact protocol provenance line published with a result tree."""

    path = Path(protocol_path).resolve()
    if not path.is_file():
        raise ReportingValidationError(f"experiment protocol is missing: {path}")
    return f"{sha256_file(path)}  {path.name}\n"


def _chart_map() -> dict[str, Any]:
    chart_specs = {
        "estimator_accuracy_latency_compute_pareto": (
            "Accuracy versus posterior latency and estimator compute",
            "relationship/scatter",
            "estimator",
        ),
        "prediction_error_vs_horizon": (
            "Future-position error across configured physical horizons",
            "trend/ordered line",
            "prediction",
        ),
        "same_information_p_pv_pva_ablation": (
            "Tracking error across same-information target-state methods",
            "comparison/bar with IQR",
            "ablation",
        ),
        "acceleration_value_phase_map": (
            "Next-cycle (H=10 ms) PVA versus PV improvement across independent acceleration/jerk ratios",
            "matrix/heatmap",
            "acceleration_phase",
        ),
        "governor_distortion_reachability": (
            "Governor distortion versus one-cycle reachability",
            "relationship/scatter",
            "governor",
        ),
        "direct_governor_vs_governor_ruckig": (
            "Paired direct-execution versus Ruckig follower tracking",
            "comparison/paired slope",
            "follower",
        ),
        "robustness_matrix": (
            "Tracking error by fixed stress scenario and method",
            "matrix/heatmap",
            "robustness",
        ),
        "sampling_rate_study": (
            "Tracking error after independent truth resampling",
            "trend/ordered line",
            "sampling_rate",
        ),
        "continuous_vs_sampled_jerk": (
            "Maximum sampled, reported, and internally audited jerk",
            "comparison/grouped bar",
            "constraints",
        ),
        "multidof_scalability": (
            "P99 in-memory cycle compute by synchronized DoF",
            "trend/ordered line",
            "scalability",
        ),
        "plant_feedback_comparison": (
            "Tracking error by transparent plant and feedback mode",
            "comparison/grouped bar",
            "plant",
        ),
        "runtime_distributions": (
            "Post-warm-up in-memory cycle compute distributions",
            "distribution/ECDF",
            "runtime_samples",
        ),
        "paired_improvement_confidence_intervals": (
            "Trajectory-paired relative improvements with bootstrap intervals",
            "uncertainty/dot interval",
            "paired",
        ),
        "representative_traces": (
            "Predeclared median, P90, worst, and optional fixed traces",
            "trend/multi-panel line",
            "trace_samples",
        ),
    }
    return {
        "schema_version": CHART_MAP_SCHEMA_VERSION,
        "surface": "deterministic_static_png_svg",
        "palette_policy": "explicit fixed palette with line/marker distinctions",
        "charts": [
            {
                "category": category,
                "analytical_question": values[0],
                "family_and_type": values[1],
                "source_table": values[2],
                "fields": list(FIGURE_TABLE_SCHEMAS[values[2]]),
                "output": [f"figures/{category}.png", f"figures/{category}.svg"],
            }
            for category, values in chart_specs.items()
        ],
    }


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _media_type(path: Path) -> str:
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".png": "image/png",
        ".svg": "image/svg+xml",
    }.get(path.suffix.lower(), "application/octet-stream")


def _role(path: Path) -> str:
    if path.parts[0] == "statistics":
        return "statistical_result"
    if path.parts[0] == "figures":
        return "figure_manifest" if path.suffix == ".json" else "figure"
    if path.parts[0] == "manifests":
        return "provenance_manifest"
    if path.parts[0] == "summaries":
        return "bounded_summary"
    if path.name == "README.md":
        return "technical_readme"
    if path.name == "FAILURE_ANALYSIS.md":
        return "failure_analysis"
    if path.name == "protocol_hash.txt":
        return "protocol_hash"
    return "technical_artifact"


def write_root_artifact_index(
    root: str | Path,
    artifact_paths: Sequence[str | Path],
    *,
    git_commit: str,
    reporting_git_commit: str | None = None,
    raw_bundle_roots: Sequence[Mapping[str, Any]],
    generation_command: Sequence[str] = (),
) -> tuple[Path, Path]:
    """Write the SHA-256 root inventory and its non-circular sidecar digest."""

    root_path = Path(root).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", git_commit):
        raise ReportingValidationError(f"invalid git commit {git_commit!r}")
    reporting_commit = reporting_git_commit or git_commit
    if not re.fullmatch(r"[0-9a-f]{40}", reporting_commit):
        raise ReportingValidationError(
            f"invalid reporting git commit {reporting_commit!r}"
        )
    records = []
    seen: set[str] = set()
    for item in artifact_paths:
        target = Path(item).resolve()
        try:
            relative = target.relative_to(root_path)
        except ValueError as error:
            raise ReportingValidationError(
                f"indexed artifact is outside result root: {target}"
            ) from error
        relative_text = relative.as_posix()
        if relative_text in {"artifact_index.json", "artifact_index.sha256"}:
            raise ReportingValidationError("root index cannot recursively index itself")
        if relative_text in seen:
            raise ReportingValidationError(
                f"duplicate root artifact path {relative_text!r}"
            )
        seen.add(relative_text)
        if not target.is_file() or target.stat().st_size == 0:
            raise ReportingValidationError(
                f"root artifact is missing or empty: {relative_text}"
            )
        records.append(
            {
                "path": relative_text,
                "role": _role(relative),
                "media_type": _media_type(target),
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    if not records:
        raise ReportingValidationError("root artifact index would be empty")
    external = []
    for index, raw in enumerate(raw_bundle_roots):
        required = {
            "bundle",
            "uri",
            "run_id",
            "git_commit",
            "artifact_index_sha256",
            "artifact_index_bytes",
            "generation_command",
        }
        missing = required - set(raw)
        if missing:
            raise ReportingValidationError(
                f"raw bundle root {index} is missing {sorted(missing)}"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", str(raw["artifact_index_sha256"])):
            raise ReportingValidationError(
                f"raw bundle root {index} has invalid artifact-index SHA-256"
            )
        if not str(raw["bundle"]) or not str(raw["uri"]) or not str(raw["run_id"]):
            raise ReportingValidationError(
                f"raw bundle root {index} has empty identity/provenance"
            )
        if str(raw["git_commit"]) != git_commit:
            raise ReportingValidationError(
                f"raw bundle root {index} commit differs from final index commit"
            )
        byte_count = raw["artifact_index_bytes"]
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count <= 0
        ):
            raise ReportingValidationError(
                f"raw bundle root {index} has invalid artifact-index byte size"
            )
        command = raw["generation_command"]
        if (
            not isinstance(command, Sequence)
            or isinstance(command, (str, bytes))
            or not command
            or any(not isinstance(item, str) or not item for item in command)
        ):
            raise ReportingValidationError(
                f"raw bundle root {index} has invalid generation command"
            )
        external.append(dict(raw))
    payload = {
        "schema_version": ROOT_INDEX_SCHEMA_VERSION,
        "git_commit": git_commit,
        "raw_run_git_commit": git_commit,
        "reporting_git_commit": reporting_commit,
        "generation_command": list(generation_command),
        "artifacts": sorted(records, key=lambda row: row["path"]),
        "raw_bundle_roots": sorted(external, key=lambda row: str(row["bundle"])),
        "root_of_trust": {
            "index": "artifact_index.json",
            "digest_sidecar": "artifact_index.sha256",
            "algorithm": "sha256",
            "self_hash_excluded_to_avoid_recursion": True,
        },
    }
    index_path = write_json(root_path / "artifact_index.json", payload)
    digest = sha256_file(index_path)
    sidecar = _write_text(
        root_path / "artifact_index.sha256", f"{digest}  artifact_index.json\n"
    )
    return index_path, sidecar


def validate_root_artifact_index(
    root: str | Path, *, expected_commit: str | None = None
) -> dict[str, Any]:
    """Independently verify the bounded final layer and root digest sidecar."""

    root_path = Path(root).resolve()
    index_path = root_path / "artifact_index.json"
    sidecar_path = root_path / "artifact_index.sha256"
    payload = read_json(index_path)
    if payload.get("schema_version") != ROOT_INDEX_SCHEMA_VERSION:
        raise ReportingValidationError("invalid final root-index schema")
    commit = str(payload.get("git_commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReportingValidationError("final root index has invalid git commit")
    if expected_commit is not None and commit != expected_commit:
        raise ReportingValidationError(
            f"final root-index commit mismatch: expected {expected_commit}, got {commit}"
        )
    if str(payload.get("raw_run_git_commit", commit)) != commit:
        raise ReportingValidationError("final root-index raw-run commit differs")
    reporting_commit = str(payload.get("reporting_git_commit", commit))
    if not re.fullmatch(r"[0-9a-f]{40}", reporting_commit):
        raise ReportingValidationError(
            "final root index has invalid reporting git commit"
        )
    records = payload.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ReportingValidationError("final root index has no bounded artifacts")
    declared: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ReportingValidationError(f"final artifact {index} is not an object")
        relative_text = record.get("path")
        if not isinstance(relative_text, str):
            raise ReportingValidationError(f"final artifact {index} has no path")
        relative = _safe_relative_path(relative_text)
        if relative_text in declared:
            raise ReportingValidationError(
                f"duplicate final artifact path {relative_text!r}"
            )
        declared.add(relative_text)
        target = root_path / relative
        if not target.is_file():
            raise ReportingValidationError(
                f"indexed final artifact is missing: {relative_text}"
            )
        if target.stat().st_size != record.get("bytes"):
            raise ReportingValidationError(
                f"indexed final artifact size differs: {relative_text}"
            )
        digest = record.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReportingValidationError(
                f"indexed final artifact SHA-256 is invalid: {relative_text}"
            )
        if sha256_file(target) != digest:
            raise ReportingValidationError(
                f"indexed final artifact hash differs: {relative_text}"
            )
        if not record.get("role") or not record.get("media_type"):
            raise ReportingValidationError(
                f"indexed final artifact provenance is incomplete: {relative_text}"
            )
    managed_files: set[str] = set()
    for name in ("summaries", "statistics", "figures", "manifests"):
        directory = root_path / name
        if not directory.is_dir():
            raise ReportingValidationError(
                f"managed result directory is missing: {name}"
            )
        managed_files.update(
            path.relative_to(root_path).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        )
    for name in ("README.md", "FAILURE_ANALYSIS.md", "protocol_hash.txt"):
        if (root_path / name).is_file():
            managed_files.add(name)
    if declared != managed_files:
        raise ReportingValidationError(
            "final root-index coverage differs from bounded files: "
            f"unindexed={sorted(managed_files - declared)}, "
            f"missing={sorted(declared - managed_files)}"
        )
    roots = payload.get("raw_bundle_roots")
    if not isinstance(roots, list) or not roots:
        raise ReportingValidationError("final root index has no raw-bundle roots")
    bundle_names = [str(record.get("bundle", "")) for record in roots]
    if any(not name for name in bundle_names) or len(set(bundle_names)) != len(
        bundle_names
    ):
        raise ReportingValidationError(
            "raw-bundle root identities are empty/duplicated"
        )
    for record in roots:
        if str(record.get("git_commit")) != commit:
            raise ReportingValidationError("raw-bundle root commit differs")
        if not re.fullmatch(
            r"[0-9a-f]{64}", str(record.get("artifact_index_sha256", ""))
        ):
            raise ReportingValidationError("raw-bundle root SHA-256 is invalid")
    if not sidecar_path.is_file():
        raise ReportingValidationError("final root-index SHA-256 sidecar is missing")
    sidecar_parts = sidecar_path.read_text(encoding="utf-8").split()
    if sidecar_parts != [sha256_file(index_path), "artifact_index.json"]:
        raise ReportingValidationError("final root-index SHA-256 sidecar differs")
    return {
        "git_commit": commit,
        "raw_run_git_commit": commit,
        "reporting_git_commit": reporting_commit,
        "artifact_count": len(records),
        "raw_bundle_count": len(roots),
        "artifact_index_sha256": sha256_file(index_path),
    }


def _portable_raw_uri(bundle: ValidatedBundle, logical_output_root: Path) -> str:
    index_path = (bundle.root / "artifact_index.json").resolve()
    try:
        return index_path.relative_to(logical_output_root.resolve()).as_posix()
    except ValueError:
        return f"raw-bundle://{bundle.name}/artifact_index.json"


def _raw_roots(
    bundles: Mapping[str, ValidatedBundle], logical_output_root: Path
) -> list[dict[str, Any]]:
    roots = []
    for name, bundle in sorted(bundles.items()):
        index_path = bundle.root / "artifact_index.json"
        roots.append(
            {
                "bundle": name,
                "uri": _portable_raw_uri(bundle, logical_output_root),
                "run_id": str(bundle.run_manifest["run_id"]),
                "git_commit": str(bundle.run_manifest["git_commit"]),
                "artifact_index_sha256": sha256_file(index_path),
                "artifact_index_bytes": index_path.stat().st_size,
                "generation_command": list(bundle.run_manifest["command"]),
            }
        )
    return roots


def _bundle_validation_manifest(
    bundles: Mapping[str, ValidatedBundle],
) -> dict[str, Any]:
    return {
        "schema_version": RAW_VALIDATION_SCHEMA_VERSION,
        "validation_steps": [
            "run_manifest",
            "artifact_index_coverage",
            "schema_hooks",
            "sha256_registry",
            "csv_row_counts",
            "independent_sample_feasibility_recomputation",
            "independent_trajectory_and_summary_recomputation",
        ],
        "bundles": [
            {
                "bundle": name,
                **dict(bundle.validation),
                "git_worktree_dirty": bool(bundle.run_manifest["git_worktree_dirty"]),
                "raw_artifact_index_sha256": sha256_file(
                    bundle.root / "artifact_index.json"
                ),
            }
            for name, bundle in sorted(bundles.items())
        ],
    }


def _write_final_tree(
    staging: Path,
    *,
    logical_output_root: Path,
    bundles: Mapping[str, ValidatedBundle],
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    statistical_tables: StatisticalTables,
    acceptance: AcceptanceAnalysis,
    comparisons: Sequence[Mapping[str, Any]],
    ci_metrics: Sequence[str],
    ranking_method: str,
    predefined_trace_ids: Sequence[str],
    generation_command: Sequence[str],
    reporting_git_commit: str | None,
    protocol_version: str,
    protocol_path: Path,
) -> dict[str, Any]:
    summaries = staging / "summaries"
    statistics = staging / "statistics"
    figures = staging / "figures"
    manifests = staging / "manifests"
    for directory in (summaries, statistics, figures, manifests):
        directory.mkdir(parents=True, exist_ok=False)

    written: list[Path] = []
    for name in FIGURE_TABLE_SCHEMAS:
        path = write_csv(
            summaries / f"figure_input_{name}.csv",
            list(tables[name]),
            fieldnames=FIGURE_TABLE_SCHEMAS[name],
        )
        written.append(path)
    expected_diagnostics = set(CORE_DIAGNOSTIC_PUBLICATIONS.values())
    if set(acceptance.core_diagnostics) != expected_diagnostics:
        raise ReportingValidationError(
            "final core-diagnostic tables differ from the publication mapping"
        )
    for relative_path in sorted(acceptance.core_diagnostics):
        relative = _safe_relative_path(relative_path)
        if relative.parts[0] != "summaries":
            raise ReportingValidationError(
                f"core diagnostic destination escapes summaries: {relative_path}"
            )
        path = write_csv(
            staging / relative,
            acceptance.core_diagnostics[relative_path],
        )
        written.append(path)
    paired_path = write_csv(
        statistics / "paired_comparisons.csv",
        statistical_tables.paired_comparisons,
        allowed_missing_fields={
            "effect_size",
            "effect_size_ci_low",
            "effect_size_ci_high",
            "relative_difference",
            "relative_ci_low",
            "relative_ci_high",
            "relative_improvement",
            "relative_improvement_ci_low",
            "relative_improvement_ci_high",
        },
    )
    confidence_path = write_csv(
        statistics / "confidence_intervals.csv",
        statistical_tables.confidence_intervals,
    )
    stratified_path = write_csv(
        statistics / "stratified_comparisons.csv",
        statistical_tables.stratified_comparisons,
        allowed_missing_fields={
            "relative_improvement",
            "relative_improvement_ci_low",
            "relative_improvement_ci_high",
        },
    )
    effects_path = write_csv(
        statistics / "stratum_effects.csv",
        statistical_tables.stratum_effects,
        allowed_missing_fields={
            "effect_size",
            "effect_size_ci_low",
            "effect_size_ci_high",
            "relative_difference",
            "relative_ci_low",
            "relative_ci_high",
            "relative_improvement",
            "relative_improvement_ci_low",
            "relative_improvement_ci_high",
        },
    )
    heterogeneity_path = write_csv(
        statistics / "heterogeneity.csv", statistical_tables.heterogeneity
    )
    outcomes_path = write_csv(
        statistics / "trajectory_outcome_summary.csv",
        statistical_tables.trajectory_outcome_summary,
    )
    worst_path = write_csv(
        statistics / "worst_trajectories.csv",
        statistical_tables.worst_trajectories,
    )
    completeness_path = write_csv(
        statistics / "denominator_completeness.csv",
        statistical_tables.completeness,
    )
    status_path = write_csv(
        statistics / "inference_status.csv", statistical_tables.inference_status
    )
    written.extend(
        (
            paired_path,
            confidence_path,
            stratified_path,
            effects_path,
            heterogeneity_path,
            outcomes_path,
            worst_path,
            completeness_path,
            status_path,
        )
    )

    acceptance_path = write_csv(
        summaries / "acceptance_criteria.csv",
        acceptance.criteria,
        fieldnames=_ACCEPTANCE_FIELDS,
    )
    fallback_path = write_csv(
        summaries / "fallback_summary.csv", acceptance.fallback_summary
    )
    evidence_path = write_csv(
        summaries / "evidence_ledger.csv", acceptance.evidence_ledger
    )
    written.extend((acceptance_path, fallback_path, evidence_path))

    figure_manifest = generate_final_figures(
        tables,
        figures,
        ranking_method=ranking_method,
        predefined_trace_ids=predefined_trace_ids,
    )
    written.extend(path for path in figures.rglob("*") if path.is_file())

    failure_analysis = build_failure_analysis(bundles, acceptance=acceptance)
    failure_summary_path = write_csv(
        summaries / "failure_summary.csv", failure_analysis.summary
    )
    failure_type_path = write_csv(
        summaries / "failure_type_counts.csv", failure_analysis.failure_types
    )
    failure_markdown_path = _write_text(
        staging / "FAILURE_ANALYSIS.md", failure_analysis.markdown
    )
    written.extend((failure_summary_path, failure_type_path, failure_markdown_path))

    validation_manifest = _bundle_validation_manifest(bundles)
    validation_path = write_json(
        manifests / "raw_bundle_validation.json", validation_manifest
    )
    validation_table_path = write_csv(
        summaries / "bundle_validation.csv",
        [
            {
                "bundle": row["bundle"],
                "run_id": row["run_id"],
                "git_commit": row["git_commit"],
                "artifact_count": row["artifact_count"],
                "checksums_verified": row["checksums_verified"],
                "recomputation_verified": row["recomputation_verified"],
                "feasibility_recomputation_verified": row.get(
                    "feasibility_recomputation_verified", False
                ),
                "git_worktree_dirty": row["git_worktree_dirty"],
                "raw_artifact_index_sha256": row["raw_artifact_index_sha256"],
            }
            for row in validation_manifest["bundles"]
        ],
    )
    chart_map_path = write_json(manifests / "chart_map.json", _chart_map())
    statistical_design_path = write_json(
        manifests / "statistical_design.json",
        {
            "schema_version": STATISTICAL_DESIGN_SCHEMA_VERSION,
            "unit": "whole_trajectory",
            "split": "test",
            "scenario": "clean",
            "expected_trajectory_count": len(
                statistical_tables.expected_trajectory_ids
            ),
            "expected_trajectory_ids": list(statistical_tables.expected_trajectory_ids),
            "resamples": 10_000,
            "confidence_level": 0.95,
            "seed": 20260721,
            "paired_difference_direction": "candidate_minus_baseline",
            "incomplete_pair_policy": "reject_no_complete_case_deletion",
            "stratification_fields": list(DEFAULT_STRATIFICATION_FIELDS),
            "stratified_resampling": "independent_within_stratum_fixed_observed_weights",
            "negative_result_policy": "retain_all_harmful_strata_and_trajectories",
            "secondary_multiplicity": "Holm family-wise adjustment",
            "comparisons": [dict(item) for item in comparisons],
            "ci_metrics": list(ci_metrics),
        },
    )
    written.extend(
        (
            validation_path,
            validation_table_path,
            chart_map_path,
            statistical_design_path,
        )
    )

    readme_path = _write_text(
        staging / "README.md",
        technical_readme(
            protocol_version=protocol_version,
            bundle_count=len(bundles),
            expected_test_trajectory_count=len(
                statistical_tables.expected_trajectory_ids
            ),
            ranking_method=ranking_method,
            comparison_count=len(statistical_tables.paired_comparisons),
            ci_count=len(statistical_tables.confidence_intervals),
            acceptance_required_count=sum(
                bool(row["required"])
                and row["criterion_id"] != "section16_overall_required_pass_rate"
                for row in acceptance.criteria
            ),
            acceptance_failure_count=sum(
                bool(row["required"])
                and row["criterion_id"] != "section16_overall_required_pass_rate"
                and row["status"] != "pass"
                for row in acceptance.criteria
            ),
        ),
    )
    protocol_hash_path = _write_text(
        staging / "protocol_hash.txt",
        protocol_hash_text(protocol_path),
    )
    written.extend((readme_path, protocol_hash_path))
    commits = {str(bundle.run_manifest["git_commit"]) for bundle in bundles.values()}
    if len(commits) != 1:
        raise ReportingValidationError("final tree cannot mix raw-bundle commits")
    root_index, sidecar = write_root_artifact_index(
        staging,
        written,
        git_commit=next(iter(commits)),
        reporting_git_commit=reporting_git_commit,
        raw_bundle_roots=_raw_roots(bundles, logical_output_root),
        generation_command=generation_command,
    )
    final_validation = validate_root_artifact_index(
        staging, expected_commit=next(iter(commits))
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "output_root": str(logical_output_root),
        "raw_bundle_count": len(bundles),
        "expected_test_trajectory_count": len(
            statistical_tables.expected_trajectory_ids
        ),
        "paired_comparison_count": len(statistical_tables.paired_comparisons),
        "confidence_interval_count": len(statistical_tables.confidence_intervals),
        "acceptance_criterion_count": len(acceptance.criteria),
        "acceptance_required_failure_count": sum(
            bool(row["required"])
            and row["criterion_id"] != "section16_overall_required_pass_rate"
            and row["status"] != "pass"
            for row in acceptance.criteria
        ),
        "figure_category_count": len(figure_manifest["categories"]),
        "root_artifact_count": final_validation["artifact_count"],
        "raw_run_git_commit": final_validation["raw_run_git_commit"],
        "reporting_git_commit": final_validation["reporting_git_commit"],
        "artifact_index_sha256": final_validation["artifact_index_sha256"],
        "artifact_index": str(logical_output_root / root_index.name),
        "artifact_index_sidecar": str(logical_output_root / sidecar.name),
    }


def build_final_result_artifacts(
    raw_root: str | Path,
    output_root: str | Path,
    *,
    required_bundles: Sequence[str] = DEFAULT_RAW_BUNDLES,
    expected_commit: str | None = None,
    reporting_git_commit: str | None = None,
    comparisons: Sequence[Mapping[str, Any]] = DEFAULT_COMPARISONS,
    ci_metrics: Sequence[str] = DEFAULT_CI_METRICS,
    ci_methods: Sequence[str] | None = None,
    ranking_method: str = "one_step_governed_pva_direct",
    predefined_trace_ids: Sequence[str] = (),
    expected_test_count: int = 120,
    maximum_runtime_rows_per_method: int = 2_000,
    maximum_trace_rows_per_joint: int = 2_000,
    generation_command: Sequence[str] = (),
    protocol_version: str = "v1",
    protocol_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the complete bounded final result layer transactionally.

    Existing managed result paths are never overwritten.  ``raw_root`` may be
    an uncommitted ``raw_runs`` directory nested below ``output_root``; it is
    left untouched and represented only by hashed roots of trust.
    """

    raw = Path(raw_root).resolve()
    output = Path(output_root).resolve()
    resolved_protocol_path = (
        Path(__file__).resolve().parent.parent / "EXPERIMENT_PROTOCOL.md"
        if protocol_path is None
        else Path(protocol_path).resolve()
    )
    if output == raw or output.is_relative_to(raw):
        raise ReportingValidationError(
            "output_root cannot equal or be nested inside raw_root"
        )
    output.mkdir(parents=True, exist_ok=True)
    existing = [name for name in _MANAGED_OUTPUTS if (output / name).exists()]
    if existing:
        raise ReportingValidationError(
            "refusing to overwrite managed result artifacts: " + ", ".join(existing)
        )
    bundles = validate_raw_bundles(
        raw,
        required_bundles=required_bundles,
        expected_commit=expected_commit,
        require_clean=True,
        require_single_commit=True,
    )
    locked = _require_bundle(bundles, "locked_test")
    trajectory_metrics = load_bundle_csv(
        locked,
        "metrics_by_trajectory.csv",
        required_fields={"trajectory_id", "split", "scenario_id", "method"},
    )
    statistical_tables = build_statistical_tables(
        trajectory_metrics,
        locked.split_manifest,
        comparisons=comparisons,
        ci_metrics=ci_metrics,
        ci_methods=ci_methods,
        resamples=10_000,
        confidence_level=0.95,
        seed=20260721,
        expected_test_count=expected_test_count,
        incomplete_policy="reject",
        default_sample_rate_hz=_configured_sample_rate_hz(locked),
        require_stratification=True,
    )
    acceptance = build_acceptance_analysis(
        bundles,
        statistical_tables,
        candidate_method=_CANDIDATE_METHOD,
    )
    tables = assemble_figure_tables(
        bundles,
        statistical_tables,
        ranking_method=ranking_method,
        predefined_trace_ids=predefined_trace_ids,
        maximum_runtime_rows_per_method=maximum_runtime_rows_per_method,
        maximum_trace_rows_per_joint=maximum_trace_rows_per_joint,
    )

    staging = Path(
        tempfile.mkdtemp(prefix=".paper-evidence-final-", dir=output.parent)
    ).resolve()
    try:
        report = _write_final_tree(
            staging,
            logical_output_root=output,
            bundles=bundles,
            tables=tables,
            statistical_tables=statistical_tables,
            acceptance=acceptance,
            comparisons=comparisons,
            ci_metrics=ci_metrics,
            ranking_method=ranking_method,
            predefined_trace_ids=predefined_trace_ids,
            generation_command=generation_command,
            reporting_git_commit=reporting_git_commit,
            protocol_version=protocol_version,
            protocol_path=resolved_protocol_path,
        )
        for name in _MANAGED_OUTPUTS:
            source = staging / name
            if not source.exists():
                raise ReportingValidationError(
                    f"staging result omitted managed output {name}"
                )
            source.replace(output / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


__all__ = [
    "ACCEPTANCE_SCHEMA_VERSION",
    "AcceptanceAnalysis",
    "CHART_MAP_SCHEMA_VERSION",
    "CORE_DIAGNOSTIC_PUBLICATIONS",
    "DEFAULT_CI_METRICS",
    "DEFAULT_COMPARISONS",
    "DEFAULT_RAW_BUNDLES",
    "DEFAULT_STRATIFICATION_FIELDS",
    "FIGURE_TABLE_SCHEMAS",
    "FailureAnalysis",
    "PRIMARY_METHOD_IDS",
    "RAW_VALIDATION_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "ROOT_INDEX_SCHEMA_VERSION",
    "ReportingValidationError",
    "STATISTICAL_DESIGN_SCHEMA_VERSION",
    "StatisticalTables",
    "ValidatedBundle",
    "assemble_figure_tables",
    "aggregate_governor_acceptance",
    "build_acceptance_analysis",
    "build_core_diagnostic_publications",
    "build_fallback_summary",
    "build_failure_analysis",
    "build_constraint_jerk_table",
    "build_final_result_artifacts",
    "build_statistical_tables",
    "csv_regression_criteria",
    "expected_trajectory_ids",
    "generate_final_figures",
    "load_bundle_csv",
    "load_bundle_parquet",
    "filter_primary_method_rows",
    "select_acceleration_phase_condition",
    "summarize_repeated_runtime",
    "technical_readme",
    "validate_figure_tables",
    "validate_raw_bundles",
    "validate_root_artifact_index",
    "write_root_artifact_index",
]
