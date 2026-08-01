#!/usr/bin/env python3
"""Generate every paper number, table, and figure from frozen evidence."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from mpl_toolkits.axes_grid1.inset_locator import inset_axes

PAPER_ROOT = Path(__file__).resolve().parents[1]
COLORS = {
    "navy": "#17365D",
    "blue": "#2B6CB0",
    "cyan": "#2C7A7B",
    "green": "#2F855A",
    "orange": "#DD6B20",
    "red": "#C53030",
    "gray": "#718096",
    "light": "#E2E8F0",
    "black": "#1A202C",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def number(row: dict[str, str], key: str) -> float:
    return float(row[key])


def truth(value: str) -> bool:
    return value.strip().lower() == "true"


def median(rows: Iterable[dict[str, str]], key: str) -> float:
    return float(np.median([number(row, key) for row in rows]))


def percentile(rows: Iterable[dict[str, str]], key: str, q: float) -> float:
    return float(np.percentile([number(row, key) for row in rows], q))


def latex_escape(value: str) -> str:
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "_": r"\_",
        "#": r"\#",
    }
    return "".join(replacements.get(char, char) for char in value)


def condition_label(family: str, level: float) -> str:
    labels = {
        "clean": "clean",
        "position_noise": r"position noise $\sigma$",
        "quantization": r"quantization $\Delta$",
        "timestamp_jitter": r"timestamp jitter $\sigma/T$",
        "delay_cycles": "delay cycles",
        "dropout_rate": "dropout rate",
    }
    if family == "clean":
        return labels[family]
    shown = f"{level:g}"
    return f"{labels[family]} = {shown}"


def configure_plots() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8.2,
            "axes.labelsize": 8.2,
            "axes.titlesize": 9.2,
            "legend.fontsize": 7.2,
            "xtick.labelsize": 7.2,
            "ytick.labelsize": 7.2,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.22,
            "grid.linewidth": 0.5,
            "figure.dpi": 160,
            "savefig.bbox": "tight",
        }
    )


def save_pdf(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        format="pdf",
        metadata={
            "Creator": "paper/scripts/build_artifacts.py",
            "Producer": "Matplotlib",
            "CreationDate": None,
            "ModDate": None,
        },
    )
    plt.close(fig)


def rest_to_rest_profile() -> dict[str, np.ndarray]:
    samples = 1001
    t = np.linspace(0.0, 1.0, samples)
    jerk_amplitude = 25.6  # 0.8 of J=32: displacement is 0.8 in T=1.
    p = np.zeros(samples)
    v = np.zeros(samples)
    a = np.zeros(samples)
    j = np.zeros(samples)
    for index in range(samples):
        tau = t[index]
        if tau < 0.25:
            j[index] = jerk_amplitude
        elif tau < 0.5:
            j[index] = -jerk_amplitude
        elif tau < 0.75:
            j[index] = -jerk_amplitude
        else:
            j[index] = jerk_amplitude
        if index:
            dt = t[index] - t[index - 1]
            a[index] = a[index - 1] + j[index - 1] * dt
            v[index] = v[index - 1] + a[index - 1] * dt + 0.5 * j[index - 1] * dt**2
            p[index] = (
                p[index - 1]
                + v[index - 1] * dt
                + 0.5 * a[index - 1] * dt**2
                + j[index - 1] * dt**3 / 6.0
            )
    j[-1] = 0.0
    return {"t": t, "p": p, "v": v, "a": a, "j": j}


def figure_one(output: Path) -> None:
    profile = rest_to_rest_profile()
    t = profile["t"]
    pv = {"p": 0.8 * t, "v": np.full_like(t, 0.8), "a": np.zeros_like(t), "j": np.zeros_like(t)}
    fig = plt.figure(figsize=(7.15, 6.25))
    grid = GridSpec(5, 2, height_ratios=[1.2, 1, 1, 1, 1], hspace=0.28, wspace=0.28)

    timeline = fig.add_subplot(grid[0, 0])
    timeline.set_axis_off()
    timeline.set_title("(a) Fixed-grid information contract", loc="left", fontweight="bold")
    for x, label in zip([0.12, 0.36, 0.60, 0.84], [r"$P[k-2]$", r"$P[k-1]$", r"$P[k]$", r"$P[k+1]$"]):
        timeline.plot([x], [0.52], "o", color=COLORS["navy"], ms=4)
        timeline.text(x, 0.38, label, ha="center")
    timeline.annotate("", xy=(0.9, 0.52), xytext=(0.06, 0.52), arrowprops={"arrowstyle": "->"})
    timeline.text(0.55, 0.86, "latest measurement", ha="center", color=COLORS["blue"])
    timeline.text(0.87, 0.70, "scheduled target", ha="center", color=COLORS["green"])
    timeline.text(0.50, 0.08, r"$\widehat V[k+1]=(2P[k]-3P[k-1]+P[k-2])/T$", ha="center")
    timeline.set_xlim(0, 1)
    timeline.set_ylim(0, 1)

    contract = fig.add_subplot(grid[0, 1])
    contract.set_axis_off()
    contract.set_title("(b) Same position, different terminal state", loc="left", fontweight="bold")
    box = {"boxstyle": "round,pad=0.35", "facecolor": "white", "linewidth": 1.0}
    contract.text(0.05, 0.66, r"P-only: $(p^\star,0,0)$  $\Rightarrow$  rest-to-rest", color=COLORS["red"], bbox={**box, "edgecolor": COLORS["red"]}, fontsize=7.6)
    contract.text(0.05, 0.22, r"matched PV: $(p^\star,v_{\rm ref},0)$  $\Rightarrow$  zero jerk", color=COLORS["blue"], bbox={**box, "edgecolor": COLORS["blue"]}, fontsize=7.6)
    contract.set_xlim(0, 1)
    contract.set_ylim(0, 1)

    units = ["position", "velocity", "acceleration", "jerk"]
    keys = ["p", "v", "a", "j"]
    for row_index, (key, unit) in enumerate(zip(keys, units), start=1):
        axis = fig.add_subplot(grid[row_index, :])
        axis.plot(t, profile[key], color=COLORS["red"], lw=1.6, label="P-only rest-to-rest")
        axis.plot(t, pv[key], color=COLORS["blue"], lw=1.5, ls="--", label="matched PV")
        axis.axvline(1.0, color=COLORS["gray"], lw=0.7)
        axis.set_ylabel(unit)
        axis.set_xlim(0, 1)
        if row_index < 4:
            axis.tick_params(labelbottom=False)
        else:
            axis.set_xlabel(r"normalized time $\tau/T$")
        if row_index == 1:
            axis.set_title("(c) Exact within-period profiles", loc="left", fontweight="bold")
            axis.legend(loc="upper left", ncol=2, frameon=False)
    save_pdf(fig, output)


def figure_two(e15: Path, output: Path) -> None:
    rows = read_csv(e15 / "boundary_grid.csv")
    required = [row for row in rows if truth(row["required"])]
    q = np.geomspace(0.12, 4.2, 500)
    g = np.where(q < 1.0, (2.0 * q - q**2) / 32.0, 1.0 / 32.0)
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 3.05), gridspec_kw={"width_ratios": [0.9, 1.35]})
    axes[0].plot(q, g, color=COLORS["navy"], lw=2)
    axes[0].axvline(1.0, color=COLORS["gray"], ls="--", lw=1)
    axes[0].fill_between(q[q < 1], 0, g[q < 1], color=COLORS["orange"], alpha=0.12)
    axes[0].fill_between(q[q >= 1], 0, g[q >= 1], color=COLORS["blue"], alpha=0.10)
    axes[0].text(0.18, 0.008, "acceleration-limited", color=COLORS["orange"])
    axes[0].text(1.2, 0.021, "jerk-limited", color=COLORS["blue"])
    axes[0].set_xscale("log")
    axes[0].set_xlabel(r"$q=4A/(JT)$")
    axes[0].set_ylabel(r"$v_{\rm crit}/(JT^2)$")
    axes[0].set_title("(a) Reachability scale", loc="left", fontweight="bold")

    q_values = np.array([number(row, "q") for row in required])
    rho_values = np.array([number(row, "rho") for row in required])
    pulse = np.array([number(row, "rest_to_rest_pulse_fraction") for row in required])
    scatter = axes[1].scatter(q_values, rho_values, c=pulse, cmap="RdYlBu_r", vmin=0, vmax=1, s=11, linewidths=0)
    axes[1].axvline(1.0, color=COLORS["gray"], ls=":", lw=0.9)
    axes[1].axhline(1.0, color=COLORS["black"], ls="--", lw=1.2, label=r"theory: $\rho=1$")
    axes[1].set_xscale("log")
    axes[1].set_yscale("log")
    axes[1].set_xlabel(r"$q$")
    axes[1].set_ylabel(r"$\rho=|v_{\rm ref}|/v_{\rm crit}$")
    axes[1].set_title("(b) E15 phase map", loc="left", fontweight="bold")
    axes[1].legend(frameon=False, loc="lower left")
    colorbar = fig.colorbar(scatter, ax=axes[1], pad=0.02)
    colorbar.set_label("rest-to-rest pulse fraction")
    fig.subplots_adjust(wspace=0.36)
    save_pdf(fig, output)


def figure_three(e15: Path, output: Path) -> None:
    grid_rows = read_csv(e15 / "boundary_grid.csv")
    required = [row for row in grid_rows if truth(row["required"])]
    seam = [row for row in grid_rows if not truth(row["required"])]
    holdout = read_csv(e15 / "holdout_thresholds.csv")
    fig, axis = plt.subplots(figsize=(7.15, 3.25))
    branch_colors = {"acceleration_limited": COLORS["orange"], "jerk_limited": COLORS["blue"]}
    for branch in branch_colors:
        subset = [row for row in required if row["branch"] == branch]
        axis.scatter(
            [number(row, "rho") for row in subset],
            [number(row, "rest_to_rest_pulse_fraction") for row in subset],
            s=11,
            alpha=0.35,
            color=branch_colors[branch],
            label=branch.replace("_", " "),
        )
    axis.scatter([1.0] * len(seam), [0.5] * len(seam), marker="x", s=30, color=COLORS["red"], label="16 exact-seam diagnostics")
    axis.axvline(1.0, color=COLORS["black"], ls="--", lw=1.2)
    axis.set_xscale("log")
    axis.set_xlim(0.45, 4.5)
    axis.set_ylim(-0.08, 1.08)
    axis.set_xlabel(r"dimensionless speed $\rho$")
    axis.set_ylabel("rest-to-rest pulse fraction")
    axis.set_title(r"E15 collapse across $T$, $J$, direction, and both $q$ branches", loc="left", fontweight="bold")
    axis.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.50, -0.13))
    inset = inset_axes(axis, width="34%", height="42%", loc="upper right", borderpad=1.1)
    holdout_q = np.array([number(row, "q") for row in holdout])
    errors = np.array([number(row, "rho_hat") - 1.0 for row in holdout]) * 100.0
    inset.scatter(holdout_q, errors, s=8, c=[branch_colors[row["branch"]] for row in holdout], alpha=0.7)
    inset.axhline(0, color=COLORS["black"], lw=0.7)
    inset.set_xscale("log")
    inset.set_xlabel(r"$q$", labelpad=0)
    inset.set_title(r"128 Sobol: $(\hat\rho-1)\times100\%$", fontsize=7.2)
    save_pdf(fig, output)


def parse_lambda(method_id: str) -> float:
    token = method_id.removeprefix("pv_lambda_")
    sign = -1.0 if token.startswith("m") else 1.0
    token = token.removeprefix("m").replace("p", ".")
    return sign * float(token)


def figure_four(e16: Path, output: Path) -> None:
    rows = read_csv(e16 / "causal_ablation.csv")
    by_method: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_method[row["method_id"]].append(row)
    fig, axes = plt.subplots(1, 3, figsize=(7.15, 3.15), gridspec_kw={"width_ratios": [1.25, 1.2, 0.9]})

    lambda_methods = sorted(
        (parse_lambda(method), method) for method in by_method if method.startswith("pv_lambda_")
    )
    lambda_values = [item[0] for item in lambda_methods]
    ripple = [median(by_method[item[1]], "profile_velocity_ripple_to_reference_median") for item in lambda_methods]
    axes[0].plot(lambda_values, ripple, "o-", color=COLORS["blue"], lw=1.5, ms=4)
    axes[0].axvline(1.0, color=COLORS["green"], ls="--", lw=1, label="matched velocity")
    axes[0].set_xlabel(r"velocity-target scale $\lambda$")
    axes[0].set_ylabel("median normalized ripple")
    axes[0].set_title("(a) Velocity dose response", loc="left", fontweight="bold")
    axes[0].legend(frameon=False)

    alternative_groups = [
        ("random sign", ["pv_random_sign"]),
        ("lookahead", [m for m in by_method if m.startswith("p_lookahead_")]),
        ("min. duration", [m for m in by_method if m.startswith("p_min_duration_")]),
        ("P-only", ["p_only_scheduled"]),
        ("matched PV", ["pv_oracle"]),
    ]
    values = []
    labels = []
    colors = []
    for label, methods in alternative_groups:
        pooled = [row for method in methods for row in by_method[method]]
        labels.append(label)
        values.append(median(pooled, "profile_velocity_ripple_to_reference_median"))
        colors.append(COLORS["green"] if label == "matched PV" else COLORS["orange"])
    axes[1].barh(np.arange(len(labels)), values, color=colors, alpha=0.9)
    axes[1].set_yticks(np.arange(len(labels)), labels)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("median normalized ripple")
    axes[1].set_title("(b) Competing explanations", loc="left", fontweight="bold")

    diagnostic = ["pv_future_o1", "pv_future_o1_deadband", "pv_oracle"]
    diagnostic_labels = ["raw", "deadband", "oracle"]
    diagnostic_values = [
        max(1e-17, percentile(by_method[method], "profile_velocity_ripple_to_reference_p95", 95))
        for method in diagnostic
    ]
    axes[2].bar(np.arange(3), diagnostic_values, color=[COLORS["red"], COLORS["green"], COLORS["blue"]])
    axes[2].set_yscale("log")
    axes[2].set_xticks(np.arange(3), diagnostic_labels)
    axes[2].set_ylabel("cross-cell P95 ripple")
    axes[2].set_title("(c) Numerical contract", loc="left", fontweight="bold")
    save_pdf(fig, output)


def e17_condition_statistics(e17: Path) -> list[dict[str, Any]]:
    rows = [
        row
        for row in read_csv(e17 / "robustness_cells.csv")
        if row["split"] == "holdout" and row["method_id"] in {"p_scheduled", "pv_local_poly"}
    ]
    grouped: dict[tuple[str, str, str], dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        pair_key = "|".join([row["q"], row["rho"], row["seed"]])
        grouped[(row["perturbation_family"], row["perturbation_level"], pair_key)][row["method_id"]] = row
    reductions: dict[tuple[str, str, bool], list[float]] = defaultdict(list)
    for (family, level, _), pair in grouped.items():
        if set(pair) != {"p_scheduled", "pv_local_poly"}:
            continue
        baseline = number(pair["p_scheduled"], "profile_velocity_ripple_to_reference_median")
        candidate = number(pair["pv_local_poly"], "profile_velocity_ripple_to_reference_median")
        if baseline <= 0:
            continue
        work = truth(pair["pv_local_poly"]["work_envelope"])
        reductions[(family, level, work)].append((baseline - candidate) / baseline)
    result = []
    for (family, level, work), values in reductions.items():
        array = np.asarray(values)
        result.append(
            {
                "family": family,
                "level": float(level),
                "work_envelope": work,
                "pair_count": len(values),
                "median": float(np.median(array)),
                "minimum": float(np.min(array)),
                "q25": float(np.percentile(array, 25)),
                "q75": float(np.percentile(array, 75)),
                "improvement_count": int(np.sum(array > 0)),
            }
        )
    order = {"clean": 0, "position_noise": 1, "quantization": 2, "timestamp_jitter": 3, "delay_cycles": 4, "dropout_rate": 5}
    return sorted(result, key=lambda item: (not item["work_envelope"], order[item["family"]], item["level"]))


def figure_five(e17: Path, output: Path, stats: list[dict[str, Any]]) -> None:
    fig, axis = plt.subplots(figsize=(7.15, 5.0))
    y = np.arange(len(stats))
    for index, item in enumerate(stats):
        color = COLORS["red"] if item["median"] < 0.5 else (COLORS["blue"] if item["work_envelope"] else COLORS["orange"])
        axis.plot([item["minimum"], item["median"]], [index, index], color=color, lw=1.6)
        axis.scatter(item["minimum"], index, marker="|", s=70, color=color)
        axis.scatter(item["median"], index, marker="o", s=25, color=color, zorder=3)
    axis.set_yticks(y, [condition_label(item["family"], item["level"]) for item in stats])
    axis.invert_yaxis()
    axis.axhline(10.5, color=COLORS["gray"], lw=0.9)
    axis.text(-0.14, 9.9, "declared work envelope", color=COLORS["blue"], fontsize=7.2)
    axis.text(-0.14, 16.0, "out-of-envelope stress diagnostics", color=COLORS["orange"], fontsize=7.2)
    axis.axvline(0.5, color=COLORS["black"], ls="--", lw=1, label="50% criterion")
    axis.axvline(0.0, color=COLORS["gray"], ls=":", lw=0.8)
    axis.set_title("E17 condition-wise robustness and the observed envelope boundary", loc="left", fontweight="bold")
    axis.set_xlabel("ripple reduction (median dot; minimum tick)")
    axis.set_xlim(-0.16, 1.05)
    axis.legend(frameon=False, loc="lower center")
    save_pdf(fig, output)


def figure_six(root: Path, output: Path) -> None:
    reference = read_csv(root / "E11" / "reference.csv")[1:]
    p_command = read_csv(root / "E11" / "p_command.csv")
    pv_command = read_csv(root / "E11" / "pv_future_o1_command.csv")
    time = np.asarray([number(row, "time_s") for row in p_command])
    ref = np.asarray([number(row, "position_rad") for row in reference])
    p = np.asarray([number(row, "position_rad") for row in p_command])
    pv = np.asarray([number(row, "position_rad") for row in pv_command])
    window = (time >= 67.8) & (time <= 69.2)

    scorecard = read_csv(root / "A04" / "selection_scorecard.csv")
    sensitivity = read_csv(root / "A06" / "selected_lag_sensitivity.csv")
    fig = plt.figure(figsize=(7.15, 5.65))
    grid = GridSpec(3, 2, height_ratios=[1.25, 0.85, 1.15], hspace=0.58, wspace=0.30)
    trace_axis = fig.add_subplot(grid[0, :])
    trace_axis.plot(time[window], ref[window], color=COLORS["black"], lw=1.4, label="reference")
    trace_axis.plot(time[window], p[window], color=COLORS["red"], lw=1.0, label="scheduled P")
    trace_axis.plot(time[window], pv[window], color=COLORS["blue"], lw=1.1, label="PV Future-O1")
    trace_axis.set_ylabel("position [rad]")
    trace_axis.set_title("(a) Recorded case: representative local window", loc="left", fontweight="bold")
    trace_axis.legend(frameon=False, ncol=3)

    error_axis = fig.add_subplot(grid[1, :], sharex=trace_axis)
    error_axis.plot(time[window], (p - ref)[window] * 1000, color=COLORS["red"], lw=1.0, label="P error")
    error_axis.plot(time[window], (pv - ref)[window] * 1000, color=COLORS["blue"], lw=1.0, label="PV error")
    error_axis.axhline(0, color=COLORS["black"], lw=0.6)
    error_axis.set_xlabel("recorded time [s]")
    error_axis.set_ylabel("error [mrad]")

    pareto_axis = fig.add_subplot(grid[2, 0])
    for component, color, marker in [("PV", COLORS["blue"], "o"), ("PVA", COLORS["orange"], "s")]:
        subset = [row for row in scorecard if row["target_components"] == component]
        pareto_axis.scatter(
            [abs(number(row, "lag_subsample_ms")) for row in subset],
            [number(row, "position_rmse_rad") * 1000 for row in subset],
            s=22,
            color=color,
            marker=marker,
            alpha=0.8,
            label=component,
        )
    baseline_rmse = number(scorecard[0], "baseline_position_rmse_rad") * 1000
    baseline_lag = abs(number(scorecard[0], "baseline_subsample_lag_ms"))
    pareto_axis.scatter([baseline_lag], [baseline_rmse], marker="x", s=45, color=COLORS["red"], label="P")
    pareto_axis.set_xlabel("absolute observed lag [ms]")
    pareto_axis.set_ylabel("position RMSE [mrad]")
    pareto_axis.set_title("(b) A04 method Pareto", loc="left", fontweight="bold")
    pareto_axis.legend(frameon=False, ncol=3)

    selection_axis = fig.add_subplot(grid[2, 1])
    categories = [
        f"V={number(row, 'max_velocity_rad_s'):g}\nJ={number(row, 'max_jerk_rad_s3'):g}"
        for row in sensitivity
    ]
    heights = [number(row, "position_rmse_rad") * 1000 for row in sensitivity]
    colors = [COLORS["green"] if row["deployment_role"] == "deployment_recommended" else COLORS["gray"] for row in sensitivity]
    selection_axis.bar(np.arange(len(sensitivity)), heights, color=colors, width=0.65)
    selection_axis.set_xticks(np.arange(len(sensitivity)), categories, fontsize=6.3)
    for index, row in enumerate(sensitivity):
        selection_axis.text(index, heights[index] + 0.025, f"{abs(number(row, 'subsample_lag_ms')):.3f} ms", ha="center", fontsize=6.4)
    selection_axis.set_ylim(0, max(heights) * 1.16)
    selection_axis.set_ylabel("position RMSE [mrad]")
    selection_axis.set_title("(c) A06 selected sensitivity", loc="left", fontweight="bold")
    save_pdf(fig, output)


def write_numbers(output: Path, values: dict[str, Any]) -> None:
    def command(name: str, body: str) -> str:
        if not re.fullmatch(r"[A-Za-z]+", name):
            raise ValueError(f"LaTeX macro names must contain letters only: {name}")
        return rf"\newcommand{{\{name}}}{{{body}}}"

    lines = ["% Generated by paper/scripts/build_artifacts.py; do not edit."]
    definitions = {
        "EvidenceProfile": values["profile"],
        "EFifteenGridCount": f"{values['e15_grid']:,}",
        "EFifteenSobolCount": str(values["e15_sobol"]),
        "EFifteenSeamCount": str(values["e15_seam"]),
        "EFifteenMaxBoundaryErrorPercent": rf"\num{{{values['e15_error_percent']:.4f}}}\%",
        "ESixteenArmCount": f"{values['e16_arms']:,}",
        "ESixteenWrongRipple": rf"\num{{{values['e16_wrong_ripple']:.4f}}}",
        "ESeventeenWorkConditionCount": str(values["e17_work_conditions"]),
        "ESeventeenStressConditionCount": str(values["e17_stress_conditions"]),
        "ESeventeenStressPairCount": f"{values['e17_stress_pairs']:,}",
        "ESeventeenPairPerCondition": str(values["e17_pairs_per_condition"]),
        "ESeventeenDevelopmentRowCount": f"{values['e17_development_rows']:,}",
        "ESeventeenHoldoutRowCount": f"{values['e17_holdout_rows']:,}",
        "ESeventeenWorkPairCount": f"{values['e17_work_pairs']:,}",
        "ESeventeenWeakMedianPercent": rf"\num{{{values['e17_weak_median'] * 100:.2f}}}\%",
        "ESeventeenWeakMinimumPercent": rf"\num{{{values['e17_weak_minimum'] * 100:.2f}}}\%",
        "ESeventeenStressMedianPercent": rf"\num{{{values['e17_stress_median'] * 100:.2f}}}\%",
        "ESeventeenStressMinimumPercent": rf"\num{{{values['e17_stress_minimum'] * 100:.2f}}}\%",
        "ESeventeenStressImprovedCount": str(values["e17_stress_improved"]),
        "ESeventeenSyntheticCount": str(values["e17_synthetic_count"]),
        "ESeventeenSyntheticWorstPercent": rf"\num{{{values['e17_synthetic_worst'] * 100:.2f}}}\%",
        "RecordedSampleCount": f"{values['recorded_samples']:,}",
        "RecordedCycleCount": f"{values['recorded_cycles']:,}",
        "RecordedDuration": rf"\num{{{values['recorded_duration']:.2f}}}\,s",
        "RecordedBaselineRMSE": rf"\num{{{values['recorded_p_rmse']:.6f}}}\,rad",
        "RecordedPVRMSE": rf"\num{{{values['recorded_pv_rmse']:.6f}}}\,rad",
        "RecordedPVReductionPercent": rf"\num{{{values['recorded_pv_reduction'] * 100:.2f}}}\%",
        "RecordedPVALRMSE": rf"\num{{{values['recorded_pva_rmse']:.6f}}}\,rad",
        "RecordedPVAIncreasePercent": rf"\num{{{values['recorded_pva_increase'] * 100:.2f}}}\%",
        "RecordedPSubsampleLag": rf"\num{{{values['recorded_p_subsample_ms']:.3f}}}\,ms",
        "RecordedPVLag": rf"\num{{{values['recorded_pv_lag_ms']:.0f}}}\,ms",
        "RecordedPVSubsampleLag": rf"\num{{{values['recorded_pv_subsample_ms']:.3f}}}\,ms",
        "RecordedPVALag": rf"\num{{{values['recorded_pva_lag_ms']:.0f}}}\,ms",
        "RecordedPVASubsampleLag": rf"\num{{{values['recorded_pva_subsample_ms']:.3f}}}\,ms",
        "RecordedDeadlineMissCount": str(values["deadline_miss_count"]),
        "RecordedBestRMSE": rf"\num{{{values['recorded_best_rmse']:.6f}}}\,rad",
        "RecordedBestReductionPercent": rf"\num{{{values['recorded_best_reduction'] * 100:.2f}}}\%",
        "RecordedBestSubsampleLag": rf"\num{{{values['recorded_best_subsample_ms']:.3f}}}\,ms",
        "RecordedBestVsVendorReductionPercent": rf"\num{{{values['recorded_best_vs_vendor_reduction'] * 100:.2f}}}\%",
        "RecordedBestLagTradeoff": rf"\num{{{values['recorded_best_lag_tradeoff_ms']:.3f}}}\,ms",
        "RecordedLocalPolyRMSE": rf"\num{{{values['recorded_local_poly_rmse']:.6f}}}\,rad",
        "RecordedDeadband": r"\num{1e-10}\,rad\,s$^{-1}$",
        "RecordedMinimumNonzeroTarget": rf"\num{{{values['recorded_min_nonzero_target']:.3e}}}\,rad\,s$^{{-1}}$",
        "RecordedZeroTargetCount": str(values["recorded_zero_target_count"]),
        "PVAPVEquivalentCoordinateCount": str(values["pva_equivalent_coordinates"]),
        "VmaxInteraction": rf"\num{{{values['vmax_interaction']:.1f}}}",
        "RuckigVersion": "0.17.3",
    }
    lines.extend(command(name, body) for name, body in definitions.items())
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_tables(output: Path, values: dict[str, Any], stats: list[dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    table_one = r"""% Generated; do not edit.
\begin{table*}[t]
\centering
\caption{Evidence matrix. Counts denote deterministic configurations or explicitly paired seed--configuration cells, not independent robot tasks. All entries in this draft are hash-pinned but provisional.}
\label{tab:experiment-matrix}
\small
\begin{tabular}{@{}llllll@{}}
\toprule
Evidence & Role & Design & Unit & Count & Primary readout \\
\midrule
E15 & boundary confirmation & $T,J,q,\rho$, direction & grid cell & 2,144 + 128 holdouts & pulse class, $\hat\rho$ \\
E16 & causal ablation & contract interventions & arm & 1,260 & exact profile, ripple \\
E17 & frozen robustness & 11 work conditions & paired seed--cell & 1,320 & ripple reduction \\
E17 stress & domain diagnostics & 6 stronger conditions & paired seed--cell & 720 & failure boundary \\
A04 & recorded case study & P/PV/PVA, five stencils & one waveform & 7,672 cycles & RMSE, observed lag \\
A06 & best-tested selection & $8\!\times\!8\!\times\!10$, PV/PVA & tested setting & 1,280 & RMSE--lag Pareto \\
\bottomrule
\end{tabular}
\end{table*}
"""
    (output / "table1_experiment_matrix.tex").write_text(table_one, encoding="utf-8")

    table_two = r"""% Generated; do not edit.
