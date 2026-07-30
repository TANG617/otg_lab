from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from otg_lab.cross_analysis import prepare_analysis
from otg_lab.cross_analysis_reporting import directional_effect, prepared_rows

PROJECT_ROOT = Path(__file__).resolve().parents[1]
A01_DIRECTORY = PROJECT_ROOT / "analyses" / "A01_E03-E06_pv_pva_comparison"
A02_DIRECTORY = PROJECT_ROOT / "analyses" / "A02_E03-E05_truth_fd_method_selection"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def a01() -> dict[str, Any]:
    module = _load_module("test_a01_analysis_impl", A01_DIRECTORY / "analysis_impl.py")
    prepared = prepare_analysis(A01_DIRECTORY / "analysis.yaml")
    metric_rows = prepared_rows(prepared, "trajectory_metrics")
    pairs = module.build_metric_pairs(metric_rows)
    return {
        "module": module,
        "prepared": prepared,
        "metric_rows": metric_rows,
        "pairs": pairs,
        "primary": module.build_primary_position_pairs(pairs),
    }


@pytest.fixture(scope="module")
def a02() -> dict[str, Any]:
    module = _load_module("test_a02_analysis_impl", A02_DIRECTORY / "analysis_impl.py")
    prepared = prepare_analysis(A02_DIRECTORY / "analysis.yaml")
    metric_rows = prepared_rows(prepared, "trajectory_metrics")
    audit = module.build_truth_fd_metric_pairs(metric_rows)
    guardrails = module.build_guardrail_summary(metric_rows)
    scorecard = module.build_method_input_scorecard(audit, guardrails)
    summaries = module.build_method_summary(scorecard)
    decisions = module.build_decision_matrix(summaries)
    return {
        "module": module,
        "prepared": prepared,
        "metric_rows": metric_rows,
        "audit": audit,
        "guardrails": guardrails,
        "scorecard": scorecard,
        "summaries": summaries,
        "decisions": decisions,
    }


def test_directional_effect_handles_all_directions_and_denominators() -> None:
    assert directional_effect(4.0, 3.0, "lower") == (pytest.approx(-1.0), 1.0, 0.25)
    assert directional_effect(4.0, 5.0, "higher") == (1.0, 1.0, 0.25)
    assert directional_effect(4.0, 5.0, "none") == (1.0, None, None)
    assert directional_effect(0.0, 1.0, "higher") == (1.0, 1.0, None)
    assert directional_effect(None, 1.0, "lower") == (None, None, None)


def test_a01_and_a02_overlap_sources_are_identical() -> None:
    a01_config = yaml.safe_load((A01_DIRECTORY / "analysis.yaml").read_text())
    a02_config = yaml.safe_load((A02_DIRECTORY / "analysis.yaml").read_text())
    a01_sources = {source["source_id"]: source for source in a01_config["sources"]}
    for source in a02_config["sources"]:
        assert a01_sources[source["source_id"]] == source
    assert a01_config["selection"] == a02_config["selection"]
    assert set(a01_config["selection"]) == {"window_ids"}


def test_a01_has_six_complete_families_and_exactly_18_primary_rows(
    a01: dict[str, Any],
) -> None:
    primary = a01["primary"]
    module = a01["module"]
    assert len(primary) == 18
    assert {row["method_family"] for row in primary} == set(module.METHOD_FAMILY_ORDER)
    assert all(row["pair_status"] == "paired" for row in a01["pairs"])
    truth_rows = [row for row in primary if row["method_family"] == "truth_kp1"]
    assert all(row["relative_improvement"] == "" for row in truth_rows)
    assert all(
        row["calculation_status"] == "absolute_only_truth_near_zero"
        for row in truth_rows
    )


def test_a01_preserves_unavailable_jerk_channel(a01: dict[str, Any]) -> None:
    jerk_rows = [
        row for row in a01["pairs"] if row["metric_id"] == "output_jerk_violation_count"
    ]
    assert jerk_rows
    assert all(
        row["pv_status"].startswith("unavailable")
        and row["pva_status"].startswith("unavailable")
        and row["calculation_status"] == "unavailable_value"
        for row in jerk_rows
    )


def test_a02_uses_only_e04_for_the_exact_15_row_fd_scorecard(
    a02: dict[str, Any],
) -> None:
    assert len(a02["scorecard"]) == 15
    assert {
        row["candidate_source_id"]
        for row in a02["audit"]
        if row["pair_type"] == "e04_fd_vs_pva_truth_and_p"
    } == {"e04_pva_finite_difference"}
    controls = [
        row
        for row in a02["audit"]
        if row["pair_type"] == "e05_pv_truth_component_control"
    ]
    assert controls
    assert all(row["candidate_source_id"] == "e05_pv_truth" for row in controls)


def test_a02_decisions_and_deadline_sensitivity_are_reproduced(
    a02: dict[str, Any],
) -> None:
    decisions = {
        row["scenario_id"]: row["selected_method_id"] for row in a02["decisions"]
    }
    assert decisions["default_strict_realtime"] == "pva_pred_backward_o2_kp1"
    assert decisions["one_sample_tolerance"] == "pva_pred_backward_o2_kp1"
    assert decisions["two_sample_tolerance"] == "pva_pred_backward_o2_kp1"
    assert decisions["no_extrapolation"] == "pva_est_backward_o1_k"
    assert decisions["no_extrapolation_ignore_deadline"] == "pva_est_backward_o2_k"
    backward_o2 = next(
        row for row in a02["summaries"] if row["method_id"] == "pva_est_backward_o2_k"
    )
    assert backward_o2["formally_eligible"] == "false"
    assert backward_o2["eligible_ignoring_deadline"] == "true"
    deadline_failure = [
        row
        for row in a02["guardrails"]
        if row["method_id"] == "pva_est_backward_o2_k"
        and row["input_id"] == "sine"
        and row["metric_id"] == "deadline_miss_rate"
    ]
    assert len(deadline_failure) == 1
    assert deadline_failure[0]["passes_no_regression"] == "false"


def test_a02_lag_semantics_and_budgets(a02: dict[str, Any]) -> None:
    by_method: dict[str, set[float]] = {}
    for row in a02["scorecard"]:
        by_method.setdefault(row["method_id"], set()).add(
            float(row["absolute_observed_lag_ms"])
        )
    expected_lags = {
        "pva_pred_backward_o1_kp1": 0.0,
        "pva_pred_backward_o2_kp1": 0.0,
        "pva_est_backward_o1_k": 10.0,
        "pva_est_backward_o2_k": 10.0,
        "pva_est_centered_o2_km1": 20.0,
    }
    for method_id, expected in expected_lags.items():
        assert len(by_method[method_id]) == 1
        assert next(iter(by_method[method_id])) == pytest.approx(expected)
    assert all(
        abs(float(row["observed_lag_ms"])) == float(row["absolute_observed_lag_ms"])
        for row in a02["scorecard"]
    )


@pytest.mark.parametrize("analysis_key", ("a01", "a02"))
def test_check_mode_never_calls_output_writer(
    analysis_key: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = request.getfixturevalue(analysis_key)
    module = analysis["module"]

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("check mode attempted to write formal outputs")

    monkeypatch.setattr(module, "_write_outputs", fail_if_called)
    assert module.run(check_only=True) == 0
