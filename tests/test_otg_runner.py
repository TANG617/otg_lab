"""Regression tests for the preconstructed target-state Ruckig runner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otg_runner import (  # noqa: E402
    PHASE_A_P_ONLY_REFERENCE_METRICS,
    PHASE_A_P_ONLY_TOLERANCES,
    run_phase_a_p_only_compatibility,
    run_target_state_sequence,
    target_state_is_feasible,
)
from target_state_experiment import csv_reference  # noqa: E402

DT = 0.01
VENDOR_LIMITS = {
    "max_velocity": 4.1,
    "max_acceleration": 8.2,
    "max_jerk": 4000.0,
}


def quintic_reference(duration=1.0, amplitude=0.1):
    """Return a feasible stationary-endpoint quintic p/v/a reference."""
    time = np.arange(0.0, duration + DT / 2.0, DT)
    phase = time / duration
    position = amplitude * (
        10.0 * phase**3 - 15.0 * phase**4 + 6.0 * phase**5
    )
    velocity = amplitude * (
        30.0 * phase**2 - 60.0 * phase**3 + 30.0 * phase**4
    ) / duration
    acceleration = amplitude * (
        60.0 * phase - 180.0 * phase**2 + 120.0 * phase**3
    ) / duration**2
    return np.column_stack((position, velocity, acceleration))


class RunTargetStateSequenceTests(unittest.TestCase):
    def run_vendor(self, reference_position, target_states, **kwargs):
        return run_target_state_sequence(
            reference_position,
            target_states,
            DT,
            **VENDOR_LIMITS,
            **kwargs,
        )

    @staticmethod
    def output_states(result):
        return np.column_stack(
            (
                result["position"],
                result["velocity"],
                result["acceleration"],
            )
        )

    def test_target_at_k_produces_output_at_k_plus_one(self):
        truth = quintic_reference()
        result = self.run_vendor(
            truth[:, 0],
            truth,
            initial_state=truth[0],
            project_targets=False,
        )

        self.assertEqual(result["target_timing"], "target[k] -> output[k+1]")
        np.testing.assert_allclose(
            self.output_states(result)[1:],
            truth[:-1],
            rtol=0.0,
            atol=1e-12,
        )

    def test_next_cycle_oracle_reproduces_feasible_reference(self):
        truth = quintic_reference()
        next_cycle_targets = np.empty_like(truth)
        next_cycle_targets[:-1] = truth[1:]
        next_cycle_targets[-1] = truth[-1]

        result = self.run_vendor(
            truth[:, 0],
            next_cycle_targets,
            initial_state=truth[0],
            project_targets=False,
        )

        np.testing.assert_allclose(
            self.output_states(result),
            truth,
            rtol=0.0,
            atol=1e-12,
        )
        self.assertFalse(np.any(result["projection_mask"]))
        self.assertTrue(np.all(result["target_feasible_mask"]))

    def test_infeasible_targets_are_projected_without_moving_position(self):
        reference_position = np.zeros(8)
        raw_targets = np.zeros((reference_position.size, 3))
        raw_targets[:, 0] = [0.0, 0.0, 0.1, 0.1, 0.1, -0.1, -0.1, -0.1]
        raw_targets[2, 1:] = [10.0, 20.0]
        raw_targets[5, 1:] = [-6.0, -30.0]

        result = self.run_vendor(reference_position, raw_targets)

        np.testing.assert_array_equal(
            result["projection_mask"],
            [False, False, True, False, False, True, False, False],
        )
        np.testing.assert_array_equal(
            result["target_feasible_mask"],
            [True, True, False, True, True, False, True, True],
        )
        np.testing.assert_array_equal(result["raw_target_states"], raw_targets)
        np.testing.assert_allclose(
            result["target_states"][:, 0],
            raw_targets[:, 0],
            rtol=0.0,
            atol=0.0,
        )

        for index in (2, 5):
            projected = result["target_states"][index]
            self.assertTrue(
                target_state_is_feasible(
                    projected[1],
                    projected[2],
                    **VENDOR_LIMITS,
                )
            )
            velocity_scale = projected[1] / raw_targets[index, 1]
            acceleration_scale = projected[2] / raw_targets[index, 2]
            self.assertAlmostEqual(velocity_scale, acceleration_scale, places=12)

        with self.assertRaisesRegex(ValueError, "projection disabled at index=2"):
            self.run_vendor(
                reference_position,
                raw_targets,
                project_targets=False,
            )

    def test_result_arrays_have_documented_shapes_and_finite_values(self):
        truth = quintic_reference(duration=0.3, amplitude=0.02)
        targets = np.empty_like(truth)
        targets[:-1] = truth[1:]
        targets[-1] = truth[-1]
        count = truth.shape[0]

        result = self.run_vendor(
            truth[:, 0],
            targets,
            initial_state=truth[0],
            project_targets=False,
        )

        for name in ("position", "velocity", "acceleration", "new_jerk"):
            self.assertEqual(result[name].shape, (count,), name)
            self.assertTrue(np.all(np.isfinite(result[name])), name)

        for name in ("raw_target_states", "target_states"):
            self.assertEqual(result[name].shape, (count, 3), name)
            self.assertTrue(np.all(np.isfinite(result[name])), name)

        for name in ("target_feasible_mask", "projection_mask"):
            self.assertEqual(result[name].shape, (count,), name)
            self.assertEqual(result[name].dtype, np.dtype(bool), name)

        durations = result["trajectory_durations"]
        self.assertEqual(durations.shape, (count,))
        self.assertTrue(np.isnan(durations[0]))
        self.assertTrue(np.all(np.isfinite(durations[1:])))
        self.assertTrue(np.all(durations[1:] >= DT - 1e-12))

        compute_us = result["ruckig_compute_us"]
        self.assertEqual(compute_us.shape, (count - 1,))
        self.assertTrue(np.all(np.isfinite(compute_us)))
        self.assertTrue(np.all(compute_us >= 0.0))
        self.assertAlmostEqual(result["minimum_duration_ms"], 10.0)
        self.assertEqual(result["native_command_executed_mask"].shape, (count - 1,))
        self.assertTrue(np.all(result["native_command_executed_mask"]))
        self.assertFalse(np.any(result["unexpected_fallback_mask"]))
        self.assertEqual(result["native_execution_rate"], 1.0)
        self.assertEqual(result["unexpected_fallback_rate"], 0.0)

    def test_legacy_csv_p_only_ordinary_ruckig_regression(self):
        reference = csv_reference(ROOT / "plot_data.csv")
        result = run_phase_a_p_only_compatibility(
            reference.position,
            original_count=reference.original_count,
        )

        np.testing.assert_array_equal(
            result["raw_target_states"][:, 0], reference.position
        )
        np.testing.assert_array_equal(result["raw_target_states"][:, 1:], 0.0)
        self.assertEqual(result["target_timing"], "target[k] -> output[k+1]")
        self.assertAlmostEqual(result["minimum_duration_ms"], 10.0)

        metrics = result["compatibility_metrics"]
        for name, expected in PHASE_A_P_ONLY_REFERENCE_METRICS.items():
            with self.subTest(metric=name):
                self.assertLessEqual(
                    abs(metrics[name] - expected),
                    PHASE_A_P_ONLY_TOLERANCES[name],
                )
        self.assertTrue(all(result["acceptance_criteria"].values()))

    def test_output_respects_velocity_acceleration_and_direct_jerk_limits(self):
        count = 240
        target_position = np.zeros(count)
        target_position[20:100] = 0.5
        target_position[100:170] = -0.2
        target_position[170:] = 0.1
        target_states = np.column_stack(
            (target_position, np.zeros(count), np.zeros(count))
        )
        limits = {
            "max_velocity": 1.0,
            "max_acceleration": 2.0,
            "max_jerk": 20.0,
        }

        result = run_target_state_sequence(
            target_position,
            target_states,
            DT,
            **limits,
        )

        tolerance = 1e-9
        self.assertLessEqual(
            float(np.max(np.abs(result["velocity"]))),
            limits["max_velocity"] + tolerance,
        )
        self.assertLessEqual(
            float(np.max(np.abs(result["acceleration"]))),
            limits["max_acceleration"] + tolerance,
        )
        self.assertLessEqual(
            float(np.max(np.abs(result["new_jerk"]))),
            limits["max_jerk"] + tolerance,
        )
        self.assertGreater(float(np.max(np.abs(result["new_jerk"]))), 0.0)

        sampled_jerk = np.diff(result["acceleration"]) / DT
        self.assertLessEqual(
            float(np.max(np.abs(sampled_jerk))),
            limits["max_jerk"] + tolerance,
        )


if __name__ == "__main__":
    unittest.main()
