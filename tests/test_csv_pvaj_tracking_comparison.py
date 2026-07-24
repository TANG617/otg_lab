import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.compare_csv_pvaj_tracking import (
    DATASET_LABELS,
    build_metric_comparisons,
    fixed_grid_pvaj,
    load_trace,
    profile_trace,
    pvaj_metric_rows,
)


class TestCsvComparisonInput(unittest.TestCase):
    def _write_trace(self, path, values, elapsed=None):
        if elapsed is None:
            elapsed = np.arange(len(values), dtype=float) * 0.01
        rows = [
            "elapsed time,timestamp,topic,value",
            *[
                f"{time},{1000.0 + time},/joint,{value}"
                for time, value in zip(elapsed, values)
            ],
        ]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def test_load_trace_profiles_expected_grain(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trace.csv"
            values = np.linspace(-0.2, 0.5, 12)
            self._write_trace(path, values)
            trace = load_trace(path, "current_csv", DATASET_LABELS["current_csv"])
            profile = profile_trace(trace)

        self.assertEqual(trace.position.size, 12)
        self.assertEqual(profile["rows"], 12)
        self.assertEqual(profile["topic_count"], 1)
        self.assertAlmostEqual(profile["source_dt_p50_ms"], 10.0)
        self.assertAlmostEqual(profile["position_range_rad"], 0.7)

    def test_load_trace_rejects_nonmonotonic_source_time(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trace.csv"
            self._write_trace(
                path,
                np.linspace(0.0, 1.0, 8),
                elapsed=[0.0, 0.01, 0.02, 0.015, 0.04, 0.05, 0.06, 0.07],
            )
            with self.assertRaisesRegex(
                ValueError, "elapsed time must be strictly increasing"
            ):
                load_trace(path, "current_csv", DATASET_LABELS["current_csv"])

    def test_repository_candidate_is_an_exact_valid_recording_shape(self):
        root = Path(__file__).resolve().parents[1]
        trace = load_trace(
            root / "data" / "simplified-tasks_no-velocity-limit.csv",
            "new_csv",
            DATASET_LABELS["new_csv"],
        )
        self.assertEqual(trace.position.size, 1275)
        self.assertEqual(np.unique(trace.topic).size, 1)
        self.assertTrue(np.all(np.isfinite(trace.position)))


class TestRawPvajMetrics(unittest.TestCase):
    def test_centered_derivatives_recover_quadratic_interior(self):
        time = np.arange(20, dtype=float) * 0.01
        position = 0.3 - 0.4 * time + 0.5 * 2.5 * time**2
        signals = fixed_grid_pvaj(position)
        interior = slice(3, -3)
        np.testing.assert_allclose(
            signals["velocity"][interior],
            -0.4 + 2.5 * time[interior],
            rtol=0.0,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            signals["acceleration"][interior],
            2.5,
            rtol=0.0,
            atol=2e-11,
        )
        np.testing.assert_allclose(
            signals["jerk"][interior],
            0.0,
            rtol=0.0,
            atol=2e-9,
        )

    def test_pvaj_summary_excludes_three_boundary_samples(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trace.csv"
            values = np.linspace(0.0, 0.2, 12)
            rows = [
                "elapsed time,timestamp,topic,value",
                *[
                    f"{index * 0.01},{1000 + index * 0.01},/joint,{value}"
                    for index, value in enumerate(values)
                ],
            ]
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            trace = load_trace(path, "current_csv", DATASET_LABELS["current_csv"])
            metrics = pvaj_metric_rows(
                trace,
                fixed_grid_pvaj(trace.position),
                "fixed_10ms_centered",
            )
        self.assertEqual(len(metrics), 4)
        self.assertTrue(all(row["evaluation_start_index"] == 3 for row in metrics))
        self.assertTrue(
            all(row["evaluation_stop_index_exclusive"] == 9 for row in metrics)
        )


class TestComparisonDirection(unittest.TestCase):
    def test_metric_comparison_uses_candidate_minus_baseline(self):
        trace_rows = [
            {
                "dataset": "current_csv",
                "position_range_rad": 2.0,
                "fixed_grid_duration_s": 4.0,
            },
            {
                "dataset": "new_csv",
                "position_range_rad": 1.0,
                "fixed_grid_duration_s": 5.0,
            },
        ]
        raw_rows = []
        for dataset, scale in (("current_csv", 2.0), ("new_csv", 1.0)):
            for signal in ("velocity", "acceleration", "jerk"):
                raw_rows.append(
                    {
                        "dataset": dataset,
                        "signal": signal,
                        "derivative_basis": "fixed_10ms_centered",
                        "max_abs": scale,
                        "p99_abs": scale,
                        "rms": scale,
                    }
                )
        tracking_rows = []
        for dataset, scale in (("current_csv", 2.0), ("new_csv", 1.0)):
            tracking_rows.append(
                {
                    "dataset": dataset,
                    "method_id": "p",
                    "rmse": scale,
                    "normalized_rmse_robust": scale,
                    "max_error": scale,
                    "normalized_max_error_range": scale,
                    "abs_best_lag_ms": scale,
                    "lag_aligned_rmse": scale,
                    "reachable_within_10ms_rate": 1.0 / scale,
                    "ruckig_compute_p99_us": scale,
                }
            )
        rows = build_metric_comparisons(trace_rows, raw_rows, tracking_rows)
        velocity = next(row for row in rows if row["metric"] == "max_abs_velocity")
        self.assertEqual(velocity["absolute_delta"], -1.0)
        self.assertEqual(velocity["change_pct"], -50.0)


if __name__ == "__main__":
    unittest.main()
