"""Numerical, causal, startup, reset, and invalid-input estimator tests."""

from __future__ import annotations

from functools import partial

import numpy as np
import pytest

from otg_lab.estimators import (
    ESTIMATOR_METHOD_IDS,
    AlphaBetaGamma,
    CausalLocalPolynomial,
    ConstantAccelerationKalmanFilter,
    ConstantJerkKalmanFilter,
    DelayOneCenteredDifference,
    JerkLimitedDifferentiator,
    LegacyEstimatorAdapter,
    NonFiniteMeasurementError,
    PositionOnly,
    RawBackwardDifference,
    RobustCAKalmanFilter,
    TimestampError,
    default_estimator_suite,
    make_estimator,
)
from otg_lab.predictors import (
    PREDICTOR_METHOD_IDS,
    ConstantAccelerationPredictor,
    ConstantJerkPredictor,
    ConstantVelocityPredictor,
    LocalPolynomialPredictor,
    OraclePredictor,
    PredictorError,
    ZeroOrderHoldPredictor,
    make_predictor,
    predict_constant_jerk,
)
from otg_lab.types import Measurement, TimedState


def _measurement(position, time, delay=0.001):
    return Measurement(position, state_time=time, available_time=time + delay)


def _run(factory, positions, times):
    estimator = factory()
    return [
        estimator.update(_measurement(position, time))
        for position, time in zip(positions, times)
    ]


ESTIMATOR_FACTORIES = [
    partial(PositionOnly, 0.01),
    partial(RawBackwardDifference, 0.01),
    partial(DelayOneCenteredDifference, 0.01),
    partial(CausalLocalPolynomial, 0.01, window=5, degree=2, lag_samples=1),
    partial(CausalLocalPolynomial, 0.01, window=7, degree=3, lag_samples=2),
    partial(AlphaBetaGamma, 0.01),
    partial(ConstantAccelerationKalmanFilter, 0.01),
    partial(RobustCAKalmanFilter, 0.01),
    partial(ConstantJerkKalmanFilter, 0.01),
    partial(JerkLimitedDifferentiator, 0.01),
]


@pytest.mark.parametrize("factory", ESTIMATOR_FACTORIES)
def test_estimators_are_ndof_causal_and_accept_variable_dt(factory):
    times = np.array([0.0, 0.009, 0.021, 0.032, 0.044, 0.055])
    positions = np.column_stack((np.sin(times), 2.0 * times**2, -times))
    outputs = _run(factory, positions, times)
    assert len(outputs) == times.size
    for output, measurement_time in zip(outputs, times):
        assert output.dof == 3
        assert output.position.shape == (3,)
        assert output.state_time <= measurement_time + 1e-15
        assert output.available_time == pytest.approx(measurement_time + 0.001)
        assert not output.is_prediction
        assert output.is_finite
        assert output.compute_time_us >= 0.0


@pytest.mark.parametrize("factory", ESTIMATOR_FACTORIES)
def test_future_mutation_does_not_change_any_output_through_cutoff(factory):
    times = np.arange(24) * 0.01
    positions = np.column_stack(
        (
            np.sin(4.0 * times),
            0.2 * times**2 - 0.1 * times,
        )
    )
    cutoff = 13
    mutated = positions.copy()
    mutated[cutoff + 1 :] = np.array([1000.0, -2000.0])
    baseline = _run(factory, positions, times)
    changed = _run(factory, mutated, times)
    for expected, actual in zip(
        baseline[: cutoff + 1],
        changed[: cutoff + 1],
    ):
        np.testing.assert_array_equal(actual.position, expected.position)
        np.testing.assert_array_equal(actual.velocity, expected.velocity)
        np.testing.assert_array_equal(actual.acceleration, expected.acceleration)
        if expected.jerk is None:
            assert actual.jerk is None
        else:
            np.testing.assert_array_equal(actual.jerk, expected.jerk)
        assert actual.state_time == expected.state_time
        assert actual.available_time == expected.available_time
        assert actual.status == expected.status


