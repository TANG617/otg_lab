from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from otg_lab.v4_handoff import (
    FIGURE_FILENAMES,
    HARMFUL_CLAIM,
    INCONCLUSIVE_CLAIM,
    POSITIVE_MATERIAL_CLAIM,
    V4HandoffError,
    _audit_gates,
    _claim,
    generate_v4_handoff,
    select_v4_representative_trajectories,
)

FAMILIES = (
    "stationary_endpoint",
    "oscillatory",
    "piecewise_constant_jerk",
    "stop_and_go",
    "rapid_reversal",
    "boundary_grazing",
)
METHODS = (
    "one_step_governed_p_direct",
    "one_step_governed_pv_direct",
    "one_step_governed_pva_direct",
)


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fieldnames or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def _primary_rows(
    *,
    classification: str = "practically_material",
    guardrails: tuple[bool, bool] = (True, True),
) -> list[dict]:
    rows = []
    ordinal = 0
    for family in FAMILIES:
        for index in range(20):
            baseline = 1.0 + ordinal * 0.001
            candidate = baseline * (1.08 if ordinal in {7, 118} else 0.92)
            rows.append(
                {
                    "trajectory_id": f"{family}__v4__test__{index:03d}",
                    "family": family,
                    "demand_stratum": ("low", "medium", "high", "near_limit")[
                        index // 5
                    ],
                    "baseline_position_rmse": baseline,
                    "candidate_position_rmse": candidate,
                    "candidate_minus_baseline_position_rmse": candidate - baseline,
                    "absolute_improvement": baseline - candidate,
                    "harmful": candidate > baseline,
                    "paired_trajectory_count": 120,
                    "required_trajectory_count": 120,
                    "overall_absolute_improvement": 0.06,
                    "overall_absolute_improvement_ci_low": 0.02,
                    "overall_absolute_improvement_ci_high": 0.09,
                    "overall_relative_improvement": 0.06,
                    "overall_relative_improvement_ci_low": 0.01,
                    "overall_relative_improvement_ci_high": 0.10,
                    "primary_result_classification": classification,
                    "max_error_guardrail_pass": guardrails[0],
                    "lag_guardrail_pass": guardrails[1],
                }
            )
            ordinal += 1
    return rows


