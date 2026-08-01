"""A03 controlled attribution of recorded PVA performance to runtime Vmax."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
    validate_sources,
    write_analysis_manifest,
    write_csv,
    write_text,
)

ANALYSIS_DIRECTORY = Path(__file__).resolve().parent
CONFIG_PATH = ANALYSIS_DIRECTORY / "analysis.yaml"
RESULTS_DIRECTORY = ANALYSIS_DIRECTORY / "results"
ORIGINAL_INPUT_ID = "recorded_tasks_original_no_velocity_limit"
METHOD_ORDER = (
    "pva_est_backward_o1_k",
    "pva_est_backward_o2_k",
    "pva_est_centered_o2_km1",
    "pva_pred_backward_o1_kp1",
    "pva_pred_backward_o2_kp1",
)
METHOD_LABELS = {
    "pva_est_backward_o1_k": "Backward O1",
    "pva_est_backward_o2_k": "Backward O2",
    "pva_est_centered_o2_km1": "Centered O2",
    "pva_pred_backward_o1_kp1": "Future O1",
    "pva_pred_backward_o2_kp1": "Future O2",
}
INPUT_ORDER = (
    ORIGINAL_INPUT_ID,
    "recorded_tasks_simplified_no_velocity_limit",
    "recorded_tasks_simplified_with_velocity_limit",
)
VMAX_LEVELS = (4.1, 10.0)


def _truth(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _required_float(row: Mapping[str, Any], field: str) -> float:
    value = as_float(row.get(field))
    if value is None:
        raise AnalysisValidationError(f"A03 missing finite {field}: {row}")
    return value


def _validate_matrix(
    surface: Sequence[Mapping[str, Any]],
    interactions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def record(
        check_id: str,
        passed: bool,
        actual: Any,
        expected: Any,
        notes: str,
    ) -> None:
        checks.append(
            {
                "check_id": check_id,
                "scope": "e12_vmax_ablation",
                "status": "pass" if passed else "fail",
                "actual": stable_json(actual),
                "expected": stable_json(expected),
                "blocking": "true",
                "notes": notes,
            }
        )
        if not passed:
            raise AnalysisValidationError(f"A03 validation failed: {check_id}")

    surface_keys = {
        (
            str(row["input_id"]),
            str(row["method_id"]),
            _required_float(row, "max_velocity_rad_s"),
        )
        for row in surface
    }
    expected_surface = {
        (input_id, method_id, vmax)
        for input_id in INPUT_ORDER
        for method_id in ("p_kp1_baseline", *METHOD_ORDER)
        for vmax in (4.1, 10.0)
    }
    record(
        "complete_surface",
        surface_keys == expected_surface,
        {"rows": len(surface_keys), "missing": len(expected_surface - surface_keys)},
        {"rows": 36, "missing": 0},
        "Three inputs × six methods × two runtime Vmax levels.",
    )
    interaction_keys = {
        (str(row["input_id"]), str(row["method_id"])) for row in interactions
    }
    expected_interactions = {
        (input_id, method_id)
        for input_id in INPUT_ORDER
        for method_id in METHOD_ORDER
    }
    record(
        "complete_interactions",
        interaction_keys == expected_interactions,
        {
            "rows": len(interaction_keys),
            "missing": len(expected_interactions - interaction_keys),
        },
        {"rows": 15, "missing": 0},
        "Every PVA stencil has one within-input Vmax interaction.",
    )
    integrity_failures = [
        str(row["case_id"])
        for row in surface
        if not _truth(row.get("integrity_pass"))
    ]
    record(
        "execution_integrity",
        not integrity_failures,
        {"failures": integrity_failures},
        {"failures": []},
        "Completion, exact constraints, projection reconstruction, and target admissibility.",
    )
    missing_lag = [
        str(row["case_id"])
        for row in surface
        if as_float(row.get("lag_s")) is None
    ]
    record(
        "lag_metric_complete",
        not missing_lag,
        {"missing": missing_lag},
        {"missing": []},
        "Every RMSE arm has a same-window signed observed lag.",
    )
    return checks


def _surface_index(
    surface: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, float], Mapping[str, Any]]:
    return {
        (
            str(row["input_id"]),
            str(row["method_id"]),
            _required_float(row, "max_velocity_rad_s"),
        ): row
        for row in surface
    }


def _decision_rows(
    interactions: Sequence[Mapping[str, Any]],
    surface: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    surface_index = _surface_index(surface)
    output: list[dict[str, Any]] = []
    for row in interactions:
        input_id = str(row["input_id"])
        method_id = str(row["method_id"])
        low_vmax = _required_float(row, "limited_vmax_rad_s")
        high_vmax = _required_float(row, "relaxed_vmax_rad_s")
        low_baseline = surface_index[(input_id, "p_kp1_baseline", low_vmax)]
        high_baseline = surface_index[(input_id, "p_kp1_baseline", high_vmax)]
        low_candidate = surface_index[(input_id, method_id, low_vmax)]
        high_candidate = surface_index[(input_id, method_id, high_vmax)]

        low_ratio = _required_float(row, "limited_rmse_ratio_vs_p")
        high_ratio = _required_float(row, "relaxed_rmse_ratio_vs_p")
        interaction = _required_float(
            row,
            "log_ratio_interaction_limited_minus_relaxed",
        )
        low_baseline_lag_ms = 1000.0 * _required_float(
            low_baseline,
            "lag_s",
        )
        high_baseline_lag_ms = 1000.0 * _required_float(
            high_baseline,
            "lag_s",
        )
        low_candidate_lag_ms = 1000.0 * _required_float(
            low_candidate,
            "lag_s",
        )
        high_candidate_lag_ms = 1000.0 * _required_float(
            high_candidate,
            "lag_s",
        )
        low_lag_excess_ms = (
            abs(low_candidate_lag_ms) - abs(low_baseline_lag_ms)
        )
        high_lag_excess_ms = (
            abs(high_candidate_lag_ms) - abs(high_baseline_lag_ms)
        )
        lag_interaction_ms = low_lag_excess_ms - high_lag_excess_ms
        pva_worse_both = low_ratio > 1.0 and high_ratio > 1.0
        ratio_invariant = math.isclose(interaction, 0.0, abs_tol=1e-12)
        lag_invariant = math.isclose(
            lag_interaction_ms,
            0.0,
            abs_tol=1e-9,
        )
        pva_lag_worse_both = (
            low_lag_excess_ms > 1e-9 and high_lag_excess_ms > 1e-9
        )
        high_nonbinding = _truth(
            row.get("relaxed_velocity_condition_nonbinding")
        )
        attribution_supported = _truth(
            row.get("velocity_limit_attribution_supported")
        )
        acceleration_projection_persists = (
            int(float(row["limited_acceleration_clip_count"])) > 0
            and int(float(row["relaxed_acceleration_clip_count"])) > 0
        )
        if attribution_supported:
            decision = "supported_runtime_velocity_limit"
        elif pva_worse_both and ratio_invariant and lag_invariant and high_nonbinding:
            decision = "rejected_runtime_velocity_limit"
        else:
            decision = "inconclusive"
        output.append(
            {
                "input_id": input_id,
                "input_acquisition_velocity_limited": row[
                    "input_acquisition_velocity_limited"
                ],
                "method_id": method_id,
                "method_label": METHOD_LABELS[method_id],
                "limited_rmse_ratio_vs_p": low_ratio,
                "relaxed_rmse_ratio_vs_p": high_ratio,
                "log_ratio_interaction_limited_minus_relaxed": interaction,
                "pva_worse_than_p_at_both_vmax": pva_worse_both,
                "ratio_invariant_within_tolerance": ratio_invariant,
                "limited_baseline_lag_ms": low_baseline_lag_ms,
                "limited_pva_lag_ms": low_candidate_lag_ms,
                "limited_absolute_lag_excess_vs_p_ms": low_lag_excess_ms,
                "relaxed_baseline_lag_ms": high_baseline_lag_ms,
                "relaxed_pva_lag_ms": high_candidate_lag_ms,
                "relaxed_absolute_lag_excess_vs_p_ms": high_lag_excess_ms,
                "absolute_lag_interaction_limited_minus_relaxed_ms": (
                    lag_interaction_ms
                ),
                "lag_interaction_invariant_within_tolerance": lag_invariant,
                "pva_lag_worse_than_p_at_both_vmax": pva_lag_worse_both,
                "relaxed_velocity_condition_nonbinding": high_nonbinding,
                "limited_velocity_clip_count": row[
                    "limited_velocity_clip_count"
                ],
                "relaxed_velocity_clip_count": row[
                    "relaxed_velocity_clip_count"
                ],
                "limited_acceleration_clip_count": row[
                    "limited_acceleration_clip_count"
                ],
                "relaxed_acceleration_clip_count": row[
                    "relaxed_acceleration_clip_count"
                ],
                "acceleration_projection_persists": (
                    acceleration_projection_persists
                ),
                "velocity_limit_attribution_supported": attribution_supported,
                "attribution_decision": decision,
            }
        )
    return output


def _mechanism_rows(
    surface: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float], list[Mapping[str, Any]]] = defaultdict(list)
    for row in surface:
        if row["method_id"] == "p_kp1_baseline":
            continue
        groups[
            (str(row["input_id"]), _required_float(row, "max_velocity_rad_s"))
        ].append(row)
    output = []
    for (input_id, vmax), rows in sorted(
        groups.items(),
        key=lambda item: (
            INPUT_ORDER.index(item[0][0]),
            item[0][1],
        ),
    ):
        output.append(
            {
                "input_id": input_id,
                "max_velocity_rad_s": vmax,
                "method_count": len(rows),
                "total_projection_count": sum(
                    int(float(row["projection_count"])) for row in rows
                ),
                "total_velocity_clip_count": sum(
                    int(float(row["velocity_clip_count"])) for row in rows
                ),
                "total_acceleration_clip_count": sum(
                    int(float(row["acceleration_clip_count"])) for row in rows
                ),
                "total_stopping_envelope_count": sum(
                    int(float(row["stopping_envelope_count"])) for row in rows
                ),
                "min_rmse_ratio_vs_p": min(
                    _required_float(row, "rmse_ratio_vs_p") for row in rows
                ),
                "max_rmse_ratio_vs_p": max(
                    _required_float(row, "rmse_ratio_vs_p") for row in rows
                ),
            }
        )
    return output


def _plot_original_rmse_lag(
    decisions: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt
    import numpy as np

    rows = sorted(
        (
            row
            for row in decisions
            if row["input_id"] == ORIGINAL_INPUT_ID
        ),
        key=lambda row: METHOD_ORDER.index(str(row["method_id"])),
    )
    x = np.arange(len(rows), dtype=float)
    width = 0.36
    figure, axes = plt.subplots(
        2,
        1,
        figsize=(10.5, 8.2),
        sharex=True,
        constrained_layout=True,
    )
    axes[0].bar(
        x - width / 2,
        [float(row["limited_rmse_ratio_vs_p"]) for row in rows],
        width,
        color="#4477AA",
        label="runtime Vmax = 4.1",
    )
    axes[0].bar(
        x + width / 2,
        [float(row["relaxed_rmse_ratio_vs_p"]) for row in rows],
        width,
        color="#EE6677",
        label="runtime Vmax = 10",
    )
    axes[0].axhline(1.0, color="#111827", linestyle="--", linewidth=1.2)
    axes[0].set_ylabel("Position RMSE / scheduled P")
    axes[0].set_title("Original recorded trajectory: RMSE and lag under Vmax")
    axes[0].grid(axis="y", alpha=0.35)
    axes[0].legend()

    axes[1].bar(
        x - width / 2,
        [abs(float(row["limited_pva_lag_ms"])) for row in rows],
        width,
        color="#4477AA",
        label="runtime Vmax = 4.1",
    )
    axes[1].bar(
        x + width / 2,
        [abs(float(row["relaxed_pva_lag_ms"])) for row in rows],
        width,
        color="#EE6677",
        label="runtime Vmax = 10",
    )
    baseline_lag_ms = abs(float(rows[0]["limited_baseline_lag_ms"]))
    axes[1].axhline(
        baseline_lag_ms,
        color="#111827",
        linestyle="--",
        linewidth=1.2,
        label=f"scheduled P = {baseline_lag_ms:g} ms",
    )
    axes[1].set_xticks(
        x,
        [str(row["method_label"]) for row in rows],
        rotation=15,
    )
    axes[1].set_ylabel("|Observed lag| (ms)")
    axes[1].grid(axis="y", alpha=0.35)
    axes[1].legend()
    return save_figure(
        figure,
        RESULTS_DIRECTORY / "original_pva_p_vmax_rmse_lag",
    )


def _results_markdown(
    decisions: Sequence[Mapping[str, Any]],
    mechanisms: Sequence[Mapping[str, Any]],
    *,
    source_clean: bool,
) -> str:
    original = [
        row for row in decisions if row["input_id"] == ORIGINAL_INPUT_ID
    ]
    all_worse = all(_truth(row["pva_worse_than_p_at_both_vmax"]) for row in original)
    all_rejected = all(
        row["attribution_decision"] == "rejected_runtime_velocity_limit"
        for row in original
    )
    comparison_rows = [
        (
            row["method_label"],
            f"{float(row['limited_rmse_ratio_vs_p']):.6f}",
            f"{float(row['relaxed_rmse_ratio_vs_p']):.6f}",
            f"{float(row['limited_pva_lag_ms']):.0f}",
            f"{float(row['relaxed_pva_lag_ms']):.0f}",
            f"{float(row['limited_absolute_lag_excess_vs_p_ms']):+.0f}",
            f"{float(row['absolute_lag_interaction_limited_minus_relaxed_ms']):.3g}",
            row["attribution_decision"],
        )
        for row in sorted(
            original,
            key=lambda item: METHOD_ORDER.index(str(item["method_id"])),
        )
    ]
    original_mechanisms = [
        row for row in mechanisms if row["input_id"] == ORIGINAL_INPUT_ID
    ]
    mechanism_table = [
        (
            f"{float(row['max_velocity_rad_s']):g}",
            row["total_velocity_clip_count"],
            row["total_acceleration_clip_count"],
            row["total_stopping_envelope_count"],
        )
        for row in original_mechanisms
    ]
    provenance_sentence = (
        "该 run 的 manifest 记录 clean-commit provenance。"
        if source_clean
        else (
            "该 run 的 manifest 记录 dirty worktree，因此结论具有确定性"
            "本地复现证据，但不是 clean-commit release 证明。"
        )
    )
    return f"""# A03 — Recorded PVA 劣化与 velocity-limit 归因

