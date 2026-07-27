"""Reproduce and diagnose centered-difference PVA regressions.

The primary experiment uses the repository's fixed 10 ms control grid and the
ordinary-Ruckig runner.  A secondary sensitivity analysis uses each CSV's
``timestamp`` column only for derivative estimation; it is explicitly a proxy
because the files do not establish that this column is JointState.header.stamp.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_lab.centered_pva_analysis import (  # noqa: E402
    METHODS,
    build_ablation_targets,
    centered_difference_nonuniform,
)
from otg_runner import best_lag_metrics, run_target_state_sequence  # noqa: E402
from scripts.compare_csv_pvaj_tracking import load_trace  # noqa: E402
from target_state_experiment import DT, SETTLE_TIME, VENDOR_LIMITS  # noqa: E402

DEFAULT_NO_LIMIT = ROOT / "data" / "simplified-tasks_no-velocity-limit.csv"
DEFAULT_VELOCITY_LIMIT = ROOT / "data" / "simplified-tasks_velocity-limit.csv"
DEFAULT_OUTPUT = ROOT / "results" / "centered_pva_regression"
DATASETS = (
    (
        "no_velocity_limit",
        "Simplified · no velocity limit",
        DEFAULT_NO_LIMIT,
    ),
    (
        "velocity_limit",
        "Simplified · velocity limit",
        DEFAULT_VELOCITY_LIMIT,
    ),
)
EVALUATION_START = 3
MAX_SAMPLE_INTERVAL_S = 0.05
SAMPLE_METHODS = {
    "p_only_latest",
    "p_only_delayed",
    "centered_pva_delayed_clamped",
    "centered_pva_latest_position_clamped",
}
METHOD_COLORS = {
    "p_only_latest": "#1F5A85",
    "p_only_delayed": "#7A8793",
    "centered_pva_delayed_unclamped": "#C58A27",
    "centered_pv_delayed_clamped": "#6B5B95",
    "centered_pva_delayed_clamped": "#B5483A",
    "centered_pva_latest_position_clamped": "#2B7A6E",
    "centered_pva_propagated_clamped": "#8B6A9E",
    "centered_pva_offline_aligned_clamped": "#4E6E58",
}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(*args):
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        text=True,
    ).strip()


def _source_worktree_dirty(output_dir):
    output_dir = Path(output_dir).resolve()
    relative_output = output_dir.relative_to(ROOT).as_posix()
    status = _git_value("status", "--porcelain", "--untracked-files=all")
    relevant = []
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path == relative_output or path.startswith(f"{relative_output}/"):
            continue
        relevant.append(line)
    return bool(relevant)


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


def _extend_trace(trace, time_basis):
    settle_count = int(round(SETTLE_TIME / DT))
    position = np.concatenate(
        (
            trace.position,
            np.full(settle_count, trace.position[-1], dtype=float),
        )
    )
    if time_basis == "fixed_10ms":
        timestamps = np.arange(position.size, dtype=float) * DT
    elif time_basis == "csv_timestamp_proxy":
        source = trace.timestamp - trace.timestamp[0]
        tail = source[-1] + DT * np.arange(1, settle_count + 1, dtype=float)
        timestamps = np.concatenate((source, tail))
    else:
        raise ValueError(f"unsupported time basis: {time_basis}")
    return position, timestamps


def _safe_percentile(values, percentile):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return float(np.percentile(values, percentile)) if values.size else float("nan")


def _tracking_metrics(
    dataset,
    label,
    time_basis,
    original_count,
    position,
    built,
    result,
):
    start = EVALUATION_START
    stop = original_count
    reference = position[start:stop]
    output = result["position"][start:stop]
    error = output - reference
    scale = float(
        np.percentile(position[:stop], 95) - np.percentile(position[:stop], 5)
    )
    lag_ms, lag_aligned_rmse = best_lag_metrics(
        reference,
        output,
        DT,
        max_lag_samples=min(100, (reference.size - 1) // 2),
    )

    # output[i] is generated by target[i - 1].
    target_slice = slice(start - 1, stop - 1)
    preclamp = built.preclamp_states[target_slice]
    hard_clamped = built.states[target_slice]
    projected = result["target_states"][target_slice]
    velocity_clamp = built.velocity_clamp_mask[target_slice]
    acceleration_clamp = built.acceleration_clamp_mask[target_slice]
    valid = built.estimate_valid[target_slice]
    age = built.target_age_s[target_slice]
    age_residual = built.state_age_position_residual[target_slice]
    target_feasible = result["target_feasible_mask"][target_slice]
    projection = result["projection_mask"][target_slice]
    durations = result["trajectory_durations"][start:stop]

    preclamp_jerk = np.diff(preclamp[:, 2]) / DT
    hard_clamped_jerk = np.diff(hard_clamped[:, 2]) / DT
    projected_jerk = np.diff(projected[:, 2]) / DT
    output_acceleration = result["acceleration"][start:stop]
    output_sampled_jerk = (
        np.diff(result["acceleration"][start - 1 : stop]) / DT
    )

    return {
        "dataset": dataset,
        "dataset_label": label,
        "time_basis": time_basis,
        "method_id": built.method.method_id,
        "method": built.method.label,
        "causal": built.method.causal,
        "position_semantics": built.method.position_semantics,
        "derivative_semantics": built.method.derivative_semantics,
        "hard_clamp": built.method.hard_clamp,
        "primary_method": built.method.primary,
        "samples": stop - start,
        "evaluation_start_index": start,
        "evaluation_stop_index_exclusive": stop,
        "rmse_rad": float(np.sqrt(np.mean(error**2))),
        "normalized_rmse_robust": float(np.sqrt(np.mean(error**2)) / scale),
        "mae_rad": float(np.mean(np.abs(error))),
        "max_error_rad": float(np.max(np.abs(error))),
        "best_lag_ms": float(lag_ms),
        "lag_aligned_rmse_rad": float(lag_aligned_rmse),
        "lag_aligned_normalized_rmse": float(lag_aligned_rmse / scale),
        "estimate_valid_rate": float(np.mean(valid)),
        "target_age_median_ms": 1000.0 * _safe_percentile(age[age > 0.0], 50),
        "state_age_position_residual_rmse_rad": (
            float(np.sqrt(np.nanmean(age_residual**2)))
            if np.any(np.isfinite(age_residual))
            else 0.0
        ),
        "preclamp_max_abs_velocity_rad_s": float(
            np.max(np.abs(preclamp[:, 1]))
        ),
        "preclamp_max_abs_acceleration_rad_s2": float(
            np.max(np.abs(preclamp[:, 2]))
        ),
        "preclamp_p99_abs_acceleration_rad_s2": _safe_percentile(
            np.abs(preclamp[:, 2]), 99
        ),
        "preclamp_p99_abs_sampled_jerk_rad_s3": _safe_percentile(
            np.abs(preclamp_jerk), 99
        ),
        "velocity_hard_clamp_rate": float(np.mean(velocity_clamp)),
        "acceleration_hard_clamp_rate": float(np.mean(acceleration_clamp)),
        "any_hard_clamp_rate": float(
            np.mean(velocity_clamp | acceleration_clamp)
        ),
        "post_hard_clamp_infeasible_rate": float(np.mean(~target_feasible)),
        "ruckig_feasibility_projection_rate": float(np.mean(projection)),
        "hard_clamped_p99_abs_sampled_jerk_rad_s3": _safe_percentile(
            np.abs(hard_clamped_jerk), 99
        ),
        "projected_p99_abs_sampled_jerk_rad_s3": _safe_percentile(
            np.abs(projected_jerk), 99
        ),
        "projection_velocity_rmse_rad_s": float(
            np.sqrt(np.mean((projected[:, 1] - hard_clamped[:, 1]) ** 2))
        ),
        "projection_acceleration_rmse_rad_s2": float(
            np.sqrt(np.mean((projected[:, 2] - hard_clamped[:, 2]) ** 2))
        ),
        "reachable_within_10ms_rate": float(np.mean(durations <= DT + 1e-9)),
        "output_max_abs_velocity_rad_s": float(
            np.max(np.abs(result["velocity"][start:stop]))
        ),
        "output_max_abs_acceleration_rad_s2": float(
            np.max(np.abs(output_acceleration))
        ),
        "output_p99_abs_sampled_jerk_rad_s3": _safe_percentile(
            np.abs(output_sampled_jerk), 99
        ),
    }


def _run_method(trace, time_basis, method):
    position, timestamps = _extend_trace(trace, time_basis)
    built = build_ablation_targets(
        position,
        timestamps,
        method,
        VENDOR_LIMITS,
        max_sample_interval_s=MAX_SAMPLE_INTERVAL_S,
    )
    result = run_target_state_sequence(
        position,
        built.states,
        DT,
        **VENDOR_LIMITS.as_dict(),
        minimum_duration=DT,
        project_targets=True,
    )
    metrics = _tracking_metrics(
        trace.dataset,
        trace.label,
        time_basis,
        trace.position.size,
        position,
        built,
        result,
    )
    return position, timestamps, built, result, metrics


def _estimate_diagnostics(trace, time_basis):
    position, timestamps = _extend_trace(trace, time_basis)
    estimate = centered_difference_nonuniform(
        position,
        timestamps,
        max_sample_interval_s=MAX_SAMPLE_INTERVAL_S,
    )
    original_arrivals = np.flatnonzero(estimate.valid & (np.arange(position.size) < trace.position.size))
    h0 = estimate.h0[original_arrivals]
    h1 = estimate.h1[original_arrivals]
    v_c0 = -h1 / (h0 * (h0 + h1))
    v_c1 = (h1 - h0) / (h0 * h1)
    v_c2 = h0 / (h1 * (h0 + h1))
    a_c0 = 2.0 / (h0 * (h0 + h1))
    a_c1 = -2.0 / (h0 * h1)
    a_c2 = 2.0 / (h1 * (h0 + h1))
    velocity_noise_gain = np.abs(v_c0) + np.abs(v_c1) + np.abs(v_c2)
    acceleration_noise_gain = np.abs(a_c0) + np.abs(a_c1) + np.abs(a_c2)
    intervals = np.diff(timestamps[: trace.position.size])
    return {
        "dataset": trace.dataset,
        "dataset_label": trace.label,
        "time_basis": time_basis,
        "rows": trace.position.size,
        "valid_estimate_count": original_arrivals.size,
        "history_reset_gap_count": int(
            np.sum(intervals > MAX_SAMPLE_INTERVAL_S)
        ),
        "dt_min_ms": 1000.0 * float(np.min(intervals)),
        "dt_p50_ms": 1000.0 * float(np.percentile(intervals, 50)),
        "dt_p99_ms": 1000.0 * float(np.percentile(intervals, 99)),
        "dt_max_ms": 1000.0 * float(np.max(intervals)),
        "velocity_noise_gain_p50_per_s": _safe_percentile(
            velocity_noise_gain, 50
        ),
        "velocity_noise_gain_p99_per_s": _safe_percentile(
            velocity_noise_gain, 99
        ),
        "velocity_noise_gain_max_per_s": float(np.max(velocity_noise_gain)),
        "acceleration_noise_gain_p50_per_s2": _safe_percentile(
            acceleration_noise_gain, 50
        ),
        "acceleration_noise_gain_p99_per_s2": _safe_percentile(
            acceleration_noise_gain, 99
        ),
        "acceleration_noise_gain_max_per_s2": float(
            np.max(acceleration_noise_gain)
        ),
    }


def _build_contrasts(metric_rows):
    rows = []
    for dataset, label, _path in DATASETS:
        selected = {
            row["method_id"]: row
            for row in metric_rows
            if row["dataset"] == dataset and row["time_basis"] == "fixed_10ms"
        }
        baseline = selected["p_only_latest"]["normalized_rmse_robust"]
        comparisons = (
            (
                "delay_only",
                "P-only delayed − P-only latest",
                "p_only_latest",
                "p_only_delayed",
            ),
            (
                "centered_state_beyond_delay",
                "Unclamped centered PVA − delayed P-only",
                "p_only_delayed",
                "centered_pva_delayed_unclamped",
            ),
            (
                "independent_clamp_vs_joint_projection",
                "Independent clamp − joint feasibility projection",
                "centered_pva_delayed_unclamped",
                "centered_pva_delayed_clamped",
            ),
            (
                "remove_position_delay",
                "Latest-P centered PVA − production-like",
                "centered_pva_delayed_clamped",
                "centered_pva_latest_position_clamped",
            ),
            (
                "propagate_derivative_age",
                "P/V age compensated − latest-P centered PVA",
                "centered_pva_latest_position_clamped",
                "centered_pva_propagated_clamped",
            ),
            (
                "remove_target_acceleration",
                "Delayed PV − production-like PVA",
                "centered_pva_delayed_clamped",
                "centered_pv_delayed_clamped",
            ),
        )
        for mechanism, contrast, left_id, right_id in comparisons:
            left = selected[left_id]["normalized_rmse_robust"]
            right = selected[right_id]["normalized_rmse_robust"]
            rows.append(
                {
                    "dataset": dataset,
                    "dataset_label": label,
                    "mechanism": mechanism,
                    "contrast": contrast,
                    "left_method_id": left_id,
                    "right_method_id": right_id,
                    "left_normalized_rmse_robust": left,
                    "right_normalized_rmse_robust": right,
                    "absolute_nrmse_change": right - left,
                    "relative_change_vs_left_pct": 100.0 * (right - left) / abs(left),
                    "absolute_change_as_p_only_latest_pct": (
                        100.0 * (right - left) / abs(baseline)
                    ),
                    "preferred_direction": "negative",
                }
            )
    return rows


def _build_sample_rows(results):
    rows = []
    for dataset, methods in results.items():
        for method_id, payload in methods.items():
            if method_id not in SAMPLE_METHODS:
                continue
            position, _timestamps, built, result, _metrics, original_count = payload
            for index in range(original_count):
                rows.append(
                    {
                        "dataset": dataset,
                        "method_id": method_id,
                        "sample_index": index,
                        "time_s": index * DT,
                        "reference_position_rad": position[index],
                        "tracked_position_rad": result["position"][index],
                        "tracking_error_rad": (
                            result["position"][index] - position[index]
                        ),
                        "target_position_rad": built.states[index, 0],
                        "target_velocity_rad_s": built.states[index, 1],
                        "target_acceleration_rad_s2": built.states[index, 2],
                        "projected_target_velocity_rad_s": result[
                            "target_states"
                        ][index, 1],
                        "projected_target_acceleration_rad_s2": result[
                            "target_states"
                        ][index, 2],
                        "velocity_hard_clamped": bool(
                            built.velocity_clamp_mask[index]
                        ),
                        "acceleration_hard_clamped": bool(
                            built.acceleration_clamp_mask[index]
                        ),
                        "ruckig_feasibility_projected": bool(
                            result["projection_mask"][index]
                        ),
                    }
                )
    return rows


def _plot_tracking(results, output_dir):
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), constrained_layout=True)
    selected = (
        "p_only_latest",
        "centered_pva_delayed_clamped",
        "centered_pva_latest_position_clamped",
    )
    for axis, (dataset, label, _path) in zip(axes, DATASETS):
        methods = results[dataset]
        position, _timestamps, _built, _result, _metrics, original_count = methods[
            "p_only_latest"
        ]
        time = np.arange(original_count) * DT
        axis.plot(
            time,
            position[:original_count],
            color="#20262D",
            linewidth=1.2,
            label="Reference",
            zorder=4,
        )
        for method_id in selected:
            result = methods[method_id][3]
            label_text = methods[method_id][2].method.label
            axis.plot(
                time,
                result["position"][:original_count],
                color=METHOD_COLORS[method_id],
                linewidth=0.9,
                alpha=0.9,
                label=label_text,
            )
        axis.set_title(label)
        axis.set_ylabel("Position (rad)")
        axis.grid(alpha=0.18)
    axes[-1].set_xlabel("Time on fixed 10 ms grid (s)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside upper center", ncol=4, frameon=False)
    fig.suptitle("Same-reference tracking: P-only and centered-PVA variants")
    _save_figure(fig, output_dir / "tracking_comparison.png")


def _plot_metric_bars(metric_rows, output_dir):
    selected_ids = (
        "p_only_latest",
        "p_only_delayed",
        "centered_pva_delayed_unclamped",
        "centered_pva_delayed_clamped",
        "centered_pva_latest_position_clamped",
        "centered_pva_offline_aligned_clamped",
    )
    labels = {
        "p_only_latest": "P latest",
        "p_only_delayed": "P delayed",
        "centered_pva_delayed_unclamped": "PVA delayed\nno hard clamp",
        "centered_pva_delayed_clamped": "PVA production-like",
        "centered_pva_latest_position_clamped": "PVA latest P",
        "centered_pva_offline_aligned_clamped": "PVA offline aligned",
    }
    lookup = {
        (row["dataset"], row["method_id"]): row
        for row in metric_rows
        if row["time_basis"] == "fixed_10ms"
    }
    x = np.arange(len(selected_ids))
    width = 0.36
    fig, axis = plt.subplots(figsize=(13, 5.8), constrained_layout=True)
    for offset, (dataset, dataset_label, _path), color in zip(
        (-width / 2, width / 2),
        DATASETS,
        ("#B7791F", "#2F6B9A"),
    ):
        values = [
            lookup[(dataset, method_id)]["normalized_rmse_robust"]
            for method_id in selected_ids
        ]
        bars = axis.bar(
            x + offset,
            values,
            width,
            label=dataset_label,
            color=color,
        )
        axis.bar_label(bars, fmt="%.4f", fontsize=8, rotation=90, padding=3)
    axis.set_yscale("log")
    axis.set_ylabel("Robust-scale tracking NRMSE (log scale)")
    axis.set_xticks(x, [labels[method_id] for method_id in selected_ids])
    axis.grid(axis="y", alpha=0.2)
    axis.legend(frameon=False)
    axis.set_title("Centered-PVA ablation on the same reference trajectory")
    _save_figure(fig, output_dir / "nrmse_ablation.png")


def _plot_target_diagnostics(results, output_dir):
    fig, axes = plt.subplots(2, 2, figsize=(14, 8), constrained_layout=True)
    for row_index, (dataset, label, _path) in enumerate(DATASETS):
        payload = results[dataset]["centered_pva_delayed_clamped"]
        position, _timestamps, built, result, _metrics, original_count = payload
        time = np.arange(original_count) * DT
        axes[row_index, 0].plot(
            time,
            built.preclamp_states[:original_count, 2],
            color="#A9AFB5",
            linewidth=0.7,
            label="Raw centered A",
        )
        axes[row_index, 0].plot(
            time,
            built.states[:original_count, 2],
            color="#B5483A",
            linewidth=0.8,
            label="Independent hard clamp",
        )
        axes[row_index, 0].plot(
            time,
            result["target_states"][:original_count, 2],
            color="#1F5A85",
            linewidth=0.8,
            label="After feasibility projection",
        )
        axes[row_index, 0].set_title(f"{label} · target acceleration")
        axes[row_index, 0].set_ylabel("Acceleration (rad/s²)")
        axes[row_index, 0].set_yscale("symlog", linthresh=1.0)
        axes[row_index, 0].grid(alpha=0.18)

        baseline = results[dataset]["p_only_latest"][3]
        production = result
        axes[row_index, 1].plot(
            time,
            np.abs(
                baseline["position"][:original_count]
                - position[:original_count]
            ),
            color=METHOD_COLORS["p_only_latest"],
            linewidth=0.8,
            label="P-only latest",
        )
        axes[row_index, 1].plot(
            time,
            np.abs(
                production["position"][:original_count]
                - position[:original_count]
            ),
            color=METHOD_COLORS["centered_pva_delayed_clamped"],
            linewidth=0.8,
            label="Production-like centered PVA",
        )
        axes[row_index, 1].set_yscale("symlog", linthresh=1e-4)
        axes[row_index, 1].set_title(f"{label} · absolute tracking error")
        axes[row_index, 1].set_ylabel("|error| (rad)")
        axes[row_index, 1].grid(alpha=0.18)
    for axis in axes[-1]:
        axis.set_xlabel("Time on fixed 10 ms grid (s)")
    axes[0, 0].legend(frameon=False, fontsize=8)
    axes[0, 1].legend(frameon=False, fontsize=8)
    fig.suptitle("Derivative clipping, feasibility correction, and tracking error")
    _save_figure(fig, output_dir / "target_diagnostics.png")


def analyze(output_dir):
    output_dir = Path(output_dir).resolve()
    run_commit = _git_value("rev-parse", "HEAD")
    source_worktree_dirty = _source_worktree_dirty(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    traces = [
        load_trace(path, dataset, label)
        for dataset, label, path in DATASETS
    ]

    results = {}
    metric_rows = []
    for trace in traces:
        dataset_results = {}
        for method in METHODS:
            payload = _run_method(trace, "fixed_10ms", method)
            position, timestamps, built, result, metrics = payload
            metric_rows.append(metrics)
            dataset_results[method.method_id] = (
                position,
                timestamps,
                built,
                result,
                metrics,
                trace.position.size,
            )
        results[trace.dataset] = dataset_results

    timestamp_rows = []
    for trace in traces:
        for method_id in (
            "p_only_latest",
            "centered_pva_delayed_clamped",
        ):
            _position, _timestamps, _built, _result, metrics = _run_method(
                trace,
                "csv_timestamp_proxy",
                method_id,
            )
            timestamp_rows.append(metrics)

    estimate_rows = [
        _estimate_diagnostics(trace, time_basis)
        for trace in traces
        for time_basis in ("fixed_10ms", "csv_timestamp_proxy")
    ]
    contrast_rows = _build_contrasts(metric_rows)
    sample_rows = _build_sample_rows(results)

    _write_rows(metric_rows, output_dir / "tracking_metrics.csv")
    _write_rows(contrast_rows, output_dir / "mechanism_decomposition.csv")
    _write_rows(timestamp_rows, output_dir / "timestamp_sensitivity.csv")
    _write_rows(estimate_rows, output_dir / "estimator_diagnostics.csv")
    _write_rows(sample_rows, output_dir / "tracking_samples.csv")
    _plot_tracking(results, output_dir)
    _plot_metric_bars(metric_rows, output_dir)
    _plot_target_diagnostics(results, output_dir)

    inputs = []
    for trace in traces:
        inputs.append(
            {
                "dataset": trace.dataset,
                "label": trace.label,
                "path": str(trace.path.relative_to(ROOT)),
                "sha256": _sha256(trace.path),
                "rows": int(trace.position.size),
            }
        )
    manifest = {
        "schema": "otg.centered-pva-regression.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": {
            "branch": _git_value("branch", "--show-current"),
            "commit": run_commit,
            "working_tree_dirty_at_run": source_worktree_dirty,
            "dirty_check_excludes": str(output_dir.relative_to(ROOT)),
        },
        "inputs": inputs,
        "design": {
            "primary_time_basis": "fixed_10ms",
            "timestamp_sensitivity_basis": (
                "CSV timestamp as derivative-time proxy; provenance is not "
                "established as JointState.header.stamp"
            ),
            "controller_dt_ms": 10.0,
            "target_timing": "target[k] -> output[k+1]",
            "production_estimator_timing": (
                "arrival k emits P/V/A evaluated at k-1"
            ),
            "warmup_emulation": (
                "hold the previous target until the third valid position sample"
            ),
            "max_sample_interval_ms": 50.0,
            "limits": VENDOR_LIMITS.as_dict(),
            "hard_clamp": "independent velocity and acceleration clipping",
            "runner_feasibility_handling": (
                "jointly scale V/A only after the production-like hard clamp "
                "when the Ruckig target-state feasibility condition fails"
            ),
            "evaluation_start_index": EVALUATION_START,
            "settle_time_s": SETTLE_TIME,
        },
        "software": {
            "python": sys.version.split()[0],
            "numpy": metadata.version("numpy"),
            "ruckig": metadata.version("ruckig"),
            "matplotlib": metadata.version("matplotlib"),
        },
        "artifacts": [
            "tracking_metrics.csv",
            "mechanism_decomposition.csv",
            "timestamp_sensitivity.csv",
            "estimator_diagnostics.csv",
            "tracking_samples.csv",
            "tracking_comparison.png",
            "tracking_comparison.svg",
            "nrmse_ablation.png",
            "nrmse_ablation.svg",
            "target_diagnostics.png",
            "target_diagnostics.svg",
        ],
    }
    (output_dir / "run.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metric_rows, contrast_rows


def parse_args():
    parser = argparse.ArgumentParser(
        description="Diagnose production-semantics centered-PVA regressions."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main():
    args = parse_args()
    metrics, contrasts = analyze(args.output_dir)
    primary = {
        (row["dataset"], row["method_id"]): row
        for row in metrics
        if row["time_basis"] == "fixed_10ms"
    }
    print("Fixed-grid robust-scale NRMSE")
    for dataset, label, _path in DATASETS:
        p_only = primary[(dataset, "p_only_latest")][
            "normalized_rmse_robust"
        ]
        production = primary[(dataset, "centered_pva_delayed_clamped")][
            "normalized_rmse_robust"
        ]
        change = 100.0 * (production - p_only) / p_only
        print(
            f"  {label}: P-only={p_only:.6f}, "
            f"production-like PVA={production:.6f}, change={change:+.1f}%"
        )
    print(f"Mechanism contrasts: {len(contrasts)}")
    print(f"Saved: {Path(args.output_dir).resolve()}")


if __name__ == "__main__":
    main()
