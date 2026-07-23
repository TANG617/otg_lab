"""Real-position import adapters with explicit clock and truth semantics.

The three replay modes are intentionally separate APIs:

``import_legacy_fixed_grid``
    Reads only the value column and assigns an exact fixed grid.
``import_timestamp_causal``
    Audits source timestamps and performs causal zero-order hold onto a control
    grid.  It never interpolates using a future sample.
``simulate_arrival_replay``
    Adds a distinct arrival clock, communication delay/jitter/drop realization,
    and causally releases samples at control ticks.

All real-data derivative truth columns remain null.
"""

from __future__ import annotations

import csv
import json
import math
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .schema import empty_sample, validate_samples, write_parquet

VALUE_COLUMNS = ("value", "position", "p_ref", "p_meas")
TIME_COLUMNS = ("timestamp", "source_time", "elapsed time", "elapsed_time", "time")


@dataclass(frozen=True)
class PositionRecord:
    source_time: float
    position: float
    arrival_time: float | None = None
    velocity: float | None = None
    acceleration: float | None = None
    topic: str | None = None
    joint_id: str = "joint_0"


@dataclass(frozen=True)
class TimestampAudit:
    sample_count: int
    finite: bool
    duplicate_count: int
    regression_count: int
    min_interval_s: float | None
    median_interval_s: float | None
    max_interval_s: float | None
    intervals_outside_tolerance: int
    expected_dt_s: float
    tolerance_s: float

    @property
    def valid_for_strict_replay(self) -> bool:
        return self.finite and self.duplicate_count == 0 and self.regression_count == 0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["valid_for_strict_replay"] = self.valid_for_strict_replay
        return result


@dataclass(frozen=True)
class ArrivalEvent:
    source_index: int
    source_time: float
    attempted_arrival_time: float
    transport_delay_s: float
    jitter_realization_s: float
    dropped: bool


@dataclass(frozen=True)
class ArrivalSimulationResult:
    rows: list[dict[str, Any]]
    events: tuple[ArrivalEvent, ...]
    timestamp_audit: TimestampAudit

    @property
    def dropped_count(self) -> int:
        return sum(event.dropped for event in self.events)


class TimestampFaultError(ValueError):
    pass


def _choose_column(
    fieldnames: Sequence[str] | None, choices: Sequence[str], label: str
) -> str:
    if not fieldnames:
        raise ValueError("CSV has no header")
    normalized = {name.strip().lower(): name for name in fieldnames}
    for choice in choices:
        if choice.lower() in normalized:
            return normalized[choice.lower()]
    raise ValueError(
        f"CSV has no recognized {label} column; looked for {list(choices)}"
    )


def _finite_float(value: Any, *, field: str, row_number: int) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"row {row_number}: invalid {field} value {value!r}"
        ) from error
    if not math.isfinite(result):
        raise ValueError(f"row {row_number}: non-finite {field} value")
    return result


def read_position_records(
    path: str | Path,
    *,
    value_column: str | None = None,
    time_column: str | None = None,
    joint_id: str = "joint_0",
) -> list[PositionRecord]:
    """Read a generic position CSV while preserving its source timestamps."""

    records: list[PositionRecord] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        value_name = value_column or _choose_column(
            reader.fieldnames, VALUE_COLUMNS, "position"
        )
        time_name = time_column or _choose_column(
            reader.fieldnames, TIME_COLUMNS, "time"
        )
        for row_number, row in enumerate(reader, start=2):
            source_time = _finite_float(
                row.get(time_name), field=time_name, row_number=row_number
            )
            position = _finite_float(
                row.get(value_name), field=value_name, row_number=row_number
            )
            topic = row.get("topic") or None
            record_joint = row.get("joint_id") or joint_id
            velocity = None
            acceleration = None
            if row.get("velocity") not in (None, ""):
                velocity = _finite_float(
                    row["velocity"], field="velocity", row_number=row_number
                )
            if row.get("acceleration") not in (None, ""):
                acceleration = _finite_float(
                    row["acceleration"], field="acceleration", row_number=row_number
                )
            arrival = None
            if row.get("arrival_time") not in (None, ""):
                arrival = _finite_float(
                    row["arrival_time"], field="arrival_time", row_number=row_number
                )
            records.append(
                PositionRecord(
                    source_time=source_time,
                    position=position,
                    arrival_time=arrival,
                    velocity=velocity,
                    acceleration=acceleration,
                    topic=topic,
                    joint_id=record_joint,
                )
            )
    if not records:
        raise ValueError("CSV contains no position records")
    return records


