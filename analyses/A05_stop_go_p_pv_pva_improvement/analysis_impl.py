"""A05 joint P/PV/PVA stop-and-go improvement analysis."""

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
OPERATIONAL_P = "position_zoh_p_ruckig"
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
GUARDRAILS = (
    "profile_velocity_violation_count",
    "profile_acceleration_violation_count",
    "profile_jerk_violation_count",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
)
EQUIVALENCE_METRICS = (
    "rest_to_rest_pulse_fraction",
    "stop_go_event_rate_hz",
    "endpoint_stop_fraction",
    "longest_rest_to_rest_pulse_run_cycles",
    "profile_peak_velocity_to_reference_median",
    "profile_velocity_ripple_median",
    "profile_velocity_ripple_to_reference_median",
    "profile_velocity_ripple_to_reference_p95",
    "position_rmse",
    "lag_s",
)
PRIMARY_STOP_GO_EQUIVALENCE_METRICS = (
    "rest_to_rest_pulse_fraction",
    "stop_go_event_rate_hz",
    "endpoint_stop_fraction",
    "longest_rest_to_rest_pulse_run_cycles",
)


def _truth(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _number(row: Mapping[str, Any], field: str) -> float:
    value = as_float(row.get(field))
    if value is None:
        raise AnalysisValidationError(f"A05 missing finite {field}: {row}")
    return value


def _validate_surface(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    keys = {
        (
            str(row["input_id"]),
            str(row["method_id"]),
            _number(row, "limit_scale"),
        )
        for row in rows
    }
    completed = all(_truth(row.get("completed")) for row in rows)
    exact = all(
        math.isclose(_number(row, "profile_exact_fraction"), 1.0, abs_tol=1e-12)
        for row in rows
    )
    guardrails = all(
        abs(_number(row, metric_id)) <= 1e-12
        for row in rows
        for metric_id in GUARDRAILS
    )
    checks = (
        (
            "complete_joint_surface",
            len(rows) == len(keys) == 960,
            {"rows": len(rows), "unique_keys": len(keys)},
            {"rows": 960, "unique_keys": 960},
            "20 inputs × 12 methods × 4 A/J scales.",
        ),
        (
            "all_runs_completed",
            completed,
            completed,
            True,
            "No prefix metric is admitted.",
        ),
        (
            "exact_profiles_available",
            exact,
            exact,
            True,
            "Stop-and-go metrics use exact sub-cycle profiles.",
        ),
        (
            "hard_guardrails_zero",
            guardrails,
            guardrails,
            True,
            "Constraint, fallback, and solver guardrails.",
        ),
    )
    output = []
    for check_id, passed, actual, expected, notes in checks:
        output.append(
            {
                "check_id": check_id,
                "scope": "e13_joint_stop_go",
                "status": "pass" if passed else "fail",
                "actual": stable_json(actual),
                "expected": stable_json(expected),
                "blocking": "true",
                "notes": notes,
            }
        )
        if not passed:
            raise AnalysisValidationError(f"A05 validation failed: {check_id}")
    return output


def _coordinate_index(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, float, str], Mapping[str, Any]]:
    return {
        (
            str(row["input_id"]),
            _number(row, "limit_scale"),
            str(row["method_id"]),
        ): row
        for row in rows
    }


def _improvement_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    index = _coordinate_index(rows)
    output: list[dict[str, Any]] = []
    for row in rows:
        family = str(row["method_family"])
        if family not in {"PV", "PVA"}:
            continue
        input_id = str(row["input_id"])
        scale = _number(row, "limit_scale")
        baseline = index[(input_id, scale, OPERATIONAL_P)]
        baseline_pulse = _number(baseline, "rest_to_rest_pulse_fraction")
        baseline_rate = _number(baseline, "stop_go_event_rate_hz")
        pulse = _number(row, "rest_to_rest_pulse_fraction")
        rate = _number(row, "stop_go_event_rate_hz")
        baseline_pulse_region = baseline_pulse >= 0.95 - 1e-12
        output.append(
            {
                "input_id": input_id,
                "vendor_velocity_ratio": row["vendor_velocity_ratio"],
                "limit_scale": scale,
                "target_components": family,
                "stencil": row["stencil"],
                "method_id": row["method_id"],
                "baseline_pulse_fraction": baseline_pulse,
                "candidate_pulse_fraction": pulse,
                "pulse_fraction_reduction": baseline_pulse - pulse,
                "baseline_event_rate_hz": baseline_rate,
                "candidate_event_rate_hz": rate,
                "event_rate_reduction_hz": baseline_rate - rate,
                "baseline_pulse_region": baseline_pulse_region,
                "stop_go_eliminated": (
                    pulse <= 1e-12 and rate <= 1e-12
                ),
            }
        )
    return output


def _summary_rows(
    improvements: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in improvements:
        if _truth(row["baseline_pulse_region"]):
            groups[(str(row["target_components"]), str(row["stencil"]))].append(row)
    output = []
    for components in ("PV", "PVA"):
        for stencil in STENCILS:
            selected = groups[(components, stencil)]
            output.append(
                {
                    "target_components": components,
                    "stencil": stencil,
                    "stencil_label": STENCIL_LABELS[stencil],
                    "baseline_pulse_coordinate_count": len(selected),
                    "eliminated_coordinate_count": sum(
                        _truth(row["stop_go_eliminated"]) for row in selected
                    ),
                    "minimum_pulse_fraction_reduction": min(
                        _number(row, "pulse_fraction_reduction") for row in selected
                    ),
                    "maximum_residual_pulse_fraction": max(
                        _number(row, "candidate_pulse_fraction") for row in selected
                    ),
                    "minimum_event_rate_reduction_hz": min(
                        _number(row, "event_rate_reduction_hz") for row in selected
                    ),
                    "maximum_residual_event_rate_hz": max(
                        _number(row, "candidate_event_rate_hz") for row in selected
                    ),
                    "all_baseline_pulses_eliminated": all(
                        _truth(row["stop_go_eliminated"]) for row in selected
                    ),
                }
            )
    return output


def _pv_pva_equivalence_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    index = _coordinate_index(rows)
    coordinates = sorted(
        {
            (str(row["input_id"]), _number(row, "limit_scale"))
            for row in rows
        }
    )
    output = []
    for stencil in STENCILS:
        maximums = {metric_id: 0.0 for metric_id in EQUIVALENCE_METRICS}
        compared = 0
        for input_id, scale in coordinates:
            pv = index[(input_id, scale, f"pv_{stencil}")]
            pva = index[(input_id, scale, f"pva_{stencil}")]
            compared += 1
            for metric_id in EQUIVALENCE_METRICS:
                difference = abs(
                    _number(pva, metric_id) - _number(pv, metric_id)
                )
                maximums[metric_id] = max(maximums[metric_id], difference)
        max_stop_go_difference = max(
            maximums[metric_id]
            for metric_id in PRIMARY_STOP_GO_EQUIVALENCE_METRICS
        )
        max_secondary_difference = max(
            value
            for metric_id, value in maximums.items()
            if metric_id not in PRIMARY_STOP_GO_EQUIVALENCE_METRICS
        )
        output.append(
            {
                "stencil": stencil,
                "stencil_label": STENCIL_LABELS[stencil],
                "paired_coordinate_count": compared,
                **{
                    f"max_abs_difference_{metric_id}": value
                    for metric_id, value in maximums.items()
                },
                "max_abs_difference_stop_go_metrics": max_stop_go_difference,
                "max_abs_difference_secondary_metrics": max_secondary_difference,
                "stop_go_equivalent_within_1e_12": (
                    max_stop_go_difference <= 1e-12
                ),
            }
        )
    return output


def _plot_vendor_response(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    selected_methods = (
        (OPERATIONAL_P, "Operational P", "#D55E00", "-"),
        ("pv_pred_backward_o1_kp1", "PV Future O1", "#4477AA", "--"),
        ("pva_pred_backward_o1_kp1", "PVA Future O1", "#EE6677", ":"),
    )
    figure, axes = plt.subplots(
        2,
        1,
        figsize=(9.8, 8.0),
        sharex=True,
        constrained_layout=True,
    )
    for method_id, label, color, linestyle in selected_methods:
        method_rows = sorted(
            (
                row
                for row in rows
                if row["method_id"] == method_id
                and math.isclose(_number(row, "limit_scale"), 1.0)
            ),
            key=lambda row: _number(row, "vendor_velocity_ratio"),
        )
        x = [_number(row, "vendor_velocity_ratio") for row in method_rows]
        axes[0].plot(
            x,
            [_number(row, "rest_to_rest_pulse_fraction") for row in method_rows],
            marker="o",
            markersize=3.5,
            color=color,
            linestyle=linestyle,
            label=label,
        )
        axes[1].plot(
            x,
            [_number(row, "stop_go_event_rate_hz") for row in method_rows],
            marker="o",
            markersize=3.5,
            color=color,
            linestyle=linestyle,
            label=label,
        )
    axes[0].set_ylabel("Rest-to-rest pulse fraction")
    axes[1].set_ylabel("Stop-and-go event rate (Hz)")
    axes[1].set_xlabel("Reference velocity / vendor P-only critical velocity")
    axes[1].set_xscale("log", base=2)
    for axis in axes:
        axis.axvline(1.0, color="#111827", linestyle="--", linewidth=1.0)
        axis.grid(alpha=0.3)
    axes[0].legend()
    axes[0].set_title("Vendor A/J: P-only stop-and-go and PV/PVA suppression")
    return save_figure(
        figure,
        RESULTS_DIRECTORY / "stop_go_p_pv_pva_vendor_response",
    )


def _results_markdown(
    summaries: Sequence[Mapping[str, Any]],
    equivalence: Sequence[Mapping[str, Any]],
) -> str:
    all_eliminated = all(
        _truth(row["all_baseline_pulses_eliminated"]) for row in summaries
    )
    all_equivalent = all(
        _truth(row["stop_go_equivalent_within_1e_12"]) for row in equivalence
    )
    summary_table = [
        (
            row["target_components"],
            row["stencil_label"],
            row["baseline_pulse_coordinate_count"],
            row["eliminated_coordinate_count"],
            f"{float(row['maximum_residual_pulse_fraction']):.3g}",
            f"{float(row['maximum_residual_event_rate_hz']):.3g}",
        )
        for row in summaries
    ]
    equality_table = [
        (
            row["stencil_label"],
            row["paired_coordinate_count"],
            f"{float(row['max_abs_difference_stop_go_metrics']):.3g}",
            f"{float(row['max_abs_difference_secondary_metrics']):.3g}",
            str(row["stop_go_equivalent_within_1e_12"]).lower(),
        )
        for row in equivalence
    ]
    return f"""# A05 — Stop-and-go：P baseline 对比 PV/PVA

## 结论

在 E07 operational P-only baseline 明确进入 pulse region 的所有坐标上，
五种 PV 和五种 PVA 方法都把 rest-to-rest pulse fraction 与 stop-and-go
event rate 降为 0：**{str(all_eliminated).lower()}**。因此加入 velocity target
对 stop-and-go 的改善得到完整 20-input × 4-limit-scale 矩阵支持。

但成熟窗口是严格 constant velocity，真实和差分 acceleration 都为 0。matched
PV/PVA 在 80 个坐标的四个 primary stop-go 指标上逐 stencil 等价到
`1e-12`：**{str(all_equivalent).lower()}**。secondary tracking/profile
指标保留浮点差分残差，最大差异单独报告，不冒充全指标 bitwise 等价。所以该实验支持的是
**PV（velocity component）解决 P-only stop-and-go**；它不能证明非零
acceleration component 带来额外改善。PVA 与 PV 一样有效，是因为此处 A=0。

## P-only pulse region 的消除结果

{markdown_table(
    ("分量", "差分", "pulse 坐标", "消除数", "最大残余 pulse", "最大残余 Hz"),
    summary_table,
)}

## Matched PV/PVA negative control

{markdown_table(
    ("差分", "配对坐标", "stop-go 最大差", "secondary 最大差", "stop-go 等价"),
    equality_table,
)}

所有 960 arms 完成，exact-profile fraction 为 1，constraint/fallback/solver
guardrails 为零。primary evaluation 使用 `t=0.5–2.5 s`，排除差分启动阶段。
"""


def _write_outputs(
    prepared: Any,
    output_directory: Path,
    checks: Sequence[Mapping[str, Any]],
    improvements: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    equivalence: Sequence[Mapping[str, Any]],
    surface: Sequence[Mapping[str, Any]],
) -> None:
    global RESULTS_DIRECTORY
    RESULTS_DIRECTORY = output_directory
    write_prepared_analysis(prepared, RESULTS_DIRECTORY / "work")
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_files: list[Path] = []
    file_specs = (
        (
            RESULTS_DIRECTORY / "source_validation.csv",
            ("check_id", "scope", "status", "actual", "expected", "blocking", "notes"),
            checks,
        ),
        (
            RESULTS_DIRECTORY / "stop_go_improvements.csv",
            tuple(improvements[0]),
            improvements,
        ),
        (
            RESULTS_DIRECTORY / "method_summary.csv",
            tuple(summaries[0]),
            summaries,
        ),
        (
            RESULTS_DIRECTORY / "matched_pv_pva_equivalence.csv",
            tuple(equivalence[0]),
            equivalence,
        ),
    )
    for path, fields, rows in file_specs:
        write_csv(path, fields, rows)
        output_files.append(path)
    figures = _plot_vendor_response(surface)
    validate_figure_files(figures)
    output_files.extend(figures)
    results_path = RESULTS_DIRECTORY / "RESULTS.md"
    results_markdown = _results_markdown(summaries, equivalence)
    write_text(results_path, results_markdown)
    write_text(ANALYSIS_DIRECTORY / "RESULTS.md", results_markdown)
    output_files.append(results_path)
    manifest_path = RESULTS_DIRECTORY / "analysis_manifest.json"
    write_analysis_manifest(prepared, manifest_path, output_files)


def run(*, check_only: bool = False) -> int:
    prepared = prepare_analysis(CONFIG_PATH)
    checks = validate_sources(prepared)
    surface = prepared_rows(prepared, "joint_stop_go_surface")
    checks.extend(_validate_surface(surface))
    improvements = _improvement_rows(surface)
    summaries = _summary_rows(improvements)
    equivalence = _pv_pva_equivalence_rows(surface)
    if check_only:
        eliminated = sum(
            _truth(row["all_baseline_pulses_eliminated"]) for row in summaries
        )
        equivalent = sum(
            _truth(row["stop_go_equivalent_within_1e_12"])
            for row in equivalence
        )
        print(
            "A05: validated 960 arms; "
            f"{eliminated}/10 PV/PVA methods eliminate baseline pulses and "
            f"{equivalent}/5 matched pairs are equivalent"
        )
        return 0
    run_directory = create_analysis_run_directory(prepared)
    _write_outputs(
        prepared,
        run_directory,
        checks,
        improvements,
        summaries,
        equivalence,
        surface,
    )
    print(f"A05: wrote stop-and-go run to {run_directory}")
    return 0
