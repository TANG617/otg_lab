"""Deterministic sources for canonical CSV-first reference trajectories."""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from .csvio import sha256_file, write_trajectory_csv
from .models import Trajectory, TrajectoryMetadata

ANALYTIC_GENERATOR_IDS = (
    "quadratic_with_extremum",
    "cubic",
    "sine",
)
_GENERATOR_ALIASES = {
    "quadratic-with-extremum": "quadratic_with_extremum",
    "quadratic": "quadratic_with_extremum",
    "quadratic_with_extremum": "quadratic_with_extremum",
    "cubic": "cubic",
    "sine": "sine",
}
_COMMON_DEFAULTS: dict[str, float] = {
    "dt_s": 0.01,
    "duration_s": 3.0,
    "settle_duration_s": 2.0,
}
_SPECIFIC_DEFAULTS: dict[str, dict[str, float]] = {
    "quadratic_with_extremum": {"scale_rad_s2": 0.5},
    "cubic": {"scale_rad_s3": 0.12},
    "sine": {"amplitude_rad": 0.37, "cycles": 1.0},
}


def _finite(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _positive(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _nonnegative(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _normalized_generator_id(generator_id: str) -> str:
    try:
        return _GENERATOR_ALIASES[str(generator_id)]
    except KeyError as error:
        raise ValueError(
            f"unknown analytic generator {generator_id!r}; expected one of "
            f"{', '.join(ANALYTIC_GENERATOR_IDS)}"
        ) from error


def resolve_analytic_parameters(
    generator_id: str,
    params: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, float]]:
    """Normalize an analytic generator ID and validate all parameters."""

    normalized_id = _normalized_generator_id(generator_id)
    if params is None:
        supplied: dict[str, Any] = {}
    elif isinstance(params, Mapping):
        supplied = dict(params)
    else:
        raise TypeError("params must be a mapping or None")
    # Small spelling aliases make the public function pleasant without
    # allowing arbitrary ignored parameters.
    aliases = {
        "dt": "dt_s",
        "duration": "duration_s",
        "settle_time": "settle_duration_s",
        "sine_amplitude": "amplitude_rad",
    }
    for alias, canonical in aliases.items():
        if alias in supplied:
            if canonical in supplied:
                raise ValueError(
                    f"params cannot contain both {alias!r} and {canonical!r}"
                )
            supplied[canonical] = supplied.pop(alias)

    defaults = {
        **_COMMON_DEFAULTS,
        **_SPECIFIC_DEFAULTS[normalized_id],
    }
    unexpected = set(supplied) - set(defaults)
    if unexpected:
        raise ValueError(
            f"unexpected parameters for {normalized_id}: {sorted(unexpected)}"
        )
    resolved = {**defaults, **supplied}
    resolved["dt_s"] = _positive(resolved["dt_s"], "dt_s")
    resolved["duration_s"] = _positive(
        resolved["duration_s"],
        "duration_s",
    )
    resolved["settle_duration_s"] = _nonnegative(
        resolved["settle_duration_s"],
        "settle_duration_s",
    )
    if normalized_id == "quadratic_with_extremum":
        resolved["scale_rad_s2"] = _finite(
            resolved["scale_rad_s2"],
            "scale_rad_s2",
        )
    elif normalized_id == "cubic":
        resolved["scale_rad_s3"] = _finite(
            resolved["scale_rad_s3"],
            "scale_rad_s3",
        )
    else:
        resolved["amplitude_rad"] = _finite(
            resolved["amplitude_rad"],
            "amplitude_rad",
        )
        resolved["cycles"] = _positive(resolved["cycles"], "cycles")

    for duration_name in ("duration_s", "settle_duration_s"):
        intervals = resolved[duration_name] / resolved["dt_s"]
        if not math.isclose(
            intervals,
            round(intervals),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                f"{duration_name} must be an integer multiple of dt_s"
            )
    return normalized_id, resolved


def generate_analytic_trajectory(
    generator_id: str,
    params: Mapping[str, Any] | None = None,
) -> Trajectory:
    """Generate one of the three stationary-endpoint analytic references.

    A seventh-order smoothstep time warp preserves the historical elementary
    trajectories while making p/v/a/j truth analytic and exactly stationary
    at both ends.  The optional settle segment begins one sample after the
    motion endpoint, avoiding duplicate timestamps.
    """

    normalized_id, resolved = resolve_analytic_parameters(generator_id, params)
    dt_s = resolved["dt_s"]
    duration_s = resolved["duration_s"]
    motion_intervals = int(round(duration_s / dt_s))
    settle_intervals = int(round(resolved["settle_duration_s"] / dt_s))
    motion_time = np.arange(motion_intervals + 1, dtype=np.float64) * dt_s
    tau = motion_time / duration_s

    # h maps [0, 1] to [0, 1] and its first three endpoint derivatives vanish.
    h = 35.0 * tau**4 - 84.0 * tau**5 + 70.0 * tau**6 - 20.0 * tau**7
    dh = 140.0 * tau**3 - 420.0 * tau**4 + 420.0 * tau**5 - 140.0 * tau**6
    ddh = (
        420.0 * tau**2
        - 1680.0 * tau**3
        + 2100.0 * tau**4
        - 840.0 * tau**5
    )
    dddh = (
        840.0 * tau
        - 5040.0 * tau**2
        + 8400.0 * tau**3
        - 4200.0 * tau**4
    )
    parameter = duration_s * h
    parameter_velocity = dh
    parameter_acceleration = ddh / duration_s
    parameter_jerk = dddh / duration_s**2
    centered = parameter - duration_s / 2.0

    if normalized_id == "quadratic_with_extremum":
        scale = resolved["scale_rad_s2"]
        position = scale * centered**2
        derivative_1 = 2.0 * scale * centered
        derivative_2 = np.full_like(centered, 2.0 * scale)
        derivative_3 = np.zeros_like(centered)
    elif normalized_id == "cubic":
        scale = resolved["scale_rad_s3"]
        position = scale * centered**3
        derivative_1 = 3.0 * scale * centered**2
        derivative_2 = 6.0 * scale * centered
        derivative_3 = np.full_like(centered, 6.0 * scale)
    else:
        amplitude = resolved["amplitude_rad"]
        omega = 2.0 * np.pi * resolved["cycles"] / duration_s
        phase = omega * parameter
        position = amplitude * np.sin(phase)
        derivative_1 = amplitude * omega * np.cos(phase)
        derivative_2 = -amplitude * omega**2 * np.sin(phase)
        derivative_3 = -amplitude * omega**3 * np.cos(phase)

    velocity = derivative_1 * parameter_velocity
    acceleration = (
        derivative_2 * parameter_velocity**2
        + derivative_1 * parameter_acceleration
    )
    jerk = (
        derivative_3 * parameter_velocity**3
        + 3.0
        * derivative_2
        * parameter_velocity
        * parameter_acceleration
        + derivative_1 * parameter_jerk
    )

    if settle_intervals:
        position = np.concatenate(
            (
                position,
                np.full(settle_intervals, position[-1], dtype=np.float64),
            )
        )
        velocity = np.concatenate(
            (velocity, np.zeros(settle_intervals, dtype=np.float64))
        )
        acceleration = np.concatenate(
            (acceleration, np.zeros(settle_intervals, dtype=np.float64))
        )
        jerk = np.concatenate(
            (jerk, np.zeros(settle_intervals, dtype=np.float64))
        )
    sample_count = motion_intervals + 1 + settle_intervals
    return Trajectory(
        sample_index=np.arange(sample_count, dtype=np.int64),
        time_s=np.arange(sample_count, dtype=np.float64) * dt_s,
        position_rad=position,
        velocity_rad_s=velocity,
        acceleration_rad_s2=acceleration,
        jerk_rad_s3=jerk,
        nominal_dt_s=dt_s,
    )


def analytic_trajectory_metadata(
    generator_id: str,
    params: Mapping[str, Any] | None = None,
    *,
    trajectory_id: str | None = None,
) -> TrajectoryMetadata:
    """Build the complete sidecar for an analytic reference."""

    normalized_id, resolved = resolve_analytic_parameters(generator_id, params)
    return TrajectoryMetadata(
        trajectory_id=trajectory_id or normalized_id,
        kind="reference",
        dt_s=resolved["dt_s"],
        channel_semantics={
            "position_rad": "analytic_truth",
            "velocity_rad_s": "analytic_truth",
            "acceleration_rad_s2": "analytic_truth",
            "jerk_rad_s3": "analytic_truth",
        },
        source={"type": "analytic_generator"},
        generator_id=normalized_id,
        generator_params=resolved,
    )


def write_analytic_trajectory_csv(
    path: str | Path,
    generator_id: str,
    params: Mapping[str, Any] | None = None,
    *,
    trajectory_id: str | None = None,
) -> Trajectory:
    """Generate and persist an analytic trajectory through the canonical I/O."""

    trajectory = generate_analytic_trajectory(generator_id, params)
    metadata = analytic_trajectory_metadata(
        generator_id,
        params,
        trajectory_id=trajectory_id,
    )
    write_trajectory_csv(path, trajectory, metadata)
    return trajectory


def _position_values(path: Path, value_column: str) -> np.ndarray:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or value_column not in reader.fieldnames:
            raise ValueError(
                f"{path} must contain a {value_column!r} column"
            )
        values: list[float] = []
        for line_number, row in enumerate(reader, start=2):
            raw = row.get(value_column)
            if raw is None or not raw.strip():
                raise ValueError(
                    f"line {line_number}: {value_column} cannot be blank"
                )
            try:
                value = float(raw)
            except ValueError as error:
                raise ValueError(
                    f"line {line_number}: {value_column} must be numeric"
                ) from error
            if not math.isfinite(value):
                raise ValueError(
                    f"line {line_number}: {value_column} must be finite"
                )
            values.append(value)
    if len(values) < 2:
        raise ValueError(f"{path} must contain at least two finite values")
    return np.asarray(values, dtype=np.float64)


def convert_value_column_csv(
    source_path: str | Path,
    output_path: str | Path | None = None,
    *,
    dt_s: float = 0.01,
    trajectory_id: str | None = None,
    value_column: str = "value",
    settle_duration_s: float = 0.0,
) -> Trajectory:
    """Convert a declared value column to a fixed-grid position trajectory.

    Other columns are deliberately ignored. Row order plus the declared period
    is the sole time base. Derivative columns remain unavailable.
    """

    source = Path(source_path)
    dt_s = _positive(dt_s, "dt_s")
    settle_duration_s = _nonnegative(
        settle_duration_s,
        "settle_duration_s",
    )
    settle_intervals = settle_duration_s / dt_s
    if not math.isclose(
        settle_intervals,
        round(settle_intervals),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("settle_duration_s must be an integer multiple of dt_s")
    values = _position_values(source, value_column)
    settle_count = int(round(settle_intervals))
    if settle_count:
        values = np.concatenate(
            (
                values,
                np.full(settle_count, values[-1], dtype=np.float64),
            )
        )
    trajectory = Trajectory(
        sample_index=np.arange(values.size, dtype=np.int64),
        time_s=np.arange(values.size, dtype=np.float64) * dt_s,
        position_rad=values,
        nominal_dt_s=dt_s,
    )
    if output_path is not None:
        identifier = trajectory_id or source.stem.replace(" ", "_")
        metadata = TrajectoryMetadata.for_trajectory(
            trajectory,
            trajectory_id=identifier,
            source={
                "type": "recorded_value_column_csv",
                "path": str(source),
                "value_column": value_column,
                "other_columns_ignored": True,
            },
            source_sha256=sha256_file(source),
        )
        write_trajectory_csv(output_path, trajectory, metadata)
    return trajectory


__all__ = [
    "ANALYTIC_GENERATOR_IDS",
    "analytic_trajectory_metadata",
    "convert_value_column_csv",
    "generate_analytic_trajectory",
    "resolve_analytic_parameters",
    "write_analytic_trajectory_csv",
]
