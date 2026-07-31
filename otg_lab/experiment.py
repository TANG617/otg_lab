"""Strict experiment declarations and the CSV-first E-series runner."""

from __future__ import annotations

import csv
import fnmatch
import json
import math
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
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
class ExperimentCase:
    """One executable method/configuration arm in an experiment matrix."""

    case_id: str
    method_id: str
    run_config: RunConfig
    factors: Mapping[str, float] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "case_id",
            _valid_identifier(self.case_id, "case_id"),
        )
        object.__setattr__(self, "method_id", str(self.method_id))
        if not isinstance(self.run_config, RunConfig):
            raise TypeError("case run_config must be RunConfig")
        normalized_factors: dict[str, float] = {}
        for factor_id, value in self.factors.items():
            normalized_id = _valid_identifier(factor_id, "factor_id")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("case factor values must be finite")
            normalized_factors[normalized_id] = numeric
        object.__setattr__(self, "factors", normalized_factors)
        object.__setattr__(self, "description", str(self.description))

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "method_id": self.method_id,
            "run_config": jsonable(self.run_config),
            "factors": dict(self.factors),
            "description": self.description,
        }


@dataclass(frozen=True)
class FactorHeatmapSpec:
    """A declared two-factor metric surface rendered from experiment cases."""

    figure_id: str
    input_id: str
    metric_id: str
    window_id: str
    row_factor: str
    row_levels: tuple[float, ...]
    column_factor: str
    column_levels: tuple[float, ...]
    baseline_case_id: str
    title: str
    subtitle: str
    row_label: str
    column_label: str
    comparison_mode: str = "ratio"
    display_multiplier: float = 1.0
    colorbar_label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "figure_id",
            _valid_identifier(self.figure_id, "figure_id"),
        )
        object.__setattr__(self, "input_id", str(self.input_id))
        object.__setattr__(self, "metric_id", str(self.metric_id))
        object.__setattr__(self, "window_id", str(self.window_id))
        object.__setattr__(
            self,
            "row_factor",
            _valid_identifier(self.row_factor, "row_factor"),
        )
        object.__setattr__(
            self,
            "column_factor",
            _valid_identifier(self.column_factor, "column_factor"),
        )
        object.__setattr__(
            self,
            "baseline_case_id",
            _valid_identifier(self.baseline_case_id, "baseline_case_id"),
        )
        if self.row_factor == self.column_factor:
            raise ValueError("heatmap row and column factors must differ")
        for name in ("row_levels", "column_levels"):
            levels = tuple(float(value) for value in getattr(self, name))
            if not levels or not all(math.isfinite(value) for value in levels):
                raise ValueError(f"{name} must contain finite values")
            if len(set(levels)) != len(levels):
                raise ValueError(f"{name} values must be unique")
            object.__setattr__(self, name, levels)
        for name in ("title", "subtitle", "row_label", "column_label"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"heatmap {name} must not be empty")
        if self.comparison_mode not in {"ratio", "difference"}:
            raise ValueError(
                "heatmap comparison_mode must be 'ratio' or 'difference'"
            )
        multiplier = float(self.display_multiplier)
        if not math.isfinite(multiplier) or multiplier <= 0.0:
            raise ValueError(
                "heatmap display_multiplier must be finite and positive"
            )
        object.__setattr__(self, "display_multiplier", multiplier)
        object.__setattr__(self, "colorbar_label", str(self.colorbar_label))


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
    cases: tuple[ExperimentCase, ...] = ()
    factor_heatmaps: tuple[FactorHeatmapSpec, ...] = ()
    artifact_writer: Callable[..., None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )
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
        if not self.inputs:
            raise ValueError("an experiment needs at least one input")
        if not self.methods:
            raise ValueError("an experiment needs at least one tracking method")
        if not isinstance(self.run_config, RunConfig):
            raise TypeError("run_config must be RunConfig")
        if not isinstance(self.comparison_spec, ComparisonSpec):
            raise TypeError("comparison_spec must be ComparisonSpec")
        if (
            self.comparison_spec.pairs
            and not self.allowed_method_differences
        ):
            raise ValueError(
                "allowed_method_differences must be declared when method "
                "comparisons are configured"
            )
        if not isinstance(self.input_gate, InputGate):
            raise TypeError("input_gate must be InputGate")
        if not all(isinstance(case, ExperimentCase) for case in self.cases):
            raise TypeError("cases must contain ExperimentCase values")
        if not all(
            isinstance(heatmap, FactorHeatmapSpec)
            for heatmap in self.factor_heatmaps
        ):
            raise TypeError(
                "factor_heatmaps must contain FactorHeatmapSpec values"
            )
        if self.artifact_writer is not None and not callable(self.artifact_writer):
            raise TypeError("artifact_writer must be callable or None")
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
        self._validate_cases()
        self._validate_metric_roles()
        self._validate_factor_heatmaps()
        self._validate_comparisons()

    def _validate_cases(self) -> None:
        if not self.cases:
            return
        methods = {method.method_id for method in self.methods}
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case IDs must be unique")
        for case in self.cases:
            if case.method_id not in methods:
                raise ValueError(
                    f"case {case.case_id!r} references unknown method "
                    f"{case.method_id!r}"
                )

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
        cases = {case.case_id: case for case in self.resolved_cases}
        for pair in self.comparison_spec.pairs:
            if pair.baseline_method_id not in cases:
                raise ValueError(
                    f"unknown comparison baseline {pair.baseline_method_id!r}"
                )
            if pair.candidate_method_id not in cases:
                raise ValueError(
                    f"unknown comparison candidate {pair.candidate_method_id!r}"
                )
            baseline_case = cases[pair.baseline_method_id]
            candidate_case = cases[pair.candidate_method_id]
            differences = _case_differences(
                methods[baseline_case.method_id],
                baseline_case,
                methods[candidate_case.method_id],
                candidate_case,
                include_run_config=bool(self.cases),
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

    def _validate_factor_heatmaps(self) -> None:
        figure_ids = [heatmap.figure_id for heatmap in self.factor_heatmaps]
        if len(set(figure_ids)) != len(figure_ids):
            raise ValueError("factor heatmap figure IDs must be unique")
        inputs = {item.input_id for item in self.inputs}
        windows = {window.window_id for window in self.windows}
        cases = {case.case_id: case for case in self.resolved_cases}
        for heatmap in self.factor_heatmaps:
            if not self.cases:
                raise ValueError("factor heatmaps require explicit cases")
            if heatmap.input_id not in inputs:
                raise ValueError(
                    f"heatmap {heatmap.figure_id!r} references unknown input "
                    f"{heatmap.input_id!r}"
                )
            if heatmap.metric_id not in self.metric_ids:
                raise ValueError(
                    f"heatmap metric {heatmap.metric_id!r} is not selected"
                )
            if heatmap.window_id not in windows:
                raise ValueError(
                    f"heatmap window {heatmap.window_id!r} is not declared"
                )
            if heatmap.baseline_case_id not in cases:
                raise ValueError(
                    f"heatmap baseline {heatmap.baseline_case_id!r} is unknown"
                )
            expected = {
                (row_value, column_value)
                for row_value in heatmap.row_levels
                for column_value in heatmap.column_levels
            }
            observed: dict[tuple[float, float], str] = {}
            for case in self.cases:
                if (
                    heatmap.row_factor not in case.factors
                    or heatmap.column_factor not in case.factors
                ):
                    continue
                key = (
                    float(case.factors[heatmap.row_factor]),
                    float(case.factors[heatmap.column_factor]),
                )
                if key in observed:
                    raise ValueError(
                        f"heatmap factor combination {key!r} is duplicated "
                        f"by {observed[key]!r} and {case.case_id!r}"
                    )
                observed[key] = case.case_id
            missing = sorted(expected - set(observed))
            extra = sorted(set(observed) - expected)
            if missing or extra:
                raise ValueError(
                    f"heatmap {heatmap.figure_id!r} does not form the declared "
                    f"full grid; missing={missing}, extra={extra}"
                )
            baseline = cases[heatmap.baseline_case_id]
            baseline_key = (
                baseline.factors.get(heatmap.row_factor),
                baseline.factors.get(heatmap.column_factor),
            )
            if baseline_key not in expected:
                raise ValueError(
                    "heatmap baseline case is outside the declared factor grid"
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

    @property
    def resolved_cases(self) -> tuple[ExperimentCase, ...]:
        if self.cases:
            return self.cases
        return tuple(
            ExperimentCase(
                case_id=method.method_id,
                method_id=method.method_id,
                run_config=self.run_config,
            )
            for method in self.methods
        )

    def method_for_case(self, case: ExperimentCase) -> TrackingMethodSpec:
        methods = {method.method_id: method for method in self.methods}
        method = methods[case.method_id]
        if case.case_id == method.method_id:
            return method
        return replace(
            method,
            method_id=case.case_id,
            description=case.description or method.description,
        )

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
            "cases": [case.as_dict() for case in self.cases],
            "factor_heatmaps": [
                jsonable(heatmap) for heatmap in self.factor_heatmaps
            ],
            "artifact_writer": (
                None
                if self.artifact_writer is None
                else jsonable(self.artifact_writer)
            ),
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


def _case_differences(
    baseline_method: TrackingMethodSpec,
    baseline_case: ExperimentCase,
    candidate_method: TrackingMethodSpec,
    candidate_case: ExperimentCase,
    *,
    include_run_config: bool,
) -> tuple[str, ...]:
    baseline_value = _method_declaration(baseline_method)
    candidate_value = _method_declaration(candidate_method)
    if include_run_config:
        baseline_value["run_config"] = jsonable(baseline_case.run_config)
        candidate_value["run_config"] = jsonable(candidate_case.run_config)
    baseline_flat = _flatten(baseline_value)
    candidate_flat = _flatten(candidate_value)
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
    spec._validate_cases()
    spec._validate_metric_roles()
    spec._validate_factor_heatmaps()
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


def _factor_heatmap_rows(
    spec: ExperimentSpec,
    heatmap: FactorHeatmapSpec,
    trajectory_rows: Sequence[MetricRow],
) -> list[dict[str, Any]]:
    cases = {case.case_id: case for case in spec.resolved_cases}
    case_by_factors = {
        (
            float(case.factors[heatmap.row_factor]),
            float(case.factors[heatmap.column_factor]),
        ): case
        for case in spec.cases
        if heatmap.row_factor in case.factors
        and heatmap.column_factor in case.factors
    }
    metric_index = {
        (row.method_id, row.input_id, row.window_id, row.metric_id): row
        for row in trajectory_rows
    }
    baseline_row = metric_index.get(
        (
            heatmap.baseline_case_id,
            heatmap.input_id,
            heatmap.window_id,
            heatmap.metric_id,
        )
    )
    baseline_value = (
        None
        if baseline_row is None
        or baseline_row.status != AVAILABLE
        or baseline_row.value is None
        else float(baseline_row.value)
    )
    if baseline_value is not None and not math.isfinite(baseline_value):
        baseline_value = None
    if (
        heatmap.comparison_mode == "ratio"
        and baseline_value is not None
        and baseline_value <= 0.0
    ):
        baseline_value = None

    output: list[dict[str, Any]] = []
    for row_value in heatmap.row_levels:
        for column_value in heatmap.column_levels:
            case = case_by_factors[(row_value, column_value)]
            metric_row = metric_index.get(
                (
                    case.case_id,
                    heatmap.input_id,
                    heatmap.window_id,
                    heatmap.metric_id,
                )
            )
            metric_value = (
                None
                if metric_row is None
                or metric_row.status != AVAILABLE
                or metric_row.value is None
                else float(metric_row.value)
            )
            if metric_value is not None and not math.isfinite(metric_value):
                metric_value = None
            if (
                heatmap.comparison_mode == "ratio"
                and metric_value is not None
                and metric_value <= 0.0
            ):
                metric_value = None
            ratio = (
                None
                if heatmap.comparison_mode != "ratio"
                else (
                    None
                    if baseline_value is None or metric_value is None
                    else metric_value / baseline_value
                )
            )
            metric_delta_display = (
                None
                if heatmap.comparison_mode != "difference"
                or baseline_value is None
                or metric_value is None
                else (
                    (metric_value - baseline_value)
                    * heatmap.display_multiplier
                )
            )
            comparison_value = (
                None
                if baseline_value is None
                or metric_value is None
                else (
                    math.log2(ratio)
                    if heatmap.comparison_mode == "ratio"
                    and ratio is not None
                    else metric_delta_display
                )
            )
            status = (
                AVAILABLE
                if comparison_value is not None
                else (
                    "unavailable_baseline"
                    if baseline_value is None
                    else (
                        metric_row.status
                        if metric_row is not None
                        else "unavailable_missing_metric"
                    )
                )
            )
            limits = cases[case.case_id].run_config.limits
            output.append(
                {
                    "case_id": case.case_id,
                    "base_method_id": case.method_id,
                    "input_id": heatmap.input_id,
                    "window_id": heatmap.window_id,
                    "metric_id": heatmap.metric_id,
                    "metric_value": metric_value,
                    "metric_unit": (
                        get_metric_spec(heatmap.metric_id).unit
                    ),
                    "comparison_mode": heatmap.comparison_mode,
                    "display_multiplier": heatmap.display_multiplier,
                    "comparison_value": comparison_value,
                    "metric_delta_display": metric_delta_display,
                    "position_rmse_rad": (
                        metric_value
                        if heatmap.metric_id == "position_rmse"
                        else None
                    ),
                    "lag_s": (
                        metric_value
                        if heatmap.metric_id == "lag_s"
                        else None
                    ),
                    "lag_ms": (
                        None
                        if metric_value is None
                        or heatmap.metric_id != "lag_s"
                        else metric_value * 1000.0
                    ),
                    "baseline_case_id": heatmap.baseline_case_id,
                    "baseline_metric_value": baseline_value,
                    "baseline_rmse_rad": (
                        baseline_value
                        if heatmap.metric_id == "position_rmse"
                        else None
                    ),
                    "baseline_lag_s": (
                        baseline_value
                        if heatmap.metric_id == "lag_s"
                        else None
                    ),
                    "baseline_lag_ms": (
                        None
                        if baseline_value is None
                        or heatmap.metric_id != "lag_s"
                        else baseline_value * 1000.0
                    ),
                    "lag_delta_ms": (
                        None
                        if baseline_value is None
                        or metric_value is None
                        or heatmap.metric_id != "lag_s"
                        else (metric_value - baseline_value) * 1000.0
                    ),
                    "metric_ratio": ratio,
                    "rmse_ratio": (
                        ratio if heatmap.metric_id == "position_rmse" else None
                    ),
                    "log2_metric_ratio": (
                        None if ratio is None else math.log2(ratio)
                    ),
                    "log2_rmse_ratio": (
                        None
                        if ratio is None
                        or heatmap.metric_id != "position_rmse"
                        else math.log2(ratio)
                    ),
                    "max_velocity_rad_s": limits.max_velocity_rad_s,
                    "max_acceleration_rad_s2": (
                        limits.max_acceleration_rad_s2
                    ),
                    "max_jerk_rad_s3": limits.max_jerk_rad_s3,
                    "row_factor": heatmap.row_factor,
                    "row_value": row_value,
                    "column_factor": heatmap.column_factor,
                    "column_value": column_value,
                    "status": status,
                    "sample_count": (
                        None if metric_row is None else metric_row.sample_count
                    ),
                }
            )
    return output


def _write_factor_heatmap(
    figures_dir: Path,
    heatmap: FactorHeatmapSpec,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    from matplotlib.patches import Rectangle

    row_index = {
        value: index for index, value in enumerate(heatmap.row_levels)
    }
    column_index = {
        value: index for index, value in enumerate(heatmap.column_levels)
    }
    transformed = np.full(
        (len(heatmap.row_levels), len(heatmap.column_levels)),
        np.nan,
        dtype=float,
    )
    annotations = np.full_like(transformed, np.nan)
    baseline_location: tuple[int, int] | None = None
    for row in rows:
        row_value = float(row["row_value"])
        column_value = float(row["column_value"])
        y = row_index[row_value]
        x = column_index[column_value]
        comparison_value = row.get("comparison_value")
        annotation_value = (
            row.get("metric_ratio")
            if heatmap.comparison_mode == "ratio"
            else row.get("metric_delta_display")
        )
        if comparison_value is not None and annotation_value is not None:
            transformed[y, x] = float(comparison_value)
            annotations[y, x] = float(annotation_value)
        if row["case_id"] == heatmap.baseline_case_id:
            baseline_location = (y, x)

    finite = transformed[np.isfinite(transformed)]
    extent = max(
        0.5 if heatmap.comparison_mode == "ratio" else 1.0,
        0.0 if not finite.size else float(np.max(np.abs(finite))),
    )
    palette = LinearSegmentedColormap.from_list(
        "blue_neutral_orange_factor_surface",
        ["#8BB9E8", "#F5F5F4", "#E6A15C"],
    )
    palette.set_bad("#EEEDEB")
    figure, axis = plt.subplots(
        figsize=(12.8, 7.2),
        dpi=160,
        constrained_layout=True,
    )
    image = axis.imshow(
        np.ma.masked_invalid(transformed),
        aspect="auto",
        cmap=palette,
        norm=TwoSlopeNorm(vmin=-extent, vcenter=0.0, vmax=extent),
    )
    for y in range(transformed.shape[0]):
        for x in range(transformed.shape[1]):
            if not np.isfinite(annotations[y, x]):
                label = "N/A"
            elif heatmap.comparison_mode == "ratio":
                label = f"×{annotations[y, x]:.2f}"
            elif abs(annotations[y, x]) < 0.5:
                label = "0"
            else:
                label = f"{annotations[y, x]:+.0f}"
            axis.text(
                x,
                y,
                label,
                ha="center",
                va="center",
                fontsize=10,
                color="#252525" if label != "N/A" else "#6B7280",
            )
    if baseline_location is not None:
        baseline_y, baseline_x = baseline_location
        axis.add_patch(
            Rectangle(
                (baseline_x - 0.5, baseline_y - 0.5),
                1.0,
                1.0,
                fill=False,
                edgecolor="#252525",
                linewidth=2.0,
            )
        )

    baseline_row = next(
        row for row in rows if row["case_id"] == heatmap.baseline_case_id
    )
    baseline_row_value = float(baseline_row["row_value"])
    baseline_column_value = float(baseline_row["column_value"])
    axis.set_xticks(
        np.arange(len(heatmap.column_levels)),
        [
            (
                f"{value:g}\n(vendor)"
                if value == baseline_column_value
                else f"{value:g}"
            )
            for value in heatmap.column_levels
        ],
    )
    axis.set_yticks(
        np.arange(len(heatmap.row_levels)),
        [
            (
                f"{value:g}\n(vendor)"
                if value == baseline_row_value
                else f"{value:g}"
            )
            for value in heatmap.row_levels
        ],
    )
    axis.set_xlabel(heatmap.column_label, fontsize=11)
    axis.set_ylabel(heatmap.row_label, fontsize=11)
    axis.tick_params(which="major", length=0, labelsize=10)
    axis.set_xticks(
        np.arange(-0.5, len(heatmap.column_levels), 1.0),
        minor=True,
    )
    axis.set_yticks(
        np.arange(-0.5, len(heatmap.row_levels), 1.0),
        minor=True,
    )
    axis.grid(which="minor", color="#FFFFFF", linewidth=1.2)
    axis.tick_params(which="minor", bottom=False, left=False)
    figure.suptitle(
        f"{heatmap.title}\n{heatmap.subtitle}",
        fontsize=15,
        color="#252525",
    )
    colorbar = figure.colorbar(image, ax=axis, fraction=0.045, pad=0.035)
    colorbar_label = heatmap.colorbar_label
    if not colorbar_label:
        colorbar_label = (
            "log₂ position RMSE ratio"
            if heatmap.metric_id == "position_rmse"
            and heatmap.comparison_mode == "ratio"
            else (
                f"log₂ {heatmap.metric_id} ratio"
                if heatmap.comparison_mode == "ratio"
                else f"{heatmap.metric_id} Δ vs baseline"
            )
        )
    colorbar.set_label(colorbar_label, fontsize=10)
    colorbar.ax.tick_params(labelsize=9)
    figures_dir.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        figures_dir / f"{heatmap.figure_id}.png",
        dpi=200,
        bbox_inches="tight",
        facecolor="white",
    )
    figure.savefig(
        figures_dir / f"{heatmap.figure_id}.svg",
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)


def _write_figures(
    figures_dir: Path,
    references: Mapping[str, Trajectory],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    spec: ExperimentSpec,
    factor_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    figures_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    baseline_cases = {
        heatmap.baseline_case_id for heatmap in spec.factor_heatmaps
    }
    for input_id, reference in references.items():
        position_figure_path = figures_dir / f"{input_id}_position.png"
        if position_figure_path.exists():
            # Experiment-specific artifact writers may provide a more
            # informative position view before the generic figure pass.
            continue
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
            if baseline_cases and method_id not in baseline_cases:
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
        figure.savefig(position_figure_path, dpi=150)
        plt.close(figure)
    for heatmap in spec.factor_heatmaps:
        _write_factor_heatmap(
            figures_dir,
            heatmap,
            factor_rows[heatmap.figure_id],
        )


def _write_report(
    path: Path,
    spec: ExperimentSpec,
    result_status: str,
    failures: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
) -> None:
    primary_window = (
        spec.factor_heatmaps[0].window_id
        if spec.factor_heatmaps
        else "full_overlap"
    )
    primary = [
        row
        for row in summaries
        if row["role"] == "primary"
        and row["window_id"] == primary_window
    ]
    lines = [
        f"# {spec.title}",
        "",
        f"- Experiment: `{spec.experiment_id}`",
        f"- Run status: `{result_status}`",
        f"- Inputs: {len(spec.inputs)}",
        f"- Methods: {len(spec.methods)}",
        f"- Executed cases: {len(spec.resolved_cases)}",
        f"- Recorded failures: {len(failures)}",
        "",
        spec.description
        or "This experiment uses the declared CSV-first tracking pipeline.",
        "",
        "## Declared question",
        "",
        spec.question,
        "",
        "## Declared hypothesis or diagnostic goal",
        "",
        spec.hypothesis,
        "",
        "## Primary metric readout",
        "",
        f"Evaluation window: `{primary_window}`.",
        "",
        "| case / method | metric | available inputs | mean | unit |",
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
        ]
    )
    if spec.factor_heatmaps:
        lines.extend(
            [
                "",
                "Declared factor-surface artifacts:",
                "",
            ]
        )
        for heatmap in spec.factor_heatmaps:
            lines.append(
                f"- `{heatmap.figure_id}.csv` and "
                f"`figures/{heatmap.figure_id}.png/.svg`"
            )
    acceptance_summary = path.parent / "acceptance_summary.md"
    if acceptance_summary.is_file():
        lines.extend(
            [
                "",
                acceptance_summary.read_text(encoding="utf-8").strip(),
            ]
        )
    lines.append("")
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
    "posterior_startup",
    "prediction_startup",
    "prediction_causal",
    "prediction_offline_only",
    "raw_target_startup",
    "raw_target_causal",
}
_TRACE_STRING_FIELDS = {
    "posterior_status",
    "prediction_status",
    "raw_target_status",
    "raw_target_position_source",
    "raw_target_derivative_source",
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
    resolved_cases = experiment_spec.resolved_cases
    methods_by_id = {
        method.method_id: method for method in experiment_spec.methods
    }

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
            for case in resolved_cases:
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=case.case_id,
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
            for case in resolved_cases:
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=case.case_id,
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
            for case in resolved_cases:
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=case.case_id,
                    status="unavailable_input_gate",
                    notes=failure["failure_reason"],
                )
                metric_tables.append(table)
                trajectory_rows.extend(table.rows)
            continue

        for case in resolved_cases:
            base_method = methods_by_id[case.method_id]
            method = experiment_spec.method_for_case(case)
            run_config = case.run_config
            method_directory = (
                run_directory / "methods" / case.case_id / input_id
            )
            method_directory.mkdir(parents=True, exist_ok=True)
            tracking_run = run_tracking(reference, method, run_config)
            tracking_runs[(case.case_id, input_id)] = tracking_run
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
            input_manifest = {
                "fingerprint": tracking_run.status.method_fingerprint,
                "completed": tracking_run.status.completed,
                "valid_cycles": tracking_run.status.valid_cycles,
                "total_cycles": tracking_run.status.total_cycles,
            }
            if experiment_spec.cases:
                case_manifest = manifest["methods"].setdefault(
                    case.case_id,
                    {
                        "base_method_id": case.method_id,
                        "factors": dict(case.factors),
                        "run_config": jsonable(run_config),
                        "inputs": {},
                    },
                )
                case_manifest["inputs"][input_id] = input_manifest
            else:
                manifest["methods"].setdefault(case.case_id, {})[
                    input_id
                ] = input_manifest
            if not tracking_run.status.completed:
                failure = _failure_row(
                    input_id=input_id,
                    method_id=case.case_id,
                    required=base_method.required,
                    layer=tracking_run.status.failure_layer or "tracking",
                    reason=tracking_run.status.failure_reason
                    or "tracking did not complete",
                    valid_cycles=tracking_run.status.valid_cycles,
                    total_cycles=tracking_run.status.total_cycles,
                )
                failures.append(failure)
                if base_method.required:
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
                        limits=run_config.limits,
                    ),
                )
            except Exception as error:
                failure = _failure_row(
                    input_id=input_id,
                    method_id=case.case_id,
                    required=base_method.required,
                    layer="tracking_analysis",
                    reason=f"{type(error).__name__}: {error}",
                    valid_cycles=tracking_run.status.valid_cycles,
                    total_cycles=tracking_run.status.total_cycles,
                )
                failures.append(failure)
                if base_method.required:
                    required_failure_count += 1
                table = _blocked_metrics(
                    experiment_spec,
                    input_id=input_id,
                    method_id=case.case_id,
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
    factor_rows: dict[str, list[dict[str, Any]]] = {}
    for heatmap in experiment_spec.factor_heatmaps:
        rows = _factor_heatmap_rows(
            experiment_spec,
            heatmap,
            trajectory_rows,
        )
        factor_rows[heatmap.figure_id] = rows
        write_rows_csv(
            analysis_directory / f"{heatmap.figure_id}.csv",
            rows,
        )

    if experiment_spec.artifact_writer is not None:
        try:
            experiment_spec.artifact_writer(
                analysis_directory=analysis_directory,
                references=references,
                tracking_runs=tracking_runs,
                trajectory_rows=tuple(trajectory_rows),
                experiment_spec=experiment_spec,
                create_figures=create_figures,
            )
        except Exception as error:
            failures.append(
                _failure_row(
                    input_id="",
                    required=True,
                    layer="artifact_writer",
                    reason=f"{type(error).__name__}: {error}",
                )
            )
            required_failure_count += 1

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
            analysis_directory / "figures",
            references,
            tracking_runs,
            experiment_spec,
            factor_rows,
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
    "ExperimentCase",
    "ExperimentInput",
    "ExperimentResult",
    "ExperimentSpec",
    "FactorHeatmapSpec",
    "InputGate",
    "load_tracking_run_artifacts",
    "run_experiment",
    "validate_experiment_spec",
]
