"""Build the canonical Data Analytics artifact for the three-CSV comparison."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results" / "csv_pvaj_tracking_comparison"
REPORT_TITLE = "Three-CSV PVAJ and Tracking Comparison"

METRIC_SQL = """
SELECT
    metric_group, metric, label, unit, preferred_direction,
    current_csv, no_velocity_limit, velocity_limit,
    no_limit_vs_current_change_pct,
    velocity_limit_vs_current_change_pct,
    velocity_limit_vs_no_limit_change_pct
FROM metric_comparison
ORDER BY source_row_order
""".strip()

METHOD_SQL = """
SELECT
    method_id, method, result_group, causal,
    current_rmse, no_velocity_limit_rmse, velocity_limit_rmse,
    velocity_limit_vs_no_limit_rmse_change_pct,
    current_normalized_rmse_robust,
    no_velocity_limit_normalized_rmse_robust,
    velocity_limit_normalized_rmse_robust,
    velocity_limit_vs_no_limit_normalized_rmse_change_pct,
    current_abs_best_lag_ms,
    no_velocity_limit_abs_best_lag_ms,
    velocity_limit_abs_best_lag_ms,
    current_target_projection_rate,
    no_velocity_limit_target_projection_rate,
    velocity_limit_target_projection_rate,
    current_reachable_within_10ms_rate,
    no_velocity_limit_reachable_within_10ms_rate,
    velocity_limit_reachable_within_10ms_rate
FROM tracking_method_comparison
ORDER BY source_row_order
""".strip()

WINDOW_SQL = """
SELECT
    dataset, label, window_index, start_index, stop_index_exclusive,
    start_time_s, stop_time_s, sample_count, position_range_rad,
    tracking_rmse_rad, tracking_max_error_rad,
    max_abs_velocity, rms_velocity,
    max_abs_acceleration, rms_acceleration,
    max_abs_jerk, rms_jerk
FROM window_diagnostics
ORDER BY source_row_order
""".strip()

RELATIONSHIP_SQL = """
SELECT
    scope, window_samples, window_duration_s, window_count,
    demand_metric, tracking_metric, spearman_rho_descriptive,
    naive_p_value_not_for_inference, inference_status
FROM window_relationships
ORDER BY source_row_order
""".strip()

TRACE_SQL = """
SELECT
    dataset, label, rows, topic_count,
    source_duration_s, fixed_grid_duration_s,
    position_start_rad, position_end_rad,
    position_min_rad, position_max_rad,
    position_range_rad, position_robust_scale_rad,
    source_dt_min_ms, source_dt_p01_ms, source_dt_p50_ms,
    source_dt_p99_ms, source_dt_max_ms,
    source_dt_within_5_to_15ms_rate,
    clock_offset_residual_max_us, exact_position_repeat_rate
