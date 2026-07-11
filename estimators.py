"""Position-only real-time state estimators for a 100 Hz signal."""

from __future__ import annotations

from collections import deque
from time import perf_counter_ns

import numpy as np


def predict_constant_jerk(state, horizon, jerk=0.0):
    """Propagate [position, velocity, acceleration] by a constant jerk."""
    p, v, a = state
    return np.array(
        [
            p + v * horizon + 0.5 * a * horizon**2 + jerk * horizon**3 / 6.0,
            v + a * horizon + 0.5 * jerk * horizon**2,
            a + jerk * horizon,
        ],
        dtype=float,
    )


class StateEstimator:
    """Base class. step(z) returns a state predicted one cycle ahead."""

    name = "estimator"
    delay_ms = 0.0

    def __init__(self, dt, lookahead=None):
        self.dt = float(dt)
        self.lookahead = self.dt if lookahead is None else float(lookahead)
        self.lookahead_ms = 1000.0 * self.lookahead
        self.compute_us = []

    def step(self, position):
        start = perf_counter_ns()
        state = self._step(float(position))
        self.compute_us.append((perf_counter_ns() - start) / 1000.0)
        return np.asarray(state, dtype=float)


class PositionOnly(StateEstimator):
    name = "Position only"

    def _step(self, position):
        # Deliberately keeps the current sample as a stale target baseline.
        return [position, 0.0, 0.0]


class RawBackwardDifference(StateEstimator):
    """The original causal finite differences, intentionally time-misaligned."""

    name = "Raw backward difference (original)"

    def __init__(self, dt):
        super().__init__(dt, lookahead=0.0)
        self.history = deque(maxlen=3)

    def _step(self, position):
        if not self.history:
            self.history.extend([position, position])
        self.history.append(position)
        p0, p1, p2 = self.history
        velocity = (p2 - p1) / self.dt
        acceleration = (p2 - 2.0 * p1 + p0) / self.dt**2
        return [p2, velocity, acceleration]


class CentralDifference10(StateEstimator):
    name = "3-point central (10 ms lag)"
    delay_ms = 10.0

    def __init__(self, dt, lookahead=None):
        super().__init__(dt, lookahead)
        self.name = (
            f"3-point central (10 ms lag, {self.lookahead_ms:.0f} ms lookahead)"
        )
        self.history = deque(maxlen=3)

    def _step(self, position):
        if not self.history:
            self.history.extend([position, position])
        self.history.append(position)
        p0, p1, p2 = self.history
        # State belongs to p1 (one sample behind the newest measurement).
        velocity = (p2 - p0) / (2.0 * self.dt)
        acceleration = (p2 - 2.0 * p1 + p0) / self.dt**2
        # Predict from k-1 to the command time k+1.
        return predict_constant_jerk(
            [p1, velocity, acceleration], self.dt + self.lookahead
        )


class LocalPolynomial(StateEstimator):
    """Fixed-lag local cubic regression (Savitzky-Golay form)."""

    def __init__(self, dt, window, lag, name, lookahead=None):
        super().__init__(dt, lookahead)
        self.name = name
        self.window = int(window)
        self.lag = int(lag)
        self.delay_ms = 1000.0 * lag * dt
        self.name = f"{name} ({self.lookahead_ms:.0f} ms lookahead)"
        self.history = deque(maxlen=window)

        evaluation_index = window - 1 - lag
        offsets = (np.arange(window) - evaluation_index) * dt
        # Coefficients directly represent p, v, a, j at the evaluation time.
        design = np.column_stack(
            (
                np.ones(window),
                offsets,
                0.5 * offsets**2,
                offsets**3 / 6.0,
            )
        )
        self.operator = np.linalg.pinv(design)

    def _step(self, position):
        if not self.history:
            self.history.extend([position] * (self.window - 1))
        self.history.append(position)
        p, v, a, jerk = self.operator @ np.asarray(self.history)
        # Estimate is at k-lag; command target is at k+1.
        horizon = self.lag * self.dt + self.lookahead
        return predict_constant_jerk([p, v, a], horizon, jerk)


class AlphaBetaGamma(StateEstimator):
    name = "Alpha-beta-gamma + 10 ms prediction"

    def __init__(
        self,
        dt,
        alpha=0.401,
        beta=0.11528,
        gamma=0.009504,
        lookahead=None,
    ):
        super().__init__(dt, lookahead)
        self.name = f"Alpha-beta-gamma ({self.lookahead_ms:.0f} ms lookahead)"
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.state = None

    def _step(self, position):
        if self.state is None:
            self.state = np.array([position, 0.0, 0.0])
        else:
            predicted = predict_constant_jerk(self.state, self.dt)
            residual = position - predicted[0]
            predicted[0] += self.alpha * residual
            predicted[1] += self.beta * residual / self.dt
            predicted[2] += 2.0 * self.gamma * residual / self.dt**2
            self.state = predicted
        return predict_constant_jerk(self.state, self.lookahead)


