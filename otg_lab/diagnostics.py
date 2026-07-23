"""Strict formal diagnostics derived from canonical per-sample artifacts.

This module deliberately contains no online control logic.  It consumes the
versioned sample rows after a run and produces finite, rectangular records for
invariant, replay, robustness, and synthetic timing/frequency audits.  Missing
values are rejected unless the sample contract gives them an explicit meaning
(for example a missing measurement on a recorded drop).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .metrics import (
    QP_FAILURE_CATEGORIES,
    MetricValidationError,
    best_lag_metrics,
    constant_jerk_segment_extrema,
    detect_reference_events,
    frequency_response_metrics,
    interpolate_truth_at_times,
    local_delay_metrics,
)
from .schema import validate_samples

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]


class DiagnosticValidationError(ValueError):
    """Raised when a diagnostic cannot be computed without hiding missing data."""


_GROUP_FIELDS = (
    "run_id",
    "dataset_id",
    "session_id",
    "trajectory_id",
    "split",
    "seed",
    "scenario_id",
    "method_id",
    "estimator_id",
    "predictor_id",
    "target_mode",
    "governor_id",
    "follower_id",
    "plant_id",
)

_OUTPUT_IDENTITY_FIELDS = (
    "run_id",
    "dataset_id",
    "session_id",
    "trajectory_id",
    "split",
    "seed",
    "scenario_id",
    "method_id",
)

_FAULT_FIELDS = (
    "event_dropped",
    "event_burst_drop",
    "event_held",
    "event_duplicate",
    "event_timestamp_regression",
    "event_future_source_time",
    "event_outlier",
    "event_nonfinite",
    "event_impossible_jump",
    "invalid_input",
)


@dataclass(frozen=True)
class _AlignedGroup:
    identity: dict[str, Any]
    joint_ids: tuple[str, ...]
    cycles: tuple[tuple[Mapping[str, Any], ...], ...]
    control_time: FloatArray

    @property
    def n_samples(self) -> int:
        return len(self.cycles)

    @property
    def dof(self) -> int:
        return len(self.joint_ids)


def _as_rows(samples: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows = list(samples)
    if not rows:
        raise DiagnosticValidationError("sample artifact is empty")
    try:
        validate_samples(rows)
    except (KeyError, TypeError, ValueError) as error:
        raise DiagnosticValidationError(
            f"canonical sample validation failed: {error}"
        ) from error
    for row in rows:
        method = row.get("method_id")
        if not isinstance(method, str) or not method:
            raise DiagnosticValidationError(
                "method_id must be a non-empty string for formal diagnostics"
            )
    return rows


def _aligned_groups(
    samples: Iterable[Mapping[str, Any]],
) -> list[_AlignedGroup]:
    rows = _as_rows(samples)
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(field) for field in _GROUP_FIELDS)].append(row)

    output: list[_AlignedGroup] = []
    for key in sorted(grouped, key=lambda item: tuple(str(value) for value in item)):
        group_rows = grouped[key]
        by_joint: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in group_rows:
            by_joint[str(row["joint_id"])].append(row)
        joint_ids = tuple(sorted(by_joint))
        ordered = [
            sorted(by_joint[joint], key=lambda row: int(row["k"]))
            for joint in joint_ids
        ]
        expected_k = tuple(int(row["k"]) for row in ordered[0])
        if len(expected_k) < 2:
            raise DiagnosticValidationError(
                "formal diagnostics require at least two samples per trajectory"
            )
        control_time = np.asarray(
            [float(row["control_time"]) for row in ordered[0]], dtype=float
        )
        if not np.all(np.isfinite(control_time)) or np.any(np.diff(control_time) <= 0):
            raise DiagnosticValidationError(
                "control_time must be finite and increasing"
            )
        for joint_rows in ordered[1:]:
            observed_k = tuple(int(row["k"]) for row in joint_rows)
            observed_time = np.asarray(
                [float(row["control_time"]) for row in joint_rows], dtype=float
            )
            if observed_k != expected_k or not np.allclose(
                observed_time, control_time, rtol=0.0, atol=1e-12
            ):
                raise DiagnosticValidationError(
                    "multi-DoF sample grids are not exactly synchronized"
                )
        cycles = tuple(
            tuple(joint_rows[index] for joint_rows in ordered)
            for index in range(len(expected_k))
        )
        identity = {field: cycles[0][0][field] for field in _OUTPUT_IDENTITY_FIELDS}
        output.append(
            _AlignedGroup(
                identity=identity,
                joint_ids=joint_ids,
                cycles=cycles,
                control_time=control_time,
            )
        )
    return output


def _matrix(group: _AlignedGroup, field: str) -> FloatArray:
    presence = np.asarray(
        [[row.get(field) is not None for row in cycle] for cycle in group.cycles],
        dtype=bool,
    )
    if not np.all(presence):
        missing = np.argwhere(~presence)[0]
        raise DiagnosticValidationError(
            f"{field} is missing at cycle {int(missing[0])}, joint {int(missing[1])}"
        )
    try:
        values = np.asarray(
            [[float(row[field]) for row in cycle] for cycle in group.cycles],
            dtype=float,
        )
    except (TypeError, ValueError) as error:
        raise DiagnosticValidationError(f"{field} is not numeric") from error
    if not np.all(np.isfinite(values)):
        raise DiagnosticValidationError(f"{field} contains NaN or infinity")
    return values


def _synchronized_vector(group: _AlignedGroup, field: str) -> FloatArray:
    values = _matrix(group, field)
    if not np.allclose(values, values[:, [0]], rtol=0.0, atol=1e-12):
        raise DiagnosticValidationError(
            f"{field} differs across joints in a synchronized control cycle"
        )
    return values[:, 0]


def _cycle_flags(
    group: _AlignedGroup, field: str, *, require_synchronized: bool = False
) -> BoolArray:
    values: list[list[bool]] = []
    for cycle_index, cycle in enumerate(group.cycles):
        local: list[bool] = []
        for joint_index, row in enumerate(cycle):
            value = row.get(field)
            if not isinstance(value, (bool, np.bool_)):
                raise DiagnosticValidationError(
                    f"{field} is not boolean at cycle {cycle_index}, "
                    f"joint {joint_index}"
                )
            local.append(bool(value))
        if require_synchronized and len(set(local)) != 1:
            raise DiagnosticValidationError(
                f"{field} differs across synchronized joints at cycle {cycle_index}"
            )
        values.append(local)
    return np.any(np.asarray(values, dtype=bool), axis=1)


def _qp_status_summary(group: _AlignedGroup) -> dict[str, float | int]:
    """Count every normalized QP failure category without collapsing causes."""

    statuses: list[str] = []
    presence = [
        [row.get("qp_status_category") is not None for row in cycle]
        for cycle in group.cycles
    ]
    if any(any(cycle) for cycle in presence):
        if not all(all(cycle) for cycle in presence):
            raise DiagnosticValidationError(
                "qp_status_category is only partially available"
            )
        for cycle_index, cycle in enumerate(group.cycles):
            local = [row.get("qp_status_category") for row in cycle]
            if any(not isinstance(value, str) for value in local):
                raise DiagnosticValidationError(
                    f"qp_status_category is non-string at cycle {cycle_index}"
                )
            if len(set(local)) != 1:
                raise DiagnosticValidationError(
                    "qp_status_category differs across synchronized joints"
                )
            statuses.append(str(local[0]))
    denominator = len(statuses)
    result: dict[str, float | int] = {
        "qp_status_evaluated_count": denominator,
        "qp_status_evaluated_fraction": denominator / group.n_samples,
    }
    for category in QP_FAILURE_CATEGORIES:
        count = statuses.count(category)
        result[f"{category}_count"] = count
        result[f"{category}_rate"] = count / denominator if denominator else 0.0
    solved_count = statuses.count("qp_solved")
    result["qp_solved_count"] = solved_count
    result["qp_solved_rate"] = solved_count / denominator if denominator else 0.0
    recognized = set(QP_FAILURE_CATEGORIES) | {"qp_solved"}
    other_count = sum(value not in recognized for value in statuses)
    result["qp_other_status_count"] = other_count
    result["qp_other_status_rate"] = other_count / denominator if denominator else 0.0
    return result


def _limits(
    values: Mapping[str, ArrayLike | float], dof: int
) -> tuple[FloatArray, FloatArray, FloatArray]:
    output: list[FloatArray] = []
    for name in ("max_velocity", "max_acceleration", "max_jerk"):
        if name not in values:
            raise DiagnosticValidationError(f"motion limits are missing {name}")
        try:
            vector = np.broadcast_to(
                np.asarray(values[name], dtype=float), (dof,)
            ).copy()
        except ValueError as error:
            raise DiagnosticValidationError(
                f"{name} cannot be broadcast to {dof} joints"
            ) from error
        if not np.all(np.isfinite(vector)) or np.any(vector <= 0.0):
            raise DiagnosticValidationError(
                f"{name} must contain finite positive limits"
            )
        output.append(vector)
    return output[0], output[1], output[2]


def _base_record(group: _AlignedGroup) -> dict[str, Any]:
    return {
        **group.identity,
        "n_samples": group.n_samples,
        "n_joints": group.dof,
    }


def _ensure_rectangular_finite(
    records: Sequence[Mapping[str, Any]], *, label: str, allow_empty: bool = False
) -> list[dict[str, Any]]:
    if not records:
        if allow_empty:
            return []
        raise DiagnosticValidationError(f"{label} produced no records")
    expected = set(records[0])
    output: list[dict[str, Any]] = []
    for index, source in enumerate(records):
        record = dict(source)
        if set(record) != expected:
            missing = sorted(expected - set(record))
            extra = sorted(set(record) - expected)
            raise DiagnosticValidationError(
                f"{label} record {index} is not rectangular; "
                f"missing={missing}, extra={extra}"
            )
        for name, value in record.items():
            if value is None:
                raise DiagnosticValidationError(
                    f"{label} record {index}.{name} is unexplained null"
                )
            if isinstance(value, Real) and not isinstance(value, (bool, np.bool_)):
                if not math.isfinite(float(value)):
                    raise DiagnosticValidationError(
                        f"{label} record {index}.{name} is non-finite"
                    )
            elif not isinstance(value, (str, bool, np.bool_, np.integer)):
                raise DiagnosticValidationError(
                    f"{label} record {index}.{name} has unsupported value type"
                )
        output.append(record)
    return output


def _rate(count: int, denominator: int, name: str) -> float:
    if denominator <= 0:
        raise DiagnosticValidationError(f"{name} has a zero denominator")
    return float(count / denominator)


def governor_invariant_summaries(
    samples: Iterable[Mapping[str, Any]],
    *,
    motion_limits: Mapping[str, ArrayLike | float],
    dynamics_tolerance: float = 1e-8,
    limit_tolerance: float = 1e-8,
) -> list[dict[str, Any]]:
    """Audit executable-target invariants once per method and trajectory.

    Adjacent executable states imply a unique constant jerk through their
    acceleration endpoints.  Position and velocity are independently checked
    against that exact segment.  A discontinuity is *explained* only when a
    neighboring cycle records fallback, projection, or an explicit state reset;
    all other inconsistent transitions are counted as unexplained.

    ``free_trajectory_duration`` is evaluated only on non-fallback cycles and
    must be present there.  The reported one-step rate is exactly
    ``T_free <= dt_control``; target-time consistency is a separate invariant.
    """

    if not math.isfinite(dynamics_tolerance) or dynamics_tolerance < 0.0:
        raise DiagnosticValidationError(
            "dynamics_tolerance must be finite and non-negative"
        )
    if not math.isfinite(limit_tolerance) or limit_tolerance < 0.0:
        raise DiagnosticValidationError(
            "limit_tolerance must be finite and non-negative"
        )

    records: list[dict[str, Any]] = []
    for group in _aligned_groups(samples):
        vmax, amax, jmax = _limits(motion_limits, group.dof)
        dt_matrix = _matrix(group, "dt_control")
        if not np.allclose(dt_matrix, dt_matrix[0, 0], rtol=0.0, atol=1e-12):
            raise DiagnosticValidationError(
                "dt_control must be constant within a governor trajectory"
            )
        dt = float(dt_matrix[0, 0])
        if dt <= 0.0:
            raise DiagnosticValidationError("dt_control must be positive")

        executable = np.stack(
            [
                _matrix(group, "executable_target_p"),
                _matrix(group, "executable_target_v"),
                _matrix(group, "executable_target_a"),
            ],
            axis=2,
        )
        target_time = _synchronized_vector(group, "executable_target_time")
        if np.any(np.diff(target_time) <= 0.0):
            raise DiagnosticValidationError(
                "executable_target_time must be strictly increasing"
            )
        fallback = _cycle_flags(group, "fallback", require_synchronized=True)
        projected = _cycle_flags(group, "target_projected", require_synchronized=True)
        state_reset = _cycle_flags(group, "state_reset")

        limit_point_admissible = np.all(
            (np.abs(executable[:, :, 1]) <= vmax[None, :] + limit_tolerance)
            & (np.abs(executable[:, :, 2]) <= amax[None, :] + limit_tolerance),
            axis=1,
        )
        available_velocity = np.maximum(
            0.0, vmax[None, :] - np.abs(executable[:, :, 1])
        )
        stopping_acceleration_bound = np.sqrt(2.0 * jmax[None, :] * available_velocity)
        point_admissible = limit_point_admissible & np.all(
            np.abs(executable[:, :, 2])
            <= stopping_acceleration_bound + limit_tolerance,
            axis=1,
        )
        velocity_margin = float(np.min(vmax[None, :] - np.abs(executable[:, :, 1])))
        acceleration_margin = float(np.min(amax[None, :] - np.abs(executable[:, :, 2])))

        durations = np.diff(target_time)
        inferred_jerk = (executable[1:, :, 2] - executable[:-1, :, 2]) / durations[
            :, None
        ]
        initial = executable[:-1]
        predicted_position = (
            initial[:, :, 0]
            + initial[:, :, 1] * durations[:, None]
            + 0.5 * initial[:, :, 2] * durations[:, None] ** 2
            + inferred_jerk * durations[:, None] ** 3 / 6.0
        )
        predicted_velocity = (
            initial[:, :, 1]
            + initial[:, :, 2] * durations[:, None]
            + 0.5 * inferred_jerk * durations[:, None] ** 2
        )
        predicted_acceleration = initial[:, :, 2] + inferred_jerk * durations[:, None]
        position_residual = executable[1:, :, 0] - predicted_position
        velocity_residual = executable[1:, :, 1] - predicted_velocity
        acceleration_residual = executable[1:, :, 2] - predicted_acceleration
        consistent = np.all(
            (np.abs(position_residual) <= dynamics_tolerance)
            & (np.abs(velocity_residual) <= dynamics_tolerance)
            & (np.abs(acceleration_residual) <= dynamics_tolerance),
            axis=1,
        )
        explained = (
            fallback[:-1]
            | fallback[1:]
            | projected[:-1]
            | projected[1:]
            | state_reset[1:]
        )
        unexplained_inconsistent = ~consistent & ~explained
        nonfallback_transition = ~fallback[:-1] & ~fallback[1:]
        nonfallback_transition_count = int(np.count_nonzero(nonfallback_transition))
        nonfallback_sequence_consistent_count = int(
            np.count_nonzero(consistent & nonfallback_transition)
        )

        continuous_limit_violation = np.zeros(durations.size, dtype=bool)
        continuous_violation_duration = np.zeros(durations.size, dtype=float)
        jerk_margin = math.inf
        for index, duration in enumerate(durations):
            try:
                audit_rows = constant_jerk_segment_extrema(
                    initial[index],
                    inferred_jerk[index],
                    float(duration),
                    max_velocity=vmax,
                    max_acceleration=amax,
                    max_jerk=jmax,
                    tolerance=limit_tolerance,
                )
            except MetricValidationError as error:
                raise DiagnosticValidationError(
                    f"constant-jerk continuous audit failed: {error}"
                ) from error
            continuous_limit_violation[index] = any(
                int(row["violation_count"]) > 0 for row in audit_rows
            )
            continuous_violation_duration[index] = max(
                float(row["violation_duration_s"]) for row in audit_rows
            )
            jerk_margin = min(
                jerk_margin, *(float(row["jerk_margin"]) for row in audit_rows)
            )

        nonfallback = ~fallback
        nonfallback_count = int(np.count_nonzero(nonfallback))
        nonfallback_rate_defined = nonfallback_count > 0
        free_duration = np.zeros(group.n_samples, dtype=float)
        free_recorded = np.zeros(group.n_samples, dtype=bool)
        for cycle_index, cycle in enumerate(group.cycles):
            local: list[float] = []
            for joint_index, row in enumerate(cycle):
                value = row.get("free_trajectory_duration")
                if value is None:
                    if nonfallback[cycle_index]:
                        raise DiagnosticValidationError(
                            "free_trajectory_duration is missing on a non-fallback "
                            f"cycle {cycle_index}, joint {joint_index}"
                        )
                    continue
                numeric = float(value)
                if not math.isfinite(numeric) or numeric < 0.0:
                    raise DiagnosticValidationError(
                        "free_trajectory_duration must be finite and non-negative"
                    )
                local.append(numeric)
            if local:
                if len(local) != group.dof:
                    raise DiagnosticValidationError(
                        "free_trajectory_duration is partially available within a cycle"
                    )
                free_duration[cycle_index] = max(local)
                free_recorded[cycle_index] = True

        reachable = free_duration[nonfallback] <= dt + dynamics_tolerance
        target_one_step = (
            np.abs(target_time[nonfallback] - group.control_time[nonfallback] - dt)
            <= dynamics_tolerance
        )
        one_step_invariant = reachable & target_one_step & point_admissible[nonfallback]
        transition_count = int(durations.size)
        consistent_count = int(np.count_nonzero(consistent))
        continuous_count = int(np.count_nonzero(continuous_limit_violation))
        combined_violation = continuous_limit_violation | ~consistent

        record = {
            **_base_record(group),
            "dt_control_s": dt,
            "adjacent_transition_count": transition_count,
            "adjacent_consistent_count": consistent_count,
            "adjacent_consistency_rate": _rate(
                consistent_count, transition_count, "adjacent consistency"
            ),
            "adjacent_explained_inconsistent_count": int(
                np.count_nonzero(~consistent & explained)
            ),
            "adjacent_unexplained_inconsistent_count": int(
                np.count_nonzero(unexplained_inconsistent)
            ),
            "adjacent_max_position_residual": float(np.max(np.abs(position_residual))),
            "adjacent_max_velocity_residual": float(np.max(np.abs(velocity_residual))),
            "adjacent_max_acceleration_residual": float(
                np.max(np.abs(acceleration_residual))
            ),
            "executable_point_admissible_count": int(
                np.count_nonzero(point_admissible)
            ),
            "executable_point_admissible_rate": float(np.mean(point_admissible)),
            "nonfallback_point_admissible_count": int(
                np.count_nonzero(point_admissible & nonfallback)
            ),
            "nonfallback_point_admissible_rate": (
                float(np.mean(point_admissible[nonfallback]))
                if nonfallback_rate_defined
                else 0.0
            ),
            "executable_limit_point_admissible_count": int(
                np.count_nonzero(limit_point_admissible)
            ),
            "executable_limit_point_admissible_rate": float(
                np.mean(limit_point_admissible)
            ),
            "executable_velocity_margin_min": velocity_margin,
            "executable_acceleration_margin_min": acceleration_margin,
            "executable_stopping_acceleration_margin_min": float(
                np.min(stopping_acceleration_bound - np.abs(executable[:, :, 2]))
            ),
            "inferred_jerk_margin_min": float(jerk_margin),
            "nonfallback_sample_count": nonfallback_count,
            "nonfallback_rate_defined": nonfallback_rate_defined,
            "nonfallback_t_free_recorded_count": int(
                np.count_nonzero(free_recorded & nonfallback)
            ),
            "nonfallback_t_free_max_s": (
                float(np.max(free_duration[nonfallback]))
                if nonfallback_rate_defined
                else 0.0
            ),
            "nonfallback_one_step_reachable_count": int(np.count_nonzero(reachable)),
            "nonfallback_one_step_reachable_rate": (
                float(np.mean(reachable)) if nonfallback_rate_defined else 0.0
            ),
            "nonfallback_one_step_target_time_count": int(
                np.count_nonzero(target_one_step)
            ),
            "nonfallback_one_step_target_time_rate": (
                float(np.mean(target_one_step)) if nonfallback_rate_defined else 0.0
            ),
            "nonfallback_one_step_invariant_count": int(
                np.count_nonzero(one_step_invariant)
            ),
            "nonfallback_one_step_invariant_rate": (
                float(np.mean(one_step_invariant)) if nonfallback_rate_defined else 0.0
            ),
            "nonfallback_transition_count": nonfallback_transition_count,
            "nonfallback_sequence_rate_defined": nonfallback_transition_count > 0,
            "nonfallback_sequence_consistent_count": (
                nonfallback_sequence_consistent_count
            ),
            "nonfallback_sequence_consistency_rate": (
                nonfallback_sequence_consistent_count / nonfallback_transition_count
                if nonfallback_transition_count
                else 0.0
            ),
            "continuous_transition_count": transition_count,
            "continuous_limit_violation_count": continuous_count,
            "continuous_limit_violation_rate": _rate(
                continuous_count, transition_count, "continuous violations"
            ),
            "continuous_violation_duration_lower_bound_s": float(
                np.sum(continuous_violation_duration)
            ),
            "continuous_invariant_or_limit_violation_count": int(
                np.count_nonzero(combined_violation)
            ),
            "continuous_invariant_or_limit_violation_rate": float(
                np.mean(combined_violation)
            ),
            "fallback_count": int(np.count_nonzero(fallback)),
            "fallback_rate": float(np.mean(fallback)),
            "projection_count": int(np.count_nonzero(projected)),
            "projection_rate": float(np.mean(projected)),
            "state_reset_count": int(np.count_nonzero(state_reset)),
            **_qp_status_summary(group),
        }
        records.append(record)
    return _ensure_rectangular_finite(records, label="governor invariant summary")


def _distribution(values: ArrayLike, prefix: str) -> dict[str, float | int]:
    numeric = np.asarray(values, dtype=float).reshape(-1)
    if numeric.size == 0 or not np.all(np.isfinite(numeric)):
        raise DiagnosticValidationError(f"{prefix} distribution is empty or non-finite")
    absolute = np.abs(numeric)
    return {
        f"{prefix}_count": int(numeric.size),
        f"{prefix}_mean": float(np.mean(numeric)),
        f"{prefix}_std": float(np.std(numeric)),
        f"{prefix}_rms": float(np.sqrt(np.mean(np.square(numeric)))),
        f"{prefix}_p50_abs": float(np.quantile(absolute, 0.5, method="linear")),
        f"{prefix}_p95_abs": float(np.quantile(absolute, 0.95, method="linear")),
        f"{prefix}_max_abs": float(np.max(absolute)),
    }


def _delta_distributions(
    values: FloatArray,
    times: FloatArray,
    prefix: str,
) -> dict[str, float | int]:
    if values.shape[0] != times.size or times.size < 2:
        raise DiagnosticValidationError(
            f"{prefix} needs at least two synchronized samples"
        )
    delta_time = np.diff(times)
    if np.any(delta_time <= 0.0) or not np.all(np.isfinite(delta_time)):
        raise DiagnosticValidationError(f"{prefix} time grid is invalid")
    delta = np.diff(values, axis=0)
    rate = delta / delta_time[:, None]
    return {
        **_distribution(delta, f"{prefix}_delta"),
        **_distribution(rate, f"{prefix}_rate"),
    }


def real_replay_diagnostics(
    samples: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize real-replay behavior without consulting derivative truth.

    The innovation is the recorded finite measurement minus the posterior
    position emitted on that control row.  It is intentionally labelled a
    posterior measurement residual: the artifact has no stored prior
    covariance or pre-update state from which to invent a Kalman innovation.
    Non-finite injected measurements are explicitly counted and excluded.
    """

    records: list[dict[str, Any]] = []
    for group in _aligned_groups(samples):
        posterior_p = _matrix(group, "posterior_p")
        posterior_v = _matrix(group, "posterior_v")
        posterior_a = _matrix(group, "posterior_a")
        command_v = _matrix(group, "command_v")
        command_a = _matrix(group, "command_a")
        command_jerk = _matrix(group, "command_jerk")
        command_time = _synchronized_vector(group, "command_time")
        if np.any(np.diff(command_time) <= 0.0):
            raise DiagnosticValidationError("command_time must be strictly increasing")

        innovations: list[float] = []
        available_measurements = 0
        valid_measurements = 0
        nonfinite_excluded = 0
        for cycle_index, cycle in enumerate(group.cycles):
            for joint_index, row in enumerate(cycle):
                available = row.get("measurement_available")
                valid = row.get("measurement_valid")
                if not isinstance(available, (bool, np.bool_)) or not isinstance(
                    valid, (bool, np.bool_)
                ):
                    raise DiagnosticValidationError(
                        "measurement availability/validity flags must be boolean"
                    )
                if not available:
                    continue
                available_measurements += 1
                valid_measurements += int(bool(valid))
                measurement = row.get("p_meas")
                if measurement is None:
                    raise DiagnosticValidationError(
                        "available measurement has no p_meas value"
                    )
                numeric = float(measurement)
                if not valid:
                    continue
                if not math.isfinite(numeric):
                    if not bool(row.get("event_nonfinite")):
                        raise DiagnosticValidationError(
                            "non-finite measurement is not explicitly fault-labelled"
                        )
                    nonfinite_excluded += 1
                    continue
                innovations.append(
                    numeric - float(posterior_p[cycle_index, joint_index])
                )
        if not innovations:
            raise DiagnosticValidationError(
                "real replay has no finite measurements for posterior innovation"
            )

        arrival_time = _matrix(group, "arrival_time")
        source_time = _matrix(group, "source_time")
        causal_source_time = (
            _matrix(group, "posterior_axis_source_time")
            if all(
                row.get("posterior_axis_source_time") is not None
                for cycle in group.cycles
                for row in cycle
            )
            else source_time
        )
        posterior_available = _matrix(group, "posterior_available_time")
        arrival_latency = command_time[:, None] - arrival_time
        source_latency = command_time[:, None] - causal_source_time
        posterior_latency = command_time[:, None] - posterior_available
        for values, name in (
            (arrival_latency, "arrival_to_command_latency"),
            (source_latency, "source_to_command_latency"),
            (posterior_latency, "posterior_available_to_command_latency"),
        ):
            if np.any(values < -1e-12):
                raise DiagnosticValidationError(f"{name} is negative")

        flags = {field: _cycle_flags(group, field) for field in _FAULT_FIELDS}
        any_fault = np.logical_or.reduce(tuple(flags.values()))
        fallback = _cycle_flags(group, "fallback_applied", require_synchronized=True)
        reset = _cycle_flags(group, "state_reset")
        deadline = _cycle_flags(group, "deadline_miss")

        source_delta = np.diff(source_time, axis=0)
        observed_repeat = np.any(np.abs(source_delta) <= 1e-15, axis=1)
        observed_regression = np.any(source_delta < 0.0, axis=1)
        explained_repeat = flags["event_held"][1:] | flags["event_duplicate"][1:]
        if np.any(observed_repeat & ~explained_repeat):
            raise DiagnosticValidationError(
                "source timestamp repeat is not labelled held or duplicate"
            )
        if np.any(observed_regression & ~flags["event_timestamp_regression"][1:]):
            raise DiagnosticValidationError(
                "source timestamp regression is not explicitly labelled"
            )

        input_drop_count = 0
        arrival_count = 0
        for cycle in group.cycles:
            local_drops: list[int] = []
            local_arrivals: list[int] = []
            for row in cycle:
                drop_value = row.get("event_input_drop_count")
                arrival_value = row.get("event_arrivals_count")
                if not isinstance(drop_value, (int, np.integer)) or int(drop_value) < 0:
                    raise DiagnosticValidationError(
                        "event_input_drop_count must be a non-negative integer"
                    )
                if (
                    not isinstance(arrival_value, (int, np.integer))
                    or int(arrival_value) < 0
                ):
                    raise DiagnosticValidationError(
                        "event_arrivals_count must be a non-negative integer"
                    )
                local_drops.append(int(drop_value))
                local_arrivals.append(int(arrival_value))
            input_drop_count += max(local_drops)
            arrival_count += max(local_arrivals)

        record: dict[str, Any] = {
            **_base_record(group),
            "derivative_truth_used": False,
            "measurement_available_count": available_measurements,
            "measurement_valid_count": valid_measurements,
            "posterior_innovation_finite_count": len(innovations),
            "posterior_innovation_nonfinite_excluded_count": nonfinite_excluded,
            **_distribution(innovations, "posterior_measurement_innovation"),
            **_distribution(posterior_v, "posterior_v"),
            **_distribution(posterior_a, "posterior_a"),
            **_delta_distributions(
                posterior_v, group.control_time, "posterior_v_smoothness"
            ),
            **_delta_distributions(
                posterior_a, group.control_time, "posterior_a_smoothness"
            ),
            **_distribution(command_v, "command_v"),
            **_distribution(command_a, "command_a"),
            **_distribution(command_jerk, "command_jerk"),
            **_delta_distributions(command_v, command_time, "command_v_smoothness"),
            **_delta_distributions(command_a, command_time, "command_a_smoothness"),
            **_delta_distributions(
                command_jerk, command_time, "command_jerk_smoothness"
            ),
            **_distribution(arrival_latency, "arrival_to_command_latency_s"),
            **_distribution(source_latency, "source_to_command_latency_s"),
            **_distribution(
                posterior_latency, "posterior_available_to_command_latency_s"
            ),
            "fault_any_count": int(np.count_nonzero(any_fault)),
            "fault_any_rate": float(np.mean(any_fault)),
            "event_dropped_count": int(np.count_nonzero(flags["event_dropped"])),
            "event_input_drop_count": input_drop_count,
            "event_burst_drop_count": int(np.count_nonzero(flags["event_burst_drop"])),
            "event_held_count": int(np.count_nonzero(flags["event_held"])),
            "event_duplicate_count": int(np.count_nonzero(flags["event_duplicate"])),
            "event_source_repeat_observed_count": int(
                np.count_nonzero(observed_repeat)
            ),
            "event_timestamp_regression_count": int(
                np.count_nonzero(flags["event_timestamp_regression"])
            ),
            "event_future_source_time_count": int(
                np.count_nonzero(flags["event_future_source_time"])
            ),
            "event_source_regression_observed_count": int(
                np.count_nonzero(observed_regression)
            ),
            "event_outlier_count": int(np.count_nonzero(flags["event_outlier"])),
            "event_nonfinite_count": int(np.count_nonzero(flags["event_nonfinite"])),
            "event_impossible_jump_count": int(
                np.count_nonzero(flags["event_impossible_jump"])
            ),
            "invalid_input_count": int(np.count_nonzero(flags["invalid_input"])),
            "event_arrivals_count": arrival_count,
            "state_reset_count": int(np.count_nonzero(reset)),
            "fallback_count": int(np.count_nonzero(fallback)),
            "deadline_miss_count": int(np.count_nonzero(deadline)),
        }
        records.append(record)
    return _ensure_rectangular_finite(records, label="real replay diagnostics")


