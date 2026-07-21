"""Tests for explicit physical clocks and layer separation."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from otg_lab.estimators import DelayOneCenteredDifference, PositionOnly
from otg_lab.followers import DirectExecutableFollower
from otg_lab.governors import MotionLimits
from otg_lab.pipeline import EstimatorPredictorPipeline, TrackingPipeline
from otg_lab.predictors import ConstantAccelerationPredictor
from otg_lab.runner import run_pipeline_rows
from otg_lab.schema import empty_sample
from otg_lab.types import Measurement, TimedState, state_from_array


def test_measurement_normalizes_ndof_and_owns_immutable_data():
    source = np.array([1.0, 2.0, 3.0])
    measurement = Measurement(
        source,
        state_time=1.0,
        available_time=1.004,
        velocity=[0.1, 0.2, 0.3],
    )
    source[:] = 99.0
    np.testing.assert_array_equal(measurement.position, [1.0, 2.0, 3.0])
    assert measurement.position.shape == (3,)
    assert measurement.dof == 3
    assert measurement.source_time == 1.0
    assert measurement.arrival_time == 1.004
    with pytest.raises(ValueError):
        measurement.position[0] = 5.0
    with pytest.raises(ValueError, match="cannot precede"):
        Measurement([0.0], state_time=1.0, available_time=0.99)


def test_timed_state_distinguishes_posterior_from_prediction():
    posterior = TimedState(
        [1.0, 2.0],
        [0.1, 0.2],
        [0.0, 0.0],
        state_time=2.0,
        available_time=2.01,
    )
    assert not posterior.is_prediction
    assert posterior.prediction_time is None
    assert posterior.lag == pytest.approx(0.01)

    prediction = posterior.with_updates(
        state_time=2.06,
        source_state_time=2.0,
        prediction_horizon=0.06,
    )
    assert prediction.is_prediction
    assert prediction.prediction_time == pytest.approx(2.06)
    assert prediction.available_time == pytest.approx(2.01)
    with pytest.raises(ValueError, match="timestamp mismatch"):
        posterior.with_updates(
            state_time=2.05,
            source_state_time=2.0,
            prediction_horizon=0.06,
        )


def test_state_from_array_uses_dof_by_component_layout():
    state = state_from_array(
        [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]],
        state_time=0.2,
        available_time=0.21,
    )
    assert state.dof == 2
    np.testing.assert_array_equal(
        state.as_array(include_jerk=True),
        [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]],
    )


def test_delayed_posterior_is_propagated_to_absolute_target_time():
    front_end = EstimatorPredictorPipeline(
        DelayOneCenteredDifference(0.01),
        ConstantAccelerationPredictor(),
        prediction_horizon=0.02,
    )
    cycles = []
    for index in range(3):
        state_time = index * 0.01
        cycles.append(
            front_end.process(
                Measurement(
                    [state_time**2],
                    state_time=state_time,
                    available_time=state_time,
                ),
                control_time=state_time,
            )
        )
    cycle = cycles[-1]
    assert cycle.posterior.state_time == pytest.approx(0.01)
    assert cycle.posterior.available_time == pytest.approx(0.02)
    assert cycle.posterior_lag == pytest.approx(0.01)
    assert cycle.target_time == pytest.approx(0.04)
    assert cycle.prediction.prediction_time == pytest.approx(0.04)
    assert cycle.prediction.available_time == pytest.approx(0.02)
    # Configured H=20 ms plus the estimator's explicit 10 ms lag.
    assert cycle.requested_horizon == pytest.approx(0.02)
    assert cycle.propagation_horizon == pytest.approx(0.03)
    assert cycle.prediction.metadata["requested_horizon"] == pytest.approx(0.02)


def test_front_end_rejects_ambiguous_or_prearrival_timing():
    front_end = EstimatorPredictorPipeline(
        PositionOnly(0.01),
        ConstantAccelerationPredictor(),
    )
    measurement = Measurement([0.0], state_time=1.0, available_time=1.01)
    with pytest.raises(ValueError, match="cannot precede"):
        front_end.process(measurement, control_time=1.0)
    with pytest.raises(ValueError, match="not both"):
        front_end.process(
            measurement,
            prediction_horizon=0.02,
            target_time=1.03,
        )


@dataclass
class _DummyFollower:
    dof: int = 1
    dt: float = 0.01
    minimum_duration: float = 0.01
    last_target: np.ndarray | None = None

    def reset(self, state):
        self.last_target = np.asarray(state, dtype=float).copy()

    def update(self, target, *, control_time, current_state=None):
        self.last_target = np.asarray(target, dtype=float).copy()
        return SimpleNamespace(
            command_state=self.last_target,
            command_jerk=np.zeros(self.dof),
            command_time=control_time + self.dt,
            solver_status="dummy",
            fallback=False,
            fallback_reason="",
            target_projected=False,
            free_trajectory_duration=self.dt,
            frozen_trajectory_duration=self.minimum_duration,
            compute_us=1.0,
            continuous_audit={},
        )


@dataclass
class _DummyGovernor:
    dof: int = 1
    dt: float = 0.01
    current: np.ndarray | None = None

    def reset(self, state=None):
        self.current = None if state is None else np.asarray(state).copy()

    def update(self, raw_target, *, control_time, current_state=None):
        target = np.asarray(raw_target, dtype=float).copy()
        self.current = target
        return SimpleNamespace(
            executable_state=target,
            jerk=np.zeros(self.dof),
            target_time=control_time + self.dt,
            target_feasible=True,
            target_projected=False,
            fallback=False,
            fallback_reason="",
            solver_status="dummy_governor",
            iterations=0,
            compute_us=2.0,
            distortion=np.zeros_like(target),
        )


@dataclass
class _DummyPlant:
    dof: int = 1
    dt: float = 0.01
    state: np.ndarray | None = None

    def reset(self, state):
        self.state = np.asarray(state).copy()

    def update(self, command_state, *, command_time):
        self.state = np.asarray(command_state, dtype=float).copy()
        return SimpleNamespace(
            true_state=self.state,
            measured_state=self.state,
            state_time=command_time,
            available_time=command_time + 0.002,
            saturated=np.zeros(self.dof, dtype=bool),
            delayed_command_age=0.0,
            compute_us=3.0,
            status="dummy_plant",
        )


def test_prediction_horizon_never_changes_follower_minimum_duration():
    follower = _DummyFollower()
    pipeline = TrackingPipeline(
        PositionOnly(0.01),
        ConstantAccelerationPredictor(),
        follower,
        dof=1,
        dt=0.01,
        prediction_horizon=0.15,
        target_components="p",
    )
    pipeline.reset(np.zeros((1, 3)))
    cycle = pipeline.step(Measurement([0.2], state_time=0.0, available_time=0.0))
    assert cycle.executable_target is None
    assert cycle.prediction.prediction_time == pytest.approx(0.15)
    assert cycle.command.state_time == pytest.approx(0.01)
    assert follower.minimum_duration == pytest.approx(0.01)
    assert (
        "minimum_duration"
        not in inspect.signature(EstimatorPredictorPipeline).parameters
    )
    assert "minimum_duration" not in inspect.signature(TrackingPipeline).parameters


def test_tracking_facade_matches_authoritative_no_governor_fallback_semantics():
    limits = MotionLimits.broadcast(1, 4.1, 8.2, 4000.0)
    facade = TrackingPipeline(
        PositionOnly(0.01),
        ConstantAccelerationPredictor(),
        DirectExecutableFollower(1, 0.01, limits, formal=True),
        dof=1,
        dt=0.01,
        target_components="pva",
    )
    facade.reset(np.zeros((1, 3)))
    cycle = facade.step(
        Measurement([0.2], state_time=0.0, available_time=0.0),
        control_time=0.0,
    )

    row = empty_sample(
        run_id="tracking-wrapper-equivalence",
        dataset_id="unit",
        session_id="unit",
        trajectory_id="one-cycle",
        split="development",
        seed=1,
        joint_id="joint_0",
        k=0,
        source_time=0.0,
        arrival_time=0.0,
        control_time=0.0,
        dt_actual=0.01,
        dt_control=0.01,
        p_ref=0.0,
        p_meas=0.2,
        source_kind="unit_test",
        scenario_id="clean",
        truth_available=False,
        measurement_available=True,
        measurement_valid=True,
    )
    config = {
        "formal": True,
        "seed": 1,
        "limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
        "control": {"dt": 0.01, "minimum_duration": 0.01},
        "pipeline": {
            "estimator": "position_only",
            "estimator_parameters": {},
            "predictor": "constant_acceleration",
            "predictor_parameters": {},
            "prediction_horizon_ms": 0.0,
            "target_mode": "pva",
            "governor": "none",
            "governor_parameters": {},
            "follower": "direct",
            "plant": "ideal",
            "plant_parameters": {},
            "measured_state_mode": "previous_command",
        },
    }
    canonical = run_pipeline_rows([row], config).rows[0]

    assert cycle.executable_target is None
    assert canonical["executable_target_available"] is False
    np.testing.assert_allclose(
        cycle.command.as_array()[0],
        [canonical["command_p"], canonical["command_v"], canonical["command_a"]],
        rtol=0.0,
        atol=1e-14,
    )
    assert cycle.command.metadata["fallback_applied"] == canonical["fallback_applied"]
    assert cycle.command.valid is canonical["safety_guarantee"] is True


def test_full_pipeline_preserves_target_command_and_plant_times():
    pipeline = TrackingPipeline(
        PositionOnly(dt=0.01),
        ConstantAccelerationPredictor(),
        _DummyFollower(),
        dof=1,
        dt=0.01,
        prediction_horizon=0.04,
        governor=_DummyGovernor(),
        plant=_DummyPlant(),
    )
    pipeline.reset(np.zeros((1, 3)), state_time=1.0)
    cycle = pipeline.step(
        Measurement([0.2], state_time=1.0, available_time=1.003),
        control_time=1.005,
    )
    assert cycle.posterior.state_time == pytest.approx(1.0)
    assert cycle.posterior.available_time == pytest.approx(1.003)
    assert cycle.prediction.prediction_time == pytest.approx(1.045)
    assert cycle.prediction.available_time == pytest.approx(1.005)
    assert cycle.raw_target.state_time == pytest.approx(1.045)
    assert cycle.executable_target.state_time == pytest.approx(1.015)
    assert cycle.command.state_time == pytest.approx(1.015)
    assert cycle.plant_state.state_time == pytest.approx(1.015)
    assert cycle.plant_state.available_time == pytest.approx(1.017)
    assert cycle.governor_compute_us == 2.0
    assert cycle.follower_compute_us == 1.0
    assert cycle.plant_compute_us == 3.0
    assert cycle.total_compute_us == pytest.approx(
        cycle.estimator_compute_us + cycle.predictor_compute_us + 6.0
    )


def test_component_ablation_preserves_position_and_timestamps():
    for mode, expected_velocity, expected_acceleration in (
        ("p", 0.0, 0.0),
        ("pv", 2.06, 0.0),
        ("pva", 2.06, 3.0),
    ):
        front_end = EstimatorPredictorPipeline(
            PositionOnly(0.01),
            ConstantAccelerationPredictor(),
            prediction_horizon=0.02,
            target_components=mode,
        )
        # Replace the position-only posterior with a direct synthetic one to
        # isolate component selection from estimator behavior.
        posterior = TimedState(
            [1.0],
            [2.0],
            [3.0],
            state_time=0.0,
            available_time=0.0,
        )
        prediction = front_end.predictor.predict(posterior, 0.02)
        from otg_lab.predictors import select_target_components

        target = select_target_components(prediction, mode)
        assert target.position[0] == pytest.approx(1.0406)
        assert target.velocity[0] == pytest.approx(expected_velocity)
        assert target.acceleration[0] == pytest.approx(expected_acceleration)
        assert target.state_time == prediction.state_time
        assert target.source_state_time == prediction.source_state_time
