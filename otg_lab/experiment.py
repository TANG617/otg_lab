"""Strict experiment declarations and the CSV-first E-series runner."""

from __future__ import annotations

import csv
import fnmatch
import json
import math
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import (
    AVAILABLE,
    AnalysisSpec,
    ComparisonSpec,
    ComparisonTable,
    EvaluationWindow,
    MetricRow,
    MetricSet,
    MetricTable,
    ReferenceAnalysis,
    analyze_reference,
    analyze_tracking,
    compare_methods,
    get_metric_spec,
)
from .csvio import (
    load_trajectory_csv,
    load_trajectory_metadata,
    sha256_file,
    write_trajectory_csv,
)
from .models import (
    RunConfig,
    TrackingMethodSpec,
    TrackingRun,
    TrackingStatus,
    Trajectory,
    TrajectoryMetadata,
)
from .runio import (
    collect_environment,
    collect_git_state,
    jsonable,
    sha256_json,
    utc_run_stamp,
    write_json,
    write_rows_csv,
)
from .tracking import PROFILE_FIELDS, TRACE_FIELDS, run_tracking

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_METRIC_ROLES = ("primary", "secondary", "guardrail", "diagnostic")


def _valid_identifier(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must begin with a letter and contain only letters, "
            "digits, '_' or '-'"
        )
    return normalized


def _method_declaration(method: TrackingMethodSpec) -> dict[str, Any]:
    value = method.as_dict()
    for name in (
        "estimator",
        "predictor",
        "target_builder",
        "governor",
        "follower",
    ):
        component = getattr(method, name)
        value[name]["factory"] = (
            None if component.factory is None else jsonable(component.factory)
        )
    return value


@dataclass(frozen=True)
class ExperimentInput:
    """One canonical reference artifact consumed by an experiment."""

    input_id: str
    csv_path: str | Path
    metadata_path: str | Path | None = None
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_id", _valid_identifier(self.input_id, "input_id")
        )
        object.__setattr__(self, "csv_path", Path(self.csv_path))
        if self.metadata_path is not None:
            object.__setattr__(self, "metadata_path", Path(self.metadata_path))
        object.__setattr__(self, "required", bool(self.required))
        object.__setattr__(self, "description", str(self.description))

    def resolve(self, project_root: Path) -> tuple[Path, Path | None]:
        csv_path = Path(self.csv_path)
        if not csv_path.is_absolute():
            csv_path = project_root / csv_path
        metadata_path = (
            None if self.metadata_path is None else Path(self.metadata_path)
        )
        if metadata_path is not None and not metadata_path.is_absolute():
            metadata_path = project_root / metadata_path
        return csv_path, metadata_path

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "csv_path": Path(self.csv_path).as_posix(),
            "metadata_path": (
                None
                if self.metadata_path is None
                else Path(self.metadata_path).as_posix()
            ),
            "required": self.required,
            "description": self.description,
        }


@dataclass(frozen=True)
class InputGate:
    """Policy separating structural validity from reported physical excess."""

    block_on_limit_violation: bool = False


