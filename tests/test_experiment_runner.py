from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from otg_lab.analysis import (
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
    MetricSet,
    analyze_tracking,
)
from otg_lab.cli import main
from otg_lab.csvio import load_trajectory_csv
from otg_lab.experiment import (
    ExperimentCase,
    ExperimentInput,
    ExperimentSpec,
    FactorHeatmapSpec,
    InputGate,
    load_tracking_run_artifacts,
    run_experiment,
)
from otg_lab.generators import write_analytic_trajectory_csv
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
)


def _method(method_id: str, follower: str = "ruckig") -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id=method_id,
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec(follower),
    )


def _spec(csv_path: Path) -> ExperimentSpec:
    baseline = _method("baseline_ruckig")
    candidate = TrackingMethodSpec(
        method_id="candidate_direct",
        estimator=ComponentSpec(
            "local_poly",
            {"window": 5, "degree": 3, "lag_samples": 0},
        ),
        predictor=ComponentSpec("constant_jerk"),
        target_builder=ComponentSpec("pva"),
        governor=ComponentSpec("one_step"),
        follower=ComponentSpec("direct"),
    )
    return ExperimentSpec(
        experiment_id="E99",
        slug="integration",
        title="E99 integration",
        question="Does the durable experiment artifact chain work?",
        hypothesis="Both required methods complete with reloadable artifacts.",
        independent_variables=("method",),
        controlled_variables={"dt_s": 0.01, "measurement": "position_only"},
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
            "governor",
            "follower",
        ),
        inputs=(ExperimentInput("tiny_sine", csv_path),),
        methods=(baseline, candidate),
        run_config=RunConfig(
            limits=MotionLimits(4.1, 8.2, 4000.0),
            dt_s=0.01,
            minimum_duration_s=0.01,
            prediction_horizon_s=0.01,
        ),
        metric_roles={
            "primary": ("position_rmse",),
            "secondary": ("position_mae",),
            "guardrail": ("profile_constraint_violation_count",),
            "diagnostic": (
                "runtime_total_p95_s",
                "solver_failure_count",
                "reset_count",
            ),
        },
        windows=(EvaluationWindow("full_overlap"),),
        comparison_spec=ComparisonSpec(
            pairs=(MethodPair("baseline_ruckig", "candidate_direct"),),
            metric_ids=(
                "position_rmse",
                "profile_constraint_violation_count",
            ),
            input_ids=("tiny_sine",),
            window_ids=("full_overlap",),
        ),
        input_gate=InputGate(False),
    )


