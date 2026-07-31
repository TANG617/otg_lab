"""A04 recorded-task PV/PVA and finite-difference method selection."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from otg_lab.analysis import _lag_metrics
from otg_lab.cross_analysis import prepare_analysis, write_prepared_analysis
from otg_lab.cross_analysis_reporting import (
    AnalysisValidationError,
    as_float,
    configure_matplotlib,
    create_analysis_run_directory,
    markdown_table,
    prepared_rows,
    save_figure,
    stable_json,
    validate_figure_files,
    write_analysis_manifest,
    write_csv,
    write_text,
)
from otg_lab.csvio import load_trajectory_csv

ANALYSIS_DIRECTORY = Path(__file__).resolve().parent
CONFIG_PATH = ANALYSIS_DIRECTORY / "analysis.yaml"
RESULTS_DIRECTORY = ANALYSIS_DIRECTORY / "results"
INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
BASELINE_METHOD_ID = "p_kp1_baseline"
STENCILS = (
    "est_backward_o1_k",
    "est_backward_o2_k",
    "est_centered_o2_km1",
    "pred_backward_o1_kp1",
    "pred_backward_o2_kp1",
)
STENCIL_LABELS = {
    "est_backward_o1_k": "Backward O1",
    "est_backward_o2_k": "Backward O2",
    "est_centered_o2_km1": "Centered O2",
    "pred_backward_o1_kp1": "Future O1",
    "pred_backward_o2_kp1": "Future O2",
}
CORE_GUARDRAILS = (
    "output_velocity_violation_count",
    "output_acceleration_violation_count",
    "profile_velocity_violation_count",
    "profile_acceleration_violation_count",
    "profile_jerk_violation_count",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
)
LAG_BUDGETS_MS = (10.0, 20.0)


def _validate_a04_sources(prepared: Any) -> list[dict[str, Any]]:
    """Validate the intentional E11/E12 overlap without requiring equal matrices."""

    sources = {source.source_id: source for source in prepared.sources}
    e11 = sources["e11_pv_recorded"]
    e12 = sources["e12_pva_vmax_ablation"]
    e11_git = e11.manifest["git"]
    e12_git = e12.manifest["git"]
    e11_input = e11.manifest["inputs"][INPUT_ID]
    e12_input = e12.manifest["inputs"][INPUT_ID]
    checks = (
        (
            "source_status_completed",
            "e11_pv_recorded,e12_pva_vmax_ablation",
            e11.manifest["status"] == e12.manifest["status"] == "completed",
            [e11.manifest["status"], e12.manifest["status"]],
            ["completed", "completed"],
            "Both exact pinned runs completed.",
        ),
        (
            "same_git_commit",
            "e11_pv_recorded_vs_e12_pva_vmax_ablation",
            e11_git.get("commit") == e12_git.get("commit"),
            [e11_git.get("commit"), e12_git.get("commit")],
            "one shared commit",
            "Dirty state is retained in provenance and allowed by analysis.yaml.",
        ),
        (
            "common_input_reference_equal",
            INPUT_ID,
            e11_input.get("reference_sha256") == e12_input.get("reference_sha256"),
            [
                e11_input.get("reference_sha256"),
                e12_input.get("reference_sha256"),
            ],
            "identical reference SHA256",
            "E12 has auxiliary inputs, so whole input matrices are intentionally unequal.",
        ),
    )
    rows = []
    for check_id, scope, passed, actual, expected, notes in checks:
        rows.append(
            {
                "check_id": check_id,
                "scope": scope,
                "status": "pass" if passed else "fail",
                "actual": stable_json(actual),
                "expected": stable_json(expected),
                "blocking": "true",
                "notes": notes,
            }
        )
        if not passed:
            raise AnalysisValidationError(f"A04 source validation failed: {check_id}")
    return rows


def _source_method(components: str, stencil: str) -> tuple[str, str]:
    if components == "PV":
        return "e11_pv_recorded", f"pv_{stencil}"
    return "e12_pva_vmax_ablation", f"pva_{stencil}__v4p1"


def _baseline_source_method(components: str) -> tuple[str, str]:
    if components == "PV":
        return "e11_pv_recorded", BASELINE_METHOD_ID
    return "e12_pva_vmax_ablation", f"{BASELINE_METHOD_ID}__v4p1"


def _metric_index(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str, str], Mapping[str, Any]]:
    index: dict[tuple[str, str, str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row["source_id"]),
            str(row["method_id"]),
            str(row["input_id"]),
            str(row["window_id"]),
            str(row["metric_id"]),
        )
        if key in index:
            raise AnalysisValidationError(f"duplicate A04 metric key: {key}")
        index[key] = row
    return index


def _value(
    index: Mapping[
        tuple[str, str, str, str, str],
        Mapping[str, Any],
    ],
    source_id: str,
    method_id: str,
    window_id: str,
    metric_id: str,
) -> float | None:
    row = index.get((source_id, method_id, INPUT_ID, window_id, metric_id))
    if row is None or row.get("status") != "available":
        return None
    return as_float(row.get("value"))


def _require(
    value: float | None,
    label: str,
) -> float:
    if value is None:
        raise AnalysisValidationError(f"A04 missing available metric: {label}")
    return value


def _trace_lag_metrics(
    prepared: Any,
    source_id: str,
    method_id: str,
) -> Mapping[str, float | int]:
    source = next(
        item for item in prepared.sources if item.source_id == source_id
    )
    reference = load_trajectory_csv(
        source.directory / "inputs" / INPUT_ID / "reference.csv"
    )
    command = load_trajectory_csv(
        source.directory / "methods" / method_id / INPUT_ID / "command.csv"
    )
    common = np.intersect1d(reference.sample_index, command.sample_index)
    reference_offsets = np.searchsorted(reference.sample_index, common)
    command_offsets = np.searchsorted(command.sample_index, common)
    main_mask = reference.time_s[reference_offsets] >= 0.04 - 1e-12
    return _lag_metrics(
        reference.position_rad[reference_offsets][main_mask],
        command.position_rad[command_offsets][main_mask],
        reference.time_s[reference_offsets][main_mask],
        0.2,
    )


def _baseline_equivalence(
    index: Mapping[
        tuple[str, str, str, str, str],
        Mapping[str, Any],
    ],
) -> dict[str, Any]:
    left_source, left_method = _baseline_source_method("PV")
    right_source, right_method = _baseline_source_method("PVA")
    compared = 0
    mismatches = 0
    max_abs = 0.0
    for window_id in ("main_evaluation", "full_overlap"):
        metric_ids = {
            key[4]
            for key in index
            if key[0] == left_source
            and key[1] == left_method
            and key[2] == INPUT_ID
            and key[3] == window_id
        }
        for metric_id in metric_ids:
            if metric_id.startswith(("runtime_", "deadline_")):
                continue
            left = _value(
                index,
                left_source,
                left_method,
                window_id,
                metric_id,
            )
            right = _value(
                index,
                right_source,
                right_method,
                window_id,
                metric_id,
            )
            if left is None and right is None:
                continue
            compared += 1
            if left is None or right is None:
                mismatches += 1
                continue
            difference = abs(left - right)
            max_abs = max(max_abs, difference)
            if not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12):
                mismatches += 1
    if mismatches:
        raise AnalysisValidationError(
            f"A04 E11/E12 baseline mismatch: {mismatches}/{compared}"
        )
    return {
        "check_id": "cross_experiment_baseline_equivalence",
        "scope": "e11_pv_recorded_vs_e12_pva_v4p1",
        "status": "pass",
        "actual": stable_json(
            {
                "compared_available_metrics": compared,
                "mismatches": mismatches,
                "max_abs_difference": max_abs,
            }
        ),
        "expected": stable_json({"mismatches": 0}),
        "blocking": "true",
        "notes": "Same scheduled P[k+1] baseline; runtime/deadline unavailable rows are not paired.",
    }


def _scorecard_rows(
    index: Mapping[
        tuple[str, str, str, str, str],
        Mapping[str, Any],
    ],
    prepared: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for components in ("PV", "PVA"):
        baseline_source, baseline_method = _baseline_source_method(components)
        baseline_rmse = _require(
            _value(
                index,
                baseline_source,
                baseline_method,
                "main_evaluation",
                "position_rmse",
            ),
            f"{components} baseline RMSE",
        )
        baseline_lag = _require(
            _value(
                index,
                baseline_source,
                baseline_method,
                "main_evaluation",
                "lag_s",
            ),
            f"{components} baseline lag",
        )
        baseline_trace_lag = _trace_lag_metrics(
            prepared,
            baseline_source,
            baseline_method,
        )
        baseline_subsample_lag = float(
            baseline_trace_lag["lag_subsample_s"]
        )
        for stencil in STENCILS:
            source_id, method_id = _source_method(components, stencil)
            rmse = _require(
                _value(
                    index,
                    source_id,
                    method_id,
                    "main_evaluation",
                    "position_rmse",
                ),
                f"{method_id} RMSE",
            )
            lag = _require(
                _value(
                    index,
                    source_id,
                    method_id,
                    "main_evaluation",
                    "lag_s",
                ),
                f"{method_id} lag",
            )
            trace_lag = _trace_lag_metrics(prepared, source_id, method_id)
            subsample_lag = float(trace_lag["lag_subsample_s"])
            core_values = {
                metric_id: _value(
                    index,
                    source_id,
                    method_id,
                    "full_overlap",
                    metric_id,
                )
                for metric_id in CORE_GUARDRAILS
            }
            core_pass = all(
                value is not None and abs(value) <= 1e-12
                for value in core_values.values()
            )
            deadline = _value(
                index,
                source_id,
                method_id,
                "full_overlap",
                "deadline_miss_rate",
            )
            deadline_pass = deadline is not None and abs(deadline) <= 1e-12
            ratio = rmse / baseline_rmse
            rows.append(
                {
                    "target_components": components,
                    "stencil": stencil,
                    "stencil_label": STENCIL_LABELS[stencil],
                    "source_id": source_id,
                    "method_id": method_id,
                    "position_rmse_rad": rmse,
                    "baseline_position_rmse_rad": baseline_rmse,
                    "rmse_ratio_vs_p": ratio,
                    "rmse_beats_p": ratio < 1.0,
                    "baseline_lag_s": baseline_lag,
                    "baseline_lag_ms": 1000.0 * baseline_lag,
                    "baseline_subsample_lag_s": baseline_subsample_lag,
                    "baseline_subsample_lag_ms": (
                        1000.0 * baseline_subsample_lag
                    ),
                    "lag_s": lag,
                    "lag_ms": 1000.0 * lag,
                    "absolute_lag_ms": 1000.0 * abs(lag),
                    "lag_subsample_s": subsample_lag,
                    "lag_subsample_ms": 1000.0 * subsample_lag,
                    "absolute_subsample_lag_ms": (
                        1000.0 * abs(subsample_lag)
                    ),
                    "lag_delta_vs_p_ms": 1000.0 * (lag - baseline_lag),
                    "absolute_lag_delta_vs_p_ms": (
                        1000.0 * (abs(lag) - abs(baseline_lag))
                    ),
                    "subsample_lag_delta_vs_p_ms": (
                        1000.0
                        * (subsample_lag - baseline_subsample_lag)
                    ),
                    "absolute_subsample_lag_delta_vs_p_ms": (
                        1000.0
                        * (
                            abs(subsample_lag)
                            - abs(baseline_subsample_lag)
                        )
                    ),
                    "core_guardrails_pass": core_pass,
                    "deadline_miss_rate": deadline,
                    "deadline_pass": deadline_pass,
                    "eligible_strict": core_pass and deadline_pass,
                    "eligible_ignoring_deadline": core_pass,
                }
            )
    sensitivity = [row for row in rows if row["eligible_ignoring_deadline"]]
    if not sensitivity:
        raise AnalysisValidationError("A04 has no core-eligible candidates")

    def dominates(
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> bool:
        left_values = (
            float(left["rmse_ratio_vs_p"]),
            float(left["absolute_subsample_lag_ms"]),
        )
        right_values = (
            float(right["rmse_ratio_vs_p"]),
            float(right["absolute_subsample_lag_ms"]),
        )
        return all(
            left_value <= right_value + 1e-12
            for left_value, right_value in zip(left_values, right_values)
        ) and any(
            left_value < right_value - 1e-12
            for left_value, right_value in zip(left_values, right_values)
        )

    pareto = [
        row
        for row in sensitivity
        if not any(
            dominates(other, row)
            for other in sensitivity
            if other["method_id"] != row["method_id"]
        )
    ]
    pareto_ids = {str(row["method_id"]) for row in pareto}
    selected_by_budget: dict[float, Mapping[str, Any]] = {}
    for budget_ms in LAG_BUDGETS_MS:
        candidates = [
            row
            for row in pareto
            if float(row["absolute_subsample_lag_ms"]) <= budget_ms + 1e-9
        ]
        if not candidates:
            raise AnalysisValidationError(
                f"A04 has no Pareto candidate within {budget_ms:g} ms"
            )
        selected_by_budget[budget_ms] = min(
            candidates,
            key=lambda row: (
                float(row["rmse_ratio_vs_p"]),
                float(row["absolute_subsample_lag_ms"]),
                str(row["target_components"]),
                STENCILS.index(str(row["stencil"])),
            ),
        )

    strict_candidates = [
        row
        for row in rows
        if row["eligible_strict"]
        and float(row["absolute_subsample_lag_ms"])
        <= LAG_BUDGETS_MS[0] + 1e-9
    ]
    best_strict = (
        None
        if not strict_candidates
        else min(
            strict_candidates,
            key=lambda row: (
                float(row["rmse_ratio_vs_p"]),
                float(row["absolute_subsample_lag_ms"]),
                str(row["target_components"]),
                STENCILS.index(str(row["stencil"])),
            ),
        )
    )
    for row in rows:
        row["rmse_lag_pareto"] = str(row["method_id"]) in pareto_ids
        for budget_ms, selected in selected_by_budget.items():
            token = f"{int(budget_ms)}ms"
            row[f"eligible_lag_budget_{token}"] = bool(
                row["eligible_ignoring_deadline"]
                and float(row["absolute_subsample_lag_ms"])
                <= budget_ms + 1e-9
            )
            row[f"selected_lag_budget_{token}"] = (
                row["method_id"] == selected["method_id"]
            )
        row["selected_primary"] = bool(row["selected_lag_budget_10ms"])
        row["selected_strict"] = (
            best_strict is not None
            and row["method_id"] == best_strict["method_id"]
        )
    return rows


def _matched_rows(
    scorecard: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    index = {
        (str(row["target_components"]), str(row["stencil"])): row
        for row in scorecard
    }
    output = []
    for stencil in STENCILS:
        pv = index[("PV", stencil)]
        pva = index[("PVA", stencil)]
        output.append(
            {
                "stencil": stencil,
                "stencil_label": STENCIL_LABELS[stencil],
                "pv_rmse_ratio_vs_p": pv["rmse_ratio_vs_p"],
                "pva_rmse_ratio_vs_p": pva["rmse_ratio_vs_p"],
                "pva_minus_pv_rmse_ratio": (
                    float(pva["rmse_ratio_vs_p"])
                    - float(pv["rmse_ratio_vs_p"])
                ),
                "pv_lag_ms": pv["lag_ms"],
                "pva_lag_ms": pva["lag_ms"],
                "pv_absolute_lag_ms": pv["absolute_lag_ms"],
                "pva_absolute_lag_ms": pva["absolute_lag_ms"],
                "pv_subsample_lag_ms": pv["lag_subsample_ms"],
                "pva_subsample_lag_ms": pva["lag_subsample_ms"],
                "pv_absolute_subsample_lag_ms": (
                    pv["absolute_subsample_lag_ms"]
                ),
                "pva_absolute_subsample_lag_ms": (
                    pva["absolute_subsample_lag_ms"]
                ),
                "pva_minus_pv_lag_ms": (
                    float(pva["lag_ms"]) - float(pv["lag_ms"])
                ),
                "pva_minus_pv_absolute_lag_ms": (
                    float(pva["absolute_lag_ms"])
                    - float(pv["absolute_lag_ms"])
                ),
                "pv_beats_p": pv["rmse_beats_p"],
                "pva_beats_p": pva["rmse_beats_p"],
                "matched_core_guardrails_pass": (
                    bool(pv["core_guardrails_pass"])
                    and bool(pva["core_guardrails_pass"])
                ),
            }
        )
    return output


def _plot_rmse_lag_pareto(
    scorecard: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10.0, 6.2), constrained_layout=True)
    for components, color, marker in (
        ("PV", "#4477AA", "o"),
        ("PVA", "#EE6677", "s"),
    ):
        rows = [
            row
            for row in scorecard
            if row["target_components"] == components
        ]
        axis.scatter(
            [float(row["absolute_subsample_lag_ms"]) for row in rows],
            [float(row["rmse_ratio_vs_p"]) for row in rows],
            color=color,
            marker=marker,
            s=[
                110.0 if row["rmse_lag_pareto"] else 55.0
                for row in rows
            ],
            edgecolors="#111827",
            linewidths=[
                1.2 if row["rmse_lag_pareto"] else 0.5
                for row in rows
            ],
            label=components,
            zorder=3,
        )
        for row in rows:
            axis.annotate(
                str(row["stencil_label"]),
                (
                    float(row["absolute_subsample_lag_ms"]),
                    float(row["rmse_ratio_vs_p"]),
                ),
                xytext=(5, 5 if components == "PV" else -12),
                textcoords="offset points",
                fontsize=8,
                color=color,
            )
    baseline_lag_ms = abs(
        float(scorecard[0]["baseline_subsample_lag_ms"])
    )
    axis.scatter(
        [baseline_lag_ms],
        [1.0],
        marker="*",
        s=210,
        color="#111827",
        label="Scheduled P",
        zorder=4,
    )
    for budget_ms, linestyle in zip(LAG_BUDGETS_MS, ("--", ":")):
        axis.axvline(
            budget_ms,
            color="#6B7280",
            linestyle=linestyle,
            linewidth=1.1,
            label=f"|lag| budget {budget_ms:g} ms",
        )
    axis.axhline(1.0, color="#111827", linestyle="--", linewidth=1.2)
    axis.set_xlabel("|Sub-sample observed lag| (ms)")
    axis.set_ylabel("Position RMSE ratio vs scheduled P")
    axis.set_title("Recorded tracking: RMSE–lag Pareto selection")
    axis.set_xlim(left=7.0)
    axis.grid(alpha=0.3)
    axis.legend()
    return save_figure(
        figure,
        RESULTS_DIRECTORY / "pv_pva_fd_rmse_lag_pareto",
    )


def _results_markdown(
    scorecard: Sequence[Mapping[str, Any]],
    matched: Sequence[Mapping[str, Any]],
) -> str:
    selected_10 = next(
        row for row in scorecard if row["selected_lag_budget_10ms"]
    )
    selected_20 = next(
        row for row in scorecard if row["selected_lag_budget_20ms"]
    )
    selected_strict = next(
        (row for row in scorecard if row["selected_strict"]),
        None,
    )
    selection_stable = (
        selected_strict is not None
        and selected_10["method_id"] == selected_strict["method_id"]
    )
    budget_stable = selected_10["method_id"] == selected_20["method_id"]
    baseline_rmse = float(selected_10["baseline_position_rmse_rad"])
    baseline_lag_ms = float(selected_10["baseline_lag_ms"])
    baseline_subsample_lag_ms = float(
        selected_10["baseline_subsample_lag_ms"]
    )
    pva_future_o1 = next(
        row
        for row in scorecard
        if row["target_components"] == "PVA"
        and row["stencil"] == "pred_backward_o1_kp1"
    )
    pva_better_than_matched_pv = sum(
        float(row["pva_minus_pv_rmse_ratio"]) < 0.0 for row in matched
    )
    table_rows = [
        (
            row["target_components"],
            row["stencil_label"],
            f"{float(row['position_rmse_rad']):.8g}",
            f"{float(row['rmse_ratio_vs_p']):.6f}",
            f"{float(row['lag_ms']):.0f}",
            f"{float(row['lag_subsample_ms']):.3f}",
            f"{float(row['absolute_subsample_lag_delta_vs_p_ms']):+.3f}",
            str(row["rmse_lag_pareto"]).lower(),
            str(row["core_guardrails_pass"]).lower(),
            str(row["deadline_pass"]).lower(),
            (
                "10/20 ms"
                if row["selected_lag_budget_10ms"]
                and row["selected_lag_budget_20ms"]
                else ""
            ),
        )
        for row in scorecard
    ]
    matched_rows = [
        (
            row["stencil_label"],
            f"{float(row['pv_rmse_ratio_vs_p']):.6f}",
            f"{float(row['pva_rmse_ratio_vs_p']):.6f}",
            f"{float(row['pva_minus_pv_rmse_ratio']):+.6f}",
            f"{float(row['pv_lag_ms']):.0f}",
            f"{float(row['pva_lag_ms']):.0f}",
            f"{float(row['pv_subsample_lag_ms']):.3f}",
            f"{float(row['pva_subsample_lag_ms']):.3f}",
        )
        for row in matched
    ]
    return f"""# A04 — Recorded tracking 的 PV/PVA 与差分方法选型

