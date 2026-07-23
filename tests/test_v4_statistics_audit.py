from __future__ import annotations

import csv
import runpy
from pathlib import Path

import pytest

from otg_lab.artifacts import ArtifactValidationError
from otg_lab.v4_statistics import analyze_v4_confirmation
from otg_lab.v4_statistics_audit import audit_v4_statistics_independently

ROOT = Path(__file__).resolve().parents[1]


def _write_raw_metrics(path: Path) -> None:
    namespace = runpy.run_path(str(ROOT / "tests/test_v4_statistics.py"))
    rows = namespace["_metrics"]()
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _prepared(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "locked"
    metrics = raw / "metrics_by_trajectory.csv"
    _write_raw_metrics(metrics)
    results = tmp_path / "results"
    analyze_v4_confirmation(
        locked_test_root=raw,
        oracle_root=None,
        results_root=results,
        manifest_path=ROOT / "split_manifest_v4.json",
        statistical_design_path=ROOT / "V4_STATISTICAL_DESIGN.json",
    )
    return metrics, results / "statistics"


def _audit(metrics: Path, statistics: Path) -> dict:
    return audit_v4_statistics_independently(
        raw_metrics_path=metrics,
        published_statistics_root=statistics,
        manifest_path=ROOT / "split_manifest_v4.json",
        statistical_design_path=ROOT / "V4_STATISTICAL_DESIGN.json",
    )


def test_independent_recomputation_accepts_source_backed_tables(tmp_path: Path) -> None:
    raw = tmp_path / "locked"
    metrics = raw / "metrics_by_trajectory.csv"
    _write_raw_metrics(metrics)
    results = tmp_path / "results"
    analyze_v4_confirmation(
        locked_test_root=raw,
        oracle_root=None,
        results_root=results,
        manifest_path=ROOT / "split_manifest_v4.json",
        statistical_design_path=ROOT / "V4_STATISTICAL_DESIGN.json",
    )
    proof = audit_v4_statistics_independently(
        raw_metrics_path=metrics,
        published_statistics_root=results / "statistics",
        manifest_path=ROOT / "split_manifest_v4.json",
        statistical_design_path=ROOT / "V4_STATISTICAL_DESIGN.json",
    )
    assert proof["all_independent_statistical_recomputations_verified"] is True
    assert proof["paired_trajectory_count"] == 120


def test_independent_recomputation_rejects_mutated_primary_claim(tmp_path: Path) -> None:
    raw = tmp_path / "locked"
    metrics = raw / "metrics_by_trajectory.csv"
    _write_raw_metrics(metrics)
    results = tmp_path / "results"
    analyze_v4_confirmation(
        locked_test_root=raw,
        oracle_root=None,
        results_root=results,
        manifest_path=ROOT / "split_manifest_v4.json",
        statistical_design_path=ROOT / "V4_STATISTICAL_DESIGN.json",
    )
    primary = results / "statistics" / "primary_comparison.csv"
    text = primary.read_text(encoding="utf-8")
    primary.write_text(text.replace("0.06368781650282984", "0.5", 1), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="primary:relative"):
        audit_v4_statistics_independently(
            raw_metrics_path=metrics,
            published_statistics_root=results / "statistics",
            manifest_path=ROOT / "split_manifest_v4.json",
            statistical_design_path=ROOT / "V4_STATISTICAL_DESIGN.json",
        )


@pytest.mark.parametrize(
    ("filename", "mutate", "message"),
    [
        (
            "primary_comparison.csv",
            lambda row: row.update(
                {
                    "absolute_improvement": "999",
                    "harmful": "False",
                    "negative_or_harmful_row_retained": "False",
                }
            ),
            "absolute_improvement|harmful",
        ),
        (
            "secondary_comparisons.csv",
            lambda row: row.update(
                {
                    "baseline_mean": "999",
                    "absolute_difference": "999",
                    "relative_difference": "999",
                }
            ),
            "baseline_mean|absolute_difference",
        ),
        (
            "worst_five_trajectories.csv",
            lambda row: row.update(
                {
                    "rank": "101",
                    "baseline_position_rmse": "999",
                    "harmful": "False",
                }
            ),
            "worst-five",
        ),
        (
            "stratified_comparisons.csv",
            lambda row: row.update(
                {
                    "bootstrap_seed": str(int(row["bootstrap_seed"]) + 99),
                    "stratum_value": "fabricated",
                }
            ),
            "subgroup identity",
        ),
        (
            "primary_comparison.csv",
            lambda row: row.update({"cohen_dz": "999", "unadjusted_p": "0.5"}),
            "cohen_dz|paired_p",
        ),
    ],
)
def test_independent_recomputation_rejects_adversarial_table_mutations(
    tmp_path: Path, filename: str, mutate, message: str
) -> None:
    metrics, statistics = _prepared(tmp_path)
    target = statistics / filename
    with target.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    mutate(rows[0])
    _write_rows(target, rows)
    with pytest.raises(ArtifactValidationError, match=message):
        _audit(metrics, statistics)


def test_independent_recomputation_rejects_duplicate_secondary_key(
    tmp_path: Path,
) -> None:
    metrics, statistics = _prepared(tmp_path)
    target = statistics / "secondary_comparisons.csv"
    with target.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows.append(dict(rows[0]))
    _write_rows(target, rows)
    with pytest.raises(ArtifactValidationError, match="duplicate"):
        _audit(metrics, statistics)


def test_incomplete_denominator_unavailable_outputs_are_fully_audited(
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(str(ROOT / "tests/test_v4_statistics.py"))
    rows = namespace["_metrics"]()
    for row in rows:
        if (
            row["method"] == "one_step_governed_pva_direct"
            and row["trajectory_id"].endswith("__019")
        ):
            row["completed"] = False
    raw = tmp_path / "locked"
    metrics = raw / "metrics_by_trajectory.csv"
    raw.mkdir()
    with metrics.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    results = tmp_path / "results"
    analyze_v4_confirmation(
        locked_test_root=raw,
        oracle_root=None,
        results_root=results,
        manifest_path=ROOT / "split_manifest_v4.json",
        statistical_design_path=ROOT / "V4_STATISTICAL_DESIGN.json",
    )
    proof = _audit(metrics, results / "statistics")
    assert proof["primary_complete"] is False
    assert proof["primary_classification"] == "unavailable_incomplete_denominator"
    assert proof["all_independent_statistical_recomputations_verified"] is True