def _factorial_spec(csv_path: Path) -> ExperimentSpec:
    method = _method("position_ruckig")
    levels_a = (4.1, 8.2)
    levels_j = (1000.0, 4000.0)
    cases = tuple(
        ExperimentCase(
            case_id=f"a{str(acceleration).replace('.', 'p')}_j{int(jerk)}",
            method_id=method.method_id,
            run_config=RunConfig(
                limits=MotionLimits(4.1, acceleration, jerk),
                dt_s=0.01,
                minimum_duration_s=0.01,
                prediction_horizon_s=0.01,
            ),
            factors={
                "max_acceleration_rad_s2": acceleration,
                "max_jerk_rad_s3": jerk,
            },
        )
        for acceleration in levels_a
        for jerk in levels_j
    )
    baseline = "a8p2_j4000"
    return ExperimentSpec(
        experiment_id="E98",
        slug="factorial",
        title="E98 factorial",
        question="Does each factor case use its declared run configuration?",
        hypothesis="All four cases complete with auditable factor metrics.",
        independent_variables=("acceleration", "jerk"),
        controlled_variables={"dt_s": 0.01, "max_velocity_rad_s": 4.1},
        allowed_method_differences=(
            "run_config.limits.max_acceleration_rad_s2",
            "run_config.limits.max_jerk_rad_s3",
        ),
        inputs=(ExperimentInput("tiny_sine", csv_path),),
        methods=(method,),
        run_config=next(
            case.run_config for case in cases if case.case_id == baseline
        ),
        metric_roles={
            "primary": ("position_rmse",),
            "secondary": ("position_mae",),
            "guardrail": ("profile_constraint_violation_count",),
            "diagnostic": ("fallback_rate", "lag_s"),
        },
        windows=(EvaluationWindow("full_overlap"),),
        comparison_spec=ComparisonSpec(
            pairs=tuple(
                MethodPair(baseline, case.case_id)
                for case in cases
                if case.case_id != baseline
            ),
            metric_ids=("position_rmse",),
            input_ids=("tiny_sine",),
            window_ids=("full_overlap",),
        ),
        cases=cases,
        factor_heatmaps=(
            FactorHeatmapSpec(
                figure_id="factor_rmse",
                input_id="tiny_sine",
                metric_id="position_rmse",
                window_id="full_overlap",
                row_factor="max_acceleration_rad_s2",
                row_levels=levels_a,
                column_factor="max_jerk_rad_s3",
                column_levels=levels_j,
                baseline_case_id=baseline,
                title="Factor RMSE",
                subtitle="Each cell is relative to the baseline",
                row_label="Acceleration",
                column_label="Jerk",
            ),
            FactorHeatmapSpec(
                figure_id="factor_lag_ms",
                input_id="tiny_sine",
                metric_id="lag_s",
                window_id="full_overlap",
                row_factor="max_acceleration_rad_s2",
                row_levels=levels_a,
                column_factor="max_jerk_rad_s3",
                column_levels=levels_j,
                baseline_case_id=baseline,
                title="Factor lag",
                subtitle="Each cell is case minus baseline in milliseconds",
                row_label="Acceleration",
                column_label="Jerk",
                comparison_mode="difference",
                display_multiplier=1000.0,
                colorbar_label="Lag Δ vs baseline [ms]",
            ),
        ),
    )


def _raise_component_error():
    raise RuntimeError("intentional construction failure")


