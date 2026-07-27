"""Build the technical report and canonical analytics artifact."""

from __future__ import annotations

import argparse
import csv
import html
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results" / "centered_pva_regression"
REPORT_TITLE = "Centered-Difference PVA 回归诊断"

TRACKING_SQL = """
SELECT *
FROM tracking_metrics
WHERE time_basis = 'fixed_10ms'
ORDER BY source_row_order
""".strip()

CONTRAST_SQL = """
SELECT *
FROM mechanism_decomposition
ORDER BY source_row_order
""".strip()

TIMESTAMP_SQL = """
SELECT *
FROM timestamp_sensitivity
ORDER BY source_row_order
""".strip()

ESTIMATOR_SQL = """
SELECT *
FROM estimator_diagnostics
ORDER BY source_row_order
""".strip()

METHOD_ORDER = (
    "p_only_latest",
    "p_only_delayed",
    "centered_pva_delayed_unclamped",
    "centered_pv_delayed_clamped",
    "centered_pva_delayed_clamped",
    "centered_pva_latest_position_clamped",
    "centered_pva_propagated_clamped",
    "centered_pva_offline_aligned_clamped",
)
METHOD_SHORT = {
    "p_only_latest": "P latest",
    "p_only_delayed": "P delayed",
    "centered_pva_delayed_unclamped": "PVA joint projection",
    "centered_pv_delayed_clamped": "PV production timing",
    "centered_pva_delayed_clamped": "PVA production-like",
    "centered_pva_latest_position_clamped": "PVA latest P",
    "centered_pva_propagated_clamped": "PVA age compensated",
    "centered_pva_offline_aligned_clamped": "PVA offline aligned",
}
MECHANISM_SHORT = {
    "delay_only": "P delay",
    "centered_state_beyond_delay": "Centered V/A",
    "independent_clamp_vs_joint_projection": "Clamp strategy",
    "remove_position_delay": "Remove P delay",
    "propagate_derivative_age": "Propagate V age",
    "remove_target_acceleration": "Remove A target",
}


def _read_csv(path):
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _float(row, field):
    return float(row[field])


def _bool(row, field):
    return row[field].lower() == "true"


def _build_database(results_dir):
    path = Path(results_dir) / "report_source.sqlite"
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    for table, filename in (
        ("tracking_metrics", "tracking_metrics.csv"),
        ("mechanism_decomposition", "mechanism_decomposition.csv"),
        ("timestamp_sensitivity", "timestamp_sensitivity.csv"),
        ("estimator_diagnostics", "estimator_diagnostics.csv"),
    ):
        rows = _read_csv(Path(results_dir) / filename)
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
    return connection, path


def _query(connection, sql):
    cursor = connection.execute(sql)
    fields = [item[0] for item in cursor.description]
    return [dict(zip(fields, row)) for row in cursor.fetchall()]


def _source(source_id, label, table, sql, description, definitions):
    return {
        "id": source_id,
        "label": label,
        "path": "results/centered_pva_regression/report_source.sqlite",
        "query": {
            "description": description,
            "engine": "SQLite 3",
            "language": "sql",
            "sql": sql,
            "filters": [
                "Primary tracking uses the fixed 10 ms control grid",
                "Tracking evaluation uses output indices [3, original_count)",
                "Ordinary Ruckig limits are 4.1/8.2/4000 in radian units",
            ],
            "metric_definitions": definitions,
            "tables_used": [table],
        },
    }


