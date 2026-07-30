"""E11: limit-projected E06 PV finite differences on a recorded waveform."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from otg_lab.analysis import (
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
    get_metric_spec,
)
from otg_lab.experiment import ExperimentInput, ExperimentSpec, InputGate
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
)
from otg_lab.trajectory_ablation import (
    BASELINE_METHOD_ID,
    build_state_target_methods,
)


def _load_recorded_transfer_artifact_writer() -> Any:
    module_name = "_otg_lab_e08_recorded_transfer_artifacts"
    module_path = (
        Path(__file__).resolve().parents[1]
        / "E08_pva_finite_difference_recorded_tracking"
        / "experiment.py"
    )
    module_spec = importlib.util.spec_from_file_location(module_name, module_path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot import recorded-transfer artifacts from {module_path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module.write_recorded_transfer_artifacts


write_recorded_transfer_artifacts = _load_recorded_transfer_artifact_writer()

INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
RAW_SOURCE_PATH = "data/raw/recorded_tasks/simplified_with_velocity_limit.csv"
CANONICAL_INPUT_PATH = f"data/trajectories/{INPUT_ID}.csv"

DT_S = 0.01
MAIN_START_S = 0.04
MAX_VELOCITY_RAD_S = 4.1
MAX_ACCELERATION_RAD_S2 = 8.2
MAX_JERK_RAD_S3 = 4000.0

PRIMARY = ("position_rmse",)
SECONDARY = (
    "position_mae",
    "position_p95_abs_error",
    "position_max_abs_error",
    "position_iae",
)
GUARDRAIL = (
    "output_velocity_violation_count",
    "output_acceleration_violation_count",
    "output_jerk_violation_count",
    "profile_velocity_violation_count",
    "profile_acceleration_violation_count",
    "profile_jerk_violation_count",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
    "deadline_miss_rate",
)
_ASSIGNED = set(PRIMARY + SECONDARY + GUARDRAIL)
DIAGNOSTIC = tuple(
    metric_id
    for metric_id in DEFAULT_TRACKING_METRIC_IDS
    if metric_id not in _ASSIGNED
    and metric_id not in {"settled", "settle_time_s"}
    and get_metric_spec(metric_id).family != "stop_go"
    and not any(
        requirement.startswith("truth_")
        for requirement in get_metric_spec(metric_id).requirements
    )
)


def _run_config() -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=MAX_VELOCITY_RAD_S,
            max_acceleration_rad_s2=MAX_ACCELERATION_RAD_S2,
            max_jerk_rad_s3=MAX_JERK_RAD_S3,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _methods() -> tuple[TrackingMethodSpec, ...]:
    methods = build_state_target_methods(
        "pv",
        include_truth=False,
        include_differences=True,
    )
    governor = ComponentSpec("configured_limit_projection")
    return tuple(
        replace(method, governor=governor, required=True) for method in methods
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    methods = _methods()
    candidate_ids = tuple(
        method.method_id for method in methods if method.method_id != BASELINE_METHOD_ID
    )
    return ExperimentSpec(
        experiment_id="E11",
        slug="pv_finite_difference_recorded_tracking",
        title="E11 recorded-task PV finite-difference transfer",
        question=(
            "Do E06's causal finite-difference PV targets remain executable "
            "and improve raw-time position tracking on a recorded task waveform "
            "when target acceleration is omitted?"
        ),
        hypothesis=(
            "Every causal PV method completes the full recorded waveform, "
            "keeps target acceleration at zero, lowers raw-time position RMSE "
            "versus P[k+1], and introduces no guardrail regression."
        ),
        description=(
            "Offline transfer of the E06 methods with explicit configured-limit "
            "projection. The target builder passes position and velocity only "
            "and fixes target acceleration to zero; raw and executable targets "
            "remain fully auditable."
        ),
        independent_variables=(
            "target_components",
            "derivative_source",
            "derivative_represented_time",
        ),
        controlled_variables={
            "input_id": INPUT_ID,
            "raw_source_path": RAW_SOURCE_PATH,
            "canonical_input_path": CANONICAL_INPUT_PATH,
            "axis_count": 1,
            "dt_s": DT_S,
            "fixed_grid": True,
            "raw_elapsed_time_ignored": True,
            "measurement_policy": "position_only",
            "scheduled_position_available_one_step_ahead": True,
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "target_components": "pv",
            "target_acceleration_policy": "zero_by_target_builder",
            "limits": {
                "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
                "max_acceleration_rad_s2": MAX_ACCELERATION_RAD_S2,
                "max_jerk_rad_s3": MAX_JERK_RAD_S3,
            },
            "target_conditioning": "configured_limit_projection",
            "projection_position_policy": "unchanged",
            "projection_velocity_policy": (
                "clip_to_configured_max_then_stopping_envelope"
            ),
            "projection_acceleration_policy": (
                "pass_through_zero_target_acceleration"
            ),
            "governor": "configured_limit_projection",
            "follower": "ordinary_ruckig_unshielded",
        },
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
        ),
        inputs=(
            ExperimentInput(
                INPUT_ID,
                CANONICAL_INPUT_PATH,
                required=True,
                description=(
                    "Fixed-grid position-only conversion of "
                    f"{RAW_SOURCE_PATH}; row order at 10 ms, no smoothing"
                ),
            ),
        ),
        methods=methods,
        run_config=_run_config(),
        metric_roles={
            "primary": PRIMARY,
            "secondary": SECONDARY,
            "guardrail": GUARDRAIL,
            "diagnostic": DIAGNOSTIC,
        },
        windows=(
            EvaluationWindow("full_overlap"),
            EvaluationWindow(
                "main_evaluation",
                start_time_s=MAIN_START_S,
            ),
        ),
        comparison_spec=ComparisonSpec(
            pairs=tuple(
                MethodPair(
                    BASELINE_METHOD_ID,
                    method_id,
                    f"{method_id}_vs_p_kp1",
                )
                for method_id in candidate_ids
            ),
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=(INPUT_ID,),
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        artifact_writer=write_recorded_transfer_artifacts,
    )


__all__ = ["INPUT_ID", "build_experiment"]
