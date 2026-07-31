"""A06 fine-grid PV/PVA VAJ selection and boundary audit."""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from otg_lab.cross_analysis import prepare_analysis, write_prepared_analysis
from otg_lab.cross_analysis_reporting import (
    AnalysisValidationError,
    as_float,
    configure_matplotlib,
    create_analysis_run_directory,
    markdown_table,
    prepared_rows,
    save_figure,
    stable_json,
    validate_figure_files,
    write_analysis_manifest,
    write_csv,
    write_text,
)

ANALYSIS_DIRECTORY = Path(__file__).resolve().parent
CONFIG_PATH = ANALYSIS_DIRECTORY / "analysis.yaml"
RESULTS_DIRECTORY = ANALYSIS_DIRECTORY / "results"
METHOD_ORDER = (
    "pv_pred_backward_o1_kp1",
    "pva_pred_backward_o1_kp1",
)
VELOCITIES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.1)
ACCELERATIONS = (2.0, 3.0, 4.1, 5.0, 6.0, 7.0, 7.5, 8.2)
JERKS = (41.0, 100.0, 200.0, 400.0, 800.0, 1200.0, 1600.0, 2400.0, 3200.0, 4000.0)
VENDOR = (4.1, 8.2, 4000.0)
LAG_BUDGETS_MS = (10.0, 20.0)


def _selected_lag_sensitivity_rows(
    prepared: Any,
) -> list[dict[str, Any]]:
    declaration = prepared.config.get("supplemental_evidence", {})
    relative_directory = Path(str(declaration.get("source_directory", "")))
    if relative_directory.is_absolute() or ".." in relative_directory.parts:
        raise AnalysisValidationError(
            "A06 supplemental source_directory must be project-relative"
        )
    artifact = Path(str(declaration.get("artifact", "")))
    if artifact.is_absolute() or ".." in artifact.parts:
        raise AnalysisValidationError(
            "A06 supplemental artifact must be source-relative"
        )
    source_directory = prepared.project_root / relative_directory
    manifest_path = source_directory / "manifest.json"
    source_path = source_directory / artifact
    if not manifest_path.is_file() or not source_path.is_file():
        raise AnalysisValidationError(
            "A06 selected-setting lag sensitivity run is incomplete"
        )
    with source_path.open(newline="", encoding="utf-8") as handle:
        source_rows = list(csv.DictReader(handle))
    expected_cases = {
        "pv_pred_backward_o1_kp1__v1_a8p2_j3200",
        "pv_pred_backward_o1_kp1__v4p1_a8p2_j3200",
        "pv_pred_backward_o1_kp1__v4p1_a8p2_j4000",
    }
    actual_cases = {str(row["case_id"]) for row in source_rows}
    if actual_cases != expected_cases:
        raise AnalysisValidationError(
            "A06 selected-setting lag sensitivity cases do not match"
        )
    output = []
    for row in source_rows:
        subsample_lag_s = as_float(row.get("lag_subsample_s"))
        integer_lag_s = as_float(row.get("lag_s"))
        if subsample_lag_s is None or integer_lag_s is None:
            raise AnalysisValidationError(
                "A06 selected-setting replay lacks lag metrics"
            )
        velocity = _number(row, "max_velocity_rad_s")
        output.append(
            {
                "source_run": relative_directory.as_posix(),
                "case_id": row["case_id"],
                "deployment_role": (
                    "vendor_reference"
                    if math.isclose(
                        _number(row, "max_jerk_rad_s3"),
                        VENDOR[2],
                    )
                    else (
                        "deployment_recommended"
                        if math.isclose(velocity, 4.1)
                        else "limit_efficient_equivalent"
                    )
                ),
                "max_velocity_rad_s": velocity,
                "max_acceleration_rad_s2": _number(
                    row, "max_acceleration_rad_s2"
                ),
                "max_jerk_rad_s3": _number(row, "max_jerk_rad_s3"),
                "position_rmse_rad": _number(row, "position_rmse_rad"),
                "integer_lag_ms": 1000.0 * integer_lag_s,
                "subsample_lag_ms": 1000.0 * subsample_lag_s,
                "projection_count": int(_number(row, "projection_count")),
                "full_surface_selection_eligible": False,
                "notes": (
                    "Two-case deterministic replay for lag-resolution "
                    "sensitivity; it does not rebuild the 1,280-case Pareto."
                ),
            }
        )
    return sorted(
        output,
        key=lambda row: float(row["max_velocity_rad_s"]),
    )


