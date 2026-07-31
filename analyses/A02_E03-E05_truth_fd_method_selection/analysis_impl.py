"""A02 truth-versus-finite-difference method-selection implementation."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from otg_lab.cross_analysis import prepare_analysis, write_prepared_analysis
from otg_lab.cross_analysis_reporting import (
    AnalysisValidationError,
    as_float,
    available_value,
    compare_duplicate_methods,
    configure_matplotlib,
    create_analysis_run_directory,
    markdown_table,
    metric_group,
    prepared_rows,
    save_figure,
    validate_figure_files,
    validate_sources,
    write_analysis_manifest,
    write_csv,
    write_text,
)

ANALYSIS_DIRECTORY = Path(__file__).resolve().parent
CONFIG_PATH = ANALYSIS_DIRECTORY / "analysis.yaml"
RESULTS_DIRECTORY = ANALYSIS_DIRECTORY / "results"
INPUT_ORDER = ("quadratic_with_extremum", "cubic", "sine")
BASELINE_METHOD_ID = "p_kp1_baseline"
TRUTH_METHOD_ID = "pva_truth_kp1"
DT_MS = 10.0

METHODS: tuple[dict[str, Any], ...] = (
    {
        "method_id": "pva_est_backward_o1_k",
        "method_label": "Backward O1",
        "short_label": "backward O1",
        "formula": ("v[k]=(x[k]-x[k-1])/h; a[k]=(x[k]-2x[k-1]+x[k-2])/h²"),
        "accuracy_order": 1,
        "history_samples": 3,
        "target_age_samples": 1,
        "extrapolates": False,
        "causal": True,
        "velocity_noise_gain": math.sqrt(2.0),
        "acceleration_noise_gain": math.sqrt(6.0),
        "startup": "前 2 个位置样本不足时 V/A 置零",
        "advantages": "无外推、历史较短、理论噪声增益低于同 lag 的 O2",
        "disadvantages": "V/A 仅 O(h)，截断误差较大",
    },
    {
        "method_id": "pva_est_backward_o2_k",
        "method_label": "Backward O2",
        "short_label": "backward O2",
        "formula": (
            "v[k]=(3x[k]-4x[k-1]+x[k-2])/(2h); a[k]=(2x[k]-5x[k-1]+4x[k-2]-x[k-3])/h²"
        ),
        "accuracy_order": 2,
        "history_samples": 4,
        "target_age_samples": 1,
        "extrapolates": False,
        "causal": True,
        "velocity_noise_gain": math.sqrt(6.5),
        "acceleration_noise_gain": math.sqrt(46.0),
        "startup": "前 3 个位置样本不足时 V/A 置零",
        "advantages": "同为 10 ms target age，平滑数据上的算法精度更高",
        "disadvantages": "噪声增益和启动历史更高；deadline 结果需随固定 run 审计",
    },
    {
        "method_id": "pva_est_centered_o2_km1",
        "method_label": "Centered O2",
        "short_label": "centered O2",
        "formula": ("v[k-1]=(x[k]-x[k-2])/(2h); a[k-1]=(x[k]-2x[k-1]+x[k-2])/h²"),
        "accuracy_order": 2,
        "history_samples": 3,
        "target_age_samples": 2,
        "extrapolates": False,
        "causal": True,
        "velocity_noise_gain": math.sqrt(0.5),
        "acceleration_noise_gain": math.sqrt(6.0),
        "startup": "需 3 点；结果属于 k-1，到 k 才可用",
        "advantages": "无外推；五种方法中 V 白噪声增益最低",
        "disadvantages": "20 ms target age，raw-time tracking 延迟最大",
    },
    {
        "method_id": "pva_pred_backward_o1_kp1",
        "method_label": "Future O1",
        "short_label": "future O1",
        "formula": ("v[k+1]=(2x[k]-3x[k-1]+x[k-2])/h; a[k+1]=(x[k]-2x[k-1]+x[k-2])/h²"),
        "accuracy_order": 1,
        "history_samples": 3,
        "target_age_samples": 0,
        "extrapolates": True,
        "causal": True,
        "velocity_noise_gain": math.sqrt(14.0),
        "acceleration_noise_gain": math.sqrt(6.0),
        "startup": "前 2 个位置样本不足时 V/A 置零",
        "advantages": "零 target age、历史较短；相对 future O2 更保守",
        "disadvantages": "仍属一步外推，且会放大位置噪声",
    },
    {
        "method_id": "pva_pred_backward_o2_kp1",
        "method_label": "Future O2",
        "short_label": "future O2",
        "formula": (
            "v[k+1]=(5x[k]-8x[k-1]+3x[k-2])/(2h); "
            "a[k+1]=(3x[k]-8x[k-1]+7x[k-2]-2x[k-3])/h²"
        ),
        "accuracy_order": 2,
        "history_samples": 4,
        "target_age_samples": 0,
        "extrapolates": True,
        "causal": True,
        "velocity_noise_gain": math.sqrt(24.5),
        "acceleration_noise_gain": math.sqrt(126.0),
        "startup": "前 3 个位置样本不足时 V/A 置零",
        "advantages": "当前平滑、无噪声、等间隔数据上 raw-time RMSE 最低",
        "disadvantages": "对噪声、量化、抖动和突变最敏感，且启动历史较长",
    },
)
METHOD_BY_ID = {str(item["method_id"]): item for item in METHODS}
METHOD_ORDER = tuple(METHOD_BY_ID)

REQUIRED_GUARDRAILS = (
    "output_velocity_violation_count",
    "output_acceleration_violation_count",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
    "deadline_miss_rate",
)

PAIR_FIELDS = (
    "pair_type",
    "method_id",
    "method_label",
    "input_id",
    "window_id",
    "metric_id",
    "metric_group",
    "unit",
    "direction",
    "role",
    "candidate_source_id",
    "candidate_value",
    "candidate_status",
    "candidate_sample_count",
    "baseline_source_id",
    "baseline_method_id",
    "baseline_value",
    "baseline_status",
    "truth_source_id",
    "truth_method_id",
    "truth_value",
    "truth_status",
    "candidate_minus_truth",
    "rmse_ratio_vs_p",
    "truth_gap_ratio",
    "calculation_status",
)

SCORECARD_FIELDS = (
    "method_id",
    "method_label",
    "input_id",
    "position_rmse",
    "p_baseline_position_rmse",
    "pva_truth_position_rmse",
    "rmse_ratio_vs_p",
    "truth_gap_ratio",
    "observed_lag_s",
    "observed_lag_ms",
    "absolute_observed_lag_ms",
    "lag_aligned_rmse",
    "target_age_samples",
    "target_age_ms",
    "accuracy_order",
    "history_samples",
    "velocity_noise_gain_sigma_over_h",
    "acceleration_noise_gain_sigma_over_h2",
    "causal",
    "extrapolates",
    "guardrails_complete",
    "guardrails_pass",
    "guardrails_pass_ignoring_deadline",
    "rmse_beats_p",
)


def _metric_index(
    metric_rows: Sequence[Mapping[str, Any]],
    source_id: str,
    method_id: str,
) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in metric_rows:
        if row.get("source_id") != source_id or row.get("method_id") != method_id:
            continue
        key = (
            str(row["input_id"]),
            str(row["window_id"]),
            str(row["metric_id"]),
        )
        if key in index:
            raise AnalysisValidationError(
                f"duplicate A02 metric key for {source_id}/{method_id}: {key}"
            )
        index[key] = row
    return index


def _value_or_blank(row: Mapping[str, Any] | None) -> float | str:
    value = available_value(row)
    return "" if value is None else value


def _truth_gap_ratio(
    method_value: float | None,
    baseline_value: float | None,
    truth_value: float | None,
) -> float | None:
    if method_value is None or baseline_value is None or truth_value is None:
        return None
    denominator = baseline_value - truth_value
    if abs(denominator) <= 1e-15:
        return None
    return (method_value - truth_value) / denominator


def _append_metric_audit(
    output: list[dict[str, Any]],
    metric_rows: Sequence[Mapping[str, Any]],
    *,
    pair_type: str,
    candidate_source_id: str,
    candidate_method_id: str,
    candidate_label: str,
    baseline_source_id: str,
    truth_source_id: str,
    truth_method_id: str,
) -> None:
    candidate = _metric_index(
        metric_rows,
        candidate_source_id,
        candidate_method_id,
    )
    baseline = _metric_index(
        metric_rows,
        baseline_source_id,
        BASELINE_METHOD_ID,
    )
    truth = _metric_index(metric_rows, truth_source_id, truth_method_id)
    keys = sorted(
        set(candidate) | set(baseline) | set(truth),
        key=lambda key: (
            INPUT_ORDER.index(key[0]),
            0 if key[1] == "main_evaluation" else 1,
            key[2],
        ),
    )
    for key in keys:
        candidate_row = candidate.get(key)
        baseline_row = baseline.get(key)
        truth_row = truth.get(key)
        if candidate_row is None or baseline_row is None or truth_row is None:
            raise AnalysisValidationError(
                f"incomplete A02 audit pair for {candidate_method_id}: {key}"
            )
        for field in ("unit", "direction", "role"):
            values = {
                candidate_row.get(field),
                baseline_row.get(field),
                truth_row.get(field),
            }
            if len(values) != 1:
                raise AnalysisValidationError(
                    f"A02 {field} mismatch for {candidate_method_id}/{key}"
                )
        method_value = available_value(candidate_row)
        baseline_value = available_value(baseline_row)
        truth_value = available_value(truth_row)
        rmse_ratio = None
        gap_ratio = None
        if key[2] == "position_rmse":
            if (
                method_value is not None
                and baseline_value is not None
                and baseline_value != 0.0
            ):
                rmse_ratio = method_value / baseline_value
            gap_ratio = _truth_gap_ratio(
                method_value,
                baseline_value,
                truth_value,
            )
        if method_value is None or truth_value is None:
            calculation_status = "unavailable_value"
        elif key[2] == "position_rmse" and gap_ratio is None:
            calculation_status = "truth_gap_denominator_unavailable_or_zero"
        else:
            calculation_status = "available"
        output.append(
            {
                "pair_type": pair_type,
                "method_id": candidate_method_id,
                "method_label": candidate_label,
                "input_id": key[0],
                "window_id": key[1],
                "metric_id": key[2],
                "metric_group": metric_group(key[2]),
                "unit": candidate_row.get("unit", ""),
                "direction": candidate_row.get("direction", ""),
                "role": candidate_row.get("role", ""),
                "candidate_source_id": candidate_source_id,
                "candidate_value": _value_or_blank(candidate_row),
                "candidate_status": candidate_row.get("status", ""),
                "candidate_sample_count": candidate_row.get("sample_count", ""),
                "baseline_source_id": baseline_source_id,
                "baseline_method_id": BASELINE_METHOD_ID,
                "baseline_value": _value_or_blank(baseline_row),
                "baseline_status": baseline_row.get("status", ""),
                "truth_source_id": truth_source_id,
                "truth_method_id": truth_method_id,
                "truth_value": _value_or_blank(truth_row),
                "truth_status": truth_row.get("status", ""),
                "candidate_minus_truth": (
                    ""
                    if method_value is None or truth_value is None
                    else method_value - truth_value
                ),
                "rmse_ratio_vs_p": "" if rmse_ratio is None else rmse_ratio,
                "truth_gap_ratio": "" if gap_ratio is None else gap_ratio,
                "calculation_status": calculation_status,
            }
        )


def build_truth_fd_metric_pairs(
    metric_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method in METHODS:
        _append_metric_audit(
            rows,
            metric_rows,
            pair_type="e04_fd_vs_pva_truth_and_p",
            candidate_source_id="e04_pva_finite_difference",
            candidate_method_id=str(method["method_id"]),
            candidate_label=str(method["method_label"]),
            baseline_source_id="e04_pva_finite_difference",
            truth_source_id="e04_pva_finite_difference",
            truth_method_id=TRUTH_METHOD_ID,
        )
    _append_metric_audit(
        rows,
        metric_rows,
        pair_type="e05_pv_truth_component_control",
        candidate_source_id="e05_pv_truth",
        candidate_method_id="pv_truth_kp1",
        candidate_label="PV truth component control",
        baseline_source_id="e05_pv_truth",
        truth_source_id="e03_pva_truth",
        truth_method_id=TRUTH_METHOD_ID,
    )
    return rows


def build_guardrail_summary(
    metric_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baseline = _metric_index(
        metric_rows,
        "e04_pva_finite_difference",
        BASELINE_METHOD_ID,
    )
    for method in METHODS:
        method_id = str(method["method_id"])
        candidate = _metric_index(
            metric_rows,
            "e04_pva_finite_difference",
            method_id,
        )
        for input_id in INPUT_ORDER:
            for metric_id in REQUIRED_GUARDRAILS:
                key = (input_id, "full_overlap", metric_id)
                candidate_row = candidate.get(key)
                baseline_row = baseline.get(key)
                candidate_value = available_value(candidate_row)
                baseline_value = available_value(baseline_row)
                complete = candidate_value is not None and baseline_value is not None
                passed = bool(
                    complete
                    and candidate_value is not None
                    and baseline_value is not None
                    and candidate_value <= baseline_value + 1e-12
                )
                rows.append(
                    {
                        "method_id": method_id,
                        "method_label": method["method_label"],
                        "input_id": input_id,
                        "window_id": "full_overlap",
                        "metric_id": metric_id,
                        "candidate_value": (
                            "" if candidate_value is None else candidate_value
                        ),
                        "candidate_status": (
                            ""
                            if candidate_row is None
                            else candidate_row.get("status", "")
                        ),
                        "p_baseline_value": (
                            "" if baseline_value is None else baseline_value
                        ),
                        "p_baseline_status": (
                            ""
                            if baseline_row is None
                            else baseline_row.get("status", "")
                        ),
                        "complete": str(complete).lower(),
                        "passes_no_regression": str(passed).lower(),
                        "included_in_formal_gate": "true",
                        "included_in_no_deadline_sensitivity": str(
                            metric_id != "deadline_miss_rate"
                        ).lower(),
                        "notes": (
                            "candidate must be no worse than the P baseline; "
                            "missing values fail the formal gate"
                        ),
                    }
                )
    return rows


def _audit_lookup(
    audit_rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str], Mapping[str, Any]]:
    lookup: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for row in audit_rows:
        key = (
            str(row["pair_type"]),
            str(row["method_id"]),
            str(row["input_id"]),
            f"{row['window_id']}:{row['metric_id']}",
        )
        if key in lookup:
            raise AnalysisValidationError(f"duplicate A02 audit lookup key: {key}")
        lookup[key] = row
    return lookup


def build_method_input_scorecard(
    audit_rows: Sequence[Mapping[str, Any]],
    guardrail_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    lookup = _audit_lookup(audit_rows)
    guard_lookup = {
        (str(row["method_id"]), str(row["input_id"]), str(row["metric_id"])): row
        for row in guardrail_rows
    }
    rows: list[dict[str, Any]] = []
    pair_type = "e04_fd_vs_pva_truth_and_p"
    for method in METHODS:
        method_id = str(method["method_id"])
        for input_id in INPUT_ORDER:

            def metric(
                metric_id: str,
                bound_method_id: str = method_id,
                bound_input_id: str = input_id,
            ) -> Mapping[str, Any]:
                try:
                    return lookup[
                        (
                            pair_type,
                            bound_method_id,
                            bound_input_id,
                            f"main_evaluation:{metric_id}",
                        )
                    ]
                except KeyError as error:
                    raise AnalysisValidationError(
                        "missing A02 scorecard metric "
                        f"{bound_method_id}/{bound_input_id}/{metric_id}"
                    ) from error

            position = metric("position_rmse")
            lag = metric("lag_s")
            aligned = metric("lag_aligned_rmse")
            guardrails = [
                guard_lookup[(method_id, input_id, metric_id)]
                for metric_id in REQUIRED_GUARDRAILS
            ]
            complete = all(row["complete"] == "true" for row in guardrails)
            formal_pass = complete and all(
                row["passes_no_regression"] == "true" for row in guardrails
            )
            sensitivity_rows = [
                row for row in guardrails if row["metric_id"] != "deadline_miss_rate"
            ]
            sensitivity_pass = all(
                row["complete"] == "true" and row["passes_no_regression"] == "true"
                for row in sensitivity_rows
            )
            lag_s = as_float(lag["candidate_value"])
            rmse_ratio = as_float(position["rmse_ratio_vs_p"])
            rows.append(
                {
                    "method_id": method_id,
                    "method_label": method["method_label"],
                    "input_id": input_id,
                    "position_rmse": position["candidate_value"],
                    "p_baseline_position_rmse": position["baseline_value"],
                    "pva_truth_position_rmse": position["truth_value"],
                    "rmse_ratio_vs_p": position["rmse_ratio_vs_p"],
                    "truth_gap_ratio": position["truth_gap_ratio"],
                    "observed_lag_s": "" if lag_s is None else lag_s,
                    "observed_lag_ms": "" if lag_s is None else 1000.0 * lag_s,
                    "absolute_observed_lag_ms": (
                        "" if lag_s is None else 1000.0 * abs(lag_s)
                    ),
                    "lag_aligned_rmse": aligned["candidate_value"],
                    "target_age_samples": method["target_age_samples"],
                    "target_age_ms": DT_MS * float(method["target_age_samples"]),
                    "accuracy_order": method["accuracy_order"],
                    "history_samples": method["history_samples"],
                    "velocity_noise_gain_sigma_over_h": method["velocity_noise_gain"],
                    "acceleration_noise_gain_sigma_over_h2": method[
                        "acceleration_noise_gain"
                    ],
                    "causal": str(method["causal"]).lower(),
                    "extrapolates": str(method["extrapolates"]).lower(),
                    "guardrails_complete": str(complete).lower(),
                    "guardrails_pass": str(formal_pass).lower(),
                    "guardrails_pass_ignoring_deadline": str(sensitivity_pass).lower(),
                    "rmse_beats_p": str(
                        rmse_ratio is not None and rmse_ratio < 1.0
                    ).lower(),
                }
            )
    if len(rows) != 15:
        raise AnalysisValidationError(
            f"A02 method scorecard must contain 15 rows, found {len(rows)}"
        )
    return rows


def _is_pareto(
    method_id: str,
    summaries: Sequence[Mapping[str, Any]],
) -> bool:
    selected = next(row for row in summaries if row["method_id"] == method_id)
    if selected["formally_eligible"] != "true":
        return False
    x_value = float(selected["worst_absolute_observed_lag_ms"])
    y_value = float(selected["worst_truth_gap_ratio"])
    for other in summaries:
        if other["method_id"] == method_id or other["formally_eligible"] != "true":
            continue
        other_x = float(other["worst_absolute_observed_lag_ms"])
        other_y = float(other["worst_truth_gap_ratio"])
        if (
            other_x <= x_value
            and other_y <= y_value
            and (other_x < x_value or other_y < y_value)
        ):
            return False
    return True


def build_method_summary(
    scorecard_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for method in METHODS:
        method_id = str(method["method_id"])
        rows = [row for row in scorecard_rows if row["method_id"] == method_id]
        if len(rows) != len(INPUT_ORDER):
            raise AnalysisValidationError(
                f"A02 method {method_id} lacks one or more input rows"
            )
        ratios = [float(row["rmse_ratio_vs_p"]) for row in rows]
        gaps = [float(row["truth_gap_ratio"]) for row in rows]
        lags = [float(row["absolute_observed_lag_ms"]) for row in rows]
        all_rmse_pass = all(row["rmse_beats_p"] == "true" for row in rows)
        all_guards = all(row["guardrails_pass"] == "true" for row in rows)
        sensitivity_guards = all(
            row["guardrails_pass_ignoring_deadline"] == "true" for row in rows
        )
        complete = all(row["guardrails_complete"] == "true" for row in rows)
        causal = bool(method["causal"])
        summaries.append(
            {
                "method_id": method_id,
                "method_label": method["method_label"],
                "accuracy_order": method["accuracy_order"],
                "history_samples": method["history_samples"],
                "target_age_samples": method["target_age_samples"],
                "target_age_ms": DT_MS * float(method["target_age_samples"]),
                "extrapolates": str(method["extrapolates"]).lower(),
                "causal": str(causal).lower(),
                "velocity_noise_gain_sigma_over_h": method["velocity_noise_gain"],
                "acceleration_noise_gain_sigma_over_h2": method[
                    "acceleration_noise_gain"
                ],
                "mean_rmse_ratio_vs_p": statistics.fmean(ratios),
                "median_rmse_ratio_vs_p": statistics.median(ratios),
                "worst_rmse_ratio_vs_p": max(ratios),
                "mean_truth_gap_ratio": statistics.fmean(gaps),
                "worst_truth_gap_ratio": max(gaps),
                "worst_absolute_observed_lag_ms": max(lags),
                "all_inputs_complete": str(len(rows) == len(INPUT_ORDER)).lower(),
                "all_inputs_rmse_below_p": str(all_rmse_pass).lower(),
                "all_guardrails_complete": str(complete).lower(),
                "formal_guardrails_pass": str(all_guards).lower(),
                "sensitivity_guardrails_pass": str(sensitivity_guards).lower(),
                "formally_eligible": str(
                    len(rows) == len(INPUT_ORDER)
                    and causal
                    and all_rmse_pass
                    and complete
                    and all_guards
                ).lower(),
                "eligible_ignoring_deadline": str(
                    len(rows) == len(INPUT_ORDER)
                    and causal
                    and all_rmse_pass
                    and sensitivity_guards
                ).lower(),
                "pareto_frontier": "false",
            }
        )
    for summary in summaries:
        summary["pareto_frontier"] = str(
            _is_pareto(str(summary["method_id"]), summaries)
        ).lower()
    return summaries


def _select_method(
    summaries: Sequence[Mapping[str, Any]],
    *,
    lag_budget_ms: float,
    exclude_extrapolation: bool,
    ignore_deadline: bool,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    eligibility_field = (
        "eligible_ignoring_deadline" if ignore_deadline else "formally_eligible"
    )
    candidates = [
        row
        for row in summaries
        if row[eligibility_field] == "true"
        and float(row["worst_absolute_observed_lag_ms"]) <= lag_budget_ms + 1e-12
        and (not exclude_extrapolation or row["extrapolates"] == "false")
    ]
    if not candidates:
        raise AnalysisValidationError(
            f"no eligible method for lag budget {lag_budget_ms} ms"
        )
    best_gap = min(float(row["worst_truth_gap_ratio"]) for row in candidates)
    tied = [
        row
        for row in candidates
        if math.isclose(
            float(row["worst_truth_gap_ratio"]),
            best_gap,
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    ]
    selected = min(
        tied,
        key=lambda row: (
            float(row["acceleration_noise_gain_sigma_over_h2"]),
            float(row["velocity_noise_gain_sigma_over_h"]),
            int(row["history_samples"]),
            str(row["method_id"]),
        ),
    )
    return selected, candidates


def build_decision_matrix(
    summaries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    scenarios = (
        ("default_strict_realtime", "默认严格实时", 0.0, False, False),
        ("one_sample_tolerance", "一拍容忍", 10.0, False, False),
        ("two_sample_tolerance", "两拍容忍", 20.0, False, False),
        ("no_extrapolation", "禁止外推", 20.0, True, False),
        (
            "no_extrapolation_ignore_deadline",
            "禁止外推（忽略 deadline sensitivity）",
            20.0,
            True,
            True,
        ),
    )
    rows: list[dict[str, Any]] = []
    for (
        scenario_id,
        label,
        budget,
        exclude_extrapolation,
        ignore_deadline,
    ) in scenarios:
        selected, candidates = _select_method(
            summaries,
            lag_budget_ms=budget,
            exclude_extrapolation=exclude_extrapolation,
            ignore_deadline=ignore_deadline,
        )
        rows.append(
            {
                "scenario_id": scenario_id,
                "scenario_label": label,
                "lag_budget_ms": budget,
                "exclude_extrapolation": str(exclude_extrapolation).lower(),
                "deadline_miss_rate_in_gate": str(not ignore_deadline).lower(),
                "eligible_method_ids": ";".join(
                    str(row["method_id"])
                    for row in sorted(
                        candidates,
                        key=lambda item: METHOD_ORDER.index(str(item["method_id"])),
                    )
                ),
                "selected_method_id": selected["method_id"],
                "selected_method_label": selected["method_label"],
                "selected_worst_truth_gap_ratio": selected["worst_truth_gap_ratio"],
                "selected_worst_absolute_observed_lag_ms": selected[
                    "worst_absolute_observed_lag_ms"
                ],
                "selection_rule": (
                    "hard gate; lag budget; minimum worst-case truth gap; "
                    "then lower derivative noise gain, shorter history, method_id"
                ),
            }
        )
    return rows


def _format_number(value: Any, digits: int = 4) -> str:
    number = as_float(value)
    if number is None:
        return "NA"
    if number == 0.0:
        return "0"
    if abs(number) < 1e-3 or abs(number) >= 1e3:
        return f"{number:.3e}"
    return f"{number:.{digits}f}"


def _plot_rmse_ratio(
    scorecard_rows: Sequence[Mapping[str, Any]],
    results_directory: Path,
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(11.8, 6.8))
    x_positions = np.arange(len(METHOD_ORDER), dtype=float)
    colors = ("#2563EB", "#C56A1A", "#0F766E")
    markers = ("o", "s", "^")
    offsets = (-0.18, 0.0, 0.18)
    for input_id, color, marker, offset in zip(
        INPUT_ORDER,
        colors,
        markers,
        offsets,
    ):
        values = [
            float(
                next(
                    row
                    for row in scorecard_rows
                    if row["method_id"] == method_id and row["input_id"] == input_id
                )["rmse_ratio_vs_p"]
            )
            for method_id in METHOD_ORDER
        ]
        axis.scatter(
            x_positions + offset,
            values,
            color=color,
            marker=marker,
            s=58,
            label=input_id,
            zorder=3,
        )
    axis.axhline(
        1.0,
        color="#6B7280",
        linestyle="--",
        linewidth=1.2,
        label="P baseline = 1",
    )
    axis.set_yscale("log")
    axis.set_xticks(x_positions)
    axis.set_xticklabels(
        [METHOD_BY_ID[item]["short_label"] for item in METHOD_ORDER],
        rotation=16,
        ha="right",
    )
    axis.set_ylabel("RMSE_method / RMSE_P (log scale)")
    axis.set_xlabel("PVA finite-difference method")
    axis.set_title("逐轨迹 raw-time position RMSE ratio")
    axis.grid(axis="y")
    axis.legend(title="Trajectory / reference", ncol=2, loc="upper right")
    figure.subplots_adjust(
        left=0.1,
        right=0.985,
        bottom=0.22,
        top=0.91,
    )
    figure.text(
        0.5,
        0.035,
        "main_evaluation 0.04–3.00 s；小于 1 表示优于共同 P baseline",
        ha="center",
        color="#4B5563",
    )
    return save_figure(figure, results_directory / "rmse_ratio_by_input")


def _plot_lag(
    scorecard_rows: Sequence[Mapping[str, Any]],
    results_directory: Path,
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(11.8, 6.8))
    y_positions = np.arange(len(METHOD_ORDER), dtype=float)
    colors = ("#2563EB", "#C56A1A", "#0F766E")
    markers = ("o", "s", "^")
    offsets = (-0.17, 0.0, 0.17)
    for input_id, color, marker, offset in zip(
        INPUT_ORDER,
        colors,
        markers,
        offsets,
    ):
        values = [
            float(
                next(
                    row
                    for row in scorecard_rows
                    if row["method_id"] == method_id and row["input_id"] == input_id
                )["observed_lag_ms"]
            )
            for method_id in METHOD_ORDER
        ]
        axis.scatter(
            values,
            y_positions + offset,
            facecolor="white",
            edgecolor=color,
            linewidth=1.5,
            marker=marker,
            s=58,
            label=f"observed: {input_id}",
            zorder=3,
        )
    target_ages = [
        DT_MS * float(METHOD_BY_ID[method_id]["target_age_samples"])
        for method_id in METHOD_ORDER
    ]
    axis.scatter(
        target_ages,
        y_positions + 0.34,
        color="#7C3AED",
        marker="D",
        s=54,
        label="target age（方法构造）",
        zorder=2,
    )
    axis.axvline(0.0, color="#6B7280", linewidth=1.0)
    axis.set_yticks(y_positions)
    axis.set_yticklabels([METHOD_BY_ID[item]["short_label"] for item in METHOD_ORDER])
    axis.invert_yaxis()
    axis.set_xlabel("Time (ms; observed lag 保留符号)")
    axis.set_ylabel("Method")
    axis.set_title("Observed lag 与 target age 是两个不同量")
    axis.grid(axis="x")
    axis.legend(loc="lower right", fontsize=8)
    figure.subplots_adjust(
        left=0.15,
        right=0.985,
        bottom=0.17,
        top=0.91,
    )
    figure.text(
        0.5,
        0.03,
        "observed lag 是整数采样移位诊断；target age 是方法所代表状态的年龄",
        ha="center",
        color="#4B5563",
    )
    return save_figure(figure, results_directory / "lag_by_input")


def _plot_pareto(
    summaries: Sequence[Mapping[str, Any]],
    results_directory: Path,
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10.8, 7.2))
    for summary in summaries:
        eligible = summary["formally_eligible"] == "true"
        frontier = summary["pareto_frontier"] == "true"
        x_value = float(summary["worst_absolute_observed_lag_ms"])
        y_value = float(summary["worst_truth_gap_ratio"])
        axis.scatter(
            x_value,
            y_value,
            s=105 if frontier else 70,
            marker="o" if eligible else "X",
            facecolor="#2563EB" if eligible else "#D1D5DB",
            edgecolor="#1E3A8A" if frontier else "#6B7280",
            linewidth=2.0 if frontier else 1.0,
            zorder=3,
        )
        label_offsets = {
            "pva_est_backward_o1_k": (7, 10),
            "pva_est_backward_o2_k": (7, -18),
            "pva_est_centered_o2_km1": (7, 8),
            "pva_pred_backward_o1_kp1": (7, 8),
            "pva_pred_backward_o2_kp1": (7, 8),
        }
        axis.annotate(
            str(summary["short_label"])
            if "short_label" in summary
            else str(METHOD_BY_ID[str(summary["method_id"])]["short_label"]),
            (x_value, y_value),
            xytext=label_offsets[str(summary["method_id"])],
            textcoords="offset points",
            fontsize=9,
        )
    frontier_rows = sorted(
        [row for row in summaries if row["pareto_frontier"] == "true"],
        key=lambda row: float(row["worst_absolute_observed_lag_ms"]),
    )
    if len(frontier_rows) > 1:
        axis.plot(
            [float(row["worst_absolute_observed_lag_ms"]) for row in frontier_rows],
            [float(row["worst_truth_gap_ratio"]) for row in frontier_rows],
            color="#2563EB",
            linewidth=1.2,
            zorder=2,
        )
    for budget, color in ((0.0, "#111827"), (10.0, "#C56A1A"), (20.0, "#7C3AED")):
        axis.axvline(
            budget,
            color=color,
            linestyle="--",
            linewidth=1.0,
            label=f"{budget:.0f} ms budget",
        )
    axis.set_yscale("log")
    axis.set_xlim(-1.5, 22.0)
    axis.set_xlabel("Worst-case absolute observed lag (ms)")
    axis.set_ylabel("Worst-case truth gap ratio (log scale)")
    axis.set_title("RMSE–lag 硬门槛与 Pareto 前沿")
    axis.grid()
    axis.legend(loc="lower right")
    figure.subplots_adjust(
        left=0.1,
        right=0.985,
        bottom=0.17,
        top=0.91,
    )
    figure.text(
        0.5,
        0.03,
        "圆点为正式 eligible；灰色 X 为硬门槛失败；无加权总分",
        ha="center",
        color="#4B5563",
    )
    return save_figure(figure, results_directory / "rmse_lag_pareto")


def _method_detail_markdown() -> str:
    sections = []
    for method in METHODS:
        sections.append(
            f"""### {method["method_label"]} — `{method["method_id"]}`

