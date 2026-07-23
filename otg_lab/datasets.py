"""Deterministic synthetic benchmark generation and replayable stress suites.

Clean references are generated on an internal grid of at least 1 kHz, with
analytic derivatives where practical, and only then sampled at an experiment
rate.  Split membership and seeds are supplied by ``split_manifest.json``;
there is intentionally no random split routine in this module.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .schema import empty_sample, validate_samples

DEFAULT_LIMITS = {"max_velocity": 4.1, "max_acceleration": 8.2, "max_jerk": 4000.0}
DEFAULT_INTERNAL_DT = 0.001
DEFAULT_CONTROL_DT = 0.010
FAMILIES = (
    "stationary_endpoint",
    "oscillatory",
    "piecewise_constant_jerk",
    "stop_and_go",
    "rapid_reversal",
    "boundary_grazing",
)
DEMAND_TARGETS = {"low": 0.20, "medium": 0.50, "high": 0.75, "near_limit": 0.93}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPLIT_MANIFEST = ROOT / "split_manifest.json"


@dataclass(frozen=True)
class MotionLimits:
    max_velocity: float = 4.1
    max_acceleration: float = 8.2
    max_jerk: float = 4000.0

    def __post_init__(self) -> None:
        if min(self.max_velocity, self.max_acceleration, self.max_jerk) <= 0.0:
            raise ValueError("all motion limits must be positive")


DEFAULT_MOTION_LIMITS = MotionLimits()


@dataclass(frozen=True)
class SplitEntry:
    trajectory_id: str
    family: str
    split: str
    seed: int
    demand_stratum: str
    locked: bool


@dataclass(frozen=True)
class ContinuousTrajectory:
    """High-resolution scalar reference with genuine p/v/a/j truth."""

    trajectory_id: str
    family: str
    split: str
    seed: int
    demand_stratum: str
    time: np.ndarray
    position: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray
    jerk: np.ndarray
    internal_dt: float
    reference_variant: str | None = None
    reference_frequency_spec_json: str | None = None
    intentionally_infeasible: bool = False

    def __post_init__(self) -> None:
        arrays = (self.time, self.position, self.velocity, self.acceleration, self.jerk)
        lengths = {np.asarray(array).shape for array in arrays}
        if len(lengths) != 1 or len(next(iter(lengths))) != 1:
            raise ValueError(
                "trajectory arrays must be equally sized one-dimensional arrays"
            )
        if self.time.size < 2:
            raise ValueError("trajectory must contain at least two samples")
        if self.internal_dt > 0.001 + 1e-15 or self.internal_dt <= 0.0:
            raise ValueError("internal truth grid must be at least 1 kHz")
        if not all(np.all(np.isfinite(array)) for array in arrays):
            raise ValueError("clean trajectory truth must be finite")
        if not np.all(np.diff(self.time) > 0.0):
            raise ValueError("truth time must be strictly increasing")
        if self.reference_frequency_spec_json is not None:
            try:
                frequency_spec = json.loads(self.reference_frequency_spec_json)
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError(
                    "reference frequency specification is invalid JSON"
                ) from error
            if not isinstance(frequency_spec, dict) or not frequency_spec.get("kind"):
                raise ValueError("reference frequency specification lacks a kind")

    @property
    def duration(self) -> float:
        return float(self.time[-1] - self.time[0])

    def demand_ratios(
        self, limits: MotionLimits = DEFAULT_MOTION_LIMITS
    ) -> dict[str, float]:
        return {
            "r_v": float(np.max(np.abs(self.velocity)) / limits.max_velocity),
            "r_a": float(np.max(np.abs(self.acceleration)) / limits.max_acceleration),
            "r_j": float(np.max(np.abs(self.jerk)) / limits.max_jerk),
        }


@dataclass(frozen=True)
class SampledTruth:
    time: np.ndarray
    position: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray
    jerk: np.ndarray
    sample_rate_hz: float


@dataclass(frozen=True)
class StressConfig:
    kind: str
    scenario_id: str
    seed: int
    noise_std: float = 0.0
    ar_coefficient: float = 0.0
    resolution: float | None = None
    jitter_std_s: float = 0.0
    drop_probability: float = 0.0
    burst_start: int | None = None
    burst_length: int = 0
    duplicate_index: int | None = None
    regression_index: int | None = None
    regression_s: float = 0.0
    outlier_kind: str | None = None
    outlier_index: int | None = None
    outlier_magnitude: float = 0.0


def load_split_manifest(path: str | Path = DEFAULT_SPLIT_MANIFEST) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    validate_split_manifest(manifest)
    return manifest


def split_entries(manifest: Mapping[str, Any]) -> list[SplitEntry]:
    return [SplitEntry(**item) for item in manifest["trajectories"]]


def validate_split_manifest(manifest: Mapping[str, Any]) -> None:
    """Enforce trajectory-level isolation and the frozen 20/10/20 design."""

    if manifest.get("manifest_version") != 1:
        raise ValueError("unsupported split manifest version")
    if tuple(manifest.get("families", ())) != FAMILIES:
        raise ValueError("split manifest family order/content does not match generator")
    raw_entries = manifest.get("trajectories")
    if not isinstance(raw_entries, list):
        raise ValueError("split manifest trajectories must be a list")
    identities: set[str] = set()
    seeds: set[tuple[str, int]] = set()
    counts = {
        (family, split): 0
        for family in FAMILIES
        for split in ("train", "validation", "test")
    }
    for raw in raw_entries:
        entry = SplitEntry(**raw)
        if entry.trajectory_id in identities:
            raise ValueError(f"duplicate trajectory ID: {entry.trajectory_id}")
        identities.add(entry.trajectory_id)
        seed_key = (entry.family, entry.seed)
        if seed_key in seeds:
            raise ValueError(f"duplicate family/seed pair: {seed_key}")
        seeds.add(seed_key)
        if entry.family not in FAMILIES:
            raise ValueError(f"unknown family: {entry.family}")
        if entry.split not in ("train", "validation", "test"):
            raise ValueError(f"invalid clean split: {entry.split}")
        if entry.demand_stratum not in DEMAND_TARGETS:
            raise ValueError(f"invalid demand stratum: {entry.demand_stratum}")
        if entry.locked != (entry.split == "test"):
            raise ValueError(
                "only test trajectories may be locked, and all test rows must be"
            )
        counts[(entry.family, entry.split)] += 1
    expected = {"train": 20, "validation": 10, "test": 20}
    for (family, split), count in counts.items():
        if count != expected[split]:
            raise ValueError(
                f"{family}/{split}: expected {expected[split]}, got {count}"
            )
    if len(raw_entries) < 300:
        raise ValueError("clean benchmark must contain at least 300 trajectories")


def validate_fresh_locked_test_manifest(
    candidate_path: str | Path,
    *,
    exposed_manifest_paths: Sequence[str | Path],
) -> None:
    """Reject a confirmation manifest that reuses any exposed identity.

    Both trajectory IDs and family/seed pairs are compared.  Checking only IDs
    would allow a cosmetic rename of a previously inspected trajectory, while
    checking only seeds would permit an accidental identity collision.
    """

    candidate = load_split_manifest(candidate_path)
    candidate_dataset = candidate.get("dataset_id")
    if not isinstance(candidate_dataset, str) or not candidate_dataset:
        raise ValueError("candidate manifest must declare dataset_id")
    candidate_test = [
        SplitEntry(**item)
        for item in candidate["trajectories"]
        if item.get("split") == "test"
    ]
    candidate_ids = {entry.trajectory_id for entry in candidate_test}
    candidate_seeds = {(entry.family, entry.seed) for entry in candidate_test}
    if not exposed_manifest_paths:
        raise ValueError("fresh locked test requires at least one exposed manifest")
    for exposed_path in exposed_manifest_paths:
        exposed = load_split_manifest(exposed_path)
        if exposed.get("dataset_id") == candidate_dataset:
            raise ValueError(
                "fresh locked test dataset_id must differ from exposed dataset_id"
            )
        # A v2 confirmation trajectory must be unseen, not merely absent from
        # the older test partition.  Development train/validation trajectories
        # may already have been generated or inspected, so compare against the
        # complete exposed manifest.
        exposed_entries = [SplitEntry(**item) for item in exposed["trajectories"]]
        reused_ids = candidate_ids & {entry.trajectory_id for entry in exposed_entries}
        reused_seeds = candidate_seeds & {
            (entry.family, entry.seed) for entry in exposed_entries
        }
        if reused_ids or reused_seeds:
            raise ValueError(
                "fresh locked test reuses exposed trajectories: "
                f"ids={sorted(reused_ids)[:5]}, "
                f"family_seeds={sorted(reused_seeds)[:5]}"
            )


def entries_for_split(
    split: str,
    *,
    family: str | None = None,
    manifest_path: str | Path = DEFAULT_SPLIT_MANIFEST,
) -> list[SplitEntry]:
    entries = split_entries(load_split_manifest(manifest_path))
    return [
        entry
        for entry in entries
        if entry.split == split and (family is None or entry.family == family)
    ]


def _truth_grid(duration: float, internal_dt: float) -> np.ndarray:
    if internal_dt > 0.001 + 1e-15 or internal_dt <= 0.0:
        raise ValueError("internal_dt must be in (0, 0.001]")
    count = int(math.ceil(duration / internal_dt))
    return np.linspace(0.0, duration, count + 1, dtype=float)


def _seventh_profile(phase: np.ndarray) -> tuple[np.ndarray, ...]:
    x = np.clip(phase, 0.0, 1.0)
    p = 35 * x**4 - 84 * x**5 + 70 * x**6 - 20 * x**7
    v = 140 * x**3 - 420 * x**4 + 420 * x**5 - 140 * x**6
    a = 420 * x**2 - 1680 * x**3 + 2100 * x**4 - 840 * x**5
    j = 840 * x - 5040 * x**2 + 8400 * x**3 - 4200 * x**4
    return p, v, a, j


def _stationary_endpoint(
    rng: np.random.Generator,
    time: np.ndarray,
    duration: float,
    variant_index: int | None = None,
) -> tuple[np.ndarray, ...]:
    phase = time / duration
    selected = int(rng.integers(0, 2)) if variant_index is None else variant_index
    if selected == 0:
        p = 10 * phase**3 - 15 * phase**4 + 6 * phase**5
        v = (30 * phase**2 - 60 * phase**3 + 30 * phase**4) / duration
        a = (60 * phase - 180 * phase**2 + 120 * phase**3) / duration**2
        j = (60 - 360 * phase + 360 * phase**2) / duration**3
    else:
        p0, v0, a0, j0 = _seventh_profile(phase)
        p, v, a, j = p0, v0 / duration, a0 / duration**2, j0 / duration**3
    sign = -1.0 if rng.random() < 0.5 else 1.0
    return sign * p, sign * v, sign * a, sign * j


def _oscillatory_with_frequency_spec(
    rng: np.random.Generator,
    time: np.ndarray,
    duration: float,
    variant_index: int | None = None,
) -> tuple[tuple[np.ndarray, ...], str]:
    mode = int(rng.integers(0, 3)) if variant_index is None else variant_index
    if mode == 0:  # sine
        frequencies = np.array([rng.uniform(0.25, 1.1)])
        amplitudes = np.array([1.0])
        phases = np.array([rng.uniform(-math.pi, math.pi)])
        omega = 2 * math.pi * frequencies[:, None]
        arg = omega * time + phases[:, None]
        p = np.sum(amplitudes[:, None] * np.sin(arg), axis=0)
        v = np.sum(amplitudes[:, None] * omega * np.cos(arg), axis=0)
        a = np.sum(-amplitudes[:, None] * omega**2 * np.sin(arg), axis=0)
        j = np.sum(-amplitudes[:, None] * omega**3 * np.cos(arg), axis=0)
    elif mode == 1:  # multi-sine
        frequencies = rng.uniform(0.18, 1.4, size=3)
        amplitudes = np.array([1.0, 0.35, 0.18]) * rng.choice((-1.0, 1.0), 3)
        phases = rng.uniform(-math.pi, math.pi, size=3)
        omega = 2 * math.pi * frequencies[:, None]
        arg = omega * time + phases[:, None]
        p = np.sum(amplitudes[:, None] * np.sin(arg), axis=0)
        v = np.sum(amplitudes[:, None] * omega * np.cos(arg), axis=0)
        a = np.sum(-amplitudes[:, None] * omega**2 * np.sin(arg), axis=0)
        j = np.sum(-amplitudes[:, None] * omega**3 * np.cos(arg), axis=0)
    else:  # linear chirp, with analytic derivatives
        f0 = rng.uniform(0.15, 0.35)
        f1 = rng.uniform(1.0, 2.2)
        phi0 = rng.uniform(-math.pi, math.pi)
        beta = 2 * math.pi * (f1 - f0) / duration
        phi_prime = 2 * math.pi * f0 + beta * time
        phi = phi0 + 2 * math.pi * f0 * time + 0.5 * beta * time**2
        p = np.sin(phi)
        v = np.cos(phi) * phi_prime
        a = -np.sin(phi) * phi_prime**2 + np.cos(phi) * beta
        j = -np.cos(phi) * phi_prime**3 - 3 * np.sin(phi) * phi_prime * beta
    if mode in {0, 1}:
        specification = {
            "kind": "discrete_tones",
            "frequencies_hz": [float(value) for value in frequencies],
        }
    else:
        specification = {
            "kind": "linear_chirp",
            "start_hz": float(f0),
            "end_hz": float(f1),
            "duration_s": float(duration),
        }
    return (p, v, a, j), json.dumps(
        specification, sort_keys=True, separators=(",", ":")
    )


def _oscillatory(
    rng: np.random.Generator,
    time: np.ndarray,
    duration: float,
    variant_index: int | None = None,
) -> tuple[np.ndarray, ...]:
    arrays, _ = _oscillatory_with_frequency_spec(rng, time, duration, variant_index)
    return arrays


def _piecewise_constant_jerk(
    rng: np.random.Generator, time: np.ndarray, duration: float
) -> tuple[np.ndarray, ...]:
    dt = np.diff(time)
    segment = max(1, int(round(rng.uniform(0.025, 0.12) / np.median(dt))))
    values = rng.uniform(-1.0, 1.0, size=int(math.ceil(time.size / segment)))
    # Remove long-term jerk bias; this keeps the unscaled integration well behaved.
    values -= np.mean(values)
    jerk = np.repeat(values, segment)[: time.size]
    p = np.zeros_like(time)
    v = np.zeros_like(time)
    a = np.zeros_like(time)
    for index, step in enumerate(dt, start=1):
        j0 = jerk[index - 1]
        p[index] = (
            p[index - 1]
            + v[index - 1] * step
            + 0.5 * a[index - 1] * step**2
            + j0 * step**3 / 6.0
        )
        v[index] = v[index - 1] + a[index - 1] * step + 0.5 * j0 * step**2
        a[index] = a[index - 1] + j0 * step
    return p, v, a, jerk


def _stop_and_go(
    rng: np.random.Generator, time: np.ndarray, duration: float
) -> tuple[np.ndarray, ...]:
    # Alternating smooth moves and exact dwell intervals.
    boundaries = np.array([0.0, 0.28, 0.43, 0.70, 0.82, 1.0]) * duration
    waypoints = np.array([0.0, 1.0, 1.0, -0.45, -0.45, 0.35])
    if rng.random() < 0.5:
        waypoints *= -1.0
    p = np.zeros_like(time)
    v = np.zeros_like(time)
    a = np.zeros_like(time)
    j = np.zeros_like(time)
    for segment in range(boundaries.size - 1):
        left, right = boundaries[segment : segment + 2]
        mask = (time >= left) & (
            time <= right if segment == boundaries.size - 2 else time < right
        )
        delta = waypoints[segment + 1] - waypoints[segment]
        span = right - left
        if delta == 0.0:
            p[mask] = waypoints[segment]
            continue
        q, q1, q2, q3 = _seventh_profile((time[mask] - left) / span)
        p[mask] = waypoints[segment] + delta * q
        v[mask] = delta * q1 / span
        a[mask] = delta * q2 / span**2
        j[mask] = delta * q3 / span**3
    return p, v, a, j


def _rapid_reversal(
    rng: np.random.Generator, time: np.ndarray, duration: float
) -> tuple[np.ndarray, ...]:
    base_frequency = rng.uniform(1.2, 2.4)
    omega = 2 * math.pi * base_frequency
    phase = rng.uniform(-math.pi, math.pi)
    # Fundamental plus a weak third harmonic creates sharp but smooth reversals.
    arg = omega * time + phase
    p = np.sin(arg) + 0.09 * np.sin(3 * arg)
    v = omega * np.cos(arg) + 0.27 * omega * np.cos(3 * arg)
    a = -(omega**2) * np.sin(arg) - 0.81 * omega**2 * np.sin(3 * arg)
    j = -(omega**3) * np.cos(arg) - 2.43 * omega**3 * np.cos(3 * arg)
    return p, v, a, j


def _boundary_grazing(
    rng: np.random.Generator, time: np.ndarray, duration: float
) -> tuple[np.ndarray, ...]:
    # Mix a slow velocity-demanding component with a tiny, fast jerk-demanding
    # component.  Global scaling below keeps the combined reference feasible.
    slow_w = rng.uniform(1.5, 2.2)
    fast_w = rng.uniform(500.0, 650.0)
    fast_amplitude = rng.uniform(12e-6, 25e-6)
    phase = rng.uniform(-math.pi, math.pi)
    slow_amplitude = 0.05
    p = slow_amplitude * np.sin(slow_w * time) + fast_amplitude * np.sin(
        fast_w * time + phase
    )
    v = slow_amplitude * slow_w * np.cos(
        slow_w * time
    ) + fast_amplitude * fast_w * np.cos(fast_w * time + phase)
    a = -slow_amplitude * slow_w**2 * np.sin(
        slow_w * time
    ) - fast_amplitude * fast_w**2 * np.sin(fast_w * time + phase)
    j = -slow_amplitude * slow_w**3 * np.cos(
        slow_w * time
    ) - fast_amplitude * fast_w**3 * np.cos(fast_w * time + phase)
    return p, v, a, j


_GENERATORS: dict[
    str, Callable[[np.random.Generator, np.ndarray, float], tuple[np.ndarray, ...]]
] = {
    "stationary_endpoint": _stationary_endpoint,
    "oscillatory": _oscillatory,
    "piecewise_constant_jerk": _piecewise_constant_jerk,
    "stop_and_go": _stop_and_go,
    "rapid_reversal": _rapid_reversal,
    "boundary_grazing": _boundary_grazing,
}


def _scale_to_stratum(
    arrays: tuple[np.ndarray, ...],
    demand_stratum: str,
    limits: MotionLimits,
) -> tuple[np.ndarray, ...]:
    p, v, a, j = arrays
    current = max(
        float(np.max(np.abs(v)) / limits.max_velocity),
        float(np.max(np.abs(a)) / limits.max_acceleration),
        float(np.max(np.abs(j)) / limits.max_jerk),
    )
    if current <= 0.0:
        raise ValueError("generator produced a static zero reference")
    scale = DEMAND_TARGETS[demand_stratum] / current
    return p * scale, v * scale, a * scale, j * scale


def generate_trajectory(
    entry: SplitEntry,
    *,
    internal_dt: float = DEFAULT_INTERNAL_DT,
    limits: MotionLimits = DEFAULT_MOTION_LIMITS,
) -> ContinuousTrajectory:
    """Generate one clean trajectory using only a manifest-supplied ID/seed."""

    if entry.family not in _GENERATORS:
        raise ValueError(f"unknown synthetic family: {entry.family}")
    if entry.demand_stratum not in DEMAND_TARGETS:
        raise ValueError(f"unknown demand stratum: {entry.demand_stratum}")
    rng = np.random.default_rng(entry.seed)
    duration = float(rng.uniform(2.5, 4.5))
    # High-frequency boundary truth receives a 2 kHz grid even if 1 kHz was
    # requested, avoiding an under-resolved jerk realization.
    actual_dt = (
        min(internal_dt, 0.0005) if entry.family == "boundary_grazing" else internal_dt
    )
    time = _truth_grid(duration, actual_dt)
    reference_frequency_spec_json: str | None = None
    if entry.family == "stationary_endpoint":
        variant_index = entry.seed % 2
        arrays = _stationary_endpoint(rng, time, duration, variant_index)
        reference_variant = ("quintic", "seventh_order")[variant_index]
    elif entry.family == "oscillatory":
        variant_index = entry.seed % 3
        arrays, reference_frequency_spec_json = _oscillatory_with_frequency_spec(
            rng, time, duration, variant_index
        )
        reference_variant = ("sine", "multi_sine", "chirp")[variant_index]
    else:
        arrays = _GENERATORS[entry.family](rng, time, duration)
        reference_variant = entry.family
    p, v, a, j = _scale_to_stratum(arrays, entry.demand_stratum, limits)
    trajectory = ContinuousTrajectory(
        trajectory_id=entry.trajectory_id,
        family=entry.family,
        split=entry.split,
        seed=entry.seed,
        demand_stratum=entry.demand_stratum,
        time=time,
        position=p,
        velocity=v,
        acceleration=a,
        jerk=j,
        internal_dt=float(np.max(np.diff(time))),
        reference_variant=reference_variant,
        reference_frequency_spec_json=reference_frequency_spec_json,
    )
    assert_truth_constraints(trajectory, limits=limits)
    return trajectory


def assert_truth_constraints(
    trajectory: ContinuousTrajectory,
    *,
    limits: MotionLimits = DEFAULT_MOTION_LIMITS,
    tolerance: float = 1e-10,
) -> None:
    if trajectory.intentionally_infeasible:
        raise ValueError(
            "deliberately infeasible references are not clean truth benchmarks"
        )
    peaks = (
        np.max(np.abs(trajectory.velocity)),
        np.max(np.abs(trajectory.acceleration)),
        np.max(np.abs(trajectory.jerk)),
    )
    bounds = (limits.max_velocity, limits.max_acceleration, limits.max_jerk)
    labels = ("velocity", "acceleration", "jerk")
    for label, peak, bound in zip(labels, peaks, bounds):
        if peak > bound * (1.0 + tolerance):
            raise ValueError(f"truth {label} limit violated: {peak} > {bound}")


def resample_truth(
    trajectory: ContinuousTrajectory, sample_rate_hz: float
) -> SampledTruth:
    """Resample each truth state directly; never reconstruct derivatives from p."""

    if sample_rate_hz <= 0.0:
        raise ValueError("sample_rate_hz must be positive")
    step = 1.0 / sample_rate_hz
    time = np.arange(0.0, trajectory.time[-1] + step * 0.25, step)
    time = time[time <= trajectory.time[-1] + 1e-12]
    return SampledTruth(
        time=time,
        position=np.interp(time, trajectory.time, trajectory.position),
        velocity=np.interp(time, trajectory.time, trajectory.velocity),
        acceleration=np.interp(time, trajectory.time, trajectory.acceleration),
        jerk=np.interp(time, trajectory.time, trajectory.jerk),
        sample_rate_hz=float(sample_rate_hz),
    )


def trajectory_to_rows(
    trajectory: ContinuousTrajectory,
    *,
    sample_rate_hz: float = 100.0,
    run_id: str = "dataset-generation",
    dataset_id: str = "synthetic-feasible-v1",
    session_id: str = "synthetic",
    joint_id: str = "joint_0",
) -> list[dict[str, Any]]:
    if trajectory.intentionally_infeasible and dataset_id == "synthetic-feasible-v1":
        dataset_id = "synthetic-deliberate-infeasible-v1"
    source_kind = (
        "synthetic_deliberate_infeasible"
        if trajectory.intentionally_infeasible
        else "synthetic_feasible"
    )
    scenario_id = (
        trajectory.reference_variant if trajectory.intentionally_infeasible else "clean"
    )
    sampled = resample_truth(trajectory, sample_rate_hz)
    dt = 1.0 / sample_rate_hz
    rows: list[dict[str, Any]] = []
    for k, (time, p, v, a, j) in enumerate(
        zip(
            sampled.time,
            sampled.position,
            sampled.velocity,
            sampled.acceleration,
            sampled.jerk,
        )
    ):
        rows.append(
            empty_sample(
                run_id=run_id,
                dataset_id=dataset_id,
                session_id=session_id,
                trajectory_id=trajectory.trajectory_id,
                split=trajectory.split,
                seed=int(trajectory.seed),
                joint_id=joint_id,
                k=k,
                source_time=float(time),
                arrival_time=float(time),
                control_time=float(time),
                dt_actual=dt if k == 0 else float(time - sampled.time[k - 1]),
                dt_control=dt,
                p_ref=float(p),
                v_ref_truth=float(v),
                a_ref_truth=float(a),
                j_ref_truth=float(j),
                p_meas=float(p),
                source_kind=source_kind,
                reference_family=trajectory.family,
                reference_variant=trajectory.reference_variant,
                reference_frequency_spec_json=(
                    trajectory.reference_frequency_spec_json
                ),
                scenario_id=scenario_id,
                truth_available=True,
                measurement_available=True,
                measurement_valid=True,
            )
        )
    validate_samples(rows)
    return rows


def _copy_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied = [copy.deepcopy(dict(row)) for row in rows]
    validate_samples(copied)
    return copied


def _refresh_event_flags(row: dict[str, Any]) -> None:
    labels = []
    for name, label in (
        ("event_dropped", "dropped"),
        ("event_burst_drop", "burst_drop"),
        ("event_held", "held"),
        ("event_duplicate", "duplicate"),
        ("event_timestamp_regression", "timestamp_regression"),
        ("event_outlier", "outlier"),
        ("event_nonfinite", "nonfinite"),
        ("event_impossible_jump", "impossible_jump"),
        ("deadline_miss", "deadline_miss"),
        ("state_reset", "state_reset"),
        ("invalid_input", "invalid_input"),
    ):
        if row[name]:
            labels.append(label)
    row["event_flags"] = ";".join(labels)


def inject_noise(
    rows: Sequence[Mapping[str, Any]],
    *,
    std: float,
    seed: int,
    colored_ar: float = 0.0,
    scenario_id: str | None = None,
) -> list[dict[str, Any]]:
    if std < 0.0 or not -1.0 < colored_ar < 1.0:
        raise ValueError(
            "std must be non-negative and AR coefficient must be in (-1, 1)"
        )
    output = _copy_rows(rows)
    rng = np.random.default_rng(seed)
    innovations = rng.normal(0.0, std, len(output))
    realization = np.empty(len(output), dtype=float)
    state = 0.0
    innovation_scale = math.sqrt(max(0.0, 1.0 - colored_ar**2))
    for index, innovation in enumerate(innovations):
        state = colored_ar * state + innovation_scale * innovation
        realization[index] = state
    label = scenario_id or (f"noise_ar{colored_ar:g}_std{std:g}")
    parameters = json.dumps(
        {"kind": "noise", "std": std, "colored_ar": colored_ar},
        sort_keys=True,
        separators=(",", ":"),
    )
    for row, noise in zip(output, realization):
        row["scenario_id"] = label
        row["stress_seed"] = seed
        row["stress_parameters_json"] = parameters
        row["noise_realization"] = float(noise)
        if row["measurement_available"] and row["p_meas"] is not None:
            row["p_meas"] = float(row["p_meas"] + noise)
    validate_samples(output)
    return output


def inject_quantization(
    rows: Sequence[Mapping[str, Any]],
    *,
    resolution: float,
    scenario_id: str | None = None,
    stress_seed: int | None = None,
) -> list[dict[str, Any]]:
    if resolution <= 0.0:
        raise ValueError("resolution must be positive")
    output = _copy_rows(rows)
    parameters = json.dumps(
        {"kind": "quantization", "resolution": resolution},
        sort_keys=True,
        separators=(",", ":"),
    )
    for row in output:
        row["scenario_id"] = scenario_id or f"quantization_{resolution:g}"
        row["stress_seed"] = stress_seed
        row["stress_parameters_json"] = parameters
        if row["measurement_available"] and row["p_meas"] is not None:
            raw = float(row["p_meas"])
            quantized = round(raw / resolution) * resolution
            row["p_meas"] = float(quantized)
            row["quantization_error"] = float(quantized - raw)
    validate_samples(output)
    return output


def empirical_jitter_realization(
    observed_intervals: Sequence[float],
    *,
    target_dt: float,
    size: int,
    seed: int,
) -> np.ndarray:
    """Bootstrap centered timing residuals from an observed trace."""

    intervals = np.asarray(observed_intervals, dtype=float)
    if intervals.ndim != 1 or intervals.size == 0 or not np.all(np.isfinite(intervals)):
        raise ValueError("observed intervals must be a non-empty finite vector")
    residual = intervals - np.median(intervals)
    rng = np.random.default_rng(seed)
    return rng.choice(residual, size=size, replace=True) + (
        np.median(intervals) - target_dt
    )


def inject_timing(
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    jitter_std_s: float = 0.0,
    empirical_jitter_s: Sequence[float] | None = None,
    drop_probability: float = 0.0,
    burst_start: int | None = None,
    burst_length: int = 0,
    duplicate_index: int | None = None,
    regression_index: int | None = None,
    regression_s: float = 0.0,
    scenario_id: str = "timing",
) -> list[dict[str, Any]]:
    if jitter_std_s < 0.0 or not 0.0 <= drop_probability <= 1.0:
        raise ValueError("invalid jitter/drop setting")
    output = _copy_rows(rows)
    rng = np.random.default_rng(seed)
    if empirical_jitter_s is None:
        jitter = rng.normal(0.0, jitter_std_s, len(output))
    else:
        values = np.asarray(empirical_jitter_s, dtype=float)
        if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)):
            raise ValueError("empirical jitter must be a finite non-empty vector")
        jitter = rng.choice(values, size=len(output), replace=True)
    jitter[0] = 0.0
    dropped = rng.random(len(output)) < drop_probability
    if burst_start is not None and burst_length > 0:
        dropped[burst_start : burst_start + burst_length] = True
    parameters = json.dumps(
        {
            "kind": "timing",
            "jitter_std_s": jitter_std_s,
            "empirical_jitter": empirical_jitter_s is not None,
            "drop_probability": drop_probability,
            "burst_start": burst_start,
            "burst_length": burst_length,
            "duplicate_index": duplicate_index,
            "regression_index": regression_index,
            "regression_s": regression_s,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    for index, row in enumerate(output):
        row["scenario_id"] = scenario_id
        row["stress_seed"] = seed
        row["stress_parameters_json"] = parameters
        row["source_jitter_s"] = float(jitter[index])
        row["source_time"] = float(row["source_time"] + jitter[index])
        # This suite perturbs the source clock.  Arrival is a separate physical
        # clock and cannot precede the newly realized state timestamp.
        row["arrival_time"] = float(max(row["arrival_time"], row["source_time"]))
        row["transport_delay_s"] = float(row["arrival_time"] - row["source_time"])
        if dropped[index]:
            row["event_dropped"] = True
            row["event_burst_drop"] = bool(
                burst_start is not None
                and burst_start <= index < burst_start + burst_length
            )
            row["measurement_available"] = False
            row["measurement_valid"] = False
            row["p_meas"] = None
            row["v_meas"] = None
            row["a_meas"] = None
    if duplicate_index is not None:
        if duplicate_index <= 0 or duplicate_index >= len(output):
            raise ValueError("duplicate_index must refer to a non-initial sample")
        duplicate = output[duplicate_index]
        source = output[duplicate_index - 1]
        if duplicate["measurement_available"]:
            # Duplicate the payload as well as its state timestamp.  Repeating
            # only the timestamp while retaining the current value would not be
            # a physically meaningful duplicated sensor sample.
            available_sources = [
                candidate
                for candidate in output[:duplicate_index]
                if candidate["measurement_available"]
                and candidate["p_meas"] is not None
            ]
            if not available_sources:
                raise ValueError(
                    "duplicate fault has no earlier available measurement to repeat"
                )
            source = available_sources[-1]
            duplicate["p_meas"] = source["p_meas"]
            duplicate["v_meas"] = source["v_meas"]
            duplicate["a_meas"] = source["a_meas"]
            duplicate["measurement_valid"] = source["measurement_valid"]
            duplicate["noise_realization"] = source["noise_realization"]
            duplicate["quantization_error"] = source["quantization_error"]
        duplicate["source_time"] = source["source_time"]
        duplicate["event_duplicate"] = True
    if regression_index is not None:
        if regression_index <= 0 or regression_index >= len(output):
            raise ValueError("regression_index must refer to a non-initial sample")
        amount = (
            regression_s
            if regression_s > 0.0
            else output[regression_index]["dt_control"]
        )
        output[regression_index]["source_time"] = float(
            output[regression_index - 1]["source_time"] - amount
        )
        output[regression_index]["event_timestamp_regression"] = True
    for index, row in enumerate(output):
        row["arrival_time"] = float(max(row["arrival_time"], row["source_time"]))
        row["transport_delay_s"] = float(row["arrival_time"] - row["source_time"])
        row["dt_actual"] = (
            float(row["dt_control"])
            if index == 0
            else float(row["source_time"] - output[index - 1]["source_time"])
        )
        _refresh_event_flags(row)
    # Jitter itself can cause an accidental regression.  This must never be
    # silent, even when no explicit regression case was requested.
    for index in range(1, len(output)):
        delta = output[index]["source_time"] - output[index - 1]["source_time"]
        if delta < 0.0:
            output[index]["event_timestamp_regression"] = True
        elif delta == 0.0:
            output[index]["event_duplicate"] = True
        _refresh_event_flags(output[index])
    validate_samples(output)
    return output


def inject_outlier(
    rows: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    index: int,
    magnitude: float = 1.0,
    burst_length: int = 1,
    scenario_id: str | None = None,
    stress_seed: int | None = None,
) -> list[dict[str, Any]]:
    valid = {"spike", "burst", "nan", "posinf", "neginf", "impossible_jump"}
    if kind not in valid or index < 0 or index >= len(rows):
        raise ValueError("invalid outlier kind/index")
    output = _copy_rows(rows)
    label = scenario_id or f"outlier_{kind}"
    parameters = json.dumps(
        {
            "kind": "outlier",
            "outlier_kind": kind,
            "requested_index": index,
            "magnitude": magnitude,
            "burst_length": burst_length,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    for row in output:
        row["scenario_id"] = label
        row["stress_seed"] = stress_seed
        row["stress_parameters_json"] = parameters
    needed = burst_length if kind == "burst" else 1
    candidates = [
        row_index
        for row_index in range(index, len(output))
        if output[row_index]["measurement_available"]
        and output[row_index]["p_meas"] is not None
    ]
    if len(candidates) < needed:
        candidates.extend(
            row_index
            for row_index in range(0, index)
            if output[row_index]["measurement_available"]
            and output[row_index]["p_meas"] is not None
        )
    affected = candidates[:needed]
    if len(affected) < needed:
        raise ValueError(
            "not enough available measurements for requested outlier realization"
        )
    for offset, row_index in enumerate(affected):
        row = output[row_index]
        row["event_outlier"] = True
        row["outlier_kind"] = kind
        row["measurement_valid"] = False
        row["invalid_input"] = True
        original = float(row["p_meas"])
        if kind in {"spike", "burst"}:
            row["p_meas"] = float(
                row["p_meas"] + magnitude * (-1.0 if offset % 2 else 1.0)
            )
            row["outlier_realization"] = float(row["p_meas"] - original)
        elif kind == "nan":
            row["p_meas"] = float("nan")
            row["event_nonfinite"] = True
        elif kind == "posinf":
            row["p_meas"] = float("inf")
            row["event_nonfinite"] = True
        elif kind == "neginf":
            row["p_meas"] = float("-inf")
            row["event_nonfinite"] = True
        else:
            row["p_meas"] = float(row["p_meas"] + magnitude)
            row["outlier_realization"] = float(row["p_meas"] - original)
            row["event_impossible_jump"] = True
        _refresh_event_flags(row)
    validate_samples(output)
    return output


def apply_stress(
    rows: Sequence[Mapping[str, Any]], config: StressConfig
) -> list[dict[str, Any]]:
    """Apply one replayable stress configuration without mutating clean rows."""

    if config.kind == "noise":
        return inject_noise(
            rows,
            std=config.noise_std,
            seed=config.seed,
            colored_ar=config.ar_coefficient,
            scenario_id=config.scenario_id,
        )
    if config.kind == "quantization":
        if config.resolution is None:
            raise ValueError("quantization stress requires resolution")
        return inject_quantization(
            rows,
            resolution=config.resolution,
            scenario_id=config.scenario_id,
            stress_seed=config.seed,
        )
    if config.kind == "timing":
        return inject_timing(
            rows,
            seed=config.seed,
            jitter_std_s=config.jitter_std_s,
            drop_probability=config.drop_probability,
            burst_start=config.burst_start,
            burst_length=config.burst_length,
            duplicate_index=config.duplicate_index,
            regression_index=config.regression_index,
            regression_s=config.regression_s,
            scenario_id=config.scenario_id,
        )
    if config.kind == "outlier":
        return inject_outlier(
            rows,
            kind=config.outlier_kind or "spike",
            index=config.outlier_index
            if config.outlier_index is not None
            else len(rows) // 2,
            magnitude=config.outlier_magnitude,
            burst_length=config.burst_length or 1,
            scenario_id=config.scenario_id,
            stress_seed=config.seed,
        )
    if config.kind == "combined":
        output = inject_noise(
            rows,
            std=config.noise_std,
            seed=config.seed,
            colored_ar=config.ar_coefficient,
            scenario_id=config.scenario_id,
        )
        output = inject_timing(
            output,
            seed=config.seed + 1,
            jitter_std_s=config.jitter_std_s,
            drop_probability=config.drop_probability,
            burst_start=config.burst_start,
            burst_length=config.burst_length,
            scenario_id=config.scenario_id,
        )
        output = inject_outlier(
            output,
            kind=config.outlier_kind or "spike",
            index=config.outlier_index
            if config.outlier_index is not None
            else len(rows) // 2,
            magnitude=config.outlier_magnitude,
            scenario_id=config.scenario_id,
            stress_seed=config.seed,
        )
        combined_parameters = json.dumps(
            {
                "kind": "combined",
                "base_seed": config.seed,
                "timing_seed": config.seed + 1,
                "noise_std": config.noise_std,
                "colored_ar": config.ar_coefficient,
                "jitter_std_s": config.jitter_std_s,
                "drop_probability": config.drop_probability,
                "outlier_kind": config.outlier_kind or "spike",
                "outlier_index": config.outlier_index,
                "outlier_magnitude": config.outlier_magnitude,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in output:
            row["scenario_id"] = config.scenario_id
            row["stress_seed"] = config.seed
            row["stress_parameters_json"] = combined_parameters
        validate_samples(output)
        return output
    raise ValueError(f"unknown stress kind: {config.kind}")


def default_stress_suite(seed: int = 91000) -> tuple[StressConfig, ...]:
    """Representative, separate suites (not a wasteful full Cartesian product)."""

    scenarios: list[StressConfig] = []
    for index, std in enumerate((1e-6, 1e-5, 1e-4)):
        scenarios.append(
            StressConfig("noise", f"white_{std:g}", seed + index, noise_std=std)
        )
        scenarios.append(
            StressConfig(
                "noise",
                f"ar08_{std:g}",
                seed + 10 + index,
                noise_std=std,
                ar_coefficient=0.8,
            )
        )
    for index, resolution in enumerate((1e-6, 1e-5, 1e-4, 1e-3)):
        scenarios.append(
            StressConfig(
                "quantization",
                f"quant_{resolution:g}",
                seed + 20 + index,
                resolution=resolution,
            )
        )
    for index, jitter_ms in enumerate((0.0, 0.5, 1.0, 2.0)):
        scenarios.append(
            StressConfig(
                "timing",
                f"jitter_{jitter_ms:g}ms",
                seed + 30 + index,
                jitter_std_s=jitter_ms / 1000.0,
            )
        )
    scenarios.extend(
        (
            StressConfig("timing", "drop_1pct", seed + 40, drop_probability=0.01),
            StressConfig("timing", "drop_5pct", seed + 41, drop_probability=0.05),
            StressConfig(
                "timing", "burst_drop", seed + 42, burst_start=40, burst_length=5
            ),
            StressConfig("timing", "duplicate", seed + 43, duplicate_index=50),
            StressConfig(
                "timing",
                "timestamp_regression",
                seed + 44,
                regression_index=60,
                regression_s=0.002,
            ),
        )
    )
    for index, kind in enumerate(
        ("spike", "burst", "nan", "posinf", "neginf", "impossible_jump")
    ):
        scenarios.append(
            StressConfig(
                "outlier",
                f"outlier_{kind}",
                seed + 50 + index,
                outlier_kind=kind,
                outlier_index=70,
                outlier_magnitude=0.25,
                burst_length=4,
            )
        )
    scenarios.append(
        StressConfig(
            "combined",
            "combined_medium",
            seed + 70,
            noise_std=1e-4,
            ar_coefficient=0.6,
            jitter_std_s=0.001,
            drop_probability=0.01,
            outlier_kind="spike",
            outlier_index=80,
            outlier_magnitude=0.15,
        )
    )
    return tuple(scenarios)


def deliberate_infeasible_suite(
    *,
    internal_dt: float = DEFAULT_INTERNAL_DT,
    limits: MotionLimits = DEFAULT_MOTION_LIMITS,
) -> list[ContinuousTrajectory]:
    """Return a separately labeled governor-only negative suite.

    These references intentionally violate one or more V/A/J bounds and must
    never be passed to estimator benchmark selection or clean truth audits.
    """

    time = _truth_grid(1.0, internal_dt)
    suite: list[ContinuousTrajectory] = []
    constructions = (
        (
            "infeasible_velocity",
            np.zeros_like(time),
            np.full_like(time, 1.25 * limits.max_velocity),
            np.zeros_like(time),
            np.zeros_like(time),
        ),
        (
            "infeasible_acceleration",
            0.55 * limits.max_acceleration * time**2,
            1.1 * limits.max_acceleration * time,
            np.full_like(time, 1.1 * limits.max_acceleration),
            np.zeros_like(time),
        ),
        (
            "infeasible_jerk",
            (1.1 * limits.max_jerk) * time**3 / 6,
            (1.1 * limits.max_jerk) * time**2 / 2,
            (1.1 * limits.max_jerk) * time,
            np.full_like(time, 1.1 * limits.max_jerk),
        ),
        (
            "infeasible_step",
            np.where(time < 0.5, 0.0, 1.0),
            np.zeros_like(time),
            np.zeros_like(time),
            np.zeros_like(time),
        ),
    )
    for seed, (name, p, v, a, j) in enumerate(constructions, start=990001):
        suite.append(
            ContinuousTrajectory(
                trajectory_id=name,
                family="deliberate_infeasible",
                split="infeasible",
                seed=seed,
                demand_stratum="outside_limits",
                time=time.copy(),
                position=p,
                velocity=v,
                acceleration=a,
                jerk=j,
                internal_dt=float(np.max(np.diff(time))),
                intentionally_infeasible=True,
                reference_variant=name,
            )
        )
    return suite


__all__ = [
    "ContinuousTrajectory",
    "DEFAULT_CONTROL_DT",
    "DEFAULT_INTERNAL_DT",
    "DEFAULT_LIMITS",
    "DEFAULT_MOTION_LIMITS",
    "DEFAULT_SPLIT_MANIFEST",
    "DEMAND_TARGETS",
    "FAMILIES",
    "MotionLimits",
    "SampledTruth",
    "SplitEntry",
    "StressConfig",
    "apply_stress",
    "assert_truth_constraints",
    "default_stress_suite",
    "deliberate_infeasible_suite",
    "empirical_jitter_realization",
    "entries_for_split",
    "generate_trajectory",
    "inject_noise",
    "inject_outlier",
    "inject_quantization",
    "inject_timing",
    "load_split_manifest",
    "resample_truth",
    "split_entries",
    "trajectory_to_rows",
    "validate_split_manifest",
    "validate_fresh_locked_test_manifest",
]
