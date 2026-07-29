"""E10: projected causal-PVA acceleration/jerk limit sensitivity."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from otg_lab.analysis import (
    AVAILABLE,
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
    MetricRow,
    get_metric_spec,
)
from otg_lab.constraints import ruckig_target_admissible
from otg_lab.experiment import (
    ExperimentCase,
    ExperimentInput,
    ExperimentSpec,
    InputGate,
)
from otg_lab.governors import MotionLimits as NumericalMotionLimits
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    TrackingRun,
)
from otg_lab.runio import write_rows_csv
from otg_lab.trajectory_ablation import (
    BASELINE_METHOD_ID as E04_BASELINE_METHOD_ID,
)
from otg_lab.trajectory_ablation import build_state_target_methods

ORIGINAL_INPUT_ID = "recorded_tasks_original_no_velocity_limit"
SIMPLIFIED_INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
INPUT_IDS = (ORIGINAL_INPUT_ID, SIMPLIFIED_INPUT_ID)
INPUT_PATHS = {
    ORIGINAL_INPUT_ID: (
        f"data/trajectories/{ORIGINAL_INPUT_ID}.csv",
        "data/raw/recorded_tasks/original_no_velocity_limit.csv",
    ),
    SIMPLIFIED_INPUT_ID: (
        f"data/trajectories/{SIMPLIFIED_INPUT_ID}.csv",
        "data/raw/recorded_tasks/simplified_with_velocity_limit.csv",
    ),
}

DT_S = 0.01
MAIN_START_S = 0.04
MAX_VELOCITY_RAD_S = 4.1
ACCELERATION_LEVELS_RAD_S2 = (4.1, 6.0, 8.2, 12.0, 16.4)
JERK_LEVELS_RAD_S3 = (
    41.0,
    200.0,
    800.0,
    1600.0,
    3200.0,
    4000.0,
    8000.0,
)
VENDOR_ACCELERATION_RAD_S2 = 8.2
VENDOR_JERK_RAD_S3 = 4000.0

METHOD_IDS = (
    "pva_est_backward_o1_k",
    "pva_est_backward_o2_k",
    "pva_est_centered_o2_km1",
    "pva_pred_backward_o1_kp1",
    "pva_pred_backward_o2_kp1",
)
METHOD_LABELS = {
    "pva_est_backward_o1_k": "Endpoint backward O1 · PVA[k]",
    "pva_est_backward_o2_k": "Endpoint backward O2 · PVA[k]",
    "pva_est_centered_o2_km1": "Delayed centered O2 · PVA[k−1]",
    "pva_pred_backward_o1_kp1": "Future backward O1 · PVA[k+1]",
    "pva_pred_backward_o2_kp1": "Future backward O2 · PVA[k+1]",
}
TARGET_AGE_SAMPLES = {
    "pva_est_backward_o1_k": 1.0,
    "pva_est_backward_o2_k": 1.0,
    "pva_est_centered_o2_km1": 2.0,
    "pva_pred_backward_o1_kp1": 0.0,
    "pva_pred_backward_o2_kp1": 0.0,
}
METHOD_COLORS = {
    "pva_est_backward_o1_k": "#4477AA",
    "pva_est_backward_o2_k": "#66CCEE",
    "pva_est_centered_o2_km1": "#228833",
    "pva_pred_backward_o1_kp1": "#EE6677",
    "pva_pred_backward_o2_kp1": "#AA3377",
}

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


def _value_token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def _case_id(method_id: str, acceleration: float, jerk: float) -> str:
    return f"{method_id}__a{_value_token(acceleration)}_j{_value_token(jerk)}"


def _vendor_case_id(method_id: str) -> str:
    return _case_id(
        method_id,
        VENDOR_ACCELERATION_RAD_S2,
        VENDOR_JERK_RAD_S3,
    )


def _run_config(acceleration: float, jerk: float) -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=MAX_VELOCITY_RAD_S,
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
    shared = build_state_target_methods(
        "pva",
        include_truth=False,
        include_differences=True,
    )
    finite_differences = tuple(
        method for method in shared if method.method_id != E04_BASELINE_METHOD_ID
    )
    if tuple(method.method_id for method in finite_differences) != METHOD_IDS:
        raise RuntimeError("E04 finite-difference method declarations changed")
    governor = ComponentSpec("configured_limit_projection")
    return tuple(
        replace(method, governor=governor, required=True)
        for method in finite_differences
    )


def _cases(methods: Sequence[TrackingMethodSpec]) -> tuple[ExperimentCase, ...]:
    return tuple(
        ExperimentCase(
            case_id=_case_id(method.method_id, acceleration, jerk),
            method_id=method.method_id,
            run_config=_run_config(acceleration, jerk),
            factors={
                "method_rank": METHOD_IDS.index(method.method_id),
                "target_age_samples": TARGET_AGE_SAMPLES[method.method_id],
                "max_acceleration_rad_s2": acceleration,
                "max_jerk_rad_s3": jerk,
            },
            description=(
                f"{METHOD_LABELS[method.method_id]}; configured-limit "
                f"projection; A={acceleration:g} rad/s², J={jerk:g} rad/s³"
            ),
        )
        for method in methods
        for acceleration in ACCELERATION_LEVELS_RAD_S2
        for jerk in JERK_LEVELS_RAD_S3
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    methods = _methods()
    cases = _cases(methods)
    pairs = tuple(
        MethodPair(
            baseline_method_id=_vendor_case_id(method_id),
            candidate_method_id=_case_id(method_id, acceleration, jerk),
            comparison_id=(f"{_case_id(method_id, acceleration, jerk)}_vs_own_vendor"),
        )
        for method_id in METHOD_IDS
        for acceleration in ACCELERATION_LEVELS_RAD_S2
        for jerk in JERK_LEVELS_RAD_S3
        if (acceleration != VENDOR_ACCELERATION_RAD_S2 or jerk != VENDOR_JERK_RAD_S3)
    )
    return ExperimentSpec(
        experiment_id="E10",
        slug="pva_finite_difference_limit_sensitivity",
        title="E10 projected PVA acceleration–jerk limit sensitivity",
        question=(
            "For each causal finite-difference PVA method, how does raw-time "
            "position tracking change across E02's acceleration × jerk surface "
            "when targets are projected to each case's configured limits?"
        ),
        hypothesis=(
            "Each PVA method has a distinct descriptive sensitivity surface; "
            "tighter limits increase raw-time position error and target "
            "projection, while lag changes remain diagnostic."
        ),
        description=(
            "Limit-conditioned/projected PVA sensitivity. Each method is "
            "normalized only to its own A=8.2, J=4000 vendor case. Projection "
            "is part of the executed pipeline, so results are not a pure "
            "unconditioned-follower sensitivity or a deployment recommendation."
        ),
        independent_variables=(
            "input_curve",
            "pva_finite_difference_method",
            "max_acceleration_rad_s2",
            "max_jerk_rad_s3",
        ),
        controlled_variables={
            "input_ids": INPUT_IDS,
            "raw_source_paths": {
                input_id: paths[1] for input_id, paths in INPUT_PATHS.items()
            },
            "canonical_input_paths": {
                input_id: paths[0] for input_id, paths in INPUT_PATHS.items()
            },
            "axis_count": 1,
            "dt_s": DT_S,
            "fixed_grid": True,
            "measurement_policy": "position_only",
            "scheduled_position_available_one_step_ahead": True,
            "initial_state_policy": "reference_position_zero_derivatives",
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
            "target_conditioning": "configured_limit_projection_per_case",
            "projection_position_policy": "unchanged",
            "projection_velocity_policy": (
                "clip_to_case_max_then_case_jerk_stopping_envelope"
            ),
            "projection_acceleration_policy": "clip_to_case_max",
            "follower": "ordinary_ruckig_unshielded",
            "vendor_reference": {
                "max_acceleration_rad_s2": VENDOR_ACCELERATION_RAD_S2,
                "max_jerk_rad_s3": VENDOR_JERK_RAD_S3,
                "normalization": "within_method_only",
            },
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
                canonical_path,
                required=True,
                description=(
                    "Fixed-grid position-only recorded-task conversion of "
                    f"{raw_path}; no derivative truth"
                ),
            )
            for input_id, (canonical_path, raw_path) in INPUT_PATHS.items()
        ),
        methods=methods,
        run_config=_run_config(
            VENDOR_ACCELERATION_RAD_S2,
            VENDOR_JERK_RAD_S3,
        ),
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
            pairs=pairs,
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=INPUT_IDS,
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=cases,
        artifact_writer=write_limit_sensitivity_artifacts,
    )


def _metric_lookup(
    rows: Sequence[MetricRow],
) -> dict[tuple[str, str, str, str], MetricRow]:
    return {
        (row.method_id, row.input_id, row.window_id, row.metric_id): row for row in rows
    }


def _metric_value(
    lookup: Mapping[tuple[str, str, str, str], MetricRow],
    input_id: str,
    case_id: str,
    metric_id: str,
    window_id: str,
) -> float | None:
    row = lookup.get((case_id, input_id, window_id, metric_id))
    if (
        row is None
        or row.status != AVAILABLE
        or row.value is None
        or isinstance(row.value, bool)
    ):
        return None
    value = float(row.value)
    return value if math.isfinite(value) else None


def _as_true(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _successful_trace_rows(run: TrackingRun) -> list[Mapping[str, Any]]:
    return [
        row
        for row in run.trace_rows
        if str(row.get("status", "")).lower() == "ok"
        and row.get("raw_target_position_rad") is not None
        and row.get("executable_target_position_rad") is not None
    ]


def _projection_audit(
    run: TrackingRun,
    limits: NumericalMotionLimits,
) -> dict[str, Any]:
    rows = _successful_trace_rows(run)
    distortion = np.asarray(
        [
            (
                float(row["executable_target_position_rad"])
                - float(row["raw_target_position_rad"]),
                float(row["executable_target_velocity_rad_s"])
                - float(row["raw_target_velocity_rad_s"]),
                float(row["executable_target_acceleration_rad_s2"])
                - float(row["raw_target_acceleration_rad_s2"]),
            )
            for row in rows
        ],
        dtype=float,
    )
    if distortion.size == 0:
        distortion = np.empty((0, 3), dtype=float)
    projected_mask = (
        np.any(np.abs(distortion) > 1e-12, axis=1)
        if distortion.shape[0]
        else np.asarray([], dtype=bool)
    )
    projected_rows = [row for row, projected in zip(rows, projected_mask) if projected]
    first = projected_rows[0] if projected_rows else None
    inadmissible_count = sum(
        not ruckig_target_admissible(
            [
                float(row["executable_target_position_rad"]),
                float(row["executable_target_velocity_rad_s"]),
                float(row["executable_target_acceleration_rad_s2"]),
            ],
            limits,
        )
        for row in rows
    )
    denominator = run.status.total_cycles
    return {
        "projection_count": len(projected_rows),
        "projection_rate": (
            None if denominator <= 0 else len(projected_rows) / denominator
        ),
        "first_projection_cycle_index": (
            None if first is None else first.get("cycle_index")
        ),
        "first_projection_measurement_time_s": (
            None if first is None else first.get("measurement_time_s")
        ),
        "first_projection_command_time_s": (
            None if first is None else first.get("command_time_s")
        ),
        "position_projection_max_abs_rad": (
            None if not distortion.shape[0] else float(np.max(np.abs(distortion[:, 0])))
        ),
        "velocity_projection_rmse_rad_s": (
            None
            if not distortion.shape[0]
            else float(np.sqrt(np.mean(distortion[:, 1] ** 2)))
        ),
        "velocity_projection_max_abs_rad_s": (
            None if not distortion.shape[0] else float(np.max(np.abs(distortion[:, 1])))
        ),
        "acceleration_projection_rmse_rad_s2": (
            None
            if not distortion.shape[0]
            else float(np.sqrt(np.mean(distortion[:, 2] ** 2)))
        ),
        "acceleration_projection_max_abs_rad_s2": (
            None if not distortion.shape[0] else float(np.max(np.abs(distortion[:, 2])))
        ),
        "executable_target_inadmissible_count": inadmissible_count,
    }


def _raw_target_scan_rows(
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for input_id in INPUT_IDS:
        for method_id in METHOD_IDS:
            run = tracking_runs[(_vendor_case_id(method_id), input_id)]
            for row in run.trace_rows:
                if row.get("raw_target_position_rad") is None:
                    continue
                output.append(
                    {
                        "input_id": input_id,
                        "method_id": method_id,
                        "source_case_id": _vendor_case_id(method_id),
                        "cycle_index": row.get("cycle_index"),
                        "measurement_time_s": row.get("measurement_time_s"),
                        "command_time_s": row.get("command_time_s"),
                        "target_time_s": row.get("raw_target_time_s"),
                        "target_available_time_s": row.get(
                            "raw_target_available_time_s"
                        ),
                        "target_age_samples": row.get("raw_target_age_samples"),
                        "target_position_rad": row.get("raw_target_position_rad"),
                        "target_velocity_rad_s": row.get("raw_target_velocity_rad_s"),
                        "target_acceleration_rad_s2": row.get(
                            "raw_target_acceleration_rad_s2"
                        ),
                        "target_status": row.get("raw_target_status"),
                        "target_startup": row.get("raw_target_startup"),
                        "target_causal": row.get("raw_target_causal"),
                        "position_source": row.get("raw_target_position_source"),
                        "derivative_source": row.get("raw_target_derivative_source"),
                        "latest_position_input_time_s": row.get(
                            "raw_target_latest_input_time_s"
                        ),
                    }
                )
    return output


def _raw_target_feasibility_rows(
    scan_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for input_id in INPUT_IDS:
        for method_id in METHOD_IDS:
            method_rows = [
                row
                for row in scan_rows
                if row["input_id"] == input_id and row["method_id"] == method_id
            ]
            mature = [row for row in method_rows if not _as_true(row["target_startup"])]
            velocity = np.asarray(
                [float(row["target_velocity_rad_s"]) for row in mature],
                dtype=float,
            )
            acceleration = np.asarray(
                [float(row["target_acceleration_rad_s2"]) for row in mature],
                dtype=float,
            )
            for acceleration_limit in ACCELERATION_LEVELS_RAD_S2:
                for jerk_limit in JERK_LEVELS_RAD_S3:
                    limits = NumericalMotionLimits.broadcast(
                        1,
                        MAX_VELOCITY_RAD_S,
                        acceleration_limit,
                        jerk_limit,
                    )
                    velocity_violation = [
                        row
                        for row in mature
                        if abs(float(row["target_velocity_rad_s"]))
                        > MAX_VELOCITY_RAD_S + 1e-10
                    ]
                    acceleration_violation = [
                        row
                        for row in mature
                        if abs(float(row["target_acceleration_rad_s2"]))
                        > acceleration_limit + 1e-10
                    ]
                    inadmissible = [
                        row
                        for row in mature
                        if not ruckig_target_admissible(
                            [
                                float(row["target_position_rad"]),
                                float(row["target_velocity_rad_s"]),
                                float(row["target_acceleration_rad_s2"]),
                            ],
                            limits,
                        )
                    ]
                    first = inadmissible[0] if inadmissible else None
                    mature_count = len(mature)
                    output.append(
                        {
                            "input_id": input_id,
                            "method_id": method_id,
                            "case_id": _case_id(
                                method_id,
                                acceleration_limit,
                                jerk_limit,
                            ),
                            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
                            "max_acceleration_rad_s2": acceleration_limit,
                            "max_jerk_rad_s3": jerk_limit,
                            "total_cycle_count": len(method_rows),
                            "startup_cycle_count": (len(method_rows) - mature_count),
                            "mature_cycle_count": mature_count,
                            "target_age_samples": (
                                None
                                if not mature
                                else float(
                                    np.median(
                                        [
                                            float(row["target_age_samples"])
                                            for row in mature
                                        ]
                                    )
                                )
                            ),
                            "target_velocity_max_abs_rad_s": (
                                None
                                if not mature_count
                                else float(np.max(np.abs(velocity)))
                            ),
                            "target_velocity_p95_abs_rad_s": (
                                None
                                if not mature_count
                                else float(np.quantile(np.abs(velocity), 0.95))
                            ),
                            "velocity_limit_violation_count": len(velocity_violation),
                            "velocity_limit_violation_rate": (
                                None
                                if not mature_count
                                else len(velocity_violation) / mature_count
                            ),
                            "target_acceleration_max_abs_rad_s2": (
                                None
                                if not mature_count
                                else float(np.max(np.abs(acceleration)))
                            ),
                            "target_acceleration_p95_abs_rad_s2": (
                                None
                                if not mature_count
                                else float(np.quantile(np.abs(acceleration), 0.95))
                            ),
                            "acceleration_limit_violation_count": len(
                                acceleration_violation
                            ),
                            "acceleration_limit_violation_rate": (
                                None
                                if not mature_count
                                else len(acceleration_violation) / mature_count
                            ),
                            "ruckig_inadmissible_count": len(inadmissible),
                            "ruckig_inadmissible_rate": (
                                None
                                if not mature_count
                                else len(inadmissible) / mature_count
                            ),
                            "first_inadmissible_cycle_index": (
                                None if first is None else first["cycle_index"]
                            ),
                            "first_inadmissible_measurement_time_s": (
                                None if first is None else first["measurement_time_s"]
                            ),
                            "first_inadmissible_command_time_s": (
                                None if first is None else first["command_time_s"]
                            ),
                            "first_inadmissible_target_velocity_rad_s": (
                                None
                                if first is None
                                else first["target_velocity_rad_s"]
                            ),
                            "first_inadmissible_target_acceleration_rad_s2": (
                                None
                                if first is None
                                else first["target_acceleration_rad_s2"]
                            ),
                        }
                    )
    return output


def _surface_rows(
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    feasibility_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    metric_lookup = _metric_lookup(trajectory_rows)
    feasibility = {
        (str(row["input_id"]), str(row["case_id"])): row for row in feasibility_rows
    }
    vendor_metrics = {
        (input_id, method_id): {
            "position_rmse": _metric_value(
                metric_lookup,
                input_id,
                _vendor_case_id(method_id),
                "position_rmse",
                "main_evaluation",
            ),
            "lag_s": _metric_value(
                metric_lookup,
                input_id,
                _vendor_case_id(method_id),
                "lag_s",
                "main_evaluation",
            ),
        }
        for input_id in INPUT_IDS
        for method_id in METHOD_IDS
    }
    output: list[dict[str, Any]] = []
    for input_id in INPUT_IDS:
        for method_id in METHOD_IDS:
            vendor_rmse = vendor_metrics[(input_id, method_id)]["position_rmse"]
            vendor_lag = vendor_metrics[(input_id, method_id)]["lag_s"]
            limit_grid = (
                (acceleration, jerk)
                for acceleration in ACCELERATION_LEVELS_RAD_S2
                for jerk in JERK_LEVELS_RAD_S3
            )
            for acceleration, jerk in limit_grid:
                case_id = _case_id(method_id, acceleration, jerk)
                run = tracking_runs[(case_id, input_id)]
                limits = NumericalMotionLimits.broadcast(
                    1,
                    MAX_VELOCITY_RAD_S,
                    acceleration,
                    jerk,
                )
                projection = _projection_audit(run, limits)
                rmse = (
                    _metric_value(
                        metric_lookup,
                        input_id,
                        case_id,
                        "position_rmse",
                        "main_evaluation",
                    )
                    if run.status.completed
                    else None
                )
                lag = (
                    _metric_value(
                        metric_lookup,
                        input_id,
                        case_id,
                        "lag_s",
                        "main_evaluation",
                    )
                    if run.status.completed
                    else None
                )
                ratio = (
                    None
                    if rmse is None or vendor_rmse is None or vendor_rmse <= 0.0
                    else rmse / vendor_rmse
                )
                lag_delta_ms = (
                    None
                    if lag is None or vendor_lag is None
                    else 1000.0 * (lag - vendor_lag)
                )
                guardrails = {
                    metric_id: _metric_value(
                        metric_lookup,
                        input_id,
                        case_id,
                        metric_id,
                        "full_overlap",
                    )
                    for metric_id in GUARDRAIL
                }
                guardrail_integrity = all(
                    guardrails[metric_id] is not None
                    and abs(float(guardrails[metric_id])) <= 1e-12
                    for metric_id in INTEGRITY_GUARDRAILS
                )
                position_projection_ok = (
                    projection["position_projection_max_abs_rad"] is not None
                    and float(projection["position_projection_max_abs_rad"]) <= 1e-12
                )
                integrity_pass = bool(
                    run.status.completed
                    and rmse is not None
                    and lag is not None
                    and guardrail_integrity
                    and position_projection_ok
                    and projection["executable_target_inadmissible_count"] == 0
                )
                if not run.status.completed:
                    status = "unavailable_incomplete"
                elif rmse is None or lag is None:
                    status = "unavailable_metric"
                elif not integrity_pass:
                    status = "available_guardrail_failure"
                else:
                    status = AVAILABLE
                raw_feasibility = feasibility[(input_id, case_id)]
                output.append(
                    {
                        "input_id": input_id,
                        "method_id": method_id,
                        "method_label": METHOD_LABELS[method_id],
                        "case_id": case_id,
                        "is_vendor_baseline": (case_id == _vendor_case_id(method_id)),
                        "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
                        "max_acceleration_rad_s2": acceleration,
                        "max_jerk_rad_s3": jerk,
                        "completed": run.status.completed,
                        "valid_cycles": run.status.valid_cycles,
                        "total_cycles": run.status.total_cycles,
                        "position_rmse_rad": rmse,
                        "vendor_position_rmse_rad": vendor_rmse,
                        "rmse_ratio_vs_own_vendor": ratio,
                        "log2_rmse_ratio_vs_own_vendor": (
                            None if ratio is None else math.log2(ratio)
                        ),
                        "lag_s": lag,
                        "lag_ms": None if lag is None else 1000.0 * lag,
                        "vendor_lag_s": vendor_lag,
                        "vendor_lag_ms": (
                            None if vendor_lag is None else 1000.0 * vendor_lag
                        ),
                        "lag_delta_vs_own_vendor_ms": lag_delta_ms,
                        **projection,
                        "raw_target_ruckig_inadmissible_count": (
                            raw_feasibility["ruckig_inadmissible_count"]
                        ),
                        "raw_target_ruckig_inadmissible_rate": (
                            raw_feasibility["ruckig_inadmissible_rate"]
                        ),
                        "raw_target_velocity_limit_violation_count": (
                            raw_feasibility["velocity_limit_violation_count"]
                        ),
                        "raw_target_acceleration_limit_violation_count": (
                            raw_feasibility["acceleration_limit_violation_count"]
                        ),
                        **guardrails,
                        "integrity_pass": integrity_pass,
                        "status": status,
                        "failure_layer": run.status.failure_layer,
                        "failure_reason": run.status.failure_reason,
                        "prefix_rmse_used": False,
                    }
                )
    return output


def _by_method_metric_rows(
    rows: Sequence[Mapping[str, Any]],
    metric: str,
) -> list[dict[str, Any]]:
    common = (
        "input_id",
        "method_id",
        "case_id",
        "is_vendor_baseline",
        "max_velocity_rad_s",
        "max_acceleration_rad_s2",
        "max_jerk_rad_s3",
        "completed",
        "status",
    )
    fields = {
        "rmse": (
            "position_rmse_rad",
            "vendor_position_rmse_rad",
            "rmse_ratio_vs_own_vendor",
            "log2_rmse_ratio_vs_own_vendor",
        ),
        "lag_ms": (
            "lag_s",
            "lag_ms",
            "vendor_lag_s",
            "vendor_lag_ms",
            "lag_delta_vs_own_vendor_ms",
        ),
        "projection_rate": (
            "projection_count",
            "projection_rate",
            "first_projection_cycle_index",
            "position_projection_max_abs_rad",
            "velocity_projection_rmse_rad_s",
            "velocity_projection_max_abs_rad_s",
            "acceleration_projection_rmse_rad_s2",
            "acceleration_projection_max_abs_rad_s2",
            "raw_target_ruckig_inadmissible_count",
            "raw_target_ruckig_inadmissible_rate",
            "executable_target_inadmissible_count",
        ),
    }[metric]
    return [{field: row.get(field) for field in (*common, *fields)} for row in rows]


def _method_summary_rows(
    surface_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for input_id in INPUT_IDS:
        for method_id in METHOD_IDS:
            rows = [
                row
                for row in surface_rows
                if row["input_id"] == input_id and row["method_id"] == method_id
            ]
            output.append(_method_summary_row(input_id, method_id, rows))
    return output


def _method_summary_row(
    input_id: str,
    method_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [
        row
        for row in rows
        if row["input_id"] == input_id and row["method_id"] == method_id
    ]
    ratios = [row for row in rows if row["rmse_ratio_vs_own_vendor"] is not None]
    lag_rows = [row for row in rows if row["lag_delta_vs_own_vendor_ms"] is not None]
    projection_rows = [row for row in rows if row["projection_rate"] is not None]
    minimum_ratio = (
        None
        if not ratios
        else min(
            ratios,
            key=lambda row: float(row["rmse_ratio_vs_own_vendor"]),
        )
    )
    maximum_ratio = (
        None
        if not ratios
        else max(
            ratios,
            key=lambda row: float(row["rmse_ratio_vs_own_vendor"]),
        )
    )
    vendor = next(row for row in rows if bool(row["is_vendor_baseline"]))
    return {
        "input_id": input_id,
        "method_id": method_id,
        "method_label": METHOD_LABELS[method_id],
        "case_count": len(rows),
        "completed_case_count": sum(bool(row["completed"]) for row in rows),
        "integrity_pass_case_count": sum(bool(row["integrity_pass"]) for row in rows),
        "vendor_case_id": vendor["case_id"],
        "vendor_position_rmse_rad": vendor["position_rmse_rad"],
        "vendor_lag_ms": vendor["lag_ms"],
        "vendor_projection_rate": vendor["projection_rate"],
        "minimum_rmse_ratio": (
            None if minimum_ratio is None else minimum_ratio["rmse_ratio_vs_own_vendor"]
        ),
        "minimum_rmse_ratio_case_id": (
            None if minimum_ratio is None else minimum_ratio["case_id"]
        ),
        "maximum_rmse_ratio": (
            None if maximum_ratio is None else maximum_ratio["rmse_ratio_vs_own_vendor"]
        ),
        "maximum_rmse_ratio_case_id": (
            None if maximum_ratio is None else maximum_ratio["case_id"]
        ),
        "minimum_lag_delta_ms": (
            None
            if not lag_rows
            else min(float(row["lag_delta_vs_own_vendor_ms"]) for row in lag_rows)
        ),
        "maximum_lag_delta_ms": (
            None
            if not lag_rows
            else max(float(row["lag_delta_vs_own_vendor_ms"]) for row in lag_rows)
        ),
        "minimum_projection_rate": (
            None
            if not projection_rows
            else min(float(row["projection_rate"]) for row in projection_rows)
        ),
        "maximum_projection_rate": (
            None
            if not projection_rows
            else max(float(row["projection_rate"]) for row in projection_rows)
        ),
    }


def _write_method_heatmap(
    figures_directory: Path,
    input_id: str,
    method_id: str,
    rows: Sequence[Mapping[str, Any]],
    metric: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import (
        LinearSegmentedColormap,
        Normalize,
        TwoSlopeNorm,
    )
    from matplotlib.patches import Rectangle

    row_index = {value: index for index, value in enumerate(ACCELERATION_LEVELS_RAD_S2)}
    column_index = {value: index for index, value in enumerate(JERK_LEVELS_RAD_S3)}
    values = np.full(
        (
            len(ACCELERATION_LEVELS_RAD_S2),
            len(JERK_LEVELS_RAD_S3),
        ),
        np.nan,
        dtype=float,
    )
    annotations = np.full_like(values, np.nan)
    field, annotation_field = {
        "rmse": (
            "log2_rmse_ratio_vs_own_vendor",
            "rmse_ratio_vs_own_vendor",
        ),
        "lag_ms": (
            "lag_delta_vs_own_vendor_ms",
            "lag_delta_vs_own_vendor_ms",
        ),
        "projection_rate": ("projection_rate", "projection_rate"),
    }[metric]
    for row in rows:
        y = row_index[float(row["max_acceleration_rad_s2"])]
        x = column_index[float(row["max_jerk_rad_s3"])]
        value = row.get(field)
        annotation = row.get(annotation_field)
        if value is not None and annotation is not None:
            values[y, x] = float(value)
            annotations[y, x] = float(annotation)

    diverging = LinearSegmentedColormap.from_list(
        "blue_neutral_orange",
        ["#8BB9E8", "#F5F5F4", "#E6A15C"],
    )
    diverging.set_bad("#EEEDEB")
    projection_palette = LinearSegmentedColormap.from_list(
        "white_purple_projection",
        ["#F7F5FA", "#714A9C"],
    )
    projection_palette.set_bad("#EEEDEB")
    finite = values[np.isfinite(values)]
    if metric == "projection_rate":
        image_values = values * 100.0
        palette = projection_palette
        norm = Normalize(vmin=0.0, vmax=100.0)
        colorbar_label = "Projected cycles [%]"
        title_suffix = "configured-limit projection rate"
    else:
        extent = max(
            0.5 if metric == "rmse" else 1.0,
            0.0 if not finite.size else float(np.max(np.abs(finite))),
        )
        image_values = values
        palette = diverging
        norm = TwoSlopeNorm(vmin=-extent, vcenter=0.0, vmax=extent)
        if metric == "rmse":
            colorbar_label = "log₂(RMSE / own vendor RMSE)"
            title_suffix = "raw-time position RMSE sensitivity"
        else:
            colorbar_label = "Lag Δ vs own vendor [ms]"
            title_suffix = "diagnostic lag sensitivity"

    figure, axis = plt.subplots(
        figsize=(12.8, 7.2),
        dpi=160,
        constrained_layout=True,
    )
    image = axis.imshow(
        np.ma.masked_invalid(image_values),
        aspect="auto",
        cmap=palette,
        norm=norm,
    )
    for y in range(values.shape[0]):
        for x in range(values.shape[1]):
            value = annotations[y, x]
            if not np.isfinite(value):
                label = "N/A"
            elif metric == "rmse":
                label = f"×{value:.2f}"
            elif metric == "lag_ms":
                label = "0" if abs(value) < 0.5 else f"{value:+.0f}"
            else:
                label = f"{100.0 * value:.1f}%"
            axis.text(
                x,
                y,
                label,
                ha="center",
                va="center",
                fontsize=9.5,
                color="#252525" if label != "N/A" else "#6B7280",
            )
    vendor_y = row_index[VENDOR_ACCELERATION_RAD_S2]
    vendor_x = column_index[VENDOR_JERK_RAD_S3]
    axis.add_patch(
        Rectangle(
            (vendor_x - 0.5, vendor_y - 0.5),
            1.0,
            1.0,
            fill=False,
            edgecolor="#252525",
            linewidth=2.0,
        )
    )
    axis.set_xticks(
        np.arange(len(JERK_LEVELS_RAD_S3)),
        [
            f"{value:g}\n(vendor)" if value == VENDOR_JERK_RAD_S3 else f"{value:g}"
            for value in JERK_LEVELS_RAD_S3
        ],
    )
    axis.set_yticks(
        np.arange(len(ACCELERATION_LEVELS_RAD_S2)),
        [
            f"{value:g}\n(vendor)"
            if value == VENDOR_ACCELERATION_RAD_S2
            else f"{value:g}"
            for value in ACCELERATION_LEVELS_RAD_S2
        ],
    )
    axis.set(
        xlabel="Max jerk limit [rad/s³]",
        ylabel="Max acceleration limit [rad/s²]",
        title=(
            f"{METHOD_LABELS[method_id]}\n{input_id} · {title_suffix} · "
            "per-case configured-limit projection"
        ),
    )
    colorbar = figure.colorbar(image, ax=axis, shrink=0.86)
    colorbar.set_label(colorbar_label)
    figures_directory.mkdir(parents=True, exist_ok=True)
    stem = f"constraint_sensitivity_{metric}"
    figure.savefig(
        figures_directory / f"{stem}.png",
        dpi=180,
        bbox_inches="tight",
        facecolor="white",
    )
    figure.savefig(
        figures_directory / f"{stem}.svg",
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)


def _write_vendor_position_overview(
    figures_directory: Path,
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures_directory.mkdir(parents=True, exist_ok=True)
    for input_id in INPUT_IDS:
        reference = references[input_id]
        figure, axis = plt.subplots(
            figsize=(12.5, 5.4),
            constrained_layout=True,
        )
        axis.plot(
            reference.time_s,
            reference.position_rad,
            color="black",
            linewidth=1.5,
            label="reference",
        )
        for method_id in METHOD_IDS:
            run = tracking_runs[(_vendor_case_id(method_id), input_id)]
            if run.command is None:
                continue
            axis.plot(
                run.command.time_s,
                run.command.position_rad,
                color=METHOD_COLORS[method_id],
                linewidth=1.0,
                label=METHOD_LABELS[method_id],
            )
        axis.set(
            title=(
                f"E10 vendor-limit overview · {input_id} · "
                "five projected causal-PVA methods"
            ),
            xlabel="Time (s)",
            ylabel="Position (rad)",
        )
        axis.grid(alpha=0.25)
        axis.legend(loc="best", fontsize=8)
        figure.savefig(
            figures_directory / f"{input_id}_position.png",
            dpi=180,
            bbox_inches="tight",
            facecolor="white",
        )
        plt.close(figure)


def _write_acceptance_summary(
    analysis_directory: Path,
    summaries: Sequence[Mapping[str, Any]],
) -> None:
    complete = all(int(row["completed_case_count"]) == 35 for row in summaries)
    integrity = all(int(row["integrity_pass_case_count"]) == 35 for row in summaries)
    lines = [
        "## E10 execution and integrity",
        "",
        f"- All 350 runs complete: `{'yes' if complete else 'no'}`",
        f"- All 350 runs pass integrity guardrails: `{'yes' if integrity else 'no'}`",
        "- Sensitivity is descriptive and normalized within each method. "
        "Projection is expected and is not itself a guardrail failure.",
        "- The deterministic integrity gate uses sampled output V/A plus exact "
        "profile V/A/J; sampled output jerk remains unavailable by contract.",
        "- Deadline misses are reported but excluded from the deterministic "
        "integrity gate because they depend on the execution host.",
        "",
        "| input | method | complete | integrity | vendor RMSE | "
        "RMSE ratio range | projection-rate range |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        vendor_rmse = row["vendor_position_rmse_rad"]
        rmse_range = (
            ""
            if row["minimum_rmse_ratio"] is None or row["maximum_rmse_ratio"] is None
            else (
                f"{float(row['minimum_rmse_ratio']):.6g}–"
                f"{float(row['maximum_rmse_ratio']):.6g}"
            )
        )
        projection_range = (
            ""
            if row["minimum_projection_rate"] is None
            or row["maximum_projection_rate"] is None
            else (
                f"{100.0 * float(row['minimum_projection_rate']):.3g}%–"
                f"{100.0 * float(row['maximum_projection_rate']):.3g}%"
            )
        )
        lines.append(
            f"| {row['input_id']} | {row['method_id']} | "
            f"{row['completed_case_count']}/35 | "
            f"{row['integrity_pass_case_count']}/35 | "
            f"{'' if vendor_rmse is None else f'{float(vendor_rmse):.8g}'} | "
            f"{rmse_range} | {projection_range} |"
        )
    lines.extend(
        [
            "",
            "Values above the vendor acceleration or jerk point remain "
            "diagnostic probes, not deployment recommendations.",
            "",
        ]
    )
    (analysis_directory / "acceptance_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def write_limit_sensitivity_artifacts(
    *,
    analysis_directory: Path,
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    experiment_spec: ExperimentSpec,
    create_figures: bool,
) -> None:
    """Write E10's method-separated sensitivity and projection audit."""

    del experiment_spec
    scan_rows = _raw_target_scan_rows(tracking_runs)
    feasibility_rows = _raw_target_feasibility_rows(scan_rows)
    surface_rows = _surface_rows(
        tracking_runs,
        trajectory_rows,
        feasibility_rows,
    )
    summary_rows = _method_summary_rows(surface_rows)

    write_rows_csv(analysis_directory / "raw_target_scan.csv", scan_rows)
    write_rows_csv(
        analysis_directory / "raw_target_feasibility.csv",
        feasibility_rows,
    )
    write_rows_csv(
        analysis_directory / "pva_limit_sensitivity.csv",
        surface_rows,
    )
    write_rows_csv(
        analysis_directory / "method_sensitivity_summary.csv",
        summary_rows,
    )
    _write_acceptance_summary(analysis_directory, summary_rows)

    for method_id in METHOD_IDS:
        for input_id in INPUT_IDS:
            method_rows = [
                row
                for row in surface_rows
                if row["input_id"] == input_id and row["method_id"] == method_id
            ]
            method_directory = analysis_directory / "by_method" / method_id / input_id
            write_rows_csv(
                method_directory / "constraint_sensitivity_rmse.csv",
                _by_method_metric_rows(method_rows, "rmse"),
            )
            write_rows_csv(
                method_directory / "constraint_sensitivity_lag_ms.csv",
                _by_method_metric_rows(method_rows, "lag_ms"),
            )
            write_rows_csv(
                method_directory / "constraint_sensitivity_projection_rate.csv",
                _by_method_metric_rows(method_rows, "projection_rate"),
            )
            if create_figures:
                method_figures = (
                    analysis_directory / "figures" / "by_method" / method_id / input_id
                )
                for metric in ("rmse", "lag_ms", "projection_rate"):
                    _write_method_heatmap(
                        method_figures,
                        input_id,
                        method_id,
                        method_rows,
                        metric,
                    )
    if create_figures:
        _write_vendor_position_overview(
            analysis_directory / "figures",
            references,
            tracking_runs,
        )


__all__ = [
    "ACCELERATION_LEVELS_RAD_S2",
    "JERK_LEVELS_RAD_S3",
    "INPUT_IDS",
    "METHOD_IDS",
    "TARGET_AGE_SAMPLES",
    "build_experiment",
    "write_limit_sensitivity_artifacts",
]
