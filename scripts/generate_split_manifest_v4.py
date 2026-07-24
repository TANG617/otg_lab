#!/usr/bin/env python3
"""Create or audit the V4 identity manifest without generating trajectories."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from otg_lab.artifacts import canonical_json_bytes, sha256_file
from otg_lab.datasets import FAMILIES, validate_split_manifest
from otg_lab.v4_freshness import OVERLAP_KINDS, validate_manifest_paths

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "split_manifest_v4.json"
DEFAULT_HISTORY_OUTPUT = ROOT / "v4_seed_namespace_history.json"
DEFAULT_HISTORICAL = (
    ROOT / "split_manifest.json",
    ROOT / "split_manifest_v2.json",
    ROOT / "split_manifest_v3.json",
)
DATASET_ID = "synthetic-feasible-v4"
SPLIT_COUNTS = {"train": 20, "validation": 10, "test": 20}
DEMAND_STRATA = ("low", "medium", "high", "near_limit")

# Attempts are precommitted source constants.  Additional entries are permitted
# only after the preceding entry produces an exact historical seed collision.
SEED_NAMESPACE_ATTEMPTS = (
    b"otg-lab/synthetic-feasible-v4/seed-lock/2026-07-23/pretest-attempt-001",
)
SEED_NAMESPACE = SEED_NAMESPACE_ATTEMPTS[0]
SEED_NAMESPACE_SHA256 = hashlib.sha256(SEED_NAMESPACE).hexdigest()
NAMESPACE_HISTORY_SCHEMA_VERSION = "otg.v4-seed-namespace-history.v1"


def _seed(namespace: bytes, family: str, split: str, index: int) -> int:
    """Derive a stable positive 63-bit identity seed."""

    identity = f"{family}/{split}/{index:03d}".encode()
    digest = hashlib.sha256(namespace + b"\0" + identity).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1) + 1


def _identity_rows(namespace: bytes) -> list[dict[str, Any]]:
    return [
        {
            "trajectory_id": f"{family}__v4__{split}__{index:03d}",
            "family": family,
            "split": split,
            "seed": _seed(namespace, family, split, index),
            "demand_stratum": DEMAND_STRATA[index % len(DEMAND_STRATA)],
            "locked": split == "test",
        }
        for family in FAMILIES
        for split, count in SPLIT_COUNTS.items()
        for index in range(count)
    ]


def build_manifest(*, namespace: bytes = SEED_NAMESPACE) -> dict[str, Any]:
    """Build identity metadata only; no truth samples or trajectories exist."""

    manifest = {
        "manifest_version": 1,
        "dataset_id": DATASET_ID,
        "generated_by": "scripts/generate_split_manifest_v4.py",
        "content_scope": "identity_only_no_trajectory_content",
        "seed_derivation": "SHA-256 namespaced identity -> positive 63-bit integer",
        "seed_namespace_sha256": hashlib.sha256(namespace).hexdigest(),
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
            "test identity may be committed before lock, but test trajectory truth "
            "must not be generated, rendered, run, or viewed before the confirmation "
            "capability is active"
        ),
        "families": list(FAMILIES),
        "trajectories": _identity_rows(namespace),
    }
    validate_v4_manifest(manifest)
    return manifest


def validate_v4_manifest(manifest: Mapping[str, Any]) -> None:
    """Enforce V4-specific identity, count, and test-balance invariants."""

    validate_split_manifest(manifest)
    if manifest.get("dataset_id") != DATASET_ID:
        raise ValueError(f"V4 dataset_id must be {DATASET_ID}")
    if manifest.get("content_scope") != "identity_only_no_trajectory_content":
        raise ValueError("V4 manifest must declare identity-only content")
    if manifest.get("seed_namespace_sha256") != SEED_NAMESPACE_SHA256:
        raise ValueError("V4 manifest does not use the precommitted namespace")

    expected_ids = {
        f"{family}__v4__{split}__{index:03d}"
        for family in FAMILIES
        for split, count in SPLIT_COUNTS.items()
        for index in range(count)
    }
    observed_ids = {str(row["trajectory_id"]) for row in manifest["trajectories"]}
    if observed_ids != expected_ids:
        raise ValueError("V4 trajectory IDs do not match the frozen identity format")
    observed_seeds = [int(row["seed"]) for row in manifest["trajectories"]]
    if len(set(observed_seeds)) != len(observed_seeds):
        raise ValueError("V4 identity seeds must be globally unique")

    for family in FAMILIES:
        test_rows = [
            row
            for row in manifest["trajectories"]
            if row["family"] == family and row["split"] == "test"
        ]
        balance = Counter(str(row["demand_stratum"]) for row in test_rows)
        expected_balance = Counter({stratum: 5 for stratum in DEMAND_STRATA})
        if balance != expected_balance:
            raise ValueError(
                f"{family}/test demand balance must be five per stratum: {balance}"
            )


def _load_historical(
    historical_manifest_paths: Sequence[Path],
) -> tuple[tuple[Path, dict[str, Any]], ...]:
    loaded = []
    for path in historical_manifest_paths:
        resolved = path.resolve()
        value = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"historical manifest is not an object: {resolved}")
        loaded.append((resolved, value))
    return tuple(loaded)


def _historical_seed_index(
    historical: Sequence[tuple[Path, Mapping[str, Any]]],
) -> dict[int, list[dict[str, Any]]]:
    index: dict[int, list[dict[str, Any]]] = {}
    for path, manifest in historical:
        for row in manifest["trajectories"]:
            index.setdefault(int(row["seed"]), []).append(
                {
                    "manifest": path.name,
                    "dataset_id": str(manifest["dataset_id"]),
                    "trajectory_id": str(row["trajectory_id"]),
                    "family": str(row["family"]),
                    "split": str(row["split"]),
                }
            )
    return index


def select_seed_namespace(
    attempts: Sequence[bytes],
    *,
    historical: Sequence[tuple[Path, Mapping[str, Any]]],
) -> tuple[bytes, list[dict[str, Any]]]:
    """Select the first namespace with zero exact historical seed collisions.

    A later attempt is invalid when an earlier attempt is collision-free.  A
    namespace-hash collision without an exact seed collision fails closed,
    because the preregistered policy does not authorize a retry for that reason.
    """

    if not attempts:
        raise ValueError("at least one precommitted namespace attempt is required")
    historical_seeds = _historical_seed_index(historical)
    historical_namespace_hashes = {
        str(manifest["seed_namespace_sha256"])
        for _, manifest in historical
        if manifest.get("seed_namespace_sha256") is not None
    }
    records: list[dict[str, Any]] = []
    seen_namespace_hashes: set[str] = set()
    for attempt_number, namespace in enumerate(attempts, start=1):
        if not isinstance(namespace, bytes) or not namespace:
            raise ValueError("namespace attempts must be non-empty bytes")
        namespace_hash = hashlib.sha256(namespace).hexdigest()
        if namespace_hash in seen_namespace_hashes:
            raise ValueError("duplicate namespace attempt")
        seen_namespace_hashes.add(namespace_hash)
        candidate_seeds = sorted(
            {int(row["seed"]) for row in _identity_rows(namespace)}
        )
        if len(candidate_seeds) != sum(
            len(FAMILIES) * count for count in SPLIT_COUNTS.values()
        ):
            raise ValueError(
                "namespace creates an internal seed collision; V4 policy does not "
                "authorize automatic reselection"
            )
        collisions = [
            {
                "seed": seed,
                "historical_identities": historical_seeds[seed],
            }
            for seed in candidate_seeds
            if seed in historical_seeds
        ]
        record = {
            "attempt": attempt_number,
            "namespace_preimage_utf8": namespace.decode("utf-8"),
            "namespace_sha256": namespace_hash,
            "candidate_seed_count": len(candidate_seeds),
            "exact_historical_seed_collision_count": len(collisions),
            "exact_historical_seed_collisions": collisions,
        }
        if collisions:
            record["status"] = "rejected_exact_historical_seed_collision"
            record["reselection_authorized"] = True
            records.append(record)
            continue
        if namespace_hash in historical_namespace_hashes:
            raise ValueError(
                "namespace hash overlaps history without an exact seed collision; "
                "the V4 policy does not authorize reselection"
            )
        record["status"] = "accepted"
        record["reselection_authorized"] = False
        records.append(record)
        if attempt_number != len(attempts):
            raise ValueError(
                "unused namespace attempts are forbidden; retry only after an exact "
                "historical seed collision"
            )
        return namespace, records
    raise ValueError("every precommitted namespace attempt has a historical collision")


def build_namespace_history(
    *,
    historical_manifest_paths: Sequence[Path] = DEFAULT_HISTORICAL,
) -> dict[str, Any]:
    historical = _load_historical(historical_manifest_paths)
    selected, records = select_seed_namespace(
        SEED_NAMESPACE_ATTEMPTS,
        historical=historical,
    )
    if selected != SEED_NAMESPACE:
        raise ValueError("selected namespace differs from the generator constant")
    return {
        "schema_version": NAMESPACE_HISTORY_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "selection_policy": (
            "precommitted attempts in source order; reselect only after an exact "
            "candidate seed collision with any v1/v2/v3 manifest seed"
        ),
        "identity_scope": "all train, validation, and test manifest rows",
        "trajectory_generation_performed": False,
        "historical_manifests": [
            {
                "path": path.name,
                "dataset_id": manifest["dataset_id"],
                "sha256": sha256_file(path),
                "seed_count": len(manifest["trajectories"]),
            }
            for path, manifest in historical
        ],
        "attempts": records,
        "accepted_attempt": records[-1]["attempt"],
        "accepted_namespace_sha256": hashlib.sha256(selected).hexdigest(),
    }


def _counts(manifest: Mapping[str, Any]) -> dict[str, int]:
    return {
        split: sum(row["split"] == split for row in manifest["trajectories"])
        for split in SPLIT_COUNTS
    }


def _test_demand_counts(
    manifest: Mapping[str, Any],
) -> dict[str, dict[str, int]]:
    return {
        family: {
            stratum: sum(
                row["family"] == family
                and row["split"] == "test"
                and row["demand_stratum"] == stratum
                for row in manifest["trajectories"]
            )
            for stratum in DEMAND_STRATA
        }
        for family in FAMILIES
    }


def write_or_check(
    output: Path,
    *,
    history_output: Path,
    historical_manifest_paths: Sequence[Path],
    check: bool,
) -> dict[str, Any]:
    """Write/check identity locks and return their zero-overlap proof."""

    manifest = build_manifest()
    history = build_namespace_history(
        historical_manifest_paths=historical_manifest_paths
    )
    expected_manifest = canonical_json_bytes(manifest)
    expected_history = canonical_json_bytes(history)
    if check:
        if not output.is_file() or output.read_bytes() != expected_manifest:
            raise RuntimeError(
                f"{output} does not match deterministic V4 identity generator output"
            )
        if (
            not history_output.is_file()
            or history_output.read_bytes() != expected_history
        ):
            raise RuntimeError(
                f"{history_output} does not match namespace selection history"
            )
    else:
        # Record the pretest namespace decision before the identity manifest.
        history_output.write_bytes(expected_history)
        output.write_bytes(expected_manifest)

    proof = validate_manifest_paths(
        output,
        historical_manifest_paths=historical_manifest_paths,
    )
    return {
        "dataset_id": manifest["dataset_id"],
        "manifest": str(output),
        "manifest_sha256": sha256_file(output),
        "namespace_history": str(history_output),
        "namespace_history_sha256": sha256_file(history_output),
        "seed_namespace_sha256": manifest["seed_namespace_sha256"],
        "trajectory_count": len(manifest["trajectories"]),
        "split_counts": _counts(manifest),
        "test_demand_counts_by_family": _test_demand_counts(manifest),
        "historical_manifests": [str(path) for path in historical_manifest_paths],
        "freshness_overlap_counts": {
            kind: proof["aggregate_overlap_counts"][kind] for kind in OVERLAP_KINDS
        },
        "freshness_total_overlap_count": proof["total_overlap_count"],
        "freshness_guard": "passed",
        "content_scope": "identity_only_no_trajectory_content",
        "trajectory_generation_performed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--namespace-history-output",
        type=Path,
        default=DEFAULT_HISTORY_OUTPUT,
    )
    parser.add_argument(
        "--historical-manifest",
        type=Path,
        action="append",
        default=None,
        help="repeat to override the default v1+v2+v3 historical manifest set",
    )
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    historical = tuple(
        path.resolve()
        for path in (
            DEFAULT_HISTORICAL
            if args.historical_manifest is None
            else args.historical_manifest
        )
    )
    summary = write_or_check(
        args.output.resolve(),
        history_output=args.namespace_history_output.resolve(),
        historical_manifest_paths=historical,
        check=bool(args.check),
    )
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
