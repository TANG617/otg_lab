"""V4 evidence packaging and reproducibility gates.

This module is deliberately downstream-only.  It does not import the V4
runner, experiment pipeline, dataset generator, or trajectory implementation.
Consequently the report-only entry point can validate and package immutable
raw artifacts without acquiring any way to execute the experiment.

The lower-level :mod:`otg_lab.artifacts` module remains the authority for the
sample schema and independent profile, feasibility, and trajectory-metric
recomputation.  This module adds the V4 bundle contract, complete root index,
negative-result preservation, deterministic release archives, and frozen-V3
proof.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactValidationError,
    canonical_json_bytes,
    read_json,
    sha256_file,
    validate_artifact_bundle,
)

V4_ROOT_INDEX_SCHEMA_VERSION = "otg.paper-evidence-root-index.v4"
V4_RAW_BUNDLE_SCHEMA_VERSION = "otg.v4-raw-bundle-validation.v1"
V4_REPORT_ONLY_SCHEMA_VERSION = "otg.v4-report-only-validation.v1"
V4_RELEASE_MANIFEST_SCHEMA_VERSION = "otg.v4-release-manifest.v1"
V3_IMMUTABILITY_PROOF_SCHEMA_VERSION = "otg.v3-immutability-proof.v1"
V3_FROZEN_REFERENCE_COMMIT = "1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe"

_PRIMARY_PROFILE_METHODS = (
    "one_step_governed_p_direct",
    "one_step_governed_pv_direct",
    "one_step_governed_pva_direct",
)
_SECONDARY_PROFILE_METHODS = frozenset(
    {
        "deployed_p_only_ordinary_ruckig",
        "predicted_p_ordinary_ruckig",
        "raw_predicted_pv_ordinary_ruckig",
        "raw_predicted_pva_ordinary_ruckig",
    }
)
_ORACLE_PROFILE_METHODS = (
    "oracle_one_step_p_direct",
    "oracle_one_step_pv_direct",
    "oracle_one_step_pva_direct",
)

V4_RESULTS_DIRECTORIES = (
    "manifests",
    "configs",
    "summaries",
    "statistics",
    "figures",
    "sample_traces",
    "raw_runs/validation",
    "raw_runs/locked_test",
    "raw_runs/oracle_diagnostic",
    "failures",
    "generated_tables",
    "generated_figures",
)

# This derived, per-cycle table is reproducible from the checksummed primary
# raw bundle but can exceed the bounded handoff's 50 MiB per-file contract.
# It remains root-indexed and locally retained; only the bounded ZIP excludes
# it.  Any other oversized non-raw artifact still fails closed.
V4_BOUNDED_HIGH_VOLUME_EXCLUSIONS = frozenset(
    {"statistics/constraint_audit.csv"}
)

V4_RAW_REQUIRED_ARTIFACTS = frozenset(
    {
        "run.json",
        "resolved_config.yaml",
        "data_manifest.json",
        "split_manifest.json",
        "method_matrix.json",
        "expected_unit_matrix.json",
        "samples.parquet",
        "metrics_by_trajectory.csv",
        "summary_metrics.csv",
        "constraint_audit.csv",
        "runtime_benchmark.csv",
        "failures.csv",
        "fallback_events.csv",
        "completion_summary.csv",
        "artifact_index.json",
        "artifact_checksums.json",
    }
)

# Each entry is a tuple of acceptable minimum-column alternatives.  Supporting
# the legacy aliases keeps the artifact layer independent of producer-internal
# naming while every alternative still carries the preregistered meaning.
V4_CSV_SCHEMAS: dict[str, tuple[frozenset[str], ...]] = {
    "metrics_by_trajectory.csv": (
        frozenset({"trajectory_id", "method", "position_rmse"}),
        frozenset({"trajectory_id", "method_id", "position_rmse"}),
    ),
    "summary_metrics.csv": (
        frozenset({"method", "metric", "n_trajectories", "mean"}),
        frozenset({"method_id", "metric", "n_trajectories", "mean"}),
        frozenset(
            {"method", "metric", "required_trajectory_count", "mean"}
        ),
    ),
    "primary_comparison.csv": (
        frozenset(
            {
                "trajectory_id",
                "family",
                "demand_stratum",
                "baseline_position_rmse",
                "candidate_position_rmse",
                "candidate_minus_baseline_position_rmse",
                "absolute_improvement",
                "harmful",
                "paired_value_available",
                "formal_inference_status",
                "paired_trajectory_count",
                "bootstrap_resamples",
                "bootstrap_seed",
                "overall_absolute_improvement",
                "overall_absolute_improvement_ci_low",
                "overall_absolute_improvement_ci_high",
                "overall_relative_improvement",
                "overall_relative_improvement_ci_low",
                "overall_relative_improvement_ci_high",
                "primary_result_classification",
                "max_error_guardrail_pass",
                "lag_guardrail_pass",
            }
        ),
        frozenset(
            {
                "comparison_id",
                "baseline_method",
                "candidate_method",
                "n_trajectories",
                "relative_improvement",
                "relative_ci_low",
                "relative_ci_high",
                "classification",
            }
        ),
        frozenset(
            {
                "comparison_id",
                "baseline_method",
                "candidate_method",
                "paired_denominator",
                "relative_improvement",
                "relative_ci_low",
                "relative_ci_high",
                "primary_result_classification",
            }
        ),
    ),
    "secondary_comparisons.csv": (
        frozenset(
            {
                "comparison_id",
                "status",
                "metric",
                "baseline_method",
                "candidate_method",
                "trajectory_count",
                "absolute_difference",
                "relative_difference",
                "absolute_improvement_ci_low",
                "absolute_improvement_ci_high",
                "relative_improvement_ci_low",
                "relative_improvement_ci_high",
                "cohen_dz",
                "unadjusted_p",
                "holm_adjusted_p",
                "harmful_count",
                "harmful_denominator",
                "harmful_rate",
                "bootstrap_resamples",
                "bootstrap_seed",
            }
        ),
        frozenset(
            {
                "comparison_id",
                "baseline_method",
                "candidate_method",
                "n_trajectories",
                "absolute_difference",
                "relative_difference",
                "unadjusted_p_value",
                "holm_adjusted_p_value",
            }
        ),
    ),
    "confidence_intervals.csv": (
        frozenset({"comparison_id", "ci_low", "ci_high"}),
        frozenset({"method", "metric", "mean_ci_low", "mean_ci_high"}),
    ),
    "stratified_comparisons.csv": (
        frozenset(
            {
                "comparison_id",
                "stratum_dimension",
                "stratum_value",
                "status",
                "trajectory_count",
                "absolute_improvement",
                "relative_improvement",
                "absolute_improvement_ci_low",
                "absolute_improvement_ci_high",
                "relative_improvement_ci_low",
                "relative_improvement_ci_high",
                "harmful_count",
                "harmful_denominator",
                "harmful_rate",
                "bootstrap_seed",
            }
        ),
        frozenset(
            {
                "comparison_id",
                "stratum_dimension",
                "stratum_value",
                "n_trajectories",
                "effect",
                "ci_low",
                "ci_high",
            }
        ),
        frozenset(
            {
                "comparison_id",
                "stratum_dimension",
                "stratum_value",
                "n_trajectories",
                "improvement",
                "ci_low",
                "ci_high",
            }
        ),
    ),
    "family_effects.csv": (
        frozenset(
            {
                "comparison_id",
                "stratum_dimension",
                "stratum_value",
                "status",
                "trajectory_count",
                "absolute_improvement",
                "relative_improvement",
                "absolute_improvement_ci_low",
                "absolute_improvement_ci_high",
                "relative_improvement_ci_low",
                "relative_improvement_ci_high",
                "harmful_count",
                "harmful_denominator",
                "harmful_rate",
                "bootstrap_seed",
            }
        ),
        frozenset({"family", "n_trajectories", "effect", "ci_low", "ci_high"}),
        frozenset(
            {
                "reference_family",
                "n_trajectories",
                "effect",
                "ci_low",
                "ci_high",
            }
        ),
    ),
    "demand_stratum_effects.csv": (
        frozenset(
            {
                "comparison_id",
                "stratum_dimension",
                "stratum_value",
                "status",
                "trajectory_count",
                "absolute_improvement",
                "relative_improvement",
                "absolute_improvement_ci_low",
                "absolute_improvement_ci_high",
                "relative_improvement_ci_low",
                "relative_improvement_ci_high",
                "harmful_count",
                "harmful_denominator",
                "harmful_rate",
                "bootstrap_seed",
            }
        ),
        frozenset(
            {"demand_stratum", "n_trajectories", "effect", "ci_low", "ci_high"}
        ),
    ),
    "acceleration_active_effect.csv": (
        frozenset(
            {
                "comparison_id",
                "stratum_dimension",
                "stratum_value",
                "status",
                "trajectory_count",
                "absolute_improvement",
                "relative_improvement",
                "absolute_improvement_ci_low",
                "absolute_improvement_ci_high",
                "relative_improvement_ci_low",
                "relative_improvement_ci_high",
                "harmful_count",
                "harmful_denominator",
                "harmful_rate",
                "bootstrap_seed",
            }
        ),
        frozenset({"subgroup", "n_trajectories", "effect", "ci_low", "ci_high"}),
        frozenset({"n_trajectories", "effect", "ci_low", "ci_high"}),
    ),
    "harmful_trajectory_rate.csv": (
        frozenset(
            {
                "comparison_id",
                "analysis_kind",
                "status",
                "harmful_count",
                "denominator",
                "evaluated_count",
                "harmful_rate",
                "wilson_ci_low",
                "wilson_ci_high",
            }
        ),
        frozenset(
            {
                "comparison_id",
                "harmful_count",
                "n_trajectories",
                "harmful_rate",
            }
        ),
        frozenset(
            {
                "comparison_id",
                "harmful_count",
                "denominator",
                "harmful_rate",
            }
        ),
    ),
    "worst_five_trajectories.csv": (
        frozenset({"trajectory_id", "baseline_value", "candidate_value", "effect"}),
        frozenset({"trajectory_id", "improvement"}),
        frozenset(
            {
                "trajectory_id",
                "baseline_position_rmse",
                "candidate_position_rmse",
                "candidate_minus_baseline_position_rmse",
                "absolute_improvement",
            }
        ),
    ),
    "method_identity_summary.csv": (
        frozenset({"method", "method_purity_rate"}),
        frozenset({"method_id", "method_purity_rate"}),
    ),
    "same_information_audit.csv": (
        frozenset({"trajectory_id", "k", "audit_passed"}),
        frozenset({"trajectory_id", "cycle", "audit_passed"}),
    ),
    "constraint_audit.csv": (
        frozenset({"trajectory_id", "violation_count"}),
    ),
    "runtime_benchmark.csv": (
        frozenset({"method", "runtime_p99_us", "runtime_max_us"}),
        frozenset({"method_id", "total_p99_us", "total_max_us"}),
    ),
    "failures.csv": (
        frozenset({"run_id", "trajectory_id", "failure_type", "reason"}),
    ),
    "fallback_events.csv": (
        frozenset({"run_id", "trajectory_id", "k", "fallback_reason"}),
    ),
    "completion_summary.csv": (
        frozenset(
            {
                "method",
                "attempted_trajectories",
                "completed_trajectories",
                "failed_trajectories",
            }
        ),
        frozenset(
            {
                "method",
                "attempted_trajectory_runs",
                "successful_trajectory_runs",
                "failed_trajectory_runs",
            }
        ),
    ),
    "oracle_target_component_metrics.csv": (
        frozenset(
            {
                "trajectory_id",
                "method",
                "information_condition",
                "causal",
                "deployable",
                "diagnostic_only",
            }
        ),
    ),
    "oracle_pv_vs_p.csv": (
        frozenset({"comparison_id", "n_trajectories", "effect"}),
    ),
    "oracle_pva_vs_pv.csv": (
        frozenset({"comparison_id", "n_trajectories", "effect"}),
    ),
    "oracle_acceleration_active_effect.csv": (
        frozenset({"n_trajectories", "effect"}),
    ),
    "ordinary_ruckig_metrics.csv": (
        frozenset({"trajectory_id", "method", "position_rmse"}),
        frozenset({"trajectory_id", "method_id", "position_rmse"}),
    ),
    "ordinary_ruckig_method_identity.csv": (
        frozenset({"method", "native_execution_rate"}),
        frozenset({"method_id", "native_execution_rate"}),
    ),
    "ordinary_ruckig_completion.csv": (
        frozenset({"method", "attempted_trajectories", "completed_trajectories"}),
        frozenset(
            {
                "method_id",
                "attempted_trajectory_runs",
                "successful_trajectory_runs",
            }
        ),
    ),
    "ordinary_ruckig_profile_audit.csv": (
        frozenset({"trajectory_id", "method", "violation_count"}),
        frozenset({"trajectory_id", "method_id", "violation_count"}),
    ),
    "bootstrap_reconstruction.csv": (
        frozenset(
            {
                "comparison_id",
                "draws_available",
                "resamples",
                "seed",
                "rng",
                "draw_algorithm",
                "input_order",
                "paired_unit",
            }
        ),
    ),
}

V4_REQUIRED_STATISTICAL_CSVS = frozenset(
    {
        "metrics_by_trajectory.csv",
        "summary_metrics.csv",
        "primary_comparison.csv",
        "secondary_comparisons.csv",
        "confidence_intervals.csv",
        "stratified_comparisons.csv",
        "family_effects.csv",
        "demand_stratum_effects.csv",
        "acceleration_active_effect.csv",
        "harmful_trajectory_rate.csv",
        "worst_five_trajectories.csv",
        "method_identity_summary.csv",
        "same_information_audit.csv",
        "constraint_audit.csv",
        "runtime_benchmark.csv",
        "failures.csv",
        "fallback_events.csv",
        "completion_summary.csv",
        "oracle_target_component_metrics.csv",
        "oracle_pv_vs_p.csv",
        "oracle_pva_vs_pv.csv",
        "oracle_acceleration_active_effect.csv",
        "ordinary_ruckig_metrics.csv",
        "ordinary_ruckig_method_identity.csv",
        "ordinary_ruckig_completion.csv",
        "ordinary_ruckig_profile_audit.csv",
        "bootstrap_reconstruction.csv",
    }
)

V4_NEGATIVE_CLASSIFICATIONS = frozenset(
    {
        "inconclusive",
        "confirmed_harmful",
        "invalid_method_identity",
        "invalid_safety_gate",
        "unavailable_incomplete_denominator",
    }
)

V3_ROOT_EVIDENCE_PATHS = (
    "protocol_status_v3.json",
    "protocol_status_v3_postreview.json",
    "V3_POSTREVIEW_ADDENDUM.md",
    "EXPERIMENT_PROTOCOL_V3.md",
    "split_manifest.json",
    "split_manifest_v1.json",
    "split_manifest_v2.json",
    "split_manifest_v3.json",
    "config_lock.json",
    "config_lock_v1.json",
    "config_lock_v2.json",
    "config_lock_v3.json",
)

_HASH_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _atomic_write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _write_json(path: Path, value: Any) -> Path:
    return _atomic_write(path, canonical_json_bytes(value))


def _safe_relative(root: Path, path: Path) -> str:
    root = root.resolve()
    path = path.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ArtifactValidationError(f"{path} is outside {root}") from error
    if relative == Path(".") or relative.is_absolute() or ".." in relative.parts:
        raise ArtifactValidationError(f"unsafe artifact path {path}")
    return relative.as_posix()


def _regular_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ArtifactValidationError(f"artifact directory is missing: {root}")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArtifactValidationError(f"symlink is forbidden in evidence: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _media_type(path: Path) -> str:
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".tex": "application/x-tex",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".parquet": "application/vnd.apache.parquet",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".zip": "application/zip",
    }.get(path.suffix.lower(), "application/octet-stream")


def _artifact_role(relative: str) -> str:
    top = Path(relative).parts[0]
    if top == "raw_runs":
        return "immutable_raw_evidence"
    if top == "statistics":
        return "statistical_result"
    if top == "summaries":
        return "bounded_summary"
    if top in {"figures", "generated_figures"}:
        return "figure"
    if top == "sample_traces":
        return "bounded_sample_trace"
    if top == "configs":
        return "locked_config_copy"
    if top == "manifests":
        return "provenance_manifest"
    if top == "failures":
        return "failure_evidence"
    if top == "generated_tables":
        return "paper_handoff_table"
    return "root_evidence"


def ensure_v4_results_layout(results_root: str | Path) -> Path:
    """Create only the preregistered directory skeleton.

    Existing files are never removed or overwritten.  Raw directories may
    already contain atomically promoted bundles when this function is called.
    """

    root = Path(results_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for relative in V4_RESULTS_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


create_v4_results_layout = ensure_v4_results_layout


def atomic_copy_file(
    source: str | Path,
    destination: str | Path,
    *,
    expected_sha256: str | None = None,
    replace: bool = False,
) -> Path:
    """Copy one immutable artifact using fsync + atomic replacement."""

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_file() or source_path.is_symlink():
        raise ArtifactValidationError(f"copy source is missing or unsafe: {source_path}")
    if destination_path.exists() and not replace:
        raise FileExistsError(
            f"refusing to overwrite immutable artifact: {destination_path}"
        )
    observed_source_hash = sha256_file(source_path)
    if expected_sha256 is not None and observed_source_hash != expected_sha256:
        raise ArtifactValidationError(
            f"copy source hash mismatch: expected {expected_sha256}, "
            f"observed {observed_source_hash}"
        )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        suffix=".copying",
        dir=destination_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with (
            source_path.open("rb") as input_stream,
            os.fdopen(descriptor, "wb") as output_stream,
        ):
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if sha256_file(temporary) != observed_source_hash:
            raise ArtifactValidationError("atomic copy verification failed")
        temporary.replace(destination_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination_path


def atomic_promote_directory(
    staging_root: str | Path, destination: str | Path
) -> Path:
    """Promote a complete same-filesystem staging tree without overwriting."""

    staging = Path(staging_root).resolve()
    target = Path(destination).resolve()
    if not staging.is_dir() or staging.is_symlink():
        raise ArtifactValidationError(f"staging directory is unsafe: {staging}")
    _regular_files(staging)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite artifact directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(target)
    return target


def atomic_copy_and_promote_bundle(
    source_root: str | Path,
    destination: str | Path,
    *,
    validator: Any | None = None,
) -> Path:
    """Copy a tree to hidden staging, validate it, then atomically promote it."""

    source = Path(source_root).resolve()
    target = Path(destination).resolve()
    if not source.is_dir() or source.is_symlink():
        raise ArtifactValidationError(f"bundle source is missing or unsafe: {source}")
    if target.exists():
        raise FileExistsError(f"refusing to overwrite artifact bundle: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent)
    )
    try:
        for path in _regular_files(source):
            relative = path.relative_to(source)
            atomic_copy_file(path, staging / relative)
        if validator is not None:
            validator(staging)
        staging.replace(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


stage_and_promote_bundle = atomic_copy_and_promote_bundle


def _csv_header_and_count(path: Path) -> tuple[frozenset[str], int]:
    if not path.is_file() or path.is_symlink():
        raise ArtifactValidationError(f"CSV artifact is missing or unsafe: {path}")
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or len(reader.fieldnames) != len(
            set(reader.fieldnames)
        ):
            raise ArtifactValidationError(f"{path} has an invalid CSV header")
        fields = frozenset(reader.fieldnames)
        count = 0
        for row_index, row in enumerate(reader):
            count += 1
            for field, value in row.items():
                if value is None:
                    raise ArtifactValidationError(
                        f"{path} row {row_index} is not rectangular"
                    )
                if value.strip().lower() in {
                    "nan",
                    "+nan",
                    "-nan",
                    "inf",
                    "+inf",
                    "-inf",
                    "infinity",
                    "+infinity",
                    "-infinity",
                }:
                    raise ArtifactValidationError(
                        f"{path} row {row_index}.{field} is non-finite"
                    )
    return fields, count


def validate_v4_csv_schema(
    path: str | Path,
    *,
    allow_empty: bool | None = None,
) -> dict[str, Any]:
    """Validate a named V4 CSV against its preregistered minimum schema."""

    target = Path(path)
    alternatives = V4_CSV_SCHEMAS.get(target.name)
    if alternatives is None:
        raise ArtifactValidationError(f"no V4 CSV schema registered for {target.name}")
    fields, row_count = _csv_header_and_count(target)
    if not any(required <= fields for required in alternatives):
        missing_alternatives = [sorted(required - fields) for required in alternatives]
        raise ArtifactValidationError(
            f"{target} does not satisfy a V4 schema; "
            f"missing alternatives={missing_alternatives}"
        )
    empty_permitted = (
        target.name in {"failures.csv", "fallback_events.csv"}
        if allow_empty is None
        else allow_empty
    )
    if row_count == 0 and not empty_permitted:
        raise ArtifactValidationError(f"required V4 CSV is empty: {target}")
    return {
        "path": str(target.resolve()),
        "columns": sorted(fields),
        "row_count": row_count,
        "sha256": sha256_file(target),
    }


def _find_bounded_artifact(root: Path, name: str) -> Path:
    candidates = [
        root / "statistics" / name,
        root / "summaries" / name,
        root / "failures" / name,
        root / name,
    ]
    observed = [candidate for candidate in candidates if candidate.is_file()]
    if not observed:
        raise ArtifactValidationError(f"required V4 artifact is missing: {name}")
    if len(observed) > 1:
        raise ArtifactValidationError(
            f"ambiguous duplicate V4 artifact {name}: "
            f"{[str(path) for path in observed]}"
        )
    return observed[0]


def validate_statistical_artifacts(
    results_root: str | Path,
    *,
    required_names: Iterable[str] = V4_REQUIRED_STATISTICAL_CSVS,
    raw_metrics_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    statistical_design_path: str | Path | None = None,
) -> dict[str, Any]:
    """Require all tables and, when raw is supplied, independently recompute."""

    root = Path(results_root).resolve()
    validated: dict[str, Any] = {}
    for name in sorted(set(required_names)):
        path = _find_bounded_artifact(root, name)
        validated[name] = validate_v4_csv_schema(path)
    independent: Mapping[str, Any] | None = None
    if raw_metrics_path is not None:
        from .v4_statistics_audit import audit_v4_statistics_independently

        repository = Path(__file__).resolve().parents[1]
        independent = audit_v4_statistics_independently(
            raw_metrics_path=raw_metrics_path,
            published_statistics_root=root / "statistics",
            manifest_path=manifest_path or repository / "split_manifest_v4.json",
            statistical_design_path=(
                statistical_design_path
                or repository / "V4_STATISTICAL_DESIGN.json"
            ),
        )
    return {
        "validated_csv_count": len(validated),
        "artifacts": validated,
        "independent_recomputation": independent,
        "all_independent_statistical_recomputations_verified": (
            None
            if independent is None
            else independent.get(
                "all_independent_statistical_recomputations_verified"
            )
        ),
    }


def _validate_profile_recomputation_report(
    report: Mapping[str, Any],
    *,
    required_complete_methods: Iterable[str] = (),
    permitted_nonapplicable_methods: Iterable[str] = (),
) -> dict[str, Any]:
    sample_report = report.get("sample_recomputation")
    if not isinstance(sample_report, Mapping):
        raise ArtifactValidationError(
            "raw bundle validation did not return sample recomputation evidence"
        )
    verified = sample_report.get("profile_fields_verified")
    unavailable = sample_report.get("profile_fields_unavailable")
    if not isinstance(verified, Mapping) or not isinstance(unavailable, Mapping):
        raise ArtifactValidationError(
            "raw bundle validation lacks profile verification counters"
        )
    verified_by_method = sample_report.get("profile_fields_verified_by_method", {})
    unavailable_by_method = sample_report.get(
        "profile_fields_unavailable_by_method", {}
    )
    if not isinstance(verified_by_method, Mapping) or not isinstance(
        unavailable_by_method, Mapping
    ):
        raise ArtifactValidationError(
            "raw bundle validation has malformed per-method profile counters"
        )
    required_fields = {
        "command_profile_segment_count",
        "command_profile_boundary_count",
        "command_endpoint_matches_profile",
        "command_first_jerk",
        "command_last_jerk",
        "command_internal_max_abs_jerk",
        "command_profile_continuous_constraints_satisfied",
        "command_max_abs_velocity",
        "command_max_abs_acceleration",
        "command_max_abs_jerk",
    }
    missing = sorted(
        field for field in required_fields if int(verified.get(field, 0)) <= 0
    )
    permitted = frozenset(str(method) for method in permitted_nonapplicable_methods)
    unavailable_totals: Counter[str] = Counter()
    unexpectedly_unavailable: dict[str, int] = {}
    for method, fields in unavailable_by_method.items():
        if not isinstance(fields, Mapping):
            raise ArtifactValidationError(
                f"profile unavailable counters for {method!r} are malformed"
            )
        for field, count in fields.items():
            field_name = str(field)
            count_value = int(count)
            if count_value <= 0:
                continue
            unavailable_totals[field_name] += count_value
            if (
                field_name != "command_constant_jerk_exact"
                and str(method) not in permitted
            ):
                unexpectedly_unavailable[f"{method}:{field_name}"] = count_value
    reported_unavailable = Counter(
        {
            str(field): int(count)
            for field, count in unavailable.items()
            if int(count) > 0
        }
    )
    if unavailable_totals and unavailable_totals != reported_unavailable:
        raise ArtifactValidationError(
            "global and per-method profile-unavailability counters differ"
        )
    if (
        any(
            field != "command_constant_jerk_exact"
            for field in reported_unavailable
        )
        and not unavailable_totals
    ):
        unexpectedly_unavailable.update(
            {
                field: count
                for field, count in reported_unavailable.items()
                if field != "command_constant_jerk_exact"
            }
        )
    incomplete_methods: dict[str, list[str]] = {}
    for method in required_complete_methods:
        method_counts = verified_by_method.get(str(method))
        if not isinstance(method_counts, Mapping):
            incomplete_methods[str(method)] = sorted(required_fields)
            continue
        absent = sorted(
            field for field in required_fields if int(method_counts.get(field, 0)) <= 0
        )
        if absent:
            incomplete_methods[str(method)] = absent
    if missing or unexpectedly_unavailable or incomplete_methods:
        raise ArtifactValidationError(
            "independent profile recomputation is incomplete: "
            f"never_verified={missing}, "
            f"unexpectedly_unavailable={unexpectedly_unavailable}, "
            f"incomplete_methods={incomplete_methods}"
        )
    return {
        "required_profile_fields_verified": sorted(required_fields),
        "complete_profile_methods_verified": sorted(
            str(method) for method in required_complete_methods
        ),
        "nonapplicable_field_counts": {
            field: count for field, count in sorted(reported_unavailable.items())
        },
    }


def validate_raw_bundle(
    bundle_root: str | Path,
    *,
    expected_commit: str | None = None,
    bundle_kind: str = "locked_test",
    require_clean: bool = True,
    recompute_arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one immutable V4 raw bundle from checksums through recomputation."""

    root = Path(bundle_root).resolve()
    if bundle_kind not in {"locked_test", "oracle_diagnostic", "validation"}:
        raise ArtifactValidationError(f"unsupported V4 raw bundle kind {bundle_kind!r}")
    if expected_commit is not None and not _COMMIT_RE.fullmatch(expected_commit):
        raise ArtifactValidationError("expected raw commit must be 40 lowercase hex")
    files = {
        path.relative_to(root).as_posix()
        for path in _regular_files(root)
    }
    missing = V4_RAW_REQUIRED_ARTIFACTS - files
    if missing:
        raise ArtifactValidationError(
            f"{bundle_kind} raw bundle is missing {sorted(missing)}"
        )
    arguments = {
        "max_lag_s": 1.0,
        "motion_limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
    }
    arguments.update(dict(recompute_arguments or {}))
    base_report = validate_artifact_bundle(
        root,
        require_standard_artifacts=True,
        require_clean=require_clean,
        expected_commit=expected_commit,
        verify_recomputation=True,
        require_complete_feasibility=True,
        recompute_arguments=arguments,
    )
    # The V4 additions are not part of the older standard bundle set, but must
    # be checksummed/indexed (already enforced above) and schema-valid here.
    csv_reports = {
        name: validate_v4_csv_schema(root / name)
        for name in (
            "metrics_by_trajectory.csv",
            "summary_metrics.csv",
            "constraint_audit.csv",
            "runtime_benchmark.csv",
            "failures.csv",
            "fallback_events.csv",
            "completion_summary.csv",
        )
    }
    run = read_json(root / "run.json")
    if expected_commit is not None and run.get("git_commit") != expected_commit:
        raise ArtifactValidationError(
            f"{bundle_kind} run commit differs from requested raw commit"
        )
    profile_report = _validate_profile_recomputation_report(
        base_report,
        required_complete_methods=(
            _ORACLE_PROFILE_METHODS
            if bundle_kind == "oracle_diagnostic"
            else _PRIMARY_PROFILE_METHODS
        ),
        permitted_nonapplicable_methods=(
            ()
            if bundle_kind == "oracle_diagnostic"
            else _SECONDARY_PROFILE_METHODS
        ),
    )
    return {
        "schema_version": V4_RAW_BUNDLE_SCHEMA_VERSION,
        "bundle_kind": bundle_kind,
        "bundle_root": str(root),
        "raw_commit": run.get("git_commit"),
        "file_count": len(files),
        "required_file_count": len(V4_RAW_REQUIRED_ARTIFACTS),
        "schema_checks": csv_reports,
        "base_validation": base_report,
        "checksums_verified": True,
        "profile_recomputation_verified": bool(profile_report),
        "profile_recomputation": profile_report,
        "feasibility_recomputation_verified": bool(
            base_report.get("feasibility_recomputation_verified")
        ),
        "trajectory_metric_recomputation_verified": bool(
            base_report.get("sample_recomputation", {}).get(
                "trajectory_metrics_verified"
            )
        ),
    }


