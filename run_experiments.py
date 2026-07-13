"""Run the position-only state estimation and Ruckig tracking experiments."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from estimators import default_estimators
from otg_runner import compute_tracking_metrics, run_tracking_experiment
from plotting import plot_tracking_result, write_metrics
from run_output import prepare_run_directory


DT = 0.01  # 100 Hz / 10 ms
DURATION = 3.0
SETTLE_TIME = 2.0
SINE_AMPLITUDE = 0.37

LIMITS = {
    "max_velocity": 4.1,
    "max_acceleration": 16,
    "max_jerk": 3200,
}

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "plot_data.csv"


def elementary_curves():
    """Generate three reference curves with stationary endpoint states."""
    time = np.arange(0.0, DURATION + DT / 2.0, DT)
    tau = time / DURATION
    smootherstep = (
        35.0 * tau**4 - 84.0 * tau**5 + 70.0 * tau**6 - 20.0 * tau**7
    )
    parameter = DURATION * smootherstep
    centered = parameter - DURATION / 2.0
    omega = 2.0 * np.pi / DURATION

    return {
        "quadratic_with_extremum": (
            0.5 * centered**2,
            "7th-order time-scaled quadratic",
        ),
        "cubic": (
            0.12 * centered**3,
            "7th-order time-scaled cubic",
        ),
        "sine": (
            SINE_AMPLITUDE * np.sin(omega * parameter),
            "7th-order time-scaled sine",
        ),
    }


def csv_curve():
    """Read the value column and treat adjacent rows as one control cycle."""
    values = np.genfromtxt(CSV_PATH, delimiter=",", names=True)["value"]
    values = np.atleast_1d(values).astype(float)
    if values.size < 3 or not np.all(np.isfinite(values)):
        raise ValueError(f"{CSV_PATH} must contain at least 3 finite values")
    return values, "CSV value (fixed 10 ms per row)"


def append_settle(position):
    settle_count = int(round(SETTLE_TIME / DT))
    return np.concatenate((position, np.full(settle_count, position[-1])))


def run_dataset(dataset_name, data, output_dir):
    raw_position, title = data
    original_count = raw_position.size
    position = append_settle(raw_position)
    time = np.arange(position.size) * DT

    results = {}
    metrics = []
    for estimator in default_estimators(DT, **LIMITS):
        result = run_tracking_experiment(
            position,
            estimator,
            DT,
            **LIMITS,
        )
        results[estimator.name] = result
        metrics.append(
            compute_tracking_metrics(
                dataset_name,
                position,
                original_count,
                estimator.name,
                result,
                DT,
            )
        )

    plot_results = results
    if dataset_name == "csv":
        top_three = sorted(metrics, key=lambda row: row["rmse"])[:3]
        plot_results = {row["method"]: results[row["method"]] for row in top_three}
        title += " — top 3 methods by RMSE"

    output = plot_tracking_result(
        dataset_name,
        title,
        time,
        position,
        original_count,
        plot_results,
        output_dir,
    )
    return output, metrics


def print_csv_summary(rows):
    print("\nCSV summary (sorted by RMSE):")
    csv_metrics = sorted(
        (row for row in rows if row["dataset"] == "csv"),
        key=lambda row: row["rmse"],
    )
    for row in csv_metrics:
        print(
            f"{row['method']:<43} "
            f"RMSE={row['rmse']:.6f}  "
            f"lag={row['best_lag_ms']:+.0f}ms  "
            f"est.p99={row['estimator_compute_p99_us']:.1f}us"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run estimator and Ruckig tracking experiments."
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
        "estimator",
        {
            "dt": f"{DT * 1000:g}ms",
            "vmax": LIMITS["max_velocity"],
            "amax": LIMITS["max_acceleration"],
            "jmax": LIMITS["max_jerk"],
        },
        args.output_dir,
    )
    datasets = elementary_curves()
    datasets["csv"] = csv_curve()
    all_metrics = []

    for dataset_name, data in datasets.items():
        output, metrics = run_dataset(dataset_name, data, output_dir)
        all_metrics.extend(metrics)
        print(f"Saved: {output}")

    metrics_output = write_metrics(all_metrics, output_dir)
    print(f"Saved: {metrics_output}")
    print(f"Run directory: {output_dir}")
    print_csv_summary(all_metrics)


if __name__ == "__main__":
    main()