def _tracking_arrays(
    group: _AlignedGroup, output_field: str
) -> tuple[FloatArray, FloatArray, FloatArray, BoolArray]:
    reference = _matrix(group, "p_ref")
    output = _matrix(group, output_field)
    command_time = _synchronized_vector(group, "command_time")
    mask = (command_time >= group.control_time[0] - 1e-12) & (
        command_time <= group.control_time[-1] + 1e-12
    )
    if not np.any(mask):
        raise DiagnosticValidationError(
            "no command/output physical times overlap the reference"
        )
    if np.count_nonzero(mask) < 2:
        raise DiagnosticValidationError(
            "fewer than two command/output times overlap the reference"
        )
    try:
        aligned_reference = interpolate_truth_at_times(
            group.control_time, reference, command_time[mask]
        )
    except MetricValidationError as error:
        raise DiagnosticValidationError(
            f"tracking reference interpolation failed: {error}"
        ) from error
    return command_time[mask], aligned_reference, output[mask], mask


def _fault_episode_records(
    group: _AlignedGroup,
    *,
    output_field: str,
    recovery_tolerance: float,
    recovery_hold_samples: int,
    pre_fault_window_s: float,
) -> tuple[list[dict[str, Any]], FloatArray, BoolArray, BoolArray]:
    output_time, reference, output, evaluation_mask = _tracking_arrays(
        group, output_field
    )
    evaluated_error = np.max(np.abs(output - reference), axis=1)
    command_time = _synchronized_vector(group, "command_time")
    error = np.full(group.n_samples, np.nan, dtype=float)
    error[evaluation_mask] = evaluated_error
    flag_values = {field: _cycle_flags(group, field) for field in _FAULT_FIELDS}
    fault = np.logical_or.reduce(tuple(flag_values.values()))
    starts = np.flatnonzero(fault & np.concatenate(([True], ~fault[:-1])))
    stops = np.flatnonzero(fault & np.concatenate((~fault[1:], [True])))
    median_dt = float(np.median(np.diff(group.control_time)))
    events: list[dict[str, Any]] = []
    for event_index, (start_value, stop_value) in enumerate(zip(starts, stops)):
        start = int(start_value)
        stop = int(stop_value)
        recovered_at: int | None = None
        for candidate in range(stop + 1, group.n_samples - recovery_hold_samples + 1):
            hold = slice(candidate, candidate + recovery_hold_samples)
            if (
                np.all(evaluation_mask[hold])
                and np.all(error[hold] <= recovery_tolerance)
                and not np.any(fault[hold])
            ):
                recovered_at = candidate
                break
        censored = recovered_at is None
        recovery_end = (
            group.n_samples - 1
            if censored
            else recovered_at + recovery_hold_samples - 1
        )
        recovery_observation_time = (
            float(output_time[-1]) if censored else float(command_time[recovered_at])
        )
        observed_recovery_time = max(
            0.0, recovery_observation_time - float(group.control_time[stop])
        )
        pre_mask = (output_time < group.control_time[start]) & (
            output_time >= group.control_time[start] - pre_fault_window_s
        )
        pre_count = int(np.count_nonzero(pre_mask))
        pre_max = float(np.max(evaluated_error[pre_mask])) if pre_count else 0.0
        window_indices = np.arange(start, recovery_end + 1, dtype=int)
        window_indices = window_indices[evaluation_mask[window_indices]]
        window_count = int(window_indices.size)
        if window_count:
            local_peak = int(window_indices[np.argmax(error[window_indices])])
            window_max = float(error[local_peak])
            peak_time = float(command_time[local_peak])
        else:
            window_max = 0.0
            peak_time = float(group.control_time[start])
        kinds = [
            field.removeprefix("event_")
            for field, values in flag_values.items()
            if np.any(values[start : stop + 1])
        ]
        if not kinds:
            raise DiagnosticValidationError("fault episode has no explicit fault kind")
        events.append(
            {
                **_base_record(group),
                "output_field": output_field,
                "fault_episode_id": event_index,
                "fault_kinds": ";".join(kinds),
                "fault_clock": "control_time",
                "fault_start_k": int(group.cycles[start][0]["k"]),
                "fault_end_k": int(group.cycles[stop][0]["k"]),
                "fault_start_time_s": float(group.control_time[start]),
                "fault_end_time_s": float(group.control_time[stop]),
                "fault_duration_s": float(
                    group.control_time[stop] - group.control_time[start] + median_dt
                ),
                "fault_cycle_count": int(stop - start + 1),
                "pre_fault_sample_count": pre_count,
                "pre_fault_error_observed": pre_count > 0,
                "pre_fault_max_abs_error": pre_max,
                "fault_window_evaluated_sample_count": window_count,
                "fault_window_error_observed": window_count > 0,
                "fault_window_max_abs_error": window_max,
                "fault_window_peak_time_s": peak_time,
                "recovered": not censored,
                "recovery_time_censored": censored,
                "recovery_observed_time_s": observed_recovery_time,
                "recovery_tolerance": recovery_tolerance,
                "recovery_hold_samples": recovery_hold_samples,
                "observation_end_time_s": float(output_time[-1]),
            }
        )
    return events, evaluated_error, fault, evaluation_mask


