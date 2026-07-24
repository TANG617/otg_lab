"""Build the canonical Data Analytics artifact for the two-CSV comparison."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results" / "csv_pvaj_tracking_comparison"
REPORT_TITLE = "Two-CSV PVAJ and Tracking Comparison"
METRIC_SQL = """
SELECT
    metric_group, metric, label, unit, preferred_direction,
    current_csv, new_csv, absolute_delta, change_pct
FROM metric_comparison
ORDER BY source_row_order
""".strip()
METHOD_SQL = """
SELECT
    method_id, method, result_group, causal,
    current_rmse, new_rmse, rmse_change_pct,
    current_normalized_rmse_robust, new_normalized_rmse_robust,
    normalized_rmse_change_pct,
    current_abs_best_lag_ms, new_abs_best_lag_ms,
    current_target_projection_rate, new_target_projection_rate,
    current_reachable_within_10ms_rate, new_reachable_within_10ms_rate
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
    for table, filename in (
        ("metric_comparison", "metric_comparison.csv"),
        ("tracking_method_comparison", "tracking_method_comparison.csv"),
        ("window_diagnostics", "window_diagnostics.csv"),
        ("trace_quality", "trace_quality.csv"),
    ):
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
                (row_order, *(row[column] for column in columns))
                for row_order, row in enumerate(rows)
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


def _number(value, digits=3):
    if abs(value) >= 1000:
        return f"{value / 1000:.2f}k"
    return f"{value:.{digits}f}"


def _source(
    source_id,
    label,
    sql,
    description,
    table,
    metric_definitions=None,
):
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
                "Both traces use a fixed 10 ms row period for the primary comparison",
                "PVAJ summaries exclude the first and last three samples",
                "Tracking metrics exclude the first three warm-up samples",
            ],
            "metric_definitions": metric_definitions or [],
            "tables_used": [table],
        },
    }


