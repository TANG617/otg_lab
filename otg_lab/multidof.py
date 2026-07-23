"""Synchronized multi-DoF analytic reference generation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .schema import empty_sample, validate_samples

PATTERNS = (
    "in_phase",
    "different_frequency",
    "staggered_reversal",
    "one_axis_near_limit",
    "correlated",
    "uncorrelated",
)


@dataclass(frozen=True)
class MultiDOFTruth:
    time: np.ndarray
    position: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray
    jerk: np.ndarray
    pattern: str
    seed: int
    internal_dt: float

    @property
    def dof(self) -> int:
        return int(self.position.shape[1])


@dataclass(frozen=True)
class MultiDOFTrackingDiagnostics:
    """Long-form inputs and summaries for synchronized multi-axis tracking.

    ``aligned_samples`` preserves every joint's output and the reference
    interpolated at that output's physical time.  ``per_cycle`` then reports
    the Euclidean geometric path error and, when reference velocity is
    available, the residual perpendicular to the local path tangent.  The
    latter is a synchronization/cross-track diagnostic after removing the
    best common local time shift; it is deliberately unavailable at stops.
    """

    aligned_samples: tuple[dict[str, Any], ...]
    per_joint: tuple[dict[str, Any], ...]
    per_cycle: tuple[dict[str, Any], ...]
    summary: tuple[dict[str, Any], ...]


_DIAGNOSTIC_ID_FIELDS = (
    "run_id",
    "method_id",
    "dataset_id",
    "trajectory_id",
    "scenario_id",
)


def _diagnostic_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple("" if row.get(name) is None else str(row[name]) for name in _DIAGNOSTIC_ID_FIELDS)


def _identity_record(identity: tuple[str, ...]) -> dict[str, str]:
    return dict(zip(_DIAGNOSTIC_ID_FIELDS, identity))


def _finite_scalar(row: Mapping[str, Any], field: str) -> float:
    value = row.get(field)
    if value is None or not np.isfinite(float(value)):
        raise ValueError(f"multi-DoF diagnostic requires finite {field}")
    return float(value)


def _distribution_summary(values: Sequence[float], prefix: str) -> dict[str, float]:
    data = np.asarray(values, dtype=float)
    if data.size == 0 or not np.all(np.isfinite(data)):
        raise ValueError(f"cannot summarize empty/non-finite {prefix} values")
    return {
        f"{prefix}_mean": float(np.mean(data)),
        f"{prefix}_rms": float(np.sqrt(np.mean(data**2))),
        f"{prefix}_p95": float(np.quantile(data, 0.95)),
        f"{prefix}_max": float(np.max(data)),
    }


def compute_multidof_tracking_diagnostics(
    rows: Sequence[Mapping[str, Any]],
    *,
    output_field: str = "command_p",
    output_time_field: str = "command_time",
    reference_field: str = "p_ref",
    reference_time_field: str = "control_time",
    reference_velocity_field: str = "v_ref_truth",
    tangent_speed_epsilon: float = 1e-9,
) -> MultiDOFTrackingDiagnostics:
    """Compute time-aligned per-joint, synchronization, and path diagnostics.

    The function is pure: input mappings are never mutated.  It accepts one or
    many run/method/trajectory groups and keeps those identities on every
    output record.  Outputs outside the observed reference-time domain (most
    commonly the final ``target[k] -> output[k+1]`` command) are excluded
    rather than extrapolated.

    Synchronization is quantified in two independent ways:

    * ``command_time_spread_s`` directly audits whether all axes share one
      output time at a control cycle;
    * ``synchronization_cross_track_error`` removes the least-squares common
      local time shift along the reference tangent and measures the remaining
      n-dimensional error.  It is null for one axis, unavailable derivative
      truth, or a near-zero tangent.
    """

    if not rows:
        raise ValueError("multi-DoF diagnostics require non-empty rows")
    if not np.isfinite(tangent_speed_epsilon) or tangent_speed_epsilon <= 0.0:
        raise ValueError("tangent_speed_epsilon must be finite and positive")

    grouped: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        for field in (
            "joint_id",
            "k",
            output_field,
            output_time_field,
            reference_field,
            reference_time_field,
        ):
            if field not in row:
                raise ValueError(f"multi-DoF diagnostic row is missing {field}")
        grouped[_diagnostic_identity(row)].append(row)

    aligned_records: list[dict[str, Any]] = []
    joint_records: list[dict[str, Any]] = []
    cycle_records: list[dict[str, Any]] = []
    summary_records: list[dict[str, Any]] = []

    for identity, group in sorted(grouped.items()):
        identity_record = _identity_record(identity)
        by_joint: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in group:
            by_joint[str(row["joint_id"])].append(row)
        joint_ids = tuple(sorted(by_joint))
        expected_dof = len(joint_ids)
        aligned_by_cycle: dict[int, list[dict[str, Any]]] = defaultdict(list)

        for joint_id in joint_ids:
            joint_rows = sorted(
                by_joint[joint_id],
                key=lambda row: (_finite_scalar(row, reference_time_field), int(row["k"])),
            )
            reference_times = np.asarray(
                [_finite_scalar(row, reference_time_field) for row in joint_rows]
            )
            if reference_times.size < 2 or np.any(np.diff(reference_times) <= 0.0):
                raise ValueError(
                    f"{identity!r}/{joint_id}: reference times must strictly increase"
                )
            reference_positions = np.asarray(
                [_finite_scalar(row, reference_field) for row in joint_rows]
            )
            velocity_available = all(
                row.get(reference_velocity_field) is not None
                and np.isfinite(float(row[reference_velocity_field]))
                for row in joint_rows
            )
            reference_velocities = (
                np.asarray(
                    [float(row[reference_velocity_field]) for row in joint_rows]
                )
                if velocity_available
                else None
            )

            joint_aligned: list[dict[str, Any]] = []
            for row in sorted(joint_rows, key=lambda item: int(item["k"])):
                if row.get(output_field) is None or row.get(output_time_field) is None:
                    continue
                output_time = _finite_scalar(row, output_time_field)
                if (
                    output_time < reference_times[0] - 1e-12
                    or output_time > reference_times[-1] + 1e-12
                ):
                    continue
                output_position = _finite_scalar(row, output_field)
                reference_position = float(
                    np.interp(output_time, reference_times, reference_positions)
                )
                reference_velocity = (
                    None
                    if reference_velocities is None
                    else float(
                        np.interp(output_time, reference_times, reference_velocities)
                    )
                )
                record = {
                    **identity_record,
                    "joint_id": joint_id,
                    "k": int(row["k"]),
                    "output_field": output_field,
                    "output_time": output_time,
                    "reference_position": reference_position,
                    "reference_velocity": reference_velocity,
                    "output_position": output_position,
                    "tracking_error": output_position - reference_position,
                }
                joint_aligned.append(record)
                aligned_records.append(record)
                aligned_by_cycle[int(row["k"])].append(record)

            if not joint_aligned:
                raise ValueError(f"{identity!r}/{joint_id}: no output overlaps reference time")
            errors = np.asarray([record["tracking_error"] for record in joint_aligned])
            times = np.asarray([record["output_time"] for record in joint_aligned])
            joint_records.append(
                {
                    **identity_record,
                    "joint_id": joint_id,
                    "output_field": output_field,
                    "sample_count": int(errors.size),
                    "position_rmse": float(np.sqrt(np.mean(errors**2))),
                    "position_mae": float(np.mean(np.abs(errors))),
                    "position_max_abs_error": float(np.max(np.abs(errors))),
                    "position_iae": float(np.trapezoid(np.abs(errors), x=times)),
                }
            )

        group_cycles: list[dict[str, Any]] = []
        for k, cycle in sorted(aligned_by_cycle.items()):
            if len(cycle) != expected_dof or {row["joint_id"] for row in cycle} != set(
                joint_ids
            ):
                raise ValueError(
                    f"{identity!r}, k={k}: incomplete joint set in aligned output"
                )
            ordered = sorted(cycle, key=lambda row: row["joint_id"])
            errors = np.asarray([row["tracking_error"] for row in ordered], dtype=float)
            output_times = np.asarray([row["output_time"] for row in ordered], dtype=float)
            geometric_error = float(np.linalg.norm(errors))
            velocities = [row["reference_velocity"] for row in ordered]
            phase_error: float | None = None
            synchronization_error: float | None = None
            if expected_dof > 1 and all(value is not None for value in velocities):
                tangent = np.asarray(velocities, dtype=float)
                tangent_norm_squared = float(np.dot(tangent, tangent))
                if tangent_norm_squared > tangent_speed_epsilon**2:
                    phase_error = float(np.dot(errors, tangent) / tangent_norm_squared)
                    cross_track = errors - phase_error * tangent
                    synchronization_error = float(
                        np.linalg.norm(cross_track) / np.sqrt(expected_dof)
                    )
            cycle_record = {
                **identity_record,
                "k": int(k),
                "dof": expected_dof,
                "output_field": output_field,
                "output_time": float(np.mean(output_times)),
                "command_time_spread_s": float(np.ptp(output_times)),
                "geometric_path_error": geometric_error,
                "geometric_path_rmse_per_dof": geometric_error
                / float(np.sqrt(expected_dof)),
                "common_phase_error_s": phase_error,
                "synchronization_cross_track_error": synchronization_error,
                "max_joint_abs_error": float(np.max(np.abs(errors))),
            }
            cycle_records.append(cycle_record)
            group_cycles.append(cycle_record)

        if not group_cycles:
            raise ValueError(f"{identity!r}: no complete aligned cycles")
        geometric = [row["geometric_path_error"] for row in group_cycles]
        synchronization = [
            float(row["synchronization_cross_track_error"])
            for row in group_cycles
            if row["synchronization_cross_track_error"] is not None
        ]
        summary: dict[str, Any] = {
            **identity_record,
            "output_field": output_field,
            "dof": expected_dof,
            "joint_count": expected_dof,
            "cycle_count": len(group_cycles),
            "aligned_sample_count": len(group_cycles) * expected_dof,
            "command_time_spread_max_s": float(
                max(row["command_time_spread_s"] for row in group_cycles)
            ),
            **_distribution_summary(geometric, "geometric_path_error"),
            "synchronization_available_cycle_count": len(synchronization),
        }
        if synchronization:
            summary.update(
                _distribution_summary(synchronization, "synchronization_cross_track_error")
            )
        else:
            summary.update(
                {
                    "synchronization_cross_track_error_mean": None,
                    "synchronization_cross_track_error_rms": None,
                    "synchronization_cross_track_error_p95": None,
                    "synchronization_cross_track_error_max": None,
                }
            )
        summary_records.append(summary)

    return MultiDOFTrackingDiagnostics(
        aligned_samples=tuple(aligned_records),
        per_joint=tuple(joint_records),
        per_cycle=tuple(cycle_records),
        summary=tuple(summary_records),
    )


def generate_multidof_truth(
    dof: int,
    pattern: str,
    *,
    seed: int = 20260721,
    duration: float = 4.0,
    internal_dt: float = 0.001,
    max_velocity: float = 4.1,
    max_acceleration: float = 8.2,
    max_jerk: float = 4000.0,
) -> MultiDOFTruth:
    if dof < 1 or pattern not in PATTERNS:
        raise ValueError("invalid dof or pattern")
    if internal_dt <= 0.0 or internal_dt > 0.001 + 1e-15:
        raise ValueError("internal_dt must provide at least 1 kHz truth")
    time = np.arange(0.0, duration + internal_dt * 0.5, internal_dt)
    rng = np.random.default_rng(seed)
    axis = np.arange(dof, dtype=float)

    if pattern == "in_phase":
        frequencies = np.full(dof, 0.45)
        phases = np.zeros(dof)
        amplitudes = np.linspace(0.15, 0.35, dof)
    elif pattern == "different_frequency":
        frequencies = np.linspace(0.2, 0.8, dof)
        phases = np.zeros(dof)
        amplitudes = np.full(dof, 0.25)
    elif pattern == "staggered_reversal":
        frequencies = np.full(dof, 0.5)
        phases = 2.0 * np.pi * axis / max(dof, 1)
        amplitudes = np.full(dof, 0.3)
    elif pattern == "one_axis_near_limit":
        frequencies = np.full(dof, 0.35)
        frequencies[0] = 0.7
        phases = np.linspace(0.0, np.pi / 3.0, dof)
        amplitudes = np.full(dof, 0.12)
        omega = 2.0 * np.pi * frequencies[0]
        amplitudes[0] = min(
            0.92 * max_velocity / omega,
            0.92 * max_acceleration / omega**2,
            0.92 * max_jerk / omega**3,
        )
    elif pattern == "correlated":
        frequencies = np.full(dof, 0.4)
        phases = np.linspace(0.0, 0.3, dof)
        amplitudes = np.linspace(0.18, 0.32, dof)
    else:
        frequencies = rng.uniform(0.2, 0.8, dof)
        phases = rng.uniform(-np.pi, np.pi, dof)
        amplitudes = rng.uniform(0.1, 0.35, dof)

    omega = 2.0 * np.pi * frequencies
    argument = time[:, None] * omega[None, :] + phases[None, :]
    position = amplitudes[None, :] * np.sin(argument)
    velocity = amplitudes[None, :] * omega[None, :] * np.cos(argument)
    acceleration = -amplitudes[None, :] * omega[None, :] ** 2 * np.sin(argument)
    jerk = -amplitudes[None, :] * omega[None, :] ** 3 * np.cos(argument)
    if pattern == "correlated":
        second_frequency = 0.17
        second_omega = 2.0 * np.pi * second_frequency
        second_amplitude = 0.06
        position += second_amplitude * np.sin(second_omega * time)[:, None]
        velocity += (
            second_amplitude * second_omega * np.cos(second_omega * time)[:, None]
        )
        acceleration -= (
            second_amplitude * second_omega**2 * np.sin(second_omega * time)[:, None]
        )
        jerk -= (
            second_amplitude * second_omega**3 * np.cos(second_omega * time)[:, None]
        )

    ratios = np.asarray(
        [
            np.max(np.abs(velocity)) / max_velocity,
            np.max(np.abs(acceleration)) / max_acceleration,
            np.max(np.abs(jerk)) / max_jerk,
        ]
    )
    scale = min(1.0, 0.95 / max(float(np.max(ratios)), 1e-12))
    position *= scale
    velocity *= scale
    acceleration *= scale
    jerk *= scale
    if (
        np.max(np.abs(velocity)) > max_velocity * (1.0 + 1e-12)
        or np.max(np.abs(acceleration)) > max_acceleration * (1.0 + 1e-12)
        or np.max(np.abs(jerk)) > max_jerk * (1.0 + 1e-12)
    ):
        raise RuntimeError("multi-DoF truth scaling failed")
    return MultiDOFTruth(
        time,
        position,
        velocity,
        acceleration,
        jerk,
        pattern,
        int(seed),
        float(internal_dt),
    )


def multidof_to_rows(
    truth: MultiDOFTruth,
    *,
    sample_rate_hz: float = 100.0,
    run_id: str = "multidof-generation",
    split: str = "test",
) -> list[dict[str, Any]]:
    dt = 1.0 / float(sample_rate_hz)
    sample_times = np.arange(0.0, truth.time[-1] + dt * 0.25, dt)
    sample_times = sample_times[sample_times <= truth.time[-1] + 1e-12]
    components = []
    for values in (truth.position, truth.velocity, truth.acceleration, truth.jerk):
        components.append(
            np.column_stack(
                [
                    np.interp(sample_times, truth.time, values[:, joint])
                    for joint in range(truth.dof)
                ]
            )
        )
    position, velocity, acceleration, jerk = components
    trajectory_id = f"multidof-{truth.dof}-{truth.pattern}-{truth.seed}"
    rows: list[dict[str, Any]] = []
    for k, sample_time in enumerate(sample_times):
        for joint in range(truth.dof):
            rows.append(
                empty_sample(
                    run_id=run_id,
                    dataset_id="synthetic-multidof-v1",
                    session_id="synthetic-multidof",
                    trajectory_id=trajectory_id,
                    split=split,
                    seed=truth.seed,
                    joint_id=f"joint_{joint}",
                    k=k,
                    source_time=float(sample_time),
                    arrival_time=float(sample_time),
                    control_time=float(sample_time),
                    dt_actual=dt,
                    dt_control=dt,
                    p_ref=float(position[k, joint]),
                    v_ref_truth=float(velocity[k, joint]),
                    a_ref_truth=float(acceleration[k, joint]),
                    j_ref_truth=float(jerk[k, joint]),
                    p_meas=float(position[k, joint]),
                    source_kind="synthetic_multidof_feasible",
                    reference_family="multidof",
                    reference_variant=truth.pattern,
                    scenario_id="clean",
                    truth_available=True,
                    measurement_available=True,
                    measurement_valid=True,
                )
            )
    validate_samples(rows)
    return rows


__all__ = [
    "MultiDOFTrackingDiagnostics",
    "MultiDOFTruth",
    "PATTERNS",
    "compute_multidof_tracking_diagnostics",
    "generate_multidof_truth",
    "multidof_to_rows",
]