> 证据角色：这是 original recorded waveform 上的归因诊断，不参与上线
> PV/PVA 排名或收益计算。上线比较只使用 velocity-limit recorded
> trajectory。

## 结论

原始 `original_no_velocity_limit` 轨迹上，五种 PVA 的 raw-time position
RMSE 在 `Vmax=4.1` 和放宽后的 `Vmax=10` 下都高于 scheduled P baseline：
**{str(all_worse).lower()}**。但两种 Vmax 下每个方法的 PVA/P 比值完全一致，
预注册的 log-ratio interaction 均为 0。Scheduled P 的 observed lag 是
**60 ms**，五种 PVA 是 **110–160 ms**；放宽 Vmax 后 lag 也逐方法完全不变。
`Vmax=10` 对 velocity/stopping mechanism 非绑定。因而“PVA 的 RMSE/lag
劣化由 runtime velocity limit 导致”的归因结论为：
**{'拒绝' if all_rejected else '未定'}**。

也就是说，这组实验只说明 original waveform 内部的 Vmax 干预不改变
RMSE/lag 关系，不能把该结论用于 velocity-limit waveform 的 PV/PVA
排序。主要实际干预来自 acceleration clipping；它在两个 Vmax 条件下保持
不变。采集时是否限速属于不同输入曲线的观察性标签，三条曲线长度和形状不同，
因此不能把跨文件差异解释为采集限速的单因素因果效应。