\begin{table*}[t]
\centering
\caption{Causal interpretation of tested target contracts. ``Exact'' refers to the preregistered within-period profile criterion in E16, not only endpoint position.}
\label{tab:causal-ablation}
\small
\begin{tabularx}{\textwidth}{@{}p{0.18\textwidth}p{0.17\textwidth}X X@{}}
\toprule
Intervention & Exact in all tested primary cells? & Observed result & Bounded interpretation \\
\midrule
P-only $(p^\star,0,0)$ & No & repeated rest-to-rest pulses & terminal-state mismatch \\
Matched/oracle PV & Yes & zero median ripple & tested exact remedy \\
Velocity scale $\lambda\ne1$ & No & dose response; wrong sign is worse & derivative value matters \\
Random velocity sign & No & median ripple $3.2007$ & not an extra-DOF effect \\
Position lookahead $0,1,2,5$ & No & no tested horizon reproduces PV & not an exact substitute here \\
Minimum duration off/$1,2,5T$ & No & no tested duration reproduces PV & timing alone is insufficient here \\
Raw Future-O1 & No & microscopic cross-cell residual & implementation-level numerical interaction \\
Future-O1 + $10^{-10}$ deadband & Yes & exact within E16 criterion & tested numerical contract \\
Matched PVA at constant speed & Equivalent to PV & 80 coordinates agree in primary metrics & $a_{\rm ref}=0$ negative control \\
\bottomrule
\end{tabularx}
\end{table*}
"""
    (output / "table2_causal_ablation.tex").write_text(table_two, encoding="utf-8")

    table_three = rf"""% Generated; do not edit.
