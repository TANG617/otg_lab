"""A01 PV-versus-PVA paired analysis implementation."""

from __future__ import annotations

import statistics
from collections import defaultdict
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
    directional_effect,
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

METHOD_PAIRS = (
    (
        "truth_kp1",
        "Truth k+1",
        "e05_pv_truth",
        "pv_truth_kp1",
        "e03_pva_truth",
        "pva_truth_kp1",
    ),
    (
        "est_backward_o1_k",
        "Estimator backward O1",
        "e06_pv_finite_difference",
        "pv_est_backward_o1_k",
        "e04_pva_finite_difference",
        "pva_est_backward_o1_k",
    ),
    (
        "est_backward_o2_k",
        "Estimator backward O2",
        "e06_pv_finite_difference",
        "pv_est_backward_o2_k",
        "e04_pva_finite_difference",
        "pva_est_backward_o2_k",
    ),
    (
        "est_centered_o2_km1",
        "Estimator centered O2",
        "e06_pv_finite_difference",
        "pv_est_centered_o2_km1",
        "e04_pva_finite_difference",
        "pva_est_centered_o2_km1",
    ),
    (
        "pred_backward_o1_kp1",
        "Predictor backward O1",
        "e06_pv_finite_difference",
        "pv_pred_backward_o1_kp1",
        "e04_pva_finite_difference",
        "pva_pred_backward_o1_kp1",
    ),
    (
        "pred_backward_o2_kp1",
        "Predictor backward O2",
        "e06_pv_finite_difference",
        "pv_pred_backward_o2_kp1",
        "e04_pva_finite_difference",
        "pva_pred_backward_o2_kp1",
    ),
)
METHOD_FAMILY_ORDER = tuple(item[0] for item in METHOD_PAIRS)
METHOD_LABELS = {item[0]: item[1] for item in METHOD_PAIRS}

PAIR_FIELDS = (
    "method_family",
    "method_label",
    "input_id",
    "window_id",
    "window_role",
    "metric_id",
    "metric_group",
    "unit",
    "direction",
    "role",
    "pair_status",
    "pv_source_id",
    "pv_method_id",
    "pv_value",
    "pv_status",
    "pv_source_semantics",
    "pv_sample_count",
    "pv_notes",
    "pva_source_id",
    "pva_method_id",
    "pva_value",
    "pva_status",
    "pva_source_semantics",
    "pva_sample_count",
    "pva_notes",
    "delta_pva_minus_pv",
    "improvement",
    "relative_improvement",
    "calculation_status",
)


def _metric_index(
    rows: Sequence[Mapping[str, Any]],
    source_id: str,
    method_id: str,
) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        if row.get("source_id") != source_id or row.get("method_id") != method_id:
            continue
        key = (
            str(row["input_id"]),
            str(row["window_id"]),
            str(row["metric_id"]),
        )
        if key in index:
            raise AnalysisValidationError(
                f"duplicate A01 metric key for {source_id}/{method_id}: {key}"
            )
        index[key] = row
    return index


def _window_role(window_id: str, group: str) -> str:
    if window_id == "main_evaluation":
        return "primary_window"
    if window_id == "full_overlap" and group in {
        "limits",
        "runtime/reliability",
        "smoothness/dynamics",
    }:
        return "whole_run_guardrail_or_diagnostic"
    return "supporting_window"


