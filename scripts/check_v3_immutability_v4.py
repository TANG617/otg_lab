#!/usr/bin/env python3
"""Emit a machine-readable proof that tracked frozen V3 evidence is unchanged."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from otg_lab.v4_artifacts import check_v3_immutability


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output")
    parser.add_argument(
        "--baseline-hashes",
        help="optional JSON object mapping tracked paths to SHA-256 values",
    )
    arguments = parser.parse_args()
    baselines = None
    if arguments.baseline_hashes:
        value = json.loads(
            Path(arguments.baseline_hashes).read_text(encoding="utf-8")
        )
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(digest, str)
            for key, digest in value.items()
        ):
            raise ValueError("baseline hashes must be a string-to-string JSON object")
        baselines = value
    proof = check_v3_immutability(
        arguments.repo_root,
        baseline_hashes=baselines,
        output_path=arguments.output,
    )
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
