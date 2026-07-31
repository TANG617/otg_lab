from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from otg_lab.cross_analysis import AnalysisConfigError, collect, prepare_analysis
from otg_lab.cross_analysis_reporting import (
    analysis_spec_hash,
    create_analysis_run_directory,
)


def _write_source(
    project_root: Path,
    experiment_id: str,
    run_id: str,
    value: str,
) -> str:
    relative = Path("experiments") / experiment_id / "results" / run_id
    directory = project_root / relative
    analysis = directory / "analysis"
    analysis.mkdir(parents=True)
    manifest = {
        "schema_version": "otg.run_manifest.v1",
        "status": "completed",
        "spec_hash": f"{experiment_id.lower()}-hash",
        "git": {"commit": "abc123", "dirty": False},
        "resolved_experiment_spec": {"experiment_id": experiment_id},
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (analysis / "trajectory_metrics.csv").write_text(
        "input_id,method_id,window_id,metric_id,value,status\n"
        f"sine,candidate,main_evaluation,position_rmse,{value},available\n"
        "sine,candidate,full_overlap,position_rmse,999,available\n",
        encoding="utf-8",
    )
    (analysis / "comparisons.csv").write_text(
        "comparison_id,window_id,metric_id,status\n"
        "candidate_vs_baseline,main_evaluation,position_rmse,available\n"
        "candidate_vs_baseline,full_overlap,position_rmse,available\n",
        encoding="utf-8",
    )
    return relative.as_posix()


def _write_config(
    project_root: Path,
    source_directories: list[str],
) -> Path:
    analysis_directory = project_root / "analyses" / "A01_test"
    analysis_directory.mkdir(parents=True)
    config = {
        "schema_version": "otg.cross_analysis.v1",
        "analysis_id": "A01",
        "slug": "test",
        "title": "Test analysis",
        "question": "Do the pinned sources combine?",
        "project_root": "../..",
        "source_requirements": {
            "status": "completed",
            "same_git_commit": True,
            "allow_dirty_git": False,
        },
        "sources": [
            {
                "source_id": f"source_{index}",
                "experiment_id": f"E0{index + 3}",
                "source_directory": source_directory,
                "factors": {
                    "target_components": ("PVA", "PV")[index],
                },
            }
            for index, source_directory in enumerate(source_directories)
        ],
        "artifacts": {
            "trajectory_metrics": "analysis/trajectory_metrics.csv",
            "comparisons": "analysis/comparisons.csv",
        },
        "selection": {
            "window_ids": ["main_evaluation"],
            "metric_ids": ["position_rmse"],
        },
    }
    path = analysis_directory / "analysis.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def test_collect_writes_filtered_tidy_tables_and_provenance(tmp_path: Path) -> None:
    source_directories = [
        _write_source(tmp_path, "E03", "run-e03", "0.3"),
        _write_source(tmp_path, "E04", "run-e04", "0.4"),
    ]
    config_path = _write_config(tmp_path, source_directories)

    output = collect(config_path)

    assert output == config_path.parent / "work"
    with (output / "combined_trajectory_metrics.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert [row["value"] for row in rows] == ["0.3", "0.4"]
    assert [row["factor_target_components"] for row in rows] == ["PVA", "PV"]
    assert {row["window_id"] for row in rows} == {"main_evaluation"}

    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["schema_version"] == "otg.cross_analysis.provenance.v1"
    assert provenance["analysis_id"] == "A01"
    assert len(provenance["sources"]) == 2
    assert len(provenance["artifacts"]) == 4


def test_collect_rejects_implicit_latest_source(tmp_path: Path) -> None:
    (tmp_path / "experiments").mkdir()
    config_path = _write_config(
        tmp_path,
        [
            "experiments/E03/results/latest",
            "experiments/E04/results/run-e04",
        ],
    )

    with pytest.raises(AnalysisConfigError, match="exact run, not latest"):
        collect(config_path, check_only=True)


def test_prepare_analysis_collects_in_memory_without_writing(
    tmp_path: Path,
) -> None:
    source_directories = [
        _write_source(tmp_path, "E03", "run-e03", "0.3"),
        _write_source(tmp_path, "E04", "run-e04", "0.4"),
    ]
    config_path = _write_config(tmp_path, source_directories)

    prepared = prepare_analysis(config_path)

    assert prepared.analysis_id == "A01"
    assert len(prepared.sources) == 2
    assert len(prepared.collected["trajectory_metrics"][1]) == 2
    assert not (config_path.parent / "work").exists()


def test_analysis_run_directory_uses_timestamp_and_pinned_input_hash(
    tmp_path: Path,
) -> None:
    source_directories = [
        _write_source(tmp_path, "E03", "run-e03", "0.3"),
        _write_source(tmp_path, "E04", "run-e04", "0.4"),
    ]
    config_path = _write_config(tmp_path, source_directories)
    prepared = prepare_analysis(config_path)

    run_directory = create_analysis_run_directory(prepared)

    assert run_directory.parent == config_path.parent / "runs"
    assert run_directory.name.endswith(f"__{analysis_spec_hash(prepared)[:12]}")
    assert run_directory.is_dir()