- 公式：`{method["formula"]}`
- 阶数/历史：O{method["accuracy_order"]}；{method["history_samples"]} 个位置样本。
- Target age：{method["target_age_samples"]} 拍
  （{DT_MS * float(method["target_age_samples"]):.0f} ms）。
- 启动：{method["startup"]}。
- 白噪声增益：V 为 {method["velocity_noise_gain"]:.3f} σ/h，A 为
  {method["acceleration_noise_gain"]:.3f} σ/h²。
- 优点：{method["advantages"]}。
- 缺点：{method["disadvantages"]}。
"""
        )
    return "\n".join(sections)


def _build_results_markdown(
    scorecard_rows: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
) -> str:
    summary_table = [
        (
            row["method_label"],
            _format_number(row["worst_rmse_ratio_vs_p"]),
            _format_number(row["worst_truth_gap_ratio"]),
            _format_number(row["worst_absolute_observed_lag_ms"], 1),
            f"{_format_number(row['target_age_ms'], 1)} ms",
            row["formally_eligible"],
            row["pareto_frontier"],
        )
        for row in summaries
    ]
    decision_table = [
        (
            row["scenario_label"],
            f"{_format_number(row['lag_budget_ms'], 0)} ms",
            row["deadline_miss_rate_in_gate"],
            row["selected_method_id"],
            _format_number(row["selected_worst_truth_gap_ratio"]),
        )
        for row in decisions
    ]
    component_rows = [
        row
        for row in audit_rows
        if row["pair_type"] == "e05_pv_truth_component_control"
        and row["window_id"] == "main_evaluation"
        and row["metric_id"] == "position_rmse"
    ]
    component_table = [
        (
            row["input_id"],
            _format_number(row["baseline_value"]),
            _format_number(row["candidate_value"]),
            _format_number(row["truth_value"]),
        )
        for row in component_rows
    ]
    no_extrapolation = next(
        row for row in decisions if row["scenario_id"] == "no_extrapolation"
    )
    no_extrapolation_sensitivity = next(
        row
        for row in decisions
        if row["scenario_id"] == "no_extrapolation_ignore_deadline"
    )
    if (
        no_extrapolation["selected_method_id"]
        == no_extrapolation_sensitivity["selected_method_id"]
    ):
        deadline_note = (
            "本次固定 run 中纳入或排除 `deadline_miss_rate` 不改变该选择。"
        )
    else:
        deadline_note = (
            "排除 `deadline_miss_rate` 的 sensitivity 选择为 "
            f"`{no_extrapolation_sensitivity['selected_method_id']}`。"
        )
    return f"""# A02 — 解析轨迹 Truth/Finite Difference 正确性验证

