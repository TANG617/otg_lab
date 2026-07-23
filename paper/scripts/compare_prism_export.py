#!/usr/bin/env python3
"""Compare a Prism export directory with the canonical paper tree."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    args = parser.parse_args()
    if not args.export_dir.is_dir():
        raise SystemExit(f"not a directory: {args.export_dir}")
    command = [
        "diff",
        "-ruN",
        "--exclude",
        ".git",
        "--exclude",
        "build",
        "--exclude",
        "dist",
        str(PAPER_ROOT),
        str(args.export_dir.resolve()),
    ]
    result = subprocess.run(command, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
