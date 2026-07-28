"""Single-axis, CSV-first data contracts for :mod:`otg_lab`.

The classes in this module intentionally contain no experiment policy and no
execution simulator. They are the small, stable values passed between CSV I/O,
tracking components, analysis, and experiment orchestration.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]

TRAJECTORY_SCHEMA_VERSION = "otg.trajectory.v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_UNIFORM_RTOL = 1e-9
_UNIFORM_ATOL = 1e-12


def _identifier(value: str, name: str) -> str:
    result = str(value)
    if not _ID_PATTERN.fullmatch(result):
        raise ValueError(
            f"{name} must start with an alphanumeric character and contain "
            "only alphanumerics, '.', '_', or '-'"
        )
    return result


def _finite_float(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _nonnegative_float(value: float, name: str) -> float:
    result = _finite_float(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _positive_float(value: float, name: str) -> float:
    result = _finite_float(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _float_array(
    values: ArrayLike,
    name: str,
    *,
    expected_length: int | None = None,
) -> FloatArray:
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if expected_length is not None and result.size != expected_length:
        raise ValueError(
            f"{name} must contain {expected_length} samples, got {result.size}"
        )
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain only finite values")
    owned = np.array(result, dtype=np.float64, copy=True)
    owned.setflags(write=False)
    return owned


def _optional_float_array(
    values: ArrayLike | None,
    name: str,
    *,
    expected_length: int,
) -> FloatArray | None:
    if values is None:
        return None
    return _float_array(values, name, expected_length=expected_length)


def _index_array(values: ArrayLike) -> IntArray:
    raw = np.asarray(values)
    if raw.ndim != 1:
        raise ValueError("sample_index must be one-dimensional")
    if raw.dtype.kind == "b":
        raise ValueError("sample_index cannot contain booleans")
    try:
        numeric = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("sample_index must contain integers") from error
    if not np.all(np.isfinite(numeric)):
        raise ValueError("sample_index must contain finite integers")
    if not np.all(numeric == np.floor(numeric)):
        raise ValueError("sample_index must contain integers")
    int64_info = np.iinfo(np.int64)
    if np.any(numeric < int64_info.min) or np.any(numeric > int64_info.max):
        raise ValueError("sample_index values exceed int64 range")
    result = np.asarray(numeric, dtype=np.int64)
    if result.size > 1 and not np.all(np.diff(result) == 1):
        raise ValueError("sample_index must be consecutive")
    owned = np.array(result, dtype=np.int64, copy=True)
    owned.setflags(write=False)
    return owned


def _owned_mapping(value: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return dict(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


@dataclass(frozen=True)
class Trajectory:
    """A fixed-grid, single-axis trajectory.

    A derivative channel is represented either by a complete finite array or
    by ``None``.  This makes partial truth impossible in memory, just as blank
    columns make it explicit in the canonical CSV representation.

    Empty and one-sample trajectories are permitted for failed or partial
    command artifacts.  They must carry ``nominal_dt_s`` because their sample
    period cannot be inferred from timestamps.  Normal trajectories infer
    their period from ``time_s`` and, when a nominal period is supplied, check
    that the two agree.
    """

    sample_index: ArrayLike
    time_s: ArrayLike
    position_rad: ArrayLike
    velocity_rad_s: ArrayLike | None = None
    acceleration_rad_s2: ArrayLike | None = None
    jerk_rad_s3: ArrayLike | None = None
    nominal_dt_s: float | None = None

    def __post_init__(self) -> None:
        sample_index = _index_array(self.sample_index)
        sample_count = int(sample_index.size)
        time_s = _float_array(
            self.time_s,
            "time_s",
            expected_length=sample_count,
        )
        position_rad = _float_array(
            self.position_rad,
            "position_rad",
            expected_length=sample_count,
        )
        velocity_rad_s = _optional_float_array(
            self.velocity_rad_s,
            "velocity_rad_s",
            expected_length=sample_count,
        )
        acceleration_rad_s2 = _optional_float_array(
            self.acceleration_rad_s2,
            "acceleration_rad_s2",
            expected_length=sample_count,
        )
        jerk_rad_s3 = _optional_float_array(
            self.jerk_rad_s3,
            "jerk_rad_s3",
            expected_length=sample_count,
        )

        nominal_dt_s = (
            None
            if self.nominal_dt_s is None
            else _positive_float(self.nominal_dt_s, "nominal_dt_s")
        )
        if sample_count >= 2:
            differences = np.diff(time_s)
            if np.any(differences <= 0.0):
                raise ValueError("time_s must be strictly increasing")
            inferred_dt = float(differences[0])
            if not np.allclose(
                differences,
                inferred_dt,
                rtol=_UNIFORM_RTOL,
                atol=_UNIFORM_ATOL,
            ):
                raise ValueError("time_s must use a uniform sampling period")
            if nominal_dt_s is not None and not math.isclose(
                inferred_dt,
                nominal_dt_s,
                rel_tol=_UNIFORM_RTOL,
                abs_tol=_UNIFORM_ATOL,
            ):
                raise ValueError(
                    "nominal_dt_s does not match the period inferred from time_s"
                )
            nominal_dt_s = inferred_dt
        elif nominal_dt_s is None:
            raise ValueError(
                "nominal_dt_s is required for trajectories with fewer than "
                "two samples"
            )

        object.__setattr__(self, "sample_index", sample_index)
        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "position_rad", position_rad)
        object.__setattr__(self, "velocity_rad_s", velocity_rad_s)
        object.__setattr__(self, "acceleration_rad_s2", acceleration_rad_s2)
        object.__setattr__(self, "jerk_rad_s3", jerk_rad_s3)
        object.__setattr__(self, "nominal_dt_s", nominal_dt_s)

    @property
    def sample_count(self) -> int:
        return int(self.sample_index.size)

    @property
    def dt(self) -> float:
        assert self.nominal_dt_s is not None
        return float(self.nominal_dt_s)

    @property
    def duration_s(self) -> float:
        if self.sample_count < 2:
            return 0.0
        return float(self.time_s[-1] - self.time_s[0])

    @property
    def has_velocity(self) -> bool:
        return self.velocity_rad_s is not None

    @property
    def has_acceleration(self) -> bool:
        return self.acceleration_rad_s2 is not None

    @property
    def has_jerk(self) -> bool:
        return self.jerk_rad_s3 is not None

    @property
    def derivative_channels(self) -> tuple[str, ...]:
        channels: list[str] = []
        if self.has_velocity:
            channels.append("velocity_rad_s")
        if self.has_acceleration:
            channels.append("acceleration_rad_s2")
        if self.has_jerk:
            channels.append("jerk_rad_s3")
        return tuple(channels)

    # Compact aliases ease migration of pure numerical routines while keeping
    # the unit-bearing names canonical at API and artifact boundaries.
    @property
    def time(self) -> FloatArray:
        return self.time_s

    @property
    def position(self) -> FloatArray:
        return self.position_rad

    @property
    def velocity(self) -> FloatArray | None:
        return self.velocity_rad_s

    @property
    def acceleration(self) -> FloatArray | None:
        return self.acceleration_rad_s2

    @property
    def jerk(self) -> FloatArray | None:
        return self.jerk_rad_s3

    def state_at(
        self,
        index: int,
        *,
        fill_missing_derivatives: float | None = None,
    ) -> State:
        """Return a state without silently inventing unavailable derivatives."""

        position = int(index)
        if position < 0:
            position += self.sample_count
        if position < 0 or position >= self.sample_count:
            raise IndexError("trajectory sample index out of range")
        unavailable = [
            name
            for name, channel in (
                ("velocity", self.velocity_rad_s),
                ("acceleration", self.acceleration_rad_s2),
            )
            if channel is None
        ]
        if unavailable and fill_missing_derivatives is None:
            raise ValueError(
                "trajectory state has unavailable "
                + " and ".join(unavailable)
                + "; pass fill_missing_derivatives explicitly"
            )
        fill = (
            0.0
            if fill_missing_derivatives is None
            else _finite_float(
                fill_missing_derivatives,
                "fill_missing_derivatives",
            )
        )
        return State(
            time_s=float(self.time_s[position]),
            position_rad=float(self.position_rad[position]),
            velocity_rad_s=(
                fill
                if self.velocity_rad_s is None
                else float(self.velocity_rad_s[position])
            ),
            acceleration_rad_s2=(
                fill
                if self.acceleration_rad_s2 is None
                else float(self.acceleration_rad_s2[position])
            ),
            jerk_rad_s3=(
                None
                if self.jerk_rad_s3 is None
                else float(self.jerk_rad_s3[position])
            ),
        )


@dataclass(frozen=True)
class State:
    """A time-explicit scalar kinematic state."""

    time_s: float
    position_rad: float
    velocity_rad_s: float = 0.0
    acceleration_rad_s2: float = 0.0
    jerk_rad_s3: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "time_s",
            "position_rad",
            "velocity_rad_s",
            "acceleration_rad_s2",
        ):
            object.__setattr__(self, name, _finite_float(getattr(self, name), name))
        if self.jerk_rad_s3 is not None:
            object.__setattr__(
                self,
                "jerk_rad_s3",
                _finite_float(self.jerk_rad_s3, "jerk_rad_s3"),
            )

    @property
    def time(self) -> float:
        return self.time_s

    @property
    def position(self) -> float:
        return self.position_rad

    @property
    def velocity(self) -> float:
        return self.velocity_rad_s

    @property
    def acceleration(self) -> float:
        return self.acceleration_rad_s2

    @property
    def jerk(self) -> float | None:
        return self.jerk_rad_s3


@dataclass(frozen=True)
class Measurement:
    """A scalar measurement available to an online estimator."""

    time_s: float
    position_rad: float
    velocity_rad_s: float | None = None
    acceleration_rad_s2: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_s", _finite_float(self.time_s, "time_s"))
        object.__setattr__(
            self,
            "position_rad",
            _finite_float(self.position_rad, "position_rad"),
        )
        for name in ("velocity_rad_s", "acceleration_rad_s2"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite_float(value, name))


@dataclass(frozen=True)
class MotionLimits:
    """Positive scalar V/A/J limits for the single controlled axis."""

    max_velocity_rad_s: float
    max_acceleration_rad_s2: float
    max_jerk_rad_s3: float

    def __post_init__(self) -> None:
        for name in (
            "max_velocity_rad_s",
            "max_acceleration_rad_s2",
            "max_jerk_rad_s3",
        ):
            object.__setattr__(self, name, _positive_float(getattr(self, name), name))

    @property
    def max_velocity(self) -> float:
        return self.max_velocity_rad_s

    @property
    def max_acceleration(self) -> float:
        return self.max_acceleration_rad_s2

    @property
    def max_jerk(self) -> float:
        return self.max_jerk_rad_s3

    def as_dict(self) -> dict[str, float]:
        return {
            "max_velocity_rad_s": self.max_velocity_rad_s,
            "max_acceleration_rad_s2": self.max_acceleration_rad_s2,
            "max_jerk_rad_s3": self.max_jerk_rad_s3,
        }


@dataclass(frozen=True)
class ComponentSpec:
    """Stable component identity, constructor, and serializable parameters."""

    component_id: str
    params: Mapping[str, Any] = field(default_factory=dict)
    factory: Callable[..., Any] | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_id",
            _identifier(self.component_id, "component_id"),
        )
        params = _owned_mapping(self.params, "params")
        # Fail at declaration time rather than midway through manifest writing.
        json.dumps(params, sort_keys=True, default=_json_default)
        object.__setattr__(self, "params", params)
        if self.factory is not None and not callable(self.factory):
            raise TypeError("factory must be callable or None")

    def build(self) -> Any:
        if self.factory is None:
            raise ValueError(
                f"component {self.component_id!r} has no injected factory"
            )
        return self.factory(**dict(self.params))

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class TrackingMethodSpec:
    """A complete estimator-to-follower tracking composition."""

    method_id: str
    estimator: ComponentSpec
    predictor: ComponentSpec
    target_builder: ComponentSpec
    governor: ComponentSpec
    follower: ComponentSpec
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "method_id",
            _identifier(self.method_id, "method_id"),
        )
        for name in (
            "estimator",
            "predictor",
            "target_builder",
            "governor",
            "follower",
        ):
            if not isinstance(getattr(self, name), ComponentSpec):
                raise TypeError(f"{name} must be a ComponentSpec")
        object.__setattr__(self, "required", bool(self.required))
        object.__setattr__(self, "description", str(self.description))

    def as_dict(self) -> dict[str, Any]:
        return {
            "method_id": self.method_id,
            "estimator": self.estimator.as_dict(),
            "predictor": self.predictor.as_dict(),
            "target_builder": self.target_builder.as_dict(),
            "governor": self.governor.as_dict(),
            "follower": self.follower.as_dict(),
            "required": self.required,
            "description": self.description,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RunConfig:
    """Timing, limit, measurement, and failure policy for one method run."""

    limits: MotionLimits
    minimum_duration_s: float = 0.0
    prediction_horizon_s: float = 0.0
    initial_state: State | None = None
    measurement_policy: str = "position_only"
    failure_policy: str = "record_and_continue"
    dt_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.limits, MotionLimits):
            raise TypeError("limits must be MotionLimits")
        object.__setattr__(
            self,
            "minimum_duration_s",
            _nonnegative_float(self.minimum_duration_s, "minimum_duration_s"),
        )
        object.__setattr__(
            self,
            "prediction_horizon_s",
            _nonnegative_float(
                self.prediction_horizon_s,
                "prediction_horizon_s",
            ),
        )
        if self.initial_state is not None and not isinstance(
            self.initial_state,
            State,
        ):
            raise TypeError("initial_state must be State or None")
        if self.measurement_policy not in {
            "position_only",
            "available_truth",
            "oracle_noncausal",
        }:
            raise ValueError(
                "measurement_policy must be 'position_only', "
                "'available_truth', or 'oracle_noncausal'"
            )
        if self.failure_policy not in {"record_and_continue", "fail_fast"}:
            raise ValueError(
                "failure_policy must be 'record_and_continue' or 'fail_fast'"
            )
        if self.dt_s is not None:
            object.__setattr__(self, "dt_s", _positive_float(self.dt_s, "dt_s"))

    def resolved_dt(self, trajectory: Trajectory) -> float:
        if not isinstance(trajectory, Trajectory):
            raise TypeError("trajectory must be Trajectory")
        if self.dt_s is not None and not math.isclose(
            self.dt_s,
            trajectory.dt,
            rel_tol=_UNIFORM_RTOL,
            abs_tol=_UNIFORM_ATOL,
        ):
            raise ValueError("RunConfig dt_s does not match trajectory sampling period")
        return trajectory.dt


@dataclass(frozen=True)
class CommandProfileSegment:
    """One exact or sampled jerk segment in an executable command profile."""

    profile_id: str
    cycle_index: int
    segment_index: int
    start_time_s: float
    end_time_s: float
    jerk_rad_s3: float
    exact: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "profile_id",
            _identifier(self.profile_id, "profile_id"),
        )
        for name in ("cycle_index", "segment_index"):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) != value or int(value) < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            object.__setattr__(self, name, int(value))
        start = _finite_float(self.start_time_s, "start_time_s")
        end = _finite_float(self.end_time_s, "end_time_s")
        if end <= start:
            raise ValueError("end_time_s must be greater than start_time_s")
        object.__setattr__(self, "start_time_s", start)
        object.__setattr__(self, "end_time_s", end)
        object.__setattr__(
            self,
            "jerk_rad_s3",
            _finite_float(self.jerk_rad_s3, "jerk_rad_s3"),
        )
        object.__setattr__(self, "exact", bool(self.exact))


@dataclass(frozen=True)
class TrackingStatus:
    """Machine-readable completion state for one method/input pair."""

    completed: bool
    failure_layer: str | None = None
    failure_reason: str | None = None
    valid_cycles: int = 0
    total_cycles: int = 0
    method_fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "completed", bool(self.completed))
        for name in ("valid_cycles", "total_cycles"):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) != value or int(value) < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            object.__setattr__(self, name, int(value))
        if self.valid_cycles > self.total_cycles:
            raise ValueError("valid_cycles cannot exceed total_cycles")
        if self.completed and (
            self.failure_layer is not None or self.failure_reason is not None
        ):
            raise ValueError("completed status cannot carry a failure")
        if (self.failure_layer is None) != (self.failure_reason is None):
            raise ValueError(
                "failure_layer and failure_reason must either both be set or "
                "both be None"
            )
        if not self.completed and self.failure_layer is None:
            raise ValueError("incomplete status must describe its failure")
        if self.failure_layer is not None:
            object.__setattr__(self, "failure_layer", str(self.failure_layer))
            object.__setattr__(self, "failure_reason", str(self.failure_reason))
        object.__setattr__(
            self,
            "method_fingerprint",
            str(self.method_fingerprint),
        )


@dataclass(frozen=True)
class TrackingRun:
    """Raw output of a single tracking method on a single reference."""

    method_id: str
    command: Trajectory | None
    trace_rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    profile_rows: Sequence[Mapping[str, Any] | CommandProfileSegment] = field(
        default_factory=tuple
    )
    status: TrackingStatus = field(
        default_factory=lambda: TrackingStatus(
            completed=False,
            failure_layer="not_run",
            failure_reason="tracking has not run",
        )
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "method_id",
            _identifier(self.method_id, "method_id"),
        )
        if self.command is not None and not isinstance(self.command, Trajectory):
            raise TypeError("command must be Trajectory or None")
        trace_rows: list[Mapping[str, Any]] = []
        for row in self.trace_rows:
            if not isinstance(row, Mapping):
                raise TypeError("every trace row must be a mapping")
            trace_rows.append(dict(row))
        profiles: list[Mapping[str, Any] | CommandProfileSegment] = []
        for row in self.profile_rows:
            if not isinstance(row, (Mapping, CommandProfileSegment)):
                raise TypeError(
                    "every profile row must be a mapping or "
                    "CommandProfileSegment"
                )
            profiles.append(dict(row) if isinstance(row, Mapping) else row)
        if not isinstance(self.status, TrackingStatus):
            raise TypeError("status must be TrackingStatus")
        object.__setattr__(self, "trace_rows", tuple(trace_rows))
        object.__setattr__(self, "profile_rows", tuple(profiles))

    @property
    def trace(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.trace_rows)

    @property
    def command_profiles(
        self,
    ) -> tuple[Mapping[str, Any] | CommandProfileSegment, ...]:
        return tuple(self.profile_rows)


@dataclass(frozen=True)
class TrajectoryMetadata:
    """Sidecar metadata for a canonical trajectory CSV."""

    trajectory_id: str
    kind: str
    dt_s: float
    channel_semantics: Mapping[str, str]
    source: Mapping[str, Any] = field(default_factory=dict)
    generator_id: str | None = None
    generator_params: Mapping[str, Any] = field(default_factory=dict)
    source_sha256: str | None = None
    csv_sha256: str | None = None
    schema_version: str = TRAJECTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trajectory_id",
            _identifier(self.trajectory_id, "trajectory_id"),
        )
        kind = str(self.kind)
        if kind not in {"reference", "command"}:
            raise ValueError("kind must be 'reference' or 'command'")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "dt_s", _positive_float(self.dt_s, "dt_s"))
        if self.schema_version != TRAJECTORY_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {TRAJECTORY_SCHEMA_VERSION!r}"
            )
        semantics = _owned_mapping(
            self.channel_semantics,
            "channel_semantics",
        )
        expected_channels = {
            "position_rad",
            "velocity_rad_s",
            "acceleration_rad_s2",
            "jerk_rad_s3",
        }
        if set(semantics) != expected_channels:
            raise ValueError(
                "channel_semantics must describe exactly position_rad, "
                "velocity_rad_s, acceleration_rad_s2, and jerk_rad_s3"
            )
        semantics = {str(key): str(value) for key, value in semantics.items()}
        if any(not value for value in semantics.values()):
            raise ValueError("channel semantics cannot be empty")
        object.__setattr__(self, "channel_semantics", semantics)
        source = _owned_mapping(self.source, "source")
        generator_params = _owned_mapping(
            self.generator_params,
            "generator_params",
        )
        json.dumps(source, sort_keys=True, default=_json_default)
        json.dumps(generator_params, sort_keys=True, default=_json_default)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "generator_params", generator_params)
        if self.generator_id is not None:
            object.__setattr__(
                self,
                "generator_id",
                _identifier(self.generator_id, "generator_id"),
            )
        for name in ("source_sha256", "csv_sha256"):
            value = getattr(self, name)
            if value is None:
                continue
            normalized = str(value).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", normalized):
                raise ValueError(f"{name} must be a SHA-256 hex digest")
            object.__setattr__(self, name, normalized)

    @classmethod
    def for_trajectory(
        cls,
        trajectory: Trajectory,
        *,
        trajectory_id: str,
        kind: str = "reference",
        channel_semantics: Mapping[str, str] | None = None,
        source: Mapping[str, Any] | None = None,
        generator_id: str | None = None,
        generator_params: Mapping[str, Any] | None = None,
        source_sha256: str | None = None,
    ) -> TrajectoryMetadata:
        if channel_semantics is None:
            channel_semantics = {
                "position_rad": "truth",
                "velocity_rad_s": (
                    "truth" if trajectory.has_velocity else "unavailable"
                ),
                "acceleration_rad_s2": (
                    "truth" if trajectory.has_acceleration else "unavailable"
                ),
                "jerk_rad_s3": (
                    "truth" if trajectory.has_jerk else "unavailable"
                ),
            }
        return cls(
            trajectory_id=trajectory_id,
            kind=kind,
            dt_s=trajectory.dt,
            channel_semantics=channel_semantics,
            source=source or {},
            generator_id=generator_id,
            generator_params=generator_params or {},
            source_sha256=source_sha256,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trajectory_id": self.trajectory_id,
            "kind": self.kind,
            "dt_s": self.dt_s,
            "channel_semantics": dict(self.channel_semantics),
            "source": dict(self.source),
            "generator_id": self.generator_id,
            "generator_params": dict(self.generator_params),
            "source_sha256": self.source_sha256,
            "csv_sha256": self.csv_sha256,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> TrajectoryMetadata:
        if not isinstance(value, Mapping):
            raise TypeError("trajectory metadata must be a mapping")
        allowed = {
            "schema_version",
            "trajectory_id",
            "kind",
            "dt_s",
            "channel_semantics",
            "source",
            "generator_id",
            "generator_params",
            "source_sha256",
            "csv_sha256",
        }
        unexpected = set(value) - allowed
        if unexpected:
            raise ValueError(
                f"trajectory metadata contains unexpected keys: "
                f"{sorted(unexpected)}"
            )
        return cls(**dict(value))
