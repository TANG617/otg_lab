"""E07: P-only stop-and-go threshold and velocity-ripple severity."""

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
    Trajectory,
)
from otg_lab.runio import write_rows_csv

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
MAIN_METHOD_ID = "position_zoh_p_ruckig"


def critical_reference_velocity(
    acceleration_rad_s2: float,
    jerk_rad_s3: float,
    dt_s: float = DT_S,
) -> float:
    """Maximum average rest-to-rest speed achievable in one cycle."""

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


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


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


def _main_case_id(limit_scale: float) -> str:
    return f"p_limit_s{_token(limit_scale)}"


def _cases() -> tuple[ExperimentCase, ...]:
    return tuple(
        ExperimentCase(
            case_id=_main_case_id(scale),
            method_id=MAIN_METHOD_ID,
            run_config=_run_config(scale),
            factors={
                "limit_scale": scale,
                "max_acceleration_rad_s2": (
                    scale * VENDOR_ACCELERATION_RAD_S2
                ),
                "max_jerk_rad_s3": scale * VENDOR_JERK_RAD_S3,
                "critical_velocity_rad_s": (
                    scale * VENDOR_CRITICAL_VELOCITY_RAD_S
                ),
            },
            description=(
                "PositionOnly → ZOH → P → NoGovernor → ordinary Ruckig; "
                f"A/J limit scale={scale:g}"
            ),
        )
        for scale in LIMIT_SCALES
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
    if metric_id not in _ASSIGNED
    and metric_id not in {"settled", "settle_time_s"}
)


