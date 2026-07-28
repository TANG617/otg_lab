"""__EXPERIMENT_ID__ experiment declaration.

Edit the question, variables, inputs, method matrix, windows, and metric roles
in this file.  Core tracking and metric formulas stay in ``otg_lab``.
"""

from __future__ import annotations

from pathlib import Path

from otg_lab.analysis import ComparisonSpec, EvaluationWindow, MethodPair
from otg_lab.experiment import ExperimentInput, ExperimentSpec, InputGate
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
)


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    baseline = TrackingMethodSpec(
        method_id="position_zoh_p_ruckig",
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
    )
    candidate = TrackingMethodSpec(
        method_id="local_poly_cj_pva_direct",
        estimator=ComponentSpec(
            "local_poly",
            {"window": 5, "degree": 3, "lag_samples": 0},
        ),
        predictor=ComponentSpec("constant_jerk"),
        target_builder=ComponentSpec("pva"),
        governor=ComponentSpec("one_step"),
        follower=ComponentSpec("direct"),
    )
    return ExperimentSpec(
        experiment_id="__EXPERIMENT_ID__",
        slug="__EXPERIMENT_SLUG__",
        title="__EXPERIMENT_TITLE__",
        question="Replace with the precise investigation question.",
        hypothesis="Replace with a falsifiable hypothesis or diagnostic goal.",
        independent_variables=("tracking_method",),
        controlled_variables={
            "dt_s": 0.01,
            "measurement_policy": "position_only",
            "limits": {"velocity": 4.1, "acceleration": 8.2, "jerk": 4000.0},
        },
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
            "governor",
            "follower",
        ),
        inputs=(
            ExperimentInput(
                "sine",
                "data/trajectories/sine.csv",
                description="Replace or extend this canonical CSV input.",
            ),
        ),
        methods=(baseline, candidate),
        run_config=RunConfig(
            limits=MotionLimits(4.1, 8.2, 4000.0),
            minimum_duration_s=0.01,
            prediction_horizon_s=0.01,
            measurement_policy="position_only",
        ),
        metric_roles={
            "primary": ("position_rmse",),
            "secondary": (
                "position_mae",
                "position_p95_abs_error",
                "position_max_abs_error",
                "position_iae",
            ),
            "guardrail": (
                "output_velocity_violation_count",
                "output_acceleration_violation_count",
                "profile_constraint_violation_count",
                "deadline_miss_rate",
            ),
            "diagnostic": (
                "position_bias",
                "lag_s",
                "lag_aligned_rmse",
                "fallback_rate",
                "runtime_total_p95_s",
            ),
        },
        windows=(EvaluationWindow("full_overlap"),),
        comparison_spec=ComparisonSpec(
            pairs=(
                MethodPair(
                    "position_zoh_p_ruckig",
                    "local_poly_cj_pva_direct",
                ),
            ),
            metric_ids=(
                "position_rmse",
                "position_max_abs_error",
                "profile_constraint_violation_count",
            ),
            window_ids=("full_overlap",),
        ),
        input_gate=InputGate(block_on_limit_violation=False),
    )
