#!/usr/bin/env python3
"""Convert ROS 2 JointState bags (or exported CSV) to canonical Parquet.

The optional ``rosbags`` package is needed only for ``.db3``/``.mcap`` input.
CSV input is dependency-light and accepts the collector schema or the existing
``plot_data.csv`` shape.  Velocity/acceleration from JointState are stored as
measurements; reference derivative truth remains null.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_lab.importers import (  # noqa: E402
    PositionRecord,
    read_position_records,
    records_to_canonical_rows,
    write_imported_parquet,
)


def _rosbag_records(path: Path, *, topic: str, joint: str | None) -> list[PositionRecord]:
    try:
        from rosbags.highlevel import AnyReader  # type: ignore
    except ImportError as error:
        raise RuntimeError(
            "ROS bag conversion requires the optional 'rosbags' package; "
            "export to CSV or install that package"
        ) from error

    records: list[PositionRecord] = []
    with AnyReader([path]) as reader:
        connections = [connection for connection in reader.connections if connection.topic == topic]
        if not connections:
            raise ValueError(f"topic {topic!r} was not found in {path}")
        for connection, bag_timestamp_ns, rawdata in reader.messages(connections=connections):
            message = reader.deserialize(rawdata, connection.msgtype)
            names = list(getattr(message, "name", ()))
            positions = list(getattr(message, "position", ()))
            if not positions:
                continue
            if joint is None:
                index = 0
                joint_name = names[0] if names else "joint_0"
            else:
                if joint not in names:
                    continue
                index = names.index(joint)
                joint_name = joint
            header = getattr(message, "header", None)
            stamp = getattr(header, "stamp", None)
            if stamp is not None:
                source_time = float(stamp.sec) + float(stamp.nanosec) * 1e-9
            else:
                source_time = float(bag_timestamp_ns) * 1e-9
            velocities = list(getattr(message, "velocity", ()))
            velocity = float(velocities[index]) if index < len(velocities) else None
            records.append(
                PositionRecord(
                    source_time=source_time,
                    arrival_time=float(bag_timestamp_ns) * 1e-9,
                    position=float(positions[index]),
                    velocity=velocity,
                    topic=topic,
                    joint_id=joint_name,
                )
            )
    if not records:
        raise ValueError("bag contained no matching finite JointState positions")
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topic", default="/joint_states")
    parser.add_argument("--joint")
    parser.add_argument("--time-column")
    parser.add_argument("--value-column")
    parser.add_argument("--run-id", default="ros-conversion")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--split", default="development")
    parser.add_argument("--control-dt", type=float, default=0.01)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input.suffix.lower() == ".csv":
        records = read_position_records(
            args.input,
            value_column=args.value_column,
            time_column=args.time_column,
            joint_id=args.joint or "joint_0",
        )
    else:
        records = _rosbag_records(args.input, topic=args.topic, joint=args.joint)
    rows = records_to_canonical_rows(
        records,
        run_id=args.run_id,
        dataset_id=args.dataset_id,
        session_id=args.session_id,
        trajectory_id=args.trajectory_id,
        split=args.split,
        dt_control_s=args.control_dt,
    )
    write_imported_parquet(rows, args.output)
    print(f"wrote {len(rows)} canonical samples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
