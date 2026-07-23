#!/usr/bin/env python3
"""Generate LaTeX result macros and machine-readable provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
EVIDENCE = PAPER_ROOT / "generated/manifests/extracted_evidence.json"
OUTPUT = PAPER_ROOT / "generated/numbers.tex"
PROVENANCE = PAPER_ROOT / "generated/manifests/number_provenance.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def by_key(rows: list[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
    matches = [row for row in rows if row[field] == value]
    if len(matches) != 1:
        raise ValueError(f"expected one {field}={value!r}, got {len(matches)}")
    return matches[0]


def fmt_fixed(digits: int) -> Callable[[float], str]:
    return lambda value: f"{value:.{digits}f}"


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
    v3_metric = {row["metric"]: row["observed"] for row in v3_rows}
    v3_by_criterion = {row["criterion_id"]: row for row in v3_rows}
    frozen = postreview["frozen_observations"]
    compat = postreview["current_code_regression"]
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
            compat["position_rmse"]["observed"],
            fmt_fixed(9),
            "rad",
            "postfreeze regression, not v3 confirmation",
        ),
        (
            "PostfreezeCompatibilityLagMS",
            1000 * compat["lag_s"]["observed"],
            fmt_fixed(0),
            "ms",
            "postfreeze regression, not v3 confirmation",
        ),
        (
            "PostfreezeNativeExecutionRate",
            100 * compat["native_execution_rate"]["observed"],
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

    tex_lines = [
        "% Generated by scripts/generate_numbers.py; do not edit.",
        "% All empirical values have a record in manifests/number_provenance.json.",
    ]
    provenance: dict[str, Any] = {
        "schema_version": "otg.paper-number-provenance.v1",
        "input_path": EVIDENCE.relative_to(REPO_ROOT).as_posix(),
        "input_sha256": sha256(EVIDENCE),
        "rounding_rule": "round-half-even via Python fixed-point formatting",
        "macros": {},
    }
    for name, raw, formatter, units, selector in values:
        formatted = formatter(float(raw))
        tex_lines.append(f"\\newcommand{{\\{name}}}{{{formatted}}}")
        provenance["macros"][name] = {
            "raw_value": raw,
            "formatted_value": formatted,
            "units": units,
            "selector": selector,
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
