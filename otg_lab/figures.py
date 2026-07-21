"""Deterministic, data-complete figures for the OTG evidence artifacts.

All plotting functions sort categorical inputs and use fixed styles.  Figure
metadata excludes timestamps and SVG object identifiers use a fixed hash salt,
so identical records produce byte-identical files on the same pinned stack.
Representative traces are selected by the predeclared median/P90/worst rule or
by IDs supplied before results are inspected; there is no visual cherry-pick
API.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REQUIRED_FIGURE_CATEGORIES = (
    "estimator_accuracy_latency_compute_pareto",
    "prediction_error_vs_horizon",
    "same_information_p_pv_pva_ablation",
    "acceleration_value_phase_map",
    "governor_distortion_reachability",
    "direct_governor_vs_governor_ruckig",
    "robustness_matrix",
    "sampling_rate_study",
    "continuous_vs_sampled_jerk",
    "multidof_scalability",
    "plant_feedback_comparison",
    "runtime_distributions",
    "paired_improvement_confidence_intervals",
    "representative_traces",
)

_PALETTE = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#000000",
)


class FigureValidationError(ValueError):
    """Raised when a figure would conceal missing or non-finite results."""


def _records(
    records: Sequence[Mapping[str, Any]], required: Sequence[str], name: str
) -> list[Mapping[str, Any]]:
    if not records:
        raise FigureValidationError(f"{name} table is empty")
    required_set = set(required)
    result = list(records)
    for index, row in enumerate(result):
        missing = required_set - set(row)
        if missing:
            raise FigureValidationError(
                f"{name} row {index} is missing {sorted(missing)}"
            )
        for field in required:
            value = row[field]
            if isinstance(value, (float, np.floating)) and not math.isfinite(
                float(value)
            ):
                raise FigureValidationError(
                    f"{name} row {index}.{field} is NaN or infinity"
                )
            if value is None:
                raise FigureValidationError(f"{name} row {index}.{field} is missing")
    return result


def _numeric(row: Mapping[str, Any], field: str) -> float:
    try:
        value = float(row[field])
    except (TypeError, ValueError) as error:
        raise FigureValidationError(f"field {field} is not numeric") from error
    if not math.isfinite(value):
        raise FigureValidationError(f"field {field} is NaN or infinity")
    return value


@contextmanager
def deterministic_style() -> Iterator[None]:
    """Apply the fixed publication-artifact style without global side effects."""

    settings = {
        "figure.dpi": 120,
        "savefig.dpi": 160,
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "svg.hashsalt": "otg-lab-paper-evidence-v1",
        "path.simplify": False,
    }
    with plt.rc_context(settings):
        yield


def save_deterministic_figure(
    figure: matplotlib.figure.Figure,
    output: str | Path,
    *,
    close: bool = True,
) -> tuple[Path, Path]:
    """Save matching PNG/SVG files with stable metadata."""

    target = Path(output)
    stem = target.with_suffix("") if target.suffix else target
    png = stem.with_suffix(".png")
    svg = stem.with_suffix(".svg")
    png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        png,
        format="png",
        bbox_inches="tight",
        metadata={"Software": "otg_lab", "Creation Time": None},
    )
    figure.savefig(
        svg,
        format="svg",
        bbox_inches="tight",
        metadata={"Creator": "otg_lab", "Date": None},
    )
    if close:
        plt.close(figure)
    return png, svg


def select_representative_trajectories(
    trajectory_metrics: Sequence[Mapping[str, Any]],
    *,
    metric: str = "position_rmse",
    trajectory_field: str = "trajectory_id",
    method_field: str = "method",
    ranking_method: str | None = None,
    predefined_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Select predefined IDs plus median, P90, and worst trajectory cases.

    Quantile representatives minimize distance to the fixed linear quantile;
    ties use lexicographic trajectory ID.  Results are kept distinct whenever
    enough trajectories exist.  Duplicate rows for a trajectory are rejected.
    """

    required = [trajectory_field, metric]
    if ranking_method is not None:
        required.append(method_field)
    rows = _records(trajectory_metrics, required, "trajectory metrics")
    if ranking_method is not None:
        rows = [row for row in rows if str(row[method_field]) == ranking_method]
        if not rows:
            raise FigureValidationError(
                f"ranking method {ranking_method!r} has no trajectories"
            )
    indexed: dict[str, float] = {}
    for row in rows:
        trajectory_id = str(row[trajectory_field])
        if trajectory_id in indexed:
            raise FigureValidationError(
                f"duplicate trajectory metric row for {trajectory_id!r}"
            )
        indexed[trajectory_id] = _numeric(row, metric)
    unknown = [
        trajectory_id
        for trajectory_id in predefined_ids
        if trajectory_id not in indexed
    ]
    if unknown:
        raise FigureValidationError(f"predefined trajectory IDs are absent: {unknown}")
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for trajectory_id in predefined_ids:
        if trajectory_id in used:
            raise FigureValidationError(
                f"predefined trajectory ID is duplicated: {trajectory_id}"
            )
        used.add(trajectory_id)
        selected.append(
            {
                "trajectory_id": trajectory_id,
                "selection_reason": "predefined_id",
                "selection_quantile": None,
                "ranking_metric": metric,
                "ranking_value": indexed[trajectory_id],
                "ranking_method": ranking_method,
            }
        )

    values = np.asarray(list(indexed.values()), dtype=float)
    rules = (("median", 0.5), ("p90", 0.9), ("worst", 1.0))
    for reason, quantile in rules:
        target = float(np.quantile(values, quantile, method="linear"))
        available = [item for item in indexed.items() if item[0] not in used]
        if not available:
            available = list(indexed.items())
        if reason == "worst":
            best_value = max(value for _, value in available)
            trajectory_id, value = min(
                (item for item in available if item[1] == best_value),
                key=lambda item: item[0],
            )
        else:
            trajectory_id, value = min(
                available,
                key=lambda item: (abs(item[1] - target), item[0]),
            )
        used.add(trajectory_id)
        selected.append(
            {
                "trajectory_id": trajectory_id,
                "selection_reason": reason,
                "selection_quantile": quantile,
                "ranking_metric": metric,
                "ranking_value": value,
                "ranking_method": ranking_method,
            }
        )
    return selected


