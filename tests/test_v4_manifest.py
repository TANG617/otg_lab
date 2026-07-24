from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from otg_lab import datasets
from otg_lab.v4_freshness import (
    OVERLAP_KINDS,
    V4FreshnessError,
    audit_manifest_freshness,
    validate_manifest_freshness,
    validate_manifest_paths,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "split_manifest_v4_generator",
    ROOT / "scripts" / "generate_split_manifest_v4.py",
)
if GENERATOR_SPEC is None or GENERATOR_SPEC.loader is None:
    raise RuntimeError("could not load V4 split generator")
generator = importlib.util.module_from_spec(GENERATOR_SPEC)
sys.path.insert(0, str(ROOT))
try:
    GENERATOR_SPEC.loader.exec_module(generator)
finally:
    sys.path.pop(0)


def _load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_checked_in_v4_manifest_and_namespace_history_are_deterministic() -> None:
    manifest_path = ROOT / "split_manifest_v4.json"
    history_path = ROOT / "v4_seed_namespace_history.json"
    manifest = _load(manifest_path.name)
    history = _load(history_path.name)

    assert manifest == generator.build_manifest()
    assert history == generator.build_namespace_history()
    generator.validate_v4_manifest(manifest)
    assert history["accepted_attempt"] == 1
    assert history["attempts"] == [
        {
            "attempt": 1,
            "namespace_preimage_utf8": (
                "otg-lab/synthetic-feasible-v4/seed-lock/2026-07-23/pretest-attempt-001"
            ),
            "namespace_sha256": generator.SEED_NAMESPACE_SHA256,
            "candidate_seed_count": 300,
            "exact_historical_seed_collision_count": 0,
            "exact_historical_seed_collisions": [],
            "status": "accepted",
            "reselection_authorized": False,
        }
    ]
    assert history["trajectory_generation_performed"] is False


def test_v4_counts_ids_and_test_demand_strata_are_frozen() -> None:
    manifest = _load("split_manifest_v4.json")
    assert manifest["dataset_id"] == "synthetic-feasible-v4"
    assert manifest["content_scope"] == "identity_only_no_trajectory_content"
    assert len(manifest["trajectories"]) == 300
    assert len({row["seed"] for row in manifest["trajectories"]}) == 300
    assert all(
        set(row)
        == {
            "trajectory_id",
            "family",
            "split",
            "seed",
            "demand_stratum",
            "locked",
        }
        for row in manifest["trajectories"]
    )
    for family in datasets.FAMILIES:
        for split, expected in generator.SPLIT_COUNTS.items():
            rows = [
                row
                for row in manifest["trajectories"]
                if row["family"] == family and row["split"] == split
            ]
            assert len(rows) == expected
            assert {row["trajectory_id"] for row in rows} == {
                f"{family}__v4__{split}__{index:03d}" for index in range(expected)
            }
        test_rows = [
            row
            for row in manifest["trajectories"]
            if row["family"] == family and row["split"] == "test"
        ]
        assert {
            stratum: sum(row["demand_stratum"] == stratum for row in test_rows)
            for stratum in generator.DEMAND_STRATA
        } == {stratum: 5 for stratum in generator.DEMAND_STRATA}


def test_v4_manifest_generation_does_not_instantiate_or_render_trajectories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("identity manifest generation touched trajectory content")

    monkeypatch.setattr(datasets, "generate_trajectory", forbidden)
    monkeypatch.setattr(datasets, "trajectory_to_rows", forbidden)
    monkeypatch.setattr(datasets, "resample_truth", forbidden)
    monkeypatch.setattr(datasets, "ContinuousTrajectory", forbidden)

    manifest = generator.build_manifest()
    history = generator.build_namespace_history()
    assert len(manifest["trajectories"]) == 300
    assert history["trajectory_generation_performed"] is False


