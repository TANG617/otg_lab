"""Isolate the value of target preview for ordinary Ruckig.

This is not an experiment with the Ruckig Pro Trackig interface.  It feeds
ordinary Ruckig either the exact current state of a feasible moving reference
or the exact state one control cycle ahead.  The comparison removes state
estimation error and inactive kinematic limits from the result.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from ruckig import InputParameter, OutputParameter, Ruckig

from estimators import CentralDifference10, RobustKalman
from otg_runner import (
    best_lag_metrics,
    compute_tracking_metrics,
    run_tracking_experiment,
)
from run_experiments import append_settle, csv_curve
from run_output import prepare_run_directory


DT = 0.01
SEGMENT_DURATION = 2.0
AMPLITUDE = 1.0
LIMITS = {
    "max_velocity": 4.1,
    "max_acceleration": 8.2,
    "max_jerk": 4000.0,
}
HISTORICAL_LIMITS = {
    "max_velocity": 4.1,
    "max_acceleration": 8.2,
    "max_jerk": 41.0,
}


def quintic_segment(local_time, upward=True):
    """Return analytic p/v/a for one stationary-endpoint quintic segment."""
    s = np.clip(local_time / SEGMENT_DURATION, 0.0, 1.0)
    position = 10.0 * s**3 - 15.0 * s**4 + 6.0 * s**5
    velocity = (30.0 * s**2 - 60.0 * s**3 + 30.0 * s**4)
    velocity /= SEGMENT_DURATION
    acceleration = (60.0 * s - 180.0 * s**2 + 120.0 * s**3)
    acceleration /= SEGMENT_DURATION**2

    if upward:
        return (
            AMPLITUDE * position,
            AMPLITUDE * velocity,
            AMPLITUDE * acceleration,
        )
    return (
        AMPLITUDE * (1.0 - position),
        -AMPLITUDE * velocity,
        -AMPLITUDE * acceleration,
    )


def analytic_reference():
    """Generate a 0 -> 1 -> 0 reference with continuous p/v/a."""
    time = np.arange(0.0, 2.0 * SEGMENT_DURATION + DT / 2.0, DT)
    position = np.empty_like(time)
    velocity = np.empty_like(time)
    acceleration = np.empty_like(time)
    rising = time <= SEGMENT_DURATION
    position[rising], velocity[rising], acceleration[rising] = quintic_segment(
        time[rising], upward=True
    )
    position[~rising], velocity[~rising], acceleration[~rising] = quintic_segment(
        time[~rising] - SEGMENT_DURATION, upward=False
    )
    return time, position, velocity, acceleration


def run_regular_ruckig(reference, preview_steps):
    """Track exact reference state k or k+1 with ordinary Ruckig.update()."""
    position, velocity, acceleration = reference
    otg = Ruckig(1, DT)
    inp = InputParameter(1)
    out = OutputParameter(1)
    inp.current_position = [float(position[0])]
    inp.current_velocity = [float(velocity[0])]
    inp.current_acceleration = [float(acceleration[0])]
    inp.max_velocity = [LIMITS["max_velocity"]]
    inp.max_acceleration = [LIMITS["max_acceleration"]]
    inp.max_jerk = [LIMITS["max_jerk"]]
    inp.minimum_duration = DT

    generated = np.empty((position.size, 3))
    generated[0] = [position[0], velocity[0], acceleration[0]]
    durations = []

    # At iteration k, current_* represents the generated state at t_k.
    # update() returns the state at t_{k+1}, stored in generated[k + 1].
    for index in range(position.size - 1):
        target_index = min(index + preview_steps, position.size - 1)
        inp.target_position = [float(position[target_index])]
        inp.target_velocity = [float(velocity[target_index])]
        inp.target_acceleration = [float(acceleration[target_index])]
        result = otg.update(inp, out)
        if int(result) < 0:
            raise RuntimeError(f"Ruckig error {result} at index={index}")

        generated[index + 1] = [
            out.new_position[0],
            out.new_velocity[0],
            out.new_acceleration[0],
        ]
        durations.append(out.trajectory.duration)
        out.pass_to_input(inp)

    error = generated[:, 0] - position
    lag_ms, aligned_rmse = best_lag_metrics(
        position, generated[:, 0], DT, max_lag_samples=50
    )
    metrics = {
        "preview_steps": preview_steps,
        "preview_ms": 1000.0 * preview_steps * DT,
        "rmse": float(np.sqrt(np.mean(error**2))),
        "max_error": float(np.max(np.abs(error))),
        "best_lag_ms": float(lag_ms),
        "lag_aligned_rmse": float(aligned_rmse),
        "trajectory_duration_p90_ms": float(
            1000.0 * np.percentile(durations, 90)
        ),
    }
    return generated, metrics


def write_metrics(rows, output_dir):
    output = output_dir / "oracle_preview_metrics.csv"
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output


def run_constraint_comparison():
    """Compare the historical assumption and vendor limits on the same CSV."""
    raw_position, _ = csv_curve()
    original_count = raw_position.size
    position = append_settle(raw_position)
    rows = []

    for limit_name, limits in (
        ("historical_4.1_8.2_41", HISTORICAL_LIMITS),
        ("vendor_4.1_8.2_4000", LIMITS),
    ):
        for estimator_name, make_estimator in (
            ("CA-KF, H=50 ms", lambda: RobustKalman(DT, lookahead=0.05)),
            (
                "3-point central, H=50 ms",
                lambda: CentralDifference10(DT, lookahead=0.05),
            ),
        ):
            estimator = make_estimator()
            result = run_tracking_experiment(
                position,
                estimator,
                DT,
                **limits,
            )
            metrics = compute_tracking_metrics(
                "csv",
                position,
                original_count,
                estimator.name,
                result,
                DT,
            )
            rows.append(
                {
                    "limits": limit_name,
                    **limits,
                    "method_short": estimator_name,
                    **metrics,
                }
            )
    return rows


def write_constraint_metrics(rows, output_dir):
    output = output_dir / "constraint_comparison_metrics.csv"
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output


def plot_constraint_comparison(rows, output_dir):
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), dpi=150)
    metrics = (
        ("rmse", "Position RMSE", 1.0),
        ("best_lag_ms", "Best global lag [ms]", 1.0),
        (
            "reachable_within_lookahead_rate",
            "Reachable within 50 ms [%]",
            100.0,
        ),
        ("trajectory_duration_p90_ms", "Trajectory duration P90 [ms]", 1.0),
    )
    methods = ("CA-KF, H=50 ms", "3-point central, H=50 ms")
    limit_groups = (
        ("historical_4.1_8.2_41", "Historical: 4.1 / 8.2 / 41"),
        ("vendor_4.1_8.2_4000", "Vendor-fixed: 4.1 / 8.2 / 4000"),
    )
    x = np.arange(len(methods))
    width = 0.34

    for axis, (metric, title, scale) in zip(axes.flat, metrics):
        for group_index, (limit_key, limit_label) in enumerate(limit_groups):
            selected = [
                next(
                    row
                    for row in rows
                    if row["limits"] == limit_key
                    and row["method_short"] == method
                )
                for method in methods
            ]
            values = [float(row[metric]) * scale for row in selected]
            axis.bar(
                x + (group_index - 0.5) * width,
                values,
                width,
                label=limit_label,
            )
        axis.set_xticks(x, methods, rotation=8, ha="right")
        axis.set_title(title)
        axis.grid(True, axis="y", alpha=0.3)

    axes[0, 0].legend(fontsize=9)
    fig.suptitle(
        "CSV ordinary-Ruckig performance: historical assumption vs vendor limits"
    )
    fig.tight_layout()
    output = output_dir / "constraint_comparison.png"
    fig.savefig(output, dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def plot_results(time, position, results, output_dir):
    fig, axes = plt.subplots(2, 1, figsize=(12, 7.5), dpi=150, sharex=True)
    axes[0].plot(time, position, "k--", linewidth=1.2, label="Reference")

    styles = (
        ("Exact current state", results[0], "tab:orange"),
        ("Exact state at t + 10 ms", results[1], "tab:blue"),
    )
    for label, generated, color in styles:
        axes[0].plot(
            time,
            generated[:, 0],
            color=color,
            linewidth=0.9,
            label=label,
        )
        axes[1].plot(
            time,
            generated[:, 0] - position,
            color=color,
            linewidth=0.9,
            label=label,
        )

    axes[0].set_ylabel("Position")
    axes[0].set_title(
        "Ordinary Ruckig with analytic truth: current target vs one-cycle oracle preview"
    )
    axes[0].legend()
    axes[1].axhline(0.0, color="black", linewidth=0.6)
    axes[1].set_xlabel("Time [s]")
    axes[1].set_ylabel("Tracking error")
    for axis in axes:
        axis.grid(True, alpha=0.3)

    fig.tight_layout()
    output = output_dir / "oracle_preview_isolation.png"
    fig.savefig(output, dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def parse_args():
    parser = argparse.ArgumentParser(
        description="Isolate target-preview effects with ordinary Ruckig."
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
        "tracking-necessity-oracle-preview",
        {
            "dt": f"{DT * 1000:g}ms",
            "vmax": LIMITS["max_velocity"],
            "amax": LIMITS["max_acceleration"],
            "jmax": LIMITS["max_jerk"],
        },
        args.output_dir,
    )

    time, position, velocity, acceleration = analytic_reference()
    current, current_metrics = run_regular_ruckig(
        (position, velocity, acceleration), preview_steps=0
    )
    next_cycle, next_metrics = run_regular_ruckig(
        (position, velocity, acceleration), preview_steps=1
    )

    sampled_jerk = np.diff(acceleration) / DT
    reference_metrics = {
        "reference_max_velocity": float(np.max(np.abs(velocity))),
        "reference_max_acceleration": float(np.max(np.abs(acceleration))),
        "reference_max_sampled_jerk": float(np.max(np.abs(sampled_jerk))),
    }
    rows = []
    for name, metrics in (
        ("exact_current_state", current_metrics),
        ("exact_next_cycle_state", next_metrics),
    ):
        rows.append({"target": name, **metrics, **reference_metrics})

    metrics_output = write_metrics(rows, output_dir)
    figure_output = plot_results(
        time, position, (current, next_cycle), output_dir
    )
    constraint_rows = run_constraint_comparison()
    constraint_metrics_output = write_constraint_metrics(
        constraint_rows, output_dir
    )
    constraint_figure_output = plot_constraint_comparison(
        constraint_rows, output_dir
    )
    print(f"Saved: {metrics_output}")
    print(f"Saved: {figure_output}")
    print(f"Saved: {figure_output.with_suffix('.svg')}")
    print(f"Saved: {constraint_metrics_output}")
    print(f"Saved: {constraint_figure_output}")
    print(f"Saved: {constraint_figure_output.with_suffix('.svg')}")
    for row in rows:
        print(
            f"{row['target']:<24} RMSE={row['rmse']:.9g} "
            f"lag={row['best_lag_ms']:+.0f}ms"
        )


if __name__ == "__main__":
    main()
