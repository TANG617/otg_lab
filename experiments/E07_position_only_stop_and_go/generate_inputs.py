"""Generate E07's experiment-local constant-velocity inputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from otg_lab.csvio import write_trajectory_csv
from otg_lab.models import Trajectory, TrajectoryMetadata

DT_S = 0.01
DURATION_S = 3.0
VENDOR_CRITICAL_VELOCITY_RAD_S = 0.012095
VENDOR_VELOCITY_RATIOS = (
    0.125,
    0.2,
    0.25,
    0.3,
    0.4,
    0.5,
    0.6,
    0.8,
    0.9,
    0.95,
    1.0,
    1.05,
    1.1,
    1.2,
    1.5,
    1.8,
    2.0,
    2.2,
    3.0,
    4.0,
)


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def generate(project_root: Path) -> tuple[Path, ...]:
    output_directory = (
        project_root
        / "experiments"
        / "E07_position_only_stop_and_go"
        / "inputs"
    )
    sample_count = int(round(DURATION_S / DT_S)) + 1
    time_s = np.arange(sample_count, dtype=np.float64) * DT_S
    written: list[Path] = []
    for ratio in VENDOR_VELOCITY_RATIOS:
        input_id = f"e07_cv_vendor_ratio_{_token(ratio)}"
        velocity = ratio * VENDOR_CRITICAL_VELOCITY_RAD_S
        trajectory = Trajectory(
            sample_index=np.arange(sample_count, dtype=np.int64),
            time_s=time_s,
            position_rad=velocity * time_s,
            velocity_rad_s=np.full(
                sample_count,
                velocity,
                dtype=np.float64,
            ),
            acceleration_rad_s2=np.zeros(
                sample_count,
                dtype=np.float64,
            ),
            jerk_rad_s3=np.zeros(sample_count, dtype=np.float64),
            nominal_dt_s=DT_S,
        )
        metadata = TrajectoryMetadata.for_trajectory(
            trajectory,
            trajectory_id=input_id,
            channel_semantics={
                "position_rad": "analytic_truth",
                "velocity_rad_s": "analytic_truth",
                "acceleration_rad_s2": "analytic_truth",
                "jerk_rad_s3": "analytic_truth",
            },
            source={
                "type": "deterministic_e07_constant_velocity_generator",
                "script": (
                    "experiments/E07_position_only_stop_and_go/"
                    "generate_inputs.py"
                ),
            },
            generator_id="e07_constant_velocity",
            generator_params={
                "dt_s": DT_S,
                "duration_s": DURATION_S,
                "reference_velocity_rad_s": velocity,
                "vendor_critical_velocity_rad_s": (
                    VENDOR_CRITICAL_VELOCITY_RAD_S
                ),
                "vendor_velocity_ratio": ratio,
            },
        )
        output = output_directory / f"{input_id}.csv"
        write_trajectory_csv(output, trajectory, metadata)
        written.append(output)
    return tuple(written)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    for path in generate(root):
        print(path.relative_to(root))
