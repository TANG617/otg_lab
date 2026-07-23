from types import SimpleNamespace

import numpy as np
import pytest

import otg_lab.followers as followers
from otg_lab.constraints import integrate_constant_jerk
from otg_lab.followers import (
    RuckigFollower,
    _command_checks,
    _trajectory_boundaries,
    audit_ruckig_prefix,
)
from otg_lab.governors import MotionLimits
from otg_lab.multidof import generate_multidof_truth, multidof_to_rows
from otg_lab.runner import run_pipeline_rows

DT = 0.01
LIMITS = MotionLimits.broadcast(1, 4.1, 8.2, 4000.0)
ZERO = np.zeros((1, 3))


def test_ruckig_prefix_keeps_switching_profile_and_native_endpoint():
    follower = RuckigFollower(1, DT, LIMITS)
    result = follower.update(
        np.array([[1.0, 0.0, 0.0]]),
        control_time=0.0,
        current_state=ZERO,
    )

    assert not result.fallback_applied
    assert result.native_command_executed
    assert result.command_profile_kind == "ruckig_piecewise_constant_jerk"
    assert result.command_profile_exact
    assert result.command_profile_segment_count == 2
    assert result.command_profile_boundary_count == 1
    assert result.command_constant_jerk_exact is None
    profile = result.command_profile
    assert profile is not None
    assert profile.segment_boundaries[1] == pytest.approx(0.00205)
    np.testing.assert_allclose(
        result.command_state,
        np.column_stack(follower.frozen_trajectory.at_time(DT)),
        rtol=0.0,
        atol=2e-8,
    )
    np.testing.assert_allclose(
        result.command_state, profile.evaluate(DT), rtol=0.0, atol=2e-8
    )
    assert result.command_endpoint_matches_profile
    assert result.command_profile_continuous_constraints_satisfied

    acceleration_difference_jerk = (result.command_state[:, 2] - ZERO[:, 2]) / DT
    constant_endpoint = integrate_constant_jerk(ZERO, acceleration_difference_jerk, DT)
    assert not np.allclose(constant_endpoint, result.command_state, atol=2e-8)
    reachable, *_rest = _command_checks(
        ZERO, result.command_state, acceleration_difference_jerk, DT, LIMITS
    )
    assert not reachable
    assert result.command_first_jerk[0] == pytest.approx(4000.0)
    assert result.command_last_jerk[0] == pytest.approx(0.0)
    assert result.command_internal_max_abs_jerk[0] == pytest.approx(4000.0)
    assert result.continuous_audit["max_internal_jerk"][0] == pytest.approx(4000.0)
    assert result.continuous_audit["acceleration_difference_jerk"][0] == pytest.approx(
        820.0
    )


@pytest.mark.parametrize(
    "boundary",
    [5e-13, 1e-6, DT / 2.0, DT - 1e-6, DT - 5e-13],
)
def test_prefix_audit_includes_adversarial_switch_boundaries(boundary):
    limits = MotionLimits.broadcast(1, 100.0, 40.0, 4000.0)
    current = np.array([[0.0, 0.0, 40.0 - 4000.0 * boundary]])
    follower = RuckigFollower(1, DT, limits)
    result = follower.update(
        np.array([[10.0, 0.0, 0.0]]),
        control_time=0.0,
        current_state=current,
    )

    assert not result.fallback_applied
    profile = result.command_profile
    assert profile is not None
    assert np.any(
        np.abs(profile.segment_boundaries[1:-1] - boundary) <= 5e-15
    )
    audit = audit_ruckig_prefix(follower.frozen_trajectory, limits, start=0.0, end=DT)
    assert audit["profile_exact"]
    assert audit["endpoint_matches_profile"]
    audit_boundaries = np.asarray(audit["segment_boundaries"])
    assert np.any(np.abs(audit_boundaries[1:-1] - boundary) <= 5e-15)
    assert np.sum(audit["violation_count"]) == 0
    assert audit["max_internal_jerk"][0] == pytest.approx(4000.0)


