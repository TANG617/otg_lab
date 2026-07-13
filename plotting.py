"""Plots and CSV output for the OTG experiments."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt


def plot_tracking_result(
    dataset_name,
    title,
    time,
    target,
    original_count,
    results,
    output_dir,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fig, (ax_position, ax_error) = plt.subplots(
        2,
        1,
        figsize=(16, 12),
        dpi=150,
        sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.0]},
    )
    ax_position.plot(
        time[:original_count],
        target[:original_count],
        "k--",
        linewidth=1.0,
        label="Target position",
    )
    for method, result in results.items():
        ax_position.plot(time, result["position"], linewidth=0.7, label=method)
        ax_error.plot(
            time[:original_count],
            result["position"][:original_count] - target[:original_count],
            linewidth=0.6,
            label=method,
        )

    ax_position.set_title(f"{title} — real-time position-only estimators")
    ax_position.set_ylabel("Position")
    ax_position.grid(True, alpha=0.3)
    ax_position.legend(fontsize=8, ncol=2)
    ax_error.axhline(0.0, color="black", linewidth=0.5)
    ax_error.set_xlabel("Time [s]")
    ax_error.set_ylabel("Tracking error")
    ax_error.grid(True, alpha=0.3)
    fig.tight_layout()

    output = output_dir / f"ruckig_{dataset_name}.png"
    fig.savefig(output, dpi=600, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def write_metrics(rows, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "realtime_metrics.csv"
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output
