"""Shared, renderer-neutral data model for the E08 dashboards."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
REFERENCE_METHOD_ID = "recorded_reference"
REFERENCE_LABEL = "Recorded reference"
BASELINE_METHOD_ID = "p_kp1_baseline"
METHOD_LABELS = {
    BASELINE_METHOD_ID: "P-only baseline",
    "pva_est_backward_o1_k": "PVA est O1 [k]",
    "pva_est_backward_o2_k": "PVA est O2 [k]",
    "pva_est_centered_o2_km1": "PVA centered O2 [k−1]",
    "pva_pred_backward_o1_kp1": "PVA pred O1 [k+1]",
    "pva_pred_backward_o2_kp1": "PVA pred O2 [k+1]",
}
LINE_STYLES = {
    BASELINE_METHOD_ID: "solid",
    "pva_est_backward_o1_k": "dashed",
    "pva_est_backward_o2_k": "dotted",
    "pva_est_centered_o2_km1": "dotted",
    "pva_pred_backward_o1_kp1": "dashed",
    "pva_pred_backward_o2_kp1": "dotted",
}
METHOD_ORDER = tuple(METHOD_LABELS)
PVA_METHODS = METHOD_ORDER[1:]

MAX_VELOCITY_RAD_S = 4.1
MAX_ACCELERATION_RAD_S2 = 8.2
PROJECTION_TOLERANCE = 1e-12
LIMIT_TOLERANCE = 1e-10


@dataclass(frozen=True)
class E08DashboardData:
    """Normalized E08 run outputs consumed by both dashboard renderers."""

    run_directory: Path
    generated_at: str
    reference_series: list[dict[str, Any]]
    position_series: list[dict[str, Any]]
    error_series: list[dict[str, Any]]
    target_audit: list[dict[str, Any]]
    projection_events: list[dict[str, Any]]
    acceptance_methods: list[dict[str, Any]]
    acceptance_candidates: list[dict[str, Any]]
    raw_feasibility: list[dict[str, Any]]
    overview_metrics: dict[str, Any]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _boolean(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _run_generated_at(run_directory: Path) -> str:
    stamp = run_directory.name.split("__", maxsplit=1)[0]
    try:
        parsed = datetime.strptime(stamp, "%Y%m%dT%H%M%S.%fZ")
        return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _projection_trigger(raw_velocity: float, raw_acceleration: float) -> str:
    if abs(raw_acceleration) > MAX_ACCELERATION_RAD_S2 + LIMIT_TOLERANCE:
        return "acceleration limit"
    if abs(raw_velocity) > MAX_VELOCITY_RAD_S + LIMIT_TOLERANCE:
        return "velocity limit"
    return "stopping envelope"


def _require_method(method_id: str) -> str:
    try:
        return METHOD_LABELS[method_id]
    except KeyError as error:
        raise ValueError(f"Unknown E08 method in run output: {method_id}") from error


def load_dashboard_data(run_directory: Path) -> E08DashboardData:
    """Load and normalize one completed E08 run without renderer dependencies."""

    run_directory = run_directory.resolve()
    analysis_directory = run_directory / "analysis"
    input_directory = run_directory / "inputs" / INPUT_ID
    generated_at = _run_generated_at(run_directory)

    reference_raw = _read_csv(input_directory / "reference.csv")
    if not reference_raw:
        raise ValueError("E08 reference.csv is empty")
    reference_by_index = {
        int(row["sample_index"]): float(row["position_rad"]) for row in reference_raw
    }
    reference_series = [
        {
            "sample_index": int(row["sample_index"]),
            "time_s": float(row["time_s"]),
            "series": REFERENCE_LABEL,
            "method_id": REFERENCE_METHOD_ID,
            "method_label": REFERENCE_LABEL,
            "position_rad": float(row["position_rad"]),
            "line_style": "solid",
            "source_row_count": len(reference_raw),
            "sampled": False,
        }
        for row in reference_raw
    ]
    position_series = list(reference_series)
    error_series: list[dict[str, Any]] = []
    target_audit: list[dict[str, Any]] = []
    projection_events: list[dict[str, Any]] = []

    for method_rank, method_id in enumerate(METHOD_ORDER):
        method_label = METHOD_LABELS[method_id]
        method_directory = run_directory / "methods" / method_id / INPUT_ID
        command_raw = _read_csv(method_directory / "command.csv")
        command_by_sample: dict[int, dict[str, Any]] = {}
        for row in command_raw:
            sample_index = int(row["sample_index"])
            position = float(row["position_rad"])
            reference_position = reference_by_index.get(sample_index)
            if reference_position is None:
                raise ValueError(
                    f"{method_id} command sample {sample_index} has no reference row"
                )
            common = {
                "sample_index": sample_index,
                "time_s": float(row["time_s"]),
                "series": method_label,
                "method_id": method_id,
                "method_label": method_label,
                "method_rank": method_rank,
                "line_style": LINE_STYLES[method_id],
                "source_row_count": len(command_raw),
                "sampled": False,
            }
            position_row = {
                **common,
                "position_rad": position,
                "reference_position_rad": reference_position,
            }
            error_row = {
                **common,
                "position_error_rad": position - reference_position,
            }
            position_series.append(position_row)
            error_series.append(error_row)
            command_by_sample[sample_index] = {
                **position_row,
                **error_row,
            }

        if method_id == BASELINE_METHOD_ID:
            continue
        trace_raw = _read_csv(method_directory / "trace.csv")
        for row in trace_raw:
            raw_velocity = _number(row.get("raw_target_velocity_rad_s"))
            raw_acceleration = _number(row.get("raw_target_acceleration_rad_s2"))
            executable_velocity = _number(
                row.get("executable_target_velocity_rad_s")
            )
            executable_acceleration = _number(
                row.get("executable_target_acceleration_rad_s2")
            )
            values = (
                raw_velocity,
                raw_acceleration,
                executable_velocity,
                executable_acceleration,
            )
            if any(value is None for value in values):
                continue
            assert raw_velocity is not None
            assert raw_acceleration is not None
            assert executable_velocity is not None
            assert executable_acceleration is not None

            cycle_index = int(row["cycle_index"])
            sample_index = cycle_index + 1
            command = command_by_sample.get(sample_index)
            if command is None:
                continue
            projected = (
                abs(raw_velocity - executable_velocity) > PROJECTION_TOLERANCE
                or abs(raw_acceleration - executable_acceleration)
                > PROJECTION_TOLERANCE
            )
            trigger = (
                _projection_trigger(raw_velocity, raw_acceleration)
                if projected
                else "pass through"
            )
            audit_row = {
                "cycle_index": cycle_index,
                "sample_index": sample_index,
                "measurement_time_s": float(row["measurement_time_s"]),
                "time_s": float(row["command_time_s"]),
                "method_id": method_id,
                "method_label": method_label,
                "method_rank": method_rank,
                "trigger": trigger,
                "projected": projected,
                "raw_position_rad": _number(row.get("raw_target_position_rad")),
                "raw_velocity_rad_s": raw_velocity,
                "raw_acceleration_rad_s2": raw_acceleration,
                "executable_position_rad": _number(
                    row.get("executable_target_position_rad")
                ),
                "executable_velocity_rad_s": executable_velocity,
                "executable_acceleration_rad_s2": executable_acceleration,
                "velocity_projection_rad_s": executable_velocity - raw_velocity,
                "acceleration_projection_rad_s2": (
                    executable_acceleration - raw_acceleration
                ),
                "command_position_rad": command["position_rad"],
                "reference_position_rad": command["reference_position_rad"],
                "position_error_rad": command["position_error_rad"],
            }
            target_audit.append(audit_row)
            if projected:
                projection_events.append(dict(audit_row))

    acceptance_methods: list[dict[str, Any]] = []
    for row in _read_csv(analysis_directory / "acceptance.csv"):
        method_id = row["method_id"]
        method_label = _require_method(method_id)
        acceptance_methods.append(
            {
                "method_id": method_id,
                "method_label": method_label,
                "completed": _boolean(row.get("completed")),
                "valid_cycles": int(row["valid_cycles"]),
                "total_cycles": int(row["total_cycles"]),
                "position_rmse_rad": _number(row.get("position_rmse_rad")),
                "rmse_ratio_vs_p": _number(row.get("rmse_ratio_vs_p")),
                "projection_count": int(row["projection_count"]),
                "projection_rate": float(row["projection_rate"]),
                "first_projection_cycle_index": _integer(
                    row.get("first_projection_cycle_index")
                ),
                "guardrail_pass": _boolean(row.get("guardrail_pass")),
                "scientific_status": row.get("scientific_status", ""),
            }
        )
    acceptance_candidates = [
        row
        for row in acceptance_methods
        if row["method_id"] != BASELINE_METHOD_ID
    ]
    acceptance_candidates.sort(
        key=lambda row: (
            row["rmse_ratio_vs_p"] is None,
            row["rmse_ratio_vs_p"] or float("inf"),
        )
    )

    raw_feasibility: list[dict[str, Any]] = []
    for row in _read_csv(analysis_directory / "raw_target_feasibility.csv"):
        method_id = row["method_id"]
        if method_id == BASELINE_METHOD_ID:
            continue
        raw_feasibility.append(
            {
                "method_id": method_id,
                "method_label": _require_method(method_id),
                "target_velocity_max_abs_rad_s": _number(
                    row.get("target_velocity_max_abs_rad_s")
                ),
                "target_acceleration_max_abs_rad_s2": _number(
                    row.get("target_acceleration_max_abs_rad_s2")
                ),
                "target_acceleration_p95_abs_rad_s2": _number(
                    row.get("target_acceleration_p95_abs_rad_s2")
                ),
                "velocity_limit_violation_count": int(
                    row["velocity_limit_violation_count"]
                ),
                "acceleration_limit_violation_count": int(
                    row["acceleration_limit_violation_count"]
                ),
                "ruckig_inadmissible_count": int(row["ruckig_inadmissible_count"]),
                "first_inadmissible_cycle_index": _integer(
                    row.get("first_inadmissible_cycle_index")
                ),
            }
        )

    baseline = next(
        (
            row
            for row in acceptance_methods
            if row["method_id"] == BASELINE_METHOD_ID
        ),
        None,
    )
    if baseline is None:
        raise ValueError("E08 acceptance.csv has no P-only baseline row")
    best_candidate = next(
        (
            row
            for row in acceptance_candidates
            if row["rmse_ratio_vs_p"] is not None
        ),
        None,
    )
    overview_metrics = {
        "duration_s": float(reference_raw[-1]["time_s"]),
        "tracking_cycles": int(baseline["total_cycles"]),
        "baseline_rmse_rad": baseline["position_rmse_rad"],
        "best_pva_ratio": (
            None if best_candidate is None else best_candidate["rmse_ratio_vs_p"]
        ),
        "best_pva_method": (
            None if best_candidate is None else best_candidate["method_label"]
        ),
        "projection_event_count": len(projection_events),
        "completed_method_count": sum(
            1 for row in acceptance_methods if row["completed"]
        ),
    }
    return E08DashboardData(
        run_directory=run_directory,
        generated_at=generated_at,
        reference_series=reference_series,
        position_series=position_series,
        error_series=error_series,
        target_audit=target_audit,
        projection_events=projection_events,
        acceptance_methods=acceptance_methods,
        acceptance_candidates=acceptance_candidates,
        raw_feasibility=raw_feasibility,
        overview_metrics=overview_metrics,
    )


__all__ = [
    "BASELINE_METHOD_ID",
    "E08DashboardData",
    "INPUT_ID",
    "LINE_STYLES",
    "MAX_ACCELERATION_RAD_S2",
    "MAX_VELOCITY_RAD_S",
    "METHOD_LABELS",
    "METHOD_ORDER",
    "PVA_METHODS",
    "REFERENCE_LABEL",
    "REFERENCE_METHOD_ID",
    "load_dashboard_data",
]
