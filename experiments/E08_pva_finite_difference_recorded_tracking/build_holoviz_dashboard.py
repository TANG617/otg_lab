# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "holoviews==1.23.1",
#   "hvplot==0.12.2",
#   "pandas==2.3.3",
#   "panel==1.9.3",
# ]
# ///
"""Serve an interactive HoloViz dashboard for one completed E08 run."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import holoviews as hv
import hvplot.pandas  # noqa: F401
import pandas as pd
import panel as pn
import param
from dashboard_data import (
    BASELINE_METHOD_ID,
    LINE_STYLES,
    MAX_ACCELERATION_RAD_S2,
    MAX_VELOCITY_RAD_S,
    METHOD_LABELS,
    METHOD_ORDER,
    PVA_METHODS,
    REFERENCE_LABEL,
    REFERENCE_METHOD_ID,
    E08DashboardData,
    load_dashboard_data,
)
from holoviews.operation.downsample import downsample1d
from holoviews.plotting.links import RangeToolLink

pn.extension(
    "tabulator",
    notifications=True,
    sizing_mode="stretch_width",
)
hv.extension("bokeh")

METHOD_COLORS = {
    REFERENCE_METHOD_ID: "#111827",
    BASELINE_METHOD_ID: "#64748B",
    "pva_est_backward_o1_k": "#2563EB",
    "pva_est_backward_o2_k": "#B45309",
    "pva_est_centered_o2_km1": "#3F6212",
    "pva_pred_backward_o1_kp1": "#BE185D",
    "pva_pred_backward_o2_kp1": "#0891B2",
}
TRIGGER_COLORS = {
    "acceleration limit": "#B45309",
    "velocity limit": "#BE185D",
    "stopping envelope": "#2563EB",
}
LABEL_TO_METHOD = {label: method_id for method_id, label in METHOD_LABELS.items()}
PVA_LABELS = [METHOD_LABELS[method_id] for method_id in PVA_METHODS]


class DashboardControls(param.Parameterized):
    """Per-browser-session HoloViz controls."""

    selected_methods = param.ListSelector(
        default=list(METHOD_LABELS.values()),
        objects=list(METHOD_LABELS.values()),
        doc="Tracking methods shown in the linked position and error views.",
    )
    show_projections = param.Boolean(
        default=True,
        doc="Overlay every exact raw-to-executable target projection event.",
    )
    audit_method = param.Selector(
        default=PVA_LABELS[0],
        objects=PVA_LABELS,
        doc="PVA method shown in the raw/executable target audit.",
    )


def _frames(data: E08DashboardData) -> dict[str, pd.DataFrame]:
    return {
        "position": pd.DataFrame(data.position_series),
        "error": pd.DataFrame(data.error_series),
        "targets": pd.DataFrame(data.target_audit),
        "events": pd.DataFrame(data.projection_events),
        "acceptance": pd.DataFrame(data.acceptance_methods),
        "candidates": pd.DataFrame(data.acceptance_candidates),
        "feasibility": pd.DataFrame(data.raw_feasibility),
    }


def _curve(
    frame: pd.DataFrame,
    *,
    value_field: str,
    value_label: str,
    label: str,
    color: str,
    line_dash: str = "solid",
    line_width: float = 1.5,
) -> Any:
    element = hv.Curve(
        frame,
        kdims=[("time_s", "Time [s]")],
        vdims=[
            (value_field, value_label),
            ("sample_index", "Sample"),
            ("method_label", "Method"),
        ],
        label=label,
    )
    sampled = downsample1d(
        element,
        algorithm="lttb",
        dynamic=True,
    )
    return sampled.opts(
        color=color,
        line_dash=line_dash,
        line_width=line_width,
        tools=["hover"],
        hover_mode="vline",
        muted_alpha=0.08,
    )


def _overlay(items: list[Any]) -> Any:
    if not items:
        return hv.Overlay()
    overlay = items[0]
    for item in items[1:]:
        overlay *= item
    return overlay


def _projection_points(
    events: pd.DataFrame,
    *,
    value_field: str,
    method_ids: list[str],
) -> Any:
    points: list[Any] = []
    for method_id in method_ids:
        method_events = events[events["method_id"] == method_id]
        if method_events.empty:
            continue
        points.append(
            hv.Scatter(
                method_events,
                kdims=[("time_s", "Time [s]")],
                vdims=[
                    (value_field, value_field),
                    ("cycle_index", "Cycle"),
                    ("method_label", "Method"),
                    ("trigger", "Trigger"),
                    ("raw_velocity_rad_s", "Raw V [rad/s]"),
                    ("raw_acceleration_rad_s2", "Raw A [rad/s²]"),
                    ("executable_velocity_rad_s", "Executable V [rad/s]"),
                    ("executable_acceleration_rad_s2", "Executable A [rad/s²]"),
                ],
                label=f"{METHOD_LABELS[method_id]} projections",
            ).opts(
                color=METHOD_COLORS[method_id],
                marker="x",
                size=7,
                line_width=1.4,
                alpha=0.72,
                tools=["hover"],
                muted_alpha=0.08,
            )
        )
    return _overlay(points)


def _tracking_view(
    selected_labels: list[str],
    show_projections: bool,
    *,
    frames: dict[str, pd.DataFrame],
    duration_s: float,
) -> Any:
    method_ids = [
        LABEL_TO_METHOD[label]
        for label in selected_labels
        if label in LABEL_TO_METHOD
    ]
    position = frames["position"]
    error = frames["error"]
    events = frames["events"]
    reference = position[position["method_id"] == REFERENCE_METHOD_ID]

    position_layers = [
        _curve(
            reference,
            value_field="position_rad",
            value_label="Position [rad]",
            label=REFERENCE_LABEL,
            color=METHOD_COLORS[REFERENCE_METHOD_ID],
            line_width=2.0,
        )
    ]
    error_layers: list[Any] = []
    for method_id in method_ids:
        method_position = position[position["method_id"] == method_id]
        method_error = error[error["method_id"] == method_id]
        position_layers.append(
            _curve(
                method_position,
                value_field="position_rad",
                value_label="Position [rad]",
                label=METHOD_LABELS[method_id],
                color=METHOD_COLORS[method_id],
                line_dash=LINE_STYLES[method_id],
            )
        )
        error_layers.append(
            _curve(
                method_error,
                value_field="position_error_rad",
                value_label="Command − reference [rad]",
                label=METHOD_LABELS[method_id],
                color=METHOD_COLORS[method_id],
                line_dash=LINE_STYLES[method_id],
            )
        )

    position_plot = _overlay(position_layers)
    error_plot = _overlay(error_layers) * hv.HLine(0).opts(
        color="#374151",
        line_width=1,
    )
    if show_projections and method_ids:
        position_plot *= _projection_points(
            events,
            value_field="command_position_rad",
            method_ids=method_ids,
        )
        error_plot *= _projection_points(
            events,
            value_field="position_error_rad",
            method_ids=method_ids,
        )

    position_plot = position_plot.opts(
        height=540,
        responsive=True,
        show_grid=True,
        gridstyle={"grid_line_alpha": 0.16},
        legend_position="bottom",
        legend_cols=3,
        click_policy="mute",
        show_legend=True,
        title="Position reference and tracking commands",
        xlabel="Time [s]",
        ylabel="Position [rad]",
        tools=["xpan", "xwheel_zoom", "box_zoom", "reset"],
        active_tools=["xwheel_zoom"],
        xlim=(0, duration_s),
    )
    error_plot = error_plot.opts(
        height=380,
        responsive=True,
        show_grid=True,
        gridstyle={"grid_line_alpha": 0.16},
        legend_position="bottom",
        legend_cols=3,
        click_policy="mute",
        show_legend=True,
        title="Raw-time position error",
        xlabel="Time [s]",
        ylabel="Command − reference [rad]",
        tools=["xpan", "xwheel_zoom", "box_zoom", "reset"],
        active_tools=["xwheel_zoom"],
        xlim=(0, duration_s),
    )

    selected_events = events[events["method_id"].isin(method_ids)]
    if selected_events.empty:
        timeline = hv.Scatter([]).opts(
            height=230,
            responsive=True,
            title="Target projection events over time",
            xlim=(0, duration_s),
        )
    else:
        timeline = hv.Scatter(
            selected_events,
            kdims=[("time_s", "Command time [s]")],
            vdims=[
                ("method_rank", "Method row"),
                ("method_label", "Method"),
                ("cycle_index", "Cycle"),
                ("trigger", "Trigger"),
                ("velocity_projection_rad_s", "ΔV [rad/s]"),
                ("acceleration_projection_rad_s2", "ΔA [rad/s²]"),
            ],
        ).opts(
            color="trigger",
            cmap=TRIGGER_COLORS,
            marker="circle",
            size=6,
            alpha=0.72,
            height=230,
            responsive=True,
            show_grid=True,
            gridstyle={"grid_line_alpha": 0.12},
            title="Target projection events over time",
            xlabel="Command time [s]",
            ylabel="PVA method",
            yticks=[
                (rank, METHOD_LABELS[method_id])
                for rank, method_id in enumerate(METHOD_ORDER)
                if method_id in PVA_METHODS
            ],
            tools=["hover", "xpan", "xwheel_zoom", "box_zoom", "reset"],
            active_tools=["xwheel_zoom"],
            xlim=(0, duration_s),
        )

    overview = hv.Curve(
        reference,
        kdims=[("time_s", "Time [s]")],
        vdims=[("position_rad", "Recorded position [rad]")],
    ).opts(
        color="#64748B",
        line_width=1,
        height=140,
        responsive=True,
        show_grid=False,
        toolbar=None,
        yaxis=None,
        title="Overview — drag the handles to inspect a time window",
        xlim=(0, duration_s),
    )
    RangeToolLink(
        overview,
        position_plot,
        axes=["x"],
        boundsx=(0, duration_s),
        use_handles=True,
    )
    return (position_plot + error_plot + timeline + overview).cols(1).opts(
        shared_axes=True,
        merge_tools=True,
    )


def _comparison_view(
    *,
    frames: dict[str, pd.DataFrame],
) -> pn.Column:
    candidates = frames["candidates"].copy()
    candidates = candidates.dropna(subset=["rmse_ratio_vs_p"]).sort_values(
        "rmse_ratio_vs_p",
        ascending=True,
    )
    rmse_chart = candidates.hvplot.barh(
        x="method_label",
        y="rmse_ratio_vs_p",
        color="#2563EB",
        alpha=0.86,
        height=330,
        responsive=True,
        xlabel="RMSE ratio vs P-only",
        ylabel="",
        title="PVA position RMSE ratio versus P-only",
        hover_cols=[
            "position_rmse_rad",
            "projection_count",
            "scientific_status",
        ],
        tools=["hover"],
    ) * hv.HLine(1).opts(
        color="#374151",
        line_dash="dashed",
        line_width=1.5,
    )
    projection_chart = candidates.sort_values(
        "projection_rate",
        ascending=True,
    ).hvplot.barh(
        x="method_label",
        y="projection_rate",
        color="#B45309",
        alpha=0.84,
        height=330,
        responsive=True,
        xlabel="Projection rate",
        ylabel="",
        title="Configured-limit projection rate",
        hover_cols=["projection_count", "first_projection_cycle_index"],
        tools=["hover"],
    )
    acceptance_columns = [
        "method_label",
        "position_rmse_rad",
        "rmse_ratio_vs_p",
        "projection_count",
        "projection_rate",
        "guardrail_pass",
        "scientific_status",
    ]
    feasibility_columns = [
        "method_label",
        "target_velocity_max_abs_rad_s",
        "target_acceleration_max_abs_rad_s2",
        "target_acceleration_p95_abs_rad_s2",
        "acceleration_limit_violation_count",
        "ruckig_inadmissible_count",
        "first_inadmissible_cycle_index",
    ]
    acceptance_table = pn.widgets.Tabulator(
        frames["candidates"][acceptance_columns],
        disabled=True,
        pagination="local",
        page_size=8,
        show_index=False,
        sizing_mode="stretch_width",
        height=265,
    )
    feasibility_table = pn.widgets.Tabulator(
        frames["feasibility"][feasibility_columns],
        disabled=True,
        pagination="local",
        page_size=8,
        show_index=False,
        sizing_mode="stretch_width",
        height=265,
    )
    return pn.Column(
        pn.pane.Markdown(
            "Values below **1.0** improve on the P-only baseline. "
            "The two bar charts intentionally use separate scales."
        ),
        pn.Row(rmse_chart, projection_chart),
        pn.pane.Markdown("### Exact acceptance results"),
        acceptance_table,
        pn.pane.Markdown("### Unprojected target feasibility"),
        feasibility_table,
    )


def _audit_plot(
    audit_label: str,
    selection: list[int],
    *,
    frames: dict[str, pd.DataFrame],
    event_table: pn.widgets.Tabulator,
) -> Any:
    method_id = LABEL_TO_METHOD[audit_label]
    targets = frames["targets"]
    method_targets = targets[targets["method_id"] == method_id]
    method_events = frames["events"]
    method_events = method_events[method_events["method_id"] == method_id]

    velocity = _overlay(
        [
            _curve(
                method_targets,
                value_field="raw_velocity_rad_s",
                value_label="Raw target V [rad/s]",
                label="Raw target V",
                color="#B45309",
                line_dash="dashed",
            ),
            _curve(
                method_targets,
                value_field="executable_velocity_rad_s",
                value_label="Executable target V [rad/s]",
                label="Executable target V",
                color="#2563EB",
                line_width=1.8,
            ),
        ]
    )
    acceleration = _overlay(
        [
            _curve(
                method_targets,
                value_field="raw_acceleration_rad_s2",
                value_label="Raw target A [rad/s²]",
                label="Raw target A",
                color="#B45309",
                line_dash="dashed",
            ),
            _curve(
                method_targets,
                value_field="executable_acceleration_rad_s2",
                value_label="Executable target A [rad/s²]",
                label="Executable target A",
                color="#2563EB",
                line_width=1.8,
            ),
        ]
    )
    if not method_events.empty:
        velocity *= hv.Scatter(
            method_events,
            kdims=[("time_s", "Command time [s]")],
            vdims=[
                ("raw_velocity_rad_s", "Raw V [rad/s]"),
                ("cycle_index", "Cycle"),
                ("trigger", "Trigger"),
                ("executable_velocity_rad_s", "Executable V [rad/s]"),
            ],
            label="Projection events",
        ).opts(
            color="#9A3412",
            marker="x",
            size=7,
            line_width=1.4,
            tools=["hover"],
        )
        acceleration *= hv.Scatter(
            method_events,
            kdims=[("time_s", "Command time [s]")],
            vdims=[
                ("raw_acceleration_rad_s2", "Raw A [rad/s²]"),
                ("cycle_index", "Cycle"),
                ("trigger", "Trigger"),
                ("executable_acceleration_rad_s2", "Executable A [rad/s²]"),
            ],
            label="Projection events",
        ).opts(
            color="#9A3412",
            marker="x",
            size=7,
            line_width=1.4,
            tools=["hover"],
        )

    velocity *= hv.HLine(MAX_VELOCITY_RAD_S).opts(
        color="#374151",
        line_dash="dotted",
    )
    velocity *= hv.HLine(-MAX_VELOCITY_RAD_S).opts(
        color="#374151",
        line_dash="dotted",
    )
    acceleration *= hv.HLine(MAX_ACCELERATION_RAD_S2).opts(
        color="#374151",
        line_dash="dotted",
    )
    acceleration *= hv.HLine(-MAX_ACCELERATION_RAD_S2).opts(
        color="#374151",
        line_dash="dotted",
    )

    if selection and selection[0] < len(event_table.value):
        selected_time = float(event_table.value.iloc[selection[0]]["time_s"])
        focus_line = hv.VLine(selected_time).opts(
            color="#111827",
            line_dash="dotdash",
            line_width=1.5,
        )
        velocity *= focus_line
        acceleration *= focus_line

    common_options = {
        "height": 320,
        "responsive": True,
        "show_grid": True,
        "gridstyle": {"grid_line_alpha": 0.16},
        "legend_position": "bottom",
        "show_legend": True,
        "tools": ["xpan", "xwheel_zoom", "box_zoom", "reset"],
        "active_tools": ["xwheel_zoom"],
    }
    velocity = velocity.opts(
        **common_options,
        title=f"{audit_label} — raw and executable target velocity",
        xlabel="Command time [s]",
        ylabel="Target velocity [rad/s]",
    )
    acceleration = acceleration.opts(
        **common_options,
        title=f"{audit_label} — raw and executable target acceleration",
        xlabel="Command time [s]",
        ylabel="Target acceleration [rad/s²]",
    )
    return (velocity + acceleration).cols(1).opts(
        shared_axes=True,
        merge_tools=True,
    )


def _event_detail(
    selection: list[int],
    *,
    event_table: pn.widgets.Tabulator,
) -> str:
    if not selection or selection[0] >= len(event_table.value):
        return (
            "Select a projection event in the table to mark its command time "
            "on both target plots and inspect its exact raw→executable delta."
        )
    row = event_table.value.iloc[selection[0]]
    return (
        f"**Cycle {int(row['cycle_index'])} · t={row['time_s']:.3f} s · "
        f"{row['trigger']}**  \n"
        f"V: `{row['raw_velocity_rad_s']:.6g}` → "
        f"`{row['executable_velocity_rad_s']:.6g}` rad/s "
        f"(Δ `{row['velocity_projection_rad_s']:+.6g}`)  \n"
        f"A: `{row['raw_acceleration_rad_s2']:.6g}` → "
        f"`{row['executable_acceleration_rad_s2']:.6g}` rad/s² "
        f"(Δ `{row['acceleration_projection_rad_s2']:+.6g}`)"
    )


def _headline(data: E08DashboardData) -> str:
    metrics = data.overview_metrics
    best_ratio = metrics["best_pva_ratio"]
    best_method = metrics["best_pva_method"]
    guardrail_failures = sum(
        1 for row in data.acceptance_candidates if not row["guardrail_pass"]
    )
    if best_ratio is None:
        comparison = "No completed PVA candidate has a comparable RMSE."
    elif best_ratio < 1:
        comparison = (
            f"**{best_method}** is the best PVA candidate at "
            f"**{best_ratio:.3f}×** the P-only RMSE."
        )
    else:
        comparison = (
            f"No PVA candidate beats P-only; **{best_method}** is closest at "
            f"**{best_ratio:.3f}×** the baseline RMSE."
        )
    guardrail = (
        "All candidate guardrails pass."
        if guardrail_failures == 0
        else f"**{guardrail_failures}** candidate has a guardrail regression."
    )
    return (
        "### Scientific readout\n\n"
        f"{comparison} {guardrail} All "
        f"**{metrics['completed_method_count']}** methods complete the declared "
        f"{metrics['tracking_cycles']:,}-cycle replay."
    )


def _kpi_strip(data: E08DashboardData) -> pn.GridBox:
    metrics = data.overview_metrics
    common = {
        "font_size": "28pt",
        "title_size": "12pt",
        "height": 92,
        "sizing_mode": "stretch_width",
    }
    cards = [
        pn.indicators.Number(
            name="Duration [s]",
            value=metrics["duration_s"],
            format="{value:.2f}",
            **common,
        ),
        pn.indicators.Number(
            name="Tracking cycles",
            value=metrics["tracking_cycles"],
            format="{value:,.0f}",
            **common,
        ),
        pn.indicators.Number(
            name="P-only RMSE [rad]",
            value=metrics["baseline_rmse_rad"],
            format="{value:.6f}",
            **common,
        ),
        pn.indicators.Number(
            name="Best PVA RMSE ratio",
            value=metrics["best_pva_ratio"],
            format="{value:.3f}",
            **common,
        ),
        pn.indicators.Number(
            name="Projection events",
            value=metrics["projection_event_count"],
            format="{value:,.0f}",
            **common,
        ),
        pn.indicators.Number(
            name="Completed methods",
            value=metrics["completed_method_count"],
            format="{value:.0f}",
            **common,
        ),
    ]
    return pn.GridBox(
        *cards,
        ncols=6,
        sizing_mode="stretch_width",
    )


def build_dashboard(run_directory: Path) -> pn.template.FastListTemplate:
    """Build a new per-session Panel dashboard for one E08 run."""

    data = load_dashboard_data(run_directory)
    frames = _frames(data)
    controls = DashboardControls()
    controls_panel = pn.Param(
        controls,
        parameters=[
            "selected_methods",
            "show_projections",
            "audit_method",
        ],
        widgets={
            "selected_methods": {
                "type": pn.widgets.MultiChoice,
                "name": "Tracking methods",
            },
            "show_projections": {
                "type": pn.widgets.Checkbox,
                "name": "Show exact projection events",
            },
            "audit_method": {
                "type": pn.widgets.Select,
                "name": "Raw-target audit method",
            },
        },
        show_name=False,
        sizing_mode="stretch_width",
    )

    event_columns = [
        "cycle_index",
        "time_s",
        "trigger",
        "raw_velocity_rad_s",
        "executable_velocity_rad_s",
        "velocity_projection_rad_s",
        "raw_acceleration_rad_s2",
        "executable_acceleration_rad_s2",
        "acceleration_projection_rad_s2",
    ]
    initial_method = LABEL_TO_METHOD[controls.audit_method]
    initial_events = frames["events"]
    initial_events = initial_events[initial_events["method_id"] == initial_method]
    event_table = pn.widgets.Tabulator(
        initial_events[event_columns].reset_index(drop=True),
        selectable=1,
        pagination="local",
        page_size=12,
        show_index=False,
        sizing_mode="stretch_width",
        height=360,
    )

    def _refresh_event_table(event: param.parameterized.Event) -> None:
        method_id = LABEL_TO_METHOD[event.new]
        rows = frames["events"]
        rows = rows[rows["method_id"] == method_id]
        event_table.value = rows[event_columns].reset_index(drop=True)
        event_table.selection = []

    controls.param.watch(_refresh_event_table, "audit_method")

    tracking = pn.bind(
        _tracking_view,
        controls.param.selected_methods,
        controls.param.show_projections,
        frames=frames,
        duration_s=float(data.overview_metrics["duration_s"]),
    )
    audit_plot = pn.bind(
        _audit_plot,
        controls.param.audit_method,
        event_table.param.selection,
        frames=frames,
        event_table=event_table,
    )
    event_detail = pn.bind(
        _event_detail,
        event_table.param.selection,
        event_table=event_table,
    )

    acceptance_overview = frames["acceptance"][
        [
            "method_label",
            "completed",
            "position_rmse_rad",
            "rmse_ratio_vs_p",
            "projection_count",
            "guardrail_pass",
            "scientific_status",
        ]
    ]
    overview_table = pn.widgets.Tabulator(
        acceptance_overview,
        disabled=True,
        pagination="local",
        page_size=8,
        show_index=False,
        sizing_mode="stretch_width",
        height=300,
    )
    tabs = pn.Tabs(
        (
            "Overview",
            pn.Column(
                _kpi_strip(data),
                pn.pane.Markdown(_headline(data)),
                pn.pane.Markdown("### Method status"),
                overview_table,
                pn.pane.Alert(
                    "Offline fixed-grid replay of a recorded position waveform; "
                    "this is not measured closed-loop robot feedback. Exact metrics "
                    "and every projection event are retained.",
                    alert_type="light",
                ),
            ),
        ),
        (
            "Tracking Explorer",
            pn.Column(
                pn.pane.Markdown(
                    "Pan or wheel-zoom any time-series view. The overview range "
                    "tool controls the detailed position window; method colors are "
                    "also distinguished by line style."
                ),
                tracking,
            ),
        ),
        (
            "Raw Target Audit",
            pn.Column(
                pn.pane.Markdown(
                    "Raw targets are dashed orange, executable projected targets "
                    "are solid blue, dotted horizontal lines are configured limits, "
                    "and every projection is marked with ×."
                ),
                audit_plot,
                pn.pane.Markdown(event_detail),
                pn.pane.Markdown("### Exact projection events"),
                event_table,
            ),
        ),
        (
            "Method Comparison",
            _comparison_view(frames=frames),
        ),
        dynamic=True,
        sizing_mode="stretch_width",
    )
    sidebar = [
        pn.pane.Markdown("## Controls"),
        controls_panel,
        pn.layout.Divider(),
        pn.pane.Markdown(
            f"**Run**  \n`{data.run_directory.name}`  \n\n"
            f"**Generated**  \n`{data.generated_at}`"
        ),
        pn.pane.Markdown(
            "The Panel server keeps Python-backed zoom resampling and "
            "cross-component state active. Use the existing artifact HTML when "
            "a server-free portable snapshot is required."
        ),
    ]
    return pn.template.FastListTemplate(
        site="OTG Lab",
        title="E08 HoloViz trajectory tracking analysis",
        accent_base_color="#2563EB",
        header_background="#111827",
        sidebar=sidebar,
        main=[tabs],
        main_layout=None,
        theme_toggle=True,
    )

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--port", type=int, default=5006)
    parser.add_argument("--address", default="localhost")
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open the HoloViz dashboard in the default browser.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Build and render once without starting a persistent server.",
    )
    arguments = parser.parse_args()
    run_directory = arguments.run_directory.resolve()
    if arguments.check:
        dashboard = build_dashboard(run_directory)
        dashboard.server_doc()
        data = load_dashboard_data(run_directory)
        frames = _frames(data)
        pn.panel(
            _tracking_view(
                list(METHOD_LABELS.values()),
                True,
                frames=frames,
                duration_s=float(data.overview_metrics["duration_s"]),
            )
        ).server_doc()
        _comparison_view(frames=frames).server_doc()
        check_method = PVA_METHODS[0]
        check_events = frames["events"]
        check_events = check_events[check_events["method_id"] == check_method]
        check_table = pn.widgets.Tabulator(
            check_events.reset_index(drop=True),
            selectable=1,
            show_index=False,
        )
        pn.panel(
            _audit_plot(
                METHOD_LABELS[check_method],
                [],
                frames=frames,
                event_table=check_table,
            )
        ).server_doc()
        print(
            "HoloViz dashboard check passed: "
            f"{len(data.position_series)} position rows, "
            f"{len(data.error_series)} error rows, "
            f"{len(data.projection_events)} exact projection events"
        )
        return

    pn.serve(
        {"e08": lambda: build_dashboard(run_directory)},
        port=arguments.port,
        address=arguments.address,
        show=arguments.show,
        title="E08 HoloViz trajectory tracking analysis",
    )


if __name__ == "__main__":
    main()
