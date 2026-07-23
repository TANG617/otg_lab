"""Locked V4 trajectory-level statistical tables.

The public builder in this module consumes one row per method and whole
trajectory plus the locked split manifest.  It deliberately never aggregates
duplicate rows: sample-level or joint-level pseudoreplication therefore fails
before inference can be produced.

All formal bootstrap settings are read from ``V4_STATISTICAL_DESIGN.json`` and
validated against the preregistered V4 constants.  Missing primary units and
incomplete ordinary-Ruckig pairs are retained in output tables but formal
paired inference is marked unavailable; complete-case deletion is never used.
"""

from __future__ import annotations

import csv
import json
import math
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

PRIMARY_BASELINE = "one_step_governed_p_direct"
PRIMARY_PV = "one_step_governed_pv_direct"
PRIMARY_CANDIDATE = "one_step_governed_pva_direct"
ORDINARY_BASELINE = "predicted_p_ordinary_ruckig"
ORDINARY_CANDIDATE = "raw_predicted_pva_ordinary_ruckig"

EXPECTED_TRAJECTORIES = 120
BOOTSTRAP_RESAMPLES = 10_000
CONFIDENCE_LEVEL = 0.95
PRIMARY_SEED = 2026072301
SECONDARY_BASE_SEED = 2026072302
GUARDRAIL_SEED = 2026072303
SUBGROUP_BASE_SEED = 2026072304

FAMILIES = (
    "stationary_endpoint",
    "oscillatory",
    "piecewise_constant_jerk",
    "stop_and_go",
    "rapid_reversal",
    "boundary_grazing",
)
DEMAND_STRATA = ("low", "medium", "high", "near_limit")
ACCELERATION_ACTIVE_FAMILIES = frozenset(
    {
        "piecewise_constant_jerk",
        "stop_and_go",
        "rapid_reversal",
        "boundary_grazing",
    }
)
ACCELERATION_ACTIVE_DEMAND = frozenset({"high", "near_limit"})

SECONDARY_COMPARISONS = (
    {
        "comparison_id": "S1",
        "candidate_method": PRIMARY_PV,
        "baseline_method": PRIMARY_BASELINE,
        "metric": "position_rmse",
        "contextual_only": False,
    },
    {
        "comparison_id": "S2",
        "candidate_method": PRIMARY_CANDIDATE,
        "baseline_method": PRIMARY_PV,
        "metric": "position_rmse",
        "contextual_only": False,
    },
    {
        "comparison_id": "S3",
        "candidate_method": PRIMARY_CANDIDATE,
        "baseline_method": PRIMARY_BASELINE,
        "metric": "position_max_abs_error",
        "contextual_only": False,
    },
    {
        "comparison_id": "S4",
        "candidate_method": PRIMARY_CANDIDATE,
        "baseline_method": PRIMARY_BASELINE,
        "metric": "lag_s",
        "contextual_only": False,
    },
    {
        "comparison_id": "S5",
        "candidate_method": ORDINARY_CANDIDATE,
        "baseline_method": ORDINARY_BASELINE,
        "metric": "position_rmse",
        "contextual_only": True,
    },
)

CSV_TABLE_NAMES = (
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
    "bootstrap_reconstruction.csv",
)


