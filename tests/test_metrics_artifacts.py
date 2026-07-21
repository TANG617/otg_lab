import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from otg_lab.artifacts import (
    ArtifactValidationError,
    ArtifactWriter,
    assert_clean_commit,
    capture_git_state,
    sha256_file,
    validate_artifact_bundle,
    validate_artifact_schema,
    verify_checksums,
    verify_recomputed_summary,
    write_csv,
)
from otg_lab.figures import (
    FigureValidationError,
    plot_representative_traces,
    plot_same_information_ablation,
    select_representative_trajectories,
)
from otg_lab.metrics import (
    MetricValidationError,
    audit_sampled_continuous_trajectory,
    constant_jerk_segment_extrema,
    estimator_metrics,
    frequency_response_metrics,
    governor_metrics,
    metrics_by_trajectory,
    prediction_metrics,
    runtime_metrics,
    summary_metrics,
    tracking_metrics,
)
from otg_lab.schema import empty_sample
from otg_lab.statistics import (
    StatisticalValidationError,
    holm_adjust,
    paired_comparison_from_records,
    paired_trajectory_bootstrap,
)


def _canonical_sample(
    k: int,
    *,
    trajectory_id: str = "trajectory-001",
    joint_id: str = "joint-0",
    joint_offset: float = 0.0,
) -> dict:
    time = 0.01 * k
    reference = joint_offset + 0.05 * k
    # Row k carries a target/command for physical time k+1.  At that time the
    # continuing reference is reference + 0.05, leaving a deliberate 0.01 rad
    # raw-time tracking error.
    future_reference = reference + 0.05
    command = future_reference + 0.01
    return empty_sample(
        run_id="run-001",
        dataset_id="synthetic-v1",
        session_id="session-001",
        trajectory_id=trajectory_id,
        split="test",
        seed=101,
        joint_id=joint_id,
        k=k,
        source_time=time,
        arrival_time=time,
        control_time=time,
        dt_actual=0.01,
        dt_control=0.01,
        p_ref=reference,
        v_ref_truth=5.0,
        a_ref_truth=0.0,
        j_ref_truth=0.0,
        p_meas=reference,
        v_meas=None,
        a_meas=None,
        posterior_p=reference,
        posterior_v=5.0,
        posterior_a=0.0,
        posterior_state_time=time,
        posterior_available_time=time,
        prediction_p=reference,
        prediction_v=5.0,
        prediction_a=0.0,
        prediction_time=time,
        prediction_horizon_ms=0.0,
        raw_target_p=future_reference,
        raw_target_v=5.0,
        raw_target_a=0.0,
        raw_target_time=time + 0.01,
        executable_target_p=command,
        executable_target_v=4.0,
        executable_target_a=0.0,
        executable_target_time=time + 0.01,
        command_p=command,
        command_v=4.0,
        command_a=0.0,
        command_jerk=0.0,
        sampled_jerk=0.0,
        new_jerk=0.0,
        internal_trajectory_jerk=0.0,
        command_time=time + 0.01,
        plant_p=None,
        plant_v=None,
        plant_a=None,
        target_feasible=True,
        target_projected=False,
        fallback=False,
        fallback_reason="",
        solver_status="solved",
        qp_iterations=2,
        qp_status_category="qp_solved",
        qp_solve_time_us=20.0 + k,
        qp_primal_residual=1e-6,
        qp_dual_residual=2e-6,
        qp_hessian_condition_number=10.0,
        qp_constraint_condition_number=4.0,
        deadline_miss=False,
        state_reset=False,
        invalid_input=False,
        free_trajectory_duration=0.01,
        estimator_compute_us=2.0 + k,
        predictor_compute_us=1.0,
        governor_compute_us=3.0,
        follower_compute_us=4.0,
        plant_compute_us=0.5,
        total_compute_us=10.0 + k,
        source_kind="synthetic",
        reference_family="constant_velocity",
        scenario_id="clean",
        truth_available=True,
        measurement_available=True,
        measurement_valid=True,
    )


