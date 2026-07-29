"""E09: E04 causal PVA finite differences on the E07 stop-and-go matrix."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from functools import partial
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
from otg_lab.runio import write_rows_csv
from otg_lab.trajectory_ablation import (
    BASELINE_METHOD_ID as E04_BASELINE_METHOD_ID,
)
from otg_lab.trajectory_ablation import (
    build_state_target_methods,
)

DT_S = 0.01
DURATION_S = 3.0
MAIN_START_S = 0.5
MAIN_END_S = 2.5
MAX_VELOCITY_RAD_S = 4.1
VENDOR_ACCELERATION_RAD_S2 = 8.2
VENDOR_JERK_RAD_S3 = 4000.0
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
LIMIT_SCALES = (0.25, 0.5, 1.0, 2.0)

P_ONLY_BASELINE_METHOD_ID = "position_zoh_p_ruckig"
FINITE_DIFFERENCE_METHOD_IDS = (
    "pva_est_backward_o1_k",
    "pva_est_backward_o2_k",
    "pva_est_centered_o2_km1",
    "pva_pred_backward_o1_kp1",
    "pva_pred_backward_o2_kp1",
)
METHOD_IDS = (P_ONLY_BASELINE_METHOD_ID, *FINITE_DIFFERENCE_METHOD_IDS)
METHOD_LABELS = {
    P_ONLY_BASELINE_METHOD_ID: "P-only baseline (E07)",
    "pva_est_backward_o1_k": "Endpoint backward O1 [k]",
    "pva_est_backward_o2_k": "Endpoint backward O2 [k]",
    "pva_est_centered_o2_km1": "Delayed centered O2 [k−1]",
    "pva_pred_backward_o1_kp1": "Future backward O1 [k+1]",
    "pva_pred_backward_o2_kp1": "Future backward O2 [k+1]",
}
TARGET_AGE_SAMPLES = {
    P_ONLY_BASELINE_METHOD_ID: 0.0,
    "pva_est_backward_o1_k": 1.0,
    "pva_est_backward_o2_k": 1.0,
    "pva_est_centered_o2_km1": 2.0,
    "pva_pred_backward_o1_kp1": 0.0,
    "pva_pred_backward_o2_kp1": 0.0,
}
METHOD_COLORS = {
    P_ONLY_BASELINE_METHOD_ID: "#D55E00",
    "pva_est_backward_o1_k": "#4477AA",
    "pva_est_backward_o2_k": "#66CCEE",
    "pva_est_centered_o2_km1": "#228833",
    "pva_pred_backward_o1_kp1": "#EE6677",
    "pva_pred_backward_o2_kp1": "#AA3377",
}
METHOD_LINESTYLES = {
    P_ONLY_BASELINE_METHOD_ID: "-",
    "pva_est_backward_o1_k": "--",
    "pva_est_backward_o2_k": ":",
    "pva_est_centered_o2_km1": "-.",
    "pva_pred_backward_o1_kp1": "--",
    "pva_pred_backward_o2_kp1": ":",
}

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

STOP_GO_ZERO_TOLERANCE = 1e-12
NORMALIZED_RIPPLE_MEDIAN_TOLERANCE = 1e-9
NORMALIZED_RIPPLE_P95_TOLERANCE = 1e-4
TARGET_VELOCITY_ABS_TOLERANCE = 1e-9
TARGET_ACCELERATION_ABS_TOLERANCE = 1e-7
TARGET_AGE_ABS_TOLERANCE = 1e-9


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def critical_reference_velocity(
    acceleration_rad_s2: float,
    jerk_rad_s3: float,
    dt_s: float = DT_S,
) -> float:
    """Return E07's one-cycle P-only rest-to-rest critical average speed."""

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


def _save_figure(figure: Any, figures_directory: Path, name: str) -> None:
    figures_directory.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        figures_directory / f"{name}.png",
        dpi=200,
        bbox_inches="tight",
        facecolor="white",
    )
    figure.savefig(
        figures_directory / f"{name}.svg",
        bbox_inches="tight",
        facecolor="white",
    )


def _geometric_edges(values: Sequence[float]) -> np.ndarray:
    data = np.asarray(values, dtype=float)
    logs = np.log(data)
    internal = 0.5 * (logs[:-1] + logs[1:])
    edges = np.empty(data.size + 1, dtype=float)
    edges[1:-1] = internal
    edges[0] = logs[0] - (internal[0] - logs[0])
    edges[-1] = logs[-1] + (logs[-1] - internal[-1])
    return np.exp(edges)


