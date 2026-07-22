from __future__ import annotations

import pytest

from otg_lab.statistics import (
    StatisticalValidationError,
    stratified_paired_trajectory_bootstrap,
)


def test_stratified_bootstrap_reports_family_effects_and_harm() -> None:
    units = [f"family_a_{index}" for index in range(4)] + [
        f"family_b_{index}" for index in range(4)
    ]
    baseline = {unit: 10.0 for unit in units}
    candidate = {
        **{f"family_a_{index}": 8.0 for index in range(4)},
        **{f"family_b_{index}": 11.0 for index in range(4)},
    }
    strata = {
        unit: ("family_a" if unit.startswith("family_a") else "family_b")
        for unit in units
    }

    result = stratified_paired_trajectory_bootstrap(
        baseline,
        candidate,
        strata,
        metric="position_rmse",
        stratum_name="reference_family",
        resamples=500,
        seed=19,
        expected_units=units,
    )

    assert result["stratified"]["improvement"] == pytest.approx(0.5)
    assert result["trajectory_summary"]["improved_count"] == 4
    assert result["trajectory_summary"]["harmful_count"] == 4
    assert result["trajectory_summary"]["harmful_rate"] == pytest.approx(0.5)
    assert result["heterogeneity"]["worst_stratum"] == "family_b"
    assert result["heterogeneity"]["worst_stratum_improvement"] == pytest.approx(-1.0)
    assert result["heterogeneity"]["improvement_range"] == pytest.approx(3.0)
    assert len(result["trajectory_summary"]["worst_5"]) == 5
    family_effects = {
        row["reference_family"]: row["improvement"] for row in result["strata"]
    }
    assert family_effects == {"family_a": pytest.approx(2.0), "family_b": pytest.approx(-1.0)}


def test_stratified_bootstrap_is_deterministic() -> None:
    baseline = {f"t{index}": float(index + 1) for index in range(8)}
    candidate = {
        unit: value * (0.8 if index < 4 else 1.1)
        for index, (unit, value) in enumerate(baseline.items())
    }
    strata = {f"t{index}": f"s{index // 4}" for index in range(8)}
    kwargs = dict(
        metric="rmse",
        resamples=300,
        seed=7,
        expected_units=list(baseline),
    )

    first = stratified_paired_trajectory_bootstrap(
        baseline, candidate, strata, **kwargs
    )
    second = stratified_paired_trajectory_bootstrap(
        baseline, candidate, strata, **kwargs
    )

    assert first == second


def test_stratified_bootstrap_rejects_incomplete_strata_and_singletons() -> None:
    baseline = {"a": 1.0, "b": 2.0, "c": 3.0}
    candidate = {"a": 0.9, "b": 1.9, "c": 2.9}

    with pytest.raises(StatisticalValidationError, match="strata differ"):
        stratified_paired_trajectory_bootstrap(
            baseline,
            candidate,
            {"a": "x", "b": "x"},
            metric="rmse",
            expected_units=list(baseline),
        )

    with pytest.raises(StatisticalValidationError, match="at least two"):
        stratified_paired_trajectory_bootstrap(
            baseline,
            candidate,
            {"a": "x", "b": "x", "c": "y"},
            metric="rmse",
            expected_units=list(baseline),
        )


def test_stratified_bootstrap_retains_stratum_with_undefined_relative_interval() -> None:
    baseline = {
        "a0": -1.0,
        "a1": 1.0,
        "a2": 1.0,
        "a3": 1.0,
        "b0": 2.0,
        "b1": 2.0,
        "b2": 2.0,
        "b3": 2.0,
    }
    candidate = {unit: value + 1.0 for unit, value in baseline.items()}
    strata = {unit: unit[0] for unit in baseline}

    result = stratified_paired_trajectory_bootstrap(
        baseline,
        candidate,
        strata,
        metric="signed_lag_s",
        stratum_name="reference_family",
        resamples=500,
        seed=4,
        expected_units=list(baseline),
    )

    family_a = next(
        row for row in result["strata"] if row["reference_family"] == "a"
    )
    assert family_a["n_trajectories"] == 4
    assert family_a["absolute_difference"] == pytest.approx(1.0)
    assert family_a["relative_point_defined"] is True
    assert family_a["relative_interval_defined"] is False
    assert family_a["relative_ci_low"] is None
