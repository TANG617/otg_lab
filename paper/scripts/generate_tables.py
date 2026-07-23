#!/usr/bin/env python3
"""Generate LaTeX result tables from the bounded evidence manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
EVIDENCE = PAPER_ROOT / "generated/manifests/extracted_evidence.json"
OUT = PAPER_ROOT / "generated/tables"

DATASET_NAMES = {
    "quadratic_with_extremum": "Quadratic",
    "cubic": "Cubic",
    "sine": "Sine",
}
METHOD_NAMES = {
    "p": "P",
    "pv_truth": "PV truth",
    "pva_truth": "PVA truth",
    "pv_backward": "PV backward",
    "pva_backward": "PVA backward",
    "pv_central_offline": "PV centered offline",
    "pva_central_offline": "PVA centered offline",
    "pv_central_causal": "PV centered causal",
    "pva_central_causal": "PVA centered causal",
}


def tabular(headers: list[str], rows: list[list[str]], spec: str) -> str:
    lines = [
        "% Generated; do not edit.",
        f"\\begin{{tabular}}{{{spec}}}",
        "\\toprule",
        " & ".join(headers) + r" \\",
        "\\midrule",
    ]
    lines.extend(" & ".join(row) + r" \\" for row in rows)
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    analytic = data["phase_a"]["analytic_tracking"]
    csv_rows = data["phase_a"]["csv_tracking"]
    deriv = data["phase_a"]["derivatives"]
    v3 = data["v3"]["acceptance_rows"]
    runtime = data["v3"]["direct_runtime_primary"]

    outputs: dict[str, str] = {}
    outputs["analytic_tracking.tex"] = tabular(
        ["Reference", "Target", "RMSE (rad)", "Max. error (rad)", "Lag (ms)"],
        [
            [
                DATASET_NAMES[row["dataset"]],
                METHOD_NAMES[row["method_id"]],
                f"{row['rmse']:.5f}",
                f"{row['max_error']:.5f}",
                f"{row['best_lag_ms']:.0f}",
            ]
            for row in analytic
        ],
        "llrrr",
    )
    outputs["derivative_accuracy.tex"] = tabular(
        [
            "Reference",
            "Derivative source",
            "Causal",
            "$v$ RMSE",
            "$a$ RMSE",
        ],
        [
            [
                DATASET_NAMES[row["dataset"]],
                {
                    "analytic_truth": "Analytic truth",
                    "backward_fd": "Backward",
                    "centered_fd_offline": "Centered offline",
                    "centered_fd_causal_delay1": "Centered causal",
                }[row["derivative_source"]],
                "yes" if row["causal"] else "no",
                f"{row['velocity_rmse']:.6f}",
                f"{row['acceleration_rmse']:.6f}",
            ]
            for row in deriv
            if row["derivative_source"] != "analytic_truth"
        ],
        "lllrr",
    )
    outputs["csv_tracking.tex"] = tabular(
        ["Target", "Info.", "RMSE (rad)", "Max. error (rad)", "Lag (ms)", "Proj. (\\%)"],
        [
            [
                METHOD_NAMES[row["method_id"]],
                "causal" if row["causal"] else "offline",
                f"{row['rmse']:.5f}",
                f"{row['max_error']:.5f}",
                f"{row['best_lag_ms']:.0f}",
                f"{100 * row['target_projection_rate']:.2f}",
            ]
            for row in csv_rows
        ],
        "llrrrr",
    )
    wanted = {
        "continuous_vaj_violation_count_zero": "Continuous V/A/internal-J violations",
        "projection_rate_zero": "Projected cycles",
        "nonfallback_point_admissibility_100pct": "Point-admissible rate",
        "nonfallback_t_free_le_dt_100pct": "One-step-reachable rate",
        "nonfallback_sequence_consistency_100pct": "Sequence-consistent rate",
    }
    outputs["v3_direct_safety.tex"] = tabular(
        ["Frozen v3 audit quantity", "Observed", "Denominator", "Status"],
        [
            [
                wanted[row["criterion_id"]],
                (
                    f"{row['observed']:.1f}"
                    if abs(row["observed"]) >= 10
                    else f"{row['observed']:.3f}"
                ),
                f"{row['denominator']:.0f}",
                "pass" if row["passed"] else "fail",
            ]
            for row in v3
            if row["criterion_id"] in wanted
        ],
        "lrrl",
    )
    outputs["v3_runtime.tex"] = tabular(
        ["Frozen v3 runtime quantity", "Observed", "Population"],
        [
            ["Timed cycles", f"{runtime['timed_cycle_count']:.0f}", "locked-test, after warm-up"],
            ["Total compute p99 ($\\mu$s)", f"{runtime['runtime_p99_us']:.2f}", "locked-test, after warm-up"],
            ["Total compute maximum ($\\mu$s)", f"{runtime['runtime_max_us']:.2f}", "locked-test, after warm-up"],
            ["10-ms deadline-miss rate", f"{runtime['runtime_deadline_miss_rate']:.3f}", "locked-test, after warm-up"],
        ],
        "lrl",
    )

    OUT.mkdir(parents=True, exist_ok=True)
    if args.check:
        stale = [
            name
            for name, content in outputs.items()
            if not (OUT / name).is_file()
            or (OUT / name).read_text(encoding="utf-8") != content
        ]
        if stale:
            raise SystemExit("stale generated tables: " + ", ".join(stale))
        print("table generation verified")
        return 0
    for name, content in outputs.items():
        (OUT / name).write_text(content, encoding="utf-8")
    print(f"wrote {len(outputs)} tables under {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
