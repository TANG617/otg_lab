#!/usr/bin/env python3
"""Run a deterministic, local-data-only end-to-end pipeline smoke probe."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

import numpy as np

from otg_lab.config import DEFAULT_CONFIG, validate_config
from otg_lab.datasets import entries_for_split, generate_trajectory, trajectory_to_rows
from otg_lab.runner import run_pipeline_rows


def run_smoke() -> dict[str, object]:
    """Exercise dataset -> estimator -> predictor -> governor -> follower -> plant."""

    entry = entries_for_split("validation")[0]
    truth = generate_trajectory(entry)
    rows = trajectory_to_rows(truth, sample_rate_hz=100.0, run_id="ci-smoke")

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["run_id"] = "ci-smoke"
    config["data"].update(split="validation", max_trajectories=1)
    validate_config(config)

    result = run_pipeline_rows(rows, config)
    output = result.rows
    if len(output) != len(rows) or len(output) < 2:
        raise AssertionError("pipeline did not return one enriched row per input row")

    numeric_trace = np.asarray(
        [[row["command_p"], row["command_v"], row["command_a"]] for row in output],
        dtype="<f8",
    )
    if not np.all(np.isfinite(numeric_trace)):
        raise AssertionError("non-finite command state in smoke output")

    causality_violations = sum(
        float(row["posterior_available_time"]) > float(row["control_time"]) + 1e-12
        or float(row["posterior_state_time"])
        > float(row["posterior_available_time"]) + 1e-12
        for row in output
    )
    if causality_violations:
        raise AssertionError(f"causality violations: {causality_violations}")
    if result.constraint_violation_count:
        raise AssertionError(
            f"continuous constraint violations: {result.constraint_violation_count}"
        )

    limits = config["limits"]
    maxima = {
        "abs_velocity": float(np.max(np.abs(numeric_trace[:, 1]))),
        "abs_acceleration": float(np.max(np.abs(numeric_trace[:, 2]))),
        "abs_command_jerk": float(
            np.max(np.abs([row["command_jerk"] for row in output]))
        ),
    }
    for observed, bound, label in (
        (maxima["abs_velocity"], limits["max_velocity"], "velocity"),
        (maxima["abs_acceleration"], limits["max_acceleration"], "acceleration"),
        (maxima["abs_command_jerk"], limits["max_jerk"], "jerk"),
    ):
        if observed > float(bound) + 1e-9:
            raise AssertionError(f"{label} limit exceeded: {observed} > {bound}")

    return {
        "schema_version": 1,
        "probe": "ci_minimal_end_to_end",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": {
            package: version(package)
            for package in ("numpy", "ruckig", "scipy", "pyarrow")
        },
        "trajectory": {
            "trajectory_id": entry.trajectory_id,
            "family": entry.family,
            "split": entry.split,
            "seed": entry.seed,
            "row_count": len(output),
        },
        "pipeline": {
            "estimator": config["pipeline"]["estimator"],
            "predictor": config["pipeline"]["predictor"],
            "governor": config["pipeline"]["governor"],
            "follower": config["pipeline"]["follower"],
            "plant": config["pipeline"]["plant"],
        },
        "checks": {
            "causality_violation_count": causality_violations,
            "constraint_violation_count": result.constraint_violation_count,
            "fallback_count": result.fallback_count,
            "deadline_miss_count_observed_not_gating": result.deadline_miss_count,
            **maxima,
        },
        "command_trace_sha256": hashlib.sha256(numeric_trace.tobytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(run_smoke(), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
