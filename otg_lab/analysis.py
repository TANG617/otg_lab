"""CSV-first, trajectory-level analysis for :mod:`otg_lab`.

This module is deliberately independent of the online tracking
implementation.  It consumes the public, single-axis data contracts by
attribute (``Trajectory`` and ``TrackingRun``) and produces tidy rows that can
be written directly to CSV.

Three rules are important here:

* tracking errors are evaluated at the raw physical time, never after a lag
  correction;
* derivatives reconstructed offline are labelled ``analysis_estimate`` and
  are never written into, or returned as, online truth; and
* method comparisons require the complete set of paired inputs.  An
  incomplete pair is reported as unavailable rather than silently reduced to
  a complete-case subset.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from itertools import combinations
from types import MappingProxyType
from typing import Any

import numpy as np

AVAILABLE = "available"
ANALYSIS_ESTIMATE = "analysis_estimate"
TRUTH = "truth"
OBSERVED = "observed"
UNAVAILABLE_INCOMPLETE_PAIR = "unavailable_incomplete_pair"
CONSTRAINT_ABSOLUTE_TOLERANCE = 1e-9
CONSTRAINT_RELATIVE_TOLERANCE = 1e-9
STOP_GO_VELOCITY_TOLERANCE_RAD_S = 1e-6
STOP_GO_ACCELERATION_TOLERANCE_RAD_S2 = 1e-4
STOP_GO_POSITION_TOLERANCE_RAD = 1e-10
STOP_GO_PEAK_REFERENCE_FRACTION = 0.25


@dataclass(frozen=True)
class MetricSpec:
    """Versioned definition of one scalar metric."""

    metric_id: str
    family: str
    unit: str
    direction: str
    description: str
    formula: str
    requirements: tuple[str, ...] = ()
    alignment: str = "raw_time"
    missing_policy: str = "emit_unavailable"
    version: str = "otg.metric.v1"

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise ValueError("metric_id must not be empty")
        if self.direction not in {"lower", "higher", "none"}:
            raise ValueError("direction must be 'lower', 'higher', or 'none'")
        if self.missing_policy not in {"emit_unavailable", "omit"}:
            raise ValueError("unsupported missing_policy")


@dataclass(frozen=True)
class MetricRow:
    """One tidy ``input × method × window × metric`` observation."""

    input_id: str
    method_id: str
    window_id: str
    metric_id: str
    value: float | int | bool | None
    unit: str
    direction: str
    role: str = "diagnostic"
    status: str = AVAILABLE
    source_semantics: str = OBSERVED
    sample_count: int | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a csv/json-friendly row with a stable column order."""

        return {
            "input_id": self.input_id,
            "method_id": self.method_id,
            "window_id": self.window_id,
            "metric_id": self.metric_id,
            "value": self.value,
            "unit": self.unit,
            "direction": self.direction,
            "role": self.role,
            "status": self.status,
            "source_semantics": self.source_semantics,
            "sample_count": self.sample_count,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class MetricTable:
    """Immutable collection of tidy metric rows."""

    rows: tuple[MetricRow, ...]

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def to_rows(self) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self.rows]

    def select(
        self,
        *,
        metric_id: str | None = None,
        input_id: str | None = None,
        method_id: str | None = None,
        window_id: str | None = None,
        available_only: bool = False,
    ) -> tuple[MetricRow, ...]:
        """Select rows without introducing a dataframe dependency."""

        return tuple(
            row
            for row in self.rows
            if (metric_id is None or row.metric_id == metric_id)
            and (input_id is None or row.input_id == input_id)
            and (method_id is None or row.method_id == method_id)
            and (window_id is None or row.window_id == window_id)
            and (not available_only or row.status == AVAILABLE)
        )

    def value(
        self,
        metric_id: str,
        *,
        input_id: str | None = None,
        method_id: str | None = None,
        window_id: str | None = None,
    ) -> float | int | bool | None:
        """Return one uniquely identified value."""

        selected = self.select(
            metric_id=metric_id,
            input_id=input_id,
            method_id=method_id,
            window_id=window_id,
        )
        if len(selected) != 1:
            raise KeyError(
                f"expected one row for {metric_id!r}, found {len(selected)}"
            )
        return selected[0].value


@dataclass(frozen=True)
class EvaluationWindow:
    """Inclusive physical-time evaluation window."""

    window_id: str = "full_overlap"
    start_time_s: float | None = None
    end_time_s: float | None = None
    terminal_hold: bool = False
    settle_tolerance_rad: float = 1e-3

    def __post_init__(self) -> None:
        if not self.window_id:
            raise ValueError("window_id must not be empty")
        if self.start_time_s is not None and not math.isfinite(self.start_time_s):
            raise ValueError("start_time_s must be finite")
        if self.end_time_s is not None and not math.isfinite(self.end_time_s):
            raise ValueError("end_time_s must be finite")
        if (
            self.start_time_s is not None
            and self.end_time_s is not None
            and self.end_time_s < self.start_time_s
        ):
            raise ValueError("window end precedes its start")
        if (
            not math.isfinite(self.settle_tolerance_rad)
            or self.settle_tolerance_rad < 0.0
        ):
            raise ValueError("settle_tolerance_rad must be finite and non-negative")


@dataclass(frozen=True)
class AnalysisSpec:
    """Configuration for reference-only analysis."""

    input_id: str = "reference"
    limits: Any | None = None
    jump_threshold_rad: float | None = None
    stop_velocity_tolerance_rad_s: float = 1e-6
    spectral_power_fraction: float = 0.95

    def __post_init__(self) -> None:
        if not self.input_id:
            raise ValueError("input_id must not be empty")
        if self.jump_threshold_rad is not None and (
            not math.isfinite(self.jump_threshold_rad)
            or self.jump_threshold_rad < 0.0
        ):
            raise ValueError("jump_threshold_rad must be finite and non-negative")
        if (
            not math.isfinite(self.stop_velocity_tolerance_rad_s)
            or self.stop_velocity_tolerance_rad_s < 0.0
        ):
            raise ValueError(
                "stop_velocity_tolerance_rad_s must be finite and non-negative"
            )
        if not 0.0 < self.spectral_power_fraction <= 1.0:
            raise ValueError("spectral_power_fraction must be in (0, 1]")


@dataclass(frozen=True)
class MetricSet:
    """Tracking metric selection, roles, and evaluation windows."""

    metric_ids: tuple[str, ...] = ()
    roles: Mapping[str, str] | None = None
    windows: tuple[EvaluationWindow, ...] = (EvaluationWindow(),)
    input_id: str = "reference"
    max_lag_s: float = 1.0
    limits: Any | None = None

    def __post_init__(self) -> None:
        if not self.input_id:
            raise ValueError("input_id must not be empty")
        if not math.isfinite(self.max_lag_s) or self.max_lag_s < 0.0:
            raise ValueError("max_lag_s must be finite and non-negative")
        if not self.windows:
            raise ValueError("at least one evaluation window is required")
        if len({window.window_id for window in self.windows}) != len(self.windows):
            raise ValueError("evaluation window ids must be unique")


@dataclass(frozen=True)
class ReferenceAnalysis:
    """Reference metrics plus an explicitly offline derivative trajectory."""

    input_id: str
    metrics: MetricTable
    derived_trajectory: Any | None
    derivative_semantics: str | None

    @property
    def rows(self) -> tuple[MetricRow, ...]:
        return self.metrics.rows

    @property
    def metric_table(self) -> MetricTable:
        return self.metrics


@dataclass(frozen=True)
class MethodPair:
    baseline_method_id: str
    candidate_method_id: str
    comparison_id: str = ""

    def __post_init__(self) -> None:
        if not self.baseline_method_id or not self.candidate_method_id:
            raise ValueError("comparison methods must not be empty")
        if self.baseline_method_id == self.candidate_method_id:
            raise ValueError("a method cannot be compared with itself")

    @property
    def resolved_id(self) -> str:
        return self.comparison_id or (
            f"{self.candidate_method_id}_vs_{self.baseline_method_id}"
        )


@dataclass(frozen=True)
class ComparisonSpec:
    """Paired, trajectory-level method comparison configuration."""

    pairs: tuple[MethodPair, ...] = ()
    metric_ids: tuple[str, ...] = ()
    input_ids: tuple[str, ...] = ()
    window_ids: tuple[str, ...] = ()
    bootstrap_seed: int | None = None
    bootstrap_repetitions: int = 0
    confidence_level: float = 0.95

    def __post_init__(self) -> None:
        if self.bootstrap_repetitions < 0:
            raise ValueError("bootstrap_repetitions must be non-negative")
        if self.bootstrap_repetitions and self.bootstrap_seed is None:
            raise ValueError("paired bootstrap requires an explicit seed")
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError("confidence_level must be in (0, 1)")


@dataclass(frozen=True)
class ComparisonRow:
    comparison_id: str
    baseline_method_id: str
    candidate_method_id: str
    window_id: str
    metric_id: str
    status: str
    paired_input_count: int
    expected_input_count: int
    baseline_mean: float | None
    candidate_mean: float | None
    difference: float | None
    relative_difference: float | None
    improvement: float | None
    ci_lower: float | None
    ci_upper: float | None
    unit: str
    direction: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComparisonTable:
    rows: tuple[ComparisonRow, ...]

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def to_rows(self) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self.rows]


@dataclass(frozen=True)
class _DerivedTrajectory:
    """Fallback used only when a duck-typed input class cannot be reconstructed."""

    sample_index: np.ndarray
    time_s: np.ndarray
    position_rad: np.ndarray
    velocity_rad_s: np.ndarray
    acceleration_rad_s2: np.ndarray
    jerk_rad_s3: np.ndarray

    @property
    def sample_count(self) -> int:
        return int(self.time_s.size)

    @property
    def dt(self) -> float:
        return float(np.diff(self.time_s)[0])

    @property
    def duration_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0])


_REGISTRY: dict[str, MetricSpec] = {}


def register_metric(spec: MetricSpec, *, replace: bool = False) -> MetricSpec:
    """Register a metric definition, rejecting accidental semantic drift."""

    existing = _REGISTRY.get(spec.metric_id)
    if existing is not None and not replace:
        if existing != spec:
            raise ValueError(f"metric {spec.metric_id!r} is already registered")
        return existing
    _REGISTRY[spec.metric_id] = spec
    return spec


def get_metric_spec(metric_id: str) -> MetricSpec:
    try:
        return _REGISTRY[metric_id]
    except KeyError as exc:
        raise KeyError(f"unknown metric_id {metric_id!r}") from exc


def metric_registry() -> Mapping[str, MetricSpec]:
    """Return a read-only view of the versioned registry."""

    return MappingProxyType(_REGISTRY)


def _register(
    metric_id: str,
    family: str,
    unit: str,
    direction: str,
    description: str,
    formula: str,
    requirements: tuple[str, ...] = (),
    *,
    alignment: str = "raw_time",
) -> None:
    register_metric(
        MetricSpec(
            metric_id=metric_id,
            family=family,
            unit=unit,
            direction=direction,
            description=description,
            formula=formula,
            requirements=requirements,
            alignment=alignment,
        )
    )


