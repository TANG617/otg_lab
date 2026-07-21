from types import SimpleNamespace

import numpy as np
import pytest

from otg_lab.constraints import (
    InvariantViolationError,
    integrate_constant_jerk,
    terminal_stopping_viable,
)
from otg_lab.followers import DirectExecutableFollower, RuckigFollower
from otg_lab.governors import MotionLimits, OneStepBoundedJerkGovernor

DT = 0.01
LIMITS = MotionLimits.broadcast(1, 4.1, 8.2, 4000.0)
ZERO = np.zeros((1, 3))


def _result(command: np.ndarray, jerk: np.ndarray, **kwargs):
    return SimpleNamespace(
        executable_state=np.asarray(command, dtype=float),
        jerk=np.asarray(jerk, dtype=float),
        emergency_mode=kwargs.get("emergency_mode", False),
    )


def _assert_executed_segment(result, current):
    np.testing.assert_allclose(
        result.command_state,
        integrate_constant_jerk(current, result.command_jerk, DT),
        rtol=0.0,
        atol=2e-8,
    )
    assert result.command_segment_feasible
    assert result.command_terminal_viable
    assert result.safety_guarantee
    assert terminal_stopping_viable(result.command_state, LIMITS)


def test_direct_free_duration_rejection_commits_actual_safety_action(monkeypatch):
    follower = DirectExecutableFollower(1, DT, LIMITS)
    candidate = integrate_constant_jerk(ZERO, np.array([100.0]), DT)
    safe_hold = integrate_constant_jerk(ZERO, np.array([0.0]), DT)
    duration_results = iter(
        [(2.0 * DT, False, "working"), (0.0, True, "working")]
    )
    monkeypatch.setattr(
        follower,
        "_free_duration",
        lambda *_args: next(duration_results),
    )
    monkeypatch.setattr(
        follower._fallback,
        "update",
        lambda *_args, **_kwargs: _result(safe_hold, np.array([0.0])),
    )

    result = follower.update(candidate, control_time=0.0, current_state=ZERO)

    assert result.fallback_requested
    assert result.fallback_applied
    assert result.fallback  # deprecated compatibility alias
    assert result.fallback_reason == "free_duration_exceeds_dt"
    assert not result.requested_target_feasible
    assert not np.allclose(result.command_state, candidate)
    _assert_executed_segment(result, ZERO)


def test_direct_nonfinite_target_uses_finite_dynamical_fallback():
    result = DirectExecutableFollower(1, DT, LIMITS).update(
        np.array([[np.nan, np.inf, -np.inf]]),
        control_time=0.0,
        current_state=ZERO,
    )

    assert result.fallback_applied
    assert result.fallback_reason == "nonfinite_target"
    assert np.all(np.isfinite(result.command_state))
    assert np.all(np.isfinite(result.command_jerk))
    _assert_executed_segment(result, ZERO)


def test_formal_direct_follower_fails_closed_on_invalid_fallback(monkeypatch):
    current = np.array([[0.0, 1.0, 0.0]])
    follower = DirectExecutableFollower(1, DT, LIMITS, formal=True)
    monkeypatch.setattr(
        follower._fallback,
        "update",
        lambda *_args, **_kwargs: _result(current, np.array([0.0])),
    )
    monkeypatch.setattr(
        follower,
        "_free_duration",
        lambda *_args: (0.0, True, "working"),
    )

    with pytest.raises(InvariantViolationError, match="fallback failed validation"):
        follower.update(
            np.array([[100.0, 0.0, 0.0]]),
            control_time=0.0,
            current_state=current,
        )


def test_ruckig_exception_commits_same_safe_fallback_semantics(monkeypatch):
    follower = RuckigFollower(1, DT, LIMITS)
    target = np.array([[1.0, 0.0, 0.0]])
    monkeypatch.setattr(
        follower,
        "_calculate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = follower.update(target, control_time=0.0, current_state=ZERO)

    assert result.fallback_requested
    assert result.fallback_applied
    assert result.fallback_reason == "ruckig_exception"
    assert "RuntimeError" in result.solver_status
    _assert_executed_segment(result, ZERO)


def test_normal_followers_sync_fallback_governor_memory():
    governed = OneStepBoundedJerkGovernor(1, DT, LIMITS).update(
        np.array([[0.01, 0.5, 1.0]]),
        control_time=0.0,
        current_state=ZERO,
    )
    for follower in (
        DirectExecutableFollower(1, DT, LIMITS),
        RuckigFollower(1, DT, LIMITS),
    ):
        result = follower.update(
            governed.executable_state,
            control_time=0.0,
            current_state=ZERO,
        )
        assert not result.fallback_applied
        np.testing.assert_allclose(follower._fallback.command_state, result.command_state)
        np.testing.assert_allclose(follower._fallback.last_jerk, result.command_jerk)
        _assert_executed_segment(result, ZERO)


def test_outside_viability_is_explicit_emergency_not_impossible_hold():
    current = np.array([[0.0, 4.2, 0.0]])
    result = DirectExecutableFollower(1, DT, LIMITS).update(
        np.array([[0.0, 0.0, 0.0]]),
        control_time=0.0,
        current_state=current,
    )

    assert result.fallback_applied
    assert result.emergency_mode
    assert not result.safety_guarantee
    np.testing.assert_allclose(
        result.command_state,
        integrate_constant_jerk(current, result.command_jerk, DT),
        rtol=0.0,
        atol=2e-8,
    )
    assert not np.array_equal(result.command_state, current)
