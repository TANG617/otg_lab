"""E01: standalone scheduled P[k+1] baseline for E03--E06."""

from __future__ import annotations

from pathlib import Path

from otg_lab.experiment import ExperimentSpec
from otg_lab.trajectory_ablation import build_p_only_baseline


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    return build_p_only_baseline()
