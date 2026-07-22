"""Versioned artifact writing, hashing, validation, and independent rebuilds.

Formal result summaries are derived from ``samples.parquet`` through the same
public recomputation function used by QA.  The writer refuses a dirty source
tree when ``require_clean`` is enabled, records the exact commit and command,
uses atomic file replacement, and emits SHA-256 checksums for every registered
artifact.  Empty CSV tables and unexplained non-finite numeric values fail at
write and validation time.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .metrics import metrics_by_trajectory, summary_metrics
from .schema import SCHEMA_VERSION as SAMPLE_SCHEMA_VERSION
from .schema import (
    read_parquet,
    recompute_sample_feasibility,
    validate_samples,
    write_parquet,
)

ARTIFACT_SCHEMA_VERSION = "otg.artifact-index.v1"
RUN_MANIFEST_SCHEMA_VERSION = "otg.run-manifest.v1"
CHECKSUM_SCHEMA_VERSION = "otg.checksums.v1"
PRIMARY_EVIDENCE_ARCHIVE_SCHEMA_VERSION = "otg.primary-evidence-archive.v1"

PRIMARY_EVIDENCE_REQUIRED_ARTIFACTS = (
    "samples.parquet",
    "metrics_by_trajectory.csv",
    "constraint_audit.csv",
    "fallback_events.csv",
    "failures.csv",
    "runtime_benchmark.csv",
    "resolved_config.yaml",
    "method_matrix.json",
    "split_manifest.json",
    "run.json",
    "artifact_checksums.json",
)

REQUIRED_RUN_ARTIFACTS = frozenset(
    {
        "run.json",
        "resolved_config.yaml",
        "data_manifest.json",
        "split_manifest.json",
        "samples.parquet",
        "metrics_by_trajectory.csv",
        "summary_metrics.csv",
        "failures.csv",
        "artifact_checksums.json",
    }
)


class ArtifactValidationError(ValueError):
    """Raised when an artifact bundle is incomplete or not reproducible."""


@dataclass(frozen=True)
class GitState:
    commit: str
    branch: str | None
    dirty: bool
    dirty_paths: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    role: str
    media_type: str
    bytes: int
    sha256: str
    generated_by: str


def _run_git(repo_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ArtifactValidationError(
            f"git {' '.join(arguments)} failed in {repo_root}: {detail}"
        )
    return completed.stdout.strip()


def capture_git_state(repo_root: str | Path) -> GitState:
    """Read commit, branch, and complete porcelain status from a repository."""

    root = Path(repo_root).resolve()
    commit = _run_git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ArtifactValidationError(f"invalid git commit returned: {commit!r}")
    branch_value = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = None if branch_value == "HEAD" else branch_value
    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    dirty_paths = tuple(line for line in status.splitlines() if line.strip())
    return GitState(
        commit=commit,
        branch=branch,
        dirty=bool(dirty_paths),
        dirty_paths=dirty_paths,
    )


def assert_clean_commit(
    repo_root: str | Path, *, expected_commit: str | None = None
) -> GitState:
    """Require a clean worktree at the optionally locked commit."""

    state = capture_git_state(repo_root)
    if state.dirty:
        preview = ", ".join(state.dirty_paths[:8])
        raise ArtifactValidationError(
            f"formal run requires a clean worktree; dirty paths: {preview}"
        )
    if expected_commit is not None and state.commit != expected_commit:
        raise ArtifactValidationError(
            f"commit mismatch: expected {expected_commit}, observed {state.commit}"
        )
    return state


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for hashes and manifests."""

    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ArtifactValidationError(
            f"value is not canonical JSON: {error}"
        ) from error
    return (text + "\n").encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: str | Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    """Stream a file into a lowercase SHA-256 digest."""

    target = Path(path)
    if not target.is_file():
        raise ArtifactValidationError(f"cannot hash missing file {target}")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def write_json(path: str | Path, value: Any) -> Path:
    """Atomically write canonical, NaN-prohibiting JSON."""

    return _atomic_write(Path(path), canonical_json_bytes(value))


def read_json(path: str | Path) -> Any:
    target = Path(path)
    try:
        with target.open("r", encoding="utf-8") as stream:
            return json.load(
                stream,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ArtifactValidationError(
                        f"{target} contains forbidden JSON constant {value}"
                    )
                ),
            )
    except json.JSONDecodeError as error:
        raise ArtifactValidationError(
            f"invalid JSON artifact {target}: {error}"
        ) from error


def _validate_cell(
    value: Any,
    *,
    field: str,
    row_index: int,
    allowed_missing_fields: frozenset[str],
) -> None:
    if value is None or value == "":
        if field not in allowed_missing_fields:
            raise ArtifactValidationError(
                f"CSV row {row_index}, field {field}: missing value is unexplained"
            )
        return
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        raise ArtifactValidationError(
            f"CSV row {row_index}, field {field}: NaN/infinity is forbidden"
        )
    if isinstance(value, np.generic):
        value.item()


def write_csv(
    path: str | Path,
    records: Sequence[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str] | None = None,
    allowed_missing_fields: Iterable[str] = (),
    allow_empty: bool = False,
) -> Path:
    """Atomically write a rectangular CSV table.

    Header-only output is opt-in and requires explicit ``fieldnames``.  It is
    used for a legitimate zero-row ``failures.csv``; analytical result tables
    retain the fail-closed non-empty default.
    """

    if not records:
        if not allow_empty:
            raise ArtifactValidationError("refusing to write an empty CSV artifact")
        if fieldnames is None:
            raise ArtifactValidationError(
                "header-only CSV requires explicit fieldnames"
            )
    columns = list(records[0]) if fieldnames is None else list(fieldnames)
    if not columns or len(set(columns)) != len(columns):
        raise ArtifactValidationError("CSV fieldnames are empty or duplicated")
    expected = set(columns)
    allowed = frozenset(allowed_missing_fields)
    for row_index, row in enumerate(records):
        if set(row) != expected:
            raise ArtifactValidationError(
                f"CSV row {row_index} columns differ: "
                f"missing={sorted(expected - set(row))}, extra={sorted(set(row) - expected)}"
            )
        for field in columns:
            _validate_cell(
                row[field],
                field=field,
                row_index=row_index,
                allowed_missing_fields=allowed,
            )

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=columns,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(records)
        stream.seek(0)
        payload = stream.read().encode("utf-8")
    return _atomic_write(Path(path), payload)


