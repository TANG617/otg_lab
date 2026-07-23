"""Downstream-only V4 paper-evidence handoff and preregistered figures.

This module deliberately does not import the experiment runner, generator, or
pipeline.  It consumes immutable CSV/Parquet evidence and writes only bounded
paper inputs.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

PRIMARY_METHODS = (
    "one_step_governed_p_direct",
    "one_step_governed_pv_direct",
    "one_step_governed_pva_direct",
)
FAMILIES = (
    "stationary_endpoint",
    "oscillatory",
    "piecewise_constant_jerk",
    "stop_and_go",
    "rapid_reversal",
    "boundary_grazing",
)
POSITIVE_MATERIAL_CLAIM = (
    "Under the frozen V4 protocol, PVA target conditioning reduced mean "
    "trajectory-level position RMSE relative to P-only conditioning with the "
    "same one-step direct follower."
)
INCONCLUSIVE_CLAIM = (
    "The frozen V4 test did not establish an overall RMSE benefit from PVA "
    "conditioning over P-only conditioning."
)
HARMFUL_CLAIM = (
    "Under the frozen V4 protocol, PVA conditioning increased mean RMSE "
    "relative to P-only conditioning."
)
FIGURE_FILENAMES = (
    "01_p_pv_pva_rmse_distribution.png",
    "02_paired_pva_minus_p_rmse.png",
    "03_family_stratified_effect.png",
    "04_demand_stratum_effect.png",
    "05_harmful_rate_worst_case.png",
    "06_target_estimates_command_profiles.png",
    "07_continuous_constraint_margins.png",
    "08_runtime_distribution.png",
)


class V4HandoffError(ValueError):
    """Raised when immutable evidence is missing, inconsistent, or incomplete."""


def _parse(value: str) -> Any:
    value = value.strip()
    if value == "":
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "none", "nan"}:
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() and "e" not in value.lower() else number
    except ValueError:
        return value


def _read_csv(path: Path, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not path.is_file():
        raise V4HandoffError(f"required locked evidence is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise V4HandoffError(f"CSV has no header: {path}")
        rows = [{key: _parse(value or "") for key, value in row.items()} for row in reader]
    if not rows and not allow_empty:
        raise V4HandoffError(f"CSV has no evidence rows: {path}")
    return rows


def _find_csv(root: Path, name: str, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    candidates = (
        root / "statistics" / name,
        root / "raw_runs" / "locked_test" / name,
        root / "summaries" / name,
        root / "raw_runs" / "oracle_diagnostic" / name,
    )
    for path in candidates:
        if path.is_file():
            return _read_csv(path, allow_empty=allow_empty)
    raise V4HandoffError(f"required locked evidence is missing: {name}")


def _number(row: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value is not None:
            try:
                result = float(value)
            except (TypeError, ValueError) as exc:
                raise V4HandoffError(f"{key} is not numeric") from exc
            if not math.isfinite(result):
                raise V4HandoffError(f"{key} is not finite")
            return result
    raise V4HandoffError(f"none of required numeric fields is present: {keys}")


def _optional_number(row: Mapping[str, Any], *keys: str) -> float | None:
    if not any(row.get(key) is not None for key in keys):
        return None
    return _number(row, *keys)


def _bool(row: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in row and row[key] is not None:
            value = row[key]
            if isinstance(value, bool):
                return value
            if value in (0, 1):
                return bool(value)
    raise V4HandoffError(f"none of required boolean fields is present: {keys}")


def _optional_bool(row: Mapping[str, Any], *keys: str) -> bool | None:
    if not any(row.get(key) is not None for key in keys):
        return None
    return _bool(row, *keys)


def _method(row: Mapping[str, Any]) -> str:
    value = row.get("method", row.get("method_id"))
    if not value:
        raise V4HandoffError("method field is missing")
    return str(value)


def _primary_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    classification = first.get(
        "primary_result_classification", first.get("classification")
    )
    if classification is None:
        raise V4HandoffError("primary classification is missing")
    point = _optional_number(
        first, "overall_relative_improvement", "relative_improvement", "relative_point"
    )
    low = _optional_number(
        first,
        "overall_relative_improvement_ci_low",
        "relative_improvement_ci_low",
        "relative_ci_low",
    )
    high = _optional_number(
        first,
        "overall_relative_improvement_ci_high",
        "relative_improvement_ci_high",
        "relative_ci_high",
    )
    denominator = int(
        _number(
            first,
            "paired_trajectory_count",
            "paired_denominator",
            "n_trajectories",
            "trajectory_count",
        )
    )
    required = int(first.get("required_trajectory_count") or denominator)
    ids = [str(row["trajectory_id"]) for row in rows if row.get("trajectory_id")]
    if ids and (len(ids) != len(set(ids)) or len(ids) != required):
        raise V4HandoffError("primary table does not retain the complete denominator")
    for row in rows:
        observed = row.get(
            "primary_result_classification", row.get("classification")
        )
        if observed is not None and observed != classification:
            raise V4HandoffError("primary classification is inconsistent across rows")
    return {
        "classification": str(classification),
        "relative_improvement": point,
        "relative_ci_low": low,
        "relative_ci_high": high,
        "absolute_improvement": first.get(
            "overall_absolute_improvement", first.get("absolute_improvement")
        ),
        "absolute_ci_low": first.get(
            "overall_absolute_improvement_ci_low",
            first.get("absolute_improvement_ci_low"),
        ),
        "absolute_ci_high": first.get(
            "overall_absolute_improvement_ci_high",
            first.get("absolute_improvement_ci_high"),
        ),
        "paired_denominator": denominator,
        "required_denominator": required,
        "max_error_guardrail_pass": _optional_bool(
            first, "max_error_guardrail_pass"
        ),
        "lag_guardrail_pass": _optional_bool(first, "lag_guardrail_pass"),
    }


def _claim(primary: Mapping[str, Any], gates_valid: bool) -> tuple[list[str], list[str]]:
    classification = str(primary["classification"])
    allowed: list[str] = []
    if not gates_valid:
        allowed.append(
            "The V4 result was retained, but a failed validity gate prohibits a "
            "confirmatory performance claim."
        )
    else:
        point = primary["relative_improvement"]
        low = primary["relative_ci_low"]
        high = primary["relative_ci_high"]
        if point is None or low is None or high is None:
            raise V4HandoffError("available inference lacks effect or confidence interval")
        point = float(point)
        low = float(low)
        high = float(high)
    if not gates_valid:
        pass
    elif classification in {"practically_material", "strongly_material"} and point >= 0.05:
        allowed.append(POSITIVE_MATERIAL_CLAIM)
    elif classification == "confirmed_positive" and low > 0:
        allowed.append(
            "The frozen V4 test established an overall RMSE benefit from PVA "
            "conditioning over P-only conditioning."
        )
    elif low <= 0 <= high or classification == "inconclusive":
        allowed.append(INCONCLUSIVE_CLAIM)
    elif high < 0 or classification == "confirmed_harmful":
        allowed.append(HARMFUL_CLAIM)
    else:
        raise V4HandoffError("classification and confidence interval disagree")
    prohibited = [
        "Oracle diagnostics establish an online or deployable benefit.",
        "Ordinary Ruckig is a V4 primary comparison.",
        "Only favorable families or trajectories may be reported.",
    ]
    if not gates_valid:
        prohibited.append("Any confirmatory PVA performance claim.")
    if not (
        primary["max_error_guardrail_pass"] and primary["lag_guardrail_pass"]
    ):
        prohibited.append(
            "PVA improves tracking without material degradation in lag or peak error."
        )
    return allowed, prohibited


def select_v4_representative_trajectories(
    primary_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the locked, deterministic representative-trajectory rules."""

    usable = [row for row in primary_rows if row.get("trajectory_id")]
    if not usable:
        raise V4HandoffError("primary table lacks trajectory rows")
    by_id = {str(row["trajectory_id"]): row for row in usable}
    if len(by_id) != len(usable):
        raise V4HandoffError("duplicate primary trajectory IDs")

    def value(row: Mapping[str, Any], *keys: str) -> float:
        return _number(row, *keys)

    ordered = sorted(usable, key=lambda row: str(row["trajectory_id"]))
    baseline = np.asarray(
        [value(row, "baseline_position_rmse", "baseline_value") for row in ordered]
    )
    candidate = np.asarray(
        [value(row, "candidate_position_rmse", "candidate_value") for row in ordered]
    )
    improvement = baseline - candidate

    def nearest(values: np.ndarray) -> int:
        median = float(np.median(values))
        return min(
            range(len(ordered)),
            key=lambda index: (
                abs(float(values[index]) - median),
                str(ordered[index]["trajectory_id"]),
            ),
        )

    selections: list[dict[str, Any]] = []
    rules = (
        ("overall_median_P_baseline_RMSE", nearest(baseline), baseline),
        (
            "overall_median_paired_PVA_minus_P_improvement",
            nearest(improvement),
            improvement,
        ),
        (
            "worst_PVA_trajectory",
            min(
                range(len(ordered)),
                key=lambda i: (-float(candidate[i]), str(ordered[i]["trajectory_id"])),
            ),
            candidate,
        ),
        (
            "maximum_PVA_harm",
            min(
                range(len(ordered)),
                key=lambda i: (
                    -float(candidate[i] - baseline[i]),
                    str(ordered[i]["trajectory_id"]),
                ),
            ),
            candidate - baseline,
        ),
    )
    for role, index, values in rules:
        row = ordered[index]
        selections.append(
            {
                "role": role,
                "trajectory_id": str(row["trajectory_id"]),
                "family": str(row.get("family", "")),
                "selection_value": float(values[index]),
            }
        )
    for family in FAMILIES:
        matches = [
            row
            for row in ordered
            if str(row.get("family")) == family
            and str(row["trajectory_id"]).endswith("__000")
        ]
        if len(matches) != 1:
            raise V4HandoffError(
                f"expected exactly one locked test index 000 for family {family}"
            )
        selections.append(
            {
                "role": f"fixed_family_index_zero:{family}",
                "trajectory_id": str(matches[0]["trajectory_id"]),
                "family": family,
                "selection_value": 0,
            }
        )
    return selections


