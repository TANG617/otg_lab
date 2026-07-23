import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from otg_lab.phase_a import (
    ORACLE_METHOD,
    _method_rows,
    _resolve_phase_a_design,
    _run_sequence,
)
from target_state_experiment import (
    DT,
    METHOD_BY_ID,
    METHODS,
    VENDOR_LIMITS,
    backward_finite_difference,
    build_target_states,
    centered_finite_difference_causal,
    centered_finite_difference_offline,
    csv_reference,
    elementary_references,
    methods_for_reference,
    reference_peak_metrics,
)


class TestVendorLimitsAndReferences(unittest.TestCase):
    def test_vendor_limits_are_the_formal_fixed_constraints(self):
        self.assertEqual(VENDOR_LIMITS.max_velocity, 4.1)
        self.assertEqual(VENDOR_LIMITS.max_acceleration, 8.2)
        self.assertEqual(VENDOR_LIMITS.max_jerk, 4000.0)
        self.assertEqual(
            VENDOR_LIMITS.as_dict(),
            {
                "max_velocity": 4.1,
                "max_acceleration": 8.2,
                "max_jerk": 4000.0,
            },
        )

    def test_analytic_reference_boundaries_and_settle_segment(self):
        references = elementary_references()
        expected_boundaries = {
            "quadratic_with_extremum": (1.125, 1.125),
            "cubic": (-0.405, 0.405),
            "sine": (0.0, 0.0),
        }

        self.assertEqual(set(references), set(expected_boundaries))
        for dataset, reference in references.items():
            with self.subTest(dataset=dataset):
                final_motion_index = reference.original_count - 1
                expected_start, expected_end = expected_boundaries[dataset]
                self.assertEqual(reference.original_count, 301)
                self.assertEqual(reference.position.size, 501)
                self.assertAlmostEqual(reference.position[0], expected_start, places=12)
                self.assertAlmostEqual(
                    reference.position[final_motion_index], expected_end, places=12
                )
                self.assertAlmostEqual(reference.velocity[0], 0.0, places=12)
                self.assertAlmostEqual(
                    reference.velocity[final_motion_index], 0.0, places=12
                )
                self.assertAlmostEqual(reference.acceleration[0], 0.0, places=12)
                self.assertAlmostEqual(
                    reference.acceleration[final_motion_index], 0.0, places=12
                )
                np.testing.assert_array_equal(
                    reference.position[reference.original_count :],
                    np.full(200, reference.position[final_motion_index]),
                )
                np.testing.assert_array_equal(
                    reference.velocity[reference.original_count :], np.zeros(200)
                )
                np.testing.assert_array_equal(
                    reference.acceleration[reference.original_count :], np.zeros(200)
                )
                np.testing.assert_array_equal(
                    reference.jerk[reference.original_count :], np.zeros(200)
                )
                numeric_jerk = np.gradient(
                    reference.acceleration[: reference.original_count],
                    reference.dt,
                    edge_order=2,
                )
                np.testing.assert_allclose(
                    reference.jerk[2 : reference.original_count - 2],
                    numeric_jerk[2 : reference.original_count - 2],
                    rtol=0.0,
                    atol=0.03,
                )
                np.testing.assert_allclose(np.diff(reference.time), DT, rtol=0.0, atol=1e-15)

    def test_analytic_reference_peaks_are_reproducible_and_feasible(self):
        expected_peaks = {
            "quadratic_with_extremum": (
                1.5061680135799291,
                4.78515625,
                13.465995800213614,
            ),
            "cubic": (
                0.5917300704286925,
                1.4712067651963803,
                7.5332719845435125,
            ),
            "sine": (
                1.6951510359994926,
                6.364585446404775,
                40.073043946224686,
            ),
        }

        for dataset, reference in elementary_references().items():
            with self.subTest(dataset=dataset):
                peaks = reference_peak_metrics(reference)
                expected_velocity, expected_acceleration, expected_jerk = expected_peaks[
                    dataset
                ]
                self.assertAlmostEqual(peaks["max_velocity"], expected_velocity, places=12)
                self.assertAlmostEqual(
                    peaks["max_acceleration"], expected_acceleration, places=12
                )
                self.assertAlmostEqual(
                    peaks["max_sampled_jerk"], expected_jerk, places=10
                )
                self.assertLess(peaks["max_velocity"], VENDOR_LIMITS.max_velocity)
                self.assertLess(peaks["max_acceleration"], VENDOR_LIMITS.max_acceleration)
                self.assertLess(peaks["max_sampled_jerk"], VENDOR_LIMITS.max_jerk)


