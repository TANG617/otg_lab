from __future__ import annotations

import otg_lab


def test_public_api_is_csv_first_and_single_axis() -> None:
    required = {
        "load_trajectory_csv",
        "write_trajectory_csv",
        "generate_analytic_trajectory",
        "analyze_reference",
        "run_tracking",
        "analyze_tracking",
        "compare_methods",
        "run_experiment",
        "Trajectory",
        "State",
        "Measurement",
        "MotionLimits",
        "ComponentSpec",
        "TrackingMethodSpec",
        "RunConfig",
        "ExperimentCase",
        "ExperimentSpec",
        "FactorHeatmapSpec",
    }
    assert required <= set(otg_lab.__all__)
    assert {
        "TrackingPipeline",
        "EstimatorPredictorPipeline",
        "TimedState",
    }.isdisjoint(otg_lab.__all__)
