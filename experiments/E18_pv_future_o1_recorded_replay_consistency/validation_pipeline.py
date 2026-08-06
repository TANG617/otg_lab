"""E18 full-axis real-controller/local-Ruckig parity validation pipeline.

This retained supplemental module consumes controller-internal, call-by-call,
full-axis captures for the original four-synchronization study and local P/PV
ablation. Rebuilt E18's default ``experiment.py`` instead evaluates real No
against local No and uses :func:`validate_no_data_sufficiency`. A right-axis
snapshot fallback is supported only to describe the currently available four
CSV files and can never pass a formal data-sufficiency gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import struct
import sys
import sysconfig
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
import ruckig
from ruckig import (
    ControlInterface,
    DurationDiscretization,
    InputParameter,
    OutputParameter,
    Ruckig,
    Synchronization,
)

from otg_lab.confirmatory import finish_compact_run, start_compact_run
from otg_lab.experiment import ExperimentResult
from otg_lab.runio import sha256_file, sha256_json, write_json, write_rows_csv

EXPERIMENT_ID = "E18"
DIRECTORY_NAME = "E18_pv_future_o1_recorded_replay_consistency"
PIPELINE_TITLE = "E18 full-axis controller/replay parity validation"

CAPTURE_SCHEMA_VERSION = "e18.full_axis_capture.v1"
PIPELINE_SCHEMA_VERSION = "e18.validation_pipeline.v1"
REQUIRED_MODES = ("No", "Time", "TimeIfNecessary", "Phase")
MODE_SLUGS = {
    "No": "no",
    "Time": "time",
    "TimeIfNecessary": "time_if_necessary",
    "Phase": "phase",
}
SNAPSHOT_FILES = {
    "No": "none.csv",
    "Time": "time.csv",
    "TimeIfNecessary": "time_if_necessary.csv",
    "Phase": "phase.csv",
}

SOURCE_NOMINAL_DT_S = 0.01
SNAPSHOT_SEGMENT_GAP_S = 1.0
SNAPSHOT_GARBAGE_EXCLUSION_S = 3.0
LAG_DIAGNOSTIC_LIMIT_S = 0.250

GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_NOT_EVALUABLE = "not_evaluable"
GATE_NOT_RUN = "not_run"
DOWNSTREAM_ALLOWED = "allowed"
DOWNSTREAM_BLOCKED = "blocked_by_parity"


@dataclass(frozen=True)
class ParityThresholds:
    position_rad: float = 1e-12
    velocity_rad_s: float = 1e-10
    acceleration_rad_s2: float = 1e-8
    trajectory_duration_s: float = 1e-12

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{name} must be finite and nonnegative")


DEFAULT_THRESHOLDS = ParityThresholds()


CALL_FIELDS = (
    "run_id",
    "mode",
    "cycle_seq",
    "call_seq",
    "callback_source",
    "active_event_seq",
    "monotonic_time_s",
    "wall_delta_time_s",
    "ruckig_delta_time_s",
    "run_reset",
    "analysis_valid",
    "result_code",
    "result_name",
    "trajectory_duration_s",
    "trajectory_time_s",
    "new_calculation",
    "did_section_change",
    "new_section",
    "was_calculation_interrupted",
    "calculation_duration_us",
    "synchronization",
    "control_interface",
    "duration_discretization",
    "minimum_duration_s",
)

AXIS_STATE_FIELDS = (
    "run_id",
    "call_seq",
    "axis_index",
    "axis_name",
    "current_position_rad",
    "current_velocity_rad_s",
    "current_acceleration_rad_s2",
    "target_position_rad",
    "target_velocity_rad_s",
    "target_acceleration_rad_s2",
    "output_position_rad",
    "output_velocity_rad_s",
    "output_acceleration_rad_s2",
    "output_jerk_rad_s3",
    "max_velocity_rad_s",
    "min_velocity_rad_s",
    "max_acceleration_rad_s2",
    "min_acceleration_rad_s2",
    "max_jerk_rad_s3",
    "min_jerk_rad_s3",
    "enabled",
    "per_dof_synchronization",
    "per_dof_control_interface",
    "independent_min_duration_s",
)

RAW_POSITION_EVENT_FIELDS = (
    "run_id",
    "event_seq",
    "applied_call_seq",
    "axis_index",
    "axis_name",
    "monotonic_time_s",
    "position_rad",
)

SNAPSHOT_FIELDS = ("elapsed time", "timestamp", "topic", "value")
SNAPSHOT_INPUT_TOPIC = "/mc/ik/joint_states.position[$right_joint_id]"
SNAPSHOT_OUTPUT_TOPIC = (
    "/mc/joint_controller/ruckig_joint_states."
    "interface_values[$right_joint_id].values[0]"
)
SNAPSHOT_ECHO_TOPIC = (
    "/mc/joint_controller/ruckig_joint_states."
    "interface_values[$right_joint_id].values[4]"
)
SNAPSHOT_TOPICS = (
    SNAPSHOT_INPUT_TOPIC,
    SNAPSHOT_OUTPUT_TOPIC,
    SNAPSHOT_ECHO_TOPIC,
)


class CaptureValidationError(ValueError):
    """A formal capture cannot be evaluated without changing its meaning."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.context = dict(context or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": "data_sufficiency",
            "code": self.code,
            "message": str(self),
            "context": self.context,
        }


@dataclass(frozen=True)
class CaptureManifest:
    source_path: Path
    schema_version: str
    dof: int
    axis_names: tuple[str, ...]
    right_axis_index: int
    future_o1_h_s: float
    nominal_control_dt_s: float
    ruckig_version: str
    ruckig_commit: str
    capture_platform: str
    compiler: str
    floating_point_options: str
    runs: tuple[tuple[str, str], ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class CallRecord:
    run_id: str
    mode: str
    cycle_seq: int
    call_seq: int
    callback_source: str
    active_event_seq: int
    monotonic_time_s: float
    wall_delta_time_s: float
    ruckig_delta_time_s: float
    run_reset: bool
    analysis_valid: bool
    result_code: int
    result_name: str
    trajectory_duration_s: float
    trajectory_time_s: float
    new_calculation: bool
    did_section_change: bool
    new_section: int
    was_calculation_interrupted: bool
    calculation_duration_us: float
    synchronization: str
    control_interface: str
    duration_discretization: str
    minimum_duration_s: float | None


@dataclass(frozen=True)
class AxisStateRecord:
    run_id: str
    call_seq: int
    axis_index: int
    axis_name: str
    current_position_rad: float
    current_velocity_rad_s: float
    current_acceleration_rad_s2: float
    target_position_rad: float
    target_velocity_rad_s: float
    target_acceleration_rad_s2: float
    output_position_rad: float
    output_velocity_rad_s: float
    output_acceleration_rad_s2: float
    output_jerk_rad_s3: float
    max_velocity_rad_s: float
    min_velocity_rad_s: float | None
    max_acceleration_rad_s2: float
    min_acceleration_rad_s2: float | None
    max_jerk_rad_s3: float
    min_jerk_rad_s3: float
    enabled: bool
    per_dof_synchronization: str | None
    per_dof_control_interface: str | None
    independent_min_duration_s: float


@dataclass(frozen=True)
class RawPositionEvent:
    run_id: str
    event_seq: int
    applied_call_seq: int
    axis_index: int
    axis_name: str
    monotonic_time_s: float
    position_rad: float


@dataclass(frozen=True)
class FullAxisCapture:
    root: Path
    manifest: CaptureManifest
    calls: tuple[CallRecord, ...]
    axis_states: tuple[AxisStateRecord, ...]
    raw_position_events: tuple[RawPositionEvent, ...]

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(sorted({call.run_id for call in self.calls}))

    def calls_for_run(self, run_id: str) -> tuple[CallRecord, ...]:
        return tuple(call for call in self.calls if call.run_id == run_id)

    def axes_for_call(
        self, run_id: str, call_seq: int
    ) -> tuple[AxisStateRecord, ...]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.axis_states
                    if row.run_id == run_id and row.call_seq == call_seq
                ),
                key=lambda row: row.axis_index,
            )
        )

    def events_for_run(self, run_id: str) -> tuple[RawPositionEvent, ...]:
        return tuple(
            sorted(
                (row for row in self.raw_position_events if row.run_id == run_id),
                key=lambda row: (row.event_seq, row.axis_index),
            )
        )


@dataclass(frozen=True)
class SnapshotObservation:
    mode: str
    path: Path
    sha256: str
    size_bytes: int
    raw_row_count: int
    source_segment_count: int
    selected_segment_index: int
    selected_source_count: int
    selected_output_count: int
    selected_echo_count: int
    analysis_valid_source_count: int
    analysis_valid_output_count: int
    segment_start_s: float
    segment_end_s: float
    observation_end_s: float
    analysis_valid_start_s: float
    output_tick_coverage_fraction: float
    largest_output_gap_s: float
    formal_gate_eligible: bool = False


@dataclass(frozen=True)
class GateOutcome:
    gate: str
    status: str
    rows: tuple[Mapping[str, Any], ...]
    evaluated_point_count: int
    bitwise_equal: bool | None
    max_abs_errors: Mapping[str, float]
    first_mismatch: Mapping[str, Any] | None
    reason: str | None = None


@dataclass(frozen=True)
class ModeParityResult:
    run_id: str
    mode: str
    target_builder: GateOutcome
    solver_step: GateOutcome
    closed_loop: GateOutcome

    @property
    def passed(self) -> bool:
        return all(
            gate.status == GATE_PASS
            for gate in (self.target_builder, self.solver_step, self.closed_loop)
        )


@dataclass(frozen=True)
class ParityReport:
    modes: tuple[ModeParityResult, ...]

    @property
    def all_passed(self) -> bool:
        return len(self.modes) == len(REQUIRED_MODES) and all(
            item.passed for item in self.modes
        )

    @property
    def first_mismatch(self) -> Mapping[str, Any] | None:
        for mode in REQUIRED_MODES:
            item = next((row for row in self.modes if row.mode == mode), None)
            if item is None:
                continue
            for gate in (item.target_builder, item.solver_step, item.closed_loop):
                if gate.first_mismatch is not None:
                    return {
                        **gate.first_mismatch,
                        "gate_max_abs_errors": gate.max_abs_errors,
                    }
        return None


def _require_exact_header(path: Path, reader: csv.DictReader, expected: Sequence[str]) -> None:
    observed = tuple(reader.fieldnames or ())
    if observed != tuple(expected):
        raise CaptureValidationError(
            "invalid_header",
            f"{path} must use the exact header {','.join(expected)}",
            context={"path": path.as_posix(), "observed": list(observed)},
        )


def _text(row: Mapping[str, Any], field: str, *, row_number: int, path: Path) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise CaptureValidationError(
            "missing_value",
            f"empty {field} at row {row_number} in {path}",
            context={"path": path.as_posix(), "row": row_number, "field": field},
        )
    return value


def _integer(row: Mapping[str, Any], field: str, *, row_number: int, path: Path) -> int:
    value = _text(row, field, row_number=row_number, path=path)
    try:
        number = int(value)
    except ValueError as error:
        raise CaptureValidationError(
            "invalid_integer",
            f"invalid integer {field} at row {row_number} in {path}",
            context={"value": value},
        ) from error
    return number


def _finite(
    row: Mapping[str, Any],
    field: str,
    *,
    row_number: int,
    path: Path,
    nullable: bool = False,
) -> float | None:
    value = str(row.get(field, "")).strip()
    if nullable and value == "":
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise CaptureValidationError(
            "invalid_number",
            f"invalid numeric {field} at row {row_number} in {path}",
            context={"value": value},
        ) from error
    if not math.isfinite(number):
        raise CaptureValidationError(
            "nonfinite_number",
            f"non-finite {field} at row {row_number} in {path}",
            context={"value": value},
        )
    return number


def _boolean(row: Mapping[str, Any], field: str, *, row_number: int, path: Path) -> bool:
    value = _text(row, field, row_number=row_number, path=path).lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise CaptureValidationError(
        "invalid_boolean",
        f"invalid boolean {field} at row {row_number} in {path}",
        context={"value": value},
    )


def _nullable_enum(
    row: Mapping[str, Any],
    field: str,
    allowed: Sequence[str],
    *,
    row_number: int,
    path: Path,
) -> str | None:
    value = str(row.get(field, "")).strip()
    if not value:
        return None
    if value not in allowed:
        raise CaptureValidationError(
            "invalid_enum",
            f"invalid {field} at row {row_number} in {path}: {value}",
            context={"allowed": list(allowed)},
        )
    return value


def _load_manifest(path: Path) -> CaptureManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureValidationError(
            "invalid_manifest", f"cannot read {path}: {error}"
        ) from error
    if not isinstance(raw, dict):
        raise CaptureValidationError("invalid_manifest", "capture manifest must be an object")

    required_top = {
        "schema_version",
        "capture_kind",
        "dof",
        "axis_names",
        "right_axis_index",
        "future_o1_h_s",
        "nominal_control_dt_s",
        "ruckig",
        "build",
        "runs",
    }
    missing = sorted(required_top - set(raw))
    if missing:
        raise CaptureValidationError(
            "manifest_missing_fields",
            f"capture manifest is missing: {', '.join(missing)}",
            context={"missing": missing},
        )
    if raw["schema_version"] != CAPTURE_SCHEMA_VERSION:
        raise CaptureValidationError(
            "schema_version_mismatch",
            f"expected {CAPTURE_SCHEMA_VERSION}, got {raw['schema_version']}",
        )
    if raw["capture_kind"] != "controller_internal_full_axis":
        raise CaptureValidationError(
            "invalid_capture_kind",
            "formal parity requires capture_kind=controller_internal_full_axis",
        )
    try:
        dof = int(raw["dof"])
        axis_names = tuple(str(item) for item in raw["axis_names"])
        right_axis_index = int(raw["right_axis_index"])
        future_h = float(raw["future_o1_h_s"])
        control_dt = float(raw["nominal_control_dt_s"])
    except (TypeError, ValueError) as error:
        raise CaptureValidationError("invalid_manifest", "invalid manifest numeric field") from error
    if dof < 2:
        raise CaptureValidationError(
            "not_multiaxis", "formal E18 parity requires at least two axes"
        )
    if len(axis_names) != dof or len(set(axis_names)) != dof:
        raise CaptureValidationError(
            "invalid_axis_names", "axis_names must be unique and have length dof"
        )
    if not 0 <= right_axis_index < dof:
        raise CaptureValidationError("invalid_right_axis", "right_axis_index is out of range")
    if not math.isfinite(future_h) or abs(future_h - SOURCE_NOMINAL_DT_S) > 1e-15:
        raise CaptureValidationError(
            "future_o1_h_mismatch",
            f"future_o1_h_s must be exactly the deployed nominal {SOURCE_NOMINAL_DT_S}",
        )
    if not math.isfinite(control_dt) or control_dt <= 0.0:
        raise CaptureValidationError("invalid_control_dt", "nominal_control_dt_s must be positive")

    ruckig_info = raw["ruckig"]
    build = raw["build"]
    if not isinstance(ruckig_info, dict) or not isinstance(build, dict):
        raise CaptureValidationError("invalid_manifest", "ruckig and build must be objects")
    for group_name, group, fields in (
        ("ruckig", ruckig_info, ("version", "commit")),
        ("build", build, ("platform", "compiler", "floating_point_options")),
    ):
        group_missing = [field for field in fields if not str(group.get(field, "")).strip()]
        if group_missing:
            raise CaptureValidationError(
                "manifest_missing_fields",
                f"{group_name} is missing: {', '.join(group_missing)}",
                context={"group": group_name, "missing": group_missing},
            )

    if not isinstance(raw["runs"], list) or not raw["runs"]:
        raise CaptureValidationError("invalid_runs", "manifest runs must be a non-empty list")
    runs: list[tuple[str, str]] = []
    for item in raw["runs"]:
        if not isinstance(item, dict):
            raise CaptureValidationError("invalid_runs", "each manifest run must be an object")
        run_id = str(item.get("run_id", "")).strip()
        mode = str(item.get("mode", "")).strip()
        if not run_id or mode not in REQUIRED_MODES:
            raise CaptureValidationError(
                "invalid_runs", f"invalid manifest run declaration: {item}"
            )
        runs.append((run_id, mode))
    if len(set(run_id for run_id, _ in runs)) != len(runs):
        raise CaptureValidationError("duplicate_run_id", "manifest run_id values must be unique")

    return CaptureManifest(
        source_path=path,
        schema_version=str(raw["schema_version"]),
        dof=dof,
        axis_names=axis_names,
        right_axis_index=right_axis_index,
        future_o1_h_s=future_h,
        nominal_control_dt_s=control_dt,
        ruckig_version=str(ruckig_info["version"]).strip(),
        ruckig_commit=str(ruckig_info["commit"]).strip(),
        capture_platform=str(build["platform"]).strip(),
        compiler=str(build["compiler"]).strip(),
        floating_point_options=str(build["floating_point_options"]).strip(),
        runs=tuple(runs),
        raw=raw,
    )


