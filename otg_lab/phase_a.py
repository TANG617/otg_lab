"""Clean Phase A reproduction with sample-level and continuous audit artifacts."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from otg_runner import run_target_state_sequence
from run_target_state_ablation import (
    ACCELERATION_SWEEP,
    JERK_SWEEP,
    compute_sequence_metrics,
)
from target_state_experiment import (
    METHOD_BY_ID,
    VENDOR_LIMITS,
    MethodSpec,
    build_next_cycle_oracle,
    build_target_states,
    csv_reference,
    elementary_references,
    methods_for_reference,
)
from target_state_experiment import (
    MotionLimits as LegacyMotionLimits,
)

from .artifacts import write_csv
from .followers import RuckigFollower
from .governors import MotionLimits
from .schema import recompute_sample_feasibility, rows_to_table, validate_samples

ORACLE_METHOD = MethodSpec(
    method_id="oracle_next_cycle",
    label="PVA · next-cycle analytic oracle",
    target_components="pva",
    derivative_source="analytic_truth",
    causal=False,
    future_samples=1,
    warmup_samples=0,
    result_group="sanity_control",
)


def _resolve_phase_a_design(
    method_ids: Sequence[str] | None,
    acceleration_limits: Sequence[float] | None,
    jerk_limits: Sequence[float] | None,
) -> tuple[tuple[MethodSpec, ...], tuple[float, ...], tuple[float, ...]]:
    available = {**METHOD_BY_ID, ORACLE_METHOD.method_id: ORACLE_METHOD}
    selected_ids = tuple(available) if method_ids is None else tuple(method_ids)
    if not selected_ids or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("Phase A method IDs must be non-empty and unique")
    unknown = sorted(set(selected_ids) - set(available))
    if unknown:
        raise ValueError(f"unknown Phase A methods: {unknown}")

    acceleration = tuple(
        float(value)
        for value in (
            ACCELERATION_SWEEP if acceleration_limits is None else acceleration_limits
        )
    )
    jerk = tuple(
        float(value) for value in (JERK_SWEEP if jerk_limits is None else jerk_limits)
    )
    for label, values in (("acceleration", acceleration), ("jerk", jerk)):
        if not values or len(set(values)) != len(values):
            raise ValueError(f"Phase A {label} limits must be non-empty and unique")
        if not all(np.isfinite(value) and value > 0.0 for value in values):
            raise ValueError(f"Phase A {label} limits must be finite and positive")
    return tuple(available[method_id] for method_id in selected_ids), acceleration, jerk


def _method_rows(
    reference,
    method: MethodSpec,
    result: Mapping[str, Any],
    limits: LegacyMotionLimits,
    *,
    run_id: str,
    experiment: str,
    sweep_type: str,
    sweep_value: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconstruct every frozen solve and return canonical/audit rows."""

    from .schema import empty_sample

    dof_limits = MotionLimits.broadcast(
        1, limits.max_velocity, limits.max_acceleration, limits.max_jerk
    )
    follower = RuckigFollower(
        1,
        reference.dt,
        dof_limits,
        minimum_duration=reference.dt,
        project_targets=False,
    )
    initial = np.asarray(
        [[result["position"][0], result["velocity"][0], result["acceleration"][0]]]
    )
    follower.reset(initial)
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    scenario = (
        "baseline" if sweep_type == "none" else f"{sweep_type}_{float(sweep_value):g}"
    )
    source_kind = (
        "real_csv_legacy_fixed_grid"
        if reference.dataset == "csv"
        else "analytic_phase_a_reference"
    )
    for k in range(reference.position.size - 1):
        target_reference_k = k + (method.method_id == "oracle_next_cycle")
        target_time = float(target_reference_k) * reference.dt
        current = np.asarray(
            [[result["position"][k], result["velocity"][k], result["acceleration"][k]]]
        )
        target = np.asarray(result["target_states"][k]).reshape(1, 3)
        followed = follower.update(
            target, control_time=k * reference.dt, current_state=current
        )
        expected = np.asarray(
            [
                result["position"][k + 1],
                result["velocity"][k + 1],
                result["acceleration"][k + 1],
            ]
        )
        if not np.allclose(followed.command_state[0], expected, rtol=0.0, atol=2e-8):
            raise RuntimeError(
                "exposed legacy Phase A reconstruction cannot be relabelled as an "
                "otg.sample.v2 command: the historical ordinary-Ruckig endpoint "
                "is not one constant-jerk executable action at "
                f"{run_id}, k={k}"
            )
        sampled_jerk = (
            result["acceleration"][k + 1] - result["acceleration"][k]
        ) / reference.dt
        internal = np.asarray(
            followed.continuous_audit.get("max_internal_jerk", [np.nan])
        )
        total_compute = float(result["ruckig_compute_us"][k])
        fallback = bool(followed.fallback)
        row = empty_sample(
            run_id=run_id,
            dataset_id=f"phase-a-{reference.dataset}",
            session_id="legacy-development",
            trajectory_id=f"phase-a-{reference.dataset}",
            split="development",
            seed=0,
            joint_id="joint_0",
            k=k,
            source_time=k * reference.dt,
            arrival_time=k * reference.dt,
            control_time=k * reference.dt,
            dt_actual=reference.dt,
            dt_control=reference.dt,
            p_ref=float(reference.position[k]),
            v_ref_truth=(
                float(reference.velocity[k]) if reference.has_analytic_truth else None
            ),
            a_ref_truth=(
                float(reference.acceleration[k])
                if reference.has_analytic_truth
                else None
            ),
            j_ref_truth=(
                float(reference.jerk[k]) if reference.has_analytic_truth else None
            ),
            p_meas=float(reference.position[k]),
            estimator_id=method.derivative_source,
            predictor_id="none",
            target_mode=method.target_components,
            governor_id="scalar_projection_baseline",
            follower_id="ordinary_ruckig",
            plant_id="ideal",
            raw_target_p=float(result["raw_target_states"][k, 0]),
            raw_target_v=float(result["raw_target_states"][k, 1]),
            raw_target_a=float(result["raw_target_states"][k, 2]),
            raw_target_time=target_time,
            executable_target_p=float(result["target_states"][k, 0]),
            executable_target_v=float(result["target_states"][k, 1]),
            executable_target_a=float(result["target_states"][k, 2]),
            executable_target_time=target_time,
            command_p=float(result["position"][k + 1]),
            command_v=float(result["velocity"][k + 1]),
            command_a=float(result["acceleration"][k + 1]),
            command_jerk=float(result["new_jerk"][k + 1]),
            sampled_jerk=float(sampled_jerk),
            new_jerk=float(result["new_jerk"][k + 1]),
            internal_trajectory_jerk=(
                float(internal[0]) if np.isfinite(internal[0]) else None
            ),
            command_time=(k + 1) * reference.dt,
            plant_p=float(result["position"][k + 1]),
            plant_v=float(result["velocity"][k + 1]),
            plant_a=float(result["acceleration"][k + 1]),
            limit_max_velocity=float(limits.max_velocity),
            limit_max_acceleration=float(limits.max_acceleration),
            limit_max_jerk=float(limits.max_jerk),
            current_p=float(current[0, 0]),
            current_v=float(current[0, 1]),
            current_a=float(current[0, 2]),
            command_max_abs_velocity=float(
                np.asarray(followed.continuous_audit["max_velocity"])[0]
            ),
            command_max_abs_acceleration=float(
                np.asarray(followed.continuous_audit["max_acceleration"])[0]
            ),
            command_max_abs_jerk=(
                float(internal[0]) if np.isfinite(internal[0]) else None
            ),
            executable_target_free_trajectory_duration=(
                float(followed.requested_target_free_trajectory_duration)
                if np.isfinite(followed.requested_target_free_trajectory_duration)
                else None
            ),
            target_projected=bool(result["projection_mask"][k]),
            fallback_requested=bool(followed.fallback_requested),
            fallback_applied=bool(followed.fallback_applied),
            fallback=fallback,
            fallback_reason=followed.fallback_reason if fallback else "",
            safety_guarantee=bool(followed.safety_guarantee),
            emergency_mode=bool(followed.emergency_mode),
            solver_status=followed.solver_status,
            deadline_miss=total_compute > reference.dt * 1e6,
            state_reset=False,
            invalid_input=False,
            free_trajectory_duration=(
                float(followed.free_trajectory_duration)
                if np.isfinite(followed.free_trajectory_duration)
                else None
            ),
            follower_compute_us=total_compute,
            plant_compute_us=0.0,
            total_compute_us=total_compute,
            source_kind=source_kind,
            reference_family=reference.dataset,
            scenario_id=scenario,
            truth_available=reference.has_analytic_truth,
            measurement_available=True,
            measurement_valid=True,
        )
        recomputed = recompute_sample_feasibility(row)
        for field, value in recomputed.items():
            row[field] = value
        row["target_feasible"] = row["raw_target_point_admissible"]
        flags = []
        if row["deadline_miss"]:
            flags.append("deadline_miss")
        if fallback:
            flags.append("fallback")
        row["event_flags"] = ";".join(flags)
        rows.append(row)
        audit = followed.continuous_audit
        audits.append(
            {
                "run_id": run_id,
                "trajectory_id": f"phase-a-{reference.dataset}",
                "joint_id": "joint_0",
                "joint_index": 0,
                "dataset": reference.dataset,
                "method_id": method.method_id,
                "experiment": experiment,
                "sweep_type": sweep_type,
                "sweep_value": "none" if sweep_value is None else sweep_value,
                "k": k,
                "target_time": target_time,
                "command_time": (k + 1) * reference.dt,
                "max_velocity_limit": limits.max_velocity,
                "max_acceleration_limit": limits.max_acceleration,
                "max_jerk_limit": limits.max_jerk,
                "audit_method": audit.get("audit_method", "constant_jerk_direct"),
                "free_trajectory_duration": followed.free_trajectory_duration,
                "frozen_trajectory_duration": followed.frozen_trajectory_duration,
                "max_velocity": float(
                    np.asarray(audit.get("max_velocity", [np.nan]))[0]
                ),
                "max_acceleration": float(
                    np.asarray(audit.get("max_acceleration", [np.nan]))[0]
                ),
                "max_internal_jerk": float(internal[0]),
                "max_new_jerk": abs(float(result["new_jerk"][k + 1])),
                "max_sampled_jerk": abs(float(sampled_jerk)),
                "velocity_margin": float(
                    np.asarray(audit.get("velocity_margin", [np.nan]))[0]
                ),
                "acceleration_margin": float(
                    np.asarray(audit.get("acceleration_margin", [np.nan]))[0]
                ),
                "jerk_margin": float(np.asarray(audit.get("jerk_margin", [np.nan]))[0]),
                "velocity_max_time": float(
                    np.asarray(audit.get("velocity_max_time", [np.nan]))[0]
                ),
                "acceleration_max_time": float(
                    np.asarray(audit.get("acceleration_max_time", [np.nan]))[0]
                ),
                "jerk_max_time": float(
                    np.asarray(audit.get("jerk_max_time", [np.nan]))[0]
                ),
                "violation_count": int(
                    np.asarray(audit.get("violation_count", [0]))[0]
                ),
            }
        )
    validate_samples(rows)
    return rows, audits