def _artifact_rows(tracking_rows, contrast_rows, timestamp_rows, estimator_rows):
    tracking_lookup = {
        (row["dataset"], row["method_id"]): row for row in tracking_rows
    }
    headline_rows = []
    method_ratios = []
    exact_metrics = []
    for dataset in ("no_velocity_limit", "velocity_limit"):
        baseline = tracking_lookup[(dataset, "p_only_latest")]
        production = tracking_lookup[
            (dataset, "centered_pva_delayed_clamped")
        ]
        p_nrmse = _float(baseline, "normalized_rmse_robust")
        production_nrmse = _float(production, "normalized_rmse_robust")
        headline_rows.append(
            {
                "dataset": dataset,
                "dataset_label": baseline["dataset_label"],
                "p_only_nrmse": p_nrmse,
                "production_pva_nrmse": production_nrmse,
                "production_vs_p_only_change_pct": (
                    100.0 * (production_nrmse - p_nrmse) / p_nrmse
                ),
                "p_only_lag_ms": abs(_float(baseline, "best_lag_ms")),
                "production_pva_lag_ms": abs(
                    _float(production, "best_lag_ms")
                ),
                "acceleration_hard_clamp_rate": _float(
                    production, "acceleration_hard_clamp_rate"
                ),
            }
        )
        for order, method_id in enumerate(METHOD_ORDER):
            row = tracking_lookup[(dataset, method_id)]
            nrmse = _float(row, "normalized_rmse_robust")
            method_ratios.append(
                {
                    "order": order,
                    "dataset": dataset,
                    "dataset_label": row["dataset_label"],
                    "method_id": method_id,
                    "method_label": METHOD_SHORT[method_id],
                    "nrmse": nrmse,
                    "p_only_nrmse": p_nrmse,
                    "nrmse_ratio_to_p_only": nrmse / p_nrmse,
                    "change_vs_p_only_pct": 100.0 * (nrmse - p_nrmse) / p_nrmse,
                    "best_lag_ms": _float(row, "best_lag_ms"),
                    "causal": _bool(row, "causal"),
                    "hard_clamp": _bool(row, "hard_clamp"),
                }
            )
            exact_metrics.append(
                {
                    "order": (
                        (0 if dataset == "no_velocity_limit" else 100) + order
                    ),
                    "dataset_label": row["dataset_label"],
                    "method_label": METHOD_SHORT[method_id],
                    "nrmse": nrmse,
                    "change_vs_p_only_pct": (
                        100.0 * (nrmse - p_nrmse) / p_nrmse
                    ),
                    "best_lag_ms": _float(row, "best_lag_ms"),
                    "lag_aligned_nrmse": _float(
                        row, "lag_aligned_normalized_rmse"
                    ),
                    "a_clamp_rate": _float(
                        row, "acceleration_hard_clamp_rate"
                    ),
                    "feasibility_projection_rate": _float(
                        row, "ruckig_feasibility_projection_rate"
                    ),
                    "preclamp_p99_a": _float(
                        row, "preclamp_p99_abs_acceleration_rad_s2"
                    ),
                    "preclamp_p99_jerk": _float(
                        row, "preclamp_p99_abs_sampled_jerk_rad_s3"
                    ),
                }
            )

    mechanism_rows = [
        {
            "order": order,
            "dataset": row["dataset"],
            "dataset_label": row["dataset_label"],
            "mechanism": row["mechanism"],
            "mechanism_label": MECHANISM_SHORT[row["mechanism"]],
            "contrast": row["contrast"],
            "absolute_change_as_p_only_latest_pct": _float(
                row, "absolute_change_as_p_only_latest_pct"
            ),
            "relative_change_vs_left_pct": _float(
                row, "relative_change_vs_left_pct"
            ),
            "left_nrmse": _float(row, "left_normalized_rmse_robust"),
            "right_nrmse": _float(row, "right_normalized_rmse_robust"),
        }
        for order, row in enumerate(contrast_rows)
    ]

    fixed_production = {
        row["dataset"]: row
        for row in tracking_rows
        if row["method_id"] == "centered_pva_delayed_clamped"
    }
    timestamp_sensitivity = []
    for row in timestamp_rows:
        if row["method_id"] != "centered_pva_delayed_clamped":
            continue
        fixed = fixed_production[row["dataset"]]
        fixed_nrmse = _float(fixed, "normalized_rmse_robust")
        proxy_nrmse = _float(row, "normalized_rmse_robust")
        timestamp_sensitivity.append(
            {
                "dataset": row["dataset"],
                "dataset_label": row["dataset_label"],
                "fixed_10ms_nrmse": fixed_nrmse,
                "timestamp_proxy_nrmse": proxy_nrmse,
                "timestamp_proxy_to_fixed_ratio": proxy_nrmse / fixed_nrmse,
                "timestamp_proxy_change_pct": (
                    100.0 * (proxy_nrmse - fixed_nrmse) / fixed_nrmse
                ),
                "fixed_a_clamp_rate": _float(
                    fixed, "acceleration_hard_clamp_rate"
                ),
                "timestamp_proxy_a_clamp_rate": _float(
                    row, "acceleration_hard_clamp_rate"
                ),
            }
        )

    estimator_table = [
        {
            "order": order,
            "dataset_label": row["dataset_label"],
            "time_basis": row["time_basis"],
            "rows": int(row["rows"]),
            "dt_min_ms": _float(row, "dt_min_ms"),
            "dt_p50_ms": _float(row, "dt_p50_ms"),
            "dt_max_ms": _float(row, "dt_max_ms"),
            "history_reset_gap_count": int(row["history_reset_gap_count"]),
            "a_noise_gain_p99_per_s2": _float(
                row, "acceleration_noise_gain_p99_per_s2"
            ),
            "a_noise_gain_max_per_s2": _float(
                row, "acceleration_noise_gain_max_per_s2"
            ),
        }
        for order, row in enumerate(estimator_rows)
    ]
    return {
        "headline_metrics": headline_rows,
        "method_ratios": method_ratios,
        "mechanism_rows": mechanism_rows,
        "exact_metrics": exact_metrics,
        "timestamp_sensitivity": timestamp_sensitivity,
        "estimator_table": estimator_table,
    }


def _fmt_pct(value):
    return f"{value:+.1f}%"


