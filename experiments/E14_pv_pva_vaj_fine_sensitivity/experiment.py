"""E14: fine three-dimensional VAJ sensitivity for selected PV/PVA targets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from otg_lab.analysis import (
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
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
from otg_lab.models import MotionLimits, RunConfig, TrackingMethodSpec, TrackingRun
from otg_lab.recorded_experiments import (
    metric_lookup,
    metric_value,
    projected_state_target_methods,
    projection_audit,
    value_token,
)
from otg_lab.runio import write_rows_csv

INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
INPUT_PATH = f"data/trajectories/{INPUT_ID}.csv"
RAW_INPUT_PATH = "data/raw/recorded_tasks/simplified_with_velocity_limit.csv"
DT_S = 0.01
MAIN_START_S = 0.04

# Predeclared refinement grid. The selected Future-O1 stencil is the only PV
# method that beat scheduled P in the current recorded transfer; its matched
# PVA arm isolates the incremental acceleration component.
METHOD_IDS = (
    "pv_pred_backward_o1_kp1",
    "pva_pred_backward_o1_kp1",
)
VELOCITY_LEVELS_RAD_S = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.1)
ACCELERATION_LEVELS_RAD_S2 = (2.0, 3.0, 4.1, 5.0, 6.0, 7.0, 7.5, 8.2)
JERK_LEVELS_RAD_S3 = (
    41.0,
    100.0,
    200.0,
    400.0,
    800.0,
    1200.0,
    1600.0,
    2400.0,
    3200.0,
    4000.0,
)
VENDOR_LIMITS = (4.1, 8.2, 4000.0)

PRIMARY = ("position_rmse",)
SECONDARY = (
    "position_mae",
    "position_bias",
    "position_p95_abs_error",
    "position_max_abs_error",
    "position_iae",
    "lag_s",
    "lag_subsample_s",
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


def _case_id(method_id: str, velocity: float, acceleration: float, jerk: float) -> str:
    return (
        f"{method_id}__v{value_token(velocity)}"
        f"_a{value_token(acceleration)}_j{value_token(jerk)}"
    )


def _run_config(velocity: float, acceleration: float, jerk: float) -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=velocity,
            max_acceleration_rad_s2=acceleration,
            max_jerk_rad_s3=jerk,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _methods() -> tuple[TrackingMethodSpec, ...]:
    available = {
        method.method_id: method
        for components in ("pv", "pva")
        for method in projected_state_target_methods(
            components,
            include_baseline=False,
        )
    }
    # Sensitivity arms are diagnostic: an infeasible low-limit point must be
    # retained as an ineligible surface coordinate without blocking all other
    # settings or the aggregate result.
    return tuple(
        replace(available[method_id], required=False)
        for method_id in METHOD_IDS
    )


def _cases(methods: Sequence[TrackingMethodSpec]) -> tuple[ExperimentCase, ...]:
    return tuple(
        ExperimentCase(
            case_id=_case_id(method.method_id, velocity, acceleration, jerk),
            method_id=method.method_id,
            run_config=_run_config(velocity, acceleration, jerk),
            factors={
                "target_components_rank": (
                    0.0 if method.method_id.startswith("pv_") else 1.0
                ),
                "max_velocity_rad_s": velocity,
                "max_acceleration_rad_s2": acceleration,
                "max_jerk_rad_s3": jerk,
            },
            description=(
                f"{method.method_id}; configured-limit projection; "
                f"V/A/J={velocity:g}/{acceleration:g}/{jerk:g}"
            ),
        )
        for method in methods
        for velocity in VELOCITY_LEVELS_RAD_S
        for acceleration in ACCELERATION_LEVELS_RAD_S2
        for jerk in JERK_LEVELS_RAD_S3
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    methods = _methods()
    return ExperimentSpec(
        experiment_id="E14",
        slug="pv_pva_vaj_fine_sensitivity",
        title="E14 fine PV/PVA velocity-acceleration-jerk sensitivity",
        question=(
            "For the selected recorded-task Future-O1 stencil, which tested "
            "V/A/J setting minimizes raw-time position RMSE for PV and PVA?"
        ),
        hypothesis=(
            "The fine 3D grid will identify a stable near-optimal VAJ region; "
            "the report will flag any boundary optimum rather than claiming "
            "an unconstrained global optimum."
        ),
        description=(
            "A full 8×8×10 VAJ grid for matched PV and PVA Future-O1 targets "
            "on the velocity-limited recorded curve. Eligibility uses hard "
            "execution guardrails; deadline is reported as sensitivity only."
        ),
        independent_variables=(
            "target_components",
            "max_velocity_rad_s",
            "max_acceleration_rad_s2",
            "max_jerk_rad_s3",
        ),
        controlled_variables={
            "input_id": INPUT_ID,
            "canonical_input_path": INPUT_PATH,
            "raw_input_path": RAW_INPUT_PATH,
            "finite_difference_stencil": "pred_backward_o1_kp1",
            "stencil_selection_source": (
                "A04 prerequisite; predeclared from current recorded PV transfer"
            ),
            "dt_s": DT_S,
            "measurement_policy": "position_only",
            "scheduled_position_available_one_step_ahead": True,
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "target_conditioning": "configured_limit_projection_per_case",
            "follower": "ordinary_ruckig_unshielded",
            "primary_window_start_s": MAIN_START_S,
            "near_optimal_tolerance": "position_rmse <= 1.01 * tested minimum",
        },
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
            "run_config.limits.max_velocity_rad_s",
            "run_config.limits.max_acceleration_rad_s2",
            "run_config.limits.max_jerk_rad_s3",
        ),
        inputs=(
            ExperimentInput(
                INPUT_ID,
                INPUT_PATH,
                required=True,
                description=(
                    f"Fixed-grid position-only conversion of {RAW_INPUT_PATH}"
                ),
            ),
        ),
        methods=methods,
        run_config=_run_config(*VENDOR_LIMITS),
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
            pairs=(),
            metric_ids=(),
            input_ids=(INPUT_ID,),
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=_cases(methods),
        artifact_writer=write_vaj_sensitivity_artifacts,
    )


def _surface_rows(
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    cases: Sequence[ExperimentCase],
) -> list[dict[str, Any]]:
    lookup = metric_lookup(trajectory_rows)
    vendor_rmse = {
        method_id: metric_value(
            lookup,
            INPUT_ID,
            _case_id(method_id, *VENDOR_LIMITS),
            "position_rmse",
            "main_evaluation",
        )
        for method_id in METHOD_IDS
    }
    output: list[dict[str, Any]] = []
    for case in cases:
        method_id = case.method_id
        case_id = case.case_id
        velocity = case.run_config.limits.max_velocity_rad_s
        acceleration = case.run_config.limits.max_acceleration_rad_s2
        jerk = case.run_config.limits.max_jerk_rad_s3
        run = tracking_runs[(case_id, INPUT_ID)]
        rmse = metric_value(
            lookup,
            INPUT_ID,
            case_id,
            "position_rmse",
            "main_evaluation",
        )
        lag = metric_value(
            lookup,
            INPUT_ID,
            case_id,
            "lag_s",
            "main_evaluation",
        )
        subsample_lag = metric_value(
            lookup,
            INPUT_ID,
            case_id,
            "lag_subsample_s",
            "main_evaluation",
        )
        guardrails = {
            metric_id: metric_value(
                lookup,
                INPUT_ID,
                case_id,
                metric_id,
                "full_overlap",
            )
            for metric_id in GUARDRAIL
        }
        limits = NumericalMotionLimits.broadcast(
            1,
            velocity,
            acceleration,
            jerk,
        )
        projection = projection_audit(run, limits)
        eligible = bool(
            run.status.completed
            and rmse is not None
            and all(
                guardrails[metric_id] is not None
                and abs(float(guardrails[metric_id])) <= 1e-12
                for metric_id in INTEGRITY_GUARDRAILS
            )
            and projection["position_projection_max_abs_rad"] is not None
            and float(projection["position_projection_max_abs_rad"]) <= 1e-12
            and projection["executable_target_inadmissible_count"] == 0
            and projection["projection_reconstruction_mismatch_count"] == 0
        )
        vendor = vendor_rmse[method_id]
        output.append(
            {
                "input_id": INPUT_ID,
                "method_id": method_id,
                "target_components": ("PV" if method_id.startswith("pv_") else "PVA"),
                "case_id": case_id,
                "max_velocity_rad_s": velocity,
                "max_acceleration_rad_s2": acceleration,
                "max_jerk_rad_s3": jerk,
                "is_vendor_setting": ((velocity, acceleration, jerk) == VENDOR_LIMITS),
                "completed": run.status.completed,
                "valid_cycles": run.status.valid_cycles,
                "total_cycles": run.status.total_cycles,
                "position_rmse_rad": rmse,
                "vendor_position_rmse_rad": vendor,
                "rmse_ratio_vs_own_vendor": (
                    None
                    if rmse is None or vendor is None or vendor <= 0.0
                    else rmse / vendor
                ),
                "lag_s": lag,
                "lag_ms": None if lag is None else 1000.0 * lag,
                "lag_subsample_s": subsample_lag,
                "lag_subsample_ms": (
                    None
                    if subsample_lag is None
                    else 1000.0 * subsample_lag
                ),
                **projection,
                **guardrails,
                "eligible": eligible,
            }
        )
    return output


def _recommendation_rows(
    surface: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    def as_true(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return value is True

    output: list[dict[str, Any]] = []
    for method_id in METHOD_IDS:
        eligible = [
            row
            for row in surface
            if row["method_id"] == method_id
            and as_true(row["eligible"])
            and row["position_rmse_rad"] is not None
        ]
        if not eligible:
            output.append(
                {
                    "method_id": method_id,
                    "target_components": (
                        "PV" if method_id.startswith("pv_") else "PVA"
                    ),
                    "eligible_case_count": 0,
                    "best_case_id": "",
                    "best_max_velocity_rad_s": "",
                    "best_max_acceleration_rad_s2": "",
                    "best_max_jerk_rad_s3": "",
                    "best_position_rmse_rad": "",
                    "near_optimal_case_count": 0,
                    "near_optimal_nondominated_case_count": 0,
                    "boundary_censored": "",
                }
            )
            continue
        best = min(
            eligible,
            key=lambda row: (
                float(row["position_rmse_rad"]),
                float(row["max_velocity_rad_s"]),
                float(row["max_acceleration_rad_s2"]),
                float(row["max_jerk_rad_s3"]),
            ),
        )
        best_rmse = float(best["position_rmse_rad"])
        near = [
            row
            for row in eligible
            if float(row["position_rmse_rad"]) <= 1.01 * best_rmse
        ]

        def dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
            left_values = (
                float(left["position_rmse_rad"]),
                float(left["max_velocity_rad_s"]),
                float(left["max_acceleration_rad_s2"]),
                float(left["max_jerk_rad_s3"]),
            )
            right_values = (
                float(right["position_rmse_rad"]),
                float(right["max_velocity_rad_s"]),
                float(right["max_acceleration_rad_s2"]),
                float(right["max_jerk_rad_s3"]),
            )
            return all(a <= b for a, b in zip(left_values, right_values)) and any(
                a < b for a, b in zip(left_values, right_values)
            )

        nondominated = [
            row
            for row in near
            if not any(
                dominates(other, row)
                for other in near
                if other["case_id"] != row["case_id"]
            )
        ]
        velocity = float(best["max_velocity_rad_s"])
        acceleration = float(best["max_acceleration_rad_s2"])
        jerk = float(best["max_jerk_rad_s3"])
        boundary = (
            velocity in {min(VELOCITY_LEVELS_RAD_S), max(VELOCITY_LEVELS_RAD_S)}
            or acceleration
            in {
                min(ACCELERATION_LEVELS_RAD_S2),
                max(ACCELERATION_LEVELS_RAD_S2),
            }
            or jerk in {min(JERK_LEVELS_RAD_S3), max(JERK_LEVELS_RAD_S3)}
        )
        output.append(
            {
                "method_id": method_id,
                "target_components": ("PV" if method_id.startswith("pv_") else "PVA"),
                "eligible_case_count": len(eligible),
                "best_case_id": best["case_id"],
                "best_max_velocity_rad_s": velocity,
                "best_max_acceleration_rad_s2": acceleration,
                "best_max_jerk_rad_s3": jerk,
                "best_position_rmse_rad": best_rmse,
                "near_optimal_case_count": len(near),
                "near_optimal_nondominated_case_count": len(nondominated),
                "boundary_censored": boundary,
            }
        )
    return output


def write_vaj_sensitivity_artifacts(
    *,
    analysis_directory: Path,
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    experiment_spec: ExperimentSpec,
    **_: Any,
) -> None:
    surface = _surface_rows(
        tracking_runs,
        trajectory_rows,
        experiment_spec.cases,
    )
    recommendations = _recommendation_rows(surface)
    write_rows_csv(analysis_directory / "vaj_sensitivity.csv", surface)
    write_rows_csv(
        analysis_directory / "vaj_recommendations.csv",
        recommendations,
    )


__all__ = [
    "ACCELERATION_LEVELS_RAD_S2",
    "INPUT_ID",
    "JERK_LEVELS_RAD_S3",
    "METHOD_IDS",
    "VELOCITY_LEVELS_RAD_S",
    "build_experiment",
]