class TestFiniteDifferences(unittest.TestCase):
    def setUp(self):
        self.dt = DT
        self.time = np.arange(9, dtype=float) * self.dt
        self.estimators = {
            "backward": backward_finite_difference,
            "centered_offline": centered_finite_difference_offline,
            "centered_causal": centered_finite_difference_causal,
        }

    def test_constant_function_is_zero_for_all_three_estimators(self):
        position = np.full_like(self.time, 2.0)
        for name, estimator in self.estimators.items():
            with self.subTest(estimator=name):
                velocity, acceleration = estimator(position, self.dt)
                np.testing.assert_array_equal(velocity, np.zeros_like(position))
                np.testing.assert_array_equal(acceleration, np.zeros_like(position))

    def test_linear_function_exposes_only_backward_startup_spike(self):
        intercept = -0.4
        slope = 1.7
        position = intercept + slope * self.time

        velocity, acceleration = backward_finite_difference(position, self.dt)
        self.assertEqual(velocity[0], 0.0)
        np.testing.assert_allclose(velocity[1:], slope, rtol=0.0, atol=2e-14)
        self.assertAlmostEqual(acceleration[1], slope / self.dt, places=10)
        np.testing.assert_allclose(acceleration[2:], 0.0, rtol=0.0, atol=5e-12)

        velocity, acceleration = centered_finite_difference_offline(
            position, self.dt
        )
        np.testing.assert_allclose(velocity, slope, rtol=0.0, atol=2e-14)
        np.testing.assert_allclose(acceleration, 0.0, rtol=0.0, atol=5e-12)

        velocity, acceleration = centered_finite_difference_causal(position, self.dt)
        np.testing.assert_array_equal(velocity[:2], np.zeros(2))
        np.testing.assert_array_equal(acceleration[:2], np.zeros(2))
        np.testing.assert_allclose(velocity[2:], slope, rtol=0.0, atol=3e-14)
        np.testing.assert_allclose(acceleration[2:], 0.0, rtol=0.0, atol=5e-12)

    def test_quadratic_function_has_expected_timestamp_alignment(self):
        intercept = 0.2
        initial_velocity = -0.6
        constant_acceleration = 3.2
        position = (
            intercept
            + initial_velocity * self.time
            + 0.5 * constant_acceleration * self.time**2
        )
        true_velocity = initial_velocity + constant_acceleration * self.time

        velocity, acceleration = backward_finite_difference(position, self.dt)
        expected_backward_velocity = (
            initial_velocity
            + constant_acceleration * (self.time[1:] - self.dt / 2.0)
        )
        np.testing.assert_allclose(
            velocity[1:], expected_backward_velocity, rtol=0.0, atol=2e-14
        )
        self.assertAlmostEqual(
            acceleration[1],
            initial_velocity / self.dt + constant_acceleration / 2.0,
            places=10,
        )
        np.testing.assert_allclose(
            acceleration[2:], constant_acceleration, rtol=0.0, atol=5e-12
        )

        velocity, acceleration = centered_finite_difference_offline(
            position, self.dt
        )
        np.testing.assert_allclose(velocity, true_velocity, rtol=0.0, atol=3e-14)
        np.testing.assert_allclose(
            acceleration, constant_acceleration, rtol=0.0, atol=5e-12
        )

        velocity, acceleration = centered_finite_difference_causal(position, self.dt)
        np.testing.assert_array_equal(velocity[:2], np.zeros(2))
        np.testing.assert_array_equal(acceleration[:2], np.zeros(2))
        np.testing.assert_allclose(
            velocity[2:], true_velocity[2:], rtol=0.0, atol=3e-14
        )
        np.testing.assert_allclose(
            acceleration[2:], constant_acceleration, rtol=0.0, atol=5e-12
        )

    def test_causal_centered_estimate_does_not_read_future_samples(self):
        position = np.array([0.0, 0.1, 0.4, 0.2, -0.3, 0.7, 0.5, -0.2, 0.9])
        cutoff = 5
        mutated = position.copy()
        mutated[cutoff + 1 :] = np.array([100.0, -50.0, 25.0])

        original_velocity, original_acceleration = centered_finite_difference_causal(
            position, self.dt
        )
        mutated_velocity, mutated_acceleration = centered_finite_difference_causal(
            mutated, self.dt
        )
        np.testing.assert_array_equal(
            mutated_velocity[: cutoff + 1], original_velocity[: cutoff + 1]
        )
        np.testing.assert_array_equal(
            mutated_acceleration[: cutoff + 1], original_acceleration[: cutoff + 1]
        )