def _build_evidence(root: Path, *, guardrails: tuple[bool, bool] = (True, True)) -> None:
    statistics = root / "statistics"
    raw = root / "raw_runs" / "locked_test"
    oracle = root / "raw_runs" / "oracle_diagnostic"
    primary = _primary_rows(guardrails=guardrails)
    _write_csv(statistics / "primary_comparison.csv", primary)

    metrics = []
    for row in primary:
        baseline = row["baseline_position_rmse"]
        for method, scale in zip(METHODS, (1.0, 0.96, 0.92)):
            metrics.append(
                {
                    "trajectory_id": row["trajectory_id"],
                    "family": row["family"],
                    "method": method,
                    "position_rmse": baseline * scale,
                }
            )
    _write_csv(statistics / "metrics_by_trajectory.csv", metrics)

    secondary = []
    for index in range(1, 6):
        secondary.append(
            {
                "comparison_id": f"S{index}",
                "status": "available",
                "relative_difference": 0.01 * index,
                "relative_improvement_ci_low": -0.01,
                "relative_improvement_ci_high": 0.04,
            }
        )
    _write_csv(statistics / "secondary_comparisons.csv", secondary)

    family_rows = [
        {
            "stratum_value": family,
            "trajectory_count": 20,
            "relative_improvement": -0.02 if family == "rapid_reversal" else 0.06,
            "relative_improvement_ci_low": -0.04,
            "relative_improvement_ci_high": 0.10,
            "harmful_rate": 0.1,
        }
        for family in FAMILIES
    ]
    demand_rows = [
        {
            "stratum_value": demand,
            "trajectory_count": 30,
            "relative_improvement": -0.01 if demand == "near_limit" else 0.05,
            "relative_improvement_ci_low": -0.03,
            "relative_improvement_ci_high": 0.09,
            "harmful_rate": 0.1,
        }
        for demand in ("low", "medium", "high", "near_limit")
    ]
    _write_csv(statistics / "family_effects.csv", family_rows)
    _write_csv(statistics / "demand_stratum_effects.csv", demand_rows)
    _write_csv(
        statistics / "acceleration_active_effect.csv",
        [
            {
                "stratum_value": "acceleration_active",
                "trajectory_count": 40,
                "relative_improvement": 0.04,
                "relative_improvement_ci_low": -0.01,
                "relative_improvement_ci_high": 0.08,
                "harmful_rate": 0.15,
            }
        ],
    )
    _write_csv(
        statistics / "harmful_trajectory_rate.csv",
        [
            {
                "comparison_id": "PVA_vs_P",
                "analysis_kind": "primary",
                "harmful_count": 2,
                "denominator": 120,
                "harmful_rate": 2 / 120,
            }
        ],
    )
    worst = sorted(
        primary,
        key=lambda row: (
            -row["candidate_minus_baseline_position_rmse"],
            row["trajectory_id"],
        ),
    )[:5]
    _write_csv(
        statistics / "worst_five_trajectories.csv",
        [
            {
                "trajectory_id": row["trajectory_id"],
                "candidate_minus_baseline_position_rmse": row[
                    "candidate_minus_baseline_position_rmse"
                ],
            }
            for row in worst
        ],
    )

    _write_csv(
        raw / "method_identity_summary.csv",
        [
            {
                "method": method,
                "trajectory_count": 120,
                "method_purity_rate": 1.0,
            }
            for method in METHODS
        ],
    )
    _write_csv(
        raw / "method_identity_by_trajectory.csv",
        [
            {
                "method_id": method,
                "trajectory_id": row["trajectory_id"],
                "method_purity_rate": 1.0,
                "passed": True,
            }
            for method in METHODS
            for row in primary
        ],
    )
    _write_csv(
        raw / "same_information_audit.csv",
        [
            {
                "trajectory_id": row["trajectory_id"],
                "k": 0,
                "same_information_passed": True,
            }
            for row in primary
        ],
    )
    _write_csv(
        raw / "constraint_audit.csv",
        [
            {
                "trajectory_id": row["trajectory_id"],
                "method": "one_step_governed_pva_direct",
                "violation_count": 0,
                "velocity_margin": 1.0,
                "acceleration_margin": 2.0,
                "jerk_margin": 3000.0,
            }
            for row in primary
        ],
    )
    _write_csv(
        raw / "completion_summary.csv",
        [
            {
                "method": method,
                "attempted_trajectories": 120,
                "completed_trajectories": 120,
                "failed_trajectories": 0,
            }
            for method in METHODS
        ],
    )
    _write_csv(
        raw / "failures.csv",
        [],
        ["run_id", "trajectory_id", "failure_type", "reason"],
    )
    _write_csv(
        raw / "fallback_events.csv",
        [],
        ["run_id", "trajectory_id", "k", "fallback_reason"],
    )
    _write_csv(
        raw / "runtime_benchmark.csv",
        [
            {
                "method": method,
                "runtime_p99_us": 400 + index,
                "runtime_max_us": 900 + index,
                "runtime_deadline_miss_rate": 0,
                "timing_population_complete": True,
                "repetition": repetition,
            }
            for repetition in range(5)
            for index, method in enumerate(METHODS)
        ],
    )
    _write_csv(
        raw / "runtime_repeated_samples.csv",
        [
            {
                "method": method,
                "dataset_id": "synthetic-feasible-v4",
                "session_id": "clean",
                "trajectory_id": row["trajectory_id"],
                "scenario_id": "clean",
                "repetition": repetition,
                "warmup_cycles_per_trajectory": 100,
                "k": 100,
                "total_compute_us": 400 + method_index,
                "deadline_miss": False,
            }
            for method_index, method in enumerate(METHODS)
            for repetition in range(5)
            for row in primary
        ],
    )
    _write_csv(
        raw / "runtime_repeated_failures.csv",
        [],
        ["method_id", "trajectory_id", "repetition", "reason"],
    )

    samples = []
    for row in primary:
        for method_index, method in enumerate(METHODS):
            for k in range(3):
                samples.append(
                    {
                        "trajectory_id": row["trajectory_id"],
                        "method_id": method,
                        "run_id": f"locked::{method}",
                        "dataset_id": "synthetic-feasible-v4",
                        "session_id": "clean",
                        "scenario_id": "clean",
                        "joint_id": "joint_0",
                        "k": k,
                        "control_time": 0.01 * k,
                        "dt_control": 0.01,
                        "command_time": 0.01 * (k + 1),
                        "command_p": 0.0,
                        "command_v": 0.0,
                        "command_a": 0.0,
                        "executable_target_p": 0.0,
                        "executable_target_v": 0.0,
                        "executable_target_a": 0.0,
                        "executable_target_time": 0.01 * (k + 1),
                        "raw_target_v": 0.0 if method_index == 0 else 0.1,
                        "raw_target_a": 0.1 if method_index == 2 else 0.0,
                        "free_trajectory_duration": 0.005,
                        "state_reset": False,
                        "target_projected": False,
                        "fallback": False,
                        "fallback_applied": False,
                        "emergency_mode": False,
                        "safety_shield_requested": False,
                        "safety_shield_applied": False,
                        "fallback_changes_algorithm": False,
                        "executable_target_point_admissible": True,
                        "command_segment_feasible": True,
                        "command_stopping_viable": True,
                        "command_next_step_exists": True,
                        "command_t_free_le_dt": True,
                        "command_continuous_constraints_satisfied": True,
                        "command_profile_exact": True,
                        "command_constant_jerk_exact": True,
                        "command_endpoint_matches_profile": True,
                        "command_profile_continuous_constraints_satisfied": True,
                        "native_command_executed": True,
                    }
                )
    raw.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(samples).to_parquet(raw / "samples.parquet", index=False)

    labels = {
        "information_condition": "offline_analytic_truth",
        "causal": False,
        "deployable": False,
        "diagnostic_only": True,
    }
    _write_csv(
        oracle / "oracle_target_component_metrics.csv",
        [
            {
                "trajectory_id": "stationary_endpoint__v4__test__000",
                "method": "oracle_one_step_pva_direct",
                **labels,
            }
        ],
    )
    _write_csv(
        oracle / "oracle_pv_vs_p.csv",
        [{"comparison_id": "oracle_pv_vs_p", "effect": 0.1}],
    )
    _write_csv(
        oracle / "oracle_pva_vs_pv.csv",
        [{"comparison_id": "oracle_pva_vs_pv", "effect": 0.2}],
    )
    _write_csv(
        oracle / "oracle_acceleration_active_effect.csv",
        [{"comparison_id": "oracle_active", "effect": 0.3}],
    )


