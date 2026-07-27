"""Production-semantics centered-difference target-state diagnostics.

The existing ``pva_central_causal`` experiment propagates a centered estimate
to the latest sample and keeps the latest position.  The production estimator
described by the controller instead emits the complete state at the middle
sample after the newest sample arrives.  This module keeps those semantics
explicit so delay, derivative age, hard clamps, and Ruckig feasibility can be
ablated independently.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from target_state_experiment import MotionLimits


@dataclass(frozen=True)
class CenteredEstimate:
    """Derivative estimates indexed by arrival sample.

    At index ``k``, a valid entry is the derivative at ``k - 1`` calculated
    from samples ``k - 2``, ``k - 1``, and ``k``.
    """

    velocity: np.ndarray
    acceleration: np.ndarray
    valid: np.ndarray
    center_index: np.ndarray
    h0: np.ndarray
    h1: np.ndarray


@dataclass(frozen=True)
class TargetMethod:
    method_id: str
    label: str
    position_semantics: str
    derivative_semantics: str
    hard_clamp: bool
    causal: bool
    primary: bool = True


@dataclass(frozen=True)
class BuiltTargets:
    method: TargetMethod
    states: np.ndarray
    preclamp_states: np.ndarray
    estimate_valid: np.ndarray
    velocity_clamp_mask: np.ndarray
    acceleration_clamp_mask: np.ndarray
    target_age_s: np.ndarray
    state_age_position_residual: np.ndarray


METHODS = (
    TargetMethod(
        "p_only_latest",
        "P-only · latest P",
        "latest",
        "zero",
        False,
        True,
    ),
    TargetMethod(
        "p_only_delayed",
        "P-only · delayed P",
        "middle_delay1",
        "zero",
        False,
        True,
    ),
    TargetMethod(
        "centered_pva_delayed_unclamped",
        "Centered PVA · delayed · no hard clamp",
        "middle_delay1",
        "centered_middle",
        False,
        True,
    ),
    TargetMethod(
        "centered_pv_delayed_clamped",
        "Centered PV · delayed · hard clamp",
        "middle_delay1",
        "centered_middle_velocity",
        True,
        True,
    ),
    TargetMethod(
        "centered_pva_delayed_clamped",
        "Centered PVA · production-like",
        "middle_delay1",
        "centered_middle",
        True,
        True,
    ),
    TargetMethod(
        "centered_pva_latest_position_clamped",
        "Centered PVA · latest P",
        "latest",
        "centered_middle",
        True,
        True,
    ),
    TargetMethod(
        "centered_pva_propagated_clamped",
        "Centered PVA · P/V age compensated",
        "latest",
        "centered_propagated",
        True,
        True,
    ),
    TargetMethod(
        "centered_pva_offline_aligned_clamped",
        "Centered PVA · offline aligned",
        "latest",
        "centered_offline",
        True,
        False,
        primary=False,
    ),
)
METHOD_BY_ID = {method.method_id: method for method in METHODS}


def _validated_inputs(position, timestamps):
    position = np.asarray(position, dtype=float)
    timestamps = np.asarray(timestamps, dtype=float)
    if position.ndim != 1 or position.size < 4:
        raise ValueError("position must be a one-dimensional array of length >= 4")
    if timestamps.shape != position.shape:
        raise ValueError("timestamps must have the same shape as position")
    if not np.all(np.isfinite(position)) or not np.all(np.isfinite(timestamps)):
        raise ValueError("position and timestamps must contain only finite values")
    timestamps = timestamps - timestamps[0]
    if np.any(np.diff(timestamps) <= 0.0):
        raise ValueError("timestamps must be strictly increasing")
    return position, timestamps


def centered_difference_nonuniform(
    position,
    timestamps,
    *,
    max_sample_interval_s=0.05,
):
    """Reproduce the three-point nonuniform centered estimator.

    The positive coefficient on ``q2`` is intentional.  It is required by
    Lagrange interpolation and reduces to ``(q2 - q0) / (2h)`` for equal
    spacing.  Likewise, the final acceleration coefficient is positive.
    """

    position, timestamps = _validated_inputs(position, timestamps)
    if max_sample_interval_s <= 0.0:
        raise ValueError("max_sample_interval_s must be positive")

    count = position.size
    velocity = np.zeros(count, dtype=float)
    acceleration = np.zeros(count, dtype=float)
    valid = np.zeros(count, dtype=bool)
    center_index = np.full(count, -1, dtype=int)
    h0_values = np.full(count, np.nan, dtype=float)
    h1_values = np.full(count, np.nan, dtype=float)

    segment_samples = 1
    for index in range(1, count):
        interval = timestamps[index] - timestamps[index - 1]
        if interval > max_sample_interval_s:
            segment_samples = 1
            continue
        segment_samples += 1
        if segment_samples < 3:
            continue

        h0 = timestamps[index - 1] - timestamps[index - 2]
        h1 = interval
        q0, q1, q2 = position[index - 2 : index + 1]
        velocity[index] = (
            -h1 / (h0 * (h0 + h1)) * q0
            + (h1 - h0) / (h0 * h1) * q1
            + h0 / (h1 * (h0 + h1)) * q2
        )
        acceleration[index] = (
            2.0 / (h0 * (h0 + h1)) * q0
            - 2.0 / (h0 * h1) * q1
            + 2.0 / (h1 * (h0 + h1)) * q2
        )
        valid[index] = True
        center_index[index] = index - 1
        h0_values[index] = h0
        h1_values[index] = h1

    return CenteredEstimate(
        velocity=velocity,
        acceleration=acceleration,
        valid=valid,
        center_index=center_index,
        h0=h0_values,
        h1=h1_values,
    )


def _hold_invalid_targets(states, valid, initial_position):
    last_state = np.array([initial_position, 0.0, 0.0], dtype=float)
    for index in range(states.shape[0]):
        if valid[index]:
            last_state = states[index].copy()
        else:
            states[index] = last_state


def build_ablation_targets(
    position,
    timestamps,
    method,
    limits,
    *,
    max_sample_interval_s=0.05,
):
    """Build one target-state sequence for a centered-PVA ablation."""

    position, timestamps = _validated_inputs(position, timestamps)
    if isinstance(method, str):
        method = METHOD_BY_ID[method]
    if not isinstance(method, TargetMethod):
        raise TypeError("method must be a method id or TargetMethod")
    if not isinstance(limits, MotionLimits):
        raise TypeError("limits must be MotionLimits")

    count = position.size
    estimate = centered_difference_nonuniform(
        position,
        timestamps,
        max_sample_interval_s=max_sample_interval_s,
    )
    valid = np.ones(count, dtype=bool)
    states = np.zeros((count, 3), dtype=float)
    states[:, 0] = position
    target_age_s = np.zeros(count, dtype=float)

    if method.method_id == "p_only_latest":
        pass
    elif method.method_id == "p_only_delayed":
        valid = estimate.valid.copy()
        states[:, 0] = position[0]
        valid_indices = np.flatnonzero(valid)
        states[valid_indices, 0] = position[valid_indices - 1]
        target_age_s[valid_indices] = (
            timestamps[valid_indices] - timestamps[valid_indices - 1]
        )
        _hold_invalid_targets(states, valid, position[0])
    elif method.derivative_semantics == "centered_offline":
        valid = np.zeros(count, dtype=bool)
        for arrival in np.flatnonzero(estimate.valid):
            center = estimate.center_index[arrival]
            states[center] = [
                position[center],
                estimate.velocity[arrival],
                estimate.acceleration[arrival],
            ]
            valid[center] = True
        _hold_invalid_targets(states, valid, position[0])
    else:
        valid = estimate.valid.copy()
        states[:, 0] = position[0]
        valid_indices = np.flatnonzero(valid)
        center_indices = valid_indices - 1
        if method.position_semantics == "middle_delay1":
            states[valid_indices, 0] = position[center_indices]
            target_age_s[valid_indices] = (
                timestamps[valid_indices] - timestamps[center_indices]
            )
        else:
            states[valid_indices, 0] = position[valid_indices]

        states[valid_indices, 1] = estimate.velocity[valid_indices]
        if method.derivative_semantics != "centered_middle_velocity":
            states[valid_indices, 2] = estimate.acceleration[valid_indices]
        if method.derivative_semantics == "centered_propagated":
            states[valid_indices, 1] += (
                estimate.acceleration[valid_indices]
                * estimate.h1[valid_indices]
            )
        _hold_invalid_targets(states, valid, position[0])

    preclamp_states = states.copy()
    velocity_clamp_mask = np.zeros(count, dtype=bool)
    acceleration_clamp_mask = np.zeros(count, dtype=bool)
    if method.hard_clamp:
        velocity_clamp_mask = (
            np.abs(states[:, 1]) > limits.max_velocity
        ) & valid
        acceleration_clamp_mask = (
            np.abs(states[:, 2]) > limits.max_acceleration
        ) & valid
        states[:, 1] = np.clip(
            states[:, 1], -limits.max_velocity, limits.max_velocity
        )
        states[:, 2] = np.clip(
            states[:, 2], -limits.max_acceleration, limits.max_acceleration
        )

    state_age_position_residual = np.full(count, np.nan, dtype=float)
    for index in np.flatnonzero(valid & (target_age_s > 0.0)):
        age = target_age_s[index]
        predicted_latest = (
            states[index, 0]
            + states[index, 1] * age
            + 0.5 * states[index, 2] * age**2
        )
        state_age_position_residual[index] = predicted_latest - position[index]

    return BuiltTargets(
        method=method,
        states=states,
        preclamp_states=preclamp_states,
        estimate_valid=valid,
        velocity_clamp_mask=velocity_clamp_mask,
        acceleration_clamp_mask=acceleration_clamp_mask,
        target_age_s=target_age_s,
        state_age_position_residual=state_age_position_residual,
    )