def _dense_profile_state(
    run: TrackingRun,
    start_time_s: float,
    end_time_s: float,
    grid_dt_s: float = 0.0001,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reconstruct exact constant-jerk profiles on a dense display grid."""

    trace_by_cycle = {
        int(row["cycle_index"]): row
        for row in run.trace_rows
        if row.get("cycle_index") is not None
    }
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for row in run.profile_rows:
        grouped.setdefault(int(row["cycle_index"]), []).append(row)
    times: list[float] = []
    positions: list[float] = []
    velocities: list[float] = []
    for cycle in sorted(grouped):
        trace = trace_by_cycle[cycle]
        position = float(trace["command_start_position_rad"])
        velocity = float(trace["command_start_velocity_rad_s"])
        acceleration = float(trace["command_start_acceleration_rad_s2"])
        for row in sorted(
            grouped[cycle],
            key=lambda item: int(item["segment_index"]),
        ):
            segment_start = float(row["start_time_s"])
            segment_end = float(row["end_time_s"])
            jerk = float(row["jerk_rad_s3"])
            left = max(segment_start, start_time_s)
            right = min(segment_end, end_time_s)
            if right >= left:
                grid = np.arange(
                    left,
                    right + grid_dt_s * 0.5,
                    grid_dt_s,
                    dtype=np.float64,
                )
                grid = grid[grid <= right + 1e-12]
                if (
                    times
                    and grid.size
                    and math.isclose(
                        float(grid[0]),
                        times[-1],
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                ):
                    grid = grid[1:]
                local = grid - segment_start
                times.extend(grid.tolist())
                positions.extend(
                    (
                        position
                        + velocity * local
                        + 0.5 * acceleration * local**2
                        + jerk * local**3 / 6.0
                    ).tolist()
                )
                velocities.extend(
                    (velocity + acceleration * local + 0.5 * jerk * local**2).tolist()
                )
            duration = segment_end - segment_start
            position = (
                position
                + velocity * duration
                + 0.5 * acceleration * duration**2
                + jerk * duration**3 / 6.0
            )
            velocity = velocity + acceleration * duration + 0.5 * jerk * duration**2
            acceleration += jerk * duration
    return (
        np.asarray(times),
        np.asarray(positions),
        np.asarray(velocities),
    )


def _run_config(limit_scale: float = 1.0) -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=MAX_VELOCITY_RAD_S,
            max_acceleration_rad_s2=(limit_scale * VENDOR_ACCELERATION_RAD_S2),
            max_jerk_rad_s3=limit_scale * VENDOR_JERK_RAD_S3,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _methods() -> tuple[TrackingMethodSpec, ...]:
    methods = build_state_target_methods(
        "pva",
        include_truth=False,
        include_differences=True,
    )
    finite_differences = tuple(
        method for method in methods if method.method_id != E04_BASELINE_METHOD_ID
    )
    if (
        tuple(method.method_id for method in finite_differences)
        != FINITE_DIFFERENCE_METHOD_IDS
    ):
        raise RuntimeError("E04 finite-difference method declarations changed")
    p_only_baseline = TrackingMethodSpec(
        method_id=P_ONLY_BASELINE_METHOD_ID,
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        description=(
            "E07 baseline: PositionOnly → ZOH → P → NoGovernor → "
            "ordinary unshielded Ruckig"
        ),
    )
    return (p_only_baseline, *finite_differences)


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
                "target_age_samples": TARGET_AGE_SAMPLES[method.method_id],
                "limit_scale": scale,
                "max_acceleration_rad_s2": (scale * VENDOR_ACCELERATION_RAD_S2),
                "max_jerk_rad_s3": scale * VENDOR_JERK_RAD_S3,
                "e07_p_only_critical_velocity_rad_s": (
                    scale * VENDOR_CRITICAL_VELOCITY_RAD_S
                ),
            },
            description=(
                f"{METHOD_LABELS[method.method_id]}; A/J limit scale={scale:g}"
            ),
        )
        for method in methods
        for scale in LIMIT_SCALES
    )


def _metric_lookup(
    rows: Sequence[MetricRow],
) -> dict[tuple[str, str, str, str], MetricRow]:
    return {
        (row.method_id, row.input_id, row.window_id, row.metric_id): row for row in rows
    }


def _metric_value(
    lookup: Mapping[tuple[str, str, str, str], MetricRow],
    case_id: str,
    input_id: str,
    metric_id: str,
    window_id: str,
) -> float | int | None:
    row = lookup.get((case_id, input_id, window_id, metric_id))
    if (
        row is None
        or row.status != AVAILABLE
        or row.value is None
        or isinstance(row.value, bool)
    ):
        return None
    return row.value


def _selected_trace_rows(run: TrackingRun) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        row
        for row in run.trace_rows
        if row.get("command_time_s") is not None
        and MAIN_START_S - 1e-12 <= float(row["command_time_s"]) <= MAIN_END_S + 1e-12
    )


def _as_true(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _target_audit(
    run: TrackingRun,
    expected_target_velocity: float,
    expected_age_samples: float,
) -> dict[str, Any]:
    rows = _selected_trace_rows(run)
    velocities = [
        float(row["raw_target_velocity_rad_s"])
        for row in rows
        if row.get("raw_target_velocity_rad_s") is not None
    ]
    accelerations = [
        float(row["raw_target_acceleration_rad_s2"])
        for row in rows
        if row.get("raw_target_acceleration_rad_s2") is not None
    ]
    ages = [
        float(row["raw_target_age_samples"])
        for row in rows
        if row.get("raw_target_age_samples") is not None
    ]
    complete = bool(
        rows
        and len(velocities) == len(rows)
        and len(accelerations) == len(rows)
        and len(ages) == len(rows)
    )
    velocity_error = (
        max(abs(value - expected_target_velocity) for value in velocities)
        if velocities
        else None
    )
    acceleration_abs = (
        max(abs(value) for value in accelerations) if accelerations else None
    )
    age_error = (
        max(abs(value - expected_age_samples) for value in ages) if ages else None
    )
    causal_pass = bool(
        rows and all(_as_true(row.get("raw_target_causal")) for row in rows)
    )
    mature_pass = bool(
        rows and all(not _as_true(row.get("raw_target_startup")) for row in rows)
    )
    derivative_pass = bool(
        complete
        and velocity_error is not None
        and velocity_error <= TARGET_VELOCITY_ABS_TOLERANCE
        and acceleration_abs is not None
        and acceleration_abs <= TARGET_ACCELERATION_ABS_TOLERANCE
    )
    age_pass = bool(
        complete and age_error is not None and age_error <= TARGET_AGE_ABS_TOLERANCE
    )
    return {
        "raw_target_row_count": len(rows),
        "raw_target_velocity_max_abs_error_rad_s": velocity_error,
        "raw_target_acceleration_max_abs_rad_s2": acceleration_abs,
        "raw_target_age_samples_min": min(ages) if ages else None,
        "raw_target_age_samples_max": max(ages) if ages else None,
        "raw_target_derivative_pass": derivative_pass,
        "raw_target_age_pass": age_pass,
        "raw_target_causal_pass": causal_pass,
        "raw_target_mature_pass": mature_pass,
    }


def _all_zero(values: Sequence[float | int | None]) -> bool:
    return bool(values) and all(
        value is not None and abs(float(value)) <= STOP_GO_ZERO_TOLERANCE
        for value in values
    )


def _guardrail_status(
    lookup: Mapping[tuple[str, str, str, str], MetricRow],
    run: TrackingRun,
    case_id: str,
    input_id: str,
) -> tuple[bool, float | int | None]:
    values = [
        _metric_value(
            lookup,
            case_id,
            input_id,
            metric_id,
            "full_overlap",
        )
        for metric_id in GUARDRAIL
    ]
    exact_fraction = _metric_value(
        lookup,
        case_id,
        input_id,
        "profile_exact_fraction",
        "full_overlap",
    )
    trace_safety = bool(
        run.trace_rows
        and all(_as_true(row.get("safety_guarantee")) for row in run.trace_rows)
    )
    passed = bool(
        exact_fraction is not None
        and math.isclose(float(exact_fraction), 1.0, abs_tol=1e-12)
        and trace_safety
        and _all_zero(values)
    )
    return passed, exact_fraction


def _surface_rows(
    experiment_spec: ExperimentSpec,
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
) -> list[dict[str, Any]]:
    lookup = _metric_lookup(trajectory_rows)
    cases = {case.case_id: case for case in experiment_spec.cases}
    output: list[dict[str, Any]] = []
    for method_id in METHOD_IDS:
        is_p_only_baseline = method_id == P_ONLY_BASELINE_METHOD_ID
        method_role = "p_only_baseline" if is_p_only_baseline else "finite_difference"
        expected_age = TARGET_AGE_SAMPLES[method_id]
        for scale in LIMIT_SCALES:
            case_id = _case_id(method_id, scale)
            case = cases[case_id]
            acceleration = case.run_config.limits.max_acceleration_rad_s2
            jerk = case.run_config.limits.max_jerk_rad_s3
            e07_critical_velocity = critical_reference_velocity(
                acceleration,
                jerk,
            )
            for input_id, vendor_ratio, reference_velocity in INPUTS:
                metric = partial(
                    _metric_value,
                    lookup,
                    case_id,
                    input_id,
                    window_id="main_evaluation",
                )
                pulse_fraction = metric("rest_to_rest_pulse_fraction")
                event_rate = metric("stop_go_event_rate_hz")
                ripple_ratio_median = metric(
                    "profile_velocity_ripple_to_reference_median"
                )
                ripple_ratio_p95 = metric("profile_velocity_ripple_to_reference_p95")
                rho_e07 = reference_velocity / e07_critical_velocity
                if is_p_only_baseline:
                    stop_go_pass = None
                    ripple_median_pass = None
                    ripple_p95_pass = None
                    ripple_pass = None
                    if rho_e07 <= 0.95 + 1e-12:
                        baseline_expected_region = "pulse"
                        baseline_threshold_pass = bool(
                            pulse_fraction is not None and float(pulse_fraction) >= 0.95
                        )
                    elif rho_e07 >= 1.05 - 1e-12:
                        baseline_expected_region = "continuous"
                        baseline_threshold_pass = bool(
                            pulse_fraction is not None and float(pulse_fraction) <= 0.05
                        )
                    else:
                        baseline_expected_region = "boundary_diagnostic"
                        baseline_threshold_pass = None
                else:
                    stop_go_pass = bool(
                        pulse_fraction is not None
                        and event_rate is not None
                        and abs(float(pulse_fraction)) <= STOP_GO_ZERO_TOLERANCE
                        and abs(float(event_rate)) <= STOP_GO_ZERO_TOLERANCE
                    )
                    ripple_median_pass = bool(
                        ripple_ratio_median is not None
                        and float(ripple_ratio_median)
                        <= NORMALIZED_RIPPLE_MEDIAN_TOLERANCE
                    )
                    ripple_p95_pass = bool(
                        ripple_ratio_p95 is not None
                        and float(ripple_ratio_p95) <= NORMALIZED_RIPPLE_P95_TOLERANCE
                    )
                    ripple_pass = ripple_median_pass and ripple_p95_pass
                    baseline_expected_region = None
                    baseline_threshold_pass = None
                run = tracking_runs[(case_id, input_id)]
                target_audit = _target_audit(
                    run,
                    0.0 if is_p_only_baseline else reference_velocity,
                    expected_age,
                )
                guardrail_pass, exact_fraction = _guardrail_status(
                    lookup,
                    run,
                    case_id,
                    input_id,
                )
                output.append(
                    {
                        "case_id": case_id,
                        "method_id": method_id,
                        "method_label": METHOD_LABELS[method_id],
                        "method_role": method_role,
                        "expected_target_age_samples": expected_age,
                        "input_id": input_id,
                        "vendor_velocity_ratio": vendor_ratio,
                        "reference_velocity_rad_s": reference_velocity,
                        "limit_scale": scale,
                        "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
                        "max_acceleration_rad_s2": acceleration,
                        "max_jerk_rad_s3": jerk,
                        "e07_p_only_critical_velocity_rad_s": (e07_critical_velocity),
                        "rho_e07_p_only": rho_e07,
                        "baseline_expected_region": baseline_expected_region,
                        "baseline_threshold_pass": baseline_threshold_pass,
                        "rest_to_rest_pulse_fraction": pulse_fraction,
                        "stop_go_event_rate_hz": event_rate,
                        "endpoint_stop_fraction": metric("endpoint_stop_fraction"),
                        "longest_rest_to_rest_pulse_run_cycles": metric(
                            "longest_rest_to_rest_pulse_run_cycles"
                        ),
                        "profile_peak_velocity_to_reference_median": metric(
                            "profile_peak_velocity_to_reference_median"
                        ),
                        "profile_velocity_ripple_median_rad_s": metric(
                            "profile_velocity_ripple_median"
                        ),
                        "profile_velocity_ripple_to_reference_median": (
                            ripple_ratio_median
                        ),
                        "profile_velocity_ripple_to_reference_p95": (ripple_ratio_p95),
                        "position_rmse_rad": metric("position_rmse"),
                        "lag_s": metric("lag_s"),
                        "profile_exact_fraction": exact_fraction,
                        **target_audit,
                        "stop_go_eliminated_pass": stop_go_pass,
                        "normalized_ripple_median_pass": ripple_median_pass,
                        "normalized_ripple_p95_pass": ripple_p95_pass,
                        "normalized_ripple_pass": ripple_pass,
                        "guardrail_pass": guardrail_pass,
                        "run_completed": run.status.completed,
                        "acceptance_pass": bool(
                            run.status.completed
                            and guardrail_pass
                            and target_audit["raw_target_derivative_pass"]
                            and target_audit["raw_target_age_pass"]
                            and target_audit["raw_target_causal_pass"]
                            and target_audit["raw_target_mature_pass"]
                            and (
                                baseline_threshold_pass is not False
                                if is_p_only_baseline
                                else bool(stop_go_pass and ripple_pass)
                            )
                        ),
                    }
                )
    return output


def _finite_values(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> list[float]:
    output: list[float] = []
    for row in rows:
        value = row.get(field)
        if value is None or isinstance(value, bool):
            continue
        number = float(value)
        if math.isfinite(number):
            output.append(number)
    return output


def _maximum_value(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> float | None:
    values = _finite_values(rows, field)
    return max(values) if values else None


def _median_value(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> float | None:
    values = _finite_values(rows, field)
    return float(np.median(values)) if values else None


def _comparison_rows(
    surface: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method_id in METHOD_IDS:
        is_p_only_baseline = method_id == P_ONLY_BASELINE_METHOD_ID
        for scale in LIMIT_SCALES:
            rows = [
                row
                for row in surface
                if row["method_id"] == method_id
                and math.isclose(float(row["limit_scale"]), scale)
            ]

            output.append(
                {
                    "method_id": method_id,
                    "method_label": METHOD_LABELS[method_id],
                    "method_role": (
                        "p_only_baseline" if is_p_only_baseline else "finite_difference"
                    ),
                    "target_age_samples": TARGET_AGE_SAMPLES[method_id],
                    "limit_scale": scale,
                    "input_count": len(rows),
                    "max_rest_to_rest_pulse_fraction": _maximum_value(
                        rows, "rest_to_rest_pulse_fraction"
                    ),
                    "max_stop_go_event_rate_hz": _maximum_value(
                        rows, "stop_go_event_rate_hz"
                    ),
                    "max_normalized_velocity_ripple_median": _maximum_value(
                        rows, "profile_velocity_ripple_to_reference_median"
                    ),
                    "max_normalized_velocity_ripple_p95": _maximum_value(
                        rows, "profile_velocity_ripple_to_reference_p95"
                    ),
                    "max_position_rmse_rad": _maximum_value(rows, "position_rmse_rad"),
                    "median_lag_s": _median_value(rows, "lag_s"),
                    "all_runs_completed": all(
                        bool(row["run_completed"]) for row in rows
                    ),
                    "all_stop_go_eliminated": (
                        None
                        if is_p_only_baseline
                        else all(bool(row["stop_go_eliminated_pass"]) for row in rows)
                    ),
                    "all_baseline_threshold_pass": (
                        all(row["baseline_threshold_pass"] is not False for row in rows)
                        if is_p_only_baseline
                        else None
                    ),
                    "all_target_semantics_pass": all(
                        bool(row["raw_target_derivative_pass"])
                        and bool(row["raw_target_age_pass"])
                        and bool(row["raw_target_causal_pass"])
                        and bool(row["raw_target_mature_pass"])
                        for row in rows
                    ),
                    "all_guardrails_pass": all(
                        bool(row["guardrail_pass"]) for row in rows
                    ),
                    "all_acceptance_pass": all(
                        bool(row["acceptance_pass"]) for row in rows
                    ),
                }
            )
    return output


def _write_acceptance_summary(
    analysis_directory: Path,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    overall = bool(rows and all(bool(row["all_acceptance_pass"]) for row in rows))
    lines = [
        "# E09 acceptance summary",
        "",
        f"- Scientific result: `{'pass' if overall else 'fail'}`",
        f"- Core matrix: `{len(METHOD_IDS)} methods × {len(LIMIT_SCALES)} "
        f"limit scales × {len(INPUTS)} inputs = "
        f"{len(METHOD_IDS) * len(LIMIT_SCALES) * len(INPUTS)} runs`",
        "- `rho_e07_p_only` is an E07 comparison coordinate, not a PVA "
        "reachability threshold.",
        "- The exact E07 PositionOnly → ZOH → P → ordinary Ruckig method is "
        "rerun as the internal P-only baseline.",
        "- Baseline acceptance reproduces E07's pulse/continuous threshold; "
        "finite-difference acceptance requires stop-and-go elimination.",
        "",
        "| method | role | age (samples) | A/J scale | max pulse fraction | "
        "max event rate (Hz) | max median ripple/ref | max P95 ripple/ref | "
        "acceptance |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['method_id']} | {row['method_role']} | "
            f"{float(row['target_age_samples']):g} | "
            f"{float(row['limit_scale']):g} | "
            f"{float(row['max_rest_to_rest_pulse_fraction']):.6g} | "
            f"{float(row['max_stop_go_event_rate_hz']):.6g} | "
            f"{float(row['max_normalized_velocity_ripple_median']):.6g} | "
            f"{float(row['max_normalized_velocity_ripple_p95']):.6g} | "
            f"{'pass' if row['all_acceptance_pass'] else 'fail'} |"
        )
    (analysis_directory / "acceptance_summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _method_surface(
    surface: Sequence[Mapping[str, Any]],
    method_id: str,
) -> list[Mapping[str, Any]]:
    return [row for row in surface if row["method_id"] == method_id]


def _write_method_phase_map(
    surface: Sequence[Mapping[str, Any]],
    method_id: str,
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = _method_surface(surface, method_id)
    by_key = {
        (
            float(row["vendor_velocity_ratio"]),
            float(row["limit_scale"]),
        ): row
        for row in rows
    }
    pulse = np.full(
        (len(VENDOR_VELOCITY_RATIOS), len(LIMIT_SCALES)),
        np.nan,
    )
    severity = np.full_like(pulse, np.nan)
    for row_index, ratio in enumerate(VENDOR_VELOCITY_RATIOS):
        for column_index, scale in enumerate(LIMIT_SCALES):
            row = by_key[(ratio, scale)]
            if row["rest_to_rest_pulse_fraction"] is not None:
                pulse[row_index, column_index] = float(
                    row["rest_to_rest_pulse_fraction"]
                )
            if row["profile_velocity_ripple_to_reference_median"] is not None:
                severity[row_index, column_index] = float(
                    row["profile_velocity_ripple_to_reference_median"]
                )

    scale_edges = _geometric_edges(LIMIT_SCALES)
    ratio_edges = _geometric_edges(VENDOR_VELOCITY_RATIOS)
    x_grid, y_grid = np.meshgrid(scale_edges, ratio_edges)
    figure, axes = plt.subplots(
        1,
        2,
        figsize=(13.2, 6.2),
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    occurrence = axes[0].pcolormesh(
        x_grid,
        y_grid,
        pulse,
        cmap="cividis",
        vmin=0.0,
        vmax=1.0,
        shading="flat",
    )
    severity_map = axes[1].pcolormesh(
        x_grid,
        y_grid,
        severity,
        cmap="viridis",
        vmin=0.0,
        vmax=2.05,
        shading="flat",
    )
    boundary_x = np.geomspace(
        scale_edges[0],
        min(scale_edges[-1], ratio_edges[-1]),
        200,
    )
    for axis in axes:
        axis.plot(
            boundary_x,
            boundary_x,
            color="white",
            linestyle="--",
            linewidth=2.5,
        )
        axis.plot(
            boundary_x,
            boundary_x,
            color="black",
            linestyle="--",
            linewidth=1.0,
            label="E07 P-only boundary ρ=1",
        )
        axis.set_xscale("log", base=2)
        axis.set_yscale("log", base=2)
        axis.set_xticks(LIMIT_SCALES)
        axis.set_xticklabels([f"{value:g}×" for value in LIMIT_SCALES])
        axis.grid(alpha=0.15, which="both")
    displayed_ratios = (0.125, 0.25, 0.5, 1.0, 2.0, 4.0)
    axes[0].set_yticks(displayed_ratios)
    axes[0].set_yticklabels([f"{value:g}×" for value in displayed_ratios])
    axes[0].set(
        title="Occurrence: exact rest-to-rest pulse",
        xlabel="Acceleration / jerk limit scale",
        ylabel="Reference speed / vendor critical speed",
    )
    axes[1].set(
        title="Severity: within-cycle velocity ripple",
        xlabel="Acceleration / jerk limit scale",
    )
    axes[0].legend(loc="upper left")
    occurrence_bar = figure.colorbar(occurrence, ax=axes[0])
    occurrence_bar.set_label("Rest-to-rest pulse fraction")
    severity_bar = figure.colorbar(severity_map, ax=axes[1])
    severity_bar.set_label("Median velocity ripple / |reference velocity|")
    figure.suptitle(
        f"E09 {METHOD_LABELS[method_id]} stop-and-go surface",
        fontsize=15,
    )
    _save_figure(figure, figures_directory, "stop_go_phase_map")
    plt.close(figure)


def _write_method_rho_response(
    surface: Sequence[Mapping[str, Any]],
    method_id: str,
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(
        1,
        4,
        figsize=(22.0, 5.0),
        sharex=True,
        constrained_layout=True,
    )
    fields = (
        "rest_to_rest_pulse_fraction",
        "stop_go_event_rate_hz",
        "profile_velocity_ripple_to_reference_median",
        "profile_velocity_ripple_to_reference_p95",
    )
    for scale in LIMIT_SCALES:
        rows = sorted(
            (
                row
                for row in surface
                if row["method_id"] == method_id
                and math.isclose(float(row["limit_scale"]), scale)
                and all(row[field] is not None for field in fields)
            ),
            key=lambda row: float(row["rho_e07_p_only"]),
        )
        rho = [float(row["rho_e07_p_only"]) for row in rows]
        for axis, field in zip(axes, fields):
            axis.plot(
                rho,
                [float(row[field]) for row in rows],
                marker="o",
                markersize=3.5,
                linewidth=1.3,
                label=f"{scale:g}× A/J",
            )
    for axis in axes:
        axis.axvline(
            1.0,
            color="black",
            linestyle="--",
            linewidth=1.1,
            label="E07 P-only ρ=1",
        )
        axis.set_xscale("log", base=2)
        axis.grid(alpha=0.25)
        axis.set_xlabel("ρ_E07 = vref / P-only critical velocity")
    axes[0].set(
        title="Pulse occurrence",
        ylabel="Rest-to-rest pulse fraction",
        ylim=(-0.03, 1.03),
    )
    axes[1].set(
        title="Pulse rate",
        ylabel="Stop-and-go event rate (Hz)",
        ylim=(-3.0, 103.0),
    )
    axes[2].set(
        title="Median exact-profile severity",
        ylabel="Median velocity ripple / |reference velocity|",
        ylim=(-0.03, 2.08),
    )
    axes[3].set(
        title="P95 exact-profile tail",
        ylabel="P95 velocity ripple / |reference velocity|",
        ylim=(-0.03, 2.08),
    )
    axes[0].legend(loc="best", fontsize=8.5)
    figure.suptitle(
        f"E09 {METHOD_LABELS[method_id]} response on the E07 coordinate",
        fontsize=15,
    )
    _save_figure(figure, figures_directory, "e07_rho_response")
    plt.close(figure)


def _input_id_for_vendor_ratio(ratio: float) -> str:
    return next(
        input_id
        for input_id, value, _velocity in INPUTS
        if math.isclose(value, ratio, abs_tol=1e-12)
    )


def _write_method_velocity_profile(
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    method_id: str,
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    start_time_s = 0.5
    end_time_s = 0.6
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(10.2, 9.0),
        sharex=True,
        constrained_layout=True,
    )
    for axis, rho in zip(axes, (0.5, 1.0, 1.2)):
        input_id = _input_id_for_vendor_ratio(rho)
        reference = references[input_id]
        reference_velocity = float(reference.velocity_rad_s[0])
        time, _position, velocity = _dense_profile_state(
            tracking_runs[(_case_id(method_id, 1.0), input_id)],
            start_time_s,
            end_time_s,
        )
        axis.axhline(
            reference_velocity,
            color="black",
            linewidth=1.5,
            linestyle="--",
            label="Reference velocity",
        )
        axis.plot(
            time,
            velocity,
            color=METHOD_COLORS[method_id],
            linewidth=1.5,
            label=METHOD_LABELS[method_id],
        )
        for boundary in np.arange(
            start_time_s,
            end_time_s + DT_S / 2.0,
            DT_S,
        ):
            axis.axvline(
                boundary,
                color="gray",
                linewidth=0.55,
                alpha=0.4,
            )
        axis.set_title(
            f"Vendor limits, ρ_E07={rho:g}, vref={reference_velocity:.6g} rad/s"
        )
        axis.set_ylabel("Velocity (rad/s)")
        axis.set_ylim(
            -0.05 * abs(reference_velocity),
            2.05 * abs(reference_velocity),
        )
        axis.ticklabel_format(axis="y", style="plain", useOffset=False)
        axis.grid(alpha=0.2)
        axis.legend(loc="upper right")
    axes[-1].set(
        xlabel="Time (s)",
        xlim=(start_time_s, end_time_s),
    )
    figure.suptitle(
        f"{METHOD_LABELS[method_id]} exact sub-cycle velocity",
        fontsize=15,
    )
    _save_figure(figure, figures_directory, "stop_go_subcycle_velocity")
    plt.close(figure)


def _write_method_position_figures(
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    method_id: str,
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures_directory.mkdir(parents=True, exist_ok=True)
    start_time_s = 0.5
    end_time_s = 0.6
    colors = ("#4477AA", "#EE6677", "#228833", "#AA3377")
    for input_id, vendor_ratio, _reference_velocity in INPUTS:
        reference = references[input_id]
        figure, axes = plt.subplots(
            2,
            1,
            figsize=(10.5, 7.2),
            sharex=True,
            constrained_layout=True,
            gridspec_kw={"height_ratios": (1.35, 1.0)},
        )
        reference_origin = float(
            np.interp(
                start_time_s,
                reference.time_s,
                reference.position_rad,
            )
        )
        reference_mask = (reference.time_s >= start_time_s - 1e-12) & (
            reference.time_s <= end_time_s + 1e-12
        )
        axes[0].plot(
            reference.time_s[reference_mask],
            (reference.position_rad[reference_mask] - reference_origin) * 1e6,
            color="black",
            linewidth=1.6,
            linestyle="--",
            label="Linear reference",
            zorder=5,
        )
        for scale, color in zip(LIMIT_SCALES, colors):
            run = tracking_runs[(_case_id(method_id, scale), input_id)]
            time, position, _velocity = _dense_profile_state(
                run,
                start_time_s,
                end_time_s,
            )
            if not time.size:
                continue
            local_position = (position - position[0]) * 1e6
            reference_dense = np.interp(
                time,
                reference.time_s,
                reference.position_rad,
            )
            error = position - reference_dense
            centered_error = (error - np.median(error)) * 1e6
            rho_e07 = vendor_ratio / scale
            label = f"{scale:g}× A/J (ρ_E07={rho_e07:.3g})"
            axes[0].plot(
                time,
                local_position,
                color=color,
                linewidth=1.25,
                label=label,
            )
            axes[1].plot(
                time,
                centered_error,
                color=color,
                linewidth=1.2,
                label=label,
            )
        for axis in axes:
            for boundary in np.arange(
                start_time_s,
                end_time_s + DT_S / 2.0,
                DT_S,
            ):
                axis.axvline(
                    boundary,
                    color="gray",
                    linewidth=0.55,
                    alpha=0.4,
                )
            axis.grid(alpha=0.2)
        axes[0].set(
            title=(
                f"{METHOD_LABELS[method_id]}: {input_id} "
                f"(vendor ratio={vendor_ratio:g})"
            ),
            ylabel="Local displacement from 0.5 s (µrad)",
        )
        axes[0].legend(loc="best", ncol=2, fontsize=8.5)
        axes[1].axhline(
            0.0,
            color="black",
            linewidth=0.8,
            alpha=0.65,
        )
        axes[1].set(
            xlabel="Time (s)",
            ylabel="Centered position error (µrad)",
            xlim=(start_time_s, end_time_s),
        )
        axes[1].legend(loc="best", ncol=2, fontsize=8.5)
        figure.savefig(
            figures_directory / f"{input_id}_position.png",
            dpi=200,
            bbox_inches="tight",
            facecolor="white",
        )
        plt.close(figure)


def _write_method_comparison(
    surface: Sequence[Mapping[str, Any]],
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(
        len(LIMIT_SCALES),
        4,
        figsize=(22.5, 15.0),
        sharex="col",
        constrained_layout=True,
    )
    fields = (
        "rest_to_rest_pulse_fraction",
        "stop_go_event_rate_hz",
        "profile_velocity_ripple_to_reference_median",
        "profile_velocity_ripple_to_reference_p95",
    )
    for row_index, scale in enumerate(LIMIT_SCALES):
        for method_id in METHOD_IDS:
            rows = sorted(
                (
                    row
                    for row in surface
                    if row["method_id"] == method_id
                    and math.isclose(float(row["limit_scale"]), scale)
                    and all(row[field] is not None for field in fields)
                ),
                key=lambda row: float(row["rho_e07_p_only"]),
            )
            rho = [float(row["rho_e07_p_only"]) for row in rows]
            for axis, field in zip(axes[row_index], fields):
                axis.plot(
                    rho,
                    [float(row[field]) for row in rows],
                    color=METHOD_COLORS[method_id],
                    linestyle=METHOD_LINESTYLES[method_id],
                    marker="o",
                    markersize=2.8,
                    linewidth=1.25,
                    label=METHOD_LABELS[method_id],
                )
        for axis in axes[row_index]:
            axis.axvline(
                1.0,
                color="black",
                linestyle="--",
                linewidth=0.9,
                alpha=0.75,
            )
            axis.set_xscale("log", base=2)
            axis.grid(alpha=0.22)
        axes[row_index, 0].set_ylabel(f"{scale:g}× A/J\nPulse fraction")
        axes[row_index, 1].set_ylabel("Event rate (Hz)")
        axes[row_index, 2].set_ylabel("Median ripple / |vref|")
        axes[row_index, 3].set_ylabel("P95 ripple / |vref|")
        axes[row_index, 0].set_ylim(-0.03, 1.03)
        axes[row_index, 1].set_ylim(-3.0, 103.0)
        axes[row_index, 2].set_ylim(-0.03, 2.08)
        axes[row_index, 3].set_ylim(-0.03, 2.08)
    for axis, title in zip(
        axes[0],
        (
            "Rest-to-rest occurrence",
            "Stop-and-go rate",
            "Median exact-profile severity",
            "P95 exact-profile tail",
        ),
    ):
        axis.set_title(title)
    for axis in axes[-1]:
        axis.set_xlabel("ρ_E07 = vref / P-only critical velocity")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        loc="outside right upper",
        ncol=1,
        fontsize=9,
    )
    figure.suptitle(
        "E09 P-only baseline and five finite-difference methods",
        fontsize=16,
    )
    _save_figure(figure, figures_directory, "stop_go_method_comparison")
    plt.close(figure)


def _write_exact_velocity_comparison(
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    start_time_s = 0.5
    end_time_s = 0.6
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(10.5, 9.4),
        sharex=True,
        constrained_layout=True,
    )
    for axis, ratio in zip(axes, (0.5, 1.0, 1.2)):
        input_id = _input_id_for_vendor_ratio(ratio)
        reference = references[input_id]
        reference_velocity = float(reference.velocity_rad_s[0])
        axis.axhline(
            reference_velocity,
            color="black",
            linewidth=1.5,
            linestyle="--",
            label="Reference velocity",
            zorder=1,
        )
        for method_id in METHOD_IDS:
            time, _position, velocity = _dense_profile_state(
                tracking_runs[(_case_id(method_id, 1.0), input_id)],
                start_time_s,
                end_time_s,
            )
            axis.plot(
                time,
                velocity,
                color=METHOD_COLORS[method_id],
                linestyle=METHOD_LINESTYLES[method_id],
                linewidth=1.35,
                label=METHOD_LABELS[method_id],
                zorder=3,
            )
        for boundary in np.arange(
            start_time_s,
            end_time_s + DT_S / 2.0,
            DT_S,
        ):
            axis.axvline(
                boundary,
                color="gray",
                linewidth=0.55,
                alpha=0.35,
            )
        axis.set_title(
            f"Vendor limits, ρ_E07={ratio:g}, vref={reference_velocity:.6g} rad/s"
        )
        axis.set_ylabel("Velocity (rad/s)")
        axis.set_ylim(
            -0.05 * abs(reference_velocity),
            2.05 * abs(reference_velocity),
        )
        axis.ticklabel_format(axis="y", style="plain", useOffset=False)
        axis.grid(alpha=0.2)
    axes[0].legend(loc="upper center", ncol=2, fontsize=8.3)
    axes[-1].set(
        xlabel="Time (s)",
        xlim=(start_time_s, end_time_s),
    )
    figure.suptitle(
        "E09 exact sub-cycle velocity: P-only baseline vs five differences",
        fontsize=15,
    )
    _save_figure(
        figure,
        figures_directory,
        "stop_go_exact_velocity_comparison",
    )
    plt.close(figure)


def _write_cross_method_position_figures(
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    figures_directory: Path,
) -> None:
    """Write the root position figures before the generic 20-case plotter runs."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures_directory.mkdir(parents=True, exist_ok=True)
    start_time_s = 0.5
    end_time_s = 0.6
    for input_id, vendor_ratio, _reference_velocity in INPUTS:
        reference = references[input_id]
        figure, axes = plt.subplots(
            2,
            1,
            figsize=(10.8, 7.4),
            sharex=True,
            constrained_layout=True,
            gridspec_kw={"height_ratios": (1.35, 1.0)},
        )
        reference_origin = float(
            np.interp(
                start_time_s,
                reference.time_s,
                reference.position_rad,
            )
        )
        reference_mask = (reference.time_s >= start_time_s - 1e-12) & (
            reference.time_s <= end_time_s + 1e-12
        )
        axes[0].plot(
            reference.time_s[reference_mask],
            (reference.position_rad[reference_mask] - reference_origin) * 1e6,
            color="black",
            linewidth=1.8,
            label="Linear reference",
            zorder=10,
        )
        for method_id in METHOD_IDS:
            time, position, _velocity = _dense_profile_state(
                tracking_runs[(_case_id(method_id, 1.0), input_id)],
                start_time_s,
                end_time_s,
            )
            if not time.size:
                continue
            reference_dense = np.interp(
                time,
                reference.time_s,
                reference.position_rad,
            )
            error = position - reference_dense
            axes[0].plot(
                time,
                (position - position[0]) * 1e6,
                color=METHOD_COLORS[method_id],
                linestyle=METHOD_LINESTYLES[method_id],
                linewidth=1.25,
                label=METHOD_LABELS[method_id],
            )
            axes[1].plot(
                time,
                (error - np.median(error)) * 1e6,
                color=METHOD_COLORS[method_id],
                linestyle=METHOD_LINESTYLES[method_id],
                linewidth=1.2,
                label=METHOD_LABELS[method_id],
            )
        for axis in axes:
            for boundary in np.arange(
                start_time_s,
                end_time_s + DT_S / 2.0,
                DT_S,
            ):
                axis.axvline(
                    boundary,
                    color="gray",
                    linewidth=0.55,
                    alpha=0.35,
                )
            axis.grid(alpha=0.2)
        axes[0].set(
            title=(
                f"P-only baseline and five differences: {input_id} "
                f"(ρ_E07={vendor_ratio:g})"
            ),
            ylabel="Local displacement from 0.5 s (µrad)",
        )
        axes[0].legend(loc="best", ncol=2, fontsize=8.1)
        axes[1].axhline(
            0.0,
            color="black",
            linewidth=0.8,
            alpha=0.65,
        )
        axes[1].set(
            xlabel="Time (s)",
            ylabel="Centered position error (µrad)",
            xlim=(start_time_s, end_time_s),
        )
        axes[1].legend(loc="best", ncol=2, fontsize=8.1)
        figure.savefig(
            figures_directory / f"{input_id}_position.png",
            dpi=200,
            bbox_inches="tight",
            facecolor="white",
        )
        plt.close(figure)


def write_e09_artifacts(
    *,
    analysis_directory: Path,
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    experiment_spec: ExperimentSpec,
    create_figures: bool,
) -> None:
    """Write six per-method E07-style suites and direct method comparisons."""

    surface = _surface_rows(
        experiment_spec,
        tracking_runs,
        trajectory_rows,
    )
    comparison = _comparison_rows(surface)
    write_rows_csv(analysis_directory / "stop_go_surface.csv", surface)
    write_rows_csv(
        analysis_directory / "stop_go_method_comparison.csv",
        comparison,
    )
    _write_acceptance_summary(analysis_directory, comparison)
    if not create_figures:
        return

    figures_directory = analysis_directory / "figures"
    for method_id in METHOD_IDS:
        method_directory = figures_directory / "by_method" / method_id
        _write_method_phase_map(surface, method_id, method_directory)
        _write_method_rho_response(surface, method_id, method_directory)
        _write_method_velocity_profile(
            references,
            tracking_runs,
            method_id,
            method_directory,
        )
        _write_method_position_figures(
            references,
            tracking_runs,
            method_id,
            method_directory / "positions",
        )
    _write_method_comparison(surface, figures_directory)
    _write_exact_velocity_comparison(
        references,
        tracking_runs,
        figures_directory,
    )
    _write_cross_method_position_figures(
        references,
        tracking_runs,
        figures_directory,
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    methods = _methods()
    input_specs = tuple(
        ExperimentInput(
            input_id,
            (f"experiments/E07_position_only_stop_and_go/inputs/{input_id}.csv"),
            required=True,
            description=(
                "E07 three-second constant-velocity analytic reference; "
                f"v={velocity:.17g} rad/s, "
                f"v/vendor_vcrit={ratio:g}"
            ),
        )
        for input_id, ratio, velocity in INPUTS
    )
    return ExperimentSpec(
        experiment_id="E09",
        slug="pva_finite_difference_stop_and_go",
        title="E09 PVA finite-difference stop-and-go suppression",
        question=(
            "Against an internally rerun E07 P-only baseline, does each of "
            "E04's five causal PVA finite-difference targets eliminate exact "
            "rest-to-rest stop-and-go pulses, and how do the methods differ?"
        ),
        hypothesis=(
            "The P-only baseline reproduces E07's critical-velocity threshold. "
            "After startup, every causal finite-difference method reconstructs "
            "the constant reference velocity and zero acceleration, eliminating "
            "baseline stop-and-go; remaining tracking differences follow the "
            "declared target ages of zero, one, or two samples."
        ),
        description=(
            "E09 reuses E07's constant-velocity and A/J matrix, reruns the exact "
            "E07 PositionOnly → ZOH → P baseline, and adds the five E04 causal "
            "PVA finite-difference methods."
        ),
        independent_variables=(
            "target_method",
            "derivative_represented_time",
            "reference_velocity_rad_s",
            "acceleration_jerk_limit_scale",
        ),
        controlled_variables={
            "input_source_experiment": "E07",
            "input_ids": tuple(input_id for input_id, _ratio, _velocity in INPUTS),
            "axis_count": 1,
            "dt_s": DT_S,
            "duration_s": DURATION_S,
            "measurement_policy": "position_only",
            "finite_difference_scheduled_position_available": True,
            "target_components_by_role": {
                "p_only_baseline": "p",
                "finite_difference": "pva",
            },
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "governor": "none",
            "follower": "ordinary_ruckig_unshielded",
            "initial_state_policy": "reference_position_zero_derivatives",
            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
            "main_evaluation_s": [MAIN_START_S, MAIN_END_S],
            "p_only_baseline_source": "exact E07 method rerun inside E09",
            "rho_interpretation": (
                "E07 P-only comparison coordinate, not a PVA threshold"
            ),
            "vendor_reference": {
                "max_acceleration_rad_s2": VENDOR_ACCELERATION_RAD_S2,
                "max_jerk_rad_s3": VENDOR_JERK_RAD_S3,
                "p_only_critical_velocity_rad_s": (VENDOR_CRITICAL_VELOCITY_RAD_S),
            },
        },
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
            "run_config.limits.max_acceleration_rad_s2",
            "run_config.limits.max_jerk_rad_s3",
        ),
        inputs=input_specs,
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
            pairs=tuple(
                MethodPair(
                    _case_id(P_ONLY_BASELINE_METHOD_ID, scale),
                    _case_id(method_id, scale),
                    f"{method_id}_vs_p_only_s{_token(scale)}",
                )
                for scale in LIMIT_SCALES
                for method_id in FINITE_DIFFERENCE_METHOD_IDS
            ),
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=tuple(input_id for input_id, _ratio, _velocity in INPUTS),
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=_cases(methods),
        artifact_writer=write_e09_artifacts,
    )
