from __future__ import annotations

from pathlib import Path

from otg_lab.cli import load_experiment_spec


def test_e02_declares_complete_position_only_factor_surface() -> None:
    project_root = Path(__file__).parents[1]
    spec = load_experiment_spec(project_root, "E02")

    assert spec.directory_name == "E02_position_only_limit_sensitivity"
    assert len(spec.methods) == 1
    method = spec.methods[0]
    assert method.estimator.component_id == "position_only"
    assert method.predictor.component_id == "zero_order_hold"
    assert method.target_builder.component_id == "p"
    assert method.governor.component_id == "none"
    assert method.follower.component_id == "ruckig"

    assert len(spec.cases) == 35
    assert len(spec.comparison_spec.pairs) == 34
    combinations = {
        (
            case.run_config.limits.max_acceleration_rad_s2,
            case.run_config.limits.max_jerk_rad_s3,
        )
        for case in spec.cases
    }
    expected = {
        (acceleration, jerk)
        for acceleration in (4.1, 6.0, 8.2, 12.0, 16.4)
        for jerk in (41.0, 200.0, 800.0, 1600.0, 3200.0, 4000.0, 8000.0)
    }
    assert combinations == expected
    assert {
        case.run_config.limits.max_velocity_rad_s for case in spec.cases
    } == {4.1}
    assert {
        case.run_config.measurement_policy for case in spec.cases
    } == {"position_only"}

    assert len(spec.factor_heatmaps) == 2
    heatmaps = {heatmap.figure_id: heatmap for heatmap in spec.factor_heatmaps}

    rmse_heatmap = heatmaps["constraint_sensitivity_rmse"]
    assert rmse_heatmap.baseline_case_id == "a8p2_j4000"
    assert rmse_heatmap.window_id == "main_evaluation"
    assert rmse_heatmap.metric_id == "position_rmse"
    assert rmse_heatmap.comparison_mode == "ratio"

    lag_heatmap = heatmaps["constraint_sensitivity_lag_ms"]
    assert lag_heatmap.baseline_case_id == "a8p2_j4000"
    assert lag_heatmap.window_id == "main_evaluation"
    assert lag_heatmap.metric_id == "lag_s"
    assert lag_heatmap.comparison_mode == "difference"
    assert lag_heatmap.display_multiplier == 1000.0