def _populate_registry() -> None:
    structural = (
        ("sample_count", "samples", "Number of reference samples", "N"),
        ("duration_s", "s", "Reference duration", "t[-1] - t[0]"),
        ("sample_interval_s", "s", "Median sample interval", "median(diff(t))"),
        (
            "time_step_max_abs_deviation_s",
            "s",
            "Maximum deviation from the median time step",
            "max(abs(diff(t) - dt))",
        ),
    )
    for metric_id, unit, description, formula in structural:
        _register(metric_id, "reference_structure", unit, "none", description, formula)

    position = (
        ("position_min", "rad", "Minimum reference position", "min(p)"),
        ("position_max", "rad", "Maximum reference position", "max(p)"),
        ("position_span", "rad", "Reference position span", "max(p) - min(p)"),
        ("position_rms", "rad", "Reference position RMS", "sqrt(mean(p^2))"),
        (
            "position_path_length",
            "rad",
            "Total sampled reference path length",
            "sum(abs(diff(p)))",
        ),
        (
            "position_increment_p95_abs",
            "rad",
            "95th percentile absolute position increment",
            "quantile(abs(diff(p)), .95)",
        ),
        (
            "position_increment_max_abs",
            "rad",
            "Maximum absolute position increment",
            "max(abs(diff(p)))",
        ),
        (
            "position_jump_count",
            "count",
            "Increments above the declared jump threshold",
            "count(abs(diff(p)) > jump_threshold)",
        ),
        ("reversal_count", "count", "Direction reversal count", "sign changes"),
        (
            "stop_sample_fraction",
            "fraction",
            "Fraction of samples at or below the stop velocity tolerance",
            "mean(abs(v) <= tolerance)",
        ),
        (
            "dwell_duration_s",
            "s",
            "Total sampled duration classified as stopped",
            "sum(stopped intervals * dt)",
        ),
        (
            "dominant_frequency_hz",
            "Hz",
            "Largest non-DC position spectral component",
            "argmax(|rfft(p - mean(p))|^2)",
        ),
        (
            "bandwidth_95_hz",
            "Hz",
            "Frequency containing 95 percent of non-DC spectral power",
            "cumulative spectral power quantile",
        ),
    )
    for metric_id, unit, description, formula in position:
        _register(metric_id, "reference_position", unit, "none", description, formula)

    channel_units = {
        "velocity": "rad/s",
        "acceleration": "rad/s^2",
        "jerk": "rad/s^3",
    }
    for channel, unit in channel_units.items():
        for suffix, description, formula in (
            ("max_abs", "maximum absolute value", "max(abs(x))"),
            ("p95_abs", "95th percentile absolute value", "quantile(abs(x), .95)"),
            ("rms", "root mean square", "sqrt(mean(x^2))"),
        ):
            _register(
                f"reference_{channel}_{suffix}",
                "reference_dynamics",
                unit,
                "none",
                f"Reference {channel} {description}",
                formula,
                (channel,),
            )
        _register(
            f"reference_{channel}_limit_utilization",
            "reference_dynamics",
            "ratio",
            "none",
            f"Reference {channel} peak divided by its motion limit",
            "max(abs(x)) / limit",
            (channel, f"{channel}_limit"),
        )
        _register(
            f"reference_{channel}_limit_margin",
            "reference_dynamics",
            unit,
            "none",
            f"Reference {channel} limit margin",
            "limit - max(abs(x))",
            (channel, f"{channel}_limit"),
        )
        _register(
            f"reference_{channel}_violation_count",
            "reference_dynamics",
            "count",
            "lower",
            f"Reference {channel} samples outside the motion limit",
            "count(abs(x) > limit)",
            (channel, f"{channel}_limit"),
        )

    for metric_id, description, requirements in (
        (
            "position_velocity_consistency_rmse",
            "RMSE between derivative of position and declared velocity",
            ("velocity",),
        ),
        (
            "velocity_acceleration_consistency_rmse",
            "RMSE between derivative of velocity and declared acceleration",
            ("velocity", "acceleration"),
        ),
        (
            "acceleration_jerk_consistency_rmse",
            "RMSE between derivative of acceleration and declared jerk",
            ("acceleration", "jerk"),
        ),
    ):
        unit = {
            "position_velocity_consistency_rmse": "rad/s",
            "velocity_acceleration_consistency_rmse": "rad/s^2",
            "acceleration_jerk_consistency_rmse": "rad/s^3",
        }[metric_id]
        _register(
            metric_id,
            "reference_consistency",
            unit,
            "lower",
            description,
            "sqrt(mean((finite_difference(x) - declared)^2))",
            requirements,
        )

    tracking = (
        ("position_rmse", "rad", "lower", "sqrt(mean((command-reference)^2))"),
        ("position_mae", "rad", "lower", "mean(abs(command-reference))"),
        ("position_bias", "rad", "none", "mean(command-reference)"),
        (
            "position_p95_abs_error",
            "rad",
            "lower",
            "quantile(abs(command-reference), .95)",
        ),
        (
            "position_max_abs_error",
            "rad",
            "lower",
            "max(abs(command-reference))",
        ),
        (
            "position_iae",
            "rad*s",
            "lower",
            "trapezoid(abs(command-reference), time)",
        ),
        ("tracking_sample_count", "samples", "none", "number of aligned samples"),
        ("lag_samples", "samples", "none", "best diagnostic integer lag"),
        ("lag_s", "s", "none", "best diagnostic physical lag"),
        (
            "lag_aligned_rmse",
            "rad",
            "lower",
            "minimum RMSE after diagnostic integer shifts",
        ),
        ("settled", "boolean", "higher", "terminal suffix remains within tolerance"),
        (
            "settle_time_s",
            "s",
            "lower",
            "time from window start to first permanently settled sample",
        ),
    )
    for metric_id, unit, direction, formula in tracking:
        alignment = "lag_diagnostic" if metric_id.startswith("lag") else "raw_time"
        _register(
            metric_id,
            "tracking",
            unit,
            direction,
            metric_id.replace("_", " "),
            formula,
            ("position",),
            alignment=alignment,
        )

    stop_go = (
        (
            "rest_to_rest_pulse_fraction",
            "fraction",
            "none",
            "Fraction of moving-reference cycles that start and end stopped "
            "while the exact profile moves",
            "count(rest-to-rest pulse cycles) / count(moving-reference cycles)",
        ),
        (
            "stop_go_event_rate_hz",
            "Hz",
            "none",
            "Rest-to-rest pulse events per second of eligible profile time",
            "count(rest-to-rest pulse cycles) / sum(eligible cycle durations)",
        ),
        (
            "endpoint_stop_fraction",
            "fraction",
            "none",
            "Fraction of moving-reference cycles stopped at both endpoints",
            "count(stopped start and end states) / count(moving-reference cycles)",
        ),
        (
            "longest_rest_to_rest_pulse_run_cycles",
            "cycles",
            "none",
            "Longest consecutive run of rest-to-rest pulse cycles",
            "max run length of rest-to-rest pulse indicators",
        ),
        (
            "profile_peak_velocity_to_reference_median",
            "ratio",
            "none",
            "Median exact within-cycle peak speed divided by reference speed",
            "median(profile peak abs velocity / reference abs velocity)",
        ),
        (
            "profile_velocity_ripple_median",
            "rad/s",
            "none",
            "Median exact within-cycle signed velocity range",
            "median(profile max velocity - profile min velocity)",
        ),
        (
            "profile_velocity_ripple_to_reference_median",
            "ratio",
            "none",
            "Median exact within-cycle velocity ripple divided by reference speed",
            "median((profile max velocity - profile min velocity) / "
            "reference abs velocity)",
        ),
        (
            "profile_velocity_ripple_to_reference_p95",
            "ratio",
            "none",
            "P95 exact within-cycle velocity ripple divided by reference speed",
            "quantile((profile max velocity - profile min velocity) / "
            "reference abs velocity, .95)",
        ),
        (
            "one_cycle_reachability_pulse_agreement",
            "fraction",
            "higher",
            "Agreement between requested free duration at most one cycle and "
            "the observed rest-to-rest pulse classification",
            "mean((requested free duration <= cycle duration) == pulse)",
        ),
    )
    for metric_id, unit, direction, description, formula in stop_go:
        _register(
            metric_id,
            "stop_go",
            unit,
            direction,
            description,
            formula,
            (
                "reference_velocity_truth",
                "trace_endpoint_state",
                "exact_profiles",
            ),
        )

    for channel, unit in channel_units.items():
        for suffix, direction, formula in (
            ("max_abs", "lower", "max(abs(x))"),
            ("p95_abs", "lower", "quantile(abs(x), .95)"),
            ("rms", "lower", "sqrt(mean(x^2))"),
        ):
            _register(
                f"output_{channel}_{suffix}",
                "dynamics",
                unit,
                direction,
                f"Command {channel} {suffix.replace('_', ' ')}",
                formula,
                (f"command_{channel}",),
            )
        _register(
            f"output_{channel}_limit_utilization",
            "dynamics",
            "ratio",
            "lower",
            f"Command {channel} peak divided by its motion limit",
            "max(abs(x)) / limit",
            (f"command_{channel}", f"{channel}_limit"),
        )
        _register(
            f"output_{channel}_limit_margin",
            "dynamics",
            unit,
            "higher",
            f"Command {channel} limit margin",
            "limit - max(abs(x))",
            (f"command_{channel}", f"{channel}_limit"),
        )
        _register(
            f"output_{channel}_violation_count",
            "dynamics",
            "count",
            "lower",
            f"Command {channel} samples outside its limit",
            "count(abs(x) > limit)",
            (f"command_{channel}", f"{channel}_limit"),
        )

    _register(
        "acceleration_total_variation",
        "dynamics",
        "rad/s^2",
        "lower",
        "Total variation of sampled command acceleration",
        "sum(abs(diff(acceleration)))",
        ("command_acceleration",),
    )
    _register(
        "sampled_jerk_estimate_rms",
        "dynamics",
        "rad/s^3",
        "lower",
        "Offline finite-difference estimate of command jerk RMS",
        "sqrt(mean(finite_difference(acceleration)^2))",
        ("command_acceleration",),
    )
    _register(
        "sampled_jerk_estimate_max_abs",
        "dynamics",
        "rad/s^3",
        "lower",
        "Offline finite-difference estimate of command peak jerk",
        "max(abs(finite_difference(acceleration)))",
        ("command_acceleration",),
    )

    for prefix in ("posterior", "prediction"):
        for channel, unit in channel_units.items():
            _register(
                f"{prefix}_{channel}_rmse",
                "pipeline",
                unit,
                "lower",
                f"{prefix.title()} {channel} error at represented physical time",
                "sqrt(mean((estimate-interpolated_truth)^2))",
                (f"{prefix}_{channel}", f"truth_{channel}"),
            )
        _register(
            f"{prefix}_position_rmse",
            "pipeline",
            "rad",
            "lower",
            f"{prefix.title()} position error at represented physical time",
            "sqrt(mean((estimate-interpolated_truth)^2))",
            (f"{prefix}_position",),
        )

    for channel, unit in {
        "velocity": "rad/s",
        "acceleration": "rad/s^2",
    }.items():
        _register(
            f"raw_target_{channel}_rmse",
            "pipeline",
            unit,
            "lower",
            f"Raw target {channel} error at represented physical time",
            "sqrt(mean((target-interpolated_truth)^2))",
            (f"raw_target_{channel}", f"truth_{channel}"),
        )
    for suffix, formula in (
        ("mean", "mean(command_index - represented_target_index)"),
        ("max", "max(command_index - represented_target_index)"),
    ):
        _register(
            f"raw_target_age_samples_{suffix}",
            "pipeline",
            "samples",
            "none",
            f"Raw target age in command samples ({suffix})",
            formula,
            ("raw_target_age_samples",),
        )

    for channel, unit in {
        "position": "rad",
        "velocity": "rad/s",
        "acceleration": "rad/s^2",
    }.items():
        _register(
            f"target_{channel}_distortion_rmse",
            "pipeline",
            unit,
            "lower",
            f"RMSE between executable and raw target {channel}",
            "sqrt(mean((executable-raw)^2))",
            (f"raw_target_{channel}", f"executable_target_{channel}"),
        )

    for metric_id, unit, direction, description, formula in (
        (
            "fallback_count",
            "count",
            "lower",
            "Committed fallback cycle count",
            "count(fallback_applied)",
        ),
        (
            "fallback_rate",
            "fraction",
            "lower",
            "Committed fallback cycle fraction",
            "mean(fallback_applied)",
        ),
        (
            "solver_failure_count",
            "count",
            "lower",
            "Non-success solver status count",
            "count(not solver_success)",
        ),
        (
            "reset_count",
            "count",
            "lower",
            "Component reset event count",
            "count(reset)",
        ),
        (
            "deadline_miss_count",
            "count",
            "lower",
            "Cycles whose total runtime exceeds dt",
            "count(runtime_total > dt)",
        ),
        (
            "deadline_miss_rate",
            "fraction",
            "lower",
            "Fraction of cycles whose total runtime exceeds dt",
            "mean(runtime_total > dt)",
        ),
    ):
        _register(
            metric_id,
            "pipeline",
            unit,
            direction,
            description,
            formula,
            ("trace",),
        )

    for component in (
        "estimator",
        "predictor",
        "target_builder",
        "governor",
        "follower",
        "total",
    ):
        for quantile in ("p50", "p95", "p99", "max"):
            _register(
                f"runtime_{component}_{quantile}_s",
                "runtime",
                "s",
                "lower",
                f"{component.replace('_', ' ').title()} runtime {quantile}",
                (
                    f"quantile(runtime_{component}, {quantile[1:]}%)"
                    if quantile != "max"
                    else f"max(runtime_{component})"
                ),
                ("trace_runtime",),
            )

    for metric_id, unit, direction, description, formula, requirements in (
        (
            "profile_exact_fraction",
            "fraction",
            "higher",
            "Fraction of command profiles declared exact",
            "mean(profile_exact)",
            ("profiles",),
        ),
        (
            "profile_jerk_max_abs",
            "rad/s^3",
            "lower",
            "Maximum absolute jerk across exact profile segments",
            "max(abs(segment_jerk))",
            ("exact_profiles",),
        ),
        (
            "profile_jerk_violation_count",
            "count",
            "lower",
            "Exact profile segments outside the jerk limit",
            "count(abs(segment_jerk) > jerk_limit)",
            ("exact_profiles", "jerk_limit"),
        ),
        (
            "profile_acceleration_max_abs",
            "rad/s^2",
            "lower",
            "Continuous maximum absolute acceleration across exact profiles",
            "analytic segment extrema",
            ("exact_profiles", "profile_initial_state"),
        ),
        (
            "profile_acceleration_violation_count",
            "count",
            "lower",
            "Exact profiles whose continuous acceleration exceeds its limit",
            "count(profile max > acceleration limit)",
            ("exact_profiles", "profile_initial_state", "acceleration_limit"),
        ),
        (
            "profile_velocity_max_abs",
            "rad/s",
            "lower",
            "Continuous maximum absolute velocity across exact profiles",
            "analytic segment extrema",
            ("exact_profiles", "profile_initial_state"),
        ),
        (
            "profile_velocity_violation_count",
            "count",
            "lower",
            "Exact profiles whose continuous velocity exceeds its limit",
            "count(profile max > velocity limit)",
            ("exact_profiles", "profile_initial_state", "velocity_limit"),
        ),
        (
            "profile_constraint_violation_count",
            "count",
            "lower",
            "Total exact continuous profile limit violations",
            "velocity + acceleration + jerk violations",
            ("exact_profiles", "limits"),
        ),
    ):
        _register(
            metric_id,
            "continuous_constraints",
            unit,
            direction,
            description,
            formula,
            requirements,
        )


