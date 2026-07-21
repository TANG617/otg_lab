"""Config-driven execution helpers for the paper-evidence experiment suites.

This module contains experiment mechanics, not method selection policy.  Every
case receives a complete resolved configuration, and every online pipeline is
fed only canonical rows whose arrival time is no later than the control tick.
Formal artifact bundles are written only through :class:`ArtifactWriter`.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .artifacts import (
    ArtifactWriter,
    sha256_file,
    validate_artifact_bundle,
)
from .config import write_resolved_config
from .datasets import (
    FAMILIES,
    SplitEntry,
    StressConfig,
    apply_stress,
    entries_for_split,
    generate_trajectory,
    trajectory_to_rows,
)
from .runner import run_pipeline_rows
from .schema import write_parquet

PRIMARY_LIMITS = {
    "max_velocity": 4.1,
    "max_acceleration": 8.2,
    "max_jerk": 4000.0,
}
FAILURE_FIELDS = (
    "run_id",
    "method_id",
    "dataset_id",
    "session_id",
    "trajectory_id",
    "scenario_id",
    "case_id",
    "joint_id",
    "k",
    "failure_type",
    "reason",
)
FALLBACK_FIELDS = (
    "run_id",
    "method_id",
    "dataset_id",
    "session_id",
    "trajectory_id",
    "scenario_id",
    "joint_id",
    "k",
    "control_time",
    "fallback_reason",
)


@dataclass(frozen=True)
class ExperimentOutcome:
    """All row-level outputs from a predeclared matrix."""

    samples: list[dict[str, Any]]
    constraint_audits: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    fallback_events: list[dict[str, Any]]
    attempted_trajectory_runs: int
    successful_trajectory_runs: int
    method_matrix: list[dict[str, Any]]
    expected_units: list[dict[str, Any]]


def combine_outcomes(outcomes: Iterable[ExperimentOutcome]) -> ExperimentOutcome:
    """Concatenate independently configured submatrices without losing failures."""

    values = list(outcomes)
    if not values:
        raise ValueError("cannot combine an empty outcome sequence")
    method_matrix: list[dict[str, Any]] = []
    expected_units: list[dict[str, Any]] = []
    for value in values:
        for row in value.method_matrix:
            if row not in method_matrix:
                method_matrix.append(copy.deepcopy(row))
        expected_units.extend(copy.deepcopy(value.expected_units))
    attempted = sum(value.attempted_trajectory_runs for value in values)
    return ExperimentOutcome(
        samples=[row for value in values for row in value.samples],
        constraint_audits=[row for value in values for row in value.constraint_audits],
        failures=[row for value in values for row in value.failures],
        fallback_events=[row for value in values for row in value.fallback_events],
        attempted_trajectory_runs=attempted,
        successful_trajectory_runs=sum(
            value.successful_trajectory_runs for value in values
        ),
        method_matrix=method_matrix,
        expected_units=expected_units,
    )


def _deep_merge(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def serializable_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Strip loader-only keys before hashing and manifest serialization."""

    return {
        key: copy.deepcopy(value)
        for key, value in config.items()
        if not str(key).startswith("_")
    }


def stratified_entries(
    split: str,
    *,
    maximum: int | None = None,
    manifest_path: str | Path = "split_manifest.json",
) -> list[SplitEntry]:
    """Select a deterministic family-balanced prefix from a frozen split."""

    entries = entries_for_split(split, manifest_path=manifest_path)
    if maximum is None or maximum >= len(entries):
        return entries
    if maximum <= 0:
        raise ValueError("maximum must be positive or None")
    by_family = {
        family: [entry for entry in entries if entry.family == family]
        for family in FAMILIES
    }
    selected: list[SplitEntry] = []
    offset = 0
    while len(selected) < maximum:
        made_progress = False
        for family in FAMILIES:
            if offset < len(by_family[family]) and len(selected) < maximum:
                selected.append(by_family[family][offset])
                made_progress = True
        if not made_progress:
            break
        offset += 1
    return selected


