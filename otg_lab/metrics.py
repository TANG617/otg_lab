"""Auditable trajectory-level metrics for the OTG evidence pipeline.

The functions in this module intentionally operate on complete trajectories.
They never treat controller samples as independent statistical observations.
Raw-time position error is primary; lag alignment is returned only as a
separately named diagnostic.  State estimates and predictions are compared at
their represented physical times, not at the row in which they were emitted.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
QP_FAILURE_CATEGORIES = (
    "qp_time_limit_reached",
    "qp_max_iter_reached",
    "qp_primal_infeasible",
    "qp_dual_infeasible",
    "qp_numerical_failure",
    "qp_postcheck_failed",
)

# These metrics are defined only when a trajectory has at least one nonfallback
# frozen-solve duration (and, for rho, a positive configured horizon). A formal
# group may legitimately contain trajectories with zero eligible cycles. Never
# aggregate the available subset: omit the group-level metric and retain the
# explicit evaluated-fraction/count metrics that use the complete denominator.
OPTIONAL_REACHABILITY_SUBSET_METRICS = frozenset(
    {
        "free_trajectory_duration_p50_s",
        "free_trajectory_duration_p90_s",
        "free_trajectory_duration_p99_s",
        "free_trajectory_duration_max_s",
        "one_step_reachable_rate",
        "rho_p50",
        "rho_p90",
        "rho_p99",
        "rho_max",
        "rho_le_one_fraction",
        "rho_exceedance_fraction",
        "rho_exceedance_segment_count",
        "rho_longest_exceedance_samples",
        "rho_longest_exceedance_duration_s",
        "rho_total_exceedance_duration_s",
    }
)


class MetricValidationError(ValueError):
    """Raised when a metric would otherwise hide missing or invalid data."""


def _finite_array(
    values: ArrayLike,
    name: str,
    *,
    ndim: int | tuple[int, ...] | None = None,
) -> FloatArray:
    result = np.asarray(values, dtype=np.float64)
    if result.size == 0:
        raise MetricValidationError(f"{name} is empty")
    if ndim is not None:
        dimensions = (ndim,) if isinstance(ndim, int) else ndim
        if result.ndim not in dimensions:
            raise MetricValidationError(
                f"{name} must have ndim in {dimensions}, got shape {result.shape}"
            )
    if not np.all(np.isfinite(result)):
        raise MetricValidationError(f"{name} contains NaN or infinity")
    return result


def _state_matrix(values: ArrayLike, name: str) -> FloatArray:
    result = _finite_array(values, name, ndim=(1, 2))
    if result.ndim == 1:
        result = result[:, None]
    return result


def _times(values: ArrayLike, expected_length: int, name: str = "times") -> FloatArray:
    result = _finite_array(values, name, ndim=1)
    if result.size != expected_length:
        raise MetricValidationError(
            f"{name} has length {result.size}, expected {expected_length}"
        )
    if result.size > 1 and np.any(np.diff(result) <= 0.0):
        raise MetricValidationError(f"{name} must be strictly increasing")
    return result


def _same_shape(left: FloatArray, right: FloatArray, names: str) -> None:
    if left.shape != right.shape:
        raise MetricValidationError(
            f"{names} shapes differ: {left.shape} versus {right.shape}"
        )


def _quantile(values: FloatArray, probability: float) -> float:
    return float(np.quantile(values, probability, method="linear"))


def _error_summary(error: FloatArray, prefix: str) -> dict[str, float]:
    absolute = np.abs(error)
    return {
        f"{prefix}_rmse": float(np.sqrt(np.mean(np.square(error)))),
        f"{prefix}_mae": float(np.mean(absolute)),
        f"{prefix}_bias": float(np.mean(error)),
        f"{prefix}_p95_abs_error": _quantile(absolute, 0.95),
        f"{prefix}_max_abs_error": float(np.max(absolute)),
    }


def best_lag_metrics(
    reference: ArrayLike,
    output: ArrayLike,
    times: ArrayLike,
    *,
    max_lag_s: float = 1.0,
    minimum_overlap_fraction: float = 0.5,
) -> dict[str, float | int]:
    """Return a secondary best-lag diagnostic on the original sample grid.

    A positive lag means that ``output`` is late relative to ``reference``.
    No shift is applied to any primary metric or plot.  Irregular raw times are
    accepted; the reported lag is the median physical separation of paired
    samples rather than ``lag_samples * configured_dt``.
    """

    ref = _state_matrix(reference, "reference")
    out = _state_matrix(output, "output")
    _same_shape(ref, out, "reference/output")
    time = _times(times, ref.shape[0])
    if not math.isfinite(max_lag_s) or max_lag_s < 0.0:
        raise MetricValidationError("max_lag_s must be finite and non-negative")
    if not 0.0 < minimum_overlap_fraction <= 1.0:
        raise MetricValidationError("minimum_overlap_fraction must be in (0, 1]")
    if ref.shape[0] == 1:
        return {
            "lag_samples": 0,
            "lag_s": 0.0,
            "lag_aligned_rmse": float(np.sqrt(np.mean(np.square(out - ref)))),
        }

    median_dt = float(np.median(np.diff(time)))
    max_samples = min(
        ref.shape[0] - 1,
        int(math.floor(max_lag_s / median_dt + 1e-12)),
    )
    minimum_overlap = max(2, int(math.ceil(ref.shape[0] * minimum_overlap_fraction)))
    candidates: list[tuple[float, int, float]] = []
    for lag in range(-max_samples, max_samples + 1):
        if lag > 0:
            ref_part = ref[:-lag]
            out_part = out[lag:]
            separations = time[lag:] - time[:-lag]
        elif lag < 0:
            ref_part = ref[-lag:]
            out_part = out[:lag]
            separations = time[:lag] - time[-lag:]
        else:
            ref_part = ref
            out_part = out
            separations = np.zeros(ref.shape[0], dtype=float)
        if ref_part.shape[0] < minimum_overlap:
            continue
        rmse = float(np.sqrt(np.mean(np.square(out_part - ref_part))))
        lag_s = 0.0 if lag == 0 else float(np.median(separations))
        candidates.append((rmse, lag, lag_s))
    if not candidates:
        raise MetricValidationError("no lag candidate has sufficient overlap")
    # Prefer less shifting on exact ties, then deterministic signed ordering.
    rmse, lag, lag_s = min(
        candidates, key=lambda item: (item[0], abs(item[1]), item[1])
    )
    return {"lag_samples": int(lag), "lag_s": lag_s, "lag_aligned_rmse": rmse}


def tracking_metrics(
    reference: ArrayLike,
    output: ArrayLike,
    times: ArrayLike,
    *,
    settle_tolerance: float = 1e-3,
    settle_start_time: float | None = None,
    max_lag_s: float = 1.0,
) -> dict[str, float | int | bool]:
    """Compute primary raw-time tracking metrics for one trajectory.

    ``position_iae`` is the mean across joints of the trapezoidal integral of
    absolute error.  A trajectory which never settles is explicitly censored:
    ``settled`` is false and ``settle_time_s`` is the observed post-start
    duration, never NaN.
    """

    ref = _state_matrix(reference, "reference")
    out = _state_matrix(output, "output")
    _same_shape(ref, out, "reference/output")
    time = _times(times, ref.shape[0])
    if not math.isfinite(settle_tolerance) or settle_tolerance < 0.0:
        raise MetricValidationError("settle_tolerance must be finite and non-negative")

    error = out - ref
    result: dict[str, float | int | bool] = _error_summary(error, "position")
    if time.size == 1:
        iae = 0.0
    else:
        iae_per_joint = np.trapezoid(np.abs(error), x=time, axis=0)
        iae = float(np.mean(iae_per_joint))
    result.update(
        {
            "position_iae": iae,
            "n_samples": int(ref.shape[0]),
            "n_joints": int(ref.shape[1]),
            "duration_s": float(time[-1] - time[0]),
        }
    )

    start_time = time[0] if settle_start_time is None else float(settle_start_time)
    if not math.isfinite(start_time) or start_time < time[0] or start_time > time[-1]:
        raise MetricValidationError("settle_start_time lies outside the trajectory")
    start = int(np.searchsorted(time, start_time, side="left"))
    within = np.all(np.abs(error) <= settle_tolerance, axis=1)
    suffix_within = np.logical_and.accumulate(within[::-1])[::-1]
    candidates = np.flatnonzero(suffix_within & (np.arange(time.size) >= start))
    if candidates.size:
        settle_index = int(candidates[0])
        settled = True
        settle_time = float(time[settle_index] - start_time)
    else:
        settle_index = -1
        settled = False
        settle_time = float(time[-1] - start_time)
    result.update(
        {
            "settled": settled,
            "settle_time_s": settle_time,
            "settle_time_censored": not settled,
            "settle_index": settle_index,
        }
    )
    result.update(best_lag_metrics(ref, out, time, max_lag_s=max_lag_s))
    return result


def state_error_metrics(
    estimate: ArrayLike,
    truth: ArrayLike,
    *,
    prefix: str,
) -> dict[str, float | int]:
    """Summarize an aligned scalar or vector state error."""

    estimate_value = _state_matrix(estimate, "estimate")
    truth_value = _state_matrix(truth, "truth")
    _same_shape(estimate_value, truth_value, "estimate/truth")
    result: dict[str, float | int] = _error_summary(
        estimate_value - truth_value, prefix
    )
    result[f"{prefix}_evaluated_samples"] = int(estimate_value.shape[0])
    return result


def interpolate_truth_at_times(
    truth_times: ArrayLike,
    truth_values: ArrayLike,
    query_times: ArrayLike,
    *,
    tolerance_s: float = 1e-12,
) -> FloatArray:
    """Linearly evaluate offline truth at represented physical times.

    Extrapolation is prohibited.  This helper is for metric computation only;
    it is not an online predictor and deliberately has no causal API.
    """

    values = _state_matrix(truth_values, "truth_values")
    source_time = _times(truth_times, values.shape[0], "truth_times")
    query = _finite_array(query_times, "query_times", ndim=1)
    if tolerance_s < 0.0 or not math.isfinite(tolerance_s):
        raise MetricValidationError("tolerance_s must be finite and non-negative")
    if np.any(query < source_time[0] - tolerance_s) or np.any(
        query > source_time[-1] + tolerance_s
    ):
        raise MetricValidationError("query time requires forbidden truth extrapolation")
    clipped = np.clip(query, source_time[0], source_time[-1])
    return np.column_stack(
        [
            np.interp(clipped, source_time, values[:, joint])
            for joint in range(values.shape[1])
        ]
    )


def timed_state_error_metrics(
    estimate_times: ArrayLike,
    estimate_values: ArrayLike,
    truth_times: ArrayLike,
    truth_values: ArrayLike,
    *,
    prefix: str,
) -> dict[str, float | int]:
    """Compare estimates to truth at each estimate's physical state time."""

    estimate = _state_matrix(estimate_values, "estimate_values")
    query = _times(estimate_times, estimate.shape[0], "estimate_times")
    truth = interpolate_truth_at_times(truth_times, truth_values, query)
    return state_error_metrics(estimate, truth, prefix=prefix)


