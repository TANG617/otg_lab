"""Deterministic tests for split-safe research benchmark orchestration."""

from __future__ import annotations

import math

import numpy as np
import pytest

from otg_lab.benchmarks import (
    ACCELERATION_ACTIVE_PHASES,
    FreeDurationUnavailable,
    SelectionLeakageError,
    acceleration_phase_design,
    benchmark_runtime,
    build_acceleration_phase_map,
    evaluate_locked_predictor,
    lock_estimator_parameters,
    rank_estimator_grid,
    rank_prediction_horizons,
    ruckig_unconstrained_free_duration,
    run_estimator_grid,
    run_predictor_horizon_sweep,
    sampling_rate_dimensionless,
    summarize_t_free_rho,
)
from otg_lab.estimators import Estimator
from otg_lab.schema import empty_sample, validate_samples
from otg_lab.types import Measurement, TimedState


def _truth_rows(
    times,
    position,
    velocity,
    acceleration,
    jerk,
    *,
    trajectory_id="trajectory",
    split="validation",
    family="analytic",
):
    times = np.asarray(times, dtype=float)
    output = []
    for k, (time, p, v, a, j) in enumerate(
        zip(times, position, velocity, acceleration, jerk)
    ):
        dt = float(times[1] - times[0]) if k == 0 else float(time - times[k - 1])
        output.append(
            empty_sample(
                run_id="benchmark-test",
                dataset_id="synthetic-unit",
                session_id="session",
                trajectory_id=trajectory_id,
                split=split,
                seed=17,
                joint_id="joint_0",
                k=k,
                source_time=float(time),
                arrival_time=float(time),
                control_time=float(time),
                dt_actual=dt,
                dt_control=float(times[1] - times[0]),
                p_ref=float(p),
                v_ref_truth=float(v),
                a_ref_truth=float(a),
                j_ref_truth=float(j),
                p_meas=float(p),
                source_kind="synthetic_feasible",
                reference_family=family,
                scenario_id="clean",
                truth_available=True,
                measurement_available=True,
                measurement_valid=True,
            )
        )
    validate_samples(output)
    return output


def _quadratic_rows(*, split="validation"):
    times = np.arange(0.0, 2.0 + 0.05, 0.1)
    return _truth_rows(
        times,
        times**2,
        2.0 * times,
        np.full_like(times, 2.0),
        np.zeros_like(times),
        split=split,
        family="constant_acceleration",
    )


def test_estimator_grid_aligns_truth_to_posterior_time_and_locks_full_ranking():
    rows = _quadratic_rows()
    metrics, canonical = run_estimator_grid(
        rows,
        [
            {"method": "position_only", "id": "position"},
            {"method": "delay_one_centered_difference", "id": "centered"},
        ],
        selection_splits=("validation",),
        return_canonical_rows=True,
    )

    assert len(metrics) == 2
    centered = metrics.set_index("estimator_id").loc["centered"]
    # The centered posterior belongs to k-1.  Its exact quadratic position is
    # zero-error only when scored at posterior.state_time rather than update k.
    assert centered["estimator_p_rmse"] < 1e-14
    centered_rows = [row for row in canonical if row["estimator_id"] == "centered"]
    assert centered_rows[1]["posterior_state_time"] == pytest.approx(0.0)
    assert centered_rows[1]["source_time"] == pytest.approx(0.1)
    assert centered["startup_samples"] == 2
    assert centered["estimator_p99_us"] >= 0.0
    validate_samples(canonical)

    ranking = rank_estimator_grid(metrics)
    assert list(ranking["rank"]) == [1, 2]
    assert set(ranking["estimator_id"]) == {"position", "centered"}
    assert ranking.iloc[0]["estimator_id"] == "centered"
    locked = lock_estimator_parameters(ranking)
    assert locked["locked"] is True
    assert locked["locked_estimators"][0]["estimator_id"] == "centered"
    assert locked["locked_estimators"][0]["selection_rank"] == 1