def robustness_fault_events(
    samples: Iterable[Mapping[str, Any]],
    *,
    recovery_tolerance: float,
    recovery_hold_samples: int = 3,
    pre_fault_window_s: float = 0.1,
    output_field: str = "plant_p",
) -> list[dict[str, Any]]:
    """Return one finite record per contiguous explicitly flagged fault episode.

    A censored episode reports the observed time from fault end to the artifact
    boundary in ``recovery_observed_time_s`` and sets
    ``recovery_time_censored``.  It never substitutes NaN for an unobserved
    recovery.
    """

    if not math.isfinite(recovery_tolerance) or recovery_tolerance < 0.0:
        raise DiagnosticValidationError(
            "recovery_tolerance must be finite and non-negative"
        )
    if not isinstance(recovery_hold_samples, int) or recovery_hold_samples < 1:
        raise DiagnosticValidationError("recovery_hold_samples must be positive")
    if not math.isfinite(pre_fault_window_s) or pre_fault_window_s <= 0.0:
        raise DiagnosticValidationError(
            "pre_fault_window_s must be finite and positive"
        )
    records: list[dict[str, Any]] = []
    for group in _aligned_groups(samples):
        local, _, _, _ = _fault_episode_records(
            group,
            output_field=output_field,
            recovery_tolerance=recovery_tolerance,
            recovery_hold_samples=recovery_hold_samples,
            pre_fault_window_s=pre_fault_window_s,
        )
        records.extend(local)
    return _ensure_rectangular_finite(
        records, label="robustness fault events", allow_empty=True
    )