\begin{{table*}}[t]
\centering
\caption{{Recorded fixed-grid case study. Lags are observed waveform lags, not wall-clock latency. The deadline column is an offline-host guardrail.}}
\label{{tab:recorded}}
\small
\begin{{tabular}}{{@{{}}llllrrrrl@{{}}}}
\toprule
Contract & Observer & $V/A/J$ & Role & RMSE [rad] & lag [ms] & sub-sample [ms] & projection & deadline \\
\midrule
P & none & $4.1/8.2/4000$ & baseline & {values['recorded_p_rmse']:.6f} & 20 & 21.029 & 0 & pass \\
PV & Future-O1 & $4.1/8.2/4000$ & method selection & {values['recorded_pv_rmse']:.6f} & 10 & {values['recorded_pv_subsample_ms']:.3f} & 0 & 1 miss \\
PVA & Future-O1 & $4.1/8.2/4000$ & negative result & {values['recorded_pva_rmse']:.6f} & 10 & 13.976 & 0 & pass \\
PV & Future-O1 & $4.1/8.2/3200$ & best tested & {values['recorded_best_rmse']:.6f} & 10 & {values['recorded_best_subsample_ms']:.3f} & 0 & diagnostic \\
\bottomrule
\end{{tabular}}
\end{{table*}}
"""
    (output / "table3_recorded.tex").write_text(table_three, encoding="utf-8")

    work = [item for item in stats if item["work_envelope"]]
    stress = [item for item in stats if not item["work_envelope"]]
    weakest = min(work, key=lambda item: item["median"])
    boundary = next(item for item in stress if item["family"] == "position_noise" and math.isclose(item["level"], 0.25))
    table_four = rf"""% Generated; do not edit.
