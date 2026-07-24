from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from test_v4_handoff import FAMILIES, METHODS, _build_evidence, _write_csv

from otg_lab.v4_contextual import (
    DERIVED_TABLES,
    V4ContextualError,
    generate_v4_contextual_tables,
)
from otg_lab.v4_handoff import generate_v4_handoff

ORDINARY = (
    "deployed_p_only_ordinary_ruckig",
    "predicted_p_ordinary_ruckig",
    "raw_predicted_pv_ordinary_ruckig",
    "raw_predicted_pva_ordinary_ruckig",
)
ORACLE = (
    "oracle_one_step_p_direct",
    "oracle_one_step_pv_direct",
    "oracle_one_step_pva_direct",
)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _raw_context_sources(root: Path) -> tuple[Path, Path]:
    _build_evidence(root)
    locked = root / "raw_runs" / "locked_test"
    oracle = root / "raw_runs" / "oracle_diagnostic"

    metrics = _read(root / "statistics" / "metrics_by_trajectory.csv")
    completion = _read(locked / "completion_summary.csv")
    constraints = _read(locked / "constraint_audit.csv")
    trajectory_rows = [
        row
        for row in _read(root / "statistics" / "primary_comparison.csv")
    ]
    for method_index, method in enumerate(ORDINARY):
        completion.append(
            {
                "method": method,
                "attempted_trajectories": "120",
                "completed_trajectories": "120",
                "failed_trajectories": "0",
            }
        )
        for row in trajectory_rows:
            metrics.append(
                {
                    "trajectory_id": row["trajectory_id"],
                    "family": row["family"],
                    "method": method,
                    "position_rmse": str(
                        float(row["baseline_position_rmse"])
                        * (1.2 - 0.02 * method_index)
                    ),
                }
            )
            constraints.append(
                {
                    "trajectory_id": row["trajectory_id"],
                    "method": method,
                    "violation_count": "0",
                    "velocity_margin": "1",
                    "acceleration_margin": "2",
                    "jerk_margin": "3000",
                    "audit_method": "analytic_profile_extrema",
                }
            )
    _write_csv(locked / "metrics_by_trajectory.csv", metrics)
    _write_csv(locked / "completion_summary.csv", completion)
    _write_csv(
        locked / "constraint_audit.csv",
        constraints,
        list(dict.fromkeys(key for row in constraints for key in row)),
    )
    _write_csv(
        locked / "ordinary_ruckig_method_identity.csv",
        [
            {
                "trajectory_id": row["trajectory_id"],
                "method_id": method,
                "native_unshielded": True,
            }
            for method in ORDINARY
            for row in trajectory_rows
        ],
    )
    _write_csv(
        locked / "runtime_repeated_summary.csv",
        [
            {
                "method": method,
                "repetition": repetition,
                "runtime_p99_us": 400,
                "runtime_max_us": 900,
                "deadline_miss_rate": 0,
                "timing_population_complete": True,
            }
            for method in METHODS
            for repetition in range(5)
        ],
    )
    _write_csv(
        locked / "runtime_repeated_failures.csv",
        [],
        ["method_id", "trajectory_id", "repetition", "reason"],
    )

    manifest_rows = []
    oracle_metrics = []
    ordinal = 0
    for family in FAMILIES:
        for index in range(20):
            trajectory_id = f"{family}__v4__test__{index:03d}"
            demand = ("low", "medium", "high", "near_limit")[index // 5]
            manifest_rows.append(
                {
                    "trajectory_id": trajectory_id,
                    "split": "test",
                    "family": family,
                    "demand_stratum": demand,
                }
            )
            for method_index, method in enumerate(ORACLE):
                oracle_metrics.append(
                    {
                        "trajectory_id": trajectory_id,
                        "method": method,
                        "position_rmse": (1.0 + ordinal * 0.001)
                        * (1.0 - 0.04 * method_index),
                    }
                )
            ordinal += 1
    _write_csv(oracle / "metrics_by_trajectory.csv", oracle_metrics)
    _write_csv(
        oracle / "oracle_method_identity.csv",
        [
            {
                "trajectory_id": row["trajectory_id"],
                "method_id": method,
                "oracle_identity_valid": True,
            }
            for method in ORACLE
            for row in manifest_rows
        ],
    )
    (oracle / "split_manifest.json").write_text(
        json.dumps({"trajectories": manifest_rows}), encoding="utf-8"
    )
    return locked, oracle


def test_contextual_producer_to_real_handoff_minimal_e2e(tmp_path: Path) -> None:
    root = tmp_path / "paper_evidence_v4"
    locked, oracle = _raw_context_sources(root)
    result = generate_v4_contextual_tables(
        results_root=root,
        locked_test_root=locked,
        oracle_root=oracle,
    )
    assert all((root / "statistics" / name).is_file() for name in DERIVED_TABLES)
    oracle_rows = _read(
        root / "statistics" / "oracle_target_component_metrics.csv"
    )
    assert len(oracle_rows) == 360
    assert {row["causal"] for row in oracle_rows} == {"False"}
    ordinary = _read(root / "statistics" / "ordinary_ruckig_completion.csv")
    assert len(ordinary) == 4
    assert {row["attempted_trajectories"] for row in ordinary} == {"120"}

    handoff = generate_v4_handoff(
        root,
        "a" * 40,
        {**result["source_hashes"], **result["table_hashes"]},
    )
    assert handoff["oracle_diagnostics"]["causal"] is False
    assert handoff["ordinary_ruckig"]["role"] == "contextual_secondary"
    assert (root / "V4_RESULT_SUMMARY.md").is_file()
    before = hashlib.sha256(
        (root / "paper_handoff.json").read_bytes()
    ).hexdigest()
    resumed = generate_v4_handoff(
        root,
        "a" * 40,
        {**result["source_hashes"], **result["table_hashes"]},
        report_only=True,
    )
    assert resumed == handoff
    assert hashlib.sha256(
        (root / "paper_handoff.json").read_bytes()
    ).hexdigest() == before


def test_report_only_is_exact_noop_and_refuses_changed_derived_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "paper_evidence_v4"
    locked, oracle = _raw_context_sources(root)
    generate_v4_contextual_tables(
        results_root=root,
        locked_test_root=locked,
        oracle_root=oracle,
    )
    second = generate_v4_contextual_tables(
        results_root=root,
        locked_test_root=locked,
        oracle_root=oracle,
        report_only=True,
    )
    assert second["report_only"] is True
    target = root / "statistics" / "ordinary_ruckig_completion.csv"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(V4ContextualError, match="differs from existing"):
        generate_v4_contextual_tables(
            results_root=root,
            locked_test_root=locked,
            oracle_root=oracle,
            report_only=True,
        )