def test_experiment_outputs_reload_and_recompute(tmp_path: Path) -> None:
    input_path = tmp_path / "tiny_sine.csv"
    write_analytic_trajectory_csv(
        input_path,
        "sine",
        {"dt_s": 0.01, "duration_s": 0.3, "settle_duration_s": 0.1},
        trajectory_id="tiny_sine",
    )
    result = run_experiment(
        _spec(input_path),
        project_root=tmp_path,
        runs_root=tmp_path / "runs",
        create_figures=False,
    )

    assert result.success
    run_directory = result.run_directory
    manifest = json.loads((run_directory / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["required_failure_count"] == 0
    assert (run_directory / "analysis/report.md").is_file()
    assert (run_directory / "analysis/comparisons.csv").is_file()
    assert (run_directory / "analysis/failures.csv").read_text().count("\n") == 1

    reference = load_trajectory_csv(
        run_directory / "inputs/tiny_sine/reference.csv",
        require_metadata=True,
    )
    artifact_directory = (
        run_directory / "methods/baseline_ruckig/tiny_sine"
    )
    loaded_run = load_tracking_run_artifacts(artifact_directory)
    assert loaded_run.status.completed
    assert loaded_run.command is not None
    assert loaded_run.command.sample_count == reference.sample_count - 1
    assert all(
        row["requested_target_free_duration_s"] is not None
        and row["frozen_trajectory_duration_s"] is not None
        for row in loaded_run.trace_rows
    )

    recomputed = analyze_tracking(
        reference,
        loaded_run,
        MetricSet(
            metric_ids=(
                "position_rmse",
                "profile_constraint_violation_count",
            ),
            input_id="tiny_sine",
            limits=MotionLimits(4.1, 8.2, 4000.0),
        ),
    )
    assert recomputed.value("profile_constraint_violation_count") == 0
    with (run_directory / "analysis/trajectory_metrics.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    persisted_rmse = next(
        float(row["value"])
        for row in rows
        if row["input_id"] == "tiny_sine"
        and row["method_id"] == "baseline_ruckig"
        and row["metric_id"] == "position_rmse"
    )
    assert recomputed.value("position_rmse") == pytest.approx(persisted_rmse)
    solver_failures = next(
        float(row["value"])
        for row in rows
        if row["input_id"] == "tiny_sine"
        and row["method_id"] == "baseline_ruckig"
        and row["metric_id"] == "solver_failure_count"
    )
    reset_count = next(
        float(row["value"])
        for row in rows
        if row["input_id"] == "tiny_sine"
        and row["method_id"] == "baseline_ruckig"
        and row["metric_id"] == "reset_count"
    )
    assert solver_failures == 0
    assert reset_count == 0


def test_default_run_directory_is_local_to_experiment(tmp_path: Path) -> None:
    input_path = tmp_path / "tiny_sine.csv"
    write_analytic_trajectory_csv(
        input_path,
        "sine",
        {"dt_s": 0.01, "duration_s": 0.1, "settle_duration_s": 0.0},
        trajectory_id="tiny_sine",
    )

    result = run_experiment(
        _spec(input_path),
        project_root=tmp_path,
        create_figures=False,
    )

    expected_parent = tmp_path / "experiments/E99_integration/runs"
    assert result.run_directory.parent == expected_parent
    assert result.run_directory.name.endswith(f"__{result.spec_hash[:12]}")


def test_optional_method_failure_keeps_empty_command_and_incomplete_pair(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tiny_sine.csv"
    write_analytic_trajectory_csv(
        input_path,
        "sine",
        {"dt_s": 0.01, "duration_s": 0.1, "settle_duration_s": 0.0},
        trajectory_id="tiny_sine",
    )
    base = _spec(input_path)
    failing = TrackingMethodSpec(
        method_id="candidate_direct",
        estimator=ComponentSpec(
            "intentional_failure",
            factory=_raise_component_error,
        ),
        predictor=ComponentSpec("constant_jerk"),
        target_builder=ComponentSpec("pva"),
        governor=ComponentSpec("one_step"),
        follower=ComponentSpec("direct"),
        required=False,
    )
    result = run_experiment(
        replace(base, methods=(base.methods[0], failing)),
        project_root=tmp_path,
        runs_root=tmp_path / "runs",
        create_figures=False,
    )

    assert result.success
    assert result.failure_count == 1
    failed_directory = (
        result.run_directory / "methods/candidate_direct/tiny_sine"
    )
    failed_run = load_tracking_run_artifacts(failed_directory)
    assert not failed_run.status.completed
    assert failed_run.command is not None
    assert failed_run.command.sample_count == 0
    with (result.run_directory / "analysis/comparisons.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        comparisons = list(csv.DictReader(handle))
    assert comparisons
    assert all(
        row["status"] == "unavailable_incomplete_pair"
        for row in comparisons
    )


def test_ablation_validation_rejects_undeclared_difference(
    tmp_path: Path,
) -> None:
    base = _spec(tmp_path / "unused.csv")
    with pytest.raises(ValueError, match="outside declared variable paths"):
        ExperimentSpec(
            **{
                **base.__dict__,
                "allowed_method_differences": ("estimator",),
            }
        )


def test_factorial_cases_use_independent_configs_and_render_heatmap(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tiny_sine.csv"
    write_analytic_trajectory_csv(
        input_path,
        "sine",
        {"dt_s": 0.01, "duration_s": 0.12, "settle_duration_s": 0.0},
        trajectory_id="tiny_sine",
    )
    result = run_experiment(
        _factorial_spec(input_path),
        project_root=tmp_path,
        runs_root=tmp_path / "runs",
        create_figures=True,
    )

    assert result.success
    run_directory = result.run_directory
    manifest = json.loads((run_directory / "manifest.json").read_text())
    assert len(manifest["methods"]) == 4
    assert (
        manifest["methods"]["a4p1_j1000"]["run_config"]["limits"][
            "max_acceleration_rad_s2"
        ]
        == 4.1
    )
    assert (
        manifest["methods"]["a8p2_j4000"]["run_config"]["limits"][
            "max_jerk_rad_s3"
        ]
        == 4000.0
    )
    fingerprints = {
        case["inputs"]["tiny_sine"]["fingerprint"]
        for case in manifest["methods"].values()
    }
    assert len(fingerprints) == 4

    with (run_directory / "analysis/factor_rmse.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4
    vendor = next(row for row in rows if row["case_id"] == "a8p2_j4000")
    assert float(vendor["rmse_ratio"]) == pytest.approx(1.0)
    assert vendor["status"] == "available"
    assert (run_directory / "analysis/figures/factor_rmse.png").is_file()
    assert (run_directory / "analysis/figures/factor_rmse.svg").is_file()

    with (run_directory / "analysis/factor_lag_ms.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        lag_rows = list(csv.DictReader(handle))
    assert len(lag_rows) == 4
    lag_vendor = next(
        row for row in lag_rows if row["case_id"] == "a8p2_j4000"
    )
    assert float(lag_vendor["lag_delta_ms"]) == pytest.approx(0.0)
    assert lag_vendor["comparison_mode"] == "difference"
    assert lag_vendor["status"] == "available"
    assert (run_directory / "analysis/figures/factor_lag_ms.png").is_file()
    assert (run_directory / "analysis/figures/factor_lag_ms.svg").is_file()


def test_factor_heatmap_validation_rejects_invalid_case_matrices(
    tmp_path: Path,
) -> None:
    base = _factorial_spec(tmp_path / "unused.csv")
    first = base.cases[0]

    with pytest.raises(ValueError, match="unknown method"):
        ExperimentSpec(
            **{
                **base.__dict__,
                "cases": (
                    replace(first, method_id="missing_method"),
                    *base.cases[1:],
                ),
            }
        )

    with pytest.raises(ValueError, match="duplicated"):
        ExperimentSpec(
            **{
                **base.__dict__,
                "cases": (
                    first,
                    replace(base.cases[1], factors=first.factors),
                    *base.cases[2:],
                ),
            }
        )

    with pytest.raises(ValueError, match="full grid"):
        ExperimentSpec(
            **{
                **base.__dict__,
                "cases": base.cases[1:],
            }
        )

    invalid_heatmap = replace(
        base.factor_heatmaps[0],
        baseline_case_id="missing_vendor",
    )
    with pytest.raises(ValueError, match="baseline"):
        ExperimentSpec(
            **{
                **base.__dict__,
                "factor_heatmaps": (invalid_heatmap,),
            }
        )

    with pytest.raises(ValueError, match="comparison_mode"):
        replace(base.factor_heatmaps[0], comparison_mode="percent")

    with pytest.raises(ValueError, match="display_multiplier"):
        replace(base.factor_heatmaps[0], display_multiplier=0.0)


def test_case_comparison_rejects_undeclared_run_config_difference(
    tmp_path: Path,
) -> None:
    base = _factorial_spec(tmp_path / "unused.csv")
    with pytest.raises(ValueError, match="outside declared variable paths"):
        ExperimentSpec(
            **{
                **base.__dict__,
                "allowed_method_differences": (
                    "run_config.limits.max_acceleration_rad_s2",
                ),
            }
        )


def test_new_experiment_writes_only_the_new_directory(
    tmp_path: Path,
) -> None:
    source_template = (
        Path(__file__).parents[1] / "experiments" / "_template"
    )
    target_template = tmp_path / "experiments" / "_template"
    target_template.mkdir(parents=True)
    for name in ("experiment.py", "README.md"):
        (target_template / name).write_text(
            (source_template / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    exit_code = main(
        [
            "--project-root",
            str(tmp_path),
            "new-experiment",
            "E02",
            "estimator_ablation",
        ]
    )
    assert exit_code == 0
    created = tmp_path / "experiments/E02_estimator_ablation"
    assert (created / "experiment.py").is_file()
    assert (created / "README.md").is_file()
    assert "__EXPERIMENT_ID__" not in (created / "experiment.py").read_text()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["experiments"]
