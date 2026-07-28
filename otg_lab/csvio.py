"""Canonical trajectory CSV and metadata sidecar I/O."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from .models import (
    TRAJECTORY_SCHEMA_VERSION,
    Trajectory,
    TrajectoryMetadata,
)

TRAJECTORY_CSV_HEADER = (
    "sample_index",
    "time_s",
    "position_rad",
    "velocity_rad_s",
    "acceleration_rad_s2",
    "jerk_rad_s3",
)
DERIVATIVE_COLUMNS = TRAJECTORY_CSV_HEADER[3:]


def trajectory_metadata_path(csv_path: str | Path) -> Path:
    """Return ``<stem>.meta.json`` for a canonical trajectory CSV."""

    path = Path(csv_path)
    return path.with_suffix(".meta.json")


def sha256_file(path: str | Path) -> str:
    """Hash a file without loading it all into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _format_float(value: float) -> str:
    # 17 significant digits round-trip every IEEE-754 double.
    return format(float(value), ".17g")


def _channel_semantics(trajectory: Trajectory) -> dict[str, str]:
    return {
        "position_rad": "truth",
        "velocity_rad_s": (
            "truth" if trajectory.has_velocity else "unavailable"
        ),
        "acceleration_rad_s2": (
            "truth" if trajectory.has_acceleration else "unavailable"
        ),
        "jerk_rad_s3": "truth" if trajectory.has_jerk else "unavailable",
    }


def _coerce_metadata(
    metadata: TrajectoryMetadata | Mapping[str, Any],
    trajectory: Trajectory,
) -> TrajectoryMetadata:
    if isinstance(metadata, TrajectoryMetadata):
        result = metadata
    elif isinstance(metadata, Mapping):
        raw = dict(metadata)
        raw.setdefault("schema_version", TRAJECTORY_SCHEMA_VERSION)
        raw.setdefault("dt_s", trajectory.dt)
        raw.setdefault("kind", "reference")
        raw.setdefault("channel_semantics", _channel_semantics(trajectory))
        raw.setdefault("source", {})
        raw.setdefault("generator_params", {})
        result = TrajectoryMetadata.from_mapping(raw)
    else:
        raise TypeError("metadata must be TrajectoryMetadata or a mapping")
    if not math.isclose(
        result.dt_s,
        trajectory.dt,
        rel_tol=1e-9,
        abs_tol=1e-12,
    ):
        raise ValueError("metadata dt_s does not match trajectory sampling period")
    return result


def _csv_text(trajectory: Trajectory) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TRAJECTORY_CSV_HEADER)
    channels = (
        trajectory.velocity_rad_s,
        trajectory.acceleration_rad_s2,
        trajectory.jerk_rad_s3,
    )
    for row_index in range(trajectory.sample_count):
        row = [
            str(int(trajectory.sample_index[row_index])),
            _format_float(trajectory.time_s[row_index]),
            _format_float(trajectory.position_rad[row_index]),
        ]
        row.extend(
            ""
            if channel is None
            else _format_float(channel[row_index])
            for channel in channels
        )
        writer.writerow(row)
    return buffer.getvalue()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_trajectory_csv(
    path: str | Path,
    trajectory: Trajectory,
    metadata: TrajectoryMetadata | Mapping[str, Any],
) -> None:
    """Write a canonical CSV and its validated ``.meta.json`` sidecar.

    The sidecar records the SHA-256 of the exact CSV bytes, so loading the
    pair detects accidental edits or mismatched copies.
    """

    if not isinstance(trajectory, Trajectory):
        raise TypeError("trajectory must be Trajectory")
    csv_path = Path(path)
    metadata_value = _coerce_metadata(metadata, trajectory)
    csv_content = _csv_text(trajectory)
    csv_hash = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
    metadata_value = replace(metadata_value, csv_sha256=csv_hash)
    metadata_content = (
        json.dumps(
            metadata_value.as_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )
    _atomic_write_text(csv_path, csv_content)
    _atomic_write_text(trajectory_metadata_path(csv_path), metadata_content)


def load_trajectory_metadata(path: str | Path) -> TrajectoryMetadata:
    """Load a sidecar by CSV path or direct ``.meta.json`` path."""

    candidate = Path(path)
    if not candidate.name.endswith(".meta.json"):
        candidate = trajectory_metadata_path(candidate)
    try:
        with candidate.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as error:
        raise ValueError(f"{candidate} is not valid JSON: {error}") from error
    if not isinstance(raw, Mapping):
        raise ValueError(f"{candidate} must contain a JSON object")
    return TrajectoryMetadata.from_mapping(raw)


def _parse_required_float(value: str, column: str, line_number: int) -> float:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"line {line_number}: {column} cannot be blank")
    try:
        result = float(stripped)
    except ValueError as error:
        raise ValueError(
            f"line {line_number}: {column} must be a number"
        ) from error
    if not math.isfinite(result):
        raise ValueError(f"line {line_number}: {column} must be finite")
    return result


def _parse_index(value: str, line_number: int) -> int:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"line {line_number}: sample_index cannot be blank")
    # Deliberately reject 1.0 and scientific notation: this is an integer
    # identity column, not a floating-point measure.
    signless = stripped[1:] if stripped[:1] in {"+", "-"} else stripped
    if not signless.isdigit():
        raise ValueError(
            f"line {line_number}: sample_index must be an integer literal"
        )
    return int(stripped)