class TestTrackingAndLayerMetrics:
    def test_qp_metrics_preserve_failure_categories_and_solver_observability(self):
        samples = [_canonical_sample(k) for k in range(8)]
        failures = (
            "qp_time_limit_reached",
            "qp_max_iter_reached",
            "qp_primal_infeasible",
            "qp_dual_infeasible",
            "qp_numerical_failure",
            "qp_postcheck_failed",
        )
        for row, category in zip(samples, failures):
            row["qp_status_category"] = category
            row["fallback"] = True
            row["fallback_requested"] = True
            row["fallback_applied"] = True
            row["fallback_reason"] = category

        result = metrics_by_trajectory(samples)[0]

        for category in failures:
            assert result[f"{category}_count"] == 1
            assert result[f"{category}_rate"] == pytest.approx(1 / 8)
        assert result["qp_solved_count"] == 2
        assert result["qp_solve_time_p99_us"] > 20.0
        assert result["qp_primal_residual_max"] == pytest.approx(1e-6)
        assert result["qp_hessian_condition_number_max"] == pytest.approx(10.0)

    def test_runtime_metrics_retain_dropout_cycles_in_availability_denominator(self):
        samples = [_canonical_sample(k) for k in range(8)]
        for row in samples[::2]:
            row["estimator_compute_us"] = None

        result = metrics_by_trajectory(samples)[0]

        assert result["estimator_count"] == 4
        assert result["estimator_evaluated_fraction"] == pytest.approx(0.5)
        assert result["estimator_unavailable_count"] == 4
        assert result["total_count"] == 8
        assert result["total_evaluated_fraction"] == pytest.approx(1.0)
        assert result["total_unavailable_count"] == 0

    def test_runtime_field_cannot_be_present_for_only_one_joint_in_a_cycle(self):
        samples = []
        for k in range(4):
            samples.extend(
                [
                    _canonical_sample(k, joint_id="joint-0"),
                    _canonical_sample(k, joint_id="joint-1", joint_offset=0.1),
                ]
            )
        samples[1]["estimator_compute_us"] = None

        with pytest.raises(MetricValidationError, match="part of a synchronized"):
            metrics_by_trajectory(samples)

    def test_raw_time_metrics_and_secondary_lag_are_separate(self):
        time = np.arange(0.0, 1.0, 0.01)
        reference = np.sin(2.0 * np.pi * time)
        output = np.concatenate(([reference[0]], reference[:-1]))
        result = tracking_metrics(
            reference,
            output,
            time,
            settle_tolerance=0.0,
            max_lag_s=0.05,
        )
        assert result["lag_samples"] == 1
        assert result["lag_s"] == pytest.approx(0.01)
        assert result["lag_aligned_rmse"] == pytest.approx(0.0, abs=1e-15)
        assert result["position_rmse"] > 0.04
        assert result["position_mae"] > 0.0
        assert result["position_p95_abs_error"] <= result["position_max_abs_error"]
        assert result["position_iae"] > 0.0
        assert result["settle_time_censored"] is True

    def test_phase_and_group_delay_use_physical_sign(self):
        dt = 0.001
        time = np.arange(0.0, 4.0, dt)
        delay = 0.02
        frequencies = [2.0, 5.0, 9.0]
        reference = sum(np.sin(2.0 * np.pi * f * time) for f in frequencies)
        output = sum(np.sin(2.0 * np.pi * f * (time - delay)) for f in frequencies)
        rows = frequency_response_metrics(
            reference,
            output,
            time,
            frequencies_hz=frequencies,
        )
        assert len(rows) == len(frequencies)
        for row in rows:
            assert row["gain"] == pytest.approx(1.0, rel=1e-11)
            assert row["phase_delay_s"] == pytest.approx(delay, abs=1e-11)
            assert row["group_delay_s"] == pytest.approx(delay, abs=1e-11)

    def test_runtime_quantiles_and_deadline(self):
        result = runtime_metrics(
            [1.0, 2.0, 3.0, 20.0], deadline_us=10.0, prefix="total"
        )
        assert result["total_p50_us"] == pytest.approx(2.5)
        assert result["total_p90_us"] == pytest.approx(14.9)
        assert result["total_p99_9_us"] > result["total_p99_us"]
        assert result["total_max_us"] == 20.0
        assert result["total_deadline_miss_count"] == 1
        assert result["total_deadline_miss_rate"] == pytest.approx(0.25)

    def test_estimator_and_prediction_use_represented_physical_times(self):
        truth_time = np.arange(0.0, 0.11, 0.01)
        truth_p = truth_time**2
        posterior_time = truth_time[:8]
        estimate = estimator_metrics(
            posterior_times=posterior_time,
            posterior_available_times=posterior_time + 0.01,
            posterior_position=posterior_time**2,
            truth_times=truth_time,
            truth_position=truth_p,
            startup_mask=[True, True, False, False, False, False, False, False],
            measurement_position=posterior_time**2 + 0.1,
        )
        assert estimate["estimator_p_rmse"] == pytest.approx(0.0)
        assert estimate["posterior_lag_s"] == pytest.approx(0.01)
        assert estimate["estimator_startup_samples"] == 2

        prediction_time = truth_time[2:9]
        prediction = prediction_metrics(
            prediction_times=prediction_time,
            prediction_position=prediction_time**2,
            truth_times=truth_time,
            truth_position=truth_p,
            prediction_horizon_ms=np.full(prediction_time.size, 20.0),
            reversal_mask=[False, False, True, False, False, False, False],
        )
        assert prediction["prediction_p_rmse"] == pytest.approx(0.0)
        assert prediction["prediction_horizon_mean_ms"] == pytest.approx(20.0)
        assert prediction["prediction_reversal_p_rmse"] == pytest.approx(0.0)

    def test_governor_reports_genuine_t_free_rho_segments_and_time_shift(self):
        zeros = np.zeros((5, 3))
        result = governor_metrics(
            zeros,
            zeros,
            raw_target_time=np.arange(5, dtype=float) * 0.01,
            executable_target_time=np.arange(5, dtype=float) * 0.01 + 0.002,
            free_trajectory_duration=[0.005, 0.012, 0.013, 0.014, 0.008],
            # H=0 is undefined and must break, rather than join, adjacent runs.
            prediction_horizon_s=[0.01, 0.01, 0.0, 0.01, 0.01],
            dt=0.01,
        )
        assert result["governor_target_time_shift_s_bias"] == pytest.approx(0.002)
        assert result["governor_target_time_shift_s_max_abs_error"] == pytest.approx(
            0.002
        )
        assert result["rho_evaluated_fraction"] == pytest.approx(0.8)
        assert result["rho_p50"] == pytest.approx(1.0)
        assert result["rho_le_one_fraction"] == pytest.approx(0.5)
        assert result["rho_exceedance_segment_count"] == 2
        assert result["rho_longest_exceedance_samples"] == 1
        assert result["rho_total_exceedance_duration_s"] == pytest.approx(0.02)

    def test_complete_trajectory_is_one_unit_across_joints(self):
        samples = [
            _canonical_sample(k, joint_id=joint, joint_offset=offset)
            for joint, offset in (("joint-0", 0.0), ("joint-1", 1.0))
            for k in range(8)
        ]
        metrics = metrics_by_trajectory(
            samples,
            context={"method": "governed_pva"},
            motion_limits={
                "max_velocity": 4.1,
                "max_acceleration": 8.2,
                "max_jerk": 4000.0,
            },
        )
        assert len(metrics) == 1
        row = metrics[0]
        assert row["n_joints"] == 2
        assert row["recorded_samples"] == 8
        assert row["n_samples"] == 7
        assert row["tracking_evaluated_fraction"] == pytest.approx(7 / 8)
        assert row["tracking_reference_time_field"] == "control_time"
        assert row["tracking_output_time_field"] == "command_time"
        assert row["position_rmse"] == pytest.approx(0.01)
        assert row["method"] == "governed_pva"
        assert row["estimator_p_rmse"] == pytest.approx(0.0)
        assert row["prediction_p_rmse"] == pytest.approx(0.0)
        assert row["governor_position_distortion_rmse"] == pytest.approx(0.01)
        assert row["target_feasible_rate"] == pytest.approx(1.0)
        assert row["total_p99_us"] < 18.0
        assert row["sampled_output_max_new_jerk"] == 0.0
        summary = summary_metrics(metrics, metric_fields=["position_rmse"])
        assert summary == [
            {
                "run_id": "run-001",
                "split": "test",
                "method": "governed_pva",
                "scenario_id": "clean",
                "metric": "position_rmse",
                "n_trajectories": 1,
                "mean": pytest.approx(0.01),
                "median": pytest.approx(0.01),
                "q25": pytest.approx(0.01),
                "q75": pytest.approx(0.01),
                "iqr": pytest.approx(0.0),
                "minimum": pytest.approx(0.01),
                "maximum": pytest.approx(0.01),
            }
        ]

    def test_held_source_timestamp_does_not_reclock_position_reference(self):
        samples = [_canonical_sample(k) for k in range(8)]
        samples[3]["source_time"] = samples[2]["source_time"]
        samples[3]["event_held"] = True
        metrics = metrics_by_trajectory(samples)
        assert len(metrics) == 1
        assert metrics[0]["tracking_reference_time_field"] == "control_time"
        assert metrics[0]["position_rmse"] == pytest.approx(0.01)

    def test_no_governor_baseline_uses_raw_target_and_configured_horizon(self):
        samples = [_canonical_sample(k) for k in range(8)]
        for sample in samples:
            sample["governor_id"] = "none"
            sample["prediction_horizon_ms"] = 30.0
            sample["executable_target_p"] = None
            sample["executable_target_v"] = None
            sample["executable_target_a"] = None
            sample["executable_target_time"] = None
        row = metrics_by_trajectory(samples)[0]
        assert row["follower_target_source"] == "raw_target"
        assert row["follower_position_rmse"] == pytest.approx(0.01)
        assert "governor_position_distortion_rmse" not in row
        # Recorded propagation includes estimator lag; configured H is the
        # raw target's physical offset from the control clock and drives rho.
        assert row["prediction_propagation_horizon_mean_ms"] == pytest.approx(30.0)
        assert row["configured_prediction_horizon_mean_ms"] == pytest.approx(10.0)
        assert row["free_trajectory_duration_target_source"] == "raw_target"
        assert row["rho_p50"] == pytest.approx(1.0)
        assert row["rho_le_one_fraction"] == pytest.approx(1.0)

    def test_fallback_duration_is_excluded_and_provenance_is_explicit(self):
        samples = [_canonical_sample(k) for k in range(8)]
        samples[3]["fallback"] = True
        samples[3]["fallback_requested"] = True
        samples[3]["fallback_applied"] = True
        samples[3]["fallback_reason"] = "forced-test-fallback"
        # A fallback follower may report a free solve for its replacement
        # command.  It must not enter executable-target reachability or rho.
        samples[3]["free_trajectory_duration"] = 999.0
        row = metrics_by_trajectory(samples)[0]
        assert row["free_trajectory_duration_target_source"] == "executable_target"
        assert (
            row["free_trajectory_duration_definition"]
            == "follower_unconstrained_frozen_solve"
        )
        assert row["rho_horizon_definition"] == "raw_target_time_minus_control_time"
        assert row["free_trajectory_duration_excluded_fallback_count"] == 1
        assert row["free_trajectory_duration_evaluated_fraction"] == pytest.approx(
            7 / 8
        )
        assert row["free_trajectory_duration_max_s"] == pytest.approx(0.01)

    def test_summary_keeps_metrics_applicable_to_only_one_method(self):
        rows = [
            {
                "run_id": "run",
                "split": "test",
                "method": "plant",
                "scenario_id": "clean",
                "position_rmse": 0.2,
                "plant_position_rmse": 0.1,
            },
            {
                "run_id": "run",
                "split": "test",
                "method": "command-only",
                "scenario_id": "clean",
                "position_rmse": 0.3,
            },
        ]
        summary = summary_metrics(rows)
        cells = {(row["method"], row["metric"]) for row in summary}
        assert ("plant", "plant_position_rmse") in cells
        assert ("command-only", "plant_position_rmse") not in cells

    def test_summary_rejects_partial_metric_within_a_method(self):
        rows = [
            {
                "run_id": "run",
                "split": "test",
                "method": "plant",
                "scenario_id": "clean",
                "position_rmse": 0.2,
                "plant_position_rmse": 0.1,
            },
            {
                "run_id": "run",
                "split": "test",
                "method": "plant",
                "scenario_id": "clean",
                "position_rmse": 0.3,
            },
        ]
        with pytest.raises(MetricValidationError, match="partially available"):
            summary_metrics(rows)

    @pytest.mark.parametrize("values", [[], [1.0, np.nan], [1.0, np.inf]])
    def test_empty_or_nonfinite_metric_inputs_fail(self, values):
        with pytest.raises(MetricValidationError):
            runtime_metrics(values, deadline_us=10.0)