def test_raw_backward_difference_uses_actual_nonuniform_timestamps():
    times = np.array([0.0, 0.008, 0.021, 0.037])
    position = 0.3 - 0.7 * times + 0.5 * 2.4 * times**2
    outputs = _run(lambda: RawBackwardDifference(0.01), position[:, None], times)
    output = outputs[-1]
    expected_velocity = -0.7 + 2.4 * (times[-2] + times[-1]) / 2.0
    assert output.velocity[0] == pytest.approx(expected_velocity, abs=1e-12)
    assert output.acceleration[0] == pytest.approx(2.4, abs=1e-12)
    assert output.metadata["velocity_time"] == pytest.approx(
        (times[-2] + times[-1]) / 2.0
    )


def test_centered_difference_has_one_sample_availability_delay():
    times = np.array([0.0, 0.009, 0.023])
    position = 0.2 + 1.5 * times + 0.5 * 3.0 * times**2
    estimator = DelayOneCenteredDifference(0.01)
    outputs = [
        estimator.update(_measurement([value], time))
        for value, time in zip(position, times)
    ]
    posterior = outputs[-1]
    assert posterior.state_time == pytest.approx(times[1])
    assert posterior.available_time == pytest.approx(times[2] + 0.001)
    assert posterior.velocity[0] == pytest.approx(1.5 + 3.0 * times[1])
    assert posterior.acceleration[0] == pytest.approx(3.0)
    assert posterior.metadata["lag_seconds"] == pytest.approx(times[2] - times[1])


@pytest.mark.parametrize("window", [5, 7, 9, 11])
@pytest.mark.parametrize("degree", [2, 3])
def test_local_polynomial_grid_has_explicit_lag_and_polynomial_accuracy(window, degree):
    lag = 2
    times = np.arange(window + 2) * 0.01
    position = 0.4 - 0.5 * times + 0.5 * 1.8 * times**2
    estimator = CausalLocalPolynomial(
        0.01,
        window=window,
        degree=degree,
        lag_samples=lag,
    )
    output = None
    for value, time in zip(position, times):
        output = estimator.update(_measurement([value], time))
    assert output is not None
    expected_time = times[-1 - lag]
    assert output.state_time == pytest.approx(expected_time)
    assert output.position[0] == pytest.approx(
        0.4 - 0.5 * expected_time + 0.9 * expected_time**2,
        abs=2e-12,
    )
    assert output.velocity[0] == pytest.approx(
        -0.5 + 1.8 * expected_time,
        abs=2e-10,
    )
    assert output.acceleration[0] == pytest.approx(1.8, abs=2e-9)
    assert output.metadata["configured_lag_samples"] == lag


def test_reset_restores_startup_and_deterministic_first_result():
    estimator = AlphaBetaGamma(0.01)
    first = estimator.update(_measurement([0.3, -0.2], 0.0))
    estimator.update(_measurement([0.4, -0.1], 0.01))
    estimator.reset()
    repeated = estimator.update(_measurement([0.3, -0.2], 0.0))
    np.testing.assert_array_equal(repeated.as_array(), first.as_array())
    assert repeated.startup
    assert estimator.sample_count == 1


def test_nonfinite_hold_is_explicit_and_does_not_advance_state_time():
    estimator = ConstantAccelerationKalmanFilter(0.01, nonfinite_policy="hold")
    with pytest.raises(NonFiniteMeasurementError):
        estimator.update(_measurement([np.nan], 0.0))
    first = estimator.update(_measurement([0.2], 0.0))
    held = estimator.update(_measurement([np.inf], 0.01))
    assert held.status == "nonfinite_hold"
    assert not held.valid
    assert held.state_time == first.state_time
    assert held.available_time == pytest.approx(0.011)
    np.testing.assert_array_equal(held.as_array(), first.as_array())
    recovered = estimator.update(_measurement([0.3], 0.02))
    assert recovered.state_time == pytest.approx(0.02)
    assert recovered.valid


