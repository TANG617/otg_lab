#!/usr/bin/env python3
"""Generate LaTeX result tables from the bounded evidence manifest."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
EVIDENCE = PAPER_ROOT / "generated/manifests/extracted_evidence.json"
OUT = PAPER_ROOT / "generated/tables"
V4_ROOT = REPO_ROOT / "results/paper_evidence_v4"
V4_TABLE_PROVENANCE = (
    PAPER_ROOT / "generated/manifests/v4_table_provenance.json"
)
V4_SOURCES = {
    "evidence_audit": PAPER_ROOT / "logic/evidence_audit.json",
    "protocol": REPO_ROOT / "EXPERIMENT_PROTOCOL_V4.md",
    "split_manifest": REPO_ROOT / "split_manifest_v4.json",
    "config_lock": REPO_ROOT / "config_lock_v4.json",
    "pretest_status": REPO_ROOT / "protocol_status_v4.json",
    "artifact_index": V4_ROOT / "artifact_index.json",
    "primary": V4_ROOT / "statistics/primary_comparison.csv",
    "secondary": V4_ROOT / "statistics/secondary_comparisons.csv",
    "family_effects": V4_ROOT / "statistics/family_effects.csv",
    "worst_five": V4_ROOT / "statistics/worst_five_trajectories.csv",
    "method_identity": V4_ROOT / "statistics/method_identity_summary.csv",
    "same_information": V4_ROOT / "statistics/same_information_audit.csv",
    "ordinary_completion": (
        V4_ROOT / "statistics/ordinary_ruckig_completion.csv"
    ),
    "oracle_pv_vs_p": V4_ROOT / "statistics/oracle_pv_vs_p.csv",
    "oracle_pva_vs_pv": V4_ROOT / "statistics/oracle_pva_vs_pv.csv",
    "oracle_acceleration": (
        V4_ROOT / "statistics/oracle_acceleration_active_effect.csv"
    ),
    "handoff": V4_ROOT / "paper_handoff.json",
    "status": V4_ROOT / "protocol_status_v4.json",
    # Report-only reviewer aid: it diagnoses the already-frozen composite
    # event-flag failures; it is never used to alter the gate classification.
    "same_information_aid": REPO_ROOT / "same_information_failures.csv",
}

DATASET_NAMES = {
    "quadratic_with_extremum": "Quadratic",
    "cubic": "Cubic",
    "sine": "Sine",
}
METHOD_NAMES = {
    "p": "P",
    "pv_truth": "PV truth",
    "pva_truth": "PVA truth",
    "pv_backward": "PV backward",
    "pva_backward": "PVA backward",
    "pv_central_offline": "PV centered offline",
    "pva_central_offline": "PVA centered offline",
    "pv_central_causal": "PV centered causal",
    "pva_central_causal": "PVA centered causal",
}
RAGGED = r">{\raggedright\arraybackslash}"
EXPECTED_CHAIN_HASHES = {
    "protocol": "baad38320593695a4c231f1802faa3a48b4a32b318da841fda5b1354cd8b770e",
    "split_manifest": "1727505734c8026ed18d87123d5d5a8c02e2f201a33ea786fbcde2c9ab398796",
    "config_lock": "d61b0f8596b04358c7bef6a1e43b6775b3dbb00020c2aca28d5d2cd4d9f6f3d3",
    "pretest_status": "c0c3d358c969dbb343ac05dc964075a514f37d8153ce47d6e4ca60a252de4909",
    "status": "48c98a81a76129a0fc2dd913aabb28bc9312d31a76a4283b27bf1fea9431a34b",
    "artifact_index": "fd78eb559d039620ae1c6e06faac44ab6fc8dbff9208c05523b4efcab4a75a95",
}


def tabular(headers: list[str], rows: list[list[str]], spec: str) -> str:
    lines = [
        "% Generated; do not edit.",
        f"\\begin{{tabular}}{{{spec}}}",
        "\\toprule",
        " & ".join(headers) + r" \\",
        "\\midrule",
    ]
    lines.extend(" & ".join(row) + r" \\" for row in rows)
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    return "\n".join(lines)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def escape_latex(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "_": r"\_",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
    }
    return "".join(replacements.get(char, char) for char in value)


def compact_trajectory_id(value: str) -> str:
    return escape_latex(value.replace("__v4__test__", "/"))


def breakable_mono(value: str, width: int = 12) -> str:
    chunks = [value[index : index + width] for index in range(0, len(value), width)]
    return "\\texttt{" + "\\allowbreak{}".join(chunks) + "}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    analytic = data["phase_a"]["analytic_tracking"]
    csv_rows = data["phase_a"]["csv_tracking"]
    deriv = data["phase_a"]["derivatives"]
    v3 = data["v3"]["acceptance_rows"]
    runtime = data["v3"]["direct_runtime_primary"]
    missing = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in V4_SOURCES.values()
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "missing V4 table evidence:\n" + "\n".join(missing)
        )
    for source_key, expected_hash in EXPECTED_CHAIN_HASHES.items():
        actual_hash = digest(V4_SOURCES[source_key])
        if actual_hash != expected_hash:
            raise ValueError(
                f"V4 evidence-chain hash mismatch for {source_key}: "
                f"{actual_hash} != {expected_hash}"
            )
    v4_primary = read_csv(V4_SOURCES["primary"])
    v4_secondary = read_csv(V4_SOURCES["secondary"])
    v4_family_effects = read_csv(V4_SOURCES["family_effects"])
    v4_worst_five = read_csv(V4_SOURCES["worst_five"])
    v4_identity = read_csv(V4_SOURCES["method_identity"])
    v4_same_information = read_csv(V4_SOURCES["same_information"])
    v4_failure_aid = read_csv(V4_SOURCES["same_information_aid"])
    v4_ordinary = read_csv(V4_SOURCES["ordinary_completion"])
    v4_oracle_pv_p = read_csv(V4_SOURCES["oracle_pv_vs_p"])
    v4_oracle_pva_pv = read_csv(V4_SOURCES["oracle_pva_vs_pv"])
    v4_oracle_accel = read_csv(V4_SOURCES["oracle_acceleration"])
    v4_artifact_index = json.loads(
        V4_SOURCES["artifact_index"].read_text(encoding="utf-8")
    )
    v4_commit_chain = json.loads(
        V4_SOURCES["evidence_audit"].read_text(encoding="utf-8")
    )["v4_evidence_registration"]
    v4_preregistration = json.loads(
        V4_SOURCES["pretest_status"].read_text(encoding="utf-8")
    )
    v4_handoff = json.loads(V4_SOURCES["handoff"].read_text(encoding="utf-8"))
    v4_status = json.loads(V4_SOURCES["status"].read_text(encoding="utf-8"))

    primary_direct_methods = {
        "one_step_governed_p_direct",
        "one_step_governed_pv_direct",
        "one_step_governed_pva_direct",
    }
    primary_identity = [
        row for row in v4_identity if row["method"] in primary_direct_methods
    ]
    failed_audit_rows = [
        row for row in v4_same_information if row["audit_passed"] == "False"
    ]
    if len(v4_primary) != 120 or {
        row["paired_trajectory_count"] for row in v4_primary
    } != {"120"}:
        raise ValueError("V4 primary table denominator is not 120/120")
    if {row["total_cycle_count"] for row in primary_identity} != {"42072"}:
        raise ValueError("V4 direct-method cycle denominator is not 42,072")
    if len(primary_identity) != 3 or any(
        float(row["method_purity_rate"]) != 1.0 for row in primary_identity
    ):
        raise ValueError("V4 direct-method purity evidence is not an exact pass")
    if len(v4_same_information) != 42072 or len(failed_audit_rows) != 5:
        raise ValueError("V4 same-information count is not the frozen 5/42072")
    if (
        len(v4_failure_aid) != 5
        or {row["differing_tokens"] for row in v4_failure_aid}
        != {"deadline_miss"}
        or {row["all_non_event_shared_fields_passed"] for row in v4_failure_aid}
        != {"true"}
        or {row["configuration_identity_passed"] for row in v4_failure_aid}
        != {"true"}
    ):
        raise ValueError("same-information reviewer aid is not deadline_miss-only")
    secondary_by_id = {row["comparison_id"]: row for row in v4_secondary}
    if secondary_by_id["S4"]["status"] != "available":
        raise ValueError("missing frozen S4 lag row")
    if v4_handoff["guardrail_status"] != {
        "lag_noninferiority_pass": False,
        "max_error_noninferiority_pass": True,
        "without_degradation_wording_permitted": False,
    }:
        raise ValueError("unexpected V4 guardrail status")
    if v4_handoff["same_information_gate"]["passed"]:
        raise ValueError("V4 same-information gate unexpectedly passed")
    if v4_handoff["runtime_gates"]["passed"]:
        raise ValueError("V4 runtime gate unexpectedly passed")
    if not v4_handoff["safety_gates"]["passed"]:
        raise ValueError("V4 safety gate unexpectedly failed")
    if v4_status["primary_result_classification"] != "invalid_method_identity":
        raise ValueError("unexpected V4 effective result classification")
    if len(v4_family_effects) != 6 or {
        row["stratum_dimension"] for row in v4_family_effects
    } != {"reference_family"}:
        raise ValueError("V4 family-effect table is incomplete")
    if len(v4_worst_five) != 5 or {
        row["harmful"] for row in v4_worst_five
    } != {"True"}:
        raise ValueError("V4 worst-five harmful table is incomplete")
    if v4_preregistration["status"] != "locked_test_unseen":
        raise ValueError("unexpected preregistration status")
    if (
        v4_artifact_index["raw_run_git_commit"]
        != v4_commit_chain["confirmation_source_commit"]
    ):
        raise ValueError("registered confirmation and root-index commits differ")
    if v4_artifact_index["artifact_count"] != 152:
        raise ValueError("unexpected V4 bounded artifact count")
    expected_commit_chain = {
        "confirmation_source_commit": (
            "461fc560461b0a4726cbabdb97b2dbd4dc305e0a"
        ),
        "bounded_result_commit": (
            "f49b4ef1cacf8228c5d243353184acb8a7d02311"
        ),
        "report_only_same_information_aid_commit": (
            "b9301eaf36dc04f1abf662c42821eddfe8c3188a"
        ),
    }
    for key, expected in expected_commit_chain.items():
        if v4_commit_chain[key] != expected:
            raise ValueError(
                f"unexpected V4 commit-chain value for {key}: "
                f"{v4_commit_chain[key]}"
            )
    if len(v4_oracle_pv_p) != 1 or len(v4_oracle_pva_pv) != 1 or len(v4_oracle_accel) != 1:
        raise ValueError("V4 oracle diagnostic rows are incomplete")
    if any(
        row["diagnostic_only"] != "True"
        or row["causal"] != "False"
        or row["deployable"] != "False"
        for row in (v4_oracle_pv_p + v4_oracle_pva_pv + v4_oracle_accel)
    ):
        raise ValueError("V4 oracle boundary is not offline/noncausal diagnostic")

    outputs: dict[str, str] = {}
    outputs["analytic_tracking.tex"] = tabular(
        ["Reference", "Target", "RMSE (rad)", "Max. error (rad)", "Lag (ms)"],
        [
            [
                DATASET_NAMES[row["dataset"]],
                METHOD_NAMES[row["method_id"]],
                f"{row['rmse']:.5f}",
                f"{row['max_error']:.5f}",
                f"{row['best_lag_ms']:.0f}",
            ]
            for row in analytic
        ],
        "llrrr",
    )
    outputs["derivative_accuracy.tex"] = tabular(
        [
            "Reference",
            "Derivative source",
            "Causal",
            "$v$ RMSE",
            "$a$ RMSE",
        ],
        [
            [
                DATASET_NAMES[row["dataset"]],
                {
                    "analytic_truth": "Analytic truth",
                    "backward_fd": "Backward",
                    "centered_fd_offline": "Centered offline",
                    "centered_fd_causal_delay1": "Centered causal",
                }[row["derivative_source"]],
                "yes" if row["causal"] else "no",
                f"{row['velocity_rmse']:.6f}",
                f"{row['acceleration_rmse']:.6f}",
            ]
            for row in deriv
            if row["derivative_source"] != "analytic_truth"
        ],
        "lllrr",
    )
    outputs["csv_tracking.tex"] = tabular(
        ["Target", "Info.", "RMSE (rad)", "Max. error (rad)", "Lag (ms)", "Proj. (\\%)"],
        [
            [
                METHOD_NAMES[row["method_id"]],
                "causal" if row["causal"] else "offline",
                f"{row['rmse']:.5f}",
                f"{row['max_error']:.5f}",
                f"{row['best_lag_ms']:.0f}",
                f"{100 * row['target_projection_rate']:.2f}",
            ]
            for row in csv_rows
        ],
        "llrrrr",
    )
    wanted = {
        "continuous_vaj_violation_count_zero": "Continuous V/A/internal-J violations",
        "projection_rate_zero": "Projected cycles",
        "nonfallback_point_admissibility_100pct": "Point-admissible rate",
        "nonfallback_t_free_le_dt_100pct": "One-step-reachable rate",
        "nonfallback_sequence_consistency_100pct": "Sequence-consistent rate",
    }
    outputs["v3_direct_safety.tex"] = tabular(
        ["Frozen v3 audit quantity", "Observed", "Denominator", "Status"],
        [
            [
                wanted[row["criterion_id"]],
                (
                    f"{row['observed']:.1f}"
                    if abs(row["observed"]) >= 10
                    else f"{row['observed']:.3f}"
                ),
                f"{row['denominator']:.0f}",
                "pass" if row["passed"] else "fail",
            ]
            for row in v3
            if row["criterion_id"] in wanted
        ],
        "lrrl",
    )
    outputs["v3_runtime.tex"] = tabular(
        ["Frozen v3 runtime quantity", "Observed", "Denominator", "Population"],
        [
            [
                "Timed cycles",
                f"{runtime['timed_cycle_count']:.0f}",
                "--",
                "locked-test, after warm-up",
            ],
            [
                "Total compute p99 ($\\mu$s)",
                f"{runtime['runtime_p99_us']:.2f}",
                f"{runtime['timed_cycle_count']:.0f}",
                "locked-test, after warm-up",
            ],
            [
                "Total compute maximum ($\\mu$s)",
                f"{runtime['runtime_max_us']:.2f}",
                f"{runtime['timed_cycle_count']:.0f}",
                "locked-test, after warm-up",
            ],
            [
                "10-ms deadline misses",
                "0",
                f"{runtime['timed_cycle_count']:.0f}",
                "locked-test, after warm-up",
            ],
        ],
        "lrrl",
    )
    outputs["v4_main_results.tex"] = tabular(
        ["Item", "Observation", "Gate/status"],
        [
            [
                "Primary PVA vs. P RMSE",
                (
                    "\\VFourPrimaryRelativeImprovement\\% observed reduction "
                    "(95\\% CI "
                    "[\\VFourPrimaryRelativeCILow\\%, "
                    "\\VFourPrimaryRelativeCIHigh\\%]); absolute "
                    "\\VFourPrimaryAbsoluteImprovement\\,rad"
                ),
                "\\VFourStatisticalClassification",
            ],
            [
                "Paired denominator",
                (
                    "\\VFourPrimaryPairedTrajectoryCount/"
                    "\\VFourTestTrajectoryCount{} trajectories"
                ),
                "pass",
            ],
            [
                "Direct method purity",
                (
                    "\\VFourDirectMethodPurityRate{} for P, PV, and PVA; "
                    "native direct execution"
                ),
                "pass",
            ],
            [
                "Same information",
                (
                    "\\VFourSameInformationFailureCount/"
                    "\\num{\\VFourSameInformationAuditCycleCount}{} composite "
                    "event-flag entries differed"
                ),
                "fail",
            ],
            [
                "Differing token",
                (
                    "\\texttt{deadline\\_miss} only; every non-event field "
                    "and configuration identity passed"
                ),
                "diagnostic; gate unchanged",
            ],
            [
                "Safety/constraints",
                (
                    "\\VFourPrimaryFailureCount{} primary failures; "
                    "\\VFourFallbackEventCount{} fallbacks; "
                    "\\VFourContinuousConstraintViolationCount{} continuous/"
                    "invariant violations"
                ),
                "pass",
            ],
            [
                "Max-error guardrail",
                (
                    "\\VFourMaxErrorRelativeImprovement\\% observed relative "
                    "reduction"
                ),
                "pass",
            ],
            [
                "Lag guardrail",
                (
                    "mean P/PVA lag "
                    "\\VFourPMeanLagMS/\\VFourPVAMeanLagMS\\,ms"
                ),
                "fail: noninferiority not established",
            ],
            [
                "Full Python runtime",
                (
                    "P/PV/PVA p99 "
                    "\\VFourPRuntimePNinetyNineUS/"
                    "\\VFourPVRuntimePNinetyNineUS/"
                    "\\VFourPVARuntimePNinetyNineUS\\,$\\mu$s; maxima "
                    "\\VFourPRuntimeMaxUS/\\VFourPVRuntimeMaxUS/"
                    "\\VFourPVARuntimeMaxUS\\,$\\mu$s; deadline misses "
                    "\\VFourPDeadlineMissCount/\\VFourPVDeadlineMissCount/"
                    "\\VFourPVADeadlineMissCount"
                ),
                "fail",
            ],
            [
                "Effective result",
                "\\VFourEffectiveClassification",
                "non-confirmatory",
            ],
        ],
        f"@{{}}{RAGGED}p{{0.18\\linewidth}}{RAGGED}p{{0.53\\linewidth}}{RAGGED}p{{0.18\\linewidth}}@{{}}",
    )
    outputs["v4_evidence_chain.tex"] = tabular(
        ["Stage", "Frozen action/state", "Commit or SHA-256"],
        [
            [
                "Protocol lock",
                (
                    "exactly-once V4 protocol; base commit "
                    + breakable_mono(v4_preregistration["base_main_commit"])
                ),
                (
                    "\\texttt{EXPERIMENT\\_PROTOCOL\\_V4.md}: "
                    + breakable_mono(digest(V4_SOURCES["protocol"]))
                ),
            ],
            [
                "Fresh split",
                "locked V4 identities/seeds and zero-overlap split",
                (
                    "\\texttt{split\\_manifest\\_v4.json}: "
                    + breakable_mono(digest(V4_SOURCES["split_manifest"]))
                ),
            ],
            [
                "Configuration lock",
                "frozen V4 method and execution configuration",
                (
                    "\\texttt{config\\_lock\\_v4.json}: "
                    + breakable_mono(digest(V4_SOURCES["config_lock"]))
                ),
            ],
            [
                "Pre-test status",
                (
                    "\\texttt{locked\\_test\\_unseen}; test identities unseen; "
                    "same-test rerun prohibited"
                ),
                (
                    "\\texttt{protocol\\_status\\_v4.json}: "
                    + breakable_mono(digest(V4_SOURCES["pretest_status"]))
                ),
            ],
            [
                "Raw confirmation",
                "\\texttt{confirm}; one locked invocation; execution count 1",
                breakable_mono(v4_artifact_index["raw_run_git_commit"]),
            ],
            [
                "Post-test freeze",
                (
                    "\\VFourProtocolStatus; raw resume and same-test rerun "
                    "prohibited"
                ),
                (
                    "status artifact: "
                    + breakable_mono(digest(V4_SOURCES["status"]))
                ),
            ],
            [
                "Reporting repair",
                (
                    "verified reporting-provenance repair; no raw experiment "
                    "rerun"
                ),
                (
                    "reporting: "
                    + breakable_mono(
                        v4_artifact_index["reporting_git_commit"]
                    )
                ),
            ],
            [
                "Bounded result",
                "bounded V4 result and evidence package published",
                (
                    "result: "
                    + breakable_mono(
                        v4_commit_chain["bounded_result_commit"]
                    )
                ),
            ],
            [
                "Diagnostic aid",
                (
                    "report-only same-information diagnosis; frozen gate "
                    "unchanged"
                ),
                (
                    "aid: "
                    + breakable_mono(
                        v4_commit_chain[
                            "report_only_same_information_aid_commit"
                        ]
                    )
                ),
            ],
            [
                "Root of trust",
                (
                    f"{v4_artifact_index['artifact_count']} bounded artifacts; "
                    "all indexed hashes verified"
                ),
                (
                    "\\texttt{artifact\\_index.json}: "
                    + breakable_mono(digest(V4_SOURCES["artifact_index"]))
                ),
            ],
        ],
        f"@{{}}{RAGGED}p{{0.14\\linewidth}}{RAGGED}p{{0.34\\linewidth}}{RAGGED}p{{0.42\\linewidth}}@{{}}",
    )
    outputs["v4_complete_gates.tex"] = tabular(
        ["Gate or classification", "Frozen observation", "Status"],
        [
            [
                "Fresh/exactly-once execution",
                (
                    "one locked confirmation invocation followed by immutable "
                    "report-only handoff"
                ),
                "retained",
            ],
            [
                "Primary cycle/sample denominator",
                (
                    "\\num{\\VFourPrimarySampleCountPerMethod} aligned cycles "
                    "per direct method"
                ),
                "pass",
            ],
            [
                "Paired trajectory denominator",
                (
                    "\\VFourPrimaryPairedTrajectoryCount/"
                    "\\VFourTestTrajectoryCount{} trajectories"
                ),
                "pass",
            ],
            [
                "Observed primary effect",
                (
                    "\\VFourPrimaryRelativeImprovement\\% relative RMSE "
                    "reduction (95\\% CI "
                    "[\\VFourPrimaryRelativeCILow\\%, "
                    "\\VFourPrimaryRelativeCIHigh\\%])"
                ),
                "\\VFourStatisticalClassification",
            ],
            [
                "Direct method purity",
                "\\VFourDirectMethodPurityRate{} for P, PV, and PVA",
                "pass",
            ],
            [
                "Same information",
                (
                    "\\VFourSameInformationFailureCount/"
                    "\\num{\\VFourSameInformationAuditCycleCount} composite "
                    "event-flag differences"
                ),
                "fail",
            ],
            [
                "Safety/completion",
                (
                    "\\VFourPrimaryFailureCount{} failures; "
                    "\\VFourFallbackEventCount{} fallbacks; "
                    "\\VFourContinuousConstraintViolationCount{} violations"
                ),
                "pass",
            ],
            [
                "Maximum-error guardrail",
                (
                    "\\VFourMaxErrorRelativeImprovement\\% observed relative "
                    "reduction"
                ),
                "pass",
            ],
            [
                "Lag noninferiority",
                (
                    "P/PVA mean lag "
                    "\\VFourPMeanLagMS/\\VFourPVAMeanLagMS\\,ms"
                ),
                "fail: not established",
            ],
            [
                "Hard runtime",
                (
                    "full instrumented Python pipeline; every direct method "
                    "missed at least one frozen threshold"
                ),
                "fail",
            ],
            [
                "Effective result",
                "\\VFourEffectiveClassification",
                "non-confirmatory",
            ],
        ],
        f"@{{}}{RAGGED}p{{0.22\\linewidth}}{RAGGED}p{{0.45\\linewidth}}{RAGGED}p{{0.17\\linewidth}}@{{}}",
    )

    def event_flag(value: str) -> str:
        return "--" if not value else "yes"

    outputs["v4_same_information_failures.tex"] = tabular(
        ["Trajectory", "$k$", "P DM", "PV DM", "PVA DM", "Differing token"],
        [
            [
                "\\texttt{" + compact_trajectory_id(row["trajectory_id"]) + "}",
                row["k"],
                event_flag(row["p_event_flags"]),
                event_flag(row["pv_event_flags"]),
                event_flag(row["pva_event_flags"]),
                "\\texttt{" + escape_latex(row["differing_tokens"]) + "}",
            ]
            for row in v4_failure_aid
        ],
        (
            f"@{{}}{RAGGED}p{{0.31\\linewidth}}r"
            f"{RAGGED}p{{0.07\\linewidth}}{RAGGED}p{{0.07\\linewidth}}"
            f"{RAGGED}p{{0.07\\linewidth}}{RAGGED}p{{0.18\\linewidth}}@{{}}"
        ),
    )

    family_labels = {
        "boundary_grazing": "Boundary grazing",
        "oscillatory": "Oscillatory",
        "piecewise_constant_jerk": "Piecewise-constant jerk",
        "rapid_reversal": "Rapid reversal",
        "stationary_endpoint": "Stationary endpoint",
        "stop_and_go": "Stop and go",
    }
    family_rows = [
        [
            "Family",
            family_labels[row["stratum_value"]],
            row["trajectory_count"],
            (
                f"{100 * float(row['relative_improvement']):.2f}\\% "
                f"[{100 * float(row['relative_improvement_ci_low']):.2f}, "
                f"{100 * float(row['relative_improvement_ci_high']):.2f}]"
            ),
            f"{row['harmful_count']}/{row['harmful_denominator']}",
        ]
        for row in v4_family_effects
    ]
    worst_rows = [
        [
            "Worst",
            "\\texttt{" + compact_trajectory_id(row["trajectory_id"]) + "}",
            "\\#" + row["rank"],
            (
                "$\\Delta$RMSE = "
                f"{float(row['candidate_minus_baseline_position_rmse']):+.4f}"
                "\\,rad"
            ),
            "yes",
        ]
        for row in v4_worst_five
    ]
    outputs["v4_family_effects.tex"] = tabular(
        ["Evidence", "Family/trajectory", "$n$/rank", "Observed effect [95\\% CI]", "Harmful"],
        family_rows + worst_rows,
        (
            f"@{{}}{RAGGED}p{{0.12\\linewidth}}{RAGGED}p{{0.28\\linewidth}}"
            f"{RAGGED}p{{0.08\\linewidth}}{RAGGED}p{{0.26\\linewidth}}"
            f"{RAGGED}p{{0.08\\linewidth}}@{{}}"
        ),
    )

    ordinary_by_method = {row["method"]: row for row in v4_ordinary}
    ordinary_pv = ordinary_by_method["raw_predicted_pv_ordinary_ruckig"]
    ordinary_pva = ordinary_by_method["raw_predicted_pva_ordinary_ruckig"]
    oracle_rows = [
        ("Oracle PV vs. P", v4_oracle_pv_p[0]),
        ("Oracle PVA vs. PV", v4_oracle_pva_pv[0]),
        ("Oracle PVA vs. PV, acceleration-active", v4_oracle_accel[0]),
    ]
    outputs["v4_runtime_context.tex"] = tabular(
        ["Evidence", "Denominator", "Observed", "Status/boundary"],
        [
            [
                "Runtime P",
                "\\num{\\VFourRuntimeCycleCountPerMethod}",
                (
                    "p99 \\VFourPRuntimePNinetyNineUS\\,$\\mu$s; max "
                    "\\VFourPRuntimeMaxUS\\,$\\mu$s; "
                    "\\VFourPDeadlineMissCount{} misses"
                ),
                "fail",
            ],
            [
                "Runtime PV",
                "\\num{\\VFourRuntimeCycleCountPerMethod}",
                (
                    "p99 \\VFourPVRuntimePNinetyNineUS\\,$\\mu$s; max "
                    "\\VFourPVRuntimeMaxUS\\,$\\mu$s; "
                    "\\VFourPVDeadlineMissCount{} misses"
                ),
                "fail",
            ],
            [
                "Runtime PVA",
                "\\num{\\VFourRuntimeCycleCountPerMethod}",
                (
                    "p99 \\VFourPVARuntimePNinetyNineUS\\,$\\mu$s; max "
                    "\\VFourPVARuntimeMaxUS\\,$\\mu$s; "
                    "\\VFourPVADeadlineMissCount{} misses"
                ),
                "fail",
            ],
            [
                "Ordinary Ruckig, raw PV",
                (
                    ordinary_pv["completed_trajectories"]
                    + "/"
                    + ordinary_pv["attempted_trajectories"]
                ),
                "incomplete completion",
                "contextual only; no paired inference",
            ],
            [
                "Ordinary Ruckig, raw PVA",
                (
                    ordinary_pva["completed_trajectories"]
                    + "/"
                    + ordinary_pva["attempted_trajectories"]
                ),
                "incomplete completion",
                "contextual only; no paired inference",
            ],
        ]
        + [
            [
                label,
                row["n_trajectories"] + "/" + row["required_trajectories"],
                f"{100 * float(row['relative_improvement']):.2f}\\% observed",
                "offline noncausal diagnostic; nondeployable",
            ]
            for label, row in oracle_rows
        ],
        (
            f"@{{}}{RAGGED}p{{0.22\\linewidth}}{RAGGED}p{{0.12\\linewidth}}"
            f"{RAGGED}p{{0.26\\linewidth}}{RAGGED}p{{0.22\\linewidth}}@{{}}"
        ),
    )

    table_provenance = {
        "schema_version": "otg.paper-v4-table-provenance.v2",
        "asset_path": "paper/generated/tables/v4_main_results.tex",
        "assets": {
            "paper/generated/tables/v4_main_results.tex": {
                "role": "main-text V4 effect-and-gates table",
                "rounding": "numeric cells use generated V4 macros",
            },
            "paper/generated/tables/v4_evidence_chain.tex": {
                "role": "Appendix F protocol/commit/hash and exactly-once timeline",
                "rounding": "timestamps, commits, and hashes are exact strings",
            },
            "paper/generated/tables/v4_complete_gates.tex": {
                "role": "Appendix F complete frozen gate table",
                "rounding": "numeric cells use generated V4 macros",
            },
            "paper/generated/tables/v4_same_information_failures.tex": {
                "role": "Appendix F five-row same-information diagnosis",
                "rounding": "cycle indices and tokens are exact; no rounding",
            },
            "paper/generated/tables/v4_family_effects.tex": {
                "role": "Appendix F family effects and retained worst five",
                "rounding": (
                    "family proportions multiplied by 100 and fixed to 2 "
                    "decimals; trajectory RMSE differences fixed to 4 decimals"
                ),
            },
            "paper/generated/tables/v4_runtime_context.tex": {
                "role": (
                    "Appendix F runtime, incomplete ordinary-Ruckig, and "
                    "offline oracle context"
                ),
                "rounding": (
                    "runtime values use generated macros; oracle proportions "
                    "multiplied by 100 and fixed to 2 decimals"
                ),
            },
        },
        "caption": (
            "Frozen V4 observed effect and all preregistered gates. The fresh "
            "synthetic locked test is non-confirmatory because the "
            "same-information validity gate and hard-runtime gate failed."
        ),
        "interpretation_boundary": (
            "The large observed effect is retained but does not establish a "
            "confirmatory PVA performance benefit. The deadline_miss-only "
            "diagnosis does not change the frozen failed gate."
        ),
        "numeric_cell_policy": (
            "Main/gate/runtime protected values use macros recorded in "
            "number_provenance.json. Appendix row-wise values use the exact "
            "source selectors and rounding rules recorded per asset here."
        ),
        "sources": {
            key: {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": digest(path),
                "bytes": path.stat().st_size,
            }
            for key, path in V4_SOURCES.items()
        },
        "selectors": {
            "primary_effect_and_denominator": {
                "source": "primary",
                "row_selector": "all 120 rows; overall fields asserted constant",
                "field_selectors": [
                    "overall_relative_improvement",
                    "overall_relative_improvement_ci_low",
                    "overall_relative_improvement_ci_high",
                    "overall_absolute_improvement",
                    "paired_trajectory_count",
                    "required_trajectory_count",
                ],
            },
            "method_purity": {
                "source": "method_identity",
                "row_selector": (
                    "method in {one_step_governed_p_direct, "
                    "one_step_governed_pv_direct, "
                    "one_step_governed_pva_direct}"
                ),
                "field_selectors": [
                    "method_purity_rate",
                    "native_execution_rate",
                    "total_cycle_count",
                ],
            },
            "same_information": {
                "source": "same_information",
                "row_selector": "audit_passed == False over all 42,072 rows",
                "field_selectors": ["audit_passed", "failed_fields"],
            },
            "same_information_diagnosis": {
                "source": "same_information_aid",
                "row_selector": "all five report-only diagnostic rows",
                "field_selectors": [
                    "differing_tokens",
                    "all_non_event_shared_fields_passed",
                    "configuration_identity_passed",
                ],
            },
            "lag": {
                "source": "secondary",
                "row_selector": "comparison_id == S4",
                "field_selectors": ["baseline_mean", "candidate_mean", "status"],
            },
            "gates_and_runtime": {
                "source": "handoff",
                "row_selector": (
                    "guardrail_status, safety_gates, same_information_gate, "
                    "and runtime_gates.methods"
                ),
                "field_selectors": [
                    "passed",
                    "total_p99_us",
                    "total_max_us",
                    "deadline_miss_count",
                ],
            },
            "effective_classification": {
                "source": "status",
                "row_selector": "root object",
                "field_selectors": [
                    "status",
                    "statistical_classification",
                    "primary_result_classification",
                ],
            },
            "protocol_timeline": {
                "sources": [
                    "evidence_audit",
                    "protocol",
                    "split_manifest",
                    "config_lock",
                    "pretest_status",
                    "locked_run_metadata",
                    "status",
                    "artifact_index",
                ],
                "row_selector": "root objects; no rows excluded",
                "field_selectors": [
                    "base_main_commit",
                    "protocol SHA-256",
                    "split-manifest SHA-256",
                    "config-lock SHA-256",
                    "pretest-status SHA-256",
                    "status",
                    "started_at",
                    "command",
                    "git_commit",
                    "raw_run_git_commit",
                    "reporting_git_commit",
                    "bounded_result_commit",
                    "report_only_same_information_aid_commit",
                    "artifact_count",
                ],
                "rounding_rule": "exact strings and exact integer artifact count",
            },
            "family_effects": {
                "source": "family_effects",
                "row_selector": "all six reference_family rows",
                "field_selectors": [
                    "stratum_value",
                    "trajectory_count",
                    "relative_improvement",
                    "relative_improvement_ci_low",
                    "relative_improvement_ci_high",
                    "harmful_count",
                    "harmful_denominator",
                ],
                "rounding_rule": (
                    "proportions multiplied by 100 and fixed-point to 2 "
                    "decimal places (round-half-even)"
                ),
            },
            "worst_five": {
                "source": "worst_five",
                "row_selector": "all five ranked rows",
                "field_selectors": [
                    "rank",
                    "trajectory_id",
                    "candidate_minus_baseline_position_rmse",
                    "harmful",
                ],
                "rounding_rule": (
                    "rank exact; RMSE difference fixed-point to 4 decimal "
                    "places (round-half-even)"
                ),
            },
            "ordinary_incomplete_context": {
                "source": "ordinary_completion",
                "row_selector": (
                    "method in {raw_predicted_pv_ordinary_ruckig, "
                    "raw_predicted_pva_ordinary_ruckig}"
                ),
                "field_selectors": [
                    "attempted_trajectories",
                    "completed_trajectories",
                    "complete_paired_inference_permitted",
                    "status",
                ],
                "rounding_rule": "exact integer counts; no rounding",
            },
            "oracle_context": {
                "sources": [
                    "oracle_pv_vs_p",
                    "oracle_pva_vs_pv",
                    "oracle_acceleration",
                ],
                "row_selector": "the sole row in each bounded oracle CSV",
                "field_selectors": [
                    "n_trajectories",
                    "required_trajectories",
                    "relative_improvement",
                    "information_condition",
                    "causal",
                    "deployable",
                    "diagnostic_only",
                ],
                "rounding_rule": (
                    "counts exact; proportions multiplied by 100 and "
                    "fixed-point to 2 decimal places (round-half-even)"
                ),
            },
        },
    }
    rendered_table_provenance = (
        json.dumps(table_provenance, indent=2, sort_keys=True) + "\n"
    )

    OUT.mkdir(parents=True, exist_ok=True)
    if args.check:
        stale = [
            name
            for name, content in outputs.items()
            if not (OUT / name).is_file()
            or (OUT / name).read_text(encoding="utf-8") != content
        ]
        if stale:
            raise SystemExit("stale generated tables: " + ", ".join(stale))
        if (
            not V4_TABLE_PROVENANCE.is_file()
            or V4_TABLE_PROVENANCE.read_text(encoding="utf-8")
            != rendered_table_provenance
        ):
            raise SystemExit("stale V4 table provenance")
        print("table generation verified")
        return 0
    for name, content in outputs.items():
        (OUT / name).write_text(content, encoding="utf-8")
    V4_TABLE_PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    V4_TABLE_PROVENANCE.write_text(
        rendered_table_provenance, encoding="utf-8"
    )
    print(f"wrote {len(outputs)} tables under {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