def read_values_only(
    path: str | Path, *, value_column: str | None = None
) -> list[float]:
    """Read only the position column (legacy semantics intentionally ignore time)."""

    values: list[float] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        value_name = value_column or _choose_column(
            reader.fieldnames, VALUE_COLUMNS, "position"
        )
        for row_number, row in enumerate(reader, start=2):
            values.append(
                _finite_float(
                    row.get(value_name), field=value_name, row_number=row_number
                )
            )
    if not values:
        raise ValueError("CSV contains no position values")
    return values


def audit_timestamps(
    timestamps: Sequence[float],
    *,
    expected_dt_s: float = 0.01,
    tolerance_s: float = 0.002,
) -> TimestampAudit:
    values = np.asarray(timestamps, dtype=float)
    if values.ndim != 1:
        raise ValueError("timestamps must be a one-dimensional sequence")
    finite = bool(np.all(np.isfinite(values)))
    if values.size < 2 or not finite:
        return TimestampAudit(
            sample_count=int(values.size),
            finite=finite,
            duplicate_count=0,
            regression_count=0,
            min_interval_s=None,
            median_interval_s=None,
            max_interval_s=None,
            intervals_outside_tolerance=0,
            expected_dt_s=expected_dt_s,
            tolerance_s=tolerance_s,
        )
    intervals = np.diff(values)
    positive = intervals[intervals > 0.0]
    return TimestampAudit(
        sample_count=int(values.size),
        finite=True,
        duplicate_count=int(np.count_nonzero(intervals == 0.0)),
        regression_count=int(np.count_nonzero(intervals < 0.0)),
        min_interval_s=float(np.min(positive)) if positive.size else None,
        median_interval_s=float(np.median(positive)) if positive.size else None,
        max_interval_s=float(np.max(positive)) if positive.size else None,
        intervals_outside_tolerance=int(
            np.count_nonzero(np.abs(intervals - expected_dt_s) > tolerance_s)
        ),
        expected_dt_s=expected_dt_s,
        tolerance_s=tolerance_s,
    )


def empirical_jitter_from_csv(
    path: str | Path,
    *,
    time_column: str | None = None,
    expected_dt_s: float = 0.01,
) -> np.ndarray:
    records = read_position_records(path, time_column=time_column)
    intervals = np.diff([record.source_time for record in records])
    if np.any(intervals <= 0.0):
        raise TimestampFaultError(
            "empirical jitter source has duplicate/regressing timestamps"
        )
    return intervals - expected_dt_s


def _real_row(
    *,
    run_id: str,
    dataset_id: str,
    session_id: str,
    trajectory_id: str,
    split: str,
    seed: int,
    joint_id: str,
    k: int,
    source_time: float,
    arrival_time: float,
    control_time: float,
    dt_actual: float,
    dt_control: float,
    position: float,
    velocity: float | None = None,
    acceleration: float | None = None,
    source_kind: str,
    scenario_id: str,
    **events: Any,
) -> dict[str, Any]:
    row = empty_sample(
        run_id=run_id,
        dataset_id=dataset_id,
        session_id=session_id,
        trajectory_id=trajectory_id,
        split=split,
        seed=seed,
        joint_id=joint_id,
        k=k,
        source_time=float(source_time),
        arrival_time=float(arrival_time),
        control_time=float(control_time),
        dt_actual=float(dt_actual),
        dt_control=float(dt_control),
        p_ref=float(position),
        v_ref_truth=None,
        a_ref_truth=None,
        j_ref_truth=None,
        p_meas=float(position),
        v_meas=None if velocity is None else float(velocity),
        a_meas=None if acceleration is None else float(acceleration),
        source_kind=source_kind,
        reference_family="real_position_trace",
        scenario_id=scenario_id,
        truth_available=False,
        measurement_available=True,
        measurement_valid=True,
    )
    row.update(events)
    flags = []
    for field, label in (
        ("event_dropped", "dropped"),
        ("event_burst_drop", "burst_drop"),
        ("event_held", "held"),
        ("event_duplicate", "duplicate"),
        ("event_timestamp_regression", "timestamp_regression"),
        ("deadline_miss", "deadline_miss"),
        ("state_reset", "state_reset"),
        ("invalid_input", "invalid_input"),
    ):
        if row[field]:
            flags.append(label)
    row["event_flags"] = ";".join(flags)
    return row