def test_component_hold_and_timestamp_policies_are_observable():
    estimator = PositionOnly(0.01, nonfinite_policy="component_hold")
    estimator.update(_measurement([1.0, 2.0], 0.0))
    repaired = estimator.update(_measurement([np.nan, 3.0], 0.01))
    np.testing.assert_array_equal(repaired.position, [1.0, 3.0])
    assert repaired.status == "nonfinite_component_hold"
    assert not repaired.valid
    assert repaired.metadata["invalid_position_mask"] == [True, False]

    strict = PositionOnly(0.01)
    strict.update(_measurement([0.0], 1.0))
    with pytest.raises(TimestampError):
        strict.update(_measurement([1.0], 1.0))

    resetting = PositionOnly(0.01, timestamp_policy="reset")
    resetting.update(_measurement([0.0], 1.0))
    reset_output = resetting.update(_measurement([1.0], 0.5, delay=1.0))
    assert reset_output.status == "timestamp_reset"
    assert reset_output.position[0] == 1.0


def test_fixed_dt_mode_rejects_variable_interval():
    estimator = RawBackwardDifference(0.01, allow_variable_dt=False)
    estimator.update(_measurement([0.0], 0.0))
    with pytest.raises(TimestampError, match="fixed-dt"):
        estimator.update(_measurement([1.0], 0.012))


def test_robust_kf_records_outlier_without_nonfinite_fallback():
    estimator = RobustCAKalmanFilter(
        0.01,
        measurement_sigma=1e-3,
        innovation_sigma_limit=2.0,
    )
    for index in range(5):
        estimator.update(_measurement([0.0], index * 0.01))
    output = estimator.update(_measurement([10.0], 0.05))
    assert output.metadata["outlier_mask"] == [True]
    assert estimator.outlier_count[0] == 1
    assert output.valid


def test_constant_jerk_kf_and_tracking_differentiator_return_bounded_jerk():
    cj = ConstantJerkKalmanFilter(0.01)
    tracker = JerkLimitedDifferentiator(
        0.01,
        max_velocity=[1.0, 2.0],
        max_acceleration=[2.0, 3.0],
        max_jerk=[10.0, 20.0],
        frequency_hz=4.0,
    )
    for index in range(20):
        time = index * 0.01
        values = [0.5 * time**3, (-1) ** index]
        cj_output = cj.update(_measurement(values, time))
        tracker_output = tracker.update(_measurement(values, time))
    assert cj_output.jerk is not None
    assert tracker_output.jerk is not None
    assert np.all(np.abs(tracker_output.jerk) <= [10.0, 20.0])
    assert np.all(
        np.abs(tracker_output.velocity) <= [1.0, 2.0] + np.array([1e-12, 1e-12])
    )
    assert np.all(
        np.abs(tracker_output.acceleration) <= [2.0, 3.0] + np.array([1e-12, 1e-12])
    )


def _posterior():
    return TimedState(
        [1.0, -2.0],
        [2.0, 3.0],
        [4.0, -5.0],
        jerk=[6.0, -7.0],
        state_time=1.0,
        available_time=1.01,
        method="test_posterior",
    )


def test_analytic_predictors_and_sequence_timestamps():
    posterior = _posterior()
    horizon = 0.2
    expected_cj = predict_constant_jerk(
        posterior.as_array(),
        horizon,
        posterior.jerk,
    )
    predictors = [
        ZeroOrderHoldPredictor(),
        ConstantVelocityPredictor(),
        ConstantAccelerationPredictor(),
        ConstantJerkPredictor(),
    ]
    outputs = [predictor.predict(posterior, horizon) for predictor in predictors]
    np.testing.assert_array_equal(outputs[0].as_array(), posterior.as_array())
    np.testing.assert_allclose(
        outputs[1].position,
        posterior.position + horizon * posterior.velocity,
    )
    np.testing.assert_allclose(
        outputs[2].position,
        posterior.position
        + horizon * posterior.velocity
        + 0.5 * horizon**2 * posterior.acceleration,
    )
    np.testing.assert_allclose(outputs[3].as_array(), expected_cj)
    for output in outputs:
        assert output.source_state_time == posterior.state_time
        assert output.prediction_horizon == pytest.approx(horizon)
        assert output.prediction_time == pytest.approx(1.2)
        assert output.available_time == posterior.available_time
        assert output.is_prediction

    sequence = ConstantAccelerationPredictor().predict_sequence(
        posterior,
        [0.0, 0.01, 0.06],
    )
    assert [item.prediction_time for item in sequence] == pytest.approx(
        [1.0, 1.01, 1.06]
    )


