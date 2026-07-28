"""E08: limit-projected E04 PVA finite differences on a recorded waveform."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from otg_lab.analysis import (
    AVAILABLE,
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
    MetricRow,
    get_metric_spec,
)
from otg_lab.components import (
    IdentityGovernor,
    build_estimator,
    build_predictor,
    build_target_builder,
    component_context,
)
from otg_lab.constraints import ruckig_target_admissible
from otg_lab.experiment import ExperimentInput, ExperimentSpec, InputGate
from otg_lab.governors import GovernorResult
from otg_lab.governors import MotionLimits as NumericalMotionLimits
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    TrackingRun,
    Trajectory,
)
from otg_lab.runio import write_rows_csv
from otg_lab.trajectory_ablation import (
    BASELINE_METHOD_ID,
    build_state_target_methods,
)
from otg_lab.types import Measurement, TimedState

INPUT_ID = "recorded_tasks_simplified_with_velocity_limit"
RAW_SOURCE_PATH = "data/raw/recorded_tasks/simplified_with_velocity_limit.csv"
CANONICAL_INPUT_PATH = f"data/trajectories/{INPUT_ID}.csv"

DT_S = 0.01
MAIN_START_S = 0.04
MAX_VELOCITY_RAD_S = 4.1
MAX_ACCELERATION_RAD_S2 = 8.2
MAX_JERK_RAD_S3 = 4000.0

PRIMARY = ("position_rmse",)
SECONDARY = (
    "position_mae",
    "position_p95_abs_error",
    "position_max_abs_error",
    "position_iae",
)
GUARDRAIL = (
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
_ASSIGNED = set(PRIMARY + SECONDARY + GUARDRAIL)
DIAGNOSTIC = tuple(
    metric_id
    for metric_id in DEFAULT_TRACKING_METRIC_IDS
    if metric_id not in _ASSIGNED
    and metric_id not in {"settled", "settle_time_s"}
    and get_metric_spec(metric_id).family != "stop_go"
    and not any(
        requirement.startswith("truth_")
        for requirement in get_metric_spec(metric_id).requirements
    )
)

_ACCEPTANCE_GUARDRAILS = (
    "output_velocity_violation_count",
    "output_acceleration_violation_count",
    "profile_constraint_violation_count",
    "fallback_rate",
    "solver_failure_count",
    "deadline_miss_rate",
)

RAW_TARGET_SCAN_FIELDS = (
    "input_id",
    "method_id",
    "cycle_index",
    "measurement_time_s",
    "command_time_s",
    "target_time_s",
    "target_available_time_s",
    "target_age_samples",
    "target_position_rad",
    "target_velocity_rad_s",
    "target_acceleration_rad_s2",
    "target_status",
    "target_startup",
    "target_causal",
    "position_source",
    "derivative_source",
    "latest_position_input_time_s",
    "velocity_within_limit",
    "acceleration_within_limit",
    "ruckig_target_admissible",
)


def _project_to_configured_limits(
    raw_target: np.ndarray,
    limits: NumericalMotionLimits,
) -> tuple[np.ndarray, bool]:
    """Project target V/A into the configured Ruckig-admissible limit set.

    Position is preserved. Acceleration and velocity are first clamped to
    their configured maxima. Velocity is then tightened only when needed by
    Ruckig's direction-dependent jerk-limited stopping envelope.
    """

    raw = np.asarray(raw_target, dtype=float)
    if raw.shape == (3,):
        raw = raw.reshape(1, 3)
    if raw.shape != (1, 3) or not np.all(np.isfinite(raw)):
        raise ValueError(
            "configured-limit projection requires a finite single-axis state"
        )

    projected = np.array(raw, copy=True)
    max_velocity = float(limits.max_velocity[0])
    max_acceleration = float(limits.max_acceleration[0])
    max_jerk = float(limits.max_jerk[0])
    projected[0, 2] = float(
        np.clip(projected[0, 2], -max_acceleration, max_acceleration)
    )
    projected[0, 1] = float(np.clip(projected[0, 1], -max_velocity, max_velocity))

    acceleration = float(projected[0, 2])
    if acceleration > 0.0:
        stopping_upper = max_velocity - acceleration**2 / (2.0 * max_jerk)
        projected[0, 1] = min(float(projected[0, 1]), stopping_upper)
    elif acceleration < 0.0:
        stopping_lower = -max_velocity + acceleration**2 / (2.0 * max_jerk)
        projected[0, 1] = max(float(projected[0, 1]), stopping_lower)

    if not ruckig_target_admissible(projected, limits):
        raise RuntimeError(
            "configured-limit projection produced an inadmissible target"
        )
    changed = not np.allclose(projected, raw, rtol=0.0, atol=1e-12)
    return projected, bool(changed)


class _ConfiguredLimitProjectionGovernor(IdentityGovernor):
    """Pass through admissible targets and project only out-of-range V/A."""

    # Preserve the represented target time in the shared tracking engine.
    name = "scalar_projection"

    def update(
        self,
        raw_target: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> GovernorResult:
        raw = np.asarray(raw_target, dtype=float)
        if raw.shape == (3,):
            raw = raw.reshape(1, 3)
        requested_feasible = ruckig_target_admissible(raw, self.limits)
        projected, changed = _project_to_configured_limits(raw, self.limits)
        result = super().update(
            projected,
            control_time=control_time,
            current_state=current_state,
        )
        return GovernorResult(
            **{
                **result.__dict__,
                "target_projected": changed,
                "solver_status": (
                    "configured_limit_projection:projected"
                    if changed
                    else "configured_limit_projection:pass_through"
                ),
                "distortion": projected - raw,
                "requested_target_feasible": bool(requested_feasible),
            }
        )


def _configured_limit_projection_factory(
    *,
    dt_s: float,
    numerical_limits: NumericalMotionLimits,
) -> _ConfiguredLimitProjectionGovernor:
    return _ConfiguredLimitProjectionGovernor(dt_s, numerical_limits)


def _run_config() -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=MAX_VELOCITY_RAD_S,
            max_acceleration_rad_s2=MAX_ACCELERATION_RAD_S2,
            max_jerk_rad_s3=MAX_JERK_RAD_S3,
        ),
        minimum_duration_s=DT_S,
        prediction_horizon_s=DT_S,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=DT_S,
    )


def _methods() -> tuple[TrackingMethodSpec, ...]:
    methods = build_state_target_methods(
        "pva",
        include_truth=False,
        include_differences=True,
    )
    governor = ComponentSpec(
        "configured_limit_projection",
        factory=_configured_limit_projection_factory,
    )
    return tuple(
        replace(method, governor=governor, required=True) for method in methods
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root
    methods = _methods()
    candidate_ids = tuple(
        method.method_id for method in methods if method.method_id != BASELINE_METHOD_ID
    )
    return ExperimentSpec(
        experiment_id="E08",
        slug="pva_finite_difference_recorded_tracking",
        title="E08 recorded-task PVA finite-difference transfer",
        question=(
            "Do E04's causal finite-difference PVA targets remain executable "
            "and improve raw-time position tracking on a recorded task waveform?"
        ),
        hypothesis=(
            "Every causal PVA method completes the full recorded waveform, "
            "lowers raw-time position RMSE versus P[k+1], and introduces no "
            "guardrail regression."
        ),
        description=(
            "Offline transfer of the E04 methods with explicit configured-limit "
            "projection. Raw targets remain fully auditable, while executable "
            "target V/A is projected before ordinary unshielded Ruckig."
        ),
        independent_variables=(
            "target_components",
            "derivative_source",
            "derivative_represented_time",
        ),
        controlled_variables={
            "input_id": INPUT_ID,
            "raw_source_path": RAW_SOURCE_PATH,
            "canonical_input_path": CANONICAL_INPUT_PATH,
            "axis_count": 1,
            "dt_s": DT_S,
            "fixed_grid": True,
            "raw_elapsed_time_ignored": True,
            "measurement_policy": "position_only",
            "scheduled_position_available_one_step_ahead": True,
            "prediction_horizon_s": DT_S,
            "minimum_duration_s": DT_S,
            "limits": {
                "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
                "max_acceleration_rad_s2": MAX_ACCELERATION_RAD_S2,
                "max_jerk_rad_s3": MAX_JERK_RAD_S3,
            },
            "target_conditioning": "configured_limit_projection",
            "projection_position_policy": "unchanged",
            "projection_velocity_policy": (
                "clip_to_configured_max_then_stopping_envelope"
            ),
            "projection_acceleration_policy": "clip_to_configured_max",
            "governor": "configured_limit_projection",
            "follower": "ordinary_ruckig_unshielded",
        },
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
        ),
        inputs=(
            ExperimentInput(
                INPUT_ID,
                CANONICAL_INPUT_PATH,
                required=True,
                description=(
                    "Fixed-grid position-only conversion of "
                    f"{RAW_SOURCE_PATH}; row order at 10 ms, no smoothing"
                ),
            ),
        ),
        methods=methods,
        run_config=_run_config(),
        metric_roles={
            "primary": PRIMARY,
            "secondary": SECONDARY,
            "guardrail": GUARDRAIL,
            "diagnostic": DIAGNOSTIC,
        },
        windows=(
            EvaluationWindow("full_overlap"),
            EvaluationWindow(
                "main_evaluation",
                start_time_s=MAIN_START_S,
            ),
        ),
        comparison_spec=ComparisonSpec(
            pairs=tuple(
                MethodPair(
                    BASELINE_METHOD_ID,
                    method_id,
                    f"{method_id}_vs_p_kp1",
                )
                for method_id in candidate_ids
            ),
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=(INPUT_ID,),
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        artifact_writer=write_recorded_transfer_artifacts,
    )


def _raw_target_scan(
    reference: Trajectory,
    methods: Sequence[TrackingMethodSpec],
    run_config: RunConfig,
) -> list[dict[str, Any]]:
    """Replay estimator→predictor→target without a governor or follower."""

    rows: list[dict[str, Any]] = []
    dt_s = float(reference.dt)
    tolerance = 1e-10
    for method in methods:
        context = component_context(reference, run_config, dt_s)
        estimator = build_estimator(method.estimator, context)
        predictor = build_predictor(method.predictor, context)
        target_builder = build_target_builder(method.target_builder, context)
        estimator.reset()
        predictor.reset()
        target_builder.reset()

        for cycle_index in range(reference.sample_count - 1):
            measurement_time = float(reference.time_s[cycle_index])
            command_time = float(reference.time_s[cycle_index + 1])
            measurement = Measurement(
                position=[float(reference.position_rad[cycle_index])],
                state_time=measurement_time,
                available_time=measurement_time,
                metadata={
                    "sample_index": int(reference.sample_index[cycle_index]),
                    "measurement_policy": "position_only",
                },
            )
            posterior = estimator.update(measurement)
            prediction_time = measurement_time + run_config.prediction_horizon_s
            horizon = max(
                0.0,
                float(prediction_time) - float(posterior.state_time),
            )
            prediction = predictor.predict(posterior, horizon)
            raw_target = target_builder.build(prediction)
            if not isinstance(raw_target, TimedState):
                raise TypeError("E08 raw target scan requires TimedState targets")
            target = raw_target.as_array()
            velocity = float(target[0, 1])
            acceleration = float(target[0, 2])
            metadata = dict(raw_target.metadata)
            rows.append(
                {
                    "input_id": INPUT_ID,
                    "method_id": method.method_id,
                    "cycle_index": cycle_index,
                    "measurement_time_s": measurement_time,
                    "command_time_s": command_time,
                    "target_time_s": float(raw_target.state_time),
                    "target_available_time_s": float(raw_target.available_time),
                    "target_age_samples": (command_time - float(raw_target.state_time))
                    / dt_s,
                    "target_position_rad": float(target[0, 0]),
                    "target_velocity_rad_s": velocity,
                    "target_acceleration_rad_s2": acceleration,
                    "target_status": raw_target.status,
                    "target_startup": bool(raw_target.startup),
                    "target_causal": bool(raw_target.causal),
                    "position_source": str(metadata.get("position_source", "")),
                    "derivative_source": str(metadata.get("derivative_source", "")),
                    "latest_position_input_time_s": metadata.get(
                        "latest_position_input_time_s"
                    ),
                    "velocity_within_limit": bool(
                        abs(velocity) <= MAX_VELOCITY_RAD_S + tolerance
                    ),
                    "acceleration_within_limit": bool(
                        abs(acceleration) <= MAX_ACCELERATION_RAD_S2 + tolerance
                    ),
                    "ruckig_target_admissible": bool(
                        ruckig_target_admissible(
                            target,
                            context.numerical_limits,
                        )
                    ),
                }
            )
    return rows


def _raw_target_feasibility_rows(
    scan_rows: Sequence[Mapping[str, Any]],
    methods: Sequence[TrackingMethodSpec],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method in methods:
        method_rows = [row for row in scan_rows if row["method_id"] == method.method_id]
        mature = [row for row in method_rows if not bool(row["target_startup"])]
        velocity = np.asarray(
            [float(row["target_velocity_rad_s"]) for row in mature],
            dtype=float,
        )
        acceleration = np.asarray(
            [float(row["target_acceleration_rad_s2"]) for row in mature],
            dtype=float,
        )
        velocity_violations = [
            row for row in mature if not bool(row["velocity_within_limit"])
        ]
        acceleration_violations = [
            row for row in mature if not bool(row["acceleration_within_limit"])
        ]
        inadmissible = [
            row for row in mature if not bool(row["ruckig_target_admissible"])
        ]
        first = inadmissible[0] if inadmissible else None
        count = len(mature)
        output.append(
            {
                "input_id": INPUT_ID,
                "method_id": method.method_id,
                "total_cycle_count": len(method_rows),
                "startup_cycle_count": len(method_rows) - count,
                "mature_cycle_count": count,
                "target_age_samples": (
                    None
                    if not mature
                    else float(
                        np.median([float(row["target_age_samples"]) for row in mature])
                    )
                ),
                "target_velocity_max_abs_rad_s": (
                    None if not count else float(np.max(np.abs(velocity)))
                ),
                "target_velocity_p95_abs_rad_s": (
                    None if not count else float(np.quantile(np.abs(velocity), 0.95))
                ),
                "velocity_limit_violation_count": len(velocity_violations),
                "velocity_limit_violation_rate": (
                    None if not count else len(velocity_violations) / count
                ),
                "target_acceleration_max_abs_rad_s2": (
                    None if not count else float(np.max(np.abs(acceleration)))
                ),
                "target_acceleration_p95_abs_rad_s2": (
                    None
                    if not count
                    else float(np.quantile(np.abs(acceleration), 0.95))
                ),
                "acceleration_limit_violation_count": len(acceleration_violations),
                "acceleration_limit_violation_rate": (
                    None if not count else len(acceleration_violations) / count
                ),
                "ruckig_inadmissible_count": len(inadmissible),
                "ruckig_inadmissible_rate": (
                    None if not count else len(inadmissible) / count
                ),
                "first_inadmissible_cycle_index": (
                    None if first is None else first["cycle_index"]
                ),
                "first_inadmissible_measurement_time_s": (
                    None if first is None else first["measurement_time_s"]
                ),
                "first_inadmissible_command_time_s": (
                    None if first is None else first["command_time_s"]
                ),
                "first_inadmissible_target_velocity_rad_s": (
                    None if first is None else first["target_velocity_rad_s"]
                ),
                "first_inadmissible_target_acceleration_rad_s2": (
                    None if first is None else first["target_acceleration_rad_s2"]
                ),
            }
        )
    return output


def _metric_index(
    rows: Sequence[MetricRow],
) -> dict[tuple[str, str, str, str], MetricRow]:
    return {
        (row.input_id, row.method_id, row.window_id, row.metric_id): row for row in rows
    }


def _available_metric(
    index: Mapping[tuple[str, str, str, str], MetricRow],
    method_id: str,
    window_id: str,
    metric_id: str,
) -> float | None:
    row = index.get((INPUT_ID, method_id, window_id, metric_id))
    if row is None or row.status != AVAILABLE or row.value is None:
        return None
    value = float(row.value)
    return value if np.isfinite(value) else None


def _projected_trace_rows(run: TrackingRun) -> list[Mapping[str, Any]]:
    projected: list[Mapping[str, Any]] = []
    pairs = (
        ("raw_target_position_rad", "executable_target_position_rad"),
        ("raw_target_velocity_rad_s", "executable_target_velocity_rad_s"),
        (
            "raw_target_acceleration_rad_s2",
            "executable_target_acceleration_rad_s2",
        ),
    )
    for row in run.trace_rows:
        if str(row.get("status", "")).lower() != "ok":
            continue
        changed = False
        for raw_field, executable_field in pairs:
            raw_value = row.get(raw_field)
            executable_value = row.get(executable_field)
            if raw_value is None or executable_value is None:
                continue
            if not np.isclose(
                float(raw_value),
                float(executable_value),
                rtol=0.0,
                atol=1e-12,
            ):
                changed = True
                break
        if changed:
            projected.append(row)
    return projected


def _acceptance_rows(
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    methods: Sequence[TrackingMethodSpec],
    feasibility_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    metric_index = _metric_index(trajectory_rows)
    feasibility = {str(row["method_id"]): row for row in feasibility_rows}
    baseline_run = tracking_runs[(BASELINE_METHOD_ID, INPUT_ID)]
    baseline_rmse = (
        _available_metric(
            metric_index,
            BASELINE_METHOD_ID,
            "main_evaluation",
            "position_rmse",
        )
        if baseline_run.status.completed
        else None
    )
    output: list[dict[str, Any]] = []
    for method in methods:
        method_id = method.method_id
        run = tracking_runs[(method_id, INPUT_ID)]
        completed = bool(run.status.completed)
        projection_rows = _projected_trace_rows(run)
        first_projection = None if not projection_rows else projection_rows[0]
        failure_trace = next(
            (
                row
                for row in run.trace_rows
                if str(row.get("status", "")).lower() == "failed"
            ),
            None,
        )
        position_rmse = (
            _available_metric(
                metric_index,
                method_id,
                "main_evaluation",
                "position_rmse",
            )
            if completed
            else None
        )
        ratio = (
            None
            if method_id == BASELINE_METHOD_ID
            or position_rmse is None
            or baseline_rmse is None
            or baseline_rmse <= 0.0
            else position_rmse / baseline_rmse
        )
        guardrail_failures: list[str] = []
        if completed and method_id != BASELINE_METHOD_ID:
            for metric_id in _ACCEPTANCE_GUARDRAILS:
                baseline_value = _available_metric(
                    metric_index,
                    BASELINE_METHOD_ID,
                    "full_overlap",
                    metric_id,
                )
                candidate_value = _available_metric(
                    metric_index,
                    method_id,
                    "full_overlap",
                    metric_id,
                )
                if baseline_value is None or candidate_value is None:
                    guardrail_failures.append(f"{metric_id}:unavailable")
                elif candidate_value > baseline_value + 1e-12:
                    guardrail_failures.append(metric_id)
        completion_pass = completed
        rmse_pass = (
            True
            if method_id == BASELINE_METHOD_ID and completed
            else ratio is not None and ratio < 1.0
        )
        guardrail_pass = completed and (
            method_id == BASELINE_METHOD_ID or not guardrail_failures
        )
        overall_pass = completion_pass and rmse_pass and guardrail_pass
        if method_id == BASELINE_METHOD_ID:
            scientific_status = (
                "baseline_complete" if completed else "baseline_incomplete"
            )
        elif not completed:
            scientific_status = "not_transferable_incomplete"
        elif not rmse_pass:
            scientific_status = "complete_but_no_rmse_improvement"
        elif not guardrail_pass:
            scientific_status = "complete_but_guardrail_regression"
        else:
            scientific_status = "transfer_pass"
        feasibility_row = feasibility[method_id]
        output.append(
            {
                "input_id": INPUT_ID,
                "method_id": method_id,
                "method_role": (
                    "p_only_baseline"
                    if method_id == BASELINE_METHOD_ID
                    else "causal_pva_finite_difference"
                ),
                "required_for_runner": method.required,
                "completed": completed,
                "valid_cycles": run.status.valid_cycles,
                "total_cycles": run.status.total_cycles,
                "completion_fraction": (
                    run.status.valid_cycles / run.status.total_cycles
                ),
                "projection_count": len(projection_rows),
                "projection_rate": (len(projection_rows) / run.status.total_cycles),
                "first_projection_cycle_index": (
                    None
                    if first_projection is None
                    else first_projection.get("cycle_index")
                ),
                "first_projection_measurement_time_s": (
                    None
                    if first_projection is None
                    else first_projection.get("measurement_time_s")
                ),
                "first_projection_command_time_s": (
                    None
                    if first_projection is None
                    else first_projection.get("command_time_s")
                ),
                "failure_cycle_index": (
                    None if failure_trace is None else failure_trace.get("cycle_index")
                ),
                "failure_measurement_time_s": (
                    None
                    if failure_trace is None
                    else failure_trace.get("measurement_time_s")
                ),
                "failure_layer": run.status.failure_layer,
                "failure_reason": run.status.failure_reason,
                "failure_raw_target_velocity_rad_s": (
                    None
                    if failure_trace is None
                    else failure_trace.get("raw_target_velocity_rad_s")
                ),
                "failure_raw_target_acceleration_rad_s2": (
                    None
                    if failure_trace is None
                    else failure_trace.get("raw_target_acceleration_rad_s2")
                ),
                "raw_target_velocity_max_abs_rad_s": feasibility_row[
                    "target_velocity_max_abs_rad_s"
                ],
                "raw_target_acceleration_max_abs_rad_s2": feasibility_row[
                    "target_acceleration_max_abs_rad_s2"
                ],
                "raw_target_ruckig_inadmissible_count": feasibility_row[
                    "ruckig_inadmissible_count"
                ],
                "raw_target_ruckig_inadmissible_rate": feasibility_row[
                    "ruckig_inadmissible_rate"
                ],
                "baseline_position_rmse_rad": baseline_rmse,
                "position_rmse_rad": position_rmse,
                "rmse_ratio_vs_p": ratio,
                "completion_pass": completion_pass,
                "rmse_pass": rmse_pass,
                "guardrail_pass": guardrail_pass,
                "overall_pass": overall_pass,
                "guardrail_failures": ";".join(guardrail_failures),
                "scientific_status": scientific_status,
                "prefix_rmse_used": False,
            }
        )
    return output


def _write_acceptance_summary(
    analysis_directory: Path,
    acceptance_rows: Sequence[Mapping[str, Any]],
) -> None:
    baseline = next(
        row for row in acceptance_rows if row["method_id"] == BASELINE_METHOD_ID
    )
    candidates = [
        row for row in acceptance_rows if row["method_id"] != BASELINE_METHOD_ID
    ]
    execution_complete = bool(
        baseline["completed"]
        and len(candidates) == 5
        and all(bool(row["completed"]) for row in candidates)
    )
    scientific_pass = bool(
        candidates and all(bool(row["overall_pass"]) for row in candidates)
    )
    lines = [
        "## Recorded-waveform transfer acceptance",
        "",
        f"- Experiment execution: `{'complete' if execution_complete else 'incomplete'}`",
        f"- Scientific transfer: `{'pass' if scientific_pass else 'fail'}`",
        "- Raw PVA targets are retained unchanged for audit; executable V/A "
        "targets are projected into the configured Ruckig-admissible limits.",
        "- A method still must complete the full trajectory before its "
        "position error is used for ranking.",
        "",
        "| method | completed | valid / total | first projection | "
        "projection count | max abs raw A | RMSE ratio vs P | result |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in acceptance_rows:
        ratio = row["rmse_ratio_vs_p"]
        ratio_text = "" if ratio is None else f"{float(ratio):.6g}"
        projection_cycle = row["first_projection_cycle_index"]
        projection_text = "" if projection_cycle is None else str(projection_cycle)
        acceleration = row["raw_target_acceleration_max_abs_rad_s2"]
        acceleration_text = "" if acceleration is None else f"{float(acceleration):.6g}"
        lines.append(
            f"| {row['method_id']} | "
            f"{'yes' if row['completed'] else 'no'} | "
            f"{row['valid_cycles']} / {row['total_cycles']} | "
            f"{projection_text} | {row['projection_count']} | "
            f"{acceleration_text} | {ratio_text} | "
            f"{row['scientific_status']} |"
        )
    lines.extend(
        [
            "",
            "The input is a fixed-10-ms offline replay of a recorded task "
            "position waveform. It is not a closed-loop robot trial and does "
            "not preserve the raw timestamp jitter.",
            "",
        ]
    )
    (analysis_directory / "acceptance_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _failure_time(run: TrackingRun) -> float | None:
    failure = next(
        (
            row
            for row in run.trace_rows
            if str(row.get("status", "")).lower() == "failed"
        ),
        None,
    )
    if failure is None:
        return None
    value = failure.get("measurement_time_s")
    return None if value is None else float(value)


def _first_projection_row(
    run: TrackingRun,
) -> Mapping[str, Any] | None:
    rows = _projected_trace_rows(run)
    return None if not rows else rows[0]


def _write_position_figure(
    figures_directory: Path,
    reference: Trajectory,
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    methods: Sequence[TrackingMethodSpec],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    figure, (position_axis, error_axis) = plt.subplots(
        2,
        1,
        figsize=(13.5, 8.0),
        sharex=True,
        gridspec_kw={"height_ratios": (2.1, 1.0)},
        constrained_layout=True,
    )
    position_axis.plot(
        reference.time_s,
        reference.position_rad,
        color="#111827",
        linewidth=0.72,
        alpha=0.72,
        label="recorded reference",
        zorder=1,
    )
    palette = (
        "#64748B",
        "#2563EB",
        "#B45309",
        "#3F6212",
        "#BE185D",
        "#0891B2",
    )
    line_styles: tuple[Any, ...] = (
        "-",
        (0, (6, 2)),
        (0, (3, 1.5, 1, 1.5)),
        (0, (1, 1.3)),
        (0, (7, 1.5, 1, 1.5)),
        (0, (4, 1.4)),
    )
    method_colors = {method.method_id: color for method, color in zip(methods, palette)}
    for draw_order, (method, line_style) in enumerate(
        zip(methods, line_styles),
        start=2,
    ):
        color = method_colors[method.method_id]
        run = tracking_runs[(method.method_id, INPUT_ID)]
        command = run.command
        if command is not None and command.sample_count:
            position_axis.plot(
                command.time_s,
                command.position_rad,
                color=color,
                linewidth=0.52,
                linestyle=line_style,
                alpha=0.92,
                label=method.method_id,
                zorder=draw_order,
            )
            reference_position = np.interp(
                command.time_s,
                reference.time_s,
                reference.position_rad,
            )
            error_axis.plot(
                command.time_s,
                command.position_rad - reference_position,
                color=color,
                linewidth=0.52,
                linestyle=line_style,
                alpha=0.92,
                label=method.method_id,
                zorder=draw_order,
            )

        projection_rows = _projected_trace_rows(run)
        if projection_rows and command is not None:
            projection_times = np.asarray(
                [float(row["command_time_s"]) for row in projection_rows],
                dtype=float,
            )
            projected_command_positions = np.interp(
                projection_times,
                command.time_s,
                command.position_rad,
            )
            projected_reference_positions = np.interp(
                projection_times,
                reference.time_s,
                reference.position_rad,
            )
            position_axis.scatter(
                projection_times,
                projected_command_positions,
                marker="x",
                s=7,
                linewidth=0.38,
                alpha=0.42,
                color=color,
                zorder=15,
            )
            error_axis.scatter(
                projection_times,
                projected_command_positions - projected_reference_positions,
                marker="x",
                s=7,
                linewidth=0.38,
                alpha=0.42,
                color=color,
                zorder=15,
            )

            projection_time = float(projection_times[0])
            command_position = float(projected_command_positions[0])
            reference_position = float(projected_reference_positions[0])
            position_axis.scatter(
                [projection_time],
                [command_position],
                marker="x",
                s=44,
                linewidth=1.15,
                color=color,
                zorder=20,
            )
            error_axis.scatter(
                [projection_time],
                [command_position - reference_position],
                marker="x",
                s=44,
                linewidth=1.15,
                color=color,
                zorder=20,
            )
            for axis in (position_axis, error_axis):
                axis.axvline(
                    projection_time,
                    color=color,
                    linewidth=0.42,
                    linestyle=":",
                    alpha=0.38,
                    zorder=0,
                )

        failure_time = _failure_time(run)
        if failure_time is not None:
            reference_position = float(
                np.interp(
                    failure_time,
                    reference.time_s,
                    reference.position_rad,
                )
            )
            position_axis.scatter(
                [failure_time],
                [reference_position],
                marker="x",
                s=55,
                linewidth=1.4,
                color=color,
                zorder=8,
            )
            for axis in (position_axis, error_axis):
                axis.axvline(
                    failure_time,
                    color=color,
                    linewidth=0.45,
                    linestyle=":",
                    alpha=0.45,
                )
    position_axis.set_title(
        "Recorded position reference and limit-projected tracking commands"
    )
    position_axis.set_ylabel("Position [rad]")
    position_axis.grid(alpha=0.18, linewidth=0.5)
    handles, labels = position_axis.get_legend_handles_labels()
    handles.append(
        Line2D(
            [],
            [],
            color="#111827",
            marker="x",
            linestyle="none",
            markersize=6,
            label="target projection cycles",
        )
    )
    labels.append("target projection cycles")
    position_axis.legend(
        handles,
        labels,
        loc="best",
        fontsize=7,
        ncol=2,
    )
    error_axis.axhline(
        0.0,
        color="#111827",
        linewidth=0.55,
        alpha=0.75,
    )
    error_axis.set_title(
        "Raw-time position error (command − reference); "
        "× = every projection, large × = first"
    )
    error_axis.set_xlabel("Time [s]")
    error_axis.set_ylabel("Position error [rad]")
    error_axis.grid(alpha=0.18, linewidth=0.5)
    figures_directory.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "svg"):
        figure.savefig(
            figures_directory / f"recorded_position_tracking.{suffix}",
            dpi=180 if suffix == "png" else None,
            bbox_inches="tight",
            facecolor="white",
        )
    plt.close(figure)


def _write_raw_target_figure(
    figures_directory: Path,
    scan_rows: Sequence[Mapping[str, Any]],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    methods: Sequence[TrackingMethodSpec],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    candidates = [
        method for method in methods if method.method_id != BASELINE_METHOD_ID
    ]
    figure, axes = plt.subplots(
        len(candidates),
        2,
        figsize=(15.0, 14.5),
        sharex=True,
        constrained_layout=True,
    )
    for row_index, method in enumerate(candidates):
        rows = [row for row in scan_rows if row["method_id"] == method.method_id]
        time = np.asarray(
            [float(row["measurement_time_s"]) for row in rows],
            dtype=float,
        )
        velocity = np.asarray(
            [float(row["target_velocity_rad_s"]) for row in rows],
            dtype=float,
        )
        acceleration = np.asarray(
            [float(row["target_acceleration_rad_s2"]) for row in rows],
            dtype=float,
        )
        projection_rows = _projected_trace_rows(
            tracking_runs[(method.method_id, INPUT_ID)]
        )
        projection_times = np.asarray(
            [float(projection["measurement_time_s"]) for projection in projection_rows],
            dtype=float,
        )
        projection_time = (
            None if projection_times.size == 0 else float(projection_times[0])
        )
        failure_time = _failure_time(tracking_runs[(method.method_id, INPUT_ID)])
        for column_index, (values, limit, label) in enumerate(
            (
                (velocity, MAX_VELOCITY_RAD_S, "Velocity [rad/s]"),
                (
                    acceleration,
                    MAX_ACCELERATION_RAD_S2,
                    "Acceleration [rad/s²]",
                ),
            )
        ):
            axis = axes[row_index, column_index]
            axis.plot(time, values, color="#2563EB", linewidth=0.58)
            axis.axhline(limit, color="#B42318", linestyle="--", linewidth=0.9)
            axis.axhline(-limit, color="#B42318", linestyle="--", linewidth=0.9)
            if projection_times.size:
                projection_values = np.interp(
                    projection_times,
                    time,
                    values,
                )
                axis.scatter(
                    projection_times,
                    projection_values,
                    marker="x",
                    s=7,
                    linewidth=0.4,
                    alpha=0.4,
                    color="#9A3412",
                    zorder=5,
                )
                projection_value = float(projection_values[0])
                axis.axvline(
                    projection_time,
                    color="#9A3412",
                    linestyle=":",
                    linewidth=0.8,
                    alpha=0.7,
                )
                axis.scatter(
                    [projection_time],
                    [projection_value],
                    marker="x",
                    s=36,
                    linewidth=1.1,
                    color="#9A3412",
                    zorder=6,
                )
            if failure_time is not None:
                axis.axvline(
                    failure_time,
                    color="#7F1D1D",
                    linestyle=":",
                    linewidth=1.0,
                )
            axis.grid(alpha=0.18)
            if row_index == 0:
                axis.set_title(f"Raw target {label.lower()}")
            if column_index == 0:
                axis.set_ylabel(f"{method.method_id}\n{label}")
            else:
                axis.set_ylabel(label)
    axes[-1, 0].set_xlabel("Measurement time [s]")
    axes[-1, 1].set_xlabel("Measurement time [s]")
    figure.suptitle(
        "Causal E04 raw PVA targets "
        "(× = every projection; large × / dotted line = first)",
        fontsize=14,
    )
    figures_directory.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "svg"):
        figure.savefig(
            figures_directory / f"raw_target_feasibility.{suffix}",
            dpi=180 if suffix == "png" else None,
            bbox_inches="tight",
            facecolor="white",
        )
    plt.close(figure)


def write_recorded_transfer_artifacts(
    *,
    analysis_directory: Path,
    references: Mapping[str, Trajectory],
    tracking_runs: Mapping[tuple[str, str], TrackingRun],
    trajectory_rows: Sequence[MetricRow],
    experiment_spec: ExperimentSpec,
    create_figures: bool,
) -> None:
    reference = references[INPUT_ID]
    methods = experiment_spec.methods
    scan_rows = _raw_target_scan(
        reference,
        methods,
        experiment_spec.run_config,
    )
    feasibility_rows = _raw_target_feasibility_rows(scan_rows, methods)
    acceptance_rows = _acceptance_rows(
        tracking_runs,
        trajectory_rows,
        methods,
        feasibility_rows,
    )
    write_rows_csv(
        analysis_directory / "raw_target_scan.csv",
        scan_rows,
        fieldnames=RAW_TARGET_SCAN_FIELDS,
    )
    write_rows_csv(
        analysis_directory / "raw_target_feasibility.csv",
        feasibility_rows,
    )
    write_rows_csv(
        analysis_directory / "acceptance.csv",
        acceptance_rows,
    )
    _write_acceptance_summary(analysis_directory, acceptance_rows)
    if create_figures:
        figures_directory = analysis_directory / "figures"
        _write_position_figure(
            figures_directory,
            reference,
            tracking_runs,
            methods,
        )
        _write_raw_target_figure(
            figures_directory,
            scan_rows,
            tracking_runs,
            methods,
        )


__all__ = [
    "INPUT_ID",
    "RAW_TARGET_SCAN_FIELDS",
    "build_experiment",
    "write_recorded_transfer_artifacts",
]