## 选型结论

在 velocity-limited recorded input、`V/A/J=4.1/8.2/4000`、`t>=0.04 s`
的同一窗口下，RMSE 与 `|observed lag|` 作为 co-primary。Scheduled P 的
RMSE/lag 是 **{baseline_rmse:.8g} rad / {baseline_lag_ms:.0f} ms**；
亚采样 lag 敏感性为 **{baseline_subsample_lag_ms:.3f} ms**。
10 ms 与 20 ms 两档均选择
**{selected_10['target_components']} + {selected_10['stencil_label']}**
（`{selected_10['method_id']}`），其 RMSE/lag 是
**{float(selected_10['position_rmse_rad']):.8g} rad /
{float(selected_10['lag_ms']):.0f} ms**，亚采样 lag 为
**{float(selected_10['lag_subsample_ms']):.3f} ms**。

该方法将 RMSE 降低 **{100.0 * (1.0 - float(selected_10['rmse_ratio_vs_p'])):.2f}%**，
同时把 lag 从 {baseline_lag_ms:.0f} ms 降到
{float(selected_10['lag_ms']):.0f} ms；亚采样敏感性从
{baseline_subsample_lag_ms:.3f} ms 降到
{float(selected_10['lag_subsample_ms']):.3f} ms，并在 RMSE–lag 平面支配
其他候选。PVA + Future O1 的整数 lag 虽同为
{float(pva_future_o1['lag_ms']):.0f} ms，但亚采样 lag 为
{float(pva_future_o1['lag_subsample_ms']):.3f} ms，RMSE
为 {float(pva_future_o1['position_rmse_rad']):.8g} rad，未形成可接受交换。
额外施加本机
deadline gate 后的敏感性选型{'保持一致' if selection_stable else '发生变化或无严格候选'}；
deadline 不参与联合选型或任意加权总分。PVA 的五种方法全部不如各自同条件
P baseline。PVA 在 {pva_better_than_matched_pv}/5 个 stencil 上优于 matched PV，
但这些改善都不足以击败 P；唯一击败 P 的 PV Future O1 加入 A 后反而退化。
10/20 ms 场景选择{'一致' if budget_stable else '不一致'}。

