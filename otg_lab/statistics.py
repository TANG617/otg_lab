"""Trajectory-level paired statistics for the OTG experiment suite.

Every public comparison API requires one unique row per complete trajectory.
Duplicated unit identifiers are rejected instead of being silently averaged,
which prevents sample-level pseudoreplication.  Paired bootstrap resampling is
10,000 draws by default and always resamples matched trajectory pairs together.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Union

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
UnitKey = Union[str, tuple[Any, ...]]


class StatisticalValidationError(ValueError):
    """Raised when an analysis violates trajectory-level statistical rules."""


@dataclass(frozen=True)
class PairedBootstrapResult:
    """Machine-readable result of one paired trajectory comparison."""

    metric: str
    baseline_method: str
    candidate_method: str
    direction: str
    relative_definition: str
    relative_point_defined: bool
    relative_interval_defined: bool
    relative_status: str
    improvement_direction: str
    n_trajectories: int
    n_expected_trajectories: int
    n_excluded_trajectories: int
    resamples: int
    seed: int
    confidence_level: float
    baseline_mean: float
    baseline_median: float
    baseline_q25: float
    baseline_q75: float
    candidate_mean: float
    candidate_median: float
    candidate_q25: float
    candidate_q75: float
    absolute_difference: float
    absolute_ci_low: float
    absolute_ci_high: float
    relative_difference: float | None
    relative_ci_low: float | None
    relative_ci_high: float | None
    improvement: float
    improvement_ci_low: float
    improvement_ci_high: float
    relative_improvement: float | None
    relative_improvement_ci_low: float | None
    relative_improvement_ci_high: float | None
    effect_size_name: str
    effect_size: float | None
    effect_size_ci_low: float | None
    effect_size_ci_high: float | None
    effect_size_defined: bool
    unadjusted_p_value: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_vector(values: ArrayLike, name: str) -> FloatArray:
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != 1 or result.size == 0:
        raise StatisticalValidationError(f"{name} must be a non-empty vector")
    if not np.all(np.isfinite(result)):
        raise StatisticalValidationError(f"{name} contains NaN or infinity")
    return result


def _validate_bootstrap_arguments(
    *, resamples: int, confidence_level: float, seed: int
) -> None:
    if not isinstance(resamples, int) or resamples < 1:
        raise StatisticalValidationError("resamples must be a positive integer")
    if not 0.0 < confidence_level < 1.0:
        raise StatisticalValidationError("confidence_level must lie in (0, 1)")
    if not isinstance(seed, (int, np.integer)) or seed < 0:
        raise StatisticalValidationError("seed must be a non-negative integer")


def _unit_sort_key(value: UnitKey) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    return (str(value),)


def _mapping(values: Mapping[UnitKey, float], name: str) -> dict[UnitKey, float]:
    if not values:
        raise StatisticalValidationError(f"{name} contains no trajectories")
    result: dict[UnitKey, float] = {}
    for unit, raw_value in values.items():
        value = float(raw_value)
        if not math.isfinite(value):
            raise StatisticalValidationError(
                f"{name} trajectory {unit!r} contains NaN or infinity"
            )
        result[unit] = value
    return result


def _expected_units(values: Sequence[UnitKey], name: str) -> set[UnitKey]:
    if not values:
        raise StatisticalValidationError(f"{name} contains no trajectories")
    try:
        result = set(values)
    except TypeError as error:
        raise StatisticalValidationError(
            f"{name} contains an unhashable unit"
        ) from error
    if len(result) != len(values):
        raise StatisticalValidationError(f"{name} contains duplicate trajectories")
    return result


def records_by_method(
    records: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    method_field: str = "method",
    unit_fields: Sequence[str] = ("dataset_id", "session_id", "trajectory_id"),
) -> dict[str, dict[UnitKey, float]]:
    """Index trajectory rows and reject duplicate statistical units.

    This function deliberately does not aggregate duplicate rows.  If a caller
    accidentally supplies per-joint or per-sample records, the analysis fails
    before a significance value can be produced.
    """

    if not records:
        raise StatisticalValidationError("records table is empty")
    if not unit_fields:
        raise StatisticalValidationError("unit_fields cannot be empty")
    indexed: dict[str, dict[UnitKey, float]] = {}
    for row_index, row in enumerate(records):
        missing = [
            field for field in (method_field, metric, *unit_fields) if field not in row
        ]
        if missing:
            raise StatisticalValidationError(
                f"row {row_index} is missing fields {missing}"
            )
        method = str(row[method_field])
        unit_tuple = tuple(row[field] for field in unit_fields)
        unit: UnitKey = unit_tuple[0] if len(unit_tuple) == 1 else unit_tuple
        value = float(row[metric])
        if not math.isfinite(value):
            raise StatisticalValidationError(
                f"row {row_index} metric {metric} contains NaN or infinity"
            )
        method_values = indexed.setdefault(method, {})
        if unit in method_values:
            raise StatisticalValidationError(
                f"duplicate statistical unit {unit!r} for method {method!r}; "
                "provide one complete-trajectory row, not samples or joints"
            )
        method_values[unit] = value
    return indexed


def _bootstrap_means(
    values: FloatArray,
    *,
    resamples: int,
    rng: np.random.Generator,
    chunk_size: int = 2048,
) -> FloatArray:
    output = np.empty(resamples, dtype=np.float64)
    for start in range(0, resamples, chunk_size):
        stop = min(start + chunk_size, resamples)
        indices = rng.integers(0, values.size, size=(stop - start, values.size))
        output[start:stop] = np.mean(values[indices], axis=1)
    return output


def _bootstrap_joint_statistics(
    baseline: FloatArray,
    candidate: FloatArray,
    *,
    resamples: int,
    rng: np.random.Generator,
    chunk_size: int = 1024,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    differences = candidate - baseline
    difference_means = np.empty(resamples, dtype=np.float64)
    baseline_means = np.empty(resamples, dtype=np.float64)
    effects = np.full(resamples, np.nan, dtype=np.float64)
    for start in range(0, resamples, chunk_size):
        stop = min(start + chunk_size, resamples)
        indices = rng.integers(
            0, differences.size, size=(stop - start, differences.size)
        )
        sampled_difference = differences[indices]
        sampled_baseline = baseline[indices]
        means = np.mean(sampled_difference, axis=1)
        sampled_baseline_means = np.mean(sampled_baseline, axis=1)
        difference_means[start:stop] = means
        baseline_means[start:stop] = sampled_baseline_means
        if differences.size >= 2:
            standard_deviation = np.std(sampled_difference, axis=1, ddof=1)
            valid = standard_deviation > np.finfo(float).eps
            chunk_effects = np.full(stop - start, np.nan, dtype=float)
            chunk_effects[valid] = means[valid] / standard_deviation[valid]
            effects[start:stop] = chunk_effects
    return difference_means, baseline_means, effects


def _relative_statistics(
    *,
    observed_difference: float,
    observed_baseline: float,
    bootstrap_difference: FloatArray,
    bootstrap_baseline: FloatArray,
    confidence_level: float,
    improvement_multiplier: float,
) -> dict[str, Any]:
    """Return relative statistics without discarding valid absolute inference.

    A zero observed baseline makes the relative point estimate undefined.  A
    zero baseline in any bootstrap resample makes a finite percentile interval
    undefined even when the observed point estimate exists.  Both cases retain
    the paired denominator, absolute interval, effect size, and p-value.
    """

    point_defined = abs(observed_baseline) > np.finfo(float).tiny
    interval_defined = point_defined and not np.any(
        np.abs(bootstrap_baseline) <= np.finfo(float).tiny
    )
    if not point_defined:
        status = "undefined_observed_baseline_mean_zero"
        relative_difference = None
        relative_improvement = None
    else:
        status = (
            "defined"
            if interval_defined
            else "point_defined_interval_undefined_bootstrap_baseline_mean_zero"
        )
        relative_difference = observed_difference / abs(observed_baseline)
        relative_improvement = improvement_multiplier * relative_difference

    if interval_defined:
        bootstrap_relative = bootstrap_difference / np.abs(bootstrap_baseline)
        relative_interval = _percentile_interval(
            bootstrap_relative, confidence_level
        )
        relative_improvement_interval = (
            (-relative_interval[1], -relative_interval[0])
            if improvement_multiplier < 0.0
            else relative_interval
        )
    else:
        relative_interval = (None, None)
        relative_improvement_interval = (None, None)
    return {
        "relative_point_defined": point_defined,
        "relative_interval_defined": interval_defined,
        "relative_status": status,
        "relative_difference": relative_difference,
        "relative_ci_low": relative_interval[0],
        "relative_ci_high": relative_interval[1],
        "relative_improvement": relative_improvement,
        "relative_improvement_ci_low": relative_improvement_interval[0],
        "relative_improvement_ci_high": relative_improvement_interval[1],
    }


def _percentile_interval(
    values: FloatArray, confidence_level: float
) -> tuple[float, float]:
    alpha = 1.0 - confidence_level
    return (
        float(np.quantile(values, alpha / 2.0, method="linear")),
        float(np.quantile(values, 1.0 - alpha / 2.0, method="linear")),
    )


def _cohen_dz(differences: FloatArray) -> float | None:
    if differences.size < 2:
        return None
    standard_deviation = float(np.std(differences, ddof=1))
    if standard_deviation <= np.finfo(float).eps:
        return None
    return float(np.mean(differences) / standard_deviation)


def paired_trajectory_bootstrap(
    baseline: Mapping[UnitKey, float],
    candidate: Mapping[UnitKey, float],
    *,
    metric: str,
    baseline_method: str = "baseline",
    candidate_method: str = "candidate",
    resamples: int = 10_000,
    confidence_level: float = 0.95,
    seed: int = 0,
    lower_is_better: bool = True,
    expected_units: Sequence[UnitKey] | None = None,
) -> PairedBootstrapResult:
    """Run a paired bootstrap in which each draw resamples trajectories.

    Signed differences are always ``candidate - baseline``.  For lower-is-
    better error/runtime metrics, ``improvement`` is also reported explicitly
    as ``baseline - candidate`` so interpretation never depends on convention.
    Pair sets must match exactly; silent complete-case deletion is prohibited.
    """

    _validate_bootstrap_arguments(
        resamples=resamples, confidence_level=confidence_level, seed=seed
    )
    baseline_map = _mapping(baseline, "baseline")
    candidate_map = _mapping(candidate, "candidate")
    baseline_units = set(baseline_map)
    candidate_units = set(candidate_map)
    if baseline_units != candidate_units:
        missing_candidate = sorted(baseline_units - candidate_units, key=_unit_sort_key)
        missing_baseline = sorted(candidate_units - baseline_units, key=_unit_sort_key)
        raise StatisticalValidationError(
            "paired trajectory sets differ; "
            f"missing candidate={missing_candidate[:5]}, "
            f"missing baseline={missing_baseline[:5]}"
        )
    expected_set = (
        baseline_units
        if expected_units is None
        else _expected_units(expected_units, "expected_units")
    )
    if baseline_units != expected_set:
        missing_both = sorted(expected_set - baseline_units, key=_unit_sort_key)
        unexpected = sorted(baseline_units - expected_set, key=_unit_sort_key)
        raise StatisticalValidationError(
            "paired data differ from the predeclared expected trajectory set; "
            f"missing from both methods={missing_both[:5]}, "
            f"unexpected={unexpected[:5]}"
        )
    units = sorted(baseline_units, key=_unit_sort_key)
    if len(units) < 2:
        raise StatisticalValidationError(
            "paired inference requires at least two independent trajectories"
        )
    baseline_values = _finite_vector(
        [baseline_map[unit] for unit in units], "baseline values"
    )
    candidate_values = _finite_vector(
        [candidate_map[unit] for unit in units], "candidate values"
    )
    difference = candidate_values - baseline_values
    baseline_mean = float(np.mean(baseline_values))

    rng = np.random.default_rng(seed)
    bootstrap_difference, bootstrap_baseline, bootstrap_effect = (
        _bootstrap_joint_statistics(
            baseline_values,
            candidate_values,
            resamples=resamples,
            rng=rng,
        )
    )
    difference_interval = _percentile_interval(bootstrap_difference, confidence_level)
    effect = _cohen_dz(difference)
    finite_effect = bootstrap_effect[np.isfinite(bootstrap_effect)]
    if effect is None or finite_effect.size < max(100, resamples // 20):
        effect_interval: tuple[float | None, float | None] = (None, None)
        effect_defined = False
    else:
        effect_interval = _percentile_interval(finite_effect, confidence_level)
        effect_defined = True

    # A centered paired-bootstrap null distribution yields a deterministic,
    # finite, two-sided p-value.  The +1 correction prevents a reported zero.
    null_distribution = bootstrap_difference - float(np.mean(difference))
    extreme = int(
        np.count_nonzero(np.abs(null_distribution) >= abs(float(np.mean(difference))))
    )
    p_value = float((extreme + 1) / (resamples + 1))
    improvement_multiplier = -1.0 if lower_is_better else 1.0
    relative = _relative_statistics(
        observed_difference=float(np.mean(difference)),
        observed_baseline=baseline_mean,
        bootstrap_difference=bootstrap_difference,
        bootstrap_baseline=bootstrap_baseline,
        confidence_level=confidence_level,
        improvement_multiplier=improvement_multiplier,
    )
    if improvement_multiplier < 0.0:
        improvement_interval = (-difference_interval[1], -difference_interval[0])
    else:
        improvement_interval = difference_interval
    return PairedBootstrapResult(
        metric=metric,
        baseline_method=baseline_method,
        candidate_method=candidate_method,
        direction="candidate_minus_baseline",
        relative_definition="mean(candidate-baseline)/abs(mean(baseline))",
        relative_point_defined=bool(relative["relative_point_defined"]),
        relative_interval_defined=bool(relative["relative_interval_defined"]),
        relative_status=str(relative["relative_status"]),
        improvement_direction=(
            "baseline_minus_candidate_lower_is_better"
            if lower_is_better
            else "candidate_minus_baseline_higher_is_better"
        ),
        n_trajectories=int(difference.size),
        n_expected_trajectories=len(expected_set),
        n_excluded_trajectories=0,
        resamples=resamples,
        seed=int(seed),
        confidence_level=float(confidence_level),
        baseline_mean=baseline_mean,
        baseline_median=float(np.median(baseline_values)),
        baseline_q25=float(np.quantile(baseline_values, 0.25, method="linear")),
        baseline_q75=float(np.quantile(baseline_values, 0.75, method="linear")),
        candidate_mean=float(np.mean(candidate_values)),
        candidate_median=float(np.median(candidate_values)),
        candidate_q25=float(np.quantile(candidate_values, 0.25, method="linear")),
        candidate_q75=float(np.quantile(candidate_values, 0.75, method="linear")),
        absolute_difference=float(np.mean(difference)),
        absolute_ci_low=difference_interval[0],
        absolute_ci_high=difference_interval[1],
        relative_difference=relative["relative_difference"],
        relative_ci_low=relative["relative_ci_low"],
        relative_ci_high=relative["relative_ci_high"],
        improvement=float(improvement_multiplier * np.mean(difference)),
        improvement_ci_low=float(improvement_interval[0]),
        improvement_ci_high=float(improvement_interval[1]),
        relative_improvement=relative["relative_improvement"],
        relative_improvement_ci_low=relative["relative_improvement_ci_low"],
        relative_improvement_ci_high=relative["relative_improvement_ci_high"],
        effect_size_name="paired_cohen_dz_candidate_minus_baseline",
        effect_size=effect,
        effect_size_ci_low=effect_interval[0],
        effect_size_ci_high=effect_interval[1],
        effect_size_defined=effect_defined,
        unadjusted_p_value=p_value,
    )


def paired_comparison_from_records(
    records: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    baseline_method: str,
    candidate_method: str,
    method_field: str = "method",
    unit_fields: Sequence[str] = ("dataset_id", "session_id", "trajectory_id"),
    resamples: int = 10_000,
    confidence_level: float = 0.95,
    seed: int = 0,
    lower_is_better: bool = True,
    expected_units: Sequence[UnitKey] | None = None,
) -> PairedBootstrapResult:
    """Validate trajectory records and run one named paired comparison."""

    indexed = records_by_method(
        records,
        metric=metric,
        method_field=method_field,
        unit_fields=unit_fields,
    )
    if baseline_method not in indexed:
        raise StatisticalValidationError(
            f"baseline method {baseline_method!r} is absent"
        )
    if candidate_method not in indexed:
        raise StatisticalValidationError(
            f"candidate method {candidate_method!r} is absent"
        )
    # In the absence of a split-manifest set, the union across every method in
    # the supplied table is the strongest recoverable denominator.  Formal
    # callers should pass expected_units from the locked design so a trajectory
    # that failed for every method cannot disappear from inference.
    comparison_expected = (
        list(set().union(*(set(values) for values in indexed.values())))
        if expected_units is None
        else expected_units
    )
    return paired_trajectory_bootstrap(
        indexed[baseline_method],
        indexed[candidate_method],
        metric=metric,
        baseline_method=baseline_method,
        candidate_method=candidate_method,
        resamples=resamples,
        confidence_level=confidence_level,
        seed=seed,
        lower_is_better=lower_is_better,
        expected_units=comparison_expected,
    )


def stratified_paired_trajectory_bootstrap(
    baseline: Mapping[UnitKey, float],
    candidate: Mapping[UnitKey, float],
    strata: Mapping[UnitKey, Any],
    *,
    metric: str,
    baseline_method: str = "baseline",
    candidate_method: str = "candidate",
    stratum_name: str = "stratum",
    resamples: int = 10_000,
    confidence_level: float = 0.95,
    seed: int = 0,
    lower_is_better: bool = True,
    expected_units: Sequence[UnitKey] | None = None,
) -> dict[str, Any]:
    """Run paired inference while resampling independently within strata.

    The aggregate draw preserves every predeclared stratum's original weight,
    so a large-error family cannot dominate by changing the resampled family
    mix.  Per-stratum effects, trajectory win/harm rates, the worst five
    trajectories, and an explicit heterogeneity summary are returned alongside
    the ordinary (unstratified) paired result.
    """

    _validate_bootstrap_arguments(
        resamples=resamples, confidence_level=confidence_level, seed=seed
    )
    baseline_map = _mapping(baseline, "baseline")
    candidate_map = _mapping(candidate, "candidate")
    baseline_units = set(baseline_map)
    candidate_units = set(candidate_map)
    if baseline_units != candidate_units:
        missing_candidate = sorted(baseline_units - candidate_units, key=_unit_sort_key)
        missing_baseline = sorted(candidate_units - baseline_units, key=_unit_sort_key)
        raise StatisticalValidationError(
            "paired trajectory sets differ; "
            f"missing candidate={missing_candidate[:5]}, "
            f"missing baseline={missing_baseline[:5]}"
        )
    expected_set = (
        baseline_units
        if expected_units is None
        else _expected_units(expected_units, "expected_units")
    )
    if baseline_units != expected_set:
        missing_both = sorted(expected_set - baseline_units, key=_unit_sort_key)
        unexpected = sorted(baseline_units - expected_set, key=_unit_sort_key)
        raise StatisticalValidationError(
            "paired data differ from the predeclared expected trajectory set; "
            f"missing from both methods={missing_both[:5]}, "
            f"unexpected={unexpected[:5]}"
        )
    strata_units = set(strata)
    if strata_units != expected_set:
        missing = sorted(expected_set - strata_units, key=_unit_sort_key)
        unexpected = sorted(strata_units - expected_set, key=_unit_sort_key)
        raise StatisticalValidationError(
            "strata differ from the predeclared expected trajectory set; "
            f"missing={missing[:5]}, unexpected={unexpected[:5]}"
        )

    grouped: dict[str, list[UnitKey]] = {}
    for unit in sorted(expected_set, key=_unit_sort_key):
        raw_stratum = strata[unit]
        if raw_stratum is None:
            raise StatisticalValidationError(f"{stratum_name} is null for {unit!r}")
        stratum = str(raw_stratum)
        if not stratum:
            raise StatisticalValidationError(f"{stratum_name} is empty for {unit!r}")
        grouped.setdefault(stratum, []).append(unit)
    if any(len(units) < 2 for units in grouped.values()):
        undersized = sorted(
            stratum for stratum, units in grouped.items() if len(units) < 2
        )
        raise StatisticalValidationError(
            "stratified bootstrap requires at least two trajectories per stratum; "
            f"undersized={undersized}"
        )

    overall = paired_trajectory_bootstrap(
        baseline_map,
        candidate_map,
        metric=metric,
        baseline_method=baseline_method,
        candidate_method=candidate_method,
        resamples=resamples,
        confidence_level=confidence_level,
        seed=seed,
        lower_is_better=lower_is_better,
        expected_units=sorted(expected_set, key=_unit_sort_key),
    )
    stratum_rows: list[dict[str, Any]] = []
    for offset, stratum in enumerate(sorted(grouped)):
        units = grouped[stratum]
        result = paired_trajectory_bootstrap(
            {unit: baseline_map[unit] for unit in units},
            {unit: candidate_map[unit] for unit in units},
            metric=metric,
            baseline_method=baseline_method,
            candidate_method=candidate_method,
            resamples=resamples,
            confidence_level=confidence_level,
            seed=seed + offset + 1,
            lower_is_better=lower_is_better,
            expected_units=units,
        )
        stratum_rows.append({stratum_name: stratum, **result.to_dict()})

    # Preserve fixed stratum weights in every draw.  This differs from an
    # ordinary bootstrap, whose random stratum counts can let one family drive
    # an aggregate draw merely through composition noise.
    rng = np.random.default_rng(seed + len(grouped) + 1)
    bootstrap_difference = np.zeros(resamples, dtype=float)
    bootstrap_baseline = np.zeros(resamples, dtype=float)
    total = len(expected_set)
    observed_difference = 0.0
    observed_baseline = 0.0
    for stratum in sorted(grouped):
        units = grouped[stratum]
        weight = len(units) / total
        baseline_values = _finite_vector(
            [baseline_map[unit] for unit in units], f"{stratum} baseline values"
        )
        candidate_values = _finite_vector(
            [candidate_map[unit] for unit in units], f"{stratum} candidate values"
        )
        difference_values = candidate_values - baseline_values
        observed_difference += weight * float(np.mean(difference_values))
        observed_baseline += weight * float(np.mean(baseline_values))
        for start in range(0, resamples, 2048):
            stop = min(start + 2048, resamples)
            indices = rng.integers(0, len(units), size=(stop - start, len(units)))
            bootstrap_difference[start:stop] += weight * np.mean(
                difference_values[indices], axis=1
            )
            bootstrap_baseline[start:stop] += weight * np.mean(
                baseline_values[indices], axis=1
            )
    difference_interval = _percentile_interval(bootstrap_difference, confidence_level)
    multiplier = -1.0 if lower_is_better else 1.0
    relative = _relative_statistics(
        observed_difference=observed_difference,
        observed_baseline=observed_baseline,
        bootstrap_difference=bootstrap_difference,
        bootstrap_baseline=bootstrap_baseline,
        confidence_level=confidence_level,
        improvement_multiplier=multiplier,
    )
    improvement_interval = (
        (-difference_interval[1], -difference_interval[0])
        if lower_is_better
        else difference_interval
    )
    trajectory_rows = []
    for unit in sorted(expected_set, key=_unit_sort_key):
        difference = candidate_map[unit] - baseline_map[unit]
        improvement = multiplier * difference
        trajectory_rows.append(
            {
                "unit": unit if isinstance(unit, str) else repr(unit),
                stratum_name: str(strata[unit]),
                "baseline": baseline_map[unit],
                "candidate": candidate_map[unit],
                "improvement": improvement,
            }
        )
    improved_count = sum(row["improvement"] > 0.0 for row in trajectory_rows)
    harmful_count = sum(row["improvement"] < 0.0 for row in trajectory_rows)
    trajectory_improvements = np.asarray(
        [float(row["improvement"]) for row in trajectory_rows], dtype=float
    )
    ordered_worst = sorted(
        trajectory_rows, key=lambda row: (row["improvement"], row["unit"])
    )[:5]
    stratum_improvements = {
        str(row[stratum_name]): float(row["improvement"]) for row in stratum_rows
    }
    worst_stratum = min(
        stratum_improvements, key=lambda item: (stratum_improvements[item], item)
    )
    return {
        "metric": metric,
        "baseline_method": baseline_method,
        "candidate_method": candidate_method,
        "stratum_name": stratum_name,
        "overall": overall.to_dict(),
        "stratified": {
            "n_trajectories": total,
            "n_strata": len(grouped),
            "resamples": resamples,
            "seed": seed + len(grouped) + 1,
            "confidence_level": confidence_level,
            "improvement": multiplier * observed_difference,
            "improvement_ci_low": float(improvement_interval[0]),
            "improvement_ci_high": float(improvement_interval[1]),
            "relative_point_defined": relative["relative_point_defined"],
            "relative_interval_defined": relative["relative_interval_defined"],
            "relative_status": relative["relative_status"],
            "relative_improvement": relative["relative_improvement"],
            "relative_improvement_ci_low": relative[
                "relative_improvement_ci_low"
            ],
            "relative_improvement_ci_high": relative[
                "relative_improvement_ci_high"
            ],
        },
        "strata": stratum_rows,
        "trajectory_summary": {
            "trajectory_count": total,
            "mean_improvement": float(np.mean(trajectory_improvements)),
            "median_improvement": float(np.median(trajectory_improvements)),
            "q25_improvement": float(
                np.quantile(trajectory_improvements, 0.25, method="linear")
            ),
            "q75_improvement": float(
                np.quantile(trajectory_improvements, 0.75, method="linear")
            ),
            "improved_count": improved_count,
            "harmful_count": harmful_count,
            "unchanged_count": total - improved_count - harmful_count,
            "improved_rate": improved_count / total,
            "harmful_rate": harmful_count / total,
            "worst_5": ordered_worst,
        },
        "heterogeneity": {
            "worst_stratum": worst_stratum,
            "worst_stratum_improvement": stratum_improvements[worst_stratum],
            "best_stratum_improvement": max(stratum_improvements.values()),
            "improvement_range": max(stratum_improvements.values())
            - min(stratum_improvements.values()),
            "improvement_std": float(
                np.std(list(stratum_improvements.values()), ddof=0)
            ),
            "harmful_strata_count": sum(
                value < 0.0 for value in stratum_improvements.values()
            ),
        },
    }


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Return Holm step-down family-wise adjusted p-values."""

    values = _finite_vector(p_values, "p_values")
    if np.any(values < 0.0) or np.any(values > 1.0):
        raise StatisticalValidationError("p-values must lie in [0, 1]")
    order = np.argsort(values, kind="stable")
    adjusted = np.empty_like(values)
    running = 0.0
    count = values.size
    for rank, original_index in enumerate(order):
        candidate = min(1.0, (count - rank) * float(values[original_index]))
        running = max(running, candidate)
        adjusted[original_index] = running
    return adjusted.tolist()


