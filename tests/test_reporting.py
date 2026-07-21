from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import otg_lab.reporting as reporting
from otg_lab.artifacts import write_json
from otg_lab.figures import REQUIRED_FIGURE_CATEGORIES
from otg_lab.reporting import (
    FIGURE_TABLE_SCHEMAS,
    PRIMARY_METHOD_IDS,
    AcceptanceAnalysis,
    ReportingValidationError,
    aggregate_governor_acceptance,
    build_constraint_jerk_table,
    build_failure_analysis,
    build_fallback_summary,
    build_statistical_tables,
    csv_regression_criteria,
    expected_trajectory_ids,
    filter_primary_method_rows,
    generate_final_figures,
    select_acceleration_phase_condition,
    summarize_repeated_runtime,
    validate_figure_tables,
    validate_raw_bundles,
    validate_root_artifact_index,
    write_root_artifact_index,
)


def _split_manifest(count: int = 6) -> dict:
    return {
        "trajectories": [
            {
                "trajectory_id": f"trajectory-{index:03d}",
                "split": "test",
                "locked": True,
            }
            for index in range(count)
        ]
        + [
            {
                "trajectory_id": "validation-000",
                "split": "validation",
                "locked": False,
            }
        ]
    }


def _statistical_records(count: int = 6) -> list[dict]:
    rows = []
    for index in range(count):
        identity = {
            "trajectory_id": f"trajectory-{index:03d}",
            "split": "test",
            "scenario_id": "clean",
        }
        baseline = 1.0 + 0.1 * index
        rows.extend(
            [
                {
                    **identity,
                    "method": "predicted_p",
                    "position_rmse": baseline,
                },
                {
                    **identity,
                    "method": "one_step_governed_pva_direct",
                    "position_rmse": 0.8 * baseline,
                },
            ]
        )
    return rows


def test_expected_test_ids_are_exact_and_duplicates_fail():
    assert expected_trajectory_ids(_split_manifest(6), expected_count=6) == tuple(
        f"trajectory-{index:03d}" for index in range(6)
    )
    duplicated = _split_manifest(6)
    duplicated["trajectories"].append(dict(duplicated["trajectories"][0]))
    with pytest.raises(ReportingValidationError, match="duplicate trajectory"):
        expected_trajectory_ids(duplicated)


def test_acceleration_phase_never_mixes_current_and_next_cycle_conditions():
    rows = [
        {
            "target_time_mode": mode,
            "configured_horizon_ms": horizon,
            "r_j": r_j,
            "r_a": r_a,
            "pva_vs_pv_rmse_improvement": value,
        }
        for mode, horizon, value in (
            ("current", 0.0, -0.5),
            ("next_cycle", 10.0, 0.25),
        )
        for r_j in (0.2, 0.8)
        for r_a in (0.2, 0.8)
    ]
    selected = select_acceleration_phase_condition(rows)
    assert len(selected) == 4
    assert {row["pva_vs_pv_rmse_improvement"] for row in selected} == {0.25}
    duplicate = [*rows, dict(rows[-1])]
    with pytest.raises(ReportingValidationError, match="duplicate r_j/r_a"):
        select_acceleration_phase_condition(duplicate)


def test_primary_figures_exclude_estimator_rank_and_jerk_uses_metric_semantics():
    rows = [
        {
            "method": method,
            "sampled_output_max_sampled_jerk": 100.0 + index,
            "sampled_output_max_new_jerk": 200.0 + index,
            "sampled_output_max_internal_jerk": 300.0 + index,
            # Deliberately unavailable audit value: reporting must not use it.
            "max_sampled_jerk": None if "ruckig" in method else 999.0,
        }
        for index, method in enumerate(PRIMARY_METHOD_IDS)
    ]
    rows.append(
        {
            "method": "estimator_rank_2::secondary::one_step_governed_pva_direct",
            "sampled_output_max_sampled_jerk": 10_000.0,
            "sampled_output_max_new_jerk": 10_001.0,
            "sampled_output_max_internal_jerk": 10_002.0,
            "max_sampled_jerk": 10_003.0,
        }
    )
    primary = filter_primary_method_rows(rows, require_all=True)
    assert {row["method"] for row in primary} == set(PRIMARY_METHOD_IDS)
    jerk = build_constraint_jerk_table(rows)
    assert {row["method"] for row in jerk} == set(PRIMARY_METHOD_IDS)
    ruckig = [row for row in jerk if row["method"] == "one_step_governed_pva_ruckig"]
    assert ruckig == [
        {
            "method": "one_step_governed_pva_ruckig",
            "jerk_semantic": "sampled_output",
            "max_abs_jerk": 105.0,
        },
        {
            "method": "one_step_governed_pva_ruckig",
            "jerk_semantic": "direct_new_jerk",
            "max_abs_jerk": 205.0,
        },
        {
            "method": "one_step_governed_pva_ruckig",
            "jerk_semantic": "internal_profile",
            "max_abs_jerk": 305.0,
        },
    ]
    unavailable_new = [dict(row) for row in rows]
    for row in unavailable_new:
        if "ruckig" in str(row["method"]):
            row["sampled_output_max_new_jerk"] = None
    honest = build_constraint_jerk_table(unavailable_new)
    assert not any(
        row["method"] == "one_step_governed_pva_ruckig"
        and row["jerk_semantic"] == "direct_new_jerk"
        for row in honest
    )
    assert {
        row["jerk_semantic"]
        for row in honest
        if row["method"] == "one_step_governed_pva_ruckig"
    } == {"sampled_output", "internal_profile"}


