"""Identity-only freshness audit for the frozen V4 synthetic split.

This module deliberately operates on manifest metadata only.  It does not
import or call the synthetic truth generator, trajectory renderer, or
experiment runner.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

FRESHNESS_SCHEMA_VERSION = "otg.v4-freshness-proof.v1"
OVERLAP_KINDS = (
    "trajectory_id",
    "seed",
    "family_seed",
    "dataset_id",
    "split_identity",
    "namespace_hash",
)


class V4FreshnessError(ValueError):
    """Raised when a V4 identity collides with an exposed manifest."""

    def __init__(self, report: Mapping[str, Any]) -> None:
        self.report = dict(report)
        counts = self.report.get("aggregate_overlap_counts", {})
        nonzero = {
            name: int(counts.get(name, 0))
            for name in OVERLAP_KINDS
            if int(counts.get(name, 0))
        }
        super().__init__(f"V4 manifest is not fresh: overlaps={nonzero}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read manifest {resolved}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be a JSON object: {resolved}")
    return value


def _identity_sets(manifest: Mapping[str, Any]) -> dict[str, set[Any]]:
    dataset_id = manifest.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("manifest must declare a non-empty dataset_id")
    raw_rows = manifest.get("trajectories")
    if not isinstance(raw_rows, list):
        raise ValueError("manifest trajectories must be a list")

    trajectory_ids: set[str] = set()
    seeds: set[int] = set()
    family_seeds: set[tuple[str, int]] = set()
    split_identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"manifest trajectory row {index} is not an object")
        trajectory_id = raw.get("trajectory_id")
        family = raw.get("family")
        split = raw.get("split")
        seed = raw.get("seed")
        if not isinstance(trajectory_id, str) or not trajectory_id:
            raise ValueError(f"manifest trajectory row {index} has invalid ID")
        if not isinstance(family, str) or not family:
            raise ValueError(f"manifest trajectory row {index} has invalid family")
        if not isinstance(split, str) or not split:
            raise ValueError(f"manifest trajectory row {index} has invalid split")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError(f"manifest trajectory row {index} has invalid seed")
        trajectory_ids.add(trajectory_id)
        seeds.add(seed)
        family_seeds.add((family, seed))
        # "Split identity" means the membership assignment attached to a
        # trajectory identity.  It is intentionally audited in addition to the
        # bare trajectory ID so the proof records both identity reuse and exact
        # identity/membership reuse.
        split_identities.add((trajectory_id, split))

    namespace_hash = manifest.get("seed_namespace_sha256")
    namespace_hashes: set[str] = set()
    if namespace_hash is not None:
        if (
            not isinstance(namespace_hash, str)
            or len(namespace_hash) != 64
            or any(character not in "0123456789abcdef" for character in namespace_hash)
        ):
            raise ValueError("seed_namespace_sha256 must be lowercase SHA-256 hex")
        namespace_hashes.add(namespace_hash)

    return {
        "trajectory_id": trajectory_ids,
        "seed": seeds,
        "family_seed": family_seeds,
        "dataset_id": {dataset_id},
        "split_identity": split_identities,
        "namespace_hash": namespace_hashes,
    }


def _jsonable_identity(kind: str, value: Any) -> Any:
    if kind in {"family_seed", "split_identity"}:
        return list(value)
    return value


def _sorted_overlaps(kind: str, values: set[Any]) -> list[Any]:
    return [_jsonable_identity(kind, value) for value in sorted(values)]


def audit_manifest_freshness(
    candidate: Mapping[str, Any],
    historical_manifests: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Return a complete V4-vs-history identity overlap report."""

    if not historical_manifests:
        raise ValueError("V4 freshness requires at least one historical manifest")
    candidate_sets = _identity_sets(candidate)
    historical_reports: list[dict[str, Any]] = []
    aggregate_counts = {kind: 0 for kind in OVERLAP_KINDS}

    for label, historical in historical_manifests:
        if not isinstance(label, str) or not label:
            raise ValueError("historical manifest label must be non-empty")
        historical_sets = _identity_sets(historical)
        overlaps = {
            kind: _sorted_overlaps(kind, candidate_sets[kind] & historical_sets[kind])
            for kind in OVERLAP_KINDS
        }
        overlap_counts = {kind: len(overlaps[kind]) for kind in OVERLAP_KINDS}
        for kind, count in overlap_counts.items():
            aggregate_counts[kind] += count
        historical_reports.append(
            {
                "label": label,
                "dataset_id": historical["dataset_id"],
                "overlap_counts": overlap_counts,
                "overlaps": overlaps,
            }
        )

    total = sum(aggregate_counts.values())
    return {
        "schema_version": FRESHNESS_SCHEMA_VERSION,
        "candidate_dataset_id": candidate["dataset_id"],
        "candidate_trajectory_count": len(candidate["trajectories"]),
        "historical_manifest_count": len(historical_reports),
        "split_identity_definition": ["trajectory_id", "split"],
        "historical_manifests": historical_reports,
        "aggregate_overlap_counts": aggregate_counts,
        "total_overlap_count": total,
        "passed": total == 0,
        "trajectory_generation_performed": False,
    }


def validate_manifest_freshness(
    candidate: Mapping[str, Any],
    historical_manifests: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Return the proof when all overlap counts are zero, otherwise fail."""

    report = audit_manifest_freshness(candidate, historical_manifests)
    if not report["passed"]:
        raise V4FreshnessError(report)
    return report


def audit_manifest_paths(
    candidate_path: str | Path,
    *,
    historical_manifest_paths: Sequence[str | Path],
) -> dict[str, Any]:
    """Audit manifest files without constructing any trajectory content."""

    candidate_resolved = Path(candidate_path).resolve()
    historical_resolved = tuple(
        Path(path).resolve() for path in historical_manifest_paths
    )
    report = audit_manifest_freshness(
        _load_manifest(candidate_resolved),
        tuple((str(path), _load_manifest(path)) for path in historical_resolved),
    )
    report["candidate_manifest"] = str(candidate_resolved)
    report["candidate_manifest_sha256"] = _sha256_file(candidate_resolved)
    for index, path in enumerate(historical_resolved):
        row = report["historical_manifests"][index]
        row["manifest"] = str(path)
        row["manifest_sha256"] = _sha256_file(path)
    return report


def validate_manifest_paths(
    candidate_path: str | Path,
    *,
    historical_manifest_paths: Sequence[str | Path],
) -> dict[str, Any]:
    """Path-based freshness validation with a machine-readable failure report."""

    report = audit_manifest_paths(
        candidate_path,
        historical_manifest_paths=historical_manifest_paths,
    )
    if not report["passed"]:
        raise V4FreshnessError(report)
    return report


__all__ = [
    "FRESHNESS_SCHEMA_VERSION",
    "OVERLAP_KINDS",
    "V4FreshnessError",
    "audit_manifest_freshness",
    "audit_manifest_paths",
    "validate_manifest_freshness",
    "validate_manifest_paths",
]