def _validate_semantics(
    trajectory: Trajectory,
    metadata: TrajectoryMetadata,
) -> None:
    present = {
        "position_rad": True,
        "velocity_rad_s": trajectory.has_velocity,
        "acceleration_rad_s2": trajectory.has_acceleration,
        "jerk_rad_s3": trajectory.has_jerk,
    }
    unavailable_labels = {"unavailable", "blank", "none", "profile_only"}
    for channel, is_present in present.items():
        declared_unavailable = (
            metadata.channel_semantics[channel].strip().lower()
            in unavailable_labels
        )
        if declared_unavailable == is_present:
            state = "present" if is_present else "blank"
            raise ValueError(
                f"metadata declares {channel!r} as "
                f"{metadata.channel_semantics[channel]!r}, but CSV is {state}"
            )
    if metadata.kind == "reference" and trajectory.sample_count:
        if int(trajectory.sample_index[0]) != 0:
            raise ValueError("reference sample_index must start at 0")


def load_trajectory_csv(
    path: str | Path,
    *,
    metadata_path: str | Path | None = None,
    require_metadata: bool = False,
    verify_hash: bool = True,
) -> Trajectory:
    """Load and strictly validate a canonical fixed-grid trajectory.

    Metadata is validated whenever its sidecar exists.  Set
    ``require_metadata=True`` at experiment boundaries to enforce the complete
    artifact contract; leaving it false is useful for focused schema-error
    diagnostics and small test fixtures.
    """

    csv_path = Path(path)
    sidecar = (
        Path(metadata_path)
        if metadata_path is not None
        else trajectory_metadata_path(csv_path)
    )
    metadata: TrajectoryMetadata | None = None
    if sidecar.exists():
        metadata = load_trajectory_metadata(sidecar)
    elif require_metadata:
        raise FileNotFoundError(f"trajectory metadata sidecar is missing: {sidecar}")

    if metadata is not None and verify_hash and metadata.csv_sha256 is not None:
        observed_hash = sha256_file(csv_path)
        if observed_hash != metadata.csv_sha256:
            raise ValueError(
                f"CSV SHA-256 mismatch for {csv_path}: expected "
                f"{metadata.csv_sha256}, got {observed_hash}"
            )

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration as error:
            raise ValueError(f"{csv_path} is empty") from error
        if header != TRAJECTORY_CSV_HEADER:
            raise ValueError(
                "trajectory CSV header must be exactly "
                f"{','.join(TRAJECTORY_CSV_HEADER)}; got {','.join(header)}"
            )

        sample_indices: list[int] = []
        times: list[float] = []
        positions: list[float] = []
        derivative_cells: dict[str, list[str]] = {
            name: [] for name in DERIVATIVE_COLUMNS
        }
        for line_number, row in enumerate(reader, start=2):
            if len(row) != len(TRAJECTORY_CSV_HEADER):
                raise ValueError(
                    f"line {line_number}: expected "
                    f"{len(TRAJECTORY_CSV_HEADER)} columns, got {len(row)}"
                )
            sample_indices.append(_parse_index(row[0], line_number))
            times.append(_parse_required_float(row[1], "time_s", line_number))
            positions.append(
                _parse_required_float(row[2], "position_rad", line_number)
            )
            for column, cell in zip(DERIVATIVE_COLUMNS, row[3:]):
                derivative_cells[column].append(cell)

    derivatives: dict[str, np.ndarray | None] = {}
    for column, cells in derivative_cells.items():
        blank = [not cell.strip() for cell in cells]
        if any(blank) and not all(blank):
            first_partial = blank.index(True) if any(blank) else 0
            raise ValueError(
                f"{column} must be entirely populated or entirely blank "
                f"(first blank data row {first_partial + 2})"
            )
        if not cells or all(blank):
            derivatives[column] = None
        else:
            derivatives[column] = np.asarray(
                [
                    _parse_required_float(cell, column, row_index)
                    for row_index, cell in enumerate(cells, start=2)
                ],
                dtype=np.float64,
            )

    nominal_dt_s = None if metadata is None else metadata.dt_s
    trajectory = Trajectory(
        sample_index=np.asarray(sample_indices, dtype=np.int64),
        time_s=np.asarray(times, dtype=np.float64),
        position_rad=np.asarray(positions, dtype=np.float64),
        velocity_rad_s=derivatives["velocity_rad_s"],
        acceleration_rad_s2=derivatives["acceleration_rad_s2"],
        jerk_rad_s3=derivatives["jerk_rad_s3"],
        nominal_dt_s=nominal_dt_s,
    )
    if metadata is not None:
        if not math.isclose(
            trajectory.dt,
            metadata.dt_s,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "metadata dt_s does not match period inferred from trajectory CSV"
            )
        _validate_semantics(trajectory, metadata)
    return trajectory


__all__ = [
    "DERIVATIVE_COLUMNS",
    "TRAJECTORY_CSV_HEADER",
    "load_trajectory_csv",
    "load_trajectory_metadata",
    "sha256_file",
    "trajectory_metadata_path",
    "write_trajectory_csv",
]
