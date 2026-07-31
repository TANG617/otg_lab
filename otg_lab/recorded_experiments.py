"""Shared declarations and audits for recorded finite-difference experiments."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from .analysis import AVAILABLE, MetricRow
from .components import configured_limit_project_target_state
from .constraints import ruckig_target_admissible
from .governors import MotionLimits as NumericalMotionLimits
from .models import ComponentSpec, TrackingMethodSpec, TrackingRun
from .trajectory_ablation import BASELINE_METHOD_ID, build_state_target_methods

FINITE_DIFFERENCE_SUFFIXES = (
    "est_backward_o1_k",
    "est_backward_o2_k",
    "est_centered_o2_km1",
    "pred_backward_o1_kp1",
    "pred_backward_o2_kp1",
)


def finite_difference_method_ids(target_components: str) -> tuple[str, ...]:
    """Return the stable five-method causal finite-difference ordering."""

    prefix = str(target_components).strip().lower()
    if prefix not in {"pv", "pva"}:
        raise ValueError("target_components must be 'pv' or 'pva'")
    return tuple(f"{prefix}_{suffix}" for suffix in FINITE_DIFFERENCE_SUFFIXES)


def projected_state_target_methods(
    target_components: str,
    *,
    include_baseline: bool,
) -> tuple[TrackingMethodSpec, ...]:
    """Build E04/E06-compatible methods with configured-limit projection."""

    shared = build_state_target_methods(
        target_components,
        include_truth=False,
        include_differences=True,
    )
    expected = finite_difference_method_ids(target_components)
    observed = tuple(
        method.method_id
        for method in shared
        if method.method_id != BASELINE_METHOD_ID
    )
    if observed != expected:
        raise RuntimeError(
            f"{target_components.upper()} finite-difference declarations changed"
        )
    governor = ComponentSpec("configured_limit_projection")
    return tuple(
        replace(method, governor=governor, required=True)
        for method in shared
        if include_baseline or method.method_id != BASELINE_METHOD_ID
    )


def value_token(value: float) -> str:
    """Encode a numeric factor as a stable case-ID token."""

    return f"{float(value):g}".replace("-", "m").replace(".", "p")


def metric_lookup(
    rows: Sequence[MetricRow],
) -> dict[tuple[str, str, str, str], MetricRow]:
    return {
        (row.method_id, row.input_id, row.window_id, row.metric_id): row
        for row in rows
    }


def metric_value(
    lookup: Mapping[tuple[str, str, str, str], MetricRow],
    input_id: str,
    case_id: str,
    metric_id: str,
    window_id: str,
) -> float | None:
    row = lookup.get((case_id, input_id, window_id, metric_id))
    if (
        row is None
        or row.status != AVAILABLE
        or row.value is None
        or isinstance(row.value, bool)
    ):
        return None
    value = float(row.value)
    return value if math.isfinite(value) else None


def successful_trace_rows(run: TrackingRun) -> list[Mapping[str, Any]]:
    return [
        row
        for row in run.trace_rows
        if str(row.get("status", "")).lower() == "ok"
        and row.get("raw_target_position_rad") is not None
        and row.get("executable_target_position_rad") is not None
    ]


def projection_audit(
    run: TrackingRun,
    limits: NumericalMotionLimits,
    *,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    """Audit projection magnitude and its velocity/acceleration/envelope causes.

    Cause counts are non-exclusive: one raw target can require both A clipping
    and a subsequent stopping-envelope adjustment.
    """

    rows = successful_trace_rows(run)
    distortions: list[tuple[float, float, float]] = []
    projected_rows: list[Mapping[str, Any]] = []
    velocity_clip_count = 0
    acceleration_clip_count = 0
    stopping_envelope_count = 0
    inadmissible_count = 0
    reconstruction_mismatch_count = 0

    max_velocity = float(limits.max_velocity[0])
    max_acceleration = float(limits.max_acceleration[0])
    for row in rows:
        raw = np.asarray(
            [
                float(row["raw_target_position_rad"]),
                float(row["raw_target_velocity_rad_s"]),
                float(row["raw_target_acceleration_rad_s2"]),
            ],
            dtype=float,
        ).reshape(1, 3)
        executable = np.asarray(
            [
                float(row["executable_target_position_rad"]),
                float(row["executable_target_velocity_rad_s"]),
                float(row["executable_target_acceleration_rad_s2"]),
            ],
            dtype=float,
        ).reshape(1, 3)
        distortion = executable - raw
        distortions.append(tuple(float(value) for value in distortion[0]))
        if np.any(np.abs(distortion) > tolerance):
            projected_rows.append(row)

        if abs(float(raw[0, 1])) > max_velocity + tolerance:
            velocity_clip_count += 1
        if abs(float(raw[0, 2])) > max_acceleration + tolerance:
            acceleration_clip_count += 1

        clamped = np.array(raw, copy=True)
        clamped[0, 1] = float(
            np.clip(clamped[0, 1], -max_velocity, max_velocity)
        )
        clamped[0, 2] = float(
            np.clip(clamped[0, 2], -max_acceleration, max_acceleration)
        )
        reconstructed, _ = configured_limit_project_target_state(raw, limits)
        if abs(float(reconstructed[0, 1] - clamped[0, 1])) > tolerance:
            stopping_envelope_count += 1
        if not np.allclose(
            reconstructed,
            executable,
            rtol=0.0,
            atol=tolerance,
        ):
            reconstruction_mismatch_count += 1
        if not ruckig_target_admissible(executable, limits):
            inadmissible_count += 1

    distortion_array = np.asarray(distortions, dtype=float)
    if distortion_array.size == 0:
        distortion_array = np.empty((0, 3), dtype=float)
    first = projected_rows[0] if projected_rows else None
    denominator = run.status.total_cycles

    def _rmse(column: int) -> float | None:
        if not distortion_array.shape[0]:
            return None
        return float(np.sqrt(np.mean(distortion_array[:, column] ** 2)))

    def _max_abs(column: int) -> float | None:
        if not distortion_array.shape[0]:
            return None
        return float(np.max(np.abs(distortion_array[:, column])))

    return {
        "projection_count": len(projected_rows),
        "projection_rate": (
            None if denominator <= 0 else len(projected_rows) / denominator
        ),
        "first_projection_cycle_index": (
            None if first is None else first.get("cycle_index")
        ),
        "first_projection_measurement_time_s": (
            None if first is None else first.get("measurement_time_s")
        ),
        "first_projection_command_time_s": (
            None if first is None else first.get("command_time_s")
        ),
        "position_projection_max_abs_rad": _max_abs(0),
        "velocity_projection_rmse_rad_s": _rmse(1),
        "velocity_projection_max_abs_rad_s": _max_abs(1),
        "acceleration_projection_rmse_rad_s2": _rmse(2),
        "acceleration_projection_max_abs_rad_s2": _max_abs(2),
        "velocity_clip_count": velocity_clip_count,
        "acceleration_clip_count": acceleration_clip_count,
        "stopping_envelope_count": stopping_envelope_count,
        "executable_target_inadmissible_count": inadmissible_count,
        "projection_reconstruction_mismatch_count": (
            reconstruction_mismatch_count
        ),
    }


__all__ = [
    "BASELINE_METHOD_ID",
    "FINITE_DIFFERENCE_SUFFIXES",
    "finite_difference_method_ids",
    "metric_lookup",
    "metric_value",
    "projected_state_target_methods",
    "projection_audit",
    "successful_trace_rows",
    "value_token",
]