def estimator_metrics(
    *,
    posterior_times: ArrayLike,
    posterior_available_times: ArrayLike,
    posterior_position: ArrayLike,
    truth_times: ArrayLike,
    truth_position: ArrayLike,
    posterior_velocity: ArrayLike | None = None,
    truth_velocity: ArrayLike | None = None,
    posterior_acceleration: ArrayLike | None = None,
    truth_acceleration: ArrayLike | None = None,
    startup_mask: ArrayLike | None = None,
    measurement_position: ArrayLike | None = None,
    outlier_mask: ArrayLike | None = None,
    recovery_tolerance: float | None = None,
    recovery_hold_samples: int = 3,
) -> dict[str, float | int | bool]:
    """Compute one estimator's complete trajectory-level truth metrics.

    Posterior error is evaluated at ``posterior_times``.  Availability lag,
    startup duration, measurement-to-posterior noise gain, and optional outlier
    recovery are reported without using downstream tracking to rank estimators.
    """

    position = _state_matrix(posterior_position, "posterior_position")
    state_time = _finite_array(posterior_times, "posterior_times", ndim=1)
    available_time = _finite_array(
        posterior_available_times, "posterior_available_times", ndim=1
    )
    if state_time.size != position.shape[0] or available_time.size != position.shape[0]:
        raise MetricValidationError("posterior time lengths do not match state length")
    if np.any(np.diff(state_time) < 0.0) or np.any(np.diff(available_time) < 0.0):
        raise MetricValidationError("posterior times must be nondecreasing")
    lag = available_time - state_time
    if np.any(lag < -1e-12):
        raise MetricValidationError("posterior is available before its state time")
    truth_time = _times(
        truth_times,
        _state_matrix(truth_position, "truth_position").shape[0],
        "truth_times",
    )
    truth_p = interpolate_truth_at_times(truth_time, truth_position, state_time)
    position_error = position - truth_p
    result: dict[str, float | int | bool] = {
        **state_error_metrics(position, truth_p, prefix="estimator_p"),
        "posterior_lag_s": float(np.mean(lag)),
        "posterior_lag_p50_s": _quantile(lag, 0.5),
        "posterior_lag_p90_s": _quantile(lag, 0.9),
        "posterior_lag_max_s": float(np.max(lag)),
    }
    for estimate, truth, label in (
        (posterior_velocity, truth_velocity, "v"),
        (posterior_acceleration, truth_acceleration, "a"),
    ):
        if estimate is None and truth is None:
            continue
        if estimate is None or truth is None:
            raise MetricValidationError(
                f"estimator {label} estimate/truth availability differs"
            )
        estimate_value = _state_matrix(estimate, f"posterior_{label}")
        if estimate_value.shape != position.shape:
            raise MetricValidationError(
                f"posterior_{label} shape differs from position"
            )
        aligned_truth = interpolate_truth_at_times(truth_time, truth, state_time)
        result.update(
            state_error_metrics(
                estimate_value, aligned_truth, prefix=f"estimator_{label}"
            )
        )

    if startup_mask is not None:
        startup = np.asarray(startup_mask)
        if startup.shape != (position.shape[0],) or startup.dtype.kind != "b":
            raise MetricValidationError(
                "startup_mask must be a matching boolean vector"
            )
        startup_count = int(np.count_nonzero(startup))
        result.update(
            {
                "estimator_startup_samples": startup_count,
                "estimator_startup_rate": float(np.mean(startup)),
                "estimator_startup_complete": bool(not startup[-1]),
                "estimator_startup_recovery_s": float(
                    available_time[min(startup_count, available_time.size - 1)]
                    - available_time[0]
                ),
            }
        )

    if measurement_position is not None:
        measurement = _state_matrix(measurement_position, "measurement_position")
        if measurement.shape != position.shape:
            raise MetricValidationError(
                "measurement_position shape differs from posterior"
            )
        measurement_error = measurement - truth_p
        denominator = float(np.std(measurement_error))
        if denominator <= np.finfo(float).eps:
            result["estimator_noise_gain_defined"] = False
        else:
            result.update(
                {
                    "estimator_noise_gain_defined": True,
                    "estimator_noise_gain": float(np.std(position_error) / denominator),
                }
            )

    if outlier_mask is not None:
        outlier = np.asarray(outlier_mask)
        if outlier.shape != (position.shape[0],) or outlier.dtype.kind != "b":
            raise MetricValidationError(
                "outlier_mask must be a matching boolean vector"
            )
        if (
            recovery_tolerance is None
            or recovery_tolerance < 0.0
            or not math.isfinite(recovery_tolerance)
        ):
            raise MetricValidationError(
                "finite non-negative recovery_tolerance is required with outlier_mask"
            )
        if recovery_hold_samples < 1:
            raise MetricValidationError("recovery_hold_samples must be positive")
        error_norm = np.max(np.abs(position_error), axis=1)
        event_indices = np.flatnonzero(
            outlier & ~np.concatenate(([False], outlier[:-1]))
        )
        recovery_times: list[float] = []
        censored = 0
        for event_index in event_indices:
            search_start = int(event_index)
            recovered_at: int | None = None
            for candidate in range(
                search_start, position.shape[0] - recovery_hold_samples + 1
            ):
                if np.all(
                    error_norm[candidate : candidate + recovery_hold_samples]
                    <= recovery_tolerance
                ):
                    recovered_at = candidate
                    break
            if recovered_at is None:
                censored += 1
            else:
                recovery_times.append(
                    float(available_time[recovered_at] - available_time[event_index])
                )
        result["estimator_outlier_event_count"] = int(event_indices.size)
        result["estimator_outlier_recovery_censored_count"] = censored
        if recovery_times:
            result["estimator_outlier_recovery_mean_s"] = float(np.mean(recovery_times))
            result["estimator_outlier_recovery_max_s"] = float(np.max(recovery_times))
    return result


def prediction_metrics(
    *,
    prediction_times: ArrayLike,
    prediction_position: ArrayLike,
    truth_times: ArrayLike,
    truth_position: ArrayLike,
    prediction_velocity: ArrayLike | None = None,
    truth_velocity: ArrayLike | None = None,
    prediction_acceleration: ArrayLike | None = None,
    truth_acceleration: ArrayLike | None = None,
    prediction_horizon_ms: ArrayLike | None = None,
    reversal_mask: ArrayLike | None = None,
    stop_mask: ArrayLike | None = None,
) -> dict[str, float | int]:
    """Evaluate a future reference strictly at its represented future time."""

    position = _state_matrix(prediction_position, "prediction_position")
    query = _finite_array(prediction_times, "prediction_times", ndim=1)
    if query.size != position.shape[0]:
        raise MetricValidationError("prediction_times length differs from predictions")
    truth_p = interpolate_truth_at_times(truth_times, truth_position, query)
    result: dict[str, float | int] = state_error_metrics(
        position, truth_p, prefix="prediction_p"
    )
    for estimate, truth, label in (
        (prediction_velocity, truth_velocity, "v"),
        (prediction_acceleration, truth_acceleration, "a"),
    ):
        if estimate is None and truth is None:
            continue
        if estimate is None or truth is None:
            raise MetricValidationError(
                f"prediction {label} estimate/truth availability differs"
            )
        estimate_value = _state_matrix(estimate, f"prediction_{label}")
        if estimate_value.shape != position.shape:
            raise MetricValidationError(
                f"prediction_{label} shape differs from position"
            )
        aligned_truth = interpolate_truth_at_times(truth_times, truth, query)
        result.update(
            state_error_metrics(
                estimate_value, aligned_truth, prefix=f"prediction_{label}"
            )
        )
    if prediction_horizon_ms is not None:
        horizon = _finite_array(prediction_horizon_ms, "prediction_horizon_ms", ndim=1)
        if horizon.size != position.shape[0] or np.any(horizon < 0.0):
            raise MetricValidationError("prediction horizon is invalid")
        result.update(
            {
                "prediction_horizon_mean_ms": float(np.mean(horizon)),
                "prediction_horizon_min_ms": float(np.min(horizon)),
                "prediction_horizon_max_ms": float(np.max(horizon)),
            }
        )
    position_error = position - truth_p
    for mask_value, label in ((reversal_mask, "reversal"), (stop_mask, "stop")):
        if mask_value is None:
            continue
        mask = np.asarray(mask_value)
        if mask.shape != (position.shape[0],) or mask.dtype.kind != "b":
            raise MetricValidationError(
                f"{label}_mask must be a matching boolean vector"
            )
        if not np.any(mask):
            result[f"prediction_{label}_evaluated_samples"] = 0
            continue
        result.update(_error_summary(position_error[mask], f"prediction_{label}_p"))
        result[f"prediction_{label}_evaluated_samples"] = int(np.count_nonzero(mask))
    return result


