from __future__ import annotations

from pathlib import Path

import pytest

from otg_lab.analysis import MetricSet, analyze_tracking
from otg_lab.cli import load_experiment_spec
from otg_lab.csvio import load_trajectory_csv, load_trajectory_metadata
from otg_lab.tracking import run_tracking

EXPECTED_VENDOR_RATIOS = (
    0.125,
    0.2,
    0.25,
    0.3,
    0.4,
    0.5,
    0.6,
    0.8,
    0.9,
    0.95,
    1.0,
    1.05,
    1.1,
    1.2,
    1.5,
    1.8,
    2.0,
    2.2,
    3.0,
    4.0,
)


def _metric_table(project_root: Path, input_id: str, case_id: str):
    spec = load_experiment_spec(project_root, "E07")
    input_spec = next(item for item in spec.inputs if item.input_id == input_id)
    csv_path, metadata_path = input_spec.resolve(project_root)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    case = next(item for item in spec.cases if item.case_id == case_id)
    run = run_tracking(reference, spec.method_for_case(case), case.run_config)
    table = analyze_tracking(
        reference,
        run,
        MetricSet(
            metric_ids=(
                "rest_to_rest_pulse_fraction",
                "stop_go_event_rate_hz",
                "endpoint_stop_fraction",
                "profile_peak_velocity_to_reference_median",
                "profile_velocity_ripple_median",
                "profile_velocity_ripple_to_reference_median",
                "profile_velocity_ripple_to_reference_p95",
                "one_cycle_reachability_pulse_agreement",
                "profile_exact_fraction",
                "profile_constraint_violation_count",
                "fallback_rate",
                "solver_failure_count",
            ),
            windows=spec.windows,
            input_id=input_id,
            limits=case.run_config.limits,
        ),
    )
    return run, table


def _input_id(ratio: float) -> str:
    token = f"{ratio:g}".replace(".", "p")
    return f"e07_cv_vendor_ratio_{token}"


def test_e07_declares_only_the_p_only_threshold_surface() -> None:
    project_root = Path(__file__).parents[1]
    spec = load_experiment_spec(project_root, "E07")

    assert spec.directory_name == "E07_position_only_stop_and_go"
    assert len(spec.inputs) == 20
    assert len(spec.methods) == 1
    assert len(spec.cases) == 4
    assert spec.artifact_writer is not None
    assert spec.comparison_spec.pairs == ()
    method = spec.methods[0]
    assert method.method_id == "position_zoh_p_ruckig"
    assert method.estimator.component_id == "position_only"
    assert method.predictor.component_id == "zero_order_hold"
    assert method.target_builder.component_id == "p"
    assert method.governor.component_id == "none"
    assert method.follower.component_id == "ruckig"
    assert all(case.method_id == method.method_id for case in spec.cases)
    assert {case.factors["limit_scale"] for case in spec.cases} == {
        0.25,
        0.5,
        1.0,
        2.0,
    }
    assert {
        (
            case.run_config.limits.max_acceleration_rad_s2,
            case.run_config.limits.max_jerk_rad_s3,
        )
        for case in spec.cases
    } == {
        (2.05, 1000.0),
        (4.1, 2000.0),
        (8.2, 4000.0),
        (16.4, 8000.0),
    }
    assert spec.windows[1].window_id == "main_evaluation"
    assert spec.windows[1].start_time_s == 0.5
    assert spec.windows[1].end_time_s == 2.5
    assert {
        "profile_velocity_ripple_median",
        "profile_velocity_ripple_to_reference_median",
        "profile_velocity_ripple_to_reference_p95",
    } <= set(spec.metric_roles["secondary"])
    experiment_source = (
        project_root
        / "experiments"
        / "E07_position_only_stop_and_go"
        / "experiment.py"
    ).read_text()
    assert "causal_ablation.csv" not in experiment_source
    assert "target_builder=ComponentSpec(\"pv\")" not in experiment_source
    assert "target_builder=ComponentSpec(\"pva\")" not in experiment_source


