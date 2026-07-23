#!/usr/bin/env python3
"""Record newline-delimited position messages into an auditable CSV trace.

Example (a real producer should supply its own source timestamps)::

    sensor_stream | python scripts/collect_csv.py \
      --output data/session-001/trajectory-001.csv \
      --session-id session-001 --trajectory-id trajectory-001

Input is one JSON object per line with at least ``position``.  Supported fields
are ``source_time``, ``arrival_time``, ``joint_id``, ``velocity``,
``acceleration``, and ``topic``.  Missing source/arrival timestamps are stamped
at collection time.  This recorder never labels differentiated values as truth.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_lab.importers import (  # noqa: E402
    audit_timestamps,
    json_lines_to_collection_records,
    read_position_records,
    write_collection_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--joint-id", default="joint_0")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--expected-dt", type=float, default=0.01)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = json_lines_to_collection_records(
        sys.stdin,
        session_id=args.session_id,
        trajectory_id=args.trajectory_id,
        default_joint_id=args.joint_id,
    )
    count = write_collection_csv(records, args.output, append=args.append)
    decoded = read_position_records(args.output, time_column="source_time")
    audit = audit_timestamps(
        [record.source_time for record in decoded], expected_dt_s=args.expected_dt
    )
    summary = {
        "output": str(args.output),
        "records_written": count,
        "total_records": len(decoded),
        "timestamp_audit": audit.to_dict(),
        "truth_fields": "unavailable (null); sensor velocity/acceleration are measurements only",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if audit.valid_for_strict_replay else 2


if __name__ == "__main__":
    raise SystemExit(main())
