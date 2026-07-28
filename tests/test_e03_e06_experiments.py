from __future__ import annotations

from pathlib import Path

import pytest

from otg_lab.analysis import MetricRow
from otg_lab.cli import load_experiment_spec
from otg_lab.components import available_components
from otg_lab.trajectory_ablation import (
    BASELINE_METHOD_ID,
    INPUT_IDS,
    _lag_comparison_rows,
    _write_lag_comparison_figure,
    write_trajectory_ablation_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("experiment_id", "components", "method_count", "pair_count"),
    (
        ("E03", "pva", 2, 1),
        ("E04", "pva", 7, 11),
        ("E05", "pv", 2, 1),
        ("E06", "pv", 7, 11),
    ),
)
def test_trajectory_ablation_specs(
    experiment_id: str,
    components: str,
    method_count: int,
    pair_count: int,
) -> None:
    spec = load_experiment_spec(ROOT, experiment_id)
    assert tuple(item.input_id for item in spec.inputs) == INPUT_IDS
    assert len(spec.methods) == method_count
    assert len(spec.comparison_spec.pairs) == pair_count
    assert spec.methods[0].method_id == BASELINE_METHOD_ID
    assert spec.run_config.measurement_policy == "position_only"
    assert spec.run_config.dt_s == pytest.approx(0.01)
    assert spec.methods[1].predictor.component_id == "oracle"
    assert spec.methods[1].predictor.params["noncausal_diagnostic"] is True
    assert spec.methods[1].target_builder.params["components"] == components
    main = next(
        window for window in spec.windows if window.window_id == "main_evaluation"
    )
    assert main.start_time_s == pytest.approx(0.04)
    assert main.end_time_s == pytest.approx(3.0)
    assert spec.artifact_writer is not None


def test_difference_specs_have_only_declared_classic_causal_matrix() -> None:
    for experiment_id, components in (("E04", "pva"), ("E06", "pv")):
        spec = load_experiment_spec(ROOT, experiment_id)
        methods = {method.method_id: method for method in spec.methods}
        assert set(methods) == {
            BASELINE_METHOD_ID,
            f"{components}_truth_kp1",
            f"{components}_est_backward_o1_k",
            f"{components}_est_backward_o2_k",
            f"{components}_est_centered_o2_km1",
            f"{components}_pred_backward_o1_kp1",
            f"{components}_pred_backward_o2_kp1",
        }
        centered = methods[f"{components}_est_centered_o2_km1"]
        assert centered.estimator.component_id == "centered_fd_o2_delay1"
        assert centered.predictor.component_id == "zero_order_hold"
        assert (
            centered.target_builder.params["time_source"]
            == "source_state_time"
        )
        assert not any(
            "centered" in method.predictor.component_id
            for method in spec.methods
        )


def test_new_components_are_registered() -> None:
    registry = available_components()
    assert {"backward_fd_o1", "backward_fd_o2", "centered_fd_o2_delay1"} <= set(
        registry["estimator"]
    )
    assert {"future_backward_fd_o1", "future_backward_fd_o2"} <= set(
        registry["predictor"]
    )
    assert "scheduled_state" in registry["target_builder"]


def test_e04_lag_artifacts_compare_p_only_and_truth(
    tmp_path: Path,
) -> None:
    spec = load_experiment_spec(ROOT, "E04")
    rows = []
    for input_id in INPUT_IDS:
        for method in spec.methods:
            method_id = method.method_id
            if method_id == BASELINE_METHOD_ID:
                lag_s = 0.07
            elif method_id == "pva_truth_kp1" or "_pred_" in method_id:
                lag_s = 0.0
            elif "_centered_" in method_id:
                lag_s = 0.02
            else:
                lag_s = 0.01
            rows.append(
                MetricRow(
                    input_id=input_id,
                    method_id=method_id,
                    window_id="main_evaluation",
                    metric_id="lag_s",
                    value=lag_s,
                    unit="s",
                    direction="none",
                )
            )

    comparison_rows = _lag_comparison_rows(rows, spec)
    assert len(comparison_rows) == len(INPUT_IDS) * len(spec.methods)
    estimator = next(
        row
        for row in comparison_rows
        if row["input_id"] == "cubic"
        and row["method_id"] == "pva_est_backward_o1_k"
    )
    assert estimator["lag_ms"] == pytest.approx(10.0)
    assert estimator["lag_delta_vs_p_only_ms"] == pytest.approx(-60.0)
    assert estimator["lag_delta_vs_truth_ms"] == pytest.approx(10.0)

    write_trajectory_ablation_artifacts(
        analysis_directory=tmp_path,
        references={},
        tracking_runs={},
        trajectory_rows=rows,
        experiment_spec=spec,
        create_figures=False,
    )
    assert (tmp_path / "lag_comparison.csv").is_file()

    _write_lag_comparison_figure(tmp_path / "figures", comparison_rows, spec)
    assert (tmp_path / "figures/lag_vs_p_and_truth.png").is_file()
    assert (tmp_path / "figures/lag_vs_p_and_truth.svg").is_file()