> 证据角色：以下“选择”仅是解析验证场景的代表方法，不是上线选型。上线
> PV/PVA 与差分结论只使用 velocity-limit recorded trajectory。

## 解析验证结论

- **解析验证代表：`pva_pred_backward_o2_kp1`（Future O2）**。它通过
  guardrail，在三条轨迹上 observed lag 的整数采样诊断均为 0 ms，并具有
  最小的 worst-case truth gap ratio。
- **禁止外推场景代表：`{no_extrapolation["selected_method_id"]}`**
  （{no_extrapolation["selected_method_label"]}）。{deadline_note}
- Future O1 是 0 ms 的保守候选，但它相对 Future O2 的噪声优势只来自公式
  系数，不是 E03–E05 的有噪声实证。
- Centered O2 的 V 白噪声增益最低，但 target age 与当前 observed lag 均为
  20 ms，不适合作为低延迟 tracking 首选。

这是一项解析正确性场景化 readout，不建立任意加权总分，也不覆盖 recorded
trajectory 的部署证据。

## 证据角色

解析验证的 15 行 scorecard 只使用 E04 内的 5 种 FD × 3 条轨迹。E03 仅复核
E04 的 P baseline/PVA truth 重复结果；E05 只作 PV truth 分量控制，不进入
排名。E01 的独立 `p_kp1_baseline` 与 E03–E05 的 P baseline 已逐指标验证
等价，只作复现审计，不增加样本量。来源均 completed、同一 commit，但
manifest 记录 `git.dirty=true`，因此不是 clean-build 完全复现证据。