def robustness_recovery_summaries(
    samples: Iterable[Mapping[str, Any]],
    *,
    recovery_tolerance: float,
    recovery_hold_samples: int = 3,
    pre_fault_window_s: float = 0.1,
    output_field: str = "plant_p",
) -> list[dict[str, Any]]:
    """Return one robustness/fault-recovery summary per method and trajectory."""

    if not math.isfinite(recovery_tolerance) or recovery_tolerance < 0.0:
        raise DiagnosticValidationError(
            "recovery_tolerance must be finite and non-negative"
        )
    if not isinstance(recovery_hold_samples, int) or recovery_hold_samples < 1:
        raise DiagnosticValidationError("recovery_hold_samples must be positive")
    if not math.isfinite(pre_fault_window_s) or pre_fault_window_s <= 0.0:
        raise DiagnosticValidationError(
            "pre_fault_window_s must be finite and positive"
        )

    records: list[dict[str, Any]] = []
    for group in _aligned_groups(samples):
        events, error, fault, evaluation_mask = _fault_episode_records(
            group,
            output_field=output_field,
            recovery_tolerance=recovery_tolerance,
            recovery_hold_samples=recovery_hold_samples,
            pre_fault_window_s=pre_fault_window_s,
        )
        recovery_values = np.asarray(
            [float(row["recovery_observed_time_s"]) for row in events], dtype=float
        )
        episode_count = len(events)
        censored_count = sum(bool(row["recovery_time_censored"]) for row in events)
        if episode_count:
            recovery_mean = float(np.mean(recovery_values))
            recovery_p95 = float(np.quantile(recovery_values, 0.95, method="linear"))
            recovery_max = float(np.max(recovery_values))
            fault_window_max = max(
                float(row["fault_window_max_abs_error"]) for row in events
            )
        else:
            recovery_mean = 0.0
            recovery_p95 = 0.0
            recovery_max = 0.0
            fault_window_max = 0.0
        records.append(
            {
                **_base_record(group),
                "output_field": output_field,
                "tracking_evaluated_sample_count": int(error.size),
                "tracking_evaluated_fraction": float(np.mean(evaluation_mask)),
                "trajectory_max_abs_error": float(np.max(error)),
                "has_explicit_fault": episode_count > 0,
                "fault_cycle_count": int(np.count_nonzero(fault)),
                "fault_cycle_rate": float(np.mean(fault)),
                "fault_episode_count": episode_count,
                "fault_recovered_episode_count": episode_count - censored_count,
                "fault_recovery_censored_count": censored_count,
                "fault_recovery_complete_rate": (
                    float((episode_count - censored_count) / episode_count)
                    if episode_count
                    else 1.0
                ),
                "fault_window_max_abs_error": fault_window_max,
                "recovery_observed_mean_s": recovery_mean,
                "recovery_observed_p95_s": recovery_p95,
                "recovery_observed_max_s": recovery_max,
                "recovery_tolerance": recovery_tolerance,
                "recovery_hold_samples": recovery_hold_samples,
                "pre_fault_window_s": pre_fault_window_s,
            }
        )
    return _ensure_rectangular_finite(records, label="robustness recovery summaries")


