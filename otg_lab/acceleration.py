"""Acceleration-active continuous truth used by the oracle component study."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np

from .benchmarks import ACCELERATION_ACTIVE_PHASES, DEFAULT_RATIO_STRATA
from .datasets import ContinuousTrajectory, MotionLimits, assert_truth_constraints


@dataclass(frozen=True)
class AccelerationCase:
    trajectory: ContinuousTrajectory
    phase: str
    r_a_stratum: str
    r_j_stratum: str
    target_r_a: float
    target_r_j: float
    direction: int


_TEMPLATES: dict[str, tuple[tuple[int, float, float], ...]] = {
    "constant_acceleration": ((1, 1.0, 0.0), (3, 1.0 / 9.0, 0.0)),
    "acceleration_sign_reversal": ((1, 1.0, 0.0),),
    "rapid_braking": ((1, 1.0, math.pi / 3.0), (2, 0.45, -math.pi / 5.0)),
    "near_amax": ((1, 1.0, math.pi / 2.0), (3, 0.08, math.pi / 2.0)),
    "nonzero_acceleration_at_moving_target": ((1, 1.0, math.pi / 4.0),),
    "high_jerk_feasible": ((1, 1.0, 0.0), (5, 0.22, math.pi / 7.0)),
    "stop_restart": ((1, 1.0, 0.0), (2, -0.35, 0.0), (3, 0.12, 0.0)),
}
_DEFAULT_LIMITS = MotionLimits()


@cache
def _template_extrema(template: tuple[tuple[int, float, float], ...]) -> tuple[float, float]:
    theta = np.linspace(0.0, 2.0 * math.pi, 200_001)
    acceleration = np.zeros_like(theta)
    derivative = np.zeros_like(theta)
    for harmonic, coefficient, phase in template:
        acceleration += coefficient * np.sin(harmonic * theta + phase)
        derivative += coefficient * harmonic * np.cos(harmonic * theta + phase)
    return float(np.max(np.abs(acceleration))), float(np.max(np.abs(derivative)))


def generate_acceleration_case(
    phase: str,
    r_a_stratum: str,
    r_j_stratum: str,
    direction: int,
    *,
    limits: MotionLimits = _DEFAULT_LIMITS,
) -> AccelerationCase:
    """Generate bounded analytic p/v/a/j with independently set a/j demand."""

    if phase not in ACCELERATION_ACTIVE_PHASES:
        raise ValueError(f"unknown acceleration phase {phase}")
    if r_a_stratum not in DEFAULT_RATIO_STRATA or r_j_stratum not in DEFAULT_RATIO_STRATA:
        raise ValueError("unknown acceleration/jerk stratum")
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or +1")
    target_a = float(DEFAULT_RATIO_STRATA[r_a_stratum]) * limits.max_acceleration
    target_j = float(DEFAULT_RATIO_STRATA[r_j_stratum]) * limits.max_jerk
    template = _TEMPLATES[phase]
    acceleration_peak, derivative_peak = _template_extrema(template)
    scale = target_a / acceleration_peak
    omega = target_j / (scale * derivative_peak)
    period = 2.0 * math.pi / omega
    duration = min(4.0, max(0.6, 4.0 * period))
    internal_dt = min(0.0005, period / 200.0)
    sample_count = int(math.ceil(duration / internal_dt))
    time = np.linspace(0.0, duration, sample_count + 1)
    theta = omega * time
    acceleration = np.zeros_like(time)
    jerk = np.zeros_like(time)
    velocity = np.zeros_like(time)
    position = np.zeros_like(time)
    for harmonic, coefficient, harmonic_phase in template:
        argument = harmonic * theta + harmonic_phase
        normalized = coefficient / acceleration_peak
        acceleration += target_a * normalized * np.sin(argument)
        jerk += target_a * normalized * harmonic * omega * np.cos(argument)
        velocity -= (
            target_a
            * normalized
            / (harmonic * omega)
            * np.cos(argument)
        )
        position -= (
            target_a
            * normalized
            / (harmonic * omega) ** 2
            * (np.sin(argument) - math.sin(harmonic_phase))
        )
    if phase == "nonzero_acceleration_at_moving_target":
        requested_offset = 0.15 * limits.max_velocity
        available = max(0.0, limits.max_velocity - float(np.max(np.abs(velocity))))
        velocity_offset = min(requested_offset, 0.8 * available)
        velocity += velocity_offset
        position += velocity_offset * time
    position *= direction
    velocity *= direction
    acceleration *= direction
    jerk *= direction
    seed = (
        950_000
        + 10_000 * ACCELERATION_ACTIVE_PHASES.index(phase)
        + 100 * list(DEFAULT_RATIO_STRATA).index(r_a_stratum)
        + 10 * list(DEFAULT_RATIO_STRATA).index(r_j_stratum)
        + (1 if direction > 0 else 0)
    )
    trajectory_id = (
        f"accel-{phase}-ra-{r_a_stratum}-rj-{r_j_stratum}-"
        f"dir-{'pos' if direction > 0 else 'neg'}"
    )
    trajectory = ContinuousTrajectory(
        trajectory_id=trajectory_id,
        family="acceleration_active",
        split="test",
        seed=seed,
        demand_stratum=f"ra_{r_a_stratum}__rj_{r_j_stratum}",
        time=time,
        position=position,
        velocity=velocity,
        acceleration=acceleration,
        jerk=jerk,
        internal_dt=float(np.max(np.diff(time))),
        reference_variant=phase,
    )
    assert_truth_constraints(trajectory, limits=limits)
    actual = trajectory.demand_ratios(limits)
    if not math.isclose(actual["r_a"], float(DEFAULT_RATIO_STRATA[r_a_stratum]), rel_tol=2e-3):
        raise RuntimeError("acceleration-active r_a construction missed its target")
    if not math.isclose(actual["r_j"], float(DEFAULT_RATIO_STRATA[r_j_stratum]), rel_tol=2e-3):
        raise RuntimeError("acceleration-active r_j construction missed its target")
    return AccelerationCase(
        trajectory,
        phase,
        r_a_stratum,
        r_j_stratum,
        float(DEFAULT_RATIO_STRATA[r_a_stratum]),
        float(DEFAULT_RATIO_STRATA[r_j_stratum]),
        direction,
    )


def _configured_ratio_labels(values: Sequence[float], name: str) -> tuple[str, ...]:
    labels = []
    for raw_value in values:
        value = float(raw_value)
        matches = [
            label
            for label, declared in DEFAULT_RATIO_STRATA.items()
            if math.isclose(value, float(declared), rel_tol=0.0, abs_tol=1e-12)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{name} value {value:g} has no prevalidated acceleration stratum"
            )
        labels.append(matches[0])
    if not labels or len(set(labels)) != len(labels):
        raise ValueError(f"{name} values must be non-empty and unique")
    return tuple(labels)


def acceleration_case_matrix(
    *,
    phases: Sequence[str] = ACCELERATION_ACTIVE_PHASES,
    r_a_values: Sequence[float] = tuple(DEFAULT_RATIO_STRATA.values()),
    r_j_values: Sequence[float] = tuple(DEFAULT_RATIO_STRATA.values()),
    directions: Sequence[int] = (-1, 1),
) -> list[AccelerationCase]:
    """Return the complete config-declared acceleration-active design."""

    normalized_phases = tuple(str(value) for value in phases)
    if set(normalized_phases) != set(ACCELERATION_ACTIVE_PHASES):
        raise ValueError("acceleration phase design must contain all declared phases")
    normalized_directions = tuple(int(value) for value in directions)
    if set(normalized_directions) != {-1, 1} or len(normalized_directions) != 2:
        raise ValueError("acceleration directions must be the unique {-1, +1} pair")
    r_a_labels = _configured_ratio_labels(r_a_values, "r_a")
    r_j_labels = _configured_ratio_labels(r_j_values, "r_j")

    return [
        generate_acceleration_case(phase, r_a, r_j, direction)
        for phase in normalized_phases
        for r_a in r_a_labels
        for r_j in r_j_labels
        for direction in normalized_directions
    ]


def acceleration_case_metadata(case: AccelerationCase) -> dict[str, Any]:
    return {
        "trajectory_id": case.trajectory.trajectory_id,
        "phase": case.phase,
        "r_a_stratum": case.r_a_stratum,
        "r_j_stratum": case.r_j_stratum,
        "r_a": case.target_r_a,
        "r_j": case.target_r_j,
        "direction": case.direction,
    }


__all__ = [
    "AccelerationCase",
    "acceleration_case_matrix",
    "acceleration_case_metadata",
    "generate_acceleration_case",
]
