"""Data/schema tests emphasize invariants, causality, and replayability."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_lab.datasets import (  # noqa: E402
    FAMILIES,
    MotionLimits,
    StressConfig,
    apply_stress,
    assert_truth_constraints,
    default_stress_suite,
    deliberate_infeasible_suite,
    empirical_jitter_realization,
    generate_trajectory,
    inject_noise,
    inject_outlier,
    inject_quantization,
    inject_timing,
    load_split_manifest,
    resample_truth,
    split_entries,
    trajectory_to_rows,
)
from otg_lab.importers import (  # noqa: E402
    PositionRecord,
    TimestampFaultError,
    audit_timestamps,
    empirical_jitter_from_csv,
    import_legacy_fixed_grid,
    import_timestamp_causal,
    records_to_canonical_rows,
    simulate_arrival_replay,
    write_collection_csv,
)
from otg_lab.schema import (  # noqa: E402
    FIELD_NAMES,
    SchemaValidationError,
    arrow_schema,
    empty_sample,
    read_parquet,
    validate_sample,
    validate_samples,
    write_parquet,
)

REQUESTED_FIELDS = {
    "run_id",
    "dataset_id",
    "session_id",
    "trajectory_id",
    "split",
    "seed",
    "joint_id",
    "k",
    "source_time",
    "arrival_time",
    "control_time",
    "dt_actual",
    "dt_control",
    "p_ref",
    "v_ref_truth",
    "a_ref_truth",
    "j_ref_truth",
    "p_meas",
    "v_meas",
    "a_meas",
    "posterior_p",
    "posterior_v",
    "posterior_a",
    "posterior_state_time",
    "posterior_available_time",
    "prediction_p",
    "prediction_v",
    "prediction_a",
    "prediction_time",
    "prediction_horizon_ms",
    "raw_target_p",
    "raw_target_v",
    "raw_target_a",
    "raw_target_time",
    "executable_target_p",
    "executable_target_v",
    "executable_target_a",
    "executable_target_time",
    "command_p",
    "command_v",
    "command_a",
    "command_jerk",
    "command_time",
    "plant_p",
    "plant_v",
    "plant_a",
    "target_feasible",
    "target_projected",
    "fallback",
    "fallback_reason",
    "solver_status",
    "free_trajectory_duration",
    "estimator_compute_us",
    "predictor_compute_us",
    "governor_compute_us",
    "follower_compute_us",
    "total_compute_us",
    "estimator_id",
    "predictor_id",
    "target_mode",
    "governor_id",
    "follower_id",
    "plant_id",
    "qp_iterations",
    "qp_status_category",
    "qp_solve_time_us",
    "qp_primal_residual",
    "qp_dual_residual",
    "qp_hessian_condition_number",
    "qp_constraint_condition_number",
    "deadline_miss",
    "state_reset",
    "sampled_jerk",
    "new_jerk",
    "internal_trajectory_jerk",
    "plant_compute_us",
}


def minimal_row(**updates):
    values = {
        "run_id": "run",
        "dataset_id": "dataset",
        "session_id": "session",
        "trajectory_id": "trajectory",
        "split": "development",
        "seed": 1,
        "joint_id": "joint_0",
        "k": 0,
        "source_time": 0.0,
        "arrival_time": 0.0,
        "control_time": 0.0,
        "dt_actual": 0.01,
        "dt_control": 0.01,
        "p_ref": 0.0,
        "p_meas": 0.0,
        "source_kind": "unit_test",
        "scenario_id": "clean",
        "truth_available": False,
        "measurement_available": True,
        "measurement_valid": True,
    }
    values.update(updates)
    return empty_sample(**values)


class CanonicalSchemaTests(unittest.TestCase):
    def test_contains_every_requested_field(self):
        self.assertTrue(REQUESTED_FIELDS.issubset(FIELD_NAMES))
        self.assertEqual(len(FIELD_NAMES), len(set(FIELD_NAMES)))

    def test_real_trace_derivative_truth_is_null(self):
        row = minimal_row()
        validate_sample(row)
        self.assertIsNone(row["v_ref_truth"])
        self.assertIsNone(row["a_ref_truth"])
        self.assertIsNone(row["j_ref_truth"])
        row["v_ref_truth"] = 1.0
        with self.assertRaisesRegex(
            SchemaValidationError, "unavailable derivative truth"
        ):
            validate_sample(row)

    def test_truth_availability_requires_complete_finite_truth(self):
        row = minimal_row(
            truth_available=True, v_ref_truth=0.0, a_ref_truth=0.0, j_ref_truth=0.0
        )
        validate_sample(row)
        row["j_ref_truth"] = None
        with self.assertRaisesRegex(SchemaValidationError, "complete v/a/j truth"):
            validate_sample(row)

    def test_qp_observability_categories_are_typed_and_not_collapsed(self):
        row = minimal_row(
            governor_id="jerk_qp",
            qp_iterations=17,
            qp_status_category="qp_solved",
            qp_solve_time_us=123.0,
            qp_primal_residual=2e-6,
            qp_dual_residual=3e-6,
            qp_hessian_condition_number=20.0,
            qp_constraint_condition_number=5.0,
        )
        validate_sample(row)
        row["qp_status_category"] = "qp_timeout"
        with self.assertRaisesRegex(SchemaValidationError, "invalid category"):
            validate_sample(row)

        failed = minimal_row(
            governor_id="jerk_qp",
            qp_iterations=1,
            qp_status_category="qp_max_iter_reached",
            fallback=False,
            fallback_requested=False,
            fallback_applied=False,
        )
        with self.assertRaisesRegex(SchemaValidationError, "safety fallback"):
            validate_sample(failed)

    def test_nonfinite_requires_explicit_invalid_outlier_flags(self):
        row = minimal_row(p_meas=float("nan"))
        with self.assertRaisesRegex(
            SchemaValidationError, "non-finite value is unflagged"
        ):
            validate_sample(row)
        row.update(
            event_outlier=True,
            event_nonfinite=True,
            measurement_valid=False,
            invalid_input=True,
        )
        validate_sample(row)
        row["source_time"] = float("inf")
        with self.assertRaisesRegex(SchemaValidationError, "source_time"):
            validate_sample(row)

    def test_strict_columns_and_timestamp_fault_flags(self):
        row0 = minimal_row()
        row1 = minimal_row(k=1, source_time=0.0, arrival_time=0.01, control_time=0.01)
        with self.assertRaisesRegex(
            SchemaValidationError, "duplicate timestamp is unflagged"
        ):
            validate_samples([row0, row1])
        row1["event_duplicate"] = True
        validate_samples([row0, row1])
        del row1["solver_status"]
        with self.assertRaisesRegex(SchemaValidationError, "missing fields"):
            validate_sample(row1)

    def test_ordering_is_scoped_to_run_and_method_stream(self):
        first = minimal_row(run_id="run-a", estimator_id="e1")
        second_run = minimal_row(run_id="run-b", estimator_id="e1")
        second_method = minimal_row(run_id="run-a", estimator_id="e2")
        validate_samples([first, second_run, second_method])
        with self.assertRaisesRegex(
            SchemaValidationError, "k must be strictly increasing"
        ):
            validate_samples([first, minimal_row(run_id="run-a", estimator_id="e1")])

    def test_arrow_schema_has_availability_metadata_when_installed(self):
        try:
            schema = arrow_schema()
        except ImportError:
            self.skipTest(
                "pyarrow is not installed in the dependency-minimal test environment"
            )
        self.assertEqual(schema.metadata[b"schema_version"], b"otg.sample.v3")
        self.assertEqual(
            schema.field("v_ref_truth").metadata[b"availability"],
            b"synthetic_truth_only",
        )
        self.assertEqual(
            schema.field("target_feasible").metadata[b"alias_for"],
            b"raw_target_point_admissible",
        )

    def test_parquet_round_trip_preserves_null_truth_and_schema(self):
        try:
            arrow_schema()
        except ImportError:
            self.skipTest("pyarrow is not installed")
        with tempfile.TemporaryDirectory() as directory:
            path = write_parquet([minimal_row()], Path(directory) / "samples.parquet")
            loaded = read_parquet(path)
        self.assertEqual(loaded[0]["trajectory_id"], "trajectory")
        self.assertIsNone(loaded[0]["v_ref_truth"])

    def test_config_lock_hashes_match_frozen_inputs(self):
        with (ROOT / "config_lock.json").open(encoding="utf-8") as handle:
            lock = json.load(handle)
        synthetic = lock["synthetic_dataset"]
        for path_key, digest_key in (
            ("config", "config_sha256"),
            ("data_manifest", "data_manifest_sha256"),
            ("split_manifest", "split_manifest_sha256"),
        ):
            actual = hashlib.sha256(
                (ROOT / synthetic[path_key]).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, synthetic[digest_key])
        real = lock["real_trace_policy"]
        actual = hashlib.sha256((ROOT / real["manifest"]).read_bytes()).hexdigest()
        self.assertEqual(actual, real["manifest_sha256"])
        with (ROOT / synthetic["data_manifest"]).open(encoding="utf-8") as handle:
            data_manifest = json.load(handle)
        # The v1 manifest is historical evidence.  Its code hashes must resolve
        # against the recorded clean selection commit, not whatever v2 code is
        # currently checked out; rewriting the v1 hashes would destroy provenance.
        source_commit = lock["selection_provenance"]["source_commit"]
        for relative_path, digest_key in (
            ("otg_lab/datasets.py", "generator_sha256"),
            ("otg_lab/schema.py", "schema_sha256"),
        ):
            content = subprocess.run(
                ("git", "show", f"{source_commit}:{relative_path}"),
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
            self.assertEqual(hashlib.sha256(content).hexdigest(), data_manifest[digest_key])
        for relative_path, digest_key in (
            (synthetic["config"], "config_sha256"),
            (synthetic["split_manifest"], "split_manifest_sha256"),
        ):
            actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
            self.assertEqual(actual, data_manifest[digest_key])


class SyntheticBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_split_manifest(ROOT / "split_manifest.json")
        cls.entries = split_entries(cls.manifest)

    def test_frozen_split_has_20_10_20_per_family_and_no_overlap(self):
        self.assertEqual(len(self.entries), 300)
        ids = {entry.trajectory_id for entry in self.entries}
        self.assertEqual(len(ids), 300)
        for family in FAMILIES:
            family_entries = [entry for entry in self.entries if entry.family == family]
            for split, expected in (("train", 20), ("validation", 10), ("test", 20)):
                selected = [entry for entry in family_entries if entry.split == split]
                self.assertEqual(len(selected), expected, (family, split))
                self.assertTrue(
                    all(entry.locked == (split == "test") for entry in selected)
                )

    def test_generation_is_deterministic_and_manifest_driven(self):
        entry = self.entries[0]
        first = generate_trajectory(entry)
        second = generate_trajectory(entry)
        np.testing.assert_array_equal(first.time, second.time)
        np.testing.assert_array_equal(first.position, second.position)
        np.testing.assert_array_equal(first.velocity, second.velocity)
        different = generate_trajectory(self.entries[1])
        self.assertFalse(np.array_equal(first.position, different.position))

    def test_required_polynomial_and_oscillatory_variants_are_fixed(self):
        stationary = {
            generate_trajectory(entry).reference_variant
            for entry in self.entries
            if entry.family == "stationary_endpoint" and entry.split == "train"
        }
        oscillatory = {
            generate_trajectory(entry).reference_variant
            for entry in self.entries
            if entry.family == "oscillatory" and entry.split == "train"
        }
        self.assertEqual(stationary, {"quintic", "seventh_order"})
        self.assertEqual(oscillatory, {"sine", "multi_sine", "chirp"})

    def test_multisine_exact_excitation_frequencies_are_persisted(self):
        entry = next(
            entry
            for entry in self.entries
            if entry.family == "oscillatory"
            and entry.split == "test"
            and entry.seed % 3 == 1
        )
        trajectory = generate_trajectory(entry)
        specification = json.loads(trajectory.reference_frequency_spec_json)
        self.assertEqual(specification["kind"], "discrete_tones")
        self.assertEqual(len(specification["frequencies_hz"]), 3)
        self.assertTrue(all(value > 0.0 for value in specification["frequencies_hz"]))
        rows = trajectory_to_rows(trajectory, sample_rate_hz=100.0)
        self.assertEqual(
            {row["reference_frequency_spec_json"] for row in rows},
            {trajectory.reference_frequency_spec_json},
        )

    def test_every_family_and_demand_stratum_is_high_resolution_and_feasible(self):
        limits = MotionLimits()
        for family in FAMILIES:
            candidates = [
                entry
                for entry in self.entries
                if entry.family == family and entry.split == "train"
            ]
            for entry in candidates[:4]:
                trajectory = generate_trajectory(entry)
                self.assertLessEqual(trajectory.internal_dt, 0.001 + 1e-15)
                assert_truth_constraints(trajectory, limits=limits)
                ratios = trajectory.demand_ratios(limits)
                expected = {
                    "low": 0.20,
                    "medium": 0.50,
                    "high": 0.75,
                    "near_limit": 0.93,
                }[entry.demand_stratum]
                self.assertAlmostEqual(max(ratios.values()), expected, places=10)

    def test_resampling_preserves_truth_columns_and_constraints_at_all_rates(self):
        trajectory = generate_trajectory(self.entries[4])
        for rate in (50.0, 100.0, 200.0, 500.0):
            sampled = resample_truth(trajectory, rate)
            self.assertAlmostEqual(sampled.time[1] - sampled.time[0], 1.0 / rate)
            self.assertLessEqual(np.max(np.abs(sampled.velocity)), 4.1 + 1e-10)
            self.assertLessEqual(np.max(np.abs(sampled.acceleration)), 8.2 + 1e-10)
            self.assertLessEqual(np.max(np.abs(sampled.jerk)), 4000.0 + 1e-10)
        rows = trajectory_to_rows(trajectory, sample_rate_hz=100.0)
        self.assertTrue(all(row["truth_available"] for row in rows))
        self.assertTrue(all(row["v_ref_truth"] is not None for row in rows))

    def test_normalized_demand_coverage_reaches_each_limit_axis(self):
        selected = {
            family: next(
                entry
                for entry in self.entries
                if entry.family == family
                and entry.split == "train"
                and entry.demand_stratum == "near_limit"
            )
            for family in (
                "stationary_endpoint",
                "oscillatory",
                "boundary_grazing",
            )
        }
        self.assertGreaterEqual(
            generate_trajectory(selected["stationary_endpoint"]).demand_ratios()["r_v"],
            0.90,
        )
        self.assertGreaterEqual(
            generate_trajectory(selected["oscillatory"]).demand_ratios()["r_a"],
            0.90,
        )
        self.assertGreaterEqual(
            generate_trajectory(selected["boundary_grazing"]).demand_ratios()["r_j"],
            0.90,
        )

    def test_deliberately_infeasible_suite_is_separate(self):
        suite = deliberate_infeasible_suite()
        self.assertTrue(suite)
        self.assertTrue(all(item.split == "infeasible" for item in suite))
        self.assertTrue(all(item.intentionally_infeasible for item in suite))
        limits = MotionLimits()
        self.assertTrue(
            all(
                max(item.demand_ratios(limits).values()) > 1.0
                or "step" in item.trajectory_id
                for item in suite
            )
        )
        with self.assertRaisesRegex(ValueError, "deliberately infeasible"):
            assert_truth_constraints(suite[0])
        rows = trajectory_to_rows(suite[0])
        self.assertEqual(rows[0]["dataset_id"], "synthetic-deliberate-infeasible-v1")
        self.assertEqual(rows[0]["source_kind"], "synthetic_deliberate_infeasible")
        self.assertEqual(rows[0]["scenario_id"], suite[0].reference_variant)


class StressSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        entry = split_entries(load_split_manifest())[0]
        cls.clean = trajectory_to_rows(generate_trajectory(entry), sample_rate_hz=100.0)

    def test_white_and_ar_noise_are_deterministic_and_saved(self):
        first = inject_noise(self.clean, std=1e-4, colored_ar=0.8, seed=42)
        second = inject_noise(self.clean, std=1e-4, colored_ar=0.8, seed=42)
        np.testing.assert_array_equal(
            [row["noise_realization"] for row in first],
            [row["noise_realization"] for row in second],
        )
        self.assertNotEqual(first[5]["p_meas"], self.clean[5]["p_meas"])
        self.assertIsNone(self.clean[5]["noise_realization"])

    def test_quantization_saves_exact_realization(self):
        resolution = 1e-3
        output = inject_quantization(self.clean, resolution=resolution)
        for clean, stressed in zip(self.clean[:20], output[:20]):
            self.assertAlmostEqual(
                stressed["p_meas"] / resolution, round(stressed["p_meas"] / resolution)
            )
            self.assertAlmostEqual(
                stressed["p_meas"] - clean["p_meas"], stressed["quantization_error"]
            )

    def test_timing_faults_are_flagged_and_replayable(self):
        kwargs = dict(
            seed=5,
            jitter_std_s=0.0005,
            drop_probability=0.02,
            burst_start=10,
            burst_length=3,
            duplicate_index=20,
            regression_index=30,
            regression_s=0.002,
        )
        first = inject_timing(self.clean, **kwargs)
        second = inject_timing(self.clean, **kwargs)
        self.assertEqual(first, second)
        self.assertTrue(first[20]["event_duplicate"])
        self.assertEqual(first[20]["p_meas"], first[19]["p_meas"])
        self.assertTrue(first[30]["event_timestamp_regression"])
        self.assertTrue(all(first[index]["event_burst_drop"] for index in (10, 11, 12)))
        self.assertIsNone(first[10]["p_meas"])
        self.assertTrue(all(row["arrival_time"] >= row["source_time"] for row in first))
        self.assertTrue(
            all(
                abs(
                    row["transport_delay_s"]
                    - (row["arrival_time"] - row["source_time"])
                )
                < 1e-12
                for row in first
            )
        )
        validate_samples(first)

    def test_nan_and_infinities_survive_only_as_flagged_invalid_measurements(self):
        for kind in ("nan", "posinf", "neginf"):
            output = inject_outlier(self.clean, kind=kind, index=12)
            row = output[12]
            self.assertTrue(row["event_outlier"])
            self.assertTrue(row["event_nonfinite"])
            self.assertEqual(row["outlier_kind"], kind)
            self.assertFalse(row["measurement_valid"])
            self.assertTrue(row["invalid_input"])
            self.assertFalse(np.isfinite(row["p_meas"]))
            validate_samples(output)

    def test_combined_stress_does_not_mutate_clean_input(self):
        config = StressConfig(
            kind="combined",
            scenario_id="combined_test",
            seed=99,
            noise_std=1e-4,
            ar_coefficient=0.6,
            jitter_std_s=0.0005,
            drop_probability=0.01,
            outlier_kind="impossible_jump",
            outlier_index=25,
            outlier_magnitude=0.5,
        )
        clean_position = self.clean[25]["p_meas"]
        output = apply_stress(self.clean, config)
        self.assertEqual(self.clean[25]["p_meas"], clean_position)
        self.assertTrue(output[25]["event_impossible_jump"])
        self.assertEqual(output[25]["scenario_id"], "combined_test")
        self.assertTrue(all(row["scenario_id"] == "combined_test" for row in output))
        self.assertTrue(all(row["stress_seed"] == 99 for row in output))
        validate_samples(output)

    def test_default_suite_includes_all_required_categories(self):
        scenarios = default_stress_suite()
        kinds = {scenario.kind for scenario in scenarios}
        self.assertEqual(
            kinds, {"noise", "quantization", "timing", "outlier", "combined"}
        )
        ids = {scenario.scenario_id for scenario in scenarios}
        for required in (
            "drop_1pct",
            "drop_5pct",
            "burst_drop",
            "duplicate",
            "timestamp_regression",
            "combined_medium",
        ):
            self.assertIn(required, ids)
        for scenario in scenarios:
            validate_samples(apply_stress(self.clean, scenario))

    def test_empirical_jitter_hook_is_deterministic(self):
        observed = [0.009, 0.011, 0.0105, 0.0085]
        first = empirical_jitter_realization(
            observed, target_dt=0.01, size=len(self.clean), seed=123
        )
        second = empirical_jitter_realization(
            observed, target_dt=0.01, size=len(self.clean), seed=123
        )
        np.testing.assert_array_equal(first, second)
        output = inject_timing(self.clean, seed=321, empirical_jitter_s=first)
        validate_samples(output)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.csv_path = Path(self.tempdir.name) / "trace.csv"

    def write_trace(self, times, values):
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["elapsed time", "timestamp", "topic", "value"])
            for source_time, value in zip(times, values):
                timestamp = (
                    1000.0 + source_time
                    if isinstance(source_time, (int, float))
                    else source_time
                )
                writer.writerow([source_time, timestamp, "/joint", value])

    def test_legacy_reads_only_values_on_exact_grid(self):
        # Deliberately malformed timestamp text demonstrates that this mode does
        # not accidentally change semantics by consulting a time column.
        self.write_trace(["not-a-time", "still-not-a-time", "ignored"], [1.0, 2.0, 3.0])
        rows = import_legacy_fixed_grid(self.csv_path)
        self.assertEqual([row["p_ref"] for row in rows], [1.0, 2.0, 3.0])
        self.assertEqual([row["source_time"] for row in rows], [0.0, 0.01, 0.02])
        self.assertTrue(all(row["v_ref_truth"] is None for row in rows))

    def test_timestamp_causal_hold_never_reads_future_sample(self):
        self.write_trace([0.0, 0.015, 0.025], [0.0, 10.0, 20.0])
        rows, audit = import_timestamp_causal(self.csv_path, time_column="elapsed time")
        self.assertTrue(audit.valid_for_strict_replay)
        self.assertEqual([row["control_time"] for row in rows], [0.0, 0.01, 0.02])
        self.assertEqual([row["p_ref"] for row in rows], [0.0, 0.0, 10.0])
        self.assertTrue(rows[1]["event_held"])
        # Mutating a not-yet-available sample cannot alter earlier controls.
        self.write_trace([0.0, 0.015, 0.025], [0.0, 9999.0, -9999.0])
        mutated, _ = import_timestamp_causal(self.csv_path, time_column="elapsed time")
        self.assertEqual(mutated[0]["p_ref"], rows[0]["p_ref"])
        self.assertEqual(mutated[1]["p_ref"], rows[1]["p_ref"])

    def test_timestamp_duplicate_and_regression_are_detected(self):
        audit = audit_timestamps([0.0, 0.01, 0.01, 0.005])
        self.assertEqual(audit.duplicate_count, 1)
        self.assertEqual(audit.regression_count, 1)
        self.write_trace([0.0, 0.01, 0.01], [0.0, 1.0, 2.0])
        with self.assertRaisesRegex(TimestampFaultError, "duplicates=1"):
            import_timestamp_causal(self.csv_path, time_column="elapsed time")
        self.write_trace([0.0, 0.01, 0.005], [0.0, 1.0, 2.0])
        with self.assertRaisesRegex(TimestampFaultError, "regressions=1"):
            import_timestamp_causal(self.csv_path, time_column="elapsed time")

    def test_arrival_simulation_separates_clocks_and_records_drops(self):
        times = np.arange(0.0, 0.5, 0.01)
        self.write_trace(times, np.sin(times))
        first = simulate_arrival_replay(
            self.csv_path,
            time_column="elapsed time",
            seed=7,
            base_delay_s=0.003,
            jitter_std_s=0.001,
            drop_probability=0.1,
        )
        second = simulate_arrival_replay(
            self.csv_path,
            time_column="elapsed time",
            seed=7,
            base_delay_s=0.003,
            jitter_std_s=0.001,
            drop_probability=0.1,
        )
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.rows, second.rows)
        self.assertGreater(first.dropped_count, 0)
        self.assertEqual(
            sum(row["event_input_drop_count"] for row in first.rows),
            first.dropped_count,
        )
        for row in first.rows:
            self.assertLessEqual(row["arrival_time"], row["control_time"] + 1e-12)
            self.assertAlmostEqual(
                row["arrival_time"] - row["source_time"], row["transport_delay_s"]
            )

    def test_arrival_simulation_rebases_epoch_clocks_without_losing_delay(self):
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["elapsed time", "timestamp", "topic", "value"])
            for index in range(20):
                writer.writerow(
                    [
                        index * 0.01,
                        1_783_674_526.9551628 + index * 0.01,
                        "/joint",
                        np.sin(index * 0.01),
                    ]
                )
        replay = simulate_arrival_replay(
            self.csv_path,
            seed=7,
            base_delay_s=0.003,
            jitter_std_s=0.001,
        )
        self.assertLess(replay.rows[0]["source_time"], 1.0)
        for row in replay.rows:
            self.assertAlmostEqual(
                row["arrival_time"] - row["source_time"],
                row["transport_delay_s"],
                places=14,
            )
        validate_samples(replay.rows)

    def test_collector_adapter_never_promotes_measurements_to_truth(self):
        records = [
            PositionRecord(
                0.0, 1.0, arrival_time=0.001, velocity=2.0, acceleration=3.0
            ),
            PositionRecord(
                0.01, 1.1, arrival_time=0.011, velocity=2.1, acceleration=3.1
            ),
        ]
        rows = records_to_canonical_rows(
            records,
            run_id="run",
            dataset_id="real",
            session_id="session",
            trajectory_id="trajectory",
        )
        self.assertEqual(rows[0]["v_meas"], 2.0)
        self.assertEqual(rows[0]["a_meas"], 3.0)
        self.assertIsNone(rows[0]["v_ref_truth"])
        self.assertIsNone(rows[0]["a_ref_truth"])
        self.assertFalse(rows[0]["truth_available"])

    def test_collection_csv_and_empirical_jitter_adapters(self):
        collection = Path(self.tempdir.name) / "collection.csv"
        count = write_collection_csv(
            [
                {
                    "source_time": 0.0,
                    "arrival_time": 0.001,
                    "session_id": "s",
                    "trajectory_id": "t",
                    "joint_id": "j",
                    "position": 1.0,
                },
                {
                    "source_time": 0.011,
                    "arrival_time": 0.012,
                    "session_id": "s",
                    "trajectory_id": "t",
                    "joint_id": "j",
                    "position": 1.1,
                },
            ],
            collection,
        )
        self.assertEqual(count, 2)
        jitter = empirical_jitter_from_csv(
            collection, time_column="source_time", expected_dt_s=0.01
        )
        np.testing.assert_allclose(jitter, [0.001], rtol=0.0, atol=1e-15)


if __name__ == "__main__":
    unittest.main()
