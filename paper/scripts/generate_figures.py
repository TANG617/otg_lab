#!/usr/bin/env python3
"""Generate self-contained vector figures for the stage manuscript."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
EVIDENCE = PAPER_ROOT / "generated/manifests/extracted_evidence.json"
OUT = PAPER_ROOT / "figures/generated"
COLORS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#666666"]
PDF_TIMESTAMP = datetime(2026, 7, 23, tzinfo=timezone.utc)


def save(fig: plt.Figure, name: str) -> None:
    path = OUT / name
    fig.savefig(
        path,
        bbox_inches="tight",
        metadata={
            "Creator": "otg_lab paper pipeline",
            "CreationDate": PDF_TIMESTAMP,
            "ModDate": PDF_TIMESTAMP,
        },
    )
    plt.close(fig)


def box(ax: plt.Axes, xy: tuple[float, float], size: tuple[float, float], text: str, color: str) -> None:
    patch = FancyBboxPatch(
        xy,
        *size,
        boxstyle="round,pad=0.02",
        facecolor=color,
        edgecolor="black",
        linewidth=0.8,
        alpha=0.16,
    )
    ax.add_patch(patch)
    ax.text(xy[0] + size[0] / 2, xy[1] + size[1] / 2, text, ha="center", va="center", fontsize=8)


def architecture() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 2.3))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    ax.axis("off")
    labels = [
        ("position-only\nreference", COLORS[0]),
        ("state\nestimator", COLORS[1]),
        ("future-reference\ngenerator", COLORS[2]),
        ("executable-target\ngovernor", COLORS[3]),
        ("constrained\nfollower", COLORS[0]),
        ("command /\nplant", COLORS[4]),
    ]
    xs = [0.05, 1.75, 3.45, 5.15, 6.85, 8.55]
    for x, (label, color) in zip(xs, labels):
        box(ax, (x, 1.15), (1.35, 0.72), label, color)
    for left, right in zip(xs[:-1], xs[1:]):
        ax.add_patch(FancyArrowPatch((left + 1.35, 1.51), (right, 1.51), arrowstyle="->", mutation_scale=11))
    ax.add_patch(FancyArrowPatch((9.25, 1.12), (5.82, 0.45), connectionstyle="arc3,rad=-0.22", arrowstyle="->", mutation_scale=11, linestyle="--"))
    ax.text(7.7, 0.38, "current measured or commanded state", fontsize=7, ha="center")
    ax.text(5.0, 2.45, "Time-explicit layered reference-following architecture", ha="center", fontsize=10, weight="bold")
    save(fig, "architecture.pdf")


def timeline() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    ax.set_xlim(-0.2, 4.2)
    ax.set_ylim(-0.25, 3.3)
    ax.axis("off")
    for y, label in [(2.5, "source / availability"), (1.5, "control computation"), (0.5, "output / evaluation")]:
        ax.hlines(y, 0, 4, color="0.6", lw=1)
        ax.text(-0.12, y, label, ha="right", va="center", fontsize=8)
    for k in range(5):
        ax.vlines(k, 0.2, 2.8, color="0.88", lw=0.8)
        ax.text(k, 3.0, f"$t_{{k{'' if k == 0 else f'+{k}'}}}$", ha="center", fontsize=8)
    ax.plot([0], [2.5], "o", color=COLORS[0])
    ax.text(0.05, 2.66, "$p_k^{ref}$ arrives", fontsize=8)
    ax.annotate("$\\hat{x}_{k|k}$", (0.1, 1.5), (0.45, 2.1), arrowprops={"arrowstyle": "->"}, fontsize=8)
    ax.annotate("$\\bar{x}_{k+H|k}$", (0.35, 1.5), (1.3, 2.12), arrowprops={"arrowstyle": "->"}, fontsize=8)
    ax.annotate("$x_{k+1}^{target}$", (0.6, 1.5), (1.6, 1.75), arrowprops={"arrowstyle": "->"}, fontsize=8)
    ax.annotate("$x_{k+1}^{cmd}$", (1, 0.5), (1.45, 0.9), arrowprops={"arrowstyle": "->"}, fontsize=8)
    ax.text(0.5, 0.08, "target[$k$] $\\rightarrow$ output[$k+1$]", ha="center", fontsize=9, weight="bold")
    save(fig, "timing.pdf")


def phase_a(data: dict) -> None:
    rows = data["phase_a"]["analytic_tracking"]
    datasets = ["quadratic_with_extremum", "cubic", "sine"]
    methods = ["p", "pv_truth", "pva_truth"]
    fig, ax = plt.subplots(figsize=(6.6, 3.3))
    x = np.arange(len(datasets))
    width = 0.24
    for i, method in enumerate(methods):
        values = [next(r["rmse"] for r in rows if r["dataset"] == d and r["method_id"] == method) for d in datasets]
        ax.bar(x + (i - 1) * width, values, width, label={"p": "P", "pv_truth": "PV truth", "pva_truth": "PVA truth"}[method], color=COLORS[i])
    ax.set_xticks(x, ["Quadratic", "Cubic", "Sine"])
    ax.set_ylabel("position RMSE (rad)")
    ax.set_yscale("log")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncols=3, frameon=False)
    save(fig, "phase_a_ablation.pdf")


def derivative_timing() -> None:
    fig, axes = plt.subplots(3, 1, figsize=(6.8, 3.3), sharex=True)
    titles = [
        ("Backward at $k$", [-2, -1, 0], ["$p_{k-2}$", "$p_{k-1}$", "$p_k$"], True),
        ("Centered aligned at $k$ (offline)", [-1, 0, 1], ["$p_{k-1}$", "$p_k$", "$p_{k+1}$"], False),
        ("Centered estimate of $k-1$ available at $k$", [-2, -1, 0], ["$p_{k-2}$", "$p_{k-1}$", "$p_k$"], True),
    ]
    for ax, (title, xs, labels, causal) in zip(axes, titles):
        ax.axhline(0, color="0.5")
        for x, label in zip(xs, labels):
            ax.plot(x, 0, "o", color=COLORS[0] if x <= 0 else COLORS[3])
            ax.text(x, 0.18, label, ha="center", fontsize=8)
        ax.text(1.15, 0, "causal" if causal else "future sample required", va="center", fontsize=8, color=COLORS[2] if causal else COLORS[3])
        ax.set_ylim(-0.3, 0.5)
        ax.set_yticks([])
        ax.set_title(title, loc="left", fontsize=8)
    axes[-1].set_xlim(-2.5, 2.5)
    axes[-1].set_xticks([-2, -1, 0, 1], ["$k-2$", "$k-1$", "$k$", "$k+1$"])
    save(fig, "derivative_timing.pdf")


def governor() -> None:
    dt = 0.01
    j = np.linspace(-4000, 4000, 401)
    a0, v0 = 0.0, 0.0
    a1 = a0 + j * dt
    v1 = v0 + a0 * dt + 0.5 * j * dt**2
    feasible = (np.abs(a1) <= 8.2) & (np.abs(v1) <= 4.1)
    fig, ax = plt.subplots(figsize=(5.8, 3.4))
    ax.plot(v1[~feasible], a1[~feasible], color="0.75", lw=2, label="jerk-limited but outside V/A box")
    ax.plot(
        v1[feasible],
        a1[feasible],
        color=COLORS[2],
        lw=3,
        label="endpoint-V/A-admissible image",
    )
    ax.add_patch(Rectangle((-4.1, -8.2), 8.2, 16.4, fill=False, linestyle="--", edgecolor=COLORS[0], label="point-admissible V/A box"))
    ax.scatter([v1[len(j)//2]], [a1[len(j)//2]], color="black", s=18, zorder=3)
    ax.set_xlabel("$v_{k+1}$ (rad/s)")
    ax.set_ylabel("$a_{k+1}$ (rad/s$^2$)")
    ax.legend(frameon=False, fontsize=8)
    ax.grid(alpha=0.2)
    save(fig, "governor_reachability.pdf")


def v3_safety(data: dict) -> None:
    rows = data["v3"]["acceptance_rows"]
    values = {
        r["metric"]: r["observed"]
        for r in rows
        if r["metric"] in {"violation_count", "projection_rate"}
    }
    fallback = data["v3"]["direct_fallback"]
    runtime = data["v3"]["direct_runtime_primary"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.1))
    axes[0].bar(
        ["V/A/J\nviolations", "projected\ncycles", "fallback\ncycles"],
        [
            values["violation_count"],
            values["projection_rate"],
            fallback["fallback_cycle_count"],
        ],
        color=[COLORS[0], COLORS[1], COLORS[2]],
    )
    axes[0].set_ylabel("observed count")
    axes[0].set_ylim(0, 1)
    axes[0].text(1, 0.72, "all observed values = 0", ha="center", fontsize=8)
    axes[1].bar(["p99", "maximum"], [runtime["runtime_p99_us"], runtime["runtime_max_us"]], color=[COLORS[0], COLORS[1]])
    axes[1].axhline(10_000, color=COLORS[3], linestyle="--", label="10 ms deadline")
    axes[1].set_yscale("log")
    axes[1].set_ylabel("total compute time ($\\mu$s)")
    axes[1].text(
        0.5,
        0.58,
        f"deadline misses: 0 / {runtime['timed_cycle_count']}",
        transform=axes[1].transAxes,
        ha="center",
        fontsize=8,
    )
    axes[1].legend(frameon=False, fontsize=8)
    fig.subplots_adjust(wspace=0.42)
    save(fig, "v3_direct_safety_runtime.pdf")


def csv_negative(data: dict) -> None:
    rows = data["phase_a"]["csv_tracking"]
    labels = [r["method_id"].replace("_", "\n") for r in rows]
    values = [r["rmse"] for r in rows]
    colors = [COLORS[0]] + [COLORS[4]] * (len(rows) - 1)
    fig, ax = plt.subplots(figsize=(7.0, 3.4))
    ax.bar(np.arange(len(rows)), values, color=colors)
    ax.set_xticks(np.arange(len(rows)), labels, fontsize=7)
    ax.set_ylabel("position RMSE (rad)")
    ax.axhline(values[0], color=COLORS[0], linestyle="--", lw=1)
    ax.grid(axis="y", alpha=0.2)
    save(fig, "csv_negative_result.pdf")


def render_all(data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    architecture()
    timeline()
    phase_a(data)
    derivative_timing()
    governor()
    v3_safety(data)
    csv_negative(data)


def main() -> int:
    global OUT

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    expected = {
        "architecture.pdf",
        "timing.pdf",
        "phase_a_ablation.pdf",
        "derivative_timing.pdf",
        "governor_reachability.pdf",
        "v3_direct_safety_runtime.pdf",
        "csv_negative_result.pdf",
    }
    canonical_out = OUT
    if args.check:
        with tempfile.TemporaryDirectory(prefix="otg-paper-figures-") as temp:
            OUT = Path(temp)
            render_all(data)
            mismatches = sorted(
                name
                for name in expected
                if not (canonical_out / name).is_file()
                or (canonical_out / name).read_bytes() != (OUT / name).read_bytes()
            )
        OUT = canonical_out
        if mismatches:
            raise SystemExit("generated figures are stale: " + ", ".join(mismatches))
        print(f"verified {len(expected)} vector figures")
        return 0

    render_all(data)
    missing = sorted(name for name in expected if not (OUT / name).is_file())
    if missing:
        raise SystemExit("figure generation failed: " + ", ".join(missing))
    print(f"wrote {len(expected)} vector figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