_populate_registry()
METRIC_REGISTRY = MappingProxyType(_REGISTRY)


DEFAULT_TRACKING_METRIC_IDS = tuple(
    metric_id
    for metric_id, spec in _REGISTRY.items()
    if spec.family
    in {
        "tracking",
        "dynamics",
        "pipeline",
        "runtime",
        "continuous_constraints",
        "stop_go",
    }
)


def _coerce_analysis_spec(value: AnalysisSpec | Mapping[str, Any] | None) -> AnalysisSpec:
    if value is None:
        return AnalysisSpec()
    if isinstance(value, AnalysisSpec):
        return value
    if isinstance(value, Mapping):
        return AnalysisSpec(**dict(value))
    raise TypeError("analysis_spec must be AnalysisSpec, a mapping, or None")


def _coerce_window(value: EvaluationWindow | Mapping[str, Any]) -> EvaluationWindow:
    if isinstance(value, EvaluationWindow):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        if "id" in data and "window_id" not in data:
            data["window_id"] = data.pop("id")
        return EvaluationWindow(**data)
    raise TypeError("window must be EvaluationWindow or a mapping")


def _coerce_metric_set(
    value: MetricSet | Mapping[str, Any] | Sequence[str] | None,
) -> MetricSet:
    if value is None:
        return MetricSet(metric_ids=DEFAULT_TRACKING_METRIC_IDS)
    if isinstance(value, MetricSet):
        result = value
    elif isinstance(value, Mapping):
        data = dict(value)
        if "metrics" in data and "metric_ids" not in data:
            data["metric_ids"] = data.pop("metrics")
        if "metric_ids" in data:
            data["metric_ids"] = tuple(data["metric_ids"])
        if "windows" in data:
            data["windows"] = tuple(_coerce_window(item) for item in data["windows"])
        result = MetricSet(**data)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result = MetricSet(metric_ids=tuple(str(item) for item in value))
    else:
        raise TypeError("metric_set must be MetricSet, a mapping, a sequence, or None")
    metric_ids = result.metric_ids or DEFAULT_TRACKING_METRIC_IDS
    unknown = sorted(set(metric_ids) - set(_REGISTRY))
    if unknown:
        raise KeyError(f"unknown metric ids: {unknown}")
    if not result.metric_ids:
        result = MetricSet(
            metric_ids=DEFAULT_TRACKING_METRIC_IDS,
            roles=result.roles,
            windows=result.windows,
            input_id=result.input_id,
            max_lag_s=result.max_lag_s,
            limits=result.limits,
        )
    return result


def _coerce_pair(value: MethodPair | Mapping[str, Any] | Sequence[str]) -> MethodPair:
    if isinstance(value, MethodPair):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        if "baseline" in data and "baseline_method_id" not in data:
            data["baseline_method_id"] = data.pop("baseline")
        if "candidate" in data and "candidate_method_id" not in data:
            data["candidate_method_id"] = data.pop("candidate")
        if "id" in data and "comparison_id" not in data:
            data["comparison_id"] = data.pop("id")
        return MethodPair(**data)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 2:
            raise ValueError("method pair sequence must contain baseline and candidate")
        return MethodPair(str(value[0]), str(value[1]))
    raise TypeError("invalid method comparison pair")


def _coerce_comparison_spec(
    value: ComparisonSpec | Mapping[str, Any] | None,
) -> ComparisonSpec:
    if value is None:
        return ComparisonSpec()
    if isinstance(value, ComparisonSpec):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        raw_pairs = data.pop("comparisons", data.get("pairs", ()))
        data["pairs"] = tuple(_coerce_pair(item) for item in raw_pairs)
        for key in ("metric_ids", "input_ids", "window_ids"):
            if key in data:
                data[key] = tuple(data[key])
        if "bootstrap_repetitions" not in data and "bootstrap_samples" in data:
            data["bootstrap_repetitions"] = data.pop("bootstrap_samples")
        return ComparisonSpec(**data)
    raise TypeError("comparison_spec must be ComparisonSpec, a mapping, or None")


def _finite_vector(
    owner: Any,
    name: str,
    *,
    optional: bool = False,
    allow_empty: bool = False,
) -> np.ndarray | None:
    value = getattr(owner, name, None)
    if value is None:
        if optional:
            return None
        raise ValueError(f"{name} is required")
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 1 or (array.size == 0 and not allow_empty):
        qualifier = "" if allow_empty else "non-empty "
        raise ValueError(f"{name} must be a {qualifier}one-dimensional array")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains NaN or infinity")
    return array


def _trajectory_arrays(
    trajectory: Any, *, minimum_samples: int = 2
) -> dict[str, np.ndarray | None]:
    if minimum_samples < 0:
        raise ValueError("minimum_samples must be non-negative")
    allow_empty = minimum_samples == 0
    time = _finite_vector(trajectory, "time_s", allow_empty=allow_empty)
    position = _finite_vector(
        trajectory, "position_rad", allow_empty=allow_empty
    )
    assert time is not None and position is not None
    if position.size != time.size:
        raise ValueError("position_rad and time_s lengths differ")
    if time.size < minimum_samples:
        raise ValueError(
            f"trajectory must contain at least {minimum_samples} samples"
        )
    intervals = np.diff(time)
    if intervals.size:
        if np.any(intervals <= 0.0):
            raise ValueError("time_s must be strictly increasing")
        dt = float(np.median(intervals))
        tolerance = max(1e-12, abs(dt) * 1e-9)
        if not np.allclose(intervals, dt, rtol=1e-9, atol=tolerance):
            raise ValueError("analysis requires a fixed sampling grid")

    sample_index_value = getattr(trajectory, "sample_index", None)
    if sample_index_value is None:
        sample_index = np.arange(time.size, dtype=np.int64)
    else:
        sample_index = np.asarray(sample_index_value)
        if (
            sample_index.ndim != 1
            or sample_index.size != time.size
            or not np.issubdtype(sample_index.dtype, np.integer)
        ):
            raise ValueError("sample_index must be an integer vector matching time_s")
        if np.any(np.diff(sample_index.astype(np.int64)) != 1):
            raise ValueError("sample_index must be consecutive")
        sample_index = sample_index.astype(np.int64, copy=False)

    result: dict[str, np.ndarray | None] = {
        "sample_index": sample_index,
        "time": time,
        "position": position,
    }
    for key, attribute in (
        ("velocity", "velocity_rad_s"),
        ("acceleration", "acceleration_rad_s2"),
        ("jerk", "jerk_rad_s3"),
    ):
        channel = _finite_vector(
            trajectory,
            attribute,
            optional=True,
            allow_empty=allow_empty,
        )
        if channel is not None and channel.size != time.size:
            raise ValueError(f"{attribute} and time_s lengths differ")
        result[key] = channel
    return result


def _second_order_derivative(values: np.ndarray, dt: float) -> np.ndarray:
    """Second-order central differences with second-order one-sided edges."""

    if values.size >= 3:
        return np.asarray(np.gradient(values, dt, edge_order=2), dtype=np.float64)
    # A two-point trajectory cannot support a second-order stencil. Preserve
    # analyzability while making no higher-order accuracy claim in row notes.
    slope = (float(values[1]) - float(values[0])) / dt
    return np.full(values.shape, slope, dtype=np.float64)


def _derived_channels(position: np.ndarray, dt: float) -> tuple[np.ndarray, ...]:
    velocity = _second_order_derivative(position, dt)
    acceleration = _second_order_derivative(velocity, dt)
    jerk = _second_order_derivative(acceleration, dt)
    return velocity, acceleration, jerk


def _construct_derived_trajectory(
    trajectory: Any,
    arrays: Mapping[str, np.ndarray | None],
    velocity: np.ndarray,
    acceleration: np.ndarray,
    jerk: np.ndarray,
) -> Any:
    kwargs = {
        "sample_index": np.array(arrays["sample_index"], dtype=np.int64, copy=True),
        "time_s": np.array(arrays["time"], dtype=np.float64, copy=True),
        "position_rad": np.array(arrays["position"], dtype=np.float64, copy=True),
        "velocity_rad_s": np.array(velocity, dtype=np.float64, copy=True),
        "acceleration_rad_s2": np.array(acceleration, dtype=np.float64, copy=True),
        "jerk_rad_s3": np.array(jerk, dtype=np.float64, copy=True),
    }
    try:
        return trajectory.__class__(**kwargs)
    except (TypeError, ValueError):
        return _DerivedTrajectory(**kwargs)


def _limit(limits: Any | None, channel: str) -> float | None:
    if limits is None:
        return None
    aliases = {
        "velocity": (
            "max_velocity_rad_s",
            "max_velocity",
            "velocity_limit_rad_s",
        ),
        "acceleration": (
            "max_acceleration_rad_s2",
            "max_acceleration",
            "acceleration_limit_rad_s2",
        ),
        "jerk": ("max_jerk_rad_s3", "max_jerk", "jerk_limit_rad_s3"),
    }[channel]
    value: Any = None
    if isinstance(limits, Mapping):
        for name in aliases:
            if name in limits:
                value = limits[name]
                break
    else:
        for name in aliases:
            if hasattr(limits, name):
                value = getattr(limits, name)
                break
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{channel} limit must be finite and positive")
    return result