def _require_synthetic_truth(group: _AlignedGroup) -> None:
    truth = _cycle_flags(group, "truth_available", require_synchronized=True)
    if not np.all(truth):
        raise DiagnosticValidationError(
            "synthetic response diagnostics require derivative truth on every cycle"
        )
    for field in ("v_ref_truth", "a_ref_truth", "j_ref_truth"):
        _matrix(group, field)
    source_kinds = {
        str(row.get("source_kind")) for cycle in group.cycles for row in cycle
    }
    if not source_kinds or any(
        not source_kind.startswith("synthetic") for source_kind in source_kinds
    ):
        raise DiagnosticValidationError(
            "synthetic response diagnostics require a synthetic source_kind"
        )


def _persisted_frequency_specification(group: _AlignedGroup) -> dict[str, Any]:
    encoded = {
        row.get("reference_frequency_spec_json")
        for cycle in group.cycles
        for row in cycle
    }
    if len(encoded) != 1 or None in encoded:
        raise DiagnosticValidationError(
            "frequency response requires one persisted excitation specification"
        )
    try:
        specification = json.loads(str(next(iter(encoded))))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise DiagnosticValidationError(
            "persisted excitation frequency specification is invalid"
        ) from error
    if not isinstance(specification, dict):
        raise DiagnosticValidationError(
            "persisted excitation frequency specification must be an object"
        )
    return specification


