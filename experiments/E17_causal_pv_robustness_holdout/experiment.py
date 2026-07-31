"""E17: perturbation robustness, frozen selection, and trajectory holdout."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import numpy as np

from otg_lab.confirmatory import (
    build_measurement_schedule,
    constant_velocity_trajectory,
    critical_reference_velocity,
    finish_compact_run,
    observer_pv_method,
    oracle_pv_method,
    start_compact_run,
    summarize_tracking,
    tracking_config,
)
from otg_lab.csvio import load_trajectory_csv
from otg_lab.experiment import ExperimentResult
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    Trajectory,
)
from otg_lab.runio import write_json, write_rows_csv
from otg_lab.tracking import run_tracking

EXPERIMENT_ID = "E17"
SLUG = "causal_pv_robustness_holdout"
DIRECTORY_NAME = f"{EXPERIMENT_ID}_{SLUG}"
TITLE = "Causal PV robustness and frozen holdout"
DT_S = 0.01
JERK_RAD_S3 = 4000.0
MAIN_START_S = 0.2
MAIN_END_S = 0.8
CANDIDATE_ORDER = (
    "pv_future_o1_deadband",
    "pv_backward_cv",
    "pv_alpha_beta",
    "pv_ca_kf",
    "pv_local_poly",
)


def _profile(name: str) -> dict[str, Any]:
    normalized = str(name).strip().lower()
    if normalized == "smoke":
        return {
            "q": (0.5, 2.0),
            "rho": (0.5,),
            "development_seeds": (0, 1),
            "holdout_seeds": (101, 102, 103),
            "conditions": (
                ("clean", 0.0),
                ("position_noise", 0.1),
                ("timestamp_jitter", 0.1),
                ("delay_cycles", 1.0),
                ("dropout_rate", 0.01),
            ),
            "trajectory_count": 5,
        }
    if normalized != "confirmatory":
        raise ValueError("profile must be smoke or confirmatory")
    return {
        "q": (0.5, 2.0),
        "rho": (0.5, 0.9),
        "development_seeds": tuple(range(5)),
        "holdout_seeds": tuple(range(100, 130)),
        "conditions": (
            ("clean", 0.0),
            ("position_noise", 0.01),
            ("position_noise", 0.05),
            ("position_noise", 0.1),
            ("position_noise", 0.25),
            ("quantization", 0.01),
            ("quantization", 0.05),
            ("quantization", 0.1),
            ("quantization", 0.25),
            ("timestamp_jitter", 0.05),
            ("timestamp_jitter", 0.1),
            ("timestamp_jitter", 0.2),
            ("delay_cycles", 1.0),
            ("delay_cycles", 2.0),
            ("delay_cycles", 3.0),
            ("dropout_rate", 0.01),
            ("dropout_rate", 0.05),
        ),
        "trajectory_count": 20,
    }


def _scheduled_target(components: str = "pv") -> ComponentSpec:
    return ComponentSpec(
        "scheduled_state",
        {"components": components, "time_source": "prediction_time"},
    )


def _method(
    method_id: str,
    estimator: ComponentSpec,
    predictor: ComponentSpec,
    target: ComponentSpec | None = None,
) -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id=method_id,
        estimator=estimator,
        predictor=predictor,
        target_builder=target or _scheduled_target(),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        required=True,
    )


def _methods(measurement_sigma_rad: float) -> dict[str, TrackingMethodSpec]:
    variable_time = {
        "allow_variable_dt": True,
        "timestamp_policy": "hold",
    }
    sigma = max(float(measurement_sigma_rad), 1e-9)
    methods = {
        "p_scheduled": _method(
            "p_scheduled",
            ComponentSpec("position_only", variable_time),
            ComponentSpec("zero_order_hold"),
            ComponentSpec(
                "scheduled_state",
                {"components": "p", "time_source": "prediction_time"},
            ),
        ),
        "pv_oracle": oracle_pv_method(),
        "pv_future_o1_deadband": _method(
            "pv_future_o1_deadband",
            ComponentSpec("position_only", variable_time),
            ComponentSpec("future_backward_fd_o1"),
            ComponentSpec(
                "scheduled_velocity_deadband",
                {
                    "components": "pv",
                    "time_source": "prediction_time",
                    "absolute_tolerance_rad_s": 1e-10,
                },
            ),
        ),
        "pv_backward_cv": observer_pv_method(
            "pv_backward_cv",
            ComponentSpec("backward_fd_o1", variable_time),
            ComponentSpec("constant_velocity"),
        ),
        "pv_alpha_beta": observer_pv_method(
            "pv_alpha_beta",
            ComponentSpec("alpha_beta_gamma", variable_time),
            ComponentSpec("constant_acceleration"),
        ),
        "pv_ca_kf": observer_pv_method(
            "pv_ca_kf",
            ComponentSpec(
                "ca_kf",
                {
                    **variable_time,
                    "measurement_sigma": sigma,
                    "jerk_spectral_density": 1.0,
                },
            ),
            ComponentSpec("constant_acceleration"),
        ),
        "pv_local_poly": observer_pv_method(
            "pv_local_poly",
            ComponentSpec(
                "local_poly",
                {
                    **variable_time,
                    "window": 7,
                    "degree": 3,
                    "lag_samples": 0,
                },
            ),
            ComponentSpec("constant_acceleration"),
        ),
    }
    return methods


def _condition_parameters(
    family: str,
    level: float,
    step_rad: float,
) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        "noise_sigma_rad": 0.0,
        "quantization_step_rad": 0.0,
        "timestamp_jitter_std_s": 0.0,
        "delay_cycles": 0,
        "dropout_rate": 0.0,
    }
    if family == "position_noise":
        result["noise_sigma_rad"] = level * step_rad
    elif family == "quantization":
        result["quantization_step_rad"] = level * step_rad
    elif family == "timestamp_jitter":
        result["timestamp_jitter_std_s"] = level * DT_S
    elif family == "delay_cycles":
        result["delay_cycles"] = int(level)
    elif family == "dropout_rate":
        result["dropout_rate"] = level
    elif family != "clean":
        raise ValueError(f"unknown perturbation family {family}")
    return result


def _work_envelope(family: str, level: float) -> bool:
    return bool(
        family == "clean"
        or (family in {"position_noise", "quantization"} and level <= 0.1)
        or (family == "timestamp_jitter" and level <= 0.1)
        or (family == "delay_cycles" and level <= 1.0)
        or (family == "dropout_rate" and level <= 0.01)
    )


def _run_robustness_cell(
    *,
    q_value: float,
    rho: float,
    family: str,
    level: float,
    seed: int,
    split: str,
) -> list[dict[str, Any]]:
    acceleration = q_value * JERK_RAD_S3 * DT_S / 4.0
    critical_velocity = critical_reference_velocity(
        acceleration,
        JERK_RAD_S3,
        DT_S,
    )
    velocity = rho * critical_velocity
    reference = constant_velocity_trajectory(velocity, DT_S, duration_s=1.0)
    step_rad = abs(velocity) * DT_S
    parameters = _condition_parameters(family, level, step_rad)
    measurements = build_measurement_schedule(
        reference,
        seed=seed,
        **parameters,
    )
    measurement_sigma = max(
        float(parameters["noise_sigma_rad"]),
        float(parameters["quantization_step_rad"]) / np.sqrt(12.0),
        1e-9,
    )
    config = tracking_config(
        dt_s=DT_S,
        acceleration_rad_s2=acceleration,
        jerk_rad_s3=JERK_RAD_S3,
    )
    output: list[dict[str, Any]] = []
    for method_id, method in _methods(measurement_sigma).items():
        tracking_run = run_tracking(
            reference,
            method,
            config,
            measurements=measurements,
        )
        metrics = summarize_tracking(
            reference,
            tracking_run,
            config.limits,
            start_time_s=MAIN_START_S,
            end_time_s=MAIN_END_S,
            input_id="robustness_constant_velocity",
        )
        output.append(
            {
                "split": split,
                "method_id": method_id,
                "q": q_value,
                "branch": "jerk_limited" if q_value >= 1.0 else "acceleration_limited",
                "rho": rho,
                "perturbation_family": family,
                "perturbation_level": level,
                "work_envelope": _work_envelope(family, level),
                "seed": seed,
                "step_rad": step_rad,
                **parameters,
                **metrics,
            }
        )
    return output


def _trajectory_from_velocity(
    trajectory_id: str,
    family: str,
    velocity: np.ndarray,
    time_s: np.ndarray,
) -> tuple[str, str, Trajectory]:
    position = np.zeros_like(time_s)
    position[1:] = np.cumsum(
        0.5 * (velocity[:-1] + velocity[1:]) * np.diff(time_s)
    )
    acceleration = np.gradient(velocity, time_s, edge_order=2)
    jerk = np.gradient(acceleration, time_s, edge_order=2)
    return (
        trajectory_id,
        family,
        Trajectory(
            sample_index=np.arange(time_s.size),
            time_s=time_s,
            position_rad=position,
            velocity_rad_s=velocity,
            acceleration_rad_s2=acceleration,
            jerk_rad_s3=jerk,
            nominal_dt_s=DT_S,
        ),
    )


def _holdout_trajectories(count: int) -> list[tuple[str, str, Trajectory]]:
    time_s = np.arange(301, dtype=float) * DT_S
    vcrit = critical_reference_velocity(8.2, JERK_RAD_S3, DT_S)
    trajectories: list[tuple[str, str, Trajectory]] = []
    families = ("constant", "ramp", "sine", "chirp", "reversal")
    per_family = max(1, int(np.ceil(count / len(families))))
    for family in families:
        for variant in range(per_family):
            phase = 0.37 * variant
            if family == "constant":
                velocity = np.full_like(time_s, vcrit * (0.45 + 0.22 * variant))
            elif family == "ramp":
                start = vcrit * (0.35 + 0.08 * variant)
                end = vcrit * (1.35 - 0.07 * variant)
                velocity = start + (end - start) * time_s / time_s[-1]
            elif family == "sine":
                omega = 2.0 * np.pi * (0.35 + 0.08 * variant)
                velocity = vcrit * (
                    0.85 + 0.45 * np.sin(omega * time_s + phase)
                )
            elif family == "chirp":
                phase_curve = 2.0 * np.pi * (
                    0.18 * time_s + (0.08 + 0.01 * variant) * time_s**2
                )
                velocity = vcrit * (0.82 + 0.5 * np.sin(phase_curve + phase))
            else:
                omega = 2.0 * np.pi * (0.22 + 0.04 * variant)
                velocity = vcrit * (1.05 * np.sin(omega * time_s + phase))
            trajectories.append(
                _trajectory_from_velocity(
                    f"holdout_{family}_{variant}",
                    family,
                    velocity,
                    time_s,
                )
            )
            if len(trajectories) == count:
                return trajectories
    return trajectories[:count]


def _paired_values(
    rows: list[dict[str, Any]],
    method_id: str,
) -> tuple[list[float], list[float]]:
    key_fields = (
        "q",
        "rho",
        "perturbation_family",
        "perturbation_level",
        "seed",
    )
    baselines = {
        tuple(row[field] for field in key_fields): row
        for row in rows
        if row["method_id"] == "p_scheduled"
    }
    ripple_reductions: list[float] = []
    rmse_excess_steps: list[float] = []
    for row in rows:
        if row["method_id"] != method_id or not row["completed"]:
            continue
        baseline = baselines.get(tuple(row[field] for field in key_fields))
        if baseline is None or not baseline["completed"]:
            continue
        candidate_ripple = row["profile_velocity_ripple_to_reference_median"]
        baseline_ripple = baseline["profile_velocity_ripple_to_reference_median"]
        candidate_rmse = row["position_rmse"]
        baseline_rmse = baseline["position_rmse"]
        if (
            candidate_ripple is not None
            and baseline_ripple is not None
            and float(baseline_ripple) > 0.0
        ):
            ripple_reductions.append(
                1.0 - float(candidate_ripple) / float(baseline_ripple)
            )
        step_rad = float(row["step_rad"])
        if (
            candidate_rmse is not None
            and baseline_rmse is not None
            and step_rad > 0.0
        ):
            rmse_excess_steps.append(
                (float(candidate_rmse) - float(baseline_rmse)) / step_rad
            )
    return ripple_reductions, rmse_excess_steps


def _bootstrap_bound(
    values: list[float],
    quantile: float,
    *,
    seed: int,
) -> float | None:
    if not values:
        return None
    rng = np.random.default_rng(seed)
    data = np.asarray(values, dtype=float)
    medians = np.median(
        data[rng.integers(0, data.size, size=(2000, data.size))],
        axis=1,
    )
    return float(np.quantile(medians, quantile))


def _condition_summaries(
    rows: list[dict[str, Any]],
    method_id: str,
) -> list[dict[str, Any]]:
    conditions = sorted(
        {
            (str(row["perturbation_family"]), float(row["perturbation_level"]))
            for row in rows
            if row["work_envelope"]
        }
    )
    output: list[dict[str, Any]] = []
    for family, level in conditions:
        selected = [
            row
            for row in rows
            if row["work_envelope"]
            and row["perturbation_family"] == family
            and float(row["perturbation_level"]) == level
        ]
        reductions, rmse_excess_steps = _paired_values(selected, method_id)
        condition_pass = bool(
            reductions
            and rmse_excess_steps
            and float(np.median(reductions)) >= 0.50
            and float(np.mean(np.asarray(reductions) > 0.0)) >= 0.90
            and float(np.median(rmse_excess_steps)) <= 0.10
        )
        output.append(
            {
                "perturbation_family": family,
                "perturbation_level": level,
                "pair_count": len(reductions),
                "median_ripple_reduction": (
                    None if not reductions else float(np.median(reductions))
                ),
                "minimum_ripple_reduction": (
                    None if not reductions else float(np.min(reductions))
                ),
                "improvement_fraction": (
                    None
                    if not reductions
                    else float(np.mean(np.asarray(reductions) > 0.0))
                ),
                "median_rmse_excess_steps": (
                    None
                    if not rmse_excess_steps
                    else float(np.median(rmse_excess_steps))
                ),
                "maximum_rmse_excess_steps": (
                    None
                    if not rmse_excess_steps
                    else float(np.max(rmse_excess_steps))
                ),
                "passed": condition_pass,
            }
        )
    return output


def _trajectory_comparisons(
    rows: list[dict[str, Any]],
    method_id: str,
) -> list[dict[str, Any]]:
    baselines = {
        str(row["trajectory_id"]): row
        for row in rows
        if row["method_id"] == "p_scheduled"
    }
    output: list[dict[str, Any]] = []
    for candidate in rows:
        if candidate["method_id"] != method_id:
            continue
        baseline = baselines[str(candidate["trajectory_id"])]
        completed_pair = bool(candidate["completed"] and baseline["completed"])
        baseline_ripple = baseline["profile_velocity_ripple_to_reference_median"]
        candidate_ripple = candidate["profile_velocity_ripple_to_reference_median"]
        baseline_rmse = baseline["position_rmse"]
        candidate_rmse = candidate["position_rmse"]
        ripple_reduction = None
        if (
            completed_pair
            and baseline_ripple is not None
            and candidate_ripple is not None
            and float(baseline_ripple) > 0.0
        ):
            ripple_reduction = 1.0 - float(candidate_ripple) / float(
                baseline_ripple
            )
        rmse_excess_rad = None
        if completed_pair and baseline_rmse is not None and candidate_rmse is not None:
            rmse_excess_rad = float(candidate_rmse) - float(baseline_rmse)
        guardrails = bool(
            completed_pair
            and (candidate["profile_constraint_violation_count"] or 0) == 0
            and (candidate["fallback_rate"] or 0) == 0
            and (candidate["solver_failure_count"] or 0) == 0
        )
        passed = bool(
            guardrails
            and ripple_reduction is not None
            and ripple_reduction >= 0.50
            and rmse_excess_rad is not None
            and rmse_excess_rad <= 1e-9
        )
        output.append(
            {
                "trajectory_id": candidate["trajectory_id"],
                "trajectory_family": candidate["trajectory_family"],
                "method_id": method_id,
                "completed_pair": completed_pair,
                "baseline_ripple": baseline_ripple,
                "candidate_ripple": candidate_ripple,
                "ripple_reduction": ripple_reduction,
                "baseline_position_rmse_rad": baseline_rmse,
                "candidate_position_rmse_rad": candidate_rmse,
                "rmse_excess_rad": rmse_excess_rad,
                "guardrails": guardrails,
                "passed": passed,
            }
        )
    return output


def _select_primary(development_rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    scorecard: list[dict[str, Any]] = []
    for method_id in CANDIDATE_ORDER:
        method_rows = [
            row
            for row in development_rows
            if row["method_id"] == method_id and row["work_envelope"]
        ]
        reductions, rmse_excess_steps = _paired_values(
            [row for row in development_rows if row["work_envelope"]],
            method_id,
        )
        complete = bool(method_rows) and all(row["completed"] for row in method_rows)
        guardrails = complete and all(
            (row["profile_constraint_violation_count"] or 0) == 0
            and (row["fallback_rate"] or 0) == 0
            and (row["solver_failure_count"] or 0) == 0
            for row in method_rows
        )
        median_ripple = (
            None if not reductions else 1.0 - float(np.median(reductions))
        )
        median_rmse_excess_steps = (
            None
            if not rmse_excess_steps
            else float(np.median(rmse_excess_steps))
        )
        eligible = bool(
            guardrails
            and median_ripple is not None
            and median_rmse_excess_steps is not None
            and median_rmse_excess_steps <= 0.10
        )
        scorecard.append(
            {
                "method_id": method_id,
                "complete": complete,
                "guardrails": guardrails,
                "median_ripple_ratio": median_ripple,
                "median_rmse_excess_steps": median_rmse_excess_steps,
                "eligible": eligible,
                "method_order": CANDIDATE_ORDER.index(method_id),
            }
        )
    eligible_rows = [row for row in scorecard if row["eligible"]]
    ranked = eligible_rows or [
        row
        for row in scorecard
        if row["complete"] and row["median_ripple_ratio"] is not None
    ]
    if not ranked:
        return CANDIDATE_ORDER[0], scorecard
    selected = min(
        ranked,
        key=lambda row: (
            float(row["median_ripple_ratio"]),
            int(row["method_order"]),
        ),
    )
    return str(selected["method_id"]), scorecard


def _run_holdouts(
    selected_method_id: str,
    count: int,
) -> list[dict[str, Any]]:
    config = RunConfig(
        limits=MotionLimits(4.1, 8.2, JERK_RAD_S3),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )
    methods = _methods(1e-9)
    selected_methods = {
        method_id: methods[method_id]
        for method_id in ("p_scheduled", "pv_oracle", selected_method_id)
    }
    rows: list[dict[str, Any]] = []
    for trajectory_id, family, reference in _holdout_trajectories(count):
        for method_id, method in selected_methods.items():
            tracking_run = run_tracking(reference, method, config)
            metrics = summarize_tracking(
                reference,
                tracking_run,
                config.limits,
                start_time_s=0.2,
                end_time_s=2.8,
                input_id=trajectory_id,
            )
            rows.append(
                {
                    "trajectory_id": trajectory_id,
                    "trajectory_family": family,
                    "method_id": method_id,
                    **metrics,
                }
            )
    return rows


def _raw_recorded_samples(path: Path) -> tuple[np.ndarray, np.ndarray]:
    times: list[float] = []
    positions: list[float] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            times.append(float(row["elapsed time"]))
            positions.append(float(row["value"]))
    time_array = np.asarray(times, dtype=float)
    time_array -= time_array[0]
    return time_array, np.asarray(positions, dtype=float)


def _run_recorded_timestamp_replay(
    root: Path,
    selected_method_id: str,
) -> list[dict[str, Any]]:
    canonical = load_trajectory_csv(
        root / "data/trajectories/recorded_tasks_simplified_with_velocity_limit.csv",
        require_metadata=True,
    )
    raw_times, raw_positions = _raw_recorded_samples(
        root / "data/raw/recorded_tasks/simplified_with_velocity_limit.csv"
    )
    measurements = build_measurement_schedule(
        canonical,
        source_time_s=raw_times,
        source_position_rad=raw_positions,
    )
    config = RunConfig(
        limits=MotionLimits(4.1, 8.2, JERK_RAD_S3),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )
    methods = _methods(1e-6)
    rows: list[dict[str, Any]] = []
    for method_id in ("p_scheduled", "pv_future_o1_deadband", selected_method_id):
        method = methods[method_id]
        tracking_run = run_tracking(
            canonical,
            method,
            config,
            measurements=measurements,
        )
        metrics = summarize_tracking(
            canonical,
            tracking_run,
            config.limits,
            start_time_s=0.04,
            end_time_s=float(canonical.time_s[-1]),
            input_id="recorded_timestamp_replay",
        )
        rows.append(
            {
                "method_id": method_id,
                "source_sample_count": raw_times.size,
                "source_dt_min_s": float(np.min(np.diff(raw_times))),
                "source_dt_max_s": float(np.max(np.diff(raw_times))),
                "held_control_cycle_fraction": float(
                    np.mean([item.metadata["held"] for item in measurements])
                ),
                **metrics,
            }
        )
    return rows


def _write_figures(run_directory: Path, rows: list[dict[str, Any]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    selected = [row for row in rows if row["split"] == "holdout" and row["work_envelope"]]
    methods = [method for method in CANDIDATE_ORDER if any(row["method_id"] == method for row in selected)]
    medians = []
    for method in methods:
        reductions, _ = _paired_values(selected, method)
        medians.append(float(np.median(reductions)) if reductions else np.nan)
    figure, axis = plt.subplots(figsize=(8.8, 4.8), constrained_layout=True)
    axis.bar(np.arange(len(methods)), medians, color="#4477AA")
    axis.set_xticks(np.arange(len(methods)), methods, rotation=35, ha="right")
    axis.set_ylabel("median normalized-ripple reduction vs P")
    axis.axhline(0.8, color="#D55E00", linestyle="--", linewidth=1.2)
    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    figure.savefig(figures / "robustness_method_comparison.png", dpi=200)
    figure.savefig(figures / "robustness_method_comparison.svg")
    plt.close(figure)


def run_confirmatory(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
    profile: str | None = None,
) -> ExperimentResult:
    root = Path(project_root).resolve()
    profile_name = profile or os.environ.get(
        "OTG_CONFIRMATORY_PROFILE", "confirmatory"
    )
    declared = _profile(profile_name)
    run = start_compact_run(
        root,
        experiment_id=EXPERIMENT_ID,
        directory_name=DIRECTORY_NAME,
        title=TITLE,
        runs_root=runs_root,
        resolved_spec={
            "profile": profile_name,
            "matrix": declared,
            "candidate_order": CANDIDATE_ORDER,
            "selection": "minimum median ripple subject to complete guardrails and median RMSE excess <=0.10 step",
            "holdout_condition_rule": (
                "each work-envelope condition has median ripple reduction >=0.50, "
                "improvement fraction >=0.90, and median RMSE excess <=0.10 step"
            ),
            "trajectory_holdout_rule": (
                "each trajectory has ripple reduction >=0.50, no execution "
                "guardrail failure, and RMSE excess <=1e-9 rad"
            ),
        },
    )
    development_rows: list[dict[str, Any]] = []
    for q_value in declared["q"]:
        for rho in declared["rho"]:
            for family, level in declared["conditions"]:
                for seed in declared["development_seeds"]:
                    development_rows.extend(
                        _run_robustness_cell(
                            q_value=float(q_value),
                            rho=float(rho),
                            family=str(family),
                            level=float(level),
                            seed=int(seed),
                            split="development",
                        )
                    )
    selected_method_id, scorecard = _select_primary(development_rows)

    holdout_rows: list[dict[str, Any]] = []
    for q_value in declared["q"]:
        for rho in declared["rho"]:
            for family, level in declared["conditions"]:
                for seed in declared["holdout_seeds"]:
                    holdout_rows.extend(
                        _run_robustness_cell(
                            q_value=float(q_value),
                            rho=float(rho),
                            family=str(family),
                            level=float(level),
                            seed=int(seed),
                            split="holdout",
                        )
                    )
    robustness_rows = development_rows + holdout_rows
    work_rows = [row for row in holdout_rows if row["work_envelope"]]
    reductions, rmse_excess_steps = _paired_values(work_rows, selected_method_id)
    ripple_ci_lower = _bootstrap_bound(reductions, 0.025, seed=1717)
    rmse_ci_upper = _bootstrap_bound(rmse_excess_steps, 0.975, seed=1718)
    improvement_fraction = (
        0.0 if not reductions else float(np.mean(np.asarray(reductions) > 0.0))
    )
    condition_summaries = _condition_summaries(work_rows, selected_method_id)
    condition_pass = bool(condition_summaries) and all(
        row["passed"] for row in condition_summaries
    )
    selected_rows = [
        row
        for row in work_rows
        if row["method_id"] == selected_method_id
    ]
    guardrail_pass = bool(selected_rows) and all(
        row["completed"]
        and (row["profile_constraint_violation_count"] or 0) == 0
        and (row["fallback_rate"] or 0) == 0
        and (row["solver_failure_count"] or 0) == 0
        for row in selected_rows
    )
    trajectory_rows = _run_holdouts(
        selected_method_id,
        int(declared["trajectory_count"]),
    )
    recorded_rows = _run_recorded_timestamp_replay(root, selected_method_id)
    trajectory_selected = [
        row for row in trajectory_rows if row["method_id"] == selected_method_id
    ]
    trajectory_comparisons = _trajectory_comparisons(
        trajectory_rows, selected_method_id
    )
    trajectory_pass = bool(trajectory_comparisons) and all(
        row["passed"] for row in trajectory_comparisons
    )
    acceptance = {
        "profile": profile_name,
        "selected_method_id": selected_method_id,
        "robustness_development_row_count": len(development_rows),
        "robustness_holdout_row_count": len(holdout_rows),
        "work_envelope_pair_count": len(reductions),
        "median_ripple_reduction": (
            None if not reductions else float(np.median(reductions))
        ),
        "ripple_reduction_bootstrap_ci_lower": ripple_ci_lower,
        "improvement_fraction": improvement_fraction,
        "median_rmse_excess_steps": (
            None
            if not rmse_excess_steps
            else float(np.median(rmse_excess_steps))
        ),
        "rmse_excess_steps_bootstrap_ci_upper": rmse_ci_upper,
        "guardrail_pass": guardrail_pass,
        "holdout_condition_count": len(condition_summaries),
        "holdout_condition_pass": condition_pass,
        "worst_condition_median_ripple_reduction": min(
            float(row["median_ripple_reduction"])
            for row in condition_summaries
        ),
        "worst_condition_improvement_fraction": min(
            float(row["improvement_fraction"])
            for row in condition_summaries
        ),
        "worst_condition_median_rmse_excess_steps": max(
            float(row["median_rmse_excess_steps"])
            for row in condition_summaries
        ),
        "trajectory_holdout_count": int(declared["trajectory_count"]),
        "trajectory_holdout_pass": trajectory_pass,
        "trajectory_worst_ripple_reduction": min(
            float(row["ripple_reduction"]) for row in trajectory_comparisons
        ),
        "trajectory_max_rmse_excess_rad": max(
            float(row["rmse_excess_rad"]) for row in trajectory_comparisons
        ),
        "recorded_timestamp_replay_is_independent_holdout": False,
        "accepted": bool(
            reductions
            and np.median(reductions) >= 0.80
            and ripple_ci_lower is not None
            and ripple_ci_lower >= 0.50
            and improvement_fraction >= 0.90
            and rmse_ci_upper is not None
            and rmse_ci_upper <= 0.10
            and guardrail_pass
            and condition_pass
            and trajectory_pass
        ),
    }
    write_rows_csv(run.run_directory / "selection_scorecard.csv", scorecard)
    write_rows_csv(run.run_directory / "robustness_cells.csv", robustness_rows)
    write_rows_csv(
        run.run_directory / "holdout_condition_summary.csv",
        condition_summaries,
    )
    write_rows_csv(run.run_directory / "trajectory_holdout.csv", trajectory_rows)
    write_rows_csv(
        run.run_directory / "trajectory_comparison.csv",
        trajectory_comparisons,
    )
    write_rows_csv(run.run_directory / "recorded_timestamp_replay.csv", recorded_rows)
    write_json(run.run_directory / "acceptance.json", acceptance)
    (run.run_directory / "acceptance_summary.md").write_text(
        "# E17 acceptance\n\n"
        f"- Profile: `{profile_name}`\n"
        f"- Frozen method: `{selected_method_id}`\n"
        f"- Median ripple reduction: `{acceptance['median_ripple_reduction']}`\n"
        f"- Ripple 95% CI lower: `{ripple_ci_lower}`\n"
        f"- Improved task-seed fraction: `{improvement_fraction}`\n"
        f"- RMSE excess/step 95% CI upper: `{rmse_ci_upper}`\n"
        f"- Guardrails: **{guardrail_pass}**\n"
        f"- Every work-envelope condition passes: **{condition_pass}**\n"
        f"- Worst condition median ripple reduction: "
        f"`{acceptance['worst_condition_median_ripple_reduction']}`\n"
        f"- Synthetic trajectory holdout: **{trajectory_pass}**\n"
        f"- Accepted: **{acceptance['accepted']}**\n\n"
        "The recorded timestamp replay is a diagnostic on the existing task, "
        "not an independent task-level holdout.\n",
        encoding="utf-8",
    )
    if create_figures:
        _write_figures(run.run_directory, robustness_rows)
    execution_failures = [
        row
        for row in robustness_rows
        if row["method_id"] == selected_method_id
        and row["work_envelope"]
        and not row["completed"]
    ]
    execution_failures.extend(
        row for row in trajectory_selected if not row["completed"]
    )
    return finish_compact_run(
        run,
        outputs={
            "selection_scorecard": "selection_scorecard.csv",
            "robustness_cells": "robustness_cells.csv",
            "holdout_condition_summary": "holdout_condition_summary.csv",
            "trajectory_holdout": "trajectory_holdout.csv",
            "trajectory_comparison": "trajectory_comparison.csv",
            "recorded_timestamp_replay": "recorded_timestamp_replay.csv",
            "acceptance": "acceptance.json",
            "summary": "acceptance_summary.md",
        },
        failures=execution_failures,
        required_failure_count=len(execution_failures),
    )


if __name__ == "__main__":
    result = run_confirmatory(project_root=Path(__file__).resolve().parents[2])
    print(result.run_directory)
