#!/usr/bin/env python3
"""Generate LaTeX result macros and machine-readable provenance."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
EVIDENCE = PAPER_ROOT / "generated/manifests/extracted_evidence.json"
OUTPUT = PAPER_ROOT / "generated/numbers.tex"
PROVENANCE = PAPER_ROOT / "generated/manifests/number_provenance.json"
V4_ROOT = REPO_ROOT / "results/paper_evidence_v4"
V4_SOURCES = {
    "v4_primary": V4_ROOT / "statistics/primary_comparison.csv",
    "v4_secondary": V4_ROOT / "statistics/secondary_comparisons.csv",
    "v4_family": V4_ROOT / "statistics/family_effects.csv",
    "v4_harmful": V4_ROOT / "statistics/harmful_trajectory_rate.csv",
    "v4_method_identity": V4_ROOT / "statistics/method_identity_summary.csv",
    "v4_same_information": V4_ROOT / "statistics/same_information_audit.csv",
    "v4_handoff": V4_ROOT / "paper_handoff.json",
    "v4_status": V4_ROOT / "protocol_status_v4.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generation_script_commit() -> str:
    return subprocess.check_output(
        [
            "git",
            "log",
            "-1",
            "--format=%H",
            "--",
            "paper/scripts/generate_numbers.py",
        ],
        cwd=REPO_ROOT,
        text=True,
    ).strip()


def by_key(rows: list[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
    matches = [row for row in rows if row[field] == value]
    if len(matches) != 1:
        raise ValueError(f"expected one {field}={value!r}, got {len(matches)}")
    return matches[0]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def csv_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"expected CSV boolean, got {value!r}")
    return normalized == "true"


def assert_constant(
    rows: list[dict[str, str]], field: str, *, expected: Any | None = None
) -> str:
    values = {row[field] for row in rows}
    if len(values) != 1:
        raise ValueError(f"expected constant {field}, got {sorted(values)!r}")
    value = next(iter(values))
    if expected is not None and value != str(expected):
        raise ValueError(f"expected {field}={expected!r}, got {value!r}")
    return value


def latex_identifier(value: str) -> str:
    return value.replace("_", r"\_")


def fmt_fixed(digits: int) -> Callable[[float], str]:
    return lambda value: f"{value:.{digits}f}"


def inferred_rounding_rule(formatted: str) -> str:
    if "e" in formatted.lower():
        coefficient = formatted.lower().split("e", maxsplit=1)[0]
        digits = len(coefficient.partition(".")[2])
        return (
            f"scientific notation with {digits} digits after the decimal point "
            "via Python formatting (round-half-even)"
        )
    if formatted.replace("-", "").isdigit():
        return "fixed-point with 0 decimal places via Python formatting (round-half-even)"
    if formatted.replace("-", "").replace(".", "", 1).isdigit():
        digits = len(formatted.partition(".")[2])
        return (
            f"fixed-point with {digits} decimal places via Python formatting "
            "(round-half-even)"
        )
    return "exact LaTeX rendering; no numeric rounding"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    tracking = data["phase_a"]["analytic_tracking"]
    derivatives = data["phase_a"]["derivatives"]
    csv_rows = data["phase_a"]["csv_tracking"]
    oracle = data["phase_a"]["oracle"]
    limit_rows = data["phase_a"]["limit_sensitivity"]
    v3_rows = data["v3"]["acceptance_rows"]
    v3_runtime = data["v3"]["direct_runtime_primary"]
    postreview = data["v3"]["postreview"]
    missing_v4 = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in V4_SOURCES.values()
        if not path.is_file()
    ]
    if missing_v4:
        raise FileNotFoundError(
            "missing bounded V4 paper evidence:\n" + "\n".join(missing_v4)
        )
    v4_primary = read_csv(V4_SOURCES["v4_primary"])
    v4_secondary = read_csv(V4_SOURCES["v4_secondary"])
    v4_family = read_csv(V4_SOURCES["v4_family"])
    v4_harmful = read_csv(V4_SOURCES["v4_harmful"])
    v4_identity = read_csv(V4_SOURCES["v4_method_identity"])
    v4_same_information = read_csv(V4_SOURCES["v4_same_information"])
    v4_handoff = json.loads(
        V4_SOURCES["v4_handoff"].read_text(encoding="utf-8")
    )
    v4_status = json.loads(V4_SOURCES["v4_status"].read_text(encoding="utf-8"))

    if len(v4_primary) != 120:
        raise ValueError(f"expected 120 V4 primary rows, got {len(v4_primary)}")
    assert_constant(v4_primary, "required_trajectory_count", expected=120)
    assert_constant(v4_primary, "paired_trajectory_count", expected=120)
    assert_constant(v4_primary, "bootstrap_resamples", expected=10000)
    assert_constant(
        v4_primary, "primary_result_classification", expected="strongly_material"
    )
    assert_constant(v4_primary, "lag_guardrail_pass", expected="False")
    assert_constant(v4_primary, "max_error_guardrail_pass", expected="True")
    primary_row = v4_primary[0]
    secondary_by_id = {row["comparison_id"]: row for row in v4_secondary}
    if not {"S1", "S2", "S3", "S4", "S5"}.issubset(secondary_by_id):
        raise ValueError("bounded V4 secondary comparison rows are incomplete")
    lag_row = secondary_by_id["S4"]
    max_error_row = secondary_by_id["S3"]
    harmful_row = by_key(
        v4_harmful,
        "comparison_id",
        "PVA_vs_P_position_RMSE",
    )
    rapid_reversal_row = by_key(v4_family, "stratum_value", "rapid_reversal")
    primary_method_ids = {
        "one_step_governed_p_direct",
        "one_step_governed_pv_direct",
        "one_step_governed_pva_direct",
    }
    primary_identity_rows = [
        row for row in v4_identity if row["method"] in primary_method_ids
    ]
    if len(primary_identity_rows) != 3 or any(
        float(row["method_purity_rate"]) != 1.0 for row in primary_identity_rows
    ):
        raise ValueError("V4 primary direct-method purity is not exactly 1.0")
    same_information_failures = [
        row
        for row in v4_same_information
        if not csv_bool(row["audit_passed"])
    ]
    if len(v4_same_information) != 42072 or len(same_information_failures) != 5:
        raise ValueError(
            "unexpected V4 same-information audit denominator/failure count"
        )
    if any(
        not row["failed_fields"].endswith(":event_flags")
        for row in same_information_failures
    ):
        raise ValueError("V4 same-information failure is not event_flags-only")
    runtime_by_method = {
        row["method"]: row for row in v4_handoff["runtime_gates"]["methods"]
    }
    if set(runtime_by_method) != primary_method_ids:
        raise ValueError("unexpected V4 runtime method set")
    if v4_handoff["runtime_gates"]["passed"]:
        raise ValueError("V4 hard-runtime gate unexpectedly passed")
    if v4_handoff["same_information_gate"]["passed"]:
        raise ValueError("V4 same-information gate unexpectedly passed")
    if not v4_handoff["method_identity_gate"]["passed"]:
        raise ValueError("V4 direct-method identity gate unexpectedly failed")
    if not v4_handoff["safety_gates"]["passed"]:
        raise ValueError("V4 safety gate unexpectedly failed")
    if v4_status["status"] != "failed_test_visible_frozen":
        raise ValueError("unexpected V4 protocol status")
    if v4_status["primary_result_classification"] != "invalid_method_identity":
        raise ValueError("unexpected V4 effective classification")
    if v4_status["same_test_rerun_permitted"]:
        raise ValueError("V4 same-test rerun must remain prohibited")

    improvements: list[float] = []
    truth_deltas: list[float] = []
    p_lags: list[float] = []
    pv_lags: list[float] = []
    for dataset in ("quadratic_with_extremum", "cubic", "sine"):
        rows = [row for row in tracking if row["dataset"] == dataset]
        p = by_key(rows, "method_id", "p")
        pv = by_key(rows, "method_id", "pv_truth")
        pva = by_key(rows, "method_id", "pva_truth")
        improvements.append(1.0 - pv["rmse"] / p["rmse"])
        truth_deltas.append(abs(pv["rmse"] - pva["rmse"]))
        p_lags.append(p["best_lag_ms"])
        pv_lags.append(pv["best_lag_ms"])

    velocity_ratios: list[float] = []
    acceleration_ratios: list[float] = []
    for dataset in ("quadratic_with_extremum", "cubic", "sine"):
        rows = [row for row in derivatives if row["dataset"] == dataset]
        bw = by_key(rows, "derivative_source", "backward_fd")
        centered = by_key(rows, "derivative_source", "centered_fd_offline")
        velocity_ratios.append(bw["velocity_rmse"] / centered["velocity_rmse"])
        acceleration_ratios.append(
            bw["acceleration_rmse"] / centered["acceleration_rmse"]
        )

    csv_p = by_key(csv_rows, "method_id", "p")
    csv_diff = [row for row in csv_rows if row["method_id"] != "p"]
    csv_pva = [row for row in csv_rows if row["method_id"].startswith("pva_")]
    v3_by_criterion = {row["criterion_id"]: row for row in v3_rows}
    frozen = postreview["frozen_observations"]
    compat = data["postfreeze_compatibility"]["phase_a_p_only_ordinary_ruckig"][
        "metrics"
    ]
    primary = frozen["primary_comparison"]
    confounded_baseline = frozen["ordinary_ruckig_named_fallback_rates"][
        primary["baseline_method"]
    ]
    analytic_eval = next(
        row
        for row in limit_rows
        if row["dataset"] == "quadratic_with_extremum"
        and row["method_id"] == "p"
    )
    csv_p_jerk = {
        float(row["sweep_value"]): row
        for row in limit_rows
        if row["dataset"] == "csv"
        and row["method_id"] == "p"
        and row["sweep_type"] == "jerk"
    }
    low_jerk = csv_p_jerk[41.0]
    nominal_jerk = csv_p_jerk[4000.0]
    high_jerk = csv_p_jerk[8000.0]
    margin_tolerance = v3_by_criterion[
        "continuous_velocity_margin_nonnegative"
    ]["threshold"]

    values: list[tuple[str, Any, str, str, str]] = [
        ("ControlPeriodMS", 10.0, fmt_fixed(0), "ms", "protocol constant"),
        ("VelocityLimit", 4.1, fmt_fixed(1), "rad/s", "protocol constant"),
        ("AccelerationLimit", 8.2, fmt_fixed(1), "rad/s^2", "protocol constant"),
        ("JerkLimit", 4000.0, fmt_fixed(0), "rad/s^3", "protocol constant"),
        (
            "PhaseACommonWarmupSamples",
            data["phase_a"]["protocol"]["common_warmup_samples"],
            fmt_fixed(0),
            "samples",
            "Phase A run manifest",
        ),
        (
            "PhaseAEvaluationStartIndex",
            analytic_eval["evaluation_start_index"],
            fmt_fixed(0),
            "index",
            "Phase A limit-sensitivity evaluation selector",
        ),
        (
            "PhaseAEvaluationStopIndex",
            analytic_eval["evaluation_stop_index_exclusive"],
            fmt_fixed(0),
            "index",
            "Phase A limit-sensitivity evaluation selector",
        ),
        (
            "CSVRowCount",
            data["phase_a"]["protocol"]["input_csv_rows"],
            fmt_fixed(0),
            "rows",
            "Phase A run-manifest CSV row count",
        ),
        (
            "AnalyticVelocityImprovementMin",
            100 * min(improvements),
            fmt_fixed(2),
            "percent",
            "computed from Phase A analytic P and PV RMSE",
        ),
        (
            "AnalyticVelocityImprovementMax",
            100 * max(improvements),
            fmt_fixed(2),
            "percent",
            "computed from Phase A analytic P and PV RMSE",
        ),
        (
            "AnalyticPOnlyLagMinMS",
            min(p_lags),
            fmt_fixed(0),
            "ms",
            "Phase A P-only lag range",
        ),
        (
            "AnalyticPOnlyLagMaxMS",
            max(p_lags),
            fmt_fixed(0),
            "ms",
            "Phase A P-only lag range",
        ),
        (
            "AnalyticPVLagMS",
            max(pv_lags),
            fmt_fixed(0),
            "ms",
            "Phase A PV truth lag (same on all references)",
        ),
        (
            "AnalyticPVPVAMaxRMSEDifference",
            max(truth_deltas),
            lambda x: f"{x:.2e}",
            "rad",
            "maximum absolute PV/PVA truth RMSE difference",
        ),
        (
            "CenteredVelocityAccuracyFactorMin",
            min(velocity_ratios),
            fmt_fixed(1),
            "ratio",
            "backward/centered velocity RMSE",
        ),
        (
            "CenteredVelocityAccuracyFactorMax",
            max(velocity_ratios),
            fmt_fixed(1),
            "ratio",
            "backward/centered velocity RMSE",
        ),
        (
            "CenteredAccelerationAccuracyFactorMin",
            min(acceleration_ratios),
            fmt_fixed(1),
            "ratio",
            "backward/centered acceleration RMSE",
        ),
        (
            "CenteredAccelerationAccuracyFactorMax",
            max(acceleration_ratios),
            fmt_fixed(1),
            "ratio",
            "backward/centered acceleration RMSE",
        ),
        ("CSVPOnlyRMSE", csv_p["rmse"], fmt_fixed(5), "rad", "Phase A CSV P row"),
        (
            "CSVPOnlyLagMS",
            csv_p["best_lag_ms"],
            fmt_fixed(0),
            "ms",
            "Phase A CSV P row",
        ),
        (
            "CSVPOnlyMaxError",
            csv_p["max_error"],
            fmt_fixed(6),
            "rad",
            "Phase A CSV P row",
        ),
        (
            "CSVDifferenceRMSEMin",
            min(row["rmse"] for row in csv_diff),
            fmt_fixed(5),
            "rad",
            "best raw finite-difference row",
        ),
        (
            "CSVDifferenceRMSEMax",
            max(row["rmse"] for row in csv_diff),
            fmt_fixed(5),
            "rad",
            "worst raw finite-difference row",
        ),
        (
            "CSVDifferenceLagMinMS",
            min(row["best_lag_ms"] for row in csv_diff),
            fmt_fixed(0),
            "ms",
            "finite-difference lag range",
        ),
        (
            "CSVDifferenceLagMaxMS",
            max(row["best_lag_ms"] for row in csv_diff),
            fmt_fixed(0),
            "ms",
            "finite-difference lag range",
        ),
        (
            "CSVProjectionRate",
            100 * max(row["target_projection_rate"] for row in csv_pva),
            fmt_fixed(2),
            "percent",
            "raw PVA target projection rate",
        ),
        (
            "CSVRawAccelerationPeak",
            max(row["raw_target_max_acceleration"] for row in csv_pva),
            fmt_fixed(2),
            "rad/s^2",
            "raw differentiated target acceleration",
        ),
        (
            "CSVAccelerationLimitMultiple",
            max(row["raw_target_max_acceleration"] for row in csv_pva) / 8.2,
            fmt_fixed(1),
            "ratio",
            "raw target acceleration divided by configured limit",
        ),
        (
            "OracleWorstRMSE",
            max(row["rmse"] for row in oracle),
            lambda x: f"{x:.2e}",
            "rad",
            "maximum next-cycle oracle RMSE",
        ),
        (
            "OracleLagMS",
            max(row["best_lag_ms"] for row in oracle),
            fmt_fixed(0),
            "ms",
            "next-cycle oracle lag",
        ),
        (
            "VThreeDirectCycleCount",
            frozen["direct_governor_locked_cycles"],
            fmt_fixed(0),
            "cycles",
            "frozen v3 post-review status",
        ),
        (
            "VThreeDirectTransitionCount",
            v3_by_criterion["nonfallback_sequence_consistency_100pct"][
                "denominator"
            ],
            fmt_fixed(0),
            "transitions",
            "frozen v3 adjacent-transition denominator",
        ),
        (
            "VThreeTrajectoryCount",
            data["v3"]["locked_test_trajectory_count"],
            fmt_fixed(0),
            "trajectories",
            "frozen v3 locked-test trajectory count",
        ),
        (
            "VThreeAuditTolerance",
            margin_tolerance,
            lambda x: r"-10^{-8}",
            "physical-unit margin",
            "frozen v3 continuous-constraint acceptance threshold",
        ),
        (
            "VThreeDirectViolationCount",
            frozen["direct_governor_continuous_constraint_violations"],
            fmt_fixed(0),
            "count",
            "frozen v3 post-review status",
        ),
        (
            "VThreeDirectFallbackCount",
            frozen["direct_governor_fallback_cycles"],
            fmt_fixed(0),
            "count",
            "frozen v3 post-review status",
        ),
        (
            "VThreeDirectProjectionCount",
            0,
            fmt_fixed(0),
            "count",
            "frozen v3 acceptance projection rate and denominator",
        ),
        (
            "VThreeRuntimePNinetyNineUS",
            v3_runtime["runtime_p99_us"],
            fmt_fixed(1),
            "us",
            "frozen v3 primary runtime benchmark after 100-cycle warm-up",
        ),
        (
            "VThreeRuntimeMaxUS",
            v3_runtime["runtime_max_us"],
            fmt_fixed(1),
            "us",
            "frozen v3 primary runtime benchmark after 100-cycle warm-up",
        ),
        (
            "VThreeRuntimeCycleCount",
            v3_runtime["timed_cycle_count"],
            fmt_fixed(0),
            "cycles",
            "frozen v3 primary runtime benchmark denominator",
        ),
        (
            "VThreeRuntimeWarmupSamples",
            v3_runtime["warmup_samples_per_trajectory"],
            fmt_fixed(0),
            "samples per trajectory",
            "frozen v3 primary runtime benchmark",
        ),
        (
            "VThreeRawBundleCount",
            data["v3"]["raw_bundle_count"],
            fmt_fixed(0),
            "bundles",
            "frozen v3 protocol status",
        ),
        (
            "VThreeBoundedArtifactCount",
            data["v3"]["bounded_artifact_count"],
            fmt_fixed(0),
            "artifacts",
            "frozen v3 protocol status",
        ),
        (
            "VThreeRequiredCriterionCount",
            data["v3"]["required_component_criteria"],
            fmt_fixed(0),
            "criteria",
            "frozen v3 protocol status",
        ),
        (
            "VThreeRequiredCriterionPassCount",
            data["v3"]["required_component_pass_count"],
            fmt_fixed(0),
            "criteria",
            "frozen v3 protocol status",
        ),
        (
            "VThreeRequiredCriterionFailureCount",
            data["v3"]["required_component_failure_count"],
            fmt_fixed(0),
            "criteria",
            "frozen v3 protocol status",
        ),
        (
            "VThreeDeadlineMissRate",
            100 * v3_runtime["runtime_deadline_miss_rate"],
            fmt_fixed(1),
            "percent",
            "frozen v3 primary runtime benchmark",
        ),
        (
            "VThreeExploratoryConfoundedImprovement",
            100 * primary["observed_relative_improvement"],
            fmt_fixed(2),
            "percent",
            "exploratory mixed/confounded frozen comparison",
        ),
        (
            "VThreeConfoundedBaselineFallbackCount",
            confounded_baseline["fallback_cycles"],
            fmt_fixed(0),
            "cycles",
            "exploratory mixed/confounded frozen baseline fallback count",
        ),
        (
            "VThreeConfoundedBaselineCycleCount",
            confounded_baseline["total_cycles"],
            fmt_fixed(0),
            "cycles",
            "exploratory mixed/confounded frozen baseline cycle denominator",
        ),
        (
            "VThreeConfoundedBaselineFallbackRate",
            100 * confounded_baseline["fallback_rate"],
            fmt_fixed(4),
            "percent",
            "exploratory mixed/confounded frozen baseline fallback rate",
        ),
        (
            "VThreeExploratoryCILow",
            100 * primary["confidence_interval_95"][0],
            fmt_fixed(2),
            "percent",
            "exploratory mixed/confounded frozen comparison",
        ),
        (
            "VThreeExploratoryCIHigh",
            100 * primary["confidence_interval_95"][1],
            fmt_fixed(2),
            "percent",
            "exploratory mixed/confounded frozen comparison",
        ),
        (
            "PostfreezeCompatibilityRMSE",
            compat["rmse"],
            fmt_fixed(9),
            "rad",
            "postfreeze regression, not v3 confirmation",
        ),
        (
            "PostfreezeCompatibilityLagMS",
            1000 * compat["best_lag_s"],
            fmt_fixed(0),
            "ms",
            "postfreeze regression, not v3 confirmation",
        ),
        (
            "PostfreezeNativeExecutionRate",
            100 * compat["native_execution_rate"],
            fmt_fixed(0),
            "percent",
            "postfreeze regression, not v3 confirmation",
        ),
        (
            "CSVLowJerkSweep",
            low_jerk["sweep_value"],
            fmt_fixed(0),
            "rad/s^3",
            "Phase A CSV P one-factor jerk sweep",
        ),
        (
            "CSVLowJerkRMSEFactor",
            low_jerk["rmse"] / nominal_jerk["rmse"],
            fmt_fixed(2),
            "ratio",
            "Phase A CSV P low-jerk RMSE divided by nominal",
        ),
        (
            "CSVLowJerkLagIncreaseMS",
            low_jerk["best_lag_ms"] - nominal_jerk["best_lag_ms"],
            fmt_fixed(0),
            "ms",
            "Phase A CSV P low-jerk lag minus nominal",
        ),
        (
            "CSVHighJerkSweep",
            high_jerk["sweep_value"],
            fmt_fixed(0),
            "rad/s^3",
            "Phase A CSV P one-factor jerk sweep",
        ),
        (
            "CSVHighJerkRMSEFactor",
            high_jerk["rmse"] / nominal_jerk["rmse"],
            fmt_fixed(2),
            "ratio",
            "Phase A CSV P high-jerk RMSE divided by nominal",
        ),
    ]

    p_runtime = runtime_by_method["one_step_governed_p_direct"]
    pv_runtime = runtime_by_method["one_step_governed_pv_direct"]
    pva_runtime = runtime_by_method["one_step_governed_pva_direct"]
    safety = v4_handoff["safety_gates"]
    v4_values: list[
        tuple[
            str,
            Any,
            Callable[[Any], str],
            str,
            str,
            list[str],
            str,
            str,
            str,
        ]
    ] = [
        (
            "VFourTestTrajectoryCount",
            int(primary_row["required_trajectory_count"]),
            fmt_fixed(0),
            "trajectories",
            "fresh locked synthetic V4 test trajectory count",
            ["v4_primary"],
            "all rows; required_trajectory_count asserted constant",
            "required_trajectory_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourPrimarySampleCountPerMethod",
            int(primary_identity_rows[0]["total_cycle_count"]),
            fmt_fixed(0),
            "aligned cycles per direct method",
            "complete direct-method sample/cycle denominator",
            ["v4_method_identity"],
            (
                "method in {one_step_governed_p_direct, "
                "one_step_governed_pv_direct, "
                "one_step_governed_pva_direct}; total_cycle_count asserted equal"
            ),
            "total_cycle_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourPrimaryPairedTrajectoryCount",
            int(primary_row["paired_trajectory_count"]),
            fmt_fixed(0),
            "paired trajectories",
            "complete paired primary denominator",
            ["v4_primary"],
            "all rows; paired_trajectory_count asserted constant",
            "paired_trajectory_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourPrimaryRelativeImprovement",
            100 * float(primary_row["overall_relative_improvement"]),
            fmt_fixed(4),
            "percent",
            "observed PVA-versus-P trajectory-level RMSE relative improvement",
            ["v4_primary"],
            "all 120 primary rows; overall value asserted constant",
            "100 * overall_relative_improvement",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourPrimaryRelativeCILow",
            100 * float(primary_row["overall_relative_improvement_ci_low"]),
            fmt_fixed(4),
            "percent",
            "lower endpoint of frozen paired-bootstrap relative-effect interval",
            ["v4_primary"],
            "all 120 primary rows; overall value asserted constant",
            "100 * overall_relative_improvement_ci_low",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourPrimaryRelativeCIHigh",
            100 * float(primary_row["overall_relative_improvement_ci_high"]),
            fmt_fixed(4),
            "percent",
            "upper endpoint of frozen paired-bootstrap relative-effect interval",
            ["v4_primary"],
            "all 120 primary rows; overall value asserted constant",
            "100 * overall_relative_improvement_ci_high",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourPrimaryAbsoluteImprovement",
            float(primary_row["overall_absolute_improvement"]),
            fmt_fixed(6),
            "rad",
            "observed PVA-versus-P absolute trajectory-level RMSE improvement",
            ["v4_primary"],
            "all 120 primary rows; overall value asserted constant",
            "overall_absolute_improvement",
            "fixed-point to 6 decimal places (round-half-even)",
        ),
        (
            "VFourPrimaryAbsoluteCILow",
            float(primary_row["overall_absolute_improvement_ci_low"]),
            fmt_fixed(6),
            "rad",
            "lower endpoint of frozen paired-bootstrap absolute-effect interval",
            ["v4_primary"],
            "all 120 primary rows; overall value asserted constant",
            "overall_absolute_improvement_ci_low",
            "fixed-point to 6 decimal places (round-half-even)",
        ),
        (
            "VFourPrimaryAbsoluteCIHigh",
            float(primary_row["overall_absolute_improvement_ci_high"]),
            fmt_fixed(6),
            "rad",
            "upper endpoint of frozen paired-bootstrap absolute-effect interval",
            ["v4_primary"],
            "all 120 primary rows; overall value asserted constant",
            "overall_absolute_improvement_ci_high",
            "fixed-point to 6 decimal places (round-half-even)",
        ),
        (
            "VFourBootstrapResampleCount",
            int(primary_row["bootstrap_resamples"]),
            fmt_fixed(0),
            "paired bootstrap resamples",
            "frozen primary paired-bootstrap resample count",
            ["v4_primary"],
            "all 120 primary rows; bootstrap_resamples asserted constant",
            "bootstrap_resamples",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourHarmfulCount",
            int(harmful_row["harmful_count"]),
            fmt_fixed(0),
            "trajectories",
            "primary trajectories with candidate RMSE greater than baseline RMSE",
            ["v4_harmful"],
            "comparison_id == PVA_vs_P_position_RMSE",
            "harmful_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourHarmfulDenominator",
            int(harmful_row["denominator"]),
            fmt_fixed(0),
            "trajectories",
            "harmful-trajectory denominator for the primary comparison",
            ["v4_harmful"],
            "comparison_id == PVA_vs_P_position_RMSE",
            "denominator",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourSameInformationFailureCount",
            len(same_information_failures),
            fmt_fixed(0),
            "aligned cycles",
            "failed composite event-flag entries in the frozen same-information audit",
            ["v4_same_information"],
            "audit_passed == False",
            "row count",
            "fixed-point with 0 decimal places (exact integer count)",
        ),
        (
            "VFourSameInformationAuditCycleCount",
            len(v4_same_information),
            fmt_fixed(0),
            "aligned cycles",
            "complete aligned-cycle denominator in the frozen same-information audit",
            ["v4_same_information"],
            "all rows",
            "row count",
            "fixed-point with 0 decimal places (exact integer count)",
        ),
        (
            "VFourSameInformationFailurePercent",
            100 * len(same_information_failures) / len(v4_same_information),
            fmt_fixed(4),
            "percent",
            "share of aligned cycles with a composite event-flag difference",
            ["v4_same_information"],
            "audit_passed == False over all rows",
            "100 * count(False) / row count",
            "ratio of exact counts multiplied by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourPMeanLagMS",
            1000 * float(lag_row["baseline_mean"]),
            fmt_fixed(2),
            "ms",
            "P mean lag in the frozen S4 lag comparison",
            ["v4_secondary"],
            "comparison_id == S4",
            "1000 * baseline_mean",
            "convert seconds to milliseconds, then fixed-point to 2 decimal places (round-half-even)",
        ),
        (
            "VFourPVAMeanLagMS",
            1000 * float(lag_row["candidate_mean"]),
            fmt_fixed(2),
            "ms",
            "PVA mean lag in the frozen S4 lag comparison",
            ["v4_secondary"],
            "comparison_id == S4",
            "1000 * candidate_mean",
            "convert seconds to milliseconds, then fixed-point to 2 decimal places (round-half-even)",
        ),
        (
            "VFourPRuntimePNinetyNineUS",
            float(p_runtime["total_p99_us"]),
            fmt_fixed(1),
            "us",
            "pooled five-repetition full-Python pipeline p99 for P",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_p_direct]",
            "total_p99_us",
            "fixed-point to 1 decimal place (round-half-even)",
        ),
        (
            "VFourPVRuntimePNinetyNineUS",
            float(pv_runtime["total_p99_us"]),
            fmt_fixed(1),
            "us",
            "pooled five-repetition full-Python pipeline p99 for PV",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_pv_direct]",
            "total_p99_us",
            "fixed-point to 1 decimal place (round-half-even)",
        ),
        (
            "VFourPVARuntimePNinetyNineUS",
            float(pva_runtime["total_p99_us"]),
            fmt_fixed(1),
            "us",
            "pooled five-repetition full-Python pipeline p99 for PVA",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_pva_direct]",
            "total_p99_us",
            "fixed-point to 1 decimal place (round-half-even)",
        ),
        (
            "VFourPRuntimeMaxUS",
            float(p_runtime["total_max_us"]),
            fmt_fixed(1),
            "us",
            "five-repetition full-Python pipeline maximum for P",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_p_direct]",
            "total_max_us",
            "fixed-point to 1 decimal place (round-half-even)",
        ),
        (
            "VFourPVRuntimeMaxUS",
            float(pv_runtime["total_max_us"]),
            fmt_fixed(1),
            "us",
            "five-repetition full-Python pipeline maximum for PV",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_pv_direct]",
            "total_max_us",
            "fixed-point to 1 decimal place (round-half-even)",
        ),
        (
            "VFourPVARuntimeMaxUS",
            float(pva_runtime["total_max_us"]),
            fmt_fixed(1),
            "us",
            "five-repetition full-Python pipeline maximum for PVA",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_pva_direct]",
            "total_max_us",
            "fixed-point to 1 decimal place (round-half-even)",
        ),
        (
            "VFourPDeadlineMissCount",
            int(p_runtime["deadline_miss_count"]),
            fmt_fixed(0),
            "cycles",
            "P full-Python pipeline deadline misses over five repetitions",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_p_direct]",
            "deadline_miss_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourPVDeadlineMissCount",
            int(pv_runtime["deadline_miss_count"]),
            fmt_fixed(0),
            "cycles",
            "PV full-Python pipeline deadline misses over five repetitions",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_pv_direct]",
            "deadline_miss_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourPVADeadlineMissCount",
            int(pva_runtime["deadline_miss_count"]),
            fmt_fixed(0),
            "cycles",
            "PVA full-Python pipeline deadline misses over five repetitions",
            ["v4_handoff"],
            "runtime_gates.methods[method == one_step_governed_pva_direct]",
            "deadline_miss_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourRuntimeCycleCountPerMethod",
            int(p_runtime["timed_cycle_count"]),
            fmt_fixed(0),
            "timed cycles per method",
            "pooled runtime denominator over five repetitions",
            ["v4_handoff"],
            "runtime_gates.methods; timed_cycle_count asserted equal for P/PV/PVA",
            "timed_cycle_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourDirectMethodPurityRate",
            min(float(row["method_purity_rate"]) for row in primary_identity_rows),
            fmt_fixed(1),
            "rate",
            "minimum method-purity rate over the three primary direct methods",
            ["v4_method_identity"],
            "method in {one_step_governed_p_direct, one_step_governed_pv_direct, one_step_governed_pva_direct}",
            "minimum(method_purity_rate)",
            "fixed-point to 1 decimal place (all selected rows are exactly 1.0)",
        ),
        (
            "VFourPrimaryFailureCount",
            int(safety["failure_count"]),
            fmt_fixed(0),
            "failures",
            "primary V4 failure count",
            ["v4_handoff"],
            "safety_gates",
            "failure_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourFallbackEventCount",
            int(safety["fallback_event_count"]),
            fmt_fixed(0),
            "events",
            "primary V4 fallback-event count",
            ["v4_handoff"],
            "safety_gates",
            "fallback_event_count",
            "fixed-point with 0 decimal places (exact integer)",
        ),
        (
            "VFourContinuousConstraintViolationCount",
            sum(safety["invariant_failure_counts"].values()),
            fmt_fixed(0),
            "violations",
            "sum of frozen continuous/invariant safety failure counts",
            ["v4_handoff"],
            "safety_gates.invariant_failure_counts",
            "sum of all fields",
            "fixed-point with 0 decimal places (exact integer sum)",
        ),
        (
            "VFourMaxErrorRelativeImprovement",
            100 * float(max_error_row["relative_improvement"]),
            fmt_fixed(4),
            "percent",
            "observed S3 PVA-versus-P maximum-error relative improvement",
            ["v4_secondary"],
            "comparison_id == S3",
            "100 * relative_improvement",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourPVRelativeImprovement",
            100 * float(secondary_by_id["S1"]["relative_improvement"]),
            fmt_fixed(4),
            "percent",
            "observed S1 PV-versus-P RMSE relative improvement",
            ["v4_secondary"],
            "comparison_id == S1",
            "100 * relative_improvement",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourPVAVersusPVRelativeImprovement",
            100 * float(secondary_by_id["S2"]["relative_improvement"]),
            fmt_fixed(4),
            "percent",
            "observed S2 PVA-versus-PV RMSE relative improvement",
            ["v4_secondary"],
            "comparison_id == S2",
            "100 * relative_improvement",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourRapidReversalRelativeImprovement",
            100 * float(rapid_reversal_row["relative_improvement"]),
            fmt_fixed(4),
            "percent",
            "descriptive rapid-reversal family relative improvement",
            ["v4_family"],
            "stratum_dimension == reference_family and stratum_value == rapid_reversal",
            "100 * relative_improvement",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourRapidReversalRelativeCILow",
            100 * float(rapid_reversal_row["relative_improvement_ci_low"]),
            fmt_fixed(4),
            "percent",
            "lower paired-bootstrap endpoint for rapid-reversal family effect",
            ["v4_family"],
            "stratum_dimension == reference_family and stratum_value == rapid_reversal",
            "100 * relative_improvement_ci_low",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourRapidReversalRelativeCIHigh",
            100 * float(rapid_reversal_row["relative_improvement_ci_high"]),
            fmt_fixed(4),
            "percent",
            "upper paired-bootstrap endpoint for rapid-reversal family effect",
            ["v4_family"],
            "stratum_dimension == reference_family and stratum_value == rapid_reversal",
            "100 * relative_improvement_ci_high",
            "multiply stored proportion by 100, then fixed-point to 4 decimal places (round-half-even)",
        ),
        (
            "VFourEffectiveClassification",
            v4_status["primary_result_classification"],
            latex_identifier,
            "status",
            "effective V4 classification after frozen validity gates",
            ["v4_status"],
            "root object",
            "primary_result_classification",
            "exact string; underscores escaped for LaTeX",
        ),
        (
            "VFourStatisticalClassification",
            v4_status["statistical_classification"],
            latex_identifier,
            "status",
            "V4 statistical-effect classification",
            ["v4_status"],
            "root object",
            "statistical_classification",
            "exact string; underscores escaped for LaTeX",
        ),
        (
            "VFourProtocolStatus",
            v4_status["status"],
            latex_identifier,
            "status",
            "frozen test-visible V4 protocol status",
            ["v4_status"],
            "root object",
            "status",
            "exact string; underscores escaped for LaTeX",
        ),
    ]

    if any(
        int(row["timed_cycle_count"]) != int(p_runtime["timed_cycle_count"])
        for row in runtime_by_method.values()
    ):
        raise ValueError("V4 pooled runtime denominators differ across primary methods")
    if {
        int(row["total_cycle_count"]) for row in primary_identity_rows
    } != {42072}:
        raise ValueError("V4 primary sample/cycle denominator is not 42,072 per method")

    tex_lines = [
        "% Generated by scripts/generate_numbers.py; do not edit.",
        "% All empirical values have a record in manifests/number_provenance.json.",
    ]
    provenance: dict[str, Any] = {
        "schema_version": "otg.paper-number-provenance.v2",
        "input_path": EVIDENCE.relative_to(REPO_ROOT).as_posix(),
        "input_sha256": sha256(EVIDENCE),
        "rounding_rule": "round-half-even via Python fixed-point formatting",
        "generation_script_path": "paper/scripts/generate_numbers.py",
        "generation_script_commit": generation_script_commit(),
        "generation_script_sha256": sha256(Path(__file__)),
        "macros": {},
    }

    def macro_sources(name: str) -> tuple[list[str], list[str]]:
        if name.startswith(("CenteredVelocity", "CenteredAcceleration")):
            return ["E_PHASE_A_DERIVATIVES"], ["phase_a_derivatives"]
        if name.startswith("Analytic"):
            return ["E_PHASE_A_TRACKING"], ["phase_a_tracking"]
        if name.startswith("Oracle"):
            return ["E_PHASE_A_ORACLE"], ["phase_a_oracle"]
        if name.startswith(("CSVLowJerk", "CSVHighJerk")):
            return ["E_PHASE_A_LIMITS"], ["phase_a_limits"]
        if name.startswith("CSV"):
            return ["E_REAL_CSV_NEGATIVE"], ["phase_a_tracking", "phase_a_run"]
        if name.startswith("VThreeRuntime"):
            return ["E_V3_RUNTIME"], ["v3_runtime_primary"]
        if name.startswith(
            (
                "VThreeExploratory",
                "VThreeConfounded",
            )
        ):
            return ["E_V3_CONFOUNDED_COMPARISON"], ["v3_postreview"]
        if name.startswith(
            (
                "VThreeRawBundle",
                "VThreeBoundedArtifact",
                "VThreeRequiredCriterion",
            )
        ):
            return ["E_V3_ARTIFACT_INTEGRITY"], [
                "v3_status",
                "v3_artifact_index",
            ]
        if name.startswith("VThree"):
            return ["E_V3_DIRECT_SAFETY"], [
                "v3_acceptance",
                "v3_fallback",
                "v3_postreview",
            ]
        if name.startswith("Postfreeze"):
            return ["E_POSTFREEZE_RUCKIG_COMPATIBILITY"], [
                "postfreeze_compatibility"
            ]
        return ["E_PHASE_A_TRACKING"], ["phase_a_run", "phase_a_tracking"]

    for name, raw, formatter, units, selector in values:
        formatted = formatter(float(raw))
        tex_lines.append(f"\\newcommand{{\\{name}}}{{{formatted}}}")
        source_ids, source_keys = macro_sources(name)
        provenance["macros"][name] = {
            "raw_value": raw,
            "formatted_value": formatted,
            "units": units,
            "selector": selector,
            "row_selector": selector,
            "field_selector": "fields named by selector; derived values retain the described operation",
            "rounding_rule": inferred_rounding_rule(formatted),
            "source_ids": source_ids,
            "sources": [
                {
                    "path": data["sources"][key]["path"],
                    "sha256": data["sources"][key]["sha256"],
                }
                for key in source_keys
            ],
            "generation_script_commit": provenance["generation_script_commit"],
            "generation_script_sha256": provenance["generation_script_sha256"],
        }

    v4_source_records = {
        key: {
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
        for key, path in V4_SOURCES.items()
    }

    def v4_source_id(name: str, key: str) -> str:
        if key == "v4_secondary":
            if name in {"VFourPMeanLagMS", "VFourPVAMeanLagMS"}:
                return "E_V4_LAG_GUARDRAIL"
            if name == "VFourMaxErrorRelativeImprovement":
                return "E_V4_SAFETY"
            return "E_V4_PRIMARY_OBSERVED_EFFECT"
        if key == "v4_handoff":
            if name.startswith(
                (
                    "VFourPrimaryFailure",
                    "VFourFallback",
                    "VFourContinuousConstraint",
                )
            ):
                return "E_V4_SAFETY"
            return "E_V4_RUNTIME_FAILURE"
        return {
            "v4_primary": "E_V4_PRIMARY_OBSERVED_EFFECT",
            "v4_family": "E_V4_SUBGROUPS",
            "v4_harmful": "E_V4_HARMFUL_TRAJECTORIES",
            "v4_method_identity": "E_V4_METHOD_PURITY",
            "v4_same_information": "E_V4_SAME_INFORMATION_FAILURE",
            "v4_status": "E_V4_FRESH_LOCKED_TEST",
        }[key]

    for (
        name,
        raw,
        formatter,
        units,
        selector,
        source_keys,
        row_selector,
        field_selector,
        rounding_rule,
    ) in v4_values:
        formatted = formatter(raw)
        tex_lines.append(f"\\newcommand{{\\{name}}}{{{formatted}}}")
        provenance["macros"][name] = {
            "raw_value": raw,
            "formatted_value": formatted,
            "units": units,
            "selector": selector,
            "row_selector": row_selector,
            "field_selector": field_selector,
            "rounding_rule": rounding_rule,
            "source_ids": [v4_source_id(name, key) for key in source_keys],
            "sources": [v4_source_records[key] for key in source_keys],
            "generation_script_commit": provenance["generation_script_commit"],
            "generation_script_sha256": provenance["generation_script_sha256"],
        }
    tex = "\n".join(tex_lines) + "\n"

    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != tex:
            raise SystemExit("generated/numbers.tex is stale")
        current = json.loads(PROVENANCE.read_text(encoding="utf-8"))
        if current != provenance:
            raise SystemExit("number provenance is stale")
        print("number generation verified")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(tex, encoding="utf-8")
    PROVENANCE.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} and provenance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
