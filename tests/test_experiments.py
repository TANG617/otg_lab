from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import otg_lab.experiments as experiments
from otg_lab.config import load_config
from otg_lab.experiments import (
    ONE_STEP_DIRECT_ABLATION_METHOD_IDS,
    ORDINARY_RUCKIG_METHOD_IDS,
    SHIELDED_RUCKIG_METHOD_IDS,
    combine_outcomes,
    locked_method,
    repeated_runtime_study,
    run_pipeline_matrix,
    same_information_methods,
    stratified_entries,
    synthetic_cases,
    validate_method_matrix_identity,
    write_experiment_bundle,
)

ROOT = Path(__file__).resolve().parents[1]


def _identity_explicit_methods() -> list[dict]:
    return same_information_methods(
        estimator="ca_kf",
        estimator_parameters={"measurement_sigma": 1e-4},
        predictor="constant_acceleration",
        horizon_ms=20.0,
        qp_horizon_steps=20,
    )


def test_method_matrix_separates_ordinary_shielded_and_direct_identities():
    methods = _identity_explicit_methods()
    by_id = {method["method_id"]: method["pipeline"] for method in methods}

    assert ORDINARY_RUCKIG_METHOD_IDS <= set(by_id)
    assert SHIELDED_RUCKIG_METHOD_IDS <= set(by_id)
    assert set(ONE_STEP_DIRECT_ABLATION_METHOD_IDS) <= set(by_id)
    for method_id in ORDINARY_RUCKIG_METHOD_IDS:
        pipeline = by_id[method_id]
        assert pipeline["method_family"] == "ordinary_ruckig_unshielded"
        assert pipeline["governor"] == "none"
        assert pipeline["follower"] == "ruckig"
        assert pipeline["follower_parameters"] == {"safety_shield": False}
    for method_id in SHIELDED_RUCKIG_METHOD_IDS:
        pipeline = by_id[method_id]
        assert pipeline["method_family"] == "ordinary_ruckig_with_viability_shield"
        assert pipeline["governor"] == "none"
        assert pipeline["follower"] == "ruckig"
        assert pipeline["follower_parameters"] == {"safety_shield": True}


def test_one_step_target_component_ablation_differs_only_by_target_mode():
    by_id = {
        method["method_id"]: method["pipeline"]
        for method in _identity_explicit_methods()
    }
    pipelines = [by_id[method_id] for method_id in ONE_STEP_DIRECT_ABLATION_METHOD_IDS]
    assert [pipeline["target_mode"] for pipeline in pipelines] == ["p", "pv", "pva"]
    comparable = [
        {key: value for key, value in pipeline.items() if key != "target_mode"}
        for pipeline in pipelines
    ]
    assert comparable[0] == comparable[1] == comparable[2]


def test_method_identity_validation_rejects_hidden_shield_and_mixed_ablation():
    methods = _identity_explicit_methods()
    hidden_shield = copy.deepcopy(methods)
    ordinary = next(
        method
        for method in hidden_shield
        if method["method_id"] == "predicted_p_ordinary_ruckig"
    )
    ordinary["pipeline"]["follower_parameters"]["safety_shield"] = True
    with pytest.raises(ValueError, match="identity mismatch"):
        validate_method_matrix_identity(hidden_shield)

    mixed = copy.deepcopy(methods)
    pva = next(
        method
        for method in mixed
        if method["method_id"] == "one_step_governed_pva_direct"
    )
    pva["pipeline"]["predictor"] = "zero_order_hold"
    with pytest.raises(ValueError, match="may differ only"):
        validate_method_matrix_identity(mixed)


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
    assert all(
        row["method_semantics"] == "direct_constant_jerk"
        and row["native_follower"] == "direct_executable"
        and row["actual_command_algorithm"] == "direct_executable"
        and row["native_command_executed"] is True
        and row["safety_shield_requested"] is False
        and row["safety_shield_applied"] is False
        and row["fallback_changes_algorithm"] is False
        for row in outcome.samples
    )
    assert all(
        row["command_profile_kind"] == "constant_jerk"
        and row["command_profile_exact"] is True
        and row["command_endpoint_matches_profile"] is True
        and row["command_constant_jerk_exact"] is True
        and row["command_profile_segment_count"] == 1
        and row["command_profile_boundary_count"] == 0
        and len(json.loads(row["command_profile_segment_boundaries_json"])) == 2
        and len(json.loads(row["command_profile_segment_jerks_json"])) == 1
        for row in outcome.samples
    )
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
    samples, summaries, failures = repeated_runtime_study(
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
    assert all(row["timing_population_complete"] for row in summaries)
    assert failures == []


def test_repeated_runtime_study_retains_failed_units_and_denominators():
    config = load_config("configs/development.yaml")
    cases = synthetic_cases("validation", sample_rate_hz=100.0, maximum=1)
    bad_rows = copy.deepcopy(cases[0][1])
    for row in bad_rows:
        row["trajectory_id"] = f"{row['trajectory_id']}::invalid"
    bad_rows[0]["p_meas"] = "not-a-number"
    mixed_cases = [cases[0], ("invalid-runtime-case", bad_rows)]
    valid = locked_method(
        estimator="position_only",
        estimator_parameters={},
        predictor="zero_order_hold",
        horizon_ms=0.0,
        method_id="runtime_valid",
    )
    samples, summaries, failures = repeated_runtime_study(
        mixed_cases,
        config,
        [valid],
        repetitions=2,
        warmup_cycles=1,
    )

    assert samples
    assert len(failures) == 2
    assert {row["repetition"] for row in failures} == {0, 1}
    assert {row["dof"] for row in failures} == {1}
    assert all(row["method"] == "runtime_valid" for row in summaries)
    assert all(row["attempted_trajectory_count"] == 2 for row in summaries)
    assert all(row["timed_trajectory_count"] == 1 for row in summaries)
    assert all(row["failed_trajectory_count"] == 1 for row in summaries)
    assert all(not row["timing_population_complete"] for row in summaries)


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