def import_legacy_fixed_grid(
    path: str | Path,
    *,
    dt_s: float = 0.01,
    value_column: str | None = None,
    run_id: str = "real-import",
    dataset_id: str = "plot-data-legacy-v1",
    session_id: str = "plot-data-session-001",
    trajectory_id: str = "plot-data-development-001",
    split: str = "development",
    seed: int = 0,
    joint_id: str = "joint_0",
) -> list[dict[str, Any]]:
    """Legacy replay: values only, exactly one sample every ``dt_s``."""

    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")
    values = read_values_only(path, value_column=value_column)
    rows = [
        _real_row(
            run_id=run_id,
            dataset_id=dataset_id,
            session_id=session_id,
            trajectory_id=trajectory_id,
            split=split,
            seed=seed,
            joint_id=joint_id,
            k=k,
            source_time=k * dt_s,
            arrival_time=k * dt_s,
            control_time=k * dt_s,
            dt_actual=dt_s,
            dt_control=dt_s,
            position=value,
            source_kind="real_csv_legacy_fixed_grid",
            scenario_id="legacy_fixed_grid",
        )
        for k, value in enumerate(values)
    ]
    validate_samples(rows)
    return rows


def import_timestamp_causal(
    path: str | Path,
    *,
    control_dt_s: float = 0.01,
    value_column: str | None = None,
    time_column: str | None = None,
    strict_timestamps: bool = True,
    run_id: str = "real-import",
    dataset_id: str = "plot-data-timestamp-v1",
    session_id: str = "plot-data-session-001",
    trajectory_id: str = "plot-data-development-001",
    split: str = "development",
    seed: int = 0,
    joint_id: str = "joint_0",
) -> tuple[list[dict[str, Any]], TimestampAudit]:
    """Causally zero-order-hold source samples onto a fixed control grid."""

    if control_dt_s <= 0.0:
        raise ValueError("control_dt_s must be positive")
    records = read_position_records(
        path, value_column=value_column, time_column=time_column, joint_id=joint_id
    )
    audit = audit_timestamps(
        [record.source_time for record in records], expected_dt_s=control_dt_s
    )
    if strict_timestamps and not audit.valid_for_strict_replay:
        raise TimestampFaultError(
            f"source timestamp audit failed: duplicates={audit.duplicate_count}, "
            f"regressions={audit.regression_count}, finite={audit.finite}"
        )
    if not audit.valid_for_strict_replay:
        raise TimestampFaultError(
            "causal resampling requires monotonic source timestamps; use the raw audit/fault path"
        )
    first = records[0].source_time
    last = records[-1].source_time
    grid = (
        first
        + np.arange(int(math.floor((last - first) / control_dt_s)) + 1) * control_dt_s
    )
    rows: list[dict[str, Any]] = []
    source_index = -1
    previous_source_index = -1
    previous_source_time: float | None = None
    for control_time in grid:
        while (
            source_index + 1 < len(records)
            and records[source_index + 1].source_time <= control_time + 1e-12
        ):
            source_index += 1
        if source_index < 0:  # Defensive against roundoff at the first tick.
            continue
        record = records[source_index]
        held = source_index == previous_source_index
        dt_actual = (
            control_dt_s
            if previous_source_time is None
            else record.source_time - previous_source_time
        )
        rows.append(
            _real_row(
                run_id=run_id,
                dataset_id=dataset_id,
                session_id=session_id,
                trajectory_id=trajectory_id,
                split=split,
                seed=seed,
                joint_id=record.joint_id or joint_id,
                k=len(rows),
                source_time=record.source_time,
                arrival_time=record.source_time,
                control_time=float(control_time),
                dt_actual=dt_actual,
                dt_control=control_dt_s,
                position=record.position,
                velocity=record.velocity,
                acceleration=record.acceleration,
                source_kind="real_csv_timestamp_causal_hold",
                scenario_id="timestamp_causal_hold",
                event_held=held,
                event_arrivals_count=0
                if held
                else source_index - previous_source_index,
            )
        )
        previous_source_index = source_index
        previous_source_time = record.source_time
    validate_samples(rows)
    return rows, audit


