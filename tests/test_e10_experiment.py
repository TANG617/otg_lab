from __future__ import annotations

import csv
from pathlib import Path

import pytest

from otg_lab.analysis import MetricRow
from otg_lab.cli import load_experiment_spec
from otg_lab.constraints import ruckig_target_admissible
from otg_lab.csvio import load_trajectory_csv
from otg_lab.governors import MotionLimits as NumericalMotionLimits
from otg_lab.models import TrackingRun, TrackingStatus
from otg_lab.tracking import run_tracking

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_INPUT_ID = "recorded_tasks_original_no_velocity_limit"
SIMPLIFIED_INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
INPUT_IDS = (ORIGINAL_INPUT_ID, SIMPLIFIED_INPUT_ID)
METHOD_IDS = (
    "pva_est_backward_o1_k",
    "pva_est_backward_o2_k",
    "pva_est_centered_o2_km1",
    "pva_pred_backward_o1_kp1",
    "pva_pred_backward_o2_kp1",
)
TARGET_AGE_SAMPLES = {
    "pva_est_backward_o1_k": 1.0,
    "pva_est_backward_o2_k": 1.0,
    "pva_est_centered_o2_km1": 2.0,
    "pva_pred_backward_o1_kp1": 0.0,
    "pva_pred_backward_o2_kp1": 0.0,
}
ACCELERATION_LEVELS = (4.1, 6.0, 8.2, 12.0, 16.4)
JERK_LEVELS = (41.0, 200.0, 800.0, 1600.0, 3200.0, 4000.0, 8000.0)
REPRESENTATIVE_LIMITS = (
    (4.1, 41.0),
    (4.1, 8000.0),
    (8.2, 4000.0),
    (16.4, 41.0),
    (16.4, 8000.0),
)


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def _case_id(method_id: str, acceleration: float, jerk: float) -> str:
    return f"{method_id}__a{_token(acceleration)}_j{_token(jerk)}"


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_e10_declares_five_separate_e02_sensitivity_surfaces() -> None:
    spec = load_experiment_spec(ROOT, "E10")
    e04 = load_experiment_spec(ROOT, "E04")

    assert spec.directory_name == "E10_pva_finite_difference_limit_sensitivity"
    assert tuple(method.method_id for method in spec.methods) == METHOD_IDS
    assert tuple(item.input_id for item in spec.inputs) == INPUT_IDS
    assert tuple(item.csv_path for item in spec.inputs) == tuple(
        Path(f"data/trajectories/{input_id}.csv") for input_id in INPUT_IDS
    )
    assert len(spec.cases) == 175
    assert len(spec.cases) * len(spec.inputs) == 350
    assert len(spec.comparison_spec.pairs) == 170
    assert spec.comparison_spec.input_ids == INPUT_IDS
    assert not spec.factor_heatmaps
    assert spec.artifact_writer is not None

    e04_methods = {method.method_id: method for method in e04.methods}
    for method in spec.methods:
        e04_method = e04_methods[method.method_id]
        for component_name in (
            "estimator",
            "predictor",
            "target_builder",
            "follower",
        ):
            assert (
                getattr(method, component_name).as_dict()
                == getattr(e04_method, component_name).as_dict()
            )
        assert method.governor.component_id == "configured_limit_projection"
        assert method.governor.factory is None
        assert method.required

    cases = {case.case_id: case for case in spec.cases}
    for method_id in METHOD_IDS:
        method_cases = [case for case in spec.cases if case.method_id == method_id]
        assert len(method_cases) == 35
        assert {
            (
                case.run_config.limits.max_acceleration_rad_s2,
                case.run_config.limits.max_jerk_rad_s3,
            )
            for case in method_cases
        } == {
            (acceleration, jerk)
            for acceleration in ACCELERATION_LEVELS
            for jerk in JERK_LEVELS
        }
        vendor_id = _case_id(method_id, 8.2, 4000.0)
        method_pairs = [
            pair
            for pair in spec.comparison_spec.pairs
            if pair.baseline_method_id == vendor_id
        ]
        assert len(method_pairs) == 34
        assert {cases[pair.candidate_method_id].method_id for pair in method_pairs} == {
            method_id
        }

    assert {case.run_config.limits.max_velocity_rad_s for case in spec.cases} == {4.1}
    main = next(
        window for window in spec.windows if window.window_id == "main_evaluation"
    )
    assert main.start_time_s == pytest.approx(0.04)
    assert main.end_time_s is None
    assert spec.metric_roles["primary"] == ("position_rmse",)
    assert "position_bias" in spec.metric_roles["secondary"]
    assert "deadline_miss_rate" in spec.metric_roles["guardrail"]
    assert "posterior_velocity_rmse" not in spec.metric_ids
    assert "raw_target_acceleration_rmse" not in spec.metric_ids