def paired_comparisons(
    records: Sequence[Mapping[str, Any]],
    comparisons: Sequence[Mapping[str, Any]],
    *,
    method_field: str = "method",
    unit_fields: Sequence[str] = ("dataset_id", "session_id", "trajectory_id"),
    resamples: int = 10_000,
    confidence_level: float = 0.95,
    seed: int = 0,
    alpha: float = 0.05,
    expected_units: Sequence[UnitKey] | None = None,
) -> list[dict[str, Any]]:
    """Run predeclared comparisons and apply Holm to secondary tests.

    Each comparison mapping requires ``metric``, ``baseline_method``, and
    ``candidate_method``.  ``secondary`` defaults to true.  Primary rows retain
    their raw p-value; Holm adjustment is performed only over the explicitly
    secondary family.
    """

    if not comparisons:
        raise StatisticalValidationError("comparisons is empty")
    if not 0.0 < alpha < 1.0:
        raise StatisticalValidationError("alpha must lie in (0, 1)")
    rows: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, comparison in enumerate(comparisons):
        required = {"metric", "baseline_method", "candidate_method"}
        missing = required - set(comparison)
        if missing:
            raise StatisticalValidationError(
                f"comparison {index} is missing {sorted(missing)}"
            )
        comparison_id = str(
            comparison.get(
                "comparison_id",
                f"{comparison['metric']}:{comparison['candidate_method']}-vs-{comparison['baseline_method']}",
            )
        )
        if comparison_id in used_ids:
            raise StatisticalValidationError(
                f"duplicate comparison_id {comparison_id!r}"
            )
        used_ids.add(comparison_id)
        result = paired_comparison_from_records(
            records,
            metric=str(comparison["metric"]),
            baseline_method=str(comparison["baseline_method"]),
            candidate_method=str(comparison["candidate_method"]),
            method_field=method_field,
            unit_fields=unit_fields,
            resamples=resamples,
            confidence_level=confidence_level,
            seed=seed + index,
            lower_is_better=bool(comparison.get("lower_is_better", True)),
            expected_units=expected_units,
        )
        rows.append(
            {
                "comparison_id": comparison_id,
                "secondary": bool(comparison.get("secondary", True)),
                **result.to_dict(),
            }
        )

    secondary_indices = [index for index, row in enumerate(rows) if row["secondary"]]
    if secondary_indices:
        adjusted = holm_adjust(
            [rows[index]["unadjusted_p_value"] for index in secondary_indices]
        )
        for index, value in zip(secondary_indices, adjusted):
            rows[index]["holm_adjusted_p_value"] = value
            rows[index]["reject_holm"] = bool(value < alpha)
    for row in rows:
        if not row["secondary"]:
            value = float(row["unadjusted_p_value"])
            row["holm_adjusted_p_value"] = value
            row["reject_holm"] = bool(value < alpha)
    return rows