@dataclass(frozen=True)
class ExperimentSpec:
    """A complete, inspectable declaration of one E-series investigation."""

    experiment_id: str
    slug: str
    title: str
    question: str
    hypothesis: str
    independent_variables: tuple[str, ...]
    controlled_variables: Mapping[str, Any]
    allowed_method_differences: tuple[str, ...]
    inputs: tuple[ExperimentInput, ...]
    methods: tuple[TrackingMethodSpec, ...]
    run_config: RunConfig
    metric_roles: Mapping[str, tuple[str, ...]]
    windows: tuple[EvaluationWindow, ...]
    comparison_spec: ComparisonSpec
    input_gate: InputGate = InputGate()
    description: str = ""

    def __post_init__(self) -> None:
        experiment_id = str(self.experiment_id).strip().upper()
        if not re.fullmatch(r"E[0-9]{2,}", experiment_id):
            raise ValueError("experiment_id must look like E01, E02, ...")
        object.__setattr__(self, "experiment_id", experiment_id)
        object.__setattr__(self, "slug", _valid_identifier(self.slug, "slug"))
        for name in ("title", "question", "hypothesis"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.independent_variables:
            raise ValueError("independent_variables must be declared")
        if not self.controlled_variables:
            raise ValueError("controlled_variables must be declared")
        if not self.allowed_method_differences:
            raise ValueError("allowed_method_differences must be declared")
        if not self.inputs:
            raise ValueError("an experiment needs at least one input")
        if not self.methods:
            raise ValueError("an experiment needs at least one tracking method")
        if not isinstance(self.run_config, RunConfig):
            raise TypeError("run_config must be RunConfig")
        if not isinstance(self.comparison_spec, ComparisonSpec):
            raise TypeError("comparison_spec must be ComparisonSpec")
        if not isinstance(self.input_gate, InputGate):
            raise TypeError("input_gate must be InputGate")
        input_ids = [item.input_id for item in self.inputs]
        method_ids = [item.method_id for item in self.methods]
        if len(set(input_ids)) != len(input_ids):
            raise ValueError("input IDs must be unique")
        if len(set(method_ids)) != len(method_ids):
            raise ValueError("method IDs must be unique")
        if not self.windows:
            raise ValueError("evaluation windows must be declared")
        if "full_overlap" not in {window.window_id for window in self.windows}:
            raise ValueError("windows must include full_overlap")
        self._validate_metric_roles()
        self._validate_comparisons()

    def _validate_metric_roles(self) -> None:
        unexpected = set(self.metric_roles) - set(_METRIC_ROLES)
        if unexpected:
            raise ValueError(f"unknown metric roles: {sorted(unexpected)}")
        if set(self.metric_roles) != set(_METRIC_ROLES):
            raise ValueError(
                "metric_roles must explicitly declare primary, secondary, "
                "guardrail, and diagnostic"
            )
        seen: dict[str, str] = {}
        for role in _METRIC_ROLES:
            for metric_id in self.metric_roles[role]:
                get_metric_spec(metric_id)
                previous = seen.setdefault(metric_id, role)
                if previous != role:
                    raise ValueError(
                        f"metric {metric_id!r} has both {previous!r} and "
                        f"{role!r} roles"
                    )
        if not seen:
            raise ValueError("at least one metric must be selected")

    def _validate_comparisons(self) -> None:
        methods = {method.method_id: method for method in self.methods}
        for pair in self.comparison_spec.pairs:
            if pair.baseline_method_id not in methods:
                raise ValueError(
                    f"unknown comparison baseline {pair.baseline_method_id!r}"
                )
            if pair.candidate_method_id not in methods:
                raise ValueError(
                    f"unknown comparison candidate {pair.candidate_method_id!r}"
                )
            differences = _method_differences(
                methods[pair.baseline_method_id],
                methods[pair.candidate_method_id],
            )
            forbidden = [
                path
                for path in differences
                if not _difference_is_allowed(
                    path, self.allowed_method_differences
                )
            ]
            if forbidden:
                raise ValueError(
                    f"comparison {pair.resolved_id!r} differs outside declared "
                    f"variable paths: {forbidden}"
                )

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(
            metric_id
            for role in _METRIC_ROLES
            for metric_id in self.metric_roles[role]
        )

    @property
    def role_by_metric(self) -> dict[str, str]:
        return {
            metric_id: role
            for role in _METRIC_ROLES
            for metric_id in self.metric_roles[role]
        }

    @property
    def directory_name(self) -> str:
        return f"{self.experiment_id}_{self.slug}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "otg.experiment.v1",
            "experiment_id": self.experiment_id,
            "slug": self.slug,
            "title": self.title,
            "question": self.question,
            "hypothesis": self.hypothesis,
            "description": self.description,
            "independent_variables": self.independent_variables,
            "controlled_variables": self.controlled_variables,
            "allowed_method_differences": self.allowed_method_differences,
            "inputs": [item.as_dict() for item in self.inputs],
            "methods": [_method_declaration(method) for method in self.methods],
            "run_config": jsonable(self.run_config),
            "metric_roles": self.metric_roles,
            "windows": [asdict(window) for window in self.windows],
            "comparison_spec": jsonable(self.comparison_spec),
            "input_gate": asdict(self.input_gate),
        }


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    run_directory: Path
    spec_hash: str
    success: bool
    failure_count: int
    required_failure_count: int


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        value = value.as_dict()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(item, path))
        return result
    return {prefix: jsonable(value)}