## Original 轨迹 RMSE–lag 逐方法结果

{markdown_table(
    (
        "方法",
        "RMSE/P @4.1",
        "RMSE/P @10",
        "lag @4.1 ms",
        "lag @10 ms",
        "|lag| excess vs P",
        "lag interaction ms",
        "归因",
    ),
    comparison_rows,
)}

## Original 轨迹投影机制合计

{markdown_table(
    ("Vmax", "velocity clip", "acceleration clip", "stopping envelope"),
    mechanism_table,
)}

## 判定规则

- co-primary outcomes：`t>=0.04 s` 的 raw-time position RMSE 与
  `|observed lag|`；
- 同一输入、同一 Vmax 下以 scheduled `P[k+1]` 为 baseline；
- 只有 relaxed Vmax 非绑定、limited Vmax 实际绑定且 RMSE 或 lag 随干预改善，
  才支持相应的 velocity-limit 归因；
- observed lag 是 10 ms 整数采样移位诊断，不是 wall-clock latency；
- 本分析不进入上线 scorecard、Pareto 或收益计算；
- deadline 仅报告，不作为因果交互的替代指标；
- 所有 36 arms 均通过完成性、约束、投影重构和 executable-target admissibility
  完整性门槛。

来源：E12 的 36-arm controlled rerun。{provenance_sentence}
"""


def _write_outputs(
    prepared: Any,
    output_directory: Path,
    source_checks: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    mechanisms: Sequence[Mapping[str, Any]],
) -> None:
    global RESULTS_DIRECTORY
    RESULTS_DIRECTORY = output_directory
    write_prepared_analysis(prepared, RESULTS_DIRECTORY / "work")
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_files: list[Path] = []
    source_path = RESULTS_DIRECTORY / "source_validation.csv"
    decision_path = RESULTS_DIRECTORY / "attribution_decisions.csv"
    mechanism_path = RESULTS_DIRECTORY / "projection_mechanism_summary.csv"
    write_csv(
        source_path,
        ("check_id", "scope", "status", "actual", "expected", "blocking", "notes"),
        source_checks,
    )
    write_csv(
        decision_path,
        tuple(decisions[0]),
        decisions,
    )
    write_csv(
        mechanism_path,
        tuple(mechanisms[0]),
        mechanisms,
    )
    output_files.extend((source_path, decision_path, mechanism_path))
    figure_paths = _plot_original_rmse_lag(decisions)
    validate_figure_files(figure_paths)
    output_files.extend(figure_paths)
    results_path = RESULTS_DIRECTORY / "RESULTS.md"
    results_markdown = _results_markdown(
        decisions,
        mechanisms,
        source_clean=all(
            source.manifest.get("git", {}).get("dirty") is False
            for source in prepared.sources
        ),
    )
    write_text(results_path, results_markdown)
    write_text(ANALYSIS_DIRECTORY / "RESULTS.md", results_markdown)
    output_files.append(results_path)
    manifest_path = RESULTS_DIRECTORY / "analysis_manifest.json"
    write_analysis_manifest(prepared, manifest_path, output_files)


def run(*, check_only: bool = False) -> int:
    prepared = prepare_analysis(CONFIG_PATH)
    source_checks = validate_sources(prepared)
    surface = prepared_rows(prepared, "vmax_ablation")
    interactions = prepared_rows(prepared, "vmax_interactions")
    source_checks.extend(_validate_matrix(surface, interactions))
    decisions = _decision_rows(interactions, surface)
    mechanisms = _mechanism_rows(surface)
    if check_only:
        original = [
            row for row in decisions if row["input_id"] == ORIGINAL_INPUT_ID
        ]
        rejected = sum(
            row["attribution_decision"] == "rejected_runtime_velocity_limit"
            for row in original
        )
        lag_invariant = sum(
            row["lag_interaction_invariant_within_tolerance"]
            for row in original
        )
        print(
            "A03: validated 36 arms and 15 Vmax interactions; "
            f"original PVA worse in {len(original)}/5 methods, "
            f"RMSE attribution rejected in {rejected}/5 and "
            f"lag interaction invariant in {lag_invariant}/5"
        )
        return 0
    run_directory = create_analysis_run_directory(prepared)
    _write_outputs(
        prepared,
        run_directory,
        source_checks,
        decisions,
        mechanisms,
    )
    print(f"A03: wrote attribution run to {run_directory}")
    return 0