## 10-arm scorecard

{markdown_table(
    (
        "分量",
        "差分",
        "RMSE rad",
        "RMSE/P",
        "integer lag ms",
        "sub-sample lag ms",
        "Δ|sub-sample lag| vs P",
        "Pareto",
        "core guardrail",
        "deadline",
        "选择",
    ),
    table_rows,
)}

## Matched PV/PVA

{markdown_table(
    (
        "差分",
        "PV ratio",
        "PVA ratio",
        "PVA−PV",
        "PV integer lag",
        "PVA integer lag",
        "PV sub-sample lag",
        "PVA sub-sample lag",
    ),
    matched_rows,
)}

## 决策规则

- hard gate：同一 `main_evaluation` 窗口的 RMSE 和 signed lag 均 available，
  且所有可定义的 constraint/fallback/solver guardrail 为零；
- primary gate 不含本机调度抖动；strict sensitivity 额外要求
  `deadline_miss_rate=0`；
- primary gate 后先计算 `(RMSE/P, |sub-sample lag|)` Pareto 前沿，再分别施加
  `|lag|<=10 ms` 和 `|lag|<=20 ms` 场景预算；不对不同单位加权求和；
- 同时保留 10 ms 网格的 integer lag；sub-sample lag 是整数最优点相邻
  MSE 的局部二次插值，两者都不是 wall-clock latency；
