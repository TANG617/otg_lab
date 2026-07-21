#!/usr/bin/env python3
"""Run the same deterministic compatibility probes in an isolated interpreter."""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

import numpy as np
import ruckig

from otg_lab.followers import DirectExecutableFollower, RuckigFollower
from otg_lab.governors import MotionLimits, OneStepBoundedJerkGovernor
from otg_runner import run_target_state_sequence
from target_state_experiment import (
    VENDOR_LIMITS,
    build_target_states,
    elementary_references,
)


def run_probe() -> dict:
    reference = elementary_references()["sine"]
    methods = {}
    for method_id in ("p", "pv_truth", "pva_truth"):
        result = run_target_state_sequence(
            reference.position,
            build_target_states(reference, method_id),
            reference.dt,
            **VENDOR_LIMITS.as_dict(),
            minimum_duration=reference.dt,
            project_targets=True,
        )
        error = (
            result["position"][3 : reference.original_count]
            - reference.position[3 : reference.original_count]
        )
        methods[method_id] = {
            "rmse": float(np.sqrt(np.mean(error**2))),
            "max_error": float(np.max(np.abs(error))),
            "max_velocity": float(np.max(np.abs(result["velocity"]))),
            "max_acceleration": float(np.max(np.abs(result["acceleration"]))),
            "max_new_jerk": float(np.max(np.abs(result["new_jerk"]))),
            "position_sha256_input": __import__("hashlib")
            .sha256(np.asarray(result["position"], dtype="<f8").tobytes())
            .hexdigest(),
        }

    limits = MotionLimits.broadcast(1)
    initial = np.zeros((1, 3))
    governor = OneStepBoundedJerkGovernor(1, 0.01, limits)
    governed = governor.update(
        np.asarray([[0.02, 0.5, 1.0]]),
        control_time=0.0,
        current_state=initial,
    )
    direct = DirectExecutableFollower(1, 0.01, limits).update(
        governed.executable_state,
        control_time=0.0,
        current_state=initial,
    )
    ordinary = RuckigFollower(1, 0.01, limits).update(
        governed.executable_state,
        control_time=0.0,
        current_state=initial,
    )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ruckig_version": getattr(ruckig, "__version__", "unknown"),
        "trackig_available": hasattr(ruckig, "Trackig"),
        "tracking_available": hasattr(ruckig, "Tracking"),
        "methods": methods,
        "governor_probe": {
            "jerk": governed.jerk.tolist(),
            "executable_state": governed.executable_state.tolist(),
            "direct_state": direct.command_state.tolist(),
            "ruckig_state": ordinary.command_state.tolist(),
            "free_duration": ordinary.free_trajectory_duration,
            "continuous_violation_count": int(
                np.sum(ordinary.continuous_audit["violation_count"])
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(run_probe(), indent=2, sort_keys=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