def _load_samples(root: Path):
    path = root / "raw_runs" / "locked_test" / "samples.parquet"
    if not path.is_file():
        raise V4HandoffError(f"required locked samples are missing: {path}")
    try:
        import pandas as pd

        frame = pd.read_parquet(path)
    except Exception as exc:  # pragma: no cover - exact engine error is external.
        raise V4HandoffError(f"cannot read locked sample Parquet: {exc}") from exc
    required = {"trajectory_id", "method_id", "command_time", "command_v", "command_a"}
    if not required <= set(frame.columns):
        raise V4HandoffError(f"sample Parquet lacks fields {sorted(required-set(frame.columns))}")
    if frame[list(required)].isnull().any().any():
        raise V4HandoffError("required sample fields contain nulls")
    return frame


def _save_figure(fig: Any, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", metadata={"Title": "Synthetic locked test"})


def _generate_v4_figures_impl(
    results_root: str | Path,
    *,
    _output_root: str | Path | None = None,
) -> Mapping[str, Any]:
    """Generate the eight preregistered PNGs and deterministic selection sidecar."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(results_root).resolve()
    destination = (
        Path(_output_root).resolve() if _output_root is not None else root
    )
    out = destination / "generated_figures"
    traces = destination / "sample_traces"
    if out.exists() or traces.exists():
        raise V4HandoffError("refusing to overwrite generated figures or sample traces")

    primary_rows = _find_csv(root, "primary_comparison.csv")
    metrics = _find_csv(root, "metrics_by_trajectory.csv")
    family = _find_csv(root, "family_effects.csv")
    demand = _find_csv(root, "demand_stratum_effects.csv")
    harm = _find_csv(root, "harmful_trajectory_rate.csv")
    worst = _find_csv(root, "worst_five_trajectories.csv", allow_empty=True)
    constraint = _find_csv(root, "constraint_audit.csv")
    runtime_samples = _find_csv(root, "runtime_repeated_samples.csv")
    complete_primary = all(
        row.get("paired_value_available", True) is True
        and _optional_number(
            row, "baseline_position_rmse", "baseline_value"
        )
        is not None
        and _optional_number(
            row, "candidate_position_rmse", "candidate_value"
        )
        is not None
        for row in primary_rows
    )
    selection = (
        select_v4_representative_trajectories(primary_rows)
        if complete_primary
        else []
    )
    samples = _load_samples(root)
    primary_samples = samples[samples["method_id"].isin(PRIMARY_METHODS)].copy()
    if primary_samples.empty:
        raise V4HandoffError("locked samples contain no primary methods")
    if not np.isfinite(primary_samples["command_time"].to_numpy(dtype=float)).all():
        raise V4HandoffError("command_time contains non-finite values")

    out.mkdir(parents=True)
    traces.mkdir(parents=True)
    colors = ("#0072B2", "#E69F00", "#009E73")
    markers = ("o", "s", "^")
    plt.rcParams.update({"axes.grid": True, "grid.alpha": 0.25})

    fig, ax = plt.subplots(figsize=(7, 4))
    values = []
    for method in PRIMARY_METHODS:
        rows = [row for row in metrics if _method(row) == method]
        values.append([_number(row, "position_rmse") for row in rows])
    if all(values):
        ax.boxplot(values, tick_labels=["P", "PV", "PVA"], showmeans=True)
    else:
        ax.text(0.5, 0.5, "Unavailable: incomplete method denominator", ha="center")
    ax.set(ylabel="Trajectory position RMSE", title=f"Synthetic locked test (n={len(primary_rows)})")
    _save_figure(fig, out / FIGURE_FILENAMES[0])
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    diffs = [
        value
        for row in primary_rows
        for value in (
            _optional_number(
                row, "candidate_minus_baseline_position_rmse", "effect"
            ),
        )
        if value is not None
    ]
    ax.axhline(0, color="black", lw=1)
    if diffs:
        ax.scatter(range(len(diffs)), diffs, s=14, color=colors[0], marker=markers[0])
    else:
        ax.text(0.5, 0.5, "Unavailable: no complete primary pairs", ha="center")
    ax.set(xlabel="Locked trajectory (lexicographic order)", ylabel="PVA − P RMSE", title=f"Paired effect; synthetic locked test (n={len(diffs)})")
    _save_figure(fig, out / FIGURE_FILENAMES[1])
    plt.close(fig)

    def effect_plot(rows: Sequence[Mapping[str, Any]], name: str, title: str) -> None:
        fig, ax = plt.subplots(figsize=(8, 4))
        available = [
            row
            for row in rows
            if _optional_number(row, "relative_improvement", "effect") is not None
            and _optional_number(row, "relative_improvement_ci_low", "ci_low")
            is not None
            and _optional_number(row, "relative_improvement_ci_high", "ci_high")
            is not None
        ]
        labels = [str(row.get("stratum_value", row.get(name))) for row in available]
        points = [_number(row, "relative_improvement", "effect") for row in available]
        lows = [_number(row, "relative_improvement_ci_low", "ci_low") for row in available]
        highs = [_number(row, "relative_improvement_ci_high", "ci_high") for row in available]
        ax.axhline(0, color="black", lw=1)
        if available:
            ax.errorbar(
                range(len(available)), points,
                yerr=[np.asarray(points)-np.asarray(lows), np.asarray(highs)-np.asarray(points)],
                fmt="o", color=colors[0], ecolor="0.35", capsize=3,
            )
        else:
            ax.text(0.5, 0.5, "Unavailable: incomplete subgroup inference", ha="center")
        ax.set_xticks(range(len(available)), labels, rotation=25, ha="right")
        ax.set(ylabel="Relative RMSE improvement", title=title)
        _save_figure(fig, out / name)
        plt.close(fig)

    effect_plot(family, FIGURE_FILENAMES[2], f"Family effects; synthetic locked test (n={len(primary_rows)})")
    effect_plot(demand, FIGURE_FILENAMES[3], f"Demand effects; synthetic locked test (n={len(primary_rows)})")

    primary_harm = next(
        (row for row in harm if row.get("analysis_kind") in ("primary", None)),
        None,
    )
    if primary_harm is None:
        raise V4HandoffError("primary harmful-rate row is missing")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    rate = _number(primary_harm, "harmful_rate")
    denominator = int(_number(primary_harm, "denominator", "n_trajectories"))
    axes[0].bar(["PVA harm"], [rate], color=colors[1], hatch="//", edgecolor="black")
    axes[0].set(ylim=(0, 1), ylabel="Harmful trajectory rate", title=f"Full denominator n={denominator}")
    worst_values = [_number(row, "candidate_minus_baseline_position_rmse", "effect", "improvement") for row in worst]
    axes[1].barh(range(len(worst)), worst_values, color=colors[1], hatch="//", edgecolor="black")
    axes[1].set_yticks(range(len(worst)), [str(row["trajectory_id"]) for row in worst])
    axes[1].set(xlabel="PVA − P RMSE", title="Worst five retained")
    _save_figure(fig, out / FIGURE_FILENAMES[4])
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    representative_id = (
        selection[0]["trajectory_id"] if selection else "unavailable_incomplete_denominator"
    )
    trace = primary_samples[primary_samples["trajectory_id"] == representative_id]
    if selection:
        if trace.empty:
            raise V4HandoffError("representative trace is absent from locked samples")
        for index, method in enumerate(PRIMARY_METHODS):
            part = trace[trace["method_id"] == method].sort_values(["command_time", "joint_id", "k"])
            if part.empty:
                raise V4HandoffError(f"representative trace is incomplete for {method}")
            axes[0].plot(part["command_time"], part["command_v"], color=colors[index], ls=("-", "--", "-.")[index], label=("P", "PV", "PVA")[index])
            axes[1].plot(part["command_time"], part["command_a"], color=colors[index], ls=("-", "--", "-.")[index], label=("P", "PV", "PVA")[index])
            if "raw_target_v" in part and part["raw_target_v"].notnull().all():
                axes[0].plot(
                    part["command_time"], part["raw_target_v"], color=colors[index],
                    ls=":", alpha=0.7, label=f"{('P', 'PV', 'PVA')[index]} target",
                )
            if "raw_target_a" in part and part["raw_target_a"].notnull().all():
                axes[1].plot(
                    part["command_time"], part["raw_target_a"], color=colors[index],
                    ls=":", alpha=0.7, label=f"{('P', 'PV', 'PVA')[index]} target",
                )
    else:
        axes[0].text(0.5, 0.5, "Unavailable: representative selection requires full pairs", ha="center")
        axes[1].text(0.5, 0.5, "No complete-case trace selection performed", ha="center")
    axes[0].set(ylabel="Command velocity")
    axes[1].set(ylabel="Command acceleration", xlabel="command_time (s)")
    if selection:
        axes[0].legend()
        axes[1].legend()
    fig.suptitle(f"Target components and command profiles; synthetic locked test\n{representative_id}")
    _save_figure(fig, out / FIGURE_FILENAMES[5])
    plt.close(fig)

    margin_fields = ("velocity_margin", "acceleration_margin", "jerk_margin")
    if not all(any(row.get(field) is not None for row in constraint) for field in margin_fields):
        raise V4HandoffError("continuous constraint margins are incomplete")
    fig, axes = plt.subplots(1, 3, figsize=(11, 4))
    for ax, field, label in zip(axes, margin_fields, ("Velocity", "Acceleration", "Jerk")):
        vals = [_number(row, field) for row in constraint if row.get(field) is not None]
        ax.boxplot(vals)
        ax.axhline(0, color="black", lw=1)
        ax.set(title=label, ylabel="Continuous margin")
    fig.suptitle(f"Continuous V/A/J margins; synthetic locked test (n={len(primary_rows)})")
    _save_figure(fig, out / FIGURE_FILENAMES[6])
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    distributions = [
        [
            _number(row, "total_compute_us")
            for row in runtime_samples
            if _method(row) == method
        ]
        for method in PRIMARY_METHODS
    ]
    if not all(distributions):
        raise V4HandoffError("formal repeated runtime samples lack a primary method")
    ax.boxplot(
        distributions,
        tick_labels=["P", "PV", "PVA"],
        showfliers=True,
        patch_artist=True,
        boxprops={"facecolor": colors[0]},
        medianprops={"color": "black"},
    )
    ax.axhline(1000, color="0.3", ls="--", label="P99 gate")
    ax.set(ylabel="Runtime (µs)", title="Runtime distribution summary; synthetic locked test")
    ax.legend()
    _save_figure(fig, out / FIGURE_FILENAMES[7])
    plt.close(fig)

    sidecar = {
        "schema_version": "otg.v4-representative-selection.v1",
        "selection_source": "V4_STATISTICAL_DESIGN.json.plot_selection",
        "manual_selection_permitted": False,
        "duplicate_roles_retained": True,
        "time_axis": "command_time",
        "denominator": len(primary_rows),
        "selection_status": (
            "available" if selection else "unavailable_incomplete_denominator"
        ),
        "selections": selection,
    }
    (traces / "representative_selection.json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    unique_ids = sorted({row["trajectory_id"] for row in selection})
    for trajectory_id in unique_ids:
        primary_samples[primary_samples["trajectory_id"] == trajectory_id].to_csv(
            traces / f"{trajectory_id}.csv", index=False
        )
    manifest = {
        "figures": [str(Path("generated_figures") / name) for name in FIGURE_FILENAMES],
        "representative_selection": "sample_traces/representative_selection.json",
        "caption_label": "synthetic locked test",
        "oracle_label": "offline noncausal diagnostic",
        "ordinary_ruckig_label": "contextual secondary",
        "grayscale_readable": True,
        "color_blind_friendly": True,
    }
    (out / "figure_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def generate_v4_figures(
    results_root: str | Path,
    *,
    _output_root: str | Path | None = None,
) -> Mapping[str, Any]:
    """Atomically generate bounded figures, or populate a caller staging root."""

    root = Path(results_root).resolve()
    if _output_root is not None:
        return _generate_v4_figures_impl(root, _output_root=_output_root)
    if (root / "generated_figures").exists() or (root / "sample_traces").exists():
        raise V4HandoffError(
            "refusing to overwrite generated figures or sample traces"
        )
    staging = Path(tempfile.mkdtemp(prefix=".v4-figures-", dir=root))
    try:
        manifest = _generate_v4_figures_impl(root, _output_root=staging)
        os.replace(staging / "generated_figures", root / "generated_figures")
        os.replace(staging / "sample_traces", root / "sample_traces")
        return manifest
    except BaseException:
        for name in ("generated_figures", "sample_traces"):
            target = root / name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _tex_escape(value: Any) -> str:
    text = str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _fmt(value: Any) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if isinstance(value, (int, float)):
        return f"{value:.6g}"
    return str(value)


def _write_tex_table(path: Path, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    align = "l" * len(headers)
    lines = [
        rf"\begin{{tabular}}{{{align}}}",
        r"\hline",
        " & ".join(_tex_escape(item) for item in headers) + r" \\",
        r"\hline",
    ]
    lines.extend(
        " & ".join(_tex_escape(_fmt(item)) for item in row) + r" \\" for row in rows
    )
    lines.extend((r"\hline", r"\end{tabular}", ""))
    path.write_text("\n".join(lines), encoding="utf-8")


def _sample_invariant_gate(frame: Any) -> dict[str, Any]:
    """Independently check point, one-step, and adjacent-state invariants."""

    required = {
        "method_id",
        "trajectory_id",
        "joint_id",
        "k",
        "control_time",
        "dt_control",
        "executable_target_p",
        "executable_target_v",
        "executable_target_a",
        "executable_target_time",
        "free_trajectory_duration",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        return {"passed": False, "error": f"missing invariant fields {missing}"}
    counts = {
        "point_admissibility_failures": 0,
        "one_step_time_failures": 0,
        "t_free_failures": 0,
        "sequence_consistency_failures": 0,
        "continuous_jerk_failures": 0,
        "nonfinite_values": 0,
    }
    numeric = (
        "control_time",
        "dt_control",
        "executable_target_p",
        "executable_target_v",
        "executable_target_a",
        "executable_target_time",
        "free_trajectory_duration",
    )
    values = frame[list(numeric)].to_numpy(dtype=float)
    counts["nonfinite_values"] = int(np.count_nonzero(~np.isfinite(values)))
    for _, group in frame.groupby(
        ["method_id", "trajectory_id", "joint_id"], sort=True
    ):
        ordered = group.sort_values("k")
        p = ordered["executable_target_p"].to_numpy(dtype=float)
        v = ordered["executable_target_v"].to_numpy(dtype=float)
        a = ordered["executable_target_a"].to_numpy(dtype=float)
        target_time = ordered["executable_target_time"].to_numpy(dtype=float)
        control_time = ordered["control_time"].to_numpy(dtype=float)
        dt_control = ordered["dt_control"].to_numpy(dtype=float)
        free = ordered["free_trajectory_duration"].to_numpy(dtype=float)
        stopping_bound = np.sqrt(np.maximum(0.0, 2.0 * 4000.0 * (4.1 - np.abs(v))))
        point = (
            (np.abs(v) <= 4.1 + 1e-8)
            & (np.abs(a) <= 8.2 + 1e-8)
            & (np.abs(a) <= stopping_bound + 1e-8)
        )
        counts["point_admissibility_failures"] += int(np.count_nonzero(~point))
        one_step = np.abs(target_time - control_time - dt_control) <= 1e-8
        counts["one_step_time_failures"] += int(np.count_nonzero(~one_step))
        counts["t_free_failures"] += int(
            np.count_nonzero(free > dt_control + 1e-8)
        )
        if len(ordered) > 1:
            duration = np.diff(target_time)
            invalid_duration = duration <= 0
            safe_duration = np.where(invalid_duration, 1.0, duration)
            jerk = np.diff(a) / safe_duration
            expected_p = (
                p[:-1]
                + v[:-1] * safe_duration
                + 0.5 * a[:-1] * safe_duration**2
                + jerk * safe_duration**3 / 6.0
            )
            expected_v = (
                v[:-1]
                + a[:-1] * safe_duration
                + 0.5 * jerk * safe_duration**2
            )
            consistent = (
                ~invalid_duration
                & (np.abs(p[1:] - expected_p) <= 1e-8)
                & (np.abs(v[1:] - expected_v) <= 1e-8)
                & (np.abs(a[1:] - (a[:-1] + jerk * safe_duration)) <= 1e-8)
            )
            counts["sequence_consistency_failures"] += int(
                np.count_nonzero(~consistent)
            )
            counts["continuous_jerk_failures"] += int(
                np.count_nonzero(np.abs(jerk) > 4000.0 + 1e-8)
            )
    return {
        "passed": all(count == 0 for count in counts.values()),
        "failure_counts": counts,
        "error": None,
    }


def _runtime_evidence_gate(root: Path) -> dict[str, Any]:
    samples = _find_csv(root, "runtime_repeated_samples.csv")
    failures = _find_csv(root, "runtime_repeated_failures.csv", allow_empty=True)
    primary_failures = [
        row for row in failures if _method(row) in PRIMARY_METHODS
    ]
    summaries = []
    all_keys: set[tuple[Any, ...]] = set()
    passed = not primary_failures
    for method in PRIMARY_METHODS:
        rows = [row for row in samples if _method(row) == method]
        repetitions = {int(_number(row, "repetition")) for row in rows}
        warmups = {
            int(_number(row, "warmup_cycles_per_trajectory")) for row in rows
        }
        totals = np.asarray(
            [_number(row, "total_compute_us") for row in rows], dtype=float
        )
        local_keys = {
            (
                method,
                int(_number(row, "repetition")),
                str(row.get("dataset_id")),
                str(row.get("session_id")),
                str(row.get("trajectory_id")),
                str(row.get("scenario_id")),
                int(_number(row, "k")),
            )
            for row in rows
        }
        duplicates = len(local_keys) != len(rows) or bool(all_keys & local_keys)
        all_keys.update(local_keys)
        misses = sum(_bool(row, "deadline_miss") for row in rows)
        method_pass = (
            bool(rows)
            and repetitions == set(range(5))
            and warmups == {100}
            and not duplicates
            and misses == 0
            and float(np.quantile(totals, 0.99, method="linear")) < 1000
            and float(np.max(totals)) < 5000
        )
        passed = passed and method_pass
        summaries.append(
            {
                "method": method,
                "passed": method_pass,
                "timed_cycle_count": len(rows),
                "repetition_count": len(repetitions),
                "warmup_cycles_per_trajectory": (
                    next(iter(warmups)) if len(warmups) == 1 else None
                ),
                "total_p99_us": (
                    float(np.quantile(totals, 0.99, method="linear"))
                    if totals.size
                    else None
                ),
                "total_max_us": float(np.max(totals)) if totals.size else None,
                "deadline_miss_count": misses,
                "duplicate_cycle_key_count": len(rows) - len(local_keys),
            }
        )
    return {
        "passed": passed,
        "p99_limit_us_strict": 1000,
        "max_limit_us_strict": 5000,
        "deadline_miss_rate_required": 0,
        "repetitions_required": 5,
        "warmup_cycles_per_trajectory_required": 100,
        "failure_count": len(primary_failures),
        "methods": summaries,
    }


def _audit_gates(root: Path) -> dict[str, Any]:
    identity = _find_csv(root, "method_identity_summary.csv")
    identity_detail = _find_csv(root, "method_identity_by_trajectory.csv")
    same = _find_csv(root, "same_information_audit.csv")
    constraints = _find_csv(root, "constraint_audit.csv")
    completion = _find_csv(root, "completion_summary.csv")
    failures = _find_csv(root, "failures.csv", allow_empty=True)
    fallbacks = _find_csv(root, "fallback_events.csv", allow_empty=True)
    runtime = _find_csv(root, "runtime_benchmark.csv")
    aggregate_primary = [row for row in identity if _method(row) in PRIMARY_METHODS]
    aggregate_counts = {
        method: [row for row in aggregate_primary if _method(row) == method]
        for method in PRIMARY_METHODS
    }
    aggregate_pass = all(
        len(aggregate_counts[method]) == 1
        and int(_number(aggregate_counts[method][0], "trajectory_count")) == 120
        and _number(aggregate_counts[method][0], "method_purity_rate") == 1.0
        for method in PRIMARY_METHODS
    )
    detail_primary = [
        row for row in identity_detail if _method(row) in PRIMARY_METHODS
    ]
    detail_keys = [
        (_method(row), str(row.get("trajectory_id", ""))) for row in detail_primary
    ]
    detail_pass = (
        len(detail_primary) == 360
        and len(set(detail_keys)) == 360
        and all(
            _number(row, "method_purity_rate") == 1.0
            and (
                _bool(row, "passed")
                if row.get("passed") is not None
                else True
            )
            for row in detail_primary
        )
        and all(
            sum(method == candidate for method, _ in detail_keys) == 120
            for candidate in PRIMARY_METHODS
        )
    )
    method_pass = aggregate_pass and detail_pass
    same_primary_ids = {
        str(row.get("trajectory_id", "")) for row in same if row.get("trajectory_id")
    }
    same_pass = len(same_primary_ids) == 120 and all(
        _bool(row, "same_information_passed", "audit_passed") for row in same
    )
    primary_constraints = [
        row for row in constraints if _method(row) in PRIMARY_METHODS
    ]
    constraint_pass = bool(primary_constraints) and all(
        _number(row, "violation_count") == 0 for row in primary_constraints
    )
    completion_by_method = {_method(row): row for row in completion}
    completion_pass = all(
        method in completion_by_method
        and int(_number(completion_by_method[method], "attempted_trajectories", "attempted_trajectory_runs")) == 120
        and int(_number(completion_by_method[method], "completed_trajectories", "successful_trajectory_runs")) == 120
        and int(_number(completion_by_method[method], "failed_trajectories", "failed_trajectory_runs")) == 0
        for method in PRIMARY_METHODS
    )
    primary_failure_rows = [
        row for row in failures if _method(row) in PRIMARY_METHODS
    ]
    primary_fallback_rows = [
        row for row in fallbacks if _method(row) in PRIMARY_METHODS
    ]
    runtime_gate = _runtime_evidence_gate(root)
    samples = _load_samples(root)
    primary_samples = samples[samples["method_id"].isin(PRIMARY_METHODS)].copy()
    sample_ids_by_method = {
        method: set(
            primary_samples.loc[
                primary_samples["method_id"] == method, "trajectory_id"
            ].astype(str)
        )
        for method in PRIMARY_METHODS
    }
    sample_denominator_pass = all(
        len(sample_ids_by_method[method]) == 120 for method in PRIMARY_METHODS
    )
    required_true = (
        "executable_target_point_admissible",
        "command_segment_feasible",
        "command_stopping_viable",
        "command_next_step_exists",
        "command_t_free_le_dt",
        "command_continuous_constraints_satisfied",
        "command_profile_exact",
        "command_constant_jerk_exact",
        "command_endpoint_matches_profile",
        "command_profile_continuous_constraints_satisfied",
        "native_command_executed",
    )
    required_false = (
        "target_projected",
        "fallback",
        "fallback_applied",
        "emergency_mode",
        "safety_shield_requested",
        "safety_shield_applied",
        "fallback_changes_algorithm",
    )
    missing_sample_gate_fields = sorted(
        (set(required_true) | set(required_false)) - set(primary_samples.columns)
    )
    gate_failure_counts: dict[str, int] = {}
    if not missing_sample_gate_fields:
        for field in required_true:
            gate_failure_counts[field] = int(
                (~primary_samples[field].astype(bool)).sum()
            )
        for field in required_false:
            gate_failure_counts[field] = int(
                primary_samples[field].astype(bool).sum()
            )
    numeric_fields = (
        "command_time",
        "command_p",
        "command_v",
        "command_a",
        "executable_target_p",
        "executable_target_v",
        "executable_target_a",
        "executable_target_time",
        "free_trajectory_duration",
    )
    missing_numeric_fields = sorted(set(numeric_fields) - set(primary_samples.columns))
    unexplained_nan_count = (
        int(primary_samples[list(numeric_fields)].isnull().sum().sum())
        if not missing_numeric_fields
        else -1
    )
    invariant = _sample_invariant_gate(primary_samples)
    invariant_pass = bool(invariant["passed"])
    sample_gate_pass = (
        sample_denominator_pass
        and not missing_sample_gate_fields
        and not missing_numeric_fields
        and unexplained_nan_count == 0
        and all(count == 0 for count in gate_failure_counts.values())
        and invariant_pass
    )
    return {
        "method_identity": {
            "passed": method_pass,
            "required_purity_rate": 1.0,
            "aggregate_row_count": len(aggregate_primary),
            "trajectory_detail_row_count": len(detail_primary),
            "trajectory_detail_unique_count": len(set(detail_keys)),
        },
        "same_information": {"passed": same_pass},
        "safety": {
            "passed": (
                constraint_pass
                and completion_pass
                and not primary_failure_rows
                and not primary_fallback_rows
                and sample_gate_pass
            ),
            "continuous_constraint_audit_passed": constraint_pass,
            "completion_denominator_passed": completion_pass,
            "sample_denominator_passed": sample_denominator_pass,
            "sample_validity_gates_passed": sample_gate_pass,
            "sample_gate_failure_counts": gate_failure_counts,
            "missing_sample_gate_fields": missing_sample_gate_fields,
            "unexplained_nan_count": unexplained_nan_count,
            "invariant_recomputation_passed": invariant_pass,
            "invariant_recomputation_error": invariant["error"],
            "invariant_failure_counts": invariant.get("failure_counts", {}),
            "failure_count": len(primary_failure_rows),
            "fallback_event_count": len(primary_fallback_rows),
        },
        "runtime": {**runtime_gate, "summary_rows": runtime},
    }


def generate_v4_handoff(
    results_root: str | Path,
    raw_commit: str,
    source_hashes: Mapping[str, str],
    *,
    report_only: bool = False,
) -> Mapping[str, Any]:
    """Create bounded JSON/Markdown/TeX and figures from locked evidence only."""

    if not re.fullmatch(r"[0-9a-f]{40}", raw_commit):
        raise V4HandoffError("raw_commit must be a lowercase 40-character Git SHA")
    if not source_hashes or any(
        not isinstance(key, str)
        or not re.fullmatch(r"[0-9a-f]{64}", value)
        for key, value in source_hashes.items()
    ):
        raise V4HandoffError("source_hashes must be a nonempty path-to-SHA-256 mapping")
    root = Path(results_root).resolve()
    outputs = (
        root / "paper_handoff.json",
        root / "paper_handoff.md",
        root / "V4_RESULT_SUMMARY.md",
        root / "generated_numbers.tex",
        root / "generated_tables",
        root / "generated_figures",
        root / "sample_traces",
    )
    if not report_only and any(path.exists() for path in outputs):
        raise V4HandoffError("refusing to overwrite an existing V4 handoff")
    primary_rows = _find_csv(root, "primary_comparison.csv")
    secondary = _find_csv(root, "secondary_comparisons.csv")
    family = _find_csv(root, "family_effects.csv")
    demand = _find_csv(root, "demand_stratum_effects.csv")
    acceleration = _find_csv(root, "acceleration_active_effect.csv")
    oracle_metrics = _find_csv(root, "oracle_target_component_metrics.csv")
    oracle_pv_p = _find_csv(root, "oracle_pv_vs_p.csv")
    oracle_pva_pv = _find_csv(root, "oracle_pva_vs_pv.csv")
    oracle_active = _find_csv(root, "oracle_acceleration_active_effect.csv")
    for row in oracle_metrics:
        if not (
            row.get("information_condition") == "offline_analytic_truth"
            and _bool(row, "causal") is False
            and _bool(row, "deployable") is False
            and _bool(row, "diagnostic_only") is True
        ):
            raise V4HandoffError("oracle evidence is not explicitly noncausal diagnostic")

    primary = _primary_summary(primary_rows)
    gates = _audit_gates(root)
    denominator_valid = (
        primary["paired_denominator"] == primary["required_denominator"] == 120
    )
    method_valid = gates["method_identity"]["passed"] and gates["same_information"]["passed"]
    safety_valid = gates["safety"]["passed"]
    gates_valid = denominator_valid and method_valid and safety_valid
    effective_classification = (
        "unavailable_incomplete_denominator"
        if not denominator_valid
        else (
            "invalid_method_identity"
            if not method_valid
            else ("invalid_safety_gate" if not safety_valid else primary["classification"])
        )
    )
    allowed, prohibited = _claim(primary, gates_valid)
    harmful_rows = [
        {
            "trajectory_id": row["trajectory_id"],
            "candidate_minus_baseline_position_rmse": _number(
                row, "candidate_minus_baseline_position_rmse", "effect"
            ),
        }
        for row in primary_rows
        if row.get("harmful") is True
        or (
            _optional_number(
                row, "candidate_minus_baseline_position_rmse", "effect"
            )
            is not None
            and _number(
                row, "candidate_minus_baseline_position_rmse", "effect"
            )
            > 0
        )
    ]
    negative_results = {
        "primary_statistical_classification": primary["classification"],
        "effective_classification": effective_classification,
        "harmful_trajectories": harmful_rows,
        "nonpositive_family_effects": [
            row
            for row in family
            if _optional_number(row, "relative_improvement", "effect") is not None
            and _number(row, "relative_improvement", "effect") <= 0
        ],
        "nonpositive_demand_effects": [
            row
            for row in demand
            if _optional_number(row, "relative_improvement", "effect") is not None
            and _number(row, "relative_improvement", "effect") <= 0
        ],
        "failed_guardrails": [
            name
            for name, passed in (
                ("max_error", primary["max_error_guardrail_pass"]),
                ("lag", primary["lag_guardrail_pass"]),
            )
            if not passed
        ],
        "failed_validity_gates": [
            name
            for name, passed in (
                ("denominator", denominator_valid),
                ("method_identity", method_valid),
                ("safety", safety_valid),
                ("runtime", gates["runtime"]["passed"]),
            )
            if not passed
        ],
    }
    staging = Path(tempfile.mkdtemp(prefix=".v4-handoff-", dir=root))
    try:
        figure_manifest = generate_v4_figures(root, _output_root=staging)
        selection = json.loads(
            (staging / "sample_traces" / "representative_selection.json").read_text()
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    handoff = {
        "schema_version": "otg.v4-paper-handoff.v1",
        "raw_commit": raw_commit,
        "post_test_result_status": (
            "complete_negative"
            if effective_classification in {"inconclusive", "confirmed_harmful"}
            else (
                "complete_confirmatory"
                if effective_classification
                in {
                    "confirmed_positive",
                    "practically_material",
                    "strongly_material",
                }
                else "failed_nonconfirmatory"
            )
        ),
        "primary_result_classification": effective_classification,
        "statistical_classification": primary["classification"],
        "primary_effect": primary,
        "guardrail_status": {
            "max_error_noninferiority_pass": primary["max_error_guardrail_pass"],
            "lag_noninferiority_pass": primary["lag_guardrail_pass"],
            "without_degradation_wording_permitted": bool(
                gates_valid
                and primary["max_error_guardrail_pass"]
                and primary["lag_guardrail_pass"]
            ),
        },
        "secondary_results": secondary,
        "family_effects": family,
        "demand_effects": demand,
        "acceleration_active_effect": acceleration,
        "oracle_diagnostics": {
            "information_condition": "offline_analytic_truth",
            "causal": False,
            "deployable": False,
            "diagnostic_only": True,
            "excluded_from_primary": True,
            "target_component_metrics": oracle_metrics,
            "pv_vs_p": oracle_pv_p,
            "pva_vs_pv": oracle_pva_pv,
            "acceleration_active": oracle_active,
        },
        "ordinary_ruckig": {
            "role": "contextual_secondary",
            "affects_primary_conclusion": False,
            "results": [row for row in secondary if row.get("comparison_id") == "S5"],
        },
        "safety_gates": gates["safety"],
        "method_identity_gate": gates["method_identity"],
        "same_information_gate": gates["same_information"],
        "runtime_gates": gates["runtime"],
        "negative_results": negative_results,
        "allowed_claim_wording": allowed,
        "prohibited_claim_wording": prohibited,
        "source_artifact_hashes": dict(sorted(source_hashes.items())),
        "representative_selection": selection,
        "figure_manifest": figure_manifest,
    }
    existing_handoff = root / "paper_handoff.json"
    if report_only and existing_handoff.is_file():
        try:
            previous = json.loads(existing_handoff.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise V4HandoffError(
                "report-only existing paper_handoff.json is unreadable"
            ) from exc
        immutable_sections = (
            "raw_commit",
            "post_test_result_status",
            "primary_result_classification",
            "statistical_classification",
            "primary_effect",
            "guardrail_status",
            "secondary_results",
            "family_effects",
            "demand_effects",
            "acceleration_active_effect",
            "oracle_diagnostics",
            "ordinary_ruckig",
            "safety_gates",
            "method_identity_gate",
            "same_information_gate",
            "runtime_gates",
            "negative_results",
        )
        if any(previous.get(key) != handoff.get(key) for key in immutable_sections):
            shutil.rmtree(staging, ignore_errors=True)
            raise V4HandoffError(
                "report-only handoff differs from existing immutable evidence"
            )
        previous_hashes = previous.get("source_artifact_hashes")
        current_hashes = handoff.get("source_artifact_hashes")
        if not isinstance(previous_hashes, dict) or not isinstance(
            current_hashes, dict
        ):
            shutil.rmtree(staging, ignore_errors=True)
            raise V4HandoffError(
                "report-only handoff has invalid source artifact hashes"
            )

        provenance_prefixes = ("raw_commit:", "reporting_commit:")

        def _base_hashes(hashes: Mapping[str, str]) -> dict[str, str]:
            return {
                key: value
                for key, value in hashes.items()
                if not key.startswith(provenance_prefixes)
            }

        previous_base = _base_hashes(previous_hashes)
        current_base = _base_hashes(current_hashes)
        if previous_base != current_base:
            shutil.rmtree(staging, ignore_errors=True)
            raise V4HandoffError(
                "report-only handoff source evidence differs from existing evidence"
            )

        provenance_paths = {
            key.removeprefix(prefix)
            for hashes in (previous_hashes, current_hashes)
            for key in hashes
            for prefix in provenance_prefixes
            if key.startswith(prefix)
        }
        valid_provenance = all(
            path in current_base
            and current_hashes.get(f"raw_commit:{path}") == current_base[path]
            and isinstance(current_hashes.get(f"reporting_commit:{path}"), str)
            and re.fullmatch(
                r"[0-9a-f]{64}",
                current_hashes[f"reporting_commit:{path}"],
            )
            is not None
            for path in provenance_paths
        )
        if not valid_provenance:
            shutil.rmtree(staging, ignore_errors=True)
            raise V4HandoffError(
                "report-only handoff has invalid reporting provenance"
            )

    tables = staging / "generated_tables"
    tables.mkdir()
    _write_tex_table(
        tables / "primary_result.tex",
        ("Classification", "Effect", "95% CI", "n"),
        (
            (
                effective_classification,
                primary["relative_improvement"],
                f"[{_fmt(primary['relative_ci_low'])}, {_fmt(primary['relative_ci_high'])}]",
                primary["paired_denominator"],
            ),
        ),
    )
    _write_tex_table(
        tables / "secondary_results.tex",
        ("ID", "Status", "Effect", "95% CI", "Role"),
        [
            (
                row.get("comparison_id"),
                row.get("status"),
                row.get("relative_difference", row.get("effect")),
                f"[{_fmt(row.get('relative_improvement_ci_low', row.get('ci_low')))}, {_fmt(row.get('relative_improvement_ci_high', row.get('ci_high')))}]",
                "contextual secondary" if row.get("comparison_id") == "S5" else "secondary",
            )
            for row in secondary
        ],
    )
    _write_tex_table(
        tables / "family_effects.tex",
        ("Family", "n", "Effect", "95% CI", "Harmful rate"),
        [
            (
                row.get("stratum_value", row.get("family")),
                row.get("trajectory_count", row.get("n_trajectories")),
                row.get("relative_improvement", row.get("effect")),
                f"[{_fmt(row.get('relative_improvement_ci_low', row.get('ci_low')))}, {_fmt(row.get('relative_improvement_ci_high', row.get('ci_high')))}]",
                row.get("harmful_rate"),
            )
            for row in family
        ],
    )
    _write_tex_table(
        tables / "demand_effects.tex",
        ("Demand", "n", "Effect", "95% CI", "Harmful rate"),
        [
            (
                row.get("stratum_value", row.get("demand_stratum")),
                row.get("trajectory_count", row.get("n_trajectories")),
                row.get("relative_improvement", row.get("effect")),
                f"[{_fmt(row.get('relative_improvement_ci_low', row.get('ci_low')))}, {_fmt(row.get('relative_improvement_ci_high', row.get('ci_high')))}]",
                row.get("harmful_rate"),
            )
            for row in demand
        ],
    )
    _write_tex_table(
        tables / "oracle_diagnostics.tex",
        ("Diagnostic", "Label"),
        (
            ("PV vs P", "offline noncausal diagnostic"),
            ("PVA vs PV", "offline noncausal diagnostic"),
            ("Acceleration active", "offline noncausal diagnostic"),
        ),
    )
    numbers = "\n".join(
        (
            rf"\providecommand{{\VFourPrimaryClassification}}{{{_tex_escape(effective_classification)}}}",
            rf"\providecommand{{\VFourPrimaryRelativeEffect}}{{{_tex_escape(_fmt(primary['relative_improvement']))}}}",
            rf"\providecommand{{\VFourPrimaryCILow}}{{{_tex_escape(_fmt(primary['relative_ci_low']))}}}",
            rf"\providecommand{{\VFourPrimaryCIHigh}}{{{_tex_escape(_fmt(primary['relative_ci_high']))}}}",
            rf"\providecommand{{\VFourPrimaryDenominator}}{{{primary['paired_denominator']}}}",
            rf"\providecommand{{\VFourAllowedClaim}}{{{_tex_escape(allowed[0])}}}",
            "",
        )
    )
    (staging / "generated_numbers.tex").write_text(numbers, encoding="utf-8")
    (staging / "paper_handoff.json").write_text(
        json.dumps(handoff, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = "\n".join(
        (
            "# V4 paper evidence handoff",
            "",
            f"- Primary classification: `{effective_classification}`",
            f"- Post-test result status: `{handoff['post_test_result_status']}`",
            f"- Statistical classification: `{primary['classification']}`",
            f"- Relative effect: {_fmt(primary['relative_improvement'])}",
            f"- 95% CI: [{_fmt(primary['relative_ci_low'])}, {_fmt(primary['relative_ci_high'])}]",
            f"- Complete paired denominator: {primary['paired_denominator']}/{primary['required_denominator']}",
            f"- Max-error guardrail: {_fmt(primary['max_error_guardrail_pass'])}",
            f"- Lag guardrail: {_fmt(primary['lag_guardrail_pass'])}",
            "",
            "## Allowed wording",
            "",
            *[f"- {claim}" for claim in allowed],
            "",
            "## Negative results retained",
            "",
            f"- Harmful trajectories: {len(harmful_rows)}",
            f"- Failed guardrails: {', '.join(negative_results['failed_guardrails']) or 'none'}",
            f"- Failed validity/runtime gates: {', '.join(negative_results['failed_validity_gates']) or 'none'}",
            "",
            "Oracle evidence is an offline noncausal diagnostic. Ordinary Ruckig is contextual secondary evidence.",
            "",
        )
    )
    (staging / "paper_handoff.md").write_text(markdown, encoding="utf-8")
    (staging / "V4_RESULT_SUMMARY.md").write_text(markdown, encoding="utf-8")
    promoted_names = (
        "paper_handoff.json",
        "paper_handoff.md",
        "V4_RESULT_SUMMARY.md",
        "generated_numbers.tex",
        "generated_tables",
        "generated_figures",
        "sample_traces",
    )
    backup = staging / ".previous"
    backup.mkdir()
    promoted: list[str] = []
    try:
        for name in promoted_names:
            target = root / name
            if target.exists():
                if not report_only:
                    raise V4HandoffError(f"refusing to overwrite {name}")
                os.replace(target, backup / name)
            os.replace(staging / name, target)
            promoted.append(name)
    except BaseException:
        for name in reversed(promoted):
            target = root / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        for previous in backup.iterdir():
            os.replace(previous, root / previous.name)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return handoff


__all__ = [
    "FIGURE_FILENAMES",
    "HARMFUL_CLAIM",
    "INCONCLUSIVE_CLAIM",
    "POSITIVE_MATERIAL_CLAIM",
    "V4HandoffError",
    "generate_v4_figures",
    "generate_v4_handoff",
    "select_v4_representative_trajectories",
]