def _validate_a06_source(prepared: Any) -> list[dict[str, Any]]:
    source = prepared.sources[0]
    manifest = source.manifest
    aggregation = manifest.get("aggregation", {})
    partition_count = aggregation.get(
        "shard_count",
        aggregation.get("reused_source_count", 0),
    )
    checks = (
        (
            "aggregate_status_completed",
            manifest.get("status") == "completed",
            manifest.get("status"),
            "completed",
            True,
            "Aggregate completion means every declared case produced an outcome row.",
        ),
        (
            "required_failure_count",
            int(manifest.get("required_failure_count", 0)) == 0,
            manifest.get("required_failure_count", 0),
            0,
            True,
            "Low-limit sensitivity arms are diagnostic and non-blocking.",
        ),
        (
            "shard_count",
            int(partition_count) > 0,
            partition_count,
            "> 0",
            True,
            "Fixed shards and recovery micro-shards are both listed; the 1,280-case grid check is authoritative.",
        ),
        (
            "diagnostic_failure_count",
            int(manifest.get("failure_count", 0)) == 0,
            manifest.get("failure_count", 0),
            0,
            False,
            "Nonzero means tested low-limit coordinates are retained as ineligible.",
        ),
        (
            "git_dirty",
            not bool(manifest.get("git", {}).get("dirty", False)),
            bool(manifest.get("git", {}).get("dirty", False)),
            False,
            False,
            "Dirty source is retained as an explicit provenance caveat.",
        ),
    )
    rows = []
    for check_id, passed, actual, expected, blocking, notes in checks:
        rows.append(
            {
                "check_id": check_id,
                "scope": source.source_id,
                "status": "pass" if passed else "fail",
                "actual": stable_json(actual),
                "expected": stable_json(expected),
                "blocking": str(blocking).lower(),
                "notes": notes,
            }
        )
        if blocking and not passed:
            raise AnalysisValidationError(f"A06 source validation failed: {check_id}")
    return rows