def test_statistical_tables_use_10000_exact_trajectory_resamples():
    comparisons = [
        {
            "comparison_id": "primary",
            "metric": "position_rmse",
            "baseline_method": "predicted_p",
            "candidate_method": "one_step_governed_pva_direct",
            "secondary": False,
        }
    ]
    result = build_statistical_tables(
        _statistical_records(),
        _split_manifest(),
        comparisons=comparisons,
        ci_metrics=("position_rmse",),
        ci_methods=("predicted_p", "one_step_governed_pva_direct"),
        expected_test_count=6,
        seed=11,
    )
    assert result.expected_trajectory_ids == tuple(
        f"trajectory-{index:03d}" for index in range(6)
    )
    assert len(result.paired_comparisons) == 1
    paired = result.paired_comparisons[0]
    assert paired["resamples"] == 10_000
    assert paired["n_trajectories"] == 6
    assert paired["n_expected_trajectories"] == 6
    assert paired["n_excluded_trajectories"] == 0
    assert paired["relative_improvement"] == pytest.approx(0.2)
    assert len(result.confidence_intervals) == 2
    assert {row["n_trajectories"] for row in result.confidence_intervals} == {6}
    assert {row["status"] for row in result.completeness} == {"complete"}


def test_incomplete_pairs_are_rejected_or_explicitly_unavailable():
    records = _statistical_records()
    records = [
        row
        for row in records
        if not (
            row["method"] == "one_step_governed_pva_direct"
            and row["trajectory_id"] == "trajectory-005"
        )
    ]
    comparisons = [
        {
            "comparison_id": "primary",
            "metric": "position_rmse",
            "baseline_method": "predicted_p",
            "candidate_method": "one_step_governed_pva_direct",
            "secondary": False,
        }
    ]
    with pytest.raises(ReportingValidationError, match="denominator is incomplete"):
        build_statistical_tables(
            records,
            _split_manifest(),
            comparisons=comparisons,
            ci_metrics=("position_rmse",),
            ci_methods=("predicted_p", "one_step_governed_pva_direct"),
            expected_test_count=6,
        )
    report = build_statistical_tables(
        records,
        _split_manifest(),
        comparisons=comparisons,
        ci_metrics=("position_rmse",),
        ci_methods=("predicted_p", "one_step_governed_pva_direct"),
        expected_test_count=6,
        incomplete_policy="report",
    )
    assert report.paired_comparisons == []
    paired_status = [
        row
        for row in report.inference_status
        if row["analysis_kind"] == "paired_comparison"
    ]
    assert paired_status[0]["status"] == "unavailable_incomplete_predeclared_family"
    incomplete = [row for row in report.completeness if row["status"] == "incomplete"]
    assert incomplete[0]["missing_trajectory_count"] == 1
    assert json.loads(incomplete[0]["missing_trajectory_ids_json"]) == [
        "trajectory-005"
    ]


