"""Independent recomputation audit for V4 whole-trajectory statistics.

This module intentionally does not import :mod:`otg_lab.v4_statistics`.  It
reimplements the locked estimands and bootstrap directly from the promoted raw
trajectory metrics, then compares them with the published statistical tables.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

from .artifacts import ArtifactValidationError

_P = "one_step_governed_p_direct"
_PV = "one_step_governed_pv_direct"
_PVA = "one_step_governed_pva_direct"
_N = 120
_B = 10_000


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ArtifactValidationError(f"independent statistics input is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ArtifactValidationError(f"independent statistics CSV has no header: {path}")
        return list(reader)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    if value is None or str(value).strip().lower() in {"", "none", "null", "nan"}:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _boolean(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    if text in {"", "none", "null"}:
        return None
    raise ArtifactValidationError(f"invalid boolean in published statistics: {value!r}")


def _same(observed: Any, expected: Any, label: str, *, atol: float = 2e-12) -> None:
    observed_number = _number(observed)
    if expected is None:
        if observed_number is not None:
            raise ArtifactValidationError(
                f"independent statistics mismatch for {label}: expected null, "
                f"observed {observed!r}"
            )
        return
    if observed_number is None or not math.isclose(
        observed_number, float(expected), rel_tol=2e-11, abs_tol=atol
    ):
        raise ArtifactValidationError(
            f"independent statistics mismatch for {label}: "
            f"expected {expected!r}, observed {observed!r}"
        )


def _draws(baseline: np.ndarray, candidate: np.ndarray, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.Generator(np.random.PCG64(seed))
    indices = rng.integers(0, baseline.size, size=(_B, baseline.size))
    baseline_means = np.mean(baseline[indices], axis=1)
    candidate_means = np.mean(candidate[indices], axis=1)
    improvement = baseline_means - candidate_means
    relative = improvement / np.abs(baseline_means)
    return {"improvement": improvement, "relative": relative}


def _ci(values: np.ndarray) -> tuple[float, float]:
    return (
        float(np.quantile(values, 0.025, method="linear")),
        float(np.quantile(values, 0.975, method="linear")),
    )


def _classification(point: float, low: float, high: float) -> str:
    if low >= 0.05:
        return "strongly_material"
    if low > 0.0 and point >= 0.05:
        return "practically_material"
    if low > 0.0:
        return "confirmed_positive"
    if high < 0.0:
        return "confirmed_harmful"
    return "inconclusive"


def _indexed_metrics(
    rows: Sequence[Mapping[str, str]], expected: set[str]
) -> dict[str, dict[str, Mapping[str, str]]]:
    indexed: dict[str, dict[str, Mapping[str, str]]] = {}
    for row in rows:
        method = str(row.get("method") or row.get("method_id") or "")
        trajectory_id = str(row.get("trajectory_id") or "")
        if not method or trajectory_id not in expected:
            raise ArtifactValidationError(
                "raw statistical metrics contain an unknown method/trajectory identity"
            )
        if trajectory_id in indexed.setdefault(method, {}):
            raise ArtifactValidationError(
                f"duplicate raw whole-trajectory metric: {method}/{trajectory_id}"
            )
        indexed[method][trajectory_id] = row
    return indexed


def _unique_by(
    rows: Sequence[Mapping[str, str]],
    key_fields: Sequence[str],
    *,
    expected_keys: set[tuple[str, ...]] | None = None,
    label: str,
) -> dict[tuple[str, ...], Mapping[str, str]]:
    result: dict[tuple[str, ...], Mapping[str, str]] = {}
    for row in rows:
        key = tuple(str(row.get(field, "")) for field in key_fields)
        if any(not value for value in key) or key in result:
            raise ArtifactValidationError(f"{label} has a missing/duplicate key: {key}")
        result[key] = row
    if expected_keys is not None and set(result) != expected_keys:
        raise ArtifactValidationError(
            f"{label} family differs: missing={sorted(expected_keys - set(result))}, "
            f"extra={sorted(set(result) - expected_keys)}"
        )
    return result


def _available(row: Mapping[str, str], metric: str) -> bool:
    return _boolean(row.get("completed", "true")) is not False and _number(row.get(metric)) is not None


def _values(
    indexed: Mapping[str, Mapping[str, Mapping[str, str]]],
    method: str,
    metric: str,
    ids: Sequence[str],
) -> np.ndarray | None:
    rows = indexed.get(method, {})
    if any(identity not in rows or not _available(rows[identity], metric) for identity in ids):
        return None
    return np.asarray([_number(rows[identity][metric]) for identity in ids], dtype=float)


def _paired_p(improvement: np.ndarray) -> float:
    standard_deviation = float(np.std(improvement, ddof=1))
    if standard_deviation <= np.finfo(float).eps:
        return 1.0 if bool(np.all(improvement == 0.0)) else 0.0
    statistic = float(np.mean(improvement)) / (
        standard_deviation / math.sqrt(improvement.size)
    )
    return float(2.0 * stats.t.sf(abs(statistic), improvement.size - 1))


def _wilson(successes: int, denominator: int) -> tuple[float, float]:
    z = 1.959963984540054
    proportion = successes / denominator
    scale = 1.0 + z * z / denominator
    center = (proportion + z * z / (2.0 * denominator)) / scale
    radius = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / denominator
            + z * z / (4.0 * denominator * denominator)
        )
        / scale
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def _holm(p_values: Mapping[str, float | None]) -> dict[str, float | None]:
    ordered = sorted(
        ((key, 1.0 if value is None else value) for key, value in p_values.items()),
        key=lambda item: (item[1], item[0]),
    )
    result: dict[str, float | None] = {}
    running = 0.0
    for rank, (key, value) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * value))
        result[key] = running
    for key, value in p_values.items():
        if value is None:
            result[key] = None
    return result


def audit_v4_statistics_independently(
    *,
    raw_metrics_path: str | Path,
    published_statistics_root: str | Path,
    manifest_path: str | Path,
    statistical_design_path: str | Path,
) -> dict[str, Any]:
    """Recompute every inferential family used by the V4 result claim."""

    raw_path = Path(raw_metrics_path).resolve()
    published = Path(published_statistics_root).resolve()
    manifest_file = Path(manifest_path).resolve()
    design_file = Path(statistical_design_path).resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    design = json.loads(design_file.read_text(encoding="utf-8"))
    manifest_rows = {
        str(row["trajectory_id"]): row
        for row in manifest["trajectories"]
        if row["split"] == "test"
    }
    ids = sorted(manifest_rows)
    if len(ids) != _N:
        raise ArtifactValidationError(
            f"independent statistics expected {_N} locked IDs, observed {len(ids)}"
        )
    published_metrics = published / "metrics_by_trajectory.csv"
    if _sha256(raw_path) != _sha256(published_metrics):
        raise ArtifactValidationError(
            "published metrics_by_trajectory.csv is not byte-identical to raw metrics"
        )
    indexed = _indexed_metrics(_rows(raw_path), set(ids))
    primary_rows = _rows(published / "primary_comparison.csv")
    if len(primary_rows) != _N or {row["trajectory_id"] for row in primary_rows} != set(ids):
        raise ArtifactValidationError("published primary comparison denominator is not exact")

    baseline = _values(indexed, _P, "position_rmse", ids)
    candidate = _values(indexed, _PVA, "position_rmse", ids)
    complete = baseline is not None and candidate is not None
    primary_by_id = {row["trajectory_id"]: row for row in primary_rows}
    paired_count = 0
    for identity in ids:
        raw_baseline = indexed.get(_P, {}).get(identity)
        raw_candidate = indexed.get(_PVA, {}).get(identity)
        b = (
            _number(raw_baseline.get("position_rmse"))
            if raw_baseline is not None and _available(raw_baseline, "position_rmse")
            else None
        )
        c = (
            _number(raw_candidate.get("position_rmse"))
            if raw_candidate is not None and _available(raw_candidate, "position_rmse")
            else None
        )
        row = primary_by_id[identity]
        expected_identity = {
            "family": manifest_rows[identity]["family"],
            "demand_stratum": manifest_rows[identity]["demand_stratum"],
            "baseline_method": _P,
            "candidate_method": _PVA,
        }
        if any(row.get(field) != expected for field, expected in expected_identity.items()):
            raise ArtifactValidationError(f"primary identity differs for {identity}")
        _same(row.get("baseline_position_rmse"), b, f"{identity}:baseline")
        _same(row.get("candidate_position_rmse"), c, f"{identity}:candidate")
        difference = None if b is None or c is None else c - b
        _same(
            row.get("candidate_minus_baseline_position_rmse"),
            difference,
            f"{identity}:paired_difference",
        )
        observed_available = _boolean(row.get("paired_value_available"))
        if observed_available is not (difference is not None):
            raise ArtifactValidationError(f"paired availability differs for {identity}")
        _same(
            row.get("absolute_improvement"),
            None if difference is None else -difference,
            f"{identity}:absolute_improvement",
        )
        if _boolean(row.get("harmful")) is not (
            None if difference is None else difference > 0.0
        ):
            raise ArtifactValidationError(f"harmful flag differs for {identity}")
        if _boolean(row.get("negative_or_harmful_row_retained")) is not (
            False if difference is None else difference >= 0.0
        ):
            raise ArtifactValidationError(
                f"negative/harmful retention flag differs for {identity}"
            )
        if (
            int(float(row["required_trajectory_count"])) != _N
            or int(float(row["bootstrap_resamples"])) != _B
            or int(float(row["bootstrap_seed"]))
            != int(design["primary"]["bootstrap"]["seed"])
            or not math.isclose(float(row["confidence_level"]), 0.95)
        ):
            raise ArtifactValidationError(f"primary locked design fields differ for {identity}")
        paired_count += int(difference is not None)
    if any(int(float(row["paired_trajectory_count"])) != paired_count for row in primary_rows):
        raise ArtifactValidationError("published paired count differs from raw completion")

    primary_seed = int(design["primary"]["bootstrap"]["seed"])
    classification = "unavailable_incomplete_denominator"
    if complete:
        assert baseline is not None and candidate is not None
        primary_draws = _draws(baseline, candidate, primary_seed)
        absolute = float(np.mean(baseline) - np.mean(candidate))
        relative = absolute / float(np.mean(baseline))
        absolute_ci = _ci(primary_draws["improvement"])
        relative_ci = _ci(primary_draws["relative"])
        classification = _classification(relative, *relative_ci)
        expected_flags = {
            "confirmed_positive": relative_ci[0] > 0.0,
            "practically_material": relative_ci[0] > 0.0 and relative >= 0.05,
            "strongly_material": relative_ci[0] >= 0.05,
            "inconclusive": relative_ci[0] <= 0.0 and relative_ci[1] >= 0.0,
            "confirmed_harmful": relative_ci[1] < 0.0,
        }
        primary_improvement = baseline - candidate
        primary_standard_deviation = float(np.std(primary_improvement, ddof=1))
        primary_zero_variance = bool(
            primary_standard_deviation <= np.finfo(float).eps
        )
        primary_dz = (
            None
            if primary_zero_variance
            else float(np.mean(primary_improvement)) / primary_standard_deviation
        )
        primary_p = _paired_p(primary_improvement)
        for row in primary_rows:
            _same(row.get("overall_absolute_improvement"), absolute, "primary:absolute")
            _same(row.get("overall_absolute_improvement_ci_low"), absolute_ci[0], "primary:absolute_ci_low")
            _same(row.get("overall_absolute_improvement_ci_high"), absolute_ci[1], "primary:absolute_ci_high")
            _same(row.get("overall_relative_improvement"), relative, "primary:relative")
            _same(row.get("overall_relative_improvement_ci_low"), relative_ci[0], "primary:relative_ci_low")
            _same(row.get("overall_relative_improvement_ci_high"), relative_ci[1], "primary:relative_ci_high")
            _same(row.get("cohen_dz"), primary_dz, "primary:cohen_dz")
            _same(row.get("unadjusted_p"), primary_p, "primary:paired_p")
            if row.get("primary_result_classification") != classification:
                raise ArtifactValidationError("independent primary classification differs")
            if row.get("formal_inference_status") != "available":
                raise ArtifactValidationError("complete primary is not marked available")
            for field, expected in expected_flags.items():
                if _boolean(row.get(field)) is not expected:
                    raise ArtifactValidationError(f"primary classification flag {field} differs")

        harmful = int(np.count_nonzero(candidate > baseline))
        harm_row = next(
            row
            for row in _rows(published / "harmful_trajectory_rate.csv")
            if row["comparison_id"] == design["primary"]["comparison_id"]
        )
        if int(float(harm_row["harmful_count"])) != harmful:
            raise ArtifactValidationError("independent primary harmful count differs")
        _same(
            harm_row.get("harmful_rate"),
            harmful / _N,
            "primary:harmful_rate",
        )
        primary_wilson = _wilson(harmful, _N)
        _same(harm_row.get("wilson_ci_low"), primary_wilson[0], "primary:harm_ci_low")
        _same(harm_row.get("wilson_ci_high"), primary_wilson[1], "primary:harm_ci_high")
        worst_indices = sorted(
                range(_N),
                key=lambda index: (-(candidate[index] - baseline[index]), ids[index]),
            )[:5]
        worst_rows = _rows(published / "worst_five_trajectories.csv")
        if len(worst_rows) != 5:
            raise ArtifactValidationError("independent worst-five row count differs")
        for rank, index in enumerate(worst_indices, start=1):
            row = worst_rows[rank - 1]
            identity = ids[index]
            difference = float(candidate[index] - baseline[index])
            if (
                int(float(row["rank"])) != rank
                or row["trajectory_id"] != identity
                or row["family"] != manifest_rows[identity]["family"]
                or row["demand_stratum"]
                != manifest_rows[identity]["demand_stratum"]
                or _boolean(row.get("harmful")) is not (difference > 0.0)
                or row.get("selection_rule")
                != (
                    "candidate_minus_baseline_position_rmse_descending_then_"
                    "trajectory_id_ascending"
                )
            ):
                raise ArtifactValidationError(
                    f"independent worst-five identity/label differs at rank {rank}"
                )
            _same(row.get("baseline_position_rmse"), baseline[index], f"worst:{rank}:baseline")
            _same(row.get("candidate_position_rmse"), candidate[index], f"worst:{rank}:candidate")
            _same(row.get("candidate_minus_baseline_position_rmse"), difference, f"worst:{rank}:difference")
            _same(row.get("absolute_improvement"), -difference, f"worst:{rank}:improvement")
    else:
        if any(
            row.get("primary_result_classification")
            != "unavailable_incomplete_denominator"
            for row in primary_rows
        ):
            raise ArtifactValidationError("incomplete primary denominator has a result claim")
        sentinel = _rows(published / "worst_five_trajectories.csv")
        if len(sentinel) != 1 or sentinel[0].get("status") != "unavailable_incomplete_denominator":
            raise ArtifactValidationError("incomplete primary worst-case sentinel is absent")
        for row in primary_rows:
            if row.get("formal_inference_status") != "unavailable_incomplete_denominator":
                raise ArtifactValidationError("incomplete primary row has inference status")
            for field in (
                "overall_absolute_improvement",
                "overall_absolute_improvement_ci_low",
                "overall_absolute_improvement_ci_high",
                "overall_relative_improvement",
                "overall_relative_improvement_ci_low",
                "overall_relative_improvement_ci_high",
            ):
                _same(row.get(field), None, f"incomplete_primary:{field}")
            _same(row.get("cohen_dz"), None, "incomplete_primary:cohen_dz")
            _same(row.get("unadjusted_p"), None, "incomplete_primary:paired_p")
            for field in (
                "confirmed_positive",
                "practically_material",
                "strongly_material",
                "inconclusive",
                "confirmed_harmful",
            ):
                if _boolean(row.get(field)) is not False:
                    raise ArtifactValidationError(
                        f"incomplete primary classification flag {field} differs"
                    )
            if (
                _boolean(row.get("max_error_guardrail_pass")) is not None
                or _boolean(row.get("lag_guardrail_pass")) is not None
            ):
                raise ArtifactValidationError("incomplete primary has guardrail claim")

    expected_secondary_ids = {
        (str(comparison["id"]),)
        for comparison in design["secondary"]["comparison_family"]
    }
    secondary_keyed = _unique_by(
        _rows(published / "secondary_comparisons.csv"),
        ("comparison_id",),
        expected_keys=expected_secondary_ids,
        label="secondary comparisons",
    )
    secondary_rows = {
        key[0]: row for key, row in secondary_keyed.items()
    }
    secondary_p: dict[str, float | None] = {}
    secondary_available: dict[str, bool] = {}
    for ordinal, comparison in enumerate(design["secondary"]["comparison_family"]):
        comparison_id = comparison["id"]
        row = secondary_rows.get(comparison_id)
        if row is None:
            raise ArtifactValidationError(f"missing secondary comparison {comparison_id}")
        b = _values(indexed, comparison["baseline_method"], comparison["metric"], ids)
        c = _values(indexed, comparison["candidate_method"], comparison["metric"], ids)
        if b is None or c is None:
            secondary_p[comparison_id] = None
            secondary_available[comparison_id] = False
            if row.get("status") != "unavailable_incomplete_denominator":
                raise ArtifactValidationError(f"incomplete secondary {comparison_id} has inference")
            baseline_rows = indexed.get(comparison["baseline_method"], {})
            candidate_rows = indexed.get(comparison["candidate_method"], {})
            baseline_available = {
                identity
                for identity in ids
                if identity in baseline_rows
                and _available(baseline_rows[identity], comparison["metric"])
            }
            candidate_available = {
                identity
                for identity in ids
                if identity in candidate_rows
                and _available(candidate_rows[identity], comparison["metric"])
            }
            paired = baseline_available & candidate_available
            harm_baseline = {
                identity
                for identity in ids
                if identity in baseline_rows
                and _available(baseline_rows[identity], "position_rmse")
            }
            harm_candidate = {
                identity
                for identity in ids
                if identity in candidate_rows
                and _available(candidate_rows[identity], "position_rmse")
            }
            missing = sorted(
                (set(ids) - paired) | (set(ids) - (harm_baseline & harm_candidate))
            )
            expected_counts = {
                "trajectory_count": len(paired),
                "required_trajectory_count": _N,
                "missing_trajectory_count": len(missing),
                "baseline_attempted_count": sum(
                    identity in baseline_rows for identity in ids
                ),
                "baseline_completed_count": len(baseline_available),
                "candidate_attempted_count": sum(
                    identity in candidate_rows for identity in ids
                ),
                "candidate_completed_count": len(candidate_available),
                "harmful_denominator": _N,
                "harmful_evaluated_count": len(harm_baseline & harm_candidate),
            }
            for field, expected in expected_counts.items():
                if int(float(row[field])) != expected:
                    raise ArtifactValidationError(
                        f"{comparison_id} incomplete {field} differs"
                    )
            if json.loads(row["missing_trajectory_ids_json"]) != missing:
                raise ArtifactValidationError(
                    f"{comparison_id} incomplete missing IDs differ"
                )
            for field in (
                "baseline_mean",
                "candidate_mean",
                "absolute_difference",
                "absolute_difference_ci_low",
                "absolute_difference_ci_high",
                "absolute_improvement",
                "absolute_improvement_ci_low",
                "absolute_improvement_ci_high",
                "relative_difference",
                "relative_difference_ci_low",
                "relative_difference_ci_high",
                "relative_improvement",
                "relative_improvement_ci_low",
                "relative_improvement_ci_high",
                "cohen_dz",
                "cohen_dz_zero_variance",
                "unadjusted_p",
                "holm_adjusted_p",
                "harmful_count",
                "harmful_rate",
                "harmful_rate_ci_low",
                "harmful_rate_ci_high",
            ):
                _same(row.get(field), None, f"{comparison_id}:incomplete:{field}")
            if (
                _boolean(row.get("formal_inference_available")) is not False
                or _boolean(row.get("relative_defined")) is not False
                or row.get("harmful_status")
                != "unavailable_incomplete_denominator"
            ):
                raise ArtifactValidationError(
                    f"{comparison_id} incomplete flags differ"
                )
            continue
        secondary_available[comparison_id] = True
        improvement = b - c
        draws = _draws(b, c, int(design["secondary"]["bootstrap"]["base_seed"]) + ordinal)
        absolute = float(np.mean(improvement))
        relative = absolute / abs(float(np.mean(b)))
        absolute_ci = _ci(draws["improvement"])
        relative_ci = _ci(draws["relative"])
        difference_ci = _ci(-draws["improvement"])
        relative_difference_ci = _ci(-draws["relative"])
        expected_identity = {
            "status": "available",
            "formal_inference_available": True,
            "contextual_only": bool(comparison.get("contextual_only", False)),
            "metric": comparison["metric"],
            "baseline_method": comparison["baseline_method"],
            "candidate_method": comparison["candidate_method"],
            "trajectory_count": _N,
            "required_trajectory_count": _N,
            "excluded_trajectory_count": 0,
            "bootstrap_resamples": _B,
            "bootstrap_seed": int(design["secondary"]["bootstrap"]["base_seed"])
            + ordinal,
            "confidence_level": 0.95,
        }
        for field, expected in expected_identity.items():
            observed = row.get(field)
            if isinstance(expected, bool):
                if _boolean(observed) is not expected:
                    raise ArtifactValidationError(f"{comparison_id}:{field} differs")
            elif isinstance(expected, (int, float)):
                _same(observed, expected, f"{comparison_id}:{field}")
            elif observed != expected:
                raise ArtifactValidationError(f"{comparison_id}:{field} differs")
        _same(row.get("baseline_mean"), float(np.mean(b)), f"{comparison_id}:baseline_mean")
        _same(row.get("candidate_mean"), float(np.mean(c)), f"{comparison_id}:candidate_mean")
        _same(row.get("absolute_difference"), -absolute, f"{comparison_id}:absolute_difference")
        _same(row.get("absolute_difference_ci_low"), difference_ci[0], f"{comparison_id}:absolute_difference_ci_low")
        _same(row.get("absolute_difference_ci_high"), difference_ci[1], f"{comparison_id}:absolute_difference_ci_high")
        _same(row.get("relative_difference"), -relative, f"{comparison_id}:relative_difference")
        _same(row.get("relative_difference_ci_low"), relative_difference_ci[0], f"{comparison_id}:relative_difference_ci_low")
        _same(row.get("relative_difference_ci_high"), relative_difference_ci[1], f"{comparison_id}:relative_difference_ci_high")
        _same(row.get("absolute_improvement"), absolute, f"{comparison_id}:absolute")
        _same(row.get("absolute_improvement_ci_low"), absolute_ci[0], f"{comparison_id}:absolute_ci_low")
        _same(row.get("absolute_improvement_ci_high"), absolute_ci[1], f"{comparison_id}:absolute_ci_high")
        _same(row.get("relative_improvement"), relative, f"{comparison_id}:relative")
        _same(row.get("relative_improvement_ci_low"), relative_ci[0], f"{comparison_id}:relative_ci_low")
        _same(row.get("relative_improvement_ci_high"), relative_ci[1], f"{comparison_id}:relative_ci_high")
        p_value = _paired_p(improvement)
        secondary_p[comparison_id] = p_value
        _same(row.get("unadjusted_p"), p_value, f"{comparison_id}:paired_p")
        standard_deviation = float(np.std(improvement, ddof=1))
        zero_variance = bool(standard_deviation <= np.finfo(float).eps)
        dz = (
            None
            if zero_variance
            else float(np.mean(improvement)) / standard_deviation
        )
        _same(row.get("cohen_dz"), dz, f"{comparison_id}:cohen_dz")
        if _boolean(row.get("cohen_dz_zero_variance")) is not zero_variance:
            raise ArtifactValidationError(f"{comparison_id} zero-variance flag differs")
        harm_b = _values(
            indexed, comparison["baseline_method"], "position_rmse", ids
        )
        harm_c = _values(
            indexed, comparison["candidate_method"], "position_rmse", ids
        )
        if harm_b is None or harm_c is None:
            raise ArtifactValidationError(
                f"{comparison_id} has incomplete harmful-rate inputs"
            )
        harmful = int(np.count_nonzero(harm_c > harm_b))
        if int(float(row["harmful_count"])) != harmful:
            raise ArtifactValidationError(f"{comparison_id} harmful count differs")
        _same(row.get("harmful_rate"), harmful / _N, f"{comparison_id}:harmful_rate")
        wilson = _wilson(harmful, _N)
        _same(row.get("harmful_rate_ci_low"), wilson[0], f"{comparison_id}:harm_ci_low")
        _same(row.get("harmful_rate_ci_high"), wilson[1], f"{comparison_id}:harm_ci_high")
    adjusted = _holm(secondary_p)
    for comparison_id, expected in adjusted.items():
        _same(
            secondary_rows[comparison_id].get("holm_adjusted_p"),
            expected,
            f"{comparison_id}:holm_adjusted_p",
        )

    subgroup_definitions: dict[str, tuple[str, str, list[int]]] = {}
    for value in design["subgroups"]["reference_family"]["levels"]:
        comparison_id = f"subgroup:reference_family:{value}"
        subgroup_definitions[comparison_id] = (
            "reference_family",
            str(value),
            [
                index
                for index, identity in enumerate(ids)
                if manifest_rows[identity]["family"] == value
            ],
        )
    for value in design["subgroups"]["demand_stratum"]["levels"]:
        comparison_id = f"subgroup:demand_stratum:{value}"
        subgroup_definitions[comparison_id] = (
            "demand_stratum",
            str(value),
            [
                index
                for index, identity in enumerate(ids)
                if manifest_rows[identity]["demand_stratum"] == value
            ],
        )
    active_id = "subgroup:acceleration_active:true"
    subgroup_definitions[active_id] = (
        "acceleration_active",
        "true",
        [
            index
            for index, identity in enumerate(ids)
            if manifest_rows[identity]["family"]
            in {
                "piecewise_constant_jerk",
                "stop_and_go",
                "rapid_reversal",
                "boundary_grazing",
            }
            and manifest_rows[identity]["demand_stratum"] in {"high", "near_limit"}
        ],
    )
    subgroup_keyed = _unique_by(
        _rows(published / "stratified_comparisons.csv"),
        ("comparison_id",),
        expected_keys={(comparison_id,) for comparison_id in subgroup_definitions},
        label="subgroup comparisons",
    )
    subgroup_rows = [subgroup_keyed[(comparison_id,)] for comparison_id in subgroup_definitions]
    subgroup_seed_order = sorted(
        (
            dimension,
            value,
            comparison_id,
        )
        for comparison_id, (dimension, value, _) in subgroup_definitions.items()
    )
    subgroup_seed = {
        comparison_id: int(design["subgroups"]["bootstrap"]["base_seed"]) + ordinal
        for ordinal, (_, _, comparison_id) in enumerate(subgroup_seed_order)
    }
    for comparison_id, (dimension, value, selected) in subgroup_definitions.items():
        row = subgroup_keyed[(comparison_id,)]
        if (
            row.get("stratum_dimension") != dimension
            or row.get("stratum_value") != value
            or int(float(row["trajectory_count"])) != len(selected)
            or int(float(row["bootstrap_seed"])) != subgroup_seed[comparison_id]
            or int(float(row["bootstrap_resamples"])) != _B
            or not math.isclose(float(row["confidence_level"]), 0.95)
        ):
            raise ArtifactValidationError(f"locked subgroup identity differs: {comparison_id}")
        if complete:
            assert baseline is not None and candidate is not None
            b = baseline[selected]
            c = candidate[selected]
            absolute = float(np.mean(b) - np.mean(c))
            relative = absolute / float(np.mean(b))
            draws = _draws(b, c, subgroup_seed[comparison_id])
            absolute_ci = _ci(draws["improvement"])
            relative_ci = _ci(draws["relative"])
            _same(row.get("absolute_improvement"), absolute, f"{comparison_id}:absolute")
            _same(row.get("relative_improvement"), relative, f"{comparison_id}:relative")
            _same(row.get("absolute_improvement_ci_low"), absolute_ci[0], f"{comparison_id}:absolute_ci_low")
            _same(row.get("absolute_improvement_ci_high"), absolute_ci[1], f"{comparison_id}:absolute_ci_high")
            _same(row.get("relative_improvement_ci_low"), relative_ci[0], f"{comparison_id}:relative_ci_low")
            _same(row.get("relative_improvement_ci_high"), relative_ci[1], f"{comparison_id}:relative_ci_high")
            harmful = int(np.count_nonzero(c > b))
            if int(float(row["harmful_count"])) != harmful:
                raise ArtifactValidationError(f"{comparison_id} harmful count differs")
            _same(row.get("harmful_rate"), harmful / len(selected), f"{comparison_id}:harmful_rate")
            wilson = _wilson(harmful, len(selected))
            _same(row.get("harmful_rate_ci_low"), wilson[0], f"{comparison_id}:harm_ci_low")
            _same(row.get("harmful_rate_ci_high"), wilson[1], f"{comparison_id}:harm_ci_high")
        else:
            if row.get("status") != "unavailable_incomplete_primary_denominator":
                raise ArtifactValidationError(f"incomplete subgroup has inference: {comparison_id}")
            for field in (
                "absolute_improvement",
                "absolute_improvement_ci_low",
                "absolute_improvement_ci_high",
                "relative_improvement",
                "relative_improvement_ci_low",
                "relative_improvement_ci_high",
                "harmful_count",
                "harmful_rate",
                "harmful_rate_ci_low",
                "harmful_rate_ci_high",
            ):
                _same(row.get(field), None, f"{comparison_id}:{field}")

    specialized = {
        "family_effects.csv": ("reference_family", 6),
        "demand_stratum_effects.csv": ("demand_stratum", 4),
        "acceleration_active_effect.csv": ("acceleration_active", 1),
    }
    for filename, (dimension, count) in specialized.items():
        expected_rows = {
            row["comparison_id"]: {key: value for key, value in row.items() if value != ""}
            for row in subgroup_rows
            if row["stratum_dimension"] == dimension
        }
        observed_list = _rows(published / filename)
        observed_keyed = _unique_by(
            observed_list,
            ("comparison_id",),
            expected_keys={(comparison_id,) for comparison_id in expected_rows},
            label=filename,
        )
        observed_rows = {
            key[0]: {field: value for field, value in row.items() if value != ""}
            for key, row in observed_keyed.items()
        }
        if len(expected_rows) != count or observed_rows != expected_rows:
            raise ArtifactValidationError(f"{filename} is not an exact subgroup projection")

    confidence_list = _rows(published / "confidence_intervals.csv")
    guard_seed = int(design["guardrails"]["bootstrap"]["seed"])
    guardrails = (
        (
            "max_error_relative_worsening",
            "position_max_abs_error",
            float(design["guardrails"]["max_error_noninferiority"]["margin"]),
            True,
        ),
        (
            "lag_absolute_worsening",
            "lag_s",
            float(design["guardrails"]["lag_noninferiority"]["margin_s"]),
            False,
        ),
    )
    guardrail_expected: dict[str, dict[str, Any]] = {}
    guardrail_passes: dict[str, bool | None] = {}
    for comparison_id, metric, margin, relative_metric in guardrails:
        b = _values(indexed, _P, metric, ids)
        c = _values(indexed, _PVA, metric, ids)
        if b is None or c is None:
            point = None
            interval = (None, None)
            status = "unavailable_incomplete_denominator"
            guardrail_passes[comparison_id] = None
        else:
            raw_draws = _draws(b, c, guard_seed)
            if relative_metric:
                point = (float(np.mean(c)) - float(np.mean(b))) / abs(float(np.mean(b)))
                draw_values = -raw_draws["relative"]
            else:
                point = float(np.mean(c) - np.mean(b))
                draw_values = -raw_draws["improvement"]
            interval = _ci(draw_values)
            status = "available"
            guardrail_passes[comparison_id] = interval[1] <= margin
        guardrail_expected[comparison_id] = {
            "analysis_kind": "guardrail",
            "estimand": "worsening",
            "point_estimate": point,
            "ci_low": interval[0],
            "ci_high": interval[1],
            "confidence_level": 0.95,
            "bootstrap_resamples": _B,
            "bootstrap_seed": guard_seed,
            "status": status,
        }
    for row in primary_rows:
        if _boolean(row.get("max_error_guardrail_pass")) is not guardrail_passes[
            "max_error_relative_worsening"
        ]:
            raise ArtifactValidationError("max-error guardrail pass flag differs")
        if _boolean(row.get("lag_guardrail_pass")) is not guardrail_passes[
            "lag_absolute_worsening"
        ]:
            raise ArtifactValidationError("lag guardrail pass flag differs")

    expected_ci: dict[tuple[str, str], dict[str, Any]] = {}
    primary_source = primary_rows[0]
    for estimand, source_prefix in (
        ("absolute_improvement", "overall_absolute_improvement"),
        ("relative_improvement", "overall_relative_improvement"),
    ):
        expected_ci[(design["primary"]["comparison_id"], estimand)] = {
            "analysis_kind": "primary",
            "estimand": estimand,
            "point_estimate": _number(primary_source.get(source_prefix)),
            "ci_low": _number(primary_source.get(f"{source_prefix}_ci_low")),
            "ci_high": _number(primary_source.get(f"{source_prefix}_ci_high")),
            "confidence_level": 0.95,
            "bootstrap_resamples": _B,
            "bootstrap_seed": primary_seed,
            "status": primary_source["formal_inference_status"],
        }
    for comparison_id, row in secondary_rows.items():
        for estimand in ("absolute_improvement", "relative_improvement"):
            expected_ci[(comparison_id, estimand)] = {
                "analysis_kind": "secondary",
                "estimand": estimand,
                "point_estimate": _number(row.get(estimand)),
                "ci_low": _number(row.get(f"{estimand}_ci_low")),
                "ci_high": _number(row.get(f"{estimand}_ci_high")),
                "confidence_level": 0.95,
                "bootstrap_resamples": _B,
                "bootstrap_seed": int(float(row["bootstrap_seed"])),
                "status": row["status"],
            }
    for comparison_id, expected in guardrail_expected.items():
        expected_ci[(comparison_id, "worsening")] = expected
    for row in subgroup_rows:
        for estimand in ("absolute_improvement", "relative_improvement"):
            expected_ci[(row["comparison_id"], estimand)] = {
                "analysis_kind": "subgroup",
                "estimand": estimand,
                "point_estimate": _number(row.get(estimand)),
                "ci_low": _number(row.get(f"{estimand}_ci_low")),
                "ci_high": _number(row.get(f"{estimand}_ci_high")),
                "confidence_level": 0.95,
                "bootstrap_resamples": _B,
                "bootstrap_seed": subgroup_seed[row["comparison_id"]],
                "status": row["status"],
            }
    confidence_keyed = _unique_by(
        confidence_list,
        ("comparison_id", "estimand"),
        expected_keys=set(expected_ci),
        label="confidence interval projection",
    )
    for key, expected in expected_ci.items():
        row = confidence_keyed[key]
        for field in ("point_estimate", "ci_low", "ci_high", "confidence_level"):
            _same(row.get(field), expected[field], f"confidence:{key}:{field}")
        for field in ("bootstrap_resamples", "bootstrap_seed"):
            if int(float(row[field])) != expected[field]:
                raise ArtifactValidationError(f"confidence:{key}:{field} differs")
        for field in ("analysis_kind", "status"):
            if row.get(field) != expected[field]:
                raise ArtifactValidationError(f"confidence:{key}:{field} differs")

    expected_harm: dict[tuple[str], dict[str, Any]] = {}
    expected_harm[(design["primary"]["comparison_id"],)] = {
        "analysis_kind": "primary",
        "status": (
            "available" if complete else "unavailable_incomplete_denominator"
        ),
        "harmful_count": (
            int(np.count_nonzero(candidate > baseline)) if complete else None
        ),
        "denominator": _N,
        "evaluated_count": paired_count,
        "harmful_rate": (
            float(np.mean(candidate > baseline)) if complete else None
        ),
        "wilson_ci_low": (
            _wilson(int(np.count_nonzero(candidate > baseline)), _N)[0]
            if complete
            else None
        ),
        "wilson_ci_high": (
            _wilson(int(np.count_nonzero(candidate > baseline)), _N)[1]
            if complete
            else None
        ),
    }
    for comparison_id, row in secondary_rows.items():
        expected_harm[(comparison_id,)] = {
            "analysis_kind": "secondary",
            "status": row["harmful_status"],
            "harmful_count": _number(row.get("harmful_count")),
            "denominator": int(float(row["harmful_denominator"])),
            "evaluated_count": int(float(row["harmful_evaluated_count"])),
            "harmful_rate": _number(row.get("harmful_rate")),
            "wilson_ci_low": _number(row.get("harmful_rate_ci_low")),
            "wilson_ci_high": _number(row.get("harmful_rate_ci_high")),
        }
    for row in subgroup_rows:
        expected_harm[(row["comparison_id"],)] = {
            "analysis_kind": "subgroup",
            "status": row["status"],
            "harmful_count": _number(row.get("harmful_count")),
            "denominator": int(float(row["harmful_denominator"])),
            "evaluated_count": (
                int(float(row["harmful_denominator"]))
                if _number(row.get("harmful_rate")) is not None
                else 0
            ),
            "harmful_rate": _number(row.get("harmful_rate")),
            "wilson_ci_low": _number(row.get("harmful_rate_ci_low")),
            "wilson_ci_high": _number(row.get("harmful_rate_ci_high")),
        }
    harmful_keyed = _unique_by(
        _rows(published / "harmful_trajectory_rate.csv"),
        ("comparison_id",),
        expected_keys=set(expected_harm),
        label="harmful-rate projection",
    )
    for key, expected in expected_harm.items():
        row = harmful_keyed[key]
        for field in (
            "harmful_count",
            "denominator",
            "evaluated_count",
            "harmful_rate",
            "wilson_ci_low",
            "wilson_ci_high",
        ):
            _same(row.get(field), expected.get(field), f"harmful:{key}:{field}")
        for field in ("analysis_kind", "status"):
            if row.get(field) != expected[field]:
                raise ArtifactValidationError(f"harmful:{key}:{field} differs")

    expected_reconstruction: dict[str, tuple[int, bool, list[str] | None]] = {
        design["primary"]["comparison_id"]: (primary_seed, complete, None),
        **{
            comparison["id"]: (
                int(design["secondary"]["bootstrap"]["base_seed"]) + ordinal,
                secondary_available[comparison["id"]],
                None,
            )
            for ordinal, comparison in enumerate(
                design["secondary"]["comparison_family"]
            )
        },
        **{
            comparison_id: (
                guard_seed,
                guardrail_passes[comparison_id] is not None,
                None,
            )
            for comparison_id in guardrail_expected
        },
        **{
            comparison_id: (
                subgroup_seed[comparison_id],
                complete,
                [dimension, value],
            )
            for comparison_id, (dimension, value, _) in subgroup_definitions.items()
        },
    }
    reconstruction_keyed = _unique_by(
        _rows(published / "bootstrap_reconstruction.csv"),
        ("comparison_id",),
        expected_keys={(comparison_id,) for comparison_id in expected_reconstruction},
        label="bootstrap reconstruction",
    )
    for comparison_id, (seed, draws_available, membership) in expected_reconstruction.items():
        row = reconstruction_keyed[(comparison_id,)]
        expected_literal = {
            "draws_available": draws_available,
            "resamples": _B,
            "seed": seed,
            "rng": "numpy.random.Generator(numpy.random.PCG64(seed))",
            "numpy_version": np.__version__,
            "input_order": "trajectory_id_lexicographic_ascending",
            "ordered_full_denominator_trajectory_ids_json": ids,
            "subgroup_membership_json": membership,
            "draw_algorithm": (
                "for each resample draw n indices independently and uniformly "
                "with replacement from integers [0,n)"
            ),
            "quantile_method": "linear",
            "quantiles_json": [0.025, 0.975],
            "paired_unit": "whole_trajectory",
        }
        if _boolean(row.get("draws_available")) is not draws_available:
            raise ArtifactValidationError(f"reconstruction:{comparison_id}:availability differs")
        for field in ("resamples", "seed"):
            if int(float(row[field])) != expected_literal[field]:
                raise ArtifactValidationError(f"reconstruction:{comparison_id}:{field} differs")
        for field in (
            "rng",
            "numpy_version",
            "input_order",
            "draw_algorithm",
            "quantile_method",
            "paired_unit",
        ):
            if row.get(field) != expected_literal[field]:
                raise ArtifactValidationError(f"reconstruction:{comparison_id}:{field} differs")
        for field in (
            "ordered_full_denominator_trajectory_ids_json",
            "subgroup_membership_json",
            "quantiles_json",
        ):
            observed = None if row.get(field) in {None, ""} else json.loads(row[field])
            if observed != expected_literal[field]:
                raise ArtifactValidationError(f"reconstruction:{comparison_id}:{field} differs")

    return {
        "independent_implementation": "otg_lab.v4_statistics_audit",
        "raw_metrics_sha256": _sha256(raw_path),
        "published_metrics_sha256": _sha256(published_metrics),
        "raw_and_published_metrics_byte_identical": True,
        "required_trajectory_count": _N,
        "paired_trajectory_count": paired_count,
        "primary_complete": complete,
        "primary_classification": classification,
        "bootstrap_resamples_verified": _B,
        "primary_recomputed": True,
        "guardrails_recomputed_or_unavailable_verified": True,
        "secondary_family_recomputed": True,
        "holm_family_recomputed": True,
        "subgroups_recomputed_or_unavailable_verified": True,
        "harm_and_worst_case_recomputed_or_unavailable_verified": True,
        "effect_sizes_recomputed": True,
        "bootstrap_reconstruction_verified": True,
        "confidence_interval_projection_verified": True,
        "all_independent_statistical_recomputations_verified": True,
    }


__all__ = ["audit_v4_statistics_independently"]