class TestConstraintAudits:
    def test_velocity_interior_extremum_is_analytic(self):
        rows = constant_jerk_segment_extrema(
            [0.0, 4.09, 8.0],
            -1600.0,
            0.01,
            max_velocity=4.1,
            max_acceleration=8.2,
            max_jerk=4000.0,
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["velocity_interior_extremum"] is True
        assert row["max_abs_velocity_time_s"] == pytest.approx(0.005)
        assert row["max_abs_velocity"] == pytest.approx(4.11)
        assert row["velocity_margin"] == pytest.approx(-0.01)
        assert row["velocity_violation"] is True
        assert row["violation_duration_s"] > 0.0
        assert row["max_sampled_jerk"] == 1600.0
        assert row["max_new_jerk"] == 1600.0
        assert row["max_internal_jerk"] == 1600.0

    def test_sampled_audit_grid_boundaries_and_jerk_sources(self):
        boundary = 0.00337

        def evaluator(time):
            jerk = 4.0
            return (
                [time**3 * jerk / 6.0],
                [0.5 * jerk * time**2],
                [jerk * time],
            )

        rows = audit_sampled_continuous_trajectory(
            evaluator,
            0.01,
            dof=1,
            max_velocity=4.1,
            max_acceleration=8.2,
            max_jerk=4000.0,
            section_boundaries_s=[boundary],
            max_step_s=0.0001,
            internal_jerk=lambda _time: [4.0],
            new_jerk=[5.0],
        )
        row = rows[0]
        assert row["max_grid_step_s"] <= 0.0001 + 1e-15
        assert row["section_boundary_count"] == 3
        assert row["max_sampled_jerk"] == pytest.approx(4.0, abs=1e-11)
        assert row["max_internal_jerk"] == pytest.approx(4.0)
        assert row["max_new_jerk"] == pytest.approx(5.0)
        assert row["internal_jerk_available"] is True
        assert row["new_jerk_available"] is True
        assert row["violation_count"] == 0

    def test_grid_coarser_than_point_one_ms_is_rejected(self):
        with pytest.raises(MetricValidationError):
            audit_sampled_continuous_trajectory(
                lambda _time: ([0.0], [0.0], [0.0]),
                0.01,
                dof=1,
                max_velocity=1.0,
                max_acceleration=1.0,
                max_jerk=1.0,
                max_step_s=0.0001001,
            )


class TestTrajectoryStatistics:
    def _records(self):
        rows = []
        for index in range(12):
            identity = {
                "dataset_id": "synthetic-v1",
                "session_id": f"session-{index // 4}",
                "trajectory_id": f"trajectory-{index:02d}",
            }
            rows.append(
                {**identity, "method": "baseline", "position_rmse": 1.0 + 0.1 * index}
            )
            rows.append(
                {**identity, "method": "candidate", "position_rmse": 0.8 + 0.08 * index}
            )
        return rows

    def test_paired_bootstrap_is_deterministic_and_trajectory_level(self):
        first = paired_comparison_from_records(
            self._records(),
            metric="position_rmse",
            baseline_method="baseline",
            candidate_method="candidate",
            seed=73,
        )
        second = paired_comparison_from_records(
            self._records(),
            metric="position_rmse",
            baseline_method="baseline",
            candidate_method="candidate",
            seed=73,
        )
        assert first == second
        assert first.resamples == 10_000
        assert first.n_trajectories == 12
        assert first.n_expected_trajectories == 12
        assert first.n_excluded_trajectories == 0
        assert first.absolute_difference < 0.0
        assert first.relative_improvement == pytest.approx(0.2)
        assert first.improvement_ci_low > 0.0
        assert first.unadjusted_p_value < 0.01

    def test_duplicate_samples_are_not_silently_pseudoreplicated(self):
        rows = self._records()
        rows.append(dict(rows[0]))
        with pytest.raises(
            StatisticalValidationError, match="duplicate statistical unit"
        ):
            paired_comparison_from_records(
                rows,
                metric="position_rmse",
                baseline_method="baseline",
                candidate_method="candidate",
            )

    def test_pair_sets_must_match_exactly(self):
        with pytest.raises(StatisticalValidationError, match="sets differ"):
            paired_trajectory_bootstrap(
                {"t1": 1.0, "t2": 2.0},
                {"t1": 0.9},
                metric="rmse",
            )

    def test_one_trajectory_cannot_claim_inferential_significance(self):
        with pytest.raises(StatisticalValidationError, match="at least two"):
            paired_trajectory_bootstrap(
                {"only-trajectory": 1.0},
                {"only-trajectory": 0.5},
                metric="rmse",
            )

    def test_shared_failures_cannot_disappear_from_expected_denominator(self):
        with pytest.raises(StatisticalValidationError, match="missing from both"):
            paired_trajectory_bootstrap(
                {"t1": 1.0, "t2": 2.0},
                {"t1": 0.9, "t2": 1.8},
                metric="rmse",
                expected_units=["t1", "t2", "failed-for-both"],
            )

    def test_higher_is_better_improvement_direction_is_explicit(self):
        result = paired_trajectory_bootstrap(
            {"t1": 0.8, "t2": 0.9, "t3": 0.7},
            {"t1": 0.9, "t2": 0.95, "t3": 0.85},
            metric="feasibility_rate",
            lower_is_better=False,
            seed=4,
        )
        assert result.improvement > 0.0
        assert result.improvement_direction.endswith("higher_is_better")

    def test_holm_is_step_down_and_order_preserving(self):
        assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def _initialize_clean_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "source.txt").write_text("locked source\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=OTG Test",
            "-c",
            "user.email=otg-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "locked",
        ],
        cwd=path,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class TestArtifactsAndIndependentRecompute:
    def test_clean_commit_check_detects_tracked_and_untracked_changes(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        commit = _initialize_clean_repo(repo)
        assert assert_clean_commit(repo, expected_commit=commit).dirty is False
        (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        state = capture_git_state(repo)
        assert state.dirty is True
        with pytest.raises(ArtifactValidationError, match="clean worktree"):
            assert_clean_commit(repo, expected_commit=commit)

    def test_dirty_development_bundle_can_be_qa_with_explicit_opt_in(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        commit = _initialize_clean_repo(repo)
        (repo / "development-change.txt").write_text("dirty\n", encoding="utf-8")
        bundle = tmp_path / "development-bundle"
        writer = ArtifactWriter(
            bundle,
            run_id="dirty-development",
            command=["python", "development_smoke.py"],
            resolved_config={"formal": False},
            repo_root=repo,
            expected_commit=commit,
            require_clean=False,
            started_at="2026-07-21T00:00:00+00:00",
        )
        writer.finalize(require_standard_artifacts=False)
        with pytest.raises(ArtifactValidationError, match="dirty worktree"):
            validate_artifact_bundle(bundle, require_standard_artifacts=False)
        result = validate_artifact_bundle(
            bundle,
            require_standard_artifacts=False,
            require_clean=False,
            expected_commit=commit,
        )
        assert result["run_id"] == "dirty-development"

    def test_writer_hashes_and_independent_summary_recompute(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        commit = _initialize_clean_repo(repo)
        bundle = tmp_path / "bundle"
        writer = ArtifactWriter(
            bundle,
            run_id="run-001",
            command=["python", "-m", "otg_lab.experiment", "locked.yaml"],
            resolved_config={"dt": 0.01, "output_field": "command_p"},
            repo_root=repo,
            expected_commit=commit,
            require_clean=True,
            started_at="2026-07-21T00:00:00+00:00",
        )
        resolved = bundle / "resolved_config.yaml"
        resolved.write_text("dt: 0.01\noutput_field: command_p\n", encoding="utf-8")
        writer.register(resolved, role="resolved_config")
        writer.write_json(
            "data_manifest.json",
            {"schema_version": "test", "dataset_id": "synthetic-v1"},
            role="data_manifest",
        )
        writer.write_json(
            "split_manifest.json",
            {"schema_version": "test", "test": ["trajectory-001"]},
            role="split_manifest",
        )
        samples = [_canonical_sample(k) for k in range(8)]
        writer.write_samples(samples)
        writer.write_recomputed_metrics(
            context={"method": "candidate"},
            summary_metric_fields=["position_rmse"],
        )
        writer.write_csv(
            "failures.csv",
            [],
            role="failure_log",
            fieldnames=("run_id", "trajectory_id", "k", "failure_type", "reason"),
            allow_empty=True,
        )
        checksum_path, index_path = writer.finalize()
        assert checksum_path.is_file()
        assert index_path.is_file()
        verified = verify_checksums(bundle)
        assert "samples.parquet" in verified
        index = json.loads(index_path.read_text(encoding="utf-8"))
        sample_record = next(
            row for row in index["artifacts"] if row["path"] == "samples.parquet"
        )
        assert sample_record["sha256"] == sha256_file(bundle / "samples.parquet")
        result = validate_artifact_bundle(
            bundle,
            expected_commit=commit,
            verify_recomputation=True,
            recompute_arguments={
                "context": {"method": "candidate"},
                "summary_metric_fields": ["position_rmse"],
            },
        )
        assert result["recomputation_verified"] is True
        manifest = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
        assert manifest["artifact_row_counts"]["failures.csv"] == 0
        verify_recomputed_summary(
            bundle / "samples.parquet",
            bundle / "metrics_by_trajectory.csv",
            bundle / "summary_metrics.csv",
            context={"method": "candidate"},
            summary_metric_fields=["position_rmse"],
        )

        # A one-byte mutation is caught independently by both SHA registries.
        metrics_path = bundle / "metrics_by_trajectory.csv"
        mutated = bytearray(metrics_path.read_bytes())
        mutated[-2] = ord("1") if mutated[-2] != ord("1") else ord("2")
        metrics_path.write_bytes(mutated)
        with pytest.raises(ArtifactValidationError, match="hash mismatch"):
            validate_artifact_bundle(bundle, expected_commit=commit)

    def test_empty_and_nan_csvs_fail(self, tmp_path):
        with pytest.raises(ArtifactValidationError, match="empty CSV"):
            write_csv(tmp_path / "empty.csv", [])
        with pytest.raises(ArtifactValidationError, match="NaN"):
            write_csv(tmp_path / "nan.csv", [{"metric": float("nan")}])

    def test_legitimate_empty_fallback_and_optional_constraint_fields(self, tmp_path):
        fallback = write_csv(
            tmp_path / "fallback_events.csv",
            [],
            fieldnames=("run_id", "trajectory_id", "k", "fallback_reason"),
            allow_empty=True,
        )
        validate_artifact_schema(fallback)

        constraint = write_csv(
            tmp_path / "constraint_audit.csv",
            [
                {
                    "trajectory_id": "trajectory-001",
                    "joint_id": "joint-0",
                    "audit_method": "sampled_continuous",
                    "violation_count": 0,
                    "max_internal_jerk": None,
                    "velocity_max_time_s": None,
                    "acceleration_max_time_s": None,
                    "jerk_max_time_s": None,
                }
            ],
            allowed_missing_fields={
                "max_internal_jerk",
                "velocity_max_time_s",
                "acceleration_max_time_s",
                "jerk_max_time_s",
            },
        )
        validate_artifact_schema(constraint)

        analytic = write_csv(
            tmp_path / "analytic" / "constraint_audit.csv",
            [
                {
                    "trajectory_id": "trajectory-analytic",
                    "joint_id": "joint-0",
                    "audit_method": "analytic_profile_extrema",
                    "violation_count": 0,
                    "fallback": False,
                    "max_abs_velocity": 1.0,
                    "max_abs_acceleration": 2.0,
                    "max_sampled_jerk": None,
                    "velocity_margin": 3.1,
                    "acceleration_margin": 6.2,
                    "jerk_margin": 3900.0,
                }
            ],
            allowed_missing_fields={"max_sampled_jerk"},
        )
        validate_artifact_schema(analytic)


class TestDeterministicFigures:
    def _metrics(self):
        values = [0.4, 0.1, 0.3, 0.2, 0.7, 0.6, 0.5, 0.8, 0.9, 1.0]
        return [
            {
                "trajectory_id": f"trajectory-{index:02d}",
                "method": "locked-method",
                "position_rmse": value,
            }
            for index, value in enumerate(values)
        ]

    def test_selection_rule_is_predeclared_and_deterministic(self):
        first = select_representative_trajectories(
            self._metrics(),
            ranking_method="locked-method",
            predefined_ids=["trajectory-00"],
        )
        second = select_representative_trajectories(
            list(reversed(self._metrics())),
            ranking_method="locked-method",
            predefined_ids=["trajectory-00"],
        )
        assert first == second
        assert [row["selection_reason"] for row in first] == [
            "predefined_id",
            "median",
            "p90",
            "worst",
        ]
        assert len({row["trajectory_id"] for row in first}) == 4

    def test_figure_files_are_byte_deterministic(self, tmp_path):
        rows = [
            {"method": "P", "position_rmse": 0.4},
            {"method": "P", "position_rmse": 0.5},
            {"method": "PV", "position_rmse": 0.3},
            {"method": "PV", "position_rmse": 0.35},
            {"method": "PVA", "position_rmse": 0.28},
            {"method": "PVA", "position_rmse": 0.32},
        ]
        first, first_svg = plot_same_information_ablation(
            rows, tmp_path / "a" / "figure"
        )
        second, second_svg = plot_same_information_ablation(
            list(reversed(rows)), tmp_path / "b" / "figure"
        )
        assert (
            hashlib.sha256(first.read_bytes()).digest()
            == hashlib.sha256(second.read_bytes()).digest()
        )
        assert (
            hashlib.sha256(first_svg.read_bytes()).digest()
            == hashlib.sha256(second_svg.read_bytes()).digest()
        )

    def test_same_information_iqr_allows_mean_outside_interval(self, tmp_path):
        rows = [
            {"method": "skewed", "position_rmse": value}
            for value in (0.0, 0.0, 0.0, 0.0, 100.0)
        ]

        png, svg = plot_same_information_ablation(
            rows, tmp_path / "skewed" / "figure"
        )

        assert png.is_file()
        assert svg.is_file()

    def test_traces_filter_method_split_joints_and_use_command_time(self, tmp_path):
        selection = select_representative_trajectories(
            self._metrics(), ranking_method="locked-method"
        )
        selected = {row["trajectory_id"] for row in selection}
        ranked = []
        distractors = []
        for trajectory_id in sorted(selected):
            for joint_index, joint_id in enumerate(("joint-0", "joint-1")):
                for k in range(3):
                    base = {
                        "trajectory_id": trajectory_id,
                        "joint_id": joint_id,
                        "control_time": 0.01 * k,
                        "command_time": 0.01 * (k + 1),
                        "p_ref": joint_index + k,
                        "command_p": joint_index + k + 0.1,
                    }
                    ranked.append({**base, "method_id": "locked-method"})
                    distractors.append(
                        {
                            **base,
                            "method_id": "other-method",
                            "command_time": 10.0 + k,
                            "command_p": 100.0 + k,
                        }
                    )
        first, first_svg = plot_representative_traces(
            ranked + distractors,
            selection,
            tmp_path / "with_other" / "traces",
        )
        second, second_svg = plot_representative_traces(
            list(reversed(ranked)),
            selection,
            tmp_path / "ranked_only" / "traces",
        )
        assert (
            hashlib.sha256(first.read_bytes()).digest()
            == hashlib.sha256(second.read_bytes()).digest()
        )
        assert (
            hashlib.sha256(first_svg.read_bytes()).digest()
            == hashlib.sha256(second_svg.read_bytes()).digest()
        )

        shifted = [dict(row, command_time=row["command_time"] + 0.2) for row in ranked]
        shifted_png, _ = plot_representative_traces(
            shifted,
            selection,
            tmp_path / "shifted" / "traces",
        )
        assert (
            hashlib.sha256(first.read_bytes()).digest()
            != hashlib.sha256(shifted_png.read_bytes()).digest()
        )

    def test_empty_figure_data_fails(self, tmp_path):
        with pytest.raises(FigureValidationError, match="empty"):
            plot_same_information_ablation([], tmp_path / "empty")
