from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from otg_lab.artifacts import ArtifactValidationError, sha256_file
from otg_lab.v4_artifacts import (
    atomic_copy_and_promote_bundle,
    build_bounded_results_archive,
    build_primary_locked_test_archive,
    build_root_artifact_index,
    check_v3_immutability,
    validate_negative_result_preservation,
    validate_report_only_inputs,
    verify_root_artifact_index,
)

ROOT = Path(__file__).resolve().parents[1]
RAW_COMMIT = "a" * 40


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_root_index_covers_every_output_and_rejects_tamper(tmp_path: Path) -> None:
    root = tmp_path / "results"
    (root / "summaries").mkdir(parents=True)
    (root / "statistics").mkdir()
    (root / "summaries" / "summary.csv").write_text("x\n1\n", encoding="utf-8")
    (root / "statistics" / "effect.csv").write_text("x\n2\n", encoding="utf-8")

    report = build_root_artifact_index(root, raw_commit=RAW_COMMIT)
    assert report["artifact_count"] == 2
    assert {
        row["path"] for row in json.loads(
            (root / "artifact_index.json").read_text(encoding="utf-8")
        )["artifacts"]
    } == {"statistics/effect.csv", "summaries/summary.csv"}
    assert verify_root_artifact_index(root)["full_coverage_verified"] is True

    (root / "summaries" / "summary.csv").write_text("x\n9\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="hash differs"):
        verify_root_artifact_index(root)


def test_root_index_rejects_unindexed_late_output(tmp_path: Path) -> None:
    root = tmp_path / "results"
    root.mkdir()
    (root / "README.md").write_text("evidence\n", encoding="utf-8")
    build_root_artifact_index(root, raw_commit=RAW_COMMIT)
    (root / "late.txt").write_text("not indexed\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="coverage differs"):
        verify_root_artifact_index(root)


def test_deterministic_zips_have_verified_checksum_and_bounded_excludes_raw(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "locked_test"
    raw.mkdir()
    (raw / "samples.parquet").write_bytes(b"raw-samples")
    (raw / "metrics.csv").write_text("x\n1\n", encoding="utf-8")
    proof = {
        "checksums_verified": True,
        "profile_recomputation_verified": True,
        "feasibility_recomputation_verified": True,
        "trajectory_metric_recomputation_verified": True,
    }
    first = build_primary_locked_test_archive(
        raw, tmp_path / "primary-a.zip", validation_report=proof
    )
    second = build_primary_locked_test_archive(
        raw, tmp_path / "primary-b.zip", validation_report=proof
    )
    assert first["archive"]["sha256"] == second["archive"]["sha256"]
    assert (
        Path(first["sha256_sidecar"]).read_text(encoding="ascii").split()[0]
        == sha256_file(first["path"])
    )
    with zipfile.ZipFile(first["path"]) as archive:
        assert set(archive.namelist()) == {
            "primary_locked_test/metrics.csv",
            "primary_locked_test/samples.parquet",
        }

    results = tmp_path / "results"
    (results / "raw_runs" / "locked_test").mkdir(parents=True)
    (results / "raw_runs" / "locked_test" / "samples.parquet").write_bytes(b"large")
    (results / "README.md").write_text("bounded\n", encoding="utf-8")
    build_root_artifact_index(results, raw_commit=RAW_COMMIT)
    bounded = build_bounded_results_archive(results, tmp_path / "bounded.zip")
    assert bounded["raw_runs_excluded"] is True
    with zipfile.ZipFile(bounded["path"]) as archive:
        names = set(archive.namelist())
    assert "paper_evidence_v4/README.md" in names
    assert not any(name.startswith("paper_evidence_v4/raw_runs/") for name in names)
    assert not any(name.endswith(".parquet") for name in names)


def test_negative_result_must_exist_in_summary_and_handoff(tmp_path: Path) -> None:
    root = tmp_path / "results"
    _write_csv(
        root / "statistics" / "primary_comparison.csv",
        (
            "comparison_id",
            "baseline_method",
            "candidate_method",
            "n_trajectories",
            "relative_improvement",
            "relative_ci_low",
            "relative_ci_high",
            "classification",
        ),
        [
            {
                "comparison_id": "PVA_vs_P",
                "baseline_method": "P",
                "candidate_method": "PVA",
                "n_trajectories": 120,
                "relative_improvement": -0.01,
                "relative_ci_low": -0.03,
                "relative_ci_high": 0.01,
                "classification": "inconclusive",
            }
        ],
    )
    (root / "V4_RESULT_SUMMARY.md").write_text(
        "# V4 result\n\ninconclusive\n", encoding="utf-8"
    )
    (root / "paper_handoff.json").write_text(
        json.dumps(
            {
                "primary_result_classification": "inconclusive",
                "negative_results": [{"classification": "inconclusive"}],
            }
        ),
        encoding="utf-8",
    )
    assert validate_negative_result_preservation(root)["negative_result"] is True
    (root / "paper_handoff.json").write_text(
        json.dumps({"primary_result_classification": "inconclusive"}),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactValidationError, match="negative_results"):
        validate_negative_result_preservation(root)


def test_atomic_bundle_copy_validates_before_promotion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "value.txt").write_text("immutable\n", encoding="utf-8")
    destination = tmp_path / "destination"
    promoted = atomic_copy_and_promote_bundle(
        source,
        destination,
        validator=lambda root: (
            None
            if (root / "value.txt").read_text(encoding="utf-8") == "immutable\n"
            else (_ for _ in ()).throw(AssertionError())
        ),
    )
    assert promoted == destination
    assert (destination / "value.txt").read_text(encoding="utf-8") == "immutable\n"

    refused = tmp_path / "refused"
    with pytest.raises(RuntimeError, match="reject staging"):
        atomic_copy_and_promote_bundle(
            source,
            refused,
            validator=lambda _: (_ for _ in ()).throw(
                RuntimeError("reject staging")
            ),
        )
    assert not refused.exists()


def test_report_only_validates_both_raw_bundles_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "results"
    locked = root / "raw_runs" / "locked_test"
    oracle = root / "raw_runs" / "oracle_diagnostic"
    locked.mkdir(parents=True)
    oracle.mkdir(parents=True)
    calls: list[tuple[Path, str]] = []

    def fake_validate(path, *, bundle_kind, **kwargs):
        calls.append((Path(path), bundle_kind))
        return {
            "raw_commit": kwargs["expected_commit"],
            "trajectory_metric_recomputation_verified": True,
        }

    monkeypatch.setattr("otg_lab.v4_artifacts.validate_raw_bundle", fake_validate)
    report = validate_report_only_inputs(
        results_root=root,
        raw_commit=RAW_COMMIT,
        locked_test_root=locked,
        oracle_root=oracle,
    )
    assert report["experiment_execution_permitted"] is False
    assert report["trajectory_generation_permitted"] is False
    assert calls == [(locked, "locked_test"), (oracle, "oracle_diagnostic")]


def test_all_tracked_v3_evidence_is_byte_identical_to_frozen_base() -> None:
    proof = check_v3_immutability(ROOT)
    assert proof["tracked_file_count"] > 60
    assert proof["all_tracked_files_byte_identical_to_git_head"] is True
    assert proof["all_tracked_files_byte_identical_to_frozen_reference"] is True
    assert (
        proof["frozen_reference_commit"]
        == "1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe"
    )
    assert proof["raw_archive_downloaded"] is False
    assert len(proof["remote_archive"]["sha256"]) == 64
