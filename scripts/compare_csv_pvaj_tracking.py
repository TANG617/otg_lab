"""Compare raw PVAJ demand and ordinary-Ruckig tracking for two CSV traces.

This is a development-only diagnostic.  Both inputs are evaluated on the
historical fixed 10 ms grid so that the comparison changes the trace, not the
controller timing convention.  Source timestamps are profiled separately as a
data-quality and derivative-sensitivity check.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_target_state_ablation import (  # noqa: E402
    COMMON_WARMUP_SAMPLES,
    run_method,
)
from target_state_experiment import (  # noqa: E402
    DT,
    VENDOR_LIMITS,
    centered_finite_difference_offline,
    csv_reference,
    methods_for_reference,
)

DEFAULT_BASELINE = ROOT / "plot_data.csv"
DEFAULT_CANDIDATE = ROOT / "data" / "simplified-tasks_no-velocity-limit.csv"
DEFAULT_OUTPUT = ROOT / "results" / "csv_pvaj_tracking_comparison"
PRIMARY_METHOD_ID = "p"
WINDOW_SAMPLES = 100
DATASET_ORDER = ("current_csv", "new_csv")
DATASET_LABELS = {
    "current_csv": "Current CSV",
    "new_csv": "New simplified CSV",
}
DATASET_COLORS = {
    "current_csv": "#5D6670",
    "new_csv": "#2F6B9A",
}
SIGNAL_UNITS = {
    "position": "rad",
    "velocity": "rad/s",
    "acceleration": "rad/s^2",
    "jerk": "rad/s^3",
}


@dataclass(frozen=True)
class CsvTrace:
    dataset: str
    label: str
    path: Path
    elapsed_time: np.ndarray
    timestamp: np.ndarray
    topic: np.ndarray
    position: np.ndarray

    @property
    def fixed_time(self):
        return np.arange(self.position.size, dtype=float) * DT


def _float_field(row, field, row_number, path):
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}: row {row_number} has invalid {field!r}") from exc
    if not np.isfinite(value):
        raise ValueError(f"{path}: row {row_number} has nonfinite {field!r}")
    return value


def load_trace(path, dataset, label):
    """Load and validate the recorded four-column CSV shape."""
    path = Path(path).resolve()
    required = ("elapsed time", "timestamp", "topic", "value")
    elapsed_time = []
    timestamp = []
    topic = []
    position = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: missing header")
        missing = [field for field in required if field not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path}: missing required columns {missing}")
        for row_number, row in enumerate(reader, start=2):
            elapsed_time.append(_float_field(row, "elapsed time", row_number, path))
            timestamp.append(_float_field(row, "timestamp", row_number, path))
            topic_value = row.get("topic", "")
            if not topic_value:
                raise ValueError(f"{path}: row {row_number} has empty topic")
            topic.append(topic_value)
            position.append(_float_field(row, "value", row_number, path))

    if len(position) < 8:
        raise ValueError(f"{path}: at least 8 data rows are required")
    elapsed_array = np.asarray(elapsed_time, dtype=float)
    timestamp_array = np.asarray(timestamp, dtype=float)
    if not np.all(np.diff(elapsed_array) > 0.0):
        raise ValueError(f"{path}: elapsed time must be strictly increasing")
    if not np.all(np.diff(timestamp_array) > 0.0):
        raise ValueError(f"{path}: timestamp must be strictly increasing")
    return CsvTrace(
        dataset=dataset,
        label=label,
        path=path,
        elapsed_time=elapsed_array,
        timestamp=timestamp_array,
        topic=np.asarray(topic, dtype=str),
        position=np.asarray(position, dtype=float),
    )


def profile_trace(trace):
    """Return the compact data-quality and workload profile."""
    source_dt_ms = np.diff(trace.elapsed_time) * 1000.0
    clock_offset = trace.timestamp - trace.elapsed_time
    return {
        "dataset": trace.dataset,
        "label": trace.label,
        "rows": int(trace.position.size),
        "topic_count": int(np.unique(trace.topic).size),
        "source_duration_s": float(trace.elapsed_time[-1] - trace.elapsed_time[0]),
        "fixed_grid_duration_s": float((trace.position.size - 1) * DT),
        "position_start_rad": float(trace.position[0]),
        "position_end_rad": float(trace.position[-1]),
        "position_min_rad": float(np.min(trace.position)),
        "position_max_rad": float(np.max(trace.position)),
        "position_range_rad": float(np.ptp(trace.position)),
        "position_robust_scale_rad": float(
            np.percentile(trace.position, 95) - np.percentile(trace.position, 5)
        ),
        "source_dt_min_ms": float(np.min(source_dt_ms)),
        "source_dt_p01_ms": float(np.percentile(source_dt_ms, 1)),
        "source_dt_p50_ms": float(np.percentile(source_dt_ms, 50)),
        "source_dt_p99_ms": float(np.percentile(source_dt_ms, 99)),
        "source_dt_max_ms": float(np.max(source_dt_ms)),
        "source_dt_within_5_to_15ms_rate": float(
            np.mean((source_dt_ms >= 5.0) & (source_dt_ms <= 15.0))
        ),
        "clock_offset_residual_max_us": float(
            np.max(np.abs(clock_offset - np.median(clock_offset))) * 1e6
        ),
        "exact_position_repeat_rate": float(np.mean(np.diff(trace.position) == 0.0)),
    }


def fixed_grid_pvaj(position):
    """Estimate sampled PVAJ on the protocol's fixed grid.

    Velocity and acceleration use the repository's timestamp-aligned centered
    finite differences.  Jerk is the centered gradient of sampled
    acceleration.  Three samples at each boundary are excluded from summary
    metrics to avoid endpoint formulas dominating a maximum.
    """
    position = np.asarray(position, dtype=float)
    velocity, acceleration = centered_finite_difference_offline(position, DT)
    jerk = np.gradient(acceleration, DT, edge_order=2)
    return {
        "position": position.copy(),
        "velocity": velocity,
        "acceleration": acceleration,
        "jerk": jerk,
    }


def actual_time_pvaj(trace):
    """Estimate a timing-sensitivity PVAJ diagnostic on source elapsed time."""
    time = trace.elapsed_time - trace.elapsed_time[0]
    velocity = np.gradient(trace.position, time, edge_order=2)
    acceleration = np.gradient(velocity, time, edge_order=2)
    jerk = np.gradient(acceleration, time, edge_order=2)
    return {
        "position": trace.position.copy(),
        "velocity": velocity,
        "acceleration": acceleration,
        "jerk": jerk,
    }


def pvaj_metric_rows(trace, signals, basis):
    start = 3
    stop = trace.position.size - 3
    if stop <= start:
        raise ValueError(f"{trace.path}: insufficient common PVAJ interior")
    rows = []
    for signal in ("position", "velocity", "acceleration", "jerk"):
        values = np.asarray(signals[signal][start:stop], dtype=float)
        rows.append(
            {
                "dataset": trace.dataset,
                "label": trace.label,
                "derivative_basis": basis,
                "signal": signal,
                "unit": SIGNAL_UNITS[signal],
                "evaluation_start_index": start,
                "evaluation_stop_index_exclusive": stop,
                "max_abs": float(np.max(np.abs(values))),
                "p99_abs": float(np.percentile(np.abs(values), 99)),
                "rms": float(np.sqrt(np.mean(values**2))),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
            }
        )
    return rows


def build_reference(trace):
    reference = csv_reference(trace.path)
    return replace(
        reference,
        dataset=trace.dataset,
        title=f"{trace.label} (fixed 10 ms per row)",
    )


def run_tracking_comparison(traces):
    rows = []
    results = {}
    references = {}
    for trace in traces:
        reference = build_reference(trace)
        references[trace.dataset] = reference
        results[trace.dataset] = {}
        source_position = reference.position[: reference.original_count]
        robust_scale = float(
            np.percentile(source_position, 95) - np.percentile(source_position, 5)
        )
        position_range = float(np.ptp(source_position))
        for method in methods_for_reference(reference):
            result, metrics = run_method(reference, method, VENDOR_LIMITS)
            metrics.update(
                {
                    "dataset_label": trace.label,
                    "position_range_rad": position_range,
                    "position_robust_scale_rad": robust_scale,
                    "normalized_rmse_robust": (
                        float(metrics["rmse"] / robust_scale)
                        if robust_scale > 1e-12
                        else float("nan")
                    ),
                    "normalized_mae_robust": (
                        float(metrics["mae"] / robust_scale)
                        if robust_scale > 1e-12
                        else float("nan")
                    ),
                    "normalized_max_error_range": (
                        float(metrics["max_error"] / position_range)
                        if position_range > 1e-12
                        else float("nan")
                    ),
                    "abs_best_lag_ms": abs(float(metrics["best_lag_ms"])),
                }
            )
            rows.append(metrics)
            results[trace.dataset][method.method_id] = result
    return references, results, rows


def _percent_change(baseline, candidate):
    if baseline == 0.0:
        return float("nan")
    return float(100.0 * (candidate - baseline) / abs(baseline))


def build_metric_comparisons(trace_rows, raw_rows, tracking_rows):
    profiles = {row["dataset"]: row for row in trace_rows}
    raw = {
        (row["dataset"], row["signal"]): row
        for row in raw_rows
        if row["derivative_basis"] == "fixed_10ms_centered"
    }
    tracking = {(row["dataset"], row["method_id"]): row for row in tracking_rows}
    specifications = [
        (
            "workload",
            "position_range_rad",
            "Position range",
            "rad",
            lambda dataset: profiles[dataset]["position_range_rad"],
            "context",
        ),
        (
            "workload",
            "fixed_grid_duration_s",
            "Fixed-grid duration",
            "s",
            lambda dataset: profiles[dataset]["fixed_grid_duration_s"],
            "context",
        ),
    ]
    for signal, unit in (
        ("velocity", "rad/s"),
        ("acceleration", "rad/s^2"),
        ("jerk", "rad/s^3"),
    ):
        for statistic, label in (
            ("max_abs", "Maximum absolute"),
            ("p99_abs", "P99 absolute"),
            ("rms", "RMS"),
        ):
            specifications.append(
                (
                    "raw_pvaj",
                    f"{statistic}_{signal}",
                    f"{label} {signal}",
                    unit,
                    lambda dataset, signal=signal, statistic=statistic: raw[
                        (dataset, signal)
                    ][statistic],
                    "lower",
                )
            )

    primary = PRIMARY_METHOD_ID
    for metric, label, unit, preferred in (
        ("rmse", "P-only tracking RMSE", "rad", "lower"),
        (
            "normalized_rmse_robust",
            "P-only robust-scale NRMSE",
            "ratio",
            "lower",
        ),
        ("max_error", "P-only maximum error", "rad", "lower"),
        (
            "normalized_max_error_range",
            "P-only range-normalized maximum error",
            "ratio",
            "lower",
        ),
        ("abs_best_lag_ms", "P-only absolute best lag", "ms", "lower"),
        (
            "lag_aligned_rmse",
            "P-only lag-aligned RMSE",
            "rad",
            "lower",
        ),
        (
            "reachable_within_10ms_rate",
            "P-only target reachable within 10 ms",
            "rate",
            "higher",
        ),
        (
            "ruckig_compute_p99_us",
            "P-only Ruckig compute P99",
            "us",
            "lower",
        ),
    ):
        specifications.append(
            (
                "tracking",
                metric,
                label,
                unit,
                lambda dataset, metric=metric: tracking[(dataset, primary)][metric],
                preferred,
            )
        )

    rows = []
    for group, metric, label, unit, accessor, preferred in specifications:
        baseline = float(accessor("current_csv"))
        candidate = float(accessor("new_csv"))
        rows.append(
            {
                "metric_group": group,
                "metric": metric,
                "label": label,
                "unit": unit,
                "preferred_direction": preferred,
                "current_csv": baseline,
                "new_csv": candidate,
                "absolute_delta": candidate - baseline,
                "change_pct": _percent_change(baseline, candidate),
            }
        )
    return rows


def build_tracking_method_comparisons(tracking_rows):
    lookup = {(row["dataset"], row["method_id"]): row for row in tracking_rows}
    method_ids = [
        row["method_id"] for row in tracking_rows if row["dataset"] == "current_csv"
    ]
    rows = []
    for method_id in method_ids:
        baseline = lookup[("current_csv", method_id)]
        candidate = lookup[("new_csv", method_id)]
        rows.append(
            {
                "method_id": method_id,
                "method": baseline["method"],
                "result_group": baseline["result_group"],
                "causal": baseline["causal"],
                "current_rmse": baseline["rmse"],
                "new_rmse": candidate["rmse"],
                "rmse_change_pct": _percent_change(baseline["rmse"], candidate["rmse"]),
                "current_normalized_rmse_robust": baseline["normalized_rmse_robust"],
                "new_normalized_rmse_robust": candidate["normalized_rmse_robust"],
                "normalized_rmse_change_pct": _percent_change(
                    baseline["normalized_rmse_robust"],
                    candidate["normalized_rmse_robust"],
                ),
                "current_abs_best_lag_ms": baseline["abs_best_lag_ms"],
                "new_abs_best_lag_ms": candidate["abs_best_lag_ms"],
                "current_target_projection_rate": baseline["target_projection_rate"],
                "new_target_projection_rate": candidate["target_projection_rate"],
                "current_reachable_within_10ms_rate": baseline[
                    "reachable_within_10ms_rate"
                ],
                "new_reachable_within_10ms_rate": candidate[
                    "reachable_within_10ms_rate"
                ],
            }
        )
    return rows


def build_sample_rows(traces, signals, references, results):
    pvaj_rows = []
    tracking_rows = []
    for trace in traces:
        dataset_signals = signals[trace.dataset]
        primary_result = results[trace.dataset][PRIMARY_METHOD_ID]
        reference = references[trace.dataset]
        for index in range(trace.position.size):
            pvaj_rows.append(
                {
                    "dataset": trace.dataset,
                    "label": trace.label,
                    "sample_index": index,
                    "time_s": index * DT,
                    "position_rad": dataset_signals["position"][index],
                    "velocity_rad_s": dataset_signals["velocity"][index],
                    "acceleration_rad_s2": dataset_signals["acceleration"][index],
                    "jerk_rad_s3": dataset_signals["jerk"][index],
                }
            )
            tracking_rows.append(
                {
                    "dataset": trace.dataset,
                    "label": trace.label,
                    "method_id": PRIMARY_METHOD_ID,
                    "sample_index": index,
                    "time_s": index * DT,
                    "reference_position_rad": reference.position[index],
                    "tracked_position_rad": primary_result["position"][index],
                    "tracking_error_rad": (
                        primary_result["position"][index] - reference.position[index]
                    ),
                }
            )
    return pvaj_rows, tracking_rows


def build_window_diagnostics(traces, signals, references, results):
    rows = []
    for trace in traces:
        dataset_signals = signals[trace.dataset]
        reference = references[trace.dataset]
        result = results[trace.dataset][PRIMARY_METHOD_ID]
        error = (
            result["position"][: reference.original_count]
            - reference.position[: reference.original_count]
        )
        for start in range(
            COMMON_WARMUP_SAMPLES,
            reference.original_count - WINDOW_SAMPLES + 1,
            WINDOW_SAMPLES,
        ):
            stop = start + WINDOW_SAMPLES
            window = slice(start, stop)
            row = {
                "dataset": trace.dataset,
                "label": trace.label,
                "window_index": len(
                    [item for item in rows if item["dataset"] == trace.dataset]
                ),
                "start_index": start,
                "stop_index_exclusive": stop,
                "start_time_s": start * DT,
                "stop_time_s": stop * DT,
                "sample_count": WINDOW_SAMPLES,
                "position_range_rad": float(np.ptp(reference.position[window])),
                "tracking_rmse_rad": float(np.sqrt(np.mean(error[window] ** 2))),
                "tracking_max_error_rad": float(np.max(np.abs(error[window]))),
            }
            for signal in ("velocity", "acceleration", "jerk"):
                values = dataset_signals[signal][window]
                row[f"max_abs_{signal}"] = float(np.max(np.abs(values)))
                row[f"rms_{signal}"] = float(np.sqrt(np.mean(values**2)))
            rows.append(row)
    return rows


def build_window_relationships(window_rows):
    metrics = (
        "position_range_rad",
        "max_abs_velocity",
        "max_abs_acceleration",
        "max_abs_jerk",
        "rms_velocity",
        "rms_acceleration",
        "rms_jerk",
    )
    rows = []
    for scope in ("combined", *DATASET_ORDER):
        selected = [
            row for row in window_rows if scope == "combined" or row["dataset"] == scope
        ]
        tracking_rmse = [row["tracking_rmse_rad"] for row in selected]
        for metric in metrics:
            correlation, p_value = spearmanr(
                [row[metric] for row in selected],
                tracking_rmse,
            )
            rows.append(
                {
                    "scope": scope,
                    "window_samples": WINDOW_SAMPLES,
                    "window_duration_s": WINDOW_SAMPLES * DT,
                    "window_count": len(selected),
                    "demand_metric": metric,
                    "tracking_metric": "tracking_rmse_rad",
                    "spearman_rho_descriptive": float(correlation),
                    "naive_p_value_not_for_inference": float(p_value),
                    "inference_status": (
                        "descriptive_only_autocorrelated_windows_two_traces"
                    ),
                }
            )
    return rows


def _write_rows(rows, path):
    if not rows:
        raise ValueError(f"cannot write empty table: {path}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _save_figure(fig, output):
    output = Path(output)
    fig.savefig(output, dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def plot_raw_pvaj(traces, signals, output_dir):
    fig, axes = plt.subplots(
        4,
        2,
        figsize=(14, 12),
        dpi=150,
        sharex="col",
        sharey="row",
    )
    signal_specs = (
        ("position", "Position [rad]"),
        ("velocity", "Velocity [rad/s]"),
        ("acceleration", "Acceleration [rad/s²]"),
        ("jerk", "Jerk [rad/s³]"),
    )
    for column, trace in enumerate(traces):
        time = trace.fixed_time
        for row, (signal, ylabel) in enumerate(signal_specs):
            axis = axes[row, column]
            axis.plot(
                time,
                signals[trace.dataset][signal],
                color=DATASET_COLORS[trace.dataset],
                linewidth=0.8,
            )
            axis.axhline(0.0, color="#252A2E", linewidth=0.5, alpha=0.5)
            axis.grid(True, color="#D8DDE2", linewidth=0.5, alpha=0.8)
            if column == 0:
                axis.set_ylabel(ylabel)
            if row == 0:
                axis.set_title(trace.label)
            if row == 3:
                axis.set_xlabel("Fixed-grid time [s]")
    fig.suptitle(
        "Raw sampled PVAJ comparison",
        fontsize=15,
        fontweight="bold",
        y=0.995,
    )
    fig.text(
        0.5,
        0.965,
        "Centered finite differences on the common 10 ms grid; row scales are shared",
        ha="center",
        va="top",
        fontsize=9,
        color="#4B535A",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save_figure(fig, Path(output_dir) / "raw_pvaj_comparison.png")


def plot_tracking(traces, references, results, output_dir):
    fig, axes = plt.subplots(
        2,
        2,
        figsize=(14, 8),
        dpi=150,
        gridspec_kw={"width_ratios": [2.1, 1.0]},
    )
    for row, trace in enumerate(traces):
        reference = references[trace.dataset]
        result = results[trace.dataset][PRIMARY_METHOD_ID]
        stop = reference.original_count
        time = reference.time[:stop]
        error = result["position"][:stop] - reference.position[:stop]
        tracking_axis, error_axis = axes[row]
        tracking_axis.plot(
            time,
            reference.position[:stop],
            color="#252A2E",
            linewidth=1.0,
            linestyle="--",
            label="Reference",
        )
        tracking_axis.plot(
            time,
            result["position"][:stop],
            color=DATASET_COLORS[trace.dataset],
            linewidth=0.9,
            label="Ordinary Ruckig P-only output",
        )
        tracking_axis.set_ylabel("Position [rad]")
        tracking_axis.set_title(trace.label)
        tracking_axis.grid(True, color="#D8DDE2", linewidth=0.5)
        tracking_axis.legend(loc="best", fontsize=8)
        error_axis.plot(
            time,
            error,
            color=DATASET_COLORS[trace.dataset],
            linewidth=0.8,
        )
        error_axis.axhline(0.0, color="#252A2E", linewidth=0.5)
        error_axis.set_title("Tracking error")
        error_axis.set_ylabel("Error [rad]")
        error_axis.grid(True, color="#D8DDE2", linewidth=0.5)
        if row == 1:
            tracking_axis.set_xlabel("Fixed-grid time [s]")
            error_axis.set_xlabel("Fixed-grid time [s]")
    fig.suptitle(
        "P-only ordinary-Ruckig tracking comparison",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return _save_figure(fig, Path(output_dir) / "tracking_trajectory_comparison.png")


def plot_summary(metric_rows, method_rows, relationship_rows, output_dir):
    metric_lookup = {row["metric"]: row for row in metric_rows}
    combined_relationship = {
        row["demand_metric"]: row
        for row in relationship_rows
        if row["scope"] == "combined"
    }
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), dpi=150)

    raw_metrics = (
        ("max_abs_velocity", "Max |V|"),
        ("max_abs_acceleration", "Max |A|"),
        ("max_abs_jerk", "Max |J|"),
    )
    axis = axes[0, 0]
    ratios = [
        metric_lookup[metric]["new_csv"] / metric_lookup[metric]["current_csv"]
        for metric, _ in raw_metrics
    ]
    axis.bar(
        [label for _, label in raw_metrics],
        ratios,
        color="#2F6B9A",
        edgecolor="#234F72",
    )
    axis.axhline(1.0, color="#252A2E", linewidth=1.0, linestyle="--")
    axis.set_ylabel("New / current ratio")
    axis.set_title("Maximum raw dynamic demand")
    axis.grid(True, axis="y", color="#D8DDE2", linewidth=0.5)
    for index, value in enumerate(ratios):
        axis.text(index, value, f"{value:.2f}×", ha="center", va="bottom")

    tracking_metrics = (
        ("normalized_rmse_robust", "NRMSE"),
        ("normalized_max_error_range", "Norm. max error"),
        ("abs_best_lag_ms", "|Lag|"),
    )
    axis = axes[0, 1]
    ratios = [
        metric_lookup[metric]["new_csv"] / metric_lookup[metric]["current_csv"]
        for metric, _ in tracking_metrics
    ]
    axis.bar(
        [label for _, label in tracking_metrics],
        ratios,
        color="#B7791F",
        edgecolor="#7A5218",
    )
    axis.axhline(1.0, color="#252A2E", linewidth=1.0, linestyle="--")
    axis.set_ylabel("New / current ratio")
    axis.set_title("P-only tracking performance")
    axis.grid(True, axis="y", color="#D8DDE2", linewidth=0.5)
    for index, value in enumerate(ratios):
        axis.text(index, value, f"{value:.2f}×", ha="center", va="bottom")

    axis = axes[1, 0]
    method_labels = [row["method_id"] for row in method_rows]
    method_ratios = [
        row["new_normalized_rmse_robust"] / row["current_normalized_rmse_robust"]
        for row in method_rows
    ]
    positions = np.arange(len(method_rows))
    axis.barh(
        positions,
        method_ratios,
        color="#2F6B9A",
        edgecolor="#234F72",
    )
    axis.axvline(1.0, color="#252A2E", linewidth=1.0, linestyle="--")
    axis.set_yticks(positions, labels=method_labels)
    axis.invert_yaxis()
    axis.set_xlabel("New / current robust-scale NRMSE")
    axis.set_title("Tracking error across target-state methods")
    axis.grid(True, axis="x", color="#D8DDE2", linewidth=0.5)

    axis = axes[1, 1]
    relationship_metrics = (
        ("rms_velocity", "RMS V"),
        ("rms_acceleration", "RMS A"),
        ("rms_jerk", "RMS J"),
    )
    correlations = [
        combined_relationship[metric]["spearman_rho_descriptive"]
        for metric, _ in relationship_metrics
    ]
    axis.bar(
        [label for _, label in relationship_metrics],
        correlations,
        color="#7D8790",
        edgecolor="#515A62",
    )
    axis.axhline(0.0, color="#252A2E", linewidth=0.7)
    axis.set_ylim(-1.0, 1.0)
    axis.set_ylabel("Descriptive Spearman ρ")
    axis.set_title("1 s window demand vs tracking RMSE")
    axis.grid(True, axis="y", color="#D8DDE2", linewidth=0.5)
    for index, value in enumerate(correlations):
        axis.text(index, value, f"{value:.2f}", ha="center", va="bottom")

    fig.suptitle(
        "Raw-demand and tracking metric comparison",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return _save_figure(fig, Path(output_dir) / "summary_metric_comparison.png")


def plot_window_relationship(window_rows, relationship_rows, output_dir):
    combined = {
        row["demand_metric"]: row
        for row in relationship_rows
        if row["scope"] == "combined"
    }
    fig, axis = plt.subplots(figsize=(9, 6), dpi=150)
    for dataset in DATASET_ORDER:
        rows = [row for row in window_rows if row["dataset"] == dataset]
        axis.scatter(
            [row["rms_velocity"] for row in rows],
            [row["tracking_rmse_rad"] for row in rows],
            color=DATASET_COLORS[dataset],
            edgecolor="#252A2E",
            linewidth=0.35,
            s=42,
            alpha=0.85,
            label=DATASET_LABELS[dataset],
        )
    rho = combined["rms_velocity"]["spearman_rho_descriptive"]
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlabel("Window RMS velocity [rad/s]")
    axis.set_ylabel("Window tracking RMSE [rad]")
    axis.set_title("One-second window velocity demand and tracking error")
    axis.grid(True, which="both", color="#D8DDE2", linewidth=0.5)
    axis.legend(title=f"Combined descriptive ρ={rho:.2f}")
    fig.tight_layout()
    return _save_figure(fig, Path(output_dir) / "window_velocity_vs_tracking_error.png")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_value(*args):
    completed = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _version(package):
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


def _conclusion(metric_rows):
    metrics = {row["metric"]: row for row in metric_rows}
    improvements = {
        metric: metrics[metric]["new_csv"] < metrics[metric]["current_csv"]
        for metric in (
            "max_abs_velocity",
            "max_abs_acceleration",
            "max_abs_jerk",
            "normalized_rmse_robust",
        )
    }
    return {
        "requested_hypothesis_supported": bool(all(improvements.values())),
        "component_improvements": improvements,
        "classification": (
            "supported"
            if all(improvements.values())
            else "not_supported_by_this_two_trace_comparison"
        ),
        "causal_status": (
            "descriptive_only; traces differ in duration, range, and shape"
        ),
    }


def write_run_manifest(output_dir, traces, artifacts, metric_rows):
    manifest = {
        "schema": "otg.csv-pvaj-tracking-comparison.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "branch": _git_value("branch", "--show-current"),
        "git_commit": _git_value("rev-parse", "HEAD"),
        "git_worktree_dirty": bool(_git_value("status", "--porcelain")),
        "data_convention": {
            "tracking_grid_period_ms": DT * 1000.0,
            "tracking_columns_used": ["value"],
            "source_timestamps_used_for_tracking": False,
            "source_timestamps_profiled_separately": True,
            "raw_pvaj_method": (
                "centered position finite differences and centered sampled "
                "acceleration gradient; common indices [3, n-3)"
            ),
            "target_timing": "target[k] -> output[k+1]",
            "motion_limits": VENDOR_LIMITS.as_dict(),
            "primary_tracking_method": PRIMARY_METHOD_ID,
            "window_diagnostic_samples": WINDOW_SAMPLES,
        },
        "inputs": [
            {
                "dataset": trace.dataset,
                "label": trace.label,
                "path": str(trace.path.relative_to(ROOT)),
                "rows": int(trace.position.size),
                "sha256": _sha256(trace.path),
            }
            for trace in traces
        ],
        "package_versions": {
            "python": sys.version.split()[0],
            "numpy": _version("numpy"),
            "scipy": _version("scipy"),
            "matplotlib": _version("matplotlib"),
            "ruckig": _version("ruckig"),
        },
        "conclusion": _conclusion(metric_rows),
        "artifacts": sorted(
            str(Path(path).relative_to(output_dir)) for path in artifacts
        ),
    }
    output = Path(output_dir) / "run.json"
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Compare raw PVAJ demand and ordinary-Ruckig tracking for two CSVs."
        )
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="write metric tables without rendering figures",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    traces = (
        load_trace(args.baseline, "current_csv", DATASET_LABELS["current_csv"]),
        load_trace(args.candidate, "new_csv", DATASET_LABELS["new_csv"]),
    )

    trace_rows = [profile_trace(trace) for trace in traces]
    fixed_signals = {trace.dataset: fixed_grid_pvaj(trace.position) for trace in traces}
    raw_rows = [
        row
        for trace in traces
        for row in pvaj_metric_rows(
            trace,
            fixed_signals[trace.dataset],
            "fixed_10ms_centered",
        )
    ]
    timing_sensitivity_rows = [
        row
        for trace in traces
        for row in pvaj_metric_rows(
            trace,
            actual_time_pvaj(trace),
            "source_elapsed_time_recursive_gradient",
        )
    ]
    references, results, tracking_rows = run_tracking_comparison(traces)
    metric_rows = build_metric_comparisons(trace_rows, raw_rows, tracking_rows)
    method_rows = build_tracking_method_comparisons(tracking_rows)
    pvaj_samples, tracking_samples = build_sample_rows(
        traces, fixed_signals, references, results
    )
    window_rows = build_window_diagnostics(traces, fixed_signals, references, results)
    relationship_rows = build_window_relationships(window_rows)

    artifacts = [
        _write_rows(trace_rows, output_dir / "trace_quality.csv"),
        _write_rows(raw_rows, output_dir / "raw_pvaj_metrics.csv"),
        _write_rows(
            timing_sensitivity_rows,
            output_dir / "source_time_pvaj_sensitivity.csv",
        ),
        _write_rows(tracking_rows, output_dir / "tracking_metrics.csv"),
        _write_rows(metric_rows, output_dir / "metric_comparison.csv"),
        _write_rows(method_rows, output_dir / "tracking_method_comparison.csv"),
        _write_rows(pvaj_samples, output_dir / "raw_pvaj_samples.csv"),
        _write_rows(tracking_samples, output_dir / "tracking_samples_p_only.csv"),
        _write_rows(window_rows, output_dir / "window_diagnostics.csv"),
        _write_rows(relationship_rows, output_dir / "window_relationships.csv"),
    ]
    if not args.no_plots:
        figures = [
            plot_raw_pvaj(traces, fixed_signals, output_dir),
            plot_tracking(traces, references, results, output_dir),
            plot_summary(metric_rows, method_rows, relationship_rows, output_dir),
            plot_window_relationship(window_rows, relationship_rows, output_dir),
        ]
        artifacts.extend(figures)
        artifacts.extend(path.with_suffix(".svg") for path in figures)
    manifest = write_run_manifest(output_dir, traces, artifacts, metric_rows)
    artifacts.append(manifest)
    print(f"Run directory: {output_dir}")
    print(f"Hypothesis status: {_conclusion(metric_rows)['classification']}")
    for artifact in artifacts:
        print(f"Saved: {artifact}")


if __name__ == "__main__":
    main()