def test_e10_methods_complete_representative_limit_cases() -> None:
    spec = load_experiment_spec(ROOT, "E10")
    input_spec = next(
        item for item in spec.inputs if item.input_id == ORIGINAL_INPUT_ID
    )
    csv_path, metadata_path = input_spec.resolve(ROOT)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    cases = {case.case_id: case for case in spec.cases}

    for method_id in METHOD_IDS:
        for acceleration, jerk in REPRESENTATIVE_LIMITS:
            case = cases[_case_id(method_id, acceleration, jerk)]
            run = run_tracking(
                reference,
                spec.method_for_case(case),
                case.run_config,
            )
            assert run.status.completed
            assert run.status.valid_cycles == reference.sample_count - 1
            limits = NumericalMotionLimits.broadcast(
                1,
                4.1,
                acceleration,
                jerk,
            )
            mature = [
                row
                for row in run.trace_rows
                if float(row["command_time_s"]) >= 0.04 - 1e-12
            ]
            assert mature
            assert all(
                row["raw_target_causal"] is True
                and row["raw_target_startup"] is False
                and float(row["raw_target_age_samples"])
                == pytest.approx(
                    TARGET_AGE_SAMPLES[method_id],
                    abs=1e-9,
                )
                and float(row["raw_target_position_rad"])
                == pytest.approx(
                    float(row["executable_target_position_rad"]),
                    abs=1e-12,
                )
                and ruckig_target_admissible(
                    [
                        float(row["executable_target_position_rad"]),
                        float(row["executable_target_velocity_rad_s"]),
                        float(row["executable_target_acceleration_rad_s2"]),
                    ],
                    limits,
                )
                for row in mature
            )

    simplified_input = next(
        item for item in spec.inputs if item.input_id == SIMPLIFIED_INPUT_ID
    )
    csv_path, metadata_path = simplified_input.resolve(ROOT)
    simplified = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    for method_id in METHOD_IDS:
        case = cases[_case_id(method_id, 8.2, 4000.0)]
        run = run_tracking(
            simplified,
            spec.method_for_case(case),
            case.run_config,
        )
        assert run.status.completed
        assert run.status.valid_cycles == simplified.sample_count - 1