def synthetic_cases(
    split: str,
    *,
    sample_rate_hz: float,
    maximum: int | None = None,
    manifest_path: str | Path = "split_manifest.json",
    run_id: str = "synthetic-cases",
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Generate clean high-resolution truth and resample it at the requested rate."""

    cases = []
    for entry in stratified_entries(
        split, maximum=maximum, manifest_path=manifest_path
    ):
        truth = generate_trajectory(entry)
        cases.append(
            (
                entry.trajectory_id,
                trajectory_to_rows(
                    truth,
                    sample_rate_hz=sample_rate_hz,
                    run_id=run_id,
                ),
            )
        )
    return cases


def stressed_cases(
    split: str,
    scenarios: Sequence[StressConfig],
    *,
    sample_rate_hz: float = 100.0,
    maximum: int | None = None,
    manifest_path: str | Path = "split_manifest.json",
    run_id: str = "stressed-cases",
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Apply every fixed stress realization to every predeclared trajectory."""

    clean = synthetic_cases(
        split,
        sample_rate_hz=sample_rate_hz,
        maximum=maximum,
        manifest_path=manifest_path,
        run_id=run_id,
    )
    cases: list[tuple[str, list[dict[str, Any]]]] = []
    for trajectory_id, rows in clean:
        for scenario in scenarios:
            stressed = apply_stress(rows, scenario)
            cases.append((f"{trajectory_id}::{scenario.scenario_id}", stressed))
    return cases


def _run_identifier(bundle_run_id: str, method_id: str) -> str:
    return f"{bundle_run_id}::{method_id}"


def run_pipeline_matrix(
    cases: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
    base_config: Mapping[str, Any],
    methods: Sequence[Mapping[str, Any]],
) -> ExperimentOutcome:
    """Execute a fixed method-by-trajectory matrix with explicit failures."""

    if not cases or not methods:
        raise ValueError("cases and methods must both be non-empty")
    bundle_run_id = str(base_config["run_id"])
    samples: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    method_matrix: list[dict[str, Any]] = []
    expected_units: list[dict[str, Any]] = []
    attempted = 0
    successful = 0
    method_ids: set[str] = set()
    for method in methods:
        if "method_id" not in method:
            raise ValueError("every method requires method_id")
        method_id = str(method["method_id"])
        if method_id in method_ids:
            raise ValueError(f"duplicate method_id {method_id}")
        method_ids.add(method_id)
        overrides = {key: value for key, value in method.items() if key != "method_id"}
        method_config = _deep_merge(base_config, overrides)
        # Parameter dictionaries describe one method and must replace, rather
        # than inherit, parameters belonging to the base method.
        pipeline_override = overrides.get("pipeline", {})
        for parameter_field in (
            "estimator_parameters",
            "predictor_parameters",
            "governor_parameters",
            "plant_parameters",
        ):
            if parameter_field in pipeline_override:
                method_config["pipeline"][parameter_field] = copy.deepcopy(
                    pipeline_override[parameter_field]
                )
        method_config["pipeline"]["method_id"] = method_id
        method_matrix.append(
            {
                "method_id": method_id,
                "pipeline": copy.deepcopy(method_config["pipeline"]),
                "control": copy.deepcopy(method_config["control"]),
                "limits": copy.deepcopy(method_config["limits"]),
                "data": copy.deepcopy(method_config["data"]),
            }
        )
        method_run_id = _run_identifier(bundle_run_id, method_id)
        for case_id, raw_rows in cases:
            attempted += 1
            rows = [copy.deepcopy(dict(row)) for row in raw_rows]
            if not rows:
                raise ValueError(f"case {case_id!r} contains no sample rows")
            for row in rows:
                row["run_id"] = method_run_id
            identity = rows[0]
            trajectory_id = str(identity.get("trajectory_id", case_id))
            expected_units.append(
                {
                    "method_id": method_id,
                    "dataset_id": str(identity["dataset_id"]),
                    "session_id": str(identity["session_id"]),
                    "trajectory_id": trajectory_id,
                    "scenario_id": str(identity["scenario_id"]),
                    "case_id": str(case_id),
                }
            )
            try:
                result = run_pipeline_rows(rows, method_config)
            except Exception as error:  # Every failure is an artifact row.
                failures.append(
                    {
                        "run_id": method_run_id,
                        "method_id": method_id,
                        "dataset_id": str(identity["dataset_id"]),
                        "session_id": str(identity["session_id"]),
                        "trajectory_id": trajectory_id,
                        "scenario_id": str(identity["scenario_id"]),
                        "case_id": str(case_id),
                        "joint_id": "__trajectory__",
                        "k": None,
                        "failure_type": type(error).__name__,
                        "reason": str(error) or repr(error),
                    }
                )
                continue
            successful += 1
            samples.extend(result.rows)
            for audit in result.constraint_audits:
                audit["method_id"] = method_id
                audit["joint_index"] = int(str(audit["joint_id"]).split("_")[-1])
                audits.append(audit)
            fallbacks.extend(
                {
                    "run_id": str(row["run_id"]),
                    "method_id": method_id,
                    "dataset_id": str(row["dataset_id"]),
                    "session_id": str(row["session_id"]),
                    "trajectory_id": str(row["trajectory_id"]),
                    "scenario_id": str(row["scenario_id"]),
                    "joint_id": str(row["joint_id"]),
                    "k": int(row["k"]),
                    "control_time": float(row["control_time"]),
                    "fallback_reason": str(row["fallback_reason"]),
                }
                for row in result.rows
                if bool(row["fallback"])
            )
    if not samples:
        raise RuntimeError("experiment matrix produced no successful sample rows")
    unit_fields = (
        "method_id",
        "dataset_id",
        "session_id",
        "trajectory_id",
        "scenario_id",
    )
    expected_keys = [
        tuple(row[field] for field in unit_fields) for row in expected_units
    ]
    if len(expected_keys) != len(set(expected_keys)):
        raise ValueError(
            "expected experiment unit matrix contains duplicate identities"
        )
    successful_keys = {tuple(row[field] for field in unit_fields) for row in samples}
    failed_keys = {tuple(row[field] for field in unit_fields) for row in failures}
    if successful_keys & failed_keys:
        raise ValueError("an experiment unit is marked both successful and failed")
    if successful_keys | failed_keys != set(expected_keys):
        raise ValueError("successful/failed units do not cover the expected matrix")
    if len(expected_units) != attempted:
        raise ValueError("expected unit count differs from attempted trajectory runs")
    return ExperimentOutcome(
        samples,
        audits,
        failures,
        fallbacks,
        attempted,
        successful,
        method_matrix,
        expected_units,
    )


def _data_manifest(
    outcome: ExperimentOutcome,
    *,
    split: str,
    sample_rates_hz: Sequence[float],
    source: str,
    selection_policy: str,
) -> dict[str, Any]:
    trajectories = sorted({str(row["trajectory_id"]) for row in outcome.expected_units})
    scenarios = sorted({str(row["scenario_id"]) for row in outcome.expected_units})
    methods = sorted({str(row["method_id"]) for row in outcome.expected_units})
    return {
        "schema_version": "otg.data-manifest.v1",
        "source": source,
        "split": split,
        "selection_policy": selection_policy,
        "sample_rates_hz": [float(value) for value in sample_rates_hz],
        "trajectory_count": len(trajectories),
        "trajectory_ids": trajectories,
        "scenario_count": len(scenarios),
        "scenario_ids": scenarios,
        "method_count": len(methods),
        "method_ids": methods,
        "attempted_trajectory_runs": outcome.attempted_trajectory_runs,
        "expected_trajectory_runs": len(outcome.expected_units),
        "successful_trajectory_runs": outcome.successful_trajectory_runs,
        "failure_count": len(outcome.failures),
        "sample_row_count": len(outcome.samples),
    }


def _bundle_split_manifest(
    outcome: ExperimentOutcome,
    config: Mapping[str, Any],
    *,
    split: str,
    source: str,
) -> dict[str, Any]:
    """Use the frozen clean manifest only when it actually declares the suite."""

    parent_path = Path(config["data"]["split_manifest"])
    with parent_path.open(encoding="utf-8") as stream:
        parent = json.load(stream)
    declared = {str(row["trajectory_id"]) for row in parent.get("trajectories", [])}
    actual = {str(row["trajectory_id"]) for row in outcome.expected_units}
    if actual and actual <= declared:
        return parent

    sample_identity: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in outcome.samples:
        key = (
            str(row["dataset_id"]),
            str(row["session_id"]),
            str(row["trajectory_id"]),
            str(row["scenario_id"]),
        )
        sample_identity.setdefault(
            key,
            {
                "dataset_id": key[0],
                "session_id": key[1],
                "trajectory_id": key[2],
                "scenario_id": key[3],
                "split": str(row["split"]),
                "seed": int(row["seed"]),
                "reference_family": str(row.get("reference_family", "unavailable")),
            },
        )
    expected_population = {
        (
            str(row["dataset_id"]),
            str(row["session_id"]),
            str(row["trajectory_id"]),
            str(row["scenario_id"]),
        )
        for row in outcome.expected_units
    }
    if set(sample_identity) != expected_population:
        missing = expected_population - set(sample_identity)
        # A method may fail while another method supplies the same population
        # identity.  If every method failed, provenance is still retained from
        # the expected matrix with explicit unavailable seed/family labels.
        for key in sorted(missing):
            sample_identity[key] = {
                "dataset_id": key[0],
                "session_id": key[1],
                "trajectory_id": key[2],
                "scenario_id": key[3],
                "split": split,
                "seed": "unavailable_due_to_all_methods_failed",
                "reference_family": "unavailable_due_to_all_methods_failed",
            }
    return {
        "schema_version": "otg.suite-population.v1",
        "source": source,
        "split": split,
        "selection_unit": "whole trajectory and scenario",
        "parent_split_manifest": {
            "path": str(parent_path),
            "sha256": sha256_file(parent_path),
            "applicable_to_population": False,
        },
        "population_count": len(sample_identity),
        "population": [sample_identity[key] for key in sorted(sample_identity)],
    }


def runtime_table(
    samples: Sequence[Mapping[str, Any]], *, warmup_samples: int = 100
) -> list[dict[str, Any]]:
    """Aggregate raw in-memory cycle timings after per-trajectory warm-up."""

    if warmup_samples < 0:
        raise ValueError("warmup_samples must be non-negative")
    trajectories: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in samples:
        trajectories[
            (
                row["method_id"],
                row["dataset_id"],
                row["session_id"],
                row["trajectory_id"],
                row["scenario_id"],
            )
        ].append(row)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for key, rows in trajectories.items():
        by_k: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            by_k[int(row["k"])].append(row)
        cycles = [
            sorted(by_k[k], key=lambda item: str(item["joint_id"]))[0]
            for k in sorted(by_k)
        ]
        grouped[str(key[0])].extend(cycles[warmup_samples:])
    output = []
    for method, rows in sorted(grouped.items()):
        if not rows:
            raise ValueError(f"warmup removed all runtime samples for {method}")
        total = np.asarray([float(row["total_compute_us"]) for row in rows])
        record = {
            "method": method,
            "warmup_samples_per_trajectory": warmup_samples,
            "timed_cycle_count": int(total.size),
            "runtime_p50_us": float(np.quantile(total, 0.50)),
            "runtime_p90_us": float(np.quantile(total, 0.90)),
            "runtime_p99_us": float(np.quantile(total, 0.99)),
            "runtime_p99_9_us": float(np.quantile(total, 0.999)),
            "runtime_max_us": float(np.max(total)),
            "runtime_deadline_miss_rate": float(
                np.mean(
                    total > np.asarray([float(row["dt_control"]) * 1e6 for row in rows])
                )
            ),
        }
        for layer in ("estimator", "predictor", "governor", "follower", "plant"):
            field = f"{layer}_compute_us"
            values = np.asarray(
                [float(row[field]) for row in rows if row.get(field) is not None]
            )
            if values.size:
                record.update(
                    {
                        f"{layer}_p50_us": float(np.quantile(values, 0.50)),
                        f"{layer}_p90_us": float(np.quantile(values, 0.90)),
                        f"{layer}_p99_us": float(np.quantile(values, 0.99)),
                        f"{layer}_p99_9_us": float(np.quantile(values, 0.999)),
                        f"{layer}_max_us": float(np.max(values)),
                    }
                )
        output.append(record)
    return output


def repeated_runtime_study(
    cases: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
    base_config: Mapping[str, Any],
    methods: Sequence[Mapping[str, Any]],
    *,
    repetitions: int = 5,
    warmup_cycles: int = 100,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Repeat the in-memory pipeline and retain post-warm-up cycle timings.

    The runner's timers enclose only estimator through plant execution.  This
    helper performs no plotting or artifact I/O while timing, discards the
    first ``warmup_cycles`` independently for every trajectory, and preserves
    all five formal repetitions rather than pooling away run-to-run variation.
    One synchronized cycle is recorded once even when the trajectory has
    multiple joints.
    """

    if isinstance(repetitions, bool) or repetitions < 1:
        raise ValueError("repetitions must be a positive integer")
    if isinstance(warmup_cycles, bool) or warmup_cycles < 0:
        raise ValueError("warmup_cycles must be a non-negative integer")
    samples: list[dict[str, Any]] = []
    for repetition in range(int(repetitions)):
        outcome = run_pipeline_matrix(cases, base_config, methods)
        if outcome.failures:
            raise RuntimeError(
                "runtime repetition produced failures; timings would be incomplete"
            )
        grouped: dict[
            tuple[str, str, str, str, str], dict[int, list[Mapping[str, Any]]]
        ] = defaultdict(lambda: defaultdict(list))
        for row in outcome.samples:
            key = (
                str(row["method_id"]),
                str(row["dataset_id"]),
                str(row["session_id"]),
                str(row["trajectory_id"]),
                str(row["scenario_id"]),
            )
            grouped[key][int(row["k"])].append(row)
        for key, by_cycle in sorted(grouped.items()):
            cycle_indices = sorted(by_cycle)
            if len(cycle_indices) <= warmup_cycles:
                raise ValueError(
                    f"warmup removed all runtime cycles for {key[0]} / {key[3]}"
                )
            for cycle_index in cycle_indices[warmup_cycles:]:
                joint_rows = sorted(
                    by_cycle[cycle_index], key=lambda row: str(row["joint_id"])
                )
                row = joint_rows[0]
                record: dict[str, Any] = {
                    "method": key[0],
                    "dataset_id": key[1],
                    "session_id": key[2],
                    "trajectory_id": key[3],
                    "scenario_id": key[4],
                    "dof": len(joint_rows),
                    "repetition": repetition,
                    "warmup_cycles_per_trajectory": int(warmup_cycles),
                    "k": cycle_index,
                    "deadline_us": float(row["dt_control"]) * 1e6,
                    "deadline_miss": bool(row["deadline_miss"]),
                    "qp_iterations": int(row["qp_iterations"] or 0),
                }
                for layer in (
                    "estimator",
                    "predictor",
                    "governor",
                    "follower",
                    "plant",
                    "total",
                ):
                    field = f"{layer}_compute_us"
                    value = row.get(field)
                    if value is None or not np.isfinite(float(value)):
                        raise ValueError(
                            f"post-warm-up runtime value {field} is unavailable"
                        )
                    record[field] = float(value)
                samples.append(record)

    summaries: list[dict[str, Any]] = []
    grouped_samples: dict[tuple[str, int, int], list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for row in samples:
        grouped_samples[
            (str(row["method"]), int(row["dof"]), int(row["repetition"]))
        ].append(row)
    for (method, dof, repetition), rows in sorted(grouped_samples.items()):
        total = np.asarray([float(row["total_compute_us"]) for row in rows])
        summary: dict[str, Any] = {
            "method": method,
            "dof": dof,
            "repetition": repetition,
            "timed_cycle_count": int(total.size),
            "runtime_p50_us": float(np.quantile(total, 0.50)),
            "runtime_p90_us": float(np.quantile(total, 0.90)),
            "runtime_p99_us": float(np.quantile(total, 0.99)),
            "runtime_p99_9_us": float(np.quantile(total, 0.999)),
            "runtime_max_us": float(np.max(total)),
            "deadline_miss_rate": float(
                np.mean([bool(row["deadline_miss"]) for row in rows])
            ),
        }
        for layer in ("estimator", "predictor", "governor", "follower", "plant"):
            values = np.asarray([float(row[f"{layer}_compute_us"]) for row in rows])
            summary[f"{layer}_p50_us"] = float(np.quantile(values, 0.50))
            summary[f"{layer}_p90_us"] = float(np.quantile(values, 0.90))
            summary[f"{layer}_p99_us"] = float(np.quantile(values, 0.99))
            summary[f"{layer}_p99_9_us"] = float(np.quantile(values, 0.999))
            summary[f"{layer}_max_us"] = float(np.max(values))
        iterations = np.asarray([int(row["qp_iterations"]) for row in rows])
        summary.update(
            {
                "qp_iterations_p50": float(np.quantile(iterations, 0.50)),
                "qp_iterations_p99": float(np.quantile(iterations, 0.99)),
                "qp_iterations_max": int(np.max(iterations)),
            }
        )
        summaries.append(summary)
    return samples, summaries


def completion_table(outcome: ExperimentOutcome) -> list[dict[str, Any]]:
    """Keep failed trajectories in the explicit completion-rate denominator."""

    success_by_run: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in outcome.samples:
        success_by_run[str(row["run_id"])].add(
            (
                str(row["dataset_id"]),
                str(row["trajectory_id"]),
                str(row["scenario_id"]),
            )
        )
    failures_by_run: dict[str, int] = defaultdict(int)
    for row in outcome.failures:
        failures_by_run[str(row["run_id"])] += 1
    run_ids = sorted(set(success_by_run) | set(failures_by_run))
    rows = []
    for run_id in run_ids:
        successes = len(success_by_run[run_id])
        failures = failures_by_run[run_id]
        attempted = successes + failures
        rows.append(
            {
                "run_id": run_id,
                "method": run_id.split("::", 1)[-1],
                "attempted_trajectory_runs": attempted,
                "successful_trajectory_runs": successes,
                "failed_trajectory_runs": failures,
                "completion_rate": successes / attempted,
                "failure_rate": failures / attempted,
                "failed_units_in_numeric_metric_tables": False,
                "paired_comparison_requires_complete_pairs": True,
            }
        )
    if (
        sum(row["attempted_trajectory_runs"] for row in rows)
        != outcome.attempted_trajectory_runs
    ):
        raise ValueError("completion denominator differs from attempted run count")
    return rows


def write_experiment_bundle(
    output_root: str | Path,
    config: Mapping[str, Any],
    outcome: ExperimentOutcome,
    *,
    command: Sequence[str],
    repo_root: str | Path,
    split: str,
    sample_rates_hz: Sequence[float],
    source: str,
    selection_policy: str,
    expected_commit: str | None = None,
    require_clean: bool = True,
    extra_csv: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    extra_json: Mapping[str, Any] | None = None,
    extra_parquet: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Write, hash, validate, and independently recompute one standard bundle."""

    expected_fields = (
        "method_id",
        "dataset_id",
        "session_id",
        "trajectory_id",
        "scenario_id",
    )
    expected_keys = [
        tuple(row[field] for field in expected_fields) for row in outcome.expected_units
    ]
    if len(expected_keys) != outcome.attempted_trajectory_runs:
        raise ValueError("expected unit count differs from attempted trajectory runs")
    if len(expected_keys) != len(set(expected_keys)):
        raise ValueError(
            "artifact outcome contains duplicate expected experiment units"
        )

    resolved = serializable_config(config)
    writer = ArtifactWriter(
        output_root,
        run_id=str(config["run_id"]),
        command=command,
        resolved_config=resolved,
        repo_root=repo_root,
        expected_commit=expected_commit,
        require_clean=require_clean,
        manifest_extra={
            "selection_split": split,
            "test_used_for_selection": False,
            "target_output_contract": "target[k] -> output[k+1]",
        },
    )
    resolved_path = write_resolved_config(
        resolved, writer.root / "resolved_config.yaml"
    )
    writer.register(resolved_path, role="resolved_config")
    writer.write_json(
        "data_manifest.json",
        _data_manifest(
            outcome,
            split=split,
            sample_rates_hz=sample_rates_hz,
            source=source,
            selection_policy=selection_policy,
        ),
        role="data_manifest",
    )
    writer.write_json(
        "method_matrix.json",
        {
            "schema_version": "otg.method-matrix.v1",
            "methods": outcome.method_matrix,
        },
        role="expanded_method_matrix",
    )
    writer.write_json(
        "expected_unit_matrix.json",
        {
            "schema_version": "otg.expected-unit-matrix.v1",
            "unit_fields": [
                "method_id",
                "dataset_id",
                "session_id",
                "trajectory_id",
                "scenario_id",
                "case_id",
            ],
            "expected_unit_count": len(outcome.expected_units),
            "units": outcome.expected_units,
        },
        role="predeclared_expected_unit_matrix",
    )
    writer.write_json(
        "split_manifest.json",
        _bundle_split_manifest(
            outcome,
            config,
            split=split,
            source=source,
        ),
        role="split_manifest",
    )
    writer.write_samples(outcome.samples)
    writer.write_recomputed_metrics(
        max_lag_s=1.0,
        motion_limits=PRIMARY_LIMITS,
    )
    writer.write_csv(
        "failures.csv",
        outcome.failures,
        fieldnames=FAILURE_FIELDS,
        role="failures",
        allowed_missing_fields={"k"},
        allow_empty=True,
    )
    writer.write_csv(
        "constraint_audit.csv",
        outcome.constraint_audits,
        role="continuous_constraint_audit",
        allowed_missing_fields={
            "max_abs_velocity",
            "max_abs_acceleration",
            "max_sampled_jerk",
            "max_new_jerk",
            "max_internal_jerk",
            "velocity_margin",
            "acceleration_margin",
            "jerk_margin",
            "velocity_max_time_s",
            "acceleration_max_time_s",
            "jerk_max_time_s",
        },
    )
    writer.write_csv(
        "fallback_events.csv",
        outcome.fallback_events,
        fieldnames=FALLBACK_FIELDS,
        role="fallback_events",
        allow_empty=True,
    )
    writer.write_csv(
        "runtime_benchmark.csv",
        runtime_table(
            outcome.samples,
            warmup_samples=int(
                config["runtime"].get(
                    "observational_warmup_cycles",
                    config["runtime"]["warmup_cycles"],
                )
            ),
        ),
        role="runtime_benchmark",
    )
    writer.write_csv(
        "completion_summary.csv",
        completion_table(outcome),
        role="completion_and_failure_denominators",
    )
    for name, records in sorted((extra_csv or {}).items()):
        writer.write_csv(name, list(records), role=f"extra_table:{name}")
    for name, value in sorted((extra_json or {}).items()):
        writer.write_json(name, value, role=f"extra_manifest:{name}")
    for name, records in sorted((extra_parquet or {}).items()):
        path = write_parquet(list(records), writer.root / name)
        writer.register(path, role=f"extra_canonical_samples:{name}")
    writer.finalize()
    return validate_artifact_bundle(
        writer.root,
        expected_commit=expected_commit,
        require_clean=require_clean,
        verify_recomputation=True,
        recompute_arguments={
            "max_lag_s": 1.0,
            "motion_limits": PRIMARY_LIMITS,
        },
    )


def same_information_methods(
    *,
    estimator: str,
    estimator_parameters: Mapping[str, Any],
    predictor: str,
    horizon_ms: float,
    qp_horizon_steps: int,
) -> list[dict[str, Any]]:
    """Return the predeclared deployable baseline/follower matrix."""

    common = {
        "estimator": estimator,
        "estimator_parameters": dict(estimator_parameters),
        "predictor": predictor,
        "prediction_horizon_ms": float(horizon_ms),
        "plant": "ideal",
        "plant_parameters": {},
        "measured_state_mode": "previous_command",
    }

    def method(method_id: str, **pipeline: Any) -> dict[str, Any]:
        return {
            "method_id": method_id,
            "pipeline": {**common, **pipeline},
        }

    return [
        method(
            "deployed_p_only",
            estimator="position_only",
            estimator_parameters={},
            predictor="zero_order_hold",
            prediction_horizon_ms=0.0,
            target_mode="p",
            governor="none",
            follower="ruckig",
        ),
        method(
            "predicted_p",
            target_mode="p",
            governor="none",
            follower="ruckig",
        ),
        method(
            "raw_predicted_pv",
            target_mode="pv",
            governor="none",
            follower="ruckig",
        ),
        method(
            "scalar_projected_pva",
            target_mode="pva",
            governor="scalar_projection",
            follower="ruckig",
        ),
        method(
            "one_step_governed_pva_direct",
            target_mode="pva",
            governor="one_step",
            follower="direct",
        ),
        method(
            "one_step_governed_pva_ruckig",
            target_mode="pva",
            governor="one_step",
            follower="ruckig",
        ),
        method(
            "jerk_qp_pva_direct",
            target_mode="pva",
            governor="jerk_qp",
            governor_parameters={"horizon_steps": int(qp_horizon_steps)},
            follower="direct",
        ),
        method(
            "jerk_qp_pva_ruckig",
            target_mode="pva",
            governor="jerk_qp",
            governor_parameters={"horizon_steps": int(qp_horizon_steps)},
            follower="ruckig",
        ),
    ]


def locked_method(
    *,
    estimator: str,
    estimator_parameters: Mapping[str, Any],
    predictor: str,
    horizon_ms: float,
    method_id: str = "locked_one_step_direct",
) -> dict[str, Any]:
    return {
        "method_id": method_id,
        "pipeline": {
            "estimator": estimator,
            "estimator_parameters": dict(estimator_parameters),
            "predictor": predictor,
            "prediction_horizon_ms": float(horizon_ms),
            "target_mode": "pva",
            "governor": "one_step",
            "governor_parameters": {},
            "follower": "direct",
            "plant": "ideal",
            "plant_parameters": {},
            "measured_state_mode": "previous_command",
        },
    }


__all__ = [
    "ExperimentOutcome",
    "FAILURE_FIELDS",
    "FALLBACK_FIELDS",
    "PRIMARY_LIMITS",
    "combine_outcomes",
    "completion_table",
    "locked_method",
    "run_pipeline_matrix",
    "repeated_runtime_study",
    "runtime_table",
    "same_information_methods",
    "serializable_config",
    "stratified_entries",
    "stressed_cases",
    "synthetic_cases",
    "write_experiment_bundle",
]
