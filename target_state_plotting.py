"""Static report figures for the target-state ablation experiment."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

INK = "#202124"
GRID = "#D7DCE2"
REFERENCE = "#111827"
COMPONENT_STYLES = {
    "p": {"color": "#6B7280", "linestyle": ":", "marker": None},
    "pv": {"color": "#D97706", "linestyle": "--", "marker": None},
    "pva": {"color": "#2563EB", "linestyle": "-", "marker": None},
}
SOURCE_STYLES = {
    "analytic_truth": {"color": "#2563EB", "linestyle": "-"},
    "backward_fd": {"color": "#D97706", "linestyle": "--"},
    "centered_fd_offline": {"color": "#708238", "linestyle": "-."},
    "centered_fd_causal_delay1": {"color": "#C0266D", "linestyle": ":"},
}
SOURCE_TITLES = {
    "analytic_truth": "Analytic truth",
    "backward_fd": "Historical backward FD",
    "centered_fd_offline": "Centered FD · offline",
    "centered_fd_causal_delay1": "Centered FD · causal delay-1",
}
METHOD_ORDER = (
    "p",
    "pv_truth",
    "pva_truth",
    "pv_backward",
    "pva_backward",
    "pv_central_offline",
    "pva_central_offline",
    "pv_central_causal",
    "pva_central_causal",
)
METHOD_SHORT = {
    "p": "P",
    "pv_truth": "PV · truth",
    "pva_truth": "PVA · truth",
    "pv_backward": "PV · backward",
    "pva_backward": "PVA · backward",
    "pv_central_offline": "PV · center offline",
    "pva_central_offline": "PVA · center offline",
    "pv_central_causal": "PV · center causal",
    "pva_central_causal": "PVA · center causal",
}
DATASET_ORDER = (
    "quadratic_with_extremum",
    "cubic",
    "sine",
    "csv",
)
DATASET_SHORT = {
    "quadratic_with_extremum": "Quadratic",
    "cubic": "Cubic",
    "sine": "Sine",
    "csv": "CSV",
}


def _style_axis(axis):
    axis.grid(True, color=GRID, linewidth=0.6, alpha=0.65)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.tick_params(colors=INK, labelsize=8)


def _save_figure(fig, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(
        output.with_suffix(".svg"),
        format="svg",
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(fig)
    return output


def plot_derivative_sources(references, derivative_sources, output_dir):
    datasets = [
        dataset
        for dataset in ("quadratic_with_extremum", "cubic", "sine")
        if dataset in references
    ]
    fig, axes = plt.subplots(
        len(datasets),
        2,
        figsize=(14, 3.25 * len(datasets)),
        dpi=150,
        sharex="row",
    )
    handles = {}
    for row_index, dataset in enumerate(datasets):
        reference = references[dataset]
        stop = reference.original_count
        time = reference.time[:stop]
        truth_velocity, truth_acceleration = derivative_sources[dataset][
            "analytic_truth"
        ]
        for source, (velocity, acceleration) in derivative_sources[dataset].items():
            if source == "analytic_truth":
                continue
            style = SOURCE_STYLES[source]
            label = SOURCE_TITLES[source]
            line = axes[row_index, 0].plot(
                time,
                velocity[:stop] - truth_velocity[:stop],
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=1.05,
                label=label,
            )[0]
            axes[row_index, 1].plot(
                time,
                acceleration[:stop] - truth_acceleration[:stop],
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=1.05,
                label=label,
            )
            handles[label] = line

        axes[row_index, 0].set_ylabel(
            f"{DATASET_SHORT[dataset]}\nvelocity error [rad/s]"
        )
        axes[row_index, 1].set_ylabel("acceleration error [rad/s²]")
        for axis in axes[row_index]:
            axis.axhline(
                0.0,
                color=REFERENCE,
                linestyle="--",
                linewidth=0.7,
                label="Analytic truth",
            )
            axis.set_xlabel("Time [s]")
            _style_axis(axis)

    handles = {"Analytic truth (zero error)": axes[0, 0].lines[-1], **handles}
    axes[0, 0].set_title("Velocity error", color=INK)
    axes[0, 1].set_title("Acceleration error", color=INK)
    fig.legend(
        handles.values(),
        handles.keys(),
        loc="upper center",
        ncol=4,
        frameon=False,
        fontsize=9,
        bbox_to_anchor=(0.5, 0.985),
    )
    fig.suptitle(
        "Finite-difference error against analytic derivatives\n"
        "Fixed 10 ms samples; offline centered FD uses one future sample",
        fontsize=14,
        color=INK,
        y=1.045,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    return _save_figure(
        fig, Path(output_dir) / "derivative_sources.png"
    )


def plot_dataset_ablation(
    reference,
    results,
    methods,
    output_dir,
    max_velocity,
    max_acceleration,
    max_jerk,
):
    method_by_id = {method.method_id: method for method in methods}
    source_groups = []
    for source in SOURCE_TITLES:
        ids = [
            method.method_id
            for method in methods
            if method.derivative_source == source
        ]
        if ids:
            source_groups.append((source, ids))

    column_count = len(source_groups)
    fig, axes = plt.subplots(
        2,
        column_count,
        figsize=(5.0 * column_count, 8.2),
        dpi=150,
        sharex=True,
        sharey="row",
        squeeze=False,
    )
    display_count = min(
        reference.position.size,
        reference.original_count + int(round(0.4 / reference.dt)),
    )
    time = reference.time[:display_count]
    target = reference.position[:display_count]
    component_labels = {"p": "P", "pv": "PV", "pva": "PVA"}
    legend_handles = {}

    for column, (source, method_ids) in enumerate(source_groups):
        position_axis = axes[0, column]
        error_axis = axes[1, column]
        reference_line = position_axis.plot(
            time,
            target,
            color=REFERENCE,
            linestyle="--",
            linewidth=1.05,
            label="Reference",
        )[0]
        legend_handles["Reference"] = reference_line

        visible_ids = ["p", *method_ids]
        for method_id in visible_ids:
            method = method_by_id[method_id]
            result = results[method_id]
            style = COMPONENT_STYLES[method.target_components]
            label = component_labels[method.target_components]
            line = position_axis.plot(
                time,
                result["position"][:display_count],
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=1.05,
                label=label,
            )[0]
            error_axis.plot(
                time,
                result["position"][:display_count] - target,
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=0.95,
            )
            legend_handles[label] = line

        boundary = (reference.original_count - 1) * reference.dt
        for axis in (position_axis, error_axis):
            axis.axvline(boundary, color="#9CA3AF", linewidth=0.7)
            _style_axis(axis)
        error_axis.axhline(0.0, color=INK, linewidth=0.65)
        position_axis.set_title(SOURCE_TITLES[source], fontsize=11, color=INK)
        error_axis.set_xlabel("Time [s]")

    axes[0, 0].set_ylabel("Position [rad]")
    axes[1, 0].set_ylabel("Signed error [rad]")
    fig.legend(
        legend_handles.values(),
        legend_handles.keys(),
        loc="upper center",
        ncol=4,
        frameon=False,
        fontsize=9,
        bbox_to_anchor=(0.5, 0.955),
    )
    fig.suptitle(
        f"Target-state ablation · {reference.title}\n"
        f"DT=10 ms, limits={max_velocity:g}/{max_acceleration:g}/{max_jerk:g}, "
        "target[k] → output[k+1]",
        fontsize=14,
        color=INK,
        y=1.01,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.925))
    return _save_figure(
        fig,
        Path(output_dir) / f"target_state_ablation_{reference.dataset}.png",
    )


def _annotated_heatmap(axis, values, annotations, title, cmap, norm):
    masked = np.ma.masked_invalid(values)
    image = axis.imshow(masked, aspect="auto", cmap=cmap, norm=norm)
    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            if np.isfinite(values[row, column]):
                axis.text(
                    column,
                    row,
                    annotations[row, column],
                    ha="center",
                    va="center",
                    fontsize=7.5,
                    color=INK,
                )
            else:
                axis.text(
                    column,
                    row,
                    "N/A",
                    ha="center",
                    va="center",
                    fontsize=7.5,
                    color="#6B7280",
                )
    axis.set_title(title, fontsize=11, color=INK)
    axis.tick_params(length=0)
    return image


def plot_ablation_summary(metrics, method_labels, output_dir):
    row_ids = [method_id for method_id in METHOD_ORDER if method_id in method_labels]
    dataset_ids = [
        dataset
        for dataset in DATASET_ORDER
        if any(row["dataset"] == dataset for row in metrics)
    ]
    lookup = {(row["dataset"], row["method_id"]): row for row in metrics}
    ratios = np.full((len(row_ids), len(dataset_ids)), np.nan)
    lag_delta = np.full_like(ratios, np.nan)
    ratio_text = np.full(ratios.shape, "", dtype=object)
    lag_text = np.full(ratios.shape, "", dtype=object)
    for column, dataset in enumerate(dataset_ids):
        baseline = lookup[(dataset, "p")]
        for row_index, method_id in enumerate(row_ids):
            row = lookup.get((dataset, method_id))
            if row is None:
                continue
            ratios[row_index, column] = row["rmse"] / baseline["rmse"]
            lag_delta[row_index, column] = (
                row["best_lag_ms"] - baseline["best_lag_ms"]
            )
            ratio_text[row_index, column] = f"×{ratios[row_index, column]:.2f}"
            lag_text[row_index, column] = f"{lag_delta[row_index, column]:+.0f}"

    blue_orange = LinearSegmentedColormap.from_list(
        "blue_neutral_orange", ["#8BB9E8", "#F5F5F4", "#E6A15C"]
    )
    ratio_finite = ratios[np.isfinite(ratios)]
    lag_finite = lag_delta[np.isfinite(lag_delta)]
    ratio_extent = max(0.5, float(np.max(np.abs(np.log2(ratio_finite)))))
    ratio_log = np.log2(ratios)
    lag_extent = max(10.0, float(np.max(np.abs(lag_finite))))

    fig, axes = plt.subplots(1, 2, figsize=(14, 8.5), dpi=150)
    ratio_image = _annotated_heatmap(
        axes[0],
        ratio_log,
        ratio_text,
        "Position RMSE relative to P baseline",
        blue_orange,
        TwoSlopeNorm(vmin=-ratio_extent, vcenter=0.0, vmax=ratio_extent),
    )
    lag_image = _annotated_heatmap(
        axes[1],
        lag_delta,
        lag_text,
        "Global lag change vs P baseline [ms]",
        blue_orange,
        TwoSlopeNorm(vmin=-lag_extent, vcenter=0.0, vmax=lag_extent),
    )
    for axis in axes:
        axis.set_xticks(
            np.arange(len(dataset_ids)),
            [DATASET_SHORT[dataset] for dataset in dataset_ids],
            rotation=20,
            ha="right",
        )
        axis.set_yticks(
            np.arange(len(row_ids)),
            [method_labels[method_id] for method_id in row_ids],
        )
    fig.colorbar(ratio_image, ax=axes[0], fraction=0.035, pad=0.03, label="log₂ ratio")
    fig.colorbar(lag_image, ax=axes[1], fraction=0.035, pad=0.03, label="lag Δ [ms]")
    fig.suptitle(
        "Target-state ablation summary\n"
        "Blue indicates lower RMSE / less lag than P; orange indicates worse",
        fontsize=14,
        color=INK,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save_figure(fig, Path(output_dir) / "ablation_summary.png")


def plot_sensitivity_heatmaps(
    metrics,
    method_labels,
    metric,
    vendor_value_by_sweep,
    output_dir,
):
    datasets = [dataset for dataset in ("sine", "csv") if any(row["dataset"] == dataset for row in metrics)]
    sweeps = ("acceleration", "jerk")
    blue_orange = LinearSegmentedColormap.from_list(
        "blue_neutral_orange_sensitivity",
        ["#8BB9E8", "#F5F5F4", "#E6A15C"],
    )
    fig, axes = plt.subplots(
        len(datasets),
        2,
        figsize=(15, 5.2 * len(datasets)),
        dpi=150,
        squeeze=False,
    )

    all_transformed = []
    panel_data = []
    for dataset in datasets:
        dataset_methods = [
            method_id
            for method_id in METHOD_ORDER
            if any(
                row["dataset"] == dataset and row["method_id"] == method_id
                for row in metrics
            )
        ]
        for sweep in sweeps:
            values = sorted(
                {
                    float(row["sweep_value"])
                    for row in metrics
                    if row["dataset"] == dataset and row["sweep_type"] == sweep
                }
            )
            lookup = {
                (row["method_id"], float(row["sweep_value"])): row
                for row in metrics
                if row["dataset"] == dataset and row["sweep_type"] == sweep
            }
            matrix = np.full((len(dataset_methods), len(values)), np.nan)
            annotations = np.full(matrix.shape, "", dtype=object)
            vendor_value = vendor_value_by_sweep[sweep]
            for row_index, method_id in enumerate(dataset_methods):
                baseline = lookup[(method_id, vendor_value)][metric]
                for column, value in enumerate(values):
                    current = lookup[(method_id, value)][metric]
                    if metric == "rmse":
                        transformed = np.log2(current / baseline)
                        annotations[row_index, column] = f"×{current / baseline:.2f}"
                    else:
                        transformed = current - baseline
                        annotations[row_index, column] = f"{transformed:+.0f}"
                    matrix[row_index, column] = transformed
            all_transformed.extend(matrix[np.isfinite(matrix)])
            panel_data.append((dataset, sweep, dataset_methods, values, matrix, annotations))

    extent = max(0.5 if metric == "rmse" else 10.0, float(np.max(np.abs(all_transformed))))
    for dataset, sweep, method_ids, values, matrix, annotations in panel_data:
        row = datasets.index(dataset)
        column = sweeps.index(sweep)
        axis = axes[row, column]
        image = _annotated_heatmap(
            axis,
            matrix,
            annotations,
            f"{DATASET_SHORT[dataset]} · max {sweep}",
            blue_orange,
            TwoSlopeNorm(vmin=-extent, vcenter=0.0, vmax=extent),
        )
        axis.set_xticks(np.arange(len(values)), [f"{value:g}" for value in values])
        axis.set_yticks(np.arange(len(method_ids)))
        if column == 0:
            axis.set_yticklabels(
                [METHOD_SHORT.get(method_id, method_id) for method_id in method_ids]
            )
        else:
            axis.set_yticklabels([])
        tick_labels = []
        vendor_value = vendor_value_by_sweep[sweep]
        for value in values:
            label = f"{value:g}"
            if value == vendor_value:
                label += "\n(vendor)"
            tick_labels.append(label)
        axis.set_xticklabels(tick_labels)
        unit = "rad/s²" if sweep == "acceleration" else "rad/s³"
        axis.set_xlabel(f"Limit [{unit}]")

    label = "log₂ RMSE ratio" if metric == "rmse" else "lag Δ [ms]"
    metric_title = "position RMSE" if metric == "rmse" else "global lag"
    fig.suptitle(
        f"One-factor motion-limit sensitivity · {metric_title}\n"
        "Each cell is relative to the same method at vendor limits; no deployment optimum is inferred",
        fontsize=14,
        color=INK,
    )
    fig.subplots_adjust(
        top=0.91,
        bottom=0.08,
        left=0.14,
        right=0.89,
        hspace=0.35,
        wspace=0.30,
    )
    colorbar_axis = fig.add_axes((0.92, 0.18, 0.016, 0.64))
    fig.colorbar(image, cax=colorbar_axis, label=label)
    return _save_figure(
        fig, Path(output_dir) / f"constraint_sensitivity_{metric}.png"
    )
