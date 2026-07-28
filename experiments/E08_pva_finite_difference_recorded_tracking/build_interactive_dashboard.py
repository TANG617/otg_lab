"""Build a bounded portable Data Analytics dashboard for one E08 run."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
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


def _minmax_downsample(
    rows: list[dict[str, Any]],
    *,
    value_field: str,
    target_count: int,
) -> list[dict[str, Any]]:
    """Keep first/last plus ordered min/max values from deterministic buckets."""

    if len(rows) <= target_count:
        return rows
    bucket_count = max(1, (target_count - 2) // 2)
    interior = rows[1:-1]
    sampled = [rows[0]]
    for bucket_index in range(bucket_count):
        start = bucket_index * len(interior) // bucket_count
        end = (bucket_index + 1) * len(interior) // bucket_count
        bucket = interior[start:end]
        if not bucket:
            continue
        candidates = {
            min(
                range(len(bucket)),
                key=lambda index: float(bucket[index][value_field]),
            ),
            max(
                range(len(bucket)),
                key=lambda index: float(bucket[index][value_field]),
            ),
        }
        sampled.extend(bucket[index] for index in sorted(candidates))
    sampled.append(rows[-1])
    return sampled


def _run_generated_at(run_directory: Path) -> str:
    stamp = run_directory.name.split("__", maxsplit=1)[0]
    try:
        parsed = datetime.strptime(stamp, "%Y%m%dT%H%M%S.%fZ")
        return (
            parsed.replace(tzinfo=timezone.utc)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )
    except ValueError:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source(
    source_id: str,
    label: str,
    path: str,
    description: str,
    generated_at: str,
    *,
    sql: str,
    tables_used: list[str],
    definitions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "label": label,
        "path": path,
        "query": {
            "description": description,
            "executed_at": generated_at,
            "engine": "duckdb",
            "language": "sql",
            "sql": sql,
            "tables_used": tables_used,
            "filters": [
                "input_id = recorded_tasks_simplified_with_velocity_limit",
                "main evaluation starts at t = 0.04 s",
            ],
            "metric_definitions": definitions or [],
        },
    }


def build_artifact(run_directory: Path) -> dict[str, Any]:
    analysis_directory = run_directory / "analysis"
    input_directory = run_directory / "inputs" / INPUT_ID
    generated_at = _run_generated_at(run_directory)

    acceptance_raw = _read_csv(analysis_directory / "acceptance.csv")
    feasibility_raw = _read_csv(analysis_directory / "raw_target_feasibility.csv")
    reference_raw = _read_csv(input_directory / "reference.csv")
    reference_by_index = {
        int(row["sample_index"]): float(row["position_rad"]) for row in reference_raw
    }

    reference_series = [
        {
            "sample_index": int(row["sample_index"]),
            "time_s": float(row["time_s"]),
            "series": "Recorded reference",
            "method_id": "recorded_reference",
            "position_rad": float(row["position_rad"]),
            "line_style": "solid",
            "source_row_count": len(reference_raw),
            "sampled": True,
        }
        for row in reference_raw
    ]
    position_overview = _minmax_downsample(
        reference_series,
        value_field="position_rad",
        target_count=240,
    )
    error_overview: list[dict[str, Any]] = []
    projection_events: list[dict[str, Any]] = []

    for method_rank, method_id in enumerate(METHOD_ORDER):
        method_directory = run_directory / "methods" / method_id / INPUT_ID
        command_raw = _read_csv(method_directory / "command.csv")
        command_series: list[dict[str, Any]] = []
        error_series: list[dict[str, Any]] = []
        for row in command_raw:
            sample_index = int(row["sample_index"])
            position = float(row["position_rad"])
            common = {
                "sample_index": sample_index,
                "time_s": float(row["time_s"]),
                "series": METHOD_LABELS[method_id],
                "method_id": method_id,
                "line_style": LINE_STYLES[method_id],
                "source_row_count": len(command_raw),
                "sampled": True,
            }
            command_series.append(
                {
                    **common,
                    "position_rad": position,
                }
            )
            error_series.append(
                {
                    **common,
                    "position_error_rad": (position - reference_by_index[sample_index]),
                }
            )
        position_overview.extend(
            _minmax_downsample(
                command_series,
                value_field="position_rad",
                target_count=240,
            )
        )
        error_overview.extend(
            _minmax_downsample(
                error_series,
                value_field="position_error_rad",
                target_count=280,
            )
        )

        if method_id == BASELINE_METHOD_ID:
            continue
        trace_raw = _read_csv(method_directory / "trace.csv")
        for row in trace_raw:
            raw_velocity = _number(row["raw_target_velocity_rad_s"])
            raw_acceleration = _number(row["raw_target_acceleration_rad_s2"])
            executable_velocity = _number(row["executable_target_velocity_rad_s"])
            executable_acceleration = _number(
                row["executable_target_acceleration_rad_s2"]
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
            if (
                abs(raw_velocity - executable_velocity) <= 1e-12
                and abs(raw_acceleration - executable_acceleration) <= 1e-12
            ):
                continue
            if abs(raw_acceleration) > 8.2 + 1e-10:
                trigger = "acceleration limit"
            elif abs(raw_velocity) > 4.1 + 1e-10:
                trigger = "velocity limit"
            else:
                trigger = "stopping envelope"
            projection_events.append(
                {
                    "cycle_index": int(row["cycle_index"]),
                    "time_s": float(row["command_time_s"]),
                    "method_id": method_id,
                    "method_label": METHOD_LABELS[method_id],
                    "method_rank": method_rank,
                    "trigger": trigger,
                    "raw_velocity_rad_s": raw_velocity,
                    "raw_acceleration_rad_s2": raw_acceleration,
                    "executable_velocity_rad_s": executable_velocity,
                    "executable_acceleration_rad_s2": (executable_acceleration),
                    "velocity_projection_rad_s": (executable_velocity - raw_velocity),
                    "acceleration_projection_rad_s2": (
                        executable_acceleration - raw_acceleration
                    ),
                }
            )

    acceptance_methods: list[dict[str, Any]] = []
    for row in acceptance_raw:
        method_id = row["method_id"]
        acceptance_methods.append(
            {
                "method_id": method_id,
                "method_label": METHOD_LABELS[method_id],
                "completed": _boolean(row["completed"]),
                "valid_cycles": int(row["valid_cycles"]),
                "total_cycles": int(row["total_cycles"]),
                "position_rmse_rad": float(row["position_rmse_rad"]),
                "rmse_ratio_vs_p": _number(row["rmse_ratio_vs_p"]),
                "projection_count": int(row["projection_count"]),
                "projection_rate": float(row["projection_rate"]),
                "first_projection_cycle_index": _integer(
                    row["first_projection_cycle_index"]
                ),
                "guardrail_pass": _boolean(row["guardrail_pass"]),
                "scientific_status": row["scientific_status"],
            }
        )
    acceptance_candidates = [
        row for row in acceptance_methods if row["method_id"] != BASELINE_METHOD_ID
    ]
    acceptance_candidates.sort(key=lambda row: float(row["rmse_ratio_vs_p"]))

    raw_feasibility: list[dict[str, Any]] = []
    for row in feasibility_raw:
        method_id = row["method_id"]
        if method_id == BASELINE_METHOD_ID:
            continue
        raw_feasibility.append(
            {
                "method_id": method_id,
                "method_label": METHOD_LABELS[method_id],
                "target_velocity_max_abs_rad_s": float(
                    row["target_velocity_max_abs_rad_s"]
                ),
                "target_acceleration_max_abs_rad_s2": float(
                    row["target_acceleration_max_abs_rad_s2"]
                ),
                "target_acceleration_p95_abs_rad_s2": float(
                    row["target_acceleration_p95_abs_rad_s2"]
                ),
                "velocity_limit_violation_count": int(
                    row["velocity_limit_violation_count"]
                ),
                "acceleration_limit_violation_count": int(
                    row["acceleration_limit_violation_count"]
                ),
                "ruckig_inadmissible_count": int(row["ruckig_inadmissible_count"]),
                "first_inadmissible_cycle_index": int(
                    row["first_inadmissible_cycle_index"]
                ),
            }
        )

    baseline = next(
        row for row in acceptance_methods if row["method_id"] == BASELINE_METHOD_ID
    )
    best_candidate = acceptance_candidates[0]
    overview_metrics = [
        {
            "duration_s": float(reference_raw[-1]["time_s"]),
            "tracking_cycles": int(baseline["total_cycles"]),
            "baseline_rmse_rad": float(baseline["position_rmse_rad"]),
            "best_pva_ratio": float(best_candidate["rmse_ratio_vs_p"]),
            "projection_event_count": len(projection_events),
            "completed_method_count": sum(
                1 for row in acceptance_methods if row["completed"]
            ),
        }
    ]

    sources = [
        _source(
            "source_reference",
            "Recorded position reference",
            f"inputs/{INPUT_ID}/reference.csv",
            "Canonical 10 ms position-only reference used by E08.",
            generated_at,
            sql=(f"SELECT * FROM read_csv_auto('inputs/{INPUT_ID}/reference.csv')"),
            tables_used=[f"inputs/{INPUT_ID}/reference.csv"],
        ),
        _source(
            "source_tracking",
            "Tracking commands",
            f"methods/*/{INPUT_ID}/command.csv",
            "Per-method command trajectories; overview lines use deterministic "
            "ordered min/max downsampling.",
            generated_at,
            sql=(
                "SELECT * FROM read_csv_auto("
                f"'methods/*/{INPUT_ID}/command.csv', filename = true)"
            ),
            tables_used=[f"methods/*/{INPUT_ID}/command.csv"],
            definitions=[
                "Position error = command position − reference position at the "
                "same canonical sample index.",
            ],
        ),
        _source(
            "source_trace",
            "Tracking traces",
            f"methods/*/{INPUT_ID}/trace.csv",
            "Per-cycle raw and executable targets used to identify every "
            "configured-limit projection.",
            generated_at,
            sql=(
                "SELECT * FROM read_csv_auto("
                f"'methods/*/{INPUT_ID}/trace.csv', filename = true)"
            ),
            tables_used=[f"methods/*/{INPUT_ID}/trace.csv"],
            definitions=[
                "Projection event: raw and executable target V or A differ by "
                "more than 1e-12.",
            ],
        ),
        _source(
            "source_acceptance",
            "E08 acceptance results",
            "analysis/acceptance.csv",
            "Full-run completion, RMSE, projection, and guardrail results.",
            generated_at,
            sql=("SELECT * FROM read_csv_auto('analysis/acceptance.csv')"),
            tables_used=["analysis/acceptance.csv"],
            definitions=[
                "RMSE ratio vs P-only = candidate main-window position RMSE / "
                "P-only main-window position RMSE.",
                "Transfer pass requires completion, RMSE ratio < 1, and no "
                "guardrail regression.",
            ],
        ),
        _source(
            "source_feasibility",
            "Raw-target feasibility",
            "analysis/raw_target_feasibility.csv",
            "Unprojected target V/A peaks and limit-violation counts.",
            generated_at,
            sql=("SELECT * FROM read_csv_auto('analysis/raw_target_feasibility.csv')"),
            tables_used=["analysis/raw_target_feasibility.csv"],
        ),
    ]

    cards = [
        {
            "id": "card_duration",
            "dataset": "overview_metrics",
            "sourceId": "source_reference",
            "description": "Full canonical fixed-grid replay duration.",
            "metrics": [
                {
                    "label": "Duration [s]",
                    "field": "duration_s",
                    "format": "number",
                }
            ],
        },
        {
            "id": "card_cycles",
            "dataset": "overview_metrics",
            "sourceId": "source_tracking",
            "description": "Committed command cycles per completed method.",
            "metrics": [
                {
                    "label": "Tracking cycles",
                    "field": "tracking_cycles",
                    "format": "number",
                }
            ],
        },
        {
            "id": "card_baseline_rmse",
            "dataset": "overview_metrics",
            "sourceId": "source_acceptance",
            "description": "P-only raw-time main-window position RMSE.",
            "metrics": [
                {
                    "label": "P-only RMSE [rad]",
                    "field": "baseline_rmse_rad",
                    "format": "number",
                }
            ],
        },
        {
            "id": "card_best_pva",
            "dataset": "overview_metrics",
            "sourceId": "source_acceptance",
            "description": (
                f"Lowest candidate ratio: {best_candidate['method_label']}."
            ),
            "metrics": [
                {
                    "label": "Best PVA RMSE ratio",
                    "field": "best_pva_ratio",
                    "format": "number",
                }
            ],
        },
        {
            "id": "card_projection_events",
            "dataset": "overview_metrics",
            "sourceId": "source_trace",
            "description": "All raw-to-executable target projection cycles.",
            "metrics": [
                {
                    "label": "Projection events",
                    "field": "projection_event_count",
                    "format": "number",
                }
            ],
        },
    ]

    common_chart_surface = {
        "surface": "explorer",
        "interactiveLegend": True,
        "showControls": True,
        "viewMode": "visualization",
    }
    charts = [
        {
            "id": "chart_position",
            "title": "Position reference and tracking commands",
            "subtitle": (
                "Toggle series in the legend; the full 76.72 s shape is "
                "preserved with ordered min/max downsampling."
            ),
            "intent": "trend",
            "question": (
                "Where do the P-only and PVA command trajectories diverge "
                "from the recorded position waveform?"
            ),
            "rationale": (
                "A multi-series line chart preserves temporal shape and makes "
                "method-level divergence directly comparable."
            ),
            "type": "line",
            "dataset": "position_overview",
            "sourceId": "source_tracking",
            "encodings": {
                "x": {
                    "field": "time_s",
                    "type": "quantitative",
                    "label": "Time",
                    "unit": "s",
                },
                "y": {
                    "field": "position_rad",
                    "type": "quantitative",
                    "label": "Position",
                    "unit": "rad",
                },
                "color": {"field": "series", "type": "nominal"},
                "lineStyle": {
                    "field": "line_style",
                    "type": "nominal",
                },
                "tooltip": [
                    {"field": "sample_index", "type": "quantitative"},
                    {"field": "method_id", "type": "text"},
                ],
            },
            "xAxisTitle": "Time [s]",
            "yAxisTitle": "Position [rad]",
            "unit": "rad",
            "layout": "full",
            "combinationRationale": (
                "Color identifies method while line style remains a redundant "
                "non-color distinction."
            ),
            "legend": {
                "position": "bottom",
                "sort": "spec",
                "title": "Series",
            },
            "maxRows": 1800,
            "palette": {"kind": "categorical"},
            "settings": {"showPoints": "never"},
            "surface": common_chart_surface,
        },
        {
            "id": "chart_error",
            "title": "Raw-time position error",
            "subtitle": (
                "Command minus reference on the canonical grid; legend "
                "controls isolate individual methods."
            ),
            "intent": "trend",
            "question": (
                "When and by how much does each method depart from the "
                "recorded reference?"
            ),
            "rationale": (
                "A zero-centered line chart exposes short error bursts that "
                "are visually compressed in the position overlay."
            ),
            "type": "line",
            "dataset": "error_overview",
            "sourceId": "source_tracking",
            "encodings": {
                "x": {
                    "field": "time_s",
                    "type": "quantitative",
                    "label": "Time",
                    "unit": "s",
                },
                "y": {
                    "field": "position_error_rad",
                    "type": "quantitative",
                    "label": "Position error",
                    "unit": "rad",
                },
                "color": {"field": "series", "type": "nominal"},
                "lineStyle": {
                    "field": "line_style",
                    "type": "nominal",
                },
                "tooltip": [
                    {"field": "sample_index", "type": "quantitative"},
                    {"field": "method_id", "type": "text"},
                ],
            },
            "xAxisTitle": "Time [s]",
            "yAxisTitle": "Command − reference [rad]",
            "unit": "rad",
            "layout": "full",
            "combinationRationale": (
                "All series share the same time grid, error definition, and "
                "physical unit."
            ),
            "legend": {
                "position": "bottom",
                "sort": "spec",
                "title": "Method",
            },
            "referenceLines": [
                {
                    "axis": "y",
                    "value": 0,
                    "label": "Zero error",
                    "color": "neutral",
                    "lineStyle": "solid",
                }
            ],
            "maxRows": 1800,
            "palette": {"kind": "categorical"},
            "settings": {"showPoints": "never"},
            "surface": common_chart_surface,
        },
        {
            "id": "chart_rmse_ratio",
            "title": "PVA position RMSE ratio versus P-only",
            "subtitle": (
                "Values below 1 improve on P-only; all five methods complete "
                "the full evaluation window."
            ),
            "intent": "comparison",
            "question": (
                "Which PVA method is closest to or better than the P-only "
                "main-window RMSE?"
            ),
            "rationale": (
                "A sorted bar chart makes the candidate ranking and the "
                "acceptance threshold immediately visible."
            ),
            "type": "bar",
            "dataset": "acceptance_candidates",
            "sourceId": "source_acceptance",
            "encodings": {
                "x": {
                    "field": "method_label",
                    "type": "nominal",
                    "label": "PVA method",
                },
                "y": {
                    "field": "rmse_ratio_vs_p",
                    "type": "quantitative",
                    "label": "RMSE ratio",
                },
                "tooltip": [
                    {"field": "position_rmse_rad", "type": "quantitative"},
                    {"field": "projection_count", "type": "quantitative"},
                    {"field": "scientific_status", "type": "text"},
                ],
            },
            "xAxisTitle": "PVA method",
            "yAxisTitle": "RMSE ratio vs P-only",
            "valueFormat": "number",
            "layout": "half",
            "referenceLines": [
                {
                    "axis": "y",
                    "value": 1,
                    "label": "P-only",
                    "color": "neutral",
                    "lineStyle": "dashed",
                }
            ],
            "labels": {"values": "all"},
            "palette": {"kind": "sequential", "name": "blue"},
            "settings": {
                "categoryLabelPolicy": "wrap",
                "showValues": True,
                "sort": "ascending",
            },
            "surface": common_chart_surface,
        },
        {
            "id": "chart_projection_events",
            "title": "Target projection events over time",
            "subtitle": (
                "Every projected cycle is retained; color and hover identify "
                "the method and raw/executable V/A values."
            ),
            "intent": "custom",
            "question": (
                "When do configured-limit target projections occur for each PVA method?"
            ),
            "rationale": (
                "A method-row scatter timeline preserves all discrete events "
                "without obscuring the position and error curves."
            ),
            "type": "scatter",
            "dataset": "projection_events",
            "sourceId": "source_trace",
            "encodings": {
                "x": {
                    "field": "time_s",
                    "type": "quantitative",
                    "label": "Command time",
                    "unit": "s",
                },
                "y": {
                    "field": "method_rank",
                    "type": "quantitative",
                    "label": "Method row",
                },
                "color": {
                    "field": "method_label",
                    "type": "nominal",
                },
                "label": {
                    "field": "method_label",
                    "type": "text",
                },
                "tooltip": [
                    {"field": "cycle_index", "type": "quantitative"},
                    {"field": "trigger", "type": "text"},
                    {
                        "field": "raw_velocity_rad_s",
                        "type": "quantitative",
                    },
                    {
                        "field": "raw_acceleration_rad_s2",
                        "type": "quantitative",
                    },
                    {
                        "field": "executable_velocity_rad_s",
                        "type": "quantitative",
                    },
                    {
                        "field": "executable_acceleration_rad_s2",
                        "type": "quantitative",
                    },
                ],
            },
            "xAxisTitle": "Command time [s]",
            "yAxisTitle": "PVA method row (1–5)",
            "layout": "half",
            "combinationRationale": (
                "Method row supplies spatial separation while color and hover "
                "provide method identity."
            ),
            "legend": {
                "position": "bottom",
                "sort": "spec",
                "title": "PVA method",
            },
            "maxRows": 1200,
            "palette": {"kind": "categorical"},
            "surface": common_chart_surface,
        },
    ]

    tables = [
        {
            "id": "table_acceptance",
            "title": "PVA method results",
            "subtitle": (
                "Exact full-window RMSE, projection, completion, and guardrail results."
            ),
            "dataset": "acceptance_candidates",
            "sourceId": "source_acceptance",
            "defaultSort": {
                "field": "rmse_ratio_vs_p",
                "direction": "asc",
            },
            "density": "dense",
            "layout": "full",
            "columns": [
                {
                    "field": "method_label",
                    "label": "Method",
                    "type": "text",
                },
                {
                    "field": "position_rmse_rad",
                    "label": "RMSE [rad]",
                    "format": "number",
                },
                {
                    "field": "rmse_ratio_vs_p",
                    "label": "RMSE / P",
                    "format": "number",
                },
                {
                    "field": "projection_count",
                    "label": "Projections",
                    "format": "number",
                },
                {
                    "field": "projection_rate",
                    "label": "Projection rate",
                    "format": "percent",
                },
                {
                    "field": "first_projection_cycle_index",
                    "label": "First cycle",
                    "format": "number",
                },
                {
                    "field": "guardrail_pass",
                    "label": "Guardrails",
                    "type": "text",
                },
                {
                    "field": "scientific_status",
                    "label": "Result",
                    "type": "text",
                },
            ],
        },
        {
            "id": "table_feasibility",
            "title": "Unprojected target feasibility",
            "subtitle": (
                "Raw target peaks and violation counts before configured-limit "
                "projection."
            ),
            "dataset": "raw_feasibility",
            "sourceId": "source_feasibility",
            "defaultSort": {
                "field": "ruckig_inadmissible_count",
                "direction": "desc",
            },
            "density": "dense",
            "layout": "full",
            "columns": [
                {
                    "field": "method_label",
                    "label": "Method",
                    "type": "text",
                },
                {
                    "field": "target_velocity_max_abs_rad_s",
                    "label": "Max |V| [rad/s]",
                    "format": "number",
                },
                {
                    "field": "target_acceleration_max_abs_rad_s2",
                    "label": "Max |A| [rad/s²]",
                    "format": "number",
                },
                {
                    "field": "target_acceleration_p95_abs_rad_s2",
                    "label": "P95 |A| [rad/s²]",
                    "format": "number",
                },
                {
                    "field": "acceleration_limit_violation_count",
                    "label": "A violations",
                    "format": "number",
                },
                {
                    "field": "ruckig_inadmissible_count",
                    "label": "Inadmissible",
                    "format": "number",
                },
                {
                    "field": "first_inadmissible_cycle_index",
                    "label": "First cycle",
                    "format": "number",
                },
            ],
        },
    ]

    manifest = {
        "version": 1,
        "surface": "dashboard",
        "title": "E08 交互式轨迹追踪分析",
        "description": (
            "Recorded position tracking, raw-time error, RMSE comparison, "
            "and all configured-limit projection events."
        ),
        "generatedAt": generated_at,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "sources": sources,
        "blocks": [
            {
                "id": "usage_note",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "### 使用方式\n"
                    "在图例中开关方法，悬停查看精确值；图表菜单可切换图形或"
                    "数据表。投影事件时间轴保留每个投影周期，position/error "
                    "曲线则使用确定性的有序 min/max 采样以保持关键峰谷。"
                ),
            },
            {
                "id": "headline_metrics",
                "type": "metric-strip",
                "layout": "full",
                "cardIds": [card["id"] for card in cards],
            },
            {
                "id": "position_block",
                "type": "chart",
                "layout": "full",
                "chartId": "chart_position",
            },
            {
                "id": "error_block",
                "type": "chart",
                "layout": "full",
                "chartId": "chart_error",
            },
            {
                "id": "rmse_block",
                "type": "chart",
                "layout": "half",
                "chartId": "chart_rmse_ratio",
            },
            {
                "id": "projection_block",
                "type": "chart",
                "layout": "half",
                "chartId": "chart_projection_events",
            },
            {
                "id": "acceptance_block",
                "type": "table",
                "layout": "full",
                "tableId": "table_acceptance",
            },
            {
                "id": "feasibility_block",
                "type": "table",
                "layout": "full",
                "tableId": "table_feasibility",
            },
            {
                "id": "scope_note",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "### 范围说明\n"
                    "这是当前 E08 run 的只读快照，不是实时机器人反馈。"
                    "所有验收指标与投影事件保持精确；仅全程 position/error "
                    "折线为浏览器可读性做了确定性采样。"
                ),
            },
        ],
    }

    snapshot = {
        "version": 1,
        "generatedAt": generated_at,
        "status": "ready",
        "datasets": {
            "overview_metrics": overview_metrics,
            "position_overview": position_overview,
            "error_overview": error_overview,
            "acceptance_candidates": acceptance_candidates,
            "projection_events": projection_events,
            "acceptance_methods": acceptance_methods,
            "raw_feasibility": raw_feasibility,
        },
    }
    return {
        "surface": "dashboard",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Artifact JSON path; defaults to analysis/interactive/artifact.json",
    )
    arguments = parser.parse_args()
    run_directory = arguments.run_directory.resolve()
    output = arguments.output
    if output is None:
        output = run_directory / "analysis" / "interactive" / "artifact.json"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact = build_artifact(run_directory)
    output.write_text(
        json.dumps(artifact, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