class RobustKalman(StateEstimator):
    name = "Robust CA-KF + 10 ms prediction"

    def __init__(self, dt, measurement_sigma=1e-2, jerk_spectral_density=1000.0, lookahead=None):
        super().__init__(dt, lookahead)
        self.name = f"Robust CA-KF ({self.lookahead_ms:.0f} ms lookahead)"
        self.measurement_variance = float(measurement_sigma) ** 2
        self.jerk_spectral_density = float(jerk_spectral_density)
        self.state = None
        self.covariance = None

        t = self.dt
        self.transition = np.array(
            [[1.0, t, 0.5 * t**2], [0.0, 1.0, t], [0.0, 0.0, 1.0]]
        )
        self.process_noise = self.jerk_spectral_density * np.array(
            [
                [t**5 / 20.0, t**4 / 8.0, t**3 / 6.0],
                [t**4 / 8.0, t**3 / 3.0, t**2 / 2.0],
                [t**3 / 6.0, t**2 / 2.0, t],
            ]
        )
        self.identity = np.eye(3)
        self.outlier_count = 0

    def _step(self, position):
        if self.state is None:
            self.state = np.array([position, 0.0, 0.0])
            self.covariance = np.diag(
                [self.measurement_variance, 0.05**2, 0.5**2]
            )
        else:
            x_pred = self.transition @ self.state
            p_pred = (
                self.transition @ self.covariance @ self.transition.T
                + self.process_noise
            )
            innovation = position - x_pred[0]
            innovation_variance = p_pred[0, 0] + self.measurement_variance

            # Huber-like 3-sigma innovation gate.
            limit = 3.0 * np.sqrt(innovation_variance)
            if abs(innovation) > limit:
                innovation = np.copysign(limit, innovation)
                self.outlier_count += 1

            gain = p_pred[:, 0] / innovation_variance
            self.state = x_pred + gain * innovation
            # Joseph-equivalent scalar measurement covariance update.
            correction = self.identity - np.outer(gain, [1.0, 0.0, 0.0])
            self.covariance = (
                correction @ p_pred @ correction.T
                + np.outer(gain, gain) * self.measurement_variance
            )
            self.covariance = 0.5 * (self.covariance + self.covariance.T)

        return predict_constant_jerk(self.state, self.lookahead)


class JerkLimitedTracker(StateEstimator):
    """A causal third-order tracking differentiator with hard limits."""

    name = "Jerk-limited tracker + 10 ms prediction"

    def __init__(self, dt, max_velocity, max_acceleration, max_jerk, frequency=2.0, lookahead=None):
        super().__init__(dt, lookahead)
        self.name = f"Jerk-limited tracker ({self.lookahead_ms:.0f} ms lookahead)"
        self.max_velocity = float(max_velocity)
        self.max_acceleration = float(max_acceleration)
        self.max_jerk = float(max_jerk)
        self.omega = 2.0 * np.pi * frequency
        self.state = None
        self.last_jerk = 0.0

    def _bounded_jerk(self, requested):
        _, velocity, acceleration = self.state
        low = -self.max_jerk
        high = self.max_jerk

        # Bounds required for next acceleration.
        low = max(low, (-self.max_acceleration - acceleration) / self.dt)
        high = min(high, (self.max_acceleration - acceleration) / self.dt)

        # Bounds required for next velocity under constant jerk integration.
        factor = 2.0 / self.dt**2
        low = max(
            low,
            factor
            * (-self.max_velocity - velocity - acceleration * self.dt),
        )
        high = min(
            high,
            factor
            * (self.max_velocity - velocity - acceleration * self.dt),
        )
        if low > high:
            return 0.0
        return float(np.clip(requested, low, high))

    def _step(self, position):
        if self.state is None:
            self.state = np.array([position, 0.0, 0.0])
        error = position - self.state[0]
        _, velocity, acceleration = self.state
        requested = (
            self.omega**3 * error
            - 3.0 * self.omega**2 * velocity
            - 3.0 * self.omega * acceleration
        )
        jerk = self._bounded_jerk(requested)
        self.state = predict_constant_jerk(self.state, self.dt, jerk)
        self.last_jerk = jerk
        return predict_constant_jerk(self.state, self.lookahead, jerk)


def default_estimators(dt, max_velocity, max_acceleration, max_jerk, lookahead=0.05):
    return [
        PositionOnly(dt),
        RawBackwardDifference(dt),
        CentralDifference10(dt, lookahead),
        LocalPolynomial(dt, 7, 1, "SG-10 cubic + prediction", lookahead),
        LocalPolynomial(dt, 5, 2, "SG-20 cubic + prediction", lookahead),
        # Stable compromise selected by validating the two halves of the CSV.
        AlphaBetaGamma(
            dt,
            alpha=0.401,
            beta=0.11528,
            gamma=0.009504,
            lookahead=0.06,
        ),
        # Keep the conservative CA-KF configuration: the more aggressive
        # full-data optimum over-fitted the first half of the CSV.
        RobustKalman(
            dt,
            measurement_sigma=0.01,
            jerk_spectral_density=1000.0,
            lookahead=0.05,
        ),
        JerkLimitedTracker(
            dt, max_velocity, max_acceleration, max_jerk, lookahead=lookahead
        ),
    ]