def _strict_causal_tracking_arrays(
    group: _AlignedGroup, output_field: str
) -> tuple[FloatArray, FloatArray, FloatArray, BoolArray, int]:
    """Align output at its represented time and reject non-causal timestamps.

    A regular one-step run has one terminal command just beyond the sampled
    truth boundary.  That right-censored tail is allowed only when it still
    satisfies ``command_time == control_time + dt_control`` and is reported as
    an explicit excluded count.  Arbitrary future timestamps are never clipped
    into an apparently valid response window.
    """

    command_time = _synchronized_vector(group, "command_time")
    dt_control = _synchronized_vector(group, "dt_control")
    if np.any(dt_control <= 0.0):
        raise DiagnosticValidationError("dt_control must be positive")
    expected_time = group.control_time + dt_control
    tolerance = max(1e-12, 1e-8 * float(np.max(dt_control)))
    if not np.allclose(command_time, expected_time, rtol=0.0, atol=tolerance):
        mismatches = np.flatnonzero(np.abs(command_time - expected_time) > tolerance)
        first = int(mismatches[0])
        direction = "future " if command_time[first] > expected_time[first] else ""
        raise DiagnosticValidationError(
            f"{direction}command timestamp is non-canonical at cycle {first}; "
            "expected control_time + dt_control"
        )
    if np.any(np.diff(command_time) <= 0.0):
        raise DiagnosticValidationError(
            "canonical command timestamps must be strictly increasing"
        )
    latest_allowed = group.control_time[-1] + float(np.max(dt_control))
    if np.any(command_time > latest_allowed + tolerance):
        raise DiagnosticValidationError(
            "future command timestamp exceeds the one-step truth boundary"
        )

    output_time, reference, output, mask = _tracking_arrays(group, output_field)
    excluded_count = int(group.n_samples - np.count_nonzero(mask))
    return output_time, reference, output, mask, excluded_count


