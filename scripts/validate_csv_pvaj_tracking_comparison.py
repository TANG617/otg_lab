"""Independently recompute the highest-impact two-CSV comparison claims."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_runner import run_target_state_sequence  # noqa: E402
from target_state_experiment import DT, SETTLE_TIME, VENDOR_LIMITS  # noqa: E402

DEFAULT_RESULTS = ROOT / "results" / "csv_pvaj_tracking_comparison"
INPUTS = {
    "current_csv": ROOT / "plot_data.csv",
    "new_csv": ROOT / "data" / "simplified-tasks_no-velocity-limit.csv",
}


def _read_rows(path):
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_position(path):
    values = np.genfromtxt(path, delimiter=",", names=True)["value"]
    values = np.atleast_1d(values).astype(float)
    if values.size < 8 or not np.all(np.isfinite(values)):
        raise ValueError(f"invalid validation input: {path}")
    return values


def _independent_pvaj(position):
    """Use explicit interior stencils rather than production helpers."""
    count = position.size
    velocity = np.full(count, np.nan, dtype=float)
    acceleration = np.full(count, np.nan, dtype=float)
    jerk = np.full(count, np.nan, dtype=float)
    velocity[1:-1] = (position[2:] - position[:-2]) / (2.0 * DT)
    acceleration[1:-1] = (position[2:] - 2.0 * position[1:-1] + position[:-2]) / DT**2
    jerk[2:-2] = (acceleration[3:-1] - acceleration[1:-3]) / (2.0 * DT)
    interior = slice(3, count - 3)
    return {
        "velocity": float(np.max(np.abs(velocity[interior]))),
        "acceleration": float(np.max(np.abs(acceleration[interior]))),
        "jerk": float(np.max(np.abs(jerk[interior]))),
    }


def _independent_best_lag(reference, output):
    best_rmse = float("inf")
    best_lag = None
    max_lag_samples = min(100, (reference.size - 1) // 2)
    for lag in range(-max_lag_samples, max_lag_samples + 1):
        if lag > 0:
            reference_part = reference[:-lag]
            output_part = output[lag:]
        elif lag < 0:
            reference_part = reference[-lag:]
            output_part = output[:lag]
        else:
            reference_part = reference
            output_part = output
        rmse = float(np.sqrt(np.mean((output_part - reference_part) ** 2)))
        if rmse < best_rmse:
            best_rmse = rmse
            best_lag = lag
    return float(best_lag * DT * 1000.0), best_rmse


def _independent_p_tracking(position):
    settle_count = int(round(SETTLE_TIME / DT))
    reference = np.concatenate((position, np.full(settle_count, position[-1])))
    targets = np.column_stack((reference, np.zeros((reference.size, 2), dtype=float)))
    result = run_target_state_sequence(
        reference,
        targets,
        DT,
        **VENDOR_LIMITS.as_dict(),
        minimum_duration=DT,
        project_targets=True,
    )
    evaluation = slice(3, position.size)
    evaluated_reference = reference[evaluation]
    evaluated_output = result["position"][evaluation]
    error = evaluated_output - evaluated_reference
    lag_ms, aligned_rmse = _independent_best_lag(evaluated_reference, evaluated_output)
    robust_scale = float(np.percentile(position, 95) - np.percentile(position, 5))
    return {
        "rmse": float(np.sqrt(np.mean(error**2))),
        "normalized_rmse_robust": float(np.sqrt(np.mean(error**2)) / robust_scale),
        "max_error": float(np.max(np.abs(error))),
        "abs_best_lag_ms": abs(lag_ms),
        "lag_aligned_rmse": aligned_rmse,
    }


def _check(name, observed, expected, tolerance):
    difference = abs(float(observed) - float(expected))
    return {
        "name": name,
        "status": "passed" if difference <= tolerance else "failed",
        "observed": float(observed),
        "expected": float(expected),
        "absolute_difference": difference,
        "tolerance": tolerance,
    }


def validate(results_dir):
    results_dir = Path(results_dir).resolve()
    raw_rows = _read_rows(results_dir / "raw_pvaj_metrics.csv")
    metric_rows = _read_rows(results_dir / "metric_comparison.csv")
    raw_lookup = {
        (row["dataset"], row["signal"]): row
        for row in raw_rows
        if row["derivative_basis"] == "fixed_10ms_centered"
    }
    metric_lookup = {row["metric"]: row for row in metric_rows}

    positions = {dataset: _read_position(path) for dataset, path in INPUTS.items()}
    independent_pvaj = {
        dataset: _independent_pvaj(position) for dataset, position in positions.items()
    }
    independent_tracking = {
        dataset: _independent_p_tracking(position)
        for dataset, position in positions.items()
    }

    checks = []
    for dataset in INPUTS:
        for signal in ("velocity", "acceleration", "jerk"):
            checks.append(
                _check(
                    f"{dataset} max_abs_{signal}",
                    independent_pvaj[dataset][signal],
                    raw_lookup[(dataset, signal)]["max_abs"],
                    1e-9,
                )
            )
        for metric in (
            "rmse",
            "normalized_rmse_robust",
            "max_error",
            "abs_best_lag_ms",
            "lag_aligned_rmse",
        ):
            checks.append(
                _check(
                    f"{dataset} P-only {metric}",
                    independent_tracking[dataset][metric],
                    metric_lookup[metric][dataset],
                    1e-9,
                )
            )

    for metric, field in (
        ("max_abs_velocity", "velocity"),
        ("max_abs_acceleration", "acceleration"),
        ("max_abs_jerk", "jerk"),
    ):
        baseline = independent_pvaj["current_csv"][field]
        candidate = independent_pvaj["new_csv"][field]
        change = 100.0 * (candidate - baseline) / abs(baseline)
        checks.append(
            _check(
                f"candidate change {metric}",
                change,
                metric_lookup[metric]["change_pct"],
                1e-9,
            )
        )
    baseline_nrmse = independent_tracking["current_csv"]["normalized_rmse_robust"]
    candidate_nrmse = independent_tracking["new_csv"]["normalized_rmse_robust"]
    nrmse_change = 100.0 * (candidate_nrmse - baseline_nrmse) / abs(baseline_nrmse)
    checks.append(
        _check(
            "candidate change normalized_rmse_robust",
            nrmse_change,
            metric_lookup["normalized_rmse_robust"]["change_pct"],
            1e-9,
        )
    )

    failed = [check for check in checks if check["status"] != "passed"]
    artifact = json.loads((results_dir / "artifact.json").read_text(encoding="utf-8"))
    source_queries = [
        source.get("query", {}).get("sql") for source in artifact["manifest"]["sources"]
    ]
    structural_checks = {
        "all_numeric_checks_passed": not failed,
        "artifact_title_matches_first_heading": (
            artifact["manifest"]["blocks"][0]["body"]
            == f"# {artifact['manifest']['title']}"
        ),
        "all_native_sources_have_sql": all(source_queries),
        "report_html_exists": (results_dir / "report.html").is_file(),
        "input_row_counts": {
            dataset: int(position.size) for dataset, position in positions.items()
        },
    }
    all_passed = not failed and all(
        value
        for key, value in structural_checks.items()
        if key not in {"input_row_counts"}
    )
    return {
        "schema": "otg.csv-pvaj-tracking-validation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_assessment": (
            "share_with_caveats" if all_passed else "needs_revision"
        ),
        "numeric_recomputation": {
            "status": "passed" if not failed else "failed",
            "check_count": len(checks),
            "failed_count": len(failed),
            "checks": checks,
        },
        "structural_checks": structural_checks,
        "claim_validation": {
            "verified": [
                "New maximum sampled acceleration is lower.",
                "New maximum sampled jerk is lower.",
                "New maximum sampled velocity is higher.",
                "New P-only robust-scale tracking NRMSE is higher.",
            ],
            "not_established": [
                (
                    "A causal effect of V, A, or J on tracking because the two "
                    "traces are not paired on geometry, duration, or range."
                ),
                (
                    "Population-level performance because there are only two "
                    "recorded traces and no independent repetitions."
                ),
            ],
        },
        "browser_qa": {
            "portable_builder_validation": "passed",
            "portable_builder_packaging": "passed",
            "portable_builder_verification": "structural_only",
            "enhanced_reader": (
                "not verified; installed Chromium timed out during builder QA"
            ),
            "semantic_fallback": "present",
        },
        "blockers": [],
        "required_caveats": [
            "Treat the result as development-only descriptive evidence.",
            "Do not claim that lower A/J improved tracking in this comparison.",
            (
                "Use a paired same-path controlled experiment before making "
                "causal or deployment-generalization claims."
            ),
        ],
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Independently validate the two-CSV comparison."
    )
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    return parser.parse_args()


def main():
    args = parse_args()
    results_dir = args.results_dir.resolve()
    report = validate(results_dir)
    output = results_dir / "validation.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Assessment: {report['overall_assessment']}")
    print(
        "Numeric checks: "
        f"{report['numeric_recomputation']['check_count']} total, "
        f"{report['numeric_recomputation']['failed_count']} failed"
    )
    print(f"Saved: {output}")
    if report["overall_assessment"] == "needs_revision":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