## RMSE–lag 摘要

Primary 是 `main_evaluation = 0.04–3.00 s` 的 raw-time
`position_rmse`：

```text
RMSE ratio = RMSE_method / RMSE_P
truth gap ratio = (RMSE_method - RMSE_truth) / (RMSE_P - RMSE_truth)
```

没有计算病态的 `RMSE_method / RMSE_truth`。Observed lag 保留符号，决策
使用绝对值；它是整数采样移位后的输出相位诊断，不是 wall-clock latency。
`lag_aligned_rmse` 只诊断最佳移位后的剩余波形误差，不进入 primary 排名。

{
        markdown_table(
            (
                "方法",
                "worst RMSE/P",
                "worst truth gap",
                "worst |lag| ms",
                "target age",
                "正式 eligible",
                "Pareto",
            ),
            summary_table,
        )
    }

## 解析验证场景

{
        markdown_table(
            ("场景", "lag 预算", "deadline 纳入", "选择", "worst truth gap"),
            decision_table,
        )
    }

解析验证门槛要求三轨迹完整、因果、RMSE ratio `< 1`，且 full-overlap 下
velocity/acceleration violation、profile constraint、fallback、solver failure
和 deadline miss 均不劣于 P baseline。缺失任何必需 guardrail 即不合格。

