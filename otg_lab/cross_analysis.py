"""Collect pinned experiment artifacts for an A-series analysis."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_SCHEMA_VERSION = "otg.cross_analysis.v1"
PROVENANCE_SCHEMA_VERSION = "otg.cross_analysis.provenance.v1"
RUN_MANIFEST_SCHEMA_VERSION = "otg.run_manifest.v1"


class AnalysisConfigError(ValueError):
    """Raised when an analysis declaration or pinned source is invalid."""


@dataclass(frozen=True)
class PinnedSource:
    source_id: str
    experiment_id: str
    relative_directory: str
    directory: Path
    factors: Mapping[str, Any]
    manifest: Mapping[str, Any]
    manifest_sha256: str


@dataclass(frozen=True)
class PreparedAnalysis:
    """Validated, in-memory inputs for one A-series analysis."""

    config_path: Path
    config: Mapping[str, Any]
    config_sha256: str
    project_root: Path
    sources: tuple[PinnedSource, ...]
    artifacts: Mapping[str, str]
    selection: Mapping[str, set[str]]
    factor_names: tuple[str, ...]
    collected: Mapping[
        str,
        tuple[list[str], list[dict[str, Any]]],
    ]
    artifact_provenance: tuple[Mapping[str, Any], ...]
    analysis_id: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisConfigError(f"{label} must be a mapping")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _load_config(config_path: Path) -> tuple[Mapping[str, Any], str]:
    if not config_path.is_file():
        raise FileNotFoundError(f"analysis config was not found: {config_path}")
    raw = config_path.read_bytes()
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise AnalysisConfigError(f"invalid YAML in {config_path}: {error}") from error
    config = _mapping(loaded, "analysis config")
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise AnalysisConfigError(f"schema_version must be {CONFIG_SCHEMA_VERSION!r}")
    analysis_id = _string(config.get("analysis_id"), "analysis_id").upper()
    if not re.fullmatch(r"A[0-9]{2,}", analysis_id):
        raise AnalysisConfigError("analysis_id must look like A01")
    slug = _string(config.get("slug"), "slug")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", slug):
        raise AnalysisConfigError("slug must be lowercase snake_case")
    _string(config.get("title"), "title")
    _string(config.get("question"), "question")
    return config, hashlib.sha256(raw).hexdigest()


def _project_root(config_path: Path, config: Mapping[str, Any]) -> Path:
    declared = _string(config.get("project_root", "../.."), "project_root")
    path = Path(declared)
    if path.is_absolute():
        raise AnalysisConfigError("project_root must be relative to analysis.yaml")
    root = (config_path.parent / path).resolve()
    if not (root / "experiments").is_dir():
        raise AnalysisConfigError(f"project_root does not contain experiments/: {root}")
    return root


def _project_relative_directory(
    project_root: Path,
    value: Any,
    label: str,
) -> tuple[str, Path]:
    declared = _string(value, label)
    relative = Path(declared)
    if relative.is_absolute():
        raise AnalysisConfigError(f"{label} must be project-relative")
    if any(part.lower() == "latest" for part in relative.parts):
        raise AnalysisConfigError(f"{label} must pin an exact run, not latest")
    if any(character in declared for character in "*?[]"):
        raise AnalysisConfigError(f"{label} must not contain a glob")
    resolved = (project_root / relative).resolve()
    try:
        normalized = resolved.relative_to(project_root)
    except ValueError as error:
        raise AnalysisConfigError(f"{label} escapes project_root") from error
    return normalized.as_posix(), resolved


def _load_manifest(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"run manifest was not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AnalysisConfigError(f"invalid run manifest {path}: {error}") from error
    return _mapping(manifest, f"run manifest {path}")


def _resolve_sources(
    project_root: Path,
    config: Mapping[str, Any],
) -> tuple[PinnedSource, ...]:
    declared_sources = config.get("sources")
    if not isinstance(declared_sources, Sequence) or isinstance(
        declared_sources,
        (str, bytes),
    ):
        raise AnalysisConfigError("sources must be a non-empty list")
    if not declared_sources:
        raise AnalysisConfigError("sources must be a non-empty list")

    requirements = _mapping(
        config.get("source_requirements", {}),
        "source_requirements",
    )
    required_status = _string(
        requirements.get("status", "completed"),
        "source_requirements.status",
    )
    allow_dirty_git = requirements.get("allow_dirty_git", False)
    if not isinstance(allow_dirty_git, bool):
        raise AnalysisConfigError("source_requirements.allow_dirty_git must be boolean")
    same_git_commit = requirements.get("same_git_commit", False)
    if not isinstance(same_git_commit, bool):
        raise AnalysisConfigError("source_requirements.same_git_commit must be boolean")

    seen_source_ids: set[str] = set()
    sources: list[PinnedSource] = []
    for index, raw_source in enumerate(declared_sources):
        label = f"sources[{index}]"
        source = _mapping(raw_source, label)
        source_id = _string(source.get("source_id"), f"{label}.source_id")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", source_id):
            raise AnalysisConfigError(f"{label}.source_id must be lowercase snake_case")
        if source_id in seen_source_ids:
            raise AnalysisConfigError(f"duplicate source_id: {source_id}")
        seen_source_ids.add(source_id)

        experiment_id = _string(
            source.get("experiment_id"),
            f"{label}.experiment_id",
        ).upper()
        if not re.fullmatch(r"E[0-9]{2,}", experiment_id):
            raise AnalysisConfigError(f"{label}.experiment_id must look like E03")

        relative_directory, directory = _project_relative_directory(
            project_root,
            source.get("source_directory"),
            f"{label}.source_directory",
        )
        manifest_path = directory / "manifest.json"
        manifest = _load_manifest(manifest_path)
        if manifest.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION:
            raise AnalysisConfigError(f"{manifest_path} has unsupported schema_version")
        if manifest.get("status") != required_status:
            raise AnalysisConfigError(
                f"{source_id} has status {manifest.get('status')!r}; "
                f"expected {required_status!r}"
            )
        resolved_spec = _mapping(
            manifest.get("resolved_experiment_spec"),
            f"{source_id} resolved_experiment_spec",
        )
        manifest_experiment_id = resolved_spec.get("experiment_id")
        if manifest_experiment_id != experiment_id:
            raise AnalysisConfigError(
                f"{source_id} declares {experiment_id}, but its manifest "
                f"contains {manifest_experiment_id!r}"
            )
        git = _mapping(manifest.get("git"), f"{source_id} manifest.git")
        if git.get("dirty") is True and not allow_dirty_git:
            raise AnalysisConfigError(
                f"{source_id} was produced from a dirty Git worktree"
            )
        factors = _mapping(source.get("factors", {}), f"{label}.factors")
        invalid_factors = [
            str(name)
            for name, value in factors.items()
            if not isinstance(value, (str, int, float, bool)) and value is not None
        ]
        if invalid_factors:
            raise AnalysisConfigError(
                f"{label}.factors values must be scalar: "
                + ", ".join(sorted(invalid_factors))
            )
        sources.append(
            PinnedSource(
                source_id=source_id,
                experiment_id=experiment_id,
                relative_directory=relative_directory,
                directory=directory,
                factors=dict(factors),
                manifest=manifest,
                manifest_sha256=_sha256(manifest_path),
            )
        )

    if same_git_commit:
        commits = {
            _mapping(source.manifest.get("git"), "manifest.git").get("commit")
            for source in sources
        }
        if None in commits or "" in commits:
            raise AnalysisConfigError("every source must record a Git commit")
        if len(commits) != 1:
            raise AnalysisConfigError(
                "pinned sources do not share one Git commit: "
                + ", ".join(sorted(str(commit) for commit in commits))
            )
    return tuple(sources)


def _artifact_map(config: Mapping[str, Any]) -> Mapping[str, str]:
    declared = _mapping(config.get("artifacts"), "artifacts")
    if not declared:
        raise AnalysisConfigError("artifacts must not be empty")
    artifacts: dict[str, str] = {}
    for artifact_id, relative_path in declared.items():
        normalized_id = _string(artifact_id, "artifact id")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", normalized_id):
            raise AnalysisConfigError(
                f"artifact id must be lowercase snake_case: {normalized_id!r}"
            )
        normalized_path = Path(_string(relative_path, f"artifacts.{normalized_id}"))
        if normalized_path.is_absolute() or ".." in normalized_path.parts:
            raise AnalysisConfigError(
                f"artifacts.{normalized_id} must be source-directory-relative"
            )
        if normalized_path.suffix.lower() != ".csv":
            raise AnalysisConfigError(
                f"artifacts.{normalized_id} must refer to a CSV file"
            )
        artifacts[normalized_id] = normalized_path.as_posix()
    return artifacts


def _selection(config: Mapping[str, Any]) -> Mapping[str, set[str]]:
    declared = _mapping(config.get("selection", {}), "selection")
    supported = {
        "window_ids": "window_id",
        "metric_ids": "metric_id",
        "method_ids": "method_id",
        "statuses": "status",
    }
    unknown = sorted(set(declared) - set(supported))
    if unknown:
        raise AnalysisConfigError("unsupported selection keys: " + ", ".join(unknown))
    result: dict[str, set[str]] = {}
    for config_key, row_key in supported.items():
        values = declared.get(config_key)
        if values is None:
            continue
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise AnalysisConfigError(f"selection.{config_key} must be a list")
        result[row_key] = {
            _string(value, f"selection.{config_key} item") for value in values
        }
    return result


def _row_selected(
    row: Mapping[str, str],
    selection: Mapping[str, set[str]],
) -> bool:
    return all(
        not allowed or row.get(field) in allowed for field, allowed in selection.items()
    )


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise FileNotFoundError(f"required analysis artifact was not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise AnalysisConfigError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def _factor_names(sources: Sequence[PinnedSource]) -> list[str]:
    return sorted({str(key) for source in sources for key in source.factors})


def _collect_artifact(
    sources: Sequence[PinnedSource],
    relative_path: str,
    selection: Mapping[str, set[str]],
    factor_names: Sequence[str],
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    source_fields = [
        "source_id",
        "source_experiment_id",
        "source_run_id",
        *(f"factor_{name}" for name in factor_names),
    ]
    expected_fields: list[str] | None = None
    rows: list[dict[str, Any]] = []
    artifact_provenance: list[dict[str, Any]] = []
    for source in sources:
        path = source.directory / relative_path
        fields, source_rows = _read_csv(path)
        if expected_fields is None:
            expected_fields = fields
        elif fields != expected_fields:
            raise AnalysisConfigError(
                f"CSV header mismatch for {path}; all sources for one artifact "
                "must use the same schema"
            )
        selected_rows = [row for row in source_rows if _row_selected(row, selection)]
        factors = {
            f"factor_{name}": source.factors.get(name, "") for name in factor_names
        }
        for row in selected_rows:
            rows.append(
                {
                    "source_id": source.source_id,
                    "source_experiment_id": source.experiment_id,
                    "source_run_id": source.directory.name,
                    **factors,
                    **row,
                }
            )
        artifact_provenance.append(
            {
                "source_id": source.source_id,
                "relative_path": relative_path,
                "sha256": _sha256(path),
                "input_row_count": len(source_rows),
                "selected_row_count": len(selected_rows),
            }
        )
    return source_fields + (expected_fields or []), rows, artifact_provenance


def _write_csv(
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _inventory(
    sources: Sequence[PinnedSource],
    factor_names: Sequence[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    fields = [
        "source_id",
        "experiment_id",
        "source_kind",
        "source_directory",
        "run_id",
        "spec_hash",
        "git_commit",
        "git_dirty",
        "status",
        "manifest_sha256",
        *(f"factor_{name}" for name in factor_names),
    ]
    rows: list[dict[str, Any]] = []
    for source in sources:
        git = _mapping(source.manifest.get("git"), "manifest.git")
        path_parts = Path(source.relative_directory).parts
        source_kind = (
            "result"
            if "results" in path_parts
            else "run"
            if "runs" in path_parts
            else "other"
        )
        row: dict[str, Any] = {
            "source_id": source.source_id,
            "experiment_id": source.experiment_id,
            "source_kind": source_kind,
            "source_directory": source.relative_directory,
            "run_id": source.directory.name,
            "spec_hash": source.manifest.get("spec_hash", ""),
            "git_commit": git.get("commit", ""),
            "git_dirty": git.get("dirty", ""),
            "status": source.manifest.get("status", ""),
            "manifest_sha256": source.manifest_sha256,
        }
        row.update(
            {f"factor_{name}": source.factors.get(name, "") for name in factor_names}
        )
        rows.append(row)
    return fields, rows


def prepare_analysis(config_path: Path) -> PreparedAnalysis:
    """Validate and collect pinned artifacts without writing analysis outputs."""

    config_path = config_path.resolve()
    config, config_sha256 = _load_config(config_path)
    project_root = _project_root(config_path, config)
    sources = _resolve_sources(project_root, config)
    artifacts = _artifact_map(config)
    selection = _selection(config)
    factor_names = _factor_names(sources)

    collected: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
    artifact_provenance: list[dict[str, Any]] = []
    for artifact_id, relative_path in artifacts.items():
        fields, rows, provenance = _collect_artifact(
            sources,
            relative_path,
            selection,
            factor_names,
        )
        collected[artifact_id] = (fields, rows)
        artifact_provenance.extend(
            {"artifact_id": artifact_id, **item} for item in provenance
        )

    analysis_id = _string(config.get("analysis_id"), "analysis_id").upper()
    return PreparedAnalysis(
        config_path=config_path,
        config=config,
        config_sha256=config_sha256,
        project_root=project_root,
        sources=sources,
        artifacts=dict(artifacts),
        selection={key: set(values) for key, values in selection.items()},
        factor_names=tuple(factor_names),
        collected=collected,
        artifact_provenance=tuple(artifact_provenance),
        analysis_id=analysis_id,
    )


def write_prepared_analysis(
    prepared: PreparedAnalysis,
    output_directory: Path | None = None,
) -> Path:
    """Write a previously prepared analysis bundle to its work directory."""

    output = (
        output_directory.resolve()
        if output_directory is not None
        else (prepared.config_path.parent / "work").resolve()
    )
    output.mkdir(parents=True, exist_ok=True)
    inventory_fields, inventory_rows = _inventory(
        prepared.sources,
        prepared.factor_names,
    )
    _write_csv(output / "source_inventory.csv", inventory_fields, inventory_rows)
    for artifact_id, (fields, rows) in prepared.collected.items():
        _write_csv(output / f"combined_{artifact_id}.csv", fields, rows)

    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "analysis_id": prepared.analysis_id,
        "config_path": prepared.config_path.relative_to(
            prepared.project_root
        ).as_posix(),
        "config_sha256": prepared.config_sha256,
        "selection": {
            key: sorted(values) for key, values in sorted(prepared.selection.items())
        },
        "sources": inventory_rows,
        "artifacts": list(prepared.artifact_provenance),
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{prepared.analysis_id}: collected pinned artifacts into {output}")
    return output


def collect(
    config_path: Path,
    output_directory: Path | None = None,
    *,
    check_only: bool = False,
) -> Path | None:
    prepared = prepare_analysis(config_path)
    if check_only:
        print(
            f"{prepared.analysis_id}: validated {len(prepared.sources)} pinned "
            f"sources and {len(prepared.artifacts)} artifact schemas"
        )
        return None
    return write_prepared_analysis(prepared, output_directory)


def build_parser(default_config: Path | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect pinned E-series artifacts for one A-series analysis"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config,
        required=default_config is None,
        help="analysis.yaml path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="output directory (default: work/ beside analysis.yaml)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate sources and artifact schemas without writing outputs",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    default_config: Path | None = None,
) -> int:
    args = build_parser(default_config).parse_args(argv)
    try:
        collect(args.config, args.output_dir, check_only=args.check)
    except (AnalysisConfigError, FileNotFoundError) as error:
        print(f"error: {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