def _limit_tolerance(limit: float) -> float:
    """Numerical tolerance used by every sampled and continuous limit audit."""

    return max(
        CONSTRAINT_ABSOLUTE_TOLERANCE,
        abs(float(limit)) * CONSTRAINT_RELATIVE_TOLERANCE,
    )


def _metric_row(
    metric_id: str,
    value: float | int | bool | None,
    *,
    input_id: str,
    method_id: str = "",
    window_id: str = "full",
    role: str = "diagnostic",
    status: str = AVAILABLE,
    source_semantics: str = OBSERVED,
    sample_count: int | None = None,
    notes: str = "",
) -> MetricRow:
    spec = get_metric_spec(metric_id)
    if value is not None and isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            raise ValueError(f"metric {metric_id} is not finite")
        value = float(value)
    elif isinstance(value, np.integer):
        value = int(value)
    elif isinstance(value, np.bool_):
        value = bool(value)
    return MetricRow(
        input_id=input_id,
        method_id=method_id,
        window_id=window_id,
        metric_id=metric_id,
        value=value,
        unit=spec.unit,
        direction=spec.direction,
        role=role,
        status=status,
        source_semantics=source_semantics,
        sample_count=sample_count,
        notes=notes,
    )


def _abs_summary(values: np.ndarray) -> dict[str, float]:
    absolute = np.abs(values)
    return {
        "max_abs": float(np.max(absolute)),
        "p95_abs": float(np.quantile(absolute, 0.95, method="linear")),
        "rms": float(np.sqrt(np.mean(np.square(values)))),
    }


def _nonzero_sign_changes(values: np.ndarray) -> int:
    signs = np.sign(values)
    signs = signs[signs != 0.0]
    if signs.size < 2:
        return 0
    return int(np.count_nonzero(signs[1:] != signs[:-1]))


def _spectral_metrics(
    position: np.ndarray, dt: float, power_fraction: float
) -> tuple[float, float]:
    centered = position - float(np.mean(position))
    frequencies = np.fft.rfftfreq(centered.size, dt)
    power = np.square(np.abs(np.fft.rfft(centered)))
    if power.size:
        power[0] = 0.0
    total = float(np.sum(power))
    if total <= np.finfo(np.float64).tiny:
        return 0.0, 0.0
    dominant = float(frequencies[int(np.argmax(power))])
    index = int(np.searchsorted(np.cumsum(power), power_fraction * total, side="left"))
    index = min(index, frequencies.size - 1)
    return dominant, float(frequencies[index])


def analyze_reference(
    trajectory: Any,
    analysis_spec: AnalysisSpec | Mapping[str, Any] | None = None,
) -> ReferenceAnalysis:
    """Analyze a canonical reference trajectory without mutating it.

    A complete offline derivative trajectory is returned for analysis and
    plotting.  If any derivative channel was absent from the input, metrics
    derived from the reconstructed channel are explicitly labelled
    ``analysis_estimate``.
    """

    spec = _coerce_analysis_spec(analysis_spec)
    arrays = _trajectory_arrays(trajectory)
    time = arrays["time"]
    position = arrays["position"]
    assert time is not None and position is not None
    dt = float(np.median(np.diff(time)))
    derived_velocity, derived_acceleration, derived_jerk = _derived_channels(
        position, dt
    )
    derived = _construct_derived_trajectory(
        trajectory,
        arrays,
        derived_velocity,
        derived_acceleration,
        derived_jerk,
    )
    rows: list[MetricRow] = []

    def add(
        metric_id: str,
        value: float | int | bool,
        *,
        semantics: str = TRUTH,
        notes: str = "",
        count: int | None = None,
    ) -> None:
        rows.append(
            _metric_row(
                metric_id,
                value,
                input_id=spec.input_id,
                source_semantics=semantics,
                sample_count=position.size if count is None else count,
                notes=notes,
            )
        )

    intervals = np.diff(time)
    add("sample_count", int(position.size))
    add("duration_s", float(time[-1] - time[0]))
    add("sample_interval_s", dt)
    add(
        "time_step_max_abs_deviation_s",
        float(np.max(np.abs(intervals - dt))),
    )
    add("position_min", float(np.min(position)))
    add("position_max", float(np.max(position)))
    add("position_span", float(np.max(position) - np.min(position)))
    add("position_rms", float(np.sqrt(np.mean(np.square(position)))))
    increments = np.diff(position)
    absolute_increments = np.abs(increments)
    add("position_path_length", float(np.sum(absolute_increments)))
    add(
        "position_increment_p95_abs",
        float(np.quantile(absolute_increments, 0.95, method="linear")),
        count=increments.size,
    )
    add(
        "position_increment_max_abs",
        float(np.max(absolute_increments)),
        count=increments.size,
    )
    if spec.jump_threshold_rad is None:
        median_increment = float(np.median(absolute_increments))
        jump_threshold = max(
            10.0 * median_increment,
            np.finfo(np.float64).eps
            * max(1.0, float(np.max(np.abs(position)))),
        )
        jump_note = f"auto threshold={jump_threshold:.17g} rad"
    else:
        jump_threshold = spec.jump_threshold_rad
        jump_note = f"declared threshold={jump_threshold:.17g} rad"
    add(
        "position_jump_count",
        int(np.count_nonzero(absolute_increments > jump_threshold)),
        notes=jump_note,
        count=increments.size,
    )
    add("reversal_count", _nonzero_sign_changes(increments), count=increments.size)

    available_channels = {
        "velocity": (
            arrays["velocity"]
            if arrays["velocity"] is not None
            else derived_velocity
        ),
        "acceleration": (
            arrays["acceleration"]
            if arrays["acceleration"] is not None
            else derived_acceleration
        ),
        "jerk": arrays["jerk"] if arrays["jerk"] is not None else derived_jerk,
    }
    channel_semantics = {
        channel: TRUTH if arrays[channel] is not None else ANALYSIS_ESTIMATE
        for channel in available_channels
    }
    velocity = available_channels["velocity"]
    assert velocity is not None
    stopped = np.abs(velocity) <= spec.stop_velocity_tolerance_rad_s
    add(
        "stop_sample_fraction",
        float(np.mean(stopped)),
        semantics=channel_semantics["velocity"],
        notes=(
            "offline derivative; not online truth"
            if channel_semantics["velocity"] == ANALYSIS_ESTIMATE
            else ""
        ),
    )
    stopped_intervals = stopped[:-1] & stopped[1:]
    add(
        "dwell_duration_s",
        float(np.sum(intervals[stopped_intervals])),
        semantics=channel_semantics["velocity"],
        count=stopped_intervals.size,
    )
    dominant, bandwidth = _spectral_metrics(
        position, dt, spec.spectral_power_fraction
    )
    add("dominant_frequency_hz", dominant)
    add(
        "bandwidth_95_hz",
        bandwidth,
        notes=f"power_fraction={spec.spectral_power_fraction:.17g}",
    )

    for channel, values in available_channels.items():
        assert values is not None
        semantics = channel_semantics[channel]
        note = (
            "second-order offline finite difference; not online truth"
            if semantics == ANALYSIS_ESTIMATE
            else ""
        )
        summary = _abs_summary(values)
        for suffix, value in summary.items():
            add(
                f"reference_{channel}_{suffix}",
                value,
                semantics=semantics,
                notes=note,
            )
        limit = _limit(spec.limits, channel)
        if limit is not None:
            peak = summary["max_abs"]
            add(
                f"reference_{channel}_limit_utilization",
                peak / limit,
                semantics=semantics,
                notes=note,
            )
            add(
                f"reference_{channel}_limit_margin",
                limit - peak,
                semantics=semantics,
                notes=note,
            )
            add(
                f"reference_{channel}_violation_count",
                int(
                    np.count_nonzero(
                        np.abs(values) > limit + _limit_tolerance(limit)
                    )
                ),
                semantics=semantics,
                notes=note,
            )

    if arrays["velocity"] is not None:
        velocity_truth = arrays["velocity"]
        assert velocity_truth is not None
        add(
            "position_velocity_consistency_rmse",
            float(
                np.sqrt(np.mean(np.square(derived_velocity - velocity_truth)))
            ),
            notes="declared truth checked against second-order finite difference",
        )
    if arrays["velocity"] is not None and arrays["acceleration"] is not None:
        velocity_truth = arrays["velocity"]
        acceleration_truth = arrays["acceleration"]
        assert velocity_truth is not None and acceleration_truth is not None
        derivative = _second_order_derivative(velocity_truth, dt)
        add(
            "velocity_acceleration_consistency_rmse",
            float(np.sqrt(np.mean(np.square(derivative - acceleration_truth)))),
            notes="declared truth checked against second-order finite difference",
        )
    if arrays["acceleration"] is not None and arrays["jerk"] is not None:
        acceleration_truth = arrays["acceleration"]
        jerk_truth = arrays["jerk"]
        assert acceleration_truth is not None and jerk_truth is not None
        derivative = _second_order_derivative(acceleration_truth, dt)
        add(
            "acceleration_jerk_consistency_rmse",
            float(np.sqrt(np.mean(np.square(derivative - jerk_truth)))),
            notes="declared truth checked against second-order finite difference",
        )

    derivative_semantics = (
        None
        if all(arrays[channel] is not None for channel in ("velocity", "acceleration", "jerk"))
        else ANALYSIS_ESTIMATE
    )
    return ReferenceAnalysis(
        input_id=spec.input_id,
        metrics=MetricTable(tuple(rows)),
        derived_trajectory=derived,
        derivative_semantics=derivative_semantics,
    )