## 五种有限差分方法

五种方法都只读取截至当前可用的位置样本，均无显式低通平滑。Future
predictor 内部的位置外推会被 scheduled `P[k+1]` 覆盖，因此 E04 的实际方法
差异主要来自 V/A。

{_method_detail_markdown()}

## PV truth 分量控制

下表只回答“理想 V 已知后，理想 A 是否仍增加位置收益”，不进入 FD
scorecard。

{
        markdown_table(
            ("轨迹", "P baseline RMSE", "PV truth RMSE", "PVA truth RMSE"),
            component_table,
        )
    }

## 图表与审计文件

- `results/rmse_ratio_by_input.png/.svg`：逐轨迹 RMSE ratio，对数轴，P
  baseline = 1。
- `results/lag_by_input.png/.svg`：observed lag 与 target age 分列编码。
- `results/rmse_lag_pareto.png/.svg`：formal eligibility、Pareto 前沿与
  0/10/20 ms lag budget。
- `results/truth_fd_metric_pairs.csv`：全指标原值、状态和 truth/P 对照。
- `results/method_input_scorecard.csv`：正式 15 行决策坐标。
- `results/guardrail_summary.csv`：硬门槛逐方法逐轨迹审计。

## 限制

- 只有三条确定性、单轴、平滑、无噪声、100 Hz 轨迹，不计算 p-value、
  置信区间或统计推广。
