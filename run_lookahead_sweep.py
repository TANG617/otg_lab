"""Sweep estimator lookahead and Ruckig terminal-state configurations on CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from estimators import AlphaBetaGamma, RobustKalman
from otg_runner import compute_tracking_metrics, run_tracking_experiment
from run_output import prepare_run_directory
from run_experiments import DT, LIMITS, append_settle, csv_curve


HORIZONS_MS = (50, 60, 100, 150, 200, 250, 300)
TARGET_STATE_MODES = ("full", "position_only")
DURATION_MODES = ("matched", "control_cycle")


def make_estimator(family, lookahead):
    if family == "CA-KF":
        return RobustKalman(
            DT,
            measurement_sigma=0.01,
            jerk_spectral_density=1000.0,
            lookahead=lookahead,
        )
    if family == "ABG":
        return AlphaBetaGamma(
            DT,
            alpha=0.401,
            beta=0.11528,
            gamma=0.009504,
            lookahead=lookahead,
        )
    raise ValueError(f"Unknown estimator family: {family}")


def prediction_metrics(result, reference, original_count, horizon_steps):
    sample_count = original_count - horizon_steps
    predicted = result["raw_target_states"][1 : sample_count + 1, 0]
    future = reference[horizon_steps:original_count]
    error = predicted - future
    return {
        "prediction_position_rmse": float(np.sqrt(np.mean(error**2))),
        "prediction_position_mae": float(np.mean(np.abs(error))),
        "prediction_position_max_error": float(np.max(np.abs(error))),
    }


def run_sweep():
    raw_position, _ = csv_curve()
    original_count = raw_position.size
    position = append_settle(raw_position)
    rows = []
    tracking_250 = {}

    for family in ("CA-KF", "ABG"):
        for horizon_ms in HORIZONS_MS:
            lookahead = horizon_ms / 1000.0
            horizon_steps = int(round(lookahead / DT))
            for target_state_mode in TARGET_STATE_MODES:
                for duration_mode in DURATION_MODES:
                    minimum_duration = lookahead if duration_mode == "matched" else DT
                    estimator = make_estimator(family, lookahead)
                    result = run_tracking_experiment(
                        position,
                        estimator,
                        DT,
                        **LIMITS,
                        minimum_duration=minimum_duration,
                        target_state_mode=target_state_mode,
                    )
                    row = compute_tracking_metrics(
                        "csv",
                        position,
                        original_count,
                        estimator.name,
                        result,
                        DT,
                    )
                    row.update(
                        {
                            "estimator_family": family,
                            "duration_mode": duration_mode,
                            **prediction_metrics(
                                result,
                                position,
                                original_count,
                                horizon_steps,
                            ),
                        }
                    )
                    rows.append(row)
                    if horizon_ms == 250:
                        tracking_250[(family, target_state_mode, duration_mode)] = result
    return rows, tracking_250, position, original_count


def write_rows(rows, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "lookahead_sweep_metrics.csv"
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output


def select(rows, family, target_state_mode, duration_mode):
    selected = [
        row
        for row in rows
        if row["estimator_family"] == family
        and row["target_state_mode"] == target_state_mode
        and row["duration_mode"] == duration_mode
    ]
    return sorted(selected, key=lambda row: row["prediction_lookahead_ms"])


def plot_sweep(rows, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(17, 10), dpi=150, sharex=True)
    metrics = (
        ("rmse", "Output RMSE"),
        ("best_lag_ms", "Best global lag [ms]"),
        ("reachable_within_lookahead_rate", "Reachable within lookahead"),
    )
    colors = {"CA-KF": "tab:blue", "ABG": "tab:orange"}
    styles = {"matched": "-", "control_cycle": "--"}

    for row_index, target_mode in enumerate(TARGET_STATE_MODES):
        for column_index, (metric, label) in enumerate(metrics):
            axis = axes[row_index, column_index]
            for family in colors:
                for duration_mode in DURATION_MODES:
                    selected = select(rows, family, target_mode, duration_mode)
                    axis.plot(
                        [row["prediction_lookahead_ms"] for row in selected],
                        [row[metric] for row in selected],
                        color=colors[family],
                        linestyle=styles[duration_mode],
                        marker="o",
                        linewidth=0.8,
                        label=f"{family}, D={'H' if duration_mode == 'matched' else '10 ms'}",
                    )
            axis.axvline(250, color="0.5", linewidth=0.5, alpha=0.6)
            axis.grid(True, alpha=0.3)
            axis.set_ylabel(label)
            axis.set_title(
                f"{'Full p/v/a' if target_mode == 'full' else 'Predicted position only'} — {label}"
            )
            if metric == "reachable_within_lookahead_rate":
                axis.set_ylim(-0.03, 1.03)
            if row_index == 1:
                axis.set_xlabel("Prediction lookahead H [ms]")
            if row_index == 0 and column_index == 0:
                axis.legend(fontsize=8)

    fig.suptitle("CSV lookahead sweep: prediction horizon and Ruckig minimum duration", fontsize=15)
    fig.tight_layout()
    output = output_dir / "lookahead_sweep.png"
    fig.savefig(output, dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def plot_prediction_error(rows, output_dir):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), dpi=150, sharex=True)
    for family, color in (("CA-KF", "tab:blue"), ("ABG", "tab:orange")):
        selected = select(rows, family, "full", "matched")
        horizons = [row["prediction_lookahead_ms"] for row in selected]
        axes[0].plot(
            horizons,
            [row["prediction_position_rmse"] for row in selected],
            marker="o",
            color=color,
            linewidth=0.8,
            label=family,
        )
        axes[1].plot(
            horizons,
            [row["prediction_position_max_error"] for row in selected],
            marker="o",
            color=color,
            linewidth=0.8,
            label=family,
        )

    for axis, ylabel in zip(
        axes,
        ("Future position prediction RMSE", "Future position max error"),
    ):
        axis.axvline(250, color="0.5", linewidth=0.5, alpha=0.6)
        axis.set_xlabel("Prediction lookahead H [ms]")
        axis.set_ylabel(ylabel)
        axis.grid(True, alpha=0.3)
        axis.legend()
    fig.suptitle("CSV estimator-only future-position error")
    fig.tight_layout()
    output = output_dir / "prediction_error.png"
    fig.savefig(output, dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def plot_250_tracking(tracking_250, reference, original_count, output_dir):
    time = np.arange(reference.size) * DT
    fig, axes = plt.subplots(2, 2, figsize=(16, 9), dpi=150, sharex=True, sharey=True)
    for row_index, target_mode in enumerate(TARGET_STATE_MODES):
        for column_index, duration_mode in enumerate(DURATION_MODES):
            axis = axes[row_index, column_index]
            axis.plot(
                time[:original_count],
                reference[:original_count],
                "k--",
                linewidth=0.9,
                label="CSV reference",
            )
            for family, color in (("CA-KF", "tab:blue"), ("ABG", "tab:orange")):
                result = tracking_250[(family, target_mode, duration_mode)]
                axis.plot(
                    time[:original_count],
                    result["position"][:original_count],
                    color=color,
                    linewidth=0.7,
                    label=family,
                )
            axis.set_title(
                f"{'Full p/v/a' if target_mode == 'full' else 'Predicted position only'}, "
                f"D={'H=250 ms' if duration_mode == 'matched' else '10 ms'}"
            )
            axis.set_xlabel("Time [s]")
            axis.set_ylabel("Position")
            axis.grid(True, alpha=0.3)
            axis.legend(fontsize=8)
    fig.suptitle("CSV tracking with 250 ms prediction lookahead", fontsize=15)
    fig.tight_layout()
    output = output_dir / "lookahead_250_tracking.png"
    fig.savefig(output, dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def print_summary(rows):
    print("\n250 ms lookahead:")
    selected = [row for row in rows if row["prediction_lookahead_ms"] == 250.0]
    for row in sorted(
        selected,
        key=lambda item: (
            item["target_state_mode"],
            item["duration_mode"],
            item["rmse"],
        ),
    ):
        print(
            f"{row['estimator_family']:<5} "
            f"target={row['target_state_mode']:<13} "
            f"D={row['minimum_duration_ms']:>3.0f}ms  "
            f"RMSE={row['rmse']:.5f}  "
            f"lag={row['best_lag_ms']:+.0f}ms  "
            f"pred.RMSE={row['prediction_position_rmse']:.5f}  "
            f"reachable={100.0 * row['reachable_within_lookahead_rate']:.1f}%"
        )

    best = min(rows, key=lambda row: row["rmse"])
    print("\nBest sweep configuration by output RMSE:")
    print(
        f"{best['estimator_family']}, H={best['prediction_lookahead_ms']:.0f}ms, "
        f"target={best['target_state_mode']}, D={best['minimum_duration_ms']:.0f}ms, "
        f"RMSE={best['rmse']:.5f}, lag={best['best_lag_ms']:+.0f}ms"
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sweep estimator prediction lookahead on the CSV input."
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
        "lookahead-sweep",
        {
            "dt": f"{DT * 1000:g}ms",
            "h": "-".join(str(value) for value in HORIZONS_MS) + "ms",
            "vmax": LIMITS["max_velocity"],
            "amax": LIMITS["max_acceleration"],
            "jmax": LIMITS["max_jerk"],
        },
        args.output_dir,
    )
    rows, tracking_250, reference, original_count = run_sweep()
    metrics_output = write_rows(rows, output_dir)
    sweep_plot = plot_sweep(rows, output_dir)
    prediction_plot = plot_prediction_error(rows, output_dir)
    tracking_plot = plot_250_tracking(
        tracking_250,
        reference,
        original_count,
        output_dir,
    )
    print(f"Saved: {metrics_output}")
    print(f"Saved: {sweep_plot}")
    print(f"Saved: {prediction_plot}")
    print(f"Saved: {tracking_plot}")
    print(f"Run directory: {output_dir}")
    print_summary(rows)


if __name__ == "__main__":
    main()