def test_claim_wording_is_locked_and_guardrail_phrase_is_prohibited() -> None:
    primary = {
        "classification": "practically_material",
        "relative_improvement": 0.05,
        "relative_ci_low": 0.01,
        "relative_ci_high": 0.08,
        "max_error_guardrail_pass": False,
        "lag_guardrail_pass": True,
    }
    allowed, prohibited = _claim(primary, True)
    assert allowed == [POSITIVE_MATERIAL_CLAIM]
    assert any("without material degradation" in item for item in prohibited)

    assert _claim(
        dict(
            primary,
            classification="inconclusive",
            relative_improvement=0.0,
            relative_ci_low=-0.02,
            relative_ci_high=0.02,
        ),
        True,
    )[0] == [INCONCLUSIVE_CLAIM]
    assert _claim(
        dict(
            primary,
            classification="confirmed_harmful",
            relative_improvement=-0.03,
            relative_ci_low=-0.05,
            relative_ci_high=-0.01,
        ),
        True,
    )[0] == [HARMFUL_CLAIM]


def test_representative_selection_is_deterministic_and_keeps_all_fixed_families() -> None:
    rows = list(reversed(_primary_rows()))
    first = select_v4_representative_trajectories(rows)
    second = select_v4_representative_trajectories(list(reversed(rows)))
    assert first == second
    assert len(first) == 10
    fixed = [row for row in first if row["role"].startswith("fixed_family_index_zero")]
    assert {row["family"] for row in fixed} == set(FAMILIES)
    assert all(row["trajectory_id"].endswith("__000") for row in fixed)


