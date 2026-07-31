"""E13: joint P/PV/PVA comparison on the E07 stop-and-go matrix."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from otg_lab.analysis import (
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
    MetricRow,
)
from otg_lab.experiment import (
    ExperimentCase,
    ExperimentInput,
    ExperimentSpec,
    InputGate,
)
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    TrackingRun,
)
from otg_lab.recorded_experiments import (
    BASELINE_METHOD_ID,
    finite_difference_method_ids,
    metric_lookup,
    metric_value,
)
from otg_lab.runio import write_rows_csv
from otg_lab.trajectory_ablation import build_state_target_methods

DT_S = 0.01
DURATION_S = 3.0
MAIN_START_S = 0.5
MAIN_END_S = 2.5
MAX_VELOCITY_RAD_S = 4.1
VENDOR_ACCELERATION_RAD_S2 = 8.2
VENDOR_JERK_RAD_S3 = 4000.0
LIMIT_SCALES = (0.25, 0.5, 1.0, 2.0)
VENDOR_VELOCITY_RATIOS = (
    0.125,
    0.2,
    0.25,
    0.3,
    0.4,
    0.5,
    0.6,
    0.8,
    0.9,
    0.95,
    1.0,
    1.05,
    1.1,
    1.2,
    1.5,
    1.8,
    2.0,
    2.2,
    3.0,
    4.0,
)
OPERATIONAL_P_METHOD_ID = "position_zoh_p_ruckig"
PV_METHOD_IDS = finite_difference_method_ids("pv")
PVA_METHOD_IDS = finite_difference_method_ids("pva")
METHOD_IDS = (
    OPERATIONAL_P_METHOD_ID,
    BASELINE_METHOD_ID,
    *PV_METHOD_IDS,
    *PVA_METHOD_IDS,
)

PRIMARY = (
    "rest_to_rest_pulse_fraction",
    "stop_go_event_rate_hz",
)
SECONDARY = (
    "endpoint_stop_fraction",
    "longest_rest_to_rest_pulse_run_cycles",
    "profile_peak_velocity_to_reference_median",
    "profile_velocity_ripple_median",
    "profile_velocity_ripple_to_reference_median",
    "profile_velocity_ripple_to_reference_p95",
    "position_rmse",
    "lag_s",
)
GUARDRAIL = (
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
    if metric_id not in _ASSIGNED and metric_id not in {"settled", "settle_time_s"}
)


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def critical_reference_velocity(
    acceleration_rad_s2: float,
    jerk_rad_s3: float,
    dt_s: float = DT_S,
) -> float:
    acceleration = float(acceleration_rad_s2)
    jerk = float(jerk_rad_s3)
    duration = float(dt_s)
    if acceleration >= jerk * duration / 4.0:
        return jerk * duration**2 / 32.0
    return acceleration * duration / 4.0 - acceleration**2 / (2.0 * jerk)


VENDOR_CRITICAL_VELOCITY_RAD_S = critical_reference_velocity(
    VENDOR_ACCELERATION_RAD_S2,
    VENDOR_JERK_RAD_S3,
)
INPUTS = tuple(
    (
        f"e07_cv_vendor_ratio_{_token(ratio)}",
        ratio,
        ratio * VENDOR_CRITICAL_VELOCITY_RAD_S,
    )
    for ratio in VENDOR_VELOCITY_RATIOS
)


def _run_config(limit_scale: float = 1.0) -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=MAX_VELOCITY_RAD_S,
            max_acceleration_rad_s2=(
                limit_scale * VENDOR_ACCELERATION_RAD_S2
            ),
            max_jerk_rad_s3=limit_scale * VENDOR_JERK_RAD_S3,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _methods() -> tuple[TrackingMethodSpec, ...]:
    scheduled_and_pv = build_state_target_methods(
        "pv",
        include_truth=False,
        include_differences=True,
    )
    pva = tuple(
        method
        for method in build_state_target_methods(
            "pva",
            include_truth=False,
            include_differences=True,
        )
        if method.method_id != BASELINE_METHOD_ID
    )
    operational_p = TrackingMethodSpec(
        method_id=OPERATIONAL_P_METHOD_ID,
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        description=(
            "E07 operational baseline: PositionOnly → ZOH → P → "
            "ordinary unshielded Ruckig"
        ),
    )
    methods = (operational_p, *scheduled_and_pv, *pva)
    if tuple(method.method_id for method in methods) != METHOD_IDS:
        raise RuntimeError("joint P/PV/PVA method declarations changed")
    return tuple(
        TrackingMethodSpec(
            method_id=method.method_id,
            estimator=method.estimator,
            predictor=method.predictor,
            target_builder=method.target_builder,
            governor=method.governor,
            follower=method.follower,
            required=True,
            description=method.description,
        )
        for method in methods
    )


def _method_family(method_id: str) -> str:
    if method_id == OPERATIONAL_P_METHOD_ID:
        return "P_operational"
    if method_id == BASELINE_METHOD_ID:
        return "P_scheduled"
    return "PV" if method_id.startswith("pv_") else "PVA"


def _stencil(method_id: str) -> str:
    for prefix in ("pv_", "pva_"):
        if method_id.startswith(prefix):
            return method_id.removeprefix(prefix)
    return "p"


def _target_age(method_id: str) -> float:
    if method_id in {OPERATIONAL_P_METHOD_ID, BASELINE_METHOD_ID}:
        return 0.0
    if "centered_o2_km1" in method_id:
        return 2.0
    if "_pred_" in method_id:
        return 0.0
    return 1.0


def _case_id(method_id: str, limit_scale: float) -> str:
    return f"{method_id}__limit_s{_token(limit_scale)}"


def _cases(methods: Sequence[TrackingMethodSpec]) -> tuple[ExperimentCase, ...]:
    return tuple(
        ExperimentCase(
            case_id=_case_id(method.method_id, scale),
            method_id=method.method_id,
            run_config=_run_config(scale),
            factors={
                "method_rank": METHOD_IDS.index(method.method_id),
                "target_age_samples": _target_age(method.method_id),
                "limit_scale": scale,
                "max_acceleration_rad_s2": (
                    scale * VENDOR_ACCELERATION_RAD_S2
                ),
                "max_jerk_rad_s3": scale * VENDOR_JERK_RAD_S3,
                "e07_p_only_critical_velocity_rad_s": (
                    scale * VENDOR_CRITICAL_VELOCITY_RAD_S
                ),
            },
            description=(
                f"{_method_family(method.method_id)} / "
                f"{_stencil(method.method_id)}; A/J scale={scale:g}"
            ),
        )
        for method in methods
        for scale in LIMIT_SCALES
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    methods = _methods()
    candidate_ids = (*PV_METHOD_IDS, *PVA_METHOD_IDS)
    pairs = []
    for scale in LIMIT_SCALES:
        for candidate_id in candidate_ids:
            pairs.extend(
                (
                    MethodPair(
                        _case_id(OPERATIONAL_P_METHOD_ID, scale),
                        _case_id(candidate_id, scale),
                        f"{candidate_id}_vs_operational_p_s{_token(scale)}",
                    ),
                    MethodPair(
                        _case_id(BASELINE_METHOD_ID, scale),
                        _case_id(candidate_id, scale),
                        f"{candidate_id}_vs_scheduled_p_s{_token(scale)}",
                    ),
                )
            )
        for pv_id, pva_id in zip(PV_METHOD_IDS, PVA_METHOD_IDS):
            pairs.append(
                MethodPair(
                    _case_id(pv_id, scale),
                    _case_id(pva_id, scale),
                    f"{pva_id}_vs_matched_pv_s{_token(scale)}",
                )
            )
    return ExperimentSpec(
        experiment_id="E13",
        slug="pv_pva_stop_and_go",
        title="E13 joint P/PV/PVA stop-and-go comparison",
        question=(
            "Do PV and PVA targets suppress the E07 P-only stop-and-go "
            "mechanism, and does acceleration add a measurable benefit over "
            "matched PV on the mature constant-velocity window?"
        ),
        hypothesis=(
            "All mature PV/PVA finite differences reconstruct constant "
            "velocity and eliminate rest-to-rest pulses; matched PV and PVA "
            "are equivalent because the true and estimated acceleration is zero."
        ),
        description=(
            "Joint rerun of the E07 operational P baseline, the scheduled "
            "P[k+1] baseline, five PV stencils, and five matched PVA stencils "
            "on the full velocity × A/J matrix."
        ),
        independent_variables=(
            "target_components",
            "finite_difference_stencil",
            "reference_velocity_rad_s",
            "acceleration_jerk_limit_scale",
        ),
        controlled_variables={
            "input_source_experiment": "E07",
            "input_ids": tuple(input_id for input_id, _, _ in INPUTS),
            "dt_s": DT_S,
            "duration_s": DURATION_S,
            "measurement_policy": "position_only",
            "scheduled_position_available_one_step_ahead": True,
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "governor": "none",
            "follower": "ordinary_ruckig_unshielded",
            "initial_state_policy": "reference_position_zero_derivatives",
            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
            "main_evaluation_s": [MAIN_START_S, MAIN_END_S],
        },
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
            "run_config.limits.max_acceleration_rad_s2",
            "run_config.limits.max_jerk_rad_s3",
        ),
        inputs=tuple(
            ExperimentInput(
                input_id,
                f"experiments/E07_position_only_stop_and_go/inputs/{input_id}.csv",
                required=True,
                description=(
                    "E07 three-second constant-velocity reference; "
                    f"v={velocity:.17g} rad/s; vendor ratio={ratio:g}"
                ),
            )
            for input_id, ratio, velocity in INPUTS
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
                end_time_s=MAIN_END_S,
            ),
        ),
        comparison_spec=ComparisonSpec(
            pairs=tuple(pairs),
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=tuple(input_id for input_id, _, _ in INPUTS),
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=_cases(methods),
        artifact_writer=write_joint_stop_go_artifacts,
    )


def write_joint_stop_go_artifacts(
    *,
    analysis_directory: Path,
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    **_: Any,
) -> None:
    lookup = metric_lookup(trajectory_rows)
    rows: list[dict[str, Any]] = []
    metric_ids = PRIMARY + SECONDARY + GUARDRAIL + ("profile_exact_fraction",)
    for input_id, vendor_ratio, reference_velocity in INPUTS:
        for scale in LIMIT_SCALES:
            operational_case = _case_id(OPERATIONAL_P_METHOD_ID, scale)
            operational_pulse = metric_value(
                lookup,
                input_id,
                operational_case,
                "rest_to_rest_pulse_fraction",
                "main_evaluation",
            )
            operational_rate = metric_value(
                lookup,
                input_id,
                operational_case,
                "stop_go_event_rate_hz",
                "main_evaluation",
            )
            for method_id in METHOD_IDS:
                case_id = _case_id(method_id, scale)
                run = tracking_runs[(case_id, input_id)]
                metrics = {
                    metric_id: metric_value(
                        lookup,
                        input_id,
                        case_id,
                        metric_id,
                        (
                            "full_overlap"
                            if metric_id in GUARDRAIL
                            or metric_id == "profile_exact_fraction"
                            else "main_evaluation"
                        ),
                    )
                    for metric_id in metric_ids
                }
                pulse = metrics["rest_to_rest_pulse_fraction"]
                rate = metrics["stop_go_event_rate_hz"]
                rows.append(
                    {
                        "input_id": input_id,
                        "vendor_velocity_ratio": vendor_ratio,
                        "reference_velocity_rad_s": reference_velocity,
                        "method_id": method_id,
                        "method_family": _method_family(method_id),
                        "stencil": _stencil(method_id),
                        "target_age_samples": _target_age(method_id),
                        "case_id": case_id,
                        "limit_scale": scale,
                        "max_acceleration_rad_s2": (
                            scale * VENDOR_ACCELERATION_RAD_S2
                        ),
                        "max_jerk_rad_s3": scale * VENDOR_JERK_RAD_S3,
                        "completed": run.status.completed,
                        "valid_cycles": run.status.valid_cycles,
                        "total_cycles": run.status.total_cycles,
                        **metrics,
                        "pulse_fraction_reduction_vs_operational_p": (
                            None
                            if pulse is None or operational_pulse is None
                            else operational_pulse - pulse
                        ),
                        "event_rate_reduction_vs_operational_p_hz": (
                            None
                            if rate is None or operational_rate is None
                            else operational_rate - rate
                        ),
                    }
                )
    write_rows_csv(analysis_directory / "joint_stop_go_surface.csv", rows)


__all__ = [
    "INPUTS",
    "LIMIT_SCALES",
    "METHOD_IDS",
    "OPERATIONAL_P_METHOD_ID",
    "PVA_METHOD_IDS",
    "PV_METHOD_IDS",
    "build_experiment",
]
