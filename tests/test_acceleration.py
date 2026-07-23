from __future__ import annotations

import numpy as np
import pytest

from otg_lab.acceleration import acceleration_case_matrix, generate_acceleration_case
from otg_lab.benchmarks import ACCELERATION_ACTIVE_PHASES, DEFAULT_RATIO_STRATA


def test_acceleration_case_has_high_resolution_truth_and_target_ratios():
    case = generate_acceleration_case(
        "high_jerk_feasible", "high", "near_limit", 1
    )
    ratios = case.trajectory.demand_ratios()
    assert case.trajectory.internal_dt <= 0.0005 + 1e-15
    assert np.isclose(ratios["r_a"], DEFAULT_RATIO_STRATA["high"], rtol=2e-3)
    assert np.isclose(
        ratios["r_j"], DEFAULT_RATIO_STRATA["near_limit"], rtol=2e-3
    )
    assert ratios["r_v"] < 1.0


def test_acceleration_matrix_is_complete_and_keeps_both_directions():
    cases = acceleration_case_matrix()
    expected = len(ACCELERATION_ACTIVE_PHASES) * len(DEFAULT_RATIO_STRATA) ** 2 * 2
    assert len(cases) == expected
    keys = {
        (case.phase, case.r_a_stratum, case.r_j_stratum, case.direction)
        for case in cases
    }
    assert len(keys) == expected


def test_acceleration_matrix_obeys_and_validates_config_order():
    cases = acceleration_case_matrix(
        phases=tuple(reversed(ACCELERATION_ACTIVE_PHASES)),
        r_a_values=[0.93, 0.75, 0.50, 0.20],
        r_j_values=[0.20, 0.50, 0.75, 0.93],
        directions=[1, -1],
    )
    assert cases[0].phase == ACCELERATION_ACTIVE_PHASES[-1]
    assert cases[0].r_a_stratum == "near_limit"
    assert cases[0].direction == 1
    with pytest.raises(ValueError, match="prevalidated"):
        acceleration_case_matrix(r_a_values=[0.21])
    with pytest.raises(ValueError, match="all declared phases"):
        acceleration_case_matrix(phases=["constant_acceleration"])
