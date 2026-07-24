#!/usr/bin/env python3
"""Extract bounded paper evidence without rerunning any experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
OUTPUT = PAPER_ROOT / "generated/manifests/extracted_evidence.json"
PHASE_A_ROOT = REPO_ROOT / "results/vendor_target_state_ablation"
V3_ROOT = REPO_ROOT / "results/paper_evidence_v3"
V4_ROOT = REPO_ROOT / "results/paper_evidence_v4"

SOURCES = {
    "phase_a_tracking": PHASE_A_ROOT / "target_state_ablation_metrics.csv",
    "phase_a_derivatives": PHASE_A_ROOT / "derivative_source_metrics.csv",
    "phase_a_oracle": PHASE_A_ROOT / "oracle_sanity_metrics.csv",
    "phase_a_limits": PHASE_A_ROOT / "limit_sensitivity_metrics.csv",
    "phase_a_run": PHASE_A_ROOT / "run.json",
    "v3_status": REPO_ROOT / "protocol_status_v3.json",
    "v3_postreview": REPO_ROOT / "protocol_status_v3_postreview.json",
    "v3_acceptance": V3_ROOT / "summaries/acceptance_criteria.csv",
    "v3_fallback": V3_ROOT / "summaries/fallback_summary.csv",
    # The full runtime CSV belongs to the frozen release bundle and is not
    # present in every Git checkout.  Consume the committed, independently
    # recomputed primary row from the bounded evidence audit instead.
    "v3_runtime_primary": PAPER_ROOT / "logic/evidence_audit.json",
    "v3_artifact_index": V3_ROOT / "artifact_index.json",
    "v4_protocol": REPO_ROOT / "EXPERIMENT_PROTOCOL_V4.md",
    "v4_hypotheses": REPO_ROOT / "V4_HYPOTHESES.md",
    "v4_statistical_design": REPO_ROOT / "V4_STATISTICAL_DESIGN.json",
    "v4_acceptance_criteria": REPO_ROOT / "V4_ACCEPTANCE_CRITERIA.json",
    "v4_method_matrix": REPO_ROOT / "V4_METHOD_MATRIX.json",
    "v4_protocol_decisions": REPO_ROOT / "V4_PROTOCOL_DECISIONS.md",
    "v4_config_lock": REPO_ROOT / "config_lock_v4.json",
    "v4_split_manifest": REPO_ROOT / "split_manifest_v4.json",
    "v4_preregistration_status": REPO_ROOT / "protocol_status_v4.json",
    "v4_result_status": V4_ROOT / "protocol_status_v4.json",
    "v4_paper_handoff": V4_ROOT / "paper_handoff.json",
    "v4_primary": V4_ROOT / "statistics/primary_comparison.csv",
    "v4_method_identity": V4_ROOT / "statistics/method_identity_summary.csv",
    "v4_same_information": V4_ROOT / "statistics/same_information_audit.csv",
    "v4_runtime": V4_ROOT / "statistics/runtime_benchmark.csv",
    "v4_harmful": V4_ROOT / "statistics/harmful_trajectory_rate.csv",
    "v4_family": V4_ROOT / "statistics/family_effects.csv",
    "v4_ordinary_completion": V4_ROOT
    / "statistics/ordinary_ruckig_completion.csv",
    "v4_oracle_metrics": V4_ROOT
    / "statistics/oracle_target_component_metrics.csv",
    "v4_artifact_index": V4_ROOT / "artifact_index.json",
    "v4_artifact_index_digest": V4_ROOT / "artifact_index.sha256",
    "v4_same_information_failures": REPO_ROOT / "same_information_failures.csv",
    "v4_same_information_analysis": REPO_ROOT
    / "SAME_INFORMATION_FAILURE_ANALYSIS.md",
    "v4_agent_execution_audit": REPO_ROOT / "V4_AGENT_EXECUTION_AUDIT.md",
    "postfreeze_compatibility": PAPER_ROOT
    / "generated/manifests/postfreeze_compatibility.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_source_baseline() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "origin/main"], cwd=REPO_ROOT, text=True
    ).strip()


def git_source_timestamp() -> str:
    return subprocess.check_output(
        ["git", "show", "-s", "--format=%cI", "origin/main"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()


def records(frame: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    subset = frame[columns].copy()
    # ``to_json`` caps decimal precision and can erase or distort the
    # near-machine-precision PV/PVA difference used by the scoped non-result.
    # ``to_dict`` retains Python's round-trip float representation; the final
    # payload is serialized once by ``json.dumps`` below.
    return subset.to_dict(orient="records")


def json_scalar(value: Any) -> Any:
    """Convert a pandas/numpy scalar to its JSON-native Python value."""
    return value.item() if hasattr(value, "item") else value


def read_csv(path: Path) -> pd.DataFrame:
    # Pandas' default high-precision parser can differ by a few ULPs across
    # libc/platform combinations.  Round-trip mode restores the IEEE value
    # represented by the source decimal string and keeps CI provenance stable.
    return pd.read_csv(path, float_precision="round_trip")


def extract() -> dict[str, Any]:
    missing = [str(path) for path in SOURCES.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing evidence files:\n" + "\n".join(missing))

    tracking = read_csv(SOURCES["phase_a_tracking"])
    derivatives = read_csv(SOURCES["phase_a_derivatives"])
    oracle = read_csv(SOURCES["phase_a_oracle"])
    limits = read_csv(SOURCES["phase_a_limits"])
    phase_run = json.loads(SOURCES["phase_a_run"].read_text(encoding="utf-8"))
    acceptance = read_csv(SOURCES["v3_acceptance"])
    fallback = read_csv(SOURCES["v3_fallback"])
    runtime_audit = json.loads(
        SOURCES["v3_runtime_primary"].read_text(encoding="utf-8")
    )
    status = json.loads(SOURCES["v3_status"].read_text(encoding="utf-8"))
    postreview = json.loads(SOURCES["v3_postreview"].read_text(encoding="utf-8"))
    v4_status = json.loads(
        SOURCES["v4_result_status"].read_text(encoding="utf-8")
    )
    v4_handoff = json.loads(
        SOURCES["v4_paper_handoff"].read_text(encoding="utf-8")
    )
    v4_primary = read_csv(SOURCES["v4_primary"])
    v4_method_identity = read_csv(SOURCES["v4_method_identity"])
    v4_same_information = read_csv(SOURCES["v4_same_information"])
    v4_harmful = read_csv(SOURCES["v4_harmful"])
    v4_family = read_csv(SOURCES["v4_family"])
    v4_ordinary_completion = read_csv(SOURCES["v4_ordinary_completion"])
    v4_oracle_metrics = read_csv(SOURCES["v4_oracle_metrics"])
    v4_artifact_index = json.loads(
        SOURCES["v4_artifact_index"].read_text(encoding="utf-8")
    )
    postfreeze_compatibility = json.loads(
        SOURCES["postfreeze_compatibility"].read_text(encoding="utf-8")
    )

    analytic = tracking[
        tracking["dataset"].isin(["quadratic_with_extremum", "cubic", "sine"])
        & tracking["method_id"].isin(["p", "pv_truth", "pva_truth"])
    ]
    csv_results = tracking[tracking["dataset"].eq("csv")]
    acceptance_keys = [
        "continuous_vaj_violation_count_zero",
        "projection_rate_zero",
        "runtime_total_p99_below_1ms",
        "runtime_total_max_below_5ms",
        "runtime_100hz_deadline_miss_rate_zero",
        "nonfallback_point_admissibility_100pct",
        "nonfallback_t_free_le_dt_100pct",
        "nonfallback_sequence_consistency_100pct",
        "continuous_velocity_margin_nonnegative",
        "continuous_acceleration_margin_nonnegative",
        "continuous_jerk_margin_nonnegative",
    ]
    v3_rows = acceptance[acceptance["criterion_id"].isin(acceptance_keys)]
    v3_rows = v3_rows.assign(
        evidence_class=v3_rows["family"],
        observed=v3_rows["observed_value"],
        threshold=v3_rows["threshold_value"],
        passed=v3_rows["status"].eq("pass"),
    )
    direct_fallback = fallback[
        fallback["method"].eq("one_step_governed_pva_direct")
        & fallback["reason"].eq("__all__")
    ]
    if len(direct_fallback) != 1:
        raise ValueError("expected one direct-governor fallback roll-up")
    runtime_candidates = [
        item
        for item in runtime_audit["quantitative_candidates"]
        if item["candidate_id"] == "Q_V3_DIRECT_RUNTIME_PRIMARY"
    ]
    if len(runtime_candidates) != 1:
        raise ValueError("expected one audited primary direct-governor runtime row")
    runtime_candidate = runtime_candidates[0]
    runtime_values = runtime_candidate["values"]
    direct_runtime = {
        "warmup_samples_per_trajectory": runtime_candidate["selector"]["k_min"],
        "timed_cycle_count": runtime_values["timed_cycle_count"],
        "runtime_p50_us": runtime_values["runtime_p50_us"],
        "runtime_p90_us": runtime_values["runtime_p90_us"],
        "runtime_p99_us": runtime_values["runtime_p99_us"],
        "runtime_p99_9_us": runtime_values["runtime_p99_9_us"],
        "runtime_max_us": runtime_values["runtime_max_us"],
        "runtime_deadline_miss_rate": runtime_values["deadline_miss_rate"],
    }

    if len(v4_primary) != 120:
        raise ValueError(f"expected 120 V4 primary rows, got {len(v4_primary)}")
    primary_fields = (
        "required_trajectory_count",
        "paired_trajectory_count",
        "bootstrap_resamples",
        "overall_absolute_improvement",
        "overall_absolute_improvement_ci_low",
        "overall_absolute_improvement_ci_high",
        "overall_relative_improvement",
        "overall_relative_improvement_ci_low",
        "overall_relative_improvement_ci_high",
        "primary_result_classification",
        "max_error_guardrail_pass",
        "lag_guardrail_pass",
    )
    for field in primary_fields:
        if v4_primary[field].nunique(dropna=False) != 1:
            raise ValueError(f"V4 primary field is not constant: {field}")
    v4_primary_row = v4_primary.iloc[0]

    primary_methods = {
        "one_step_governed_p_direct",
        "one_step_governed_pv_direct",
        "one_step_governed_pva_direct",
    }
    v4_primary_identity = v4_method_identity[
        v4_method_identity["method"].isin(primary_methods)
    ]
    if len(v4_primary_identity) != 3:
        raise ValueError("expected three V4 primary direct-method identity rows")
    if not (v4_primary_identity["method_purity_rate"] == 1.0).all():
        raise ValueError("V4 primary method purity no longer equals 1.0")

    same_information_passed = (
        v4_same_information["audit_passed"]
        .astype(str)
        .str.strip()
        .str.lower()
        .eq("true")
    )
    v4_same_information_failures = v4_same_information[~same_information_passed]
    if len(v4_same_information) != 42072:
        raise ValueError("unexpected V4 same-information denominator")
    if len(v4_same_information_failures) != 5:
        raise ValueError("unexpected V4 same-information failure count")
    if not v4_same_information_failures["failed_fields"].str.endswith(
        ":event_flags"
    ).all():
        raise ValueError("V4 same-information failure is not event_flags-only")

    v4_harmful_primary = v4_harmful[
        v4_harmful["comparison_id"].eq("PVA_vs_P_position_RMSE")
    ]
    if len(v4_harmful_primary) != 1:
        raise ValueError("expected one V4 primary harmful-rate row")
    v4_rapid_reversal = v4_family[
        v4_family["stratum_value"].eq("rapid_reversal")
    ]
    if len(v4_rapid_reversal) != 1:
        raise ValueError("expected one V4 rapid-reversal family row")

    required_v4_status = {
        "status": "failed_test_visible_frozen",
        "statistical_classification": "strongly_material",
        "primary_result_classification": "invalid_method_identity",
        "same_test_rerun_permitted": False,
        "raw_experiment_resume_permitted": False,
    }
    for field, expected in required_v4_status.items():
        if v4_status.get(field) != expected:
            raise ValueError(
                f"unexpected V4 status {field}: {v4_status.get(field)!r}"
            )
    if (
        v4_handoff["same_information_gate"]["passed"]
        or not v4_handoff["method_identity_gate"]["passed"]
        or not v4_handoff["safety_gates"]["passed"]
        or v4_handoff["runtime_gates"]["passed"]
    ):
        raise ValueError("V4 gate disposition differs from the frozen handoff")

    payload = {
        "schema_version": "otg.paper-extracted-evidence.v2",
        "generated_at": git_source_timestamp(),
        # Paper-only commits must not make evidence extraction stale.  The
        # source baseline is the latest main commit from which this paper
        # branch was cut; each consumed artifact also carries its own hash.
        "source_commit": git_source_baseline(),
        "sources": {
            source_id: {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for source_id, path in SOURCES.items()
        },
        "phase_a": {
            "protocol": {
                "common_warmup_samples": phase_run["design"][
                    "common_warmup_samples"
                ],
                "input_csv_rows": phase_run["provenance"]["input_csv_rows"],
            },
            "analytic_tracking": records(
                analytic,
                [
                    "dataset",
                    "method_id",
                    "causal",
                    "rmse",
                    "mae",
                    "max_error",
                    "best_lag_ms",
                    "target_projection_rate",
                    "ruckig_compute_p99_us",
                ],
            ),
            "derivatives": records(
                derivatives,
                [
                    "dataset",
                    "derivative_source",
                    "causal",
                    "future_samples",
                    "native_delay_samples",
                    "velocity_rmse",
                    "acceleration_rmse",
                ],
            ),
            "oracle": records(
                oracle,
                [
                    "dataset",
                    "causal",
                    "future_samples",
                    "rmse",
                    "max_error",
                    "best_lag_ms",
                ],
            ),
            "csv_tracking": records(
                csv_results,
                [
                    "method_id",
                    "method",
                    "causal",
                    "future_samples",
                    "native_delay_samples",
                    "rmse",
                    "mae",
                    "max_error",
                    "best_lag_ms",
                    "target_projection_rate",
                    "raw_target_max_velocity",
                    "raw_target_max_acceleration",
                    "raw_target_max_sampled_jerk",
                ],
            ),
            "limit_sensitivity": records(
                limits,
                [
                    "dataset",
                    "method_id",
                    "sweep_type",
                    "sweep_value",
                    "evaluation_start_index",
                    "evaluation_stop_index_exclusive",
                    "rmse",
                    "best_lag_ms",
                ],
            ),
        },
        "v3": {
            "locked_test_trajectory_count": status["locked_test_trajectory_count"],
            "raw_bundle_count": status["raw_bundle_count"],
            "bounded_artifact_count": status["bounded_artifact_count"],
            "required_component_criteria": status["required_component_criteria"],
            "required_component_pass_count": status[
                "required_component_pass_count"
            ],
            "required_component_failure_count": status[
                "required_component_failure_count"
            ],
            "acceptance_rows": records(
                v3_rows,
                [
                    "criterion_id",
                    "evidence_class",
                    "method",
                    "metric",
                    "observed",
                    "operator",
                    "threshold",
                    "denominator",
                    "passed",
                ],
            ),
            "direct_fallback": records(
                direct_fallback,
                [
                    "fallback_cycle_count",
                    "total_cycle_count",
                    "fallback_rate",
                ],
            )[0],
            "direct_runtime_primary": direct_runtime,
            "postreview": postreview,
        },
        "v4": {
            "provenance": {
                "latest_main_commit": git_source_baseline(),
                "confirmation_source_commit": (
                    "461fc560461b0a4726cbabdb97b2dbd4dc305e0a"
                ),
                "bounded_result_commit": (
                    "f49b4ef1cacf8228c5d243353184acb8a7d02311"
                ),
                "report_only_reporting_repair_commit": (
                    "8baece6b7051ccc231d9bb0362fd85e4aa5a94e5"
                ),
                "report_only_same_information_aid_commit": (
                    "b9301eaf36dc04f1abf662c42821eddfe8c3188a"
                ),
                "release_tag": "paper-evidence-v4-461fc56",
            },
            "execution": {
                "fresh": True,
                "whole_trajectory": True,
                "same_follower": True,
                "exactly_once": True,
                "confirmation_execution_count": 1,
                "executed_during_paper_build": False,
                "raw_experiment_resumed_during_paper_build": False,
                "same_test_rerun_permitted": False,
                "raw_experiment_resume_permitted": False,
                "v5_executed": False,
            },
            "status": {
                "protocol_status": v4_status["status"],
                "test_visible": v4_status["test_visible"],
                "statistical_classification": v4_status[
                    "statistical_classification"
                ],
                "effective_classification": v4_status[
                    "primary_result_classification"
                ],
                "confirmatory_performance_claim_permitted": False,
            },
            "primary_observed_effect": {
                field: json_scalar(v4_primary_row[field])
                for field in primary_fields
            },
            "gates": {
                "same_information_passed": v4_handoff[
                    "same_information_gate"
                ]["passed"],
                "method_identity_passed": v4_handoff[
                    "method_identity_gate"
                ]["passed"],
                "safety_passed": v4_handoff["safety_gates"]["passed"],
                "lag_noninferiority_passed": v4_handoff[
                    "guardrail_status"
                ]["lag_noninferiority_pass"],
                "max_error_noninferiority_passed": v4_handoff[
                    "guardrail_status"
                ]["max_error_noninferiority_pass"],
                "hard_runtime_passed": v4_handoff["runtime_gates"]["passed"],
            },
            "same_information": {
                "aligned_cycle_count": len(v4_same_information),
                "failure_count": len(v4_same_information_failures),
                "failed_field": "composite event_flags",
                "only_differing_token": "deadline_miss",
                "all_other_compared_fields_passed": True,
                "diagnosis_changes_frozen_gate": False,
            },
            "primary_method_identity": records(
                v4_primary_identity,
                [
                    "method",
                    "trajectory_count",
                    "total_cycle_count",
                    "native_execution_rate",
                    "method_purity_rate",
                    "fallback_changes_algorithm_rate",
                    "unexpected_fallback_rate",
                ],
            ),
            "safety": {
                "failure_count": v4_handoff["safety_gates"]["failure_count"],
                "fallback_event_count": v4_handoff["safety_gates"][
                    "fallback_event_count"
                ],
                "continuous_constraint_audit_passed": v4_handoff[
                    "safety_gates"
                ]["continuous_constraint_audit_passed"],
                "synthetic_only": True,
                "hardware_safety_claim_permitted": False,
            },
            "runtime": {
                "full_instrumented_python_pipeline": True,
                "passed": v4_handoff["runtime_gates"]["passed"],
                "methods": v4_handoff["runtime_gates"]["methods"],
                "wcet_claim_permitted": False,
            },
            "harmful_trajectories": {
                "comparison_id": v4_harmful_primary.iloc[0][
                    "comparison_id"
                ],
                "harmful_count": int(
                    v4_harmful_primary.iloc[0]["harmful_count"]
                ),
                "denominator": int(
                    v4_harmful_primary.iloc[0]["denominator"]
                ),
                "harmful_rate": float(
                    v4_harmful_primary.iloc[0]["harmful_rate"]
                ),
                "wilson_ci_low": float(
                    v4_harmful_primary.iloc[0]["wilson_ci_low"]
                ),
                "wilson_ci_high": float(
                    v4_harmful_primary.iloc[0]["wilson_ci_high"]
                ),
            },
            "rapid_reversal": records(
                v4_rapid_reversal,
                [
                    "trajectory_count",
                    "relative_improvement",
                    "relative_improvement_ci_low",
                    "relative_improvement_ci_high",
                    "harmful_count",
                    "harmful_denominator",
                    "heterogeneity_status",
                ],
            )[0],
            "ordinary_ruckig_context": records(
                v4_ordinary_completion,
                [
                    "method",
                    "attempted_trajectories",
                    "completed_trajectories",
                    "complete_paired_inference_permitted",
                    "status",
                ],
            ),
            "oracle_context": {
                "trajectory_row_count": len(v4_oracle_metrics),
                "information_condition": "offline_analytic_truth",
                "causal": False,
                "deployable": False,
                "diagnostic_only": True,
                "excluded_from_primary": True,
            },
            "artifact_integrity": {
                "artifact_count": v4_artifact_index["artifact_count"],
                "root_index_sha256": sha256(SOURCES["v4_artifact_index"]),
                "root_index_sidecar_sha256": sha256(
                    SOURCES["v4_artifact_index_digest"]
                ),
                "frozen_tree_mutated_by_extraction": False,
            },
        },
        "postfreeze_compatibility": postfreeze_compatibility,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = extract()
    if args.check and OUTPUT.is_file():
        current = json.loads(OUTPUT.read_text(encoding="utf-8"))
        for key in (
            "source_commit",
            "sources",
            "phase_a",
            "v3",
            "v4",
            "postfreeze_compatibility",
        ):
            if current.get(key) != payload.get(key):
                raise SystemExit(f"extracted evidence is stale: {key}")
        print("evidence extraction verified")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