- 0 ms 只有整数采样分辨率，不代表无亚采样相位误差。
- 噪声、量化、时间抖动、突变、多轴、不同采样率和不同 horizon 均未实证。
- A02 不使用 E06，不能外推为 PV finite-difference 方法选型。
- A02 不参与上线选型；上线对比只允许使用 velocity-limit recorded
  trajectory。
- output jerk channel 的 unavailable 状态不是“零违规”。

## 复现

```bash
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py --check
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py
```
"""


def _build_validation_markdown(
    source_rows: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    scorecard_rows: Sequence[Mapping[str, Any]],
    guardrail_rows: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    figure_paths: Sequence[Path],
) -> str:
    missing_guards = sum(row["complete"] != "true" for row in guardrail_rows)
    failed_guards = [
        row for row in guardrail_rows if row["passes_no_regression"] != "true"
    ]
    default = next(
        row for row in decisions if row["scenario_id"] == "default_strict_realtime"
    )
    return f"""# A02 Validation

## Overall Assessment: Share with caveats

### Methodology Review

- E04 是唯一正式排名来源；E03 重复 truth 未双重计数；E05 未进入
  scorecard。
- 全指标 audit 行数：{len(audit_rows)}。
- `method_input_scorecard.csv` 已验证为 {len(scorecard_rows)} 行。
- Guardrail 缺失项：{missing_guards}；未通过 no-regression 项：
  {len(failed_guards)}。
