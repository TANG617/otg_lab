from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from otg_lab.v4_statistics import (
    BOOTSTRAP_RESAMPLES,
    PRIMARY_CANDIDATE,
    analyze_v4_confirmation,
    build_v4_statistical_tables,
    classify_primary_result,
    holm_adjust_v4,
    reconstruct_v4_bootstrap_draws,
)

ROOT = Path(__file__).resolve().parents[1]


def _locked_inputs() -> tuple[dict, dict]:
    manifest = json.loads((ROOT / "split_manifest_v4.json").read_text())
    design = json.loads((ROOT / "V4_STATISTICAL_DESIGN.json").read_text())
    return manifest, design


def _metrics() -> list[dict]:
    manifest, _ = _locked_inputs()
    test_rows = sorted(
        (
            row
            for row in manifest["trajectories"]
            if row["split"] == "test"
        ),
        key=lambda row: row["trajectory_id"],
    )
    methods = (
        "one_step_governed_p_direct",
        "one_step_governed_pv_direct",
        "one_step_governed_pva_direct",
        "predicted_p_ordinary_ruckig",
        "raw_predicted_pva_ordinary_ruckig",
    )
    records = []
    for index, manifest_row in enumerate(test_rows):
        baseline = 1.0 + 0.002 * index
        for method in methods:
            if method == "one_step_governed_p_direct":
                rmse = baseline
                max_error = 2.0 * baseline
                lag = 0.020 + 0.00001 * index
            elif method == "one_step_governed_pv_direct":
                rmse = 0.96 * baseline
                max_error = 1.99 * baseline
                lag = 0.022 + 0.00001 * index
            elif method == "one_step_governed_pva_direct":
                # Twenty explicitly harmful trajectories are retained.
                rmse = (1.10 if index >= 100 else 0.90) * baseline
                max_error = 2.02 * baseline
                lag = 0.025 + 0.00001 * index
            elif method == "predicted_p_ordinary_ruckig":
                rmse = 1.20 * baseline
                max_error = 2.40 * baseline
                lag = 0.030 + 0.00001 * index
            else:
                rmse = 1.10 * baseline
                max_error = 2.30 * baseline
                lag = 0.029 + 0.00001 * index
            records.append(
                {
                    "dataset_id": manifest["dataset_id"],
                    "trajectory_id": manifest_row["trajectory_id"],
                    "split": "test",
                    "family": manifest_row["family"],
                    "demand_stratum": manifest_row["demand_stratum"],
                    "method": method,
                    "position_rmse": rmse,
                    "position_max_abs_error": max_error,
                    "lag_s": lag,
                    "completed": True,
                }
            )
    return records


@pytest.fixture(scope="module")
def complete_tables() -> dict[str, list[dict]]:
    manifest, design = _locked_inputs()
    return build_v4_statistical_tables(_metrics(), manifest, design)


def test_exact_resamples_fixed_seeds_and_repeatability(
    complete_tables: dict[str, list[dict]],
) -> None:
    assert {
        row["bootstrap_resamples"]
        for row in complete_tables["secondary_comparisons.csv"]
    } == {BOOTSTRAP_RESAMPLES}
    assert [
        row["bootstrap_seed"]
        for row in complete_tables["secondary_comparisons.csv"]
    ] == [2026072302, 2026072303, 2026072304, 2026072305, 2026072306]
    manifest, design = _locked_inputs()
    first = reconstruct_v4_bootstrap_draws(_metrics(), manifest, design)
    second = reconstruct_v4_bootstrap_draws(_metrics(), manifest, design)
    primary = first["PVA_vs_P_position_RMSE"]
    assert len(primary["relative_improvement"]) == 10_000
    assert primary == second["PVA_vs_P_position_RMSE"]


def test_complete_denominator_subgroups_harm_and_negative_preservation(
    complete_tables: dict[str, list[dict]],
) -> None:
    paired = complete_tables["primary_comparison.csv"]
    assert len(paired) == 120
    assert {row["paired_trajectory_count"] for row in paired} == {120}
    assert sum(row["harmful"] is True for row in paired) == 20
    assert sum(row["negative_or_harmful_row_retained"] for row in paired) == 20

    family = complete_tables["family_effects.csv"]
    demand = complete_tables["demand_stratum_effects.csv"]
    active = complete_tables["acceleration_active_effect.csv"]
    assert len(family) == 6
    assert {row["trajectory_count"] for row in family} == {20}
    assert len(demand) == 4
    assert {row["trajectory_count"] for row in demand} == {30}
    assert len(active) == 1
    assert active[0]["trajectory_count"] == 40
    assert {row["worst_family"] for row in family} == {
        min(
            family,
            key=lambda row: (
                row["relative_improvement"],
                row["stratum_value"],
            ),
        )["stratum_value"]
    }

    harm = next(
        row
        for row in complete_tables["harmful_trajectory_rate.csv"]
        if row["analysis_kind"] == "primary"
    )
    assert harm["harmful_count"] == 20
    assert harm["denominator"] == 120
    assert harm["evaluated_count"] == 120
    assert harm["harmful_rate"] == pytest.approx(1 / 6)
    assert len(complete_tables["worst_five_trajectories.csv"]) == 5