def _run_sequence(reference, method, limits):
    raw_target = (
        build_next_cycle_oracle(reference)
        if method.method_id == "oracle_next_cycle"
        else build_target_states(reference, method)
    )
    result = run_target_state_sequence(
        reference.position,
        raw_target,
        reference.dt,
        **limits.as_dict(),
        minimum_duration=reference.dt,
        project_targets=True,
    )
    return result


def _regression_report(new_metrics: pd.DataFrame, legacy_path: Path) -> pd.DataFrame:
    if not legacy_path.exists():
        return pd.DataFrame(
            [{"status": "legacy_missing", "legacy_path": str(legacy_path)}]
        )
    legacy = pd.read_csv(legacy_path)
    new = new_metrics[new_metrics["experiment"] == "baseline"].copy()
    keys = ["dataset", "method_id"]
    numeric = [
        "rmse",
        "mae",
        "max_error",
        "best_lag_ms",
        "lag_aligned_rmse",
        "raw_target_feasible_rate",
        "target_projection_rate",
        "output_max_velocity",
        "output_max_acceleration",
        "output_max_new_jerk",
        "output_max_sampled_jerk",
    ]
    merged = legacy.merge(new, on=keys, suffixes=("_legacy", "_clean"))
    rows = []
    for _, item in merged.iterrows():
        differences = {
            name: abs(float(item[f"{name}_clean"]) - float(item[f"{name}_legacy"]))
            for name in numeric
            if np.isfinite(item[f"{name}_clean"])
            and np.isfinite(item[f"{name}_legacy"])
        }
        maximum = max(differences.values(), default=0.0)
        rows.append(
            {
                "dataset": item["dataset"],
                "method_id": item["method_id"],
                "rmse_legacy": item["rmse_legacy"],
                "rmse_clean": item["rmse_clean"],
                "rmse_abs_difference": differences.get("rmse", np.nan),
                "lag_legacy_ms": item["best_lag_ms_legacy"],
                "lag_clean_ms": item["best_lag_ms_clean"],
                "max_numeric_abs_difference": maximum,
                "tolerance": 1e-9,
                "status": "within_tolerance" if maximum <= 1e-9 else "investigate",
            }
        )
    return pd.DataFrame(rows)