def frequency_response_metrics(
    reference: ArrayLike,
    output: ArrayLike,
    times: ArrayLike,
    *,
    frequencies_hz: Sequence[float] | None = None,
    relative_amplitude_threshold: float = 1e-3,
    max_frequency_bins: int = 128,
) -> list[dict[str, float]]:
    """Estimate gain, phase delay, and group delay for chirp/multi-sine data.

    Complex Fourier projection is deterministic and sufficient for the
    predeclared excitation frequencies used by the synthetic benchmark.  With
    no explicit frequencies, energetic positive FFT bins are selected from a
    uniformly sampled trajectory.
    """

    ref = _state_matrix(reference, "reference")
    out = _state_matrix(output, "output")
    _same_shape(ref, out, "reference/output")
    time = _times(times, ref.shape[0])
    if ref.shape[0] < 4:
        raise MetricValidationError("frequency response needs at least four samples")
    intervals = np.diff(time)
    dt = float(np.median(intervals))
    tolerance = max(1e-12, 1e-6 * dt)
    if np.max(np.abs(intervals - dt)) > tolerance:
        raise MetricValidationError("frequency response requires a uniform time grid")
    if relative_amplitude_threshold <= 0.0:
        raise MetricValidationError("relative_amplitude_threshold must be positive")

    centered_ref = ref - np.mean(ref, axis=0, keepdims=True)
    centered_out = out - np.mean(out, axis=0, keepdims=True)
    if frequencies_hz is None:
        bins = np.fft.rfftfreq(ref.shape[0], d=dt)
        amplitude = np.mean(np.abs(np.fft.rfft(centered_ref, axis=0)), axis=1)
        if amplitude.size <= 1 or np.max(amplitude[1:]) <= 0.0:
            raise MetricValidationError("reference has no non-DC frequency content")
        keep = np.flatnonzero(
            (bins > 0.0)
            & (amplitude >= relative_amplitude_threshold * np.max(amplitude[1:]))
        )
        if keep.size > max_frequency_bins:
            strongest = keep[np.argsort(amplitude[keep])[-max_frequency_bins:]]
            keep = np.sort(strongest)
        frequencies = bins[keep]
    else:
        frequencies = _finite_array(frequencies_hz, "frequencies_hz", ndim=1)
        nyquist = 0.5 / dt
        if np.any(frequencies <= 0.0) or np.any(frequencies >= nyquist):
            raise MetricValidationError(
                "frequencies must lie strictly inside (0, Nyquist)"
            )
        frequencies = np.unique(frequencies)
    if frequencies.size == 0:
        raise MetricValidationError("no usable frequency bins")

    response_per_joint = np.empty((frequencies.size, ref.shape[1]), dtype=complex)
    for index, frequency in enumerate(frequencies):
        kernel = np.exp(-2j * np.pi * frequency * time)[:, None]
        x_coefficient = np.sum(centered_ref * kernel, axis=0)
        y_coefficient = np.sum(centered_out * kernel, axis=0)
        scale = np.max(np.abs(x_coefficient))
        if scale <= 0.0 or np.any(np.abs(x_coefficient) <= scale * 1e-12):
            raise MetricValidationError(
                f"reference excitation is zero at {frequency:g} Hz"
            )
        response_per_joint[index] = y_coefficient / x_coefficient

    phase = np.unwrap(np.angle(response_per_joint), axis=0)
    omega = 2.0 * np.pi * frequencies
    if frequencies.size >= 2:
        edge_order = 2 if frequencies.size >= 3 else 1
        group_delay = -np.gradient(phase, omega, axis=0, edge_order=edge_order)
    else:
        group_delay = np.full_like(phase, np.nan)
    rows: list[dict[str, float]] = []
    for frequency_index, frequency in enumerate(frequencies):
        for joint in range(ref.shape[1]):
            phase_value = float(phase[frequency_index, joint])
            row = {
                "frequency_hz": float(frequency),
                "joint_index": float(joint),
                "gain": float(abs(response_per_joint[frequency_index, joint])),
                "phase_rad": phase_value,
                "phase_delay_s": float(-phase_value / omega[frequency_index]),
            }
            if frequencies.size >= 2:
                row["group_delay_s"] = float(group_delay[frequency_index, joint])
            rows.append(row)
    return rows


def local_delay_metrics(
    reference: ArrayLike,
    output: ArrayLike,
    times: ArrayLike,
    events: Sequence[Mapping[str, Any]],
    *,
    window_before_s: float = 0.1,
    window_after_s: float = 0.1,
    max_lag_s: float = 0.05,
) -> list[dict[str, Any]]:
    """Measure raw local delay in predeclared reversal/stop windows."""

    ref = _state_matrix(reference, "reference")
    out = _state_matrix(output, "output")
    _same_shape(ref, out, "reference/output")
    time = _times(times, ref.shape[0])
    if not events:
        raise MetricValidationError("events is empty")
    if window_before_s <= 0.0 or window_after_s <= 0.0:
        raise MetricValidationError("local-delay windows must be positive")
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_time = float(event["time_s"])
        if not math.isfinite(event_time):
            raise MetricValidationError("event time is not finite")
        mask = (time >= event_time - window_before_s) & (
            time <= event_time + window_after_s
        )
        if np.count_nonzero(mask) < 4:
            raise MetricValidationError(f"event {index} has fewer than four samples")
        joint_index = event.get("joint_index")
        if joint_index is None:
            event_reference = ref[mask]
            event_output = out[mask]
        else:
            joint = int(joint_index)
            if joint < 0 or joint >= ref.shape[1]:
                raise MetricValidationError(f"event {index} has invalid joint_index")
            event_reference = ref[mask, joint]
            event_output = out[mask, joint]
        lag = best_lag_metrics(
            event_reference,
            event_output,
            time[mask],
            max_lag_s=max_lag_s,
            minimum_overlap_fraction=0.5,
        )
        rows.append(
            {
                "event_id": str(event.get("event_id", index)),
                "event_type": str(event.get("event_type", "unknown")),
                "event_time_s": event_time,
                "joint_index": None if joint_index is None else int(joint_index),
                **lag,
            }
        )
    return rows


def detect_reference_events(
    times: ArrayLike,
    velocity_truth: ArrayLike,
    *,
    stop_threshold: float | None = None,
    minimum_separation_s: float = 0.02,
) -> list[dict[str, Any]]:
    """Predeclare reversal and stop events from reference velocity truth.

    Event detection uses reference truth only, never method output.  This makes
    local-delay windows identical for every compared method and prevents
    retrospective selection of visually favorable reversals/stops.
    """

    velocity = _state_matrix(velocity_truth, "velocity_truth")
    time = _times(times, velocity.shape[0])
    if minimum_separation_s < 0.0 or not math.isfinite(minimum_separation_s):
        raise MetricValidationError(
            "minimum_separation_s must be finite and non-negative"
        )
    events: list[dict[str, Any]] = []
    for joint in range(velocity.shape[1]):
        values = velocity[:, joint]
        threshold = (
            max(1e-6, 0.01 * float(np.max(np.abs(values))))
            if stop_threshold is None
            else float(stop_threshold)
        )
        if threshold < 0.0 or not math.isfinite(threshold):
            raise MetricValidationError(
                "stop_threshold must be finite and non-negative"
            )
        last_event_time = -math.inf
        last_nonzero_index: int | None = None
        for index, value in enumerate(values):
            if abs(value) > threshold:
                if (
                    last_nonzero_index is not None
                    and values[last_nonzero_index] * value < 0.0
                    and time[index] - last_event_time >= minimum_separation_s
                ):
                    event_time = 0.5 * (time[last_nonzero_index] + time[index])
                    events.append(
                        {
                            "event_id": f"reversal-j{joint}-k{index}",
                            "event_type": "reversal",
                            "time_s": float(event_time),
                            "joint_index": joint,
                        }
                    )
                    last_event_time = float(event_time)
                last_nonzero_index = index
            elif (
                index > 0
                and abs(values[index - 1]) > threshold
                and time[index] - last_event_time >= minimum_separation_s
            ):
                events.append(
                    {
                        "event_id": f"stop-j{joint}-k{index}",
                        "event_type": "stop",
                        "time_s": float(time[index]),
                        "joint_index": joint,
                    }
                )
                last_event_time = float(time[index])
    return sorted(
        events,
        key=lambda event: (
            float(event["time_s"]),
            int(event["joint_index"]),
            str(event["event_type"]),
        ),
    )


def _reachability_metrics(
    free_trajectory_duration: ArrayLike,
    *,
    prediction_horizon_s: ArrayLike | None,
    dt: float | None,
    sample_indices: ArrayLike | None = None,
) -> dict[str, float | int]:
    durations = _finite_array(
        free_trajectory_duration, "free_trajectory_duration", ndim=1
    )
    if np.any(durations < 0.0):
        raise MetricValidationError("invalid free trajectory durations")
    result: dict[str, float | int] = {
        "free_trajectory_duration_p50_s": _quantile(durations, 0.5),
        "free_trajectory_duration_p90_s": _quantile(durations, 0.9),
        "free_trajectory_duration_p99_s": _quantile(durations, 0.99),
        "free_trajectory_duration_max_s": float(np.max(durations)),
    }
    if dt is not None:
        if dt <= 0.0 or not math.isfinite(dt):
            raise MetricValidationError("dt must be finite and positive")
        result["one_step_reachable_rate"] = float(np.mean(durations <= dt + 1e-12))
    if prediction_horizon_s is None:
        return result

    horizon = _finite_array(prediction_horizon_s, "prediction_horizon_s", ndim=1)
    if horizon.size != durations.size or np.any(horizon < 0.0):
        raise MetricValidationError("invalid prediction horizons")
    positive = horizon > 0.0
    result["rho_evaluated_fraction"] = float(np.mean(positive))
    if not np.any(positive):
        return result

    rho = durations[positive] / horizon[positive]
    if sample_indices is None:
        indices = np.arange(durations.size, dtype=np.int64)
    else:
        index_values = _finite_array(sample_indices, "sample_indices", ndim=1)
        indices = index_values.astype(np.int64)
        if (
            indices.size != durations.size
            or not np.array_equal(index_values, indices)
            or np.any(np.diff(indices) <= 0)
        ):
            raise MetricValidationError(
                "sample_indices must be matching strictly increasing integers"
            )
    # Undefined H=0 and unavailable intervening samples split, rather than
    # accidentally join, adjacent exceedance runs.
    exceedance = np.zeros(durations.size, dtype=bool)
    exceedance[positive] = rho > 1.0 + 1e-12
    previous_is_adjacent = np.concatenate(([False], np.diff(indices) == 1))
    next_is_adjacent = np.concatenate((np.diff(indices) == 1, [False]))
    run_starts = np.flatnonzero(
        exceedance
        & (~previous_is_adjacent | np.concatenate(([False], ~exceedance[:-1])))
    )
    run_stops = np.flatnonzero(
        exceedance & (~next_is_adjacent | np.concatenate((~exceedance[1:], [False])))
    )
    run_lengths = run_stops - run_starts + 1
    result.update(
        {
            "rho_p50": _quantile(rho, 0.5),
            "rho_p90": _quantile(rho, 0.9),
            "rho_p99": _quantile(rho, 0.99),
            "rho_max": float(np.max(rho)),
            "rho_le_one_fraction": float(np.mean(rho <= 1.0 + 1e-12)),
            "rho_exceedance_fraction": float(np.mean(rho > 1.0 + 1e-12)),
            "rho_exceedance_segment_count": int(run_starts.size),
            "rho_longest_exceedance_samples": int(
                np.max(run_lengths) if run_lengths.size else 0
            ),
        }
    )
    if dt is not None:
        result["rho_total_exceedance_duration_s"] = float(
            np.count_nonzero(exceedance) * dt
        )
        result["rho_longest_exceedance_duration_s"] = float(
            (np.max(run_lengths) if run_lengths.size else 0) * dt
        )
    return result