def simulate_arrival_replay(
    path: str | Path,
    *,
    control_dt_s: float = 0.01,
    base_delay_s: float = 0.002,
    jitter_std_s: float = 0.001,
    drop_probability: float = 0.0,
    seed: int = 81001,
    value_column: str | None = None,
    time_column: str | None = None,
    run_id: str = "real-import",
    dataset_id: str = "plot-data-arrival-v1",
    session_id: str = "plot-data-session-001",
    trajectory_id: str = "plot-data-development-001",
    split: str = "development",
    joint_id: str = "joint_0",
) -> ArrivalSimulationResult:
    """Simulate communication arrival and causal control-grid consumption."""

    if control_dt_s <= 0.0 or base_delay_s < 0.0 or jitter_std_s < 0.0:
        raise ValueError("invalid timing parameter")
    if not 0.0 <= drop_probability <= 1.0:
        raise ValueError("drop_probability must be in [0, 1]")
    records = read_position_records(
        path, value_column=value_column, time_column=time_column, joint_id=joint_id
    )
    audit = audit_timestamps(
        [record.source_time for record in records], expected_dt_s=control_dt_s
    )
    if not audit.valid_for_strict_replay:
        raise TimestampFaultError(
            "arrival replay requires finite, monotonic source timestamps"
        )
    rng = np.random.default_rng(seed)
    jitter = rng.normal(0.0, jitter_std_s, len(records))
    delays = np.maximum(0.0, base_delay_s + jitter)
    dropped = rng.random(len(records)) < drop_probability
    # Rebase epoch-scale clocks before adding millisecond delays. Binary64
    # otherwise loses sub-microsecond precision when arrival-source is later
    # checked against the recorded transport delay.
    source_origin = float(records[0].source_time)
    events_list = []
    for index, record in enumerate(records):
        source_time = float(record.source_time - source_origin)
        attempted_arrival_time = float(source_time + delays[index])
        realized_delay = float(attempted_arrival_time - source_time)
        events_list.append(
            ArrivalEvent(
                source_index=index,
                source_time=source_time,
                attempted_arrival_time=attempted_arrival_time,
                transport_delay_s=realized_delay,
                jitter_realization_s=float(realized_delay - base_delay_s),
                dropped=bool(dropped[index]),
            )
        )
    events = tuple(events_list)
    delivered = sorted(
        (event for event in events if not event.dropped),
        key=lambda event: event.attempted_arrival_time,
    )
    if not delivered:
        raise ValueError(
            "arrival simulation dropped every sample; no causal replay is possible"
        )
    origin = 0.0
    first_tick_index = int(
        math.ceil((delivered[0].attempted_arrival_time - origin) / control_dt_s - 1e-12)
    )
    last_time = max(event.attempted_arrival_time for event in events)
    last_tick_index = int(math.ceil((last_time - origin) / control_dt_s + 1e-12))
    grid = origin + np.arange(first_tick_index, last_tick_index + 1) * control_dt_s

    delivered_index = 0
    attempt_index = 0
    attempts_by_time = sorted(events, key=lambda event: event.attempted_arrival_time)
    selected: ArrivalEvent | None = None
    prior_selected: ArrivalEvent | None = None
    previous_tick = float("-inf")
    rows: list[dict[str, Any]] = []
    for control_time in grid:
        arrivals_count = 0
        while (
            delivered_index < len(delivered)
            and delivered[delivered_index].attempted_arrival_time
            <= control_time + 1e-12
        ):
            selected = delivered[delivered_index]
            delivered_index += 1
            arrivals_count += 1
        input_drop_count = 0
        while (
            attempt_index < len(attempts_by_time)
            and attempts_by_time[attempt_index].attempted_arrival_time
            <= control_time + 1e-12
        ):
            event = attempts_by_time[attempt_index]
            if event.attempted_arrival_time > previous_tick + 1e-12 and event.dropped:
                input_drop_count += 1
            attempt_index += 1
        if selected is None:
            previous_tick = float(control_time)
            continue
        record = records[selected.source_index]
        held = (
            prior_selected is not None
            and selected.source_index == prior_selected.source_index
        )
        source_delta = (
            control_dt_s
            if prior_selected is None
            else selected.source_time - prior_selected.source_time
        )
        regressed = prior_selected is not None and source_delta < 0.0
        duplicated = prior_selected is not None and source_delta == 0.0 and not held
        rows.append(
            _real_row(
                run_id=run_id,
                dataset_id=dataset_id,
                session_id=session_id,
                trajectory_id=trajectory_id,
                split=split,
                seed=seed,
                joint_id=record.joint_id or joint_id,
                k=len(rows),
                source_time=selected.source_time,
                arrival_time=selected.attempted_arrival_time,
                control_time=float(control_time),
                dt_actual=source_delta,
                dt_control=control_dt_s,
                position=record.position,
                velocity=record.velocity,
                acceleration=record.acceleration,
                source_kind="real_csv_arrival_simulation",
                scenario_id="arrival_simulation",
                event_held=held,
                event_duplicate=duplicated,
                event_timestamp_regression=regressed,
                event_arrivals_count=arrivals_count,
                event_input_drop_count=input_drop_count,
                source_jitter_s=selected.jitter_realization_s,
                transport_delay_s=selected.transport_delay_s,
            )
        )
        prior_selected = selected
        previous_tick = float(control_time)
    validate_samples(rows)
    return ArrivalSimulationResult(rows=rows, events=events, timestamp_audit=audit)