def run_phase_a(
    output_dir: str | Path,
    *,
    plot_data_path: str | Path = "plot_data.csv",
    include_sensitivity: bool = True,
    method_ids: Sequence[str] | None = None,
    acceleration_limits: Sequence[float] | None = None,
    jerk_limits: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Execute the full clean Phase A matrix into a new versioned directory."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selected_methods, acceleration_sweep, jerk_sweep = _resolve_phase_a_design(
        method_ids,
        acceleration_limits,
        jerk_limits,
    )
    references = elementary_references()
    references["csv"] = csv_reference(plot_data_path)
    metrics: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    sample_tmp = output / "samples.parquet.tmp"
    writer = None
    audit_path = output / "constraint_audit.csv"
    audit_handle = audit_path.open("w", encoding="utf-8", newline="")
    audit_writer = None
    run_count = 0

    def emit(
        reference,
        method,
        limits,
        *,
        experiment="baseline",
        sweep_type="none",
        sweep_value=None,
    ):
        nonlocal writer, audit_writer, run_count
        suffix = (
            "base" if sweep_type == "none" else f"{sweep_type}-{float(sweep_value):g}"
        )
        run_id = (
            f"phase-a::{experiment}::{reference.dataset}::{method.method_id}::{suffix}"
        )
        try:
            result = _run_sequence(reference, method, limits)
            metric = compute_sequence_metrics(
                reference,
                method,
                result,
                limits,
                experiment=experiment,
                sweep_type=sweep_type,
                sweep_value=(float("nan") if sweep_value is None else sweep_value),
            )
            sample_rows, audit_rows = _method_rows(
                reference,
                method,
                result,
                limits,
                run_id=run_id,
                experiment=experiment,
                sweep_type=sweep_type,
                sweep_value=sweep_value,
            )
            table = rows_to_table(sample_rows)
            if writer is None:
                writer = pq.ParquetWriter(sample_tmp, table.schema, compression="zstd")
            writer.write_table(table)
            for audit in audit_rows:
                if audit_writer is None:
                    audit_writer = csv.DictWriter(audit_handle, fieldnames=list(audit))
                    audit_writer.writeheader()
                audit_writer.writerow(audit)
            metrics.append(metric)
            run_count += 1
        except Exception as error:
            failures.append(
                {
                    "run_id": run_id,
                    "trajectory_id": f"phase-a-{reference.dataset}",
                    "k": None,
                    "failure_type": type(error).__name__,
                    "reason": (
                        f"method={method.method_id};experiment={experiment};"
                        f"sweep={sweep_type}:{sweep_value};error={error}"
                    ),
                }
            )

    try:
        for reference in references.values():
            compatible_ids = {
                method.method_id for method in methods_for_reference(reference)
            }
            for method in selected_methods:
                if method.method_id not in compatible_ids:
                    continue
                emit(reference, method, VENDOR_LIMITS)
        if ORACLE_METHOD in selected_methods:
            for reference in references.values():
                if not reference.has_analytic_truth:
                    continue
                emit(
                    reference,
                    ORACLE_METHOD,
                    VENDOR_LIMITS,
                    experiment="oracle_sanity_control",
                )
        if include_sensitivity:
            for reference in references.values():
                compatible_ids = {
                    method.method_id for method in methods_for_reference(reference)
                }
                for method in selected_methods:
                    if method.method_id not in compatible_ids:
                        continue
                    for max_acceleration in acceleration_sweep:
                        emit(
                            reference,
                            method,
                            LegacyMotionLimits(
                                VENDOR_LIMITS.max_velocity,
                                max_acceleration,
                                VENDOR_LIMITS.max_jerk,
                            ),
                            experiment="limit_sensitivity",
                            sweep_type="acceleration",
                            sweep_value=max_acceleration,
                        )
                    for max_jerk in jerk_sweep:
                        emit(
                            reference,
                            method,
                            LegacyMotionLimits(
                                VENDOR_LIMITS.max_velocity,
                                VENDOR_LIMITS.max_acceleration,
                                max_jerk,
                            ),
                            experiment="limit_sensitivity",
                            sweep_type="jerk",
                            sweep_value=max_jerk,
                        )
    finally:
        if writer is not None:
            writer.close()
        audit_handle.close()
    if writer is None:
        raise RuntimeError("Phase A generated no successful sample runs")
    sample_tmp.replace(output / "samples.parquet")
    metric_frame = pd.DataFrame(metrics)
    metric_frame.fillna("none").to_csv(output / "phase_a_metrics.csv", index=False)
    write_csv(
        output / "failures.csv",
        failures,
        fieldnames=("run_id", "trajectory_id", "k", "failure_type", "reason"),
        allowed_missing_fields={"k"},
        allow_empty=True,
    )
    regression = _regression_report(
        metric_frame,
        Path("results/vendor_target_state_ablation/target_state_ablation_metrics.csv"),
    )
    regression.to_csv(output / "legacy_vs_clean_regression.csv", index=False)
    summary = {
        "successful_runs": run_count,
        "failure_count": len(failures),
        "sample_artifact": "samples.parquet",
        "constraint_audit": "constraint_audit.csv",
        "regression_status_counts": regression.get("status", pd.Series(dtype=str))
        .value_counts()
        .to_dict(),
    }
    (output / "phase_a_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


__all__ = ["run_phase_a"]