def test_constant_jerk_missing_policy_is_explicit():
    posterior = _posterior().with_updates(jerk=None)
    fallback = ConstantJerkPredictor().predict(posterior, 0.1)
    assert fallback.status == "missing_jerk_zero"
    assert fallback.metadata["fallback"]
    with pytest.raises(PredictorError, match="no jerk"):
        ConstantJerkPredictor(missing_jerk="raise").predict(posterior, 0.1)


def test_local_polynomial_predictor_fits_only_observed_history():
    predictor = LocalPolynomialPredictor(window=7, degree=3)
    times = np.arange(7) * 0.01

    def polynomial(t):
        return 0.2 - 0.4 * t + 0.7 * t**2 - 0.3 * t**3

    for time in times:
        posterior = TimedState(
            [polynomial(time)],
            [0.0],
            [0.0],
            state_time=time,
            available_time=time,
        )
        predictor.predict(posterior, 0.0)
    future = predictor.predict(posterior, 0.04)
    assert future.position[0] == pytest.approx(polynomial(times[-1] + 0.04), abs=1e-11)
    assert future.metadata["samples_used"] == 7


def test_oracle_is_unambiguously_offline_and_queries_correct_future_time():
    times = np.arange(0.0, 1.01, 0.01)
    position = np.column_stack((times**2, -(times**2)))
    velocity = np.column_stack((2.0 * times, -2.0 * times))
    acceleration = np.tile([2.0, -2.0], (times.size, 1))
    oracle = OraclePredictor(times, position, velocity, acceleration)
    posterior = TimedState(
        [0.04, -0.04],
        [0.4, -0.4],
        [2.0, -2.0],
        state_time=0.2,
        available_time=0.21,
    )
    prediction = oracle.predict(posterior, 0.15)
    np.testing.assert_allclose(prediction.position, [0.35**2, -(0.35**2)])
    assert prediction.prediction_time == pytest.approx(0.35)
    assert not prediction.causal
    assert prediction.status == "offline_oracle"
    assert prediction.metadata["offline_only"]

    with pytest.raises(ValueError, match="velocity"):
        OraclePredictor(times, position, None, acceleration)
    with pytest.raises(PredictorError, match="outside"):
        oracle.predict(posterior, 2.0)


def test_legacy_adapter_refuses_to_hide_future_prediction():
    class Legacy:
        lookahead = 0.05
        dt = 0.01

        def step(self, position):
            return [position, 0.0, 0.0]

    with pytest.raises(ValueError, match="future prediction"):
        LegacyEstimatorAdapter(Legacy())

    Legacy.lookahead = 0.0
    adapter = LegacyEstimatorAdapter(Legacy())
    posterior = adapter.update(_measurement([0.2], 0.0))
    assert not posterior.is_prediction
    np.testing.assert_array_equal(posterior.as_array(), [[0.2, 0.0, 0.0]])


def test_factories_expose_stable_ids_and_complete_required_suite():
    assert "ca_kf" in ESTIMATOR_METHOD_IDS
    assert set(PREDICTOR_METHOD_IDS) == {
        "zero_order_hold",
        "constant_velocity",
        "constant_acceleration",
        "constant_jerk",
        "local_polynomial",
        "oracle",
    }
    assert isinstance(
        make_estimator("ca_kf", dt=0.01), ConstantAccelerationKalmanFilter
    )
    local = make_estimator("local_polynomial_w9_d3_lag2", dt=0.01)
    assert isinstance(local, CausalLocalPolynomial)
    assert (local.window, local.degree, local.lag_samples) == (9, 3, 2)
    assert isinstance(make_predictor("constant_velocity"), ConstantVelocityPredictor)
    with pytest.raises(ValueError, match="lookahead is forbidden"):
        make_estimator("ca_kf", dt=0.01, lookahead=0.05)
    with pytest.raises(ValueError, match="belongs to a Predictor"):
        make_estimator("ca_kf", dt=0.01, prediction_horizon=0.05)
    suite = default_estimator_suite(0.01)
    assert len(suite) == 16
    assert len({estimator.name for estimator in suite}) == len(suite)