- E11 与 E12 内部重复的 scheduled P baseline 已逐 metric 校验等价；
- 输入只有一条 recorded waveform，因此该选择是当前轨迹的部署候选，不外推为
  普遍最优差分公式。
"""


def _write_outputs(
    prepared: Any,
    output_directory: Path,
    source_checks: Sequence[Mapping[str, Any]],
    scorecard: Sequence[Mapping[str, Any]],
    matched: Sequence[Mapping[str, Any]],
) -> None:
    global RESULTS_DIRECTORY
    RESULTS_DIRECTORY = output_directory
    write_prepared_analysis(prepared, RESULTS_DIRECTORY / "work")
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_files: list[Path] = []
    specs = (
        (
            RESULTS_DIRECTORY / "source_validation.csv",
            ("check_id", "scope", "status", "actual", "expected", "blocking", "notes"),
            source_checks,
        ),
        (
            RESULTS_DIRECTORY / "selection_scorecard.csv",
            tuple(scorecard[0]),
            scorecard,
        ),
        (
            RESULTS_DIRECTORY / "matched_pv_pva.csv",
            tuple(matched[0]),
            matched,
        ),
    )
    for path, fields, rows in specs:
        write_csv(path, fields, rows)
        output_files.append(path)
    figures = _plot_rmse_lag_pareto(scorecard)
    validate_figure_files(figures)
    output_files.extend(figures)
    results_path = RESULTS_DIRECTORY / "RESULTS.md"
    results_markdown = _results_markdown(scorecard, matched)
    write_text(results_path, results_markdown)
    write_text(ANALYSIS_DIRECTORY / "RESULTS.md", results_markdown)
    output_files.append(results_path)
    manifest_path = RESULTS_DIRECTORY / "analysis_manifest.json"
    write_analysis_manifest(prepared, manifest_path, output_files)


def run(*, check_only: bool = False) -> int:
    prepared = prepare_analysis(CONFIG_PATH)
    source_checks = _validate_a04_sources(prepared)
    metrics = prepared_rows(prepared, "trajectory_metrics")
    index = _metric_index(metrics)
    source_checks.append(_baseline_equivalence(index))
    scorecard = _scorecard_rows(index, prepared)
    matched = _matched_rows(scorecard)
    if check_only:
        selected_10 = next(
            row for row in scorecard if row["selected_lag_budget_10ms"]
        )
        selected_20 = next(
            row for row in scorecard if row["selected_lag_budget_20ms"]
        )
        print(
            "A04: validated 2 pinned sources, 10 candidates, and 5 matched "
            "PV/PVA stencils; 10/20 ms selections="
            f"{selected_10['method_id']}/{selected_20['method_id']}"
        )
        return 0
    run_directory = create_analysis_run_directory(prepared)
    _write_outputs(
        prepared,
        run_directory,
        source_checks,
        scorecard,
        matched,
    )
    print(f"A04: wrote method-selection run to {run_directory}")
    return 0