def synthetic_frequency_response(
    samples: Iterable[Mapping[str, Any]],
    *,
    frequencies_hz: Sequence[float] | None = None,
    output_field: str = "command_p",
    relative_amplitude_threshold: float = 1e-3,
    max_frequency_bins: int = 128,
) -> list[dict[str, Any]]:
    """Wrap :func:`frequency_response_metrics` with canonical clock alignment."""

    if frequencies_hz is not None and len(frequencies_hz) < 2:
        raise DiagnosticValidationError(
            "at least two frequencies are required for finite group delay"
        )
    records: list[dict[str, Any]] = []
    for group in _aligned_groups(samples):
        _require_synthetic_truth(group)
        output_time, reference, output, _ = _tracking_arrays(group, output_field)
        group_frequencies = frequencies_hz
        if group_frequencies is None:
            specification = _persisted_frequency_specification(group)
            try:
                if specification.get("kind") != "discrete_tones":
                    raise DiagnosticValidationError(
                        "automatic frequency response supports persisted discrete tones only"
                    )
                group_frequencies = tuple(
                    float(value) for value in specification["frequencies_hz"]
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise DiagnosticValidationError(
                    "persisted excitation frequency specification is invalid"
                ) from error
            if len(group_frequencies) < 2 or not all(
                math.isfinite(value) and value > 0.0 for value in group_frequencies
            ):
                raise DiagnosticValidationError(
                    "persisted frequency response requires at least two positive tones"
                )
        try:
            response = frequency_response_metrics(
                reference,
                output,
                output_time,
                frequencies_hz=group_frequencies,
                relative_amplitude_threshold=relative_amplitude_threshold,
                max_frequency_bins=max_frequency_bins,
            )
        except MetricValidationError as error:
            raise DiagnosticValidationError(
                f"frequency response failed for {group.identity['trajectory_id']}: {error}"
            ) from error
        for row in response:
            if "group_delay_s" not in row:
                raise DiagnosticValidationError(
                    "frequency response has no finite group delay; provide >=2 frequencies"
                )
            joint_index = int(row["joint_index"])
            records.append(
                {
                    **_base_record(group),
                    "output_field": output_field,
                    "response_sample_count": int(output_time.size),
                    "response_start_time_s": float(output_time[0]),
                    "response_end_time_s": float(output_time[-1]),
                    "joint_index": joint_index,
                    "joint_id": group.joint_ids[joint_index],
                    "frequency_hz": float(row["frequency_hz"]),
                    "gain": float(row["gain"]),
                    "phase_rad": float(row["phase_rad"]),
                    "phase_delay_s": float(row["phase_delay_s"]),
                    "group_delay_s": float(row["group_delay_s"]),
                }
            )
    return _ensure_rectangular_finite(records, label="synthetic frequency response")


def synthetic_chirp_frequency_response(
    samples: Iterable[Mapping[str, Any]],
    *,
    output_field: str = "command_p",
    band_count: int = 6,
    minimum_samples_per_band: int = 8,
    max_local_lag_s: float = 0.05,
    minimum_lag_overlap_fraction: float = 0.5,
    projection_relative_tolerance: float = 1e-8,
) -> list[dict[str, Any]]:
    """Measure a linear chirp in deterministic metadata-defined time windows.

    The frequency bands are equal-width subdivisions of the persisted chirp
    range.  Their time windows follow directly from the declared linear sweep;
    neither the output nor an FFT is used to select a favorable frequency.
    Reference truth is interpolated at canonical ``command_time`` so each
    response compares states representing the same physical instant.

    ``reference_projection_magnitude`` is the explicit complex-ratio
    denominator.  Window and local-lag sample denominators are also emitted so
    truncation and overlap cannot be hidden behind finite summary values.
    """

    if (
        isinstance(band_count, bool)
        or not isinstance(band_count, int)
        or band_count < 2
    ):
        raise DiagnosticValidationError("band_count must be an integer of at least two")
    if (
        isinstance(minimum_samples_per_band, bool)
        or not isinstance(minimum_samples_per_band, int)
        or minimum_samples_per_band < 4
    ):
        raise DiagnosticValidationError(
            "minimum_samples_per_band must be an integer of at least four"
        )
    if not math.isfinite(max_local_lag_s) or max_local_lag_s < 0.0:
        raise DiagnosticValidationError(
            "max_local_lag_s must be finite and non-negative"
        )
    if not 0.0 < minimum_lag_overlap_fraction <= 1.0:
        raise DiagnosticValidationError(
            "minimum_lag_overlap_fraction must lie in (0, 1]"
        )
    if (
        not math.isfinite(projection_relative_tolerance)
        or not 0.0 < projection_relative_tolerance < 1.0
    ):
        raise DiagnosticValidationError(
            "projection_relative_tolerance must lie in (0, 1)"
        )

    records: list[dict[str, Any]] = []
    for group in _aligned_groups(samples):
        _require_synthetic_truth(group)
        specification = _persisted_frequency_specification(group)
        kind = specification.get("kind")
        if kind not in {"chirp", "linear_chirp"}:
            raise DiagnosticValidationError(
                "chirp response requires persisted kind=chirp or kind=linear_chirp"
            )
        try:
            raw_start = specification["start_hz"]
            raw_end = specification["end_hz"]
            raw_duration = specification["duration_s"]
            if any(
                isinstance(value, bool) for value in (raw_start, raw_end, raw_duration)
            ):
                raise TypeError("boolean chirp metadata is invalid")
            start_hz = float(raw_start)
            end_hz = float(raw_end)
            duration_s = float(raw_duration)
        except (KeyError, TypeError, ValueError) as error:
            raise DiagnosticValidationError(
                "persisted chirp metadata requires numeric start_hz, end_hz, "
                "and duration_s"
            ) from error
        if not all(math.isfinite(value) for value in (start_hz, end_hz, duration_s)):
            raise DiagnosticValidationError("persisted chirp metadata is non-finite")
        if start_hz <= 0.0 or end_hz <= start_hz or duration_s <= 0.0:
            raise DiagnosticValidationError(
                "chirp metadata requires 0 < start_hz < end_hz and duration_s > 0"
            )

        dt_control = _synchronized_vector(group, "dt_control")
        observed_duration = float(group.control_time[-1] - group.control_time[0])
        duration_tolerance = max(1e-9, float(np.max(dt_control)) + 1e-12)
        if (
            duration_s < observed_duration - 1e-9
            or duration_s > observed_duration + duration_tolerance
        ):
            raise DiagnosticValidationError(
                "persisted chirp duration does not match the sampled truth horizon"
            )

        output_time, reference, output, _, excluded_count = (
            _strict_causal_tracking_arrays(group, output_field)
        )
        intervals = np.diff(output_time)
        median_dt = float(np.median(intervals))
        if end_hz >= 0.5 / median_dt:
            raise DiagnosticValidationError(
                "persisted chirp end_hz must lie strictly below Nyquist"
            )

        frequency_edges = np.linspace(start_hz, end_hz, band_count + 1)
        sweep_rate = (end_hz - start_hz) / duration_s
        trajectory_start = float(group.control_time[0])
        band_payloads: list[dict[str, Any]] = []
        ratios = np.empty((band_count, group.dof), dtype=np.complex128)
        for band_index in range(band_count):
            low_hz = float(frequency_edges[band_index])
            high_hz = float(frequency_edges[band_index + 1])
            window_start = trajectory_start + (low_hz - start_hz) / sweep_rate
            window_end = trajectory_start + (high_hz - start_hz) / sweep_rate
            final_band = band_index == band_count - 1

            if final_band:
                evaluated_mask = (output_time >= window_start) & (
                    output_time <= window_end + 1e-12
                )
                truth_mask = (group.control_time >= window_start) & (
                    group.control_time <= window_end + 1e-12
                )
            else:
                evaluated_mask = (output_time >= window_start) & (
                    output_time < window_end
                )
                truth_mask = (group.control_time >= window_start) & (
                    group.control_time < window_end
                )
            evaluated_count = int(np.count_nonzero(evaluated_mask))
            truth_count = int(np.count_nonzero(truth_mask))
            if evaluated_count < minimum_samples_per_band:
                raise DiagnosticValidationError(
                    f"chirp band {band_index} has {evaluated_count} evaluated "
                    f"samples, fewer than {minimum_samples_per_band}"
                )
            if truth_count <= 0:
                raise DiagnosticValidationError(
                    f"chirp band {band_index} has a zero truth-window denominator"
                )

            local_time = output_time[evaluated_mask]
            local_reference = reference[evaluated_mask]
            local_output = output[evaluated_mask]
            relative_time = local_time - trajectory_start
            chirp_phase = (
                2.0
                * np.pi
                * (
                    start_hz * relative_time
                    + 0.5 * sweep_rate * np.square(relative_time)
                )
            )
            normalized_window_time = (local_time - local_time[0]) / (
                local_time[-1] - local_time[0]
            )
            taper = np.sin(np.pi * normalized_window_time) ** 2
            quadrature = np.gradient(local_time)
            weights = taper * quadrature
            weight_sum = float(np.sum(weights))
            if not math.isfinite(weight_sum) or weight_sum <= 0.0:
                raise DiagnosticValidationError(
                    f"chirp band {band_index} has a zero projection weight denominator"
                )
            reference_mean = (
                np.sum(weights[:, None] * local_reference, axis=0) / weight_sum
            )
            output_mean = np.sum(weights[:, None] * local_output, axis=0) / weight_sum
            centered_reference = local_reference - reference_mean[None, :]
            centered_output = local_output - output_mean[None, :]
            kernel = np.exp(-1j * chirp_phase)[:, None]
            reference_coefficient = np.sum(
                weights[:, None] * centered_reference * kernel, axis=0
            )
            output_coefficient = np.sum(
                weights[:, None] * centered_output * kernel, axis=0
            )
            reference_energy = np.sqrt(
                np.sum(weights[:, None] * np.square(centered_reference), axis=0)
                * weight_sum
            )
            if np.any(reference_energy <= 0.0):
                raise DiagnosticValidationError(
                    f"chirp band {band_index} has zero reference energy"
                )
            normalized_projection = np.abs(reference_coefficient) / reference_energy
            if np.any(normalized_projection <= projection_relative_tolerance):
                raise DiagnosticValidationError(
                    f"chirp band {band_index} has an insufficient reference "
                    "projection denominator"
                )
            ratios[band_index] = output_coefficient / reference_coefficient

            elapsed_coverage = min(
                window_end - window_start,
                local_time[-1] - local_time[0] + median_dt,
            )
            band_payloads.append(
                {
                    "frequency_low_hz": low_hz,
                    "frequency_high_hz": high_hz,
                    "frequency_center_hz": 0.5 * (low_hz + high_hz),
                    "window_start_time_s": float(window_start),
                    "window_end_time_s": float(window_end),
                    "window_duration_s": float(window_end - window_start),
                    "window_truth_sample_denominator": truth_count,
                    "evaluated_sample_count": evaluated_count,
                    "evaluated_sample_fraction": float(evaluated_count / truth_count),
                    "evaluated_start_time_s": float(local_time[0]),
                    "evaluated_end_time_s": float(local_time[-1]),
                    "evaluated_time_coverage_fraction": float(
                        elapsed_coverage / (window_end - window_start)
                    ),
                    "projection_weight_denominator_s": weight_sum,
                    "local_time": local_time,
                    "local_reference": local_reference,
                    "local_output": local_output,
                    "reference_coefficient": reference_coefficient,
                    "normalized_projection": normalized_projection,
                }
            )

        unwrapped_phase = np.unwrap(np.angle(ratios), axis=0)
        centers_hz = np.asarray(
            [payload["frequency_center_hz"] for payload in band_payloads],
            dtype=float,
        )
        group_delay = -np.gradient(
            unwrapped_phase,
            2.0 * np.pi * centers_hz,
            axis=0,
            edge_order=2 if band_count >= 3 else 1,
        )
        for band_index, payload in enumerate(band_payloads):
            local_time = payload.pop("local_time")
            local_reference = payload.pop("local_reference")
            local_output = payload.pop("local_output")
            reference_coefficient = payload.pop("reference_coefficient")
            normalized_projection = payload.pop("normalized_projection")
            evaluated_count = int(payload["evaluated_sample_count"])
            max_lag_samples = min(
                evaluated_count - 1,
                int(math.floor(max_local_lag_s / median_dt + 1e-12)),
            )
            minimum_overlap = max(
                2,
                int(math.ceil(evaluated_count * minimum_lag_overlap_fraction)),
            )
            maximum_valid_shift = min(
                max_lag_samples, evaluated_count - minimum_overlap
            )
            lag_candidate_count = 2 * maximum_valid_shift + 1
            for joint_index, joint_id in enumerate(group.joint_ids):
                try:
                    lag = best_lag_metrics(
                        local_reference[:, joint_index],
                        local_output[:, joint_index],
                        local_time,
                        max_lag_s=max_local_lag_s,
                        minimum_overlap_fraction=minimum_lag_overlap_fraction,
                    )
                except MetricValidationError as error:
                    raise DiagnosticValidationError(
                        f"chirp local delay failed in band {band_index}, "
                        f"joint {joint_id}: {error}"
                    ) from error
                lag_samples = int(lag["lag_samples"])
                overlap_count = evaluated_count - abs(lag_samples)
                phase_value = float(unwrapped_phase[band_index, joint_index])
                frequency_center = float(payload["frequency_center_hz"])
                records.append(
                    {
                        **_base_record(group),
                        "output_field": output_field,
                        "chirp_metadata_kind": str(kind),
                        "chirp_start_hz": start_hz,
                        "chirp_end_hz": end_hz,
                        "chirp_duration_s": duration_s,
                        "chirp_observed_truth_duration_s": observed_duration,
                        "frequency_band_index": band_index,
                        "frequency_band_count": band_count,
                        **payload,
                        "trajectory_sample_count_denominator": group.n_samples,
                        "truth_aligned_sample_count": int(output_time.size),
                        "future_tail_excluded_sample_count": excluded_count,
                        "joint_index": joint_index,
                        "joint_id": joint_id,
                        "reference_projection_magnitude": float(
                            abs(reference_coefficient[joint_index])
                        ),
                        "reference_projection_normalized": float(
                            normalized_projection[joint_index]
                        ),
                        "gain": float(abs(ratios[band_index, joint_index])),
                        "phase_rad": phase_value,
                        "phase_delay_s": float(
                            -phase_value / (2.0 * np.pi * frequency_center)
                        ),
                        "group_delay_s": float(group_delay[band_index, joint_index]),
                        "local_delay_samples": lag_samples,
                        "local_delay_s": float(lag["lag_s"]),
                        "local_delay_aligned_rmse": float(lag["lag_aligned_rmse"]),
                        "local_delay_overlap_count": overlap_count,
                        "local_delay_overlap_denominator": evaluated_count,
                        "local_delay_overlap_fraction": float(
                            overlap_count / evaluated_count
                        ),
                        "local_delay_candidate_count": lag_candidate_count,
                        "max_local_lag_s": max_local_lag_s,
                        "minimum_lag_overlap_fraction": (minimum_lag_overlap_fraction),
                    }
                )
    return _ensure_rectangular_finite(
        records, label="synthetic chirp frequency response"
    )


def synthetic_local_delay(
    samples: Iterable[Mapping[str, Any]],
    *,
    output_field: str = "command_p",
    event_types: Sequence[str] = ("reversal", "stop"),
    stop_threshold: float | None = None,
    minimum_separation_s: float = 0.02,
    window_before_s: float = 0.1,
    window_after_s: float = 0.1,
    max_lag_s: float = 0.05,
) -> list[dict[str, Any]]:
    """Detect truth-only reversal/stops and wrap the existing local-delay metric."""

    allowed = {"reversal", "stop"}
    selected_types = set(event_types)
    if not selected_types or not selected_types <= allowed:
        raise DiagnosticValidationError(
            "event_types must be a non-empty subset of reversal and stop"
        )
    records: list[dict[str, Any]] = []
    for group in _aligned_groups(samples):
        _require_synthetic_truth(group)
        velocity_truth = _matrix(group, "v_ref_truth")
        try:
            detected = detect_reference_events(
                group.control_time,
                velocity_truth,
                stop_threshold=stop_threshold,
                minimum_separation_s=minimum_separation_s,
            )
        except MetricValidationError as error:
            raise DiagnosticValidationError(
                f"reference event detection failed: {error}"
            ) from error
        output_time, reference, output, _ = _tracking_arrays(group, output_field)
        events = [
            event
            for event in detected
            if str(event["event_type"]) in selected_types
            and float(event["time_s"])
            >= float(output_time[0]) + window_before_s - 1e-12
            and float(event["time_s"])
            <= float(output_time[-1]) - window_after_s + 1e-12
        ]
        if not events:
            raise DiagnosticValidationError(
                f"trajectory {group.identity['trajectory_id']} has no requested "
                "truth-defined reversal/stop events"
            )
        try:
            delays = local_delay_metrics(
                reference,
                output,
                output_time,
                events,
                window_before_s=window_before_s,
                window_after_s=window_after_s,
                max_lag_s=max_lag_s,
            )
        except MetricValidationError as error:
            raise DiagnosticValidationError(
                f"local delay failed for {group.identity['trajectory_id']}: {error}"
            ) from error
        for row in delays:
            joint_index = int(row["joint_index"])
            records.append(
                {
                    **_base_record(group),
                    "output_field": output_field,
                    "event_id": str(row["event_id"]),
                    "event_type": str(row["event_type"]),
                    "event_time_s": float(row["event_time_s"]),
                    "joint_index": joint_index,
                    "joint_id": group.joint_ids[joint_index],
                    "lag_samples": int(row["lag_samples"]),
                    "lag_s": float(row["lag_s"]),
                    "lag_aligned_rmse": float(row["lag_aligned_rmse"]),
                    "window_before_s": window_before_s,
                    "window_after_s": window_after_s,
                    "max_lag_s": max_lag_s,
                }
            )
    return _ensure_rectangular_finite(records, label="synthetic local delay")


__all__ = [
    "DiagnosticValidationError",
    "governor_invariant_summaries",
    "real_replay_diagnostics",
    "robustness_fault_events",
    "robustness_recovery_summaries",
    "synthetic_chirp_frequency_response",
    "synthetic_frequency_response",
    "synthetic_local_delay",
]