def _method_differences(
    baseline: TrackingMethodSpec, candidate: TrackingMethodSpec
) -> tuple[str, ...]:
    baseline_flat = _flatten(_method_declaration(baseline))
    candidate_flat = _flatten(_method_declaration(candidate))
    ignored = {"method_id", "description", "required"}
    paths = (set(baseline_flat) | set(candidate_flat)) - ignored
    return tuple(
        sorted(
            path
            for path in paths
            if baseline_flat.get(path) != candidate_flat.get(path)
        )
    )


def _difference_is_allowed(path: str, allowed: Sequence[str]) -> bool:
    for pattern in allowed:
        normalized = str(pattern).rstrip(".")
        if (
            path == normalized
            or path.startswith(normalized + ".")
            or fnmatch.fnmatchcase(path, normalized)
        ):
            return True
    return False


def validate_experiment_spec(spec: ExperimentSpec) -> None:
    """Public validation hook used by tests and custom experiment tooling."""

    if not isinstance(spec, ExperimentSpec):
        raise TypeError("spec must be ExperimentSpec")
    spec._validate_metric_roles()
    spec._validate_comparisons()


def _reference_metadata(
    source_metadata: TrajectoryMetadata,
    source_csv: Path,
    input_id: str,
) -> TrajectoryMetadata:
    return TrajectoryMetadata(
        trajectory_id=input_id,
        kind="reference",
        dt_s=source_metadata.dt_s,
        channel_semantics=dict(source_metadata.channel_semantics),
        source={
            "artifact": source_csv.as_posix(),
            "metadata": source_metadata.as_dict(),
        },
        generator_id=source_metadata.generator_id,
        generator_params=dict(source_metadata.generator_params),
        source_sha256=sha256_file(source_csv),
    )


def _derived_metadata(
    trajectory: Trajectory, input_id: str, source_hash: str
) -> TrajectoryMetadata:
    return TrajectoryMetadata.for_trajectory(
        trajectory,
        trajectory_id=f"{input_id}_derived",
        kind="reference",
        channel_semantics={
            "position_rad": "copied_reference",
            "velocity_rad_s": "analysis_estimate",
            "acceleration_rad_s2": "analysis_estimate",
            "jerk_rad_s3": "analysis_estimate",
        },
        source={
            "source_reference_sha256": source_hash,
            "derivative_method": "second_order_centered_v1",
            "online_use_forbidden": True,
        },
    )


def _command_metadata(
    trajectory: Trajectory,
    *,
    input_id: str,
    method: TrackingMethodSpec,
    tracking_run: TrackingRun,
    reference_hash: str,
) -> TrajectoryMetadata:
    return TrajectoryMetadata.for_trajectory(
        trajectory,
        trajectory_id=f"{method.method_id}_{input_id}_command",
        kind="command",
        channel_semantics={
            "position_rad": "command",
            "velocity_rad_s": (
                "command"
                if trajectory.has_velocity and trajectory.sample_count
                else "unavailable"
            ),
            "acceleration_rad_s2": (
                "command"
                if trajectory.has_acceleration and trajectory.sample_count
                else "unavailable"
            ),
            "jerk_rad_s3": (
                "instantaneous_command"
                if trajectory.has_jerk and trajectory.sample_count
                else "profile_only"
            ),
        },
        source={
            "input_id": input_id,
            "reference_sha256": reference_hash,
            "method_fingerprint": tracking_run.status.method_fingerprint,
            "interval_jerk_artifact": "command_profiles.csv",
        },
    )


def _failure_row(
    *,
    input_id: str,
    method_id: str = "",
    required: bool,
    layer: str,
    reason: str,
    valid_cycles: int = 0,
    total_cycles: int = 0,
) -> dict[str, Any]:
    return {
        "input_id": input_id,
        "method_id": method_id,
        "required": required,
        "failure_layer": layer,
        "failure_reason": reason,
        "valid_cycles": valid_cycles,
        "total_cycles": total_cycles,
    }