def test_e07_inputs_are_experiment_local_constant_velocity_truth() -> None:
    project_root = Path(__file__).parents[1]
    spec = load_experiment_spec(project_root, "E07")
    vendor_critical = 0.012095

    for input_spec, ratio in zip(spec.inputs, EXPECTED_VENDOR_RATIOS):
        csv_path, metadata_path = input_spec.resolve(project_root)
        assert csv_path.parent == (
            project_root
            / "experiments"
            / "E07_position_only_stop_and_go"
            / "inputs"
        )
        trajectory = load_trajectory_csv(
            csv_path,
            metadata_path=metadata_path,
            require_metadata=True,
        )
        metadata = load_trajectory_metadata(metadata_path or csv_path)
        expected_velocity = ratio * vendor_critical
        assert trajectory.sample_count == 301
        assert trajectory.dt == pytest.approx(0.01)
        assert trajectory.duration_s == pytest.approx(3.0)
        assert trajectory.velocity_rad_s is not None
        assert trajectory.acceleration_rad_s2 is not None
        assert trajectory.jerk_rad_s3 is not None
        assert trajectory.velocity_rad_s == pytest.approx(expected_velocity)
        assert trajectory.acceleration_rad_s2 == pytest.approx(0.0)
        assert trajectory.jerk_rad_s3 == pytest.approx(0.0)
        assert trajectory.position_rad == pytest.approx(
            trajectory.time_s * expected_velocity
        )
        assert metadata.generator_id == "e07_constant_velocity"
        assert metadata.generator_params[
            "vendor_velocity_ratio"
        ] == pytest.approx(ratio)


def test_e07_threshold_separates_pulse_and_continuous_regions() -> None:
    project_root = Path(__file__).parents[1]
    below_run, below = _metric_table(
        project_root,
        _input_id(0.95),
        "p_limit_s1",
    )
    above_run, above = _metric_table(
        project_root,
        _input_id(1.05),
        "p_limit_s1",
    )

    assert below.value(
        "rest_to_rest_pulse_fraction",
        window_id="main_evaluation",
    ) >= 0.95
    assert above.value(
        "rest_to_rest_pulse_fraction",
        window_id="main_evaluation",
    ) <= 0.05
    for run, table in ((below_run, below), (above_run, above)):
        assert run.status.completed
        assert all(
            abs(float(row["raw_target_velocity_rad_s"])) <= 1e-12
            and abs(float(row["raw_target_acceleration_rad_s2"])) <= 1e-12
            and row["requested_target_free_duration_s"] is not None
            and row["frozen_trajectory_duration_s"] is not None
            for row in run.trace_rows
        )
        assert table.value(
            "one_cycle_reachability_pulse_agreement",
            window_id="main_evaluation",
        ) >= 0.99
        assert table.value("profile_exact_fraction") == pytest.approx(1.0)
        assert table.value("profile_constraint_violation_count") == 0
        assert table.value("fallback_rate") == 0
        assert table.value("solver_failure_count") == 0


@pytest.mark.parametrize(
    ("vendor_ratio", "expected_ripple"),
    (
        (0.5, 2.0),
        (2.0, 1.0),
        (4.0, 0.5),
    ),
)
def test_e07_exact_velocity_ripple_severity_probes(
    vendor_ratio: float,
    expected_ripple: float,
) -> None:
    project_root = Path(__file__).parents[1]
    run, table = _metric_table(
        project_root,
        _input_id(vendor_ratio),
        "p_limit_s1",
    )

    assert run.status.completed
    assert table.value(
        "profile_velocity_ripple_to_reference_median",
        window_id="main_evaluation",
    ) == pytest.approx(expected_ripple, abs=0.1)
    assert table.value(
        "profile_velocity_ripple_to_reference_p95",
        window_id="main_evaluation",
    ) >= expected_ripple - 0.1
    assert table.value(
        "profile_velocity_ripple_median",
        window_id="main_evaluation",
    ) > 0.0
    if vendor_ratio == 0.5:
        assert table.value(
            "stop_go_event_rate_hz",
            window_id="main_evaluation",
        ) == pytest.approx(100.0)
