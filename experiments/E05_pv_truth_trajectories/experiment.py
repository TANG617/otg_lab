"""E05: PV[k+1] analytic truth versus the scheduled P[k+1] baseline."""

from __future__ import annotations

from pathlib import Path

from otg_lab.experiment import ExperimentSpec
from otg_lab.trajectory_ablation import build_trajectory_ablation


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    return build_trajectory_ablation(
        "E05",
        components="pv",
        include_differences=False,
    )