def test_v4_is_fresh_against_every_v1_v2_v3_identity_dimension() -> None:
    proof = validate_manifest_paths(
        ROOT / "split_manifest_v4.json",
        historical_manifest_paths=generator.DEFAULT_HISTORICAL,
    )
    assert proof["passed"] is True
    assert proof["historical_manifest_count"] == 3
    assert proof["aggregate_overlap_counts"] == {kind: 0 for kind in OVERLAP_KINDS}
    assert proof["total_overlap_count"] == 0
    assert proof["trajectory_generation_performed"] is False
    assert all(
        row["overlap_counts"] == {kind: 0 for kind in OVERLAP_KINDS}
        for row in proof["historical_manifests"]
    )


@pytest.mark.parametrize(
    ("kind", "inject"),
    (
        (
            "trajectory_id",
            lambda candidate, historical: candidate["trajectories"][0].update(
                trajectory_id=historical["trajectories"][0]["trajectory_id"]
            ),
        ),
        (
            "seed",
            lambda candidate, historical: candidate["trajectories"][0].update(
                seed=next(
                    row["seed"]
                    for row in historical["trajectories"]
                    if row["family"] != candidate["trajectories"][0]["family"]
                )
            ),
        ),
        (
            "family_seed",
            lambda candidate, historical: candidate["trajectories"][0].update(
                seed=next(
                    row["seed"]
                    for row in historical["trajectories"]
                    if row["family"] == candidate["trajectories"][0]["family"]
                )
            ),
        ),
        (
            "dataset_id",
            lambda candidate, historical: candidate.update(
                dataset_id=historical["dataset_id"]
            ),
        ),
        (
            "split_identity",
            lambda candidate, historical: candidate["trajectories"][0].update(
                trajectory_id=next(
                    row["trajectory_id"]
                    for row in historical["trajectories"]
                    if row["split"] == candidate["trajectories"][0]["split"]
                )
            ),
        ),
        (
            "namespace_hash",
            lambda candidate, historical: candidate.update(
                seed_namespace_sha256=historical["seed_namespace_sha256"]
            ),
        ),
    ),
)
def test_v4_freshness_rejects_each_injected_overlap(
    kind: str,
    inject: object,
) -> None:
    candidate = copy.deepcopy(generator.build_manifest())
    historical = _load("split_manifest_v3.json")
    inject(candidate, historical)  # type: ignore[operator]
    report = audit_manifest_freshness(candidate, [("v3", historical)])
    assert report["aggregate_overlap_counts"][kind] > 0
    with pytest.raises(V4FreshnessError) as captured:
        validate_manifest_freshness(candidate, [("v3", historical)])
    assert captured.value.report["aggregate_overlap_counts"][kind] > 0


def test_namespace_retry_is_authorized_only_by_exact_historical_seed_collision() -> (
    None
):
    first = b"unit/v4/attempt-001"
    second = b"unit/v4/attempt-002"
    first_seed = generator._identity_rows(first)[0]["seed"]
    historical = (
        (
            Path("historical.json"),
            {
                "dataset_id": "historical",
                "seed_namespace_sha256": "f" * 64,
                "trajectories": [
                    {
                        "trajectory_id": "historical-id",
                        "family": "stationary_endpoint",
                        "split": "train",
                        "seed": first_seed,
                    }
                ],
            },
        ),
    )
    selected, records = generator.select_seed_namespace(
        (first, second),
        historical=historical,
    )
    assert selected == second
    assert [row["status"] for row in records] == [
        "rejected_exact_historical_seed_collision",
        "accepted",
    ]
    assert records[0]["exact_historical_seed_collision_count"] == 1

    with pytest.raises(ValueError, match="unused namespace attempts"):
        generator.select_seed_namespace(
            (first, second),
            historical=(
                (
                    Path("collision-free.json"),
                    {
                        "dataset_id": "historical",
                        "seed_namespace_sha256": "f" * 64,
                        "trajectories": [],
                    },
                ),
            ),
        )