def test_estimator_and_horizon_selection_reject_locked_test_rows():
    rows = _quadratic_rows(split="test")
    with pytest.raises(SelectionLeakageError, match="test"):
        run_estimator_grid(
            rows,
            ["position_only"],
            selection_splits=("validation",),
        )
    locked = {"method": "position_only", "estimator_id": "locked-position"}
    with pytest.raises(SelectionLeakageError, match="test"):
        run_predictor_horizon_sweep(
            rows,
            locked,
            ["constant_velocity"],
            horizons_ms=(0.0, 10.0),
            stress_horizons_ms=(),
            selection_splits=("validation",),
        )


def test_unconstrained_free_duration_never_sets_prediction_horizon_as_minimum():
    posterior = TimedState(
        position=[0.0],
        velocity=[0.0],
        acceleration=[0.0],
        state_time=0.0,
        available_time=0.0,
        method="unit",
    )
    prediction = TimedState(
        position=[0.1],
        velocity=[0.0],
        acceleration=[0.0],
        state_time=0.15,
        available_time=0.0,
        method="unit_prediction",
        source_state_time=0.0,
        prediction_horizon=0.15,
    )
    duration = ruckig_unconstrained_free_duration(prediction, posterior)
    assert duration > 0.0
    assert duration != pytest.approx(prediction.prediction_horizon)


def test_unconstrained_free_duration_marks_out_of_limit_target_unavailable():
    posterior = TimedState(
        position=[0.0],
        velocity=[0.0],
        acceleration=[0.0],
        state_time=0.0,
        available_time=0.0,
        method="unit",
    )
    prediction = TimedState(
        position=[0.1],
        velocity=[0.0],
        acceleration=[8.3],
        state_time=0.01,
        available_time=0.0,
        method="unit_prediction",
        source_state_time=0.0,
        prediction_horizon=0.01,
    )

    with pytest.raises(FreeDurationUnavailable, match="target acceleration"):
        ruckig_unconstrained_free_duration(prediction, posterior)


