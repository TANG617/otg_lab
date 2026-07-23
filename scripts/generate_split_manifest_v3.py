#!/usr/bin/env python3
"""Create or audit the v3 seed/split lock without generating trajectories."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from otg_lab.artifacts import canonical_json_bytes, sha256_file
from otg_lab.datasets import (
    FAMILIES,
    validate_fresh_locked_test_manifest,
    validate_split_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "split_manifest_v3.json"
DEFAULT_EXPOSED = (ROOT / "split_manifest.json", ROOT / "split_manifest_v2.json")
DATASET_ID = "synthetic-feasible-v3"
SEED_NAMESPACE = (
    b"otg-lab/synthetic-feasible-v3/seed-lock/2026-07-22/after-frozen-v2"
)
SPLIT_COUNTS = {"train": 20, "validation": 10, "test": 20}
DEMAND_STRATA = ("low", "medium", "high", "near_limit")


def _seed(family: str, split: str, index: int) -> int:
    """Derive a stable positive 63-bit seed from the v3-only namespace."""

    identity = f"{family}/{split}/{index:03d}".encode()
    digest = hashlib.sha256(SEED_NAMESPACE + b"\0" + identity).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1) + 1


def build_manifest() -> dict[str, Any]:
    trajectories = []
    for family in FAMILIES:
        for split, count in SPLIT_COUNTS.items():
            for index in range(count):
                trajectories.append(
                    {
                        "trajectory_id": f"{family}__v3__{split}__{index:03d}",
                        "family": family,
                        "split": split,
                        "seed": _seed(family, split, index),
                        "demand_stratum": DEMAND_STRATA[index % len(DEMAND_STRATA)],
                        "locked": split == "test",
                    }
                )
    manifest = {
        "manifest_version": 1,
        "dataset_id": DATASET_ID,
        "generated_by": "scripts/generate_split_manifest_v3.py",
        "seed_derivation": "SHA-256 namespaced identity -> positive 63-bit integer",
        "seed_namespace_sha256": hashlib.sha256(SEED_NAMESPACE).hexdigest(),
        "motion_limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
        "internal_truth_rate_hz_min": 1000,
        "split_policy": (
            "whole trajectory; 20 train / 10 validation / 20 locked test per family"
        ),
        "locked_test_policy": (
            "test IDs and seeds are excluded from tuning, horizon selection, method "
            "selection, qualification, and figure selection"
        ),
        "families": list(FAMILIES),
        "trajectories": trajectories,
    }
    validate_split_manifest(manifest)
    return manifest


def _summary(path: Path, exposed_manifests: Sequence[Path]) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    counts = {
        split: sum(row["split"] == split for row in manifest["trajectories"])
        for split in SPLIT_COUNTS
    }
    return {
        "dataset_id": manifest["dataset_id"],
        "manifest": str(path),
        "manifest_sha256": sha256_file(path),
        "trajectory_count": len(manifest["trajectories"]),
        "split_counts": counts,
        "exposed_manifests": [str(path) for path in exposed_manifests],
        "freshness_guard": "passed",
        "trajectory_generation_performed": False,
    }


def write_or_check(
    output: Path, *, exposed_manifests: Sequence[Path], check: bool
) -> dict[str, Any]:
    expected = canonical_json_bytes(build_manifest())
    if check:
        if not output.is_file() or output.read_bytes() != expected:
            raise RuntimeError(f"{output} does not match deterministic generator output")
    else:
        output.write_bytes(expected)
    validate_fresh_locked_test_manifest(
        output,
        exposed_manifest_paths=tuple(exposed_manifests),
    )
    return _summary(output, exposed_manifests)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--exposed-manifest",
        type=Path,
        action="append",
        default=None,
        help="repeat to override the default v1+v2 exposed-manifest set",
    )
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    exposed = tuple(
        path.resolve()
        for path in (
            DEFAULT_EXPOSED if args.exposed_manifest is None else args.exposed_manifest
        )
    )
    summary = write_or_check(
        args.output.resolve(),
        exposed_manifests=exposed,
        check=bool(args.check),
    )
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