def governor_metrics(
    raw_target: ArrayLike,
    executable_target: ArrayLike | None = None,
    *,
    raw_target_time: ArrayLike | None = None,
    executable_target_time: ArrayLike | None = None,
    jerk: ArrayLike | None = None,
    feasible: ArrayLike | None = None,
    projected: ArrayLike | None = None,
    fallback: ArrayLike | None = None,
    free_trajectory_duration: ArrayLike | None = None,
    prediction_horizon_s: ArrayLike | None = None,
    dt: float | None = None,
) -> dict[str, float | int]:
    """Summarize governor distortion, feasibility, reachability, and fallback.

    ``free_trajectory_duration`` must come from a frozen solve without
    ``minimum_duration=H``.  ``executable_target`` is optional so an ordinary
    no-governor baseline can still report raw-target feasibility and T_free.
    """

    raw = _finite_array(raw_target, "raw_target", ndim=(2, 3))
    if raw.ndim == 2 and raw.shape[-1] == 3:
        raw = raw[:, None, :]
    if raw.ndim != 3 or raw.shape[-1] != 3:
        raise MetricValidationError("targets must have shape (n, 3) or (n, dof, 3)")
    result: dict[str, float | int] = {"governor_samples": int(raw.shape[0])}

    executable: FloatArray | None = None
    if executable_target is not None:
        executable = _finite_array(executable_target, "executable_target", ndim=(2, 3))
        if executable.ndim == 2 and executable.shape[-1] == 3:
            executable = executable[:, None, :]
        if executable.ndim != 3 or executable.shape[-1] != 3:
            raise MetricValidationError("targets must have shape (n, 3) or (n, dof, 3)")
        _same_shape(raw, executable, "raw/executable target")
        for component, name in enumerate(("position", "velocity", "acceleration")):
            result.update(
                _error_summary(
                    executable[..., component] - raw[..., component],
                    f"governor_{name}_distortion",
                )
            )

    if executable_target_time is not None and raw_target_time is None:
        raise MetricValidationError("executable target time has no raw target time")
    if raw_target_time is not None:
        raw_time = _finite_array(raw_target_time, "raw_target_time", ndim=1)
        if raw_time.size != raw.shape[0]:
            raise MetricValidationError("raw target time length does not match target")
        if executable_target_time is not None:
            if executable is None:
                raise MetricValidationError(
                    "executable target time has no executable target"
                )
            executable_time = _finite_array(
                executable_target_time,
                "executable_target_time",
                ndim=1,
            )
            if executable_time.size != executable.shape[0]:
                raise MetricValidationError(
                    "executable target time length does not match target"
                )
            result.update(
                _error_summary(
                    executable_time - raw_time,
                    "governor_target_time_shift_s",
                )
            )

    if jerk is not None:
        jerk_value = _finite_array(jerk, "jerk", ndim=(1, 2))
        if jerk_value.shape[0] != raw.shape[0]:
            raise MetricValidationError("jerk length does not match target length")
        absolute = np.abs(jerk_value)
        result.update(
            {
                "governor_jerk_mean_abs": float(np.mean(absolute)),
                "governor_jerk_p95_abs": _quantile(absolute, 0.95),
                "governor_jerk_max_abs": float(np.max(absolute)),
                "governor_delta_jerk_mean_abs": float(
                    np.mean(np.abs(np.diff(jerk_value, axis=0)))
                )
                if jerk_value.shape[0] > 1
                else 0.0,
                "governor_delta_jerk_max_abs": float(
                    np.max(np.abs(np.diff(jerk_value, axis=0)))
                )
                if jerk_value.shape[0] > 1
                else 0.0,
            }
        )
    for values, name in (
        (feasible, "raw_target_point_admissible"),
        (projected, "target_projected"),
        (fallback, "fallback_applied"),
    ):
        if values is None:
            continue
        flags = np.asarray(values)
        if flags.shape[0] != raw.shape[0] or flags.dtype.kind != "b":
            raise MetricValidationError(f"{name} must be a matching boolean array")
        result[f"{name}_count"] = int(np.count_nonzero(flags))
        result[f"{name}_rate"] = float(np.mean(flags))
    # Compatibility metric aliases have one exact v2 meaning and are not used
    # as inputs to new reports.
    if "raw_target_point_admissible_count" in result:
        result["target_feasible_count"] = result["raw_target_point_admissible_count"]
        result["target_feasible_rate"] = result["raw_target_point_admissible_rate"]
    if "fallback_applied_count" in result:
        result["fallback_count"] = result["fallback_applied_count"]
        result["fallback_rate"] = result["fallback_applied_rate"]

    if free_trajectory_duration is not None:
        durations = _finite_array(
            free_trajectory_duration, "free_trajectory_duration", ndim=1
        )
        if durations.size != raw.shape[0]:
            raise MetricValidationError("invalid free trajectory durations")
        result.update(
            _reachability_metrics(
                durations,
                prediction_horizon_s=prediction_horizon_s,
                dt=dt,
            )
        )
    elif prediction_horizon_s is not None:
        raise MetricValidationError(
            "prediction_horizon_s requires free_trajectory_duration"
        )
    return result


def runtime_metrics(
    compute_us: ArrayLike,
    *,
    deadline_us: float,
    prefix: str = "runtime",
    warmup_samples: int = 0,
) -> dict[str, float | int]:
    """Return required runtime quantiles and an explicit deadline-miss rate."""

    values = _finite_array(compute_us, "compute_us", ndim=1)
    if np.any(values < 0.0):
        raise MetricValidationError("compute_us contains negative durations")
    if not isinstance(warmup_samples, int) or warmup_samples < 0:
        raise MetricValidationError("warmup_samples must be a non-negative integer")
    if warmup_samples >= values.size:
        raise MetricValidationError("warmup removes every runtime sample")
    if not math.isfinite(deadline_us) or deadline_us <= 0.0:
        raise MetricValidationError("deadline_us must be finite and positive")
    values = values[warmup_samples:]
    misses = values > deadline_us
    return {
        f"{prefix}_count": int(values.size),
        f"{prefix}_p50_us": _quantile(values, 0.5),
        f"{prefix}_p90_us": _quantile(values, 0.9),
        f"{prefix}_p99_us": _quantile(values, 0.99),
        f"{prefix}_p99_9_us": _quantile(values, 0.999),
        f"{prefix}_max_us": float(np.max(values)),
        f"{prefix}_deadline_us": float(deadline_us),
        f"{prefix}_deadline_miss_count": int(np.count_nonzero(misses)),
        f"{prefix}_deadline_miss_rate": float(np.mean(misses)),
    }


