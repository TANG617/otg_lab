"""Run one E14 case in an isolated process and write its compact surface row."""

from __future__ import annotations

import argparse
from pathlib import Path

from experiment import _surface_rows

from otg_lab.analysis import MetricSet, analyze_tracking
from otg_lab.cli import load_experiment_spec
from otg_lab.csvio import load_trajectory_csv
from otg_lab.runio import write_json
from otg_lab.tracking import run_tracking


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    spec = load_experiment_spec(project_root, "E14")
    case = next(
        (item for item in spec.cases if item.case_id == arguments.case_id),
        None,
    )
    if case is None:
        parser.error(f"unknown E14 case: {arguments.case_id}")
    input_spec = spec.inputs[0]
    csv_path, metadata_path = input_spec.resolve(project_root)
    reference = load_trajectory_csv(
        csv_path,
        metadata_path=metadata_path,
        require_metadata=True,
    )
    run = run_tracking(
        reference,
        spec.method_for_case(case),
        case.run_config,
    )
    metric_ids = (
        "position_rmse",
        "lag_s",
        "lag_subsample_s",
        "output_velocity_violation_count",
        "output_acceleration_violation_count",
        "output_jerk_violation_count",
        "profile_velocity_violation_count",
        "profile_acceleration_violation_count",
        "profile_jerk_violation_count",
        "profile_constraint_violation_count",
        "fallback_rate",
        "solver_failure_count",
        "deadline_miss_rate",
    )
    table = analyze_tracking(
        reference,
        run,
        MetricSet(
            metric_ids=metric_ids,
            roles={
                metric_id: spec.role_by_metric[metric_id]
                for metric_id in metric_ids
            },
            windows=spec.windows,
            input_id=input_spec.input_id,
            limits=case.run_config.limits,
        ),
    )
    row = _surface_rows(
        {(case.case_id, input_spec.input_id): run},
        table.rows,
        (case,),
    )[0]
    write_json(arguments.output, row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