\begin{{table*}}[t]
\centering
\caption{{Robustness and negative controls. The recorded replay is diagnostic and is not an independent holdout.}}
\label{{tab:robustness}}
\small
\begin{{tabularx}}{{\textwidth}}{{@{{}}p{{0.16\textwidth}}p{{0.17\textwidth}}p{{0.22\textwidth}}X X@{{}}}}
\toprule
Evidence & Evaluation unit & Result & Supports & Does not support \\
\midrule
E17 work envelope & 11 conditions, 1,320 pairs & all improve; weakest median {weakest['median']*100:.2f}\% & robustness in declared envelope & robot generalization \\
E17 stress diagnostics & 6 conditions, 720 pairs & noise 0.25 median {boundary['median']*100:.2f}\%, min. {boundary['minimum']*100:.2f}\% & visible envelope boundary & universal noise robustness \\
Synthetic trajectories & 20 trajectories & 20/20 pass; worst {values['e17_synthetic_worst']*100:.2f}\% & tested nonconstant references & physical-task coverage \\
Recorded irregular replay & one waveform & local poly {values['recorded_local_poly_rmse']:.6f} vs. P {values['recorded_p_rmse']:.6f} rad & explicit negative result & irregular-sampling remedy \\
\bottomrule
\end{{tabularx}}
\end{{table*}}
"""
    (output / "table4_robustness.tex").write_text(table_four, encoding="utf-8")


def build(profile: str) -> dict[str, Any]:
    frozen = PAPER_ROOT / "evidence" / "frozen" / profile
    manifest = read_json(frozen / "artifact_manifest.json")
    if manifest["profile"] != profile:
        raise ValueError("frozen manifest profile mismatch")
    e15 = read_json(frozen / "E15" / "acceptance.json")
    e16 = read_json(frozen / "E16" / "acceptance.json")
    e17 = read_json(frozen / "E17" / "acceptance.json")
    assert e15["required_grid_count"] == e15["required_grid_completed_count"] == 2144
    assert e15["holdout_count"] == e15["holdout_completed_count"] == 128
    assert e15["exact_seam_count"] == e15["exact_seam_failure_count"] == 16
    assert e16["run_count"] == e16["completed_count"] == 1260
    assert e16["pv_is_only_tested_exact_profile_remedy"] is True
    assert e17["holdout_condition_count"] == 11
    assert e17["work_envelope_pair_count"] == 1320
    assert e17["trajectory_holdout_count"] == 20

    stats = e17_condition_statistics(frozen / "E17")
    work = [item for item in stats if item["work_envelope"]]
    stress = [item for item in stats if not item["work_envelope"]]
    assert len(work) == 11 and len(stress) == 6
    assert sum(item["pair_count"] for item in work) == 1320
    assert sum(item["pair_count"] for item in stress) == 720
    weak = min(work, key=lambda item: item["median"])
    boundary = next(item for item in stress if item["family"] == "position_noise" and math.isclose(item["level"], 0.25))
    assert weak["family"] == "position_noise" and math.isclose(weak["level"], 0.1)
    assert boundary["median"] < 0.5 and boundary["minimum"] < 0.0

    a04 = read_csv(frozen / "A04" / "selection_scorecard.csv")
    pv_row = next(row for row in a04 if row["target_components"] == "PV" and row["stencil"] == "pred_backward_o1_kp1")
    pva_row = next(row for row in a04 if row["target_components"] == "PVA" and row["stencil"] == "pred_backward_o1_kp1")
    p_rmse = number(pv_row, "baseline_position_rmse_rad")
    pv_rmse = number(pv_row, "position_rmse_rad")
    pva_rmse = number(pva_row, "position_rmse_rad")
    deadline_miss_count = round(number(pv_row, "deadline_miss_rate") * 7672)
    assert deadline_miss_count == 1

    sensitivity = read_csv(frozen / "A06" / "selected_lag_sensitivity.csv")
    deployment = next(row for row in sensitivity if row["deployment_role"] == "deployment_recommended")
    vendor_reference = next(row for row in sensitivity if row["deployment_role"] == "vendor_reference")
    a05 = read_csv(frozen / "A05" / "matched_pv_pva_equivalence.csv")
    assert all(truth(row["stop_go_equivalent_within_1e_12"]) for row in a05)
    equivalence_count = min(int(row["paired_coordinate_count"]) for row in a05)
    a03 = read_csv(frozen / "A03" / "attribution_decisions.csv")
    attribution = next(
        row
        for row in a03
        if row["input_id"] == "recorded_tasks_simplified_with_velocity_limit"
        and row["method_id"] == "pva_pred_backward_o1_kp1"
    )
    recorded_replay = read_csv(frozen / "E17" / "recorded_timestamp_replay.csv")
    local_poly = next(row for row in recorded_replay if row["method_id"] == "pv_local_poly")
    reference = read_csv(frozen / "E11" / "reference.csv")

    raw_velocities = []
    for row in read_csv(frozen / "E11" / "pv_future_o1_trace.csv"):
        value = row["raw_target_velocity_rad_s"]
        if value != "":
            raw_velocities.append(float(value))
    nonzero = [abs(value) for value in raw_velocities if value != 0.0]
    zero_count = sum(value == 0.0 for value in raw_velocities)
    minimum_nonzero = min(nonzero)
    assert len(raw_velocities) == 7672 and zero_count == 2
    assert minimum_nonzero > 1e-10

    values: dict[str, Any] = {
        "profile": profile,
        "release_ready": bool(manifest["release_ready"]),
        "e15_grid": e15["required_grid_count"],
        "e15_sobol": e15["holdout_count"],
        "e15_seam": e15["exact_seam_count"],
        "e15_error_percent": e15["holdout_max_abs_rho_error"] * 100.0,
        "e16_arms": e16["run_count"],
        "e16_wrong_ripple": e16["wrong_control_median_ripple"],
        "e17_work_conditions": len(work),
        "e17_stress_conditions": len(stress),
        "e17_stress_pairs": sum(item["pair_count"] for item in stress),
        "e17_pairs_per_condition": min(item["pair_count"] for item in stats),
        "e17_development_rows": e17["robustness_development_row_count"],
        "e17_holdout_rows": e17["robustness_holdout_row_count"],
        "e17_work_pairs": sum(item["pair_count"] for item in work),
        "e17_weak_median": weak["median"],
        "e17_weak_minimum": weak["minimum"],
        "e17_stress_median": boundary["median"],
        "e17_stress_minimum": boundary["minimum"],
        "e17_stress_improved": boundary["improvement_count"],
        "e17_synthetic_count": e17["trajectory_holdout_count"],
        "e17_synthetic_worst": e17["trajectory_worst_ripple_reduction"],
        "recorded_samples": len(reference),
        "recorded_cycles": len(reference) - 1,
        "recorded_duration": number(reference[-1], "time_s"),
        "recorded_p_rmse": p_rmse,
        "recorded_pv_rmse": pv_rmse,
        "recorded_pv_reduction": 1.0 - pv_rmse / p_rmse,
        "recorded_pva_rmse": pva_rmse,
        "recorded_pva_increase": pva_rmse / p_rmse - 1.0,
        "recorded_p_subsample_ms": abs(number(pv_row, "baseline_subsample_lag_ms")),
        "recorded_pv_lag_ms": abs(number(pv_row, "lag_ms")),
        "recorded_pv_subsample_ms": abs(number(pv_row, "lag_subsample_ms")),
        "recorded_pva_lag_ms": abs(number(pva_row, "lag_ms")),
        "recorded_pva_subsample_ms": abs(number(pva_row, "lag_subsample_ms")),
        "deadline_miss_count": deadline_miss_count,
        "recorded_best_rmse": number(deployment, "position_rmse_rad"),
        "recorded_best_reduction": 1.0 - number(deployment, "position_rmse_rad") / p_rmse,
        "recorded_best_subsample_ms": abs(number(deployment, "subsample_lag_ms")),
        "recorded_best_vs_vendor_reduction": 1.0 - number(deployment, "position_rmse_rad") / number(vendor_reference, "position_rmse_rad"),
        "recorded_best_lag_tradeoff_ms": abs(number(deployment, "subsample_lag_ms")) - abs(number(vendor_reference, "subsample_lag_ms")),
        "recorded_local_poly_rmse": number(local_poly, "position_rmse"),
        "recorded_min_nonzero_target": minimum_nonzero,
        "recorded_zero_target_count": zero_count,
        "pva_equivalent_coordinates": equivalence_count,
        "vmax_interaction": number(attribution, "log_ratio_interaction_limited_minus_relaxed"),
    }

    configure_plots()
    figures = PAPER_ROOT / "figures" / "generated"
    figure_one(figures / "fig1_mechanism_profiles.pdf")
    figure_two(frozen / "E15", figures / "fig2_dimensionless_boundary.pdf")
    figure_three(frozen / "E15", figures / "fig3_e15_collapse.pdf")
    figure_four(frozen / "E16", figures / "fig4_e16_ablation.pdf")
    figure_five(frozen / "E17", figures / "fig5_e17_robustness.pdf", stats)
    figure_six(frozen, figures / "fig6_recorded_case.pdf")
    write_numbers(PAPER_ROOT / "generated" / "numbers.tex", values)
    write_tables(PAPER_ROOT / "tables" / "generated", values, stats)
    summary = PAPER_ROOT / "generated" / "artifact_summary.json"
    summary.write_text(json.dumps({"values": values, "e17_conditions": stats}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="provisional")
    args = parser.parse_args()
    values = build(args.profile)
    print(json.dumps(values, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
