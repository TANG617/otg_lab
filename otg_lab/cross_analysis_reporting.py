"""Shared reporting utilities for repository-local A-series analyses."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .cross_analysis import PreparedAnalysis


class AnalysisValidationError(RuntimeError):
    """Raised when an A-series analysis cannot be trusted."""


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def available_value(row: Mapping[str, Any] | None) -> float | None:
    if row is None or row.get("status") != "available":
        return None
    return as_float(row.get("value"))


def metric_group(metric_id: str) -> str:
    lowered = metric_id.lower()
    if lowered.startswith("lag_") or lowered in {"lag_s", "lag_samples"}:
        return "lag"
    if any(
        token in lowered
        for token in (
            "posterior_",
            "prediction_",
            "raw_target_",
            "target_position_distortion",
            "target_velocity_distortion",
            "target_acceleration_distortion",
        )
    ):
        return "target-state error"
    if any(
        token in lowered
        for token in (
            "runtime_",
            "deadline_",
            "fallback",
            "solver_failure",
            "reset_",
        )
    ):
        return "runtime/reliability"
    if any(
        token in lowered
        for token in (
            "violation",
            "limit_",
            "constraint",
            "profile_exact",
            "profile_peak",
        )
    ):
        return "limits"
    if any(
        token in lowered
        for token in (
            "stop_go",
            "rest_to_rest",
            "endpoint_stop",
            "one_cycle_reachability",
        )
    ):
        return "stop-go"
    if any(
        token in lowered
        for token in (
            "jerk",
            "acceleration_total_variation",
            "output_velocity_",
            "output_acceleration_",
            "output_jerk_",
        )
    ):
        return "smoothness/dynamics"
    if any(
        token in lowered
        for token in (
            "position_rmse",
            "position_mae",
            "position_bias",
            "position_p95",
            "position_max",
            "position_iae",
            "tracking_sample",
            "settled",
            "settle_time",
        )
    ):
        return "tracking"
    return "other"


def directional_effect(
    pv_value: float | None,
    pva_value: float | None,
    direction: str,
    *,
    relative_allowed: bool = True,
) -> tuple[float | None, float | None, float | None]:
    if pv_value is None or pva_value is None:
        return None, None, None
    delta = pva_value - pv_value
    if direction == "lower":
        improvement = pv_value - pva_value
    elif direction == "higher":
        improvement = pva_value - pv_value
    else:
        return delta, None, None
    relative = None
    if relative_allowed and pv_value != 0.0:
        relative = improvement / abs(pv_value)
    return delta, improvement, relative


def write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    def clean(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(clean(value) for value in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend(
        "| " + " | ".join(clean(value) for value in row) + " |" for row in rows
    )
    return "\n".join(lines)


def prepared_rows(
    prepared: PreparedAnalysis,
    artifact_id: str,
) -> list[dict[str, Any]]:
    try:
        _, rows = prepared.collected[artifact_id]
    except KeyError as error:
        raise AnalysisValidationError(
            f"prepared analysis lacks artifact {artifact_id!r}"
        ) from error
    return [dict(row) for row in rows]


def validate_sources(prepared: PreparedAnalysis) -> list[dict[str, Any]]:
    """Validate common source controls and return an auditable check table."""

    rows: list[dict[str, Any]] = []
    blocking_failures: list[str] = []

    def record(
        check_id: str,
        scope: str,
        passed: bool,
        actual: Any,
        expected: Any,
        notes: str = "",
        *,
        blocking: bool = True,
    ) -> None:
        rows.append(
            {
                "check_id": check_id,
                "scope": scope,
                "status": "pass" if passed else "fail",
                "actual": stable_json(actual),
                "expected": stable_json(expected),
                "blocking": str(blocking).lower(),
                "notes": notes,
            }
        )
        if blocking and not passed:
            blocking_failures.append(f"{check_id}:{scope}")

    for source in prepared.sources:
        manifest = source.manifest
        scope = source.source_id
        record(
            "status_completed",
            scope,
            manifest.get("status") == "completed",
            manifest.get("status"),
            "completed",
        )
        record(
            "required_failure_count",
            scope,
            int(manifest.get("required_failure_count", 0)) == 0,
            manifest.get("required_failure_count", 0),
            0,
        )
        record(
            "failure_count",
            scope,
            int(manifest.get("failure_count", 0)) == 0,
            manifest.get("failure_count", 0),
            0,
        )
        dirty = bool(manifest.get("git", {}).get("dirty", False))
        record(
            "git_dirty",
            scope,
            not dirty,
            dirty,
            False,
            "dirty source is an explicit report caveat",
            blocking=False,
        )

    commits = {
        source.manifest.get("git", {}).get("commit") for source in prepared.sources
    }
    record(
        "same_git_commit",
        "all_sources",
        len(commits) == 1 and None not in commits,
        sorted(str(value) for value in commits),
        "one shared non-empty commit",
    )

    first = prepared.sources[0]
    first_spec = first.manifest["resolved_experiment_spec"]
    expected_controls = first_spec.get("controlled_variables")
    expected_run_config = first_spec.get("run_config")
    expected_windows = first_spec.get("windows")
    expected_inputs = [
        {
            "input_id": item.get("input_id"),
            "csv_path": item.get("csv_path"),
            "required": item.get("required"),
        }
        for item in first_spec.get("inputs", [])
    ]
    for source in prepared.sources[1:]:
        spec = source.manifest["resolved_experiment_spec"]
        scope = f"{first.source_id}_vs_{source.source_id}"
        record(
            "controlled_variables_equal",
            scope,
            spec.get("controlled_variables") == expected_controls,
            spec.get("controlled_variables"),
            expected_controls,
        )
        record(
            "run_config_equal",
            scope,
            spec.get("run_config") == expected_run_config,
            spec.get("run_config"),
            expected_run_config,
        )
        record(
            "windows_equal",
            scope,
            spec.get("windows") == expected_windows,
            spec.get("windows"),
            expected_windows,
        )
        inputs = [
            {
                "input_id": item.get("input_id"),
                "csv_path": item.get("csv_path"),
                "required": item.get("required"),
            }
            for item in spec.get("inputs", [])
        ]
        record(
            "inputs_equal",
            scope,
            inputs == expected_inputs,
            inputs,
            expected_inputs,
        )

    if blocking_failures:
        raise AnalysisValidationError(
            "source validation failed: " + ", ".join(blocking_failures)
        )
    return rows


def compare_duplicate_methods(
    metric_rows: Sequence[Mapping[str, Any]],
    *,
    left_source_id: str,
    right_source_id: str,
    method_ids: Sequence[str],
    excluded_metric_prefixes: Sequence[str] = (),
    atol: float = 1e-12,
    rtol: float = 1e-9,
) -> list[dict[str, Any]]:
    """Compare duplicated deterministic outputs across pinned experiment sources."""

    results: list[dict[str, Any]] = []
    for method_id in method_ids:
        indexes: dict[str, dict[tuple[str, str, str], Mapping[str, Any]]] = {}
        for source_id in (left_source_id, right_source_id):
            selected = [
                row
                for row in metric_rows
                if row.get("source_id") == source_id
                and row.get("method_id") == method_id
                and not any(
                    str(row.get("metric_id", "")).startswith(prefix)
                    for prefix in excluded_metric_prefixes
                )
            ]
            index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
            for row in selected:
                key = (
                    str(row.get("input_id")),
                    str(row.get("window_id")),
                    str(row.get("metric_id")),
                )
                if key in index:
                    raise AnalysisValidationError(
                        f"duplicate metric key for {source_id}/{method_id}: {key}"
                    )
                index[key] = row
            indexes[source_id] = index

        left = indexes[left_source_id]
        right = indexes[right_source_id]
        keys = sorted(set(left) | set(right))
        mismatches = 0
        max_abs_difference = 0.0
        for key in keys:
            left_row = left.get(key)
            right_row = right.get(key)
            if left_row is None or right_row is None:
                mismatches += 1
                continue
            for field in ("status", "unit", "direction", "role"):
                if left_row.get(field) != right_row.get(field):
                    mismatches += 1
                    break
            else:
                left_value = as_float(left_row.get("value"))
                right_value = as_float(right_row.get("value"))
                if left_value is None or right_value is None:
                    if left_row.get("value", "") != right_row.get("value", ""):
                        mismatches += 1
                else:
                    difference = abs(left_value - right_value)
                    max_abs_difference = max(max_abs_difference, difference)
                    if not math.isclose(
                        left_value,
                        right_value,
                        abs_tol=atol,
                        rel_tol=rtol,
                    ):
                        mismatches += 1
        results.append(
            {
                "check_id": "duplicate_method_equivalence",
                "scope": f"{left_source_id}_vs_{right_source_id}:{method_id}",
                "status": "pass" if mismatches == 0 else "fail",
                "actual": stable_json(
                    {
                        "paired_keys": len(keys),
                        "mismatches": mismatches,
                        "max_abs_difference": max_abs_difference,
                    }
                ),
                "expected": stable_json(
                    {
                        "mismatches": 0,
                        "atol": atol,
                        "rtol": rtol,
                    }
                ),
                "blocking": "true",
                "notes": (
                    "repeated baseline/truth rows are validation coordinates, "
                    "not extra samples; excluded non-deterministic prefixes="
                    + stable_json(list(excluded_metric_prefixes))
                ),
            }
        )
        if mismatches:
            raise AnalysisValidationError(
                f"duplicate method comparison failed for {method_id}: "
                f"{mismatches} mismatches"
            )
    return results


def configure_matplotlib() -> None:
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "PingFang SC",
                "Noto Sans CJK SC",
                "Arial Unicode MS",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "axes.edgecolor": "#374151",
            "axes.labelcolor": "#1F2937",
            "axes.titlecolor": "#111827",
            "axes.titlesize": 12,
            "axes.titleweight": "semibold",
            "figure.facecolor": "#FFFFFF",
            "axes.facecolor": "#FFFFFF",
            "grid.color": "#E5E7EB",
            "grid.linewidth": 0.8,
            "text.color": "#111827",
            "xtick.color": "#4B5563",
            "ytick.color": "#4B5563",
            "svg.hashsalt": "otg-lab-cross-analysis",
        }
    )


def save_figure(figure: Any, base_path: Path) -> tuple[Path, Path]:
    base_path.parent.mkdir(parents=True, exist_ok=True)
    png_path = base_path.with_suffix(".png")
    svg_path = base_path.with_suffix(".svg")
    figure.savefig(
        png_path,
        dpi=180,
        bbox_inches="tight",
        metadata={"Software": "otg-lab"},
    )
    figure.savefig(
        svg_path,
        bbox_inches="tight",
        metadata={"Creator": "otg-lab", "Date": None},
    )
    return png_path, svg_path


def validate_figure_files(paths: Iterable[Path]) -> None:
    failures: list[str] = []
    for path in paths:
        if not path.is_file() or path.stat().st_size < 100:
            failures.append(f"missing_or_empty:{path}")
            continue
        if path.suffix == ".png" and path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            failures.append(f"invalid_png:{path}")
        if (
            path.suffix == ".svg"
            and "<svg" not in path.read_text(encoding="utf-8")[:1000]
        ):
            failures.append(f"invalid_svg:{path}")
    if failures:
        raise AnalysisValidationError("figure QA failed: " + ", ".join(failures))


def write_analysis_manifest(
    prepared: PreparedAnalysis,
    output_path: Path,
    output_files: Sequence[Path],
) -> None:
    analysis_directory = prepared.config_path.parent
    sources = []
    for source in prepared.sources:
        sources.append(
            {
                "source_id": source.source_id,
                "experiment_id": source.experiment_id,
                "source_directory": source.relative_directory,
                "manifest_sha256": source.manifest_sha256,
                "git_commit": source.manifest.get("git", {}).get("commit"),
                "git_dirty": source.manifest.get("git", {}).get("dirty"),
            }
        )
    artifacts = [
        dict(item)
        for item in sorted(
            prepared.artifact_provenance,
            key=lambda item: (str(item["artifact_id"]), str(item["source_id"])),
        )
    ]
    outputs = [
        {
            "path": path.resolve().relative_to(analysis_directory.resolve()).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(output_files, key=lambda item: item.as_posix())
        if path.resolve() != output_path.resolve()
    ]
    payload = {
        "schema_version": "otg.cross_analysis.result_manifest.v1",
        "analysis_id": prepared.analysis_id,
        "config_path": prepared.config_path.relative_to(
            prepared.project_root
        ).as_posix(),
        "config_sha256": prepared.config_sha256,
        "sources": sources,
        "input_artifacts": artifacts,
        "outputs": outputs,
    }
    write_text(
        output_path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    )
