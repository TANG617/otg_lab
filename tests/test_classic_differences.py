from __future__ import annotations

import numpy as np
import pytest

from otg_lab.components import ScheduledStateTargetBuilder
from otg_lab.estimators import make_estimator
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    Trajectory,
)
from otg_lab.predictors import make_predictor
from otg_lab.tracking import run_tracking
from otg_lab.types import Measurement, TimedState


def _measurement(time_s: float, position: float) -> Measurement:
    return Measurement(
        position=[position],
        state_time=time_s,
        available_time=time_s,
    )


def _posterior(time_s: float, position: float) -> TimedState:
    return TimedState(
        [position],
        [0.0],
        [0.0],
        state_time=time_s,
        available_time=time_s,
        method="position_only",
    )


def test_classic_estimator_formulas_startup_and_represented_time() -> None:
    o1 = make_estimator("backward_fd_o1", nominal_dt=1.0)
    o1_rows = [
        o1.update(_measurement(float(index), float(index**2)))
        for index in range(3)
    ]
    assert all(row.startup for row in o1_rows[:2])
    np.testing.assert_allclose(o1_rows[0].as_array(), [[0.0, 0.0, 0.0]])
    assert not o1_rows[-1].startup
    np.testing.assert_allclose(o1_rows[-1].as_array(), [[4.0, 3.0, 2.0]])
    assert o1_rows[-1].state_time == 2.0

    o2 = make_estimator("backward_fd_o2", nominal_dt=1.0)
    o2_rows = [
        o2.update(_measurement(float(index), float(index**2)))
        for index in range(4)
    ]
    assert all(row.startup for row in o2_rows[:3])
    np.testing.assert_allclose(o2_rows[-1].as_array(), [[9.0, 6.0, 2.0]])
    assert o2_rows[-1].state_time == 3.0

    centered = make_estimator("centered_fd_o2_delay1", nominal_dt=1.0)
    centered_rows = [
        centered.update(_measurement(float(index), float(index**2)))
        for index in range(3)
    ]
    assert centered_rows[-1].state_time == 1.0
    assert centered_rows[-1].available_time == 2.0
    np.testing.assert_allclose(
        centered_rows[-1].as_array(),
        [[1.0, 2.0, 2.0]],
    )


def test_future_backward_predictor_formulas_and_causality() -> None:
    o1 = make_predictor("future_backward_fd_o1", nominal_dt=1.0)
    o1_rows = [
        o1.predict(_posterior(float(index), float(index**2)), 1.0)
        for index in range(3)
    ]
    assert all(row.startup for row in o1_rows[:2])
    result = o1_rows[-1]
    assert result.state_time == 3.0
    assert result.available_time == 2.0
    assert result.causal
    np.testing.assert_allclose(result.as_array(), [[9.0, 5.0, 2.0]])

    o2 = make_predictor("future_backward_fd_o2", nominal_dt=1.0)
    o2_rows = [
        o2.predict(_posterior(float(index), float(index**3)), 1.0)
        for index in range(4)
    ]
    result = o2_rows[-1]
    assert result.state_time == 4.0
    assert result.available_time == 3.0
    assert result.causal
    np.testing.assert_allclose(result.as_array(), [[64.0, 37.0, 24.0]])


def test_scheduled_target_uses_only_position_schedule_and_keeps_one_time() -> None:
    times = 0.1 * np.arange(5)
    trajectory = Trajectory(
        sample_index=np.arange(5),
        time_s=times,
        position_rad=np.square(times),
        velocity_rad_s=2.0 * times,
        acceleration_rad_s2=np.full(5, 2.0),
        jerk_rad_s3=np.zeros(5),
        nominal_dt_s=0.1,
    )
    prediction = TimedState(
        [999.0],
        [0.4],
        [2.0],
        state_time=0.3,
        available_time=0.2,
        source_state_time=0.2,
        prediction_horizon=0.1,
        method="future_backward_fd_o2",
        causal=True,
        metadata={"predictor": "future_backward_fd_o2"},
    )
    future_builder = ScheduledStateTargetBuilder(
        trajectory,
        components="pva",
        time_source="prediction_time",
    )
    future = future_builder.build(prediction)
    assert future.state_time == pytest.approx(0.3)
    np.testing.assert_allclose(future.as_array(), [[0.09, 0.4, 2.0]])
    assert future.metadata["position_source"] == "reference_schedule"

    altered_truth = Trajectory(
        sample_index=np.arange(5),
        time_s=times,
        position_rad=np.square(times),
        velocity_rad_s=np.full(5, 1.0e6),
        acceleration_rad_s2=np.full(5, -1.0e6),
        jerk_rad_s3=np.zeros(5),
        nominal_dt_s=0.1,
    )
    altered_future = ScheduledStateTargetBuilder(
        altered_truth,
        components="pva",
        time_source="prediction_time",
    ).build(prediction)
    np.testing.assert_allclose(altered_future.as_array(), future.as_array())

    held_prediction = prediction.with_updates(
        position=[0.04],
        state_time=0.3,
        source_state_time=0.2,
        method="zoh",
        metadata={
            "model": "zero_order_hold",
            "predictor": "zoh",
            "posterior_method": "backward_fd_o1",
        },
    )
    source_builder = ScheduledStateTargetBuilder(
        trajectory,
        components="pv",
        time_source="source_state_time",
    )
    held = source_builder.build(held_prediction)
    assert held.state_time == pytest.approx(0.2)
    assert held.available_time == pytest.approx(0.2)
    np.testing.assert_allclose(held.as_array(), [[0.04, 0.4, 0.0]])
    assert held.metadata["derivative_source"] == "backward_fd_o1"


