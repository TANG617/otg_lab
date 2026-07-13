"""Sweep Ruckig acceleration and jerk limits for causal central differences.

The CSV experiment is kept identical to ``run_experiments.py`` except for
``max_acceleration`` and ``max_jerk``.  In particular, max velocity remains
4.1, and the causal three-point central-difference estimator keeps its 10 ms
fixed lag and 50 ms prediction lookahead.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np

from estimators import CentralDifference10
from otg_runner import compute_tracking_metrics, run_tracking_experiment
from run_output import prepare_run_directory
from run_experiments import DT, LIMITS, append_settle, csv_curve


LOOKAHEAD = 0.05
ACCELERATION_LIMITS = np.array(
    [4, 6, 8.2, 12, 16, 20, 24, 32, 40, 48, 52, 56, 58, 59, 60, 61, 64, 68, 72, 80],
    dtype=float,
)
JERK_LIMITS = np.array(
    [20, 41, 80, 200, 400, 800, 1600, 3200, 6400, 12800, 19200, 25600, 38400, 51200],
    dtype=float,
)
KNEE_RMSE_TOLERANCE = 0.03
RECOMMENDED_MAX_ACCELERATION = 60.0
RECOMMENDED_MAX_JERK = 25600.0


def run_configuration(reference, original_count, max_acceleration, max_jerk):
    estimator = CentralDifference10(DT, lookahead=LOOKAHEAD)
    result = run_tracking_experiment(
        reference,
        estimator,
        DT,
        max_velocity=LIMITS["max_velocity"],
        max_acceleration=float(max_acceleration),
        max_jerk=float(max_jerk),
    )
    metrics = compute_tracking_metrics(
        "csv",
        reference,
        original_count,
        estimator.name,
        result,
        DT,
    )
    metrics.update(
        {
            "max_velocity_limit": LIMITS["max_velocity"],
            "max_acceleration_limit": float(max_acceleration),
            "max_jerk_limit": float(max_jerk),
        }
    )
    return result, metrics


def run_sweep(reference, original_count):
    rows = []
    failures = []
    for max_acceleration in ACCELERATION_LIMITS:
        for max_jerk in JERK_LIMITS:
            try:
                _, metrics = run_configuration(
                    reference,
                    original_count,
                    max_acceleration,
                    max_jerk,
                )
                rows.append(metrics)
            except Exception as error:  # Keep the rest of the grid useful.
                failures.append(
                    {
                        "max_acceleration_limit": float(max_acceleration),
                        "max_jerk_limit": float(max_jerk),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
    return rows, failures


def select_candidates(rows):
    best = min(rows, key=lambda row: row["rmse"])
    threshold = best["rmse"] * (1.0 + KNEE_RMSE_TOLERANCE)
    knee = next(
        row
        for row in rows
        if np.isclose(
            row["max_acceleration_limit"], RECOMMENDED_MAX_ACCELERATION
        )
        and np.isclose(row["max_jerk_limit"], RECOMMENDED_MAX_JERK)
    )
    if knee["rmse"] > threshold:
        raise RuntimeError(
            "The configured practical knee is no longer within "
            f"{100.0 * KNEE_RMSE_TOLERANCE:.0f}% of the grid-best RMSE"
        )
    baseline = next(
        row
        for row in rows
        if np.isclose(
            row["max_acceleration_limit"], LIMITS["max_acceleration"]
        )
        and np.isclose(row["max_jerk_limit"], LIMITS["max_jerk"])
    )
    return baseline, knee, best


def metric_grid(rows, metric):
    lookup = {
        (row["max_acceleration_limit"], row["max_jerk_limit"]): row[metric]
        for row in rows
    }
    return np.array(
        [
            [lookup.get((acceleration, jerk), np.nan) for acceleration in ACCELERATION_LIMITS]
            for jerk in JERK_LIMITS
        ],
        dtype=float,
    )


def configuration_label(row):
    return (
        f"a={row['max_acceleration_limit']:g}, "
        f"j={row['max_jerk_limit']:g}"
    )


def mark_configuration(axis, row, marker, color, label):
    x = int(np.flatnonzero(np.isclose(ACCELERATION_LIMITS, row["max_acceleration_limit"]))[0])
    y = int(np.flatnonzero(np.isclose(JERK_LIMITS, row["max_jerk_limit"]))[0])
    axis.plot(
        x,
        y,
        marker=marker,
        markersize=10,
        markerfacecolor="none",
        markeredgecolor=color,
        markeredgewidth=2.0,
        linestyle="none",
        label=label,
    )


def format_heatmap(axis, title):
    axis.set_title(title)
    axis.set_xlabel("max acceleration")
    axis.set_ylabel("max jerk")
    axis.set_xticks(np.arange(ACCELERATION_LIMITS.size))
    axis.set_xticklabels([f"{value:g}" for value in ACCELERATION_LIMITS], rotation=60)
    axis.set_yticks(np.arange(JERK_LIMITS.size))
    axis.set_yticklabels([f"{value:g}" for value in JERK_LIMITS])


def plot_summary(rows, reference, original_count, candidates, output_dir):
    baseline, knee, best = candidates
    rmse = metric_grid(rows, "rmse")
    max_error = metric_grid(rows, "max_error")

    fig, axes = plt.subplots(2, 2, figsize=(20, 14), dpi=150)
    rmse_axis, error_axis, curves_axis, tracking_axis = axes.ravel()

    rmse_image = rmse_axis.imshow(
        rmse,
        origin="lower",
        aspect="auto",
        cmap="viridis_r",
        norm=LogNorm(vmin=np.nanmin(rmse), vmax=np.nanmax(rmse)),
    )
    format_heatmap(rmse_axis, "Raw-time tracking RMSE (lower is better)")
    fig.colorbar(rmse_image, ax=rmse_axis, label="RMSE")

    error_image = error_axis.imshow(
        max_error,
        origin="lower",
        aspect="auto",
        cmap="magma_r",
        norm=LogNorm(vmin=np.nanmin(max_error), vmax=np.nanmax(max_error)),
    )
    format_heatmap(error_axis, "Maximum absolute tracking error")
    fig.colorbar(error_image, ax=error_axis, label="max |error|")

    for axis in (rmse_axis, error_axis):
        mark_configuration(axis, baseline, "s", "white", "current 8.2 / 41")
        mark_configuration(axis, knee, "o", "cyan", "recommended ≤3% knee")
        mark_configuration(axis, best, "*", "red", "grid-best RMSE")
        axis.legend(loc="upper right", fontsize=8, framealpha=0.9)

    selected_accelerations = sorted(
        {
            LIMITS["max_acceleration"],
            24.0,
            32.0,
            knee["max_acceleration_limit"],
            best["max_acceleration_limit"],
            64.0,
        }
    )
    for acceleration in selected_accelerations:
        selected = sorted(
            (
                row
                for row in rows
                if np.isclose(row["max_acceleration_limit"], acceleration)
            ),
            key=lambda row: row["max_jerk_limit"],
        )
        curves_axis.plot(
            [row["max_jerk_limit"] for row in selected],
            [row["rmse"] for row in selected],
            marker="o",
            markersize=3.5,
            linewidth=0.7,
            label=f"max a={acceleration:g}",
        )
    curves_axis.set_xscale("log")
    curves_axis.set_yscale("log")
    curves_axis.set_xlabel("max jerk")
    curves_axis.set_ylabel("RMSE")
    curves_axis.set_title("Jerk sweep at representative acceleration limits")
    curves_axis.grid(True, which="both", alpha=0.3)
    curves_axis.legend(fontsize=8, ncol=2)

    time = np.arange(reference.size) * DT
    tracking_axis.plot(
        time[:original_count],
        reference[:original_count],
        "k--",
        linewidth=0.9,
        label="CSV reference",
    )
    styles = (
        (baseline, "tab:gray", "current"),
        (knee, "tab:blue", "recommended knee"),
        (best, "tab:red", "grid-best"),
    )
    for row, color, name in styles:
        result, _ = run_configuration(
            reference,
            original_count,
            row["max_acceleration_limit"],
            row["max_jerk_limit"],
        )
        tracking_axis.plot(
            time[:original_count],
            result["position"][:original_count],
            color=color,
            linewidth=0.7,
            label=(
                f"{name}: {configuration_label(row)}, "
                f"RMSE={row['rmse']:.5f}"
            ),
        )
    tracking_axis.set_title("CSV tracking comparison")
    tracking_axis.set_xlabel("Time [s]")
    tracking_axis.set_ylabel("Position")
    tracking_axis.grid(True, alpha=0.3)
    tracking_axis.legend(fontsize=8)

    fig.suptitle(
        "Causal 3-point central difference: acceleration × jerk limit sweep\n"
        "fixed max velocity=4.1, 10 ms lag, 50 ms lookahead, 10 ms control cycle",
        fontsize=16,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "central_limit_sweep.png"
    fig.savefig(output, dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def write_rows(rows, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "central_limit_sweep_metrics.csv"
    fieldnames = (
        "max_velocity_limit",
        "max_acceleration_limit",
        "max_jerk_limit",
        *(
            field
            for field in rows[0]
            if field
            not in {
                "max_velocity_limit",
                "max_acceleration_limit",
                "max_jerk_limit",
            }
        ),
    )
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output


def print_candidate(name, row):
    print(
        f"{name:<18} {configuration_label(row):<24} "
        f"RMSE={row['rmse']:.6f}  MAE={row['mae']:.6f}  "
        f"max_error={row['max_error']:.6f}  "
        f"lag={row['best_lag_ms']:+.0f} ms  "
        f"projected={100.0 * row['target_projection_rate']:.1f}%"
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sweep Ruckig acceleration and jerk limits."
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
        "central-limit-sweep",
        {
            "dt": f"{DT * 1000:g}ms",
            "lookahead": f"{LOOKAHEAD * 1000:g}ms",
            "vmax": LIMITS["max_velocity"],
            "agrid": (
                f"{ACCELERATION_LIMITS[0]:g}to{ACCELERATION_LIMITS[-1]:g}"
                f"-n{ACCELERATION_LIMITS.size}"
            ),
            "jgrid": (
                f"{JERK_LIMITS[0]:g}to{JERK_LIMITS[-1]:g}"
                f"-n{JERK_LIMITS.size}"
            ),
        },
        args.output_dir,
    )
    raw_position, _ = csv_curve()
    original_count = raw_position.size
    reference = append_settle(raw_position)
    rows, failures = run_sweep(reference, original_count)
    if not rows:
        raise RuntimeError("Every acceleration/jerk configuration failed")

    candidates = select_candidates(rows)
    metrics_output = write_rows(rows, output_dir)
    figure_output = plot_summary(
        rows,
        reference,
        original_count,
        candidates,
        output_dir,
    )

    print(f"Successful configurations: {len(rows)}")
    print(f"Failed configurations: {len(failures)}")
    for failure in failures:
        print(
            "FAILED "
            f"a={failure['max_acceleration_limit']:g}, "
            f"j={failure['max_jerk_limit']:g}: {failure['error']}"
        )
    print_candidate("Current baseline", candidates[0])
    print_candidate("Recommended knee", candidates[1])
    print_candidate("Grid-best RMSE", candidates[2])
    print(f"Saved: {metrics_output}")
    print(f"Saved: {figure_output}")
    print(f"Saved: {figure_output.with_suffix('.svg')}")
    print(f"Run directory: {output_dir}")


if __name__ == "__main__":
    main()