def read_csv(
    path: str | Path,
    *,
    required_fields: Iterable[str] = (),
    allowed_missing_fields: Iterable[str] = (),
    allow_empty: bool = False,
) -> list[dict[str, str]]:
    """Read and structurally validate a non-empty CSV artifact."""

    target = Path(path)
    with target.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(
            reader.fieldnames
        ):
            raise ArtifactValidationError(f"{target} has an invalid CSV header")
        missing = set(required_fields) - set(reader.fieldnames)
        if missing:
            raise ArtifactValidationError(
                f"{target} is missing required columns {sorted(missing)}"
            )
        rows = list(reader)
    if not rows and not allow_empty:
        raise ArtifactValidationError(f"CSV artifact {target} is empty")
    allowed = frozenset(allowed_missing_fields)
    for row_index, row in enumerate(rows):
        for field, value in row.items():
            if value == "" and field not in allowed:
                raise ArtifactValidationError(
                    f"{target} row {row_index}, field {field}: empty value is unexplained"
                )
            normalized = value.strip().lower()
            if normalized in {
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
                    f"{target} row {row_index}, field {field}: non-finite token"
                )
    return rows


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def runtime_environment() -> dict[str, Any]:
    """Capture the reproducibility metadata relevant to runtime benchmarks."""

    packages = {
        name: _package_version(name)
        for name in ("numpy", "scipy", "pandas", "pyarrow", "ruckig", "osqp")
    }
    thread_variables = {
        name: os.environ.get(name)
        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
        )
    }
    affinity: list[int] | None = None
    if hasattr(os, "sched_getaffinity"):
        try:
            affinity = sorted(os.sched_getaffinity(0))  # type: ignore[attr-defined]
        except OSError:
            affinity = None
    blas: dict[str, Any] = {}
    try:
        configuration = np.__config__.CONFIG
        blas = dict(configuration.get("Build Dependencies", {}).get("blas", {}))
    except (AttributeError, TypeError):
        pass
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "cpu_affinity": affinity,
        "packages": packages,
        "blas": blas,
        "thread_environment": thread_variables,
    }