def test_unshielded_failure_is_not_hidden_and_shield_is_explicit(monkeypatch):
    target = np.array([[1.0, 0.0, 0.0]])
    unshielded = RuckigFollower(1, DT, LIMITS)
    monkeypatch.setattr(
        unshielded,
        "_calculate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="unshielded method failure"):
        unshielded.update(target, control_time=0.0, current_state=ZERO)

    shielded = RuckigFollower(1, DT, LIMITS, safety_shield=True)
    monkeypatch.setattr(
        shielded,
        "_calculate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = shielded.update(target, control_time=0.0, current_state=ZERO)
    assert result.safety_shield_requested
    assert result.safety_shield_applied
    assert result.safety_shield_reason == "ruckig_exception"
    assert not result.native_command_executed
    assert result.fallback_changes_algorithm
    assert result.command_profile_kind == "constant_jerk"
    assert result.command_constant_jerk_exact


def test_unshielded_native_execution_uses_explicit_sampled_profile(monkeypatch):
    """A binding without exposed segments must not activate a hidden shield."""

    monkeypatch.setattr(
        followers,
        "_extract_ruckig_command_profile",
        lambda *_args, **_kwargs: None,
    )
    follower = RuckigFollower(1, DT, LIMITS)
    result = follower.update(
        np.array([[1.0, 0.0, 0.0]]),
        control_time=0.0,
        current_state=ZERO,
    )

    assert result.native_command_executed
    assert not result.fallback_requested
    assert not result.fallback_applied
    assert result.command_profile_kind == "ruckig_piecewise_constant_jerk"
    assert not result.command_profile_exact
    assert result.command_profile_segment_count == 0
    assert result.command_endpoint_matches_profile
    assert result.command_profile_continuous_constraints_satisfied
    assert result.command_first_jerk is None
    assert result.command_last_jerk is None
    assert result.command_internal_max_abs_jerk is None
    assert np.isnan(result.command_jerk).all()
    assert np.isnan(result.continuous_audit["max_internal_jerk"]).all()
    assert np.isfinite(result.continuous_audit["max_sampled_jerk"]).all()
    assert (
        result.continuous_audit["audit_method"]
        == "sampled_ruckig_grid_with_boundaries"
    )
    np.testing.assert_allclose(
        result.command_state,
        result.command_profile.evaluate(DT),
        rtol=0.0,
        atol=2e-8,
    )


def test_sampled_prefix_counts_only_internal_boundaries(monkeypatch):
    follower = RuckigFollower(1, DT, LIMITS)
    follower.update(
        np.array([[1.0, 0.0, 0.0]]),
        control_time=0.0,
        current_state=ZERO,
    )
    monkeypatch.setattr(
        followers,
        "_extract_ruckig_command_profile",
        lambda *_args, **_kwargs: None,
    )

    audit = audit_ruckig_prefix(
        follower.frozen_trajectory,
        LIMITS,
        start=0.001,
        end=0.01,
    )

    np.testing.assert_allclose(
        audit["segment_boundaries"],
        [0.0, 0.00105, 0.009],
        rtol=0.0,
        atol=2e-12,
    )
    assert audit["boundary_count"] == 1


def test_boundary_extraction_offsets_later_sections():
    first = SimpleNamespace(brake=None, t=[0.03, 0.07], accel=None)
    second = SimpleNamespace(brake=None, t=[0.04, 0.16], accel=None)
    trajectory = SimpleNamespace(
        duration=0.3,
        profiles=[[first], [second]],
    )

    np.testing.assert_allclose(
        _trajectory_boundaries(trajectory),
        [0.0, 0.03, 0.1, 0.14, 0.3],
        rtol=0.0,
        atol=1e-15,
    )


def test_random_ruckig_prefix_profiles_reconstruct_native_samples():
    rng = np.random.default_rng(1701)
    follower = RuckigFollower(1, DT, LIMITS)
    for index in range(40):
        current = np.array(
            [
                [
                    rng.uniform(-1.0, 1.0),
                    rng.uniform(-3.5, 3.5),
                    rng.uniform(-7.0, 7.0),
                ]
            ]
        )
        target = np.array([[rng.uniform(-1.5, 1.5), 0.0, 0.0]])
        result = follower.update(
            target,
            control_time=index * DT,
            current_state=current,
        )

        assert result.native_command_executed
        assert not result.fallback_applied
        assert result.command_profile_exact
        for sample_time in np.linspace(0.0, DT, 7):
            native = np.column_stack(
                follower.frozen_trajectory.at_time(float(sample_time))
            )
            np.testing.assert_allclose(
                result.command_profile.evaluate(float(sample_time)),
                native,
                rtol=0.0,
                atol=2e-8,
            )


def test_runner_serializes_sampled_native_profile_without_fake_jerk(monkeypatch):
    monkeypatch.setattr(
        followers,
        "_extract_ruckig_command_profile",
        lambda *_args, **_kwargs: None,
    )
    truth = generate_multidof_truth(
        1,
        "different_frequency",
        seed=909,
        duration=0.04,
        internal_dt=0.001,
    )
    rows = multidof_to_rows(
        truth,
        sample_rate_hz=100.0,
        run_id="sampled-ruckig-profile-test",
    )
    config = {
        "seed": 909,
        "limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
        "control": {"dt": DT, "minimum_duration": DT},
        "pipeline": {
            "method_id": "sampled-ordinary-ruckig",
            "method_family": "ordinary_ruckig_unshielded",
            "estimator": "position_only",
            "estimator_parameters": {},
            "predictor": "zero_order_hold",
            "predictor_parameters": {},
            "prediction_horizon_ms": 0.0,
            "target_mode": "p",
            "governor": "none",
            "governor_parameters": {},
            "follower": "ruckig",
            "follower_parameters": {"safety_shield": False},
            "plant": "ideal",
            "plant_parameters": {},
            "measured_state_mode": "previous_command",
        },
    }

    result = run_pipeline_rows(rows, config)

    assert result.rows
    assert all(row["native_command_executed"] is True for row in result.rows)
    assert all(row["fallback_applied"] is False for row in result.rows)
    assert all(row["command_profile_exact"] is False for row in result.rows)
    assert all(row["command_endpoint_matches_profile"] is True for row in result.rows)
    assert all(row["command_jerk"] is None for row in result.rows)
    assert all(row["command_first_jerk"] is None for row in result.rows)
    assert all(row["command_last_jerk"] is None for row in result.rows)
    assert all(
        row["command_internal_max_abs_jerk"] is None for row in result.rows
    )
    assert all(
        row["command_profile_segment_jerks_json"] is None for row in result.rows
    )
