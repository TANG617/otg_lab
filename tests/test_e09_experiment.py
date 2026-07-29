from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from otg_lab.analysis import MetricSet, analyze_tracking
from otg_lab.cli import load_experiment_spec
from otg_lab.csvio import load_trajectory_csv
from otg_lab.tracking import run_tracking

ROOT = Path(__file__).resolve().parents[1]
P_ONLY_BASELINE_METHOD_ID = "position_zoh_p_ruckig"
FINITE_DIFFERENCE_METHOD_IDS = (
    "pva_est_backward_o1_k",
    "pva_est_backward_o2_k",
    "pva_est_centered_o2_km1",
    "pva_pred_backward_o1_kp1",
    "pva_pred_backward_o2_kp1",
)
METHOD_IDS = (P_ONLY_BASELINE_METHOD_ID, *FINITE_DIFFERENCE_METHOD_IDS)
TARGET_AGE_SAMPLES = {
    P_ONLY_BASELINE_METHOD_ID: 0.0,
    "pva_est_backward_o1_k": 1.0,
    "pva_est_backward_o2_k": 1.0,
    "pva_est_centered_o2_km1": 2.0,
    "pva_pred_backward_o1_kp1": 0.0,
    "pva_pred_backward_o2_kp1": 0.0,
}
LIMIT_SCALES = (0.25, 0.5, 1.0, 2.0)
DT_S = 0.01


def _case_id(method_id: str, limit_scale: float) -> str:
    token = f"{limit_scale:g}".replace(".", "p")
    return f"{method_id}__limit_s{token}"


