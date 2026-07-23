#!/usr/bin/env python3
"""Run the same deterministic compatibility probes in an isolated interpreter."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import numpy as np
import ruckig

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_lab.followers import DirectExecutableFollower, RuckigFollower  # noqa: E402
from otg_lab.governors import (  # noqa: E402
    MotionLimits,
    OneStepBoundedJerkGovernor,
)
from otg_runner import (  # noqa: E402
    PHASE_A_FIXED_GRID_DT,
    PHASE_A_FIXED_GRID_LIMITS,
    run_phase_a_p_only_compatibility,
    run_target_state_sequence,
)
from target_state_experiment import (  # noqa: E402
    VENDOR_LIMITS,
    build_target_states,
    csv_reference,
    elementary_references,
)


def run_probe(plot_data_path: str | Path = "plot_data.csv") -> dict:
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
    recorded = csv_reference(plot_data_path)
    phase_a = run_phase_a_p_only_compatibility(
        recorded.position,
        original_count=recorded.original_count,
    )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ruckig_version": getattr(ruckig, "__version__", "unknown"),
        "trackig_available": hasattr(ruckig, "Trackig"),
        "tracking_available": hasattr(ruckig, "Tracking"),
        "methods": methods,
        "phase_a_p_only_ordinary_ruckig": {
            "dt": PHASE_A_FIXED_GRID_DT,
            "limits": PHASE_A_FIXED_GRID_LIMITS,
            "minimum_duration": PHASE_A_FIXED_GRID_DT,
            "target": "[p[k], 0, 0]",
            "target_timing": phase_a["target_timing"],
            "current_state_feedback": "previous_native_ruckig_output",
            "metrics": phase_a["compatibility_metrics"],
            "acceptance_criteria": phase_a["acceptance_criteria"],
        },
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
    parser.add_argument(
        "--output",
        type=Path,
        help="write JSON to this path; omit to emit JSON on stdout",
    )
    parser.add_argument("--plot-data", type=Path, default=Path("plot_data.csv"))
    args = parser.parse_args()
    payload = json.dumps(run_probe(args.plot_data), indent=2, sort_keys=True)
    if args.output is None:
        print(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