def test_e10_artifacts_are_grouped_by_method(tmp_path: Path) -> None:
    spec = load_experiment_spec(ROOT, "E10")
    tracking_runs: dict[tuple[str, str], TrackingRun] = {}
    metric_rows: list[MetricRow] = []
    guardrails = spec.metric_roles["guardrail"]

    for input_index, input_id in enumerate(INPUT_IDS):
        for case in spec.cases:
            method_id = case.method_id
            acceleration = case.run_config.limits.max_acceleration_rad_s2
            jerk = case.run_config.limits.max_jerk_rad_s3
            case_id = case.case_id
            trace = {
                "cycle_index": 0,
                "measurement_time_s": 0.0,
                "command_time_s": 0.01,
                "raw_target_time_s": 0.01,
                "raw_target_available_time_s": 0.0,
                "raw_target_age_samples": TARGET_AGE_SAMPLES[method_id],
                "raw_target_position_rad": 0.0,
                "raw_target_velocity_rad_s": 0.0,
                "raw_target_acceleration_rad_s2": 0.0,
                "raw_target_status": "ok",
                "raw_target_startup": False,
                "raw_target_causal": True,
                "raw_target_position_source": "scheduled_reference",
                "raw_target_derivative_source": "finite_difference",
                "raw_target_latest_input_time_s": 0.0,
                "executable_target_position_rad": 0.0,
                "executable_target_velocity_rad_s": 0.0,
                "executable_target_acceleration_rad_s2": 0.0,
                "status": "ok",
            }
            tracking_runs[(case_id, input_id)] = TrackingRun(
                method_id=case_id,
                command=None,
                trace_rows=(trace,),
                status=TrackingStatus(
                    completed=True,
                    valid_cycles=1,
                    total_cycles=1,
                ),
            )
            rmse = 0.01 + 0.01 * input_index + 1e-4 * acceleration + 1e-8 * jerk
            lag = (
                0.001 * METHOD_IDS.index(method_id)
                + 0.002 * input_index
                + 1e-5 * acceleration
            )
            for window_id in ("main_evaluation", "full_overlap"):
                metric_rows.extend(
                    (
                        MetricRow(
                            input_id,
                            case_id,
                            window_id,
                            "position_rmse",
                            rmse,
                            "rad",
                            "lower",
                        ),
                        MetricRow(
                            input_id,
                            case_id,
                            window_id,
                            "lag_s",
                            lag,
                            "s",
                            "none",
                        ),
                    )
                )
            metric_rows.extend(
                MetricRow(
                    input_id,
                    case_id,
                    "full_overlap",
                    metric_id,
                    (None if metric_id == "output_jerk_violation_count" else 0.0),
                    "count" if metric_id.endswith("_count") else "1",
                    "lower",
                    status=(
                        "unavailable_missing_command_jerk"
                        if metric_id == "output_jerk_violation_count"
                        else "available"
                    ),
                )
                for metric_id in guardrails
            )

    assert spec.artifact_writer is not None
    spec.artifact_writer(
        analysis_directory=tmp_path,
        references={},
        tracking_runs=tracking_runs,
        trajectory_rows=metric_rows,
        experiment_spec=spec,
        create_figures=False,
    )

    surface = _csv_rows(tmp_path / "pva_limit_sensitivity.csv")
    feasibility = _csv_rows(tmp_path / "raw_target_feasibility.csv")
    summaries = _csv_rows(tmp_path / "method_sensitivity_summary.csv")
    assert len(surface) == 350
    assert len(feasibility) == 350
    assert len(summaries) == 10
    assert {row["input_id"] for row in surface} == set(INPUT_IDS)
    assert {row["status"] for row in surface} == {"available"}
    assert all(row["prefix_rmse_used"] == "false" for row in surface)

    for method_id in METHOD_IDS:
        vendor_rmse_by_input: dict[str, float] = {}
        for input_id in INPUT_IDS:
            method_directory = tmp_path / "by_method" / method_id / input_id
            rmse_rows = _csv_rows(method_directory / "constraint_sensitivity_rmse.csv")
            lag_rows = _csv_rows(method_directory / "constraint_sensitivity_lag_ms.csv")
            projection_rows = _csv_rows(
                method_directory / "constraint_sensitivity_projection_rate.csv"
            )
            assert len(rmse_rows) == len(lag_rows) == len(projection_rows) == 35
            assert {row["method_id"] for row in rmse_rows} == {method_id}
            assert {row["input_id"] for row in rmse_rows} == {input_id}
            vendor = next(
                row for row in rmse_rows if row["is_vendor_baseline"] == "true"
            )
            assert float(vendor["rmse_ratio_vs_own_vendor"]) == pytest.approx(1.0)
            vendor_rmse_by_input[input_id] = float(vendor["vendor_position_rmse_rad"])
        assert (
            vendor_rmse_by_input[ORIGINAL_INPUT_ID]
            != vendor_rmse_by_input[SIMPLIFIED_INPUT_ID]
        )

    summary = (tmp_path / "acceptance_summary.md").read_text(encoding="utf-8")
    assert "All 350 runs complete: `yes`" in summary
    assert "normalized within each method" in summary