def build_artifact(results_dir):
    results_dir = Path(results_dir).resolve()
    connection, database_path = _build_database(results_dir)
    try:
        tracking_rows = _query(connection, TRACKING_SQL)
        contrast_rows = _query(connection, CONTRAST_SQL)
        timestamp_rows = _query(connection, TIMESTAMP_SQL)
        estimator_rows = _query(connection, ESTIMATOR_SQL)
    finally:
        connection.close()
    datasets = _artifact_rows(
        tracking_rows,
        contrast_rows,
        timestamp_rows,
        estimator_rows,
    )
    run = json.loads((results_dir / "run.json").read_text(encoding="utf-8"))
    generated_at = run["generated_at"]

    headline = {row["dataset"]: row for row in datasets["headline_metrics"]}
    no_limit = headline["no_velocity_limit"]
    velocity_limit = headline["velocity_limit"]
    tracking_lookup = {
        (row["dataset"], row["method_id"]): row
        for row in datasets["method_ratios"]
    }
    no_latest_p = tracking_lookup[
        ("no_velocity_limit", "centered_pva_latest_position_clamped")
    ]
    limited_latest_p = tracking_lookup[
        ("velocity_limit", "centered_pva_latest_position_clamped")
    ]
    no_timestamp = next(
        row
        for row in datasets["timestamp_sensitivity"]
        if row["dataset"] == "no_velocity_limit"
    )
    limited_timestamp = next(
        row
        for row in datasets["timestamp_sensitivity"]
        if row["dataset"] == "velocity_limit"
    )
    no_source_gain = next(
        row
        for row in datasets["estimator_table"]
        if row["dataset_label"].endswith("no velocity limit")
        and row["time_basis"] == "csv_timestamp_proxy"
    )

    sources = [
        _source(
            "tracking_metrics",
            "Fixed-grid tracking metrics",
            "tracking_metrics",
            TRACKING_SQL,
            (
                "Same-reference ordinary-Ruckig tracking metrics for the "
                "centered-PVA ablation matrix."
            ),
            [
                (
                    "Robust-scale NRMSE = position RMSE / "
                    "(reference P95 − reference P05)."
                ),
                (
                    "Production-like PVA emits q[k−1], centered v/a at k−1, "
                    "then independently clamps V and A."
                ),
                "Positive best lag means the output is late.",
            ],
        ),
        _source(
            "mechanism_decomposition",
            "Mechanism contrasts",
            "mechanism_decomposition",
            CONTRAST_SQL,
            (
                "Ordered pairwise changes that isolate target delay, "
                "derivative injection, clamp strategy, and age compensation."
            ),
            [
                (
                    "Absolute change as P-only latest (%) = "
                    "100 × (right NRMSE − left NRMSE) / P-only latest NRMSE."
                )
            ],
        ),
        _source(
            "timestamp_sensitivity",
            "CSV timestamp sensitivity",
            "timestamp_sensitivity",
            TIMESTAMP_SQL,
            (
                "Derivative-time sensitivity using the CSV timestamp column "
                "as an unverified header-stamp proxy."
            ),
            [
                (
                    "The Ruckig control period remains 10 ms; only derivative "
                    "estimation intervals change."
                )
            ],
        ),
        _source(
            "estimator_diagnostics",
            "Estimator coefficient diagnostics",
            "estimator_diagnostics",
            ESTIMATOR_SQL,
            (
                "Sampling intervals, reset counts, and finite-difference "
                "coefficient L1 noise gains."
            ),
            [
                (
                    "Acceleration noise gain = |c0| + |c1| + |c2| for the "
                    "nonuniform second-derivative stencil."
                )
            ],
        ),
    ]

    cards = []
    for dataset, label in (
        ("no_velocity_limit", "No-limit production PVA"),
        ("velocity_limit", "Velocity-limit production PVA"),
    ):
        cards.append(
            {
                "id": f"card_{dataset}",
                "dataset": "headline_metrics",
                "filter": {"dataset": dataset},
                "description": (
                    "Production-like centered PVA compared with P-only latest "
                    "on exactly the same reference samples."
                ),
                "sourceId": "tracking_metrics",
                "metrics": [
                    {
                        "label": f"{label} NRMSE",
                        "field": "production_pva_nrmse",
                        "format": "number",
                    },
                    {
                        "label": "vs P-only (%)",
                        "field": "production_vs_p_only_change_pct",
                        "format": "number",
                        "signed": True,
                    },
                    {
                        "label": "A hard-clamp rate",
                        "field": "acceleration_hard_clamp_rate",
                        "format": "percent",
                    },
                ],
            }
        )

    charts = [
        {
            "id": "nrmse_ratio_chart",
            "title": "Tracking NRMSE relative to P-only by method",
            "subtitle": (
                "The rough no-limit trace degrades under all centered target "
                "variants, while latest-position PVA helps the smoother trace"
            ),
            "showDescription": True,
            "intent": "comparison",
            "question": (
                "How does each target-state construction compare with the "
                "same-trace P-only baseline?"
            ),
            "rationale": (
                "Ratios put the two trajectories on a common baseline despite "
                "their very different absolute error scales."
            ),
            "type": "bar",
            "dataset": "method_ratios",
            "sourceId": "tracking_metrics",
            "encodings": {
                "x": {
                    "field": "method_label",
                    "type": "nominal",
                    "label": "Method",
                },
                "y": {
                    "field": "nrmse_ratio_to_p_only",
                    "type": "quantitative",
                    "label": "NRMSE / P-only NRMSE",
                },
                "color": {
                    "field": "dataset_label",
                    "type": "nominal",
                    "label": "Reference trajectory",
                },
                "tooltip": [
                    {"field": "nrmse", "label": "NRMSE"},
                    {
                        "field": "change_vs_p_only_pct",
                        "label": "Change vs P-only (%)",
                    },
                    {"field": "best_lag_ms", "label": "Best lag (ms)"},
                ],
            },
            "legend": {
                "position": "bottom",
                "sort": "labelAsc",
                "title": "Reference trajectory",
            },
            "referenceLines": [
                {
                    "axis": "y",
                    "value": 1.0,
                    "label": "P-only",
                    "color": "neutral",
                    "lineStyle": "dashed",
                }
            ],
            "settings": {
                "orientation": "vertical",
                "groupMode": "grouped",
                "categoryLabelPolicy": "rotate",
            },
            "surface": {"surface": "card", "showControls": False},
            "layout": "full",
        },
        {
            "id": "mechanism_chart",
            "title": "Tracking NRMSE change for each ablation step",
            "subtitle": (
                "Positive values worsen tracking; the clamp strategy dominates "
                "the rough trace, while position delay dominates the smooth trace"
            ),
            "showDescription": True,
            "intent": "comparison",
            "question": "Which mechanism accounts for the PVA regression?",
            "rationale": (
                "Sequential contrasts separate delay, derivatives, clamps, "
                "and age compensation at the same metric scale."
            ),
            "type": "bar",
            "dataset": "mechanism_rows",
            "sourceId": "mechanism_decomposition",
            "encodings": {
                "x": {
                    "field": "mechanism_label",
                    "type": "nominal",
                    "label": "Ablation step",
                },
                "y": {
                    "field": "absolute_change_as_p_only_latest_pct",
                    "type": "quantitative",
                    "label": "NRMSE change as % of P-only",
                },
                "color": {
                    "field": "dataset_label",
                    "type": "nominal",
                    "label": "Reference trajectory",
                },
                "tooltip": [
                    {"field": "contrast", "label": "Contrast"},
                    {"field": "left_nrmse", "label": "Left NRMSE"},
                    {"field": "right_nrmse", "label": "Right NRMSE"},
                ],
            },
            "legend": {
                "position": "bottom",
                "sort": "labelAsc",
                "title": "Reference trajectory",
            },
            "referenceLines": [
                {
                    "axis": "y",
                    "value": 0.0,
                    "label": "No change",
                    "color": "neutral",
                    "lineStyle": "solid",
                }
            ],
            "settings": {
                "orientation": "vertical",
                "groupMode": "grouped",
                "categoryLabelPolicy": "rotate",
            },
            "surface": {"surface": "card", "showControls": False},
            "layout": "full",
        },
        {
            "id": "timestamp_chart",
            "title": "Production-like PVA NRMSE under timestamp assumptions",
            "subtitle": (
                "Irregular source intervals increase derivative clipping and "
                "tracking error, especially on the no-limit trace"
            ),
            "showDescription": True,
            "intent": "comparison",
            "question": (
                "How sensitive is the production-like result to derivative "
                "sampling intervals?"
            ),
            "rationale": (
                "A ratio isolates derivative-time sensitivity while retaining "
                "the fixed 10 ms controller period."
            ),
            "type": "bar",
            "dataset": "timestamp_sensitivity",
            "sourceId": "timestamp_sensitivity",
            "encodings": {
                "x": {
                    "field": "dataset_label",
                    "type": "nominal",
                    "label": "Reference trajectory",
                },
                "y": {
                    "field": "timestamp_proxy_to_fixed_ratio",
                    "type": "quantitative",
                    "label": "Timestamp-proxy / fixed-grid NRMSE",
                },
                "tooltip": [
                    {
                        "field": "fixed_10ms_nrmse",
                        "label": "Fixed 10 ms NRMSE",
                    },
                    {
                        "field": "timestamp_proxy_nrmse",
                        "label": "Timestamp-proxy NRMSE",
                    },
                    {
                        "field": "timestamp_proxy_change_pct",
                        "label": "Change (%)",
                    },
                ],
            },
            "referenceLines": [
                {
                    "axis": "y",
                    "value": 1.0,
                    "label": "Fixed 10 ms",
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
    ]

    tables = [
        {
            "id": "exact_metrics_table",
            "title": "Exact same-reference tracking metrics",
            "subtitle": (
                "NRMSE, lag, derivative demand, clamp rate, and projection rate "
                "for every ablation method"
            ),
            "showDescription": True,
            "dataset": "exact_metrics",
            "sourceId": "tracking_metrics",
            "defaultSort": {"field": "order", "direction": "asc"},
            "density": "spacious",
            "layout": "full",
            "columns": [
                {"field": "order", "label": "Order", "format": "number"},
                {
                    "field": "dataset_label",
                    "label": "Trajectory",
                    "type": "text",
                },
                {"field": "method_label", "label": "Method", "type": "text"},
                {"field": "nrmse", "label": "NRMSE", "format": "number"},
                {
                    "field": "change_vs_p_only_pct",
                    "label": "vs P-only (%)",
                    "format": "number",
                },
                {
                    "field": "best_lag_ms",
                    "label": "Best lag (ms)",
                    "format": "number",
                },
                {
                    "field": "a_clamp_rate",
                    "label": "A clamp rate",
                    "format": "percent",
                },
                {
                    "field": "preclamp_p99_a",
                    "label": "Pre-clamp P99 |A|",
                    "format": "number",
                },
                {
                    "field": "preclamp_p99_jerk",
                    "label": "Pre-clamp P99 |ΔA/Δt|",
                    "format": "number",
                },
            ],
        },
        {
            "id": "estimator_table",
            "title": "Sampling and finite-difference amplification",
            "subtitle": (
                "No interval exceeds the 50 ms reset threshold; short uneven "
                "intervals increase derivative noise gain"
            ),
            "showDescription": True,
            "dataset": "estimator_table",
            "sourceId": "estimator_diagnostics",
            "defaultSort": {"field": "order", "direction": "asc"},
            "density": "spacious",
            "layout": "full",
            "columns": [
                {"field": "order", "label": "Order", "format": "number"},
                {
                    "field": "dataset_label",
                    "label": "Trajectory",
                    "type": "text",
                },
                {"field": "time_basis", "label": "Time basis", "type": "text"},
                {
                    "field": "dt_min_ms",
                    "label": "Min Δt (ms)",
                    "format": "number",
                },
                {
                    "field": "dt_max_ms",
                    "label": "Max Δt (ms)",
                    "format": "number",
                },
                {
                    "field": "history_reset_gap_count",
                    "label": "Gap resets",
                    "format": "number",
                },
                {
                    "field": "a_noise_gain_p99_per_s2",
                    "label": "P99 A noise gain (s⁻²)",
                    "format": "number",
                },
                {
                    "field": "a_noise_gain_max_per_s2",
                    "label": "Max A noise gain (s⁻²)",
                    "format": "number",
                },
            ],
        },
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
                "## 技术摘要\n\n"
                "**当前现象可以复现，但不能归因于“PVA 天生不如 "
                "P-only”。** 在同一无速度限制参考轨迹上，生产式中心差分 "
                f"PVA 的 robust NRMSE 为 {no_limit['production_pva_nrmse']:.6f}，"
                f"相对 P-only 的 {no_limit['p_only_nrmse']:.6f} 恶化 "
                f"{_fmt_pct(no_limit['production_vs_p_only_change_pct'])}；"
                "在更平滑的速度限制轨迹上仅恶化 "
                f"{_fmt_pct(velocity_limit['production_vs_p_only_change_pct'])}。"
                "差异主要来自一拍位置老化、二阶差分放大，以及 V/A 独立硬限幅"
                "后目标状态不再保持原来的局部运动学关系。\n\n"
                "更关键的反事实是：速度限制轨迹使用最新位置但保留中心差分 "
                f"V/A 时，NRMSE 比 P-only 低 "
                f"{abs(limited_latest_p['change_vs_p_only_pct']):.1f}%；"
                "无速度限制轨迹做同样处理仍恶化 "
                f"{no_latest_p['change_vs_p_only_pct']:.1f}%。"
                "因此，平滑轨迹中 PVA 有价值，粗糙且不可行的导数目标才是主要问题。"
            ),
            "layout": "full",
        },
        {
            "id": "headline_strip",
            "type": "metric-strip",
            "cardIds": [card["id"] for card in cards],
            "layout": "full",
        },
        {
            "id": "same_reference_finding",
            "type": "markdown",
            "body": (
                "## 同轨迹消融结果\n\n"
                "固定 10 ms 栅格、相同 Ruckig 限制和相同参考样本排除了"
                "跨轨迹几何差异。生产式 estimator 在 arrival k 输出 "
                "`q[k−1], v[k−1], a[k−1]`；runner 的 `target[k] → "
                "output[k+1]` 又意味着输出索引相对最新输入多一个控制周期。"
                "P-only 本身也有 runner 的一拍执行时序，但没有 estimator "
                "额外的一拍老化。图中 1.0 是每条轨迹自己的 P-only 基线。"
            ),
            "layout": "full",
        },
        {
            "id": "nrmse_ratio_block",
            "type": "chart",
            "chartId": "nrmse_ratio_chart",
            "layout": "full",
        },
        {
            "id": "mechanism_finding",
            "type": "markdown",
            "body": (
                "## 回归机制分解\n\n"
                "无速度限制轨迹中，仅位置延后一拍就让 NRMSE 增加约 "
                "5.9%；加入未独立硬限幅的中心 PVA 后再增加约 10.7% "
                "（均以 P-only NRMSE 为分母）。最显著的变化来自限幅策略："
                "独立裁剪 A、保留 V，相比把 V/A 联合缩放到可行域，额外增加"
                "约 111.9% 的 P-only NRMSE。这个对照是“独立裁剪 vs "
                "联合可行性投影”，不是让不可行目标原样进入 Ruckig。\n\n"
                "速度限制轨迹正相反：中心 V/A 能抵消一部分位置延迟，"
                "而把目标位置恢复到最新采样后，整体优于 P-only。"
            ),
            "layout": "full",
        },
        {
            "id": "mechanism_block",
            "type": "chart",
            "chartId": "mechanism_chart",
            "layout": "full",
        },
        {
            "id": "derivative_finding",
            "type": "markdown",
            "body": (
                "## 差分放大与限幅\n\n"
                "无速度限制轨迹的中心差分 P99 |A| 为 64.87 rad/s²，"
                "而 stream 上限为 8.2 rad/s²；约 24.9% 的目标 A 被硬限幅。"
                "它的最大 |V| 只有 3.15 rad/s，低于 4.1 rad/s 上限，"
                "所以实际发生的是高度不对称的“保留 V、裁剪 A”。"
                "速度限制轨迹的 P99 |A| 为 7.07 rad/s²，只有约 0.83% "
                "发生 A 限幅。P-only 的零 V/A 因而起到强正则化作用："
                "它不向每次重规划注入高频、局部不可行的终端导数。\n\n"
                "硬限幅后的边界目标会触发严格可行性检查，但后续投影只产生"
                "约 1e−8 量级的数值变化，因此该安全投影不是回归主因。"
            ),
            "layout": "full",
        },
        {
            "id": "timestamp_finding",
            "type": "markdown",
            "body": (
                "## 时间戳敏感性\n\n"
                "CSV 的 `timestamp` 列不能证明就是生产 "
                "`JointState.header.stamp`，因此这里只将其作为敏感性代理。"
                "无速度限制 CSV 的间隔范围为约 2.71–22.08 ms，二阶差分"
                f"系数 L1 增益最大达到 {no_source_gain['a_noise_gain_max_per_s2']:.0f} "
                "s⁻²，是固定 10 ms 的约 5.6 倍；production-like PVA "
                f"NRMSE 再增加 {_fmt_pct(no_timestamp['timestamp_proxy_change_pct'])}。"
                "速度限制 CSV 的间隔更规则，代理时间戳仍使 NRMSE 增加 "
                f"{_fmt_pct(limited_timestamp['timestamp_proxy_change_pct'])}。"
                "两条轨迹都没有超过 50 ms 的间隔，所以本实验中历史 reset "
                "不是原因。"
            ),
            "layout": "full",
        },
        {
            "id": "timestamp_block",
            "type": "chart",
            "chartId": "timestamp_chart",
            "layout": "full",
        },
        {
            "id": "definitions",
            "type": "markdown",
            "body": (
                "## 范围、数据与指标定义\n\n"
                "输入为 `simplified-tasks_no-velocity-limit.csv` 和 "
                "`simplified-tasks_velocity-limit.csv`。主实验只使用 position "
                "`value`，每行固定 10 ms；V/A estimator 使用三个点并在中心"
                "时间求导。Ruckig 限制为 4.1 rad/s、8.2 rad/s²、"
                "4000 rad/s³。Robust NRMSE = tracking RMSE / "
                "(position P95 − P05)，评估区间为输出索引 `[3, n)`。"
            ),
            "layout": "full",
        },
        {
            "id": "exact_table_block",
            "type": "table",
            "tableId": "exact_metrics_table",
            "layout": "full",
        },
        {
            "id": "estimator_table_block",
            "type": "table",
            "tableId": "estimator_table",
            "layout": "full",
        },
        {
            "id": "methodology",
            "type": "markdown",
            "body": (
                "## 方法与可复现性\n\n"
                "`scripts/analyze_centered_pva_regression.py` 构造八个目标状态"
                "变体，运行同一个 ordinary-Ruckig runner，并保存逐样本、聚合、"
                "机制分解和时间戳敏感性表。`otg_lab/centered_pva_analysis.py` "
                "实现非等间隔三点二次插值、50 ms reset、生产式一拍延迟和 "
                "V/A 独立硬限幅。前两个 estimator 预热周期在 runner 中用"
                "保持上一目标近似，因为真实节点在此期间不产生新 target。"
            ),
            "layout": "full",
        },
        {
            "id": "limitations",
            "type": "markdown",
            "body": (
                "## 限制与不确定性\n\n"
                "- 当前 worktree 不包含用户所指的生产 C++ 文件；公式符号依据"
                "三点二次插值和等间隔退化式实现，仍应和真实 header 做逐行核对。\n"
                "- CSV 没有明确保存 JointState header stamp；timestamp 结果"
                "只能作为敏感性分析。\n"
                "- runner 在 estimator 预热时用 hold-last 近似，真实节点可能"
                "暂停更新或采用不同 fallback。\n"
                "- ordinary-Ruckig harness 复现的是算法机制，不包含机器人 plant、"
                "通信、反馈噪声和多关节同步。\n"
                "- 两条 CSV 各只有一次记录；结论适合定位机制，不代表总体分布。"
            ),
            "layout": "full",
        },
        {
            "id": "recommendations",
            "type": "markdown",
            "body": (
                "## 建议\n\n"
                "1. 先修正时间对齐：arrival k 不要直接把 center k−1 的完整状态"
                "当作当前目标；至少使用最新 P，并对 V 做有界传播。\n"
                "2. 不要独立裁剪 V/A 后假设状态仍一致；使用联合 target-state "
                "projection，或在滤波/拟合阶段直接加入 V/A/J 约束。\n"
                "3. 对 position 先做抗噪平滑，再求导；监控 A clamp rate、"
                "target ΔA/Δt 和 timestamp jitter。\n"
                "4. 当 A clamp rate 持续偏高或 source Δt 过短时，自动降级到 "
                "P-only 或可信的 PV，而不是继续注入饱和 A。\n"
                "5. 用真实 JointState.header.stamp 和节点调用时刻录制一条 MCAP，"
                "逐周期核对 target age、clamp、Ruckig result 和 tracking error。"
            ),
            "layout": "full",
        },
        {
            "id": "further_questions",
            "type": "markdown",
            "body": (
                "## 后续待确认\n\n"
                "- 生产配置中的 `stream.max_velocity/max_acceleration` 是否确实"
                "为本实验使用的 4.1/8.2？\n"
                "- C++ 实现的非等间隔系数符号是否与标准 Lagrange 插值一致？\n"
                "- estimator 无输出的预热/reset 周期，joint controller 是保持"
                "上个 target、跳过 Ruckig，还是发送零导数？\n"
                "- Ruckig 收到 stamp=t1 的目标后是否做任何 age compensation？"
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
                "Production-semantics centered-difference PVA regression "
                "reproduction and mechanism ablation."
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
            "datasets": datasets,
        },
        "sources": sources,
        "package_info": {
            "report_kind": "technical",
            "development_only": True,
            "primary_comparison": (
                "production-like centered PVA versus latest-position P-only "
                "on the same reference"
            ),
        },
    }
    return artifact, database_path


def _html_table(rows, columns):
    head = "".join(f"<th>{html.escape(label)}</th>" for _field, label in columns)
    body = []
    for row in rows:
        cells = []
        for field, _label in columns:
            value = row[field]
            if isinstance(value, float):
                rendered = f"{value:.6g}"
            else:
                rendered = str(value)
            cells.append(f"<td>{html.escape(rendered)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<div class='table-wrap'><table><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def write_static_html(artifact, results_dir):
    datasets = artifact["snapshot"]["datasets"]
    headline = {row["dataset"]: row for row in datasets["headline_metrics"]}
    no_limit = headline["no_velocity_limit"]
    velocity_limit = headline["velocity_limit"]
    exact = datasets["exact_metrics"]
    estimator = datasets["estimator_table"]
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(REPORT_TITLE)}</title>
<style>
:root {{ color-scheme: light; --ink:#20262d; --muted:#5d6670; --line:#d8dee4;
  --panel:#f6f8fa; --accent:#1f5a85; --warn:#b5483a; }}
body {{ margin:0; font:16px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  color:var(--ink); background:white; }}
main {{ max-width:1100px; margin:auto; padding:48px 28px 80px; }}
h1 {{ font-size:2.35rem; margin-bottom:.25rem; }}
h2 {{ margin-top:2.7rem; border-top:1px solid var(--line); padding-top:1.5rem; }}
.subtitle {{ color:var(--muted); margin-bottom:2rem; }}
.summary {{ background:var(--panel); border-left:5px solid var(--accent);
  padding:20px 24px; border-radius:8px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
  gap:16px; margin:24px 0; }}
.card {{ border:1px solid var(--line); border-radius:10px; padding:18px; }}
.metric {{ font-size:1.75rem; font-weight:700; }}
.worse {{ color:var(--warn); }}
figure {{ margin:28px 0; }}
img {{ width:100%; height:auto; border:1px solid var(--line); border-radius:8px; }}
figcaption {{ color:var(--muted); font-size:.92rem; margin-top:8px; }}
.table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
th,td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:right;
  white-space:nowrap; }}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2) {{ text-align:left; }}
th {{ background:var(--panel); position:sticky; top:0; }}
code {{ background:#eef1f4; padding:.1em .3em; border-radius:4px; }}
li {{ margin:.45rem 0; }}
</style>
</head>
<body><main>
<h1>{html.escape(REPORT_TITLE)}</h1>
<p class="subtitle">同轨迹、同控制器条件下，复现并拆解 centered-difference PVA
相对 P-only 的性能回归。</p>
<section class="summary"><strong>结论：</strong>不是 PVA 信息本身有害，而是
<code>q[k−1]</code> 的一拍老化、位置二阶差分放大、以及独立裁剪 V/A 后的
目标状态不一致叠加造成回归。无速度限制轨迹中 production-like PVA 比
P-only 恶化 {no_limit['production_vs_p_only_change_pct']:.1f}%；速度限制轨迹
只恶化 {velocity_limit['production_vs_p_only_change_pct']:.1f}%，且改用最新位置后
可以优于 P-only。</section>
<div class="cards">
<div class="card"><div>无速度限制 · P-only NRMSE</div>
<div class="metric">{no_limit['p_only_nrmse']:.6f}</div>
<div>Production-like: <span class="worse">{no_limit['production_pva_nrmse']:.6f}
({no_limit['production_vs_p_only_change_pct']:+.1f}%)</span></div></div>
<div class="card"><div>速度限制 · P-only NRMSE</div>
<div class="metric">{velocity_limit['p_only_nrmse']:.6f}</div>
<div>Production-like: <span class="worse">{velocity_limit['production_pva_nrmse']:.6f}
({velocity_limit['production_vs_p_only_change_pct']:+.1f}%)</span></div></div>
</div>
<h2>同轨迹消融</h2>
<p>生产 estimator 在 arrival k 输出中心点 k−1 的 P/V/A；ordinary-Ruckig
runner 使用 <code>target[k] → output[k+1]</code>。因此相对最新输入，
production-like 目标比 latest-P 的 P-only 多一拍老化。</p>
<figure><img src="nrmse_ablation.png" alt="NRMSE ablation">
<figcaption>所有方法使用相同参考、10 ms 控制周期和 4.1/8.2/4000 限制；
纵轴为 log scale。</figcaption></figure>
<h2>导数限幅与跟踪误差</h2>
<p>无速度限制轨迹的 P99 |A| 为 64.87 rad/s²，约 24.9% 的 A 目标被裁到
±8.2；速度限制轨迹的 P99 |A| 为 7.07 rad/s²，只有约 0.83% 被裁剪。
P-only 的零 V/A 在粗糙、局部不可行的输入上相当于强正则化。</p>
<figure><img src="target_diagnostics.png" alt="Target acceleration diagnostics">
<figcaption>原始中心差分 A、独立硬限幅 A、Ruckig 可行性投影后的 A，以及
P-only/production-like 的绝对跟踪误差。</figcaption></figure>
<h2>轨迹对比</h2>
<figure><img src="tracking_comparison.png" alt="Tracking comparison">
<figcaption>参考、P-only latest、production-like PVA 和 latest-position PVA。</figcaption>
</figure>
<h2>精确指标</h2>
{_html_table(exact, (
    ("dataset_label", "Trajectory"),
    ("method_label", "Method"),
    ("nrmse", "NRMSE"),
    ("change_vs_p_only_pct", "vs P-only (%)"),
    ("best_lag_ms", "Lag (ms)"),
    ("a_clamp_rate", "A clamp rate"),
    ("preclamp_p99_a", "P99 |A|"),
))}
<h2>时间戳与差分放大</h2>
<p>CSV timestamp 仅作为 header stamp 的未验证代理。无速度限制 CSV 的
Δt 为 2.71–22.08 ms，最大二阶差分系数 L1 增益约为固定 10 ms 的 5.6 倍。
两条轨迹都没有超过 50 ms，因此 reset 不是本次回归原因。</p>
{_html_table(estimator, (
    ("dataset_label", "Trajectory"),
    ("time_basis", "Time basis"),
    ("dt_min_ms", "Min Δt (ms)"),
    ("dt_max_ms", "Max Δt (ms)"),
    ("history_reset_gap_count", "Gap resets"),
    ("a_noise_gain_max_per_s2", "Max A gain (s⁻²)"),
))}
<h2>建议</h2>
<ol>
<li>优先消除 target age：至少使用最新 P，并明确 V/A 是中心点还是当前时刻。</li>
<li>不要独立裁剪 V/A 后假设状态仍自洽；采用联合可行域投影或受约束拟合。</li>
<li>position 先滤波再求导，并监控 A clamp rate、target ΔA/Δt 与 timestamp jitter。</li>
<li>当导数饱和率高时降级为 P-only/可信 PV，而不是持续注入饱和 A。</li>
<li>用真实 header stamp、回调时刻、clamp 和 Ruckig result 做逐周期在线复核。</li>
</ol>
<h2>限制</h2>
<p>当前 worktree 不含生产 C++ 文件；timestamp provenance 和 estimator
预热期间 controller 的真实行为仍需确认。该实验是单关节 ordinary-Ruckig
机制复现，不含 plant、通信噪声和多关节同步。</p>
</main></body></html>
"""
    path = Path(results_dir) / "report.html"
    path.write_text(document, encoding="utf-8")
    return path


def write_readme(results_dir):
    content = """# Centered-difference PVA regression

This directory reproduces the production-semantics centered-difference target:
arrival `k` emits P/V/A evaluated at `k-1`, V/A are independently hard-clamped,
and the target is passed to the ordinary-Ruckig runner.

Primary outputs:

- `report.html`: standalone Chinese technical report.
- `artifact.json`: validated canonical Data Analytics report payload.
- `artifact_package.tar.gz`: exported report runtime package.
- `tracking_metrics.csv`: same-reference method metrics.
- `mechanism_decomposition.csv`: ordered delay/derivative/clamp ablations.
- `timestamp_sensitivity.csv`: CSV-timestamp derivative sensitivity.
- `validation.json`: independent numeric and structural validation.

Reproduce from the repository root:

```bash
uv run --frozen python scripts/analyze_centered_pva_regression.py
uv run --frozen python scripts/build_centered_pva_regression_report.py
uv run --frozen python scripts/validate_centered_pva_regression.py \
  --mcp-validation-passed
```

The final validation flag records that `artifact.json` was separately accepted
by the Data Analytics artifact validator. CSV timestamp is an unverified proxy
for `JointState.header.stamp`, not part of the primary fixed-10-ms result.
"""
    path = Path(results_dir) / "README.md"
    path.write_text(content, encoding="utf-8")
    return path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the centered-PVA regression technical report."
    )
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    return parser.parse_args()


def main():
    args = parse_args()
    results_dir = args.results_dir.resolve()
    artifact, database_path = build_artifact(results_dir)
    artifact_path = results_dir / "artifact.json"
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path = write_static_html(artifact, results_dir)
    readme_path = write_readme(results_dir)
    print(f"Saved: {artifact_path}")
    print(f"Saved: {database_path}")
    print(f"Saved: {report_path}")
    print(f"Saved: {readme_path}")


if __name__ == "__main__":
    main()