def import_csv(path: str | Path, *, mode: str, **kwargs: Any) -> Any:
    """Dispatch explicit CSV semantics without silently changing modes."""

    if mode == "legacy_fixed_grid":
        return import_legacy_fixed_grid(path, **kwargs)
    if mode == "timestamp_causal":
        return import_timestamp_causal(path, **kwargs)
    if mode == "arrival_simulation":
        return simulate_arrival_replay(path, **kwargs)
    raise ValueError(
        "mode must be one of: legacy_fixed_grid, timestamp_causal, arrival_simulation"
    )


COLLECTION_COLUMNS = (
    "source_time",
    "arrival_time",
    "session_id",
    "trajectory_id",
    "joint_id",
    "position",
    "velocity",
    "acceleration",
    "topic",
)


def write_collection_csv(
    records: Iterable[Mapping[str, Any]], path: str | Path, *, append: bool = False
) -> int:
    """Write recorder/ROS adapter output with a stable, importable header."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    exists = output.exists() and output.stat().st_size > 0
    count = 0
    with output.open("a" if append else "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=COLLECTION_COLUMNS, extrasaction="ignore"
        )
        if not append or not exists:
            writer.writeheader()
        for record in records:
            writer.writerow({name: record.get(name, "") for name in COLLECTION_COLUMNS})
            count += 1
    return count


def json_lines_to_collection_records(
    lines: Iterable[str],
    *,
    session_id: str,
    trajectory_id: str,
    default_joint_id: str = "joint_0",
) -> Iterator[dict[str, Any]]:
    """Adapt newline-delimited sensor JSON to the recorder CSV contract."""

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        arrival_time = float(payload.get("arrival_time", time.time()))
        source_time = float(payload.get("source_time", arrival_time))
        position = _finite_float(
            payload.get("position"), field="position", row_number=line_number
        )
        yield {
            "source_time": source_time,
            "arrival_time": arrival_time,
            "session_id": payload.get("session_id", session_id),
            "trajectory_id": payload.get("trajectory_id", trajectory_id),
            "joint_id": payload.get("joint_id", default_joint_id),
            "position": position,
            "velocity": payload.get("velocity", ""),
            "acceleration": payload.get("acceleration", ""),
            "topic": payload.get("topic", ""),
        }


def records_to_canonical_rows(
    records: Sequence[PositionRecord],
    *,
    run_id: str,
    dataset_id: str,
    session_id: str,
    trajectory_id: str,
    split: str = "development",
    dt_control_s: float = 0.01,
) -> list[dict[str, Any]]:
    """Convert already-decoded recorder/ROS records without inventing truth."""

    audit = audit_timestamps(
        [record.source_time for record in records], expected_dt_s=dt_control_s
    )
    if not audit.valid_for_strict_replay:
        raise TimestampFaultError(
            "record adapter input has duplicate/regressing source time"
        )
    rows = []
    for k, record in enumerate(records):
        arrival = (
            record.arrival_time
            if record.arrival_time is not None
            else record.source_time
        )
        dt_actual = (
            dt_control_s if k == 0 else record.source_time - records[k - 1].source_time
        )
        rows.append(
            _real_row(
                run_id=run_id,
                dataset_id=dataset_id,
                session_id=session_id,
                trajectory_id=trajectory_id,
                split=split,
                seed=0,
                joint_id=record.joint_id,
                k=k,
                source_time=record.source_time,
                arrival_time=arrival,
                control_time=arrival,
                dt_actual=dt_actual,
                dt_control=dt_control_s,
                position=record.position,
                velocity=record.velocity,
                acceleration=record.acceleration,
                source_kind="real_collector_or_ros",
                scenario_id="raw_import",
                transport_delay_s=float(arrival - record.source_time),
            )
        )
    validate_samples(rows)
    return rows


def write_imported_parquet(rows: Sequence[Mapping[str, Any]], path: str | Path) -> Path:
    """Named adapter for collector/converter scripts."""

    return write_parquet(rows, path)


__all__ = [
    "ArrivalEvent",
    "ArrivalSimulationResult",
    "COLLECTION_COLUMNS",
    "PositionRecord",
    "TimestampAudit",
    "TimestampFaultError",
    "audit_timestamps",
    "empirical_jitter_from_csv",
    "import_csv",
    "import_legacy_fixed_grid",
    "import_timestamp_causal",
    "json_lines_to_collection_records",
    "read_position_records",
    "read_values_only",
    "records_to_canonical_rows",
    "simulate_arrival_replay",
    "write_collection_csv",
    "write_imported_parquet",
]