def _figure_tables() -> dict[str, list[dict]]:
    trajectory_metrics = [
        {
            "trajectory_id": f"trajectory-{index:02d}",
            "method": "locked-method",
            "position_rmse": value,
        }
        for index, value in enumerate((0.1, 0.4, 0.2, 0.3))
    ]
    trace_samples = []
    for trajectory in trajectory_metrics:
        for k in range(4):
            trace_samples.append(
                {
                    "trajectory_id": trajectory["trajectory_id"],
                    "joint_id": "joint-0",
                    "method_id": "locked-method",
                    "control_time": 0.01 * k,
                    "command_time": 0.01 * (k + 1),
                    "p_ref": float(k),
                    "command_p": float(k) + 0.1,
                }
            )
    return {
        "estimator": [
            {
                "method": "estimator-a",
                "estimator_p_rmse": 0.1,
                "posterior_lag_s": 0.01,
                "estimator_p99_us": 5.0,
            },
            {
                "method": "estimator-b",
                "estimator_p_rmse": 0.2,
                "posterior_lag_s": 0.0,
                "estimator_p99_us": 3.0,
            },
        ],
        "prediction": [
            {
                "method": "constant_velocity",
                "prediction_horizon_ms": 10.0,
                "prediction_p_rmse": 0.1,
            },
            {
                "method": "constant_velocity",
                "prediction_horizon_ms": 20.0,
                "prediction_p_rmse": 0.2,
            },
        ],
        "ablation": [
            {"method": "P", "position_rmse": 0.4},
            {"method": "PV", "position_rmse": 0.3},
            {"method": "PVA", "position_rmse": 0.2},
        ],
        "acceleration_phase": [
            {"r_j": r_j, "r_a": r_a, "pva_vs_pv_rmse_improvement": r_j - r_a}
            for r_j in (0.2, 0.8)
            for r_a in (0.2, 0.8)
        ],
        "governor": [
            {
                "method": "one-step",
                "governor_position_distortion_rmse": 0.1,
                "one_step_reachable_rate": 0.9,
            },
            {
                "method": "qp",
                "governor_position_distortion_rmse": 0.05,
                "one_step_reachable_rate": 1.0,
            },
        ],
        "follower": [
            {
                "trajectory_id": trajectory,
                "follower": follower,
                "position_rmse": value,
            }
            for trajectory, value in (("t0", 0.2), ("t1", 0.3))
            for follower in ("direct", "ruckig")
        ],
        "robustness": [
            {"scenario_id": scenario, "method": method, "position_rmse": value}
            for scenario, value in (("clean", 0.1), ("drop", 0.2))
            for method in ("baseline", "candidate")
        ],
        "sampling_rate": [
            {
                "sampling_rate_hz": rate,
                "method": "locked-method",
                "position_rmse": 1.0 / rate,
            }
            for rate in (50.0, 100.0, 200.0, 500.0)
        ],
        "constraints": [
            {
                "method": method,
                "jerk_semantic": semantic,
                "max_abs_jerk": value,
            }
            for method in ("direct", "ruckig")
            for semantic, value in (
                ("sampled_output", 10.0),
                ("direct_new_jerk", 11.0),
                ("internal_profile", 12.0),
            )
            if not (method == "ruckig" and semantic == "direct_new_jerk")
        ],
        "scalability": [
            {"dof": dof, "method": "locked-method", "total_p99_us": dof * 2.0}
            for dof in (1, 3, 6, 12)
        ],
        "plant": [
            {"plant": plant, "method": method, "position_rmse": value}
            for plant, value in (("ideal", 0.1), ("servo", 0.2))
            for method in ("previous_command", "measured")
        ],
        "runtime_samples": [
            {"method": method, "total_compute_us": value}
            for method in ("baseline", "candidate")
            for value in (1.0, 2.0, 3.0, 4.0)
        ],
        "paired": [
            {
                "comparison_id": "primary",
                "relative_improvement": 0.2,
                "relative_improvement_ci_low": 0.1,
                "relative_improvement_ci_high": 0.3,
            }
        ],
        "trajectory_metrics": trajectory_metrics,
        "trace_samples": trace_samples,
    }


