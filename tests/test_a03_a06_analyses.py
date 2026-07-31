from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from otg_lab.cross_analysis import prepare_analysis
from otg_lab.cross_analysis_reporting import prepared_rows

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIRECTORIES = {
    "a03": ROOT / "analyses/A03_recorded_pva_velocity_limit_attribution",
    "a04": ROOT / "analyses/A04_recorded_pv_pva_fd_selection",
    "a05": ROOT / "analyses/A05_stop_go_p_pv_pva_improvement",
    "a06": ROOT / "analyses/A06_pv_pva_vaj_fine_selection",
}


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def a03() -> dict[str, Any]:
    directory = ANALYSIS_DIRECTORIES["a03"]
    module = _load_module("test_a03_impl", directory / "analysis_impl.py")
    prepared = prepare_analysis(directory / "analysis.yaml")
    surface = prepared_rows(prepared, "vmax_ablation")
    interactions = prepared_rows(prepared, "vmax_interactions")
    return {
        "module": module,
        "prepared": prepared,
        "decisions": module._decision_rows(interactions, surface),
        "mechanisms": module._mechanism_rows(surface),
    }


@pytest.fixture(scope="module")
def a04() -> dict[str, Any]:
    directory = ANALYSIS_DIRECTORIES["a04"]
    module = _load_module("test_a04_impl", directory / "analysis_impl.py")
    prepared = prepare_analysis(directory / "analysis.yaml")
    index = module._metric_index(prepared_rows(prepared, "trajectory_metrics"))
    scorecard = module._scorecard_rows(index, prepared)
    return {
        "module": module,
        "prepared": prepared,
        "scorecard": scorecard,
        "matched": module._matched_rows(scorecard),
    }


@pytest.fixture(scope="module")
def a05() -> dict[str, Any]:
    directory = ANALYSIS_DIRECTORIES["a05"]
    module = _load_module("test_a05_impl", directory / "analysis_impl.py")
    prepared = prepare_analysis(directory / "analysis.yaml")
    surface = prepared_rows(prepared, "joint_stop_go_surface")
    improvements = module._improvement_rows(surface)
    return {
        "module": module,
        "prepared": prepared,
        "summary": module._summary_rows(improvements),
        "equivalence": module._pv_pva_equivalence_rows(surface),
    }


@pytest.fixture(scope="module")
def a06() -> dict[str, Any]:
    directory = ANALYSIS_DIRECTORIES["a06"]
    module = _load_module("test_a06_impl", directory / "analysis_impl.py")
    prepared = prepare_analysis(directory / "analysis.yaml")
    raw_surface = prepared_rows(prepared, "vaj_sensitivity")
    surface = module._rmse_lag_rows(raw_surface)
    best, frontier = module._ranked_rows(surface)
    lag_sensitivity = module._selected_lag_sensitivity_rows(prepared)
    return {
        "module": module,
        "prepared": prepared,
        "surface": surface,
        "best": best,
        "frontier": frontier,
        "lag_sensitivity": lag_sensitivity,
    }


def test_a03_nails_pva_deficit_but_rejects_runtime_vmax_attribution(
    a03: dict[str, Any],
) -> None:
    original = [
        row
        for row in a03["decisions"]
        if row["input_id"] == a03["module"].ORIGINAL_INPUT_ID
    ]
    assert len(original) == 5
    assert all(row["pva_worse_than_p_at_both_vmax"] for row in original)
    assert all(row["ratio_invariant_within_tolerance"] for row in original)
    assert all(
        row["attribution_decision"] == "rejected_runtime_velocity_limit"
        for row in original
    )
    assert all(row["acceleration_projection_persists"] for row in original)
    assert all(
        row["lag_interaction_invariant_within_tolerance"]
        for row in original
    )
    assert all(
        row["pva_lag_worse_than_p_at_both_vmax"] for row in original
    )
    assert {
        round(float(row["limited_pva_lag_ms"]))
        for row in original
    } == {110, 130, 140, 160}
    assert all(
        float(row["limited_baseline_lag_ms"]) == pytest.approx(60.0)
        for row in original
    )


def test_a04_selects_pv_future_o1_and_all_matched_pva_are_worse(
    a04: dict[str, Any],
) -> None:
    selected = next(
        row for row in a04["scorecard"] if row["selected_primary"]
    )
    assert selected["method_id"] == "pv_pred_backward_o1_kp1"
    assert selected["rmse_ratio_vs_p"] == pytest.approx(0.7969602612977835)
    assert selected["baseline_lag_ms"] == pytest.approx(20.0)
    assert selected["lag_ms"] == pytest.approx(10.0)
    assert selected["absolute_lag_delta_vs_p_ms"] == pytest.approx(-10.0)
    assert selected["baseline_subsample_lag_ms"] == pytest.approx(
        21.0294369286
    )
    assert selected["lag_subsample_ms"] == pytest.approx(9.55430969875)
    assert selected["rmse_lag_pareto"]
    assert selected["selected_lag_budget_10ms"]
    assert selected["selected_lag_budget_20ms"]
    assert sum(row["rmse_lag_pareto"] for row in a04["scorecard"]) == 1
    assert sum(row["rmse_beats_p"] for row in a04["scorecard"]) == 1
    assert sum(
        float(row["pva_minus_pv_rmse_ratio"]) < 0.0
        for row in a04["matched"]
    ) == 4
    future_o1 = next(
        row for row in a04["matched"] if row["stencil"] == "pred_backward_o1_kp1"
    )
    assert float(future_o1["pva_minus_pv_rmse_ratio"]) > 0.0