def test_full_handoff_preserves_negative_rows_labels_figures_and_tex(tmp_path: Path) -> None:
    root = tmp_path / "paper_evidence_v4"
    _build_evidence(root, guardrails=(False, True))
    handoff = generate_v4_handoff(
        root,
        "a" * 40,
        {"statistics/primary_comparison.csv": hashlib.sha256(b"primary").hexdigest()},
    )
    assert handoff["primary_result_classification"] == "practically_material"
    assert {row["trajectory_id"] for row in handoff["negative_results"]["harmful_trajectories"]} == {
        _primary_rows()[7]["trajectory_id"],
        _primary_rows()[118]["trajectory_id"],
    }
    assert handoff["oracle_diagnostics"]["causal"] is False
    assert handoff["ordinary_ruckig"]["role"] == "contextual_secondary"
    assert handoff["guardrail_status"]["without_degradation_wording_permitted"] is False
    assert len(list((root / "generated_figures").glob("*.png"))) == len(
        FIGURE_FILENAMES
    )
    assert (root / "sample_traces" / "representative_selection.json").is_file()
    assert (root / "paper_handoff.json").is_file()
    assert (root / "paper_handoff.md").is_file()
    assert (root / "V4_RESULT_SUMMARY.md").is_file()
    assert (root / "generated_numbers.tex").is_file()
    tex = (root / "generated_tables" / "secondary_results.tex").read_text()
    assert r"contextual secondary" in tex
    assert r"\_" not in tex  # IDs are S1--S5; table remains minimally compile-safe.
    assert "\\begin{tabular}" in tex and "\\end{tabular}" in tex


def test_missing_data_fails_closed_without_creating_outputs(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    root.mkdir()
    with pytest.raises(V4HandoffError, match="required locked evidence"):
        generate_v4_handoff(
            root,
            "a" * 40,
            {"statistics/primary_comparison.csv": "b" * 64},
        )
    assert not (root / "paper_handoff.json").exists()


def test_identity_and_sample_gates_do_not_hide_an_early_failure(tmp_path: Path) -> None:
    root = tmp_path / "gates"
    _build_evidence(root)
    detail_path = root / "raw_runs" / "locked_test" / "method_identity_by_trajectory.csv"
    detail = list(csv.DictReader(detail_path.open(newline="", encoding="utf-8")))
    detail[0]["method_purity_rate"] = "0"
    detail[0]["passed"] = "False"
    _write_csv(detail_path, detail)
    assert _audit_gates(root)["method_identity"]["passed"] is False

    # Restore identity, then prove a single projected/emergency sample invalidates
    # the safety gate even though all later rows are clean.
    _build_evidence(root)
    sample_path = root / "raw_runs" / "locked_test" / "samples.parquet"
    samples = pd.read_parquet(sample_path)
    samples.loc[0, "target_projected"] = True
    samples.loc[0, "emergency_mode"] = True
    samples.to_parquet(sample_path, index=False)
    safety = _audit_gates(root)["safety"]
    assert safety["passed"] is False
    assert safety["sample_gate_failure_counts"]["target_projected"] == 1
    assert safety["sample_gate_failure_counts"]["emergency_mode"] == 1


def test_incomplete_primary_is_preserved_with_nullable_outputs(tmp_path: Path) -> None:
    root = tmp_path / "incomplete"
    _build_evidence(root)
    path = root / "statistics" / "primary_comparison.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    for row in rows:
        row["paired_trajectory_count"] = "114"
        row["overall_absolute_improvement"] = ""
        row["overall_absolute_improvement_ci_low"] = ""
        row["overall_absolute_improvement_ci_high"] = ""
        row["overall_relative_improvement"] = ""
        row["overall_relative_improvement_ci_low"] = ""
        row["overall_relative_improvement_ci_high"] = ""
        row["primary_result_classification"] = "unavailable_incomplete_denominator"
        row["max_error_guardrail_pass"] = ""
        row["lag_guardrail_pass"] = ""
    for row in rows[:6]:
        row["candidate_position_rmse"] = ""
        row["candidate_minus_baseline_position_rmse"] = ""
        row["absolute_improvement"] = ""
        row["harmful"] = ""
        row["paired_value_available"] = "False"
    _write_csv(path, rows)
    _write_csv(
        root / "statistics" / "worst_five_trajectories.csv",
        [],
        ["trajectory_id", "candidate_minus_baseline_position_rmse"],
    )
    handoff = generate_v4_handoff(
        root,
        "a" * 40,
        {"statistics/primary_comparison.csv": "b" * 64},
    )
    assert (
        handoff["primary_result_classification"]
        == "unavailable_incomplete_denominator"
    )
    assert handoff["primary_effect"]["relative_improvement"] is None
    assert handoff["representative_selection"]["selection_status"] == (
        "unavailable_incomplete_denominator"
    )
    assert "failed validity gate" in handoff["allowed_claim_wording"][0]
    assert "--" in (root / "generated_numbers.tex").read_text(encoding="utf-8")