@pytest.mark.parametrize(
    ("estimator_id", "sample_count"),
    (("backward_fd_o1", 3), ("backward_fd_o2", 4)),
)
def test_estimators_are_immune_to_unseen_future_samples(
    estimator_id: str,
    sample_count: int,
) -> None:
    first = make_estimator(estimator_id, nominal_dt=1.0)
    second = make_estimator(estimator_id, nominal_dt=1.0)
    shared = [float(index**2) for index in range(sample_count)]
    first_result = None
    second_result = None
    for index, position in enumerate(shared):
        first_result = first.update(_measurement(float(index), position))
        second_result = second.update(_measurement(float(index), position))
    assert first_result is not None and second_result is not None

    # Different P[k+1:] values exist conceptually, but neither instance receives
    # them before the compared output is produced.
    unseen_future_a = [1.0e6, 2.0e6]
    unseen_future_b = [-1.0e6, -2.0e6]
    assert unseen_future_a != unseen_future_b
    np.testing.assert_allclose(first_result.as_array(), second_result.as_array())


@pytest.mark.parametrize(
    ("predictor_id", "sample_count"),
    (("future_backward_fd_o1", 3), ("future_backward_fd_o2", 4)),
)
def test_predictors_are_immune_to_unseen_future_samples(
    predictor_id: str,
    sample_count: int,
) -> None:
    first = make_predictor(predictor_id, nominal_dt=1.0)
    second = make_predictor(predictor_id, nominal_dt=1.0)
    shared = [float(index**3) for index in range(sample_count)]
    first_result = None
    second_result = None
    for index, position in enumerate(shared):
        posterior = _posterior(float(index), position)
        first_result = first.predict(posterior, 1.0)
        second_result = second.predict(posterior, 1.0)
    assert first_result is not None and second_result is not None

    unseen_future_a = [1.0e6, 2.0e6]
    unseen_future_b = [-1.0e6, -2.0e6]
    assert unseen_future_a != unseen_future_b
    np.testing.assert_allclose(first_result.as_array(), second_result.as_array())


def _tracking_reference() -> Trajectory:
    times = 0.01 * np.arange(12)
    return Trajectory(
        sample_index=np.arange(times.size),
        time_s=times,
        position_rad=0.2 * np.square(times),
        velocity_rad_s=0.4 * times,
        acceleration_rad_s2=np.full(times.size, 0.4),
        jerk_rad_s3=np.zeros(times.size),
        nominal_dt_s=0.01,
    )


def _method(
    method_id: str,
    estimator: str,
    predictor: ComponentSpec,
    time_source: str,
) -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id=method_id,
        estimator=ComponentSpec(estimator),
        predictor=predictor,
        target_builder=ComponentSpec(
            "scheduled_state",
            {"components": "pva", "time_source": time_source},
        ),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
    )


def test_tracking_trace_audits_target_age_and_noncausal_oracle() -> None:
    reference = _tracking_reference()
    config = RunConfig(
        limits=MotionLimits(10.0, 100.0, 10000.0),
        minimum_duration_s=0.01,
        prediction_horizon_s=0.01,
        measurement_policy="position_only",
        dt_s=0.01,
    )
    methods = {
        "estimator": _method(
            "estimator",
            "backward_fd_o1",
            ComponentSpec("zero_order_hold"),
            "source_state_time",
        ),
        "centered": _method(
            "centered",
            "centered_fd_o2_delay1",
            ComponentSpec("zero_order_hold"),
            "source_state_time",
        ),
        "predictor": _method(
            "predictor",
            "position_only",
            ComponentSpec("future_backward_fd_o2"),
            "prediction_time",
        ),
        "oracle": _method(
            "oracle",
            "position_only",
            ComponentSpec("oracle", {"noncausal_diagnostic": True}),
            "prediction_time",
        ),
    }
    expected_age = {
        "estimator": 1.0,
        "centered": 2.0,
        "predictor": 0.0,
        "oracle": 0.0,
    }
    for method_id, method in methods.items():
        run = run_tracking(reference, method, config)
        assert run.status.completed, run.status.failure_reason
        assert run.command is not None
        assert run.command.sample_count == reference.sample_count - 1
        mature = next(
            row for row in run.trace_rows if not row["raw_target_startup"]
        )
        assert mature["raw_target_age_samples"] == pytest.approx(
            expected_age[method_id]
        )
        target_time = float(mature["raw_target_time_s"])
        expected_position = np.interp(
            target_time,
            reference.time_s,
            reference.position_rad,
        )
        assert mature["raw_target_position_rad"] == pytest.approx(
            expected_position
        )
        assert (
            mature["raw_target_causal"] is (method_id != "oracle")
        )
        assert (
            mature["prediction_offline_only"] is (method_id == "oracle")
        )