def test_a05_all_derivative_methods_eliminate_pulses_and_match_on_stop_go(
    a05: dict[str, Any],
) -> None:
    assert len(a05["summary"]) == 10
    assert all(row["all_baseline_pulses_eliminated"] for row in a05["summary"])
    assert len(a05["equivalence"]) == 5
    assert all(
        row["stop_go_equivalent_within_1e_12"]
        for row in a05["equivalence"]
    )


def test_a06_selects_complete_grid_best_tested_settings(
    a06: dict[str, Any],
) -> None:
    assert len(a06["surface"]) == 1280
    by_component = {
        row["target_components"]: row for row in a06["best"]
    }
    pv = by_component["PV"]
    assert pv["eligible_case_count"] == 639
    assert (
        pv["best_max_velocity_rad_s"],
        pv["best_max_acceleration_rad_s2"],
        pv["best_max_jerk_rad_s3"],
    ) == pytest.approx((1.0, 8.2, 3200.0))
    assert pv["best_position_rmse_rad"] == pytest.approx(
        0.0021286587847327188
    )
    assert pv["best_lag_ms"] == pytest.approx(10.0)
    assert pv["best_projection_count"] == 6
    assert pv["performance_equivalent_case_count"] == 6
    assert pv["selected_lag_budget_10ms_case_id"] == pv["best_case_id"]
    assert pv["selected_lag_budget_20ms_case_id"] == pv["best_case_id"]
    assert pv["deployment_recommended_case_id"].endswith(
        "__v4p1_a8p2_j3200"
    )
    assert pv["deployment_max_velocity_rad_s"] == pytest.approx(4.1)
    assert pv["deployment_max_acceleration_rad_s2"] == pytest.approx(8.2)
    assert pv["deployment_max_jerk_rad_s3"] == pytest.approx(3200.0)
    assert pv["deployment_position_rmse_rad"] == pytest.approx(
        pv["best_position_rmse_rad"]
    )
    assert pv["deployment_lag_ms"] == pytest.approx(pv["best_lag_ms"])
    assert pv["deployment_projection_count"] == 0
    assert pv["boundary_axes"] == "A"

    pva = by_component["PVA"]
    assert pva["eligible_case_count"] == 560
    assert (
        pva["best_max_velocity_rad_s"],
        pva["best_max_acceleration_rad_s2"],
        pva["best_max_jerk_rad_s3"],
    ) == pytest.approx((1.0, 7.5, 3200.0))
    assert pva["best_position_rmse_rad"] == pytest.approx(
        0.0033910493274395916
    )
    assert pva["best_lag_ms"] == pytest.approx(10.0)
    assert not pva["boundary_censored"]


def test_a06_selected_setting_subsample_lag_is_performance_equivalent(
    a06: dict[str, Any],
) -> None:
    rows = a06["lag_sensitivity"]

    assert len(rows) == 3
    assert [row["integer_lag_ms"] for row in rows] == pytest.approx(
        [10.0, 10.0, 10.0]
    )
    assert rows[0]["subsample_lag_ms"] == pytest.approx(
        rows[1]["subsample_lag_ms"],
        abs=1e-12,
    )
    assert rows[0]["position_rmse_rad"] == pytest.approx(
        rows[1]["position_rmse_rad"],
        abs=1e-15,
    )
    assert rows[0]["projection_count"] == 6
    assert rows[1]["projection_count"] == 0
    assert rows[2]["subsample_lag_ms"] == pytest.approx(9.55430969875)
    assert rows[2]["deployment_role"] == "vendor_reference"


@pytest.mark.parametrize("analysis_key", ("a03", "a04", "a05", "a06"))
def test_completed_analysis_check_modes_do_not_write(
    analysis_key: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = request.getfixturevalue(analysis_key)
    module = analysis["module"]

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("check mode attempted to write outputs")

    monkeypatch.setattr(module, "_write_outputs", fail_if_called)
    assert module.run(check_only=True) == 0


def test_final_recorded_report_keeps_rmse_and_lag_together() -> None:
    report_path = ROOT / "docs/final_experiment_conclusions.md"
    report = report_path.read_text(encoding="utf-8")
    normalized_report = " ".join(report.split())

    assert (
        "| 角色/方案 | recorded waveform | VAJ | RMSE rad | integer lag | "
        "sub-sample lag | projection |"
    ) in report
    assert (
        "| 角色 | VAJ | RMSE rad | integer lag | sub-sample lag | "
        "projection |"
    ) in report
    assert (
        "**RMSE -27.87%，integer lag 20 → 10 ms**"
        in report
    )
    assert "**4.1 / 8.2 / 3200**" in report
    assert "**4.2/8.2/41**" in report
    assert "**0.0772111911**" in report
    assert "**180 ms**" in report
    assert (
        "10 ms 和 20 ms 两档 lag budget 得到相同选择"
        in normalized_report
    )
    assert "解析轨迹仅用于实验中间的方法正确性验证" in report
    assert "当前实际上线报告 baseline" in report
    assert "其余实验 baseline 不变" in report
    assert "A03：Recorded PVA velocity-limit 归因" not in report
    assert (
        ROOT
        / "docs/assets/final_experiment_conclusions/"
        "recorded_rmse_lag_pareto.svg"
    ).is_file()
    assert (
        ROOT
        / "docs/assets/final_experiment_conclusions/"
        "vaj_rmse_lag_pareto.svg"
    ).is_file()
