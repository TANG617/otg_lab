from __future__ import annotations

import copy
from pathlib import Path

import pytest

import otg_lab.experiments as experiments
from otg_lab.config import load_config
from otg_lab.experiments import (
    combine_outcomes,
    locked_method,
    repeated_runtime_study,
    run_pipeline_matrix,
    stratified_entries,
    synthetic_cases,
    write_experiment_bundle,
)

ROOT = Path(__file__).resolve().parents[1]


def test_stratified_prefix_covers_every_family_before_repeating():
    entries = stratified_entries("validation", maximum=6)
    assert len(entries) == 6
    assert len({entry.family for entry in entries}) == 6
    assert all(entry.split == "validation" and not entry.locked for entry in entries)


def test_pipeline_matrix_records_method_and_continuous_audit():
    config = load_config("configs/development.yaml")
    cases = synthetic_cases("validation", sample_rate_hz=100.0, maximum=1)
    method = locked_method(
        estimator="ca_kf",
        estimator_parameters={"measurement_sigma": 1e-4},
        predictor="constant_acceleration",
        horizon_ms=20.0,
        method_id="unit_locked",
    )
    outcome = run_pipeline_matrix(cases, config, [method])
    assert outcome.successful_trajectory_runs == 1
    assert outcome.attempted_trajectory_runs == 1
    assert not outcome.failures
    assert outcome.samples
    assert outcome.method_matrix[0]["method_id"] == "unit_locked"
    assert outcome.method_matrix[0]["pipeline"]["prediction_horizon_ms"] == 20.0
    assert all(row["method_id"] == "unit_locked" for row in outcome.samples)
    assert len(outcome.constraint_audits) == len(outcome.samples)
    assert all(
        row["audit_method"] == "analytic_constant_jerk"
        for row in outcome.constraint_audits
    )
    assert all(row["violation_count"] == 0 for row in outcome.constraint_audits)


def test_matrix_failure_is_explicit_and_combine_preserves_it():
    config = load_config("configs/development.yaml")
    cases = synthetic_cases("validation", sample_rate_hz=100.0, maximum=1)
    valid = locked_method(
        estimator="position_only",
        estimator_parameters={},
        predictor="zero_order_hold",
        horizon_ms=0.0,
        method_id="valid",
    )
    invalid = copy.deepcopy(valid)
    invalid["method_id"] = "invalid"
    invalid["pipeline"]["estimator"] = "not_an_estimator"
    first = run_pipeline_matrix(cases, config, [valid, invalid])
    second = run_pipeline_matrix(cases, config, [valid])
    combined = combine_outcomes([first, second])
    assert first.attempted_trajectory_runs == 2
    assert first.successful_trajectory_runs == 1
    assert first.failures[0]["failure_type"] == "KeyError"
    assert first.failures[0]["k"] is None
    assert combined.attempted_trajectory_runs == 3
    assert combined.successful_trajectory_runs == 2
    assert len(combined.failures) == 1


def test_repeated_runtime_study_keeps_each_repetition_and_discards_warmup():
    config = load_config("configs/development.yaml")
    cases = synthetic_cases("validation", sample_rate_hz=100.0, maximum=1)
    method = locked_method(
        estimator="position_only",
        estimator_parameters={},
        predictor="zero_order_hold",
        horizon_ms=0.0,
        method_id="runtime_unit",
    )
    samples, summaries = repeated_runtime_study(
        cases,
        config,
        [method],
        repetitions=2,
        warmup_cycles=1,
    )
    assert samples
    assert {row["repetition"] for row in samples} == {0, 1}
    assert all(row["k"] >= 1 for row in samples)
    assert all(row["total_compute_us"] >= 0.0 for row in samples)
    assert len(summaries) == 2
    assert all(row["method"] == "runtime_unit" for row in summaries)


def test_standard_bundle_validation_failure_never_publishes_destination(
    tmp_path, monkeypatch
):
    config = load_config("configs/development.yaml")
    config["run_id"] = "atomic-failure-test"
    cases = synthetic_cases("validation", sample_rate_hz=100.0, maximum=1)
    method = locked_method(
        estimator="position_only",
        estimator_parameters={},
        predictor="zero_order_hold",
        horizon_ms=0.0,
        method_id="atomic_unit",
    )
    outcome = run_pipeline_matrix(cases, config, [method])
    destination = tmp_path / "atomic-bundle"

    monkeypatch.setattr(
        experiments,
        "validate_artifact_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("QA failed")),
    )
    with pytest.raises(RuntimeError, match="QA failed"):
        write_experiment_bundle(
            destination,
            config,
            outcome,
            command=("unit", "atomic"),
            repo_root=ROOT,
            split="validation",
            sample_rates_hz=(100.0,),
            source="unit",
            selection_policy="unit",
            require_clean=False,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".atomic-bundle.staging-*"))
