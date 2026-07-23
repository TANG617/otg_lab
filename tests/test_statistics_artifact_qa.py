from __future__ import annotations

import json
import zipfile

import pytest

from otg_lab.artifacts import (
    PRIMARY_EVIDENCE_REQUIRED_ARTIFACTS,
    ArtifactValidationError,
    build_primary_evidence_archive,
    verify_sample_artifact_recomputation,
    write_checksums,
)
from otg_lab.reporting import build_statistical_tables
from otg_lab.schema import empty_sample, recompute_sample_feasibility


def test_reporting_publishes_all_predeclared_strata_and_negative_results() -> None:
    trajectories = []
    records = []
    for index in range(8):
        trajectory_id = f"trajectory-{index:02d}"
        family = "family_a" if index < 4 else "family_b"
        trajectories.append(
            {
                "trajectory_id": trajectory_id,
                "split": "test",
                "family": family,
                "demand_stratum": "low" if index % 2 == 0 else "near_limit",
                "locked": True,
            }
        )
        candidate = 8.0 if family == "family_a" else 11.0
        identity = {
            "trajectory_id": trajectory_id,
            "split": "test",
            "scenario_id": "clean",
            "method_purity_rate": 1.0,
        }
        records.extend(
            (
                {**identity, "method": "baseline", "position_rmse": 10.0},
                {**identity, "method": "candidate", "position_rmse": candidate},
            )
        )

    result = build_statistical_tables(
        records,
        {"trajectories": trajectories},
        comparisons=(
            {
                "comparison_id": "primary",
                "metric": "position_rmse",
                "baseline_method": "baseline",
                "candidate_method": "candidate",
                "secondary": False,
            },
        ),
        ci_metrics=("position_rmse",),
        ci_methods=("baseline", "candidate"),
        resamples=300,
        seed=17,
        expected_test_count=8,
        default_sample_rate_hz=100.0,
        require_stratification=True,
    )

    assert {row["stratum_dimension"] for row in result.stratified_comparisons} == {
        "reference_family",
        "demand_stratum",
        "sample_rate_hz",
    }
    family_effects = {
        row["stratum_value"]: row["improvement"]
        for row in result.stratum_effects
        if row["stratum_dimension"] == "reference_family"
    }
    assert family_effects == {
        "family_a": pytest.approx(2.0),
        "family_b": pytest.approx(-1.0),
    }
    family_heterogeneity = next(
        row
        for row in result.heterogeneity
        if row["stratum_dimension"] == "reference_family"
    )
    assert family_heterogeneity["worst_stratum"] == "family_b"
    summary = result.trajectory_outcome_summary[0]
    assert summary["mean_improvement"] == pytest.approx(0.5)
    assert summary["median_improvement"] == pytest.approx(0.5)
    assert summary["improved_count"] == 4
    assert summary["harmful_rate"] == pytest.approx(0.5)
    assert len(result.worst_trajectories) == 5
    assert result.worst_trajectories[0]["improvement"] == pytest.approx(-1.0)


def _auditable_sample() -> dict:
    dt = 0.1
    jerk = 1.0
    terminal = (jerk * dt**3 / 6.0, 0.5 * jerk * dt**2, jerk * dt)
    row = empty_sample(
        run_id="qa",
        dataset_id="qa",
        session_id="qa",
        trajectory_id="qa",
        split="development",
        seed=1,
        joint_id="joint-0",
        k=0,
        source_time=0.0,
        arrival_time=0.0,
        control_time=0.0,
        dt_actual=dt,
        dt_control=dt,
        p_ref=0.0,
        p_meas=0.0,
        raw_target_p=terminal[0],
        raw_target_v=terminal[1],
        raw_target_a=terminal[2],
        raw_target_time=dt,
        executable_target_p=terminal[0],
        executable_target_v=terminal[1],
        executable_target_a=terminal[2],
        executable_target_time=dt,
        executable_target_free_trajectory_duration=0.05,
        command_p=terminal[0],
        command_v=terminal[1],
        command_a=terminal[2],
        command_jerk=jerk,
        command_time=dt,
        current_p=0.0,
        current_v=0.0,
        current_a=0.0,
        limit_max_velocity=1.0,
        limit_max_acceleration=1.0,
        limit_max_jerk=1.0,
        command_max_abs_velocity=terminal[1],
        command_max_abs_acceleration=terminal[2],
        command_max_abs_jerk=jerk,
        target_projected=False,
        fallback_requested=False,
        fallback_applied=False,
        fallback=False,
        fallback_reason="",
        safety_guarantee=True,
        emergency_mode=False,
        source_kind="unit_test",
        scenario_id="clean",
        truth_available=False,
        measurement_available=True,
        measurement_valid=True,
    )
    row.update(recompute_sample_feasibility(row))
    row["target_feasible"] = row["raw_target_point_admissible"]
    return row


def test_sample_artifact_qa_explicitly_recomputes_feasibility(monkeypatch) -> None:
    row = _auditable_sample()
    monkeypatch.setattr("otg_lab.artifacts.read_parquet", lambda *args, **kwargs: [row])
    monkeypatch.setattr(
        "otg_lab.artifacts.verify_recomputed_summary", lambda *args, **kwargs: None
    )

    report = verify_sample_artifact_recomputation(
        "samples.parquet",
        "metrics_by_trajectory.csv",
        "summary_metrics.csv",
        require_complete_feasibility=True,
    )
    assert report["sample_count"] == 1
    assert report["feasibility_fields_verified"]["command_segment_feasible"] == 1
    assert report["trajectory_metrics_verified"] is True
    assert report["summary_metrics_verified"] is True

    row["command_segment_feasible"] = False
    with pytest.raises(ArtifactValidationError, match="independent recomputation"):
        verify_sample_artifact_recomputation(
            "samples.parquet",
            "metrics_by_trajectory.csv",
            "summary_metrics.csv",
        )


def test_primary_evidence_archive_has_minimum_files_hash_and_local_path(
    tmp_path,
) -> None:
    bundle = tmp_path / "locked_test"
    bundle.mkdir()
    checksummed = []
    for relative in PRIMARY_EVIDENCE_REQUIRED_ARTIFACTS:
        if relative == "artifact_checksums.json":
            continue
        target = bundle / relative
        target.write_bytes(f"fixture:{relative}\n".encode())
        checksummed.append(target)
    write_checksums(bundle, checksummed)

    first = build_primary_evidence_archive(
        bundle, tmp_path / "primary-a.zip", validate_schemas=False
    )
    second = build_primary_evidence_archive(
        bundle, tmp_path / "primary-b.zip", validate_schemas=False
    )

    assert first["archive"]["sha256"] == second["archive"]["sha256"]
    assert first["archive"]["bytes"] > 0
    assert first["archive"]["local_path"].startswith(str(tmp_path.resolve()))
    assert (
        json.loads(
            (tmp_path / "primary-a.zip.manifest.json").read_text(encoding="utf-8")
        )["archive"]["sha256"]
        == first["archive"]["sha256"]
    )
    with zipfile.ZipFile(first["archive"]["local_path"]) as archive:
        assert set(archive.namelist()) == {
            f"primary_locked_test/{relative}"
            for relative in PRIMARY_EVIDENCE_REQUIRED_ARTIFACTS
        }

    (bundle / "method_matrix.json").unlink()
    with pytest.raises(ArtifactValidationError, match="missing"):
        build_primary_evidence_archive(
            bundle, tmp_path / "incomplete.zip", validate_schemas=False
        )