FROM trace_quality
ORDER BY source_row_order
""".strip()


def _read_csv(path):
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _build_report_database(results_dir):
    database_path = Path(results_dir) / "report_source.sqlite"
    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    sources = (
        ("metric_comparison", "metric_comparison.csv"),
        ("tracking_method_comparison", "tracking_method_comparison.csv"),
        ("window_diagnostics", "window_diagnostics.csv"),
        ("window_relationships", "window_relationships.csv"),
        ("trace_quality", "trace_quality.csv"),
    )
    for table, filename in sources:
        rows = _read_csv(Path(results_dir) / filename)
        if not rows:
            raise ValueError(f"cannot load empty report source: {filename}")
        columns = list(rows[0])
        declarations = ", ".join(f'"{column}" TEXT NOT NULL' for column in columns)
        connection.execute(
            f'CREATE TABLE "{table}" '
            f"(source_row_order INTEGER PRIMARY KEY, {declarations})"
        )
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        connection.executemany(
            f'INSERT INTO "{table}" VALUES ({placeholders})',
            [
                (order, *(row[column] for column in columns))
                for order, row in enumerate(rows)
            ],
        )
    connection.commit()
    return connection, database_path


def _query_rows(connection, query):
    cursor = connection.execute(query)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _float(row, field):
    return float(row[field])


def _metric_lookup(rows):
    return {row["metric"]: row for row in rows}


def _percent(value):
    return f"{value:+.1f}%"


def _source(source_id, label, sql, description, table, metric_definitions=None):
    return {
        "id": source_id,
        "label": label,
        "path": "results/csv_pvaj_tracking_comparison/report_source.sqlite",
        "query": {
            "description": description,
            "engine": "SQLite 3",
            "language": "sql",
            "sql": sql,
            "filters": [
                "All three traces use a fixed 10 ms row period",
                "PVAJ summaries exclude the first and last three samples",
                "Tracking metrics exclude the first three warm-up samples",
            ],
            "metric_definitions": metric_definitions or [],
            "tables_used": [table],
        },
    }


def _metric_change(metrics, metric, comparison):
    return _float(metrics[metric], comparison)


def build_artifact(results_dir):
    results_dir = Path(results_dir).resolve()
    connection, database_path = _build_report_database(results_dir)
    try:
        metric_rows = _query_rows(connection, METRIC_SQL)
        method_rows = _query_rows(connection, METHOD_SQL)
        window_rows = _query_rows(connection, WINDOW_SQL)
        relationship_rows = _query_rows(connection, RELATIONSHIP_SQL)
        trace_rows = _query_rows(connection, TRACE_SQL)
    finally:
        connection.close()

    run_manifest = json.loads((results_dir / "run.json").read_text(encoding="utf-8"))
    generated_at = run_manifest["generated_at"]
    metrics = _metric_lookup(metric_rows)
    primary_change = "velocity_limit_vs_no_limit_change_pct"
    versus_current = "velocity_limit_vs_current_change_pct"

    max_velocity_change = _metric_change(
        metrics, "max_abs_velocity", primary_change
    )
    max_acceleration_change = _metric_change(
        metrics, "max_abs_acceleration", primary_change
    )
    max_jerk_change = _metric_change(metrics, "max_abs_jerk", primary_change)
    p99_velocity_change = _metric_change(
        metrics, "p99_abs_velocity", primary_change
    )
    p99_acceleration_change = _metric_change(
        metrics, "p99_abs_acceleration", primary_change
    )
    p99_jerk_change = _metric_change(metrics, "p99_abs_jerk", primary_change)
    nrmse_change = _metric_change(
        metrics, "normalized_rmse_robust", primary_change
    )
    max_error_change = _metric_change(
        metrics, "normalized_max_error_range", primary_change
    )
    lag_change = _metric_change(metrics, "abs_best_lag_ms", primary_change)
    duration_change = _metric_change(
        metrics, "fixed_grid_duration_s", primary_change
    )
    range_change = _metric_change(metrics, "position_range_rad", primary_change)

    relationship = next(
        row
        for row in relationship_rows
        if row["scope"] == "combined" and row["demand_metric"] == "rms_velocity"
    )
    velocity_rho = _float(relationship, "spearman_rho_descriptive")
    window_count = int(relationship["window_count"])

    sources = [
        _source(
            "comparison_metrics",
            "Three-CSV comparison metrics",
            METRIC_SQL,
            (
                "Three-way aggregate comparison generated by "
                "scripts/compare_csv_pvaj_tracking.py."
            ),
            "metric_comparison",
            [
                "Change (%) = 100 × (comparison − baseline) / |baseline|.",
                (
                    "Robust-scale NRMSE = tracking RMSE / "
                    "(reference P95 − reference P05)."
                ),
                (
                    "Maximum sampled V/A/J use a common fixed 10 ms grid "
                    "over indices [3, n−3)."
                ),
            ],
        ),
        _source(
            "method_metrics",
            "Tracking comparison across target-state methods",
            METHOD_SQL,
            (
                "Ordinary-Ruckig tracking under the same vendor limits for "
                "all seven real-CSV-compatible target-state methods."
            ),
            "tracking_method_comparison",
            [
                (
                    "Method ratio = velocity-limit robust-scale NRMSE / "
                    "no-velocity-limit robust-scale NRMSE."
                ),
                (
                    "Ordinary Ruckig uses vmax=4.1 rad/s, "
                    "amax=8.2 rad/s², jmax=4000 rad/s³."
                ),
            ],
        ),
        _source(
            "window_metrics",
            "One-second window diagnostics",
            WINDOW_SQL,
            (
                "Non-overlapping one-second windows from all three traces, "
                "using P-only tracking and fixed-grid sampled demand."
            ),
            "window_diagnostics",
            [
                "Window RMS velocity = sqrt(mean(v²)) over 100 samples.",
                (
                    "Window tracking RMSE = sqrt(mean((output − reference)²)) "
                    "over the same 100 samples."
                ),
            ],
        ),
        _source(
            "relationship_metrics",
            "Window-level descriptive relationships",
            RELATIONSHIP_SQL,
            (
                "Descriptive Spearman correlations between window demand "
                "and P-only tracking error."
            ),
            "window_relationships",
            [
                (
                    "Correlations are descriptive only because windows are "
                    "autocorrelated and nested within three traces."
                )
            ],
        ),
        _source(
            "trace_quality",
            "CSV source and timing profile",
            TRACE_SQL,
            (
                "Row count, duration, source-time jitter, topic count, "
                "position range, and clock-consistency checks."
            ),
            "trace_quality",
        ),
    ]

    headline_specs = (
        (
            "max_velocity",
            "Velocity-limit max |V|",
            "max_abs_velocity",
            (
                "Maximum sampled velocity on the fixed 10 ms grid; "
                "comparison is against the no-limit trace."
            ),
        ),
        (
            "max_acceleration",
            "Velocity-limit max |A|",
            "max_abs_acceleration",
            (
                "Maximum sampled acceleration on the fixed 10 ms grid; "
                "comparison is against the no-limit trace."
            ),
        ),
        (
            "max_jerk",
            "Velocity-limit max |J|",
            "max_abs_jerk",
            (
                "Maximum sampled jerk on the fixed 10 ms grid; comparison "
                "is against the no-limit trace."
            ),
        ),
        (
            "tracking_nrmse",
            "Velocity-limit P-only NRMSE",
            "normalized_rmse_robust",
            (
                "P-only tracking RMSE normalized by the velocity-limit "
                "trace's P95−P05 position scale."
            ),
        ),
    )
    headline_rows = [
        {
            "metric": metric_id,
            "value": _float(metrics[metric], "velocity_limit"),
            "vs_no_limit_change_pct": _float(metrics[metric], primary_change),
            "vs_current_change_pct": _float(metrics[metric], versus_current),
            "unit": metrics[metric]["unit"],
        }
        for metric_id, _label, metric, _description in headline_specs
    ]
    cards = [
        {
            "id": f"card_{metric_id}",
            "dataset": "headline_metrics",
            "filter": {"metric": metric_id},
            "description": description,
            "sourceId": "comparison_metrics",
            "metrics": [
                {
                    "label": label,
                    "field": "value",
                    "format": "number",
                },
                {
                    "label": "vs no limit (%)",
                    "field": "vs_no_limit_change_pct",
                    "format": "number",
                    "signed": True,
                },
                {
                    "label": "vs current (%)",
                    "field": "vs_current_change_pct",
                    "format": "number",
                    "signed": True,
                },
            ],
        }
        for metric_id, label, _metric, description in headline_specs
    ]

    key_metric_names = (
        "max_abs_velocity",
        "max_abs_acceleration",
        "max_abs_jerk",
        "normalized_rmse_robust",
        "normalized_max_error_range",
        "abs_best_lag_ms",
    )
    short_labels = {
        "max_abs_velocity": "Max |V|",
        "max_abs_acceleration": "Max |A|",
        "max_abs_jerk": "Max |J|",
        "normalized_rmse_robust": "P-only NRMSE",
        "normalized_max_error_range": "Normalized max error",
        "abs_best_lag_ms": "Absolute lag",
    }
    key_comparisons = []
    for metric in key_metric_names:
        row = metrics[metric]
        current = _float(row, "current_csv")
        for dataset, variant in (
            ("no_velocity_limit", "No velocity limit"),
            ("velocity_limit", "Velocity limit"),
        ):
            value = _float(row, dataset)
            key_comparisons.append(
                {
                    "metric": metric,
                    "label": short_labels[metric],
                    "variant": variant,
                    "value_to_current_ratio": value / current,
                    "current_value": current,
                    "variant_value": value,
                    "unit": row["unit"],
                    "preferred_direction": row["preferred_direction"],
                }
            )

    method_comparison = []
    for row in method_rows:
        no_limit = _float(row, "no_velocity_limit_normalized_rmse_robust")
        velocity_limit = _float(row, "velocity_limit_normalized_rmse_robust")
        method_comparison.append(
            {
                "method_id": row["method_id"],
                "method": row["method"],
                "result_group": row["result_group"],
                "causal": row["causal"] == "True",
                "velocity_to_no_limit_nrmse_ratio": velocity_limit / no_limit,
                "no_velocity_limit_nrmse": no_limit,
                "velocity_limit_nrmse": velocity_limit,
                "change_pct": _float(
                    row,
                    "velocity_limit_vs_no_limit_normalized_rmse_change_pct",
                ),
                "no_velocity_limit_abs_lag_ms": _float(
                    row, "no_velocity_limit_abs_best_lag_ms"
                ),
                "velocity_limit_abs_lag_ms": _float(
                    row, "velocity_limit_abs_best_lag_ms"
                ),
            }
        )

    window_points = []
    for row in window_rows:
        rms_velocity = _float(row, "rms_velocity")
        tracking_rmse = _float(row, "tracking_rmse_rad")
        if rms_velocity <= 0.0 or tracking_rmse <= 0.0:
            continue
        window_points.append(
            {
                "point_label": f"{row['dataset']} window {row['window_index']}",
                "dataset": row["dataset"],
                "dataset_label": row["label"],
                "window_index": int(row["window_index"]),
                "start_time_s": _float(row, "start_time_s"),
                "sample_count": int(row["sample_count"]),
                "rms_velocity_rad_s": rms_velocity,
                "tracking_rmse_rad": tracking_rmse,
                "log10_rms_velocity": math.log10(rms_velocity),
                "log10_tracking_rmse": math.log10(tracking_rmse),
                "rms_acceleration_rad_s2": _float(row, "rms_acceleration"),
                "rms_jerk_rad_s3": _float(row, "rms_jerk"),
                "position_range_rad": _float(row, "position_range_rad"),
            }
        )

    metric_table = [
        {
            "order": order,
            "metric_group": row["metric_group"],
            "metric": row["label"],
            "unit": row["unit"],
            "current_csv": _float(row, "current_csv"),
            "no_velocity_limit": _float(row, "no_velocity_limit"),
            "velocity_limit": _float(row, "velocity_limit"),
            "velocity_limit_vs_no_limit_change_pct": _float(
                row, primary_change
            ),
            "preferred_direction": row["preferred_direction"],
        }
        for order, row in enumerate(metric_rows)
    ]

    trace_quality = [
        {
            "order": order,
            "dataset": row["dataset"],
            "label": row["label"],
            "rows": int(row["rows"]),
            "source_duration_s": _float(row, "source_duration_s"),
            "fixed_grid_duration_s": _float(row, "fixed_grid_duration_s"),
            "position_range_rad": _float(row, "position_range_rad"),
            "source_dt_p50_ms": _float(row, "source_dt_p50_ms"),
            "source_dt_min_ms": _float(row, "source_dt_min_ms"),
            "source_dt_max_ms": _float(row, "source_dt_max_ms"),
            "source_dt_within_5_to_15ms_rate": _float(
                row, "source_dt_within_5_to_15ms_rate"
            ),
            "topic_count": int(row["topic_count"]),
        }
        for order, row in enumerate(trace_rows)
    ]

    charts = [
        {
            "id": "key_ratio_chart",
            "title": "Raw-demand and tracking ratios relative to the current CSV",
            "subtitle": (
                "Grouped bars retain the current CSV as 1.0 while separating "
                "the no-limit and velocity-limit variants"
            ),
            "showDescription": True,
            "intent": "comparison",
            "question": (
                "How do both simplified variants compare with the existing "
                "CSV across demand and tracking metrics?"
            ),
            "rationale": (
                "A grouped ratio chart compares unlike units on a shared "
                "baseline without merging the two variants."
            ),
            "comparisonContext": {
                "baseline": "Current CSV = 1.0",
                "denominator": "Current CSV value for the same metric",
                "grain": "One aggregate per trace and metric",
                "unit": "ratio",
            },
            "type": "bar",
            "dataset": "key_comparisons",
            "sourceId": "comparison_metrics",
            "encodings": {
                "x": {"field": "label", "type": "nominal", "label": "Metric"},
                "y": {
                    "field": "value_to_current_ratio",
                    "type": "quantitative",
                    "label": "Variant / current",
                },
                "color": {
                    "field": "variant",
                    "type": "nominal",
                    "label": "Variant",
                },
                "tooltip": [
                    {"field": "current_value", "label": "Current"},
                    {"field": "variant_value", "label": "Variant"},
                    {"field": "unit", "label": "Unit"},
                ],
            },
            "palette": {"kind": "categorical", "name": "comparison"},
            "legend": {
                "position": "bottom",
                "sort": "labelAsc",
                "title": "Variant",
            },
            "labels": {"values": "all"},
            "referenceLines": [
                {
                    "axis": "y",
                    "value": 1.0,
                    "label": "Current CSV",
                    "color": "neutral",
                    "lineStyle": "dashed",
                }
            ],
            "settings": {
                "orientation": "vertical",
                "groupMode": "grouped",
                "categoryLabelPolicy": "wrap",
            },
            "surface": {"surface": "card", "showControls": False},
            "layout": "full",
        },
        {
            "id": "method_ratio_chart",
            "title": "Velocity-limit tracking NRMSE across methods",
            "subtitle": (
                "Each bar is velocity-limit NRMSE divided by no-limit NRMSE; "
                "values below 1 indicate lower tracking error"
            ),
            "showDescription": True,
            "intent": "comparison",
            "question": (
                "Is the tracking improvement specific to P-only, or does it "
                "hold across target-state methods?"
            ),
            "rationale": (
                "A method-level ratio chart exposes reversals and the size "
                "of the improvement under each estimator."
            ),
            "comparisonContext": {
                "baseline": "No-velocity-limit method NRMSE = 1.0",
                "denominator": "No-limit NRMSE for the same method",
                "grain": "One row per target-state method",
                "unit": "ratio",
            },
            "type": "bar",
            "dataset": "method_comparison",
            "sourceId": "method_metrics",
            "encodings": {
                "x": {
                    "field": "method_id",
                    "type": "nominal",
                    "label": "Method",
                },
                "y": {
                    "field": "velocity_to_no_limit_nrmse_ratio",
                    "type": "quantitative",
                    "label": "Velocity limit / no limit NRMSE",
                },
                "tooltip": [
                    {"field": "method", "label": "Method definition"},
                    {
                        "field": "no_velocity_limit_nrmse",
                        "label": "No-limit NRMSE",
                    },
                    {
                        "field": "velocity_limit_nrmse",
                        "label": "Velocity-limit NRMSE",
                    },
                    {"field": "change_pct", "label": "Change (%)"},
                ],
            },
            "palette": {"kind": "sequential", "name": "blue"},
            "referenceLines": [
                {
                    "axis": "y",
                    "value": 1.0,
                    "label": "No velocity limit",
                    "color": "neutral",
                    "lineStyle": "dashed",
                }
            ],
            "settings": {
                "orientation": "vertical",
                "groupMode": "single",
                "categoryLabelPolicy": "rotate",
            },
            "surface": {"surface": "card", "showControls": False},
            "layout": "full",
        },
        {
            "id": "window_relationship_chart",
            "title": "One-second velocity demand and tracking error",
            "subtitle": (
                "Log10 axes; each point is a non-overlapping 100-sample "
                "window from one of the three CSVs"
            ),
            "showDescription": True,
            "intent": "relationship",
            "question": (
                "Do windows with higher RMS velocity also carry larger "
                "P-only tracking error?"
            ),
            "rationale": (
                "A window-level scatter reveals the relationship at a finer "
                "grain than three trace aggregates."
            ),
            "comparisonContext": {
                "grain": "Non-overlapping 100-sample windows",
                "normalization": "Log10 transform for display only",
                "unit": "log10(rad/s) by log10(rad)",
            },
            "type": "scatter",
            "dataset": "window_points",
            "sourceId": "window_metrics",
            "encodings": {
                "x": {
                    "field": "log10_rms_velocity",
                    "type": "quantitative",
                    "label": "log10 RMS velocity",
                },
                "y": {
                    "field": "log10_tracking_rmse",
                    "type": "quantitative",
                    "label": "log10 tracking RMSE",
                },
                "color": {
                    "field": "dataset_label",
                    "type": "nominal",
                    "label": "Dataset",
                },
                "label": {"field": "point_label", "type": "text"},
                "tooltip": [
                    {
                        "field": "rms_velocity_rad_s",
                        "label": "RMS velocity (rad/s)",
                    },
                    {
                        "field": "tracking_rmse_rad",
                        "label": "Tracking RMSE (rad)",
                    },
                    {
                        "field": "position_range_rad",
                        "label": "Position range (rad)",
                    },
                    {
                        "field": "rms_acceleration_rad_s2",
                        "label": "RMS acceleration (rad/s²)",
                    },
                    {
                        "field": "rms_jerk_rad_s3",
                        "label": "RMS jerk (rad/s³)",
                    },
                ],
            },
            "combinationRationale": (
                "Color identifies the three traces while x and y retain a "
                "common one-second analytical grain."
            ),
            "palette": {"kind": "categorical", "name": "dataset"},
            "legend": {
                "position": "bottom",
                "sort": "labelAsc",
                "title": "Dataset",
            },
            "surface": {"surface": "card", "showControls": False},
            "layout": "full",
        },
    ]

    tables = [
        {
            "id": "trace_table",
            "title": "Trace shape and timing profile",
            "subtitle": (
                "All sources are valid single-topic position recordings; "
                "duration and workload are not paired"
            ),
            "showDescription": True,
            "dataset": "trace_quality",
            "sourceId": "trace_quality",
            "defaultSort": {"field": "order", "direction": "asc"},
            "density": "spacious",
            "layout": "full",
            "columns": [
                {"field": "order", "label": "Order", "format": "number"},
                {"field": "label", "label": "CSV", "type": "text"},
                {"field": "rows", "label": "Rows", "format": "number"},
                {
                    "field": "fixed_grid_duration_s",
                    "label": "Duration (s)",
                    "format": "number",
                },
                {
                    "field": "position_range_rad",
                    "label": "Position range (rad)",
                    "format": "number",
                },
                {
                    "field": "source_dt_p50_ms",
                    "label": "Median source Δt (ms)",
                    "format": "number",
                },
                {
                    "field": "source_dt_within_5_to_15ms_rate",
                    "label": "Δt in 5–15 ms rate",
                    "format": "percent",
                },
            ],
        },
        {
            "id": "metric_table",
            "title": "Exact three-way comparison metrics",
            "subtitle": (
                "The final change column compares velocity limit with no "
                "velocity limit under identical metric definitions"
            ),
            "showDescription": True,
            "dataset": "metric_table",
            "sourceId": "comparison_metrics",
            "defaultSort": {"field": "order", "direction": "asc"},
            "density": "spacious",
            "layout": "full",
            "columns": [
                {"field": "order", "label": "Order", "format": "number"},
                {"field": "metric_group", "label": "Group", "type": "text"},
                {"field": "metric", "label": "Metric", "type": "text"},
                {"field": "unit", "label": "Unit", "type": "text"},
                {
                    "field": "current_csv",
                    "label": "Current CSV",
                    "format": "number",
                },
                {
                    "field": "no_velocity_limit",
                    "label": "No velocity limit",
                    "format": "number",
                },
                {
                    "field": "velocity_limit",
                    "label": "Velocity limit",
                    "format": "number",
                },
                {
                    "field": "velocity_limit_vs_no_limit_change_pct",
                    "label": "Velocity limit vs no limit (%)",
                    "format": "number",
                    "movement": True,
                    "role": "movement",
                    "semantic": "movement",
                },
                {
                    "field": "preferred_direction",
                    "label": "Preferred",
                    "type": "text",
                },
            ],
        },
    ]

    method_changes = [
        row["change_pct"] for row in method_comparison
    ]
    blocks = [
        {
            "id": "title",
            "type": "markdown",
            "body": f"# {REPORT_TITLE}",
            "layout": "full",
        },
        {
            "id": "technical_summary",
            "type": "markdown",
            "body": (
                "## Technical summary\n\n"
                "**The velocity-limit CSV shows lower sampled max V/A/J and "
                "substantially better tracking than the no-limit CSV.** On "
                "the common 10 ms grid, max |V|, |A|, and |J| change by "
                f"{_percent(max_velocity_change)}, "
                f"{_percent(max_acceleration_change)}, and "
                f"{_percent(max_jerk_change)}. P-only robust-scale NRMSE "
                f"changes by {_percent(nrmse_change)}, normalized maximum "
                f"error by {_percent(max_error_change)}, and absolute lag by "
                f"{_percent(lag_change)} (120 ms to 30 ms).\n\n"
                "The direction is robust across all seven target-state "
                f"methods: normalized tracking error falls by "
                f"{abs(max(method_changes)):.1f}% to "
                f"{abs(min(method_changes)):.1f}%. The result supports the "
                "engineering hypothesis that lower dynamic demand is "
                "associated with easier tracking. It is **not a causal proof**: "
                f"the velocity-limit trace is {_percent(duration_change)} "
                f"longer and its position range is {_percent(range_change)} "
                "different, so path and time-law changes are confounded."
            ),
            "layout": "full",
        },
        {
            "id": "headline_metrics",
            "type": "metric-strip",
            "cardIds": [card["id"] for card in cards],
            "layout": "full",
        },
        {
            "id": "demand_finding",
            "type": "markdown",
            "body": (
                "## Velocity limiting lowers both peaks and typical demand\n\n"
                "Relative to the no-limit CSV, the velocity-limit CSV reduces "
                f"P99 |V|, |A|, and |J| by "
                f"{abs(p99_velocity_change):.1f}%, "
                f"{abs(p99_acceleration_change):.1f}%, and "
                f"{abs(p99_jerk_change):.1f}%. The maximum jerk improvement "
                f"is smaller ({abs(max_jerk_change):.1f}%), which indicates "
                "one remaining local spike; the P99 and RMS measures show "
                "that the broader trajectory is much smoother. The grouped "
                "chart retains the current CSV as context rather than treating "
                "the two simplified traces as the only baseline."
            ),
            "layout": "full",
        },
        {
            "id": "key_ratio_block",
            "type": "chart",
            "chartId": "key_ratio_chart",
            "layout": "full",
        },
        {
            "id": "tracking_finding",
            "type": "markdown",
            "body": (
                "## Tracking improves under every tested target-state method\n\n"
                f"P-only robust-scale NRMSE is {_percent(nrmse_change)} and "
                f"range-normalized maximum error is "
                f"{_percent(max_error_change)} on the velocity-limit trace. "
                "Backward-FD, centered-FD, causal, and offline variants all "
                "show the same direction. This rules out a P-only-specific "
                "artifact, although estimator consistency cannot remove the "
                "cross-trajectory confounding."
            ),
            "layout": "full",
        },
        {
            "id": "method_ratio_block",
            "type": "chart",
            "chartId": "method_ratio_chart",
            "layout": "full",
        },
        {
            "id": "window_finding",
            "type": "markdown",
            "body": (
                "## Lower-velocity windows coincide with lower tracking error\n\n"
                f"Across {window_count} non-overlapping one-second windows, "
                "RMS velocity has a descriptive Spearman correlation of "
                f"{velocity_rho:.2f} with P-only tracking RMSE. This "
                "window-level evidence is consistent with the aggregate "
                "improvement, but the windows are autocorrelated and nested "
                "inside three recordings. The correlation is therefore "
                "descriptive, not an independent-sample significance test."
            ),
            "layout": "full",
        },
        {
            "id": "window_relationship_block",
            "type": "chart",
            "chartId": "window_relationship_chart",
            "layout": "full",
        },
        {
            "id": "scope_definitions",
            "type": "markdown",
            "body": (
                "## Scope, data, and metric definitions\n\n"
                "All three files contain one finite, monotonic, single-topic "
                "joint-position recording. Primary tracking consumes only "
                "`value`, assigns 10 ms to every row, applies "
                "`vmax=4.1 rad/s`, `amax=8.2 rad/s²`, and "
                "`jmax=4000 rad/s³`, and uses target[k] → output[k+1].\n\n"
                "Velocity and acceleration use centered finite differences; "
                "jerk is the centered gradient of acceleration. Max, P99, and "
                "RMS summaries share the interior [3, n−3). Robust-scale NRMSE "
                "divides tracking RMSE by reference P95−P05; maximum error is "
                "normalized by full position range."
            ),
            "layout": "full",
        },
        {
            "id": "trace_context",
            "type": "markdown",
            "body": (
                "## Trace duration prevents a paired causal interpretation\n\n"
                "The velocity-limit trace contains 7,673 rows and spans "
                "76.72 s on the fixed grid, versus 1,275 rows and 12.74 s for "
                "the no-limit trace. Its position range is only 6.1% smaller, "
                "but the much longer execution time materially reduces dynamic "
                "demand. This is valid performance evidence for the recorded "
                "inputs, but not an isolated intervention on velocity alone."
            ),
            "layout": "full",
        },
        {
            "id": "trace_table_block",
            "type": "table",
            "tableId": "trace_table",
            "layout": "full",
        },
        {
            "id": "exact_metrics_intro",
            "type": "markdown",
            "body": (
                "## Exact metrics preserve peaks, tails, and tracking outcomes\n\n"
                "The table retains workload context, max/P99/RMS sampled "
                "V/A/J, P-only tracking quality, reachability, and compute "
                "timing. Negative change means the velocity-limit value is "
                "lower than the no-limit value; the preferred direction is "
                "listed separately."
            ),
            "layout": "full",
        },
        {
            "id": "exact_metrics_block",
            "type": "table",
            "tableId": "metric_table",
            "layout": "full",
        },
        {
            "id": "methodology",
            "type": "markdown",
            "body": (
                "## Methodology and reproducibility\n\n"
                "`scripts/compare_csv_pvaj_tracking.py` validates schema, "
                "finite values, topic presence, and strictly increasing source "
                "clocks; computes fixed-grid PVAJ; runs the same ordinary-"
                "Ruckig method matrix for all references; writes sample, "
                "aggregate, method, and one-second-window tables; and saves "
                "PNG/SVG small multiples. Source-time recursive gradients are "
                "kept as a timing-sensitivity table and are not substituted "
                "for the primary fixed-grid comparison."
            ),
            "layout": "full",
        },
        {
            "id": "limitations",
            "type": "markdown",
            "body": (
                "## Limitations, uncertainty, and robustness checks\n\n"
                "- **Not paired geometry or duration:** start/end state, path "
                "shape, sample count, and duration are not controlled.\n"
                "- **Position-only derivatives:** maximum acceleration and "
                "jerk are finite-difference diagnostics and can amplify noise; "
                "P99 and RMS provide less spike-sensitive checks.\n"
                "- **Source timing:** the velocity-limit source is much more "
                "regular (all intervals within 5–15 ms), but the primary test "
                "deliberately applies the same fixed 10 ms convention.\n"
                "- **No independent repetitions:** three traces cannot support "
                "population confidence intervals, and windows are not "
                "independent replicates.\n"
                "- **Development-only evidence:** this analysis does not alter "
                "the repository's frozen V3/V4 confirmation claims."
            ),
            "layout": "full",
        },
        {
            "id": "next_steps",
            "type": "markdown",
            "body": (
                "## Recommended next steps\n\n"
                "1. Re-run the no-limit and velocity-limit variants on the "
                "same waypoint sequence, start/end state, and sample count.\n"
                "2. Perform a controlled time-scaling sweep of one fixed path "
                "and predeclare max/P99 V/A/J plus NRMSE, max error, and lag.\n"
                "3. Collect multiple paired task executions and compare one "
                "metric per trajectory with paired confidence intervals.\n"
                "4. Treat the current result as strong development evidence "
                "for the limit setting, while keeping the causal wording "
                "qualified until the paired design is complete."
            ),
            "layout": "full",
        },
        {
            "id": "further_questions",
            "type": "markdown",
            "body": (
                "## Further questions\n\n"
                "- Were the two simplified CSVs generated from the same "
                "waypoint sequence and only retimed?\n"
                "- What velocity limit produced the velocity-limit recording?\n"
                "- Should success prioritize max peaks, P99 demand, tracking "
                "lag, or a predeclared multi-metric acceptance rule?"
            ),
            "layout": "full",
        },
    ]

    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": REPORT_TITLE,
            "description": (
                "Development-only technical comparison of three position CSV "
                "traces, sampled PVAJ demand, and ordinary-Ruckig tracking."
            ),
            "generatedAt": generated_at,
            "blocks": blocks,
            "cards": cards,
            "charts": charts,
            "tables": tables,
            "sources": sources,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "headline_metrics": headline_rows,
                "key_comparisons": key_comparisons,
                "method_comparison": method_comparison,
                "window_points": window_points,
                "metric_table": metric_table,
                "trace_quality": trace_quality,
            },
        },
        "sources": sources,
        "package_info": {
            "report_kind": "technical",
            "development_only": True,
            "primary_method": "ordinary Ruckig P-only",
            "primary_comparison": "velocity limit versus no velocity limit",
        },
    }
    return artifact, database_path


def write_source_notes(results_dir):
    notes = {
        "audience": "technical",
        "delivery_mode": "html",
        "required_structure_mapping": {
            "Title": "title",
            "Technical summary": "technical_summary",
            "Key findings with visual evidence": [
                "demand_finding",
                "tracking_finding",
                "window_finding",
            ],
            "Scope, data, and metric definitions": "scope_definitions",
            "Methodology": "methodology",
            "Limitations, uncertainty, and robustness checks": "limitations",
            "Recommended next steps": "next_steps",
            "Further questions": "further_questions",
        },
        "chart_map": [
            {
                "section": "Velocity limiting lowers peaks and typical demand",
                "question": "How do both variants compare with the current CSV?",
                "family": "comparison",
                "type": "grouped bar",
                "fields": ["label", "value_to_current_ratio", "variant"],
                "claim": "Velocity limit lowers V/A/J and tracking ratios.",
                "palette_policy": "hard two-root cap",
                "artifact": "key_ratio_chart",
            },
            {
                "section": "Tracking improves under every method",
                "question": "Does any target-state method reverse the result?",
                "family": "comparison",
                "type": "bar",
                "fields": ["method_id", "velocity_to_no_limit_nrmse_ratio"],
                "claim": "All seven method ratios are below 1.",
                "palette_policy": "single-root preferred",
                "artifact": "method_ratio_chart",
            },
            {
                "section": "Lower velocity coincides with lower tracking error",
                "question": "Do higher-velocity windows carry more error?",
                "family": "relationship",
                "type": "scatter",
                "fields": [
                    "log10_rms_velocity",
                    "log10_tracking_rmse",
                    "dataset_label",
                ],
                "claim": "Velocity is a plausible driver, not a causal estimate.",
                "palette_policy": "relaxed multi-category",
                "artifact": "window_relationship_chart",
            },
        ],
        "omitted_visuals": [
            {
                "item": "Raw PVAJ time series inside the portable report",
                "reason": (
                    "PNG/SVG three-column small multiples are retained as "
                    "supporting artifacts because unequal paths and durations "
                    "are not pointwise comparable."
                ),
            },
            {
                "item": "Tracking time series inside the portable report",
                "reason": (
                    "PNG/SVG faceted output is retained because the native "
                    "artifact chart contract does not require unequal-duration "
                    "faceting."
                ),
            },
        ],
    }
    output = Path(results_dir) / "source_notes.json"
    output.write_text(
        json.dumps(notes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the canonical three-CSV comparison report artifact."
    )
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    return parser.parse_args()


def main():
    args = parse_args()
    results_dir = args.results_dir.resolve()
    artifact, database_path = build_artifact(results_dir)
    output = results_dir / "artifact.json"
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    notes = write_source_notes(results_dir)
    print(f"Saved: {output}")
    print(f"Saved: {database_path}")
    print(f"Saved: {notes}")


if __name__ == "__main__":
    main()
