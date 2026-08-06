from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments/E20_pv_future_o1_acceleration_conditioning/experiment.py"
)
SPEC = importlib.util.spec_from_file_location("_e20_experiment_test", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
e20 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e20
SPEC.loader.exec_module(e20)


def _synthetic_events() -> list[dict[str, object]]:
    return [
        {
            "source_index": index,
            "source_elapsed_time_s": index * 0.01,
            "source_position_rad": position,
            "target_position_rad": position,
            "target_velocity_rad_s": velocity,
            "target_acceleration_rad_s2": 0.0,
            "prediction_startup": index < 2,
        }
        for index, (position, velocity) in enumerate(
            ((0.0, 0.0), (0.01, 2.0), (0.02, -2.0), (0.03, 1.0))
        )
    ]


def test_acceleration_conditioning_constructs_a_strictly_compliant_curve() -> None:
    result = e20.condition_future_o1_acceleration(_synthetic_events())
    position = np.asarray(
        [event["target_position_rad"] for event in result.events], dtype=float
    )
    velocity = np.asarray(
        [event["target_velocity_rad_s"] for event in result.events], dtype=float
    )
    elapsed = np.asarray(
        [event["source_elapsed_time_s"] for event in result.events], dtype=float
    )
    dt = np.diff(elapsed)
    acceleration = np.diff(velocity) / dt

    assert result.audit["conditioning_stage"] == "offline_before_replay"
    assert result.audit["runtime_projection_or_governor"] is False
    assert result.audit["raw_acceleration_violation_count"] == 3
    assert result.audit["projected_acceleration_violation_count"] == 0
    assert result.audit["strict_acceleration_compliance"]
    assert np.all(np.abs(acceleration) <= e20.e18.MAX_ACCELERATION_RAD_S2)
    assert np.all(np.abs(velocity) <= e20.e18.MAX_VELOCITY_RAD_S)
    np.testing.assert_allclose(
        np.diff(position),
        0.5 * (velocity[:-1] + velocity[1:]) * dt,
        atol=e20.PROJECTION_DYNAMICS_TOLERANCE,
        rtol=0.0,
    )
    assert position[0] == 0.0
    assert velocity[0] == 0.0
    assert all(event["target_acceleration_rad_s2"] == 0.0 for event in result.events)


@pytest.mark.parametrize(
    "keyword,value",
    (
        ("max_velocity_rad_s", 0.0),
        ("max_velocity_rad_s", float("nan")),
        ("max_acceleration_rad_s2", -1.0),
        ("max_acceleration_rad_s2", float("inf")),
    ),
)
def test_acceleration_conditioning_rejects_invalid_limits(
    keyword: str,
    value: float,
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        e20.condition_future_o1_acceleration(
            _synthetic_events(),
            **{keyword: value},
        )


def test_recorded_e18_conditioning_removes_every_target_acceleration_violation() -> None:
    data = e20.e18.load_none_snapshot(ROOT / e20.e18.RAW_INPUT_PATH)
    original = e20.e18.build_future_o1_target_events(data)

    result = e20.condition_future_o1_acceleration(original)

    assert result.audit["event_count"] == 1620
    assert result.audit["interval_count"] == 1619
    assert result.audit["raw_acceleration_violation_count"] == 16
    assert result.audit["raw_min_acceleration_rad_s2"] == pytest.approx(
        -176.78992682021388
    )
    assert result.audit["raw_max_acceleration_rad_s2"] == pytest.approx(
        132.8234554662319
    )
    assert result.audit["projected_acceleration_violation_count"] == 0
    assert result.audit["projected_velocity_violation_count"] == 0
    assert result.audit["projected_max_abs_acceleration_rad_s2"] < 16.2
    assert result.audit["maximum_dynamics_residual"] < 1e-12
    assert result.audit["strict_acceleration_compliance"]


def test_e20_reports_large_improvement_without_claiming_elimination(
    tmp_path: Path,
) -> None:
    result = e20.run_acceleration_conditioning(
        project_root=ROOT,
        runs_root=tmp_path / "runs",
        create_figures=True,
    )

    assert result.success
    summary = json.loads(
        (result.run_directory / "summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (result.run_directory / "manifest.json").read_text(encoding="utf-8")
    )
    audit = json.loads(
        (result.run_directory / "acceleration_projection_summary.json").read_text(
            encoding="utf-8"
        )
    )
    with (result.run_directory / "method_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        metrics = {row["method_id"]: row for row in csv.DictReader(handle)}

    raw_target = metrics[e20.RAW_TARGET_METHOD_ID]
    conditioned_target = metrics[e20.ACCEL_PROJECTED_METHOD_ID]
    assert float(raw_target["focal_max_drawdown_mrad"]) > 8.0
    assert 0.9 < float(conditioned_target["focal_max_drawdown_mrad"]) < 1.0
    assert float(conditioned_target["rising_max_drawdown_mrad"]) == pytest.approx(
        float(conditioned_target["focal_max_drawdown_mrad"])
    )
    assert summary["scientific_result"] == "improved_not_eliminated"
    assert summary["engineering_result"] == "improved_not_eliminated"
    assert summary["focal_drawdown_change"]["relative_reduction_fraction"] > 0.88
    assert summary["strict_target_conditioning_passed"]
    assert summary["all_output_constraint_audits_passed"]
    assert audit["raw_acceleration_violation_count"] == 16
    assert audit["projected_acceleration_violation_count"] == 0
    assert manifest["status"] == "completed"
    assert (
        manifest["methods"][e20.ACCEL_PROJECTED_METHOD_ID]["runtime_projection"]
        is False
    )
    assert len(
        (result.run_directory / "acceleration_projection_audit.csv")
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 1620
    assert len(
        (result.run_directory / "acceleration_projected_target_events.csv")
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 1621
    for figure_name in (
        "target_conditioning_rising_episode",
        "target_and_output_comparison",
        "target_recorded_replay_comparison",
        "dip_position_comparison",
    ):
        assert (result.run_directory / f"figures/{figure_name}.png").is_file()
        assert (result.run_directory / f"figures/{figure_name}.svg").is_file()