class V4StatisticalValidationError(ValueError):
    """Raised when V4 inputs would violate the preregistered analysis."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise V4StatisticalValidationError(message)


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            raise V4StatisticalValidationError(
                f"statistical design is missing {'.'.join(keys)}"
            )
        value = value[key]
    return value


def validate_v4_statistical_design(design: Mapping[str, Any]) -> None:
    """Fail closed if a formal caller supplies a changed V4 design."""

    _require(
        design.get("schema_version") == "otg.v4-statistical-design.v1",
        "unexpected V4 statistical-design schema",
    )
    _require(
        design.get("statistical_unit") == "whole_trajectory",
        "V4 statistical unit must be whole_trajectory",
    )
    _require(
        int(_nested(design, "population", "trajectory_count"))
        == EXPECTED_TRAJECTORIES,
        "V4 denominator must be exactly 120 trajectories",
    )
    _require(
        tuple(_nested(design, "population", "families")) == FAMILIES,
        "V4 family levels or order changed",
    )
    _require(
        tuple(_nested(design, "population", "demand_strata")) == DEMAND_STRATA,
        "V4 demand-stratum levels or order changed",
    )
    primary = _nested(design, "primary")
    _require(
        primary.get("baseline_method") == PRIMARY_BASELINE
        and primary.get("candidate_method") == PRIMARY_CANDIDATE
        and primary.get("metric") == "position_rmse",
        "V4 primary comparison changed",
    )
    primary_bootstrap = _nested(design, "primary", "bootstrap")
    _require(
        int(primary_bootstrap.get("resamples", -1)) == BOOTSTRAP_RESAMPLES
        and int(primary_bootstrap.get("seed", -1)) == PRIMARY_SEED
        and float(primary_bootstrap.get("confidence_level", -1))
        == CONFIDENCE_LEVEL,
        "V4 primary bootstrap settings changed",
    )
    guardrail_bootstrap = _nested(design, "guardrails", "bootstrap")
    _require(
        int(guardrail_bootstrap.get("resamples", -1)) == BOOTSTRAP_RESAMPLES
        and int(guardrail_bootstrap.get("seed", -1)) == GUARDRAIL_SEED,
        "V4 guardrail bootstrap settings changed",
    )
    secondary = tuple(_nested(design, "secondary", "comparison_family"))
    expected_secondary = tuple(
        (
            item["comparison_id"],
            item["candidate_method"],
            item["baseline_method"],
            item["metric"],
        )
        for item in SECONDARY_COMPARISONS
    )
    actual_secondary = tuple(
        (
            str(item.get("id")),
            str(item.get("candidate_method")),
            str(item.get("baseline_method")),
            str(item.get("metric")),
        )
        for item in secondary
    )
    _require(
        actual_secondary == expected_secondary,
        "V4 secondary comparison family changed",
    )
    secondary_bootstrap = _nested(design, "secondary", "bootstrap")
    _require(
        int(secondary_bootstrap.get("resamples", -1)) == BOOTSTRAP_RESAMPLES
        and int(secondary_bootstrap.get("base_seed", -1)) == SECONDARY_BASE_SEED,
        "V4 secondary bootstrap settings changed",
    )
    subgroup_bootstrap = _nested(design, "subgroups", "bootstrap")
    _require(
        int(subgroup_bootstrap.get("resamples", -1)) == BOOTSTRAP_RESAMPLES
        and int(subgroup_bootstrap.get("base_seed", -1)) == SUBGROUP_BASE_SEED,
        "V4 subgroup bootstrap settings changed",
    )
    _require(
        float(
            _nested(design, "guardrails", "max_error_noninferiority", "margin")
        )
        == 0.05,
        "V4 max-error guardrail margin changed",
    )
    _require(
        float(_nested(design, "guardrails", "lag_noninferiority", "margin_s"))
        == 0.01,
        "V4 lag guardrail margin changed",
    )


def _manifest_test_rows(
    manifest: Mapping[str, Any],
    design: Mapping[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    _require(
        manifest.get("dataset_id") == _nested(design, "population", "dataset_id"),
        "manifest dataset_id does not match the statistical design",
    )
    raw_rows = manifest.get("trajectories")
    _require(
        isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)),
        "manifest trajectories must be a sequence",
    )
    test_rows: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_rows):
        _require(isinstance(raw, Mapping), f"manifest row {index} is not a mapping")
        if raw.get("split") != "test":
            continue
        trajectory_id = str(raw.get("trajectory_id", ""))
        _require(trajectory_id, f"manifest test row {index} has no trajectory_id")
        _require(
            trajectory_id not in test_rows,
            f"manifest has duplicate test trajectory {trajectory_id!r}",
        )
        _require(
            raw.get("locked") is True,
            f"manifest test trajectory {trajectory_id!r} is not locked",
        )
        family = str(raw.get("family", ""))
        demand = str(raw.get("demand_stratum", ""))
        _require(family in FAMILIES, f"unknown V4 family {family!r}")
        _require(demand in DEMAND_STRATA, f"unknown V4 demand stratum {demand!r}")
        test_rows[trajectory_id] = dict(raw)
    expected_ids = sorted(test_rows)
    _require(
        len(expected_ids) == EXPECTED_TRAJECTORIES,
        f"locked V4 test manifest has {len(expected_ids)} trajectories, expected 120",
    )
    for family in FAMILIES:
        family_rows = [
            row for row in test_rows.values() if row["family"] == family
        ]
        _require(
            len(family_rows) == 20,
            f"family {family!r} has {len(family_rows)} test trajectories, expected 20",
        )
        for demand in DEMAND_STRATA:
            count = sum(row["demand_stratum"] == demand for row in family_rows)
            _require(
                count == 5,
                f"family/demand cell {family!r}/{demand!r} has {count}, expected 5",
            )
    return expected_ids, test_rows


def _finite_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _index_records(
    records: Sequence[Mapping[str, Any]],
    expected_ids: Sequence[str],
    *,
    method_field: str,
    trajectory_field: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    expected = set(expected_ids)
    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for row_index, raw in enumerate(records):
        _require(isinstance(raw, Mapping), f"metrics row {row_index} is not a mapping")
        _require(
            method_field in raw and trajectory_field in raw,
            f"metrics row {row_index} is missing method or trajectory identity",
        )
        method = str(raw[method_field])
        trajectory_id = str(raw[trajectory_field])
        if trajectory_id not in expected:
            raise V4StatisticalValidationError(
                f"metrics row {row_index} has a trajectory outside locked test: "
                f"{trajectory_id!r}"
            )
        by_trajectory = indexed.setdefault(method, {})
        if trajectory_id in by_trajectory:
            raise V4StatisticalValidationError(
                f"duplicate statistical unit for method {method!r}, trajectory "
                f"{trajectory_id!r}; provide one whole-trajectory row, not "
                "samples or joints"
            )
        by_trajectory[trajectory_id] = dict(raw)
    return indexed


def _availability(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    method: str,
    metric: str,
    expected_ids: Sequence[str],
) -> dict[str, Any]:
    rows = indexed.get(method, {})
    available_ids = [
        trajectory_id
        for trajectory_id in expected_ids
        if trajectory_id in rows
        and rows[trajectory_id].get("completed") is not False
        and _finite_or_none(rows[trajectory_id].get(metric)) is not None
    ]
    attempted_ids = [trajectory_id for trajectory_id in expected_ids if trajectory_id in rows]
    missing_record_ids = sorted(set(expected_ids) - set(attempted_ids))
    unavailable_metric_ids = sorted(set(attempted_ids) - set(available_ids))
    return {
        "attempted_count": len(attempted_ids),
        "completed_count": len(available_ids),
        "failed_or_unavailable_count": len(expected_ids) - len(available_ids),
        "missing_record_count": len(missing_record_ids),
        "missing_record_ids": missing_record_ids,
        "unavailable_metric_count": len(unavailable_metric_ids),
        "unavailable_metric_ids": unavailable_metric_ids,
        "available_ids": available_ids,
    }


def _comparison_availability(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    comparison: Mapping[str, Any],
    expected_ids: Sequence[str],
    *,
    require_harm_metric: bool = True,
) -> dict[str, Any]:
    baseline_method = str(comparison["baseline_method"])
    candidate_method = str(comparison["candidate_method"])
    metric = str(comparison["metric"])
    baseline = _availability(indexed, baseline_method, metric, expected_ids)
    candidate = _availability(indexed, candidate_method, metric, expected_ids)
    baseline_rows = indexed.get(baseline_method, {})
    candidate_rows = indexed.get(candidate_method, {})
    complete_ids = [
        trajectory_id
        for trajectory_id in expected_ids
        if trajectory_id in baseline["available_ids"]
        and trajectory_id in candidate["available_ids"]
    ]
    harm_complete_ids = [
        trajectory_id
        for trajectory_id in expected_ids
        if trajectory_id in baseline_rows
        and trajectory_id in candidate_rows
        and baseline_rows[trajectory_id].get("completed") is not False
        and candidate_rows[trajectory_id].get("completed") is not False
        and _finite_or_none(
            baseline_rows[trajectory_id].get("position_rmse")
        )
        is not None
        and _finite_or_none(
            candidate_rows[trajectory_id].get("position_rmse")
        )
        is not None
    ]
    complete = len(complete_ids) == len(expected_ids)
    harm_complete = len(harm_complete_ids) == len(expected_ids)
    return {
        "complete": complete and (harm_complete or not require_harm_metric),
        "metric_complete": complete,
        "harm_metric_complete": harm_complete,
        "paired_count": len(complete_ids),
        "harm_paired_count": len(harm_complete_ids),
        "missing_pair_ids": sorted(set(expected_ids) - set(complete_ids)),
        "missing_harm_pair_ids": sorted(set(expected_ids) - set(harm_complete_ids)),
        "baseline": baseline,
        "candidate": candidate,
    }


def _values(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    method: str,
    metric: str,
    trajectory_ids: Sequence[str],
) -> np.ndarray:
    values = [
        _finite_or_none(indexed[method][trajectory_id].get(metric))
        for trajectory_id in trajectory_ids
    ]
    if any(value is None for value in values):
        raise V4StatisticalValidationError(
            f"{method!r}/{metric!r} is incomplete for the requested denominator"
        )
    return np.asarray(values, dtype=np.float64)


def _bootstrap_draws(
    baseline: np.ndarray,
    candidate: np.ndarray,
    *,
    seed: int,
) -> dict[str, np.ndarray]:
    _require(
        baseline.ndim == candidate.ndim == 1
        and baseline.size == candidate.size
        and baseline.size > 1,
        "paired bootstrap needs at least two matched trajectory values",
    )
    rng = np.random.Generator(np.random.PCG64(seed))
    indices = rng.integers(
        0,
        baseline.size,
        size=(BOOTSTRAP_RESAMPLES, baseline.size),
    )
    baseline_means = np.mean(baseline[indices], axis=1)
    candidate_means = np.mean(candidate[indices], axis=1)
    difference = candidate_means - baseline_means
    improvement = baseline_means - candidate_means
    relative_difference = np.full(BOOTSTRAP_RESAMPLES, np.nan, dtype=float)
    relative_improvement = np.full(BOOTSTRAP_RESAMPLES, np.nan, dtype=float)
    valid = np.abs(baseline_means) > np.finfo(float).tiny
    relative_difference[valid] = difference[valid] / np.abs(baseline_means[valid])
    relative_improvement[valid] = improvement[valid] / np.abs(
        baseline_means[valid]
    )
    return {
        "baseline_mean": baseline_means,
        "candidate_mean": candidate_means,
        "absolute_difference": difference,
        "absolute_improvement": improvement,
        "relative_difference": relative_difference,
        "relative_improvement": relative_improvement,
    }


def _percentile(values: np.ndarray) -> tuple[float | None, float | None]:
    if values.size != BOOTSTRAP_RESAMPLES or not np.all(np.isfinite(values)):
        return None, None
    return (
        float(np.quantile(values, 0.025, method="linear")),
        float(np.quantile(values, 0.975, method="linear")),
    )


def _paired_t_and_dz(improvements: np.ndarray) -> dict[str, Any]:
    mean = float(np.mean(improvements))
    standard_deviation = float(np.std(improvements, ddof=1))
    zero_variance = bool(standard_deviation <= np.finfo(float).eps)
    if zero_variance:
        p_value = 1.0 if np.all(improvements == 0.0) else 0.0
        cohen_dz = None
    else:
        t_statistic = mean / (standard_deviation / math.sqrt(improvements.size))
        p_value = float(2.0 * stats.t.sf(abs(t_statistic), improvements.size - 1))
        cohen_dz = mean / standard_deviation
    return {
        "cohen_dz": cohen_dz,
        "cohen_dz_zero_variance": zero_variance,
        "unadjusted_p": p_value,
    }


def _wilson(successes: int, denominator: int) -> tuple[float, float]:
    _require(denominator > 0, "Wilson interval denominator must be positive")
    z = 1.959963984540054
    proportion = successes / denominator
    z2_over_n = z * z / denominator
    center = (proportion + z * z / (2.0 * denominator)) / (1.0 + z2_over_n)
    half = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / denominator
            + z * z / (4.0 * denominator * denominator)
        )
        / (1.0 + z2_over_n)
    )
    return center - half, center + half


def _harm_summary(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    baseline_method: str,
    candidate_method: str,
    expected_ids: Sequence[str],
) -> dict[str, Any]:
    availability = _comparison_availability(
        indexed,
        {
            "baseline_method": baseline_method,
            "candidate_method": candidate_method,
            "metric": "position_rmse",
        },
        expected_ids,
        require_harm_metric=False,
    )
    if not availability["metric_complete"]:
        return {
            "harmful_status": "unavailable_incomplete_denominator",
            "harmful_count": None,
            "harmful_denominator": EXPECTED_TRAJECTORIES,
            "harmful_evaluated_count": availability["paired_count"],
            "harmful_rate": None,
            "harmful_rate_ci_low": None,
            "harmful_rate_ci_high": None,
        }
    baseline = _values(
        indexed, baseline_method, "position_rmse", expected_ids
    )
    candidate = _values(
        indexed, candidate_method, "position_rmse", expected_ids
    )
    harmful_count = int(np.count_nonzero(candidate > baseline))
    low, high = _wilson(harmful_count, len(expected_ids))
    return {
        "harmful_status": "available",
        "harmful_count": harmful_count,
        "harmful_denominator": len(expected_ids),
        "harmful_evaluated_count": len(expected_ids),
        "harmful_rate": harmful_count / len(expected_ids),
        "harmful_rate_ci_low": low,
        "harmful_rate_ci_high": high,
    }


def _available_comparison_row(
    comparison: Mapping[str, Any],
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    expected_ids: Sequence[str],
    *,
    seed: int,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    baseline_method = str(comparison["baseline_method"])
    candidate_method = str(comparison["candidate_method"])
    metric = str(comparison["metric"])
    baseline = _values(indexed, baseline_method, metric, expected_ids)
    candidate = _values(indexed, candidate_method, metric, expected_ids)
    draws = _bootstrap_draws(baseline, candidate, seed=seed)
    improvement = baseline - candidate
    baseline_mean = float(np.mean(baseline))
    candidate_mean = float(np.mean(candidate))
    absolute_difference = candidate_mean - baseline_mean
    absolute_improvement = baseline_mean - candidate_mean
    relative_defined = abs(baseline_mean) > np.finfo(float).tiny
    relative_difference = (
        absolute_difference / abs(baseline_mean) if relative_defined else None
    )
    relative_improvement = (
        absolute_improvement / abs(baseline_mean) if relative_defined else None
    )
    difference_ci = _percentile(draws["absolute_difference"])
    improvement_ci = _percentile(draws["absolute_improvement"])
    relative_difference_ci = _percentile(draws["relative_difference"])
    relative_improvement_ci = _percentile(draws["relative_improvement"])
    row = {
        "comparison_id": str(comparison["comparison_id"]),
        "status": "available",
        "formal_inference_available": True,
        "contextual_only": bool(comparison.get("contextual_only", False)),
        "metric": metric,
        "baseline_method": baseline_method,
        "candidate_method": candidate_method,
        "direction": "baseline_minus_candidate_is_improvement",
        "trajectory_count": len(expected_ids),
        "required_trajectory_count": EXPECTED_TRAJECTORIES,
        "excluded_trajectory_count": 0,
        "baseline_mean": baseline_mean,
        "candidate_mean": candidate_mean,
        "absolute_difference": absolute_difference,
        "absolute_difference_ci_low": difference_ci[0],
        "absolute_difference_ci_high": difference_ci[1],
        "absolute_improvement": absolute_improvement,
        "absolute_improvement_ci_low": improvement_ci[0],
        "absolute_improvement_ci_high": improvement_ci[1],
        "relative_difference": relative_difference,
        "relative_difference_ci_low": relative_difference_ci[0],
        "relative_difference_ci_high": relative_difference_ci[1],
        "relative_improvement": relative_improvement,
        "relative_improvement_ci_low": relative_improvement_ci[0],
        "relative_improvement_ci_high": relative_improvement_ci[1],
        "relative_defined": relative_defined
        and relative_improvement_ci[0] is not None,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": seed,
        "confidence_level": CONFIDENCE_LEVEL,
        **_paired_t_and_dz(improvement),
        **_harm_summary(
            indexed, baseline_method, candidate_method, expected_ids
        ),
    }
    return row, draws


def _unavailable_comparison_row(
    comparison: Mapping[str, Any],
    availability: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    missing = sorted(
        set(availability["missing_pair_ids"])
        | set(availability["missing_harm_pair_ids"])
    )
    return {
        "comparison_id": str(comparison["comparison_id"]),
        "status": "unavailable_incomplete_denominator",
        "formal_inference_available": False,
        "contextual_only": bool(comparison.get("contextual_only", False)),
        "metric": str(comparison["metric"]),
        "baseline_method": str(comparison["baseline_method"]),
        "candidate_method": str(comparison["candidate_method"]),
        "direction": "baseline_minus_candidate_is_improvement",
        "trajectory_count": availability["paired_count"],
        "required_trajectory_count": EXPECTED_TRAJECTORIES,
        "excluded_trajectory_count": 0,
        "missing_trajectory_count": len(missing),
        "missing_trajectory_ids_json": json.dumps(missing, separators=(",", ":")),
        "baseline_attempted_count": availability["baseline"]["attempted_count"],
        "baseline_completed_count": availability["baseline"]["completed_count"],
        "candidate_attempted_count": availability["candidate"]["attempted_count"],
        "candidate_completed_count": availability["candidate"]["completed_count"],
        "baseline_mean": None,
        "candidate_mean": None,
        "absolute_difference": None,
        "absolute_difference_ci_low": None,
        "absolute_difference_ci_high": None,
        "absolute_improvement": None,
        "absolute_improvement_ci_low": None,
        "absolute_improvement_ci_high": None,
        "relative_difference": None,
        "relative_difference_ci_low": None,
        "relative_difference_ci_high": None,
        "relative_improvement": None,
        "relative_improvement_ci_low": None,
        "relative_improvement_ci_high": None,
        "relative_defined": False,
        "cohen_dz": None,
        "cohen_dz_zero_variance": None,
        "unadjusted_p": None,
        "holm_adjusted_p": None,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": seed,
        "confidence_level": CONFIDENCE_LEVEL,
        "harmful_status": "unavailable_incomplete_denominator",
        "harmful_count": None,
        "harmful_denominator": EXPECTED_TRAJECTORIES,
        "harmful_evaluated_count": availability["harm_paired_count"],
        "harmful_rate": None,
        "harmful_rate_ci_low": None,
        "harmful_rate_ci_high": None,
    }


def holm_adjust_v4(p_values: Mapping[str, float | None]) -> dict[str, float | None]:
    """Apply the locked five-test Holm family with ID tie-breaking.

    An unavailable member is represented by p=1 while adjusting available
    members, so the family size remains five.  Its own adjusted value remains
    unavailable rather than being fabricated.
    """

    _require(
        set(p_values) == {item["comparison_id"] for item in SECONDARY_COMPARISONS},
        "Holm adjustment requires exactly S1 through S5",
    )
    numeric: list[tuple[str, float]] = []
    for comparison_id, raw in p_values.items():
        if raw is None:
            numeric.append((comparison_id, 1.0))
            continue
        value = float(raw)
        _require(
            math.isfinite(value) and 0.0 <= value <= 1.0,
            f"invalid p-value for {comparison_id}",
        )
        numeric.append((comparison_id, value))
    ordered = sorted(numeric, key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float | None] = {}
    running = 0.0
    family_size = len(ordered)
    for rank, (comparison_id, value) in enumerate(ordered):
        running = max(running, min(1.0, (family_size - rank) * value))
        adjusted[comparison_id] = running
    for comparison_id, value in p_values.items():
        if value is None:
            adjusted[comparison_id] = None
    return adjusted


def classify_primary_result(
    relative_point: float | None,
    relative_ci_low: float | None,
    relative_ci_high: float | None,
) -> dict[str, Any]:
    """Return the single preregistered label and all derived flags."""

    if (
        relative_point is None
        or relative_ci_low is None
        or relative_ci_high is None
    ):
        return {
            "classification": "unavailable_incomplete_denominator",
            "confirmed_positive": False,
            "practically_material": False,
            "strongly_material": False,
            "inconclusive": False,
            "confirmed_harmful": False,
        }
    strongly_material = relative_ci_low >= 0.05
    confirmed_positive = relative_ci_low > 0.0
    practically_material = confirmed_positive and relative_point >= 0.05
    inconclusive = relative_ci_low <= 0.0 and relative_ci_high >= 0.0
    confirmed_harmful = relative_ci_high < 0.0
    # The order is the locked V4 single-label precedence.  Harm and
    # inconclusiveness are disjoint from the three nested positive labels.
    if strongly_material:
        label = "strongly_material"
    elif practically_material:
        label = "practically_material"
    elif confirmed_positive:
        label = "confirmed_positive"
    elif inconclusive:
        label = "inconclusive"
    elif confirmed_harmful:
        label = "confirmed_harmful"
    else:
        label = "inconclusive"
    return {
        "classification": label,
        "confirmed_positive": confirmed_positive,
        "practically_material": practically_material,
        "strongly_material": strongly_material,
        "inconclusive": inconclusive,
        "confirmed_harmful": confirmed_harmful,
    }


def _guardrail_rows(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    expected_ids: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, bool | None], dict[str, dict[str, np.ndarray]]]:
    definitions = (
        (
            "max_error_relative_worsening",
            "position_max_abs_error",
            "relative",
            0.05,
        ),
        ("lag_absolute_worsening", "lag_s", "absolute", 0.01),
    )
    rows: list[dict[str, Any]] = []
    pass_status: dict[str, bool | None] = {}
    retained_draws: dict[str, dict[str, np.ndarray]] = {}
    for guardrail_id, metric, kind, margin in definitions:
        comparison = {
            "comparison_id": guardrail_id,
            "baseline_method": PRIMARY_BASELINE,
            "candidate_method": PRIMARY_CANDIDATE,
            "metric": metric,
        }
        availability = _comparison_availability(
            indexed, comparison, expected_ids, require_harm_metric=False
        )
        if not availability["metric_complete"]:
            row = {
                "comparison_id": guardrail_id,
                "analysis_kind": "guardrail",
                "status": "unavailable_incomplete_denominator",
                "metric": metric,
                "trajectory_count": availability["paired_count"],
                "required_trajectory_count": EXPECTED_TRAJECTORIES,
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": GUARDRAIL_SEED,
                "confidence_level": CONFIDENCE_LEVEL,
                "worsening": None,
                "worsening_ci_low": None,
                "worsening_ci_high": None,
                "margin": margin,
                "guardrail_pass": None,
            }
            rows.append(row)
            pass_status[guardrail_id] = None
            continue
        baseline = _values(indexed, PRIMARY_BASELINE, metric, expected_ids)
        candidate = _values(indexed, PRIMARY_CANDIDATE, metric, expected_ids)
        draws = _bootstrap_draws(
            baseline, candidate, seed=GUARDRAIL_SEED
        )
        retained_draws[guardrail_id] = draws
        if kind == "relative":
            baseline_mean = float(np.mean(baseline))
            if baseline_mean <= np.finfo(float).tiny:
                worsening = None
                interval = (None, None)
                guardrail_pass = None
                status = "unavailable_nonpositive_baseline_mean"
            else:
                worsening = (
                    float(np.mean(candidate)) - baseline_mean
                ) / baseline_mean
                interval = _percentile(draws["relative_difference"])
                guardrail_pass = (
                    interval[1] is not None and interval[1] <= margin
                )
                status = "available"
        else:
            worsening = float(np.mean(candidate - baseline))
            interval = _percentile(draws["absolute_difference"])
            guardrail_pass = interval[1] is not None and interval[1] <= margin
            status = "available"
        rows.append(
            {
                "comparison_id": guardrail_id,
                "analysis_kind": "guardrail",
                "status": status,
                "metric": metric,
                "trajectory_count": len(expected_ids),
                "required_trajectory_count": EXPECTED_TRAJECTORIES,
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": GUARDRAIL_SEED,
                "confidence_level": CONFIDENCE_LEVEL,
                "worsening": worsening,
                "worsening_ci_low": interval[0],
                "worsening_ci_high": interval[1],
                "margin": margin,
                "guardrail_pass": guardrail_pass,
            }
        )
        pass_status[guardrail_id] = guardrail_pass
    return rows, pass_status, retained_draws


def _subgroup_definitions(
    manifest_rows: Mapping[str, Mapping[str, Any]],
    expected_ids: Sequence[str],
) -> list[tuple[str, str, list[str]]]:
    definitions: list[tuple[str, str, list[str]]] = []
    for family in FAMILIES:
        definitions.append(
            (
                "reference_family",
                family,
                [
                    trajectory_id
                    for trajectory_id in expected_ids
                    if manifest_rows[trajectory_id]["family"] == family
                ],
            )
        )
    for demand in DEMAND_STRATA:
        definitions.append(
            (
                "demand_stratum",
                demand,
                [
                    trajectory_id
                    for trajectory_id in expected_ids
                    if manifest_rows[trajectory_id]["demand_stratum"] == demand
                ],
            )
        )
    definitions.append(
        (
            "acceleration_active",
            "true",
            [
                trajectory_id
                for trajectory_id in expected_ids
                if manifest_rows[trajectory_id]["family"]
                in ACCELERATION_ACTIVE_FAMILIES
                and manifest_rows[trajectory_id]["demand_stratum"]
                in ACCELERATION_ACTIVE_DEMAND
            ],
        )
    )
    expected_counts = {
        "reference_family": 20,
        "demand_stratum": 30,
        "acceleration_active": 40,
    }
    for dimension, value, trajectory_ids in definitions:
        _require(
            len(trajectory_ids) == expected_counts[dimension],
            f"subgroup {dimension}={value} has {len(trajectory_ids)}, expected "
            f"{expected_counts[dimension]}",
        )
    return definitions


def _subgroup_rows(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    manifest_rows: Mapping[str, Mapping[str, Any]],
    expected_ids: Sequence[str],
    *,
    overall_available: bool,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, np.ndarray]]]:
    definitions = _subgroup_definitions(manifest_rows, expected_ids)
    seed_by_key = {
        (dimension, value): SUBGROUP_BASE_SEED + ordinal
        for ordinal, (dimension, value, _) in enumerate(
            sorted(definitions, key=lambda item: (item[0], item[1]))
        )
    }
    rows: list[dict[str, Any]] = []
    retained_draws: dict[str, dict[str, np.ndarray]] = {}
    for dimension, value, trajectory_ids in definitions:
        seed = seed_by_key[(dimension, value)]
        comparison_id = f"subgroup:{dimension}:{value}"
        if not overall_available:
            rows.append(
                {
                    "comparison_id": comparison_id,
                    "analysis_kind": "subgroup",
                    "stratum_dimension": dimension,
                    "stratum_value": value,
                    "status": "unavailable_incomplete_primary_denominator",
                    "trajectory_count": len(trajectory_ids),
                    "absolute_improvement": None,
                    "absolute_improvement_ci_low": None,
                    "absolute_improvement_ci_high": None,
                    "relative_improvement": None,
                    "relative_improvement_ci_low": None,
                    "relative_improvement_ci_high": None,
                    "harmful_count": None,
                    "harmful_denominator": len(trajectory_ids),
                    "harmful_rate": None,
                    "harmful_rate_ci_low": None,
                    "harmful_rate_ci_high": None,
                    "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                    "bootstrap_seed": seed,
                    "confidence_level": CONFIDENCE_LEVEL,
                }
            )
            continue
        baseline = _values(
            indexed, PRIMARY_BASELINE, "position_rmse", trajectory_ids
        )
        candidate = _values(
            indexed, PRIMARY_CANDIDATE, "position_rmse", trajectory_ids
        )
        draws = _bootstrap_draws(baseline, candidate, seed=seed)
        retained_draws[comparison_id] = draws
        absolute = float(np.mean(baseline) - np.mean(candidate))
        baseline_mean = float(np.mean(baseline))
        relative = (
            absolute / baseline_mean
            if baseline_mean > np.finfo(float).tiny
            else None
        )
        absolute_ci = _percentile(draws["absolute_improvement"])
        relative_ci = _percentile(draws["relative_improvement"])
        harmful_count = int(np.count_nonzero(candidate > baseline))
        harmful_ci = _wilson(harmful_count, len(trajectory_ids))
        rows.append(
            {
                "comparison_id": comparison_id,
                "analysis_kind": "subgroup",
                "stratum_dimension": dimension,
                "stratum_value": value,
                "status": "available",
                "trajectory_count": len(trajectory_ids),
                "absolute_improvement": absolute,
                "absolute_improvement_ci_low": absolute_ci[0],
                "absolute_improvement_ci_high": absolute_ci[1],
                "relative_improvement": relative,
                "relative_improvement_ci_low": relative_ci[0],
                "relative_improvement_ci_high": relative_ci[1],
                "harmful_count": harmful_count,
                "harmful_denominator": len(trajectory_ids),
                "harmful_rate": harmful_count / len(trajectory_ids),
                "harmful_rate_ci_low": harmful_ci[0],
                "harmful_rate_ci_high": harmful_ci[1],
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": seed,
                "confidence_level": CONFIDENCE_LEVEL,
            }
        )
    return rows, retained_draws


def _add_heterogeneity(rows: list[dict[str, Any]]) -> None:
    available_family = [
        row
        for row in rows
        if row["stratum_dimension"] == "reference_family"
        and row["relative_improvement"] is not None
    ]
    available_demand = [
        row
        for row in rows
        if row["stratum_dimension"] == "demand_stratum"
        and row["relative_improvement"] is not None
    ]
    if len(available_family) == len(FAMILIES):
        family_values = {
            str(row["stratum_value"]): float(row["relative_improvement"])
            for row in available_family
        }
        worst_family = min(
            family_values, key=lambda name: (family_values[name], name)
        )
        family_range = max(family_values.values()) - min(family_values.values())
        for row in available_family:
            row["family_effect_range"] = family_range
            row["worst_family"] = worst_family
            row["worst_family_relative_improvement"] = family_values[worst_family]
            row["heterogeneity_status"] = "descriptive_secondary"
    if len(available_demand) == len(DEMAND_STRATA):
        demand_values = [
            float(row["relative_improvement"]) for row in available_demand
        ]
        demand_range = max(demand_values) - min(demand_values)
        for row in available_demand:
            row["demand_effect_range"] = demand_range
            row["heterogeneity_status"] = "descriptive_secondary"


def _summary_metrics(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    expected_ids: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method in sorted(indexed):
        for metric in ("position_rmse", "position_max_abs_error", "lag_s"):
            availability = _availability(indexed, method, metric, expected_ids)
            values = [
                _finite_or_none(indexed[method][trajectory_id].get(metric))
                for trajectory_id in availability["available_ids"]
            ]
            finite = np.asarray(values, dtype=float)
            rows.append(
                {
                    "method": method,
                    "metric": metric,
                    "required_trajectory_count": len(expected_ids),
                    "attempted_trajectory_count": availability["attempted_count"],
                    "available_trajectory_count": availability["completed_count"],
                    "failed_or_unavailable_trajectory_count": availability[
                        "failed_or_unavailable_count"
                    ],
                    "summary_status": (
                        "complete"
                        if availability["completed_count"] == len(expected_ids)
                        else "descriptive_available_subset_incomplete_denominator"
                    ),
                    "mean": float(np.mean(finite)) if finite.size else None,
                    "median": float(np.median(finite)) if finite.size else None,
                    "q25": (
                        float(np.quantile(finite, 0.25, method="linear"))
                        if finite.size
                        else None
                    ),
                    "q75": (
                        float(np.quantile(finite, 0.75, method="linear"))
                        if finite.size
                        else None
                    ),
                    "minimum": float(np.min(finite)) if finite.size else None,
                    "maximum": float(np.max(finite)) if finite.size else None,
                }
            )
    return rows


def _primary_pair_rows(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    manifest_rows: Mapping[str, Mapping[str, Any]],
    expected_ids: Sequence[str],
    primary_summary: Mapping[str, Any],
    classification: Mapping[str, Any],
    guardrail_pass: Mapping[str, bool | None],
) -> list[dict[str, Any]]:
    baseline_rows = indexed.get(PRIMARY_BASELINE, {})
    candidate_rows = indexed.get(PRIMARY_CANDIDATE, {})
    result: list[dict[str, Any]] = []
    for trajectory_id in expected_ids:
        baseline_value = (
            _finite_or_none(baseline_rows[trajectory_id].get("position_rmse"))
            if trajectory_id in baseline_rows
            and baseline_rows[trajectory_id].get("completed") is not False
            else None
        )
        candidate_value = (
            _finite_or_none(candidate_rows[trajectory_id].get("position_rmse"))
            if trajectory_id in candidate_rows
            and candidate_rows[trajectory_id].get("completed") is not False
            else None
        )
        difference = (
            candidate_value - baseline_value
            if baseline_value is not None and candidate_value is not None
            else None
        )
        result.append(
            {
                "trajectory_id": trajectory_id,
                "family": manifest_rows[trajectory_id]["family"],
                "demand_stratum": manifest_rows[trajectory_id]["demand_stratum"],
                "baseline_method": PRIMARY_BASELINE,
                "candidate_method": PRIMARY_CANDIDATE,
                "baseline_position_rmse": baseline_value,
                "candidate_position_rmse": candidate_value,
                "candidate_minus_baseline_position_rmse": difference,
                "absolute_improvement": -difference if difference is not None else None,
                "harmful": difference > 0.0 if difference is not None else None,
                "paired_value_available": difference is not None,
                "formal_inference_status": primary_summary["status"],
                "required_trajectory_count": EXPECTED_TRAJECTORIES,
                "paired_trajectory_count": primary_summary["trajectory_count"],
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": PRIMARY_SEED,
                "confidence_level": CONFIDENCE_LEVEL,
                "overall_absolute_improvement": primary_summary[
                    "absolute_improvement"
                ],
                "overall_absolute_improvement_ci_low": primary_summary[
                    "absolute_improvement_ci_low"
                ],
                "overall_absolute_improvement_ci_high": primary_summary[
                    "absolute_improvement_ci_high"
                ],
                "overall_relative_improvement": primary_summary[
                    "relative_improvement"
                ],
                "overall_relative_improvement_ci_low": primary_summary[
                    "relative_improvement_ci_low"
                ],
                "overall_relative_improvement_ci_high": primary_summary[
                    "relative_improvement_ci_high"
                ],
                "cohen_dz": primary_summary["cohen_dz"],
                "unadjusted_p": primary_summary["unadjusted_p"],
                "primary_result_classification": classification["classification"],
                "confirmed_positive": classification["confirmed_positive"],
                "practically_material": classification["practically_material"],
                "strongly_material": classification["strongly_material"],
                "inconclusive": classification["inconclusive"],
                "confirmed_harmful": classification["confirmed_harmful"],
                "max_error_guardrail_pass": guardrail_pass[
                    "max_error_relative_worsening"
                ],
                "lag_guardrail_pass": guardrail_pass[
                    "lag_absolute_worsening"
                ],
                "negative_or_harmful_row_retained": (
                    difference is not None and difference >= 0.0
                ),
            }
        )
    return result


def _confidence_rows(
    primary: Mapping[str, Any],
    secondary: Sequence[Mapping[str, Any]],
    guardrails: Sequence[Mapping[str, Any]],
    subgroups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        {
            "comparison_id": primary["comparison_id"],
            "analysis_kind": "primary",
            "estimand": "absolute_improvement",
            "point_estimate": primary["absolute_improvement"],
            "ci_low": primary["absolute_improvement_ci_low"],
            "ci_high": primary["absolute_improvement_ci_high"],
            "confidence_level": CONFIDENCE_LEVEL,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": PRIMARY_SEED,
            "status": primary["status"],
        },
        {
            "comparison_id": primary["comparison_id"],
            "analysis_kind": "primary",
            "estimand": "relative_improvement",
            "point_estimate": primary["relative_improvement"],
            "ci_low": primary["relative_improvement_ci_low"],
            "ci_high": primary["relative_improvement_ci_high"],
            "confidence_level": CONFIDENCE_LEVEL,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": PRIMARY_SEED,
            "status": primary["status"],
        },
    ]
    for row in secondary:
        for estimand in ("absolute_improvement", "relative_improvement"):
            rows.append(
                {
                    "comparison_id": row["comparison_id"],
                    "analysis_kind": "secondary",
                    "estimand": estimand,
                    "point_estimate": row[estimand],
                    "ci_low": row[f"{estimand}_ci_low"],
                    "ci_high": row[f"{estimand}_ci_high"],
                    "confidence_level": CONFIDENCE_LEVEL,
                    "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                    "bootstrap_seed": row["bootstrap_seed"],
                    "status": row["status"],
                }
            )
    for row in guardrails:
        rows.append(
            {
                "comparison_id": row["comparison_id"],
                "analysis_kind": "guardrail",
                "estimand": "worsening",
                "point_estimate": row["worsening"],
                "ci_low": row["worsening_ci_low"],
                "ci_high": row["worsening_ci_high"],
                "confidence_level": CONFIDENCE_LEVEL,
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": row["bootstrap_seed"],
                "status": row["status"],
            }
        )
    for row in subgroups:
        for estimand in ("absolute_improvement", "relative_improvement"):
            rows.append(
                {
                    "comparison_id": row["comparison_id"],
                    "analysis_kind": "subgroup",
                    "estimand": estimand,
                    "point_estimate": row[estimand],
                    "ci_low": row[f"{estimand}_ci_low"],
                    "ci_high": row[f"{estimand}_ci_high"],
                    "confidence_level": CONFIDENCE_LEVEL,
                    "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                    "bootstrap_seed": row["bootstrap_seed"],
                    "status": row["status"],
                }
            )
    return rows


def _harm_rows(
    primary: Mapping[str, Any],
    secondary: Sequence[Mapping[str, Any]],
    subgroups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        {
            "comparison_id": primary["comparison_id"],
            "analysis_kind": "primary",
            "status": primary["harmful_status"],
            "harmful_count": primary["harmful_count"],
            "denominator": primary["harmful_denominator"],
            "evaluated_count": primary["harmful_evaluated_count"],
            "harmful_rate": primary["harmful_rate"],
            "wilson_ci_low": primary["harmful_rate_ci_low"],
            "wilson_ci_high": primary["harmful_rate_ci_high"],
        }
    ]
    for row in secondary:
        rows.append(
            {
                "comparison_id": row["comparison_id"],
                "analysis_kind": "secondary",
                "status": row["harmful_status"],
                "harmful_count": row["harmful_count"],
                "denominator": row["harmful_denominator"],
                "evaluated_count": row["harmful_evaluated_count"],
                "harmful_rate": row["harmful_rate"],
                "wilson_ci_low": row["harmful_rate_ci_low"],
                "wilson_ci_high": row["harmful_rate_ci_high"],
            }
        )
    for row in subgroups:
        rows.append(
            {
                "comparison_id": row["comparison_id"],
                "analysis_kind": "subgroup",
                "status": row["status"],
                "harmful_count": row["harmful_count"],
                "denominator": row["harmful_denominator"],
                "evaluated_count": (
                    row["harmful_denominator"]
                    if row["harmful_rate"] is not None
                    else 0
                ),
                "harmful_rate": row["harmful_rate"],
                "wilson_ci_low": row["harmful_rate_ci_low"],
                "wilson_ci_high": row["harmful_rate_ci_high"],
            }
        )
    return rows


def _worst_five(
    indexed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    manifest_rows: Mapping[str, Mapping[str, Any]],
    expected_ids: Sequence[str],
) -> list[dict[str, Any]]:
    availability = _comparison_availability(
        indexed,
        {
            "baseline_method": PRIMARY_BASELINE,
            "candidate_method": PRIMARY_CANDIDATE,
            "metric": "position_rmse",
        },
        expected_ids,
        require_harm_metric=False,
    )
    if not availability["metric_complete"]:
        return [
            {
                "rank": None,
                "trajectory_id": "__unavailable_incomplete_denominator__",
                "family": None,
                "demand_stratum": None,
                "baseline_position_rmse": None,
                "candidate_position_rmse": None,
                "candidate_minus_baseline_position_rmse": None,
                "absolute_improvement": None,
                "harmful": None,
                "selection_rule": "not_applied_incomplete_primary_denominator",
                "status": "unavailable_incomplete_denominator",
                "paired_trajectory_count": availability["paired_count"],
                "required_trajectory_count": len(expected_ids),
                "missing_pair_ids": "|".join(availability["missing_pair_ids"]),
            }
        ]
    baseline = _values(
        indexed, PRIMARY_BASELINE, "position_rmse", expected_ids
    )
    candidate = _values(
        indexed, PRIMARY_CANDIDATE, "position_rmse", expected_ids
    )
    candidate_minus_baseline = candidate - baseline
    order = sorted(
        range(len(expected_ids)),
        key=lambda index: (-candidate_minus_baseline[index], expected_ids[index]),
    )[:5]
    return [
        {
            "rank": rank,
            "trajectory_id": expected_ids[index],
            "family": manifest_rows[expected_ids[index]]["family"],
            "demand_stratum": manifest_rows[expected_ids[index]][
                "demand_stratum"
            ],
            "baseline_position_rmse": float(baseline[index]),
            "candidate_position_rmse": float(candidate[index]),
            "candidate_minus_baseline_position_rmse": float(
                candidate_minus_baseline[index]
            ),
            "absolute_improvement": float(-candidate_minus_baseline[index]),
            "harmful": bool(candidate_minus_baseline[index] > 0.0),
            "selection_rule": (
                "candidate_minus_baseline_position_rmse_descending_then_"
                "trajectory_id_ascending"
            ),
        }
        for rank, index in enumerate(order, start=1)
    ]


def _reconstruction_rows(
    retained_draws: Mapping[str, Mapping[str, np.ndarray]],
    expected_ids: Sequence[str],
    subgroup_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    subgroup_ids = {
        str(row["comparison_id"]): [
            # Input membership is fully reproducible from the locked manifest
            # and the predeclared dimension/value carried on the row.
            str(row["stratum_dimension"]),
            str(row["stratum_value"]),
        ]
        for row in subgroup_rows
    }
    seed_by_id: dict[str, int] = {
        "PVA_vs_P_position_RMSE": PRIMARY_SEED,
        "max_error_relative_worsening": GUARDRAIL_SEED,
        "lag_absolute_worsening": GUARDRAIL_SEED,
    }
    seed_by_id.update(
        {
            str(item["comparison_id"]): SECONDARY_BASE_SEED + index
            for index, item in enumerate(SECONDARY_COMPARISONS)
        }
    )
    seed_by_id.update(
        {
            str(row["comparison_id"]): int(row["bootstrap_seed"])
            for row in subgroup_rows
        }
    )
    rows: list[dict[str, Any]] = []
    for comparison_id in sorted(seed_by_id):
        draws = retained_draws.get(comparison_id)
        rows.append(
            {
                "comparison_id": comparison_id,
                "draws_available": draws is not None,
                "resamples": BOOTSTRAP_RESAMPLES,
                "seed": seed_by_id[comparison_id],
                "rng": "numpy.random.Generator(numpy.random.PCG64(seed))",
                "numpy_version": np.__version__,
                "input_order": "trajectory_id_lexicographic_ascending",
                "ordered_full_denominator_trajectory_ids_json": json.dumps(
                    list(expected_ids), separators=(",", ":")
                ),
                "subgroup_membership_json": (
                    json.dumps(subgroup_ids[comparison_id], separators=(",", ":"))
                    if comparison_id in subgroup_ids
                    else None
                ),
                "draw_algorithm": (
                    "for each resample draw n indices independently and uniformly "
                    "with replacement from integers [0,n)"
                ),
                "quantile_method": "linear",
                "quantiles_json": "[0.025,0.975]",
                "paired_unit": "whole_trajectory",
            }
        )
    return rows


def build_v4_statistical_tables(
    trajectory_metrics: Sequence[Mapping[str, Any]],
    locked_manifest: Mapping[str, Any],
    statistical_design: Mapping[str, Any],
    *,
    method_field: str = "method",
    trajectory_field: str = "trajectory_id",
) -> dict[str, list[dict[str, Any]]]:
    """Build all V4 statistical CSV rows without reading or writing files.

    The returned mapping is directly consumable by a CSV writer.  Incomplete
    formal pairs produce explicit unavailable rows with the full required
    denominator and missing IDs; no available subset is used for inference.
    """

    validate_v4_statistical_design(statistical_design)
    expected_ids, manifest_rows = _manifest_test_rows(
        locked_manifest, statistical_design
    )
    indexed = _index_records(
        trajectory_metrics,
        expected_ids,
        method_field=method_field,
        trajectory_field=trajectory_field,
    )

    primary_definition = {
        "comparison_id": "PVA_vs_P_position_RMSE",
        "baseline_method": PRIMARY_BASELINE,
        "candidate_method": PRIMARY_CANDIDATE,
        "metric": "position_rmse",
        "contextual_only": False,
    }
    primary_availability = _comparison_availability(
        indexed, primary_definition, expected_ids
    )
    retained_draws: dict[str, dict[str, np.ndarray]] = {}
    if primary_availability["complete"]:
        primary_summary, draws = _available_comparison_row(
            primary_definition,
            indexed,
            expected_ids,
            seed=PRIMARY_SEED,
        )
        retained_draws[primary_definition["comparison_id"]] = draws
    else:
        primary_summary = _unavailable_comparison_row(
            primary_definition,
            primary_availability,
            seed=PRIMARY_SEED,
        )

    classification = classify_primary_result(
        primary_summary["relative_improvement"],
        primary_summary["relative_improvement_ci_low"],
        primary_summary["relative_improvement_ci_high"],
    )
    primary_summary.update(classification)
    primary_summary["protocol_result_status"] = (
        "complete_negative"
        if classification["classification"]
        in {"inconclusive", "confirmed_harmful"}
        else (
            "unavailable_incomplete_denominator"
            if classification["classification"]
            == "unavailable_incomplete_denominator"
            else "complete_confirmatory"
        )
    )

    guardrail_rows, guardrail_pass, guardrail_draws = _guardrail_rows(
        indexed, expected_ids
    )
    retained_draws.update(guardrail_draws)
    primary_summary["max_error_guardrail_pass"] = guardrail_pass[
        "max_error_relative_worsening"
    ]
    primary_summary["lag_guardrail_pass"] = guardrail_pass[
        "lag_absolute_worsening"
    ]
    primary_summary["without_material_degradation_claim_permitted"] = bool(
        classification["confirmed_positive"]
        and guardrail_pass["max_error_relative_worsening"] is True
        and guardrail_pass["lag_absolute_worsening"] is True
    )

    secondary_rows: list[dict[str, Any]] = []
    for index, definition in enumerate(SECONDARY_COMPARISONS):
        seed = SECONDARY_BASE_SEED + index
        availability = _comparison_availability(
            indexed, definition, expected_ids
        )
        if availability["complete"]:
            row, draws = _available_comparison_row(
                definition, indexed, expected_ids, seed=seed
            )
            retained_draws[str(definition["comparison_id"])] = draws
        else:
            row = _unavailable_comparison_row(
                definition, availability, seed=seed
            )
        secondary_rows.append(row)
    adjusted = holm_adjust_v4(
        {
            str(row["comparison_id"]): (
                float(row["unadjusted_p"])
                if row["unadjusted_p"] is not None
                else None
            )
            for row in secondary_rows
        }
    )
    for row in secondary_rows:
        row["holm_family_size"] = 5
        row["holm_adjusted_p"] = adjusted[str(row["comparison_id"])]
        row["reject_holm_alpha_0_05"] = (
            row["holm_adjusted_p"] is not None
            and row["holm_adjusted_p"] < 0.05
        )

    subgroup_rows, subgroup_draws = _subgroup_rows(
        indexed,
        manifest_rows,
        expected_ids,
        overall_available=bool(primary_availability["complete"]),
    )
    retained_draws.update(subgroup_draws)
    _add_heterogeneity(subgroup_rows)

    primary_pair_rows = _primary_pair_rows(
        indexed,
        manifest_rows,
        expected_ids,
        primary_summary,
        classification,
        guardrail_pass,
    )
    family_rows = [
        dict(row)
        for row in subgroup_rows
        if row["stratum_dimension"] == "reference_family"
    ]
    demand_rows = [
        dict(row)
        for row in subgroup_rows
        if row["stratum_dimension"] == "demand_stratum"
    ]
    acceleration_rows = [
        dict(row)
        for row in subgroup_rows
        if row["stratum_dimension"] == "acceleration_active"
    ]
    metrics_rows = [dict(row) for row in trajectory_metrics]
    tables = {
        "metrics_by_trajectory.csv": metrics_rows,
        "summary_metrics.csv": _summary_metrics(indexed, expected_ids),
        "primary_comparison.csv": primary_pair_rows,
        "secondary_comparisons.csv": secondary_rows,
        "confidence_intervals.csv": _confidence_rows(
            primary_summary,
            secondary_rows,
            guardrail_rows,
            subgroup_rows,
        ),
        "stratified_comparisons.csv": subgroup_rows,
        "family_effects.csv": family_rows,
        "demand_stratum_effects.csv": demand_rows,
        "acceleration_active_effect.csv": acceleration_rows,
        "harmful_trajectory_rate.csv": _harm_rows(
            primary_summary, secondary_rows, subgroup_rows
        ),
        "worst_five_trajectories.csv": _worst_five(
            indexed, manifest_rows, expected_ids
        ),
        "bootstrap_reconstruction.csv": _reconstruction_rows(
            retained_draws, expected_ids, subgroup_rows
        ),
    }
    _require(
        tuple(tables) == CSV_TABLE_NAMES,
        "internal V4 statistical table contract changed",
    )
    return tables


def reconstruct_v4_bootstrap_draws(
    trajectory_metrics: Sequence[Mapping[str, Any]],
    locked_manifest: Mapping[str, Any],
    statistical_design: Mapping[str, Any],
    *,
    method_field: str = "method",
    trajectory_field: str = "trajectory_id",
) -> dict[str, dict[str, list[float]]]:
    """Rebuild every available formal V4 draw from locked inputs and seeds."""

    validate_v4_statistical_design(statistical_design)
    expected_ids, manifest_rows = _manifest_test_rows(
        locked_manifest, statistical_design
    )
    indexed = _index_records(
        trajectory_metrics,
        expected_ids,
        method_field=method_field,
        trajectory_field=trajectory_field,
    )
    definitions: list[tuple[str, str, str, str, int, list[str]]] = [
        (
            "PVA_vs_P_position_RMSE",
            PRIMARY_BASELINE,
            PRIMARY_CANDIDATE,
            "position_rmse",
            PRIMARY_SEED,
            list(expected_ids),
        ),
        (
            "max_error_relative_worsening",
            PRIMARY_BASELINE,
            PRIMARY_CANDIDATE,
            "position_max_abs_error",
            GUARDRAIL_SEED,
            list(expected_ids),
        ),
        (
            "lag_absolute_worsening",
            PRIMARY_BASELINE,
            PRIMARY_CANDIDATE,
            "lag_s",
            GUARDRAIL_SEED,
            list(expected_ids),
        ),
    ]
    definitions.extend(
        (
            str(item["comparison_id"]),
            str(item["baseline_method"]),
            str(item["candidate_method"]),
            str(item["metric"]),
            SECONDARY_BASE_SEED + index,
            list(expected_ids),
        )
        for index, item in enumerate(SECONDARY_COMPARISONS)
    )
    subgroup_definitions = _subgroup_definitions(manifest_rows, expected_ids)
    seed_by_key = {
        (dimension, value): SUBGROUP_BASE_SEED + ordinal
        for ordinal, (dimension, value, _) in enumerate(
            sorted(subgroup_definitions, key=lambda item: (item[0], item[1]))
        )
    }
    definitions.extend(
        (
            f"subgroup:{dimension}:{value}",
            PRIMARY_BASELINE,
            PRIMARY_CANDIDATE,
            "position_rmse",
            seed_by_key[(dimension, value)],
            trajectory_ids,
        )
        for dimension, value, trajectory_ids in subgroup_definitions
    )
    result: dict[str, dict[str, list[float]]] = {}
    for comparison_id, baseline_method, candidate_method, metric, seed, ids in definitions:
        availability = _comparison_availability(
            indexed,
            {
                "baseline_method": baseline_method,
                "candidate_method": candidate_method,
                "metric": metric,
            },
            ids,
            require_harm_metric=False,
        )
        if not availability["metric_complete"]:
            continue
        draws = _bootstrap_draws(
            _values(indexed, baseline_method, metric, ids),
            _values(indexed, candidate_method, metric, ids),
            seed=seed,
        )
        result[comparison_id] = {
            name: values.tolist() for name, values in draws.items()
        }
    return result


def write_v4_bootstrap_draws_csv(
    path: str | Path,
    draws: Mapping[str, Mapping[str, Sequence[float]]],
) -> None:
    """Save already reconstructed draws without changing their ordering."""

    target = Path(path)
    fieldnames = (
        "comparison_id",
        "draw_index",
        "baseline_mean",
        "candidate_mean",
        "absolute_difference",
        "absolute_improvement",
        "relative_difference",
        "relative_improvement",
    )
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for comparison_id in sorted(draws):
            comparison_draws = draws[comparison_id]
            lengths = {
                len(comparison_draws[field])
                for field in fieldnames[2:]
                if field in comparison_draws
            }
            _require(
                lengths == {BOOTSTRAP_RESAMPLES},
                f"{comparison_id} does not contain exactly 10000 draws",
            )
            for draw_index in range(BOOTSTRAP_RESAMPLES):
                writer.writerow(
                    {
                        "comparison_id": comparison_id,
                        "draw_index": draw_index,
                        **{
                            field: comparison_draws[field][draw_index]
                            for field in fieldnames[2:]
                        },
                    }
                )


def _decode_csv_value(value: str) -> Any:
    stripped = value.strip()
    if stripped == "":
        return None
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        number = float(stripped)
    except ValueError:
        return value
    if not math.isfinite(number):
        return None
    return number


def _read_metrics_csv(path: Path) -> list[dict[str, Any]]:
    _require(path.is_file(), f"locked trajectory metrics do not exist: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(reader.fieldnames is not None, f"CSV has no header: {path}")
        _require(
            len(reader.fieldnames) == len(set(reader.fieldnames)),
            f"CSV has duplicate columns: {path}",
        )
        rows = [
            {field: _decode_csv_value(value) for field, value in row.items()}
            for row in reader
        ]
    _require(bool(rows), f"CSV has no trajectory rows: {path}")
    return rows


def _csv_columns(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                columns.append(field)
                seen.add(field)
    _require(bool(columns), "cannot write a CSV without columns")
    return columns


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, np.generic):
        return value.item()
    return value


def _write_table_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _require(bool(rows), f"refusing to write empty statistical table {path.name}")
    columns = _csv_columns(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: _csv_cell(row.get(field)) for field in columns}
            )


def analyze_v4_confirmation(
    *,
    locked_test_root: str | Path,
    oracle_root: str | Path | None,
    results_root: str | Path,
    manifest_path: str | Path,
    statistical_design_path: str | Path,
) -> Mapping[str, Any]:
    """Read a promoted locked-test bundle and atomically write V4 statistics.

    ``oracle_root`` is accepted as part of the runner's stable hook but is not
    mixed into the confirmatory or Holm families.  Oracle diagnostics remain a
    separate, explicitly noncausal evidence block.
    """

    locked_root = Path(locked_test_root).resolve()
    output_root = Path(results_root).resolve()
    manifest_file = Path(manifest_path).resolve()
    design_file = Path(statistical_design_path).resolve()
    if oracle_root is not None:
        # Resolving records provenance without consuming oracle outcomes makes
        # the exclusion from confirmatory inference explicit.
        Path(oracle_root).resolve()
    _require(manifest_file.is_file(), f"manifest does not exist: {manifest_file}")
    _require(
        design_file.is_file(),
        f"statistical design does not exist: {design_file}",
    )
    with manifest_file.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    with design_file.open("r", encoding="utf-8") as handle:
        design = json.load(handle)
    _require(isinstance(manifest, Mapping), "manifest JSON root must be an object")
    _require(
        isinstance(design, Mapping), "statistical-design JSON root must be an object"
    )
    records = _read_metrics_csv(locked_root / "metrics_by_trajectory.csv")
    tables = build_v4_statistical_tables(records, manifest, design)

    statistics_root = output_root / "statistics"
    _require(
        not statistics_root.exists(),
        f"refusing to overwrite existing V4 statistics: {statistics_root}",
    )
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".v4-statistics-", dir=output_root)
    ).resolve()
    try:
        for filename, rows in tables.items():
            _write_table_csv(staging / filename, rows)
        staging.rename(statistics_root)
    except Exception:
        for child in staging.iterdir():
            if child.is_file():
                child.unlink()
        staging.rmdir()
        raise

    primary_row = tables["primary_comparison.csv"][0]
    secondary_by_id = {
        str(row["comparison_id"]): row
        for row in tables["secondary_comparisons.csv"]
    }
    return {
        "statistics_root": str(statistics_root),
        "statistical_table_paths": {
            filename: str(statistics_root / filename) for filename in tables
        },
        "primary_result_classification": primary_row[
            "primary_result_classification"
        ],
        "protocol_result_status": (
            "complete_negative"
            if primary_row["primary_result_classification"]
            in {"inconclusive", "confirmed_harmful"}
            else (
                "unavailable_incomplete_denominator"
                if primary_row["primary_result_classification"]
                == "unavailable_incomplete_denominator"
                else "complete_confirmatory"
            )
        ),
        "paired_denominator": primary_row["paired_trajectory_count"],
        "required_denominator": EXPECTED_TRAJECTORIES,
        "max_error_guardrail_pass": primary_row["max_error_guardrail_pass"],
        "lag_guardrail_pass": primary_row["lag_guardrail_pass"],
        "ordinary_ruckig_secondary_status": secondary_by_id["S5"]["status"],
        "oracle_excluded_from_confirmatory_statistics": True,
    }