def _blocked_metrics(
    spec: ExperimentSpec,
    *,
    input_id: str,
    method_id: str,
    status: str,
    notes: str,
) -> MetricTable:
    rows = []
    for window in spec.windows:
        for metric_id in spec.metric_ids:
            metric = get_metric_spec(metric_id)
            rows.append(
                MetricRow(
                    input_id=input_id,
                    method_id=method_id,
                    window_id=window.window_id,
                    metric_id=metric_id,
                    value=None,
                    unit=metric.unit,
                    direction=metric.direction,
                    role=spec.role_by_metric[metric_id],
                    status=status,
                    source_semantics="unavailable",
                    sample_count=0,
                    notes=notes,
                )
            )
    return MetricTable(tuple(rows))


def _input_gate_failed(analysis: ReferenceAnalysis, gate: InputGate) -> bool:
    if not gate.block_on_limit_violation:
        return False
    return any(
        row.metric_id.endswith("_violation_count")
        and row.status == AVAILABLE
        and row.value is not None
        and float(row.value) > 0.0
        for row in analysis.rows
    )


def _method_summary(rows: Sequence[MetricRow]) -> list[dict[str, Any]]:
    groups: dict[
        tuple[str, str, str, str, str, str],
        list[MetricRow],
    ] = {}
    for row in rows:
        key = (
            row.method_id,
            row.window_id,
            row.metric_id,
            row.unit,
            row.direction,
            row.role,
        )
        groups.setdefault(key, []).append(row)
    output: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        values = [
            float(row.value)
            for row in group
            if row.status == AVAILABLE
            and row.value is not None
            and not isinstance(row.value, bool)
            and math.isfinite(float(row.value))
        ]
        total = len(group)
        available = len(values)
        if values:
            array = np.asarray(values, dtype=float)
            summary = {
                "mean": float(np.mean(array)),
                "std": (
                    float(statistics.stdev(values)) if len(values) >= 2 else 0.0
                ),
                "min": float(np.min(array)),
                "p25": float(np.quantile(array, 0.25, method="linear")),
                "median": float(np.median(array)),
                "p75": float(np.quantile(array, 0.75, method="linear")),
                "max": float(np.max(array)),
            }
            status = AVAILABLE if available == total else "partial"
        else:
            summary = {
                "mean": None,
                "std": None,
                "min": None,
                "p25": None,
                "median": None,
                "p75": None,
                "max": None,
            }
            status = "unavailable"
        method_id, window_id, metric_id, unit, direction, role = key
        output.append(
            {
                "method_id": method_id,
                "window_id": window_id,
                "metric_id": metric_id,
                "unit": unit,
                "direction": direction,
                "role": role,
                "status": status,
                "available_input_count": available,
                "total_input_count": total,
                **summary,
            }
        )
    return output


def _write_figures(
    figures_dir: Path,
    references: Mapping[str, Trajectory],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
) -> None:
    figures_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    for input_id, reference in references.items():
        figure, axis = plt.subplots(figsize=(10, 4.5), constrained_layout=True)
        axis.plot(
            reference.time_s,
            reference.position_rad,
            color="black",
            linewidth=1.5,
            label="reference",
        )
        for (method_id, run_input_id), tracking_run in sorted(
            tracking_runs.items()
        ):
            if run_input_id != input_id or tracking_run.command is None:
                continue
            command = tracking_run.command
            if command.sample_count:
                axis.plot(
                    command.time_s,
                    command.position_rad,
                    linewidth=1.0,
                    label=method_id,
                )
        axis.set(
            title=f"{input_id}: reference and commands",
            xlabel="time (s)",
            ylabel="position (rad)",
        )
        axis.grid(alpha=0.25)
        axis.legend(loc="best")
        figure.savefig(figures_dir / f"{input_id}_position.png", dpi=150)
        plt.close(figure)