def build_metric_pairs(
    metric_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for (
        family,
        label,
        pv_source_id,
        pv_method_id,
        pva_source_id,
        pva_method_id,
    ) in METHOD_PAIRS:
        pv_index = _metric_index(metric_rows, pv_source_id, pv_method_id)
        pva_index = _metric_index(metric_rows, pva_source_id, pva_method_id)
        keys = sorted(
            set(pv_index) | set(pva_index),
            key=lambda key: (
                INPUT_ORDER.index(key[0]),
                0 if key[1] == "main_evaluation" else 1,
                key[2],
            ),
        )
        for key in keys:
            pv_row = pv_index.get(key)
            pva_row = pva_index.get(key)
            if pv_row is None or pva_row is None:
                raise AnalysisValidationError(
                    f"incomplete A01 pair for {family}: {key}"
                )
            for field in ("unit", "direction", "role"):
                if pv_row.get(field) != pva_row.get(field):
                    raise AnalysisValidationError(
                        f"A01 {field} mismatch for {family}/{key}: "
                        f"{pv_row.get(field)!r} != {pva_row.get(field)!r}"
                    )
            group = metric_group(key[2])
            pv_value = available_value(pv_row)
            pva_value = available_value(pva_row)
            relative_allowed = not (
                family == "truth_kp1"
                and key[1] == "main_evaluation"
                and key[2] == "position_rmse"
            )
            delta, improvement, relative = directional_effect(
                pv_value,
                pva_value,
                str(pv_row.get("direction", "none")),
                relative_allowed=relative_allowed,
            )
            if pv_value is None or pva_value is None:
                calculation_status = "unavailable_value"
            elif pv_row.get("direction") == "none":
                calculation_status = "delta_only_direction_none"
            elif not relative_allowed:
                calculation_status = "absolute_only_truth_near_zero"
            elif pv_value == 0.0:
                calculation_status = "absolute_only_zero_denominator"
            else:
                calculation_status = "available"
            pairs.append(
                {
                    "method_family": family,
                    "method_label": label,
                    "input_id": key[0],
                    "window_id": key[1],
                    "window_role": _window_role(key[1], group),
                    "metric_id": key[2],
                    "metric_group": group,
                    "unit": pv_row.get("unit", ""),
                    "direction": pv_row.get("direction", ""),
                    "role": pv_row.get("role", ""),
                    "pair_status": "paired",
                    "pv_source_id": pv_source_id,
                    "pv_method_id": pv_method_id,
                    "pv_value": "" if pv_value is None else pv_value,
                    "pv_status": pv_row.get("status", ""),
                    "pv_source_semantics": pv_row.get("source_semantics", ""),
                    "pv_sample_count": pv_row.get("sample_count", ""),
                    "pv_notes": pv_row.get("notes", ""),
                    "pva_source_id": pva_source_id,
                    "pva_method_id": pva_method_id,
                    "pva_value": "" if pva_value is None else pva_value,
                    "pva_status": pva_row.get("status", ""),
                    "pva_source_semantics": pva_row.get(
                        "source_semantics",
                        "",
                    ),
                    "pva_sample_count": pva_row.get("sample_count", ""),
                    "pva_notes": pva_row.get("notes", ""),
                    "delta_pva_minus_pv": "" if delta is None else delta,
                    "improvement": "" if improvement is None else improvement,
                    "relative_improvement": ("" if relative is None else relative),
                    "calculation_status": calculation_status,
                }
            )
    return pairs


def build_primary_position_pairs(
    pair_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in pair_rows
        if row["window_id"] == "main_evaluation" and row["metric_id"] == "position_rmse"
    ]
    rows.sort(
        key=lambda row: (
            METHOD_FAMILY_ORDER.index(str(row["method_family"])),
            INPUT_ORDER.index(str(row["input_id"])),
        )
    )
    if len(rows) != 18:
        raise AnalysisValidationError(
            f"A01 primary position table must contain 18 rows, found {len(rows)}"
        )
    return rows


def build_pair_summary(
    pair_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        grouped[
            (
                str(row["method_family"]),
                str(row["window_id"]),
                str(row["metric_id"]),
            )
        ].append(row)
    results: list[dict[str, Any]] = []
    for (family, window_id, metric_id), rows in sorted(
        grouped.items(),
        key=lambda item: (
            METHOD_FAMILY_ORDER.index(item[0][0]),
            0 if item[0][1] == "main_evaluation" else 1,
            item[0][2],
        ),
    ):
        available = [
            row
            for row in rows
            if as_float(row["pv_value"]) is not None
            and as_float(row["pva_value"]) is not None
        ]
        pv_values = [float(row["pv_value"]) for row in available]
        pva_values = [float(row["pva_value"]) for row in available]
        improvements = [
            value
            for value in (as_float(row["improvement"]) for row in available)
            if value is not None
        ]
        results.append(
            {
                "method_family": family,
                "method_label": METHOD_LABELS[family],
                "window_id": window_id,
                "metric_id": metric_id,
                "metric_group": rows[0]["metric_group"],
                "unit": rows[0]["unit"],
                "direction": rows[0]["direction"],
                "paired_input_count": len(available),
                "expected_input_count": len(INPUT_ORDER),
                "pv_mean": ("" if not pv_values else statistics.fmean(pv_values)),
                "pva_mean": ("" if not pva_values else statistics.fmean(pva_values)),
                "mean_delta_pva_minus_pv": (
                    ""
                    if not available
                    else statistics.fmean(
                        float(row["delta_pva_minus_pv"]) for row in available
                    )
                ),
                "mean_improvement": (
                    "" if not improvements else statistics.fmean(improvements)
                ),
                "median_improvement": (
                    "" if not improvements else statistics.median(improvements)
                ),
                "min_improvement": ("" if not improvements else min(improvements)),
                "max_improvement": ("" if not improvements else max(improvements)),
            }
        )
    return results


def build_metric_group_summary(
    pair_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        grouped[(str(row["metric_group"]), str(row["window_id"]))].append(row)
    results: list[dict[str, Any]] = []
    for (group, window_id), rows in sorted(grouped.items()):
        improvements = [as_float(row["improvement"]) for row in rows]
        comparable = [value for value in improvements if value is not None]
        results.append(
            {
                "metric_group": group,
                "window_id": window_id,
                "metric_count": len({str(row["metric_id"]) for row in rows}),
                "pair_row_count": len(rows),
                "available_pair_count": sum(
                    as_float(row["pv_value"]) is not None
                    and as_float(row["pva_value"]) is not None
                    for row in rows
                ),
                "unavailable_pair_count": sum(
                    as_float(row["pv_value"]) is None
                    or as_float(row["pva_value"]) is None
                    for row in rows
                ),
                "directional_comparison_count": len(comparable),
                "pva_better_count": sum(value > 0.0 for value in comparable),
                "pv_better_count": sum(value < 0.0 for value in comparable),
                "equal_count": sum(value == 0.0 for value in comparable),
                "direction_none_count": sum(row["direction"] == "none" for row in rows),
            }
        )
    return results


def build_guardrail_summary(
    pair_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    guardrail_ids = {
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
    }
    rows = [
        dict(row)
        for row in pair_rows
        if row["window_id"] == "full_overlap" and row["metric_id"] in guardrail_ids
    ]
    rows.sort(
        key=lambda row: (
            METHOD_FAMILY_ORDER.index(str(row["method_family"])),
            INPUT_ORDER.index(str(row["input_id"])),
            str(row["metric_id"]),
        )
    )
    return rows


def _format_scientific(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "NA"
    if number == 0.0:
        return "0"
    if abs(number) < 1e-3 or abs(number) >= 1e3:
        return f"{number:.3e}"
    return f"{number:.6f}"


def _plot_position_pairs(
    primary_rows: Sequence[Mapping[str, Any]],
    results_directory: Path,
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(
        1,
        len(INPUT_ORDER),
        figsize=(15.5, 6.6),
        sharey=True,
    )
    y_positions = np.arange(len(METHOD_FAMILY_ORDER), dtype=float)
    for axis, input_id in zip(axes, INPUT_ORDER):
        by_family = {
            str(row["method_family"]): row
            for row in primary_rows
            if row["input_id"] == input_id
        }
        for y_position, family in zip(y_positions, METHOD_FAMILY_ORDER):
            row = by_family[family]
            pv_value = float(row["pv_value"])
            pva_value = float(row["pva_value"])
            axis.plot(
                [pv_value, pva_value],
                [y_position, y_position],
                color="#9CA3AF",
                linewidth=1.2,
                zorder=1,
            )
            axis.scatter(
                pv_value,
                y_position,
                color="#2563EB",
                edgecolor="#1E3A8A",
                marker="o",
                s=48,
                label="PV" if y_position == 0 else None,
                zorder=2,
            )
            axis.scatter(
                pva_value,
                y_position,
                facecolor="#FFF7ED",
                edgecolor="#C56A1A",
                marker="s",
                s=48,
                label="PVA" if y_position == 0 else None,
                zorder=2,
            )
        axis.set_xscale("log")
        axis.grid(axis="x")
        axis.set_title(input_id)
        axis.set_xlabel("Position RMSE (rad, log scale)")
        axis.set_yticks(y_positions)
        axis.set_yticklabels([METHOD_LABELS[family] for family in METHOD_FAMILY_ORDER])
        axis.invert_yaxis()
    axes[0].legend(loc="upper left", bbox_to_anchor=(0.0, 1.18), ncol=2)
    figure.subplots_adjust(
        left=0.18,
        right=0.985,
        bottom=0.19,
        top=0.79,
        wspace=0.04,
    )
    figure.suptitle(
        "PV 与 PVA 的逐轨迹 position RMSE 配对",
        fontsize=15,
        y=0.96,
    )
    figure.text(
        0.5,
        0.035,
        "main_evaluation 0.04–3.00 s；连线仅表示同方法族配对，不表示跨方法排名",
        ha="center",
        color="#4B5563",
    )
    return save_figure(
        figure,
        results_directory / "pv_pva_position_rmse",
    )


def _plot_effect_heatmap(
    primary_rows: Sequence[Mapping[str, Any]],
    results_directory: Path,
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    families = METHOD_FAMILY_ORDER[1:]
    matrix = np.empty((len(families), len(INPUT_ORDER)), dtype=float)
    for row_index, family in enumerate(families):
        for column_index, input_id in enumerate(INPUT_ORDER):
            row = next(
                item
                for item in primary_rows
                if item["method_family"] == family and item["input_id"] == input_id
            )
            value = as_float(row["relative_improvement"])
            if value is None:
                raise AnalysisValidationError(
                    f"missing A01 FD relative improvement for {family}/{input_id}"
                )
            matrix[row_index, column_index] = 100.0 * value
    maximum = max(1e-12, float(np.max(np.abs(matrix))))
    colormap = LinearSegmentedColormap.from_list(
        "a01_diverging",
        ["#C56A1A", "#F9FAFB", "#2563EB"],
    )
    norm = TwoSlopeNorm(vmin=-maximum, vcenter=0.0, vmax=maximum)
    figure, axis = plt.subplots(figsize=(10.5, 6.4))
    image = axis.imshow(matrix, cmap=colormap, norm=norm, aspect="auto")
    axis.set_xticks(np.arange(len(INPUT_ORDER)))
    axis.set_xticklabels(INPUT_ORDER, rotation=16, ha="right")
    axis.set_yticks(np.arange(len(families)))
    axis.set_yticklabels([METHOD_LABELS[family] for family in families])
    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = matrix[row_index, column_index]
            axis.text(
                column_index,
                row_index,
                f"{value:+.2f}%",
                ha="center",
                va="center",
                color="#111827",
                fontsize=9,
            )
    colorbar = figure.colorbar(image, ax=axis, shrink=0.82)
    colorbar.set_label("PVA relative improvement vs PV (%)")
    axis.set_title("PVA 对同族有限差分 position RMSE 的影响")
    axis.set_xlabel("Trajectory")
    axis.set_ylabel("Finite-difference family")
    figure.subplots_adjust(
        left=0.22,
        right=0.88,
        bottom=0.23,
        top=0.88,
    )
    figure.text(
        0.5,
        0.035,
        "正值表示 PVA 的 raw-time RMSE 更低；truth 不进入相对变化图",
        ha="center",
        color="#4B5563",
    )
    return save_figure(
        figure,
        results_directory / "pva_effect_on_position_rmse",
    )


def _build_results_markdown(
    primary_rows: Sequence[Mapping[str, Any]],
    group_summary: Sequence[Mapping[str, Any]],
) -> str:
    table_rows = []
    for row in primary_rows:
        table_rows.append(
            (
                row["method_label"],
                row["input_id"],
                _format_scientific(row["pv_value"]),
                _format_scientific(row["pva_value"]),
                _format_scientific(row["delta_pva_minus_pv"]),
                _format_scientific(row["improvement"]),
            )
        )
    domain_rows = [
        (
            row["metric_group"],
            row["window_id"],
            row["metric_count"],
            row["available_pair_count"],
            row["unavailable_pair_count"],
        )
        for row in group_summary
    ]
    return f"""# A01 — 解析轨迹 PV/PVA 配对正确性审计

> 证据角色：仅用于中间方法正确性验证，不参与 recorded trajectory
> 的上线 PV/PVA 选型，也不能据此声明收益。

## 技术摘要

- 本分析只检查解析轨迹上，同一 estimator/predictor、输入、约束和 follower
  下加入 acceleration target（PV → PVA）后的配对行为。
- 证据包含 1 组 truth 配对和 5 组同名有限差分配对；三条解析轨迹均按
  `input_id` 独立保留。
- E01 的独立 `p_kp1_baseline` 只用于复现审计；E03–E06 内部 baseline
  继续作为同次运行的配对坐标，E01 不增加样本量。
- A01 不进行上线方法排名，不评价有限差分与 truth ceiling 的距离，也不向
  recorded trajectory 外推。
- 来源 run 均 completed 且记录同一 commit，但 manifest 标记
  `git.dirty=true`，因此结论应作为可审计的固定结果分析，而不是 clean-build
  完全复现证明。

## Position RMSE 的逐输入配对

主窗口为 `main_evaluation = 0.04–3.00 s`。`improvement` 已按
lower-is-better 方向计算为 `PV - PVA`；正值表示同一方法族中 PVA 的 RMSE
更低。Truth 数值接近机器精度，因此不报告不稳定的相对比值。

{
        markdown_table(
            ("方法族", "轨迹", "PV RMSE", "PVA RMSE", "PVA-PV", "改善"),
            table_rows,
        )
    }

## 全指标审计

完整逐指标结果见 `results/pv_pva_metric_pairs.csv`。正文按指标语义分组，
而不是将不同单位或角色压缩成一个总分。

{
        markdown_table(
            ("指标域", "窗口", "指标数", "可比较行", "不可用行"),
            domain_rows,
        )
    }

## Guardrail 与缺失通道

`results/guardrail_summary.csv` 保留 limit violation、profile constraint、
fallback、solver failure、deadline miss 以及 jerk channel 的原始状态。输出
command jerk 不可用的行继续标记为 unavailable，不解释为“零违规”。

## 图表

- `results/pv_pva_position_rmse.png/.svg`：六个方法族的逐轨迹 PV/PVA
  配对点图，使用对数轴。
- `results/pva_effect_on_position_rmse.png/.svg`：五组有限差分的
  direction-aware RMSE 相对变化；正值表示 PVA 优于 PV。

图表只展示配对效应，不构成跨方法排名。

## 限制

- 只有三条单轴、平滑、无噪声、100 Hz 解析轨迹，不计算 p-value、置信区间
  或统计推广。
- 本分析不是上线证据；上线比较只允许使用 velocity-limit recorded
  trajectory。
- `full_overlap` 仅用于 whole-run guardrail/diagnostic；tracking 主结论使用
  `main_evaluation`。
- Truth RMSE 接近数值精度，只比较绝对值和绝对差。
- 当前来源来自 dirty worktree。

## 复现

```bash
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py --check
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py
```
"""


def _build_validation_markdown(
    source_rows: Sequence[Mapping[str, Any]],
    pair_rows: Sequence[Mapping[str, Any]],
    primary_rows: Sequence[Mapping[str, Any]],
    figure_paths: Sequence[Path],
) -> str:
    dirty_failures = [
        row
        for row in source_rows
        if row["check_id"] == "git_dirty" and row["status"] == "fail"
    ]
    unavailable = sum(
        as_float(row["pv_value"]) is None or as_float(row["pva_value"]) is None
        for row in pair_rows
    )
    return f"""# A01 Validation

## Overall Assessment: Share with caveats

### Methodology Review

- 六个声明方法族均按 `method_family + input_id + window_id + metric_id`
  完整配对。
- `primary_position_pairs.csv` 已验证为 {len(primary_rows)} 行。
- direction-aware delta、improvement 和相对值均由逐输入原值重算。
- 不可用 pair 行数：{unavailable}；这些行保留状态，不被填零。

### Source Checks

- 来源审计行数：{len(source_rows)}。
- dirty-source caveat 数：{len(dirty_failures)}。
- E01 与 E03–E06 的独立 P baseline、E03/E04 与 E05/E06 的重复
  baseline/truth 均已作一致性检查。

### Visualization Review

- 已生成并验证 {len(figure_paths)} 个 PNG/SVG 文件。
- RMSE 配对图使用对数轴；热图以零为中心并直接标注带符号百分比。
- 图表不使用跨方法排名标题或排序。

### Required Caveats

- 三条确定性解析轨迹不支持统计推广。
- 来源 manifest 记录 dirty worktree。
- unavailable jerk channel 不是零违规。
"""


def _build_chart_map_markdown() -> str:
    return """# A01 Chart Map

| 图表 | 分析问题 | 图形 | 字段 | 色彩与非色彩编码 | 用途 |
|---|---|---|---|---|---|
| `pv_pva_position_rmse` | 同一方法族内 PV/PVA 的 raw-time RMSE 如何变化？ | 三分面 paired dot plot，log x | method_family, input_id, pv_value, pva_value | PV 蓝色圆点；PVA 橙色空心方点；灰色连线 | 报告核心配对证据 |
| `pva_effect_on_position_rmse` | 五组 FD 中 PVA 相对 PV 的方向和幅度是什么？ | 发散热图 | method_family, input_id, relative_improvement | 橙—中性—蓝；每格直接标注符号百分比 | 展示配对效应，不作排名 |

两图均使用 `main_evaluation = 0.04–3.00 s`，输出 PNG/SVG。
"""


def _write_outputs(
    prepared: Any,
    output_directory: Path,
    source_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
    primary_rows: list[dict[str, Any]],
    pair_summary: list[dict[str, Any]],
    group_summary: list[dict[str, Any]],
    guardrail_rows: list[dict[str, Any]],
) -> None:
    global RESULTS_DIRECTORY
    RESULTS_DIRECTORY = output_directory
    write_prepared_analysis(prepared, RESULTS_DIRECTORY / "work")
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_files: list[Path] = []

    file_specs = (
        (
            RESULTS_DIRECTORY / "pv_pva_metric_pairs.csv",
            PAIR_FIELDS,
            pair_rows,
        ),
        (
            RESULTS_DIRECTORY / "primary_position_pairs.csv",
            PAIR_FIELDS,
            primary_rows,
        ),
        (
            RESULTS_DIRECTORY / "pair_summary.csv",
            tuple(pair_summary[0]),
            pair_summary,
        ),
        (
            RESULTS_DIRECTORY / "metric_group_summary.csv",
            tuple(group_summary[0]),
            group_summary,
        ),
        (
            RESULTS_DIRECTORY / "guardrail_summary.csv",
            PAIR_FIELDS,
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
        *_plot_position_pairs(primary_rows, RESULTS_DIRECTORY),
        *_plot_effect_heatmap(primary_rows, RESULTS_DIRECTORY),
    ]
    validate_figure_files(figure_paths)
    output_files.extend(figure_paths)

    results_path = RESULTS_DIRECTORY / "RESULTS.md"
    results_markdown = _build_results_markdown(primary_rows, group_summary)
    validation_path = RESULTS_DIRECTORY / "validation.md"
    chart_map_path = RESULTS_DIRECTORY / "chart_map.md"
    write_text(results_path, results_markdown)
    write_text(ANALYSIS_DIRECTORY / "RESULTS.md", results_markdown)
    write_text(
        validation_path,
        _build_validation_markdown(
            source_rows,
            pair_rows,
            primary_rows,
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
        "e06_pv_finite_difference",
    ):
        baseline_rows.extend(
            compare_duplicate_methods(
                metric_rows,
                left_source_id="e01_p_only_baseline",
                right_source_id=source_id,
                method_ids=("p_kp1_baseline",),
                excluded_metric_prefixes=("runtime_", "deadline_"),
            )
        )
    source_rows.extend(baseline_rows)
    source_rows.extend(
        compare_duplicate_methods(
            metric_rows,
            left_source_id="e03_pva_truth",
            right_source_id="e04_pva_finite_difference",
            method_ids=("p_kp1_baseline", "pva_truth_kp1"),
            excluded_metric_prefixes=("runtime_",),
        )
    )
    source_rows.extend(
        compare_duplicate_methods(
            metric_rows,
            left_source_id="e05_pv_truth",
            right_source_id="e06_pv_finite_difference",
            method_ids=("p_kp1_baseline", "pv_truth_kp1"),
            excluded_metric_prefixes=("runtime_",),
        )
    )
    pair_rows = build_metric_pairs(metric_rows)
    primary_rows = build_primary_position_pairs(pair_rows)
    pair_summary = build_pair_summary(pair_rows)
    group_summary = build_metric_group_summary(pair_rows)
    guardrail_rows = build_guardrail_summary(pair_rows)

    if check_only:
        print(
            "A01: validated 5 pinned sources, 4 independent baseline checks, "
            "6 method families, "
            f"{len(pair_rows)} metric pairs, and 18 primary position pairs"
        )
        return 0

    run_directory = create_analysis_run_directory(prepared)
    _write_outputs(
        prepared,
        run_directory,
        source_rows,
        baseline_rows,
        pair_rows,
        primary_rows,
        pair_summary,
        group_summary,
        guardrail_rows,
    )
    print(f"A01: wrote paired analysis run to {run_directory}")
    return 0