def bootstrap_confidence_intervals(
    records: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    method_field: str = "method",
    unit_fields: Sequence[str] = ("dataset_id", "session_id", "trajectory_id"),
    resamples: int = 10_000,
    confidence_level: float = 0.95,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Bootstrap method means and medians over independent trajectories."""

    _validate_bootstrap_arguments(
        resamples=resamples, confidence_level=confidence_level, seed=seed
    )
    indexed = records_by_method(
        records,
        metric=metric,
        method_field=method_field,
        unit_fields=unit_fields,
    )
    rows: list[dict[str, Any]] = []
    for method_index, method in enumerate(sorted(indexed)):
        values = _finite_vector(
            [
                indexed[method][unit]
                for unit in sorted(indexed[method], key=_unit_sort_key)
            ],
            f"{method} values",
        )
        if values.size < 2:
            raise StatisticalValidationError(
                f"bootstrap CI for {method!r} requires at least two trajectories"
            )
        rng = np.random.default_rng(seed + method_index)
        bootstrap_mean = np.empty(resamples, dtype=float)
        bootstrap_median = np.empty(resamples, dtype=float)
        chunk_size = 2048
        for start in range(0, resamples, chunk_size):
            stop = min(start + chunk_size, resamples)
            indices = rng.integers(0, values.size, size=(stop - start, values.size))
            sampled = values[indices]
            bootstrap_mean[start:stop] = np.mean(sampled, axis=1)
            bootstrap_median[start:stop] = np.median(sampled, axis=1)
        mean_interval = _percentile_interval(bootstrap_mean, confidence_level)
        median_interval = _percentile_interval(bootstrap_median, confidence_level)
        rows.append(
            {
                "method": method,
                "metric": metric,
                "n_trajectories": int(values.size),
                "resamples": resamples,
                "seed": seed + method_index,
                "confidence_level": confidence_level,
                "mean": float(np.mean(values)),
                "mean_ci_low": mean_interval[0],
                "mean_ci_high": mean_interval[1],
                "median": float(np.median(values)),
                "median_ci_low": median_interval[0],
                "median_ci_high": median_interval[1],
                "q25": float(np.quantile(values, 0.25, method="linear")),
                "q75": float(np.quantile(values, 0.75, method="linear")),
                "iqr": float(
                    np.quantile(values, 0.75, method="linear")
                    - np.quantile(values, 0.25, method="linear")
                ),
            }
        )
    return rows


__all__ = [
    "PairedBootstrapResult",
    "StatisticalValidationError",
    "bootstrap_confidence_intervals",
    "holm_adjust",
    "paired_comparison_from_records",
    "paired_comparisons",
    "paired_trajectory_bootstrap",
    "records_by_method",
    "stratified_paired_trajectory_bootstrap",
]
