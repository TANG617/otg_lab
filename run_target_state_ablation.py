"""Run controlled Ruckig target-state and motion-limit ablations.

The formal baseline fixes DT=10 ms, minimum_duration=10 ms, no lookahead,
and the vendor motion limits 4.1 / 8.2 / 4000.  At cycle k, target[k] is
passed to ordinary Ruckig and the returned state is stored at output[k+1].
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import numpy as np

from otg_runner import best_lag_metrics, run_target_state_sequence
from run_output import prepare_run_directory
from target_state_experiment import (
    VENDOR_LIMITS,
    MethodSpec,
    MotionLimits,
    build_next_cycle_oracle,
    build_target_states,
    csv_reference,
    derivative_quality_metrics,
    derivative_sources,
    elementary_references,
    methods_for_reference,
    reference_peak_metrics,
)
from target_state_plotting import (
    plot_ablation_summary,
    plot_dataset_ablation,
    plot_derivative_sources,
    plot_sensitivity_heatmaps,
)

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "plot_data.csv"
ACCELERATION_SWEEP = (4.1, 6.0, 8.2, 12.0, 16.4)
JERK_SWEEP = (41.0, 200.0, 800.0, 1600.0, 3200.0, 4000.0, 8000.0)
COMMON_WARMUP_SAMPLES = 3
SETTLE_TOLERANCE = 1e-3
PROVENANCE_FILES = (
    "plot_data.csv",
    "run_target_state_ablation.py",
    "target_state_experiment.py",
    "target_state_plotting.py",
    "otg_runner.py",
    "run_output.py",
)


def write_rows(rows, path):
    if not rows:
        raise ValueError(f"cannot write an empty metric table: {path}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _safe_percentile(values, percentile):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    return float(np.percentile(values, percentile))


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_version(package):
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


def _git_value(*args):
    completed = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _settle_metrics(reference, output_position):
    start = reference.original_count
    if start >= output_position.size:
        return float("nan"), float("nan"), float("nan")
    error = np.abs(output_position[start:] - reference.position[-1])
    settle_index = None
    for index in range(error.size):
        if np.all(error[index:] <= SETTLE_TOLERANCE):
            settle_index = index
            break
    settle_time_ms = (
        float("nan")
        if settle_index is None
        else 1000.0 * settle_index * reference.dt
    )
    return (
        settle_time_ms,
        float(np.max(error)),
        float(error[-1]),
    )


def compute_sequence_metrics(
    reference,
    method,
    result,
    limits,
    experiment="baseline",
    sweep_type="none",
    sweep_value=float("nan"),
):
    """Summarize target feasibility separately from final tracking quality."""
    start = COMMON_WARMUP_SAMPLES
    stop = reference.original_count
    if stop - start < 4:
        raise ValueError("evaluation interval is too short")

    ref = reference.position[start:stop]
    output = result["position"][start:stop]
    error = output - ref
    lag_ms, lag_aligned_rmse = best_lag_metrics(
        ref,
        output,
        reference.dt,
        max_lag_samples=min(100, (ref.size - 1) // 2),
    )

    # output[i] was generated from target[i-1].
    target_start = start - 1
    target_stop = stop - 1
    raw_target = result["raw_target_states"][target_start:target_stop]
    target = result["target_states"][target_start:target_stop]
    target_feasible = result["target_feasible_mask"][target_start:target_stop]
    projection_mask = result["projection_mask"][target_start:target_stop]
    target_velocity_delta = target[:, 1] - raw_target[:, 1]
    target_acceleration_delta = target[:, 2] - raw_target[:, 2]
    raw_target_jerk = np.diff(raw_target[:, 2]) / reference.dt

    output_velocity = result["velocity"][start:stop]
    output_acceleration = result["acceleration"][start:stop]
    output_new_jerk = result["new_jerk"][start:stop]
    output_sampled_jerk = (
        np.diff(result["acceleration"][start - 1 : stop]) / reference.dt
    )
    durations = result["trajectory_durations"][start:stop]
    ruckig_compute_us = result["ruckig_compute_us"][start - 1 : stop - 1]
    settle_time_ms, settle_max_error, settle_final_error = _settle_metrics(
        reference, result["position"]
    )

    return {
        "experiment": experiment,
        "dataset": reference.dataset,
        "method_id": method.method_id,
        "method": method.label,
        "result_group": method.result_group,
        "target_components": method.target_components,
        "derivative_source": method.derivative_source,
        "causal": method.causal,
        "future_samples": method.future_samples,
        "native_delay_samples": method.native_delay_samples,
        "target_timing": result["target_timing"],
        "evaluation_start_index": start,
        "evaluation_stop_index_exclusive": stop,
        "sweep_type": sweep_type,
        "sweep_value": sweep_value,
        "max_velocity_limit": limits.max_velocity,
        "max_acceleration_limit": limits.max_acceleration,
        "max_jerk_limit": limits.max_jerk,
        "minimum_duration_ms": result["minimum_duration_ms"],
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(np.abs(error))),
        "max_error": float(np.max(np.abs(error))),
        "best_lag_ms": float(lag_ms),
        "lag_aligned_rmse": float(lag_aligned_rmse),
        "raw_target_feasible_rate": float(np.mean(target_feasible)),
        "target_projection_rate": float(np.mean(projection_mask)),
        "projection_velocity_rmse": float(
            np.sqrt(np.mean(target_velocity_delta**2))
        ),
        "projection_acceleration_rmse": float(
            np.sqrt(np.mean(target_acceleration_delta**2))
        ),
        "raw_target_max_velocity": float(
            np.max(np.abs(raw_target[:, 1]))
        ),
        "raw_target_max_acceleration": float(
            np.max(np.abs(raw_target[:, 2]))
        ),
        "raw_target_max_sampled_jerk": (
            float(np.max(np.abs(raw_target_jerk)))
            if raw_target_jerk.size
            else 0.0
        ),
        "projected_target_max_velocity": float(
            np.max(np.abs(target[:, 1]))
        ),
        "projected_target_max_acceleration": float(
            np.max(np.abs(target[:, 2]))
        ),
        "trajectory_duration_p50_ms": 1000.0
        * _safe_percentile(durations, 50),
        "trajectory_duration_p90_ms": 1000.0
        * _safe_percentile(durations, 90),
        "trajectory_duration_p99_ms": 1000.0
        * _safe_percentile(durations, 99),
        "reachable_within_10ms_rate": float(
            np.mean(durations <= reference.dt + 1e-9)
        ),
        "output_max_velocity": float(np.max(np.abs(output_velocity))),
        "output_max_acceleration": float(np.max(np.abs(output_acceleration))),
        "output_max_new_jerk": float(
            np.max(np.abs(output_new_jerk))
        ),
        "output_max_sampled_jerk": float(
            np.max(np.abs(output_sampled_jerk))
        ),
        "output_velocity_saturation_rate": float(
            np.mean(np.abs(output_velocity) >= limits.max_velocity * (1 - 1e-7))
        ),
        "output_acceleration_saturation_rate": float(
            np.mean(
                np.abs(output_acceleration)
                >= limits.max_acceleration * (1 - 1e-7)
            )
        ),
        "output_new_jerk_saturation_rate": float(
            np.mean(
                np.abs(output_new_jerk)
                >= limits.max_jerk * (1 - 1e-7)
            )
        ),
        "ruckig_compute_p99_us": _safe_percentile(ruckig_compute_us, 99),
        "ruckig_compute_max_us": float(np.max(ruckig_compute_us)),
        "settle_time_to_1e3_ms": settle_time_ms,
        "settle_max_error": settle_max_error,
        "settle_final_error": settle_final_error,
    }


def run_method(reference, method, limits):
    raw_target = build_target_states(reference, method)
    result = run_target_state_sequence(
        reference.position,
        raw_target,
        reference.dt,
        **limits.as_dict(),
        minimum_duration=reference.dt,
        project_targets=True,
    )
    metrics = compute_sequence_metrics(reference, method, result, limits)
    return result, metrics


def run_baseline(references):
    all_results = {}
    rows = []
    for dataset, reference in references.items():
        dataset_results = {}
        for method in methods_for_reference(reference):
            result, metrics = run_method(reference, method, VENDOR_LIMITS)
            dataset_results[method.method_id] = result
            rows.append(metrics)
        all_results[dataset] = dataset_results
    return all_results, rows


def run_oracle_controls(references):
    oracle_method = MethodSpec(
        method_id="oracle_next_cycle",
        label="PVA · next-cycle analytic oracle",
        target_components="pva",
        derivative_source="analytic_truth",
        causal=False,
        future_samples=1,
        warmup_samples=0,
        result_group="sanity_control",
    )
    rows = []
    for reference in references.values():
        if not reference.has_analytic_truth:
            continue
        result = run_target_state_sequence(
            reference.position,
            build_next_cycle_oracle(reference),
            reference.dt,
            **VENDOR_LIMITS.as_dict(),
            minimum_duration=reference.dt,
            project_targets=True,
        )
        rows.append(
            compute_sequence_metrics(
                reference,
                oracle_method,
                result,
                VENDOR_LIMITS,
                experiment="oracle_sanity_control",
            )
        )
    return rows


def run_sensitivity(references):
    rows = []
    for reference in references.values():
        for method in methods_for_reference(reference):
            raw_target = build_target_states(reference, method)
            for max_acceleration in ACCELERATION_SWEEP:
                limits = MotionLimits(
                    VENDOR_LIMITS.max_velocity,
                    max_acceleration,
                    VENDOR_LIMITS.max_jerk,
                )
                result = run_target_state_sequence(
                    reference.position,
                    raw_target,
                    reference.dt,
                    **limits.as_dict(),
                    minimum_duration=reference.dt,
                    project_targets=True,
                )
                rows.append(
                    compute_sequence_metrics(
                        reference,
                        method,
                        result,
                        limits,
                        experiment="limit_sensitivity",
                        sweep_type="acceleration",
                        sweep_value=max_acceleration,
                    )
                )
            for max_jerk in JERK_SWEEP:
                limits = MotionLimits(
                    VENDOR_LIMITS.max_velocity,
                    VENDOR_LIMITS.max_acceleration,
                    max_jerk,
                )
                result = run_target_state_sequence(
                    reference.position,
                    raw_target,
                    reference.dt,
                    **limits.as_dict(),
                    minimum_duration=reference.dt,
                    project_targets=True,
                )
                rows.append(
                    compute_sequence_metrics(
                        reference,
                        method,
                        result,
                        limits,
                        experiment="limit_sensitivity",
                        sweep_type="jerk",
                        sweep_value=max_jerk,
                    )
                )
    return rows


def update_manifest(output_dir, mode, artifacts):
    path = Path(output_dir) / "run.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "data_convention": {
                "csv_columns_used": ["value"],
                "ignored_columns": ["elapsed time", "timestamp", "topic"],
                "row_period_ms": 10.0,
            },
            "design": {
                "mode": mode,
                "target_timing": "target[k] -> output[k+1]",
                "minimum_duration_ms": 10.0,
                "lookahead_ms": 0.0,
                "common_warmup_samples": COMMON_WARMUP_SAMPLES,
                "target_projection": "project_target_state; raw feasibility and distortion are reported separately",
                "offline_centered_fd": "one future sample; noncausal diagnostic",
                "causal_centered_fd": "delay-one estimate propagated to latest sample",
            },
            "sensitivity": {
                "acceleration_limits": list(ACCELERATION_SWEEP),
                "acceleration_fixed_jerk": VENDOR_LIMITS.max_jerk,
                "jerk_limits": list(JERK_SWEEP),
                "jerk_fixed_acceleration": VENDOR_LIMITS.max_acceleration,
                "interpretation": "one-factor sensitivity only; not a deployment-parameter recommendation",
            },
            "provenance": {
                "python_version": sys.version.split()[0],
                "package_versions": {
                    "numpy": _package_version("numpy"),
                    "matplotlib": _package_version("matplotlib"),
                    "ruckig": _package_version("ruckig"),
                },
                "git_commit": _git_value("rev-parse", "HEAD"),
                "git_worktree_dirty": bool(_git_value("status", "--porcelain")),
                "input_csv_rows": sum(
                    1 for _ in CSV_PATH.open("r", encoding="utf-8")
                )
                - 1,
                "sha256": {
                    name: _sha256(ROOT / name) for name in PROVENANCE_FILES
                },
            },
            "artifacts": sorted(str(Path(item).name) for item in artifacts),
        }
    )
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run controlled target-state and limit-sensitivity experiments."
    )
    parser.add_argument(
        "--mode",
        choices=("baseline", "sensitivity", "all"),
        default="all",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="override the automatically named directory under runs/",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = prepare_run_directory(
        "vendor-target-state-ablation",
        {
            "dt": "10ms",
            "vmax": VENDOR_LIMITS.max_velocity,
            "amax": VENDOR_LIMITS.max_acceleration,
            "jmax": VENDOR_LIMITS.max_jerk,
            "mode": args.mode,
        },
        args.output_dir,
    )
    references = elementary_references()
    references["csv"] = csv_reference(CSV_PATH)
    methods = {
        method.method_id: method
        for reference in references.values()
        for method in methods_for_reference(reference)
    }
    method_labels = {method_id: method.label for method_id, method in methods.items()}
    artifacts = []

    derivative_rows = derivative_quality_metrics(references)
    derivative_output = write_rows(
        derivative_rows, output_dir / "derivative_source_metrics.csv"
    )
    artifacts.append(derivative_output)
    peak_rows = [
        reference_peak_metrics(reference)
        for reference in references.values()
        if reference.has_analytic_truth
    ]
    peak_output = write_rows(
        peak_rows, output_dir / "reference_peak_metrics.csv"
    )
    artifacts.append(peak_output)
    derivative_figure = plot_derivative_sources(
        references,
        {
            dataset: derivative_sources(reference)
            for dataset, reference in references.items()
            if reference.has_analytic_truth
        },
        output_dir,
    )
    artifacts.extend((derivative_figure, derivative_figure.with_suffix(".svg")))

    if args.mode in {"baseline", "all"}:
        baseline_results, baseline_rows = run_baseline(references)
        baseline_output = write_rows(
            baseline_rows, output_dir / "target_state_ablation_metrics.csv"
        )
        artifacts.append(baseline_output)
        oracle_rows = run_oracle_controls(references)
        oracle_output = write_rows(
            oracle_rows, output_dir / "oracle_sanity_metrics.csv"
        )
        artifacts.append(oracle_output)
        for dataset, reference in references.items():
            figure = plot_dataset_ablation(
                reference,
                baseline_results[dataset],
                methods_for_reference(reference),
                output_dir,
                **VENDOR_LIMITS.as_dict(),
            )
            artifacts.extend((figure, figure.with_suffix(".svg")))
        summary = plot_ablation_summary(
            baseline_rows, method_labels, output_dir
        )
        artifacts.extend((summary, summary.with_suffix(".svg")))

    if args.mode in {"sensitivity", "all"}:
        sensitivity_rows = run_sensitivity(references)
        sensitivity_output = write_rows(
            sensitivity_rows, output_dir / "limit_sensitivity_metrics.csv"
        )
        artifacts.append(sensitivity_output)
        rmse_figure = plot_sensitivity_heatmaps(
            sensitivity_rows,
            method_labels,
            "rmse",
            {"acceleration": 8.2, "jerk": 4000.0},
            output_dir,
        )
        lag_figure = plot_sensitivity_heatmaps(
            sensitivity_rows,
            method_labels,
            "best_lag_ms",
            {"acceleration": 8.2, "jerk": 4000.0},
            output_dir,
        )
        artifacts.extend(
            (
                rmse_figure,
                rmse_figure.with_suffix(".svg"),
                lag_figure,
                lag_figure.with_suffix(".svg"),
            )
        )

    update_manifest(output_dir, args.mode, artifacts)
    print(f"Run directory: {output_dir}")
    for artifact in artifacts:
        print(f"Saved: {artifact}")


if __name__ == "__main__":
    main()
