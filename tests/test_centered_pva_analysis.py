import numpy as np
import pytest

from otg_lab.centered_pva_analysis import (
    build_ablation_targets,
    centered_difference_nonuniform,
)
from target_state_experiment import MotionLimits

LIMITS = MotionLimits(4.1, 8.2, 4000.0)


def test_nonuniform_centered_difference_recovers_quadratic_at_center():
    timestamps = np.array([0.0, 0.01, 0.025, 0.04])
    position = timestamps**2 + 3.0 * timestamps + 0.7

    estimate = centered_difference_nonuniform(position, timestamps)

    assert estimate.valid.tolist() == [False, False, True, True]
    assert estimate.center_index.tolist() == [-1, -1, 1, 2]
    assert estimate.velocity[2] == pytest.approx(3.02, abs=1e-12)
    assert estimate.acceleration[2] == pytest.approx(2.0, abs=1e-11)
    assert estimate.velocity[3] == pytest.approx(3.05, abs=1e-12)
    assert estimate.acceleration[3] == pytest.approx(2.0, abs=1e-11)


def test_equal_spacing_reduces_to_standard_centered_formula():
    timestamps = np.arange(5, dtype=float) * 0.01
    position = np.array([0.0, 0.001, 0.004, 0.009, 0.016])

    estimate = centered_difference_nonuniform(position, timestamps)

    assert estimate.velocity[2] == pytest.approx(
        (position[2] - position[0]) / 0.02
    )
    assert estimate.acceleration[2] == pytest.approx(
        (position[2] - 2.0 * position[1] + position[0]) / 0.01**2
    )


def test_large_gap_resets_history_and_requires_three_new_samples():
    timestamps = np.array([0.0, 0.01, 0.02, 0.10, 0.11, 0.12])
    position = timestamps**2

    estimate = centered_difference_nonuniform(
        position,
        timestamps,
        max_sample_interval_s=0.05,
    )

    assert estimate.valid.tolist() == [False, False, True, False, False, True]
    assert estimate.center_index[5] == 4


def test_production_like_target_is_middle_state_with_independent_clamps():
    timestamps = np.arange(5, dtype=float) * 0.01
    position = np.array([0.0, 0.001, 0.004, 0.009, 0.016])

    built = build_ablation_targets(
        position,
        timestamps,
        "centered_pva_delayed_clamped",
        LIMITS,
    )

    # Arrival index 2 emits the state evaluated at position index 1.
    assert built.states[2, 0] == pytest.approx(position[1])
    assert built.preclamp_states[2, 1] == pytest.approx(0.2)
    assert built.preclamp_states[2, 2] == pytest.approx(20.0)
    assert built.states[2, 1] == pytest.approx(0.2)
    assert built.states[2, 2] == pytest.approx(8.2)
    assert not built.velocity_clamp_mask[2]
    assert built.acceleration_clamp_mask[2]
    assert built.target_age_s[2] == pytest.approx(0.01)


def test_latest_position_ablation_removes_only_position_delay():
    timestamps = np.arange(5, dtype=float) * 0.01
    position = np.array([0.0, 0.001, 0.004, 0.009, 0.016])

    delayed = build_ablation_targets(
        position,
        timestamps,
        "centered_pva_delayed_clamped",
        LIMITS,
    )
    latest = build_ablation_targets(
        position,
        timestamps,
        "centered_pva_latest_position_clamped",
        LIMITS,
    )

    assert delayed.states[2, 0] == pytest.approx(position[1])
    assert latest.states[2, 0] == pytest.approx(position[2])
    np.testing.assert_allclose(delayed.states[2:, 1:], latest.states[2:, 1:])


def test_propagated_ablation_advances_center_velocity_by_one_interval():
    timestamps = np.arange(5, dtype=float) * 0.01
    position = timestamps**2

    centered = build_ablation_targets(
        position,
        timestamps,
        "centered_pva_latest_position_clamped",
        LIMITS,
    )
    propagated = build_ablation_targets(
        position,
        timestamps,
        "centered_pva_propagated_clamped",
        LIMITS,
    )

    assert propagated.states[2, 0] == pytest.approx(position[2])
    assert propagated.preclamp_states[2, 1] == pytest.approx(
        centered.preclamp_states[2, 1]
        + centered.preclamp_states[2, 2] * 0.01
    )
