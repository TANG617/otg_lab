import unittest

import numpy as np

from otg_lab.followers import (
    DirectExecutableFollower,
    RuckigFollower,
    scalar_project_target_state,
)
from otg_lab.governors import (
    JerkQPGovernor,
    MotionLimits,
    OneStepBoundedJerkGovernor,
    feasible_jerk_intervals,
    integrate_constant_jerk,
    point_is_admissible,
    segment_is_feasible,
    velocity_extrema_constant_jerk,
)
from otg_lab.plants import DelayedServoPlant, IdealCommandPlant


class GovernorPhysicsTests(unittest.TestCase):
    def setUp(self):
        self.dt = 0.01
        self.limits = MotionLimits.broadcast(1, 4.1, 8.2, 4000.0)

    def test_constant_jerk_integration(self):
        state = np.array([0.2, -0.4, 1.5])
        jerk = -30.0
        result = integrate_constant_jerk(state, jerk, self.dt)
        expected = np.array(
            [
                0.2 - 0.4 * self.dt + 0.5 * 1.5 * self.dt**2 - 30 * self.dt**3 / 6,
                -0.4 + 1.5 * self.dt - 15 * self.dt**2,
                1.5 - 30 * self.dt,
            ]
        )
        np.testing.assert_allclose(result, expected, rtol=0.0, atol=1e-15)

    def test_velocity_interior_extremum_is_checked(self):
        state = np.array([0.0, 4.09, 8.0])
        jerk = -1600.0
        minimum, maximum, times = velocity_extrema_constant_jerk(
            state, jerk, self.dt
        )
        self.assertIn(0.005, times)
        self.assertAlmostEqual(maximum, 4.11, places=12)
        self.assertFalse(segment_is_feasible(state, jerk, self.dt, self.limits))
        intervals = feasible_jerk_intervals(state, self.dt, self.limits)
        for low, high in intervals:
            self.assertFalse(low < jerk < high)
        self.assertLess(minimum, maximum)

    def test_one_step_governor_is_reachable_and_recursive(self):
        governor = OneStepBoundedJerkGovernor(1, self.dt, self.limits)
        state = np.zeros((1, 3))
        rng = np.random.default_rng(17)
        for k in range(500):
            raw_target = rng.normal(size=(1, 3)) * np.array([[0.5, 3.0, 20.0]])
            result = governor.update(
                raw_target, control_time=k * self.dt, current_state=state if k == 0 else None
            )
            reconstructed = integrate_constant_jerk(state, result.jerk, self.dt)
            np.testing.assert_allclose(
                reconstructed, result.executable_state, rtol=0.0, atol=2e-10
            )
            self.assertTrue(point_is_admissible(result.executable_state, self.limits))
            self.assertTrue(
                segment_is_feasible(state[0], result.jerk[0], self.dt, self.limits)
            )
            self.assertTrue(
                feasible_jerk_intervals(
                    result.executable_state[0], self.dt, self.limits
                )
            )
            state = result.executable_state

    def test_nonfinite_target_has_explicit_fallback(self):
        governor = OneStepBoundedJerkGovernor(1, self.dt, self.limits)
        result = governor.update(
            np.array([[np.nan, np.inf, 0.0]]),
            control_time=0.0,
            current_state=np.zeros((1, 3)),
        )
        self.assertTrue(result.fallback)
        self.assertEqual(result.fallback_reason, "nonfinite_raw_target")
        self.assertTrue(np.all(np.isfinite(result.executable_state)))

    def test_qp_optimal_and_nonfinite_fallback(self):
        target = np.repeat(np.array([[[0.02, 0.4, 0.0]]]), 10, axis=0)
        qp = JerkQPGovernor(1, self.dt, self.limits, horizon_steps=10)
        solved = qp.update(target, control_time=0.0, current_state=np.zeros((1, 3)))
        self.assertFalse(solved.fallback)
        self.assertIn(solved.solver_status, {"solved", "solved_inaccurate"})
        self.assertTrue(
            segment_is_feasible(
                np.zeros(3), solved.jerk[0], self.dt, self.limits
            )
        )

        impossible = target.copy()
        impossible[0, 0, 0] = np.nan
        fallback = qp.update(impossible, control_time=self.dt)
        self.assertTrue(fallback.fallback)
        self.assertEqual(fallback.fallback_reason, "nonfinite_reference_sequence")
        self.assertEqual(fallback.qp_status_category, "qp_invalid_input")

    def test_qp_real_max_iteration_timeout_and_infeasible_status_fallbacks(self):
        target = np.repeat(np.array([[[2.0, 4.0, 8.0]]]), 30, axis=0)
        timeout = JerkQPGovernor(
            1,
            self.dt,
            self.limits,
            horizon_steps=30,
            max_iter=1,
        ).update(target, control_time=0.0, current_state=np.zeros((1, 3)))
        self.assertTrue(timeout.fallback)
        self.assertEqual(timeout.fallback_reason, "qp_max_iter_reached")
        self.assertIn("maximum_iterations_reached", timeout.solver_status)

        class _Info:
            status = "primal infeasible"
            iter = 7

        class _Solution:
            info = _Info()
            x = None

        class _InfeasibleSolver:
            def update(self, **_kwargs):
                return None

            def solve(self):
                return _Solution()

        infeasible_governor = JerkQPGovernor(
            1, self.dt, self.limits, horizon_steps=10
        )
        infeasible_governor._solver = _InfeasibleSolver()
        infeasible = infeasible_governor.update(
            target[:10], control_time=0.0, current_state=np.zeros((1, 3))
        )
        self.assertTrue(infeasible.fallback)
        self.assertEqual(infeasible.fallback_reason, "qp_primal_infeasible")
        self.assertIn("primal_infeasible", infeasible.solver_status)