def _load_calls(path: Path) -> tuple[CallRecord, ...]:
    rows: list[CallRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require_exact_header(path, reader, CALL_FIELDS)
        for row_number, row in enumerate(reader, start=2):
            mode = _text(row, "mode", row_number=row_number, path=path)
            synchronization = _text(
                row, "synchronization", row_number=row_number, path=path
            )
            control_interface = _text(
                row, "control_interface", row_number=row_number, path=path
            )
            duration_discretization = _text(
                row, "duration_discretization", row_number=row_number, path=path
            )
            if mode not in REQUIRED_MODES or synchronization not in REQUIRED_MODES:
                raise CaptureValidationError(
                    "invalid_enum", f"invalid mode/synchronization at row {row_number}"
                )
            if control_interface not in {"Position", "Velocity"}:
                raise CaptureValidationError(
                    "invalid_enum", f"invalid control_interface at row {row_number}"
                )
            if duration_discretization not in {"Continuous", "Discrete"}:
                raise CaptureValidationError(
                    "invalid_enum", f"invalid duration_discretization at row {row_number}"
                )
            rows.append(
                CallRecord(
                    run_id=_text(row, "run_id", row_number=row_number, path=path),
                    mode=mode,
                    cycle_seq=_integer(row, "cycle_seq", row_number=row_number, path=path),
                    call_seq=_integer(row, "call_seq", row_number=row_number, path=path),
                    callback_source=_text(
                        row, "callback_source", row_number=row_number, path=path
                    ),
                    active_event_seq=_integer(
                        row, "active_event_seq", row_number=row_number, path=path
                    ),
                    monotonic_time_s=float(
                        _finite(row, "monotonic_time_s", row_number=row_number, path=path)
                    ),
                    wall_delta_time_s=float(
                        _finite(
                            row, "wall_delta_time_s", row_number=row_number, path=path
                        )
                    ),
                    ruckig_delta_time_s=float(
                        _finite(
                            row,
                            "ruckig_delta_time_s",
                            row_number=row_number,
                            path=path,
                        )
                    ),
                    run_reset=_boolean(row, "run_reset", row_number=row_number, path=path),
                    analysis_valid=_boolean(
                        row, "analysis_valid", row_number=row_number, path=path
                    ),
                    result_code=_integer(
                        row, "result_code", row_number=row_number, path=path
                    ),
                    result_name=_text(
                        row, "result_name", row_number=row_number, path=path
                    ),
                    trajectory_duration_s=float(
                        _finite(
                            row,
                            "trajectory_duration_s",
                            row_number=row_number,
                            path=path,
                        )
                    ),
                    trajectory_time_s=float(
                        _finite(row, "trajectory_time_s", row_number=row_number, path=path)
                    ),
                    new_calculation=_boolean(
                        row, "new_calculation", row_number=row_number, path=path
                    ),
                    did_section_change=_boolean(
                        row, "did_section_change", row_number=row_number, path=path
                    ),
                    new_section=_integer(
                        row, "new_section", row_number=row_number, path=path
                    ),
                    was_calculation_interrupted=_boolean(
                        row,
                        "was_calculation_interrupted",
                        row_number=row_number,
                        path=path,
                    ),
                    calculation_duration_us=float(
                        _finite(
                            row,
                            "calculation_duration_us",
                            row_number=row_number,
                            path=path,
                        )
                    ),
                    synchronization=synchronization,
                    control_interface=control_interface,
                    duration_discretization=duration_discretization,
                    minimum_duration_s=_finite(
                        row,
                        "minimum_duration_s",
                        row_number=row_number,
                        path=path,
                        nullable=True,
                    ),
                )
            )
    if not rows:
        raise CaptureValidationError("empty_table", f"{path} is empty")
    return tuple(rows)


def _load_axis_states(path: Path) -> tuple[AxisStateRecord, ...]:
    rows: list[AxisStateRecord] = []
    synchronization_values = REQUIRED_MODES
    control_values = ("Position", "Velocity")
    numeric_fields = (
        "current_position_rad",
        "current_velocity_rad_s",
        "current_acceleration_rad_s2",
        "target_position_rad",
        "target_velocity_rad_s",
        "target_acceleration_rad_s2",
        "output_position_rad",
        "output_velocity_rad_s",
        "output_acceleration_rad_s2",
        "output_jerk_rad_s3",
        "max_velocity_rad_s",
        "max_acceleration_rad_s2",
        "max_jerk_rad_s3",
        "min_jerk_rad_s3",
        "independent_min_duration_s",
    )
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require_exact_header(path, reader, AXIS_STATE_FIELDS)
        for row_number, row in enumerate(reader, start=2):
            values = {
                field: float(_finite(row, field, row_number=row_number, path=path))
                for field in numeric_fields
            }
            rows.append(
                AxisStateRecord(
                    run_id=_text(row, "run_id", row_number=row_number, path=path),
                    call_seq=_integer(row, "call_seq", row_number=row_number, path=path),
                    axis_index=_integer(row, "axis_index", row_number=row_number, path=path),
                    axis_name=_text(row, "axis_name", row_number=row_number, path=path),
                    current_position_rad=values["current_position_rad"],
                    current_velocity_rad_s=values["current_velocity_rad_s"],
                    current_acceleration_rad_s2=values["current_acceleration_rad_s2"],
                    target_position_rad=values["target_position_rad"],
                    target_velocity_rad_s=values["target_velocity_rad_s"],
                    target_acceleration_rad_s2=values["target_acceleration_rad_s2"],
                    output_position_rad=values["output_position_rad"],
                    output_velocity_rad_s=values["output_velocity_rad_s"],
                    output_acceleration_rad_s2=values["output_acceleration_rad_s2"],
                    output_jerk_rad_s3=values["output_jerk_rad_s3"],
                    max_velocity_rad_s=values["max_velocity_rad_s"],
                    min_velocity_rad_s=_finite(
                        row,
                        "min_velocity_rad_s",
                        row_number=row_number,
                        path=path,
                        nullable=True,
                    ),
                    max_acceleration_rad_s2=values["max_acceleration_rad_s2"],
                    min_acceleration_rad_s2=_finite(
                        row,
                        "min_acceleration_rad_s2",
                        row_number=row_number,
                        path=path,
                        nullable=True,
                    ),
                    max_jerk_rad_s3=values["max_jerk_rad_s3"],
                    min_jerk_rad_s3=values["min_jerk_rad_s3"],
                    enabled=_boolean(row, "enabled", row_number=row_number, path=path),
                    per_dof_synchronization=_nullable_enum(
                        row,
                        "per_dof_synchronization",
                        synchronization_values,
                        row_number=row_number,
                        path=path,
                    ),
                    per_dof_control_interface=_nullable_enum(
                        row,
                        "per_dof_control_interface",
                        control_values,
                        row_number=row_number,
                        path=path,
                    ),
                    independent_min_duration_s=values["independent_min_duration_s"],
                )
            )
    if not rows:
        raise CaptureValidationError("empty_table", f"{path} is empty")
    return tuple(rows)


def _load_raw_position_events(path: Path) -> tuple[RawPositionEvent, ...]:
    rows: list[RawPositionEvent] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require_exact_header(path, reader, RAW_POSITION_EVENT_FIELDS)
        for row_number, row in enumerate(reader, start=2):
            rows.append(
                RawPositionEvent(
                    run_id=_text(row, "run_id", row_number=row_number, path=path),
                    event_seq=_integer(row, "event_seq", row_number=row_number, path=path),
                    applied_call_seq=_integer(
                        row, "applied_call_seq", row_number=row_number, path=path
                    ),
                    axis_index=_integer(row, "axis_index", row_number=row_number, path=path),
                    axis_name=_text(row, "axis_name", row_number=row_number, path=path),
                    monotonic_time_s=float(
                        _finite(row, "monotonic_time_s", row_number=row_number, path=path)
                    ),
                    position_rad=float(
                        _finite(row, "position_rad", row_number=row_number, path=path)
                    ),
                )
            )
    if not rows:
        raise CaptureValidationError("empty_table", f"{path} is empty")
    return tuple(rows)


def _validate_full_axis_capture(capture: FullAxisCapture) -> None:
    manifest = capture.manifest
    declared_runs = dict(manifest.runs)
    observed_runs = {call.run_id for call in capture.calls}
    if observed_runs != set(declared_runs):
        raise CaptureValidationError(
            "run_set_mismatch",
            "calls.csv run IDs do not match capture_manifest.json",
            context={
                "declared": sorted(declared_runs),
                "observed": sorted(observed_runs),
            },
        )

    call_keys: set[tuple[str, int]] = set()
    calls_by_run: dict[str, list[CallRecord]] = defaultdict(list)
    for call in capture.calls:
        key = (call.run_id, call.call_seq)
        if key in call_keys:
            raise CaptureValidationError(
                "duplicate_call", f"duplicate call key {key}", context={"key": key}
            )
        call_keys.add(key)
        calls_by_run[call.run_id].append(call)
        if call.mode != declared_runs[call.run_id]:
            raise CaptureValidationError(
                "mode_mismatch", f"call {key} mode differs from manifest"
            )
        if call.synchronization != call.mode:
            raise CaptureValidationError(
                "mode_mismatch",
                f"call {key} synchronization must equal its run mode",
            )
        if call.wall_delta_time_s <= 0.0 or call.ruckig_delta_time_s <= 0.0:
            raise CaptureValidationError(
                "invalid_delta_time",
                f"call {key} wall and Ruckig delta times must be positive",
            )
        if call.minimum_duration_s is not None and call.minimum_duration_s <= 0.0:
            raise CaptureValidationError(
                "invalid_minimum_duration",
                f"call {key} minimum_duration_s must be blank or positive",
            )

    for run_id, calls in calls_by_run.items():
        calls.sort(key=lambda item: item.call_seq)
        sequences = [call.call_seq for call in calls]
        if any(right != left + 1 for left, right in zip(sequences, sequences[1:])):
            raise CaptureValidationError(
                "missing_call_seq",
                f"run {run_id} has a missing or reordered call_seq",
                context={"call_seq": sequences},
            )
        if any(
            right.monotonic_time_s <= left.monotonic_time_s
            for left, right in zip(calls, calls[1:])
        ):
            raise CaptureValidationError(
                "nonmonotonic_call_time", f"run {run_id} call times are not increasing"
            )
        if any(
            right.cycle_seq < left.cycle_seq for left, right in zip(calls, calls[1:])
        ):
            raise CaptureValidationError(
                "nonmonotonic_cycle_seq", f"run {run_id} cycle_seq moves backwards"
            )
        cycle_sequences = sorted({call.cycle_seq for call in calls})
        if any(
            right != left + 1
            for left, right in zip(cycle_sequences, cycle_sequences[1:])
        ):
            raise CaptureValidationError(
                "missing_cycle_seq",
                f"run {run_id} has a missing cycle_seq",
                context={"cycle_seq": cycle_sequences},
            )
        reset_calls = [call for call in calls if call.run_reset]
        if len(reset_calls) != 1 or reset_calls[0].call_seq != calls[0].call_seq:
            raise CaptureValidationError(
                "invalid_run_reset",
                f"run {run_id} needs exactly one run_reset on its first call",
            )
        valid_flags = [call.analysis_valid for call in calls]
        if not any(valid_flags):
            raise CaptureValidationError(
                "missing_analysis_valid", f"run {run_id} has no analysis_valid call"
            )
        first_valid = valid_flags.index(True)
        if not all(valid_flags[first_valid:]):
            raise CaptureValidationError(
                "nonmonotonic_analysis_valid",
                f"run {run_id} analysis_valid must change false-to-true at most once",
            )

    axis_keys: set[tuple[str, int, int]] = set()
    axes_by_call: dict[tuple[str, int], list[AxisStateRecord]] = defaultdict(list)
    for axis in capture.axis_states:
        key = (axis.run_id, axis.call_seq, axis.axis_index)
        if key in axis_keys:
            raise CaptureValidationError(
                "duplicate_axis_state", f"duplicate axis-state key {key}"
            )
        axis_keys.add(key)
        if (axis.run_id, axis.call_seq) not in call_keys:
            raise CaptureValidationError(
                "orphan_axis_state", f"axis state references unknown call {key[:2]}"
            )
        if not 0 <= axis.axis_index < manifest.dof:
            raise CaptureValidationError("invalid_axis_index", f"invalid axis index in {key}")
        if axis.axis_name != manifest.axis_names[axis.axis_index]:
            raise CaptureValidationError("axis_name_mismatch", f"axis name mismatch in {key}")
        if axis.max_velocity_rad_s <= 0.0 or axis.max_acceleration_rad_s2 <= 0.0:
            raise CaptureValidationError(
                "invalid_limits", f"nonpositive maximum V/A in {key}"
            )
        if axis.max_jerk_rad_s3 <= 0.0:
            raise CaptureValidationError("invalid_limits", f"nonpositive max jerk in {key}")
        if (
            axis.min_velocity_rad_s is not None
            and axis.min_velocity_rad_s >= 0.0
        ) or (
            axis.min_acceleration_rad_s2 is not None
            and axis.min_acceleration_rad_s2 >= 0.0
        ):
            raise CaptureValidationError("invalid_limits", f"minimum V/A must be negative in {key}")
        if not math.isclose(
            axis.min_jerk_rad_s3,
            -axis.max_jerk_rad_s3,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise CaptureValidationError(
                "asymmetric_jerk_unsupported",
                f"Ruckig 0.17 Python input only exposes symmetric jerk in {key}",
            )
        axes_by_call[(axis.run_id, axis.call_seq)].append(axis)
    for call_key in call_keys:
        observed = sorted(axis.axis_index for axis in axes_by_call.get(call_key, ()))
        expected = list(range(manifest.dof))
        if observed != expected:
            raise CaptureValidationError(
                "incomplete_axis_set",
                f"call {call_key} does not contain every axis exactly once",
                context={"expected": expected, "observed": observed},
            )
        axes = sorted(axes_by_call[call_key], key=lambda item: item.axis_index)
        for attribute in ("min_velocity_rad_s", "min_acceleration_rad_s2"):
            values = [getattr(axis, attribute) for axis in axes]
            if any(value is None for value in values) and not all(
                value is None for value in values
            ):
                raise CaptureValidationError(
                    "partial_optional_limit",
                    f"{attribute} must be blank for all axes or populated for all axes at {call_key}",
                )
        for attribute in ("per_dof_synchronization", "per_dof_control_interface"):
            values = [getattr(axis, attribute) for axis in axes]
            if any(value is None for value in values) and not all(
                value is None for value in values
            ):
                raise CaptureValidationError(
                    "partial_per_dof_configuration",
                    f"{attribute} must be blank for all axes or populated for all axes at {call_key}",
                )

    event_keys: set[tuple[str, int, int]] = set()
    events_by_run_seq: dict[tuple[str, int], list[RawPositionEvent]] = defaultdict(list)
    for event in capture.raw_position_events:
        key = (event.run_id, event.event_seq, event.axis_index)
        if key in event_keys:
            raise CaptureValidationError(
                "duplicate_position_event", f"duplicate raw-position event key {key}"
            )
        event_keys.add(key)
        if event.run_id not in declared_runs:
            raise CaptureValidationError("orphan_position_event", f"unknown run in {key}")
        if (event.run_id, event.applied_call_seq) not in call_keys:
            raise CaptureValidationError(
                "orphan_position_event",
                f"raw-position event {key} references an unknown applied call",
            )
        if not 0 <= event.axis_index < manifest.dof:
            raise CaptureValidationError("invalid_axis_index", f"invalid axis index in {key}")
        if event.axis_name != manifest.axis_names[event.axis_index]:
            raise CaptureValidationError("axis_name_mismatch", f"axis name mismatch in {key}")
        events_by_run_seq[(event.run_id, event.event_seq)].append(event)
    for key, rows in events_by_run_seq.items():
        observed = sorted(row.axis_index for row in rows)
        if observed != list(range(manifest.dof)):
            raise CaptureValidationError(
                "incomplete_position_event",
                f"position event {key} does not contain every axis",
            )
        applied = {row.applied_call_seq for row in rows}
        timestamps = {row.monotonic_time_s for row in rows}
        if len(applied) != 1 or len(timestamps) != 1:
            raise CaptureValidationError(
                "inconsistent_position_event",
                f"position event {key} has inconsistent call/time metadata",
            )

    for run_id, calls in calls_by_run.items():
        events = sorted(
            (key[1], rows[0].applied_call_seq) for key, rows in events_by_run_seq.items() if key[0] == run_id
        )
        if not events:
            raise CaptureValidationError("missing_position_events", f"run {run_id} has no events")
        event_seq = [item[0] for item in events]
        if any(right != left + 1 for left, right in zip(event_seq, event_seq[1:])):
            raise CaptureValidationError(
                "missing_event_seq", f"run {run_id} event_seq is not contiguous"
            )
        if any(right[1] < left[1] for left, right in zip(events, events[1:])):
            raise CaptureValidationError(
                "reordered_position_event", f"run {run_id} events move backwards in call order"
            )
        if events[0][1] != calls[0].call_seq:
            raise CaptureValidationError(
                "missing_initial_target",
                f"run {run_id} needs a full-axis position event on its reset call",
            )
        active: int | None = None
        events_at_call: dict[int, list[int]] = defaultdict(list)
        for sequence, call_seq in events:
            events_at_call[call_seq].append(sequence)
        for call in calls:
            if call.call_seq in events_at_call:
                active = max(events_at_call[call.call_seq])
            if call.active_event_seq != active:
                raise CaptureValidationError(
                    "active_event_mismatch",
                    f"run {run_id} call {call.call_seq} active_event_seq does not match callback order",
                    context={"recorded": call.active_event_seq, "expected": active},
                )


def load_full_axis_capture(root: str | Path) -> FullAxisCapture:
    """Load and strictly validate a controller-internal full-axis capture."""

    capture_root = Path(root).resolve()
    required = {
        "capture_manifest.json": capture_root / "capture_manifest.json",
        "calls.csv": capture_root / "calls.csv",
        "axis_states.csv": capture_root / "axis_states.csv",
        "raw_position_events.csv": capture_root / "raw_position_events.csv",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise CaptureValidationError(
            "missing_capture_files",
            f"full-axis capture is missing: {', '.join(missing)}",
            context={"root": capture_root.as_posix(), "missing": missing},
        )
    capture = FullAxisCapture(
        root=capture_root,
        manifest=_load_manifest(required["capture_manifest.json"]),
        calls=_load_calls(required["calls.csv"]),
        axis_states=_load_axis_states(required["axis_states.csv"]),
        raw_position_events=_load_raw_position_events(
            required["raw_position_events.csv"]
        ),
    )
    _validate_full_axis_capture(capture)
    return capture


def collect_local_ruckig_build() -> dict[str, Any]:
    try:
        package_version = version("ruckig")
    except PackageNotFoundError:
        package_version = "unknown"
    binary = Path(getattr(ruckig, "__file__", ""))
    return {
        "version": package_version,
        "module_version": getattr(ruckig, "__version__", "unknown"),
        "commit": "unknown",
        "binary_path": binary.as_posix() if binary.is_file() else None,
        "binary_sha256": sha256_file(binary) if binary.is_file() else None,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_compiler": platform.python_compiler(),
        "compiler": sysconfig.get_config_var("CC"),
        "cflags": sysconfig.get_config_var("CFLAGS"),
        "float_info": {
            "mant_dig": sys.float_info.mant_dig,
            "epsilon": sys.float_info.epsilon,
            "rounds": sys.float_info.rounds,
        },
    }


def _normalize_snapshot_topic(topic: str) -> str:
    value = str(topic).strip()
    return value[2:] if value.startswith("/A/") else value


def inspect_right_axis_snapshot(path: str | Path, mode: str) -> SnapshotObservation:
    """Describe the final source segment of one cumulative right-axis snapshot."""

    if mode not in REQUIRED_MODES:
        raise ValueError(f"unknown synchronization mode: {mode}")
    source_path = Path(path).resolve()
    grouped: dict[str, list[tuple[float, float]]] = {
        topic: [] for topic in SNAPSHOT_TOPICS
    }
    row_count = 0
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != SNAPSHOT_FIELDS:
            raise ValueError(f"{source_path} has an invalid snapshot header")
        for row_count, row in enumerate(reader, start=1):
            topic = _normalize_snapshot_topic(str(row["topic"]))
            if topic not in grouped:
                raise ValueError(f"unexpected topic in {source_path}: {topic}")
            try:
                elapsed = float(row["elapsed time"])
                value = float(row["value"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid numeric row {row_count} in {source_path}") from error
            if not math.isfinite(elapsed) or not math.isfinite(value):
                raise ValueError(f"non-finite row {row_count} in {source_path}")
            grouped[topic].append((elapsed, value))
    for topic, rows in grouped.items():
        if not rows:
            raise ValueError(f"missing snapshot topic {topic} in {source_path}")
        elapsed = np.asarray([row[0] for row in rows], dtype=np.float64)
        if np.any(np.diff(elapsed) <= 0.0):
            raise ValueError(f"snapshot topic is not strictly increasing: {topic}")

    source_elapsed = np.asarray(
        [row[0] for row in grouped[SNAPSHOT_INPUT_TOPIC]], dtype=np.float64
    )
    boundaries = np.flatnonzero(np.diff(source_elapsed) > SNAPSHOT_SEGMENT_GAP_S) + 1
    segments = np.split(np.arange(source_elapsed.size), boundaries)
    selected = segments[-1]
    start = float(source_elapsed[selected[0]])
    end = float(source_elapsed[selected[-1]])
    selected_source_elapsed = source_elapsed[selected]
    source_dt = (
        float(np.median(np.diff(selected_source_elapsed)))
        if selected_source_elapsed.size > 1
        else SOURCE_NOMINAL_DT_S
    )
    observation_end = end + source_dt
    valid_start = min(end, start + SNAPSHOT_GARBAGE_EXCLUSION_S)

    def window(topic: str, lower: float) -> np.ndarray:
        elapsed = np.asarray([row[0] for row in grouped[topic]], dtype=np.float64)
        return elapsed[(elapsed >= lower) & (elapsed <= observation_end)]

    selected_output = window(SNAPSHOT_OUTPUT_TOPIC, start)
    selected_echo = window(SNAPSHOT_ECHO_TOPIC, start)
    valid_source = source_elapsed[(source_elapsed >= valid_start) & (source_elapsed <= end)]
    valid_output = window(SNAPSHOT_OUTPUT_TOPIC, valid_start)
    if selected_output.size > 1:
        output_gaps = np.diff(selected_output)
        largest_gap = float(np.max(output_gaps))
        expected_ticks = int(np.rint((selected_output[-1] - selected_output[0]) / 0.001)) + 1
        coverage = float(selected_output.size / expected_ticks)
    else:
        largest_gap = 0.0
        coverage = 0.0
    return SnapshotObservation(
        mode=mode,
        path=source_path,
        sha256=sha256_file(source_path),
        size_bytes=source_path.stat().st_size,
        raw_row_count=row_count,
        source_segment_count=len(segments),
        selected_segment_index=len(segments) - 1,
        selected_source_count=int(selected.size),
        selected_output_count=int(selected_output.size),
        selected_echo_count=int(selected_echo.size),
        analysis_valid_source_count=int(valid_source.size),
        analysis_valid_output_count=int(valid_output.size),
        segment_start_s=start,
        segment_end_s=end,
        observation_end_s=observation_end,
        analysis_valid_start_s=valid_start,
        output_tick_coverage_fraction=coverage,
        largest_output_gap_s=largest_gap,
    )


def inspect_snapshot_directory(root: str | Path) -> tuple[SnapshotObservation, ...]:
    snapshot_root = Path(root).resolve()
    rows: list[SnapshotObservation] = []
    for mode in REQUIRED_MODES:
        path = snapshot_root / SNAPSHOT_FILES[mode]
        if not path.is_file():
            raise CaptureValidationError(
                "missing_snapshot",
                f"missing exploratory snapshot: {path}",
                context={"mode": mode},
            )
        rows.append(inspect_right_axis_snapshot(path, mode))
    return tuple(rows)


def _events_by_run_sequence(
    capture: FullAxisCapture, run_id: str
) -> list[tuple[int, int, float, np.ndarray]]:
    grouped: dict[int, list[RawPositionEvent]] = defaultdict(list)
    for event in capture.events_for_run(run_id):
        grouped[event.event_seq].append(event)
    result: list[tuple[int, int, float, np.ndarray]] = []
    for event_seq in sorted(grouped):
        rows = sorted(grouped[event_seq], key=lambda row: row.axis_index)
        result.append(
            (
                event_seq,
                rows[0].applied_call_seq,
                rows[0].monotonic_time_s,
                np.asarray([row.position_rad for row in rows], dtype=np.float64),
            )
        )
    return result


def build_local_target_sequence(
    capture: FullAxisCapture,
    run_id: str,
    *,
    strategy: str = "pv_future_o1_live",
) -> dict[int, dict[str, Any]]:
    """Build the held target at every recorded call from reset onward."""

    if strategy not in {"pv_future_o1_live", "p_only", "predictor_p_legacy"}:
        raise ValueError(f"unknown target strategy: {strategy}")
    calls = capture.calls_for_run(run_id)
    events = _events_by_run_sequence(capture, run_id)
    events_at_call: dict[int, list[tuple[int, float, np.ndarray]]] = defaultdict(list)
    for event_seq, call_seq, event_time, position in events:
        events_at_call[call_seq].append((event_seq, event_time, position))
    history: deque[np.ndarray] = deque(maxlen=3)
    active: dict[str, Any] | None = None
    result: dict[int, dict[str, Any]] = {}
    h = capture.manifest.future_o1_h_s
    for call in calls:
        applied: list[int] = []
        for event_seq, event_time, raw_position in sorted(
            events_at_call.get(call.call_seq, ()), key=lambda item: item[0]
        ):
            history.append(np.array(raw_position, copy=True))
            applied.append(event_seq)
            if strategy == "p_only":
                target_position = np.array(raw_position, copy=True)
                target_velocity = np.zeros(capture.manifest.dof, dtype=np.float64)
                startup = len(history) < 3
            elif len(history) < 3:
                target_position = np.array(raw_position, copy=True)
                target_velocity = np.zeros(capture.manifest.dof, dtype=np.float64)
                startup = True
            else:
                p2, p1, p0 = history
                target_position = 3.0 * p0 - 3.0 * p1 + p2
                target_velocity = (2.0 * p0 - 3.0 * p1 + p2) / h
                startup = False
                if strategy == "predictor_p_legacy":
                    target_velocity = np.zeros(capture.manifest.dof, dtype=np.float64)
            active = {
                "event_seq": event_seq,
                "event_time_s": event_time,
                "raw_position": np.array(raw_position, copy=True),
                "target_position": target_position,
                "target_velocity": target_velocity,
                "target_acceleration": np.zeros(
                    capture.manifest.dof, dtype=np.float64
                ),
                "startup": startup,
            }
        if active is None:
            raise CaptureValidationError(
                "missing_initial_target",
                f"run {run_id} has no target at call {call.call_seq}",
            )
        result[call.call_seq] = {
            **active,
            "event_seq": int(active["event_seq"]),
            "events_applied": tuple(applied),
            "held": not bool(applied),
            "active_event_matches_call": int(active["event_seq"])
            == call.active_event_seq,
        }
    return result


SYNCHRONIZATION_ENUMS = {
    "No": Synchronization.No,
    "Time": Synchronization.Time,
    "TimeIfNecessary": Synchronization.TimeIfNecessary,
    "Phase": Synchronization.Phase,
}
CONTROL_INTERFACE_ENUMS = {
    "Position": ControlInterface.Position,
    "Velocity": ControlInterface.Velocity,
}
DURATION_DISCRETIZATION_ENUMS = {
    "Continuous": DurationDiscretization.Continuous,
    "Discrete": DurationDiscretization.Discrete,
}
RESULT_NAMES = {
    1: "Finished",
    0: "Working",
    -1: "Error",
    -100: "ErrorInvalidInput",
    -101: "ErrorTrajectoryDuration",
    -102: "ErrorPositionalLimits",
    -104: "ErrorZeroLimits",
    -110: "ErrorExecutionTimeCalculation",
    -111: "ErrorSynchronizationCalculation",
}


@dataclass(frozen=True)
class SolverSnapshot:
    result_code: int
    result_name: str
    position: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray
    jerk: np.ndarray
    trajectory_duration_s: float
    trajectory_time_s: float
    independent_min_durations_s: np.ndarray
    new_calculation: bool
    did_section_change: bool
    new_section: int
    was_calculation_interrupted: bool
    calculation_duration_us: float


class _RuckigRunEngine:
    """One persistent update()-based Ruckig instance for one captured run."""

    def __init__(self, dof: int, initial_dt_s: float) -> None:
        self.dof = int(dof)
        self.otg = Ruckig(self.dof, float(initial_dt_s))
        self.input = InputParameter(self.dof)
        self.output = OutputParameter(self.dof)

    @staticmethod
    def _optional_axis_values(
        axes: Sequence[AxisStateRecord], attribute: str
    ) -> list[float] | None:
        values = [getattr(axis, attribute) for axis in axes]
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise CaptureValidationError(
                "partial_optional_limit",
                f"{attribute} must be absent for every axis or present for every axis",
            )
        return [float(value) for value in values]

    @staticmethod
    def _optional_enums(
        axes: Sequence[AxisStateRecord],
        attribute: str,
        mapping: Mapping[str, Any],
        override: str | None = None,
    ) -> list[Any] | None:
        values = [getattr(axis, attribute) for axis in axes]
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise CaptureValidationError(
                "partial_per_dof_configuration",
                f"{attribute} must be absent for every axis or present for every axis",
            )
        if override is not None:
            return [mapping[override] for _ in values]
        return [mapping[str(value)] for value in values]

    def step(
        self,
        call: CallRecord,
        axes: Sequence[AxisStateRecord],
        current: np.ndarray,
        target: np.ndarray,
        *,
        synchronization_override: str | None = None,
    ) -> SolverSnapshot:
        if current.shape != (self.dof, 3) or target.shape != (self.dof, 3):
            raise ValueError("current and target must have shape (dof, 3)")
        if not np.all(np.isfinite(current)) or not np.all(np.isfinite(target)):
            raise ValueError("current and target must be finite")
        if len(axes) != self.dof:
            raise ValueError("axis configuration must have exactly dof rows")

        self.otg.delta_time = float(call.ruckig_delta_time_s)
        inp = self.input
        inp.current_position = current[:, 0].tolist()
        inp.current_velocity = current[:, 1].tolist()
        inp.current_acceleration = current[:, 2].tolist()
        inp.target_position = target[:, 0].tolist()
        inp.target_velocity = target[:, 1].tolist()
        inp.target_acceleration = target[:, 2].tolist()
        inp.max_velocity = [axis.max_velocity_rad_s for axis in axes]
        inp.min_velocity = self._optional_axis_values(axes, "min_velocity_rad_s")
        inp.max_acceleration = [axis.max_acceleration_rad_s2 for axis in axes]
        inp.min_acceleration = self._optional_axis_values(
            axes, "min_acceleration_rad_s2"
        )
        inp.max_jerk = [axis.max_jerk_rad_s3 for axis in axes]
        inp.enabled = [axis.enabled for axis in axes]
        synchronization = synchronization_override or call.synchronization
        inp.synchronization = SYNCHRONIZATION_ENUMS[synchronization]
        inp.per_dof_synchronization = self._optional_enums(
            axes,
            "per_dof_synchronization",
            SYNCHRONIZATION_ENUMS,
            override=synchronization_override,
        )
        inp.control_interface = CONTROL_INTERFACE_ENUMS[call.control_interface]
        inp.per_dof_control_interface = self._optional_enums(
            axes,
            "per_dof_control_interface",
            CONTROL_INTERFACE_ENUMS,
        )
        inp.duration_discretization = DURATION_DISCRETIZATION_ENUMS[
            call.duration_discretization
        ]
        inp.minimum_duration = call.minimum_duration_s

        result = self.otg.update(inp, self.output)
        code = int(result)
        independent = np.asarray(
            self.output.trajectory.independent_min_durations, dtype=np.float64
        )
        snapshot = SolverSnapshot(
            result_code=code,
            result_name=RESULT_NAMES.get(code, str(result).split(".")[-1]),
            position=np.asarray(self.output.new_position, dtype=np.float64),
            velocity=np.asarray(self.output.new_velocity, dtype=np.float64),
            acceleration=np.asarray(self.output.new_acceleration, dtype=np.float64),
            jerk=np.asarray(self.output.new_jerk, dtype=np.float64),
            trajectory_duration_s=float(self.output.trajectory.duration),
            trajectory_time_s=float(self.output.time),
            independent_min_durations_s=independent,
            new_calculation=bool(self.output.new_calculation),
            did_section_change=bool(self.output.did_section_change),
            new_section=int(self.output.new_section),
            was_calculation_interrupted=bool(
                self.output.was_calculation_interrupted
            ),
            calculation_duration_us=float(self.output.calculation_duration),
        )
        self.output.pass_to_input(inp)
        return snapshot


def _recorded_current(axes: Sequence[AxisStateRecord]) -> np.ndarray:
    return np.asarray(
        [
            (
                axis.current_position_rad,
                axis.current_velocity_rad_s,
                axis.current_acceleration_rad_s2,
            )
            for axis in axes
        ],
        dtype=np.float64,
    )


def _recorded_target(axes: Sequence[AxisStateRecord]) -> np.ndarray:
    return np.asarray(
        [
            (
                axis.target_position_rad,
                axis.target_velocity_rad_s,
                axis.target_acceleration_rad_s2,
            )
            for axis in axes
        ],
        dtype=np.float64,
    )


def _recorded_output(axes: Sequence[AxisStateRecord]) -> np.ndarray:
    return np.asarray(
        [
            (
                axis.output_position_rad,
                axis.output_velocity_rad_s,
                axis.output_acceleration_rad_s2,
            )
            for axis in axes
        ],
        dtype=np.float64,
    )


def _target_matrix(target: Mapping[str, Any]) -> np.ndarray:
    return np.column_stack(
        (
            np.asarray(target["target_position"], dtype=np.float64),
            np.asarray(target["target_velocity"], dtype=np.float64),
            np.asarray(target["target_acceleration"], dtype=np.float64),
        )
    )


def _same_bits(left: float, right: float) -> bool:
    return struct.pack("!d", float(left)) == struct.pack("!d", float(right))


def _float_error(local: float, recorded: float) -> tuple[float, float]:
    error = float(local) - float(recorded)
    return error, abs(error)


def _status_gate(
    gate: str,
    status: str,
    reason: str,
    *,
    run_id: str,
    mode: str,
) -> GateOutcome:
    row = {
        "run_id": run_id,
        "mode": mode,
        "gate": gate,
        "status": status,
        "reason": reason,
        "analysis_valid": False,
        "evaluated": False,
        "pointwise_pass": False,
    }
    return GateOutcome(
        gate=gate,
        status=status,
        rows=(row,),
        evaluated_point_count=0,
        bitwise_equal=None,
        max_abs_errors={},
        first_mismatch=None,
        reason=reason,
    )


def _axis_context(axis: AxisStateRecord) -> dict[str, Any]:
    return {
        "axis_index": axis.axis_index,
        "axis_name": axis.axis_name,
        "current": {
            "position_rad": axis.current_position_rad,
            "velocity_rad_s": axis.current_velocity_rad_s,
            "acceleration_rad_s2": axis.current_acceleration_rad_s2,
        },
        "target": {
            "position_rad": axis.target_position_rad,
            "velocity_rad_s": axis.target_velocity_rad_s,
            "acceleration_rad_s2": axis.target_acceleration_rad_s2,
        },
        "output": {
            "position_rad": axis.output_position_rad,
            "velocity_rad_s": axis.output_velocity_rad_s,
            "acceleration_rad_s2": axis.output_acceleration_rad_s2,
            "jerk_rad_s3": axis.output_jerk_rad_s3,
        },
        "limits": {
            "max_velocity_rad_s": axis.max_velocity_rad_s,
            "min_velocity_rad_s": axis.min_velocity_rad_s,
            "max_acceleration_rad_s2": axis.max_acceleration_rad_s2,
            "min_acceleration_rad_s2": axis.min_acceleration_rad_s2,
            "max_jerk_rad_s3": axis.max_jerk_rad_s3,
            "min_jerk_rad_s3": axis.min_jerk_rad_s3,
        },
        "enabled": axis.enabled,
        "per_dof_synchronization": axis.per_dof_synchronization,
        "per_dof_control_interface": axis.per_dof_control_interface,
    }


def _call_context(
    capture: FullAxisCapture,
    run_id: str,
    center_call_seq: int,
    *,
    radius: int = 2,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for call in capture.calls_for_run(run_id):
        if abs(call.call_seq - center_call_seq) > radius:
            continue
        rows.append(
            {
                "call": asdict(call),
                "axes": [
                    _axis_context(axis)
                    for axis in capture.axes_for_call(run_id, call.call_seq)
                ],
            }
        )
    return rows


def _first_mismatch_from_rows(
    capture: FullAxisCapture,
    run_id: str,
    mode: str,
    gate: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for row in rows:
        if not bool(row.get("evaluated")) or bool(row.get("pointwise_pass")):
            continue
        call_seq = int(row["call_seq"])
        mismatch = {
            "classification": str(row.get("mismatch_classification", gate)),
            "gate": gate,
            "run_id": run_id,
            "mode": mode,
            "call_seq": call_seq,
            "cycle_seq": row.get("cycle_seq"),
            "axis_index": row.get("axis_index"),
            "axis_name": row.get("axis_name"),
            "callback_source": row.get("callback_source"),
            "active_event_seq": row.get("active_event_seq"),
            "mismatch_components": row.get("mismatch_components", ()),
            "recorded": row.get("recorded_values", {}),
            "local": row.get("local_values", {}),
            "errors": row.get("errors", {}),
            "tolerances": row.get("tolerances", {}),
            "constraints": row.get("constraints", {}),
            "context_calls": _call_context(capture, run_id, call_seq),
        }
        if row.get("solver_exception"):
            mismatch["solver_exception"] = row["solver_exception"]
        return mismatch
    return None


def _outcome_from_rows(
    capture: FullAxisCapture,
    run_id: str,
    mode: str,
    gate: str,
    rows: Sequence[Mapping[str, Any]],
    error_fields: Sequence[str],
) -> GateOutcome:
    evaluated = [row for row in rows if bool(row.get("evaluated"))]
    passed = bool(evaluated) and all(
        bool(row.get("pointwise_pass")) for row in evaluated
    )
    max_errors: dict[str, float] = {}
    for field in error_fields:
        values = [
            float(row["errors"][field])
            for row in evaluated
            if field in row.get("errors", {})
            and row["errors"][field] is not None
        ]
        if values:
            max_errors[field] = max(abs(value) for value in values)
    bitwise_values = [
        bool(value)
        for row in evaluated
        for value in row.get("bitwise", {}).values()
    ]
    status = GATE_PASS if passed else GATE_FAIL
    mismatch = _first_mismatch_from_rows(capture, run_id, mode, gate, rows)
    return GateOutcome(
        gate=gate,
        status=status,
        rows=tuple(rows),
        evaluated_point_count=len(evaluated),
        bitwise_equal=(all(bitwise_values) if bitwise_values else None),
        max_abs_errors=max_errors,
        first_mismatch=mismatch,
        reason=None if passed else "at least one evaluated point failed",
    )


def _constraints(axis: AxisStateRecord) -> dict[str, Any]:
    return {
        "max_velocity_rad_s": axis.max_velocity_rad_s,
        "min_velocity_rad_s": axis.min_velocity_rad_s,
        "max_acceleration_rad_s2": axis.max_acceleration_rad_s2,
        "min_acceleration_rad_s2": axis.min_acceleration_rad_s2,
        "max_jerk_rad_s3": axis.max_jerk_rad_s3,
        "min_jerk_rad_s3": axis.min_jerk_rad_s3,
        "enabled": axis.enabled,
        "per_dof_synchronization": axis.per_dof_synchronization,
        "per_dof_control_interface": axis.per_dof_control_interface,
    }


def _run_target_builder_gate(
    capture: FullAxisCapture,
    run_id: str,
    thresholds: ParityThresholds,
) -> tuple[GateOutcome, dict[int, dict[str, Any]]]:
    calls = capture.calls_for_run(run_id)
    mode = calls[0].mode
    targets = build_local_target_sequence(capture, run_id)
    rows: list[dict[str, Any]] = []
    for call in calls:
        local = targets[call.call_seq]
        local_matrix = _target_matrix(local)
        for axis in capture.axes_for_call(run_id, call.call_seq):
            index = axis.axis_index
            recorded_values = {
                "target_position_rad": axis.target_position_rad,
                "target_velocity_rad_s": axis.target_velocity_rad_s,
                "target_acceleration_rad_s2": axis.target_acceleration_rad_s2,
                "active_event_seq": call.active_event_seq,
            }
            local_values = {
                "target_position_rad": float(local_matrix[index, 0]),
                "target_velocity_rad_s": float(local_matrix[index, 1]),
                "target_acceleration_rad_s2": float(local_matrix[index, 2]),
                "active_event_seq": int(local["event_seq"]),
            }
            errors = {
                "target_position_rad": local_values["target_position_rad"]
                - recorded_values["target_position_rad"],
                "target_velocity_rad_s": local_values["target_velocity_rad_s"]
                - recorded_values["target_velocity_rad_s"],
                "target_acceleration_rad_s2": local_values[
                    "target_acceleration_rad_s2"
                ]
                - recorded_values["target_acceleration_rad_s2"],
            }
            tolerances = {
                "target_position_rad": thresholds.position_rad,
                "target_velocity_rad_s": thresholds.velocity_rad_s,
                "target_acceleration_rad_s2": thresholds.acceleration_rad_s2,
            }
            matches = {
                name: abs(errors[name]) <= tolerance
                for name, tolerance in tolerances.items()
            }
            matches["active_event_seq"] = bool(local["active_event_matches_call"])
            evaluated = call.analysis_valid
            mismatch_components = tuple(
                name for name, matched in matches.items() if not matched
            )
            rows.append(
                {
                    "run_id": run_id,
                    "mode": mode,
                    "gate": "target_builder",
                    "cycle_seq": call.cycle_seq,
                    "call_seq": call.call_seq,
                    "callback_source": call.callback_source,
                    "active_event_seq": call.active_event_seq,
                    "events_applied": local["events_applied"],
                    "target_held": local["held"],
                    "predictor_startup": local["startup"],
                    "axis_index": index,
                    "axis_name": axis.axis_name,
                    "analysis_valid": call.analysis_valid,
                    "evaluated": evaluated,
                    "pointwise_pass": (all(matches.values()) if evaluated else True),
                    "mismatch_classification": "target_builder_mismatch",
                    "mismatch_components": mismatch_components,
                    "recorded_values": recorded_values,
                    "local_values": local_values,
                    "errors": errors,
                    "tolerances": tolerances,
                    "bitwise": {
                        name: _same_bits(local_values[name], recorded_values[name])
                        for name in (
                            "target_position_rad",
                            "target_velocity_rad_s",
                            "target_acceleration_rad_s2",
                        )
                    },
                    "constraints": _constraints(axis),
                }
            )
    outcome = _outcome_from_rows(
        capture,
        run_id,
        mode,
        "target_builder",
        rows,
        (
            "target_position_rad",
            "target_velocity_rad_s",
            "target_acceleration_rad_s2",
        ),
    )
    return outcome, targets


def _call_level_matches(
    call: CallRecord,
    snapshot: SolverSnapshot,
    thresholds: ParityThresholds,
) -> tuple[dict[str, bool], dict[str, float], dict[str, float], dict[str, Any], dict[str, Any]]:
    errors = {
        "trajectory_duration_s": snapshot.trajectory_duration_s
        - call.trajectory_duration_s,
        "trajectory_time_s": snapshot.trajectory_time_s - call.trajectory_time_s,
    }
    tolerances = {
        "trajectory_duration_s": thresholds.trajectory_duration_s,
        "trajectory_time_s": thresholds.trajectory_duration_s,
    }
    matches = {
        name: abs(errors[name]) <= tolerance
        for name, tolerance in tolerances.items()
    }
    local = {
        "result_code": snapshot.result_code,
        "result_name": snapshot.result_name,
        "trajectory_duration_s": snapshot.trajectory_duration_s,
        "trajectory_time_s": snapshot.trajectory_time_s,
        "new_calculation": snapshot.new_calculation,
        "did_section_change": snapshot.did_section_change,
        "new_section": snapshot.new_section,
        "was_calculation_interrupted": snapshot.was_calculation_interrupted,
    }
    recorded = {
        "result_code": call.result_code,
        "result_name": call.result_name,
        "trajectory_duration_s": call.trajectory_duration_s,
        "trajectory_time_s": call.trajectory_time_s,
        "new_calculation": call.new_calculation,
        "did_section_change": call.did_section_change,
        "new_section": call.new_section,
        "was_calculation_interrupted": call.was_calculation_interrupted,
    }
    for name in (
        "result_code",
        "result_name",
        "new_calculation",
        "did_section_change",
        "new_section",
        "was_calculation_interrupted",
    ):
        matches[name] = local[name] == recorded[name]
    return matches, errors, tolerances, recorded, local


def _solver_failure_rows(
    capture: FullAxisCapture,
    call: CallRecord,
    gate: str,
    error: Exception,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for axis in capture.axes_for_call(call.run_id, call.call_seq):
        rows.append(
            {
                "run_id": call.run_id,
                "mode": call.mode,
                "gate": gate,
                "cycle_seq": call.cycle_seq,
                "call_seq": call.call_seq,
                "callback_source": call.callback_source,
                "active_event_seq": call.active_event_seq,
                "axis_index": axis.axis_index,
                "axis_name": axis.axis_name,
                "analysis_valid": call.analysis_valid,
                "evaluated": call.analysis_valid,
                "pointwise_pass": not call.analysis_valid,
                "mismatch_classification": "local_solver_exception",
                "mismatch_components": ("solver_exception",),
                "recorded_values": _axis_context(axis),
                "local_values": {},
                "errors": {},
                "tolerances": {},
                "bitwise": {},
                "constraints": _constraints(axis),
                "solver_exception": f"{type(error).__name__}: {error}",
            }
        )
    return rows


def _run_solver_step_gate(
    capture: FullAxisCapture,
    run_id: str,
    thresholds: ParityThresholds,
) -> GateOutcome:
    calls = capture.calls_for_run(run_id)
    mode = calls[0].mode
    engine = _RuckigRunEngine(
        capture.manifest.dof, calls[0].ruckig_delta_time_s
    )
    rows: list[dict[str, Any]] = []
    for call in calls:
        axes = capture.axes_for_call(run_id, call.call_seq)
        current = _recorded_current(axes)
        target = _recorded_target(axes)
        try:
            snapshot = engine.step(call, axes, current, target)
        except Exception as error:  # RuckigThrow is not exported consistently.
            rows.extend(_solver_failure_rows(capture, call, "solver_step", error))
            if call.analysis_valid:
                break
            continue
        call_matches, call_errors, call_tolerances, call_recorded, call_local = (
            _call_level_matches(call, snapshot, thresholds)
        )
        for axis in axes:
            index = axis.axis_index
            recorded_values = {
                **call_recorded,
                "output_position_rad": axis.output_position_rad,
                "output_velocity_rad_s": axis.output_velocity_rad_s,
                "output_acceleration_rad_s2": axis.output_acceleration_rad_s2,
                "output_jerk_rad_s3": axis.output_jerk_rad_s3,
                "independent_min_duration_s": axis.independent_min_duration_s,
            }
            local_values = {
                **call_local,
                "output_position_rad": float(snapshot.position[index]),
                "output_velocity_rad_s": float(snapshot.velocity[index]),
                "output_acceleration_rad_s2": float(snapshot.acceleration[index]),
                "output_jerk_rad_s3": float(snapshot.jerk[index]),
                "independent_min_duration_s": float(
                    snapshot.independent_min_durations_s[index]
                ),
            }
            errors = {
                **call_errors,
                "output_position_rad": local_values["output_position_rad"]
                - recorded_values["output_position_rad"],
                "output_velocity_rad_s": local_values["output_velocity_rad_s"]
                - recorded_values["output_velocity_rad_s"],
                "output_acceleration_rad_s2": local_values[
                    "output_acceleration_rad_s2"
                ]
                - recorded_values["output_acceleration_rad_s2"],
                "independent_min_duration_s": local_values[
                    "independent_min_duration_s"
                ]
                - recorded_values["independent_min_duration_s"],
            }
            tolerances = {
                **call_tolerances,
                "output_position_rad": thresholds.position_rad,
                "output_velocity_rad_s": thresholds.velocity_rad_s,
                "output_acceleration_rad_s2": thresholds.acceleration_rad_s2,
                "independent_min_duration_s": thresholds.trajectory_duration_s,
            }
            matches = {
                **call_matches,
                **{
                    name: abs(errors[name]) <= tolerance
                    for name, tolerance in tolerances.items()
                    if name not in call_matches
                },
            }
            evaluated = call.analysis_valid
            mismatch_components = tuple(
                name for name, matched in matches.items() if not matched
            )
            bitwise_names = (
                "trajectory_duration_s",
                "trajectory_time_s",
                "output_position_rad",
                "output_velocity_rad_s",
                "output_acceleration_rad_s2",
                "output_jerk_rad_s3",
                "independent_min_duration_s",
            )
            rows.append(
                {
                    "run_id": run_id,
                    "mode": mode,
                    "gate": "solver_step",
                    "cycle_seq": call.cycle_seq,
                    "call_seq": call.call_seq,
                    "callback_source": call.callback_source,
                    "active_event_seq": call.active_event_seq,
                    "axis_index": index,
                    "axis_name": axis.axis_name,
                    "analysis_valid": call.analysis_valid,
                    "evaluated": evaluated,
                    "pointwise_pass": (all(matches.values()) if evaluated else True),
                    "mismatch_classification": "solver_step_mismatch",
                    "mismatch_components": mismatch_components,
                    "recorded_values": recorded_values,
                    "local_values": local_values,
                    "errors": errors,
                    "tolerances": tolerances,
                    "bitwise": {
                        name: _same_bits(local_values[name], recorded_values[name])
                        for name in bitwise_names
                    },
                    "constraints": _constraints(axis),
                }
            )
    return _outcome_from_rows(
        capture,
        run_id,
        mode,
        "solver_step",
        rows,
        (
            "trajectory_duration_s",
            "trajectory_time_s",
            "output_position_rad",
            "output_velocity_rad_s",
            "output_acceleration_rad_s2",
            "independent_min_duration_s",
        ),
    )


def _run_closed_loop_gate(
    capture: FullAxisCapture,
    run_id: str,
    targets: Mapping[int, Mapping[str, Any]],
    thresholds: ParityThresholds,
) -> GateOutcome:
    calls = capture.calls_for_run(run_id)
    mode = calls[0].mode
    first_axes = capture.axes_for_call(run_id, calls[0].call_seq)
    current = _recorded_current(first_axes)
    engine = _RuckigRunEngine(
        capture.manifest.dof, calls[0].ruckig_delta_time_s
    )
    rows: list[dict[str, Any]] = []
    for call in calls:
        axes = capture.axes_for_call(run_id, call.call_seq)
        recorded_current = _recorded_current(axes)
        local_current = np.array(current, copy=True)
        target = _target_matrix(targets[call.call_seq])
        try:
            snapshot = engine.step(call, axes, local_current, target)
        except Exception as error:
            rows.extend(_solver_failure_rows(capture, call, "closed_loop", error))
            if call.analysis_valid:
                break
            continue
        call_matches, call_errors, call_tolerances, call_recorded, call_local = (
            _call_level_matches(call, snapshot, thresholds)
        )
        for axis in axes:
            index = axis.axis_index
            recorded_values = {
                **call_recorded,
                "current_position_rad": float(recorded_current[index, 0]),
                "current_velocity_rad_s": float(recorded_current[index, 1]),
                "current_acceleration_rad_s2": float(recorded_current[index, 2]),
                "output_position_rad": axis.output_position_rad,
                "output_velocity_rad_s": axis.output_velocity_rad_s,
                "output_acceleration_rad_s2": axis.output_acceleration_rad_s2,
                "independent_min_duration_s": axis.independent_min_duration_s,
            }
            local_values = {
                **call_local,
                "current_position_rad": float(local_current[index, 0]),
                "current_velocity_rad_s": float(local_current[index, 1]),
                "current_acceleration_rad_s2": float(local_current[index, 2]),
                "output_position_rad": float(snapshot.position[index]),
                "output_velocity_rad_s": float(snapshot.velocity[index]),
                "output_acceleration_rad_s2": float(snapshot.acceleration[index]),
                "independent_min_duration_s": float(
                    snapshot.independent_min_durations_s[index]
                ),
            }
            errors = {
                **call_errors,
                **{
                    name: local_values[name] - recorded_values[name]
                    for name in (
                        "current_position_rad",
                        "current_velocity_rad_s",
                        "current_acceleration_rad_s2",
                        "output_position_rad",
                        "output_velocity_rad_s",
                        "output_acceleration_rad_s2",
                        "independent_min_duration_s",
                    )
                },
            }
            tolerances = {
                **call_tolerances,
                "current_position_rad": thresholds.position_rad,
                "current_velocity_rad_s": thresholds.velocity_rad_s,
                "current_acceleration_rad_s2": thresholds.acceleration_rad_s2,
                "output_position_rad": thresholds.position_rad,
                "output_velocity_rad_s": thresholds.velocity_rad_s,
                "output_acceleration_rad_s2": thresholds.acceleration_rad_s2,
                "independent_min_duration_s": thresholds.trajectory_duration_s,
            }
            matches = {
                **call_matches,
                **{
                    name: abs(errors[name]) <= tolerance
                    for name, tolerance in tolerances.items()
                    if name not in call_matches
                },
            }
            evaluated = call.analysis_valid
            mismatch_components = tuple(
                name for name, matched in matches.items() if not matched
            )
            rows.append(
                {
                    "run_id": run_id,
                    "mode": mode,
                    "gate": "closed_loop",
                    "cycle_seq": call.cycle_seq,
                    "call_seq": call.call_seq,
                    "callback_source": call.callback_source,
                    "active_event_seq": call.active_event_seq,
                    "axis_index": index,
                    "axis_name": axis.axis_name,
                    "analysis_valid": call.analysis_valid,
                    "evaluated": evaluated,
                    "pointwise_pass": (all(matches.values()) if evaluated else True),
                    "mismatch_classification": "closed_loop_mismatch",
                    "mismatch_components": mismatch_components,
                    "recorded_values": recorded_values,
                    "local_values": local_values,
                    "errors": errors,
                    "tolerances": tolerances,
                    "bitwise": {
                        name: _same_bits(local_values[name], recorded_values[name])
                        for name in tolerances
                    },
                    "constraints": _constraints(axis),
                }
            )
        current = np.column_stack(
            (snapshot.position, snapshot.velocity, snapshot.acceleration)
        )
    return _outcome_from_rows(
        capture,
        run_id,
        mode,
        "closed_loop",
        rows,
        (
            "trajectory_duration_s",
            "trajectory_time_s",
            "current_position_rad",
            "current_velocity_rad_s",
            "current_acceleration_rad_s2",
            "output_position_rad",
            "output_velocity_rad_s",
            "output_acceleration_rad_s2",
            "independent_min_duration_s",
        ),
    )


def _mode_run_map(capture: FullAxisCapture) -> dict[str, str]:
    result: dict[str, str] = {}
    for run_id, mode in capture.manifest.runs:
        if mode in result:
            raise CaptureValidationError(
                "duplicate_mode_run",
                f"formal pipeline requires exactly one run for mode {mode}",
            )
        result[mode] = run_id
    return result


def _cross_mode_signature(capture: FullAxisCapture, run_id: str) -> dict[str, Any]:
    calls = capture.calls_for_run(run_id)
    first_call = calls[0]
    first_axes = capture.axes_for_call(run_id, first_call.call_seq)
    call_structure = [
        {
            "cycle_seq": call.cycle_seq,
            "call_seq": call.call_seq,
            "callback_source": call.callback_source,
            "active_event_seq": call.active_event_seq,
            "ruckig_delta_time_s": call.ruckig_delta_time_s,
            "run_reset": call.run_reset,
            "analysis_valid": call.analysis_valid,
            "control_interface": call.control_interface,
            "duration_discretization": call.duration_discretization,
            "minimum_duration_s": call.minimum_duration_s,
        }
        for call in calls
    ]
    configurations = []
    for call in calls:
        for axis in capture.axes_for_call(run_id, call.call_seq):
            configurations.append(
                {
                    "call_seq": call.call_seq,
                    "axis_index": axis.axis_index,
                    "max_velocity_rad_s": axis.max_velocity_rad_s,
                    "min_velocity_rad_s": axis.min_velocity_rad_s,
                    "max_acceleration_rad_s2": axis.max_acceleration_rad_s2,
                    "min_acceleration_rad_s2": axis.min_acceleration_rad_s2,
                    "max_jerk_rad_s3": axis.max_jerk_rad_s3,
                    "min_jerk_rad_s3": axis.min_jerk_rad_s3,
                    "enabled": axis.enabled,
                    "per_dof_control_interface": axis.per_dof_control_interface,
                }
            )
    events = [
        {
            "event_seq": event.event_seq,
            "applied_call_seq": event.applied_call_seq,
            "axis_index": event.axis_index,
            "position_rad": event.position_rad,
        }
        for event in capture.events_for_run(run_id)
    ]
    return {
        "initial_state": _recorded_current(first_axes).tolist(),
        "call_structure": call_structure,
        "configurations": configurations,
        "raw_position_events": events,
    }


def validate_pipeline_data_sufficiency(
    capture: FullAxisCapture,
    *,
    local_build: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Apply formal all-mode/version/cross-mode data requirements."""

    build = dict(local_build or collect_local_ruckig_build())
    if capture.manifest.ruckig_version != str(build.get("version")):
        raise CaptureValidationError(
            "ruckig_version_mismatch",
            "controller and local Ruckig versions must match before parity",
            context={
                "controller": capture.manifest.ruckig_version,
                "local": build.get("version"),
            },
        )
    mode_runs = _mode_run_map(capture)
    if set(mode_runs) != set(REQUIRED_MODES):
        raise CaptureValidationError(
            "missing_modes",
            "formal pipeline requires exactly one run for all four synchronization modes",
            context={
                "required": list(REQUIRED_MODES),
                "observed": sorted(mode_runs),
            },
        )
    signatures = {
        mode: sha256_json(_cross_mode_signature(capture, run_id))
        for mode, run_id in mode_runs.items()
    }
    if len(set(signatures.values())) != 1:
        raise CaptureValidationError(
            "cross_mode_control_mismatch",
            "four runs differ in input/reset/call-sequence/config beyond synchronization",
            context={"signatures": signatures},
        )
    rows: list[dict[str, Any]] = []
    for mode in REQUIRED_MODES:
        run_id = mode_runs[mode]
        calls = capture.calls_for_run(run_id)
        valid = [call for call in calls if call.analysis_valid]
        rows.append(
            {
                "run_id": run_id,
                "mode": mode,
                "status": GATE_PASS,
                "formal_gate_eligible": True,
                "dof": capture.manifest.dof,
                "call_count": len(calls),
                "analysis_valid_call_count": len(valid),
                "axis_state_row_count": len(calls) * capture.manifest.dof,
                "raw_position_event_row_count": len(
                    capture.events_for_run(run_id)
                ),
                "run_reset_call_seq": calls[0].call_seq,
                "first_analysis_valid_call_seq": valid[0].call_seq,
                "controller_ruckig_version": capture.manifest.ruckig_version,
                "local_ruckig_version": build.get("version"),
                "cross_mode_control_signature": signatures[mode],
                "reason": "complete full-axis controller-internal capture",
            }
        )
    return tuple(rows)


def validate_no_data_sufficiency(
    capture: FullAxisCapture,
    *,
    local_build: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Apply the rebuilt E18 formal requirements to the No run only.

    Other synchronization runs may be present in the capture, but they are not
    required and do not participate in E18's controller/replay identity decision.
    """

    build = dict(local_build or collect_local_ruckig_build())
    if capture.manifest.ruckig_version != str(build.get("version")):
        raise CaptureValidationError(
            "ruckig_version_mismatch",
            "controller and local Ruckig versions must match before parity",
            context={
                "controller": capture.manifest.ruckig_version,
                "local": build.get("version"),
            },
        )
    mode_runs = _mode_run_map(capture)
    if "No" not in mode_runs:
        raise CaptureValidationError(
            "missing_no_mode",
            "rebuilt E18 requires one complete Synchronization.No run",
            context={"observed": sorted(mode_runs)},
        )
    run_id = mode_runs["No"]
    calls = capture.calls_for_run(run_id)
    valid = [call for call in calls if call.analysis_valid]
    return (
        {
            "run_id": run_id,
            "mode": "No",
            "status": GATE_PASS,
            "formal_gate_eligible": True,
            "dof": capture.manifest.dof,
            "call_count": len(calls),
            "analysis_valid_call_count": len(valid),
            "axis_state_row_count": len(calls) * capture.manifest.dof,
            "raw_position_event_row_count": len(capture.events_for_run(run_id)),
            "run_reset_call_seq": calls[0].call_seq,
            "first_analysis_valid_call_seq": valid[0].call_seq,
            "controller_ruckig_version": capture.manifest.ruckig_version,
            "local_ruckig_version": build.get("version"),
            "reason": "complete No-mode full-axis controller-internal capture",
        },
    )


def run_parity(
    capture: FullAxisCapture | str | Path,
    *,
    thresholds: ParityThresholds = DEFAULT_THRESHOLDS,
    modes: Sequence[str] | None = None,
) -> ParityReport:
    """Run target, solver-step, and closed-loop gates in strict order."""

    data = (
        load_full_axis_capture(capture)
        if isinstance(capture, (str, Path))
        else capture
    )
    mode_runs = _mode_run_map(data)
    selected = tuple(modes or REQUIRED_MODES)
    unknown = [mode for mode in selected if mode not in REQUIRED_MODES]
    if unknown:
        raise ValueError(f"unknown synchronization modes: {unknown}")
    results: list[ModeParityResult] = []
    for mode in selected:
        if mode not in mode_runs:
            not_evaluable = _status_gate(
                "target_builder",
                GATE_NOT_EVALUABLE,
                "mode is absent from capture",
                run_id="",
                mode=mode,
            )
            results.append(
                ModeParityResult(
                    run_id="",
                    mode=mode,
                    target_builder=not_evaluable,
                    solver_step=_status_gate(
                        "solver_step",
                        GATE_NOT_EVALUABLE,
                        "mode is absent from capture",
                        run_id="",
                        mode=mode,
                    ),
                    closed_loop=_status_gate(
                        "closed_loop",
                        GATE_NOT_EVALUABLE,
                        "mode is absent from capture",
                        run_id="",
                        mode=mode,
                    ),
                )
            )
            continue
        run_id = mode_runs[mode]
        target, targets = _run_target_builder_gate(data, run_id, thresholds)
        if target.status != GATE_PASS:
            solver = _status_gate(
                "solver_step",
                GATE_NOT_RUN,
                "blocked by target_builder parity",
                run_id=run_id,
                mode=mode,
            )
            closed = _status_gate(
                "closed_loop",
                GATE_NOT_RUN,
                "blocked by target_builder parity",
                run_id=run_id,
                mode=mode,
            )
        else:
            solver = _run_solver_step_gate(data, run_id, thresholds)
            if solver.status != GATE_PASS:
                closed = _status_gate(
                    "closed_loop",
                    GATE_NOT_RUN,
                    "blocked by solver_step parity",
                    run_id=run_id,
                    mode=mode,
                )
            else:
                closed = _run_closed_loop_gate(data, run_id, targets, thresholds)
        results.append(
            ModeParityResult(
                run_id=run_id,
                mode=mode,
                target_builder=target,
                solver_step=solver,
                closed_loop=closed,
            )
        )
    return ParityReport(modes=tuple(results))


def _effective_lower(value: float | None, maximum: float) -> float:
    return -float(maximum) if value is None else float(value)


def _signed_limit_utilization(value: float, lower: float, upper: float) -> float:
    if value >= 0.0:
        return abs(value) / upper if upper > 0.0 else math.inf
    return abs(value) / abs(lower) if lower < 0.0 else math.inf


def _controlled_input_payload(
    capture: FullAxisCapture, run_id: str, strategy: str
) -> dict[str, Any]:
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "strategy": strategy,
        "manifest": {
            "dof": capture.manifest.dof,
            "axis_names": capture.manifest.axis_names,
            "right_axis_index": capture.manifest.right_axis_index,
            "future_o1_h_s": capture.manifest.future_o1_h_s,
        },
        "cross_mode_controls": _cross_mode_signature(capture, run_id),
    }


def _run_local_arm(
    capture: FullAxisCapture,
    run_id: str,
    *,
    arm_id: str,
    target_strategy: str,
    synchronization: str,
) -> list[dict[str, Any]]:
    calls = capture.calls_for_run(run_id)
    targets = build_local_target_sequence(
        capture, run_id, strategy=target_strategy
    )
    first_axes = capture.axes_for_call(run_id, calls[0].call_seq)
    current = _recorded_current(first_axes)
    engine = _RuckigRunEngine(
        capture.manifest.dof, calls[0].ruckig_delta_time_s
    )
    controlled_hash = sha256_json(
        _controlled_input_payload(capture, run_id, target_strategy)
    )
    rows: list[dict[str, Any]] = []
    for call in calls:
        axes = capture.axes_for_call(run_id, call.call_seq)
        target_info = targets[call.call_seq]
        target = _target_matrix(target_info)
        snapshot = engine.step(
            call,
            axes,
            current,
            target,
            synchronization_override=synchronization,
        )
        independent = snapshot.independent_min_durations_s
        longest = float(np.max(independent))
        limiting = tuple(
            int(index)
            for index, value in enumerate(independent)
            if math.isclose(
                float(value), longest, rel_tol=1e-12, abs_tol=1e-15
            )
        )
        extension = snapshot.trajectory_duration_s - longest
        for axis in axes:
            index = axis.axis_index
            reference = float(target_info["raw_position"][index])
            position = float(snapshot.position[index])
            velocity = float(snapshot.velocity[index])
            acceleration = float(snapshot.acceleration[index])
            jerk = float(snapshot.jerk[index])
            velocity_lower = _effective_lower(
                axis.min_velocity_rad_s, axis.max_velocity_rad_s
            )
            acceleration_lower = _effective_lower(
                axis.min_acceleration_rad_s2, axis.max_acceleration_rad_s2
            )
            rows.append(
                {
                    "arm_id": arm_id,
                    "target_strategy": target_strategy,
                    "synchronization": synchronization,
                    "canonical_run_id": run_id,
                    "controlled_input_hash": controlled_hash,
                    "cycle_seq": call.cycle_seq,
                    "call_seq": call.call_seq,
                    "callback_source": call.callback_source,
                    "active_event_seq": call.active_event_seq,
                    "events_applied": target_info["events_applied"],
                    "target_held": target_info["held"],
                    "predictor_startup": target_info["startup"],
                    "monotonic_time_s": call.monotonic_time_s,
                    "wall_delta_time_s": call.wall_delta_time_s,
                    "ruckig_delta_time_s": call.ruckig_delta_time_s,
                    "analysis_valid": call.analysis_valid,
                    "axis_index": index,
                    "axis_name": axis.axis_name,
                    "reference_raw_position_rad": reference,
                    "target_position_rad": float(target[index, 0]),
                    "target_velocity_rad_s": float(target[index, 1]),
                    "target_acceleration_rad_s2": float(target[index, 2]),
                    "output_position_rad": position,
                    "output_velocity_rad_s": velocity,
                    "output_acceleration_rad_s2": acceleration,
                    "output_jerk_rad_s3": jerk,
                    "position_error_rad": position - reference,
                    "velocity_limit_utilization": _signed_limit_utilization(
                        velocity, velocity_lower, axis.max_velocity_rad_s
                    ),
                    "acceleration_limit_utilization": _signed_limit_utilization(
                        acceleration,
                        acceleration_lower,
                        axis.max_acceleration_rad_s2,
                    ),
                    "jerk_limit_utilization": abs(jerk)
                    / axis.max_jerk_rad_s3,
                    "result_code": snapshot.result_code,
                    "result_name": snapshot.result_name,
                    "trajectory_duration_s": snapshot.trajectory_duration_s,
                    "trajectory_time_s": snapshot.trajectory_time_s,
                    "independent_min_duration_s": float(independent[index]),
                    "limiting_axis_indices": limiting,
                    "synchronization_extension_s": extension,
                    "synchronization_intervened": extension > 1e-12,
                    "new_calculation": snapshot.new_calculation,
                    "did_section_change": snapshot.did_section_change,
                    "new_section": snapshot.new_section,
                }
            )
        current = np.column_stack(
            (snapshot.position, snapshot.velocity, snapshot.acceleration)
        )
    return rows


def _error_metrics(values: np.ndarray, prefix: str = "position") -> dict[str, float]:
    errors = np.asarray(values, dtype=np.float64)
    if errors.ndim != 1 or errors.size == 0:
        raise ValueError("error metrics need a non-empty vector")
    absolute = np.abs(errors)
    return {
        f"{prefix}_rmse_rad": float(np.sqrt(np.mean(errors**2))),
        f"{prefix}_mae_rad": float(np.mean(absolute)),
        f"{prefix}_bias_rad": float(np.mean(errors)),
        f"{prefix}_p95_abs_error_rad": float(
            np.quantile(absolute, 0.95, method="linear")
        ),
        f"{prefix}_max_abs_error_rad": float(np.max(absolute)),
    }


def _arm_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    right_axis_index: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    valid = [row for row in rows if bool(row["analysis_valid"])]
    if not valid:
        raise ValueError("local arm has no analysis_valid rows")
    axis_rows: list[dict[str, Any]] = []
    for axis_index in sorted({int(row["axis_index"]) for row in valid}):
        subset = [row for row in valid if int(row["axis_index"]) == axis_index]
        errors = np.asarray(
            [row["position_error_rad"] for row in subset], dtype=np.float64
        )
        axis_rows.append(
            {
                "arm_id": subset[0]["arm_id"],
                "synchronization": subset[0]["synchronization"],
                "target_strategy": subset[0]["target_strategy"],
                "axis_index": axis_index,
                "axis_name": subset[0]["axis_name"],
                "sample_count": len(subset),
                **_error_metrics(errors),
            }
        )
    aggregate_errors = np.asarray(
        [row["position_error_rad"] for row in valid], dtype=np.float64
    )
    right = next(row for row in axis_rows if row["axis_index"] == right_axis_index)
    unique_call_rows: dict[int, Mapping[str, Any]] = {}
    for row in valid:
        unique_call_rows.setdefault(int(row["call_seq"]), row)
    limiting_counter: Counter[int] = Counter()
    for row in unique_call_rows.values():
        for axis_index in row["limiting_axis_indices"]:
            limiting_counter[int(axis_index)] += 1
    durations = np.asarray(
        [row["trajectory_duration_s"] for row in unique_call_rows.values()],
        dtype=np.float64,
    )
    result = {
        "arm_id": valid[0]["arm_id"],
        "target_strategy": valid[0]["target_strategy"],
        "synchronization": valid[0]["synchronization"],
        "controlled_input_hash": valid[0]["controlled_input_hash"],
        "analysis_valid_call_count": len(unique_call_rows),
        "analysis_valid_axis_point_count": len(valid),
        "right_axis_index": right_axis_index,
        "right_position_rmse_rad": right["position_rmse_rad"],
        "right_position_mae_rad": right["position_mae_rad"],
        "right_position_bias_rad": right["position_bias_rad"],
        "right_position_p95_abs_error_rad": right[
            "position_p95_abs_error_rad"
        ],
        "right_position_max_abs_error_rad": right[
            "position_max_abs_error_rad"
        ],
        "aggregate_position_rmse_rad": float(
            np.sqrt(np.mean(aggregate_errors**2))
        ),
        "worst_axis_position_rmse_rad": max(
            float(row["position_rmse_rad"]) for row in axis_rows
        ),
        "trajectory_duration_mean_s": float(np.mean(durations)),
        "trajectory_duration_p95_s": float(
            np.quantile(durations, 0.95, method="linear")
        ),
        "trajectory_duration_max_s": float(np.max(durations)),
        "synchronization_intervention_count": sum(
            bool(row["synchronization_intervened"])
            for row in unique_call_rows.values()
        ),
        "limiting_axis_call_counts": dict(sorted(limiting_counter.items())),
        "max_velocity_limit_utilization": max(
            float(row["velocity_limit_utilization"]) for row in valid
        ),
        "max_acceleration_limit_utilization": max(
            float(row["acceleration_limit_utilization"]) for row in valid
        ),
        "max_jerk_limit_utilization": max(
            float(row["jerk_limit_utilization"]) for row in valid
        ),
    }
    return result, axis_rows


def _lag_scan(
    rows: Sequence[Mapping[str, Any]],
    *,
    right_axis_index: int,
    max_lag_s: float = LAG_DIAGNOSTIC_LIMIT_S,
) -> list[dict[str, Any]]:
    right = [
        row
        for row in rows
        if bool(row["analysis_valid"])
        and int(row["axis_index"]) == right_axis_index
    ]
    if len(right) < 2:
        return []
    times = np.asarray([row["monotonic_time_s"] for row in right], dtype=np.float64)
    median_dt = float(np.median(np.diff(times)))
    if median_dt <= 0.0:
        median_dt = float(np.median([row["ruckig_delta_time_s"] for row in right]))
    max_shift = min(len(right) - 1, int(round(max_lag_s / median_dt)))
    reference = np.asarray(
        [row["reference_raw_position_rad"] for row in right], dtype=np.float64
    )
    output = np.asarray([row["output_position_rad"] for row in right], dtype=np.float64)
    result: list[dict[str, Any]] = []
    for shift in range(-max_shift, max_shift + 1):
        if shift < 0:
            local = output[:shift]
            truth = reference[-shift:]
        elif shift > 0:
            local = output[shift:]
            truth = reference[:-shift]
        else:
            local = output
            truth = reference
        errors = local - truth
        result.append(
            {
                "arm_id": right[0]["arm_id"],
                "synchronization": right[0]["synchronization"],
                "shift_calls": shift,
                "nominal_shift_s": shift * median_dt,
                "sample_count": int(errors.size),
                "position_rmse_rad": float(np.sqrt(np.mean(errors**2))),
                "diagnostic_only": True,
                "primary_remains_zero_shift": True,
            }
        )
    return result


def _transition_diagnostics(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["axis_index"])].append(row)
    result: list[dict[str, Any]] = []
    for axis_index, axis_rows in grouped.items():
        axis_rows.sort(key=lambda row: int(row["call_seq"]))
        position = np.asarray(
            [row["output_position_rad"] for row in axis_rows], dtype=np.float64
        )
        steps = np.abs(np.diff(position))
        for index, row in enumerate(axis_rows):
            if not row["events_applied"] or index == 0 or index + 1 >= len(axis_rows):
                continue
            neighborhood_start = max(0, index - 5)
            neighborhood_end = min(steps.size, index + 5)
            neighborhood = steps[neighborhood_start:neighborhood_end]
            baseline = float(np.median(neighborhood)) if neighborhood.size else 0.0
            first_step = float(position[index] - position[index - 1])
            second_step = float(position[index + 1] - position[index])
            ratio = (
                (abs(first_step) + abs(second_step)) / (2.0 * baseline)
                if baseline > 0.0
                else None
            )
            result.append(
                {
                    "arm_id": row["arm_id"],
                    "synchronization": row["synchronization"],
                    "event_seq": row["active_event_seq"],
                    "call_seq": row["call_seq"],
                    "callback_source": row["callback_source"],
                    "axis_index": axis_index,
                    "axis_name": row["axis_name"],
                    "first_step_rad": first_step,
                    "second_step_rad": second_step,
                    "local_median_abs_step_rad": baseline,
                    "double_step_jump_ratio": ratio,
                    "definition": (
                        "(|step_at_transition|+|next_step|)/(2*local_median_abs_step)"
                    ),
                }
            )
    return result


def select_robust_sync_winner(
    metric_rows: Sequence[Mapping[str, Any]],
    *,
    relative_tie_tolerance: float = 1e-12,
    absolute_tie_tolerance: float = 1e-15,
) -> dict[str, Any]:
    """Select a winner only when every predeclared view has one same winner."""

    criteria = (
        "right_position_rmse_rad",
        "aggregate_position_rmse_rad",
        "worst_axis_position_rmse_rad",
        "right_position_max_abs_error_rad",
    )
    rows = [dict(row) for row in metric_rows]
    if {row.get("synchronization") for row in rows} != set(REQUIRED_MODES):
        return {
            "status": "no_robust_single_best",
            "winner": None,
            "reason": "all four synchronization modes are required",
            "criterion_winners": {},
        }
    winners: dict[str, str | None] = {}
    contenders: dict[str, list[str]] = {}
    for criterion in criteria:
        best_value = min(float(row[criterion]) for row in rows)
        tolerance = max(
            absolute_tie_tolerance,
            abs(best_value) * relative_tie_tolerance,
        )
        tied = sorted(
            str(row["synchronization"])
            for row in rows
            if float(row[criterion]) <= best_value + tolerance
        )
        contenders[criterion] = tied
        winners[criterion] = tied[0] if len(tied) == 1 else None
    unique = {winner for winner in winners.values() if winner is not None}
    robust = len(unique) == 1 and all(winner is not None for winner in winners.values())
    return {
        "status": "robust_single_best" if robust else "no_robust_single_best",
        "winner": next(iter(unique)) if robust else None,
        "reason": (
            "same unique winner across right-axis, aggregate, worst-axis, and max-error views"
            if robust
            else "evaluation views disagree or contain a numerical tie"
        ),
        "criterion_winners": winners,
        "criterion_contenders": contenders,
    }


def _real_observation_metrics(
    capture: FullAxisCapture,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for run_id, mode in capture.manifest.runs:
        targets = build_local_target_sequence(capture, run_id)
        rows: list[dict[str, Any]] = []
        for call in capture.calls_for_run(run_id):
            if not call.analysis_valid:
                continue
            raw = np.asarray(targets[call.call_seq]["raw_position"], dtype=np.float64)
            for axis in capture.axes_for_call(run_id, call.call_seq):
                rows.append(
                    {
                        "axis_index": axis.axis_index,
                        "error": axis.output_position_rad - raw[axis.axis_index],
                    }
                )
        axis_rmse: dict[int, float] = {}
        for axis_index in range(capture.manifest.dof):
            errors = np.asarray(
                [row["error"] for row in rows if row["axis_index"] == axis_index],
                dtype=np.float64,
            )
            axis_rmse[axis_index] = float(np.sqrt(np.mean(errors**2)))
        all_errors = np.asarray([row["error"] for row in rows], dtype=np.float64)
        right_errors = np.asarray(
            [
                row["error"]
                for row in rows
                if row["axis_index"] == capture.manifest.right_axis_index
            ],
            dtype=np.float64,
        )
        result.append(
            {
                "run_id": run_id,
                "mode": mode,
                "observation_role": "post-parity_real_run_crosscheck",
                "ranking_role": "not_used_for_controlled_ranking",
                "right_position_rmse_rad": float(
                    np.sqrt(np.mean(right_errors**2))
                ),
                "aggregate_position_rmse_rad": float(
                    np.sqrt(np.mean(all_errors**2))
                ),
                "worst_axis_position_rmse_rad": max(axis_rmse.values()),
                "per_axis_rmse_rad": axis_rmse,
            }
        )
    return result


def run_synchronization_counterfactual(
    capture: FullAxisCapture,
) -> dict[str, Any]:
    """Run four synchronization modes on one canonical TimeIfNecessary input."""

    run_id = _mode_run_map(capture)["TimeIfNecessary"]
    all_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    axis_metric_rows: list[dict[str, Any]] = []
    lag_rows: list[dict[str, Any]] = []
    transition_rows: list[dict[str, Any]] = []
    for mode in REQUIRED_MODES:
        rows = _run_local_arm(
            capture,
            run_id,
            arm_id=f"sync_{MODE_SLUGS[mode]}",
            target_strategy="pv_future_o1_live",
            synchronization=mode,
        )
        metrics, per_axis = _arm_metrics(
            rows, right_axis_index=capture.manifest.right_axis_index
        )
        transitions = _transition_diagnostics(rows)
        ratios = [
            float(row["double_step_jump_ratio"])
            for row in transitions
            if row["double_step_jump_ratio"] is not None
        ]
        metrics["transition_double_step_jump_ratio_p95"] = (
            float(np.quantile(ratios, 0.95, method="linear")) if ratios else None
        )
        metrics["transition_double_step_jump_ratio_max"] = max(ratios) if ratios else None
        all_rows.extend(rows)
        metric_rows.append(metrics)
        axis_metric_rows.extend(per_axis)
        lag_rows.extend(
            _lag_scan(rows, right_axis_index=capture.manifest.right_axis_index)
        )
        transition_rows.extend(transitions)
    controlled_hashes = {row["controlled_input_hash"] for row in metric_rows}
    if len(controlled_hashes) != 1:
        raise RuntimeError("synchronization arms did not share one controlled input")
    winner = select_robust_sync_winner(metric_rows)
    return {
        "rows": all_rows,
        "metrics": metric_rows,
        "axis_metrics": axis_metric_rows,
        "lag_scan": lag_rows,
        "transitions": transition_rows,
        "real_observation_metrics": _real_observation_metrics(capture),
        "summary": {
            "controlled_input_hash": next(iter(controlled_hashes)),
            "primary_metric": "right_position_rmse_rad at zero time offset",
            "lag_role": "diagnostic_only",
            "ranking_source": "local controlled counterfactual",
            "real_runs_role": "post-parity observational crosscheck only",
            **winner,
        },
    }


def run_p_only_pv_ablation(capture: FullAxisCapture) -> dict[str, Any]:
    """Run local P-only, deployed PV Future-O1, and legacy predictor-P arms."""

    run_id = _mode_run_map(capture)["TimeIfNecessary"]
    specifications = (
        ("p_only", "p_only", "primary_comparator"),
        ("pv_future_o1_live", "pv_future_o1_live", "primary_candidate"),
        ("predictor_p_legacy", "predictor_p_legacy", "sensitivity_only"),
    )
    all_rows: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    axis_metrics: list[dict[str, Any]] = []
    for arm_id, strategy, role in specifications:
        rows = _run_local_arm(
            capture,
            run_id,
            arm_id=arm_id,
            target_strategy=strategy,
            synchronization="TimeIfNecessary",
        )
        for row in rows:
            row["method_role"] = role
            row["inference_scope"] = "local_causal_ablation_only"
        metric, per_axis = _arm_metrics(
            rows, right_axis_index=capture.manifest.right_axis_index
        )
        metric["method_role"] = role
        metric["inference_scope"] = "local_causal_ablation_only"
        all_rows.extend(rows)
        metrics.append(metric)
        axis_metrics.extend(per_axis)
    by_arm = {row["arm_id"]: row for row in metrics}
    p = by_arm["p_only"]
    pv = by_arm["pv_future_o1_live"]
    return {
        "rows": all_rows,
        "metrics": metrics,
        "axis_metrics": axis_metrics,
        "summary": {
            "comparison": "p_only vs deployed pv_future_o1_live",
            "synchronization": "TimeIfNecessary",
            "right_position_rmse_ratio_pv_over_p": (
                pv["right_position_rmse_rad"] / p["right_position_rmse_rad"]
                if p["right_position_rmse_rad"] > 0.0
                else None
            ),
            "aggregate_position_rmse_ratio_pv_over_p": (
                pv["aggregate_position_rmse_rad"]
                / p["aggregate_position_rmse_rad"]
                if p["aggregate_position_rmse_rad"] > 0.0
                else None
            ),
            "legacy_predictor_p_role": "sensitivity_only_not_primary",
            "inference_scope": "local causal ablation; no real P-only claim",
        },
    }


def _write_post_parity_figures(
    run_directory: Path,
    sync: Mapping[str, Any],
    ablation: Mapping[str, Any],
    *,
    right_axis_index: int,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    figure, axes = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, constrained_layout=True
    )
    for mode in REQUIRED_MODES:
        rows = [
            row
            for row in sync["rows"]
            if row["synchronization"] == mode
            and row["axis_index"] == right_axis_index
            and row["analysis_valid"]
        ]
        time = np.asarray([row["monotonic_time_s"] for row in rows], dtype=float)
        time -= time[0]
        if mode == REQUIRED_MODES[0]:
            axes[0].plot(
                time,
                [row["reference_raw_position_rad"] for row in rows],
                color="#222222",
                linestyle="--",
                linewidth=1.0,
                label="raw P reference",
            )
        axes[0].plot(
            time,
            [row["output_position_rad"] for row in rows],
            linewidth=0.9,
            label=mode,
        )
        axes[1].plot(
            time,
            [row["position_error_rad"] for row in rows],
            linewidth=0.8,
            label=mode,
        )
    axes[0].set_ylabel("right-axis position [rad]")
    axes[0].legend(frameon=False, ncol=2)
    axes[1].set(xlabel="elapsed from analysis_valid [s]", ylabel="output - raw P [rad]")
    figure.savefig(figures / "synchronization_counterfactual.png", dpi=200)
    figure.savefig(figures / "synchronization_counterfactual.svg")
    plt.close(figure)

    figure, axes = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, constrained_layout=True
    )
    for arm_id, label in (
        ("p_only", "P-only"),
        ("pv_future_o1_live", "PV Future-O1 live"),
        ("predictor_p_legacy", "legacy predictor-P"),
    ):
        rows = [
            row
            for row in ablation["rows"]
            if row["arm_id"] == arm_id
            and row["axis_index"] == right_axis_index
            and row["analysis_valid"]
        ]
        time = np.asarray([row["monotonic_time_s"] for row in rows], dtype=float)
        time -= time[0]
        if arm_id == "p_only":
            axes[0].plot(
                time,
                [row["reference_raw_position_rad"] for row in rows],
                color="#222222",
                linestyle="--",
                linewidth=1.0,
                label="raw P reference",
            )
        axes[0].plot(
            time,
            [row["output_position_rad"] for row in rows],
            linewidth=0.9,
            label=label,
        )
        axes[1].plot(
            time,
            [row["position_error_rad"] for row in rows],
            linewidth=0.8,
            label=label,
        )
    axes[0].set_ylabel("right-axis position [rad]")
    axes[0].legend(frameon=False)
    axes[1].set(xlabel="elapsed from analysis_valid [s]", ylabel="output - raw P [rad]")
    figure.savefig(figures / "p_only_vs_pv_future_o1.png", dpi=200)
    figure.savefig(figures / "p_only_vs_pv_future_o1.svg")
    plt.close(figure)


def _blocked_parity_report(reason: str) -> ParityReport:
    modes: list[ModeParityResult] = []
    for mode in REQUIRED_MODES:
        run_id = ""
        modes.append(
            ModeParityResult(
                run_id=run_id,
                mode=mode,
                target_builder=_status_gate(
                    "target_builder",
                    GATE_NOT_EVALUABLE,
                    reason,
                    run_id=run_id,
                    mode=mode,
                ),
                solver_step=_status_gate(
                    "solver_step",
                    GATE_NOT_EVALUABLE,
                    reason,
                    run_id=run_id,
                    mode=mode,
                ),
                closed_loop=_status_gate(
                    "closed_loop",
                    GATE_NOT_EVALUABLE,
                    reason,
                    run_id=run_id,
                    mode=mode,
                ),
            )
        )
    return ParityReport(modes=tuple(modes))


def _snapshot_sufficiency_rows(
    observations: Sequence[SnapshotObservation],
) -> tuple[dict[str, Any], ...]:
    missing = (
        "full_axis_current_pva",
        "full_axis_target_pva_actually_passed_to_ruckig",
        "full_axis_output_pva",
        "complete_call_and_callback_sequence",
        "per_axis_limits_and_nullable_configuration",
        "run_reset_and_analysis_valid_controller_markers",
        "ruckig_version_commit_and_build_identity",
    )
    return tuple(
        {
            "run_id": "",
            "mode": observation.mode,
            "status": GATE_NOT_EVALUABLE,
            "formal_gate_eligible": False,
            "source_kind": "exploratory_right_axis_snapshot",
            "path": observation.path.as_posix(),
            "sha256": observation.sha256,
            "source_segment_count": observation.source_segment_count,
            "selected_segment_index": observation.selected_segment_index,
            "selected_source_count": observation.selected_source_count,
            "selected_output_count": observation.selected_output_count,
            "analysis_valid_source_count": observation.analysis_valid_source_count,
            "analysis_valid_output_count": observation.analysis_valid_output_count,
            "analysis_valid_start_s": observation.analysis_valid_start_s,
            "garbage_exclusion_s": SNAPSHOT_GARBAGE_EXCLUSION_S,
            "output_tick_coverage_fraction": observation.output_tick_coverage_fraction,
            "missing_requirements": missing,
            "reason": (
                "right-axis position snapshot cannot establish full-axis, "
                "per-call Ruckig parity"
            ),
        }
        for observation in observations
    )


def _generic_not_evaluable_rows(
    error: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "run_id": "",
            "mode": mode,
            "status": GATE_NOT_EVALUABLE,
            "formal_gate_eligible": False,
            "reason": error.get("message", "capture is not evaluable"),
            "error_code": error.get("code", "capture_validation_error"),
            "error_context": error.get("context", {}),
        }
        for mode in REQUIRED_MODES
    )


def _gate_summary_rows(report: ParityReport) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mode in report.modes:
        for gate in (mode.target_builder, mode.solver_step, mode.closed_loop):
            rows.append(
                {
                    "run_id": mode.run_id,
                    "mode": mode.mode,
                    "gate": gate.gate,
                    "status": gate.status,
                    "evaluated_point_count": gate.evaluated_point_count,
                    "bitwise_equal": gate.bitwise_equal,
                    "max_abs_errors": gate.max_abs_errors,
                    "reason": gate.reason,
                    "first_mismatch_call_seq": (
                        None
                        if gate.first_mismatch is None
                        else gate.first_mismatch.get("call_seq")
                    ),
                    "first_mismatch_axis_index": (
                        None
                        if gate.first_mismatch is None
                        else gate.first_mismatch.get("axis_index")
                    ),
                    "first_mismatch_components": (
                        ()
                        if gate.first_mismatch is None
                        else gate.first_mismatch.get("mismatch_components", ())
                    ),
                }
            )
    return rows


def _output_hashes(run_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(run_directory).as_posix(): sha256_file(path)
        for path in sorted(run_directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def _input_provenance(capture_root: Path) -> tuple[str, dict[str, Any]]:
    manifest = capture_root / "capture_manifest.json"
    if manifest.is_file():
        source_kind = "full_axis_controller_capture"
        names = (
            "capture_manifest.json",
            "calls.csv",
            "axis_states.csv",
            "raw_position_events.csv",
        )
    else:
        source_kind = "right_axis_snapshots"
        names = tuple(SNAPSHOT_FILES[mode] for mode in REQUIRED_MODES)
    files: dict[str, Any] = {}
    for name in names:
        path = capture_root / name
        files[name] = {
            "path": path.as_posix(),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256_file(path) if path.is_file() else None,
        }
    return source_kind, files


def _write_acceptance_summary(
    path: Path,
    *,
    data_status: str,
    report: ParityReport,
    downstream_status: str,
    first_mismatch: Mapping[str, Any] | None,
    sync_summary: Mapping[str, Any] | None,
    ablation_summary: Mapping[str, Any] | None,
) -> None:
    lines = [
        "# E18 full-axis parity validation",
        "",
        "- Operational run status: **completed**",
        f"- Data sufficiency: **{data_status}**",
        f"- All four modes passed all parity gates: **{report.all_passed}**",
        f"- Downstream status: **{downstream_status}**",
        "",
    ]
    for mode in report.modes:
        lines.append(
            f"- {mode.mode}: target={mode.target_builder.status}, "
            f"solver-step={mode.solver_step.status}, "
            f"closed-loop={mode.closed_loop.status}"
        )
    lines.append("")
    if first_mismatch is not None:
        lines.extend(
            (
                "## First blocking difference",
                "",
                f"- Classification: `{first_mismatch.get('classification')}`",
                f"- Mode/run: `{first_mismatch.get('mode', '')}` / "
                f"`{first_mismatch.get('run_id', '')}`",
                f"- Call/axis: `{first_mismatch.get('call_seq', '')}` / "
                f"`{first_mismatch.get('axis_index', '')}`",
                f"- Components: `{first_mismatch.get('mismatch_components', first_mismatch.get('code', ''))}`",
                "",
            )
        )
    if downstream_status == DOWNSTREAM_BLOCKED:
        lines.extend(
            (
                "同步模式排名和 P-only/PV 消融未生成。它们被 parity 门禁阻断，",
                "因此本次只陈述数据充分性或首个数值差异，不发布方法优劣结论。",
                "",
            )
        )
    else:
        lines.extend(
            (
                "## Post-parity analysis",
                "",
                f"- Synchronization conclusion: `{sync_summary}`",
                f"- P/PV local-ablation conclusion: `{ablation_summary}`",
                "",
                "同步排名来自同一 TimeIfNecessary 输入上的本地受控反事实；真机四模式",
                "只作 parity 后观测复核。P-only/PV 是本地因果消融，不外推为真机",
                "P-only 优劣结论。",
                "",
            )
        )
    lines.extend(
        (
            "逐点门限为 P=1e-12 rad、V=1e-10 rad/s、A=1e-8 rad/s²、",
            "trajectory duration=1e-12 s；RMSE 仅报告，不替代逐点判定。",
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_e18_validation_pipeline(
    *,
    project_root: str | Path,
    capture_root: str | Path | None = None,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
    thresholds: ParityThresholds = DEFAULT_THRESHOLDS,
) -> ExperimentResult:
    """Run the strict E18 stage pipeline; scientific blocking is not a crash."""

    root = Path(project_root).resolve()
    experiment_root = root / "experiments" / DIRECTORY_NAME
    if capture_root is None:
        formal_root = experiment_root / "data" / "full_axis_capture"
        capture_path = (
            formal_root
            if (formal_root / "capture_manifest.json").is_file()
            else experiment_root / "data" / "raw"
        )
    else:
        capture_path = Path(capture_root)
        if not capture_path.is_absolute():
            capture_path = root / capture_path
    capture_path = capture_path.resolve()
    source_kind, input_files = _input_provenance(capture_path)
    local_build = collect_local_ruckig_build()
    resolved_spec = {
        "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
        "capture_schema_version": CAPTURE_SCHEMA_VERSION,
        "capture_root": capture_path.as_posix(),
        "source_kind": source_kind,
        "input_files": input_files,
        "required_modes": REQUIRED_MODES,
        "parity_thresholds": asdict(thresholds),
        "stage_order": (
            "data_sufficiency",
            "target_builder_parity",
            "solver_step_parity",
            "closed_loop_parity",
            "synchronization_impact",
            "p_only_vs_pv_future_o1",
        ),
        "garbage_policy": (
            "simulate from run_reset; analysis_valid only controls scoring; "
            "snapshot fallback selects final source segment and excludes first 3 s"
        ),
        "failure_semantics": (
            "parity fail/not_evaluable blocks downstream but operational run completes"
        ),
    }
    run = start_compact_run(
        root,
        experiment_id=EXPERIMENT_ID,
        directory_name=DIRECTORY_NAME,
        title=PIPELINE_TITLE,
        resolved_spec=resolved_spec,
        runs_root=runs_root,
    )

    capture: FullAxisCapture | None = None
    observations: tuple[SnapshotObservation, ...] = ()
    first_mismatch: Mapping[str, Any] | None = None
    try:
        if source_kind == "full_axis_controller_capture":
            capture = load_full_axis_capture(capture_path)
            data_rows = validate_pipeline_data_sufficiency(
                capture, local_build=local_build
            )
            data_status = GATE_PASS
            report = run_parity(capture, thresholds=thresholds)
            first_mismatch = report.first_mismatch
        else:
            observations = inspect_snapshot_directory(capture_path)
            data_rows = _snapshot_sufficiency_rows(observations)
            data_status = GATE_NOT_EVALUABLE
            reason = (
                "current files contain only exploratory right-axis position topics; "
                "full-axis controller-internal capture is required"
            )
            report = _blocked_parity_report(reason)
            first_mismatch = {
                "classification": "data_sufficiency",
                "code": "right_axis_snapshot_only",
                "message": reason,
                "mismatch_components": tuple(
                    data_rows[0]["missing_requirements"]
                ),
                "snapshot_modes": [row.mode for row in observations],
            }
    except (CaptureValidationError, ValueError, OSError) as error:
        if isinstance(error, CaptureValidationError):
            first_mismatch = error.as_dict()
        else:
            first_mismatch = {
                "classification": "data_sufficiency",
                "code": "capture_read_error",
                "message": f"{type(error).__name__}: {error}",
                "context": {"capture_root": capture_path.as_posix()},
            }
        data_rows = _generic_not_evaluable_rows(first_mismatch)
        data_status = GATE_NOT_EVALUABLE
        report = _blocked_parity_report(str(first_mismatch["message"]))

    write_rows_csv(run.run_directory / "data_sufficiency.csv", data_rows)
    if observations:
        write_rows_csv(
            run.run_directory / "snapshot_data_quality.csv",
            [asdict(row) for row in observations],
        )

    output_paths: dict[str, str] = {
        "data_sufficiency": "data_sufficiency.csv",
    }
    if observations:
        output_paths["snapshot_data_quality"] = "snapshot_data_quality.csv"
    for mode_result in report.modes:
        directory = Path("parity") / MODE_SLUGS[mode_result.mode]
        mode_mismatch: Mapping[str, Any] | None = None
        for output_name, gate in (
            ("target_builder_parity", mode_result.target_builder),
            ("solver_step_parity", mode_result.solver_step),
            ("closed_loop_parity", mode_result.closed_loop),
        ):
            relative = directory / f"{output_name}.csv"
            write_rows_csv(run.run_directory / relative, gate.rows)
            output_paths[f"{MODE_SLUGS[mode_result.mode]}_{output_name}"] = (
                relative.as_posix()
            )
            if mode_mismatch is None and gate.first_mismatch is not None:
                mode_mismatch = {
                    **gate.first_mismatch,
                    "gate_max_abs_errors": gate.max_abs_errors,
                }
        if mode_mismatch is not None:
            relative = directory / "first_mismatch.json"
            write_json(run.run_directory / relative, mode_mismatch)
            output_paths[f"{MODE_SLUGS[mode_result.mode]}_first_mismatch"] = (
                relative.as_posix()
            )
    gate_summary = _gate_summary_rows(report)
    write_rows_csv(run.run_directory / "gate_summary.csv", gate_summary)
    output_paths["gate_summary"] = "gate_summary.csv"

    downstream_status = (
        DOWNSTREAM_ALLOWED if data_status == GATE_PASS and report.all_passed else DOWNSTREAM_BLOCKED
    )
    sync: dict[str, Any] | None = None
    ablation: dict[str, Any] | None = None
    if downstream_status == DOWNSTREAM_ALLOWED:
        if capture is None:
            raise RuntimeError("parity passed without a full-axis capture")
        sync = run_synchronization_counterfactual(capture)
        ablation = run_p_only_pv_ablation(capture)
        downstream_files = {
            "synchronization_counterfactual": sync["rows"],
            "synchronization_metrics": sync["metrics"],
            "synchronization_axis_metrics": sync["axis_metrics"],
            "synchronization_lag_scan": sync["lag_scan"],
            "target_transition_diagnostics": sync["transitions"],
            "real_mode_observation_metrics": sync["real_observation_metrics"],
            "p_only_pv_outputs": ablation["rows"],
            "p_only_pv_metrics": ablation["metrics"],
            "p_only_pv_axis_metrics": ablation["axis_metrics"],
        }
        for name, rows in downstream_files.items():
            relative = Path("post_parity") / f"{name}.csv"
            write_rows_csv(run.run_directory / relative, rows)
            output_paths[name] = relative.as_posix()
        write_json(
            run.run_directory / "post_parity/synchronization_summary.json",
            sync["summary"],
        )
        write_json(
            run.run_directory / "post_parity/p_only_pv_summary.json",
            ablation["summary"],
        )
        output_paths["synchronization_summary"] = (
            "post_parity/synchronization_summary.json"
        )
        output_paths["p_only_pv_summary"] = "post_parity/p_only_pv_summary.json"
        if create_figures:
            _write_post_parity_figures(
                run.run_directory,
                sync,
                ablation,
                right_axis_index=capture.manifest.right_axis_index,
            )
            output_paths["figures"] = "figures"

    downstream = {
        "downstream_status": downstream_status,
        "synchronization_analysis_generated": sync is not None,
        "p_only_pv_analysis_generated": ablation is not None,
        "blocked_reason": (
            None
            if downstream_status == DOWNSTREAM_ALLOWED
            else "all four modes must pass all three pointwise parity gates"
        ),
    }
    write_json(run.run_directory / "downstream_status.json", downstream)
    output_paths["downstream_status"] = "downstream_status.json"
    if first_mismatch is not None:
        write_json(run.run_directory / "first_mismatch.json", first_mismatch)
        output_paths["first_mismatch"] = "first_mismatch.json"

    summary = {
        "operational_status": "completed",
        "scientific_status": (
            "parity_passed" if report.all_passed else "blocked_by_parity"
        ),
        "data_sufficiency_status": data_status,
        "all_modes_all_gates_passed": report.all_passed,
        "gate_summary": gate_summary,
        "downstream": downstream,
        "synchronization": None if sync is None else sync["summary"],
        "p_only_vs_pv": None if ablation is None else ablation["summary"],
    }
    write_json(run.run_directory / "summary.json", summary)
    output_paths["summary"] = "summary.json"
    _write_acceptance_summary(
        run.run_directory / "acceptance_summary.md",
        data_status=data_status,
        report=report,
        downstream_status=downstream_status,
        first_mismatch=first_mismatch,
        sync_summary=None if sync is None else sync["summary"],
        ablation_summary=None if ablation is None else ablation["summary"],
    )
    output_paths["acceptance_summary"] = "acceptance_summary.md"

    run.manifest["capture"] = {
        "root": capture_path.as_posix(),
        "source_kind": source_kind,
        "input_files": input_files,
        "manifest": None if capture is None else capture.manifest.raw,
    }
    run.manifest["local_ruckig_build"] = local_build
    run.manifest["parity_thresholds"] = asdict(thresholds)
    run.manifest["data_sufficiency"] = {
        "status": data_status,
        "rows": data_rows,
    }
    run.manifest["parity"] = {
        "all_modes_all_gates_passed": report.all_passed,
        "gate_summary": gate_summary,
        "first_mismatch": first_mismatch,
    }
    run.manifest["downstream"] = downstream
    run.manifest["output_hashes"] = _output_hashes(run.run_directory)
    return finish_compact_run(
        run,
        outputs=output_paths,
        failures=(),
        required_failure_count=0,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run E18 full-axis controller/replay parity validation"
    )
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=None,
        help=(
            "full-axis capture root; defaults to data/full_axis_capture when "
            "present, otherwise the four exploratory data/raw snapshots"
        ),
    )
    parser.add_argument("--runs-root", type=Path, default=None)
    parser.add_argument("--no-figures", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_e18_validation_pipeline(
        project_root=Path(__file__).resolve().parents[2],
        capture_root=args.capture_root,
        runs_root=args.runs_root,
        create_figures=not args.no_figures,
    )
    print(result.run_directory)
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AXIS_STATE_FIELDS",
    "CALL_FIELDS",
    "CAPTURE_SCHEMA_VERSION",
    "CaptureValidationError",
    "DEFAULT_THRESHOLDS",
    "FullAxisCapture",
    "GateOutcome",
    "ModeParityResult",
    "ParityReport",
    "ParityThresholds",
    "RAW_POSITION_EVENT_FIELDS",
    "REQUIRED_MODES",
    "SnapshotObservation",
    "build_local_target_sequence",
    "collect_local_ruckig_build",
    "inspect_right_axis_snapshot",
    "inspect_snapshot_directory",
    "load_full_axis_capture",
    "main",
    "run_e18_validation_pipeline",
    "run_p_only_pv_ablation",
    "run_parity",
    "run_synchronization_counterfactual",
    "select_robust_sync_winner",
    "validate_no_data_sufficiency",
    "validate_pipeline_data_sufficiency",
]
