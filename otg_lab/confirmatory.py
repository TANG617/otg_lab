"""Compact, reproducible helpers for confirmatory mechanism experiments.

The regular E-series runner deliberately preserves every command and exact
profile.  Confirmatory phase maps can contain thousands of deterministic
cells, so these helpers keep the same tracking engine and metric definitions
while retaining only predeclared aggregate rows plus selected sentinel traces.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import EvaluationWindow, MetricSet, analyze_tracking
from .experiment import ExperimentResult
from .models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    TrackingRun,
    Trajectory,
)
from .runio import (
    collect_environment,
    collect_git_state,
    sha256_json,
    utc_run_stamp,
    write_json,
    write_rows_csv,
)
from .types import Measurement

CONFIRMATORY_METRIC_IDS = (
    "rest_to_rest_pulse_fraction",
    "stop_go_event_rate_hz",
    "endpoint_stop_fraction",
    "profile_peak_velocity_to_reference_median",
    "profile_velocity_ripple_to_reference_median",
    "profile_velocity_ripple_to_reference_p95",
    "profile_min_abs_velocity_to_reference_p05",
    "profile_near_zero_cycle_fraction",
    "position_rmse",
    "lag_s",
    "profile_exact_fraction",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
    "deadline_miss_rate",
)


def critical_reference_velocity(
    acceleration_rad_s2: float,
    jerk_rad_s3: float,
    dt_s: float,
) -> float:
    """One-cycle rest-to-rest average-speed boundary."""

    acceleration = float(acceleration_rad_s2)
    jerk = float(jerk_rad_s3)
    duration = float(dt_s)
    if acceleration <= 0.0 or jerk <= 0.0 or duration <= 0.0:
        raise ValueError("A, J, and dt must be positive")
    if acceleration >= jerk * duration / 4.0:
        return jerk * duration**2 / 32.0
    return acceleration * duration / 4.0 - acceleration**2 / (2.0 * jerk)


def constant_velocity_trajectory(
    velocity_rad_s: float,
    dt_s: float,
    *,
    duration_s: float = 1.0,
) -> Trajectory:
    """Construct an exact constant-velocity probe in memory."""

    intervals = int(math.ceil(float(duration_s) / float(dt_s) - 1e-12))
    if intervals < 2:
        raise ValueError("duration_s must contain at least two intervals")
    time_s = np.arange(intervals + 1, dtype=np.float64) * float(dt_s)
    velocity = float(velocity_rad_s)
    return Trajectory(
        sample_index=np.arange(time_s.size, dtype=np.int64),
        time_s=time_s,
        position_rad=velocity * time_s,
        velocity_rad_s=np.full(time_s.size, velocity, dtype=np.float64),
        acceleration_rad_s2=np.zeros(time_s.size, dtype=np.float64),
        jerk_rad_s3=np.zeros(time_s.size, dtype=np.float64),
        nominal_dt_s=float(dt_s),
    )


def tracking_config(
    *,
    dt_s: float,
    acceleration_rad_s2: float,
    jerk_rad_s3: float,
    max_velocity_rad_s: float = 4.1,
    prediction_horizon_steps: float = 1.0,
    minimum_duration_steps: float = 1.0,
) -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=float(max_velocity_rad_s),
            max_acceleration_rad_s2=float(acceleration_rad_s2),
            max_jerk_rad_s3=float(jerk_rad_s3),
        ),
        minimum_duration_s=float(minimum_duration_steps) * float(dt_s),
        prediction_horizon_s=float(prediction_horizon_steps) * float(dt_s),
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=float(dt_s),
    )


def _method(
    method_id: str,
    estimator: ComponentSpec,
    predictor: ComponentSpec,
    target_builder: ComponentSpec,
    *,
    follower: ComponentSpec | None = None,
) -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id=method_id,
        estimator=estimator,
        predictor=predictor,
        target_builder=target_builder,
        governor=ComponentSpec("none"),
        follower=follower or ComponentSpec("ruckig"),
        required=True,
    )


def p_only_method(method_id: str = "p_only_current") -> TrackingMethodSpec:
    return _method(
        method_id,
        ComponentSpec("position_only"),
        ComponentSpec("zero_order_hold"),
        ComponentSpec("p"),
    )


def scheduled_p_method(
    method_id: str = "p_only_scheduled",
    *,
    use_minimum_duration: bool = True,
) -> TrackingMethodSpec:
    return _method(
        method_id,
        ComponentSpec("position_only"),
        ComponentSpec("zero_order_hold"),
        ComponentSpec(
            "scheduled_state",
            {"components": "p", "time_source": "prediction_time"},
        ),
        follower=ComponentSpec(
            "ruckig",
            {"use_minimum_duration": bool(use_minimum_duration)},
        ),
    )


def causal_pv_method(
    method_id: str = "pv_future_o1",
) -> TrackingMethodSpec:
    return _method(
        method_id,
        ComponentSpec("position_only"),
        ComponentSpec("future_backward_fd_o1"),
        ComponentSpec(
            "scheduled_state",
            {"components": "pv", "time_source": "prediction_time"},
        ),
    )


def observer_pv_method(
    method_id: str,
    estimator: ComponentSpec,
    predictor: ComponentSpec,
) -> TrackingMethodSpec:
    return _method(
        method_id,
        estimator,
        predictor,
        ComponentSpec(
            "scheduled_state",
            {"components": "pv", "time_source": "prediction_time"},
        ),
    )


def oracle_pv_method(method_id: str = "pv_oracle") -> TrackingMethodSpec:
    return _method(
        method_id,
        ComponentSpec("position_only"),
        ComponentSpec("oracle", {"noncausal_diagnostic": True}),
        ComponentSpec(
            "scheduled_state",
            {"components": "pv", "time_source": "prediction_time"},
        ),
    )


def summarize_tracking(
    reference: Trajectory,
    tracking_run: TrackingRun,
    limits: MotionLimits,
    *,
    start_time_s: float,
    end_time_s: float,
    input_id: str,
) -> dict[str, Any]:
    """Return one flat confirmatory metric row."""

    windows = (
        EvaluationWindow("full_overlap"),
        EvaluationWindow(
            "main_evaluation",
            start_time_s=float(start_time_s),
            end_time_s=float(end_time_s),
        ),
    )
    output: dict[str, Any] = {
        "completed": tracking_run.status.completed,
        "failure_layer": tracking_run.status.failure_layer,
        "failure_reason": tracking_run.status.failure_reason,
        "valid_cycles": tracking_run.status.valid_cycles,
        "total_cycles": tracking_run.status.total_cycles,
    }
    try:
        table = analyze_tracking(
            reference,
            tracking_run,
            MetricSet(
                metric_ids=CONFIRMATORY_METRIC_IDS,
                windows=windows,
                input_id=input_id,
                limits=limits,
            ),
        )
    except (RuntimeError, ValueError) as error:
        output["analysis_failure"] = f"{type(error).__name__}: {error}"
        output.update({metric_id: None for metric_id in CONFIRMATORY_METRIC_IDS})
        return output
    full_metrics = {
        "profile_exact_fraction",
        "profile_constraint_violation_count",
        "fallback_rate",
        "solver_failure_count",
        "deadline_miss_rate",
    }
    for metric_id in CONFIRMATORY_METRIC_IDS:
        window_id = "full_overlap" if metric_id in full_metrics else "main_evaluation"
        try:
            output[metric_id] = table.value(metric_id, window_id=window_id)
        except (KeyError, ValueError):
            output[metric_id] = None
    return output


def build_measurement_schedule(
    reference: Trajectory,
    *,
    noise_sigma_rad: float = 0.0,
    quantization_step_rad: float = 0.0,
    timestamp_jitter_std_s: float = 0.0,
    delay_cycles: int = 0,
    dropout_rate: float = 0.0,
    seed: int = 0,
    source_time_s: Sequence[float] | None = None,
    source_position_rad: Sequence[float] | None = None,
) -> tuple[Measurement, ...]:
    """Deliver the latest causal irregular sample at each fixed control tick."""

    rng = np.random.default_rng(int(seed))
    delay = int(delay_cycles)
    if delay < 0:
        raise ValueError("delay_cycles must be non-negative")
    dropout = float(dropout_rate)
    if not 0.0 <= dropout < 1.0:
        raise ValueError("dropout_rate must lie in [0, 1)")
    noise = float(noise_sigma_rad)
    quantization = float(quantization_step_rad)
    jitter = float(timestamp_jitter_std_s)
    if min(noise, quantization, jitter) < 0.0:
        raise ValueError("noise, quantization, and jitter must be non-negative")

    if (source_time_s is None) != (source_position_rad is None):
        raise ValueError("source_time_s and source_position_rad must be paired")
    if source_time_s is None:
        if jitter == 0.0:
            source_times = np.array(reference.time_s[:-1], copy=True)
        else:
            count = reference.sample_count - 1
            intervals = reference.dt + rng.normal(0.0, jitter, count - 1)
            intervals = np.clip(intervals, 0.1 * reference.dt, 1.9 * reference.dt)
            source_times = np.concatenate(
                (np.asarray([0.0]), np.cumsum(intervals))
            )
            source_times = source_times[source_times <= reference.time_s[-2] + 1e-12]
        source_positions = np.interp(
            source_times,
            reference.time_s,
            reference.position_rad,
        )
    else:
        source_times = np.asarray(source_time_s, dtype=np.float64)
        source_positions = np.asarray(source_position_rad, dtype=np.float64)
        if (
            source_times.ndim != 1
            or source_positions.shape != source_times.shape
            or source_times.size < 1
            or np.any(np.diff(source_times) <= 0.0)
            or source_times[0] < -1e-12
        ):
            raise ValueError("external source samples must be finite and increasing")

    observed_positions = np.array(source_positions, copy=True)
    if noise > 0.0:
        observed_positions += rng.normal(0.0, noise, observed_positions.size)
    if quantization > 0.0:
        observed_positions = (
            np.round(observed_positions / quantization) * quantization
        )

    measurements: list[Measurement] = []
    previous_index = 0
    for cycle_index, control_time in enumerate(reference.time_s[:-1]):
        cutoff = float(control_time) - delay * reference.dt
        candidate = int(np.searchsorted(source_times, cutoff, side="right") - 1)
        candidate = max(0, min(candidate, source_times.size - 1))
        dropped = bool(
            candidate != previous_index
            and cycle_index > 0
            and rng.random() < dropout
        )
        selected = previous_index if dropped else candidate
        held = bool(cycle_index > 0 and selected == previous_index)
        measurements.append(
            Measurement(
                position=[float(observed_positions[selected])],
                state_time=float(source_times[selected]),
                available_time=float(control_time),
                metadata={
                    "source_sample_index": selected,
                    "held": held,
                    "dropped": dropped,
                    "delay_cycles": delay,
                    "seed": int(seed),
                },
            )
        )
        previous_index = selected
    return tuple(measurements)


@dataclass
class CompactRun:
    project_root: Path
    run_directory: Path
    manifest_path: Path
    manifest: dict[str, Any]
    spec_hash: str
    experiment_id: str


def start_compact_run(
    project_root: str | Path,
    *,
    experiment_id: str,
    directory_name: str,
    title: str,
    resolved_spec: Mapping[str, Any],
    runs_root: str | Path | None = None,
) -> CompactRun:
    root = Path(project_root).resolve()
    payload = {
        "schema_version": "otg.confirmatory_experiment.v1",
        "experiment_id": str(experiment_id),
        "title": str(title),
        **dict(resolved_spec),
    }
    spec_hash = sha256_json(payload)
    output_root = Path(
        runs_root
        or root / "experiments" / directory_name / "runs"
    )
    if not output_root.is_absolute():
        output_root = root / output_root
    run_directory = output_root / f"{utc_run_stamp()}__{spec_hash[:12]}"
    run_directory.mkdir(parents=True, exist_ok=False)
    manifest_path = run_directory / "manifest.json"
    manifest = {
        "schema_version": "otg.run_manifest.v1",
        "status": "running",
        "spec_hash": spec_hash,
        "resolved_experiment_spec": payload,
        "git": collect_git_state(root),
        "environment": collect_environment(),
        "inputs": {},
        "methods": {},
        "outputs": {},
    }
    write_json(manifest_path, manifest)
    return CompactRun(
        project_root=root,
        run_directory=run_directory,
        manifest_path=manifest_path,
        manifest=manifest,
        spec_hash=spec_hash,
        experiment_id=str(experiment_id),
    )


def finish_compact_run(
    run: CompactRun,
    *,
    outputs: Mapping[str, str | Path],
    failures: Sequence[Mapping[str, Any]],
    required_failure_count: int,
) -> ExperimentResult:
    failure_rows = [dict(item) for item in failures]
    if failure_rows:
        write_rows_csv(run.run_directory / "failures.csv", failure_rows)
    run.manifest["status"] = (
        "completed" if required_failure_count == 0 else "failed"
    )
    run.manifest["outputs"] = {
        key: Path(value).as_posix() for key, value in outputs.items()
    }
    run.manifest["failure_count"] = len(failure_rows)
    run.manifest["required_failure_count"] = int(required_failure_count)
    write_json(run.manifest_path, run.manifest)
    return ExperimentResult(
        experiment_id=run.experiment_id,
        run_directory=run.run_directory,
        spec_hash=run.spec_hash,
        success=required_failure_count == 0,
        failure_count=len(failure_rows),
        required_failure_count=int(required_failure_count),
    )


__all__ = [
    "CONFIRMATORY_METRIC_IDS",
    "CompactRun",
    "build_measurement_schedule",
    "causal_pv_method",
    "constant_velocity_trajectory",
    "critical_reference_velocity",
    "finish_compact_run",
    "observer_pv_method",
    "oracle_pv_method",
    "p_only_method",
    "scheduled_p_method",
    "start_compact_run",
    "summarize_tracking",
    "tracking_config",
]