def _aggregate(
    records: Sequence[Mapping[str, Any]],
    group_fields: Sequence[str],
    value_field: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for row in records:
        grouped[tuple(str(row[field]) for field in group_fields)].append(
            _numeric(row, value_field)
        )
    return [
        {
            **dict(zip(group_fields, key)),
            value_field: float(np.mean(values)),
            f"{value_field}_q25": float(np.quantile(values, 0.25, method="linear")),
            f"{value_field}_q75": float(np.quantile(values, 0.75, method="linear")),
            "n_trajectories": len(values),
        }
        for key, values in sorted(grouped.items())
    ]


def _plot_grouped_lines(
    records: Sequence[Mapping[str, Any]],
    *,
    x_field: str,
    y_field: str,
    series_field: str,
    xlabel: str,
    ylabel: str,
    title: str,
    output: str | Path,
) -> tuple[Path, Path]:
    rows = _records(records, [x_field, y_field, series_field], title)
    aggregated = _aggregate(rows, (series_field, x_field), y_field)
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in aggregated:
        groups[str(row[series_field])].append(row)
    with deterministic_style():
        figure, axis = plt.subplots(figsize=(6.4, 3.8), constrained_layout=True)
        for color_index, series in enumerate(sorted(groups)):
            series_rows = sorted(groups[series], key=lambda row: float(row[x_field]))
            x = np.asarray([float(row[x_field]) for row in series_rows])
            y = np.asarray([float(row[y_field]) for row in series_rows])
            low = np.asarray([float(row[f"{y_field}_q25"]) for row in series_rows])
            high = np.asarray([float(row[f"{y_field}_q75"]) for row in series_rows])
            color = _PALETTE[color_index % len(_PALETTE)]
            axis.plot(x, y, marker="o", color=color, label=series)
            axis.fill_between(x, low, high, color=color, alpha=0.14, linewidth=0)
        axis.set(xlabel=xlabel, ylabel=ylabel, title=title)
        axis.legend(frameon=False)
        return save_deterministic_figure(figure, output)


def _plot_complete_heatmap(
    records: Sequence[Mapping[str, Any]],
    *,
    row_field: str,
    column_field: str,
    value_field: str,
    title: str,
    colorbar_label: str,
    output: str | Path,
) -> tuple[Path, Path]:
    rows = _records(records, [row_field, column_field, value_field], title)
    aggregated = _aggregate(rows, (row_field, column_field), value_field)
    row_values = sorted({str(row[row_field]) for row in aggregated})
    column_values = sorted(
        {str(row[column_field]) for row in aggregated},
        key=lambda value: (float(value) if _is_float(value) else math.inf, value),
    )
    lookup = {
        (str(row[row_field]), str(row[column_field])): float(row[value_field])
        for row in aggregated
    }
    missing = [
        (row_value, column_value)
        for row_value in row_values
        for column_value in column_values
        if (row_value, column_value) not in lookup
    ]
    if missing:
        raise FigureValidationError(
            f"{title} matrix has missing cells; first missing={missing[0]}"
        )
    matrix = np.asarray(
        [
            [lookup[(row_value, column_value)] for column_value in column_values]
            for row_value in row_values
        ]
    )
    with deterministic_style():
        width = max(5.0, 0.55 * len(column_values) + 2.0)
        height = max(3.5, 0.42 * len(row_values) + 1.5)
        figure, axis = plt.subplots(figsize=(width, height), constrained_layout=True)
        image = axis.imshow(matrix, aspect="auto", cmap="viridis")
        axis.set_xticks(
            np.arange(len(column_values)), column_values, rotation=35, ha="right"
        )
        axis.set_yticks(np.arange(len(row_values)), row_values)
        axis.set(xlabel=column_field, ylabel=row_field, title=title)
        colorbar = figure.colorbar(image, ax=axis)
        colorbar.set_label(colorbar_label)
        return save_deterministic_figure(figure, output)


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def plot_estimator_pareto(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(
        records,
        ["method", "estimator_p_rmse", "posterior_lag_s", "estimator_p99_us"],
        "estimator Pareto",
    )
    aggregated = _aggregate(rows, ("method",), "estimator_p_rmse")
    lag = {
        row["method"]: row["posterior_lag_s"]
        for row in _aggregate(rows, ("method",), "posterior_lag_s")
    }
    compute = {
        row["method"]: row["estimator_p99_us"]
        for row in _aggregate(rows, ("method",), "estimator_p99_us")
    }
    with deterministic_style():
        figure, axes = plt.subplots(1, 2, figsize=(8.2, 3.6), constrained_layout=True)
        for index, row in enumerate(aggregated):
            method = str(row["method"])
            color = _PALETTE[index % len(_PALETTE)]
            y = float(row["estimator_p_rmse"])
            axes[0].scatter(float(lag[method]), y, color=color)
            axes[1].scatter(float(compute[method]), y, color=color)
            axes[0].annotate(
                method,
                (float(lag[method]), y),
                xytext=(3, 3),
                textcoords="offset points",
            )
            axes[1].annotate(
                method,
                (float(compute[method]), y),
                xytext=(3, 3),
                textcoords="offset points",
            )
        axes[0].set(
            xlabel="posterior lag (s)",
            ylabel="position RMSE",
            title="Accuracy vs latency",
        )
        axes[1].set(
            xlabel="estimator P99 (µs)",
            ylabel="position RMSE",
            title="Accuracy vs compute",
        )
        return save_deterministic_figure(figure, output)


def plot_prediction_error_vs_horizon(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    return _plot_grouped_lines(
        records,
        x_field="prediction_horizon_ms",
        y_field="prediction_p_rmse",
        series_field="method",
        xlabel="prediction horizon (ms)",
        ylabel="future position RMSE",
        title="Prediction error at physical future time",
        output=output,
    )


def plot_same_information_ablation(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(records, ["method", "position_rmse"], "P/PV/PVA ablation")
    aggregated = _aggregate(rows, ("method",), "position_rmse")
    methods = [str(row["method"]) for row in aggregated]
    values = [float(row["position_rmse"]) for row in aggregated]
    low = [float(row["position_rmse_q25"]) for row in aggregated]
    high = [float(row["position_rmse_q75"]) for row in aggregated]
    with deterministic_style():
        figure, axis = plt.subplots(figsize=(6.4, 3.8), constrained_layout=True)
        x = np.arange(len(methods))
        axis.bar(x, values, color=[_PALETTE[index % len(_PALETTE)] for index in x])
        # The bar is the arithmetic mean while the interval is the IQR.  For
        # skewed trajectory distributions the mean can legitimately lie
        # outside the IQR, so centering yerr on the mean would both fail and
        # misstate the interval.  Preserve the exact quartile endpoints.
        interval_center = (np.asarray(low) + np.asarray(high)) / 2.0
        interval_half_width = (np.asarray(high) - np.asarray(low)) / 2.0
        axis.errorbar(
            x,
            interval_center,
            yerr=interval_half_width,
            fmt="none",
            color="black",
            capsize=3,
        )
        axis.set_xticks(x, methods, rotation=25, ha="right")
        axis.set(
            ylabel="raw-time position RMSE",
            title="Same-information target-state ablation",
        )
        return save_deterministic_figure(figure, output)


def plot_acceleration_phase_map(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    return _plot_complete_heatmap(
        records,
        row_field="r_j",
        column_field="r_a",
        value_field="pva_vs_pv_rmse_improvement",
        title="Independent acceleration value phase map",
        colorbar_label="PVA vs PV relative RMSE improvement",
        output=output,
    )


def plot_governor_distortion_reachability(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(
        records,
        ["method", "governor_position_distortion_rmse", "one_step_reachable_rate"],
        "governor distortion",
    )
    distortion = _aggregate(rows, ("method",), "governor_position_distortion_rmse")
    reachability = {
        row["method"]: row["one_step_reachable_rate"]
        for row in _aggregate(rows, ("method",), "one_step_reachable_rate")
    }
    with deterministic_style():
        figure, axis = plt.subplots(figsize=(6.0, 3.8), constrained_layout=True)
        for index, row in enumerate(distortion):
            method = str(row["method"])
            x = float(row["governor_position_distortion_rmse"])
            y = float(reachability[method])
            axis.scatter(x, y, color=_PALETTE[index % len(_PALETTE)])
            axis.annotate(method, (x, y), xytext=(4, 3), textcoords="offset points")
        axis.set(
            xlabel="position distortion RMSE",
            ylabel="one-step reachable rate",
            title="Governor distortion and reachability",
            ylim=(-0.02, 1.02),
        )
        return save_deterministic_figure(figure, output)


def plot_direct_vs_ruckig(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(
        records,
        ["trajectory_id", "follower", "position_rmse"],
        "direct governor comparison",
    )
    followers = sorted({str(row["follower"]) for row in rows})
    if len(followers) != 2:
        raise FigureValidationError(
            "direct-vs-Ruckig figure requires exactly two followers"
        )
    paired: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        trajectory = str(row["trajectory_id"])
        follower = str(row["follower"])
        if follower in paired[trajectory]:
            raise FigureValidationError("duplicate trajectory/follower row")
        paired[trajectory][follower] = _numeric(row, "position_rmse")
    if any(set(values) != set(followers) for values in paired.values()):
        raise FigureValidationError("direct-vs-Ruckig trajectory pairs are incomplete")
    with deterministic_style():
        figure, axis = plt.subplots(figsize=(5.2, 3.8), constrained_layout=True)
        for trajectory in sorted(paired):
            axis.plot(
                [0, 1],
                [paired[trajectory][followers[0]], paired[trajectory][followers[1]]],
                color="#888888",
                alpha=0.35,
                linewidth=0.8,
            )
        means = [
            np.mean([values[follower] for values in paired.values()])
            for follower in followers
        ]
        axis.plot(
            [0, 1],
            means,
            marker="o",
            color=_PALETTE[0],
            linewidth=2.2,
            label="trajectory mean",
        )
        axis.set_xticks([0, 1], followers, rotation=15)
        axis.set(
            ylabel="raw-time position RMSE",
            title="Direct governor vs governor → Ruckig",
        )
        axis.legend(frameon=False)
        return save_deterministic_figure(figure, output)


def plot_robustness_matrix(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    return _plot_complete_heatmap(
        records,
        row_field="scenario_id",
        column_field="method",
        value_field="position_rmse",
        title="Robustness matrix",
        colorbar_label="raw-time position RMSE",
        output=output,
    )


def plot_sampling_rate_study(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    return _plot_grouped_lines(
        records,
        x_field="sampling_rate_hz",
        y_field="position_rmse",
        series_field="method",
        xlabel="sampling rate (Hz)",
        ylabel="raw-time position RMSE",
        title="Sampling-rate generalization",
        output=output,
    )


def plot_jerk_comparison(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(
        records,
        ["method", "jerk_semantic", "max_abs_jerk"],
        "jerk comparison",
    )
    methods = sorted({str(row["method"]) for row in rows})
    semantic_order = ("sampled_output", "direct_new_jerk", "internal_profile")
    present_semantics = {str(row["jerk_semantic"]) for row in rows}
    semantics = [value for value in semantic_order if value in present_semantics]
    unknown = present_semantics - set(semantic_order)
    if unknown:
        raise FigureValidationError(
            f"jerk comparison has unknown semantics {sorted(unknown)}"
        )
    means = {
        (str(row["method"]), str(row["jerk_semantic"])): float(row["max_abs_jerk"])
        for row in _aggregate(rows, ("method", "jerk_semantic"), "max_abs_jerk")
    }
    with deterministic_style():
        figure, axis = plt.subplots(figsize=(7.0, 3.8), constrained_layout=True)
        x = np.arange(len(methods))
        width = 0.24
        labels = {
            "sampled_output": "sampled jerk",
            "direct_new_jerk": "direct new_jerk",
            "internal_profile": "internal jerk",
        }
        for index, semantic in enumerate(semantics):
            available = [method for method in methods if (method, semantic) in means]
            locations = np.asarray([methods.index(method) for method in available])
            axis.bar(
                locations + (index - (len(semantics) - 1) / 2.0) * width,
                [means[(method, semantic)] for method in available],
                width,
                label=labels[semantic],
                color=_PALETTE[index],
            )
        axis.set_xticks(x, methods, rotation=20, ha="right")
        axis.set(ylabel="maximum |jerk|", title="Continuous, online, and sampled jerk")
        axis.legend(frameon=False)
        return save_deterministic_figure(figure, output)


def plot_multidof_scalability(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    return _plot_grouped_lines(
        records,
        x_field="dof",
        y_field="total_p99_us",
        series_field="method",
        xlabel="degrees of freedom",
        ylabel="total compute P99 (µs)",
        title="Multi-DoF compute scalability",
        output=output,
    )


def plot_plant_feedback_comparison(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(records, ["plant", "method", "position_rmse"], "plant comparison")
    aggregated = _aggregate(rows, ("plant", "method"), "position_rmse")
    plants = sorted({str(row["plant"]) for row in aggregated})
    methods = sorted({str(row["method"]) for row in aggregated})
    lookup = {
        (str(row["plant"]), str(row["method"])): float(row["position_rmse"])
        for row in aggregated
    }
    missing = [
        (plant, method)
        for plant in plants
        for method in methods
        if (plant, method) not in lookup
    ]
    if missing:
        raise FigureValidationError(
            f"plant comparison matrix is incomplete: {missing[0]}"
        )
    with deterministic_style():
        figure, axis = plt.subplots(figsize=(6.6, 3.8), constrained_layout=True)
        x = np.arange(len(plants))
        width = 0.8 / len(methods)
        for index, method in enumerate(methods):
            axis.bar(
                x - 0.4 + width / 2 + index * width,
                [lookup[(plant, method)] for plant in plants],
                width,
                label=method,
                color=_PALETTE[index % len(_PALETTE)],
            )
        axis.set_xticks(x, plants, rotation=15)
        axis.set(ylabel="raw-time position RMSE", title="Plant and feedback comparison")
        axis.legend(frameon=False)
        return save_deterministic_figure(figure, output)


def plot_runtime_distributions(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(records, ["method", "total_compute_us"], "runtime distribution")
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        groups[str(row["method"])].append(_numeric(row, "total_compute_us"))
    with deterministic_style():
        figure, axis = plt.subplots(figsize=(6.4, 3.8), constrained_layout=True)
        for index, method in enumerate(sorted(groups)):
            values = np.sort(np.asarray(groups[method]))
            probability = np.arange(1, values.size + 1) / values.size
            axis.step(
                values,
                probability,
                where="post",
                label=method,
                color=_PALETTE[index % len(_PALETTE)],
            )
        axis.set(
            xlabel="total compute (µs)",
            ylabel="empirical CDF",
            title="Runtime distributions",
            ylim=(0.0, 1.01),
        )
        axis.legend(frameon=False)
        return save_deterministic_figure(figure, output)


def plot_paired_confidence_intervals(
    records: Sequence[Mapping[str, Any]], output: str | Path
) -> tuple[Path, Path]:
    rows = _records(
        records,
        [
            "comparison_id",
            "relative_improvement",
            "relative_improvement_ci_low",
            "relative_improvement_ci_high",
        ],
        "paired confidence intervals",
    )
    rows = sorted(rows, key=lambda row: str(row["comparison_id"]))
    labels = [str(row["comparison_id"]) for row in rows]
    center = 100.0 * np.asarray([_numeric(row, "relative_improvement") for row in rows])
    low = 100.0 * np.asarray(
        [_numeric(row, "relative_improvement_ci_low") for row in rows]
    )
    high = 100.0 * np.asarray(
        [_numeric(row, "relative_improvement_ci_high") for row in rows]
    )
    with deterministic_style():
        figure, axis = plt.subplots(
            figsize=(7.0, max(3.2, 0.36 * len(rows) + 1.4)), constrained_layout=True
        )
        y = np.arange(len(rows))
        axis.errorbar(
            center,
            y,
            xerr=[center - low, high - center],
            fmt="o",
            color=_PALETTE[0],
            capsize=3,
        )
        axis.axvline(0.0, color="black", linewidth=0.8)
        axis.set_yticks(y, labels)
        axis.set(
            xlabel="relative improvement (%)",
            title="Paired trajectory bootstrap intervals",
        )
        return save_deterministic_figure(figure, output)


def plot_representative_traces(
    samples: Sequence[Mapping[str, Any]],
    selection: Sequence[Mapping[str, Any]],
    output: str | Path,
    *,
    trajectory_field: str = "trajectory_id",
    reference_time_field: str = "control_time",
    output_time_field: str = "command_time",
    reference_field: str = "p_ref",
    output_field: str = "command_p",
    method_field: str | None = None,
    joint_field: str | None = None,
) -> tuple[Path, Path]:
    rows = _records(
        samples,
        [
            trajectory_field,
            reference_time_field,
            output_time_field,
            reference_field,
            output_field,
        ],
        "trace samples",
    )
    choices = _records(
        selection, ["trajectory_id", "selection_reason"], "trace selection"
    )
    selected_ids = [str(row["trajectory_id"]) for row in choices]
    if len(set(selected_ids)) != len(selected_ids):
        raise FigureValidationError("trace selection contains duplicate IDs")
    ranking_methods = {
        str(row["ranking_method"])
        for row in choices
        if row.get("ranking_method") is not None
    }
    if len(ranking_methods) > 1:
        raise FigureValidationError("trace selection mixes ranking methods")
    ranking_method = next(iter(ranking_methods), None)
    if method_field is None:
        for candidate in ("method_id", "method"):
            if all(candidate in row and row[candidate] is not None for row in rows):
                method_field = candidate
                break
    if ranking_method is not None and method_field is None:
        raise FigureValidationError(
            "ranked trace selection requires a method field in samples"
        )
    if method_field is not None:
        if any(method_field not in row or row[method_field] is None for row in rows):
            raise FigureValidationError(
                f"trace samples have incomplete method field {method_field}"
            )
        if ranking_method is not None:
            rows = [row for row in rows if str(row[method_field]) == ranking_method]
        else:
            for trajectory_id in selected_ids:
                methods = {
                    str(row[method_field])
                    for row in rows
                    if str(row[trajectory_field]) == trajectory_id
                }
                if len(methods) > 1:
                    raise FigureValidationError(
                        f"trace {trajectory_id!r} mixes methods {sorted(methods)}"
                    )
    if joint_field is None and all(
        "joint_id" in row and row["joint_id"] is not None for row in rows
    ):
        joint_field = "joint_id"
    if joint_field is not None and any(
        joint_field not in row or row[joint_field] is None for row in rows
    ):
        raise FigureValidationError(
            f"trace samples have incomplete joint field {joint_field}"
        )

    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        trajectory_id = str(row[trajectory_field])
        if trajectory_id in selected_ids:
            joint_id = str(row[joint_field]) if joint_field is not None else "joint"
            grouped[trajectory_id][joint_id].append(row)
    missing = set(selected_ids) - set(grouped)
    if missing:
        raise FigureValidationError(
            f"selected trace samples are missing: {sorted(missing)}"
        )
    with deterministic_style():
        figure, axes = plt.subplots(
            len(choices),
            1,
            figsize=(7.2, max(2.4, 2.0 * len(choices))),
            squeeze=False,
            constrained_layout=True,
        )
        for axis, choice in zip(axes[:, 0], choices):
            trajectory_id = str(choice["trajectory_id"])
            joint_groups = grouped[trajectory_id]
            for index, joint_id in enumerate(sorted(joint_groups)):
                trace = sorted(
                    joint_groups[joint_id],
                    key=lambda row: (
                        _numeric(row, reference_time_field),
                        _numeric(row, output_time_field),
                    ),
                )
                reference_time = np.asarray(
                    [_numeric(row, reference_time_field) for row in trace]
                )
                output_time = np.asarray(
                    [_numeric(row, output_time_field) for row in trace]
                )
                reference = np.asarray(
                    [_numeric(row, reference_field) for row in trace]
                )
                tracked = np.asarray([_numeric(row, output_field) for row in trace])
                color = _PALETTE[index % len(_PALETTE)]
                label_prefix = "" if len(joint_groups) == 1 else f"{joint_id} "
                axis.plot(
                    reference_time,
                    reference,
                    color=color,
                    linewidth=1.0,
                    linestyle="--",
                    label=f"{label_prefix}reference",
                )
                axis.plot(
                    output_time,
                    tracked,
                    color=color,
                    linewidth=1.2,
                    label=f"{label_prefix}{output_field}",
                )
            axis.set(
                ylabel="position",
                title=f"{choice['selection_reason']}: {trajectory_id}",
            )
        axes[-1, 0].set_xlabel("physical time (s)")
        axes[0, 0].legend(frameon=False, ncol=2)
        return save_deterministic_figure(figure, output)


def _write_figure_manifest(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return path


def generate_required_figures(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    output_directory: str | Path,
    *,
    ranking_method: str,
    predefined_trace_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Generate every required figure category or fail on missing inputs."""

    required_tables = {
        "estimator",
        "prediction",
        "ablation",
        "acceleration_phase",
        "governor",
        "follower",
        "robustness",
        "sampling_rate",
        "constraints",
        "scalability",
        "plant",
        "runtime_samples",
        "paired",
        "trajectory_metrics",
        "trace_samples",
    }
    missing = required_tables - set(tables)
    if missing:
        raise FigureValidationError(
            f"required figure tables are missing: {sorted(missing)}"
        )
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    selection = select_representative_trajectories(
        tables["trajectory_metrics"],
        ranking_method=ranking_method,
        predefined_ids=predefined_trace_ids,
    )
    generators = (
        (
            "estimator_accuracy_latency_compute_pareto",
            plot_estimator_pareto,
            "estimator",
        ),
        ("prediction_error_vs_horizon", plot_prediction_error_vs_horizon, "prediction"),
        (
            "same_information_p_pv_pva_ablation",
            plot_same_information_ablation,
            "ablation",
        ),
        (
            "acceleration_value_phase_map",
            plot_acceleration_phase_map,
            "acceleration_phase",
        ),
        (
            "governor_distortion_reachability",
            plot_governor_distortion_reachability,
            "governor",
        ),
        ("direct_governor_vs_governor_ruckig", plot_direct_vs_ruckig, "follower"),
        ("robustness_matrix", plot_robustness_matrix, "robustness"),
        ("sampling_rate_study", plot_sampling_rate_study, "sampling_rate"),
        ("continuous_vs_sampled_jerk", plot_jerk_comparison, "constraints"),
        ("multidof_scalability", plot_multidof_scalability, "scalability"),
        ("plant_feedback_comparison", plot_plant_feedback_comparison, "plant"),
        ("runtime_distributions", plot_runtime_distributions, "runtime_samples"),
        (
            "paired_improvement_confidence_intervals",
            plot_paired_confidence_intervals,
            "paired",
        ),
    )
    artifacts: dict[str, list[str]] = {}
    for category, generator, table_name in generators:
        png, svg = generator(tables[table_name], output / category)
        artifacts[category] = [png.name, svg.name]
    png, svg = plot_representative_traces(
        tables["trace_samples"], selection, output / "representative_traces"
    )
    artifacts["representative_traces"] = [png.name, svg.name]
    if tuple(artifacts) != REQUIRED_FIGURE_CATEGORIES:
        raise FigureValidationError("internal required-category registry mismatch")
    manifest = {
        "schema_version": "otg.figure-manifest.v1",
        "selection_policy": {
            "ranking_method": ranking_method,
            "ranking_metric": "position_rmse",
            "rules": ["predefined_id", "median", "p90", "worst"],
            "predefined_ids": list(predefined_trace_ids),
        },
        "selected_trajectories": selection,
        "categories": artifacts,
    }
    manifest_path = _write_figure_manifest(output / "figure_manifest.json", manifest)
    manifest["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return manifest


__all__ = [
    "FigureValidationError",
    "REQUIRED_FIGURE_CATEGORIES",
    "deterministic_style",
    "generate_required_figures",
    "plot_acceleration_phase_map",
    "plot_direct_vs_ruckig",
    "plot_estimator_pareto",
    "plot_governor_distortion_reachability",
    "plot_jerk_comparison",
    "plot_multidof_scalability",
    "plot_paired_confidence_intervals",
    "plot_plant_feedback_comparison",
    "plot_prediction_error_vs_horizon",
    "plot_representative_traces",
    "plot_robustness_matrix",
    "plot_runtime_distributions",
    "plot_same_information_ablation",
    "plot_sampling_rate_study",
    "save_deterministic_figure",
    "select_representative_trajectories",
]