class TestTargetStatesAndMethodMatrix(unittest.TestCase):
    def test_target_component_zeroing(self):
        reference = elementary_references()["sine"]

        position_only = build_target_states(reference, "p")
        np.testing.assert_array_equal(position_only[:, 0], reference.position)
        np.testing.assert_array_equal(position_only[:, 1:], 0.0)

        position_velocity = build_target_states(reference, "pv_truth")
        np.testing.assert_array_equal(position_velocity[:, 0], reference.position)
        np.testing.assert_array_equal(position_velocity[:, 1], reference.velocity)
        np.testing.assert_array_equal(position_velocity[:, 2], 0.0)

        position_velocity_acceleration = build_target_states(reference, "pva_truth")
        np.testing.assert_array_equal(
            position_velocity_acceleration[:, 0], reference.position
        )
        np.testing.assert_array_equal(
            position_velocity_acceleration[:, 1], reference.velocity
        )
        np.testing.assert_array_equal(
            position_velocity_acceleration[:, 2], reference.acceleration
        )

    def test_method_matrix_counts_and_ids_are_unique(self):
        method_ids = [method.method_id for method in METHODS]
        self.assertEqual(len(method_ids), 9)
        self.assertEqual(len(set(method_ids)), 9)
        self.assertEqual(set(method_ids), set(METHOD_BY_ID))

        synthetic = elementary_references()["sine"]
        self.assertEqual(len(methods_for_reference(synthetic)), 9)
        self.assertEqual(
            len(methods_for_reference(synthetic, include_realtime_supplement=False)),
            7,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            csv_path = Path(temporary_directory) / "positions.csv"
            csv_path.write_text(
                "elapsed time,value\n0.0,0.0\n1.0,0.1\n2.0,0.2\n3.0,0.3\n",
                encoding="utf-8",
            )
            recorded = csv_reference(csv_path)
        self.assertEqual(len(methods_for_reference(recorded)), 7)
        self.assertEqual(
            len(methods_for_reference(recorded, include_realtime_supplement=False)),
            5,
        )

    def test_offline_and_causal_centered_metadata(self):
        for method_id in ("pv_central_offline", "pva_central_offline"):
            with self.subTest(method_id=method_id):
                method = METHOD_BY_ID[method_id]
                self.assertFalse(method.causal)
                self.assertEqual(method.future_samples, 1)
                self.assertEqual(method.native_delay_samples, 0)
                self.assertEqual(method.warmup_samples, 1)
                self.assertEqual(method.result_group, "core")

        for method_id in ("pv_central_causal", "pva_central_causal"):
            with self.subTest(method_id=method_id):
                method = METHOD_BY_ID[method_id]
                self.assertTrue(method.causal)
                self.assertEqual(method.future_samples, 0)
                self.assertEqual(method.native_delay_samples, 1)
                self.assertEqual(method.warmup_samples, 2)
                self.assertEqual(method.result_group, "realtime_supplement")

    def test_phase_a_reconstruction_emits_native_profile_aware_v3_rows(self):
        full = elementary_references()["sine"]
        count = 12
        reference = replace(
            full,
            time=full.time[:count],
            position=full.position[:count],
            velocity=full.velocity[:count],
            acceleration=full.acceleration[:count],
            jerk=full.jerk[:count],
            original_count=count,
        )

        position_method = METHOD_BY_ID["p"]
        position_result = _run_sequence(reference, position_method, VENDOR_LIMITS)
        rows, audits = _method_rows(
            reference,
            position_method,
            position_result,
            VENDOR_LIMITS,
            run_id="phase-a-test-position",
            experiment="test",
            sweep_type="none",
            sweep_value=None,
        )
        self.assertEqual(len(rows), count - 1)
        self.assertEqual(len(audits), count - 1)
        self.assertTrue(
            all(
                row["command_profile_kind"]
                == "ruckig_piecewise_constant_jerk"
                and row["command_profile_exact"]
                and row["command_endpoint_matches_profile"]
                and row["command_profile_continuous_constraints_satisfied"]
                and row["native_command_executed"]
                and row["actual_command_algorithm"] == "ordinary_ruckig"
                and row["method_semantics"] == "ordinary_ruckig_unshielded"
                and not row["safety_shield_requested"]
                and not row["safety_shield_applied"]
                and not row["fallback_applied"]
                and row["command_constant_jerk_exact"] is None
                for row in rows
            )
        )

        oracle_result = _run_sequence(reference, ORACLE_METHOD, VENDOR_LIMITS)
        self.assertEqual(oracle_result["raw_target_states"][5, 0], reference.position[6])
        self.assertAlmostEqual(reference.time[6], reference.time[5] + reference.dt)

    def test_phase_a_design_is_resolved_from_declared_config_values(self):
        methods, acceleration, jerk = _resolve_phase_a_design(
            ["p", "pva_truth", "oracle_next_cycle"],
            [4.1, 8.2],
            [400.0, 4000.0],
        )
        self.assertEqual(
            [method.method_id for method in methods],
            ["p", "pva_truth", "oracle_next_cycle"],
        )
        self.assertEqual(acceleration, (4.1, 8.2))
        self.assertEqual(jerk, (400.0, 4000.0))
        with self.assertRaisesRegex(ValueError, "unknown Phase A"):
            _resolve_phase_a_design(["not-a-method"], [8.2], [4000.0])
        with self.assertRaisesRegex(ValueError, "non-empty and unique"):
            _resolve_phase_a_design(["p", "p"], [8.2], [4000.0])


class TestCsvTimeConvention(unittest.TestCase):
    def test_elapsed_time_is_ignored_and_fixed_dt_is_used(self):
        values = [0.25, -0.1, 0.4, 0.2, 0.6]
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            regular_path = directory / "regular.csv"
            irregular_path = directory / "irregular.csv"
            regular_path.write_text(
                "elapsed time,value\n"
                + "\n".join(
                    f"{index * DT:.2f},{value}" for index, value in enumerate(values)
                )
                + "\n",
                encoding="utf-8",
            )
            irregular_path.write_text(
                "elapsed time,value\n"
                + "\n".join(
                    f"{elapsed},{value}"
                    for elapsed, value in zip(
                        [100.0, -8.0, 25.5, 25.50001, 99999.0], values
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            regular = csv_reference(regular_path)
            irregular = csv_reference(irregular_path)

        self.assertEqual(regular.dt, DT)
        self.assertEqual(irregular.dt, DT)
        self.assertEqual(regular.original_count, len(values))
        self.assertEqual(irregular.original_count, len(values))
        np.testing.assert_array_equal(regular.position, irregular.position)
        np.testing.assert_array_equal(regular.time, irregular.time)
        np.testing.assert_array_equal(
            regular.time, np.arange(regular.position.size, dtype=float) * DT
        )


if __name__ == "__main__":
    unittest.main()
