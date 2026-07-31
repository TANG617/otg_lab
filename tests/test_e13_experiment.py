from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from otg_lab.analysis import MetricSet, analyze_tracking
from otg_lab.cli import load_experiment_spec
from otg_lab.csvio import load_trajectory_csv
from otg_lab.tracking import run_tracking

ROOT = Path(__file__).resolve().parents[1]
PV_METHOD_IDS = (
    "pv_est_backward_o1_k",
    "pv_est_backward_o2_k",
    "pv_est_centered_o2_km1",
    "pv_pred_backward_o1_kp1",
    "pv_pred_backward_o2_kp1",
)
PVA_METHOD_IDS = tuple(method_id.replace("pv_", "pva_") for method_id in PV_METHOD_IDS)


def _case_id(method_id: str, scale: float) -> str:
    return f"{method_id}__limit_s{f'{scale:g}'.replace('.', 'p')}"


def test_e13_declares_joint_p_pv_pva_matrix() -> None:
    spec = load_experiment_spec(ROOT, "E13")

    assert spec.directory_name == "E13_pv_pva_stop_and_go"
    assert len(spec.inputs) == 20
    assert len(spec.methods) == 12
    assert len(spec.cases) == 48
    assert len(spec.inputs) * len(spec.cases) == 960
    assert tuple(method.method_id for method in spec.methods) == (
        "position_zoh_p_ruckig",
        "p_kp1_baseline",
        *PV_METHOD_IDS,
        *PVA_METHOD_IDS,
    )
    assert len(spec.comparison_spec.pairs) == 100
    assert {float(case.factors["limit_scale"]) for case in spec.cases} == {
        0.25,
        0.5,
        1.0,
        2.0,
    }
    assert spec.artifact_writer is not None


def test_e13_pv_and_pva_suppress_p_only_pulses_and_match_when_a_is_zero() -> None:
    spec = load_experiment_spec(ROOT, "E13")
    input_id = "e07_cv_vendor_ratio_0p5"
    input_spec = next(item for item in spec.inputs if item.input_id == input_id)
    csv_path, metadata_path = input_spec.resolve(ROOT)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    cases = {case.case_id: case for case in spec.cases}
    selected = (
        "position_zoh_p_ruckig",
        "pv_pred_backward_o1_kp1",
        "pva_pred_backward_o1_kp1",
    )
    runs = {}
    metrics = {}
    for method_id in selected:
        case = cases[_case_id(method_id, 1.0)]
        run = run_tracking(reference, spec.method_for_case(case), case.run_config)
        runs[method_id] = run
        table = analyze_tracking(
            reference,
            run,
            MetricSet(
                metric_ids=(
                    "rest_to_rest_pulse_fraction",
                    "stop_go_event_rate_hz",
                    "profile_constraint_violation_count",
                ),
                windows=spec.windows,
                input_id=input_id,
                limits=case.run_config.limits,
            ),
        )
        metrics[method_id] = table
        assert run.status.completed
        assert table.value(
            "profile_constraint_violation_count",
            window_id="full_overlap",
        ) == 0

    assert metrics["position_zoh_p_ruckig"].value(
        "rest_to_rest_pulse_fraction",
        window_id="main_evaluation",
    ) >= 0.95
    for method_id in selected[1:]:
        assert metrics[method_id].value(
            "rest_to_rest_pulse_fraction",
            window_id="main_evaluation",
        ) == pytest.approx(0.0)
        assert metrics[method_id].value(
            "stop_go_event_rate_hz",
            window_id="main_evaluation",
        ) == pytest.approx(0.0)

    mature = runs["pv_pred_backward_o1_kp1"].command.time_s >= 0.5 - 1e-12
    np.testing.assert_allclose(
        runs["pv_pred_backward_o1_kp1"].command.position_rad[mature],
        runs["pva_pred_backward_o1_kp1"].command.position_rad[mature],
        rtol=0.0,
        atol=1e-12,
    )