def _object_row(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    try:
        return vars(value)
    except TypeError as exc:
        raise TypeError("trace/profile rows must be mappings or objects") from exc


def _first_present(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row and row[name] is not None and row[name] != "":
            return row[name]
    return None


def _as_finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "applied"}:
            return True
        if normalized in {"0", "false", "no", "none", "not_applied"}:
            return False
        return None
    return bool(value)


def _align_command(
    reference: Mapping[str, np.ndarray | None],
    command: Mapping[str, np.ndarray | None],
) -> tuple[np.ndarray, np.ndarray]:
    ref_index = reference["sample_index"]
    command_index = command["sample_index"]
    ref_time = reference["time"]
    command_time = command["time"]
    assert (
        ref_index is not None
        and command_index is not None
        and ref_time is not None
        and command_time is not None
    )
    lookup = {int(sample): offset for offset, sample in enumerate(ref_index)}
    reference_offsets: list[int] = []
    command_offsets: list[int] = []
    for offset, sample in enumerate(command_index):
        reference_offset = lookup.get(int(sample))
        if reference_offset is None:
            continue
        tolerance = max(1e-12, abs(float(ref_time[reference_offset])) * 1e-10)
        if not math.isclose(
            float(command_time[offset]),
            float(ref_time[reference_offset]),
            rel_tol=1e-10,
            abs_tol=tolerance,
        ):
            raise ValueError(
                f"sample_index {int(sample)} has different command/reference times"
            )
        reference_offsets.append(reference_offset)
        command_offsets.append(offset)
    if not reference_offsets:
        raise ValueError("command and reference have no overlapping samples")
    return (
        np.asarray(reference_offsets, dtype=np.int64),
        np.asarray(command_offsets, dtype=np.int64),
    )


def _window_mask(times: np.ndarray, window: EvaluationWindow) -> np.ndarray:
    mask = np.ones(times.size, dtype=bool)
    tolerance = max(1e-12, abs(float(times[-1] - times[0])) * 1e-12)
    if window.start_time_s is not None:
        mask &= times >= window.start_time_s - tolerance
    if window.end_time_s is not None:
        mask &= times <= window.end_time_s + tolerance
    if not np.any(mask):
        raise ValueError(f"evaluation window {window.window_id!r} has no samples")
    return mask


def _lag_metrics(
    reference: np.ndarray,
    command: np.ndarray,
    times: np.ndarray,
    max_lag_s: float,
) -> dict[str, float | int]:
    if times.size == 1:
        return {
            "lag_samples": 0,
            "lag_s": 0.0,
            "lag_aligned_rmse": float(
                np.sqrt(np.mean(np.square(command - reference)))
            ),
        }
    dt = float(np.median(np.diff(times)))
    max_samples = min(
        times.size - 1, int(math.floor(max_lag_s / dt + 1e-12))
    )
    minimum_overlap = max(2, int(math.ceil(times.size * 0.5)))
    candidates: list[tuple[float, int, float]] = []
    for lag in range(-max_samples, max_samples + 1):
        if lag > 0:
            ref_part = reference[:-lag]
            command_part = command[lag:]
            lag_s = float(np.median(times[lag:] - times[:-lag]))
        elif lag < 0:
            ref_part = reference[-lag:]
            command_part = command[:lag]
            lag_s = float(np.median(times[:lag] - times[-lag:]))
        else:
            ref_part = reference
            command_part = command
            lag_s = 0.0
        if ref_part.size < minimum_overlap:
            continue
        rmse = float(np.sqrt(np.mean(np.square(command_part - ref_part))))
        candidates.append((rmse, lag, lag_s))
    if not candidates:
        raw = float(np.sqrt(np.mean(np.square(command - reference))))
        return {"lag_samples": 0, "lag_s": 0.0, "lag_aligned_rmse": raw}
    rmse, lag, lag_s = min(
        candidates, key=lambda item: (item[0], abs(item[1]), item[1])
    )
    return {"lag_samples": int(lag), "lag_s": lag_s, "lag_aligned_rmse": rmse}


def _runtime_value(row: Mapping[str, Any], component: str) -> float | None:
    aliases = (
        f"runtime_{component}_s",
        f"{component}_runtime_s",
        f"runtime_{component}",
        f"{component}_runtime",
    )
    value = _as_finite_float(_first_present(row, aliases))
    if value is not None:
        return value
    for suffix, scale in (("_ms", 1e-3), ("_us", 1e-6), ("_ns", 1e-9)):
        unit_aliases = (
            f"runtime_{component}{suffix}",
            f"{component}_runtime{suffix}",
        )
        value = _as_finite_float(_first_present(row, unit_aliases))
        if value is not None:
            return value * scale
    return None


def _trace_runtime(rows: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {}
    components = ("estimator", "predictor", "target_builder", "governor", "follower")
    per_component: dict[str, list[float | None]] = {
        component: [_runtime_value(row, component) for row in rows]
        for component in components
    }
    total_values = [_runtime_value(row, "total") for row in rows]
    if not any(value is not None for value in total_values):
        total_values = []
        for index in range(len(rows)):
            present = [
                per_component[component][index]
                for component in components
                if per_component[component][index] is not None
            ]
            total_values.append(sum(present) if present else None)
    per_component["total"] = total_values
    for component, values in per_component.items():
        if values and all(value is not None and value >= 0.0 for value in values):
            result[component] = np.asarray(values, dtype=np.float64)
    return result


def _boolean_count(
    rows: Sequence[Mapping[str, Any]], aliases: Sequence[str]
) -> tuple[int, int]:
    values: list[bool] = []
    for row in rows:
        raw = _first_present(row, aliases)
        value = _as_bool(raw)
        if value is None:
            continue
        values.append(value)
    return sum(values), len(values)


_SOLVER_FAILURE_MARKERS = (
    "error",
    "fail",
    "infeasible",
    "invalid",
    "unavailable",
    "maximum_iterations",
    "non_convex",
    "unsolved",
)


def _solver_failure_count(rows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    failed: list[bool] = []
    for row in rows:
        raw = _first_present(
            row,
            (
                "solver_status",
                "governor_status",
                "follower_status",
            ),
        )
        if raw is not None:
            status = str(raw).strip().lower()
            cycle_status = str(row.get("status", "")).strip().lower()
            failed.append(
                cycle_status == "failed"
                or any(marker in status for marker in _SOLVER_FAILURE_MARKERS)
            )
    return sum(failed), len(failed)


def _trace_series(
    rows: Sequence[Mapping[str, Any]], aliases: Sequence[str]
) -> np.ndarray | None:
    values: list[float] = []
    for row in rows:
        value = _as_finite_float(_first_present(row, aliases))
        if value is None:
            return None
        values.append(value)
    return np.asarray(values, dtype=np.float64) if values else None


def _truth_at_times(
    reference: Mapping[str, np.ndarray | None],
    channel: str,
    query_times: np.ndarray,
) -> np.ndarray | None:
    values = reference[channel]
    times = reference["time"]
    if values is None or times is None:
        return None
    tolerance = max(1e-12, float(np.median(np.diff(times))) * 1e-9)
    if query_times[0] < times[0] - tolerance or query_times[-1] > times[-1] + tolerance:
        return None
    return np.interp(query_times, times, values)


def _pipeline_metrics(
    reference: Mapping[str, np.ndarray | None],
    trace_rows: Sequence[Mapping[str, Any]],
    window: EvaluationWindow,
) -> dict[str, tuple[float, str, int]]:
    result: dict[str, tuple[float, str, int]] = {}
    if not trace_rows:
        return result
    channel_attributes = {
        "position": "position_rad",
        "velocity": "velocity_rad_s",
        "acceleration": "acceleration_rad_s2",
    }

    def selected_rows(prefix: str) -> list[Mapping[str, Any]]:
        if prefix == "posterior":
            time_aliases = (
                "posterior_state_time_s",
                "posterior_time_s",
                "represented_time_s",
            )
        elif prefix == "prediction":
            time_aliases = (
                "prediction_state_time_s",
                "prediction_time_s",
            )
        else:
            time_aliases = ("raw_target_time_s",)
        output: list[Mapping[str, Any]] = []
        for row in trace_rows:
            state_time = _as_finite_float(_first_present(row, time_aliases))
            if state_time is None:
                continue
            startup = _as_bool(
                _first_present(row, (f"{prefix}_startup",))
            )
            if startup is None:
                status = str(
                    _first_present(row, (f"{prefix}_status",)) or ""
                ).lower()
                startup = status.startswith("startup")
            if startup:
                continue
            if (
                window.start_time_s is not None
                and state_time < window.start_time_s - 1e-12
            ):
                continue
            if (
                window.end_time_s is not None
                and state_time > window.end_time_s + 1e-12
            ):
                continue
            output.append(row)
        return output

    for prefix in ("posterior", "prediction"):
        rows = selected_rows(prefix)
        if not rows:
            continue
        time = _trace_series(
            rows,
            (
                f"{prefix}_state_time_s",
                f"{prefix}_time_s",
                "represented_time_s" if prefix == "posterior" else "prediction_time_s",
            ),
        )
        if time is None:
            continue
        for channel, suffix in channel_attributes.items():
            estimate = _trace_series(
                rows,
                (
                    f"{prefix}_{suffix}",
                    f"{prefix}_{channel}",
                ),
            )
            truth = _truth_at_times(reference, channel, time)
            if estimate is None or truth is None:
                continue
            rmse = float(np.sqrt(np.mean(np.square(estimate - truth))))
            result[f"{prefix}_{channel}_rmse"] = (rmse, TRUTH, estimate.size)

    raw_rows = selected_rows("raw_target")
    raw_time = _trace_series(raw_rows, ("raw_target_time_s",))
    if raw_time is not None:
        for channel in ("velocity", "acceleration"):
            suffix = channel_attributes[channel]
            estimate = _trace_series(
                raw_rows,
                (f"raw_target_{suffix}", f"raw_target_{channel}"),
            )
            truth = _truth_at_times(reference, channel, raw_time)
            if estimate is None or truth is None:
                continue
            rmse = float(np.sqrt(np.mean(np.square(estimate - truth))))
            result[f"raw_target_{channel}_rmse"] = (
                rmse,
                TRUTH,
                estimate.size,
            )
    target_age = _trace_series(raw_rows, ("raw_target_age_samples",))
    if target_age is not None:
        result["raw_target_age_samples_mean"] = (
            float(np.mean(target_age)),
            OBSERVED,
            target_age.size,
        )
        result["raw_target_age_samples_max"] = (
            float(np.max(target_age)),
            OBSERVED,
            target_age.size,
        )

    distortion_rows = raw_rows
    if (
        not distortion_rows
        and window.window_id == "full_overlap"
        and window.start_time_s is None
        and window.end_time_s is None
    ):
        # Older trace schemas did not persist raw_target_time_s. Preserve
        # their whole-run target-distortion contract while requiring explicit
        # represented time for truth-error/windowed metrics.
        distortion_rows = list(trace_rows)
    for channel, suffix in channel_attributes.items():
        raw = _trace_series(
            distortion_rows,
            (f"raw_target_{suffix}", f"raw_target_{channel}"),
        )
        executable = _trace_series(
            distortion_rows,
            (
                f"executable_target_{suffix}",
                f"executable_target_{channel}",
                f"governed_target_{suffix}",
            ),
        )
        if raw is not None and executable is not None:
            value = float(np.sqrt(np.mean(np.square(executable - raw))))
            result[f"target_{channel}_distortion_rmse"] = (
                value,
                OBSERVED,
                raw.size,
            )
    return result


def _profile_row_cycle(row: Mapping[str, Any]) -> int | str:
    raw = _first_present(row, ("cycle_index", "k", "profile_id"))
    if raw is None:
        return ""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return str(raw)


def _profile_initial_state(
    profile_row: Mapping[str, Any],
    trace_by_cycle: Mapping[int | str, Mapping[str, Any]],
) -> tuple[float, float] | None:
    velocity = _as_finite_float(
        _first_present(
            profile_row,
            (
                "initial_velocity_rad_s",
                "start_velocity_rad_s",
                "velocity_start_rad_s",
            ),
        )
    )
    acceleration = _as_finite_float(
        _first_present(
            profile_row,
            (
                "initial_acceleration_rad_s2",
                "start_acceleration_rad_s2",
                "acceleration_start_rad_s2",
            ),
        )
    )
    if velocity is not None and acceleration is not None:
        return velocity, acceleration
    trace = trace_by_cycle.get(_profile_row_cycle(profile_row))
    if trace is None:
        return None
    velocity = _as_finite_float(
        _first_present(
            trace,
            (
                "command_start_velocity_rad_s",
                "initial_command_velocity_rad_s",
                "previous_command_velocity_rad_s",
            ),
        )
    )
    acceleration = _as_finite_float(
        _first_present(
            trace,
            (
                "command_start_acceleration_rad_s2",
                "initial_command_acceleration_rad_s2",
                "previous_command_acceleration_rad_s2",
            ),
        )
    )
    if velocity is None or acceleration is None:
        return None
    return velocity, acceleration


def _continuous_profile_metrics(
    profile_rows: Sequence[Mapping[str, Any]],
    trace_rows: Sequence[Mapping[str, Any]],
    limits: Any | None,
) -> dict[str, tuple[float | int, str, int]]:
    result: dict[str, tuple[float | int, str, int]] = {}
    if not profile_rows:
        return result
    exact_by_profile: dict[int | str, list[bool]] = {}
    for row in profile_rows:
        exact = _as_bool(_first_present(row, ("exact", "profile_exact")))
        if exact is None:
            return result
        exact_by_profile.setdefault(_profile_row_cycle(row), []).append(exact)
    exact_flags = [all(flags) for flags in exact_by_profile.values()]
    result["profile_exact_fraction"] = (
        float(np.mean(exact_flags)),
        OBSERVED,
        len(exact_flags),
    )
    if not all(exact_flags):
        return result
    jerks: list[float] = []
    for row in profile_rows:
        value = _as_finite_float(
            _first_present(row, ("jerk_rad_s3", "segment_jerk_rad_s3", "jerk"))
        )
        if value is None:
            return result
        jerks.append(value)
    jerk_array = np.asarray(jerks, dtype=np.float64)
    result["profile_jerk_max_abs"] = (
        float(np.max(np.abs(jerk_array))),
        OBSERVED,
        jerk_array.size,
    )
    jerk_limit = _limit(limits, "jerk")
    jerk_violations: int | None = None
    if jerk_limit is not None:
        jerk_violations = int(
            np.count_nonzero(
                np.abs(jerk_array)
                > jerk_limit + _limit_tolerance(jerk_limit)
            )
        )
        result["profile_jerk_violation_count"] = (
            jerk_violations,
            OBSERVED,
            jerk_array.size,
        )

    trace_by_cycle = {
        _profile_row_cycle(row): row
        for row in trace_rows
        if _profile_row_cycle(row) != ""
    }
    grouped: dict[int | str, list[Mapping[str, Any]]] = {}
    for row in profile_rows:
        grouped.setdefault(_profile_row_cycle(row), []).append(row)
    maximum_velocity = 0.0
    maximum_acceleration = 0.0
    velocity_violations = 0
    acceleration_violations = 0
    reconstructable = True
    velocity_limit = _limit(limits, "velocity")
    acceleration_limit = _limit(limits, "acceleration")
    for cycle_rows in grouped.values():
        cycle_rows.sort(
            key=lambda row: (
                _as_finite_float(
                    _first_present(row, ("segment_index", "start_time_s"))
                )
                or 0.0
            )
        )
        initial = _profile_initial_state(cycle_rows[0], trace_by_cycle)
        if initial is None:
            reconstructable = False
            break
        velocity, acceleration = initial
        cycle_max_velocity = abs(velocity)
        cycle_max_acceleration = abs(acceleration)
        for row in cycle_rows:
            start = _as_finite_float(
                _first_present(row, ("start_time_s", "segment_start_time_s"))
            )
            end = _as_finite_float(
                _first_present(row, ("end_time_s", "segment_end_time_s"))
            )
            jerk = _as_finite_float(
                _first_present(row, ("jerk_rad_s3", "segment_jerk_rad_s3", "jerk"))
            )
            if start is None or end is None or jerk is None or end <= start:
                reconstructable = False
                break
            duration = end - start
            endpoint_velocity = (
                velocity + acceleration * duration + 0.5 * jerk * duration**2
            )
            endpoint_acceleration = acceleration + jerk * duration
            velocity_candidates = [abs(velocity), abs(endpoint_velocity)]
            if jerk != 0.0:
                turning_time = -acceleration / jerk
                if 0.0 < turning_time < duration:
                    velocity_candidates.append(
                        abs(
                            velocity
                            + acceleration * turning_time
                            + 0.5 * jerk * turning_time**2
                        )
                    )
            cycle_max_velocity = max(cycle_max_velocity, *velocity_candidates)
            cycle_max_acceleration = max(
                cycle_max_acceleration, abs(acceleration), abs(endpoint_acceleration)
            )
            velocity = endpoint_velocity
            acceleration = endpoint_acceleration
        if not reconstructable:
            break
        maximum_velocity = max(maximum_velocity, cycle_max_velocity)
        maximum_acceleration = max(maximum_acceleration, cycle_max_acceleration)
        if (
            velocity_limit is not None
            and cycle_max_velocity
            > velocity_limit + _limit_tolerance(velocity_limit)
        ):
            velocity_violations += 1
        if (
            acceleration_limit is not None
            and cycle_max_acceleration
            > acceleration_limit + _limit_tolerance(acceleration_limit)
        ):
            acceleration_violations += 1
    if reconstructable:
        result["profile_velocity_max_abs"] = (
            maximum_velocity,
            OBSERVED,
            len(grouped),
        )
        result["profile_acceleration_max_abs"] = (
            maximum_acceleration,
            OBSERVED,
            len(grouped),
        )
        if velocity_limit is not None:
            result["profile_velocity_violation_count"] = (
                velocity_violations,
                OBSERVED,
                len(grouped),
            )
        if acceleration_limit is not None:
            result["profile_acceleration_violation_count"] = (
                acceleration_violations,
                OBSERVED,
                len(grouped),
            )
        if (
            jerk_violations is not None
            and velocity_limit is not None
            and acceleration_limit is not None
        ):
            result["profile_constraint_violation_count"] = (
                jerk_violations + velocity_violations + acceleration_violations,
                OBSERVED,
                len(grouped),
            )
    return result


def _exact_cycle_velocity_stats(
    cycle_rows: Sequence[Mapping[str, Any]],
    initial_velocity: float,
    initial_acceleration: float,
) -> tuple[float, float, float] | None:
    """Return exact peak speed, signed velocity range, and cycle duration."""

    ordered = sorted(
        cycle_rows,
        key=lambda row: (
            _as_finite_float(
                _first_present(row, ("segment_index", "start_time_s"))
            )
            or 0.0
        ),
    )
    velocity = float(initial_velocity)
    acceleration = float(initial_acceleration)
    minimum_velocity = velocity
    maximum_velocity = velocity
    total_duration = 0.0
    previous_end: float | None = None
    for row in ordered:
        exact = _as_bool(_first_present(row, ("exact", "profile_exact")))
        start = _as_finite_float(
            _first_present(row, ("start_time_s", "segment_start_time_s"))
        )
        end = _as_finite_float(
            _first_present(row, ("end_time_s", "segment_end_time_s"))
        )
        jerk = _as_finite_float(
            _first_present(row, ("jerk_rad_s3", "segment_jerk_rad_s3", "jerk"))
        )
        if (
            exact is not True
            or start is None
            or end is None
            or jerk is None
            or end <= start
        ):
            return None
        if previous_end is not None and not math.isclose(
            start,
            previous_end,
            rel_tol=0.0,
            abs_tol=max(1e-12, abs(previous_end) * 1e-10),
        ):
            return None
        duration = end - start
        endpoint_velocity = (
            velocity + acceleration * duration + 0.5 * jerk * duration**2
        )
        endpoint_acceleration = acceleration + jerk * duration
        candidates = [velocity, endpoint_velocity]
        if jerk != 0.0:
            turning_time = -acceleration / jerk
            if 0.0 < turning_time < duration:
                candidates.append(
                    velocity
                    + acceleration * turning_time
                    + 0.5 * jerk * turning_time**2
                )
        minimum_velocity = min(minimum_velocity, *candidates)
        maximum_velocity = max(maximum_velocity, *candidates)
        velocity = endpoint_velocity
        acceleration = endpoint_acceleration
        total_duration += duration
        previous_end = end
    peak_velocity = max(abs(minimum_velocity), abs(maximum_velocity))
    return peak_velocity, maximum_velocity - minimum_velocity, total_duration


def _longest_true_run(values: Sequence[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def _stop_go_metrics(
    reference: Mapping[str, np.ndarray | None],
    trace_rows: Sequence[Mapping[str, Any]],
    profile_rows: Sequence[Mapping[str, Any]],
    window: EvaluationWindow,
) -> dict[str, tuple[float | int, str, int]]:
    """Classify exact within-cycle motion hidden by endpoint sampling."""

    reference_time = reference["time"]
    reference_velocity = reference["velocity"]
    if (
        reference_time is None
        or reference_velocity is None
        or not trace_rows
        or not profile_rows
    ):
        return {}

    grouped_profiles: dict[int | str, list[Mapping[str, Any]]] = {}
    for row in profile_rows:
        cycle = _profile_row_cycle(row)
        if cycle != "":
            grouped_profiles.setdefault(cycle, []).append(row)

    selected: list[tuple[float, Mapping[str, Any]]] = []
    for row in trace_rows:
        command_time = _as_finite_float(
            _first_present(row, ("command_time_s", "time_s"))
        )
        if command_time is None:
            return {}
        if (
            window.start_time_s is not None
            and command_time < window.start_time_s - 1e-12
        ):
            continue
        if (
            window.end_time_s is not None
            and command_time > window.end_time_s + 1e-12
        ):
            continue
        selected.append((command_time, row))
    if not selected:
        return {}
    selected.sort(key=lambda item: item[0])

    query_times = np.asarray([item[0] for item in selected], dtype=np.float64)
    tolerance = max(
        1e-12,
        (
            float(np.median(np.diff(reference_time)))
            if reference_time.size >= 2
            else 0.0
        )
        * 1e-9,
    )
    if (
        query_times[0] < reference_time[0] - tolerance
        or query_times[-1] > reference_time[-1] + tolerance
    ):
        return {}
    truth_velocity = np.interp(query_times, reference_time, reference_velocity)

    pulse_flags: list[bool] = []
    endpoint_stop_flags: list[bool] = []
    peak_ratios: list[float] = []
    velocity_ripples: list[float] = []
    normalized_velocity_ripples: list[float] = []
    reachability_flags: list[bool] = []
    eligible_durations: list[float] = []
    reachability_complete = True
    for (_, row), velocity_truth in zip(selected, truth_velocity):
        if abs(float(velocity_truth)) <= STOP_GO_VELOCITY_TOLERANCE_RAD_S:
            continue
        cycle = _profile_row_cycle(row)
        cycle_rows = grouped_profiles.get(cycle)
        start_velocity = _as_finite_float(
            _first_present(row, ("command_start_velocity_rad_s",))
        )
        start_acceleration = _as_finite_float(
            _first_present(row, ("command_start_acceleration_rad_s2",))
        )
        end_velocity = _as_finite_float(
            _first_present(row, ("command_velocity_rad_s",))
        )
        end_acceleration = _as_finite_float(
            _first_present(row, ("command_acceleration_rad_s2",))
        )
        start_position = _as_finite_float(
            _first_present(row, ("command_start_position_rad",))
        )
        end_position = _as_finite_float(
            _first_present(row, ("command_position_rad",))
        )
        if (
            cycle_rows is None
            or start_velocity is None
            or start_acceleration is None
            or end_velocity is None
            or end_acceleration is None
            or start_position is None
            or end_position is None
        ):
            return {}
        reconstructed = _exact_cycle_velocity_stats(
            cycle_rows,
            start_velocity,
            start_acceleration,
        )
        # A binding can occasionally expose only a sampled prefix for a single
        # boundary cycle. Primary stop/go metrics remain exact by excluding
        # that cycle rather than discarding every exact cycle in the window.
        if reconstructed is None:
            continue
        peak_velocity, velocity_ripple, cycle_duration = reconstructed
        endpoint_stop = bool(
            abs(start_velocity) <= STOP_GO_VELOCITY_TOLERANCE_RAD_S
            and abs(end_velocity) <= STOP_GO_VELOCITY_TOLERANCE_RAD_S
            and abs(start_acceleration)
            <= STOP_GO_ACCELERATION_TOLERANCE_RAD_S2
            and abs(end_acceleration) <= STOP_GO_ACCELERATION_TOLERANCE_RAD_S2
        )
        pulse = bool(
            endpoint_stop
            and abs(end_position - start_position)
            > STOP_GO_POSITION_TOLERANCE_RAD
            and peak_velocity
            >= STOP_GO_PEAK_REFERENCE_FRACTION * abs(float(velocity_truth))
        )
        endpoint_stop_flags.append(endpoint_stop)
        pulse_flags.append(pulse)
        peak_ratios.append(peak_velocity / abs(float(velocity_truth)))
        velocity_ripples.append(velocity_ripple)
        normalized_velocity_ripples.append(
            velocity_ripple / abs(float(velocity_truth))
        )
        eligible_durations.append(cycle_duration)

        requested_duration = _as_finite_float(
            _first_present(row, ("requested_target_free_duration_s",))
        )
        if requested_duration is None:
            reachability_complete = False
        else:
            reachable = requested_duration <= cycle_duration + max(
                1e-12, cycle_duration * 1e-9
            )
            reachability_flags.append(reachable == pulse)

    eligible_count = len(pulse_flags)
    if not eligible_count:
        return {}
    pulse_count = int(np.count_nonzero(pulse_flags))
    total_duration = float(np.sum(eligible_durations))
    result: dict[str, tuple[float | int, str, int]] = {
        "rest_to_rest_pulse_fraction": (
            pulse_count / eligible_count,
            OBSERVED,
            eligible_count,
        ),
        "stop_go_event_rate_hz": (
            0.0 if total_duration <= 0.0 else pulse_count / total_duration,
            OBSERVED,
            eligible_count,
        ),
        "endpoint_stop_fraction": (
            float(np.mean(endpoint_stop_flags)),
            OBSERVED,
            eligible_count,
        ),
        "longest_rest_to_rest_pulse_run_cycles": (
            _longest_true_run(pulse_flags),
            OBSERVED,
            eligible_count,
        ),
        "profile_peak_velocity_to_reference_median": (
            float(np.median(peak_ratios)),
            OBSERVED,
            eligible_count,
        ),
        "profile_velocity_ripple_median": (
            float(np.median(velocity_ripples)),
            OBSERVED,
            eligible_count,
        ),
        "profile_velocity_ripple_to_reference_median": (
            float(np.median(normalized_velocity_ripples)),
            OBSERVED,
            eligible_count,
        ),
        "profile_velocity_ripple_to_reference_p95": (
            float(np.quantile(normalized_velocity_ripples, 0.95)),
            OBSERVED,
            eligible_count,
        ),
    }
    if reachability_complete and len(reachability_flags) == eligible_count:
        result["one_cycle_reachability_pulse_agreement"] = (
            float(np.mean(reachability_flags)),
            OBSERVED,
            eligible_count,
        )
    return result


def _run_method_id(tracking_run: Any) -> str:
    if isinstance(tracking_run, Mapping):
        value = tracking_run.get("method_id", "")
    else:
        value = getattr(tracking_run, "method_id", "")
    if not value:
        raise ValueError("tracking_run.method_id must not be empty")
    return str(value)


def _run_value(tracking_run: Any, name: str, default: Any = None) -> Any:
    if isinstance(tracking_run, Mapping):
        return tracking_run.get(name, default)
    return getattr(tracking_run, name, default)


def _run_is_complete(tracking_run: Any) -> bool:
    status = _run_value(tracking_run, "status")
    if status is None:
        return True
    if isinstance(status, Mapping):
        complete = status.get("complete", status.get("completed"))
        state = status.get("status", status.get("state"))
    else:
        complete = getattr(status, "complete", getattr(status, "completed", None))
        state = getattr(status, "status", getattr(status, "state", None))
    if complete is not None:
        return bool(complete)
    if state is None:
        return True
    return str(state).strip().lower() in {"complete", "completed", "success", "ok"}


def analyze_tracking(
    reference: Any,
    tracking_run: Any,
    metric_set: MetricSet | Mapping[str, Any] | Sequence[str] | None = None,
) -> MetricTable:
    """Analyze one method output against a reference on their shared raw grid."""

    selection = _coerce_metric_set(metric_set)
    method_id = _run_method_id(tracking_run)

    def unavailable_command_table(reason: str) -> MetricTable:
        roles = dict(selection.roles or {})
        unavailable: list[MetricRow] = []
        for metric_id in selection.metric_ids:
            metric = get_metric_spec(metric_id)
            windows = (
                selection.windows
                if metric.family in {"tracking", "dynamics", "stop_go"}
                else (EvaluationWindow(),)
            )
            for window in windows:
                unavailable.append(
                    _metric_row(
                        metric_id,
                        None,
                        input_id=selection.input_id,
                        method_id=method_id,
                        window_id=window.window_id,
                        role=roles.get(metric_id, "diagnostic"),
                        status=reason,
                        notes="tracking run produced no command samples",
                    )
                )
        return MetricTable(tuple(unavailable))

    command_object = _run_value(tracking_run, "command")
    if command_object is None:
        return unavailable_command_table("unavailable_missing_command")
    reference_arrays = _trajectory_arrays(reference)
    command_arrays = _trajectory_arrays(command_object, minimum_samples=0)
    command_time = command_arrays["time"]
    assert command_time is not None
    if command_time.size == 0:
        return unavailable_command_table("unavailable_empty_command")
    reference_offsets, command_offsets = _align_command(
        reference_arrays, command_arrays
    )
    aligned_time = reference_arrays["time"][reference_offsets]
    reference_position = reference_arrays["position"][reference_offsets]
    command_position = command_arrays["position"][command_offsets]
    assert (
        aligned_time is not None
        and reference_position is not None
        and command_position is not None
    )
    trace_rows = tuple(
        _object_row(row) for row in (_run_value(tracking_run, "trace_rows", ()) or ())
    )
    profile_rows = tuple(
        _object_row(row)
        for row in (_run_value(tracking_run, "profile_rows", ()) or ())
    )
    roles = dict(selection.roles or {})
    wanted = set(selection.metric_ids)
    rows: list[MetricRow] = []
    run_complete = _run_is_complete(tracking_run)

    def emit(
        metric_id: str,
        value: float | int | bool | None,
        *,
        window_id: str,
        semantics: str = OBSERVED,
        count: int | None = None,
        status: str = AVAILABLE,
        notes: str = "",
    ) -> None:
        if metric_id not in wanted:
            return
        if not run_complete and status == AVAILABLE:
            status = "unavailable_incomplete_run"
            value = None
            notes = (notes + "; " if notes else "") + "tracking run did not complete"
        rows.append(
            _metric_row(
                metric_id,
                value,
                input_id=selection.input_id,
                method_id=method_id,
                window_id=window_id,
                role=roles.get(metric_id, "diagnostic"),
                status=status,
                source_semantics=semantics,
                sample_count=count,
                notes=notes,
            )
        )

    for window in selection.windows:
        mask = _window_mask(aligned_time, window)
        time = aligned_time[mask]
        ref_position = reference_position[mask]
        out_position = command_position[mask]
        error = out_position - ref_position
        absolute_error = np.abs(error)
        emit(
            "position_rmse",
            float(np.sqrt(np.mean(np.square(error)))),
            window_id=window.window_id,
            count=error.size,
        )
        emit(
            "position_mae",
            float(np.mean(absolute_error)),
            window_id=window.window_id,
            count=error.size,
        )
        emit(
            "position_bias",
            float(np.mean(error)),
            window_id=window.window_id,
            count=error.size,
        )
        emit(
            "position_p95_abs_error",
            float(np.quantile(absolute_error, 0.95, method="linear")),
            window_id=window.window_id,
            count=error.size,
        )
        emit(
            "position_max_abs_error",
            float(np.max(absolute_error)),
            window_id=window.window_id,
            count=error.size,
        )
        iae = (
            0.0
            if time.size == 1
            else float(np.trapezoid(absolute_error, x=time))
        )
        emit(
            "position_iae",
            iae,
            window_id=window.window_id,
            count=error.size,
        )
        emit(
            "tracking_sample_count",
            int(error.size),
            window_id=window.window_id,
            count=error.size,
        )
        lag = _lag_metrics(ref_position, out_position, time, selection.max_lag_s)
        for metric_id, value in lag.items():
            emit(
                metric_id,
                value,
                window_id=window.window_id,
                count=error.size,
                notes="diagnostic only; primary metrics remain raw-time",
            )
        if window.terminal_hold:
            within = absolute_error <= window.settle_tolerance_rad
            suffix = np.logical_and.accumulate(within[::-1])[::-1]
            candidates = np.flatnonzero(suffix)
            settled = bool(candidates.size)
            settle_time = (
                float(time[int(candidates[0])] - time[0])
                if settled
                else float(time[-1] - time[0])
            )
            emit(
                "settled",
                settled,
                window_id=window.window_id,
                count=error.size,
            )
            emit(
                "settle_time_s",
                settle_time,
                window_id=window.window_id,
                count=error.size,
                notes="" if settled else "right-censored at the window end",
            )

        stop_go = _stop_go_metrics(
            reference_arrays,
            trace_rows,
            profile_rows,
            window,
        )
        for metric_id, (value, semantics, count) in stop_go.items():
            emit(
                metric_id,
                value,
                window_id=window.window_id,
                semantics=semantics,
                count=count,
                notes=(
                    "exact profile classification; "
                    f"|v|≤{STOP_GO_VELOCITY_TOLERANCE_RAD_S:g} rad/s, "
                    f"|a|≤{STOP_GO_ACCELERATION_TOLERANCE_RAD_S2:g} rad/s²"
                ),
            )

        for channel in ("velocity", "acceleration", "jerk"):
            channel_values = command_arrays[channel]
            if channel_values is None:
                for suffix in (
                    "max_abs",
                    "p95_abs",
                    "rms",
                    "limit_utilization",
                    "limit_margin",
                    "violation_count",
                ):
                    emit(
                        f"output_{channel}_{suffix}",
                        None,
                        window_id=window.window_id,
                        status=f"unavailable_missing_command_{channel}",
                    )
                continue
            selected_values = channel_values[command_offsets][mask]
            summary = _abs_summary(selected_values)
            for suffix, value in summary.items():
                emit(
                    f"output_{channel}_{suffix}",
                    value,
                    window_id=window.window_id,
                    count=selected_values.size,
                )
            limit = _limit(selection.limits, channel)
            if limit is None:
                for suffix in (
                    "limit_utilization",
                    "limit_margin",
                    "violation_count",
                ):
                    emit(
                        f"output_{channel}_{suffix}",
                        None,
                        window_id=window.window_id,
                        status=f"unavailable_missing_{channel}_limit",
                    )
            else:
                peak = summary["max_abs"]
                emit(
                    f"output_{channel}_limit_utilization",
                    peak / limit,
                    window_id=window.window_id,
                    count=selected_values.size,
                )
                emit(
                    f"output_{channel}_limit_margin",
                    limit - peak,
                    window_id=window.window_id,
                    count=selected_values.size,
                )
                emit(
                    f"output_{channel}_violation_count",
                    int(
                        np.count_nonzero(
                            np.abs(selected_values)
                            > limit + _limit_tolerance(limit)
                        )
                    ),
                    window_id=window.window_id,
                    count=selected_values.size,
                )

        command_acceleration = command_arrays["acceleration"]
        if command_acceleration is not None:
            selected_acceleration = command_acceleration[command_offsets][mask]
            emit(
                "acceleration_total_variation",
                float(np.sum(np.abs(np.diff(selected_acceleration)))),
                window_id=window.window_id,
                count=max(0, selected_acceleration.size - 1),
            )
            if selected_acceleration.size >= 2:
                window_dt = (
                    float(np.median(np.diff(time)))
                    if time.size >= 2
                    else float(np.median(np.diff(aligned_time)))
                )
                sampled_jerk = _second_order_derivative(
                    selected_acceleration, window_dt
                )
                summary = _abs_summary(sampled_jerk)
                emit(
                    "sampled_jerk_estimate_rms",
                    summary["rms"],
                    window_id=window.window_id,
                    semantics=ANALYSIS_ESTIMATE,
                    count=sampled_jerk.size,
                    notes="offline derivative; not an executable profile truth channel",
                )
                emit(
                    "sampled_jerk_estimate_max_abs",
                    summary["max_abs"],
                    window_id=window.window_id,
                    semantics=ANALYSIS_ESTIMATE,
                    count=sampled_jerk.size,
                    notes="offline derivative; not an executable profile truth channel",
                )

    # Pipeline truth errors use each state/target's represented physical time.
    # Explicit startup rows are excluded rather than silently mixing lower-
    # history initialization with the declared finite-difference formulas.
    for window in selection.windows:
        pipeline = _pipeline_metrics(reference_arrays, trace_rows, window)
        for metric_id, (value, semantics, count) in pipeline.items():
            emit(
                metric_id,
                value,
                window_id=window.window_id,
                semantics=semantics,
                count=count,
            )

    # Runtime, fallback and continuous-profile metrics retain their whole-run
    # full_overlap contract because they use command/profile clocks.
    trace_window = "full_overlap"
    if trace_rows:
        fallback_count, fallback_denominator = _boolean_count(
            trace_rows, ("fallback_applied", "fallback")
        )
        if fallback_denominator:
            emit(
                "fallback_count",
                fallback_count,
                window_id=trace_window,
                count=fallback_denominator,
            )
            emit(
                "fallback_rate",
                fallback_count / fallback_denominator,
                window_id=trace_window,
                count=fallback_denominator,
            )
        solver_failures, solver_denominator = _solver_failure_count(trace_rows)
        if solver_denominator:
            emit(
                "solver_failure_count",
                solver_failures,
                window_id=trace_window,
                count=solver_denominator,
            )
        resets, reset_denominator = _boolean_count(
            trace_rows, ("reset", "reset_applied", "component_reset")
        )
        if reset_denominator:
            emit(
                "reset_count",
                resets,
                window_id=trace_window,
                count=reset_denominator,
            )
        runtimes = _trace_runtime(trace_rows)
        for component, values in runtimes.items():
            for quantile, probability in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
                emit(
                    f"runtime_{component}_{quantile}_s",
                    float(np.quantile(values, probability, method="linear")),
                    window_id=trace_window,
                    count=values.size,
                )
            emit(
                f"runtime_{component}_max_s",
                float(np.max(values)),
                window_id=trace_window,
                count=values.size,
            )
        total_runtime = runtimes.get("total")
        if total_runtime is not None:
            dt = float(np.median(np.diff(reference_arrays["time"])))
            misses = int(np.count_nonzero(total_runtime > dt))
            emit(
                "deadline_miss_count",
                misses,
                window_id=trace_window,
                count=total_runtime.size,
            )
            emit(
                "deadline_miss_rate",
                misses / total_runtime.size,
                window_id=trace_window,
                count=total_runtime.size,
            )

    profile_metrics = _continuous_profile_metrics(
        profile_rows, trace_rows, selection.limits
    )
    for metric_id, (value, semantics, count) in profile_metrics.items():
        emit(
            metric_id,
            value,
            window_id=trace_window,
            semantics=semantics,
            count=count,
        )

    # Make requested-but-unavailable trace/profile metrics explicit once.
    observed_ids = {row.metric_id for row in rows}
    for metric_id in selection.metric_ids:
        if metric_id in observed_ids:
            continue
        metric_spec = get_metric_spec(metric_id)
        if metric_spec.family not in {
            "pipeline",
            "runtime",
            "continuous_constraints",
            "stop_go",
        }:
            continue
        if metric_spec.family == "stop_go":
            for window in selection.windows:
                emit(
                    metric_id,
                    None,
                    window_id=window.window_id,
                    status=(
                        "unavailable_missing_reference_velocity_or_exact_profiles"
                    ),
                )
            continue
        if metric_spec.missing_policy == "omit":
            continue
        if metric_spec.family == "continuous_constraints":
            status = (
                "unavailable_missing_profiles"
                if not profile_rows
                else "unavailable_inexact_or_unreconstructable_profiles"
            )
        elif metric_spec.family == "runtime":
            status = "unavailable_missing_runtime"
        else:
            status = "unavailable_missing_trace_field"
        emit(metric_id, None, window_id=trace_window, status=status)

    return MetricTable(tuple(rows))


def _flatten_metric_tables(
    tables: Mapping[str, MetricTable] | Iterable[MetricTable],
) -> tuple[MetricRow, ...]:
    rows: list[MetricRow] = []
    if isinstance(tables, Mapping):
        items = tables.items()
        for method_id, table in items:
            if not isinstance(table, MetricTable):
                raise TypeError("metric table mapping values must be MetricTable")
            for row in table.rows:
                if row.method_id and row.method_id != method_id:
                    raise ValueError(
                        f"table key {method_id!r} differs from row method "
                        f"{row.method_id!r}"
                    )
                if row.method_id:
                    rows.append(row)
                else:
                    rows.append(
                        MetricRow(
                            input_id=row.input_id,
                            method_id=str(method_id),
                            window_id=row.window_id,
                            metric_id=row.metric_id,
                            value=row.value,
                            unit=row.unit,
                            direction=row.direction,
                            role=row.role,
                            status=row.status,
                            source_semantics=row.source_semantics,
                            sample_count=row.sample_count,
                            notes=row.notes,
                        )
                    )
    else:
        for table in tables:
            if not isinstance(table, MetricTable):
                raise TypeError("metric_tables must contain MetricTable values")
            rows.extend(table.rows)
    return tuple(rows)


def _paired_bootstrap_interval(
    differences: np.ndarray,
    *,
    seed: int,
    repetitions: int,
    confidence_level: float,
) -> tuple[float, float]:
    generator = np.random.default_rng(seed)
    indices = generator.integers(
        0, differences.size, size=(repetitions, differences.size)
    )
    estimates = np.mean(differences[indices], axis=1)
    alpha = (1.0 - confidence_level) / 2.0
    return (
        float(np.quantile(estimates, alpha, method="linear")),
        float(np.quantile(estimates, 1.0 - alpha, method="linear")),
    )


def compare_methods(
    metric_tables: Mapping[str, MetricTable] | Iterable[MetricTable],
    comparison_spec: ComparisonSpec | Mapping[str, Any] | None = None,
) -> ComparisonTable:
    """Compare methods with strict complete-input pairing.

    ``difference`` is always candidate minus baseline. ``improvement`` flips
    the sign for lower-is-better metrics, so positive values uniformly mean
    better.  Bootstrap intervals, when requested, are paired intervals for the
    mean difference and use the explicitly supplied deterministic seed.
    """

    spec = _coerce_comparison_spec(comparison_spec)
    rows = _flatten_metric_tables(metric_tables)
    methods = sorted({row.method_id for row in rows if row.method_id})
    pairs = spec.pairs
    if not pairs:
        pairs = tuple(MethodPair(left, right) for left, right in combinations(methods, 2))
    metric_ids = spec.metric_ids or tuple(
        sorted(
            {
                row.metric_id
                for row in rows
                if row.method_id in {
                    method
                    for pair in pairs
                    for method in (
                        pair.baseline_method_id,
                        pair.candidate_method_id,
                    )
                }
            }
        )
    )
    unknown = sorted(set(metric_ids) - set(_REGISTRY))
    if unknown:
        raise KeyError(f"unknown comparison metric ids: {unknown}")
    window_ids = spec.window_ids or tuple(
        sorted({row.window_id for row in rows if row.metric_id in metric_ids})
    )
    index: dict[tuple[str, str, str, str], MetricRow] = {}
    for row in rows:
        key = (row.method_id, row.input_id, row.window_id, row.metric_id)
        if key in index:
            raise ValueError(f"duplicate metric row for key {key!r}")
        index[key] = row

    output: list[ComparisonRow] = []
    for pair_index, pair in enumerate(pairs):
        for window_id in window_ids:
            for metric_id in metric_ids:
                observed_inputs = {
                    row.input_id
                    for row in rows
                    if row.method_id
                    in {pair.baseline_method_id, pair.candidate_method_id}
                    and row.window_id == window_id
                    and row.metric_id == metric_id
                }
                expected_inputs = set(spec.input_ids) if spec.input_ids else observed_inputs
                baseline_values: list[float] = []
                candidate_values: list[float] = []
                missing: list[str] = []
                for input_id in sorted(expected_inputs):
                    baseline = index.get(
                        (
                            pair.baseline_method_id,
                            input_id,
                            window_id,
                            metric_id,
                        )
                    )
                    candidate = index.get(
                        (
                            pair.candidate_method_id,
                            input_id,
                            window_id,
                            metric_id,
                        )
                    )
                    invalid = (
                        baseline is None
                        or candidate is None
                        or baseline.status != AVAILABLE
                        or candidate.status != AVAILABLE
                        or baseline.value is None
                        or candidate.value is None
                    )
                    if not invalid:
                        baseline_value = (
                            float(baseline.value)
                            if isinstance(baseline.value, (bool, np.bool_))
                            else _as_finite_float(baseline.value)
                        )
                        candidate_value = (
                            float(candidate.value)
                            if isinstance(candidate.value, (bool, np.bool_))
                            else _as_finite_float(candidate.value)
                        )
                        invalid = baseline_value is None or candidate_value is None
                    if invalid:
                        missing.append(input_id)
                        continue
                    assert baseline_value is not None and candidate_value is not None
                    baseline_values.append(baseline_value)
                    candidate_values.append(candidate_value)
                metric = get_metric_spec(metric_id)
                expected_count = len(expected_inputs)
                if (
                    expected_count == 0
                    or missing
                    or len(baseline_values) != expected_count
                ):
                    output.append(
                        ComparisonRow(
                            comparison_id=pair.resolved_id,
                            baseline_method_id=pair.baseline_method_id,
                            candidate_method_id=pair.candidate_method_id,
                            window_id=window_id,
                            metric_id=metric_id,
                            status=UNAVAILABLE_INCOMPLETE_PAIR,
                            paired_input_count=len(baseline_values),
                            expected_input_count=expected_count,
                            baseline_mean=None,
                            candidate_mean=None,
                            difference=None,
                            relative_difference=None,
                            improvement=None,
                            ci_lower=None,
                            ci_upper=None,
                            unit=metric.unit,
                            direction=metric.direction,
                            notes=(
                                "missing/unavailable inputs: " + ",".join(missing)
                                if missing
                                else "no paired inputs"
                            ),
                        )
                    )
                    continue
                baseline_array = np.asarray(baseline_values, dtype=np.float64)
                candidate_array = np.asarray(candidate_values, dtype=np.float64)
                differences = candidate_array - baseline_array
                baseline_mean = float(np.mean(baseline_array))
                candidate_mean = float(np.mean(candidate_array))
                difference = float(np.mean(differences))
                relative = (
                    None
                    if abs(baseline_mean) <= np.finfo(np.float64).tiny
                    else difference / abs(baseline_mean)
                )
                improvement = (
                    -difference
                    if metric.direction == "lower"
                    else difference
                    if metric.direction == "higher"
                    else None
                )
                ci_lower: float | None = None
                ci_upper: float | None = None
                if spec.bootstrap_repetitions:
                    ci_lower, ci_upper = _paired_bootstrap_interval(
                        differences,
                        seed=int(spec.bootstrap_seed) + pair_index,
                        repetitions=spec.bootstrap_repetitions,
                        confidence_level=spec.confidence_level,
                    )
                output.append(
                    ComparisonRow(
                        comparison_id=pair.resolved_id,
                        baseline_method_id=pair.baseline_method_id,
                        candidate_method_id=pair.candidate_method_id,
                        window_id=window_id,
                        metric_id=metric_id,
                        status=AVAILABLE,
                        paired_input_count=expected_count,
                        expected_input_count=expected_count,
                        baseline_mean=baseline_mean,
                        candidate_mean=candidate_mean,
                        difference=difference,
                        relative_difference=relative,
                        improvement=improvement,
                        ci_lower=ci_lower,
                        ci_upper=ci_upper,
                        unit=metric.unit,
                        direction=metric.direction,
                    )
                )
    return ComparisonTable(tuple(output))


__all__ = [
    "ANALYSIS_ESTIMATE",
    "AVAILABLE",
    "ComparisonRow",
    "ComparisonSpec",
    "ComparisonTable",
    "DEFAULT_TRACKING_METRIC_IDS",
    "EvaluationWindow",
    "METRIC_REGISTRY",
    "MethodPair",
    "MetricRow",
    "MetricSet",
    "MetricSpec",
    "MetricTable",
    "ReferenceAnalysis",
    "TRUTH",
    "UNAVAILABLE_INCOMPLETE_PAIR",
    "AnalysisSpec",
    "analyze_reference",
    "analyze_tracking",
    "compare_methods",
    "get_metric_spec",
    "metric_registry",
    "register_metric",
]
