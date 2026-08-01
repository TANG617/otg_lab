from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

PAPER_ROOT = Path(__file__).resolve().parents[1]


def vcrit(T: float, A: float, J: float) -> float:
    if A >= J * T / 4:
        return J * T**2 / 32
    return A * T / 4 - A**2 / (2 * J)


def test_dimensionless_branches_are_continuous() -> None:
    T, J = 0.01, 4000.0
    A = J * T / 4
    expected = J * T**2 / 32
    assert math.isclose(vcrit(T, A, J), expected, rel_tol=0, abs_tol=1e-15)
    below = vcrit(T, A * (1 - 1e-8), J)
    assert math.isclose(below, expected, rel_tol=1e-8)


def test_critical_speed_is_monotone_in_limits_and_period() -> None:
    assert vcrit(0.01, 5.0, 4000.0) <= vcrit(0.01, 8.2, 4000.0)
    assert vcrit(0.01, 8.2, 1000.0) <= vcrit(0.01, 8.2, 4000.0)
    assert vcrit(0.005, 8.2, 4000.0) <= vcrit(0.01, 8.2, 4000.0)


def test_provisional_profile_maps_all_claims_and_is_release_blocked() -> None:
    profile = yaml.safe_load((PAPER_ROOT / "evidence" / "provisional.yaml").read_text())
    assert set(profile["claims"]) == {f"C{index}" for index in range(1, 14)}
    assert profile["release_ready"] is False
    assert profile["generated_from_clean_git"] is False
    assert all(source["git_dirty"] for source in profile["sources"])


def test_generated_summary_preserves_core_negative_results() -> None:
    summary = json.loads((PAPER_ROOT / "generated" / "artifact_summary.json").read_text())
    values = summary["values"]
    assert values["e15_seam"] == 16
    assert values["e17_work_conditions"] == 11
    assert values["e17_stress_conditions"] == 6
    assert values["e17_stress_median"] < 0.5
    assert values["e17_stress_minimum"] < 0
    assert values["recorded_local_poly_rmse"] > values["recorded_p_rmse"]
    assert values["deadline_miss_count"] == 1


def test_recorded_deadband_changes_no_nonzero_target() -> None:
    values = json.loads((PAPER_ROOT / "generated" / "artifact_summary.json").read_text())["values"]
    assert values["recorded_zero_target_count"] == 2
    assert values["recorded_min_nonzero_target"] > 1e-10
