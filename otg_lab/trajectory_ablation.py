"""Shared declarations and artifacts for the E03--E06 state-target ablations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import (
    AVAILABLE,
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
    MetricRow,
)
from .experiment import ExperimentCase, ExperimentInput, ExperimentSpec, InputGate
from .models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
)
from .runio import write_rows_csv

INPUT_IDS = ("quadratic_with_extremum", "cubic", "sine")
RECORDED_BASELINE_INPUT_IDS = (
    "recorded_tasks_original_no_velocity_limit",
    "recorded_tasks_simplified_with_velocity_limit",
)
E01_INPUT_IDS = INPUT_IDS + RECORDED_BASELINE_INPUT_IDS
BASELINE_METHOD_ID = "p_kp1_baseline"
CURRENT_ONLINE_BASELINE_CASE_ID = "p_kp1_current_online_v4p2_a8p2_j41"
DT_S = 0.01
MOTION_END_S = 3.0

PRIMARY = ("position_rmse",)
SECONDARY = (
    "position_mae",
    "position_p95_abs_error",
    "position_max_abs_error",
    "position_iae",
    "posterior_velocity_rmse",
    "posterior_acceleration_rmse",
    "prediction_velocity_rmse",
    "prediction_acceleration_rmse",
    "raw_target_velocity_rmse",
    "raw_target_acceleration_rmse",
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
)

_ACCEPTANCE_GUARDRAILS = (
    "output_velocity_violation_count",
    "output_acceleration_violation_count",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
    "deadline_miss_rate",
)


def _config() -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=4.1,
            max_acceleration_rad_s2=8.2,
            max_jerk_rad_s3=4000.0,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _current_online_config() -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=4.2,
            max_acceleration_rad_s2=8.2,
            max_jerk_rad_s3=41.0,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _target(components: str, time_source: str) -> ComponentSpec:
    return ComponentSpec(
        "scheduled_state",
        {
            "components": components,
            "time_source": time_source,
        },
    )


def _method(
    method_id: str,
    estimator: str | ComponentSpec,
    predictor: str | ComponentSpec,
    target: ComponentSpec,
    description: str,
) -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id=method_id,
        estimator=(
            estimator
            if isinstance(estimator, ComponentSpec)
            else ComponentSpec(estimator)
        ),
        predictor=(
            predictor
            if isinstance(predictor, ComponentSpec)
            else ComponentSpec(predictor)
        ),
        target_builder=target,
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        required=True,
        description=description,
    )


def build_state_target_methods(
    components: str,
    *,
    include_truth: bool,
    include_differences: bool,
) -> tuple[TrackingMethodSpec, ...]:
    """Build the shared P/PV/PVA target-method matrix.

    Recorded position-only inputs can set ``include_truth=False`` while
    reusing exactly the same causal finite-difference declarations as E04/E06.
    """

    truth_id = f"{components}_truth_kp1"
    methods = [
        _method(
            BASELINE_METHOD_ID,
            "position_only",
            "zero_order_hold",
            _target("p", "prediction_time"),
            "Scheduled P[k+1], V=A=0 → ordinary Ruckig",
        ),
    ]
    if include_truth:
        methods.append(
            _method(
                truth_id,
                "position_only",
                ComponentSpec("oracle", {"noncausal_diagnostic": True}),
                _target(components, "prediction_time"),
                f"Scheduled P[{components.upper()}] truth at k+1 → ordinary Ruckig",
            )
        )
    if not include_differences:
        return tuple(methods)

    methods.extend(
        (
            _method(
                f"{components}_est_backward_o1_k",
                "backward_fd_o1",
                "zero_order_hold",
                _target(components, "source_state_time"),
                f"Backward FD O1 {components.upper()}[k], one-sample age",
            ),
            _method(
                f"{components}_est_backward_o2_k",
                "backward_fd_o2",
                "zero_order_hold",
                _target(components, "source_state_time"),
                f"Backward FD O2 {components.upper()}[k], one-sample age",
            ),
            _method(
                f"{components}_est_centered_o2_km1",
                "centered_fd_o2_delay1",
                "zero_order_hold",
                _target(components, "source_state_time"),
                f"Centered FD O2 {components.upper()}[k-1], two-sample age",
            ),
            _method(
                f"{components}_pred_backward_o1_kp1",
                "position_only",
                "future_backward_fd_o1",
                _target(components, "prediction_time"),
                f"Causal backward FD O1 prediction of {components.upper()}[k+1]",
            ),
            _method(
                f"{components}_pred_backward_o2_kp1",
                "position_only",
                "future_backward_fd_o2",
                _target(components, "prediction_time"),
                f"Causal backward FD O2 prediction of {components.upper()}[k+1]",
            ),
        )
    )
    return tuple(methods)


def _common_inputs() -> tuple[ExperimentInput, ...]:
    return tuple(
        ExperimentInput(
            input_id,
            f"data/trajectories/{input_id}.csv",
            required=True,
            description="Analytic P/V/A/J truth trajectory",
        )
        for input_id in INPUT_IDS
    )


def _p_only_baseline_inputs() -> tuple[ExperimentInput, ...]:
    return _common_inputs() + tuple(
        ExperimentInput(
            input_id,
            f"data/trajectories/{input_id}.csv",
            required=True,
            description=(
                "Fixed-grid position-only recorded trajectory; scheduled "
                "P[k+1] is available one step ahead"
            ),
        )
        for input_id in RECORDED_BASELINE_INPUT_IDS
    )


def _common_windows() -> tuple[EvaluationWindow, ...]:
    return (
        EvaluationWindow("full_overlap"),
        EvaluationWindow(
            "main_evaluation",
            start_time_s=0.04,
            end_time_s=MOTION_END_S,
        ),
    )


def _p_only_baseline_windows() -> tuple[EvaluationWindow, ...]:
    return (
        EvaluationWindow("full_overlap"),
        EvaluationWindow(
            "main_evaluation",
            start_time_s=0.04,
        ),
    )


def _common_metric_roles() -> dict[str, tuple[str, ...]]:
    return {
        "primary": PRIMARY,
        "secondary": SECONDARY,
        "guardrail": GUARDRAIL,
        "diagnostic": DIAGNOSTIC,
    }


def _common_controlled_variables() -> dict[str, Any]:
    return {
        "inputs": INPUT_IDS,
        "axis_count": 1,
        "dt_s": DT_S,
        "measurement_policy": "position_only",
        "scheduled_position_available_one_step_ahead": True,
        "prediction_horizon_s": DT_S,
        "minimum_duration_s": DT_S,
        "limits": {
            "max_velocity_rad_s": 4.1,
            "max_acceleration_rad_s2": 8.2,
            "max_jerk_rad_s3": 4000.0,
        },
        "governor": "none",
        "follower": "ordinary_ruckig_unshielded",
    }


def build_p_only_baseline() -> ExperimentSpec:
    """Build the standalone scheduled P[k+1] baseline audit."""

    methods = build_state_target_methods(
        "p",
        include_truth=False,
        include_differences=False,
    )
    controlled_variables = {
        **_common_controlled_variables(),
        "inputs": E01_INPUT_IDS,
        "deployment_comparison_input": (
            "recorded_tasks_simplified_with_velocity_limit"
        ),
        "report_baseline": {
            "case_id": CURRENT_ONLINE_BASELINE_CASE_ID,
            "input_id": "recorded_tasks_original_no_velocity_limit",
            "target_components": "P",
            "max_velocity_rad_s": 4.2,
            "max_acceleration_rad_s2": 8.2,
            "max_jerk_rad_s3": 41.0,
            "role": "current_online_status_quo",
        },
        "experimental_paired_baseline": {
            "case_id": BASELINE_METHOD_ID,
            "input_id": "recorded_tasks_simplified_with_velocity_limit",
            "max_velocity_rad_s": 4.1,
            "max_acceleration_rad_s2": 8.2,
            "max_jerk_rad_s3": 4000.0,
            "role": "unchanged_a04_a06_gain_denominator",
        },
        "original_recorded_role": "current_online_p_only_report_baseline",
        "analytic_role": "intermediate_method_correctness_only",
    }
    return ExperimentSpec(
        experiment_id="E01",
        slug="p_only_baseline",
        title="E01 scheduled P-only trajectory baseline",
        question=(
            "What raw-time position-tracking performance does the scheduled "
            "P[k+1], V=A=0 baseline achieve on the analytic verification "
            "set and the original/velocity-limited recorded trajectories, "
            "including the current online P-only 4.2/8.2/41 setting?"
        ),
        hypothesis=(
            "The baseline completes all five trajectories with N−1 aligned "
            "commands and no declared guardrail violations, providing "
            "auditable P-only references. The original recorded 4.2/8.2/41 "
            "arm represents the current online status quo; only the "
            "velocity-limited recorded 4.1/8.2/4000 arm is eligible as the "
            "unchanged paired denominator for PV/PVA experiment gains."
        ),
        description=(
            "Standalone scheduled-position baseline. P[k+1] comes from the "
            "declared reference schedule; online derivative truth is never "
            "read, and the target builder explicitly sets V=A=0. Analytic "
            "inputs are correctness checks; original recorded at 4.2/8.2/41 "
            "is the report's current-online baseline; deployment experiment "
            "comparisons keep the velocity-limited recorded 4.1/8.2/4000 "
            "paired baseline."
        ),
        independent_variables=("input_trajectory", "baseline_role", "runtime_limits"),
        controlled_variables=controlled_variables,
        allowed_method_differences=(),
        inputs=_p_only_baseline_inputs(),
        methods=methods,
        run_config=_config(),
        metric_roles=_common_metric_roles(),
        windows=_p_only_baseline_windows(),
        comparison_spec=ComparisonSpec(),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=(
            ExperimentCase(
                case_id=BASELINE_METHOD_ID,
                method_id=BASELINE_METHOD_ID,
                run_config=_config(),
                factors={"baseline_role_rank": 1.0},
                description=(
                    "Unchanged experimental paired baseline; "
                    "V/A/J=4.1/8.2/4000"
                ),
            ),
            ExperimentCase(
                case_id=CURRENT_ONLINE_BASELINE_CASE_ID,
                method_id=BASELINE_METHOD_ID,
                run_config=_current_online_config(),
                factors={"baseline_role_rank": 0.0},
                description=(
                    "Current online P-only status quo; "
                    "original no-velocity-limit waveform; V/A/J=4.2/8.2/41"
                ),
            ),
        ),
    )


def _experiment_identity(
    experiment_id: str,
) -> tuple[str, str, str, str]:
    identities = {
        "E03": (
            "pva_truth_trajectories",
            "E03 PVA truth trajectory tracking",
            "Does a time-coherent PVA[k+1] truth target outperform P[k+1]?",
            "PVA[k+1] truth lowers raw-time position RMSE on every trajectory.",
        ),
        "E04": (
            "pva_finite_difference_trajectories",
            "E04 causal PVA finite-difference trajectory tracking",
            "Do causal finite-difference PVA targets outperform P[k+1]?",
            "Every declared causal PVA difference method lowers raw-time "
            "position RMSE on every trajectory without guardrail regression.",
        ),
        "E05": (
            "pv_truth_trajectories",
            "E05 PV truth trajectory tracking",
            "Does a time-coherent PV[k+1] truth target outperform P[k+1]?",
            "PV[k+1] truth lowers raw-time position RMSE on every trajectory.",
        ),
        "E06": (
            "pv_finite_difference_trajectories",
            "E06 causal PV finite-difference trajectory tracking",
            "Do causal finite-difference PV targets outperform P[k+1]?",
            "Every declared causal PV difference method lowers raw-time "
            "position RMSE on every trajectory without guardrail regression.",
        ),
    }
    return identities[experiment_id]


def build_trajectory_ablation(
    experiment_id: str,
    *,
    components: str,
    include_differences: bool,
) -> ExperimentSpec:
    slug, title, question, hypothesis = _experiment_identity(experiment_id)
    methods = build_state_target_methods(
        components,
        include_truth=True,
        include_differences=include_differences,
    )
    truth_id = f"{components}_truth_kp1"
    pairs = [
        MethodPair(
            BASELINE_METHOD_ID,
            method.method_id,
            f"{method.method_id}_vs_p_kp1",
        )
        for method in methods
        if method.method_id != BASELINE_METHOD_ID
    ]
    if include_differences:
        pairs.extend(
            MethodPair(
                truth_id,
                method.method_id,
                f"{method.method_id}_vs_truth",
            )
            for method in methods
            if method.method_id not in {BASELINE_METHOD_ID, truth_id}
        )
    return ExperimentSpec(
        experiment_id=experiment_id,
        slug=slug,
        title=title,
        question=question,
        hypothesis=hypothesis,
        description=(
            "Scheduled P[k+1] is declared in advance. Causality restrictions "
            "apply to finite-difference V/A; truth V/A is an explicit "
            "offline, noncausal ceiling."
        ),
        independent_variables=(
            "target_components",
            "derivative_source",
            "derivative_represented_time",
        ),
        controlled_variables=_common_controlled_variables(),
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
        ),
        inputs=_common_inputs(),
        methods=methods,
        run_config=_config(),
        metric_roles=_common_metric_roles(),
        windows=_common_windows(),
        comparison_spec=ComparisonSpec(
            pairs=tuple(pairs),
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=INPUT_IDS,
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        artifact_writer=write_trajectory_ablation_artifacts,
    )


def _metric_index(
    rows: Sequence[MetricRow],
) -> dict[tuple[str, str, str, str], MetricRow]:
    return {
        (row.input_id, row.method_id, row.window_id, row.metric_id): row
        for row in rows
    }


def _available_value(row: MetricRow | None) -> float | None:
    if row is None or row.status != AVAILABLE or row.value is None:
        return None
    value = float(row.value)
    return value if np.isfinite(value) else None


def write_trajectory_ablation_artifacts(
    *,
    analysis_directory: Path,
    references: Mapping[str, Any],
    tracking_runs: Mapping[tuple[str, str], Any],
    trajectory_rows: Sequence[MetricRow],
    experiment_spec: ExperimentSpec,
    create_figures: bool,
) -> None:
    """Write per-input scientific acceptance rows and the RMSE-ratio figure."""

    del references, tracking_runs
    index = _metric_index(trajectory_rows)
    candidates = [
        method.method_id
        for method in experiment_spec.methods
        if method.method_id != BASELINE_METHOD_ID
    ]
    rows: list[dict[str, Any]] = []
    for input_id in INPUT_IDS:
        baseline = _available_value(
            index.get(
                (
                    input_id,
                    BASELINE_METHOD_ID,
                    "main_evaluation",
                    "position_rmse",
                )
            )
        )
        for method_id in candidates:
            candidate = _available_value(
                index.get(
                    (
                        input_id,
                        method_id,
                        "main_evaluation",
                        "position_rmse",
                    )
                )
            )
            ratio = (
                None
                if baseline is None or baseline <= 0.0 or candidate is None
                else candidate / baseline
            )
            guardrail_failures: list[str] = []
            for metric_id in _ACCEPTANCE_GUARDRAILS:
                baseline_guardrail = _available_value(
                    index.get(
                        (
                            input_id,
                            BASELINE_METHOD_ID,
                            "full_overlap",
                            metric_id,
                        )
                    )
                )
                candidate_guardrail = _available_value(
                    index.get(
                        (
                            input_id,
                            method_id,
                            "full_overlap",
                            metric_id,
                        )
                    )
                )
                if baseline_guardrail is None or candidate_guardrail is None:
                    guardrail_failures.append(f"{metric_id}:unavailable")
                elif candidate_guardrail > baseline_guardrail + 1e-12:
                    guardrail_failures.append(metric_id)
            rmse_pass = ratio is not None and ratio < 1.0
            guardrail_pass = not guardrail_failures
            rows.append(
                {
                    "input_id": input_id,
                    "method_id": method_id,
                    "baseline_method_id": BASELINE_METHOD_ID,
                    "window_id": "main_evaluation",
                    "baseline_position_rmse_rad": baseline,
                    "candidate_position_rmse_rad": candidate,
                    "rmse_ratio_vs_p": ratio,
                    "rmse_pass": rmse_pass,
                    "guardrail_pass": guardrail_pass,
                    "overall_pass": rmse_pass and guardrail_pass,
                    "guardrail_failures": ";".join(guardrail_failures),
                }
            )

    write_rows_csv(analysis_directory / "acceptance.csv", rows)
    _write_acceptance_summary(analysis_directory, rows, experiment_spec)
    if experiment_spec.experiment_id in {"E04", "E06"}:
        lag_rows = _lag_comparison_rows(trajectory_rows, experiment_spec)
        write_rows_csv(
            analysis_directory / "lag_comparison.csv",
            lag_rows,
        )
        if create_figures:
            _write_lag_comparison_figure(
                analysis_directory / "figures",
                lag_rows,
                experiment_spec,
            )
    if create_figures:
        _write_rmse_ratio_figure(analysis_directory / "figures", rows)


def _write_acceptance_summary(
    analysis_directory: Path,
    rows: Sequence[Mapping[str, Any]],
    spec: ExperimentSpec,
) -> None:
    overall = bool(rows) and all(bool(row["overall_pass"]) for row in rows)
    lines = [
        "## Scientific acceptance",
        "",
        f"- Overall: `{'pass' if overall else 'fail'}`",
        "- Criterion: every candidate has raw-time position RMSE below "
        "`p_kp1_baseline` on every input, with no guardrail regression.",
        "",
        "| input | method | RMSE ratio vs P | RMSE | guardrails | overall |",
        "|---|---|---:|---|---|---|",
    ]
    for row in rows:
        ratio = row["rmse_ratio_vs_p"]
        ratio_text = "" if ratio is None else f"{float(ratio):.6g}"
        lines.append(
            f"| {row['input_id']} | {row['method_id']} | {ratio_text} | "
            f"{'pass' if row['rmse_pass'] else 'fail'} | "
            f"{'pass' if row['guardrail_pass'] else 'fail'} | "
            f"{'pass' if row['overall_pass'] else 'fail'} |"
        )
    lines.extend(
        [
            "",
            f"Experiment: `{spec.experiment_id}`.",
            "",
        ]
    )
    if spec.experiment_id in {"E04", "E06"}:
        lines.extend(
            [
                "Lag diagnostics compare every finite-difference method with "
                "both `p_kp1_baseline` and the matching truth method in "
                "`lag_comparison.csv`; figures are written to "
                "`figures/lag_vs_p_and_truth.{png,svg}` when enabled.",
                "",
            ]
        )
    (analysis_directory / "acceptance_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _lag_comparison_rows(
    trajectory_rows: Sequence[MetricRow],
    spec: ExperimentSpec,
) -> list[dict[str, Any]]:
    index = _metric_index(trajectory_rows)
    truth_method_id = (
        "pva_truth_kp1" if spec.experiment_id == "E04" else "pv_truth_kp1"
    )
    rows: list[dict[str, Any]] = []
    for input_id in INPUT_IDS:
        baseline_lag_s = _available_value(
            index.get(
                (
                    input_id,
                    BASELINE_METHOD_ID,
                    "main_evaluation",
                    "lag_s",
                )
            )
        )
        truth_lag_s = _available_value(
            index.get(
                (
                    input_id,
                    truth_method_id,
                    "main_evaluation",
                    "lag_s",
                )
            )
        )
        for method in spec.methods:
            method_id = method.method_id
            lag_s = _available_value(
                index.get(
                    (
                        input_id,
                        method_id,
                        "main_evaluation",
                        "lag_s",
                    )
                )
            )
            if method_id == BASELINE_METHOD_ID:
                method_role = "p_only_baseline"
            elif method_id == truth_method_id:
                method_role = "truth_baseline"
            else:
                method_role = "finite_difference"
            rows.append(
                {
                    "input_id": input_id,
                    "method_id": method_id,
                    "method_role": method_role,
                    "window_id": "main_evaluation",
                    "lag_ms": None if lag_s is None else 1000.0 * lag_s,
                    "p_only_lag_ms": (
                        None
                        if baseline_lag_s is None
                        else 1000.0 * baseline_lag_s
                    ),
                    "truth_method_id": truth_method_id,
                    "truth_lag_ms": (
                        None if truth_lag_s is None else 1000.0 * truth_lag_s
                    ),
                    "lag_delta_vs_p_only_ms": (
                        None
                        if lag_s is None or baseline_lag_s is None
                        else 1000.0 * (lag_s - baseline_lag_s)
                    ),
                    "lag_delta_vs_truth_ms": (
                        None
                        if lag_s is None or truth_lag_s is None
                        else 1000.0 * (lag_s - truth_lag_s)
                    ),
                    "status": (
                        AVAILABLE
                        if lag_s is not None
                        and baseline_lag_s is not None
                        and truth_lag_s is not None
                        else "unavailable"
                    ),
                }
            )
    return rows


def _lag_method_label(method_id: str) -> str:
    if method_id == BASELINE_METHOD_ID:
        return "P-only baseline"
    if method_id.endswith("_truth_kp1"):
        return "truth k+1"
    suffixes = {
        "_est_backward_o1_k": "estimator backward O1",
        "_est_backward_o2_k": "estimator backward O2",
        "_est_centered_o2_km1": "estimator centered O2",
        "_pred_backward_o1_kp1": "predictor backward O1",
        "_pred_backward_o2_kp1": "predictor backward O2",
    }
    for suffix, label in suffixes.items():
        if method_id.endswith(suffix):
            return label
    return method_id


def _write_lag_comparison_figure(
    figures_directory: Path,
    rows: Sequence[Mapping[str, Any]],
    spec: ExperimentSpec,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    method_ids = [method.method_id for method in spec.methods]
    labels = [_lag_method_label(method_id) for method_id in method_ids]
    colors = []
    for method_id in method_ids:
        if method_id == BASELINE_METHOD_ID:
            colors.append("#B42318")
        elif method_id.endswith("_truth_kp1"):
            colors.append("#111827")
        elif "_est_" in method_id:
            colors.append("#2563EB")
        else:
            colors.append("#059669")

    figure, axes = plt.subplots(
        1,
        len(INPUT_IDS),
        figsize=(15.5, 6.0),
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    y_positions = np.arange(len(method_ids), dtype=float)
    all_values = [
        float(row["lag_ms"])
        for row in rows
        if row["lag_ms"] is not None
    ]
    maximum = max(all_values, default=0.0)
    x_margin = max(5.0, 0.15 * maximum)
    for axis, input_id in zip(axes, INPUT_IDS):
        by_method = {
            str(row["method_id"]): row
            for row in rows
            if row["input_id"] == input_id
        }
        values = [
            (
                np.nan
                if by_method[method_id]["lag_ms"] is None
                else float(by_method[method_id]["lag_ms"])
            )
            for method_id in method_ids
        ]
        axis.barh(y_positions, values, color=colors, alpha=0.86)
        for y_position, value in zip(y_positions, values):
            if np.isfinite(value):
                axis.text(
                    value + 0.7,
                    y_position,
                    f"{value:.0f}",
                    va="center",
                    ha="left",
                    fontsize=8,
                )
        axis.axvline(0.0, color="#667085", linewidth=0.8)
        axis.set_title(input_id.replace("_", "\n"))
        axis.set_xlabel("Diagnostic lag (ms)")
        axis.grid(axis="x", alpha=0.25)
        axis.set_xlim(min(-x_margin * 0.15, -1.0), maximum + x_margin)
    axes[0].set_yticks(y_positions, labels)
    axes[0].invert_yaxis()
    components = "PVA" if spec.experiment_id == "E04" else "PV"
    figure.suptitle(
        f"{spec.experiment_id}: lag vs P-only and {components} truth "
        "(main evaluation window)\n"
        "Positive lag means the command trails the reference; "
        "primary RMSE remains raw-time.",
        fontsize=12,
    )
    figures_directory.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        figures_directory / "lag_vs_p_and_truth.png",
        dpi=180,
        bbox_inches="tight",
        facecolor="white",
    )
    figure.savefig(
        figures_directory / "lag_vs_p_and_truth.svg",
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)


def _write_rmse_ratio_figure(
    figures_directory: Path,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    method_ids = sorted({str(row["method_id"]) for row in rows})
    input_ids = list(INPUT_IDS)
    y_positions = np.arange(len(method_ids), dtype=float)
    figure, axis = plt.subplots(
        figsize=(11.5, max(4.5, 0.55 * len(method_ids) + 2.0)),
        constrained_layout=True,
    )
    offsets = np.linspace(-0.22, 0.22, len(input_ids))
    for offset, input_id in zip(offsets, input_ids):
        ratios = []
        for method_id in method_ids:
            row = next(
                item
                for item in rows
                if item["input_id"] == input_id
                and item["method_id"] == method_id
            )
            value = row["rmse_ratio_vs_p"]
            ratios.append(np.nan if value is None else float(value))
        axis.scatter(
            ratios,
            y_positions + offset,
            s=42,
            label=input_id,
        )
    axis.axvline(1.0, color="#B42318", linestyle="--", linewidth=1.2)
    axis.set_yticks(y_positions, method_ids)
    axis.set_xlabel("Raw-time position RMSE ratio vs P[k+1] baseline")
    axis.set_title("RMSE ratio by method and trajectory (ratio < 1 improves)")
    axis.grid(axis="x", alpha=0.25)
    axis.legend(loc="best", fontsize=8)
    figures_directory.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        figures_directory / "rmse_ratio_vs_p.png",
        dpi=180,
        bbox_inches="tight",
        facecolor="white",
    )
    figure.savefig(
        figures_directory / "rmse_ratio_vs_p.svg",
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(figure)


__all__ = [
    "BASELINE_METHOD_ID",
    "CURRENT_ONLINE_BASELINE_CASE_ID",
    "E01_INPUT_IDS",
    "INPUT_IDS",
    "RECORDED_BASELINE_INPUT_IDS",
    "build_p_only_baseline",
    "build_state_target_methods",
    "build_trajectory_ablation",
    "write_trajectory_ablation_artifacts",
]