def test_incomplete_primary_is_unavailable_without_complete_case_inference() -> None:
    manifest, design = _locked_inputs()
    records = [
        row
        for row in _metrics()
        if not (
            row["method"] == PRIMARY_CANDIDATE
            and row["trajectory_id"].endswith("__019")
        )
    ]
    tables = build_v4_statistical_tables(records, manifest, design)
    primary = tables["primary_comparison.csv"]
    assert len(primary) == 120
    assert {row["formal_inference_status"] for row in primary} == {
        "unavailable_incomplete_denominator"
    }
    assert {row["paired_trajectory_count"] for row in primary} == {114}
    # Six IDs end in __019, one in each family.  None are silently deleted
    # from the output denominator or replaced by complete-case inference.
    assert sum(row["paired_value_available"] is False for row in primary) == 6
    assert {row["primary_result_classification"] for row in primary} == {
        "unavailable_incomplete_denominator"
    }
    assert tables["worst_five_trajectories.csv"] == []


def test_incomplete_ordinary_pair_marks_only_s5_unavailable() -> None:
    manifest, design = _locked_inputs()
    records = [
        row
        for row in _metrics()
        if not (
            row["method"] == "raw_predicted_pva_ordinary_ruckig"
            and row["trajectory_id"].endswith("__000")
        )
    ]
    tables = build_v4_statistical_tables(records, manifest, design)
    secondary = {
        row["comparison_id"]: row
        for row in tables["secondary_comparisons.csv"]
    }
    assert secondary["S5"]["status"] == "unavailable_incomplete_denominator"
    assert secondary["S5"]["trajectory_count"] == 114
    assert secondary["S5"]["unadjusted_p"] is None
    assert secondary["S5"]["holm_adjusted_p"] is None
    assert all(secondary[key]["status"] == "available" for key in ("S1", "S2", "S3", "S4"))
    assert all(
        secondary[key]["holm_adjusted_p"] is not None
        for key in ("S1", "S2", "S3", "S4")
    )


def test_holm_uses_id_tie_break_and_full_five_test_family() -> None:
    adjusted = holm_adjust_v4(
        {"S1": 0.01, "S2": 0.01, "S3": 0.03, "S4": 0.20, "S5": 0.90}
    )
    assert adjusted == pytest.approx(
        {"S1": 0.05, "S2": 0.05, "S3": 0.09, "S4": 0.40, "S5": 0.90}
    )
    missing = holm_adjust_v4(
        {"S1": 0.01, "S2": 0.02, "S3": 0.03, "S4": 0.20, "S5": None}
    )
    assert missing["S1"] == pytest.approx(0.05)
    assert missing["S5"] is None


def test_secondary_p_value_is_paired_t_and_zero_variance_is_explicit(
    complete_tables: dict[str, list[dict]],
) -> None:
    secondary = {
        row["comparison_id"]: row
        for row in complete_tables["secondary_comparisons.csv"]
    }
    # S4 has the same nonzero -0.005 s improvement on every trajectory:
    # locked zero-variance rule is p=0 and Cohen dz is null, not infinity.
    assert secondary["S4"]["unadjusted_p"] == 0.0
    assert secondary["S4"]["cohen_dz"] is None
    assert secondary["S4"]["cohen_dz_zero_variance"] is True


def test_classification_boundaries_and_precedence() -> None:
    assert classify_primary_result(0.06, 0.05, 0.08)["classification"] == (
        "strongly_material"
    )
    assert classify_primary_result(0.05, 0.01, 0.08)["classification"] == (
        "practically_material"
    )
    assert classify_primary_result(0.02, 0.01, 0.03)["classification"] == (
        "confirmed_positive"
    )
    assert classify_primary_result(0.00, 0.00, 0.02)["classification"] == (
        "inconclusive"
    )
    assert classify_primary_result(-0.02, -0.04, -0.01)["classification"] == (
        "confirmed_harmful"
    )


def test_duplicate_rows_cannot_be_treated_as_samples() -> None:
    manifest, design = _locked_inputs()
    records = _metrics()
    records.append(dict(records[0], joint_id="duplicate-joint"))
    with pytest.raises(ValueError, match="one whole-trajectory row"):
        build_v4_statistical_tables(records, manifest, design)


def test_file_hook_writes_statistics_and_returns_runner_handoff(tmp_path: Path) -> None:
    manifest, design = _locked_inputs()
    locked = tmp_path / "locked_test"
    locked.mkdir()
    records = _metrics()
    with (locked / "metrics_by_trajectory.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    manifest_path = tmp_path / "manifest.json"
    design_path = tmp_path / "design.json"
    manifest_path.write_text(json.dumps(manifest))
    design_path.write_text(json.dumps(design))

    handoff = analyze_v4_confirmation(
        locked_test_root=locked,
        oracle_root=None,
        results_root=tmp_path / "results",
        manifest_path=manifest_path,
        statistical_design_path=design_path,
    )

    statistics_root = Path(handoff["statistics_root"])
    assert statistics_root.is_dir()
    assert (statistics_root / "primary_comparison.csv").is_file()
    assert (statistics_root / "secondary_comparisons.csv").is_file()
    assert handoff["paired_denominator"] == 120
    assert handoff["ordinary_ruckig_secondary_status"] == "available"
    assert handoff["oracle_excluded_from_confirmatory_statistics"] is True