class FollowerAndPlantTests(unittest.TestCase):
    def setUp(self):
        self.dt = 0.01
        self.limits = MotionLimits.broadcast(1)
        self.initial = np.zeros((1, 3))

    def test_direct_and_ruckig_reach_governed_target(self):
        governor = OneStepBoundedJerkGovernor(1, self.dt, self.limits)
        governed = governor.update(
            np.array([[0.01, 0.5, 1.0]]),
            control_time=0.0,
            current_state=self.initial,
        )
        direct = DirectExecutableFollower(1, self.dt, self.limits).update(
            governed.executable_state,
            control_time=0.0,
            current_state=self.initial,
        )
        self.assertFalse(direct.fallback)
        np.testing.assert_allclose(direct.command_state, governed.executable_state)

        ordinary = RuckigFollower(1, self.dt, self.limits).update(
            governed.executable_state,
            control_time=0.0,
            current_state=self.initial,
        )
        self.assertFalse(ordinary.fallback)
        self.assertLessEqual(ordinary.free_trajectory_duration, self.dt + 1e-8)
        np.testing.assert_allclose(
            ordinary.command_state, governed.executable_state, rtol=0.0, atol=2e-8
        )
        self.assertEqual(np.sum(ordinary.continuous_audit["violation_count"]), 0)

    def test_scalar_projection_is_explicit(self):
        target = np.array([[0.0, 4.0, 100.0]])
        projected, changed = scalar_project_target_state(target, self.limits)
        self.assertTrue(changed)
        self.assertEqual(projected[0, 0], target[0, 0])
        self.assertLess(abs(projected[0, 2]), abs(target[0, 2]))

    def test_single_ruckig_solver_synchronizes_multiple_joints(self):
        limits = MotionLimits.broadcast(3)
        follower = RuckigFollower(3, self.dt, limits)
        current = np.zeros((3, 3))
        target = np.array([[0.1, 0.0, 0.0], [0.2, 0.0, 0.0], [-0.05, 0.0, 0.0]])
        result = follower.update(target, control_time=0.0, current_state=current)
        self.assertEqual(result.command_state.shape, (3, 3))
        self.assertEqual(result.continuous_audit["max_velocity"].shape, (3,))
        # A synchronized Ruckig solve can switch jerk inside one control period.
        # A single recorded command jerk cannot represent that endpoint, so the
        # follower must commit its verified constant-jerk safety action instead
        # of publishing a dynamically impossible command.
        self.assertTrue(result.fallback_applied)
        np.testing.assert_allclose(
            result.command_state,
            integrate_constant_jerk(current, result.command_jerk, self.dt),
            rtol=0.0,
            atol=2e-8,
        )

    def test_ideal_and_delayed_plants_are_distinct(self):
        command = np.array([[0.1, 0.2, 0.0]])
        ideal = IdealCommandPlant(1, self.dt)
        ideal.reset(self.initial)
        ideal_result = ideal.update(command, command_time=self.dt)
        np.testing.assert_array_equal(ideal_result.true_state, command)

        delayed = DelayedServoPlant(
            1,
            self.dt,
            self.limits,
            bandwidth_hz=5.0,
            damping_ratio=0.8,
            delay_s=self.dt,
            position_noise_sigma=1e-4,
            seed=3,
        )
        delayed.reset(self.initial)
        delayed_result = delayed.update(command, command_time=self.dt)
        self.assertFalse(np.allclose(delayed_result.true_state, command))
        self.assertFalse(
            np.array_equal(delayed_result.true_state, delayed_result.measured_state)
        )


if __name__ == "__main__":
    unittest.main()