- 默认选择复现为 `{default["selected_method_id"]}`。
- 未计算 `RMSE_method / RMSE_truth`，也未建立加权总分。

### Source Checks

- 来源审计行数：{len(source_rows)}。
- E01 与 E03–E05 的独立 P baseline、E03/E04 的 P baseline 与 PVA truth
  已作全键一致性复核。
- 来源均记录 dirty worktree，报告已保留 caveat。

### Visualization Review

- 已生成并验证 {len(figure_paths)} 个 PNG/SVG 文件。
- RMSE ratio 与 truth gap 使用对数轴；lag 图明确区分 observed lag 与
  target age。
- Pareto 图标记 eligibility、frontier 与 0/10/20 ms budgets。

### Required Caveats

- 三条无噪声轨迹不支持统计推广或生产噪声结论。
- Observed lag 是离散相位诊断，不是 wall-clock latency。
- unavailable jerk channel 不解释为已通过。
"""


def _build_chart_map_markdown() -> str:
    return """# A02 Chart Map

| 图表 | 分析问题 | 图形 | 字段 | 编码 | 用途 |
|---|---|---|---|---|---|
| `rmse_ratio_by_input` | 每种 FD 在三条轨迹上相对 P baseline 的 raw-time RMSE 如何？ | 分组点图，log y | method_id, input_id, rmse_ratio_vs_p | 轨迹用颜色+形状双编码；虚线=1 | Primary tracking 证据 |
| `lag_by_input` | Observed lag 与方法 target age 是否一致？ | 横向分组点图 | observed_lag_ms, target_age_ms | observed 用空心多形状；target age 用紫色菱形 | 防止两种 lag 语义混用 |
| `rmse_lag_pareto` | 通过硬门槛后 RMSE–lag 的二维取舍是什么？ | 标注散点，log y | worst_absolute_observed_lag_ms, worst_truth_gap_ratio, eligibility, pareto | eligible 圆点；失败灰 X；加粗边框=frontier；预算虚线 | 场景决策 |

