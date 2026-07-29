"""Build a bounded portable Data Analytics dashboard for one E08 run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dashboard_data import (
    INPUT_ID,
    METHOD_LABELS,
    REFERENCE_LABEL,
    load_dashboard_data,
)


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
    dashboard_data = load_dashboard_data(run_directory)
    generated_at = dashboard_data.generated_at

    position_overview: list[dict[str, Any]] = []
    for series_label in (REFERENCE_LABEL, *METHOD_LABELS.values()):
        rows = [
            row
            for row in dashboard_data.position_series
            if row["series"] == series_label
        ]
        position_overview.extend(
            {
                **row,
                "sampled": True,
            }
            for row in _minmax_downsample(
                rows,
                value_field="position_rad",
                target_count=240,
            )
        )

    error_overview: list[dict[str, Any]] = []
    for series_label in METHOD_LABELS.values():
        rows = [
            row
            for row in dashboard_data.error_series
            if row["series"] == series_label
        ]
        error_overview.extend(
            {
                **row,
                "sampled": True,
            }
            for row in _minmax_downsample(
                rows,
                value_field="position_error_rad",
                target_count=280,
            )
        )

    projection_events = list(dashboard_data.projection_events)
    acceptance_methods = list(dashboard_data.acceptance_methods)
    acceptance_candidates = list(dashboard_data.acceptance_candidates)
    raw_feasibility = list(dashboard_data.raw_feasibility)
    overview_metrics = [dict(dashboard_data.overview_metrics)]
    best_candidate = acceptance_candidates[0]

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
