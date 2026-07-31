"""E12: recorded PVA/P baseline with an explicit runtime-Vmax ablation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from otg_lab.analysis import (
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
    MetricRow,
    get_metric_spec,
)
from otg_lab.experiment import (
    ExperimentCase,
    ExperimentInput,
    ExperimentSpec,
    InputGate,
)
from otg_lab.governors import MotionLimits as NumericalMotionLimits
from otg_lab.models import MotionLimits, RunConfig, TrackingRun
from otg_lab.recorded_experiments import (
    BASELINE_METHOD_ID,
    finite_difference_method_ids,
    metric_lookup,
    metric_value,
    projected_state_target_methods,
    projection_audit,
    value_token,
)
from otg_lab.runio import write_rows_csv

ORIGINAL_INPUT_ID = "recorded_tasks_original_no_velocity_limit"
SIMPLIFIED_UNLIMITED_INPUT_ID = "recorded_tasks_simplified_no_velocity_limit"
SIMPLIFIED_LIMITED_INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
INPUT_IDS = (
    ORIGINAL_INPUT_ID,
    SIMPLIFIED_UNLIMITED_INPUT_ID,
    SIMPLIFIED_LIMITED_INPUT_ID,
)
INPUT_PATHS = {
    ORIGINAL_INPUT_ID: (
        f"data/trajectories/{ORIGINAL_INPUT_ID}.csv",
        "data/raw/recorded_tasks/original_no_velocity_limit.csv",
    ),
    SIMPLIFIED_UNLIMITED_INPUT_ID: (
        f"data/trajectories/{SIMPLIFIED_UNLIMITED_INPUT_ID}.csv",
        "data/raw/recorded_tasks/simplified_no_velocity_limit.csv",
    ),
    SIMPLIFIED_LIMITED_INPUT_ID: (
        f"data/trajectories/{SIMPLIFIED_LIMITED_INPUT_ID}.csv",
        "data/raw/recorded_tasks/simplified_with_velocity_limit.csv",
    ),
}
INPUT_ACQUISITION_VELOCITY_LIMITED = {
    ORIGINAL_INPUT_ID: False,
    SIMPLIFIED_UNLIMITED_INPUT_ID: False,
    SIMPLIFIED_LIMITED_INPUT_ID: True,
}

DT_S = 0.01
MAIN_START_S = 0.04
VMAX_LEVELS_RAD_S = (4.1, 10.0)
MAX_ACCELERATION_RAD_S2 = 8.2
MAX_JERK_RAD_S3 = 4000.0
METHOD_IDS = (BASELINE_METHOD_ID, *finite_difference_method_ids("pva"))

PRIMARY = ("position_rmse",)
SECONDARY = (
    "position_mae",
    "position_bias",
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
INTEGRITY_GUARDRAILS = (
    "output_velocity_violation_count",
    "output_acceleration_violation_count",
    "profile_velocity_violation_count",
    "profile_acceleration_violation_count",
    "profile_jerk_violation_count",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
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


def _case_id(method_id: str, vmax: float) -> str:
    return f"{method_id}__v{value_token(vmax)}"


def _run_config(vmax: float) -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=vmax,
            max_acceleration_rad_s2=MAX_ACCELERATION_RAD_S2,
            max_jerk_rad_s3=MAX_JERK_RAD_S3,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _cases() -> tuple[ExperimentCase, ...]:
    return tuple(
        ExperimentCase(
            case_id=_case_id(method_id, vmax),
            method_id=method_id,
            run_config=_run_config(vmax),
            factors={
                "max_velocity_rad_s": vmax,
                "runtime_velocity_limit_condition": (
                    0.0 if vmax == VMAX_LEVELS_RAD_S[0] else 1.0
                ),
            },
            description=(
                f"{method_id}; configured-limit projection; "
                f"V/A/J={vmax:g}/{MAX_ACCELERATION_RAD_S2:g}/"
                f"{MAX_JERK_RAD_S3:g}"
            ),
        )
        for method_id in METHOD_IDS
        for vmax in VMAX_LEVELS_RAD_S
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    methods = projected_state_target_methods("pva", include_baseline=True)
    if tuple(method.method_id for method in methods) != METHOD_IDS:
        raise RuntimeError("recorded PVA method ordering changed")
    pairs = tuple(
        MethodPair(
            _case_id(BASELINE_METHOD_ID, vmax),
            _case_id(method_id, vmax),
            f"{method_id}_vs_p_kp1__v{value_token(vmax)}",
        )
        for vmax in VMAX_LEVELS_RAD_S
        for method_id in METHOD_IDS
        if method_id != BASELINE_METHOD_ID
    )
    return ExperimentSpec(
        experiment_id="E12",
        slug="pva_recorded_vmax_ablation",
        title="E12 recorded-task PVA runtime-Vmax ablation",
        question=(
            "Is PVA's recorded-task deficit versus scheduled P caused by the "
            "runtime velocity limit, rather than by acceleration clipping, "
            "stopping-envelope projection, or input-curve differences?"
        ),
        hypothesis=(
            "If runtime Vmax is causal, relaxing Vmax from 4.1 to 10 rad/s "
            "will reduce PVA/P RMSE ratios while leaving non-velocity "
            "projection mechanisms separately visible."
        ),
        description=(
            "A two-level runtime-Vmax intervention over three recorded inputs. "
            "P[k+1] and all five causal PVA stencils are rerun under both "
            "limits; raw-target projection is decomposed by cause."
        ),
        independent_variables=(
            "input_curve",
            "input_acquisition_velocity_limit_condition",
            "pva_finite_difference_method",
            "runtime_max_velocity_rad_s",
        ),
        controlled_variables={
            "input_ids": INPUT_IDS,
            "input_acquisition_velocity_limited": (
                INPUT_ACQUISITION_VELOCITY_LIMITED
            ),
            "dt_s": DT_S,
            "fixed_grid": True,
            "measurement_policy": "position_only",
            "scheduled_position_available_one_step_ahead": True,
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "max_acceleration_rad_s2": MAX_ACCELERATION_RAD_S2,
            "max_jerk_rad_s3": MAX_JERK_RAD_S3,
            "target_conditioning": "configured_limit_projection_per_case",
            "projection_position_policy": "unchanged",
            "follower": "ordinary_ruckig_unshielded",
            "primary_window_start_s": MAIN_START_S,
        },
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
            "run_config.limits.max_velocity_rad_s",
        ),
        inputs=tuple(
            ExperimentInput(
                input_id,
                canonical_path,
                required=True,
                description=(
                    f"Fixed-grid position-only conversion of {raw_path}; "
                    "source row order at 10 ms"
                ),
            )
            for input_id, (canonical_path, raw_path) in INPUT_PATHS.items()
        ),
        methods=methods,
        run_config=_run_config(VMAX_LEVELS_RAD_S[0]),
        metric_roles={
            "primary": PRIMARY,
            "secondary": SECONDARY,
            "guardrail": GUARDRAIL,
            "diagnostic": DIAGNOSTIC,
        },
        windows=(
            EvaluationWindow("full_overlap"),
            EvaluationWindow("main_evaluation", start_time_s=MAIN_START_S),
        ),
        comparison_spec=ComparisonSpec(
            pairs=pairs,
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=INPUT_IDS,
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=_cases(),
        artifact_writer=write_vmax_ablation_artifacts,
    )


def _surface_rows(
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
) -> list[dict[str, Any]]:
    lookup = metric_lookup(trajectory_rows)
    output: list[dict[str, Any]] = []
    for input_id in INPUT_IDS:
        for vmax in VMAX_LEVELS_RAD_S:
            baseline_case = _case_id(BASELINE_METHOD_ID, vmax)
            baseline_rmse = metric_value(
                lookup,
                input_id,
                baseline_case,
                "position_rmse",
                "main_evaluation",
            )
            for method_id in METHOD_IDS:
                case_id = _case_id(method_id, vmax)
                run = tracking_runs[(case_id, input_id)]
                rmse = metric_value(
                    lookup,
                    input_id,
                    case_id,
                    "position_rmse",
                    "main_evaluation",
                )
                lag_s = metric_value(
                    lookup,
                    input_id,
                    case_id,
                    "lag_s",
                    "main_evaluation",
                )
                ratio = (
                    None
                    if rmse is None
                    or baseline_rmse is None
                    or baseline_rmse <= 0.0
                    else rmse / baseline_rmse
                )
                numerical_limits = NumericalMotionLimits.broadcast(
                    1,
                    vmax,
                    MAX_ACCELERATION_RAD_S2,
                    MAX_JERK_RAD_S3,
                )
                projection = projection_audit(run, numerical_limits)
                guardrails = {
                    metric_id: metric_value(
                        lookup,
                        input_id,
                        case_id,
                        metric_id,
                        "full_overlap",
                    )
                    for metric_id in GUARDRAIL
                }
                integrity_pass = bool(
                    run.status.completed
                    and rmse is not None
                    and all(
                        guardrails[metric_id] is not None
                        and abs(float(guardrails[metric_id])) <= 1e-12
                        for metric_id in INTEGRITY_GUARDRAILS
                    )
                    and projection["position_projection_max_abs_rad"] is not None
                    and float(
                        projection["position_projection_max_abs_rad"]
                    )
                    <= 1e-12
                    and projection["executable_target_inadmissible_count"] == 0
                    and projection[
                        "projection_reconstruction_mismatch_count"
                    ]
                    == 0
                )
                output.append(
                    {
                        "input_id": input_id,
                        "input_acquisition_velocity_limited": (
                            INPUT_ACQUISITION_VELOCITY_LIMITED[input_id]
                        ),
                        "method_id": method_id,
                        "target_components": (
                            "P" if method_id == BASELINE_METHOD_ID else "PVA"
                        ),
                        "case_id": case_id,
                        "max_velocity_rad_s": vmax,
                        "max_acceleration_rad_s2": MAX_ACCELERATION_RAD_S2,
                        "max_jerk_rad_s3": MAX_JERK_RAD_S3,
                        "completed": run.status.completed,
                        "valid_cycles": run.status.valid_cycles,
                        "total_cycles": run.status.total_cycles,
                        "position_rmse_rad": rmse,
                        "baseline_position_rmse_rad": baseline_rmse,
                        "rmse_ratio_vs_p": ratio,
                        "log_rmse_ratio_vs_p": (
                            None
                            if ratio is None or ratio <= 0.0
                            else math.log(ratio)
                        ),
                        "lag_s": lag_s,
                        "lag_ms": None if lag_s is None else 1000.0 * lag_s,
                        **projection,
                        **{
                            f"{metric_id}": value
                            for metric_id, value in guardrails.items()
                        },
                        "integrity_pass": integrity_pass,
                    }
                )
    return output


def _interaction_rows(
    surface_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    index = {
        (
            str(row["input_id"]),
            str(row["method_id"]),
            float(row["max_velocity_rad_s"]),
        ): row
        for row in surface_rows
    }
    output: list[dict[str, Any]] = []
    low_vmax, high_vmax = VMAX_LEVELS_RAD_S
    for input_id in INPUT_IDS:
        for method_id in METHOD_IDS:
            if method_id == BASELINE_METHOD_ID:
                continue
            low = index[(input_id, method_id, low_vmax)]
            high = index[(input_id, method_id, high_vmax)]
            low_ratio = low["rmse_ratio_vs_p"]
            high_ratio = high["rmse_ratio_vs_p"]
            interaction = (
                None
                if low_ratio is None
                or high_ratio is None
                or float(low_ratio) <= 0.0
                or float(high_ratio) <= 0.0
                else math.log(float(low_ratio)) - math.log(float(high_ratio))
            )
            high_velocity_nonbinding = (
                int(high["velocity_clip_count"]) == 0
                and int(high["stopping_envelope_count"]) == 0
            )
            output.append(
                {
                    "input_id": input_id,
                    "input_acquisition_velocity_limited": (
                        INPUT_ACQUISITION_VELOCITY_LIMITED[input_id]
                    ),
                    "method_id": method_id,
                    "limited_vmax_rad_s": low_vmax,
                    "relaxed_vmax_rad_s": high_vmax,
                    "limited_rmse_ratio_vs_p": low_ratio,
                    "relaxed_rmse_ratio_vs_p": high_ratio,
                    "log_ratio_interaction_limited_minus_relaxed": interaction,
                    "limited_velocity_clip_count": low["velocity_clip_count"],
                    "relaxed_velocity_clip_count": high["velocity_clip_count"],
                    "limited_acceleration_clip_count": (
                        low["acceleration_clip_count"]
                    ),
                    "relaxed_acceleration_clip_count": (
                        high["acceleration_clip_count"]
                    ),
                    "limited_stopping_envelope_count": (
                        low["stopping_envelope_count"]
                    ),
                    "relaxed_stopping_envelope_count": (
                        high["stopping_envelope_count"]
                    ),
                    "relaxed_velocity_condition_nonbinding": (
                        high_velocity_nonbinding
                    ),
                    "velocity_limit_attribution_supported": bool(
                        low["integrity_pass"]
                        and high["integrity_pass"]
                        and int(low["velocity_clip_count"]) > 0
                        and high_velocity_nonbinding
                        and interaction is not None
                        and interaction > 0.0
                    ),
                }
            )
    return output


def write_vmax_ablation_artifacts(
    *,
    analysis_directory: Path,
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    **_: Any,
) -> None:
    surface = _surface_rows(tracking_runs, trajectory_rows)
    interactions = _interaction_rows(surface)
    write_rows_csv(analysis_directory / "vmax_ablation.csv", surface)
    write_rows_csv(analysis_directory / "vmax_interactions.csv", interactions)


__all__ = [
    "INPUT_IDS",
    "METHOD_IDS",
    "VMAX_LEVELS_RAD_S",
    "build_experiment",
    "write_vmax_ablation_artifacts",
]