def _limit_vectors(
    max_velocity: ArrayLike | float,
    max_acceleration: ArrayLike | float,
    max_jerk: ArrayLike | float,
    dof: int,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    vectors = []
    for value, name in (
        (max_velocity, "max_velocity"),
        (max_acceleration, "max_acceleration"),
        (max_jerk, "max_jerk"),
    ):
        vector = np.broadcast_to(np.asarray(value, dtype=float), (dof,)).copy()
        if not np.all(np.isfinite(vector)) or np.any(vector <= 0.0):
            raise MetricValidationError(f"{name} must contain finite positive limits")
        vectors.append(vector)
    return vectors[0], vectors[1], vectors[2]


def _polynomial_roots_in_interval(
    coefficients: Sequence[float], duration: float
) -> list[float]:
    values = np.trim_zeros(np.asarray(coefficients, dtype=float), trim="f")
    if values.size <= 1:
        return []
    roots = np.roots(values)
    return sorted(
        float(root.real)
        for root in roots
        if abs(root.imag) <= 1e-10 and 0.0 < root.real < duration
    )


def constant_jerk_segment_extrema(
    initial_state: ArrayLike,
    jerk: ArrayLike | float,
    duration_s: float,
    *,
    max_velocity: ArrayLike | float,
    max_acceleration: ArrayLike | float,
    max_jerk: ArrayLike | float,
    tolerance: float = 1e-10,
) -> list[dict[str, float | int | bool | str]]:
    """Analytically audit continuous constant-jerk segment extrema.

    Velocity's interior extremum at ``t=-a0/j`` is always included when it is
    inside the segment.  Violation duration is the union of time intervals in
    which any V/A/J bound is exceeded, found from all analytic boundary roots.
    """

    state = _finite_array(initial_state, "initial_state", ndim=(1, 2))
    if state.ndim == 1:
        if state.shape != (3,):
            raise MetricValidationError("initial_state must have three components")
        state = state[None, :]
    if state.shape[1] != 3:
        raise MetricValidationError("initial_state must have shape (dof, 3)")
    dof = state.shape[0]
    jerk_value = np.broadcast_to(np.asarray(jerk, dtype=float), (dof,)).copy()
    if not np.all(np.isfinite(jerk_value)):
        raise MetricValidationError("jerk contains NaN or infinity")
    if not math.isfinite(duration_s) or duration_s <= 0.0:
        raise MetricValidationError("duration_s must be finite and positive")
    vmax, amax, jmax = _limit_vectors(max_velocity, max_acceleration, max_jerk, dof)

    rows: list[dict[str, float | int | bool | str]] = []
    for joint in range(dof):
        _, velocity0, acceleration0 = state[joint]
        jerk_joint = jerk_value[joint]
        candidate_times = [0.0, float(duration_s)]
        if jerk_joint != 0.0:
            interior = -acceleration0 / jerk_joint
            if 0.0 < interior < duration_s:
                candidate_times.append(float(interior))
        candidate_times = sorted(set(candidate_times))
        velocities = np.asarray(
            [
                velocity0 + acceleration0 * time + 0.5 * jerk_joint * time**2
                for time in candidate_times
            ]
        )
        accelerations = np.asarray(
            [acceleration0 + jerk_joint * time for time in (0.0, duration_s)]
        )
        velocity_index = int(np.argmax(np.abs(velocities)))
        acceleration_index = int(np.argmax(np.abs(accelerations)))
        max_abs_velocity = float(abs(velocities[velocity_index]))
        max_abs_acceleration = float(abs(accelerations[acceleration_index]))
        max_abs_jerk = float(abs(jerk_joint))

        breakpoints = {0.0, float(duration_s)}
        for sign in (-1.0, 1.0):
            breakpoints.update(
                _polynomial_roots_in_interval(
                    (0.5 * jerk_joint, acceleration0, velocity0 - sign * vmax[joint]),
                    duration_s,
                )
            )
            if jerk_joint != 0.0:
                crossing = (sign * amax[joint] - acceleration0) / jerk_joint
                if 0.0 < crossing < duration_s:
                    breakpoints.add(float(crossing))
        ordered = sorted(breakpoints)
        violation_duration = 0.0
        violation_intervals = 0
        in_violation = False
        for left, right in zip(ordered[:-1], ordered[1:]):
            midpoint = 0.5 * (left + right)
            velocity_mid = (
                velocity0 + acceleration0 * midpoint + 0.5 * jerk_joint * midpoint**2
            )
            acceleration_mid = acceleration0 + jerk_joint * midpoint
            violated = bool(
                abs(velocity_mid) > vmax[joint] + tolerance
                or abs(acceleration_mid) > amax[joint] + tolerance
                or abs(jerk_joint) > jmax[joint] + tolerance
            )
            if violated:
                violation_duration += right - left
                if not in_violation:
                    violation_intervals += 1
            in_violation = violated
        velocity_violation = bool(max_abs_velocity > vmax[joint] + tolerance)
        acceleration_violation = bool(max_abs_acceleration > amax[joint] + tolerance)
        jerk_violation = bool(max_abs_jerk > jmax[joint] + tolerance)
        rows.append(
            {
                "joint_index": joint,
                "audit_method": "analytic_constant_jerk",
                "duration_s": float(duration_s),
                "velocity_min": float(np.min(velocities)),
                "velocity_max": float(np.max(velocities)),
                "max_abs_velocity": max_abs_velocity,
                "max_abs_velocity_time_s": float(candidate_times[velocity_index]),
                "velocity_interior_extremum": len(candidate_times) == 3,
                "max_abs_acceleration": max_abs_acceleration,
                "max_abs_acceleration_time_s": float(
                    (0.0, duration_s)[acceleration_index]
                ),
                "max_sampled_jerk": max_abs_jerk,
                "max_new_jerk": max_abs_jerk,
                "max_internal_jerk": max_abs_jerk,
                "velocity_margin": float(vmax[joint] - max_abs_velocity),
                "acceleration_margin": float(amax[joint] - max_abs_acceleration),
                "jerk_margin": float(jmax[joint] - max_abs_jerk),
                "velocity_violation": velocity_violation,
                "acceleration_violation": acceleration_violation,
                "internal_jerk_violation": jerk_violation,
                "violation_count": int(
                    velocity_violation + acceleration_violation + jerk_violation
                ),
                "violation_interval_count": violation_intervals,
                "violation_duration_s": float(violation_duration),
            }
        )
    return rows


def audit_constant_jerk_segments(
    initial_states: ArrayLike,
    jerks: ArrayLike,
    durations_s: ArrayLike | float,
    *,
    max_velocity: ArrayLike | float,
    max_acceleration: ArrayLike | float,
    max_jerk: ArrayLike | float,
    tolerance: float = 1e-10,
) -> list[dict[str, Any]]:
    """Audit a sequence of independent constant-jerk segments analytically."""

    states = _finite_array(initial_states, "initial_states", ndim=3)
    if states.shape[2] != 3:
        raise MetricValidationError("initial_states must have shape (segments, dof, 3)")
    jerk_values = _finite_array(jerks, "jerks", ndim=(1, 2))
    if jerk_values.ndim == 1:
        jerk_values = jerk_values[:, None]
    if jerk_values.shape != states.shape[:2]:
        raise MetricValidationError("jerks must have shape (segments, dof)")
    durations = np.broadcast_to(
        np.asarray(durations_s, dtype=float), (states.shape[0],)
    ).copy()
    if not np.all(np.isfinite(durations)) or np.any(durations <= 0.0):
        raise MetricValidationError("durations must be finite and positive")
    rows: list[dict[str, Any]] = []
    offset = 0.0
    for segment in range(states.shape[0]):
        audited = constant_jerk_segment_extrema(
            states[segment],
            jerk_values[segment],
            float(durations[segment]),
            max_velocity=max_velocity,
            max_acceleration=max_acceleration,
            max_jerk=max_jerk,
            tolerance=tolerance,
        )
        for row in audited:
            row.update(
                {
                    "segment_index": segment,
                    "segment_start_time_s": offset,
                    "segment_end_time_s": offset + float(durations[segment]),
                }
            )
            rows.append(row)
        offset += float(durations[segment])
    return rows


def _normalize_optional_jerk(
    value: ArrayLike | float | Callable[[float], ArrayLike] | None,
    times: FloatArray,
    dof: int,
    name: str,
) -> FloatArray | None:
    if value is None:
        return None
    if callable(value):
        result = np.vstack(
            [
                np.broadcast_to(np.asarray(value(float(time)), dtype=float), (dof,))
                for time in times
            ]
        )
    else:
        raw = np.asarray(value, dtype=float)
        if raw.ndim == 0:
            result = np.full((times.size, dof), float(raw))
        elif raw.shape == (dof,):
            result = np.repeat(raw[None, :], times.size, axis=0)
        else:
            result = np.broadcast_to(raw, (times.size, dof)).copy()
    if not np.all(np.isfinite(result)):
        raise MetricValidationError(f"{name} contains NaN or infinity")
    return result


def audit_sampled_continuous_trajectory(
    evaluator: Callable[[float], Sequence[ArrayLike]],
    duration_s: float,
    *,
    dof: int,
    max_velocity: ArrayLike | float,
    max_acceleration: ArrayLike | float,
    max_jerk: ArrayLike | float,
    section_boundaries_s: Sequence[float] = (),
    max_step_s: float = 0.0001,
    internal_jerk: ArrayLike | float | Callable[[float], ArrayLike] | None = None,
    new_jerk: ArrayLike | float | Callable[[float], ArrayLike] | None = None,
    tolerance: float = 1e-8,
) -> list[dict[str, Any]]:
    """Audit a continuous trajectory on a <=0.1 ms grid plus all boundaries.

    ``evaluator(t)`` must return position, velocity, and acceleration.  Profile
    (internal) jerk and online ``new_jerk`` are optional and remain explicitly
    unavailable when not supplied; neither is silently replaced by finite
    differences.  ``max_sampled_jerk`` is always the distinct acceleration
    difference diagnostic.
    """

    if not callable(evaluator):
        raise MetricValidationError("evaluator must be callable")
    if not isinstance(dof, int) or dof <= 0:
        raise MetricValidationError("dof must be a positive integer")
    if not math.isfinite(duration_s) or duration_s <= 0.0:
        raise MetricValidationError("duration_s must be finite and positive")
    if not math.isfinite(max_step_s) or not 0.0 < max_step_s <= 0.0001 + 1e-15:
        raise MetricValidationError("max_step_s must be in (0, 0.0001]")
    vmax, amax, jmax = _limit_vectors(max_velocity, max_acceleration, max_jerk, dof)
    boundaries = _finite_array(
        [0.0, duration_s, *section_boundaries_s], "section_boundaries_s", ndim=1
    )
    if np.any(boundaries < 0.0) or np.any(boundaries > duration_s):
        raise MetricValidationError("section boundary lies outside trajectory")
    interval_count = int(math.ceil(duration_s / max_step_s))
    regular = np.linspace(0.0, duration_s, interval_count + 1)
    times = np.unique(np.concatenate((regular, boundaries)))
    evaluated = [evaluator(float(time)) for time in times]
    if any(len(item) < 3 for item in evaluated):
        raise MetricValidationError(
            "evaluator must return position, velocity, acceleration"
        )
    position = np.vstack(
        [
            np.broadcast_to(np.asarray(item[0], dtype=float), (dof,))
            for item in evaluated
        ]
    )
    velocity = np.vstack(
        [
            np.broadcast_to(np.asarray(item[1], dtype=float), (dof,))
            for item in evaluated
        ]
    )
    acceleration = np.vstack(
        [
            np.broadcast_to(np.asarray(item[2], dtype=float), (dof,))
            for item in evaluated
        ]
    )
    del position
    if not np.all(np.isfinite(velocity)) or not np.all(np.isfinite(acceleration)):
        raise MetricValidationError("trajectory evaluator returned NaN or infinity")
    delta_time = np.diff(times)
    sampled_jerk = np.diff(acceleration, axis=0) / delta_time[:, None]
    midpoint_times = 0.5 * (times[:-1] + times[1:])
    internal = _normalize_optional_jerk(
        internal_jerk, midpoint_times, dof, "internal_jerk"
    )
    online_new = _normalize_optional_jerk(new_jerk, times, dof, "new_jerk")

    rows: list[dict[str, Any]] = []
    for joint in range(dof):
        velocity_index = int(np.argmax(np.abs(velocity[:, joint])))
        acceleration_index = int(np.argmax(np.abs(acceleration[:, joint])))
        sampled_index = int(np.argmax(np.abs(sampled_jerk[:, joint])))
        velocity_mask = np.abs(velocity[:, joint]) > vmax[joint] + tolerance
        acceleration_mask = np.abs(acceleration[:, joint]) > amax[joint] + tolerance
        internal_mask = (
            np.abs(internal[:, joint]) > jmax[joint] + tolerance
            if internal is not None
            else np.zeros(midpoint_times.size, dtype=bool)
        )
        midpoint_velocity = 0.5 * (velocity[:-1, joint] + velocity[1:, joint])
        midpoint_acceleration = 0.5 * (
            acceleration[:-1, joint] + acceleration[1:, joint]
        )
        interval_violation = (
            (np.abs(midpoint_velocity) > vmax[joint] + tolerance)
            | (np.abs(midpoint_acceleration) > amax[joint] + tolerance)
            | internal_mask
        )
        starts = interval_violation & np.concatenate(([True], ~interval_violation[:-1]))
        row: dict[str, Any] = {
            "joint_index": joint,
            "audit_method": "sampled_continuous",
            "duration_s": float(duration_s),
            "sample_count": int(times.size),
            "max_grid_step_s": float(np.max(delta_time)),
            "section_boundary_count": int(np.unique(boundaries).size),
            "max_abs_velocity": float(abs(velocity[velocity_index, joint])),
            "max_abs_velocity_time_s": float(times[velocity_index]),
            "max_abs_acceleration": float(abs(acceleration[acceleration_index, joint])),
            "max_abs_acceleration_time_s": float(times[acceleration_index]),
            "max_sampled_jerk": float(abs(sampled_jerk[sampled_index, joint])),
            "max_sampled_jerk_time_s": float(midpoint_times[sampled_index]),
            "internal_jerk_available": internal is not None,
            "new_jerk_available": online_new is not None,
            "velocity_margin": float(vmax[joint] - np.max(np.abs(velocity[:, joint]))),
            "acceleration_margin": float(
                amax[joint] - np.max(np.abs(acceleration[:, joint]))
            ),
            "sampled_jerk_margin": float(
                jmax[joint] - np.max(np.abs(sampled_jerk[:, joint]))
            ),
            "velocity_violation_count": int(np.count_nonzero(velocity_mask)),
            "acceleration_violation_count": int(np.count_nonzero(acceleration_mask)),
            "internal_jerk_violation_count": int(np.count_nonzero(internal_mask)),
            "violation_interval_count": int(np.count_nonzero(starts)),
            "violation_duration_s": float(np.sum(delta_time[interval_violation])),
        }
        if internal is not None:
            index = int(np.argmax(np.abs(internal[:, joint])))
            row.update(
                {
                    "max_internal_jerk": float(abs(internal[index, joint])),
                    "max_internal_jerk_time_s": float(midpoint_times[index]),
                    "internal_jerk_margin": float(
                        jmax[joint] - np.max(np.abs(internal[:, joint]))
                    ),
                }
            )
        if online_new is not None:
            index = int(np.argmax(np.abs(online_new[:, joint])))
            row.update(
                {
                    "max_new_jerk": float(abs(online_new[index, joint])),
                    "max_new_jerk_time_s": float(times[index]),
                    "new_jerk_margin": float(
                        jmax[joint] - np.max(np.abs(online_new[:, joint]))
                    ),
                    "new_jerk_violation_count": int(
                        np.count_nonzero(
                            np.abs(online_new[:, joint]) > jmax[joint] + tolerance
                        )
                    ),
                }
            )
        row["violation_count"] = int(
            row["velocity_violation_count"]
            + row["acceleration_violation_count"]
            + row["internal_jerk_violation_count"]
            + row.get("new_jerk_violation_count", 0)
        )
        rows.append(row)
    return rows


_IDENTITY_FIELDS = (
    "run_id",
    "dataset_id",
    "session_id",
    "trajectory_id",
    "split",
    "seed",
    "scenario_id",
    "reference_family",
)

_METHOD_FIELD_MAP = {
    "estimator_id": "estimator",
    "predictor_id": "predictor",
    "target_mode": "target_mode",
    "governor_id": "governor",
    "follower_id": "follower",
    "plant_id": "plant",
}


def _available_matrix(
    aligned_rows: Sequence[Sequence[Mapping[str, Any]]], field: str
) -> FloatArray | None:
    present = [
        [row.get(field) is not None for row in joint_rows]
        for joint_rows in aligned_rows
    ]
    any_present = any(any(flags) for flags in present)
    if not any_present:
        return None
    if not all(all(flags) for flags in present):
        raise MetricValidationError(f"{field} is only partially available")
    value = np.column_stack(
        [[float(row[field]) for row in joint_rows] for joint_rows in aligned_rows]
    )
    return _finite_array(value, field, ndim=2)


def _partially_available_matrix(
    aligned_rows: Sequence[Sequence[Mapping[str, Any]]], field: str
) -> tuple[FloatArray, NDArray[np.bool_]] | None:
    """Return complete synchronized rows for an explicitly optional field.

    Missing values may remove whole control cycles (for example internal jerk
    on a fallback), but a field present for only some joints at one synchronized
    cycle is rejected because that would make a vector maximum ambiguous.
    """

    presence = np.column_stack(
        [
            [row.get(field) is not None for row in joint_rows]
            for joint_rows in aligned_rows
        ]
    )
    if not np.any(presence):
        return None
    complete = np.all(presence, axis=1)
    partial = np.any(presence, axis=1) & ~complete
    if np.any(partial):
        raise MetricValidationError(
            f"{field} is present for only part of a synchronized n-DoF sample"
        )
    values = np.column_stack(
        [
            [float(row[field]) for row, keep in zip(joint_rows, complete) if keep]
            for joint_rows in aligned_rows
        ]
    )
    return _finite_array(values, field, ndim=2), complete


def _boolean_vector(
    aligned_rows: Sequence[Sequence[Mapping[str, Any]]], field: str
) -> NDArray[np.bool_] | None:
    flattened = [row.get(field) for joint_rows in aligned_rows for row in joint_rows]
    if all(value is None for value in flattened):
        return None
    if any(
        value is None or not isinstance(value, (bool, np.bool_)) for value in flattened
    ):
        raise MetricValidationError(
            f"{field} is only partially available or non-boolean"
        )
    # A trajectory sample is flagged if any joint is flagged.
    return np.any(
        np.column_stack(
            [[bool(row[field]) for row in joint_rows] for joint_rows in aligned_rows]
        ),
        axis=1,
    )


def _optional_synchronized_categories(
    aligned_rows: Sequence[Sequence[Mapping[str, Any]]], field: str
) -> tuple[str, ...] | None:
    """Return one identical categorical value per synchronized n-DoF cycle."""

    sample_count = len(aligned_rows[0])
    cycles: list[str] = []
    any_present = False
    for index in range(sample_count):
        values = [joint_rows[index].get(field) for joint_rows in aligned_rows]
        if all(value is None for value in values):
            if any_present:
                raise MetricValidationError(
                    f"{field} is missing on part of a trajectory"
                )
            continue
        any_present = True
        if any(value is None or not isinstance(value, str) for value in values):
            raise MetricValidationError(
                f"{field} is only partially available or non-string"
            )
        if len(set(values)) != 1:
            raise MetricValidationError(f"{field} differs across synchronized joints")
        cycles.append(str(values[0]))
    if not any_present:
        return None
    if len(cycles) != sample_count:
        raise MetricValidationError(f"{field} is missing on part of a trajectory")
    return tuple(cycles)


def _trajectory_metric_row(
    rows: Sequence[Mapping[str, Any]],
    *,
    output_field: str | None,
    settle_tolerance: float,
    max_lag_s: float,
    deadline_us: float | None,
    motion_limits: Mapping[str, ArrayLike | float] | None,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    joint_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        joint_groups[str(row["joint_id"])].append(row)
    aligned = []
    expected_k: tuple[int, ...] | None = None
    expected_time: FloatArray | None = None
    for joint in sorted(joint_groups):
        joint_rows = sorted(joint_groups[joint], key=lambda row: int(row["k"]))
        keys = tuple(int(row["k"]) for row in joint_rows)
        if len(set(keys)) != len(keys):
            raise MetricValidationError(
                "duplicate sample index within trajectory/joint"
            )
        times = _finite_array(
            [float(row["control_time"]) for row in joint_rows],
            "control_time",
            ndim=1,
        )
        if expected_k is None:
            expected_k = keys
            expected_time = times
        elif keys != expected_k or not np.allclose(
            times, expected_time, rtol=0.0, atol=1e-12
        ):
            raise MetricValidationError("joint sample grids are not synchronized")
        aligned.append(joint_rows)
    if not aligned or expected_time is None:
        raise MetricValidationError("trajectory is empty")

    first = aligned[0][0]
    identity = {field: first.get(field) for field in _IDENTITY_FIELDS}
    for row in rows:
        for field, expected in identity.items():
            if row.get(field) != expected:
                raise MetricValidationError(
                    f"trajectory identity field {field} changes"
                )
    method_identity = {
        output_field: first.get(source_field)
        for source_field, output_field in _METHOD_FIELD_MAP.items()
        if first.get(source_field) is not None
    }
    for row in rows:
        for source_field in _METHOD_FIELD_MAP:
            if source_field in row and row.get(source_field) != first.get(source_field):
                raise MetricValidationError(
                    f"trajectory method field {source_field} changes"
                )
    result: dict[str, Any] = {
        **identity,
        **method_identity,
        **dict(context),
    }
    if "method" not in result:
        explicit_method = first.get("method_id")
        if explicit_method is not None:
            result["method"] = str(explicit_method)
        elif method_identity:
            result["method"] = "|".join(
                f"{name}={method_identity.get(name, 'none')}"
                for name in _METHOD_FIELD_MAP.values()
            )
        else:
            result["method"] = str(identity["run_id"])
    result["recorded_samples"] = len(aligned[0])

    reference = _available_matrix(aligned, "p_ref")
    if reference is None:  # Canonical schema prevents this, retain direct-input safety.
        raise MetricValidationError("p_ref is unavailable")
    selected_output = output_field
    if selected_output is None:
        selected_output = (
            "plant_p"
            if _available_matrix(aligned, "plant_p") is not None
            else "command_p"
        )
    output = _available_matrix(aligned, selected_output)
    if output is None:
        raise MetricValidationError(f"tracking output {selected_output} is unavailable")
    # p_ref and derivative truth are control-grid reference values.  source_time
    # belongs to the measurement and may repeat under a causal hold, so using it
    # as the reference clock would corrupt replay metrics.
    truth_time = _times(expected_time, reference.shape[0], "control_time")

    # target[k] -> command/output[k+1]: a command is evaluated against the
    # position reference at command_time, never against p_ref from its input
    # row/control_time.  Offline interpolation is metric-only and never enters
    # an online component.
    output_times = _available_matrix(aligned, "command_time")
    if output_times is None:
        raise MetricValidationError("command_time is unavailable")
    if not np.allclose(output_times, output_times[:, [0]], rtol=0.0, atol=1e-12):
        raise MetricValidationError("command_time differs across synchronized joints")
    output_time = output_times[:, 0]
    evaluation_mask = (output_time >= truth_time[0] - 1e-12) & (
        output_time <= truth_time[-1] + 1e-12
    )
    if not np.any(evaluation_mask):
        raise MetricValidationError("no command/output physical time overlaps p_ref")
    reference_at_output = interpolate_truth_at_times(
        truth_time, reference, output_time[evaluation_mask]
    )
    evaluated_output = output[evaluation_mask]
    evaluated_time = output_time[evaluation_mask]
    result.update(
        {
            "tracking_output_field": selected_output,
            "tracking_reference_time_field": "control_time",
            "tracking_output_time_field": "command_time",
            "tracking_evaluated_fraction": float(np.mean(evaluation_mask)),
        }
    )
    result.update(
        tracking_metrics(
            reference_at_output,
            evaluated_output,
            evaluated_time,
            settle_tolerance=settle_tolerance,
            max_lag_s=max_lag_s,
        )
    )
    per_joint_rmse = np.sqrt(
        np.mean(np.square(evaluated_output - reference_at_output), axis=0)
    )
    result["worst_joint_position_rmse"] = float(np.max(per_joint_rmse))
    result["vector_position_rmse"] = float(
        np.sqrt(
            np.mean(np.sum(np.square(evaluated_output - reference_at_output), axis=1))
        )
    )

    truth_components = {
        "p": reference,
        "v": _available_matrix(aligned, "v_ref_truth"),
        "a": _available_matrix(aligned, "a_ref_truth"),
    }
    for layer, time_field, fields in (
        (
            "estimator",
            "posterior_state_time",
            {"p": "posterior_p", "v": "posterior_v", "a": "posterior_a"},
        ),
        (
            "prediction",
            "prediction_time",
            {"p": "prediction_p", "v": "prediction_v", "a": "prediction_a"},
        ),
    ):
        state_times = _available_matrix(aligned, time_field)
        if state_times is None:
            continue
        if not np.allclose(state_times, state_times[:, [0]], rtol=0.0, atol=1e-12):
            raise MetricValidationError(
                f"{time_field} differs across synchronized joints"
            )
        query = state_times[:, 0]
        in_range = (query >= truth_time[0] - 1e-12) & (query <= truth_time[-1] + 1e-12)
        if not np.any(in_range):
            raise MetricValidationError(f"no {layer} state time overlaps truth")
        result[f"{layer}_evaluated_time_fraction"] = float(np.mean(in_range))
        for component, field in fields.items():
            estimate = _available_matrix(aligned, field)
            truth = truth_components[component]
            if estimate is None or truth is None:
                continue
            aligned_truth = interpolate_truth_at_times(
                truth_time, truth, query[in_range]
            )
            result.update(
                state_error_metrics(
                    estimate[in_range],
                    aligned_truth,
                    prefix=f"{layer}_{component}",
                )
            )
        if layer == "estimator":
            axis_source_times = _available_matrix(aligned, "posterior_axis_source_time")
            if axis_source_times is not None:
                axis_source_lag = expected_time[:, None] - axis_source_times
                if np.any(axis_source_lag < -1e-12):
                    raise MetricValidationError(
                        "per-axis posterior source time is in the future"
                    )
                result.update(
                    {
                        "posterior_axis_source_lag_mean_s": float(
                            np.mean(axis_source_lag)
                        ),
                        "posterior_axis_source_lag_p90_s": _quantile(
                            axis_source_lag, 0.9
                        ),
                        "posterior_axis_source_lag_max_s": float(
                            np.max(axis_source_lag)
                        ),
                        "posterior_axis_source_time_spread_max_s": float(
                            np.max(np.ptp(axis_source_times, axis=1))
                        ),
                    }
                )
            available_times = _available_matrix(aligned, "posterior_available_time")
            if available_times is not None:
                if not np.allclose(
                    available_times,
                    available_times[:, [0]],
                    rtol=0.0,
                    atol=1e-12,
                ):
                    raise MetricValidationError(
                        "posterior_available_time differs across synchronized joints"
                    )
                availability_lag = available_times[:, 0] - query
                if np.any(availability_lag < -1e-12):
                    raise MetricValidationError(
                        "posterior is recorded available before its physical state time"
                    )
                result.update(
                    {
                        "posterior_lag_s": float(np.mean(availability_lag)),
                        "posterior_lag_p50_s": _quantile(availability_lag, 0.5),
                        "posterior_lag_p90_s": _quantile(availability_lag, 0.9),
                        "posterior_lag_max_s": float(np.max(availability_lag)),
                    }
                )

    raw_components_by_name = {
        component: _available_matrix(aligned, f"raw_target_{component}")
        for component in ("p", "v", "a")
    }
    executable_components_by_name = {
        component: _available_matrix(aligned, f"executable_target_{component}")
        for component in ("p", "v", "a")
    }
    raw_present = [value is not None for value in raw_components_by_name.values()]
    executable_present = [
        value is not None for value in executable_components_by_name.values()
    ]
    if any(raw_present) and not all(raw_present):
        raise MetricValidationError("raw target state is only partially available")
    if any(executable_present) and not all(executable_present):
        raise MetricValidationError(
            "executable target state is only partially available"
        )
    if all(executable_present) and not all(raw_present):
        raise MetricValidationError("executable target has no raw target")

    raw_target_time = _available_matrix(aligned, "raw_target_time")
    executable_target_time = _available_matrix(aligned, "executable_target_time")
    horizon_ms = _available_matrix(aligned, "prediction_horizon_ms")
    for values, field in (
        (horizon_ms, "prediction_horizon_ms"),
        (raw_target_time, "raw_target_time"),
        (executable_target_time, "executable_target_time"),
    ):
        if values is not None and not np.allclose(
            values, values[:, [0]], rtol=0.0, atol=1e-12
        ):
            raise MetricValidationError(f"{field} differs across synchronized joints")
    if horizon_ms is not None:
        recorded_horizon = horizon_ms[:, 0]
        if np.any(recorded_horizon < 0.0):
            raise MetricValidationError("prediction_horizon_ms is negative")
        result.update(
            {
                "prediction_propagation_horizon_mean_ms": float(
                    np.mean(recorded_horizon)
                ),
                "prediction_propagation_horizon_min_ms": float(
                    np.min(recorded_horizon)
                ),
                "prediction_propagation_horizon_max_ms": float(
                    np.max(recorded_horizon)
                ),
            }
        )

    configured_horizon: FloatArray | None = None
    if raw_target_time is not None:
        configured_horizon = raw_target_time[:, 0] - truth_time
        if np.any(configured_horizon < -1e-12):
            raise MetricValidationError("raw target precedes its control time")
        configured_horizon = np.maximum(configured_horizon, 0.0)
        result.update(
            {
                "configured_prediction_horizon_mean_ms": float(
                    1000.0 * np.mean(configured_horizon)
                ),
                "configured_prediction_horizon_min_ms": float(
                    1000.0 * np.min(configured_horizon)
                ),
                "configured_prediction_horizon_max_ms": float(
                    1000.0 * np.max(configured_horizon)
                ),
            }
        )

    if all(raw_present):
        raw_target = np.stack(
            [raw_components_by_name[name] for name in ("p", "v", "a")], axis=2
        )
        executable_target = (
            np.stack(
                [executable_components_by_name[name] for name in ("p", "v", "a")],
                axis=2,
            )
            if all(executable_present)
            else None
        )
        fallback_flags = _boolean_vector(aligned, "fallback_applied")
        duration_target_source = (
            "executable_target" if executable_target is not None else "raw_target"
        )
        result.update(
            {
                "free_trajectory_duration_target_source": duration_target_source,
                "free_trajectory_duration_definition": (
                    "follower_unconstrained_frozen_solve"
                ),
                "rho_horizon_definition": "raw_target_time_minus_control_time",
            }
        )
        result.update(
            governor_metrics(
                raw_target,
                executable_target,
                raw_target_time=None
                if raw_target_time is None
                else raw_target_time[:, 0],
                executable_target_time=None
                if executable_target_time is None
                else executable_target_time[:, 0],
                jerk=_available_matrix(aligned, "command_jerk")
                if executable_target is not None
                else None,
                feasible=_boolean_vector(aligned, "raw_target_point_admissible"),
                projected=_boolean_vector(aligned, "target_projected"),
                fallback=fallback_flags,
            )
        )
        duration_data = _partially_available_matrix(aligned, "free_trajectory_duration")
        if duration_data is not None:
            duration_matrix, recorded_duration = duration_data
            duration_valid = recorded_duration.copy()
            excluded_fallback = np.zeros(duration_valid.size, dtype=bool)
            if fallback_flags is not None:
                excluded_fallback = duration_valid & fallback_flags
                duration_valid &= ~fallback_flags
                duration_matrix = duration_matrix[~fallback_flags[recorded_duration]]
            result["free_trajectory_duration_excluded_fallback_count"] = int(
                np.count_nonzero(excluded_fallback)
            )
            result["free_trajectory_duration_recorded_fraction"] = float(
                np.mean(recorded_duration)
            )
            duration_indices = np.flatnonzero(duration_valid)
            result["free_trajectory_duration_evaluated_fraction"] = float(
                np.mean(duration_valid)
            )
            if duration_indices.size:
                result.update(
                    _reachability_metrics(
                        np.max(duration_matrix, axis=1),
                        prediction_horizon_s=None
                        if configured_horizon is None
                        else configured_horizon[duration_valid],
                        dt=float(np.median(np.diff(expected_time)))
                        if expected_time.size > 1
                        else None,
                        sample_indices=duration_indices,
                    )
                )
            if configured_horizon is not None:
                result["rho_evaluated_fraction"] = float(
                    np.mean(duration_valid & (configured_horizon > 0.0))
                )

    command_components = {
        component: _available_matrix(aligned, f"command_{component}")
        for component in ("p", "v", "a")
    }
    if all(executable_present):
        follower_target = executable_components_by_name
        result["follower_target_source"] = "executable_target"
    else:
        follower_target = raw_components_by_name
        if all(raw_present):
            result["follower_target_source"] = "raw_target"
    # The row contract is target[k] -> command/output[k+1].  Both values are
    # stored on row k, so follower distortion is paired directly by row; it is
    # not re-clocked against a later target row.
    for component, label in (
        ("p", "position"),
        ("v", "velocity"),
        ("a", "acceleration"),
    ):
        command_value = command_components[component]
        target_value = follower_target[component]
        if command_value is not None and target_value is not None:
            result.update(
                state_error_metrics(
                    command_value,
                    target_value,
                    prefix=f"follower_{label}",
                )
            )
    plant_components = {
        component: _available_matrix(aligned, f"plant_{component}")
        for component in ("p", "v", "a")
    }
    for component, label in (
        ("p", "position"),
        ("v", "velocity"),
        ("a", "acceleration"),
    ):
        plant_value = plant_components[component]
        command_value = command_components[component]
        if plant_value is not None and command_value is not None:
            result.update(
                state_error_metrics(
                    plant_value,
                    command_value,
                    prefix=f"plant_{label}",
                )
            )

    for field in (
        "estimator_compute_us",
        "predictor_compute_us",
        "governor_compute_us",
        "follower_compute_us",
        "plant_compute_us",
        "total_compute_us",
    ):
        available = _partially_available_matrix(aligned, field)
        if available is None:
            continue
        values, valid_cycles = available
        local_deadline = deadline_us
        if local_deadline is None:
            dt_control = _available_matrix(aligned, "dt_control")
            if dt_control is None:
                raise MetricValidationError("dt_control is unavailable")
            local_deadline = 1e6 * float(np.min(dt_control))
        # One synchronized n-DoF call is commonly repeated on every per-joint
        # schema row.  The per-cycle maximum avoids multiplying that runtime by
        # DoF and remains conservative if instrumentation differs by joint.
        prefix = field.removesuffix("_compute_us")
        result.update(
            runtime_metrics(
                np.max(values, axis=1),
                deadline_us=local_deadline,
                prefix=prefix,
            )
        )
        result[f"{prefix}_evaluated_fraction"] = float(np.mean(valid_cycles))
        result[f"{prefix}_unavailable_count"] = int(
            valid_cycles.size - np.count_nonzero(valid_cycles)
        )

    for field, prefix in (
        ("deadline_miss", "recorded_deadline_miss"),
        ("state_reset", "state_reset"),
        ("invalid_input", "invalid_input"),
    ):
        flags = _boolean_vector(aligned, field)
        if flags is not None:
            result[f"{prefix}_count"] = int(np.count_nonzero(flags))
            result[f"{prefix}_rate"] = float(np.mean(flags))
    for field in (
        "raw_target_ruckig_admissible",
        "executable_target_available",
        "executable_target_point_admissible",
        "executable_target_stopping_viable",
        "executable_target_segment_feasible",
        "executable_target_t_free_le_dt",
        "command_t_free_le_dt",
        "command_segment_feasible",
        "command_stopping_viable",
        "command_next_step_exists",
        "command_continuous_constraints_satisfied",
        "fallback_requested",
        "fallback_applied",
        "safety_guarantee",
        "emergency_mode",
    ):
        flags = _boolean_vector(aligned, field)
        if flags is not None:
            result[f"{field}_count"] = int(np.count_nonzero(flags))
            result[f"{field}_rate"] = float(np.mean(flags))
    qp_iterations = _partially_available_matrix(aligned, "qp_iterations")
    if qp_iterations is not None:
        values, valid = qp_iterations
        result.update(
            {
                "qp_iteration_evaluated_fraction": float(np.mean(valid)),
                "qp_iteration_p50": _quantile(values, 0.5),
                "qp_iteration_p90": _quantile(values, 0.9),
                "qp_iteration_p99": _quantile(values, 0.99),
                "qp_iteration_max": float(np.max(values)),
            }
        )

    qp_categories = _optional_synchronized_categories(aligned, "qp_status_category")
    if qp_categories is not None:
        denominator = len(qp_categories)
        result["qp_status_evaluated_count"] = denominator
        result["qp_status_evaluated_fraction"] = denominator / len(aligned[0])
        for category in QP_FAILURE_CATEGORIES:
            count = sum(value == category for value in qp_categories)
            result[f"{category}_count"] = count
            result[f"{category}_rate"] = count / denominator
        solved_count = sum(value == "qp_solved" for value in qp_categories)
        result["qp_solved_count"] = solved_count
        result["qp_solved_rate"] = solved_count / denominator
        recognized = set(QP_FAILURE_CATEGORIES) | {"qp_solved"}
        other_count = sum(value not in recognized for value in qp_categories)
        result["qp_other_status_count"] = other_count
        result["qp_other_status_rate"] = other_count / denominator

    for field, prefix in (
        ("qp_solve_time_us", "qp_solve_time"),
        ("qp_primal_residual", "qp_primal_residual"),
        ("qp_dual_residual", "qp_dual_residual"),
        ("qp_hessian_condition_number", "qp_hessian_condition_number"),
        ("qp_constraint_condition_number", "qp_constraint_condition_number"),
    ):
        available = _partially_available_matrix(aligned, field)
        if available is None:
            continue
        values, valid = available
        cycle_values = np.max(values, axis=1)
        unit = "_us" if field == "qp_solve_time_us" else ""
        result[f"{prefix}_evaluated_fraction"] = float(np.mean(valid))
        result[f"{prefix}_p50{unit}"] = _quantile(cycle_values, 0.5)
        result[f"{prefix}_p90{unit}"] = _quantile(cycle_values, 0.9)
        result[f"{prefix}_p99{unit}"] = _quantile(cycle_values, 0.99)
        result[f"{prefix}_max{unit}"] = float(np.max(cycle_values))

    if motion_limits is not None:
        command_v = _available_matrix(aligned, "command_v")
        command_a = _available_matrix(aligned, "command_a")
        if command_v is not None and command_a is not None:
            vmax, amax, jmax = _limit_vectors(
                motion_limits["max_velocity"],
                motion_limits["max_acceleration"],
                motion_limits["max_jerk"],
                command_v.shape[1],
            )
            max_v = np.max(np.abs(command_v), axis=0)
            max_a = np.max(np.abs(command_a), axis=0)
            result.update(
                {
                    "sampled_output_max_velocity": float(np.max(max_v)),
                    "sampled_output_max_acceleration": float(np.max(max_a)),
                    "sampled_output_velocity_margin": float(np.min(vmax - max_v)),
                    "sampled_output_acceleration_margin": float(np.min(amax - max_a)),
                    "sampled_output_velocity_violation_count": int(
                        np.count_nonzero(np.abs(command_v) > vmax[None, :] + 1e-8)
                    ),
                    "sampled_output_acceleration_violation_count": int(
                        np.count_nonzero(np.abs(command_a) > amax[None, :] + 1e-8)
                    ),
                }
            )
            if output_time.size > 1:
                sampled_jerk = (
                    np.diff(command_a, axis=0) / np.diff(output_time)[:, None]
                )
                result.update(
                    {
                        "sampled_output_max_sampled_jerk": float(
                            np.max(np.abs(sampled_jerk))
                        ),
                        "sampled_output_sampled_jerk_margin": float(
                            np.min(jmax - np.max(np.abs(sampled_jerk), axis=0))
                        ),
                    }
                )
            # Preserve all three jerk semantics from the canonical artifact.
            # A finite difference of command acceleration above is retained as
            # an independent cross-check, never relabeled as either new_jerk or
            # internal profile jerk.
            for field, label in (
                ("sampled_jerk", "sampled"),
                ("new_jerk", "new"),
                ("internal_trajectory_jerk", "internal"),
            ):
                available = _partially_available_matrix(aligned, field)
                if available is None:
                    continue
                jerk_values, valid = available
                maximum_by_joint = np.max(np.abs(jerk_values), axis=0)
                result.update(
                    {
                        f"sampled_output_{label}_jerk_available_fraction": float(
                            np.mean(valid)
                        ),
                        f"sampled_output_max_{label}_jerk": float(
                            np.max(maximum_by_joint)
                        ),
                        f"sampled_output_{label}_jerk_margin": float(
                            np.min(jmax - maximum_by_joint)
                        ),
                        f"sampled_output_{label}_jerk_violation_count": int(
                            np.count_nonzero(np.abs(jerk_values) > jmax[None, :] + 1e-8)
                        ),
                    }
                )
    return result


def metrics_by_trajectory(
    samples: Iterable[Mapping[str, Any]],
    *,
    output_field: str | None = None,
    settle_tolerance: float = 1e-3,
    max_lag_s: float = 1.0,
    deadline_us: float | None = None,
    motion_limits: Mapping[str, ArrayLike | float] | None = None,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Independently recompute one aggregate row per complete trajectory.

    Joint rows are synchronized and combined before aggregation, so a 7-DoF
    trajectory still contributes exactly one statistical unit.  ``context``
    may add locked configuration labels (method, estimator, predictor, etc.)
    which are intentionally not inferred from numeric samples.
    """

    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    count = 0
    for sample in samples:
        count += 1
        try:
            key = (
                tuple(sample[field] for field in _IDENTITY_FIELDS)
                + tuple(sample.get(field) for field in _METHOD_FIELD_MAP)
                + (sample.get("method_id"),)
            )
        except KeyError as error:
            raise MetricValidationError(
                f"sample is missing identity field {error.args[0]}"
            ) from error
        grouped[key].append(sample)
    if count == 0:
        raise MetricValidationError("samples artifact is empty")
    rows = [
        _trajectory_metric_row(
            grouped[key],
            output_field=output_field,
            settle_tolerance=settle_tolerance,
            max_lag_s=max_lag_s,
            deadline_us=deadline_us,
            motion_limits=motion_limits,
            context=dict(context or {}),
        )
        for key in sorted(grouped, key=lambda item: tuple(str(value) for value in item))
    ]
    trajectory_ids = [
        (
            row["run_id"],
            row["dataset_id"],
            row["session_id"],
            row["trajectory_id"],
            row["scenario_id"],
            row["method"],
        )
        for row in rows
    ]
    if len(set(trajectory_ids)) != len(trajectory_ids):
        raise MetricValidationError(
            "metrics contain duplicate trajectory statistical units"
        )
    return rows


def summary_metrics(
    trajectory_rows: Sequence[Mapping[str, Any]],
    *,
    group_fields: Sequence[str] = ("run_id", "split", "method", "scenario_id"),
    metric_fields: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Create a deterministic long-form summary from trajectory-level rows."""

    if not trajectory_rows:
        raise MetricValidationError("trajectory metrics table is empty")
    for field in group_fields:
        if any(field not in row for row in trajectory_rows):
            raise MetricValidationError(f"group field {field} is missing")
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in trajectory_rows:
        grouped[tuple(row[field] for field in group_fields)].append(row)
    if metric_fields is None:
        excluded = (
            set(group_fields)
            | set(_IDENTITY_FIELDS)
            | {
                "tracking_output_field",
                "method",
                "estimator",
                "predictor",
                "governor",
                "follower",
                "plant",
            }
        )
        candidates = set.union(*(set(row) for row in trajectory_rows)) - excluded
        metric_fields = sorted(
            field
            for field in candidates
            if all(
                isinstance(row[field], (int, float, np.integer, np.floating))
                and not isinstance(row[field], (bool, np.bool_))
                for row in trajectory_rows
                if field in row
            )
        )
    if not metric_fields:
        raise MetricValidationError("no numeric metric fields were selected")

    output: list[dict[str, Any]] = []
    for group_key in sorted(
        grouped, key=lambda item: tuple(str(value) for value in item)
    ):
        rows = grouped[group_key]
        for metric in metric_fields:
            availability = [metric in row for row in rows]
            if not any(availability):
                # Layer-specific metrics can be inapplicable to an entire
                # method (for example plant or governor-only measurements).
                continue
            if not all(availability):
                if metric in OPTIONAL_REACHABILITY_SUBSET_METRICS:
                    continue
                raise MetricValidationError(
                    f"metric {metric} is partially available in group {group_key}"
                )
            values = _finite_array([row[metric] for row in rows], metric, ndim=1)
            output.append(
                {
                    **dict(zip(group_fields, group_key)),
                    "metric": metric,
                    "n_trajectories": int(values.size),
                    "mean": float(np.mean(values)),
                    "median": float(np.median(values)),
                    "q25": _quantile(values, 0.25),
                    "q75": _quantile(values, 0.75),
                    "iqr": _quantile(values, 0.75) - _quantile(values, 0.25),
                    "minimum": float(np.min(values)),
                    "maximum": float(np.max(values)),
                }
            )
    return output


__all__ = [
    "MetricValidationError",
    "audit_constant_jerk_segments",
    "audit_sampled_continuous_trajectory",
    "best_lag_metrics",
    "constant_jerk_segment_extrema",
    "detect_reference_events",
    "estimator_metrics",
    "frequency_response_metrics",
    "governor_metrics",
    "interpolate_truth_at_times",
    "local_delay_metrics",
    "metrics_by_trajectory",
    "prediction_metrics",
    "runtime_metrics",
    "state_error_metrics",
    "summary_metrics",
    "timed_state_error_metrics",
    "tracking_metrics",
]