def create_run_manifest(
    *,
    run_id: str,
    command: Sequence[str],
    resolved_config: Mapping[str, Any],
    repo_root: str | Path,
    expected_commit: str | None = None,
    require_clean: bool = True,
    started_at: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a run manifest after checking source-control provenance."""

    if not run_id or not run_id.strip():
        raise ArtifactValidationError("run_id cannot be empty")
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ArtifactValidationError("command must be a non-empty argv sequence")
    state = (
        assert_clean_commit(repo_root, expected_commit=expected_commit)
        if require_clean
        else capture_git_state(repo_root)
    )
    if expected_commit is not None and state.commit != expected_commit:
        raise ArtifactValidationError(
            f"commit mismatch: expected {expected_commit}, observed {state.commit}"
        )
    timestamp = started_at or datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "sample_schema_version": SAMPLE_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": timestamp,
        "command": list(command),
        "resolved_config": dict(resolved_config),
        "resolved_config_sha256": canonical_json_hash(resolved_config),
        "git_commit": state.commit,
        "git_branch": state.branch,
        "git_worktree_dirty": state.dirty,
        "git_dirty_paths": list(state.dirty_paths),
        "environment": runtime_environment(),
    }
    if extra:
        overlap = set(manifest) & set(extra)
        if overlap:
            raise ArtifactValidationError(
                f"extra manifest fields overwrite canonical fields: {sorted(overlap)}"
            )
        manifest.update(extra)
    canonical_json_bytes(manifest)
    return manifest


def validate_run_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_commit: str | None = None,
    require_clean: bool = True,
) -> None:
    required = {
        "schema_version",
        "sample_schema_version",
        "run_id",
        "started_at",
        "command",
        "resolved_config",
        "resolved_config_sha256",
        "git_commit",
        "git_worktree_dirty",
        "environment",
    }
    missing = required - set(manifest)
    if missing:
        raise ArtifactValidationError(
            f"run manifest is missing fields {sorted(missing)}"
        )
    if manifest["schema_version"] != RUN_MANIFEST_SCHEMA_VERSION:
        raise ArtifactValidationError("unsupported run manifest schema")
    if manifest["sample_schema_version"] != SAMPLE_SCHEMA_VERSION:
        raise ArtifactValidationError("sample schema version mismatch")
    commit = str(manifest["git_commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ArtifactValidationError("run manifest has an invalid git commit")
    if expected_commit is not None and commit != expected_commit:
        raise ArtifactValidationError(
            f"manifest commit mismatch: expected {expected_commit}, got {commit}"
        )
    if not isinstance(manifest["git_worktree_dirty"], bool):
        raise ArtifactValidationError("git_worktree_dirty must be boolean")
    if require_clean and manifest["git_worktree_dirty"]:
        raise ArtifactValidationError("formal run manifest records a dirty worktree")
    observed_hash = canonical_json_hash(manifest["resolved_config"])
    if observed_hash != manifest["resolved_config_sha256"]:
        raise ArtifactValidationError("resolved config hash does not match manifest")
    row_counts = manifest.get("artifact_row_counts", {})
    if not isinstance(row_counts, dict) or any(
        not isinstance(path, str)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for path, count in row_counts.items()
    ):
        raise ArtifactValidationError(
            "artifact_row_counts must map paths to nonnegative integers"
        )
    canonical_json_bytes(dict(manifest))


def _relative_file(root: Path, path: Path) -> str:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    try:
        relative = path_resolved.relative_to(root_resolved)
    except ValueError as error:
        raise ArtifactValidationError(
            f"artifact {path_resolved} is outside bundle root {root_resolved}"
        ) from error
    if path_resolved == root_resolved or not path_resolved.is_file():
        raise ArtifactValidationError(f"artifact is not a file: {path_resolved}")
    return relative.as_posix()


def _media_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".csv": "text/csv",
        ".parquet": "application/vnd.apache.parquet",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "application/octet-stream")


def build_artifact_record(
    root: str | Path,
    path: str | Path,
    *,
    role: str,
    generated_by: str,
) -> ArtifactRecord:
    root_path = Path(root)
    target = Path(path)
    relative = _relative_file(root_path, target)
    if not role:
        raise ArtifactValidationError("artifact role cannot be empty")
    return ArtifactRecord(
        path=relative,
        role=role,
        media_type=_media_type(target),
        bytes=target.stat().st_size,
        sha256=sha256_file(target),
        generated_by=generated_by,
    )


def write_checksums(
    root: str | Path,
    paths: Sequence[str | Path],
    *,
    output_name: str = "artifact_checksums.json",
) -> Path:
    """Write deterministic hashes for an explicit, non-empty artifact set."""

    root_path = Path(root).resolve()
    if not paths:
        raise ArtifactValidationError("cannot write an empty checksum set")
    checksums: dict[str, dict[str, Any]] = {}
    for path in paths:
        target = Path(path).resolve()
        relative = _relative_file(root_path, target)
        if relative == output_name:
            raise ArtifactValidationError("checksum artifact cannot hash itself")
        if relative in checksums:
            raise ArtifactValidationError(f"duplicate checksum target {relative}")
        checksums[relative] = {
            "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
        }
    payload = {
        "schema_version": CHECKSUM_SCHEMA_VERSION,
        "algorithm": "sha256",
        "artifacts": dict(sorted(checksums.items())),
    }
    return write_json(root_path / output_name, payload)


def verify_checksums(
    root: str | Path, checksum_path: str | Path | None = None
) -> list[str]:
    """Verify every declared size and SHA-256; return relative paths."""

    root_path = Path(root).resolve()
    target = (
        root_path / "artifact_checksums.json"
        if checksum_path is None
        else Path(checksum_path)
    )
    payload = read_json(target)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CHECKSUM_SCHEMA_VERSION
    ):
        raise ArtifactValidationError("invalid checksum artifact schema")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ArtifactValidationError("checksum artifact has no entries")
    verified: list[str] = []
    for relative, expected in sorted(artifacts.items()):
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ArtifactValidationError(f"unsafe checksum path {relative!r}")
        artifact = root_path / relative
        if not artifact.is_file():
            raise ArtifactValidationError(
                f"checksummed artifact is missing: {relative}"
            )
        if artifact.stat().st_size != expected.get("bytes"):
            raise ArtifactValidationError(f"artifact size mismatch: {relative}")
        observed = sha256_file(artifact)
        if observed != expected.get("sha256"):
            raise ArtifactValidationError(f"artifact hash mismatch: {relative}")
        verified.append(relative)
    return verified


CsvValidator = Callable[[Path], None]


def _csv_schema_validator(
    required_fields: Iterable[str],
    *,
    allowed_missing_fields: Iterable[str] = (),
    allow_empty: bool = False,
) -> CsvValidator:
    def validate(path: Path) -> None:
        read_csv(
            path,
            required_fields=required_fields,
            allowed_missing_fields=allowed_missing_fields,
            allow_empty=allow_empty,
        )

    return validate


def _validate_constraint_audit(path: Path) -> None:
    fallback_optional = {
        "max_abs_velocity",
        "max_abs_acceleration",
        "max_sampled_jerk",
        "velocity_margin",
        "acceleration_margin",
        "jerk_margin",
    }
    rows = read_csv(
        path,
        required_fields={"trajectory_id", "audit_method", "violation_count"},
        allowed_missing_fields={
            "max_new_jerk",
            "max_internal_jerk",
            "velocity_max_time_s",
            "acceleration_max_time_s",
            "jerk_max_time_s",
            "max_abs_velocity_time_s",
            "max_abs_acceleration_time_s",
            "max_sampled_jerk_time_s",
            "max_new_jerk_time_s",
            "max_internal_jerk_time_s",
            "internal_jerk_margin",
            "new_jerk_margin",
            *fallback_optional,
        },
    )
    for row_index, row in enumerate(rows):
        if not row.get("joint_id") and not row.get("joint_index"):
            raise ArtifactValidationError(
                f"constraint audit row {row_index} needs joint_id or joint_index"
            )
        missing_fallback_values = {
            field for field in fallback_optional if field in row and row[field] == ""
        }
        fallback = str(row.get("fallback", "")).strip().lower() == "true"
        analytic_unavailable = str(
            row.get("audit_method", "")
        ) == "analytic_profile_extrema" and missing_fallback_values <= {
            "max_sampled_jerk"
        }
        if missing_fallback_values and not fallback and not analytic_unavailable:
            raise ArtifactValidationError(
                f"constraint audit row {row_index} has unexplained unavailable "
                f"values {sorted(missing_fallback_values)}"
            )


def _validate_trajectory_metrics(path: Path) -> None:
    required = {"run_id", "dataset_id", "trajectory_id", "position_rmse", "n_samples"}
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ArtifactValidationError(f"{path} has no CSV header")
        fields = set(reader.fieldnames)
    read_csv(
        path,
        required_fields=required,
        # Method-specific layers are intentionally sparse across rows, but core
        # identity/tracking fields remain mandatory and non-empty.
        allowed_missing_fields=fields - required,
    )


DEFAULT_SCHEMA_HOOKS: dict[str, Callable[[Path], Any]] = {
    "samples.parquet": lambda path: read_parquet(path, validate=True),
    "metrics_by_trajectory.csv": _validate_trajectory_metrics,
    "summary_metrics.csv": _csv_schema_validator(
        {"run_id", "split", "metric", "n_trajectories", "mean", "median", "iqr"}
    ),
    "paired_comparisons.csv": _csv_schema_validator(
        {
            "comparison_id",
            "metric",
            "n_trajectories",
            "absolute_difference",
            "relative_difference",
            "unadjusted_p_value",
            "holm_adjusted_p_value",
        },
        allowed_missing_fields={
            "effect_size",
            "effect_size_ci_low",
            "effect_size_ci_high",
        },
    ),
    "confidence_intervals.csv": _csv_schema_validator(
        {"method", "metric", "n_trajectories", "mean_ci_low", "mean_ci_high"}
    ),
    "constraint_audit.csv": _validate_constraint_audit,
    "runtime_benchmark.csv": _csv_schema_validator(
        {
            "method",
            "runtime_p50_us",
            "runtime_p90_us",
            "runtime_p99_us",
            "runtime_p99_9_us",
            "runtime_max_us",
            "runtime_deadline_miss_rate",
        }
    ),
    "failures.csv": _csv_schema_validator(
        {"run_id", "trajectory_id", "k", "failure_type", "reason"},
        allowed_missing_fields={"k"},
        allow_empty=True,
    ),
    "runtime_repetition_failures.csv": _csv_schema_validator(
        {
            "run_id",
            "method_id",
            "trajectory_id",
            "k",
            "failure_type",
            "reason",
            "repetition",
            "dof",
        },
        allowed_missing_fields={"k"},
    ),
    "fallback_events.csv": _csv_schema_validator(
        {"run_id", "trajectory_id", "k", "fallback_reason"},
        allow_empty=True,
    ),
}


def validate_artifact_schema(
    path: str | Path,
    *,
    hooks: Mapping[str, Callable[[Path], Any]] | None = None,
) -> None:
    """Run a built-in or caller-provided validator selected by basename."""

    target = Path(path)
    validators = dict(DEFAULT_SCHEMA_HOOKS)
    if hooks:
        validators.update(hooks)
    validator = validators.get(target.name)
    if validator is None:
        if target.suffix == ".json":
            read_json(target)
        elif target.suffix == ".csv":
            read_csv(target)
        elif not target.is_file() or target.stat().st_size == 0:
            raise ArtifactValidationError(f"artifact is missing or empty: {target}")
        return
    validator(target)


def recompute_summary_from_samples(
    samples_path: str | Path,
    *,
    output_field: str | None = None,
    settle_tolerance: float = 1e-3,
    max_lag_s: float = 1.0,
    deadline_us: float | None = None,
    motion_limits: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    summary_group_fields: Sequence[str] = (
        "run_id",
        "split",
        "method",
        "scenario_id",
    ),
    summary_metric_fields: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Independently rebuild trajectory and summary tables from Parquet."""

    samples = read_parquet(samples_path, validate=True)
    trajectory = metrics_by_trajectory(
        samples,
        output_field=output_field,
        settle_tolerance=settle_tolerance,
        max_lag_s=max_lag_s,
        deadline_us=deadline_us,
        motion_limits=motion_limits,
        context=context,
    )
    summary = summary_metrics(
        trajectory,
        group_fields=summary_group_fields,
        metric_fields=summary_metric_fields,
    )
    return trajectory, summary


def _parse_csv_scalar(value: str) -> Any:
    if value in {"True", "False"}:
        return value == "True"
    try:
        integer = int(value)
        if str(integer) == value:
            return integer
    except ValueError:
        pass
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except ValueError:
        pass
    return value


def _normalize_csv_rows(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    with target.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ArtifactValidationError(f"{target} has no CSV header")
        fields = tuple(reader.fieldnames)
    return [
        {field: _parse_csv_scalar(value) for field, value in row.items() if value != ""}
        for row in read_csv(target, allowed_missing_fields=fields)
    ]


def assert_records_close(
    expected: Sequence[Mapping[str, Any]],
    observed: Sequence[Mapping[str, Any]],
    *,
    key_fields: Sequence[str],
    rtol: float = 1e-12,
    atol: float = 1e-12,
) -> None:
    """Compare two record tables by stable keys and strict column equality."""

    if not expected or not observed:
        raise ArtifactValidationError("cannot compare empty record tables")

    def index(records: Sequence[Mapping[str, Any]], label: str):
        output: dict[tuple[Any, ...], Mapping[str, Any]] = {}
        for row_number, row in enumerate(records):
            if any(field not in row for field in key_fields):
                raise ArtifactValidationError(
                    f"{label} row {row_number} lacks comparison key"
                )
            key = tuple(row[field] for field in key_fields)
            if key in output:
                raise ArtifactValidationError(f"{label} has duplicate key {key}")
            output[key] = row
        return output

    expected_index = index(expected, "expected")
    observed_index = index(observed, "observed")
    if set(expected_index) != set(observed_index):
        raise ArtifactValidationError("recomputed artifact keys differ")
    for key in expected_index:
        left = expected_index[key]
        right = observed_index[key]
        if set(left) != set(right):
            raise ArtifactValidationError(
                f"recomputed columns differ for {key}: {set(left) ^ set(right)}"
            )
        for field in left:
            left_value = left[field]
            right_value = right[field]
            numeric = isinstance(
                left_value, (int, float, np.number)
            ) and not isinstance(left_value, (bool, np.bool_))
            if numeric:
                if not isinstance(
                    right_value, (int, float, np.number)
                ) or not np.isclose(
                    float(left_value), float(right_value), rtol=rtol, atol=atol
                ):
                    raise ArtifactValidationError(
                        f"recomputed value differs for {key}.{field}: "
                        f"{left_value!r} versus {right_value!r}"
                    )
            elif left_value != right_value:
                raise ArtifactValidationError(
                    f"recomputed value differs for {key}.{field}: "
                    f"{left_value!r} versus {right_value!r}"
                )


def verify_recomputed_summary(
    samples_path: str | Path,
    trajectory_metrics_path: str | Path,
    summary_path: str | Path,
    **recompute_arguments: Any,
) -> None:
    """Rebuild both published tables and fail on any numeric disagreement."""

    trajectory, summary = recompute_summary_from_samples(
        samples_path, **recompute_arguments
    )
    observed_trajectory = _normalize_csv_rows(trajectory_metrics_path)
    observed_summary = _normalize_csv_rows(summary_path)
    assert_records_close(
        trajectory,
        observed_trajectory,
        key_fields=(
            "run_id",
            "dataset_id",
            "session_id",
            "trajectory_id",
            "scenario_id",
            "method",
        ),
    )
    group_fields = tuple(
        recompute_arguments.get(
            "summary_group_fields",
            ("run_id", "split", "method", "scenario_id"),
        )
    )
    assert_records_close(
        summary,
        observed_summary,
        key_fields=(*group_fields, "metric"),
    )


def verify_sample_artifact_recomputation(
    samples_path: str | Path,
    trajectory_metrics_path: str | Path,
    summary_path: str | Path,
    *,
    require_complete_feasibility: bool = False,
    **recompute_arguments: Any,
) -> dict[str, Any]:
    """Recompute sample feasibility and both published metric layers.

    This QA path starts from Parquet with row validation disabled, recomputes
    every v2 feasibility meaning explicitly, and only then rebuilds the
    trajectory and summary tables.  It therefore cannot pass merely because a
    producer copied its own feasibility flags into a summary CSV.
    """

    samples = read_parquet(samples_path, validate=False)
    feasibility_fields: Counter[str] = Counter()
    unavailable_fields: Counter[str] = Counter()
    for row_index, row in enumerate(samples):
        try:
            expected = recompute_sample_feasibility(row)
        except (TypeError, ValueError) as error:
            raise ArtifactValidationError(
                f"sample {row_index} feasibility recomputation failed: {error}"
            ) from error
        for field, value in expected.items():
            observed = row.get(field)
            if observed is None:
                unavailable_fields[field] += 1
                if require_complete_feasibility and value is not None:
                    raise ArtifactValidationError(
                        f"sample {row_index}.{field} is unavailable in a bundle "
                        "despite having complete inputs for recomputation"
                    )
                continue
            if value is None:
                unavailable_fields[field] += 1
                if require_complete_feasibility:
                    raise ArtifactValidationError(
                        f"sample {row_index}.{field} is populated but its inputs "
                        "are unavailable for independent recomputation"
                    )
                continue
            feasibility_fields[field] += 1
            if not isinstance(observed, (bool, np.bool_)) or bool(observed) != value:
                raise ArtifactValidationError(
                    f"sample {row_index}.{field} differs from independent "
                    f"recomputation: observed={observed!r}, expected={value!r}"
                )
    # Full schema validation remains a separate check after the explicit QA
    # above, covering aliases, nullability, clocks, and cross-field semantics.
    validate_samples(samples)
    verify_recomputed_summary(
        samples_path,
        trajectory_metrics_path,
        summary_path,
        **recompute_arguments,
    )
    return {
        "sample_count": len(samples),
        "feasibility_fields_verified": dict(sorted(feasibility_fields.items())),
        "feasibility_fields_unavailable": dict(sorted(unavailable_fields.items())),
        "trajectory_metrics_verified": True,
        "summary_metrics_verified": True,
    }


def build_primary_evidence_archive(
    bundle_root: str | Path,
    output_path: str | Path,
    *,
    required_artifacts: Sequence[str] = PRIMARY_EVIDENCE_REQUIRED_ARTIFACTS,
    validate_schemas: bool = True,
) -> dict[str, Any]:
    """Build a deterministic local ZIP and auditable publication sidecars.

    The archive is deliberately local-only: callers may publish it with an
    approved external mechanism, while environments without upload authority
    can cite the absolute path, byte count, and SHA-256 in blockers/PR text.
    """

    root = Path(bundle_root).resolve()
    output = Path(output_path).resolve()
    if output.suffix.lower() != ".zip":
        raise ArtifactValidationError("primary evidence archive must end in .zip")
    if not required_artifacts or len(set(required_artifacts)) != len(
        required_artifacts
    ):
        raise ArtifactValidationError(
            "primary evidence required-artifact list is empty or duplicated"
        )
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ArtifactValidationError(
            "primary evidence archive must be written outside the source bundle"
        )

    verified = set(verify_checksums(root))
    sources: list[tuple[str, Path]] = []
    file_records: list[dict[str, Any]] = []
    for relative_text in required_artifacts:
        relative = Path(relative_text)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != relative_text
        ):
            raise ArtifactValidationError(
                f"unsafe primary evidence path {relative_text!r}"
            )
        source = root / relative
        if not source.is_file() or source.is_symlink():
            raise ArtifactValidationError(
                f"primary evidence artifact is missing or unsafe: {relative_text}"
            )
        if relative_text != "artifact_checksums.json" and relative_text not in verified:
            raise ArtifactValidationError(
                f"primary evidence artifact is not checksummed: {relative_text}"
            )
        sources.append((relative_text, source))
        file_records.append(
            {
                "path": relative_text,
                "bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )
    if validate_schemas:
        for _, source in sources:
            validate_artifact_schema(source)
        validate_run_manifest(read_json(root / "run.json"), require_clean=False)

    output.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for relative_text, source in sources:
                information = zipfile.ZipInfo(
                    filename=f"primary_locked_test/{relative_text}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                information.compress_type = zipfile.ZIP_DEFLATED
                information.create_system = 3
                information.external_attr = 0o100644 << 16
                with (
                    source.open("rb") as input_stream,
                    archive.open(
                        information, mode="w", force_zip64=True
                    ) as output_stream,
                ):
                    while chunk := input_stream.read(1024 * 1024):
                        output_stream.write(chunk)
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    digest = sha256_file(output)
    sidecar_path = Path(f"{output}.sha256")
    _atomic_write(sidecar_path, f"{digest}  {output.name}\n".encode())
    manifest_path = Path(f"{output}.manifest.json")
    manifest = {
        "schema_version": PRIMARY_EVIDENCE_ARCHIVE_SCHEMA_VERSION,
        "archive": {
            "local_path": str(output),
            "bytes": output.stat().st_size,
            "sha256": digest,
            "format": "zip",
        },
        "minimum_required_artifacts": list(required_artifacts),
        "schema_validation_performed": validate_schemas,
        "files": file_records,
        "sha256_sidecar_local_path": str(sidecar_path),
    }
    write_json(manifest_path, manifest)
    return {**manifest, "manifest_local_path": str(manifest_path)}


class ArtifactWriter:
    """Stage one run directory and atomically promote only a finalized bundle."""

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        command: Sequence[str],
        resolved_config: Mapping[str, Any],
        repo_root: str | Path,
        expected_commit: str | None = None,
        require_clean: bool = True,
        started_at: str | None = None,
        manifest_extra: Mapping[str, Any] | None = None,
    ) -> None:
        # Capture provenance before creating an untracked result directory.
        self.manifest = create_run_manifest(
            run_id=run_id,
            command=command,
            resolved_config=resolved_config,
            repo_root=repo_root,
            expected_commit=expected_commit,
            require_clean=require_clean,
            started_at=started_at,
            extra=manifest_extra,
        )
        self.destination = Path(root).resolve()
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        if self.destination.exists():
            raise FileExistsError(
                f"refusing to overwrite artifact bundle: {self.destination}"
            )
        self.root = Path(
            tempfile.mkdtemp(
                prefix=f".{self.destination.name}.staging-",
                dir=self.destination.parent,
            )
        ).resolve()
        self.run_id = run_id
        self.command = list(command)
        self._registered: dict[str, tuple[str, str]] = {}
        self._csv_row_counts: dict[str, int] = {}
        self._finalized = False
        manifest_path = write_json(self.root / "run.json", self.manifest)
        self.register(manifest_path, role="run_manifest")

    @staticmethod
    def cleanup_staging_for_destination(root: str | Path) -> None:
        """Remove only hidden staging directories belonging to one destination."""

        destination = Path(root).resolve()
        prefix = f".{destination.name}.staging-"
        if not destination.parent.is_dir():
            return
        for candidate in destination.parent.iterdir():
            if candidate.is_dir() and candidate.name.startswith(prefix):
                shutil.rmtree(candidate)

    def abort(self) -> None:
        """Discard this writer's unpublished staging directory."""

        if not self._finalized and self.root != self.destination:
            shutil.rmtree(self.root, ignore_errors=True)

    def _ensure_open(self) -> None:
        if self._finalized:
            raise ArtifactValidationError("artifact writer has already been finalized")

    def register(
        self,
        path: str | Path,
        *,
        role: str,
        generated_by: str | None = None,
    ) -> Path:
        self._ensure_open()
        target = Path(path).resolve()
        relative = _relative_file(self.root, target)
        if relative in self._registered:
            raise ArtifactValidationError(f"artifact already registered: {relative}")
        self._registered[relative] = (role, generated_by or " ".join(self.command))
        if target.suffix.lower() == ".csv":
            with target.open("r", encoding="utf-8", newline="") as stream:
                reader = csv.reader(stream)
                try:
                    next(reader)
                except StopIteration as error:
                    raise ArtifactValidationError(
                        f"registered CSV has no header: {relative}"
                    ) from error
                self._csv_row_counts[relative] = sum(1 for _ in reader)
        return target

    def write_json(self, relative_path: str, value: Any, *, role: str) -> Path:
        self._ensure_open()
        path = write_json(self.root / relative_path, value)
        return self.register(path, role=role)

    def write_csv(
        self,
        relative_path: str,
        records: Sequence[Mapping[str, Any]],
        *,
        role: str,
        fieldnames: Sequence[str] | None = None,
        allowed_missing_fields: Iterable[str] = (),
        allow_empty: bool = False,
    ) -> Path:
        self._ensure_open()
        basename = Path(relative_path).name
        empty_log_fields = {
            "failures.csv": (
                "run_id",
                "trajectory_id",
                "k",
                "failure_type",
                "reason",
            ),
            "fallback_events.csv": (
                "run_id",
                "trajectory_id",
                "k",
                "fallback_reason",
            ),
        }
        if not records and basename in empty_log_fields:
            allow_empty = True
            if fieldnames is None:
                fieldnames = empty_log_fields[basename]
        inferred_missing = {
            "paired_comparisons.csv": {
                "effect_size",
                "effect_size_ci_low",
                "effect_size_ci_high",
            },
            "constraint_audit.csv": {
                "max_internal_jerk",
                "velocity_max_time_s",
                "acceleration_max_time_s",
                "jerk_max_time_s",
                "max_abs_velocity_time_s",
                "max_abs_acceleration_time_s",
                "max_sampled_jerk_time_s",
                "max_new_jerk_time_s",
                "max_internal_jerk_time_s",
                "internal_jerk_margin",
                "new_jerk_margin",
                "max_abs_velocity",
                "max_abs_acceleration",
                "max_sampled_jerk",
                "velocity_margin",
                "acceleration_margin",
                "jerk_margin",
            },
            "failures.csv": {"k"},
            "runtime_repetition_failures.csv": {"k"},
        }.get(basename, set())
        path = write_csv(
            self.root / relative_path,
            records,
            fieldnames=fieldnames,
            allowed_missing_fields=set(allowed_missing_fields) | inferred_missing,
            allow_empty=allow_empty,
        )
        return self.register(path, role=role)

    def write_samples(self, samples: Sequence[Mapping[str, Any]]) -> Path:
        self._ensure_open()
        validate_samples(samples)
        path = write_parquet(samples, self.root / "samples.parquet")
        return self.register(path, role="canonical_samples")

    def write_recomputed_metrics(
        self,
        *,
        output_field: str | None = None,
        settle_tolerance: float = 1e-3,
        max_lag_s: float = 1.0,
        deadline_us: float | None = None,
        motion_limits: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        summary_group_fields: Sequence[str] = (
            "run_id",
            "split",
            "method",
            "scenario_id",
        ),
        summary_metric_fields: Sequence[str] | None = None,
    ) -> tuple[Path, Path]:
        self._ensure_open()
        samples_path = self.root / "samples.parquet"
        if "samples.parquet" not in self._registered:
            raise ArtifactValidationError("samples.parquet must be written first")
        trajectory, summary = recompute_summary_from_samples(
            samples_path,
            output_field=output_field,
            settle_tolerance=settle_tolerance,
            max_lag_s=max_lag_s,
            deadline_us=deadline_us,
            motion_limits=motion_limits,
            context=context,
            summary_group_fields=summary_group_fields,
            summary_metric_fields=summary_metric_fields,
        )
        trajectory_columns = list(trajectory[0])
        seen_columns = set(trajectory_columns)
        for column in sorted(set().union(*(set(row) for row in trajectory))):
            if column not in seen_columns:
                trajectory_columns.append(column)
                seen_columns.add(column)
        optional_columns = {
            column
            for column in trajectory_columns
            if any(
                row.get(column) is None or row.get(column) == "" for row in trajectory
            )
        }
        rectangular_trajectory = [
            {column: row.get(column) for column in trajectory_columns}
            for row in trajectory
        ]
        trajectory_path = self.write_csv(
            "metrics_by_trajectory.csv",
            rectangular_trajectory,
            role="trajectory_metrics",
            fieldnames=trajectory_columns,
            allowed_missing_fields=optional_columns,
        )
        summary_path = self.write_csv(
            "summary_metrics.csv",
            summary,
            role="summary_metrics",
        )
        return trajectory_path, summary_path

    def finalize(
        self,
        *,
        require_standard_artifacts: bool = True,
        external_artifacts: Sequence[Mapping[str, Any]] = (),
        promote: bool = True,
    ) -> tuple[Path, Path]:
        self._ensure_open()
        # Row counts make a legitimate header-only failures table explicit in
        # the signed run manifest rather than indistinguishable from truncation.
        self.manifest["artifact_row_counts"] = dict(
            sorted(self._csv_row_counts.items())
        )
        write_json(self.root / "run.json", self.manifest)
        if require_standard_artifacts:
            missing = (REQUIRED_RUN_ARTIFACTS - {"artifact_checksums.json"}) - set(
                self._registered
            )
            if missing:
                raise ArtifactValidationError(
                    f"run bundle is missing required artifacts {sorted(missing)}"
                )
        for relative in sorted(self._registered):
            validate_artifact_schema(self.root / relative)
        checksum_path = write_checksums(
            self.root,
            [self.root / relative for relative in sorted(self._registered)],
        )
        checksum_record = build_artifact_record(
            self.root,
            checksum_path,
            role="artifact_checksums",
            generated_by=" ".join(self.command),
        )
        records = [
            build_artifact_record(
                self.root,
                self.root / relative,
                role=self._registered[relative][0],
                generated_by=self._registered[relative][1],
            )
            for relative in sorted(self._registered)
        ]
        records.append(checksum_record)
        for external in external_artifacts:
            required = {"uri", "sha256", "bytes", "generation_command", "role"}
            missing = required - set(external)
            if missing:
                raise ArtifactValidationError(
                    f"external artifact is missing {sorted(missing)}"
                )
            if not re.fullmatch(r"[0-9a-f]{64}", str(external["sha256"])):
                raise ArtifactValidationError("external artifact has invalid SHA-256")
        index = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "git_commit": self.manifest["git_commit"],
            "git_worktree_dirty": self.manifest["git_worktree_dirty"],
            "generation_command": self.command,
            "artifacts": [asdict(record) for record in records],
            "external_artifacts": list(external_artifacts),
        }
        index_path = write_json(self.root / "artifact_index.json", index)
        self._finalized = True
        if promote:
            return self.promote()
        return checksum_path, index_path

    def promote(self) -> tuple[Path, Path]:
        """Atomically publish a finalized staging directory."""

        if not self._finalized:
            raise ArtifactValidationError("cannot promote an unfinalized bundle")
        if self.root != self.destination:
            self.root.replace(self.destination)
            self.root = self.destination
        return (
            self.destination / "artifact_checksums.json",
            self.destination / "artifact_index.json",
        )


def validate_artifact_bundle(
    root: str | Path,
    *,
    require_standard_artifacts: bool = True,
    require_clean: bool = True,
    expected_commit: str | None = None,
    verify_recomputation: bool = False,
    require_complete_feasibility: bool = False,
    recompute_arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate manifest, index, schemas, hashes, and optional recomputation."""

    root_path = Path(root).resolve()
    manifest = read_json(root_path / "run.json")
    validate_run_manifest(
        manifest,
        expected_commit=expected_commit,
        require_clean=require_clean,
    )
    index = read_json(root_path / "artifact_index.json")
    if index.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("invalid artifact index schema")
    if index.get("run_id") != manifest["run_id"]:
        raise ArtifactValidationError("artifact index run_id differs from manifest")
    if index.get("git_commit") != manifest["git_commit"]:
        raise ArtifactValidationError("artifact index commit differs from manifest")
    if index.get("git_worktree_dirty") != manifest["git_worktree_dirty"]:
        raise ArtifactValidationError(
            "artifact index dirty-state flag differs from manifest"
        )
    records = index.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ArtifactValidationError("artifact index has no local artifacts")
    indexed_paths: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ArtifactValidationError("artifact index entry is not an object")
        relative = record.get("path")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ArtifactValidationError(f"unsafe artifact index path {relative!r}")
        if relative in indexed_paths:
            raise ArtifactValidationError(f"duplicate artifact index path {relative}")
        indexed_paths.add(relative)
        target = root_path / relative
        if not target.is_file():
            raise ArtifactValidationError(f"indexed artifact is missing: {relative}")
        if target.stat().st_size != record.get("bytes"):
            raise ArtifactValidationError(f"indexed size mismatch: {relative}")
        declared_hash = record.get("sha256")
        if not isinstance(declared_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", declared_hash
        ):
            raise ArtifactValidationError(f"indexed SHA-256 is invalid: {relative}")
        if (
            not record.get("role")
            or not record.get("media_type")
            or not record.get("generated_by")
        ):
            raise ArtifactValidationError(
                f"indexed provenance metadata is incomplete: {relative}"
            )
        if sha256_file(target) != declared_hash:
            raise ArtifactValidationError(f"indexed hash mismatch: {relative}")
        validate_artifact_schema(target)
    on_disk = {
        path.relative_to(root_path).as_posix()
        for path in root_path.rglob("*")
        if path.is_file() and path.name != "artifact_index.json"
    }
    if on_disk != indexed_paths:
        raise ArtifactValidationError(
            "artifact index coverage differs from files on disk: "
            f"unindexed={sorted(on_disk - indexed_paths)}, "
            f"missing={sorted(indexed_paths - on_disk)}"
        )
    external_records = index.get("external_artifacts", [])
    if not isinstance(external_records, list):
        raise ArtifactValidationError("external_artifacts must be a list")
    external_uris: set[str] = set()
    for external in external_records:
        required = {"uri", "sha256", "bytes", "generation_command", "role"}
        if not isinstance(external, dict) or required - set(external):
            raise ArtifactValidationError(
                "external artifact lacks URI/hash/size/generation provenance"
            )
        uri = str(external["uri"])
        if not uri or uri in external_uris:
            raise ArtifactValidationError(
                "external artifact URI is empty or duplicated"
            )
        external_uris.add(uri)
        if not re.fullmatch(r"[0-9a-f]{64}", str(external["sha256"])):
            raise ArtifactValidationError("external artifact has invalid SHA-256")
        if (
            not isinstance(external["bytes"], int)
            or isinstance(external["bytes"], bool)
            or external["bytes"] < 0
        ):
            raise ArtifactValidationError("external artifact has invalid byte size")
        if not external["generation_command"] or not external["role"]:
            raise ArtifactValidationError(
                "external artifact generation command/role is empty"
            )
    row_counts = manifest.get("artifact_row_counts", {})
    for relative, expected_count in row_counts.items():
        target = root_path / relative
        if not target.is_file() or target.suffix.lower() != ".csv":
            raise ArtifactValidationError(
                f"manifest row count targets a missing/non-CSV artifact: {relative}"
            )
        with target.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            try:
                next(reader)
            except StopIteration as error:
                raise ArtifactValidationError(
                    f"CSV with declared row count lacks a header: {relative}"
                ) from error
            observed_count = sum(1 for _ in reader)
        if observed_count != expected_count:
            raise ArtifactValidationError(
                f"CSV row count mismatch for {relative}: "
                f"expected {expected_count}, observed {observed_count}"
            )
    if require_standard_artifacts:
        missing = REQUIRED_RUN_ARTIFACTS - indexed_paths
        if missing:
            raise ArtifactValidationError(
                f"artifact index is missing standard artifacts {sorted(missing)}"
            )
    verified = verify_checksums(root_path)
    checksum_declared = set(verified)
    expected_checked = indexed_paths - {"artifact_checksums.json"}
    # artifact_index.json is the root of trust and is intentionally not listed
    # inside itself or the checksum document.
    expected_checked.discard("artifact_index.json")
    if checksum_declared != expected_checked:
        raise ArtifactValidationError(
            "checksum coverage differs from indexed local artifacts"
        )
    recomputation: dict[str, Any] | None = None
    if verify_recomputation:
        recomputation = verify_sample_artifact_recomputation(
            root_path / "samples.parquet",
            root_path / "metrics_by_trajectory.csv",
            root_path / "summary_metrics.csv",
            require_complete_feasibility=require_complete_feasibility,
            **dict(recompute_arguments or {}),
        )
    return {
        "run_id": manifest["run_id"],
        "git_commit": manifest["git_commit"],
        "artifact_count": len(records),
        "checksums_verified": len(verified),
        "recomputation_verified": bool(verify_recomputation),
        "feasibility_recomputation_verified": bool(recomputation),
        "sample_recomputation": recomputation,
    }


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ArtifactRecord",
    "ArtifactValidationError",
    "ArtifactWriter",
    "CHECKSUM_SCHEMA_VERSION",
    "DEFAULT_SCHEMA_HOOKS",
    "GitState",
    "PRIMARY_EVIDENCE_ARCHIVE_SCHEMA_VERSION",
    "PRIMARY_EVIDENCE_REQUIRED_ARTIFACTS",
    "REQUIRED_RUN_ARTIFACTS",
    "RUN_MANIFEST_SCHEMA_VERSION",
    "assert_clean_commit",
    "assert_records_close",
    "build_artifact_record",
    "build_primary_evidence_archive",
    "canonical_json_bytes",
    "canonical_json_hash",
    "capture_git_state",
    "create_run_manifest",
    "read_csv",
    "read_json",
    "recompute_summary_from_samples",
    "runtime_environment",
    "sha256_file",
    "validate_artifact_bundle",
    "validate_artifact_schema",
    "validate_run_manifest",
    "verify_checksums",
    "verify_recomputed_summary",
    "verify_sample_artifact_recomputation",
    "write_checksums",
    "write_csv",
    "write_json",
]