def _metric_lookup(
    rows: Sequence[MetricRow],
) -> dict[tuple[str, str, str, str], MetricRow]:
    return {
        (row.method_id, row.input_id, row.window_id, row.metric_id): row
        for row in rows
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


def _selected_trace_rows(
    run: TrackingRun,
    start_time_s: float = MAIN_START_S,
    end_time_s: float = MAIN_END_S,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        row
        for row in run.trace_rows
        if row.get("command_time_s") is not None
        and start_time_s - 1e-12
        <= float(row["command_time_s"])
        <= end_time_s + 1e-12
    )


def _target_zero_va(run: TrackingRun) -> bool:
    rows = _selected_trace_rows(run)
    return bool(rows) and all(
        row.get("raw_target_velocity_rad_s") is not None
        and row.get("raw_target_acceleration_rad_s2") is not None
        and abs(float(row["raw_target_velocity_rad_s"])) <= 1e-12
        and abs(float(row["raw_target_acceleration_rad_s2"])) <= 1e-12
        for row in rows
    )


def _all_zero(values: Sequence[float | int | None]) -> bool:
    return bool(values) and all(
        value is not None and abs(float(value)) <= 1e-12 for value in values
    )


def _trace_safety_guaranteed(run: TrackingRun) -> bool:
    def guaranteed(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return value is True

    return bool(run.trace_rows) and all(
        guaranteed(row.get("safety_guarantee")) for row in run.trace_rows
    )


def _guardrail_status(
    lookup: Mapping[tuple[str, str, str, str], MetricRow],
    run: TrackingRun,
    case_id: str,
    input_id: str,
) -> tuple[bool, bool, bool, float | int | None]:
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
    exact_profiles_available = all(value is not None for value in values)
    exact_fraction = _metric_value(
        lookup,
        case_id,
        input_id,
        "profile_exact_fraction",
        "full_overlap",
    )
    trace_safety_pass = _trace_safety_guaranteed(run)
    guardrail_pass = bool(
        exact_profiles_available
        and exact_fraction is not None
        and math.isclose(float(exact_fraction), 1.0, abs_tol=1e-12)
        and trace_safety_pass
        and _all_zero(values)
    )
    return (
        exact_profiles_available,
        trace_safety_pass,
        guardrail_pass,
        exact_fraction,
    )


def _severity_probe_pass(
    limit_scale: float,
    vendor_ratio: float,
    ripple_ratio: float | int | None,
) -> bool | None:
    if not math.isclose(limit_scale, 1.0, abs_tol=1e-12):
        return None
    expected_ranges = {
        0.5: (1.9, 2.1),
        2.0: (0.9, 1.1),
        4.0: (0.45, 0.55),
    }
    for probe_ratio, (lower, upper) in expected_ranges.items():
        if math.isclose(vendor_ratio, probe_ratio, abs_tol=1e-12):
            return bool(
                ripple_ratio is not None
                and lower <= float(ripple_ratio) <= upper
            )
    return None


def _surface_rows(
    experiment_spec: ExperimentSpec,
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
) -> list[dict[str, Any]]:
    lookup = _metric_lookup(trajectory_rows)
    cases = {case.case_id: case for case in experiment_spec.cases}
    output: list[dict[str, Any]] = []
    for scale in LIMIT_SCALES:
        case_id = _main_case_id(scale)
        case = cases[case_id]
        acceleration = case.run_config.limits.max_acceleration_rad_s2
        jerk = case.run_config.limits.max_jerk_rad_s3
        critical_velocity = critical_reference_velocity(acceleration, jerk)
        for input_id, vendor_ratio, reference_velocity in INPUTS:
            rho = reference_velocity / critical_velocity
            metric = partial(
                _metric_value,
                lookup,
                case_id,
                input_id,
                window_id="main_evaluation",
            )
            pulse_fraction = metric("rest_to_rest_pulse_fraction")
            reachability_agreement = metric(
                "one_cycle_reachability_pulse_agreement"
            )
            ripple_median = metric("profile_velocity_ripple_median")
            ripple_ratio_median = metric(
                "profile_velocity_ripple_to_reference_median"
            )
            ripple_ratio_p95 = metric(
                "profile_velocity_ripple_to_reference_p95"
            )
            severity_metrics_available = bool(
                ripple_median is not None
                and ripple_ratio_median is not None
                and ripple_ratio_p95 is not None
                and float(ripple_median) >= 0.0
                and float(ripple_ratio_median) >= 0.0
                and float(ripple_ratio_p95) >= 0.0
            )
            if rho <= 0.95 + 1e-12:
                expected_region = "pulse"
                threshold_pass = bool(
                    pulse_fraction is not None
                    and float(pulse_fraction) >= 0.95
                )
            elif rho >= 1.05 - 1e-12:
                expected_region = "continuous"
                threshold_pass = bool(
                    pulse_fraction is not None
                    and float(pulse_fraction) <= 0.05
                )
            else:
                expected_region = "boundary_diagnostic"
                threshold_pass = None
            reachability_pass = (
                None
                if expected_region == "boundary_diagnostic"
                else bool(
                    reachability_agreement is not None
                    and float(reachability_agreement) >= 0.99
                )
            )
            severity_probe_pass = _severity_probe_pass(
                scale,
                vendor_ratio,
                ripple_ratio_median,
            )
            run = tracking_runs[(case_id, input_id)]
            (
                exact_guardrails_available,
                trace_safety_pass,
                guardrail_pass,
                exact_fraction,
            ) = _guardrail_status(
                lookup,
                run,
                case_id,
                input_id,
            )
            target_zero_pass = _target_zero_va(run)
            output.append(
                {
                    "case_id": case_id,
                    "input_id": input_id,
                    "vendor_velocity_ratio": vendor_ratio,
                    "reference_velocity_rad_s": reference_velocity,
                    "limit_scale": scale,
                    "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
                    "max_acceleration_rad_s2": acceleration,
                    "max_jerk_rad_s3": jerk,
                    "critical_velocity_rad_s": critical_velocity,
                    "rho": rho,
                    "expected_region": expected_region,
                    "rest_to_rest_pulse_fraction": pulse_fraction,
                    "stop_go_event_rate_hz": metric("stop_go_event_rate_hz"),
                    "endpoint_stop_fraction": metric("endpoint_stop_fraction"),
                    "longest_rest_to_rest_pulse_run_cycles": metric(
                        "longest_rest_to_rest_pulse_run_cycles"
                    ),
                    "profile_peak_velocity_to_reference_median": metric(
                        "profile_peak_velocity_to_reference_median"
                    ),
                    "profile_velocity_ripple_median_rad_s": ripple_median,
                    "profile_velocity_ripple_to_reference_median": (
                        ripple_ratio_median
                    ),
                    "profile_velocity_ripple_to_reference_p95": (
                        ripple_ratio_p95
                    ),
                    "one_cycle_reachability_pulse_agreement": (
                        reachability_agreement
                    ),
                    "position_rmse_rad": metric("position_rmse"),
                    "lag_s": metric("lag_s"),
                    "profile_exact_fraction": exact_fraction,
                    "raw_target_zero_va_pass": target_zero_pass,
                    "threshold_acceptance_pass": threshold_pass,
                    "reachability_acceptance_pass": reachability_pass,
                    "severity_metrics_available_pass": (
                        severity_metrics_available
                    ),
                    "severity_probe_acceptance_pass": severity_probe_pass,
                    "exact_profile_guardrails_available": (
                        exact_guardrails_available
                    ),
                    "trace_safety_guarantee_pass": trace_safety_pass,
                    "guardrail_pass": guardrail_pass,
                    "run_completed": run.status.completed,
                    "acceptance_pass": bool(
                        run.status.completed
                        and target_zero_pass
                        and guardrail_pass
                        and severity_metrics_available
                        and (threshold_pass is not False)
                        and (reachability_pass is not False)
                        and (severity_probe_pass is not False)
                    ),
                }
            )
    return output


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


def _write_phase_map(
    surface: Sequence[Mapping[str, Any]],
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    by_key = {
        (
            float(row["vendor_velocity_ratio"]),
            float(row["limit_scale"]),
        ): row
        for row in surface
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
            if (
                row["profile_velocity_ripple_to_reference_median"]
                is not None
            ):
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
    discrete = ListedColormap(["#3b4cc0", "#f2c14e"])
    discrete.set_bad("#d9d9d9")
    occurrence = axes[0].pcolormesh(
        x_grid,
        y_grid,
        pulse,
        cmap=discrete,
        norm=BoundaryNorm((-0.5, 0.5, 1.5), discrete.N),
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
            label="Theoretical boundary ρ=1",
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
    occurrence_bar = figure.colorbar(
        occurrence,
        ax=axes[0],
        ticks=(0.0, 1.0),
    )
    occurrence_bar.ax.set_yticklabels(("No pulse", "Pulse"))
    severity_bar = figure.colorbar(severity_map, ax=axes[1])
    severity_bar.set_label("Median velocity ripple / |reference velocity|")
    figure.suptitle(
        "E07 P-only stop-and-go occurrence and severity",
        fontsize=15,
    )
    _save_figure(figure, figures_directory, "stop_go_phase_map")
    plt.close(figure)


def _write_collapse_plot(
    surface: Sequence[Mapping[str, Any]],
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(
        1,
        2,
        figsize=(13.0, 5.0),
        sharex=True,
        constrained_layout=True,
    )
    for scale in LIMIT_SCALES:
        rows = sorted(
            (
                row
                for row in surface
                if math.isclose(float(row["limit_scale"]), scale)
                and row["rest_to_rest_pulse_fraction"] is not None
                and row["profile_velocity_ripple_to_reference_median"]
                is not None
            ),
            key=lambda row: float(row["rho"]),
        )
        rho = [float(row["rho"]) for row in rows]
        axes[0].plot(
            rho,
            [float(row["rest_to_rest_pulse_fraction"]) for row in rows],
            marker="o",
            markersize=3.5,
            linewidth=1.3,
            label=f"{scale:g}× A/J",
        )
        axes[1].plot(
            rho,
            [
                float(
                    row["profile_velocity_ripple_to_reference_median"]
                )
                for row in rows
            ],
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
            linewidth=1.2,
            label="ρ=1",
        )
        axis.set_xscale("log", base=2)
        axis.grid(alpha=0.25)
        axis.set_xlabel(
            "ρ = reference velocity / one-cycle critical velocity"
        )
    axes[0].set(
        title="Occurrence",
        ylabel="Rest-to-rest pulse fraction",
        ylim=(-0.03, 1.03),
    )
    axes[1].set(
        title="Severity (configuration-dependent)",
        ylabel="Median velocity ripple / |reference velocity|",
        ylim=(-0.03, 2.15),
    )
    axes[0].legend(loc="best")
    figure.suptitle(
        "Reachability predicts pulse occurrence; ripple quantifies severity",
        fontsize=15,
    )
    _save_figure(figure, figures_directory, "stop_go_threshold_collapse")
    plt.close(figure)


def _dense_profile_state(
    run: TrackingRun,
    start_time_s: float,
    end_time_s: float,
    grid_dt_s: float = 0.0001,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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
                if times and grid.size and math.isclose(
                    float(grid[0]),
                    times[-1],
                    rel_tol=0.0,
                    abs_tol=1e-12,
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
                    (
                        velocity
                        + acceleration * local
                        + 0.5 * jerk * local**2
                    ).tolist()
                )
            duration = segment_end - segment_start
            position = (
                position
                + velocity * duration
                + 0.5 * acceleration * duration**2
                + jerk * duration**3 / 6.0
            )
            velocity = (
                velocity
                + acceleration * duration
                + 0.5 * jerk * duration**2
            )
            acceleration += jerk * duration
    return (
        np.asarray(times),
        np.asarray(positions),
        np.asarray(velocities),
    )


def _input_id_for_vendor_ratio(ratio: float) -> str:
    return next(
        input_id
        for input_id, value, _velocity in INPUTS
        if math.isclose(value, ratio, abs_tol=1e-12)
    )


def _write_velocity_profile(
    references: Mapping[str, Trajectory],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    figures_directory: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures_directory.mkdir(parents=True, exist_ok=True)
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
            tracking_runs[(_main_case_id(1.0), input_id)],
            start_time_s,
            end_time_s,
        )
        axis.axhline(
            reference_velocity,
            color="black",
            linewidth=1.5,
            label="Reference velocity",
        )
        axis.plot(
            time,
            velocity,
            color="#d95f02",
            linewidth=1.5,
            label="P-only exact profile",
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
            f"Vendor limits, ρ={rho:g}, "
            f"vref={reference_velocity:.6g} rad/s"
        )
        axis.set_ylabel("Velocity (rad/s)")
        axis.grid(alpha=0.2)
        axis.legend(loc="upper right")
    axes[-1].set(
        xlabel="Time (s)",
        xlim=(start_time_s, end_time_s),
    )
    figure.suptitle(
        "P-only sub-cycle velocity below, at, and above the threshold",
        fontsize=15,
    )
    _save_figure(figure, figures_directory, "stop_go_subcycle_velocity")
    plt.close(figure)


def _write_local_position_figures(
    references: Mapping[str, Trajectory],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
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
        reference_mask = (
            (reference.time_s >= start_time_s - 1e-12)
            & (reference.time_s <= end_time_s + 1e-12)
        )
        reference_time = reference.time_s[reference_mask]
        reference_position = reference.position_rad[reference_mask]
        axes[0].plot(
            reference_time,
            (reference_position - reference_origin) * 1e6,
            color="black",
            linewidth=1.6,
            linestyle="--",
            label="Linear reference",
            zorder=5,
        )
        for scale, color in zip(LIMIT_SCALES, colors):
            run = tracking_runs[(_main_case_id(scale), input_id)]
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
            rho = vendor_ratio / scale
            label = f"{scale:g}× A/J (ρ={rho:.3g})"
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
                f"{input_id}: exact P-only position within 100 ms "
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


def write_e07_artifacts(
    *,
    analysis_directory: Path,
    references: Mapping[str, Trajectory],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    experiment_spec: ExperimentSpec,
    create_figures: bool,
) -> None:
    """Write E07's P-only threshold surface and mechanism figures."""

    surface = _surface_rows(
        experiment_spec,
        tracking_runs,
        trajectory_rows,
    )
    write_rows_csv(analysis_directory / "stop_go_surface.csv", surface)
    if not create_figures:
        return
    figures_directory = analysis_directory / "figures"
    _write_local_position_figures(
        references,
        tracking_runs,
        figures_directory,
    )
    _write_phase_map(surface, figures_directory)
    _write_collapse_plot(surface, figures_directory)
    _write_velocity_profile(references, tracking_runs, figures_directory)


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root

    methods = (
        TrackingMethodSpec(
            method_id=MAIN_METHOD_ID,
            estimator=ComponentSpec("position_only"),
            predictor=ComponentSpec("zero_order_hold"),
            target_builder=ComponentSpec("p"),
            governor=ComponentSpec("none"),
            follower=ComponentSpec("ruckig"),
            description=(
                "PositionOnly → ZOH → P → NoGovernor → "
                "ordinary unshielded Ruckig"
            ),
        ),
    )
    input_specs = tuple(
        ExperimentInput(
            input_id,
            (
                "experiments/E07_position_only_stop_and_go/"
                f"inputs/{input_id}.csv"
            ),
            required=True,
            description=(
                "Three-second constant-velocity analytic reference; "
                f"v={velocity:.17g} rad/s, "
                f"v/vendor_vcrit={ratio:g}"
            ),
        )
        for input_id, ratio, velocity in INPUTS
    )
    return ExperimentSpec(
        experiment_id="E07",
        slug="position_only_stop_and_go",
        title="E07 P-only stop-and-go threshold and severity",
        question=(
            "At which reference-speed and acceleration/jerk configurations "
            "does P-only tracking create exact rest-to-rest velocity pulses, "
            "and how large is the resulting within-cycle velocity ripple?"
        ),
        hypothesis=(
            "Pulse occurrence changes at "
            "rho = reference velocity / critical velocity = 1, while exact "
            "normalized velocity ripple quantifies severity continuously."
        ),
        description=(
            "P-only mechanism experiment. It contains no PV/PVA method or "
            "target-builder ablation."
        ),
        independent_variables=(
            "reference_velocity_rad_s",
            "acceleration_jerk_limit_scale",
        ),
        controlled_variables={
            "axis_count": 1,
            "dt_s": DT_S,
            "duration_s": DURATION_S,
            "measurement_policy": "position_only",
            "estimator": "position_only",
            "predictor": "zero_order_hold",
            "target_builder": "p",
            "governor": "none",
            "follower": "ruckig",
            "initial_state_policy": "reference_position_zero_derivatives",
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
            "main_evaluation_s": [MAIN_START_S, MAIN_END_S],
            "vendor_reference": {
                "max_acceleration_rad_s2": VENDOR_ACCELERATION_RAD_S2,
                "max_jerk_rad_s3": VENDOR_JERK_RAD_S3,
                "critical_velocity_rad_s": (
                    VENDOR_CRITICAL_VELOCITY_RAD_S
                ),
            },
        },
        allowed_method_differences=(
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
        comparison_spec=ComparisonSpec(),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=_cases(),
        artifact_writer=write_e07_artifacts,
    )