def validate_report_only_inputs(
    *,
    results_root: str | Path,
    raw_commit: str,
    locked_test_root: str | Path,
    oracle_root: str | Path,
    recompute_arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Authorize reporting only after both immutable raw bundles revalidate.

    This function has no callback or runner argument and imports no experiment
    code.  Its return value is canonical JSON data, not an execution capability.
    """

    if not _COMMIT_RE.fullmatch(raw_commit):
        raise ArtifactValidationError(
            "report-only requires a 40-character lowercase raw commit"
        )
    root = Path(results_root).resolve()
    locked = Path(locked_test_root).resolve()
    oracle = Path(oracle_root).resolve()
    for bundle in (locked, oracle):
        try:
            bundle.relative_to(root)
        except ValueError as error:
            raise ArtifactValidationError(
                f"report-only raw bundle must be inside results root: {bundle}"
            ) from error
    locked_report = validate_raw_bundle(
        locked,
        expected_commit=raw_commit,
        bundle_kind="locked_test",
        require_clean=True,
        recompute_arguments=recompute_arguments,
    )
    oracle_report = validate_raw_bundle(
        oracle,
        expected_commit=raw_commit,
        bundle_kind="oracle_diagnostic",
        require_clean=True,
        recompute_arguments=recompute_arguments,
    )
    return {
        "schema_version": V4_REPORT_ONLY_SCHEMA_VERSION,
        "report_only": True,
        "experiment_execution_permitted": False,
        "trajectory_generation_permitted": False,
        "pipeline_runner_imported": False,
        "raw_commit": raw_commit,
        "results_root": str(root),
        "locked_test": locked_report,
        "oracle_diagnostic": oracle_report,
        "all_raw_checksums_verified": True,
        "all_independent_recomputations_verified": True,
    }


def _classification_from_primary(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ArtifactValidationError("primary_comparison.csv is empty")
    classifications = {
        value
        for row in rows
        for value in (
            row.get("classification")
            or row.get("primary_result_classification")
            or row.get("result_classification"),
        )
        if value
    }
    if len(classifications) != 1:
        raise ArtifactValidationError(
            "primary comparison must contain one consistent result classification"
        )
    return next(iter(classifications))


def _recursive_values(value: Any, key: str) -> list[Any]:
    output: list[Any] = []
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            if child_key == key:
                output.append(child)
            output.extend(_recursive_values(child, key))
    elif isinstance(value, list):
        for child in value:
            output.extend(_recursive_values(child, key))
    return output


def validate_negative_result_preservation(
    results_root: str | Path,
) -> dict[str, Any]:
    """Ensure an unfavorable primary result survives every bounded handoff."""

    root = Path(results_root).resolve()
    primary_path = _find_bounded_artifact(root, "primary_comparison.csv")
    classification = _classification_from_primary(primary_path)
    with primary_path.open("r", encoding="utf-8", newline="") as stream:
        primary_rows = list(csv.DictReader(stream))
    harmful_count = sum(
        str(row.get("harmful", "")).strip().lower() in {"true", "1"}
        for row in primary_rows
    )
    harmful_source = {
        str(row.get("trajectory_id")): float(
            row["candidate_minus_baseline_position_rmse"]
        )
        for row in primary_rows
        if str(row.get("harmful", "")).strip().lower() in {"true", "1"}
    }
    summary_path = root / "V4_RESULT_SUMMARY.md"
    handoff_path = root / "paper_handoff.json"
    if not summary_path.is_file() or not handoff_path.is_file():
        raise ArtifactValidationError(
            "result summary and paper_handoff.json are required before finalization"
        )
    summary = summary_path.read_text(encoding="utf-8")
    handoff = read_json(handoff_path)
    handoff_classifications = {
        str(value)
        for key in ("primary_result_classification", "classification")
        for value in _recursive_values(handoff, key)
    }
    if classification not in summary:
        raise ArtifactValidationError(
            "V4_RESULT_SUMMARY.md does not preserve the primary classification"
        )
    if classification not in handoff_classifications:
        raise ArtifactValidationError(
            "paper_handoff.json classification differs from primary comparison"
        )
    negative = classification in V4_NEGATIVE_CLASSIFICATIONS
    primary_effect = handoff.get("primary_effect")
    if isinstance(primary_effect, Mapping) and primary_rows:
        source = primary_rows[0]
        numeric_pairs = (
            ("relative_improvement", "overall_relative_improvement"),
            ("relative_ci_low", "overall_relative_improvement_ci_low"),
            ("relative_ci_high", "overall_relative_improvement_ci_high"),
        )
        for handoff_field, source_field in numeric_pairs:
            if source.get(source_field, "") == "":
                expected = None
            else:
                expected = float(source[source_field])
            observed_raw = primary_effect.get(handoff_field)
            observed = (
                None
                if observed_raw in {None, ""}
                else float(observed_raw)
            )
            if (
                expected is None
                and observed is not None
                or expected is not None
                and (
                    observed is None
                    or not math.isclose(
                        observed, expected, rel_tol=1e-12, abs_tol=1e-12
                    )
                )
            ):
                raise ArtifactValidationError(
                    f"paper_handoff.json changes primary {handoff_field}"
                )
    elif negative or harmful_count:
        raise ArtifactValidationError(
            "paper_handoff.json lacks exact primary_effect preservation"
        )
    if negative or harmful_count:
        negative_section = handoff.get("negative_results")
        if not isinstance(negative_section, Mapping):
            raise ArtifactValidationError(
                "negative/harmful primary evidence is absent from handoff "
                "negative_results"
            )
        harmful_handoff = negative_section.get("harmful_trajectories")
        if not isinstance(harmful_handoff, Sequence) or isinstance(
            harmful_handoff, (str, bytes)
        ):
            raise ArtifactValidationError(
                "negative_results lacks harmful trajectory evidence"
            )
        observed_harmful: dict[str, float] = {}
        for row in harmful_handoff:
            if not isinstance(row, Mapping):
                raise ArtifactValidationError("harmful trajectory handoff row is invalid")
            identity = str(row.get("trajectory_id", ""))
            effect = row.get("candidate_minus_baseline_position_rmse")
            if not identity or effect is None or identity in observed_harmful:
                raise ArtifactValidationError(
                    "harmful trajectory handoff identity/effect is incomplete"
                )
            observed_harmful[identity] = float(effect)
        if set(observed_harmful) != set(harmful_source) or any(
            not math.isclose(
                observed_harmful[identity],
                harmful_source[identity],
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for identity in harmful_source
        ):
            raise ArtifactValidationError(
                "paper_handoff.json changes harmful trajectory IDs/effects"
            )
        if f"Harmful trajectories: {harmful_count}" not in summary:
            raise ArtifactValidationError(
                "V4_RESULT_SUMMARY.md changes the harmful trajectory count"
            )
    source_hashes = handoff.get("source_artifact_hashes")
    if isinstance(source_hashes, Mapping):
        matching_hashes = [
            value
            for path, value in source_hashes.items()
            if Path(str(path)).name == "primary_comparison.csv"
        ]
        if matching_hashes and matching_hashes != [sha256_file(primary_path)]:
            raise ArtifactValidationError(
                "paper_handoff.json primary source hash differs"
            )
    return {
        "classification": classification,
        "negative_result": negative,
        "harmful_trajectory_count": harmful_count,
        "harmful_trajectory_ids_and_effects_preserved": True,
        "primary_effect_and_interval_preserved": True,
        "summary_preserved": True,
        "handoff_preserved": True,
    }


def build_root_artifact_index(
    results_root: str | Path,
    *,
    raw_commit: str,
    reporting_commit: str | None = None,
    generation_command: Sequence[str] = (),
) -> dict[str, Any]:
    """Atomically write a root index covering every other file exactly once."""

    if not _COMMIT_RE.fullmatch(raw_commit):
        raise ArtifactValidationError("raw_commit must be 40 lowercase hex")
    if reporting_commit is not None and not _COMMIT_RE.fullmatch(reporting_commit):
        raise ArtifactValidationError("reporting_commit must be 40 lowercase hex")
    root = Path(results_root).resolve()
    excluded = {"artifact_index.json", "artifact_index.sha256"}
    records: list[dict[str, Any]] = []
    for path in _regular_files(root):
        relative = _safe_relative(root, path)
        if relative in excluded:
            continue
        records.append(
            {
                "path": relative,
                "role": _artifact_role(relative),
                "media_type": _media_type(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if not records:
        raise ArtifactValidationError("cannot index an empty V4 result directory")
    index = {
        "schema_version": V4_ROOT_INDEX_SCHEMA_VERSION,
        "raw_run_git_commit": raw_commit,
        "reporting_git_commit": reporting_commit or raw_commit,
        "generation_command": list(generation_command),
        "root_of_trust": {
            "algorithm": "sha256",
            "index": "artifact_index.json",
            "digest_sidecar": "artifact_index.sha256",
            "self_hash_excluded_to_avoid_recursion": True,
        },
        "artifact_count": len(records),
        "artifacts": records,
    }
    index_path = _write_json(root / "artifact_index.json", index)
    digest = sha256_file(index_path)
    _atomic_write(
        root / "artifact_index.sha256",
        f"{digest}  artifact_index.json\n".encode("ascii"),
    )
    verify_root_artifact_index(root)
    return {
        **index,
        "artifact_index_sha256": digest,
        "artifact_index_path": str(index_path),
    }


def verify_root_artifact_index(results_root: str | Path) -> dict[str, Any]:
    """Reject missing, extra, resized, or tampered root artifacts."""

    root = Path(results_root).resolve()
    index_path = root / "artifact_index.json"
    sidecar_path = root / "artifact_index.sha256"
    index = read_json(index_path)
    if index.get("schema_version") != V4_ROOT_INDEX_SCHEMA_VERSION:
        raise ArtifactValidationError("invalid V4 root artifact-index schema")
    sidecar = sidecar_path.read_text(encoding="ascii").strip().split()
    if len(sidecar) != 2 or sidecar[1] != "artifact_index.json":
        raise ArtifactValidationError("invalid root artifact-index SHA-256 sidecar")
    observed_index_hash = sha256_file(index_path)
    if sidecar[0] != observed_index_hash:
        raise ArtifactValidationError("root artifact-index SHA-256 mismatch")
    records = index.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ArtifactValidationError("root artifact index has no records")
    indexed: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ArtifactValidationError("root index record is not an object")
        relative = record.get("path")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative in indexed
        ):
            raise ArtifactValidationError(f"unsafe/duplicate root index path {relative!r}")
        indexed.add(relative)
        target = root / relative
        if not target.is_file() or target.is_symlink():
            raise ArtifactValidationError(f"indexed artifact is missing: {relative}")
        if target.stat().st_size != record.get("bytes"):
            raise ArtifactValidationError(f"indexed artifact size differs: {relative}")
        if not _HASH_RE.fullmatch(str(record.get("sha256", ""))):
            raise ArtifactValidationError(f"invalid indexed SHA-256: {relative}")
        if sha256_file(target) != record["sha256"]:
            raise ArtifactValidationError(f"indexed artifact hash differs: {relative}")
        if not record.get("role") or not record.get("media_type"):
            raise ArtifactValidationError(
                f"indexed artifact provenance is incomplete: {relative}"
            )
    on_disk = {
        relative
        for path in _regular_files(root)
        for relative in (_safe_relative(root, path),)
        if relative not in {"artifact_index.json", "artifact_index.sha256"}
    }
    if indexed != on_disk:
        raise ArtifactValidationError(
            "root artifact index coverage differs from disk: "
            f"unindexed={sorted(on_disk - indexed)}, "
            f"missing={sorted(indexed - on_disk)}"
        )
    if index.get("artifact_count") != len(records):
        raise ArtifactValidationError("root artifact_count is inconsistent")
    return {
        "artifact_count": len(records),
        "artifact_index_sha256": observed_index_hash,
        "full_coverage_verified": True,
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    information = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    information.compress_type = zipfile.ZIP_DEFLATED
    information.create_system = 3
    information.external_attr = 0o100644 << 16
    return information


def _write_deterministic_zip(
    output: Path,
    sources: Sequence[tuple[str, Path]],
) -> Path:
    names = [name for name, _ in sources]
    if not names or len(names) != len(set(names)):
        raise ArtifactValidationError("ZIP source list is empty or duplicated")
    if names != sorted(names):
        raise ArtifactValidationError("ZIP source list must be deterministically sorted")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name, source in sources:
                if not source.is_file() or source.is_symlink():
                    raise ArtifactValidationError(f"unsafe ZIP source: {source}")
                with (
                    source.open("rb") as input_stream,
                    archive.open(_zip_info(name), "w", force_zip64=True) as output_stream,
                ):
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _verify_zip_against_sources(
    archive_path: Path,
    sources: Sequence[tuple[str, Path]],
) -> None:
    expected = {name: sha256_file(path) for name, path in sources}
    digest_by_name: dict[str, str] = {}
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ArtifactValidationError(f"corrupt ZIP member in {archive_path}")
        for information in archive.infolist():
            if information.filename in digest_by_name:
                raise ArtifactValidationError("duplicate member in release ZIP")
            digest = hashlib.sha256()
            with archive.open(information) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digest_by_name[information.filename] = digest.hexdigest()
    if digest_by_name != expected:
        raise ArtifactValidationError(
            f"release ZIP contents differ from sources: {archive_path}"
        )


def _write_archive_sidecars(
    output: Path,
    *,
    role: str,
    sources: Sequence[tuple[str, Path]],
) -> tuple[Path, Path, dict[str, Any]]:
    digest = sha256_file(output)
    sidecar = Path(f"{output}.sha256")
    _atomic_write(sidecar, f"{digest}  {output.name}\n".encode("ascii"))
    manifest_path = Path(f"{output}.manifest.json")
    manifest = {
        "schema_version": V4_RELEASE_MANIFEST_SCHEMA_VERSION,
        "role": role,
        "archive": {
            "path": str(output),
            "bytes": output.stat().st_size,
            "sha256": digest,
        },
        "files": [
            {
                "path": name,
                "bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
            for name, source in sources
        ],
    }
    _write_json(manifest_path, manifest)
    return sidecar, manifest_path, manifest


def build_primary_locked_test_archive(
    locked_test_root: str | Path,
    output_path: str | Path,
    *,
    expected_commit: str | None = None,
    validation_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Archive every file in the validated primary raw bundle."""

    root = Path(locked_test_root).resolve()
    independently_validated = validate_raw_bundle(
        root,
        expected_commit=expected_commit,
        bundle_kind="locked_test",
    )
    if (
        validation_report is not None
        and dict(validation_report) != dict(independently_validated)
    ):
        raise ArtifactValidationError(
            "supplied raw validation report differs from independent revalidation"
        )
    validation_report = independently_validated
    required_proof = {
        "checksums_verified",
        "profile_recomputation_verified",
        "feasibility_recomputation_verified",
        "trajectory_metric_recomputation_verified",
    }
    if (
        validation_report.get("bundle_root") != str(root)
        or validation_report.get("raw_commit")
        != expected_commit
        or not all(validation_report.get(field) is True for field in required_proof)
    ):
        raise ArtifactValidationError("primary raw archive requires recomputation proof")
    sources = [
        (f"primary_locked_test/{path.relative_to(root).as_posix()}", path)
        for path in _regular_files(root)
    ]
    output = Path(output_path).resolve()
    _write_deterministic_zip(output, sources)
    _verify_zip_against_sources(output, sources)
    sidecar, manifest_path, manifest = _write_archive_sidecars(
        output, role="primary_locked_test_raw", sources=sources
    )
    return {
        **manifest,
        "path": str(output),
        "sha256_sidecar": str(sidecar),
        "manifest_path": str(manifest_path),
        "zip_contents_verified": True,
    }


def build_bounded_results_archive(
    results_root: str | Path,
    output_path: str | Path,
    *,
    max_file_bytes: int = 50 * 1024 * 1024,
    max_total_source_bytes: int = 250 * 1024 * 1024,
) -> dict[str, Any]:
    """Archive report outputs while categorically excluding raw/sample Parquet."""

    root = Path(results_root).resolve()
    verify_root_artifact_index(root)
    sources: list[tuple[str, Path]] = []
    excluded_high_volume: list[str] = []
    total = 0
    for path in _regular_files(root):
        relative = path.relative_to(root)
        if relative.parts[0] == "raw_runs" or path.suffix.lower() == ".parquet":
            continue
        relative_name = relative.as_posix()
        if relative_name in V4_BOUNDED_HIGH_VOLUME_EXCLUSIONS:
            excluded_high_volume.append(relative_name)
            continue
        if path.stat().st_size > max_file_bytes:
            raise ArtifactValidationError(
                f"bounded artifact exceeds per-file limit: {relative}"
            )
        total += path.stat().st_size
        sources.append((f"paper_evidence_v4/{relative.as_posix()}", path))
    if total > max_total_source_bytes:
        raise ArtifactValidationError(
            f"bounded results exceed source-byte limit: {total}"
        )
    sources.sort(key=lambda item: item[0])
    output = Path(output_path).resolve()
    _write_deterministic_zip(output, sources)
    _verify_zip_against_sources(output, sources)
    sidecar, manifest_path, manifest = _write_archive_sidecars(
        output, role="bounded_results", sources=sources
    )
    return {
        **manifest,
        "path": str(output),
        "sha256_sidecar": str(sidecar),
        "manifest_path": str(manifest_path),
        "raw_runs_excluded": True,
        "parquet_excluded": True,
        "excluded_high_volume_artifacts": sorted(excluded_high_volume),
        "excluded_high_volume_artifacts_remain_root_indexed": True,
        "source_bytes": total,
        "zip_contents_verified": True,
    }


def _asset_record(path: Path, role: str) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "name": path.name,
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_release_archives(
    *,
    results_root: str | Path,
    locked_test_root: str | Path,
    release_dir: str | Path,
    raw_commit: str,
    protocol_path: str | Path | None = None,
    config_lock_path: str | Path | None = None,
    status_path: str | Path | None = None,
    preregistration_status_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    locked_test_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic archives plus a complete release-ready inventory."""

    if not _COMMIT_RE.fullmatch(raw_commit):
        raise ArtifactValidationError("raw_commit must be 40 lowercase hex")
    results = Path(results_root).resolve()
    release = Path(release_dir).resolve()
    if release.exists():
        raise FileExistsError(
            f"refusing to overwrite an existing V4 release directory: {release}"
        )
    release.mkdir(parents=True)
    short_sha = raw_commit[:7]
    try:
        primary = build_primary_locked_test_archive(
            locked_test_root,
            release / f"primary_locked_test_v4-{short_sha}.zip",
            expected_commit=raw_commit,
            validation_report=locked_test_validation,
        )
        bounded = build_bounded_results_archive(
            results, release / f"paper_evidence_v4_bounded-{short_sha}.zip"
        )
        assets: list[dict[str, Any]] = []
        for report in (primary, bounded):
            assets.extend(
                (
                    _asset_record(Path(report["path"]), str(report["role"])),
                    _asset_record(Path(report["sha256_sidecar"]), "sha256_sidecar"),
                    _asset_record(Path(report["manifest_path"]), "archive_manifest"),
                )
            )
        source_assets = (
            ("protocol", protocol_path, None),
            ("config_lock", config_lock_path, None),
            (
                "post_test_protocol_status",
                status_path or results / "protocol_status_v4.json",
                "protocol_status_v4.json",
            ),
            (
                "frozen_preregistration_status",
                preregistration_status_path
                or Path(__file__).resolve().parents[1] / "protocol_status_v4.json",
                "preregistration_status_v4.json",
            ),
            (
                "split_manifest",
                manifest_path
                or Path(__file__).resolve().parents[1] / "split_manifest_v4.json",
                None,
            ),
        )
        for role, optional_source, destination_name in source_assets:
            if optional_source is None:
                continue
            source = Path(optional_source).resolve()
            destination = release / (destination_name or source.name)
            if source != destination:
                atomic_copy_file(source, destination)
            assets.append(_asset_record(destination, role))
            if role == "split_manifest":
                digest = sha256_file(destination)
                checksum = release / f"{destination.name}.sha256"
                _atomic_write(
                    checksum,
                    f"{digest}  {destination.name}\n".encode("ascii"),
                )
                assets.append(_asset_record(checksum, "split_manifest_sha256"))
        names = [record["name"] for record in assets]
        if len(names) != len(set(names)):
            raise ArtifactValidationError("release asset names are not unique")
        inventory = {
            "schema_version": V4_RELEASE_MANIFEST_SCHEMA_VERSION,
            "release_tag": f"paper-evidence-v4-{short_sha}",
            "raw_commit": raw_commit,
            "upload_performed": False,
            "local_release_ready": True,
            "asset_count": len(assets),
            "assets": sorted(assets, key=lambda row: str(row["name"])),
        }
        inventory_path = _write_json(
            release / "release_asset_inventory.json", inventory
        )
        return {**inventory, "inventory_path": str(inventory_path)}
    except BaseException:
        shutil.rmtree(release, ignore_errors=True)
        raise


def finalize_v4_results(
    *,
    results_root: str | Path,
    locked_test_root: str | Path,
    oracle_root: str | Path,
    raw_commit: str,
    phase_a_result: Mapping[str, Any] | None = None,
    confirmation_context: Mapping[str, Any] | None = None,
    report_only: bool = False,
    validation_report: Mapping[str, Any] | None = None,
    reporting_commit: str | None = None,
    release_dir: str | Path | None = None,
    protocol_path: str | Path | None = None,
    config_lock_path: str | Path | None = None,
    status_path: str | Path | None = None,
    require_all_statistical_artifacts: bool = True,
    generation_command: Sequence[str] = (),
) -> dict[str, Any]:
    """Final QA entry point callable after figures/handoff have been written."""

    root = ensure_v4_results_layout(results_root)
    if report_only:
        expected_report = validate_report_only_inputs(
            results_root=root,
            raw_commit=raw_commit,
            locked_test_root=locked_test_root,
            oracle_root=oracle_root,
        )
        if validation_report is not None:
            if (
                validation_report.get("raw_commit") != raw_commit
                or not validation_report.get("all_raw_checksums_verified")
                or not validation_report.get("all_independent_recomputations_verified")
            ):
                raise ArtifactValidationError(
                    "supplied report-only validation proof is invalid"
                )
        validation_report = expected_report
        locked_report = expected_report["locked_test"]
        oracle_report = expected_report["oracle_diagnostic"]
    else:
        locked_report = validate_raw_bundle(
            locked_test_root,
            expected_commit=raw_commit,
            bundle_kind="locked_test",
        )
        oracle_report = validate_raw_bundle(
            oracle_root,
            expected_commit=raw_commit,
            bundle_kind="oracle_diagnostic",
        )
    statistical_report: Mapping[str, Any] | None = None
    if require_all_statistical_artifacts:
        statistical_report = validate_statistical_artifacts(
            root,
            raw_metrics_path=Path(locked_test_root) / "metrics_by_trajectory.csv",
        )
    negative_report = validate_negative_result_preservation(root)
    raw_validation_manifest = {
        "schema_version": V4_RAW_BUNDLE_SCHEMA_VERSION,
        "report_only": report_only,
        "raw_commit": raw_commit,
        "locked_test": locked_report,
        "oracle_diagnostic": oracle_report,
        "phase_a_compatibility": dict(phase_a_result or {}),
        "confirmation_context": dict(confirmation_context or {}),
        "statistical_artifact_validation": statistical_report,
        "negative_result_preservation": negative_report,
    }
    _write_json(
        root / "manifests" / "raw_bundle_validation.json",
        raw_validation_manifest,
    )
    index = build_root_artifact_index(
        root,
        raw_commit=raw_commit,
        reporting_commit=reporting_commit,
        generation_command=generation_command,
    )
    releases = build_release_archives(
        results_root=root,
        locked_test_root=locked_test_root,
        release_dir=release_dir or root.parent / f"paper-evidence-v4-{raw_commit[:7]}",
        raw_commit=raw_commit,
        protocol_path=protocol_path,
        config_lock_path=config_lock_path,
        status_path=status_path,
        locked_test_validation=locked_report,
    )
    return {
        "status": "finalized",
        "report_only": report_only,
        "raw_validation": raw_validation_manifest,
        "root_index": index,
        "release_assets": releases,
    }


def _git(repo_root: Path, *arguments: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=text,
    )
    if completed.returncode != 0:
        stderr = completed.stderr if text else completed.stderr.decode(errors="replace")
        stdout = completed.stdout if text else completed.stdout.decode(errors="replace")
        raise ArtifactValidationError(
            f"git {' '.join(arguments)} failed: {(stderr or stdout).strip()}"
        )
    return completed.stdout


def _tracked_v3_paths(repo_root: Path, reference_commit: str) -> list[str]:
    arguments = (
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        reference_commit,
        "--",
        "results/paper_evidence_v3",
        *V3_ROOT_EVIDENCE_PATHS,
    )
    output = _git(repo_root, *arguments, text=False)
    assert isinstance(output, bytes)
    return sorted(
        path.decode("utf-8")
        for path in output.split(b"\0")
        if path
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def check_v3_immutability(
    repo_root: str | Path,
    *,
    reference_commit: str = V3_FROZEN_REFERENCE_COMMIT,
    baseline_hashes: Mapping[str, str] | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compare every frozen-V3 byte against the fixed merged-main tree.

    The large raw archive is intentionally not downloaded.  Its immutable
    release URL, byte count, and SHA-256 declaration are carried into the proof.
    """

    root = Path(repo_root).resolve()
    head = str(_git(root, "rev-parse", "HEAD")).strip()
    if not _COMMIT_RE.fullmatch(head):
        raise ArtifactValidationError("repository HEAD is not a full commit")
    if not _COMMIT_RE.fullmatch(reference_commit):
        raise ArtifactValidationError("V3 reference commit is not a full commit")
    resolved_reference = str(
        _git(root, "rev-parse", f"{reference_commit}^{{commit}}")
    ).strip()
    if resolved_reference != reference_commit:
        raise ArtifactValidationError("V3 reference commit did not resolve exactly")
    tracked = _tracked_v3_paths(root, reference_commit)
    head_tracked = _tracked_v3_paths(root, "HEAD")
    if tracked != head_tracked:
        raise ArtifactValidationError(
            "tracked V3 path set differs from frozen base-main tree: "
            f"added={sorted(set(head_tracked) - set(tracked))}, "
            f"removed={sorted(set(tracked) - set(head_tracked))}"
        )
    if not tracked:
        raise ArtifactValidationError("no tracked V3 evidence files found")
    records: list[dict[str, Any]] = []
    for relative in tracked:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ArtifactValidationError(
                f"tracked frozen V3 file is missing or unsafe: {relative}"
            )
        head_bytes = _git(root, "show", f"HEAD:{relative}", text=False)
        reference_bytes = _git(
            root, "show", f"{reference_commit}:{relative}", text=False
        )
        assert isinstance(head_bytes, bytes)
        assert isinstance(reference_bytes, bytes)
        working_hash = sha256_file(path)
        head_hash = _sha256_bytes(head_bytes)
        reference_hash = _sha256_bytes(reference_bytes)
        if working_hash != head_hash:
            raise ArtifactValidationError(
                f"V3 byte identity differs from Git HEAD: {relative}"
            )
        if working_hash != reference_hash:
            raise ArtifactValidationError(
                f"V3 byte identity differs from frozen base-main tree: {relative}"
            )
        baseline_hash = (baseline_hashes or {}).get(relative)
        if baseline_hash is not None:
            if not _HASH_RE.fullmatch(baseline_hash):
                raise ArtifactValidationError(
                    f"invalid V3 baseline SHA-256 for {relative}"
                )
            if working_hash != baseline_hash:
                raise ArtifactValidationError(
                    f"V3 byte identity differs from declared baseline: {relative}"
                )
        records.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "working_tree_sha256": working_hash,
                "git_head_sha256": head_hash,
                "frozen_reference_sha256": reference_hash,
                "baseline_sha256": baseline_hash,
                "byte_identical_to_git_head": True,
                "byte_identical_to_frozen_reference": True,
                "byte_identical_to_baseline": (
                    None if baseline_hash is None else True
                ),
            }
        )
    if baseline_hashes:
        unknown = set(baseline_hashes) - set(tracked)
        if unknown:
            raise ArtifactValidationError(
                f"V3 baseline contains untracked/out-of-scope paths: {sorted(unknown)}"
            )
    status = json.loads(
        _git(
            root,
            "show",
            f"{reference_commit}:protocol_status_v3.json",
            text=True,
        )
    )
    release = status.get("primary_evidence", status.get("release_evidence", {}))
    archive_hash = release.get("archive_sha256")
    archive_bytes = release.get("archive_bytes")
    archive_url = release.get("archive_url")
    if (
        not _HASH_RE.fullmatch(str(archive_hash or ""))
        or not isinstance(archive_bytes, int)
        or isinstance(archive_bytes, bool)
        or archive_bytes <= 0
        or not isinstance(archive_url, str)
        or not archive_url.startswith("https://")
    ):
        raise ArtifactValidationError(
            "tracked V3 status lacks a valid remote archive declaration"
        )
    proof = {
        "schema_version": V3_IMMUTABILITY_PROOF_SCHEMA_VERSION,
        "git_head": head,
        "frozen_reference_commit": reference_commit,
        "tracked_scope_only": True,
        "raw_archive_downloaded": False,
        "tracked_file_count": len(records),
        "tracked_path_set_identical_to_frozen_reference": True,
        "all_tracked_files_byte_identical_to_git_head": True,
        "all_tracked_files_byte_identical_to_frozen_reference": True,
        "all_declared_baselines_verified": True,
        "remote_archive": {
            "url": archive_url,
            "bytes": archive_bytes,
            "sha256": archive_hash,
            "verification_scope": "tracked_declaration_only_no_download",
        },
        "files": records,
    }
    if output_path is not None:
        _write_json(Path(output_path).resolve(), proof)
    return proof


__all__ = [
    "V3_IMMUTABILITY_PROOF_SCHEMA_VERSION",
    "V3_ROOT_EVIDENCE_PATHS",
    "V4_CSV_SCHEMAS",
    "V4_RAW_REQUIRED_ARTIFACTS",
    "V4_RELEASE_MANIFEST_SCHEMA_VERSION",
    "V4_REQUIRED_STATISTICAL_CSVS",
    "V4_RESULTS_DIRECTORIES",
    "V4_ROOT_INDEX_SCHEMA_VERSION",
    "atomic_copy_and_promote_bundle",
    "atomic_copy_file",
    "atomic_promote_directory",
    "build_bounded_results_archive",
    "build_primary_locked_test_archive",
    "build_release_archives",
    "build_root_artifact_index",
    "check_v3_immutability",
    "create_v4_results_layout",
    "ensure_v4_results_layout",
    "finalize_v4_results",
    "stage_and_promote_bundle",
    "validate_negative_result_preservation",
    "validate_raw_bundle",
    "validate_report_only_inputs",
    "validate_statistical_artifacts",
    "validate_v4_csv_schema",
    "verify_root_artifact_index",
]