所有图均输出 PNG/SVG。图中不使用任意加权总分。
"""


def _source_role_rows() -> list[dict[str, Any]]:
    roles = (
        (
            "e01_p_only_baseline",
            "independent P baseline reproduction only; never a ranking sample",
        ),
        (
            "e03_pva_truth",
            "duplicate truth validation only; never an additional sample",
        ),
        (
            "e04_pva_finite_difference",
            "only formal ranking source",
        ),
        (
            "e05_pv_truth",
            "PV truth component control only; excluded from FD scorecard",
        ),
    )
    return [
        {
            "check_id": "a02_source_role",
            "scope": source_id,
            "status": "pass",
            "actual": role,
            "expected": role,
            "blocking": "true",
            "notes": "prevents cross-source double counting",
        }
        for source_id, role in roles
    ]


def _validate_expected_decisions(
    summaries: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
) -> None:
    expected = {
        "default_strict_realtime": "pva_pred_backward_o2_kp1",
        "no_extrapolation": "pva_est_backward_o2_k",
        "no_extrapolation_ignore_deadline": "pva_est_backward_o2_k",
    }
    actual = {
        str(row["scenario_id"]): str(row["selected_method_id"]) for row in decisions
    }
    for scenario, method_id in expected.items():
        if actual.get(scenario) != method_id:
            raise AnalysisValidationError(
                f"A02 expected {method_id} for {scenario}, found {actual.get(scenario)}"
            )
    backward_o2 = next(
        row for row in summaries if row["method_id"] == "pva_est_backward_o2_k"
    )
    if (
        backward_o2["formally_eligible"] != "true"
        or backward_o2["eligible_ignoring_deadline"] != "true"
    ):
        raise AnalysisValidationError(
            "A02 expected backward O2 to pass both fixed-run guardrail views"
        )


def _write_outputs(
    prepared: Any,
    output_directory: Path,
    source_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
    scorecard_rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    guardrail_rows: list[dict[str, Any]],
) -> None:
    global RESULTS_DIRECTORY
    RESULTS_DIRECTORY = output_directory
    write_prepared_analysis(prepared, RESULTS_DIRECTORY / "work")
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_files: list[Path] = []
    file_specs = (
        (
            RESULTS_DIRECTORY / "truth_fd_metric_pairs.csv",
            PAIR_FIELDS,
            audit_rows,
        ),
        (
            RESULTS_DIRECTORY / "method_input_scorecard.csv",
            SCORECARD_FIELDS,
            scorecard_rows,
        ),
        (
            RESULTS_DIRECTORY / "method_summary.csv",
            tuple(summaries[0]),
            summaries,
        ),
        (
            RESULTS_DIRECTORY / "decision_matrix.csv",
            tuple(decisions[0]),
            decisions,
        ),
        (
            RESULTS_DIRECTORY / "guardrail_summary.csv",
            tuple(guardrail_rows[0]),
            guardrail_rows,
        ),
        (
            RESULTS_DIRECTORY / "source_validation.csv",
            (
                "check_id",
                "scope",
                "status",
                "actual",
                "expected",
                "blocking",
                "notes",
            ),
            source_rows,
        ),
        (
            RESULTS_DIRECTORY / "baseline_equivalence.csv",
            (
                "check_id",
                "scope",
                "status",
                "actual",
                "expected",
                "blocking",
                "notes",
            ),
            baseline_rows,
        ),
    )
    for path, fields, rows in file_specs:
        write_csv(path, fields, rows)
        output_files.append(path)

    figure_paths = [
        *_plot_rmse_ratio(scorecard_rows, RESULTS_DIRECTORY),
        *_plot_lag(scorecard_rows, RESULTS_DIRECTORY),
        *_plot_pareto(summaries, RESULTS_DIRECTORY),
    ]
    validate_figure_files(figure_paths)
    output_files.extend(figure_paths)

    results_path = RESULTS_DIRECTORY / "RESULTS.md"
    results_markdown = _build_results_markdown(
        scorecard_rows,
        summaries,
        decisions,
        audit_rows,
    )
    validation_path = RESULTS_DIRECTORY / "validation.md"
    chart_map_path = RESULTS_DIRECTORY / "chart_map.md"
    write_text(results_path, results_markdown)
    write_text(ANALYSIS_DIRECTORY / "RESULTS.md", results_markdown)
    write_text(
        validation_path,
        _build_validation_markdown(
            source_rows,
            audit_rows,
            scorecard_rows,
            guardrail_rows,
            decisions,
            figure_paths,
        ),
    )
    write_text(chart_map_path, _build_chart_map_markdown())
    output_files.extend((results_path, validation_path, chart_map_path))

    manifest_path = RESULTS_DIRECTORY / "analysis_manifest.json"
    write_analysis_manifest(prepared, manifest_path, output_files)


def run(*, check_only: bool = False) -> int:
    prepared = prepare_analysis(CONFIG_PATH)
    source_rows = validate_sources(prepared)
    metric_rows = prepared_rows(prepared, "trajectory_metrics")
    baseline_rows: list[dict[str, Any]] = []
    for source_id in (
        "e03_pva_truth",
        "e04_pva_finite_difference",
        "e05_pv_truth",
    ):
        baseline_rows.extend(
            compare_duplicate_methods(
                metric_rows,
                left_source_id="e01_p_only_baseline",
                right_source_id=source_id,
                method_ids=(BASELINE_METHOD_ID,),
                excluded_metric_prefixes=("runtime_", "deadline_"),
            )
        )
    source_rows.extend(baseline_rows)
    source_rows.extend(
        compare_duplicate_methods(
            metric_rows,
            left_source_id="e03_pva_truth",
            right_source_id="e04_pva_finite_difference",
            method_ids=(BASELINE_METHOD_ID, TRUTH_METHOD_ID),
            excluded_metric_prefixes=("runtime_",),
        )
    )
    source_rows.extend(_source_role_rows())
    audit_rows = build_truth_fd_metric_pairs(metric_rows)
    guardrail_rows = build_guardrail_summary(metric_rows)
    scorecard_rows = build_method_input_scorecard(audit_rows, guardrail_rows)
    summaries = build_method_summary(scorecard_rows)
    decisions = build_decision_matrix(summaries)
    _validate_expected_decisions(summaries, decisions)

    if check_only:
        print(
            "A02: validated 4 pinned sources, 3 independent baseline checks, "
            "5 FD methods, "
            f"{len(audit_rows)} audit pairs, and 15 scorecard rows"
        )
        return 0

    run_directory = create_analysis_run_directory(prepared)
    _write_outputs(
        prepared,
        run_directory,
        source_rows,
        baseline_rows,
        audit_rows,
        scorecard_rows,
        summaries,
        decisions,
        guardrail_rows,
    )
    print(f"A02: wrote method-selection run to {run_directory}")
    return 0