def _write_report(
    path: Path,
    spec: ExperimentSpec,
    result_status: str,
    failures: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
) -> None:
    primary = [
        row
        for row in summaries
        if row["role"] == "primary" and row["window_id"] == "full_overlap"
    ]
    lines = [
        f"# {spec.title}",
        "",
        f"- Experiment: `{spec.experiment_id}`",
        f"- Run status: `{result_status}`",
        f"- Inputs: {len(spec.inputs)}",
        f"- Methods: {len(spec.methods)}",
        f"- Recorded failures: {len(failures)}",
        "",
        "This run validates the CSV-first infrastructure and artifact chain. "
        "It does not claim scientific superiority for either method.",
        "",
        "## Declared question",
        "",
        spec.question,
        "",
        "## Primary metric readout",
        "",
        "| method | metric | available inputs | mean | unit |",
        "|---|---|---:|---:|---|",
    ]
    for row in primary:
        mean = "" if row["mean"] is None else f"{float(row['mean']):.8g}"
        lines.append(
            f"| {row['method_id']} | {row['metric_id']} | "
            f"{row['available_input_count']}/{row['total_input_count']} | "
            f"{mean} | {row['unit']} |"
        )
    if failures:
        lines.extend(
            [
                "",
                "## Failures",
                "",
                "| input | method | layer | reason |",
                "|---|---|---|---|",
            ]
        )
        for failure in failures:
            reason = str(failure["failure_reason"]).replace("|", "\\|")
            lines.append(
                f"| {failure['input_id']} | {failure['method_id']} | "
                f"{failure['failure_layer']} | {reason} |"
            )
    lines.extend(
        [
            "",
            "## Artifact interpretation",
            "",
            "`reference_metrics.csv` describes inputs; "
            "`trajectory_metrics.csv` contains one metric per tidy row; "
            "`method_summary.csv` contains descriptive statistics; and "
            "`comparisons.csv` reports only complete paired comparisons.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _output_hashes(run_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(run_directory).as_posix(): sha256_file(path)
        for path in sorted(run_directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


_TRACE_BOOLEAN_FIELDS = {
    "fallback_requested",
    "fallback_applied",
    "safety_guarantee",
    "emergency_mode",
    "deadline_miss",
    "component_reset",
}
_TRACE_STRING_FIELDS = {
    "posterior_status",
    "prediction_status",
    "estimator_id",
    "predictor_id",
    "target_builder_id",
    "governor_id",
    "follower_id",
    "governor_status",
    "follower_status",
    "solver_status",
    "fallback_reason",
    "status",
    "error_layer",
    "error_reason",
}


def _parse_bool(value: str, field_name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"{field_name} must contain true or false")


def _load_artifact_rows(path: Path, kind: str) -> tuple[dict[str, Any], ...]:
    expected = TRACE_FIELDS if kind == "trace" else PROFILE_FIELDS
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != tuple(expected):
            raise ValueError(f"{path} does not use the canonical {kind} header")
        rows: list[dict[str, Any]] = []
        for raw in reader:
            row: dict[str, Any] = {}
            for field_name in expected:
                value = raw[field_name]
                if value == "":
                    row[field_name] = None
                elif kind == "trace" and field_name == "cycle_index":
                    row[field_name] = int(value)
                elif kind == "trace" and field_name in _TRACE_BOOLEAN_FIELDS:
                    row[field_name] = _parse_bool(value, field_name)
                elif kind == "trace" and field_name in _TRACE_STRING_FIELDS:
                    row[field_name] = value
                elif kind == "profile" and field_name in {
                    "cycle_index",
                    "segment_index",
                }:
                    row[field_name] = int(value)
                elif kind == "profile" and field_name == "exact":
                    row[field_name] = _parse_bool(value, field_name)
                elif kind == "profile" and field_name == "profile_id":
                    row[field_name] = value
                else:
                    row[field_name] = float(value)
            rows.append(row)
    return tuple(rows)


def load_tracking_run_artifacts(
    method_input_directory: str | Path,
    *,
    method_id: str | None = None,
) -> TrackingRun:
    """Reconstruct a tracking run solely from its durable artifact directory."""

    directory = Path(method_input_directory)
    command = load_trajectory_csv(
        directory / "command.csv", require_metadata=True
    )
    with (directory / "status.json").open("r", encoding="utf-8") as handle:
        status_value = json.load(handle)
    if not isinstance(status_value, Mapping):
        raise ValueError("status.json must contain an object")
    status = TrackingStatus(**dict(status_value))
    resolved_method_id = method_id
    if resolved_method_id is None:
        if directory.parent == directory:
            raise ValueError("method_id cannot be inferred from directory")
        resolved_method_id = directory.parent.name
    return TrackingRun(
        method_id=resolved_method_id,
        command=command,
        trace_rows=_load_artifact_rows(directory / "trace.csv", "trace"),
        profile_rows=_load_artifact_rows(
            directory / "command_profiles.csv", "profile"
        ),
        status=status,
    )


def run_experiment(
    experiment_spec: ExperimentSpec,
    *,
    project_root: str | Path | None = None,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
) -> ExperimentResult:
    """Execute an experiment while isolating method/input failures.

    By default, durable artifacts stay beside their experiment declaration at
    ``experiments/<experiment-directory>/runs``.  ``runs_root`` can override
    that container directory for CI or temporary executions.
    """

    validate_experiment_spec(experiment_spec)
    root = Path(project_root or Path.cwd()).resolve()
    output_root = Path(
        runs_root
        or root
        / "experiments"
        / experiment_spec.directory_name
        / "runs"
    )
    if not output_root.is_absolute():
        output_root = root / output_root
    spec_payload = experiment_spec.as_dict()
    spec_hash = sha256_json(spec_payload)
    run_directory = output_root / f"{utc_run_stamp()}__{spec_hash[:12]}"
    (run_directory / "inputs").mkdir(parents=True, exist_ok=False)
    (run_directory / "methods").mkdir(parents=True, exist_ok=True)
    analysis_directory = run_directory / "analysis"
    analysis_directory.mkdir(parents=True, exist_ok=True)

    manifest_path = run_directory / "manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": "otg.run_manifest.v1",
        "status": "running",
        "spec_hash": spec_hash,
        "resolved_experiment_spec": spec_payload,
        "git": collect_git_state(root),
        "environment": collect_environment(),
        "inputs": {},
        "methods": {},
        "outputs": {},
    }
    write_json(manifest_path, manifest)

    failures: list[dict[str, Any]] = []
    reference_rows: list[MetricRow] = []
    trajectory_rows: list[MetricRow] = []
    metric_tables: list[MetricTable] = []
    references: dict[str, Trajectory] = {}
    tracking_runs: dict[tuple[str, str], TrackingRun] = {}
    required_failure_count = 0

    for input_spec in experiment_spec.inputs:
        input_id = input_spec.input_id
        input_directory = run_directory / "inputs" / input_id
        input_directory.mkdir(parents=True, exist_ok=True)
        source_csv, source_metadata_path = input_spec.resolve(root)
        try:
            reference = load_trajectory_csv(
                source_csv,
                metadata_path=source_metadata_path,
                require_metadata=True,
            )
            source_metadata = load_trajectory_metadata(
                source_metadata_path or source_csv
            )
            run_reference_path = input_directory / "reference.csv"
            write_trajectory_csv(
                run_reference_path,
                reference,
                _reference_metadata(
                    source_metadata,
                    source_csv,
                    input_id,
                ),
            )
            # Re-read the run-local bytes so both generated and recorded
            # sources enter all later stages through the same strict loader.
            reference = load_trajectory_csv(
                run_reference_path, require_metadata=True
            )
            references[input_id] = reference
            reference_hash = sha256_file(run_reference_path)
            manifest["inputs"][input_id] = {
                "source_csv": source_csv.as_posix(),
                "source_metadata": (
                    source_metadata_path.as_posix()
                    if source_metadata_path is not None
                    else source_csv.with_suffix(".meta.json").as_posix()
                ),
                "reference_sha256": reference_hash,
                "metadata_sha256": sha256_file(
                    run_reference_path.with_suffix(".meta.json")
                ),
                "sample_count": reference.sample_count,
                "dt_s": reference.dt,
            }
        except Exception as error:
            failure = _failure_row(
                input_id=input_id,
                required=input_spec.required,
                layer="input_load",
                reason=f"{type(error).__name__}: {error}",
            )
            failures.append(failure)
            if input_spec.required:
                required_failure_count += 1
            for method in experiment_spec.methods:
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=method.method_id,
                    status="unavailable_invalid_input",
                    notes=failure["failure_reason"],
                )
                metric_tables.append(table)
                trajectory_rows.extend(table.rows)
            continue

        try:
            reference_analysis = analyze_reference(
                reference,
                AnalysisSpec(
                    input_id=input_id,
                    limits=experiment_spec.run_config.limits,
                ),
            )
            reference_rows.extend(reference_analysis.rows)
            if reference_analysis.derivative_semantics is not None:
                derived = reference_analysis.derived_trajectory
                if not isinstance(derived, Trajectory):
                    derived = Trajectory(
                        sample_index=derived.sample_index,
                        time_s=derived.time_s,
                        position_rad=derived.position_rad,
                        velocity_rad_s=derived.velocity_rad_s,
                        acceleration_rad_s2=derived.acceleration_rad_s2,
                        jerk_rad_s3=derived.jerk_rad_s3,
                    )
                write_trajectory_csv(
                    input_directory / "reference_derived.csv",
                    derived,
                    _derived_metadata(derived, input_id, reference_hash),
                )
        except Exception as error:
            failure = _failure_row(
                input_id=input_id,
                required=input_spec.required,
                layer="reference_analysis",
                reason=f"{type(error).__name__}: {error}",
            )
            failures.append(failure)
            if input_spec.required:
                required_failure_count += 1
            for method in experiment_spec.methods:
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=method.method_id,
                    status="unavailable_reference_analysis",
                    notes=failure["failure_reason"],
                )
                metric_tables.append(table)
                trajectory_rows.extend(table.rows)
            continue

        if _input_gate_failed(reference_analysis, experiment_spec.input_gate):
            failure = _failure_row(
                input_id=input_id,
                required=input_spec.required,
                layer="input_gate",
                reason="declared reference exceeds one or more motion limits",
            )
            failures.append(failure)
            if input_spec.required:
                required_failure_count += 1
            for method in experiment_spec.methods:
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=method.method_id,
                    status="unavailable_input_gate",
                    notes=failure["failure_reason"],
                )
                metric_tables.append(table)
                trajectory_rows.extend(table.rows)
            continue

        for method in experiment_spec.methods:
            method_directory = (
                run_directory / "methods" / method.method_id / input_id
            )
            method_directory.mkdir(parents=True, exist_ok=True)
            tracking_run = run_tracking(
                reference, method, experiment_spec.run_config
            )
            tracking_runs[(method.method_id, input_id)] = tracking_run
            if tracking_run.command is None:
                raise RuntimeError("run_tracking returned no command trajectory")
            write_trajectory_csv(
                method_directory / "command.csv",
                tracking_run.command,
                _command_metadata(
                    tracking_run.command,
                    input_id=input_id,
                    method=method,
                    tracking_run=tracking_run,
                    reference_hash=reference_hash,
                ),
            )
            write_rows_csv(
                method_directory / "trace.csv",
                tracking_run.trace_rows,
                fieldnames=TRACE_FIELDS,
            )
            write_rows_csv(
                method_directory / "command_profiles.csv",
                (
                    asdict(row)
                    if hasattr(row, "__dataclass_fields__")
                    else row
                    for row in tracking_run.profile_rows
                ),
                fieldnames=PROFILE_FIELDS,
            )
            write_json(method_directory / "status.json", tracking_run.status)
            manifest["methods"].setdefault(method.method_id, {})[input_id] = {
                "fingerprint": tracking_run.status.method_fingerprint,
                "completed": tracking_run.status.completed,
                "valid_cycles": tracking_run.status.valid_cycles,
                "total_cycles": tracking_run.status.total_cycles,
            }
            if not tracking_run.status.completed:
                failure = _failure_row(
                    input_id=input_id,
                    method_id=method.method_id,
                    required=method.required,
                    layer=tracking_run.status.failure_layer or "tracking",
                    reason=tracking_run.status.failure_reason
                    or "tracking did not complete",
                    valid_cycles=tracking_run.status.valid_cycles,
                    total_cycles=tracking_run.status.total_cycles,
                )
                failures.append(failure)
                if method.required:
                    required_failure_count += 1
            try:
                table = analyze_tracking(
                    reference,
                    tracking_run,
                    MetricSet(
                        metric_ids=experiment_spec.metric_ids,
                        roles=experiment_spec.role_by_metric,
                        windows=experiment_spec.windows,
                        input_id=input_id,
                        limits=experiment_spec.run_config.limits,
                    ),
                )
            except Exception as error:
                failure = _failure_row(
                    input_id=input_id,
                    method_id=method.method_id,
                    required=method.required,
                    layer="tracking_analysis",
                    reason=f"{type(error).__name__}: {error}",
                    valid_cycles=tracking_run.status.valid_cycles,
                    total_cycles=tracking_run.status.total_cycles,
                )
                failures.append(failure)
                if method.required:
                    required_failure_count += 1
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=method.method_id,
                    status="unavailable_analysis_failure",
                    notes=failure["failure_reason"],
                )
            metric_tables.append(table)
            trajectory_rows.extend(table.rows)

    resolved_comparison = experiment_spec.comparison_spec
    if not resolved_comparison.input_ids:
        resolved_comparison = replace(
            resolved_comparison,
            input_ids=tuple(item.input_id for item in experiment_spec.inputs),
        )
    comparison_table: ComparisonTable = compare_methods(
        metric_tables, resolved_comparison
    )
    summaries = _method_summary(trajectory_rows)

    write_rows_csv(
        analysis_directory / "reference_metrics.csv",
        (row.to_dict() for row in reference_rows),
        fieldnames=(
            "input_id",
            "method_id",
            "window_id",
            "metric_id",
            "value",
            "unit",
            "direction",
            "role",
            "status",
            "source_semantics",
            "sample_count",
            "notes",
        ),
    )
    write_rows_csv(
        analysis_directory / "trajectory_metrics.csv",
        (row.to_dict() for row in trajectory_rows),
        fieldnames=(
            "input_id",
            "method_id",
            "window_id",
            "metric_id",
            "value",
            "unit",
            "direction",
            "role",
            "status",
            "source_semantics",
            "sample_count",
            "notes",
        ),
    )
    write_rows_csv(analysis_directory / "method_summary.csv", summaries)
    write_rows_csv(
        analysis_directory / "comparisons.csv", comparison_table.to_rows()
    )
    write_rows_csv(
        analysis_directory / "failures.csv",
        failures,
        fieldnames=(
            "input_id",
            "method_id",
            "required",
            "failure_layer",
            "failure_reason",
            "valid_cycles",
            "total_cycles",
        ),
    )
    if create_figures:
        _write_figures(
            analysis_directory / "figures", references, tracking_runs
        )
    else:
        (analysis_directory / "figures").mkdir(parents=True, exist_ok=True)

    status = "completed" if required_failure_count == 0 else "failed"
    _write_report(
        analysis_directory / "report.md",
        experiment_spec,
        status,
        failures,
        summaries,
    )
    manifest.update(
        {
            "status": status,
            "failure_count": len(failures),
            "required_failure_count": required_failure_count,
            "outputs": _output_hashes(run_directory),
        }
    )
    write_json(manifest_path, manifest)
    return ExperimentResult(
        experiment_id=experiment_spec.experiment_id,
        run_directory=run_directory,
        spec_hash=spec_hash,
        success=required_failure_count == 0,
        failure_count=len(failures),
        required_failure_count=required_failure_count,
    )


__all__ = [
    "ExperimentInput",
    "ExperimentResult",
    "ExperimentSpec",
    "InputGate",
    "load_tracking_run_artifacts",
    "run_experiment",
    "validate_experiment_spec",
]
