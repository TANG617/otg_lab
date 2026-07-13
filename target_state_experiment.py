"""Reference data and target-state construction for controlled Ruckig ablations.

The formal experiments in this module deliberately keep the control period,
target timestamp, planning duration, and motion limits fixed.  Only the target
components (p / pv / pva) and derivative source are changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


DT = 0.01
DURATION = 3.0
SETTLE_TIME = 2.0
SINE_AMPLITUDE = 0.37


@dataclass(frozen=True)
class MotionLimits:
    max_velocity: float
    max_acceleration: float
    max_jerk: float

    def as_dict(self):
        return {
            "max_velocity": self.max_velocity,
            "max_acceleration": self.max_acceleration,
            "max_jerk": self.max_jerk,
        }


VENDOR_LIMITS = MotionLimits(4.1, 8.2, 4000.0)


@dataclass(frozen=True)
class ReferenceTrajectory:
    dataset: str
    title: str
    time: np.ndarray
    position: np.ndarray
    velocity: np.ndarray | None
    acceleration: np.ndarray | None
    original_count: int
    dt: float = DT

    @property
    def has_analytic_truth(self):
        return self.velocity is not None and self.acceleration is not None


@dataclass(frozen=True)
class MethodSpec:
    method_id: str
    label: str
    target_components: str
    derivative_source: str
    causal: bool
    future_samples: int
    warmup_samples: int
    native_delay_samples: int = 0
    result_group: str = "core"


METHODS = (
    MethodSpec("p", "P", "p", "none", True, 0, 0),
    MethodSpec(
        "pv_truth",
        "PV · analytic truth",
        "pv",
        "analytic_truth",
        True,
        0,
        0,
    ),
    MethodSpec(
        "pva_truth",
        "PVA · analytic truth",
        "pva",
        "analytic_truth",
        True,
        0,
        0,
    ),
    MethodSpec(
        "pv_backward",
        "PV · historical backward FD",
        "pv",
        "backward_fd",
        True,
        0,
        2,
    ),
    MethodSpec(
        "pva_backward",
        "PVA · historical backward FD",
        "pva",
        "backward_fd",
        True,
        0,
        2,
    ),
    MethodSpec(
        "pv_central_offline",
        "PV · centered FD (offline)",
        "pv",
        "centered_fd_offline",
        False,
        1,
        1,
    ),
    MethodSpec(
        "pva_central_offline",
        "PVA · centered FD (offline)",
        "pva",
        "centered_fd_offline",
        False,
        1,
        1,
    ),
    MethodSpec(
        "pv_central_causal",
        "PV · centered FD (causal delay-1)",
        "pv",
        "centered_fd_causal_delay1",
        True,
        0,
        2,
        native_delay_samples=1,
        result_group="realtime_supplement",
    ),
    MethodSpec(
        "pva_central_causal",
        "PVA · centered FD (causal delay-1)",
        "pva",
        "centered_fd_causal_delay1",
        True,
        0,
        2,
        native_delay_samples=1,
        result_group="realtime_supplement",
    ),
)

METHOD_BY_ID = {method.method_id: method for method in METHODS}


def _append_settle(position, velocity, acceleration, dt, settle_time):
    settle_count = int(round(settle_time / dt))
    settled_position = np.concatenate(
        (position, np.full(settle_count, position[-1], dtype=float))
    )
    if velocity is None:
        settled_velocity = None
        settled_acceleration = None
    else:
        settled_velocity = np.concatenate(
            (velocity, np.zeros(settle_count, dtype=float))
        )
        settled_acceleration = np.concatenate(
            (acceleration, np.zeros(settle_count, dtype=float))
        )
    time = np.arange(settled_position.size, dtype=float) * dt
    return time, settled_position, settled_velocity, settled_acceleration


def elementary_references(
    dt=DT,
    duration=DURATION,
    settle_time=SETTLE_TIME,
    sine_amplitude=SINE_AMPLITUDE,
):
    """Generate analytic p/v/a for three stationary-endpoint references."""
    sample_time = np.arange(0.0, duration + dt / 2.0, dt)
    tau = sample_time / duration
    h = 35.0 * tau**4 - 84.0 * tau**5 + 70.0 * tau**6 - 20.0 * tau**7
    dh = 140.0 * tau**3 - 420.0 * tau**4 + 420.0 * tau**5 - 140.0 * tau**6
    ddh = 420.0 * tau**2 - 1680.0 * tau**3 + 2100.0 * tau**4 - 840.0 * tau**5

    parameter = duration * h
    parameter_velocity = dh
    parameter_acceleration = ddh / duration
    centered = parameter - duration / 2.0
    omega = 2.0 * np.pi / duration

    curve_definitions = {
        "quadratic_with_extremum": (
            0.5 * centered**2,
            centered,
            np.ones_like(centered),
            "7th-order time-scaled quadratic",
        ),
        "cubic": (
            0.12 * centered**3,
            0.36 * centered**2,
            0.72 * centered,
            "7th-order time-scaled cubic",
        ),
        "sine": (
            sine_amplitude * np.sin(omega * parameter),
            sine_amplitude * omega * np.cos(omega * parameter),
            -sine_amplitude * omega**2 * np.sin(omega * parameter),
            "7th-order time-scaled sine",
        ),
    }

    references = {}
    original_count = sample_time.size
    for dataset, (position, df_ds, d2f_ds2, title) in curve_definitions.items():
        velocity = df_ds * parameter_velocity
        acceleration = (
            d2f_ds2 * parameter_velocity**2
            + df_ds * parameter_acceleration
        )
        time, position, velocity, acceleration = _append_settle(
            np.asarray(position, dtype=float),
            np.asarray(velocity, dtype=float),
            np.asarray(acceleration, dtype=float),
            dt,
            settle_time,
        )
        references[dataset] = ReferenceTrajectory(
            dataset=dataset,
            title=title,
            time=time,
            position=position,
            velocity=velocity,
            acceleration=acceleration,
            original_count=original_count,
            dt=dt,
        )
    return references


def csv_reference(path, dt=DT, settle_time=SETTLE_TIME):
    """Load only CSV ``value`` and assign one fixed control period per row."""
    path = Path(path)
    values = np.genfromtxt(path, delimiter=",", names=True)["value"]
    values = np.atleast_1d(values).astype(float)
    if values.size < 4 or not np.all(np.isfinite(values)):
        raise ValueError(f"{path} must contain at least 4 finite values")
    original_count = values.size
    time, position, _, _ = _append_settle(
        values,
        None,
        None,
        dt,
        settle_time,
    )
    return ReferenceTrajectory(
        dataset="csv",
        title="Recorded joint position (fixed 10 ms per row)",
        time=time,
        position=position,
        velocity=None,
        acceleration=None,
        original_count=original_count,
        dt=dt,
    )


def backward_finite_difference(position, dt=DT):
    """Historical causal backward differences with mixed timestamps."""
    position = np.asarray(position, dtype=float)
    if position.ndim != 1 or position.size < 3:
        raise ValueError("position must be a one-dimensional array of length >= 3")
    velocity = np.zeros_like(position)
    acceleration = np.zeros_like(position)
    velocity[1:] = np.diff(position) / dt
    # Repeat the initial sample as the unavailable p[-1], matching the
    # historical causal initialization.
    acceleration[1] = (position[1] - position[0]) / dt**2
    acceleration[2:] = (
        position[2:] - 2.0 * position[1:-1] + position[:-2]
    ) / dt**2
    return velocity, acceleration


def centered_finite_difference_offline(position, dt=DT):
    """Timestamp-aligned three-point centered differences (noncausal)."""
    position = np.asarray(position, dtype=float)
    if position.ndim != 1 or position.size < 4:
        raise ValueError("position must be a one-dimensional array of length >= 4")
    velocity = np.gradient(position, dt, edge_order=2)
    acceleration = np.empty_like(position)
    acceleration[1:-1] = (
        position[2:] - 2.0 * position[1:-1] + position[:-2]
    ) / dt**2
    acceleration[0] = (
        2.0 * position[0]
        - 5.0 * position[1]
        + 4.0 * position[2]
        - position[3]
    ) / dt**2
    acceleration[-1] = (
        2.0 * position[-1]
        - 5.0 * position[-2]
        + 4.0 * position[-3]
        - position[-4]
    ) / dt**2
    return velocity, acceleration


def centered_finite_difference_causal(position, dt=DT):
    """Delay-one centered estimate propagated from t[k-1] to t[k]."""
    position = np.asarray(position, dtype=float)
    if position.ndim != 1 or position.size < 3:
        raise ValueError("position must be a one-dimensional array of length >= 3")
    velocity = np.zeros_like(position)
    acceleration = np.zeros_like(position)
    for index in range(2, position.size):
        lagged_velocity = (position[index] - position[index - 2]) / (2.0 * dt)
        lagged_acceleration = (
            position[index]
            - 2.0 * position[index - 1]
            + position[index - 2]
        ) / dt**2
        velocity[index] = lagged_velocity + lagged_acceleration * dt
        acceleration[index] = lagged_acceleration
    return velocity, acceleration


def derivative_sources(reference):
    sources = {}
    if reference.has_analytic_truth:
        sources["analytic_truth"] = (
            reference.velocity.copy(),
            reference.acceleration.copy(),
        )
    sources["backward_fd"] = backward_finite_difference(
        reference.position, reference.dt
    )
    sources["centered_fd_offline"] = centered_finite_difference_offline(
        reference.position, reference.dt
    )
    sources["centered_fd_causal_delay1"] = centered_finite_difference_causal(
        reference.position, reference.dt
    )
    return sources


def methods_for_reference(reference, include_realtime_supplement=True):
    methods = []
    for method in METHODS:
        if method.derivative_source == "analytic_truth" and not reference.has_analytic_truth:
            continue
        if method.result_group == "realtime_supplement" and not include_realtime_supplement:
            continue
        methods.append(method)
    return tuple(methods)


def build_target_states(reference, method):
    """Build raw [p, v, a] targets without limit projection or scaling."""
    if isinstance(method, str):
        method = METHOD_BY_ID[method]
    target = np.zeros((reference.position.size, 3), dtype=float)
    target[:, 0] = reference.position
    if method.target_components == "p":
        return target

    sources = derivative_sources(reference)
    if method.derivative_source not in sources:
        raise ValueError(
            f"{method.derivative_source} is unavailable for {reference.dataset}"
        )
    velocity, acceleration = sources[method.derivative_source]
    target[:, 1] = velocity
    if method.target_components == "pva":
        target[:, 2] = acceleration
    return target


def build_next_cycle_oracle(reference):
    """Return x[k+1] analytic targets for indexing/semantics validation."""
    if not reference.has_analytic_truth:
        raise ValueError("next-cycle oracle requires analytic derivatives")
    state = np.column_stack(
        (reference.position, reference.velocity, reference.acceleration)
    )
    return np.vstack((state[1:], state[-1]))


def derivative_quality_metrics(references):
    """Compare derivative sources to analytic truth on a common interior."""
    rows = []
    source_metadata = {}
    for method in METHODS:
        if method.derivative_source == "none":
            continue
        metadata = (
            method.causal,
            method.future_samples,
            method.native_delay_samples,
        )
        previous = source_metadata.setdefault(method.derivative_source, metadata)
        if previous != metadata:
            raise RuntimeError(
                f"inconsistent metadata for {method.derivative_source}: "
                f"{previous} vs {metadata}"
            )
    for reference in references.values():
        if not reference.has_analytic_truth:
            continue
        sources = derivative_sources(reference)
        # Common interior removes all method-specific startup and endpoint rules.
        start = 3
        stop = reference.original_count - 2
        truth_velocity = reference.velocity[start:stop]
        truth_acceleration = reference.acceleration[start:stop]
        for source, (velocity, acceleration) in sources.items():
            velocity_error = velocity[start:stop] - truth_velocity
            acceleration_error = acceleration[start:stop] - truth_acceleration
            causal, future_samples, native_delay_samples = source_metadata[source]
            rows.append(
                {
                    "dataset": reference.dataset,
                    "derivative_source": source,
                    "causal": causal,
                    "future_samples": future_samples,
                    "native_delay_samples": native_delay_samples,
                    "evaluation_start_index": start,
                    "evaluation_stop_index_exclusive": stop,
                    "velocity_rmse": float(
                        np.sqrt(np.mean(velocity_error**2))
                    ),
                    "velocity_bias": float(np.mean(velocity_error)),
                    "velocity_max_error": float(np.max(np.abs(velocity_error))),
                    "acceleration_rmse": float(
                        np.sqrt(np.mean(acceleration_error**2))
                    ),
                    "acceleration_bias": float(np.mean(acceleration_error)),
                    "acceleration_max_error": float(
                        np.max(np.abs(acceleration_error))
                    ),
                }
            )
    return rows


def reference_peak_metrics(reference):
    if not reference.has_analytic_truth:
        raise ValueError("reference peaks require analytic derivatives")
    original = slice(0, reference.original_count)
    sampled_jerk = np.diff(reference.acceleration[original]) / reference.dt
    return {
        "dataset": reference.dataset,
        "max_velocity": float(np.max(np.abs(reference.velocity[original]))),
        "max_acceleration": float(
            np.max(np.abs(reference.acceleration[original]))
        ),
        "max_sampled_jerk": float(np.max(np.abs(sampled_jerk))),
    }