def test_e09_declares_the_e04_methods_on_the_e07_matrix() -> None:
    spec = load_experiment_spec(ROOT, "E09")
    e04 = load_experiment_spec(ROOT, "E04")

    assert spec.directory_name == "E09_pva_finite_difference_stop_and_go"
    assert len(spec.inputs) == 20
    assert len(spec.methods) == 6
    assert len(spec.cases) == 24
    assert len(spec.inputs) * len(spec.cases) == 480
    assert tuple(method.method_id for method in spec.methods) == METHOD_IDS
    assert len(spec.comparison_spec.pairs) == 20
    assert {pair.baseline_method_id for pair in spec.comparison_spec.pairs} == {
        _case_id(P_ONLY_BASELINE_METHOD_ID, scale) for scale in LIMIT_SCALES
    }
    assert {pair.candidate_method_id for pair in spec.comparison_spec.pairs} == {
        _case_id(method_id, scale)
        for method_id in FINITE_DIFFERENCE_METHOD_IDS
        for scale in LIMIT_SCALES
    }
    assert spec.artifact_writer is not None

    e04_methods = {method.method_id: method for method in e04.methods}
    p_only = next(
        method
        for method in spec.methods
        if method.method_id == P_ONLY_BASELINE_METHOD_ID
    )
    assert (
        p_only.estimator.component_id,
        p_only.predictor.component_id,
        p_only.target_builder.component_id,
        p_only.governor.component_id,
        p_only.follower.component_id,
    ) == ("position_only", "zero_order_hold", "p", "none", "ruckig")

    for method in spec.methods:
        assert method.required
        if method.method_id == P_ONLY_BASELINE_METHOD_ID:
            continue
        assert method.method_id != "p_kp1_baseline"
        assert method.method_id != "pva_truth_kp1"
        e04_method = e04_methods[method.method_id]
        for component_name in (
            "estimator",
            "predictor",
            "target_builder",
            "governor",
            "follower",
        ):
            assert (
                getattr(method, component_name).as_dict()
                == getattr(e04_method, component_name).as_dict()
            )

    assert all(
        item.csv_path.parent == Path("experiments/E07_position_only_stop_and_go/inputs")
        for item in spec.inputs
    )
    assert {float(case.factors["limit_scale"]) for case in spec.cases} == set(
        LIMIT_SCALES
    )
    assert {float(case.factors["target_age_samples"]) for case in spec.cases} == {
        0.0,
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

    main = next(
        window for window in spec.windows if window.window_id == "main_evaluation"
    )
    assert main.start_time_s == pytest.approx(0.5)
    assert main.end_time_s == pytest.approx(2.5)
    assert "rest_to_rest_pulse_fraction" in spec.metric_roles["primary"]
    assert (
        "profile_velocity_ripple_to_reference_median" in spec.metric_roles["secondary"]
    )


def test_e09_constant_velocity_probes_eliminate_stop_and_go() -> None:
    spec = load_experiment_spec(ROOT, "E09")
    input_id = "e07_cv_vendor_ratio_0p5"
    input_spec = next(item for item in spec.inputs if item.input_id == input_id)
    csv_path, metadata_path = input_spec.resolve(ROOT)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    reference_velocity = float(reference.velocity_rad_s[0])
    metric_ids = (
        "rest_to_rest_pulse_fraction",
        "stop_go_event_rate_hz",
        "endpoint_stop_fraction",
        "profile_velocity_ripple_to_reference_median",
        "profile_velocity_ripple_to_reference_p95",
        "position_rmse",
        "lag_s",
        "profile_exact_fraction",
        "profile_constraint_violation_count",
        "fallback_rate",
        "solver_failure_count",
    )
    commands: dict[str, np.ndarray] = {}

    for method_id in FINITE_DIFFERENCE_METHOD_IDS:
        case = next(
            item for item in spec.cases if item.case_id == _case_id(method_id, 1.0)
        )
        run = run_tracking(
            reference,
            spec.method_for_case(case),
            case.run_config,
        )
        assert run.status.completed
        mature_mask = run.command.time_s >= 0.5 - 1e-12
        commands[method_id] = np.array(
            run.command.position_rad[mature_mask],
            copy=True,
        )

        table = analyze_tracking(
            reference,
            run,
            MetricSet(
                metric_ids=metric_ids,
                windows=spec.windows,
                input_id=input_id,
                limits=case.run_config.limits,
            ),
        )
        assert table.value(
            "rest_to_rest_pulse_fraction",
            window_id="main_evaluation",
        ) == pytest.approx(0.0)
        assert table.value(
            "stop_go_event_rate_hz",
            window_id="main_evaluation",
        ) == pytest.approx(0.0)
        assert table.value(
            "endpoint_stop_fraction",
            window_id="main_evaluation",
        ) == pytest.approx(0.0)
        assert (
            table.value(
                "profile_velocity_ripple_to_reference_median",
                window_id="main_evaluation",
            )
            <= 1e-9
        )
        assert (
            table.value(
                "profile_velocity_ripple_to_reference_p95",
                window_id="main_evaluation",
            )
            <= 1e-9
        )
        assert table.value(
            "profile_exact_fraction",
            window_id="full_overlap",
        ) == pytest.approx(1.0)
        assert (
            table.value(
                "profile_constraint_violation_count",
                window_id="full_overlap",
            )
            == 0
        )
        assert table.value(
            "fallback_rate",
            window_id="full_overlap",
        ) == pytest.approx(0.0)
        assert (
            table.value(
                "solver_failure_count",
                window_id="full_overlap",
            )
            == 0
        )

        main_rows = [
            row
            for row in run.trace_rows
            if 0.5 - 1e-12 <= float(row["command_time_s"]) <= 2.5 + 1e-12
        ]
        assert main_rows
        assert all(
            abs(float(row["raw_target_velocity_rad_s"]) - reference_velocity) <= 1e-9
            and abs(float(row["raw_target_acceleration_rad_s2"])) <= 1e-7
            and float(row["raw_target_age_samples"])
            == pytest.approx(TARGET_AGE_SAMPLES[method_id], abs=1e-9)
            and row["raw_target_causal"] is True
            and row["raw_target_startup"] is False
            for row in main_rows
        )
        assert table.value(
            "lag_s",
            window_id="main_evaluation",
        ) == pytest.approx(
            TARGET_AGE_SAMPLES[method_id] * DT_S,
            abs=1e-12,
        )

    np.testing.assert_allclose(
        commands["pva_est_backward_o1_k"],
        commands["pva_est_backward_o2_k"],
        rtol=0.0,
        atol=1e-9,
    )
    np.testing.assert_allclose(
        commands["pva_pred_backward_o1_kp1"],
        commands["pva_pred_backward_o2_kp1"],
        rtol=0.0,
        atol=5e-9,
    )

    baseline_case = next(
        item
        for item in spec.cases
        if item.case_id == _case_id(P_ONLY_BASELINE_METHOD_ID, 1.0)
    )
    baseline_run = run_tracking(
        reference,
        spec.method_for_case(baseline_case),
        baseline_case.run_config,
    )
    assert baseline_run.status.completed
    baseline_table = analyze_tracking(
        reference,
        baseline_run,
        MetricSet(
            metric_ids=metric_ids,
            windows=spec.windows,
            input_id=input_id,
            limits=baseline_case.run_config.limits,
        ),
    )
    assert baseline_table.value(
        "rest_to_rest_pulse_fraction",
        window_id="main_evaluation",
    ) == pytest.approx(1.0)
    assert baseline_table.value(
        "stop_go_event_rate_hz",
        window_id="main_evaluation",
    ) == pytest.approx(100.0)
    assert baseline_table.value(
        "endpoint_stop_fraction",
        window_id="main_evaluation",
    ) == pytest.approx(1.0)
    baseline_main_rows = [
        row
        for row in baseline_run.trace_rows
        if 0.5 - 1e-12 <= float(row["command_time_s"]) <= 2.5 + 1e-12
    ]
    assert baseline_main_rows
    assert all(
        abs(float(row["raw_target_velocity_rad_s"])) <= 1e-12
        and abs(float(row["raw_target_acceleration_rad_s2"])) <= 1e-12
        and float(row["raw_target_age_samples"]) == pytest.approx(0.0, abs=1e-9)
        and row["raw_target_causal"] is True
        and row["raw_target_startup"] is False
        for row in baseline_main_rows
    )


def test_e09_declares_all_custom_artifacts() -> None:
    source = (
        ROOT / "experiments/E09_pva_finite_difference_stop_and_go/experiment.py"
    ).read_text(encoding="utf-8")
    for name in (
        "stop_go_surface.csv",
        "stop_go_method_comparison.csv",
        "acceptance_summary.md",
        "stop_go_phase_map",
        "e07_rho_response",
        "stop_go_subcycle_velocity",
        "stop_go_method_comparison",
        "stop_go_exact_velocity_comparison",
    ):
        assert name in source