def build_artifact(results_dir):
    results_dir = Path(results_dir).resolve()
    connection, database_path = _build_report_database(results_dir)
    try:
        metric_rows = _query_rows(connection, METRIC_SQL)
        method_rows = _query_rows(connection, METHOD_SQL)
        window_rows = _query_rows(connection, WINDOW_SQL)
        trace_rows = _query_rows(connection, TRACE_SQL)
    finally:
        connection.close()
    run_manifest = json.loads((results_dir / "run.json").read_text(encoding="utf-8"))
    metrics = _metric_lookup(metric_rows)
    generated_at = run_manifest["generated_at"]

    max_velocity_change = _float(metrics["max_abs_velocity"], "change_pct")
    max_acceleration_change = _float(metrics["max_abs_acceleration"], "change_pct")
    max_jerk_change = _float(metrics["max_abs_jerk"], "change_pct")
    nrmse_change = _float(metrics["normalized_rmse_robust"], "change_pct")
    max_error_change = _float(metrics["normalized_max_error_range"], "change_pct")
    lag_change = _float(metrics["abs_best_lag_ms"], "change_pct")

    sources = [
        _source(
            "comparison_metrics",
            "Two-CSV comparison metrics",
            METRIC_SQL,
            (
                "Candidate-minus-baseline comparison generated by "
                "scripts/compare_csv_pvaj_tracking.py."
            ),
            "metric_comparison",
            [
                "Change (%) = 100 × (new − current) / |current|.",
                (
                    "Robust-scale NRMSE = tracking RMSE / "
                    "(reference P95 − reference P05)."
                ),
                (
                    "Maximum sampled V/A/J are computed on the common fixed "
                    "10 ms grid over indices [3, n−3)."
                ),
            ],
        ),
        _source(
            "method_metrics",
            "Tracking comparison across target-state methods",
            METHOD_SQL,
            (
                "Same ordinary-Ruckig limits and timing applied to all seven "
                "real-CSV-compatible target-state methods."
            ),
            "tracking_method_comparison",
            [
                (
                    "Each method ratio is new robust-scale NRMSE divided by "
                    "current robust-scale NRMSE."
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
                "Non-overlapping one-second windows from both traces, using "
                "P-only tracking and fixed-grid sampled demand."
            ),
            "window_diagnostics",
            [
                "Window RMS velocity is sqrt(mean(v²)) over 100 samples.",
                (
                    "Window tracking RMSE is sqrt(mean((output − "
                    "reference)²)) over the same 100 samples."
                ),
            ],
        ),
        _source(
            "trace_quality",
            "CSV source and timing profile",
            TRACE_SQL,
            (
                "Row counts, duration, source-time jitter, topic count, "
                "position range, and clock-consistency checks."
            ),
            "trace_quality",
        ),
    ]

    headline_specs = [
        (
            "max_velocity",
            "New max |V| (rad/s)",
            _float(metrics["max_abs_velocity"], "new_csv"),
            max_velocity_change,
            (
                "Maximum sampled velocity on the fixed 10 ms grid; higher "
                "than the current CSV."
            ),
        ),
        (
            "max_acceleration",
            "New max |A| (rad/s²)",
            _float(metrics["max_abs_acceleration"], "new_csv"),
            max_acceleration_change,
            (
                "Maximum sampled acceleration on the fixed 10 ms grid; lower "
                "than the current CSV."
            ),
        ),
        (
            "max_jerk",
            "New max |J| (rad/s³)",
            _float(metrics["max_abs_jerk"], "new_csv"),
            max_jerk_change,
            (
                "Maximum sampled jerk on the fixed 10 ms grid; lower than the "
                "current CSV."
            ),
        ),
        (
            "tracking_nrmse",
            "New P-only NRMSE",
            _float(metrics["normalized_rmse_robust"], "new_csv"),
            nrmse_change,
            (
                "P-only tracking RMSE normalized by the new trace's P95−P05 "
                "position scale."
            ),
        ),
    ]
    headline_rows = [
        {
            "metric": metric,
            "new_value": new_value,
            "change_pct": change_pct,
            "description": description,
        }
        for metric, _label, new_value, change_pct, description in headline_specs
    ]
    cards = [
        {
            "id": f"card_{metric}",
            "dataset": "headline_metrics",
            "filter": {"metric": metric},
            "description": description,
            "sourceId": "comparison_metrics",
            "metrics": [
                {
                    "label": label,
                    "field": "new_value",
                    "format": "number",
                },
                {
                    "label": "vs current (%)",
                    "field": "change_pct",
                    "format": "number",
                    "signed": True,
                },
            ],
        }
        for metric, label, _new_value, _change_pct, description in headline_specs
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
        new = _float(row, "new_csv")
        key_comparisons.append(
            {
                "metric": metric,
                "label": short_labels[metric],
                "metric_group": row["metric_group"],
                "new_to_current_ratio": new / current,
                "current_value": current,
                "new_value": new,
                "change_pct": _float(row, "change_pct"),
                "unit": row["unit"],
                "preferred_direction": row["preferred_direction"],
            }
        )

    method_comparison = []
    for row in method_rows:
        current = _float(row, "current_normalized_rmse_robust")
        new = _float(row, "new_normalized_rmse_robust")
        method_comparison.append(
            {
                "method_id": row["method_id"],
                "method": row["method"],
                "result_group": row["result_group"],
                "causal": row["causal"] == "True",
                "new_to_current_nrmse_ratio": new / current,
                "current_nrmse": current,
                "new_nrmse": new,
                "nrmse_change_pct": _float(row, "normalized_rmse_change_pct"),
                "current_abs_lag_ms": _float(row, "current_abs_best_lag_ms"),
                "new_abs_lag_ms": _float(row, "new_abs_best_lag_ms"),
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
                "point_label": (f"{row['dataset']} window {row['window_index']}"),
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

    metric_table = []
    for order, row in enumerate(metric_rows):
        metric_table.append(
            {
                "order": order,
                "metric_group": row["metric_group"],
                "metric": row["label"],
                "unit": row["unit"],
                "current_csv": _float(row, "current_csv"),
                "new_csv": _float(row, "new_csv"),
                "change_pct": _float(row, "change_pct"),
                "preferred_direction": row["preferred_direction"],
            }
        )

    trace_quality = []
    for row in trace_rows:
        trace_quality.append(
            {
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
        )

    charts = [
        {
            "id": "key_ratio_chart",
            "title": "New-to-current ratios for raw demand and tracking",
            "subtitle": (
                "Values below 1 are lower on the new CSV; values above 1 are higher"
            ),
            "showDescription": True,
            "intent": "comparison",
            "question": (
                "Which raw-demand and tracking metrics are lower or higher on "
                "the new CSV?"
            ),
            "rationale": (
                "A common ratio scale compares metrics with different physical "
                "units without hiding their direction."
            ),
            "comparisonContext": {
                "baseline": "Current CSV = 1.0",
                "denominator": "Current CSV metric value",
                "grain": "One aggregate per trace and metric",
                "normalization": "New value divided by current value",
                "unit": "ratio",
            },
            "type": "bar",
            "dataset": "key_comparisons",
            "sourceId": "comparison_metrics",
            "encodings": {
                "x": {
                    "field": "label",
                    "type": "nominal",
                    "label": "Metric",
                },
                "y": {
                    "field": "new_to_current_ratio",
                    "type": "quantitative",
                    "label": "New / current",
                },
                "tooltip": [
                    {"field": "current_value", "label": "Current"},
                    {"field": "new_value", "label": "New"},
                    {"field": "change_pct", "label": "Change (%)"},
                    {"field": "unit", "label": "Unit"},
                ],
            },
            "palette": {"kind": "categorical", "name": "comparison"},
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
                "groupMode": "single",
                "categoryLabelPolicy": "wrap",
            },
            "surface": {"surface": "card", "showControls": False},
            "layout": "full",
        },
        {
            "id": "method_ratio_chart",
            "title": "Robust-scale tracking NRMSE across methods",
            "subtitle": ("Every tested real-CSV-compatible method has a ratio above 1"),
            "showDescription": True,
            "intent": "comparison",
            "question": (
                "Is the tracking result specific to the P-only baseline or "
                "consistent across target-state methods?"
            ),
            "rationale": (
                "A method-level bar chart exposes whether one method reverses "
                "the aggregate result."
            ),
            "comparisonContext": {
                "baseline": "Current CSV method NRMSE = 1.0",
                "denominator": "Current robust-scale NRMSE for the same method",
                "grain": "One row per target-state method",
                "normalization": "New NRMSE divided by current NRMSE",
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
                    "field": "new_to_current_nrmse_ratio",
                    "type": "quantitative",
                    "label": "New / current NRMSE",
                },
                "tooltip": [
                    {"field": "method", "label": "Method definition"},
                    {"field": "current_nrmse", "label": "Current NRMSE"},
                    {"field": "new_nrmse", "label": "New NRMSE"},
                    {"field": "nrmse_change_pct", "label": "Change (%)"},
                ],
            },
            "palette": {"kind": "sequential", "name": "blue"},
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
                "groupMode": "single",
                "categoryLabelPolicy": "rotate",
            },
            "surface": {"surface": "card", "showControls": False},
            "layout": "full",
        },
        {
            "id": "window_relationship_chart",
            "title": "One-second velocity demand and tracking error",
            "subtitle": ("Log10 axes; 31 non-overlapping windows from the two traces"),
            "showDescription": True,
            "intent": "relationship",
            "question": (
                "Do higher-velocity windows also show larger P-only tracking error?"
            ),
            "rationale": (
                "A scatter plot at one-second grain reveals the demand/error "
                "relationship without treating only two trace aggregates as a "
                "correlation."
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
                "label": {
                    "field": "point_label",
                    "type": "text",
                },
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
                "Color distinguishes the two trace identities while x and y "
                "retain a common window grain."
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
            "id": "metric_table",
            "title": "Exact comparison metrics",
            "subtitle": ("Current and new values use the same fixed-grid definitions"),
            "showDescription": True,
            "dataset": "metric_table",
            "sourceId": "comparison_metrics",
            "defaultSort": {"field": "metric", "direction": "asc"},
            "density": "spacious",
            "layout": "full",
            "columns": [
                {"field": "metric_group", "label": "Group", "type": "text"},
                {"field": "metric", "label": "Metric", "type": "text"},
                {"field": "unit", "label": "Unit", "type": "text"},
                {
                    "field": "current_csv",
                    "label": "Current CSV",
                    "format": "number",
                },
                {
                    "field": "new_csv",
                    "label": "New CSV",
                    "format": "number",
                },
                {
                    "field": "change_pct",
                    "label": "Change (%)",
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
        }
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
                "**The requested hypothesis is not supported by these two "
                "traces.** The new CSV lowers maximum sampled acceleration by "
                f"{abs(max_acceleration_change):.1f}% and maximum sampled jerk "
                f"by {abs(max_jerk_change):.1f}%, but maximum sampled velocity "
                f"increases by {max_velocity_change:.1f}%. Under the same "
                "10 ms grid, vendor limits, and ordinary-Ruckig P-only "
                f"follower, robust-scale NRMSE increases by {nrmse_change:.1f}% "
                f"and absolute lag increases by {lag_change:.1f}%.\n\n"
                "The result is a **V versus A/J tradeoff**, not a uniform VAJ "
                "improvement. All seven real-CSV-compatible target-state "
                "methods have worse normalized tracking error on the new CSV. "
                "Because the traces also differ in duration, range, and path "
                "shape, this is descriptive evidence and does not establish "
                "that velocity alone caused the degradation."
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
                "## A/J fall, but velocity and workload rise\n\n"
                "The new trace has a 26.9% larger position range and is 34.2% "
                "shorter on the fixed grid. Against that harder workload, max "
                f"|V| is {_percent(max_velocity_change)}, while max |A| is "
                f"{_percent(max_acceleration_change)} and max |J| is "
                f"{_percent(max_jerk_change)}. The ratio chart uses 1.0 as the "
                "current-CSV benchmark, so it makes the mixed direction "
                "explicit rather than collapsing V, A, and J into one score."
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
                "## Tracking does not improve under the common controller\n\n"
                f"P-only robust-scale NRMSE is {_percent(nrmse_change)}, "
                f"range-normalized maximum error is {_percent(max_error_change)}, "
                f"and absolute lag is {_percent(lag_change)} on the new CSV. "
                "The same direction holds for P, backward-FD PV/PVA, offline "
                "centered-FD PV/PVA, and causal delay-one centered-FD PV/PVA. "
                "This consistency makes a method-specific anomaly unlikely, "
                "but it still does not make the comparison causal."
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
                "## Higher-velocity windows carry more tracking error\n\n"
                "Across 31 non-overlapping one-second windows, RMS velocity has "
                "a descriptive Spearman correlation of 0.87 with P-only "
                "tracking RMSE. Velocity is the strongest of the reported "
                "window-level demand relationships. This supports velocity as "
                "a plausible driver of the observed degradation, but the "
                "windows are autocorrelated and nested inside only two traces, "
                "so the correlation is not an inferential or causal estimate."
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
                "Both files contain one recorded joint-position topic. The "
                "current CSV has 1,936 rows; the new CSV has 1,275. Primary "
                "tracking follows the repository convention: only `value` is "
                "consumed, every row represents 10 ms, limits are "
                "`vmax=4.1 rad/s`, `amax=8.2 rad/s²`, and "
                "`jmax=4000 rad/s³`, and target[k] produces output[k+1].\n\n"
                "Raw sampled velocity and acceleration use centered finite "
                "differences; sampled jerk is the centered gradient of "
                "acceleration. Max, P99, and RMS summaries share the interior "
                "[3, n−3). Tracking starts after three warm-up samples. "
                "Robust-scale NRMSE divides RMSE by reference P95−P05; maximum "
                "error is also normalized by full position range."
            ),
            "layout": "full",
        },
        {
            "id": "exact_metrics_intro",
            "type": "markdown",
            "body": (
                "## Exact metrics preserve the tradeoffs\n\n"
                "The table retains workload context, max/P99/RMS sampled V/A/J, "
                "P-only tracking quality, reachability, and compute timing. "
                "Negative change means the new value is lower; whether lower is "
                "desirable is stated separately."
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
                "The comparison is implemented by "
                "`scripts/compare_csv_pvaj_tracking.py`. It validates finite "
                "values, a non-empty topic, and strictly increasing source "
                "elapsed time and timestamps; computes fixed-grid PVAJ; runs "
                "the same ordinary-Ruckig method matrix for each reference; "
                "writes full sample, aggregate, and one-second-window tables; "
                "and saves static PNG/SVG trace figures. Source-time recursive "
                "gradients are written separately as a timing-sensitivity "
                "check and are not substituted into the fixed-grid primary "
                "comparison."
            ),
            "layout": "full",
        },
        {
            "id": "limitations",
            "type": "markdown",
            "body": (
                "## Limitations, uncertainty, and robustness checks\n\n"
                "- **Not paired geometry:** the two traces differ in path "
                "shape, range, endpoint, and duration.\n"
                "- **Position-only derivative noise:** maximum acceleration "
                "and jerk are finite-difference diagnostics and are sensitive "
                "to measurement noise. P99 and RMS move in the same direction "
                "as their maxima, which reduces—but does not remove—this risk.\n"
                "- **Timing convention:** both source clocks are monotonic and "
                "have approximately 10 ms medians, but both contain jitter. "
                "Actual-time recursive-gradient sensitivity still shows lower "
                "A/J and higher V for the new trace.\n"
                "- **No independent repetitions:** two traces cannot support "
                "confidence intervals for a population of tasks; one-second "
                "windows are not independent replicates.\n"
                "- **Development-only evidence:** this analysis does not alter "
                "or extend the repository's frozen V3/V4 confirmation claims."
            ),
            "layout": "full",
        },
        {
            "id": "next_steps",
            "type": "markdown",
            "body": (
                "## Recommended next steps\n\n"
                "1. Generate a paired candidate with the same geometric path, "
                "start/end state, amplitude, and sample count as the current "
                "trace; change only the simplification or time law.\n"
                "2. Predeclare a joint success rule requiring max and P99 "
                "|V|, |A|, and |J| all to decrease without worsening "
                "robust-scale NRMSE, maximum error, or lag.\n"
                "3. Run a controlled time-scaling sweep on the same path. This "
                "directly tests whether reducing velocity demand improves "
                "tracking while avoiding cross-path confounding.\n"
                "4. Collect multiple paired tasks or sessions and compare one "
                "metric value per trajectory with paired confidence intervals. "
                "Keep the new CSV in development-only evidence until that "
                "design is complete."
            ),
            "layout": "full",
        },
        {
            "id": "further_questions",
            "type": "markdown",
            "body": (
                "## Further questions\n\n"
                "- Is the new CSV intended to preserve exactly the same task "
                "sequence and geometric waypoints as the current recording?\n"
                "- Was removing a velocity limit an intentional optimization "
                "tradeoff, or should the candidate also satisfy a maximum "
                "velocity target?\n"
                "- Should the decision optimize absolute tracking error, "
                "range-normalized error, lag, or a predeclared combination?"
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
                "Development-only technical comparison of two position CSV "
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
                "section": "A/J fall, but velocity and workload rise",
                "question": (
                    "Which raw-demand and tracking metrics are lower or higher?"
                ),
                "family": "comparison",
                "type": "bar",
                "fields": ["label", "new_to_current_ratio"],
                "claim": "A/J fall, velocity and tracking ratios rise.",
                "palette_policy": "hard two-root cap",
                "artifact": "key_ratio_chart",
            },
            {
                "section": "Tracking does not improve",
                "question": "Does any target-state method reverse the result?",
                "family": "comparison",
                "type": "bar",
                "fields": ["method_id", "new_to_current_nrmse_ratio"],
                "claim": "All seven method ratios exceed 1.",
                "palette_policy": "single-root preferred",
                "artifact": "method_ratio_chart",
            },
            {
                "section": "Higher-velocity windows carry more error",
                "question": ("Do higher-velocity windows show larger tracking error?"),
                "family": "relationship",
                "type": "scatter",
                "fields": [
                    "log10_rms_velocity",
                    "log10_tracking_rmse",
                    "dataset_label",
                ],
                "claim": ("Velocity is a plausible driver, not a causal estimate."),
                "palette_policy": "hard two-root cap",
                "artifact": "window_relationship_chart",
            },
        ],
        "omitted_visuals": [
            {
                "item": "Raw PVAJ time-series traces in portable report",
                "reason": (
                    "Static PNG/SVG small multiples are retained as supporting "
                    "artifacts because a native overlaid chart would imply "
                    "pointwise comparability between different paths."
                ),
            },
            {
                "item": "Tracking time-series traces in portable report",
                "reason": (
                    "Static faceted PNG/SVG output is retained because the "
                    "portable native chart contract has no required facet "
                    "layout for two unequal-duration paths."
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
        description="Build the canonical two-CSV comparison report artifact."
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