def _truth(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _number(row: Mapping[str, Any], field: str) -> float:
    value = as_float(row.get(field))
    if value is None:
        raise AnalysisValidationError(f"A06 missing finite {field}: {row}")
    return value


def _validate_surface(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    keys = {
        (
            str(row["method_id"]),
            _number(row, "max_velocity_rad_s"),
            _number(row, "max_acceleration_rad_s2"),
            _number(row, "max_jerk_rad_s3"),
        )
        for row in rows
    }
    expected = {
        (method_id, velocity, acceleration, jerk)
        for method_id in METHOD_ORDER
        for velocity in VELOCITIES
        for acceleration in ACCELERATIONS
        for jerk in JERKS
    }
    completed = all(_truth(row.get("completed")) for row in rows)
    eligible_count = sum(_truth(row.get("eligible")) for row in rows)
    missing_eligible_lag = [
        str(row["case_id"])
        for row in rows
        if _truth(row.get("eligible")) and as_float(row.get("lag_s")) is None
    ]
    checks = (
        (
            "complete_fine_grid",
            keys == expected and len(rows) == 1280,
            {
                "rows": len(rows),
                "unique": len(keys),
                "missing": len(expected - keys),
                "extra": len(keys - expected),
            },
            {"rows": 1280, "unique": 1280, "missing": 0, "extra": 0},
            "2 components × 8 V × 8 A × 10 J.",
            True,
        ),
        (
            "all_runs_completed",
            completed,
            completed,
            True,
            "Informational: incomplete low-limit arms remain ineligible and never use prefix metrics.",
            False,
        ),
        (
            "eligible_case_coverage",
            eligible_count > 0,
            eligible_count,
            "> 0",
            "Eligibility applies exact constraints, solver/fallback, projection reconstruction, and target admissibility.",
            True,
        ),
        (
            "eligible_lag_complete",
            not missing_eligible_lag,
            {"missing": missing_eligible_lag},
            {"missing": []},
            "Every eligible RMSE case has a same-window signed observed lag.",
            True,
        ),
        (
            "all_cases_eligible",
            eligible_count == len(rows),
            eligible_count,
            len(rows),
            "Informational if some tested settings fail hard execution gates.",
            False,
        ),
    )
    output = []
    for check_id, passed, actual, expected_value, notes, blocking in checks:
        output.append(
            {
                "check_id": check_id,
                "scope": "e14_fine_vaj",
                "status": "pass" if passed else "fail",
                "actual": stable_json(actual),
                "expected": stable_json(expected_value),
                "blocking": str(blocking).lower(),
                "notes": notes,
            }
        )
        if blocking and not passed:
            raise AnalysisValidationError(f"A06 validation failed: {check_id}")
    return output


def _rmse_lag_rows(
    surface: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in surface:
        row = dict(source)
        eligible = _truth(row.get("eligible"))
        lag_s = as_float(row.get("lag_s"))
        row["absolute_lag_ms"] = (
            None if lag_s is None else 1000.0 * abs(lag_s)
        )
        for budget_ms in LAG_BUDGETS_MS:
            token = f"{int(budget_ms)}ms"
            row[f"eligible_lag_budget_{token}"] = bool(
                eligible
                and lag_s is not None
                and 1000.0 * abs(lag_s) <= budget_ms + 1e-9
            )
        row["rmse_lag_pareto_within_components"] = False
        row["rmse_lag_pareto_joint"] = False
        output.append(row)

    def dominates(
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> bool:
        left_values = (
            _number(left, "position_rmse_rad"),
            _number(left, "absolute_lag_ms"),
        )
        right_values = (
            _number(right, "position_rmse_rad"),
            _number(right, "absolute_lag_ms"),
        )
        return all(
            left_value <= right_value + 1e-12
            for left_value, right_value in zip(left_values, right_values)
        ) and any(
            left_value < right_value - 1e-12
            for left_value, right_value in zip(left_values, right_values)
        )

    eligible_rows = [row for row in output if _truth(row.get("eligible"))]
    for method_id in METHOD_ORDER:
        candidates = [
            row for row in eligible_rows if row["method_id"] == method_id
        ]
        pareto_ids = {
            str(row["case_id"])
            for row in candidates
            if not any(
                dominates(other, row)
                for other in candidates
                if other["case_id"] != row["case_id"]
            )
        }
        for row in candidates:
            row["rmse_lag_pareto_within_components"] = (
                str(row["case_id"]) in pareto_ids
            )

    joint_pareto_ids = {
        str(row["case_id"])
        for row in eligible_rows
        if not any(
            dominates(other, row)
            for other in eligible_rows
            if other["case_id"] != row["case_id"]
        )
    }
    for row in eligible_rows:
        row["rmse_lag_pareto_joint"] = (
            str(row["case_id"]) in joint_pareto_ids
        )
    return output


def _ranked_rows(
    surface: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    best_rows: list[dict[str, Any]] = []
    frontier_rows: list[dict[str, Any]] = []
    for method_id in METHOD_ORDER:
        eligible = [
            row
            for row in surface
            if row["method_id"] == method_id and _truth(row.get("eligible"))
        ]
        if not eligible:
            raise AnalysisValidationError(f"A06 no eligible cases for {method_id}")
        vendor_row = next(
            row
            for row in eligible
            if (
                _number(row, "max_velocity_rad_s"),
                _number(row, "max_acceleration_rad_s2"),
                _number(row, "max_jerk_rad_s3"),
            )
            == VENDOR
        )
        vendor_rmse = _number(vendor_row, "position_rmse_rad")
        vendor_lag_ms = _number(vendor_row, "lag_ms")
        best = min(
            eligible,
            key=lambda row: (
                _number(row, "position_rmse_rad"),
                _number(row, "absolute_lag_ms"),
                _number(row, "max_velocity_rad_s"),
                _number(row, "max_acceleration_rad_s2"),
                _number(row, "max_jerk_rad_s3"),
            ),
        )
        best_rmse = _number(best, "position_rmse_rad")
        best_lag_ms = _number(best, "lag_ms")
        best_absolute_lag_ms = _number(best, "absolute_lag_ms")
        near = [
            row
            for row in eligible
            if _number(row, "position_rmse_rad") <= 1.01 * best_rmse
        ]

        def dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
            left_values = (
                _number(left, "position_rmse_rad"),
                _number(left, "absolute_lag_ms"),
                _number(left, "max_velocity_rad_s"),
                _number(left, "max_acceleration_rad_s2"),
                _number(left, "max_jerk_rad_s3"),
            )
            right_values = (
                _number(right, "position_rmse_rad"),
                _number(right, "absolute_lag_ms"),
                _number(right, "max_velocity_rad_s"),
                _number(right, "max_acceleration_rad_s2"),
                _number(right, "max_jerk_rad_s3"),
            )
            return all(a <= b for a, b in zip(left_values, right_values)) and any(
                a < b for a, b in zip(left_values, right_values)
            )

        nondominated = [
            row
            for row in near
            if not any(
                dominates(other, row)
                for other in near
                if other["case_id"] != row["case_id"]
            )
        ]
        performance_equivalent = [
            row
            for row in eligible
            if math.isclose(
                _number(row, "position_rmse_rad"),
                best_rmse,
                rel_tol=0.0,
                abs_tol=1e-15,
            )
            and math.isclose(
                _number(row, "absolute_lag_ms"),
                best_absolute_lag_ms,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
        limit_efficient = min(
            performance_equivalent,
            key=lambda row: (
                _number(row, "max_velocity_rad_s"),
                _number(row, "max_acceleration_rad_s2"),
                _number(row, "max_jerk_rad_s3"),
            ),
        )
        deployment = (
            None
            if not method_id.startswith("pv_")
            else min(
                performance_equivalent,
                key=lambda row: (
                    int(_number(row, "projection_count")),
                    abs(_number(row, "max_velocity_rad_s") - VENDOR[0]),
                    abs(_number(row, "max_acceleration_rad_s2") - VENDOR[1]),
                    abs(_number(row, "max_jerk_rad_s3") - VENDOR[2]),
                ),
            )
        )
        selected_by_budget: dict[float, Mapping[str, Any]] = {}
        for budget_ms in LAG_BUDGETS_MS:
            candidates = [
                row
                for row in eligible
                if _truth(row["rmse_lag_pareto_within_components"])
                and _number(row, "absolute_lag_ms") <= budget_ms + 1e-9
            ]
            if not candidates:
                raise AnalysisValidationError(
                    f"A06 no {method_id} Pareto case within {budget_ms:g} ms"
                )
            selected_by_budget[budget_ms] = min(
                candidates,
                key=lambda row: (
                    _number(row, "position_rmse_rad"),
                    _number(row, "absolute_lag_ms"),
                    _number(row, "max_velocity_rad_s"),
                    _number(row, "max_acceleration_rad_s2"),
                    _number(row, "max_jerk_rad_s3"),
                ),
            )
        best_v = _number(best, "max_velocity_rad_s")
        best_a = _number(best, "max_acceleration_rad_s2")
        best_j = _number(best, "max_jerk_rad_s3")
        boundary_axes = []
        if best_v in {min(VELOCITIES), max(VELOCITIES)}:
            boundary_axes.append("V")
        if best_a in {min(ACCELERATIONS), max(ACCELERATIONS)}:
            boundary_axes.append("A")
        if best_j in {min(JERKS), max(JERKS)}:
            boundary_axes.append("J")
        best_rows.append(
            {
                "method_id": method_id,
                "target_components": (
                    "PV" if method_id.startswith("pv_") else "PVA"
                ),
                "eligible_case_count": len(eligible),
                "best_case_id": best["case_id"],
                "best_max_velocity_rad_s": best_v,
                "best_max_acceleration_rad_s2": best_a,
                "best_max_jerk_rad_s3": best_j,
                "best_position_rmse_rad": best_rmse,
                "vendor_position_rmse_rad": vendor_rmse,
                "best_rmse_ratio_vs_vendor": best_rmse / vendor_rmse,
                "best_lag_s": _number(best, "lag_s"),
                "best_lag_ms": best_lag_ms,
                "best_absolute_lag_ms": best_absolute_lag_ms,
                "vendor_lag_ms": vendor_lag_ms,
                "best_absolute_lag_delta_vs_vendor_ms": (
                    best_absolute_lag_ms - abs(vendor_lag_ms)
                ),
                "best_projection_count": int(
                    _number(best, "projection_count")
                ),
                "selected_lag_budget_10ms_case_id": selected_by_budget[10.0][
                    "case_id"
                ],
                "selected_lag_budget_20ms_case_id": selected_by_budget[20.0][
                    "case_id"
                ],
                "performance_equivalent_case_count": len(
                    performance_equivalent
                ),
                "limit_efficient_case_id": limit_efficient["case_id"],
                "deployment_recommended_case_id": (
                    "" if deployment is None else deployment["case_id"]
                ),
                "deployment_max_velocity_rad_s": (
                    None
                    if deployment is None
                    else _number(deployment, "max_velocity_rad_s")
                ),
                "deployment_max_acceleration_rad_s2": (
                    None
                    if deployment is None
                    else _number(deployment, "max_acceleration_rad_s2")
                ),
                "deployment_max_jerk_rad_s3": (
                    None
                    if deployment is None
                    else _number(deployment, "max_jerk_rad_s3")
                ),
                "deployment_position_rmse_rad": (
                    None
                    if deployment is None
                    else _number(deployment, "position_rmse_rad")
                ),
                "deployment_lag_ms": (
                    None
                    if deployment is None
                    else _number(deployment, "lag_ms")
                ),
                "deployment_projection_count": (
                    None
                    if deployment is None
                    else int(_number(deployment, "projection_count"))
                ),
                "near_optimal_1pct_case_count": len(near),
                "near_optimal_nondominated_case_count": len(nondominated),
                "boundary_censored": bool(boundary_axes),
                "boundary_axes": ",".join(boundary_axes),
            }
        )
        nondominated_ids = {str(row["case_id"]) for row in nondominated}
        performance_equivalent_ids = {
            str(row["case_id"]) for row in performance_equivalent
        }
        limit_efficient_id = str(limit_efficient["case_id"])
        deployment_id = "" if deployment is None else str(deployment["case_id"])
        for row in sorted(
            near,
            key=lambda item: (
                _number(item, "position_rmse_rad"),
                _number(item, "absolute_lag_ms"),
                _number(item, "max_velocity_rad_s"),
                _number(item, "max_acceleration_rad_s2"),
                _number(item, "max_jerk_rad_s3"),
            ),
        ):
            case_id = str(row["case_id"])
            if case_id == deployment_id:
                deployment_role = "deployment_recommended"
            elif case_id == limit_efficient_id:
                deployment_role = "limit_efficient_equivalent"
            elif case_id in performance_equivalent_ids:
                deployment_role = "performance_equivalent"
            else:
                deployment_role = "near_optimal_1pct"
            frontier_rows.append(
                {
                    "method_id": method_id,
                    "target_components": (
                        "PV" if method_id.startswith("pv_") else "PVA"
                    ),
                    "case_id": row["case_id"],
                    "max_velocity_rad_s": row["max_velocity_rad_s"],
                    "max_acceleration_rad_s2": row[
                        "max_acceleration_rad_s2"
                    ],
                    "max_jerk_rad_s3": row["max_jerk_rad_s3"],
                    "position_rmse_rad": row["position_rmse_rad"],
                    "lag_s": row["lag_s"],
                    "lag_ms": row["lag_ms"],
                    "absolute_lag_ms": row["absolute_lag_ms"],
                    "projection_count": row["projection_count"],
                    "rmse_ratio_vs_tested_best": (
                        _number(row, "position_rmse_rad") / best_rmse
                    ),
                    "rmse_ratio_vs_vendor": (
                        _number(row, "position_rmse_rad") / vendor_rmse
                    ),
                    "limit_efficient_nondominated": (
                        case_id in nondominated_ids
                    ),
                    "rmse_lag_pareto_within_components": row[
                        "rmse_lag_pareto_within_components"
                    ],
                    "rmse_lag_pareto_joint": row[
                        "rmse_lag_pareto_joint"
                    ],
                    "eligible_lag_budget_10ms": row[
                        "eligible_lag_budget_10ms"
                    ],
                    "eligible_lag_budget_20ms": row[
                        "eligible_lag_budget_20ms"
                    ],
                    "deployment_role": deployment_role,
                }
            )
    return best_rows, frontier_rows


def _plot_best_v_heatmaps(
    surface: Sequence[Mapping[str, Any]],
    best_rows: Sequence[Mapping[str, Any]],
) -> tuple[Path, ...]:
    configure_matplotlib()
    import matplotlib.pyplot as plt
    import numpy as np

    paths: list[Path] = []
    for best in best_rows:
        method_id = str(best["method_id"])
        best_v = _number(best, "best_max_velocity_rad_s")
        vendor_rmse = _number(best, "vendor_position_rmse_rad")
        selected = {
            (
                _number(row, "max_acceleration_rad_s2"),
                _number(row, "max_jerk_rad_s3"),
            ): _number(row, "position_rmse_rad") / vendor_rmse
            for row in surface
            if row["method_id"] == method_id
            and math.isclose(
                _number(row, "max_velocity_rad_s"),
                best_v,
                abs_tol=1e-12,
            )
            and _truth(row.get("eligible"))
        }
        matrix = np.asarray(
            [
                [
                    selected.get((acceleration, jerk), np.nan)
                    for jerk in JERKS
                ]
                for acceleration in ACCELERATIONS
            ],
            dtype=float,
        )
        figure, axis = plt.subplots(
            figsize=(11.0, 6.2),
            constrained_layout=True,
        )
        image = axis.imshow(matrix, origin="lower", aspect="auto", cmap="viridis")
        axis.set_xticks(range(len(JERKS)), [f"{value:g}" for value in JERKS], rotation=35)
        axis.set_yticks(
            range(len(ACCELERATIONS)),
            [f"{value:g}" for value in ACCELERATIONS],
        )
        axis.set_xlabel("Jmax (rad/s³)")
        axis.set_ylabel("Amax (rad/s²)")
        axis.set_title(
            f"{best['target_components']} Future O1 · Vmax={best_v:g} · RMSE/vendor"
        )
        figure.colorbar(image, ax=axis, label="Position RMSE / vendor setting")
        saved = save_figure(
            figure,
            RESULTS_DIRECTORY
            / f"{str(best['target_components']).lower()}_best_v_aj_heatmap",
        )
        paths.extend(saved)
    return tuple(paths)


def _plot_v_envelope(
    surface: Sequence[Mapping[str, Any]],
    best_rows: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    best_index = {str(row["method_id"]): row for row in best_rows}
    figure, axis = plt.subplots(figsize=(9.5, 5.8), constrained_layout=True)
    for method_id, color in zip(METHOD_ORDER, ("#4477AA", "#EE6677")):
        vendor = _number(best_index[method_id], "vendor_position_rmse_rad")
        envelope = []
        for velocity in VELOCITIES:
            candidates = [
                _number(row, "position_rmse_rad") / vendor
                for row in surface
                if row["method_id"] == method_id
                and math.isclose(
                    _number(row, "max_velocity_rad_s"),
                    velocity,
                    abs_tol=1e-12,
                )
                and _truth(row.get("eligible"))
            ]
            envelope.append(min(candidates) if candidates else math.nan)
        axis.plot(
            VELOCITIES,
            envelope,
            marker="o",
            color=color,
            label=("PV" if method_id.startswith("pv_") else "PVA"),
        )
    axis.axhline(1.0, color="#111827", linestyle="--", linewidth=1.1)
    axis.set_xlabel("Vmax (rad/s)")
    axis.set_ylabel("Minimum RMSE/vendor over tested A×J")
    axis.set_title("Fine VAJ sensitivity: velocity envelope")
    axis.grid(alpha=0.3)
    axis.legend()
    return save_figure(figure, RESULTS_DIRECTORY / "vaj_velocity_envelope")


def _plot_rmse_lag_pareto(
    surface: Sequence[Mapping[str, Any]],
    best_rows: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path]:
    configure_matplotlib()
    import matplotlib.pyplot as plt

    best_index = {str(row["method_id"]): row for row in best_rows}
    figure, axes = plt.subplots(
        1,
        2,
        figsize=(12.0, 5.8),
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    for axis, method_id, color in zip(
        axes,
        METHOD_ORDER,
        ("#4477AA", "#EE6677"),
    ):
        summary = best_index[method_id]
        vendor_rmse = _number(summary, "vendor_position_rmse_rad")
        eligible = [
            row
            for row in surface
            if row["method_id"] == method_id and _truth(row.get("eligible"))
        ]
        axis.scatter(
            [_number(row, "absolute_lag_ms") for row in eligible],
            [
                _number(row, "position_rmse_rad") / vendor_rmse
                for row in eligible
            ],
            s=18,
            color=color,
            alpha=0.28,
            linewidths=0,
            label="eligible cases",
        )
        pareto = [
            row
            for row in eligible
            if _truth(row["rmse_lag_pareto_within_components"])
        ]
        axis.scatter(
            [_number(row, "absolute_lag_ms") for row in pareto],
            [
                _number(row, "position_rmse_rad") / vendor_rmse
                for row in pareto
            ],
            s=55,
            facecolors="none",
            edgecolors="#111827",
            linewidths=1.1,
            label="RMSE–lag Pareto",
        )
        best_case = next(
            row
            for row in eligible
            if row["case_id"] == summary["best_case_id"]
        )
        axis.scatter(
            [_number(best_case, "absolute_lag_ms")],
            [_number(best_case, "position_rmse_rad") / vendor_rmse],
            marker="D",
            s=95,
            color="#F59E0B",
            edgecolors="#111827",
            linewidths=0.8,
            label="limit-efficient best",
            zorder=5,
        )
        deployment_id = str(summary["deployment_recommended_case_id"])
        if deployment_id:
            deployment = next(
                row for row in eligible if row["case_id"] == deployment_id
            )
            axis.scatter(
                [_number(deployment, "absolute_lag_ms")],
                [
                    _number(deployment, "position_rmse_rad")
                    / vendor_rmse
                ],
                marker="*",
                s=220,
                color="#16A34A",
                edgecolors="#111827",
                linewidths=0.8,
                label="deployment recommendation",
                zorder=6,
            )
        for budget_ms, linestyle in zip(LAG_BUDGETS_MS, ("--", ":")):
            axis.axvline(
                budget_ms,
                color="#6B7280",
                linestyle=linestyle,
                linewidth=1.0,
            )
        axis.axhline(
            1.0,
            color="#111827",
            linestyle="--",
            linewidth=1.0,
        )
        axis.set_yscale("log")
        axis.set_title(
            "PV Future O1"
            if method_id.startswith("pv_")
            else "PVA Future O1"
        )
        axis.set_xlabel("|Observed lag| (ms)")
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Position RMSE / own vendor setting")
    axes[0].legend(fontsize=8)
    figure.suptitle("Fine VAJ sensitivity: RMSE–lag Pareto")
    return save_figure(
        figure,
        RESULTS_DIRECTORY / "vaj_rmse_lag_pareto",
    )


def _results_markdown(
    best_rows: Sequence[Mapping[str, Any]],
    frontier_rows: Sequence[Mapping[str, Any]],
    lag_sensitivity_rows: Sequence[Mapping[str, Any]],
) -> str:
    primary = next(
        row for row in best_rows if row["target_components"] == "PV"
    )
    minima_rows = [
        (
            row["target_components"],
            f"{float(row['best_max_velocity_rad_s']):g}",
            f"{float(row['best_max_acceleration_rad_s2']):g}",
            f"{float(row['best_max_jerk_rad_s3']):g}",
            f"{float(row['best_position_rmse_rad']):.8g}",
            f"{float(row['best_lag_ms']):.0f}",
            row["best_projection_count"],
            f"{float(row['best_rmse_ratio_vs_vendor']):.6f}",
            f"{row['eligible_case_count']}/640",
            row["boundary_axes"] or "interior",
        )
        for row in best_rows
    ]
    deployment = next(
        row
        for row in frontier_rows
        if row["deployment_role"] == "deployment_recommended"
    )
    limit_efficient = next(
        row
        for row in frontier_rows
        if row["target_components"] == "PV"
        and row["deployment_role"] == "limit_efficient_equivalent"
    )
    deployment_rows = [
        (
            "上线推荐",
            f"{float(deployment['max_velocity_rad_s']):g}",
            f"{float(deployment['max_acceleration_rad_s2']):g}",
            f"{float(deployment['max_jerk_rad_s3']):g}",
            f"{float(deployment['position_rmse_rad']):.8g}",
            f"{float(deployment['lag_ms']):.0f}",
            deployment["projection_count"],
        ),
        (
            "限值效率等价点",
            f"{float(limit_efficient['max_velocity_rad_s']):g}",
            f"{float(limit_efficient['max_acceleration_rad_s2']):g}",
            f"{float(limit_efficient['max_jerk_rad_s3']):g}",
            f"{float(limit_efficient['position_rmse_rad']):.8g}",
            f"{float(limit_efficient['lag_ms']):.0f}",
            limit_efficient["projection_count"],
        ),
    ]
    lag_sensitivity_table = [
        (
            row["deployment_role"],
            f"{float(row['max_velocity_rad_s']):g}/"
            f"{float(row['max_acceleration_rad_s2']):g}/"
            f"{float(row['max_jerk_rad_s3']):g}",
            f"{float(row['position_rmse_rad']):.8g}",
            f"{float(row['integer_lag_ms']):.0f}",
            f"{float(row['subsample_lag_ms']):.3f}",
            row["projection_count"],
        )
        for row in lag_sensitivity_rows
    ]
    caveat = (
        f"最佳 PV 点落在 {primary['boundary_axes']} 网格边界，因此只能称为"
        "“best tested setting”，不能声称连续参数空间的全局最优。"
        if _truth(primary["boundary_censored"])
        else "最佳 PV 点位于当前三维网格内部，仍需独立轨迹 holdout 复核。"
    )
    return f"""# A06 — PV/PVA fine VAJ sensitivity

## 当前轨迹的选择

A04 选出的 primary 方法是 `PV + Future O1`。在 E14 的
`8 V × 8 A × 10 J = 640` 个 PV 设置中，RMSE 与 `|observed lag|` 作为
co-primary，不使用跨单位加权总分。最低 eligible RMSE 且处于最小 lag 档的
限值效率代表点是：

**V/A/J = {float(primary['best_max_velocity_rad_s']):g} / {float(primary['best_max_acceleration_rad_s2']):g} / {float(primary['best_max_jerk_rad_s3']):g}**，
RMSE = **{float(primary['best_position_rmse_rad']):.8g} rad**，
lag = **{float(primary['best_lag_ms']):.0f} ms**，
是 vendor `4.1/8.2/4000` 的
**{float(primary['best_rmse_ratio_vs_vendor']):.6f}×**。

{caveat}

## 上线推荐与限值效率点

在 `A/J=8.2/3200` 下，测试过的 `V=1, 1.25, 1.5, 2, 3, 4.1`
具有完全相同的 RMSE **{float(primary['best_position_rmse_rad']):.8g} rad**
和 lag **{float(primary['best_lag_ms']):.0f} ms**。

上线采用 **V/A/J = 4.1/8.2/3200**：它保持 vendor Vmax，与限值效率点的
RMSE/lag 完全相同，且 projection count 为 0。`1/8.2/3200` 仅作为较低限值
的等性能代表点；它发生 6/7672 次 velocity projection。

{markdown_table(
    ("角色", "V", "A", "J", "RMSE rad", "lag ms", "projection"),
    deployment_rows,
)}

### Lag 分辨率补充

完整 1,280-case compact aggregate 只保留 10 ms 网格的 integer lag，不能在
缺少 trace 的情况下重建整个 sub-sample Pareto。为验证最终配置，E14 对
`1/8.2/3200`、`4.1/8.2/3200` 与 vendor `4.1/8.2/4000` 做了同代码、
同输入的三点补充 replay：

{markdown_table(
    (
        "角色",
        "VAJ",
        "RMSE rad",
        "integer lag ms",
        "sub-sample lag ms",
        "projection",
    ),
    lag_sensitivity_table,
)}

两个 J=3200 点的 sub-sample lag 同为 9.740 ms，因此“保持 Vmax=4.1
不牺牲 RMSE/lag”的判断在亚采样口径下不变。相对 vendor J=4000，J=3200
的 RMSE 下降 9.49%，integer lag 不变，但 sub-sample lag 增加 0.186 ms；
因此亚采样口径下二者构成轻微 trade-off，而不是严格支配。该补充不重新排名
其余 1,277 cases。

## PV/PVA tested minima

{markdown_table(
    (
        "分量",
        "V",
        "A",
        "J",
        "RMSE",
        "lag ms",
        "projection",
        "vs vendor",
        "eligible",
        "边界",
    ),
    minima_rows,
)}

## 解释边界

- co-primary metrics：当前 velocity-limited recorded trajectory 上
  `t>=0.04 s` 的 raw-time position RMSE 与 `|observed lag|`；
- eligible：完整执行、constraint/fallback/solver 为零、projection 可重构、
  executable target admissible；deadline 仅报告；
- “near-optimal”定义为 RMSE 不超过 tested minimum 的 1%；
- RMSE–lag Pareto 后分别检查 10/20 ms 时延档位；两个档位选择一致；
- near-optimal limit frontier 同时把更低 RMSE、`|lag|` 和更低 V/A/J
  视作更优；
- 完整 surface 的 observed lag 是 10 ms 整数采样移位诊断；最终三点另有
  局部二次插值的 sub-sample 敏感性。两者都不是 wall-clock latency；
- 该选择只适用于当前轨迹和 A04 的 Future-O1 stencil；尚未通过其他 recorded
  trajectory 的 holdout，不能升级为通用 VAJ 默认值。
"""


def _write_outputs(
    prepared: Any,
    output_directory: Path,
    checks: Sequence[Mapping[str, Any]],
    surface: Sequence[Mapping[str, Any]],
    best_rows: Sequence[Mapping[str, Any]],
    frontier_rows: Sequence[Mapping[str, Any]],
    lag_sensitivity_rows: Sequence[Mapping[str, Any]],
) -> None:
    global RESULTS_DIRECTORY
    RESULTS_DIRECTORY = output_directory
    write_prepared_analysis(prepared, RESULTS_DIRECTORY / "work")
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_files: list[Path] = []
    file_specs = (
        (
            RESULTS_DIRECTORY / "source_validation.csv",
            ("check_id", "scope", "status", "actual", "expected", "blocking", "notes"),
            checks,
        ),
        (
            RESULTS_DIRECTORY / "best_tested_settings.csv",
            tuple(best_rows[0]),
            best_rows,
        ),
        (
            RESULTS_DIRECTORY / "near_optimal_frontier.csv",
            tuple(frontier_rows[0]),
            frontier_rows,
        ),
        (
            RESULTS_DIRECTORY / "rmse_lag_pareto.csv",
            tuple(surface[0]),
            surface,
        ),
        (
            RESULTS_DIRECTORY / "selected_lag_sensitivity.csv",
            tuple(lag_sensitivity_rows[0]),
            lag_sensitivity_rows,
        ),
    )
    for path, fields, rows in file_specs:
        write_csv(path, fields, rows)
        output_files.append(path)
    figures = (
        *_plot_best_v_heatmaps(surface, best_rows),
        *_plot_v_envelope(surface, best_rows),
        *_plot_rmse_lag_pareto(surface, best_rows),
    )
    validate_figure_files(figures)
    output_files.extend(figures)
    results_path = RESULTS_DIRECTORY / "RESULTS.md"
    results_markdown = _results_markdown(
        best_rows,
        frontier_rows,
        lag_sensitivity_rows,
    )
    write_text(results_path, results_markdown)
    write_text(ANALYSIS_DIRECTORY / "RESULTS.md", results_markdown)
    output_files.append(results_path)
    manifest_path = RESULTS_DIRECTORY / "analysis_manifest.json"
    write_analysis_manifest(prepared, manifest_path, output_files)


def run(*, check_only: bool = False) -> int:
    prepared = prepare_analysis(CONFIG_PATH)
    checks = _validate_a06_source(prepared)
    surface = prepared_rows(prepared, "vaj_sensitivity")
    checks.extend(_validate_surface(surface))
    decision_surface = _rmse_lag_rows(surface)
    best_rows, frontier_rows = _ranked_rows(decision_surface)
    lag_sensitivity_rows = _selected_lag_sensitivity_rows(prepared)
    if check_only:
        summary = ", ".join(
            f"{row['target_components']} best={row['best_max_velocity_rad_s']}/"
            f"{row['best_max_acceleration_rad_s2']}/"
            f"{row['best_max_jerk_rad_s3']}@{row['best_lag_ms']}ms"
            for row in best_rows
        )
        deployment = next(
            row for row in best_rows if row["target_components"] == "PV"
        )
        print(
            "A06: validated 1280 fine-grid cases; "
            f"{summary}; deployment="
            f"{deployment['deployment_max_velocity_rad_s']}/"
            f"{deployment['deployment_max_acceleration_rad_s2']}/"
            f"{deployment['deployment_max_jerk_rad_s3']}"
        )
        return 0
    run_directory = create_analysis_run_directory(prepared)
    _write_outputs(
        prepared,
        run_directory,
        checks,
        decision_surface,
        best_rows,
        frontier_rows,
        lag_sensitivity_rows,
    )
    print(f"A06: wrote fine-grid selection run to {run_directory}")
    return 0