def test_locked_predictor_retains_unavailable_t_free_in_denominator():
    rows = _quadratic_rows()

    def partial_duration(prediction, posterior, row):
        del prediction, posterior
        if int(row["k"]) % 2 == 0:
            raise FreeDurationUnavailable("deliberate unavailable target")
        return 0.005

    metrics, canonical = evaluate_locked_predictor(
        rows,
        {"method": "position_only", "estimator_id": "locked-position"},
        {"method": "zero_order_hold", "id": "locked-zoh"},
        horizons_ms=(10.0,),
        free_duration_fn=partial_duration,
        return_canonical_rows=True,
    )

    result = metrics.iloc[0]
    assert result["t_free_requested_samples"] == len(rows)
    assert result["t_free_unavailable_samples"] == (len(rows) + 1) // 2
    assert result["t_free_samples"] == len(rows) // 2
    assert result["t_free_available_fraction"] == pytest.approx(
        (len(rows) // 2) / len(rows)
    )
    unavailable = [row for row in canonical if row["free_trajectory_duration"] is None]
    assert unavailable
    assert all(row["target_feasible"] is None for row in unavailable)
    assert all("unavailable" in row["solver_status"] for row in unavailable)
    validate_samples(canonical)


class _DelayedExactLinearEstimator(Estimator):
    name = "delayed_exact_linear"

    def _update_valid(self, measurement: Measurement, dt: float | None) -> TimedState:
        lag = 0.0 if self._sample_count == 0 else float(self.nominal_dt)
        state_time = measurement.state_time - lag
        return TimedState(
            position=[state_time],
            velocity=[1.0],
            acceleration=[0.0],
            state_time=state_time,
            available_time=measurement.available_time,
            method=self.name,
            startup=self._sample_count == 0,
        )


def _delayed_linear_factory(name, **params):
    assert name == "delayed_linear"
    return _DelayedExactLinearEstimator(**params)


def test_predictor_sweep_reports_configured_and_actual_horizon_separately():
    times = np.arange(0.0, 0.20 + 0.005, 0.01)
    rows = _truth_rows(
        times,
        times,
        np.ones_like(times),
        np.zeros_like(times),
        np.zeros_like(times),
        family="linear",
    )
    metrics, canonical = run_predictor_horizon_sweep(
        rows,
        {"method": "delayed_linear", "estimator_id": "locked-delayed"},
        ["constant_velocity"],
        horizons_ms=(0.0, 10.0),
        stress_horizons_ms=(),
        nominal_dt=0.01,
        selection_splits=("validation",),
        estimator_factory=_delayed_linear_factory,
        return_canonical_rows=True,
    )
    horizon_10 = metrics.loc[metrics["configured_horizon_ms"] == 10.0].iloc[0]
    assert horizon_10["prediction_p_rmse"] < 1e-14
    assert horizon_10["actual_propagation_horizon_ms"] > 19.0
    assert horizon_10["prediction_horizon_ms"] == 10.0
    assert set(metrics["horizon_set"]) == {"primary"}
    assert {row["predictor_id"] for row in canonical} == {
        "constant_velocity@0ms",
        "constant_velocity@10ms",
    }
    propagation = [
        row["prediction_horizon_ms"]
        for row in canonical
        if row["predictor_id"] == "constant_velocity@10ms" and row["k"] == 2
    ]
    assert propagation == pytest.approx([20.0])
    validate_samples(canonical)

    ranking = rank_prediction_horizons(metrics)
    assert ranking["eligible_for_selection"].all()
    assert set(ranking["rank"].astype(int)) == {1, 2}


def test_prediction_metrics_use_prediction_time_and_include_event_subsets():
    times = np.arange(0.0, 1.0 + 0.005, 0.01)
    omega = 2.0 * np.pi
    rows = _truth_rows(
        times,
        np.sin(omega * times),
        omega * np.cos(omega * times),
        -(omega**2) * np.sin(omega * times),
        -(omega**3) * np.cos(omega * times),
        family="rapid_reversal",
    )
    metrics = run_predictor_horizon_sweep(
        rows,
        {"method": "position_only", "estimator_id": "locked-position"},
        ["oracle"],
        horizons_ms=(10.0,),
        stress_horizons_ms=(),
        selection_splits=("validation",),
    )
    result = metrics.iloc[0]
    assert result["prediction_p_rmse"] < 1e-14
    assert result["prediction_v_rmse"] < 1e-14
    assert result["prediction_a_rmse"] < 1e-14
    assert result["reversal_sample_count"] > 0
    assert result["stop_sample_count"] > 0
    assert result["prediction_reversal_p_rmse"] < 1e-14
    assert result["prediction_stop_p_rmse"] < 1e-14
    assert result["prediction_evaluated_time_fraction"] < 1.0


def test_t_free_rho_uses_positive_h_and_counts_trajectory_segments():
    rho_a = [0.5, 1.2, 1.3, 0.8]
    rho_b = [1.1, 0.9, 1.2]
    samples = []
    for trajectory_id, values in (("a", rho_a), ("b", rho_b)):
        for k, rho in enumerate(values):
            samples.append(
                {
                    "trajectory_id": trajectory_id,
                    "k": k,
                    "control_time": k * 0.01,
                    "configured_horizon_ms": 10.0,
                    "free_trajectory_duration": rho * 0.01,
                }
            )
    # Undefined H=0 is ignored rather than reported as an infinite ratio.
    samples.append(
        {
            "trajectory_id": "zero",
            "k": 0,
            "control_time": 0.0,
            "configured_horizon_ms": 0.0,
            "free_trajectory_duration": 0.01,
        }
    )
    summary, detailed = summarize_t_free_rho(
        samples,
        group_fields=("configured_horizon_ms",),
        return_samples=True,
    )
    assert len(summary) == 1
    result = summary.iloc[0]
    assert result["rho_p50"] == pytest.approx(1.1)
    assert result["rho_le_one_fraction"] == pytest.approx(3.0 / 7.0)
    assert result["rho_exceedance_segment_count"] == 3
    assert result["rho_longest_exceedance_samples"] == 2
    assert set(detailed["trajectory_id"]) == {"a", "b"}


def test_acceleration_oracle_design_is_complete_and_phase_map_keeps_harmful_pairs():
    design = acceleration_phase_design(
        r_a_strata={"one": 0.5},
        r_j_strata={"one": 0.75},
    )
    assert len(design) == len(ACCELERATION_ACTIVE_PHASES) * 2 * 6
    assert set(design["phase"]) == set(ACCELERATION_ACTIVE_PHASES)
    assert set(design["direction"]) == {-1, 1}
    condition_matrix = set(
        zip(
            design["target_time_mode"],
            design["configured_horizon_ms"],
            design["target_mode"],
        )
    )
    assert condition_matrix == {
        ("current", 0.0, "p"),
        ("current", 0.0, "pv"),
        ("current", 0.0, "pva"),
        ("next_cycle", 10.0, "p"),
        ("next_cycle", 10.0, "pv"),
        ("next_cycle", 10.0, "pva"),
    }

    records = []
    index = 0
    for phase in ACCELERATION_ACTIVE_PHASES:
        for direction in (-1, 1):
            trajectory_id = f"{phase}-{direction}"
            pva_rmse = 1.2 if index == 0 else 0.8
            for mode, rmse, lag in (("pv", 1.0, 0.03), ("pva", pva_rmse, 0.02)):
                records.append(
                    {
                        "dataset_id": "acceleration-active",
                        "session_id": "oracle",
                        "trajectory_id": trajectory_id,
                        "phase": phase,
                        "r_a": 0.5,
                        "r_j": 0.75,
                        "direction": direction,
                        "target_time_mode": "current",
                        "configured_horizon_ms": 0.0,
                        "predictor_id": "oracle_future_state_offline",
                        "target_mode": mode,
                        "position_rmse": rmse,
                        "lag_s": lag,
                    }
                )
            index += 1
    phase_map, pairs = build_acceleration_phase_map(
        records,
        expected_r_a=(0.5,),
        expected_r_j=(0.75,),
        return_pairs=True,
    )
    assert len(phase_map) == 1
    assert phase_map.iloc[0]["negative_pair_count"] == 1
    assert phase_map.iloc[0]["positive_pair_count"] == len(pairs) - 1
    assert pairs["pva_vs_pv_rmse_improvement"].min() == pytest.approx(-0.2)
    assert phase_map.iloc[0]["pva_vs_pv_rmse_improvement_min"] == pytest.approx(-0.2)


def test_sampling_rate_chi_values_and_runtime_warmup_repetitions():
    rates = sampling_rate_dimensionless()
    assert list(rates["sample_rate_hz"]) == [50.0, 100.0, 200.0, 500.0]
    primary = rates.loc[rates["sample_rate_hz"] == 100.0].iloc[0]
    assert primary["chi_j"] == pytest.approx(8.2 / (4000.0 * 0.01))
    assert primary["chi_a"] == pytest.approx(4.1 / (8.2 * 0.01))
    assert bool(primary["primary_condition"])

    calls = {"count": 0}

    def task():
        calls["count"] += 1
        return math.sqrt(4.0)

    summary, samples = benchmark_runtime(
        {"sqrt": task},
        warmup=3,
        repetitions=5,
        calls_per_repetition=2,
        deadline_us=1e9,
        return_samples=True,
    )
    assert calls["count"] == (3 + 5) * 2
    assert len(samples) == 5
    assert summary.iloc[0]["warmup_repetitions"] == 3
    assert summary.iloc[0]["repetitions"] == 5
    assert summary.iloc[0]["runtime_p99_us"] >= 0.0
    assert summary.iloc[0]["deadline_miss_rate"] == 0.0
