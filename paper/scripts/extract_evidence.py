#!/usr/bin/env python3
"""Extract bounded paper evidence without rerunning any experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
OUTPUT = PAPER_ROOT / "generated/manifests/extracted_evidence.json"
PHASE_A_ROOT = REPO_ROOT / "results/vendor_target_state_ablation"
V3_ROOT = REPO_ROOT / "results/paper_evidence_v3"

SOURCES = {
    "phase_a_tracking": PHASE_A_ROOT / "target_state_ablation_metrics.csv",
    "phase_a_derivatives": PHASE_A_ROOT / "derivative_source_metrics.csv",
    "phase_a_oracle": PHASE_A_ROOT / "oracle_sanity_metrics.csv",
    "phase_a_limits": PHASE_A_ROOT / "limit_sensitivity_metrics.csv",
    "phase_a_run": PHASE_A_ROOT / "run.json",
    "v3_status": REPO_ROOT / "protocol_status_v3.json",
    "v3_postreview": REPO_ROOT / "protocol_status_v3_postreview.json",
    "v3_acceptance": V3_ROOT / "summaries/acceptance_criteria.csv",
    "v3_fallback": V3_ROOT / "summaries/fallback_summary.csv",
    # The full runtime CSV belongs to the frozen release bundle and is not
    # present in every Git checkout.  Consume the committed, independently
    # recomputed primary row from the bounded evidence audit instead.
    "v3_runtime_primary": PAPER_ROOT / "logic/evidence_audit.json",
    "v3_artifact_index": V3_ROOT / "artifact_index.json",
    "postfreeze_compatibility": PAPER_ROOT
    / "generated/manifests/postfreeze_compatibility.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_source_baseline() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "origin/main"], cwd=REPO_ROOT, text=True
    ).strip()


def git_source_timestamp() -> str:
    return subprocess.check_output(
        ["git", "show", "-s", "--format=%cI", "origin/main"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()


def records(frame: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    subset = frame[columns].copy()
    # ``to_json`` caps decimal precision and can erase or distort the
    # near-machine-precision PV/PVA difference used by the scoped non-result.
    # ``to_dict`` retains Python's round-trip float representation; the final
    # payload is serialized once by ``json.dumps`` below.
    return subset.to_dict(orient="records")


def read_csv(path: Path) -> pd.DataFrame:
    # Pandas' default high-precision parser can differ by a few ULPs across
    # libc/platform combinations.  Round-trip mode restores the IEEE value
    # represented by the source decimal string and keeps CI provenance stable.
    return pd.read_csv(path, float_precision="round_trip")


def extract() -> dict[str, Any]:
    missing = [str(path) for path in SOURCES.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing evidence files:\n" + "\n".join(missing))

    tracking = read_csv(SOURCES["phase_a_tracking"])
    derivatives = read_csv(SOURCES["phase_a_derivatives"])
    oracle = read_csv(SOURCES["phase_a_oracle"])
    limits = read_csv(SOURCES["phase_a_limits"])
    phase_run = json.loads(SOURCES["phase_a_run"].read_text(encoding="utf-8"))
    acceptance = read_csv(SOURCES["v3_acceptance"])
    fallback = read_csv(SOURCES["v3_fallback"])
    runtime_audit = json.loads(
        SOURCES["v3_runtime_primary"].read_text(encoding="utf-8")
    )
    status = json.loads(SOURCES["v3_status"].read_text(encoding="utf-8"))
    postreview = json.loads(SOURCES["v3_postreview"].read_text(encoding="utf-8"))
    postfreeze_compatibility = json.loads(
        SOURCES["postfreeze_compatibility"].read_text(encoding="utf-8")
    )

    analytic = tracking[
        tracking["dataset"].isin(["quadratic_with_extremum", "cubic", "sine"])
        & tracking["method_id"].isin(["p", "pv_truth", "pva_truth"])
    ]
    csv_results = tracking[tracking["dataset"].eq("csv")]
    acceptance_keys = [
        "continuous_vaj_violation_count_zero",
        "projection_rate_zero",
        "runtime_total_p99_below_1ms",
        "runtime_total_max_below_5ms",
        "runtime_100hz_deadline_miss_rate_zero",
        "nonfallback_point_admissibility_100pct",
        "nonfallback_t_free_le_dt_100pct",
        "nonfallback_sequence_consistency_100pct",
        "continuous_velocity_margin_nonnegative",
        "continuous_acceleration_margin_nonnegative",
        "continuous_jerk_margin_nonnegative",
    ]
    v3_rows = acceptance[acceptance["criterion_id"].isin(acceptance_keys)]
    v3_rows = v3_rows.assign(
        evidence_class=v3_rows["family"],
        observed=v3_rows["observed_value"],
        threshold=v3_rows["threshold_value"],
        passed=v3_rows["status"].eq("pass"),
    )
    direct_fallback = fallback[
        fallback["method"].eq("one_step_governed_pva_direct")
        & fallback["reason"].eq("__all__")
    ]
    if len(direct_fallback) != 1:
        raise ValueError("expected one direct-governor fallback roll-up")
    runtime_candidates = [
        item
        for item in runtime_audit["quantitative_candidates"]
        if item["candidate_id"] == "Q_V3_DIRECT_RUNTIME_PRIMARY"
    ]
    if len(runtime_candidates) != 1:
        raise ValueError("expected one audited primary direct-governor runtime row")
    runtime_candidate = runtime_candidates[0]
    runtime_values = runtime_candidate["values"]
    direct_runtime = {
        "warmup_samples_per_trajectory": runtime_candidate["selector"]["k_min"],
        "timed_cycle_count": runtime_values["timed_cycle_count"],
        "runtime_p50_us": runtime_values["runtime_p50_us"],
        "runtime_p90_us": runtime_values["runtime_p90_us"],
        "runtime_p99_us": runtime_values["runtime_p99_us"],
        "runtime_p99_9_us": runtime_values["runtime_p99_9_us"],
        "runtime_max_us": runtime_values["runtime_max_us"],
        "runtime_deadline_miss_rate": runtime_values["deadline_miss_rate"],
    }

    payload = {
        "schema_version": "otg.paper-extracted-evidence.v1",
        "generated_at": git_source_timestamp(),
        # Paper-only commits must not make evidence extraction stale.  The
        # source baseline is the latest main commit from which this paper
        # branch was cut; each consumed artifact also carries its own hash.
        "source_commit": git_source_baseline(),
        "sources": {
            source_id: {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for source_id, path in SOURCES.items()
        },
        "phase_a": {
            "protocol": {
                "common_warmup_samples": phase_run["design"][
                    "common_warmup_samples"
                ],
                "input_csv_rows": phase_run["provenance"]["input_csv_rows"],
            },
            "analytic_tracking": records(
                analytic,
                [
                    "dataset",
                    "method_id",
                    "causal",
                    "rmse",
                    "mae",
                    "max_error",
                    "best_lag_ms",
                    "target_projection_rate",
                    "ruckig_compute_p99_us",
                ],
            ),
            "derivatives": records(
                derivatives,
                [
                    "dataset",
                    "derivative_source",
                    "causal",
                    "future_samples",
                    "native_delay_samples",
                    "velocity_rmse",
                    "acceleration_rmse",
                ],
            ),
            "oracle": records(
                oracle,
                [
                    "dataset",
                    "causal",
                    "future_samples",
                    "rmse",
                    "max_error",
                    "best_lag_ms",
                ],
            ),
            "csv_tracking": records(
                csv_results,
                [
                    "method_id",
                    "method",
                    "causal",
                    "future_samples",
                    "native_delay_samples",
                    "rmse",
                    "mae",
                    "max_error",
                    "best_lag_ms",
                    "target_projection_rate",
                    "raw_target_max_velocity",
                    "raw_target_max_acceleration",
                    "raw_target_max_sampled_jerk",
                ],
            ),
            "limit_sensitivity": records(
                limits,
                [
                    "dataset",
                    "method_id",
                    "sweep_type",
                    "sweep_value",
                    "evaluation_start_index",
                    "evaluation_stop_index_exclusive",
                    "rmse",
                    "best_lag_ms",
                ],
            ),
        },
        "v3": {
            "locked_test_trajectory_count": status["locked_test_trajectory_count"],
            "raw_bundle_count": status["raw_bundle_count"],
            "bounded_artifact_count": status["bounded_artifact_count"],
            "required_component_criteria": status["required_component_criteria"],
            "required_component_pass_count": status[
                "required_component_pass_count"
            ],
            "required_component_failure_count": status[
                "required_component_failure_count"
            ],
            "acceptance_rows": records(
                v3_rows,
                [
                    "criterion_id",
                    "evidence_class",
                    "method",
                    "metric",
                    "observed",
                    "operator",
                    "threshold",
                    "denominator",
                    "passed",
                ],
            ),
            "direct_fallback": records(
                direct_fallback,
                [
                    "fallback_cycle_count",
                    "total_cycle_count",
                    "fallback_rate",
                ],
            )[0],
            "direct_runtime_primary": direct_runtime,
            "postreview": postreview,
        },
        "postfreeze_compatibility": postfreeze_compatibility,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = extract()
    if args.check and OUTPUT.is_file():
        current = json.loads(OUTPUT.read_text(encoding="utf-8"))
        for key in (
            "source_commit",
            "sources",
            "phase_a",
            "v3",
            "postfreeze_compatibility",
        ):
            if current.get(key) != payload.get(key):
                raise SystemExit(f"extracted evidence is stale: {key}")
        print("evidence extraction verified")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