def test_exact_figure_contract_and_full_generation_are_deterministic(tmp_path):
    tables = _figure_tables()
    validate_figure_tables(tables)
    first = generate_final_figures(
        tables, tmp_path / "first", ranking_method="locked-method"
    )
    second = generate_final_figures(
        {name: list(reversed(rows)) for name, rows in tables.items()},
        tmp_path / "second",
        ranking_method="locked-method",
    )
    assert tuple(first["categories"]) == REQUIRED_FIGURE_CATEGORIES
    assert tuple(second["categories"]) == REQUIRED_FIGURE_CATEGORIES
    for category in REQUIRED_FIGURE_CATEGORIES:
        for suffix in ("png", "svg"):
            first_bytes = (tmp_path / "first" / f"{category}.{suffix}").read_bytes()
            second_bytes = (tmp_path / "second" / f"{category}.{suffix}").read_bytes()
            assert (
                hashlib.sha256(first_bytes).digest()
                == hashlib.sha256(second_bytes).digest()
            )

    broken = {name: list(rows) for name, rows in tables.items()}
    broken["plant"] = [dict(broken["plant"][0], extra="not allowed")]
    with pytest.raises(ReportingValidationError, match="schema differs"):
        validate_figure_tables(broken)


def test_each_raw_bundle_is_independently_recomputed(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    calls = []
    commit = "a" * 40
    for name in ("one", "two"):
        bundle = raw / name
        bundle.mkdir(parents=True)
        write_json(
            bundle / "run.json",
            {
                "run_id": name,
                "git_commit": commit,
                "git_worktree_dirty": False,
                "command": ["python", "run.py", name],
            },
        )
        write_json(bundle / "artifact_index.json", {"run_id": name})
        write_json(bundle / "data_manifest.json", {"source": "test"})
        write_json(bundle / "split_manifest.json", _split_manifest())

    def fake_validate(path, **kwargs):
        calls.append((Path(path).name, kwargs))
        return {
            "run_id": Path(path).name,
            "git_commit": commit,
            "artifact_count": 9,
            "checksums_verified": 8,
            "recomputation_verified": True,
        }

    monkeypatch.setattr("otg_lab.reporting.validate_artifact_bundle", fake_validate)
    bundles = validate_raw_bundles(raw, required_bundles=("one", "two"))
    assert tuple(bundles) == ("one", "two")
    assert [name for name, _ in calls] == ["one", "two"]
    assert all(kwargs["verify_recomputation"] is True for _, kwargs in calls)
    assert all(
        kwargs["recompute_arguments"]["motion_limits"]["max_jerk"] == 4000.0
        for _, kwargs in calls
    )


def test_root_index_hashes_bounded_artifacts_and_raw_roots(tmp_path):
    root = tmp_path / "final"
    (root / "summaries").mkdir(parents=True)
    for name in ("statistics", "figures", "manifests"):
        (root / name).mkdir()
    table = root / "summaries" / "summary.csv"
    table.write_text("metric,value\nrmse,0.1\n", encoding="utf-8")
    readme = root / "README.md"
    readme.write_text("# Technical index\n", encoding="utf-8")
    protocol_hash = root / "protocol_hash.txt"
    protocol_hash.write_text(
        f"{'c' * 64}  EXPERIMENT_PROTOCOL.md\n",
        encoding="utf-8",
    )
    raw_roots = [
        {
            "bundle": "locked_test",
            "uri": "raw_runs/locked_test/artifact_index.json",
            "run_id": "locked",
            "git_commit": "a" * 40,
            "artifact_index_sha256": "b" * 64,
            "artifact_index_bytes": 100,
            "generation_command": ["python", "run_paper_evidence.py", "locked-test"],
        }
    ]
    index, sidecar = write_root_artifact_index(
        root,
        [table, readme, protocol_hash],
        git_commit="a" * 40,
        reporting_git_commit="d" * 40,
        raw_bundle_roots=raw_roots,
        generation_command=["python", "run_paper_evidence.py", "report"],
    )
    payload = json.loads(index.read_text(encoding="utf-8"))
    assert payload["raw_run_git_commit"] == "a" * 40
    assert payload["reporting_git_commit"] == "d" * 40
    assert [row["path"] for row in payload["artifacts"]] == [
        "README.md",
        "protocol_hash.txt",
        "summaries/summary.csv",
    ]
    artifacts = {row["path"]: row for row in payload["artifacts"]}
    assert artifacts["summaries/summary.csv"]["sha256"] == hashlib.sha256(
        table.read_bytes()
    ).hexdigest()
    assert artifacts["protocol_hash.txt"]["role"] == "protocol_hash"
    assert artifacts["protocol_hash.txt"]["media_type"] == "text/plain"
    digest, filename = sidecar.read_text(encoding="utf-8").split()
    assert digest == hashlib.sha256(index.read_bytes()).hexdigest()
    assert filename == "artifact_index.json"
    validation = validate_root_artifact_index(root, expected_commit="a" * 40)
    assert validation["artifact_count"] == 3
    assert validation["raw_run_git_commit"] == "a" * 40
    assert validation["reporting_git_commit"] == "d" * 40
    table.write_text("metric,value\nrmse,0.2\n", encoding="utf-8")
    with pytest.raises(ReportingValidationError, match="hash differs"):
        validate_root_artifact_index(root)


def test_figure_schema_registry_matches_required_generate_inputs():
    assert set(FIGURE_TABLE_SCHEMAS) == {
        "estimator",
        "prediction",
        "ablation",
        "acceleration_phase",
        "governor",
        "follower",
        "robustness",
        "sampling_rate",
        "constraints",
        "scalability",
        "plant",
        "runtime_samples",
        "paired",
        "trajectory_metrics",
        "trace_samples",
    }


def test_governor_acceptance_uses_exact_nonfallback_denominators():
    rows = [
        {
            "n_samples": 10,
            "nonfallback_sample_count": 8,
            "nonfallback_point_admissible_count": 8,
            "nonfallback_one_step_reachable_count": 7,
            "nonfallback_t_free_recorded_count": 8,
            "nonfallback_transition_count": 6,
            "nonfallback_sequence_consistent_count": 6,
            "projection_count": 0,
            "fallback_count": 2,
        },
        {
            "n_samples": 5,
            "nonfallback_sample_count": 0,
            "nonfallback_point_admissible_count": 0,
            "nonfallback_one_step_reachable_count": 0,
            "nonfallback_t_free_recorded_count": 0,
            "nonfallback_transition_count": 0,
            "nonfallback_sequence_consistent_count": 0,
            "projection_count": 1,
            "fallback_count": 5,
        },
    ]
    summary = aggregate_governor_acceptance(rows)
    assert summary["sample_count"] == 15
    assert summary["point_admissible_rate"] == pytest.approx(1.0)
    assert summary["t_free_reachable_rate"] == pytest.approx(7 / 8)
    assert summary["sequence_consistency_rate"] == pytest.approx(1.0)
    assert summary["projection_rate"] == pytest.approx(1 / 15)
    assert summary["fallback_rate"] == pytest.approx(7 / 15)

    broken = [dict(rows[0], nonfallback_t_free_recorded_count=7)]
    with pytest.raises(ReportingValidationError, match="T_free coverage"):
        aggregate_governor_acceptance(broken)


def _runtime_rows(values_by_repetition: tuple[tuple[float, ...], ...]) -> list[dict]:
    rows = []
    for repetition, values in enumerate(values_by_repetition):
        for index, value in enumerate(values):
            rows.append(
                {
                    "method": "candidate",
                    "repetition": repetition,
                    "warmup_cycles_per_trajectory": 20,
                    "dataset_id": "data",
                    "session_id": "session",
                    "trajectory_id": "trajectory",
                    "scenario_id": "clean",
                    "k": 20 + index,
                    "dof": 1,
                    "deadline_us": 10_000.0,
                    "deadline_miss": value > 10_000.0,
                    "total_compute_us": value,
                }
            )
    return rows


def test_runtime_acceptance_uses_all_repetitions_and_strict_boundaries():
    summary = summarize_repeated_runtime(
        _runtime_rows(((100.0, 200.0), (300.0, 4_999.0))),
        method="candidate",
        expected_repetitions=2,
        expected_warmup_cycles=20,
    )
    assert summary["timed_cycle_count"] == 4
    assert summary["repetition_count"] == 2
    assert summary["total_max_us"] == 4_999.0
    assert summary["deadline_miss_rate"] == 0.0

    p99_boundary = reporting._acceptance_record(
        "p99-boundary",
        family="runtime",
        scope="test",
        method="candidate",
        metric="total_p99_us",
        source_artifact="runtime.csv",
        observed_value=1_000.0,
        operator="<",
        threshold_value=1_000.0,
        failure_stage="governor",
        notes="strict boundary",
    )
    max_boundary = reporting._acceptance_record(
        "max-boundary",
        family="runtime",
        scope="test",
        method="candidate",
        metric="total_max_us",
        source_artifact="runtime.csv",
        observed_value=5_000.0,
        operator="<",
        threshold_value=5_000.0,
        failure_stage="governor",
        notes="strict boundary",
    )
    assert p99_boundary["status"] == "fail"
    assert max_boundary["status"] == "fail"

    with pytest.raises(ReportingValidationError, match="repetitions differ"):
        summarize_repeated_runtime(
            _runtime_rows(((100.0,), (200.0,))),
            method="candidate",
            expected_repetitions=3,
            expected_warmup_cycles=20,
        )


def test_csv_regression_is_legacy_only_and_uses_absolute_lag():
    rows = [
        {
            "scenario_id": "legacy_fixed_grid",
            "source_kind": "real_csv_legacy_fixed_grid",
            "method": "deployed_p_only",
            "position_rmse": 0.035187,
            "lag_s": -0.070,
            "position_max_abs_error": 0.184528,
        },
        {
            "scenario_id": "legacy_fixed_grid",
            "source_kind": "real_csv_legacy_fixed_grid",
            "method": "one_step_governed_pva_direct",
            "position_rmse": 0.02991,
            "lag_s": -0.030,
            "position_max_abs_error": 0.184528,
        },
        {
            "scenario_id": "timestamp_causal_hold",
            "method": "one_step_governed_pva_direct",
            "position_rmse": 99.0,
            "lag_s": 99.0,
            "position_max_abs_error": 99.0,
        },
    ]
    criteria = csv_regression_criteria(rows)
    strict = [row for row in criteria if row["required"]]
    assert len(strict) == 3
    assert {row["status"] for row in strict} == {"pass"}
    lag = next(row for row in strict if row["metric"] == "lag_s")
    assert lag["observed_value"] == pytest.approx(0.030)
    assert all(row["status"] == "reported" for row in criteria if not row["required"])


def test_core_diagnostic_publication_mapping_includes_frequency_chirp_and_events(
    monkeypatch, tmp_path
):
    bundle = reporting.ValidatedBundle(
        name="locked_test",
        root=tmp_path,
        validation={},
        run_manifest={},
        artifact_index={},
        data_manifest={},
        split_manifest={},
    )
    calls = []

    def fake_load(_bundle, relative_path, *, required_fields, **_kwargs):
        calls.append((str(relative_path), set(required_fields)))
        return [{field: field for field in required_fields}]

    monkeypatch.setattr(reporting, "load_bundle_csv", fake_load)
    publications = reporting.build_core_diagnostic_publications(
        {"locked_test": bundle}
    )
    assert set(publications) == {
        "summaries/frequency_response.csv",
        "summaries/chirp_frequency_response.csv",
        "summaries/local_event_delay.csv",
    }
    assert [path for path, _ in calls] == [
        "frequency_response.csv",
        "chirp_frequency_response.csv",
        "local_event_delay.csv",
    ]
    chirp_required = next(fields for path, fields in calls if path.startswith("chirp"))
    assert {
        "gain",
        "phase_delay_s",
        "group_delay_s",
        "local_delay_s",
    } <= chirp_required


def test_chirp_response_enters_layer_evidence_with_complete_band_coverage():
    rows = []
    for band, gain, phase, group, local in (
        (0, 0.9, -0.01, 0.02, -0.005),
        (1, 1.2, 0.03, -0.04, 0.015),
    ):
        rows.append(
            {
                "run_id": "run",
                "dataset_id": "data",
                "session_id": "session",
                "trajectory_id": "chirp-001",
                "scenario_id": "clean",
                "method_id": "candidate",
                "joint_id": "joint_0",
                "frequency_band_index": band,
                "frequency_band_count": 2,
                "gain": gain,
                "phase_delay_s": phase,
                "group_delay_s": group,
                "local_delay_s": local,
                "window_truth_sample_denominator": 50,
                "evaluated_sample_count": 49,
                "local_delay_overlap_count": 48,
                "local_delay_overlap_denominator": 49,
            }
        )
    evidence = reporting._chirp_evidence_rows(rows, candidate_method="candidate")
    observed = {row["metric"]: row["observed_value"] for row in evidence}
    assert observed == pytest.approx(
        {
            "chirp_max_abs_gain_error": 0.2,
            "chirp_max_abs_phase_delay_s": 0.03,
            "chirp_max_abs_group_delay_s": 0.04,
            "chirp_max_abs_local_delay_s": 0.015,
        }
    )
    assert {row["source_artifact"] for row in evidence} == {
        "locked_test/chirp_frequency_response.csv"
    }
    with pytest.raises(ReportingValidationError, match="incomplete band coverage"):
        reporting._chirp_evidence_rows(rows[:1], candidate_method="candidate")


def test_fallback_summary_deduplicates_joint_rows_and_preserves_reason():
    samples = []
    events = []
    for k in range(3):
        fallback = k == 1
        for joint in ("joint_0", "joint_1"):
            row = {
                "method_id": "candidate",
                "run_id": "run",
                "dataset_id": "data",
                "session_id": "session",
                "trajectory_id": "trajectory",
                "scenario_id": "clean",
                "joint_id": joint,
                "k": k,
                "fallback": fallback,
                "fallback_reason": "solver_timeout" if fallback else "",
            }
            samples.append(row)
            if fallback:
                events.append(dict(row))
    summary = build_fallback_summary(samples, events, methods=("candidate",))
    overall = next(row for row in summary if row["reason"] == "__all__")
    reason = next(row for row in summary if row["reason"] == "solver_timeout")
    assert overall["fallback_cycle_count"] == 1
    assert overall["total_cycle_count"] == 3
    assert overall["fallback_rate"] == pytest.approx(1 / 3)
    assert reason["fallback_cycle_count"] == 1


def test_scientific_failures_and_layer_evidence_enter_failure_analysis():
    criterion = reporting._acceptance_record(
        "failed-scientific-criterion",
        family="runtime",
        scope="test",
        method="candidate",
        metric="total_max_us",
        source_artifact="runtime.csv",
        observed_value=6_000.0,
        operator="<",
        threshold_value=5_000.0,
        failure_stage="governor|follower",
        notes="failure is retained",
    )
    acceptance = AcceptanceAnalysis(
        criteria=[criterion],
        fallback_summary=[
            {
                "method": "candidate",
                "reason": "__all__",
                "fallback_cycle_count": 0,
                "total_cycle_count": 10,
                "fallback_rate": 0.0,
                "deduplication_unit": "cycle",
            }
        ],
        evidence_ledger=[
            {
                "evidence_id": "estimated-harmful",
                "stage": "information_condition",
                "source_artifact": "acceleration.csv",
                "metric": "estimated_pva_harmful_rate",
                "observed_value": 0.25,
                "negative_observation": True,
                "trajectory_or_cycle_count": 4,
                "interpretation": "negative result retained",
            }
        ],
    )
    result = build_failure_analysis({}, acceptance=acceptance)
    assert "Scientific acceptance failures" in result.markdown
    assert "failed-scientific-criterion" in result.markdown
    assert "governor/follower" in result.markdown
    assert "information_condition" in result.markdown
    assert "negative result retained" in result.markdown


def test_saved_governor_invariants_must_match_independent_recomputation(
    monkeypatch, tmp_path
):
    bundle = reporting.ValidatedBundle(
        name="locked_test",
        root=tmp_path,
        validation={},
        run_manifest={},
        artifact_index={},
        data_manifest={},
        split_manifest={},
    )
    samples = [
        {
            "method_id": "candidate",
            "split": "test",
            "scenario_id": "clean",
        }
    ]
    invariant = {
        "run_id": "run",
        "dataset_id": "data",
        "session_id": "session",
        "trajectory_id": "trajectory-000",
        "scenario_id": "clean",
        "method_id": "candidate",
        "nonfallback_point_admissible_count": 10,
        "nonfallback_one_step_reachable_count": 10,
        "nonfallback_sequence_consistent_count": 9,
    }
    monkeypatch.setattr(reporting, "load_bundle_parquet", lambda _bundle: samples)
    monkeypatch.setattr(
        reporting,
        "governor_invariant_summaries",
        lambda _samples, motion_limits: [dict(invariant)],
    )
    monkeypatch.setattr(
        reporting,
        "load_bundle_csv",
        lambda *_args, **_kwargs: [dict(invariant)],
    )
    _, verified = reporting._verify_candidate_governor_invariants(
        bundle,
        expected_ids=("trajectory-000",),
        candidate_method="candidate",
    )
    assert verified == [invariant]

    mismatched = dict(invariant, nonfallback_one_step_reachable_count=9)
    monkeypatch.setattr(
        reporting,
        "load_bundle_csv",
        lambda *_args, **_kwargs: [mismatched],
    )
    with pytest.raises(ReportingValidationError, match="differ from independent"):
        reporting._verify_candidate_governor_invariants(
            bundle,
            expected_ids=("trajectory-000",),
            candidate_method="candidate",
        )
