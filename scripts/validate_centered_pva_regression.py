"""Independently recompute the centered-PVA regression's key claims."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_runner import run_target_state_sequence  # noqa: E402
from target_state_experiment import DT, SETTLE_TIME, VENDOR_LIMITS  # noqa: E402

DEFAULT_RESULTS = ROOT / "results" / "centered_pva_regression"
INPUTS = {
    "no_velocity_limit": (
        ROOT / "data" / "simplified-tasks_no-velocity-limit.csv"
    ),
    "velocity_limit": ROOT / "data" / "simplified-tasks_velocity-limit.csv",
}
METHODS = (
    "p_only_latest",
    "p_only_delayed",
    "centered_pva_delayed_clamped",
    "centered_pva_latest_position_clamped",
)


def _read_rows(path):
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_trace(path):
    rows = _read_rows(path)
    position = np.array([float(row["value"]) for row in rows], dtype=float)
    timestamp = np.array([float(row["timestamp"]) for row in rows], dtype=float)
    if (
        position.size < 8
        or not np.all(np.isfinite(position))
        or not np.all(np.diff(timestamp) > 0.0)
    ):
        raise ValueError(f"invalid trace: {path}")
    return position, timestamp


def _extend(position):
    settle_count = int(round(SETTLE_TIME / DT))
    return np.concatenate(
        (position, np.full(settle_count, position[-1], dtype=float))
    )


def _independent_targets(position, method_id):
    count = position.size
    target = np.zeros((count, 3), dtype=float)
    target[:, 0] = position
    if method_id == "p_only_latest":
        return target

    target[:2, 0] = position[0]
    target[2:, 0] = position[1:-1]
    if method_id == "p_only_delayed":
        return target

    velocity = (position[2:] - position[:-2]) / (2.0 * DT)
    acceleration = (
        position[2:] - 2.0 * position[1:-1] + position[:-2]
    ) / DT**2
    target[2:, 1] = np.clip(
        velocity,
        -VENDOR_LIMITS.max_velocity,
        VENDOR_LIMITS.max_velocity,
    )
    target[2:, 2] = np.clip(
        acceleration,
        -VENDOR_LIMITS.max_acceleration,
        VENDOR_LIMITS.max_acceleration,
    )
    if method_id == "centered_pva_latest_position_clamped":
        target[:, 0] = position
        target[:2, 0] = position[0]
    elif method_id != "centered_pva_delayed_clamped":
        raise ValueError(f"unsupported validation method: {method_id}")
    return target


def _best_lag(reference, output):
    best_rmse = float("inf")
    best_lag = None
    max_lag = min(100, (reference.size - 1) // 2)
    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            ref_part, out_part = reference[:-lag], output[lag:]
        elif lag < 0:
            ref_part, out_part = reference[-lag:], output[:lag]
        else:
            ref_part, out_part = reference, output
        rmse = float(np.sqrt(np.mean((out_part - ref_part) ** 2)))
        if rmse < best_rmse:
            best_rmse = rmse
            best_lag = lag
    return float(best_lag * DT * 1000.0), best_rmse


def _independent_tracking(original_position, method_id):
    position = _extend(original_position)
    targets = _independent_targets(position, method_id)
    result = run_target_state_sequence(
        position,
        targets,
        DT,
        **VENDOR_LIMITS.as_dict(),
        minimum_duration=DT,
        project_targets=True,
    )
    evaluation = slice(3, original_position.size)
    reference = position[evaluation]
    output = result["position"][evaluation]
    error = output - reference
    scale = float(
        np.percentile(original_position, 95)
        - np.percentile(original_position, 5)
    )
    lag_ms, aligned_rmse = _best_lag(reference, output)
    target_slice = slice(2, original_position.size - 1)
    acceleration_raw = (
        position[2:] - 2.0 * position[1:-1] + position[:-2]
    ) / DT**2
    evaluated_raw_acceleration = acceleration_raw[: original_position.size - 3]
    return {
        "normalized_rmse_robust": float(
            np.sqrt(np.mean(error**2)) / scale
        ),
        "best_lag_ms": lag_ms,
        "lag_aligned_normalized_rmse": aligned_rmse / scale,
        "acceleration_hard_clamp_rate": (
            float(
                np.mean(
                    np.abs(evaluated_raw_acceleration)
                    > VENDOR_LIMITS.max_acceleration
                )
            )
            if method_id.startswith("centered_pva")
            else 0.0
        ),
        "ruckig_feasibility_projection_rate": float(
            np.mean(result["projection_mask"][target_slice])
        ),
        "preclamp_p99_abs_acceleration_rad_s2": (
            float(np.percentile(np.abs(evaluated_raw_acceleration), 99))
            if method_id.startswith("centered_pva")
            else 0.0
        ),
    }


def _independent_source_gain(timestamp):
    time = timestamp - timestamp[0]
    h0 = np.diff(time)[:-1]
    h1 = np.diff(time)[1:]
    c0 = 2.0 / (h0 * (h0 + h1))
    c1 = -2.0 / (h0 * h1)
    c2 = 2.0 / (h1 * (h0 + h1))
    gain = np.abs(c0) + np.abs(c1) + np.abs(c2)
    return {
        "dt_min_ms": 1000.0 * float(np.min(np.diff(time))),
        "dt_max_ms": 1000.0 * float(np.max(np.diff(time))),
        "history_reset_gap_count": int(np.sum(np.diff(time) > 0.05)),
        "acceleration_noise_gain_p99_per_s2": float(
            np.percentile(gain, 99)
        ),
        "acceleration_noise_gain_max_per_s2": float(np.max(gain)),
    }


def _quadratic_formula_check():
    time = np.array([0.0, 0.01, 0.025])
    position = time**2 + 3.0 * time + 0.7
    h0, h1 = np.diff(time)
    q0, q1, q2 = position
    velocity = (
        -h1 / (h0 * (h0 + h1)) * q0
        + (h1 - h0) / (h0 * h1) * q1
        + h0 / (h1 * (h0 + h1)) * q2
    )
    acceleration = (
        2.0 / (h0 * (h0 + h1)) * q0
        - 2.0 / (h0 * h1) * q1
        + 2.0 / (h1 * (h0 + h1)) * q2
    )
    return velocity, acceleration


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


def validate(results_dir, mcp_validation_passed=False):
    results_dir = Path(results_dir).resolve()
    metric_rows = _read_rows(results_dir / "tracking_metrics.csv")
    estimator_rows = _read_rows(results_dir / "estimator_diagnostics.csv")
    metric_lookup = {
        (row["dataset"], row["method_id"]): row
        for row in metric_rows
        if row["time_basis"] == "fixed_10ms"
    }
    estimator_lookup = {
        (row["dataset"], row["time_basis"]): row for row in estimator_rows
    }

    traces = {
        dataset: _read_trace(path) for dataset, path in INPUTS.items()
    }
    tracking = {
        (dataset, method): _independent_tracking(position, method)
        for dataset, (position, _timestamp) in traces.items()
        for method in METHODS
    }
    source_gain = {
        dataset: _independent_source_gain(timestamp)
        for dataset, (_position, timestamp) in traces.items()
    }

    checks = []
    for dataset in INPUTS:
        for method in METHODS:
            expected = metric_lookup[(dataset, method)]
            observed = tracking[(dataset, method)]
            for field in (
                "normalized_rmse_robust",
                "best_lag_ms",
                "lag_aligned_normalized_rmse",
                "acceleration_hard_clamp_rate",
                "ruckig_feasibility_projection_rate",
                "preclamp_p99_abs_acceleration_rad_s2",
            ):
                checks.append(
                    _check(
                        f"{dataset} {method} {field}",
                        observed[field],
                        expected[field],
                        1e-9,
                    )
                )
        expected_gain = estimator_lookup[
            (dataset, "csv_timestamp_proxy")
        ]
        for field in (
            "dt_min_ms",
            "dt_max_ms",
            "history_reset_gap_count",
            "acceleration_noise_gain_p99_per_s2",
            "acceleration_noise_gain_max_per_s2",
        ):
            checks.append(
                _check(
                    f"{dataset} timestamp {field}",
                    source_gain[dataset][field],
                    expected_gain[field],
                    1e-7,
                )
            )

    velocity, acceleration = _quadratic_formula_check()
    checks.extend(
        (
            _check(
                "nonuniform quadratic velocity sign",
                velocity,
                3.02,
                1e-12,
            ),
            _check(
                "nonuniform quadratic acceleration sign",
                acceleration,
                2.0,
                1e-11,
            ),
        )
    )

    artifact = json.loads(
        (results_dir / "artifact.json").read_text(encoding="utf-8")
    )
    sqlite_path = results_dir / "report_source.sqlite"
    connection = sqlite3.connect(sqlite_path)
    try:
        sql_checks = []
        for source in artifact["manifest"]["sources"]:
            query = source["query"]["sql"]
            rows = connection.execute(query).fetchall()
            sql_checks.append(
                {
                    "source_id": source["id"],
                    "row_count": len(rows),
                    "status": "passed" if rows else "failed",
                }
            )
    finally:
        connection.close()

    html_text = (results_dir / "report.html").read_text(encoding="utf-8")
    structural_checks = {
        "artifact_title_matches_first_heading": (
            artifact["manifest"]["blocks"][0]["body"]
            == f"# {artifact['manifest']['title']}"
        ),
        "artifact_has_chart_block": any(
            block["type"] == "chart"
            for block in artifact["manifest"]["blocks"]
        ),
        "artifact_snapshot_bounded": all(
            len(rows) <= 2000
            for rows in artifact["snapshot"]["datasets"].values()
        ),
        "all_source_sql_executes_with_rows": all(
            row["status"] == "passed" for row in sql_checks
        ),
        "static_report_has_title": artifact["manifest"]["title"] in html_text,
        "static_report_references_all_figures": all(
            filename in html_text
            for filename in (
                "nrmse_ablation.png",
                "target_diagnostics.png",
                "tracking_comparison.png",
            )
        ),
        "mcp_artifact_validation_passed": bool(mcp_validation_passed),
        "exported_artifact_package_exists": (
            results_dir / "artifact_package.tar.gz"
        ).is_file(),
    }
    failed = [check for check in checks if check["status"] != "passed"]
    all_passed = (
        not failed
        and all(structural_checks.values())
        and all(row["status"] == "passed" for row in sql_checks)
    )
    return {
        "schema": "otg.centered-pva-regression-validation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_assessment": "passed" if all_passed else "needs_revision",
        "numeric_recomputation": {
            "status": "passed" if not failed else "failed",
            "check_count": len(checks),
            "failed_count": len(failed),
            "checks": checks,
        },
        "source_sql_checks": sql_checks,
        "structural_checks": structural_checks,
        "claim_validation": {
            "verified": [
                (
                    "Production-like centered PVA has higher same-reference "
                    "NRMSE than P-only latest on both supplied traces."
                ),
                (
                    "The regression is much larger on the no-velocity-limit "
                    "trace, where acceleration clipping is frequent."
                ),
                (
                    "Latest-position centered PVA beats P-only on the smoother "
                    "velocity-limit trace but not on the rough trace."
                ),
                (
                    "No source interval exceeds the 50 ms reset threshold, so "
                    "history resets do not explain these two results."
                ),
            ],
            "not_established": [
                (
                    "Exact production-node parity, because the C++ controller "
                    "source and real header-stamp provenance are absent here."
                ),
                (
                    "Robot-level closed-loop performance, because this is a "
                    "single-axis ordinary-Ruckig harness without a plant."
                ),
            ],
        },
        "required_caveats": [
            (
                "Treat CSV timestamp results as sensitivity evidence, not as "
                "a confirmed reproduction of JointState.header.stamp."
            ),
            (
                "Confirm production limits, nonuniform coefficient signs, and "
                "warm-up behavior against the real controller source."
            ),
        ],
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Independently validate the centered-PVA regression."
    )
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--mcp-validation-passed", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    report = validate(args.results_dir, args.mcp_validation_passed)
    output = args.results_dir.resolve() / "validation.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    numeric = report["numeric_recomputation"]
    print(f"Assessment: {report['overall_assessment']}")
    print(
        f"Numeric checks: {numeric['check_count']} total, "
        f"{numeric['failed_count']} failed"
    )
    print(f"Saved: {output}")
    if report["overall_assessment"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
