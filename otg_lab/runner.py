"""Configuration-driven causal pipeline execution on canonical sample rows."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Any

import numpy as np

from .estimators import NonFiniteMeasurementError, make_estimator
from .followers import (
    DirectExecutableFollower,
    RuckigFollower,
    scalar_project_target_state,
)
from .governors import (
    JerkQPGovernor,
    MotionLimits,
    OneStepBoundedJerkGovernor,
)
from .pipeline import (
    PER_AXIS_CAUSAL_SYNC,
    select_replanning_state,
    synchronize_axis_posteriors,
)
from .plants import DelayedServoPlant, IdealCommandPlant
from .predictors import make_predictor, select_target_components
from .schema import FIELD_NAMES, recompute_sample_feasibility, validate_samples
from .types import Measurement, TimedState, state_from_array


@dataclass(frozen=True)
class PipelineRunResult:
    rows: list[dict[str, Any]]
    fallback_count: int
    deadline_miss_count: int
    constraint_violation_count: int
    constraint_audits: list[dict[str, Any]]


def _set(row: dict[str, Any], **values: Any) -> None:
    """Set only canonical fields, permitting schema evolution during replay."""

    for key, value in values.items():
        if key in row:
            row[key] = value


def _audit_vector(
    audit: Mapping[str, Any], dof: int, name: str, default: float = np.nan
) -> np.ndarray:
    return np.broadcast_to(
        np.asarray(audit.get(name, np.full(dof, default)), dtype=float),
        (dof,),
    )


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


_METHOD_FAMILY_SEMANTICS = {
    "ordinary_ruckig_unshielded": "ordinary_ruckig_unshielded",
    "ordinary_ruckig_with_viability_shield": "safety_shielded_ruckig",
    "one_step_governed_direct": "direct_constant_jerk",
    "jerk_qp_direct": "direct_constant_jerk",
    "jerk_qp_ruckig_with_viability_shield": "safety_shielded_ruckig",
}


def _method_semantics(pipeline: Mapping[str, Any]) -> str:
    """Resolve the declared command identity independently of a method label."""

    family = pipeline.get("method_family")
    if family in _METHOD_FAMILY_SEMANTICS:
        return _METHOD_FAMILY_SEMANTICS[str(family)]
    follower = str(pipeline["follower"])
    shielded = bool(
        dict(pipeline.get("follower_parameters", {})).get("safety_shield", False)
    )
    if follower == "direct":
        return "direct_constant_jerk"
    if follower == "ruckig":
        return (
            "safety_shielded_ruckig"
            if shielded
            else "ordinary_ruckig_unshielded"
        )
    return "mixed"


def _actual_command_algorithm(followed: Any) -> str:
    """Return the algorithm that actually produced the committed command."""

    native = str(getattr(followed, "native_follower", ""))
    fallback = str(getattr(followed, "fallback_controller", ""))
    native_executed = bool(getattr(followed, "native_command_executed", False))
    changes_algorithm = bool(getattr(followed, "fallback_changes_algorithm", False))
    if native_executed:
        if not native or changes_algorithm:
            raise ValueError("native follower result has inconsistent algorithm identity")
        return native
    if changes_algorithm:
        if not fallback:
            raise ValueError("replacement follower result lacks fallback_controller")
        return fallback
    raise ValueError("follower result does not identify the executed command algorithm")


def _limits(config: Mapping[str, Any], dof: int) -> MotionLimits:
    values = config["limits"]
    return MotionLimits.broadcast(
        dof,
        values["max_velocity"],
        values["max_acceleration"],
        values["max_jerk"],
    )


def _build_estimator(
    config: Mapping[str, Any],
    dt: float,
    limits: MotionLimits,
    *,
    joint: int | None = None,
):
    pipeline = config["pipeline"]
    method = pipeline["estimator"]
    params = dict(pipeline.get("estimator_parameters", {}))
    params.setdefault("nominal_dt", dt)
    params.setdefault("nonfinite_policy", "hold")
    params.setdefault("timestamp_policy", "hold")
    if method in {"jerk_limited_differentiator", "jerk_limited"}:
        params.setdefault(
            "max_velocity",
            limits.max_velocity if joint is None else float(limits.max_velocity[joint]),
        )
        params.setdefault(
            "max_acceleration",
            limits.max_acceleration
            if joint is None
            else float(limits.max_acceleration[joint]),
        )
        params.setdefault(
            "max_jerk",
            limits.max_jerk if joint is None else float(limits.max_jerk[joint]),
        )
    return make_estimator(method, **params)


def _build_predictor(
    config: Mapping[str, Any], rows_by_tick: Sequence[Sequence[Mapping[str, Any]]]
):
    pipeline = config["pipeline"]
    method = pipeline["predictor"]
    params = dict(pipeline.get("predictor_parameters", {}))
    if method in {"oracle", "oracle_future_state", "oracle_future_state_offline"}:
        first_joint = [group[0] for group in rows_by_tick]
        if not all(row["truth_available"] for row in first_joint):
            raise ValueError("oracle predictor requires genuine derivative truth")
        params.update(
            truth_times=np.asarray([row["control_time"] for row in first_joint]),
            truth_position=np.asarray(
                [[row["p_ref"] for row in group] for group in rows_by_tick]
            ),
            truth_velocity=np.asarray(
                [[row["v_ref_truth"] for row in group] for group in rows_by_tick]
            ),
            truth_acceleration=np.asarray(
                [[row["a_ref_truth"] for row in group] for group in rows_by_tick]
            ),
            truth_jerk=np.asarray(
                [[row["j_ref_truth"] for row in group] for group in rows_by_tick]
            ),
            out_of_range="clip",
        )
    return make_predictor(method, **params)


def _group_rows(rows: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    copied = [copy.deepcopy(dict(row)) for row in rows]
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in copied:
        groups.setdefault(int(row["k"]), []).append(row)
    ordered = []
    expected_joints: tuple[str, ...] | None = None
    for k in sorted(groups):
        group = sorted(groups[k], key=lambda row: str(row["joint_id"]))
        joints = tuple(str(row["joint_id"]) for row in group)
        if expected_joints is None:
            expected_joints = joints
        if joints != expected_joints:
            raise ValueError("joint set/order changed within trajectory")
        control_times = {float(row["control_time"]) for row in group}
        if len(control_times) != 1:
            raise ValueError("multi-DoF rows at one k must share control_time")
        ordered.append(group)
    if not ordered:
        raise ValueError("cannot run an empty trajectory")
    return ordered


def _build_governor(
    config: Mapping[str, Any], dof: int, dt: float, limits: MotionLimits
):
    pipeline = config["pipeline"]
    kind = pipeline["governor"]
    params = dict(pipeline.get("governor_parameters", {}))
    # The runner resolves previous-command/measured/hybrid state externally and
    # passes it explicitly.  This keeps governor and follower current states identical.
    params.pop("measured_state_mode", None)
    if kind == "one_step":
        return OneStepBoundedJerkGovernor(
            dof, dt, limits, measured_state_mode="measured", **params
        )
    if kind == "jerk_qp":
        horizon_steps = int(params.pop("horizon_steps", 20))
        return JerkQPGovernor(dof, dt, limits, horizon_steps=horizon_steps, **params)
    return None


def _build_follower(
    config: Mapping[str, Any], dof: int, dt: float, limits: MotionLimits
):
    pipeline = config["pipeline"]
    formal = bool(config.get("formal", False))
    params = dict(pipeline.get("follower_parameters", {}))
    if pipeline["follower"] == "direct":
        params.pop("safety_shield", None)
        return DirectExecutableFollower(dof, dt, limits, formal=formal, **params)
    return RuckigFollower(
        dof,
        dt,
        limits,
        minimum_duration=float(config["control"]["minimum_duration"]),
        project_targets=False,
        formal=formal,
        **params,
    )


def _build_plant(config: Mapping[str, Any], dof: int, dt: float, limits: MotionLimits):
    pipeline = config["pipeline"]
    if pipeline["plant"] == "ideal":
        return IdealCommandPlant(dof, dt)
    params = dict(pipeline.get("plant_parameters", {}))
    params.setdefault("seed", int(config.get("seed", 0)))
    return DelayedServoPlant(dof, dt, limits, **params)


def _posterior_state_reset(posterior: TimedState) -> bool:
    """Return true only when the estimator actually cleared internal state."""

    return bool(
        posterior.metadata.get("timestamp_reset", False)
        or posterior.status in {"timestamp_reset", "nonfinite_reset"}
    )


def run_pipeline_rows(
    rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any]
) -> PipelineRunResult:
    """Run one complete trajectory and return canonical enriched rows.

    Input rows may contain one or many joints in long format.  The estimator,
    governor, QP, and Ruckig follower all receive vector state; multi-joint
    Ruckig therefore uses a single synchronized solve.
    """

    groups = _group_rows(rows)
    dof = len(groups[0])
    dt = float(config["control"]["dt"])
    limits = _limits(config, dof)
    pipeline = config["pipeline"]
    estimators = [
        _build_estimator(config, dt, limits, joint=joint) for joint in range(dof)
    ]
    predictor = _build_predictor(config, groups)
    governor = _build_governor(config, dof, dt, limits)
    follower = _build_follower(config, dof, dt, limits)
    plant = _build_plant(config, dof, dt, limits)

    initial = np.column_stack(
        (
            np.asarray([row["p_ref"] for row in groups[0]], dtype=float),
            np.zeros(dof),
            np.zeros(dof),
        )
    )
    command_state = initial.copy()
    measured_plant_state = initial.copy()
    if governor is not None:
        governor.reset(initial)
    follower.reset(initial)
    plant.reset(initial)
    last_posterior: TimedState | None = None
    last_axis_posteriors: list[TimedState | None] = [None for _ in range(dof)]
    last_command_acceleration = initial[:, 2].copy()
    pending: list[list[tuple[float, float, float, Mapping[str, Any]]]] = [
        [] for _ in range(dof)
    ]
    enriched: list[dict[str, Any]] = []
    constraint_audits: list[dict[str, Any]] = []
    fallback_count = 0
    deadline_miss_count = 0
    constraint_violation_count = 0
    horizon_s = float(pipeline["prediction_horizon_ms"]) / 1000.0
    feedback_threshold = float(
        pipeline.get("governor_parameters", {}).get("divergence_threshold", 0.05)
    )

    for group in groups:
        tick_started = perf_counter_ns()
        control_time = float(group[0]["control_time"])
        for joint, row in enumerate(group):
            if row["measurement_available"] and row["p_meas"] is not None:
                if float(row["source_time"]) > float(row["arrival_time"]) + 1e-12:
                    _set(
                        row,
                        event_future_source_time=True,
                        measurement_valid=False,
                        invalid_input=True,
                        transport_delay_s=None,
                    )
                    flags = set(
                        filter(
                            None,
                            str(row.get("event_flags", ""))
                            .replace(";", "|")
                            .split("|"),
                        )
                    )
                    flags.add("future_source_time_rejected")
                    row["event_flags"] = ";".join(sorted(flags))
                    continue
                pending[joint].append(
                    (
                        float(row["arrival_time"]),
                        float(row["source_time"]),
                        float(row["p_meas"]),
                        row,
                    )
                )
                pending[joint].sort(key=lambda item: (item[0], item[1]))

        arrived: list[tuple[float, float, float, Mapping[str, Any]] | None] = []
        for joint_queue in pending:
            usable = [
                item
                for item in joint_queue
                if item[0] <= control_time + 1e-12 and item[1] <= control_time + 1e-12
            ]
            arrived.append(usable[-1] if usable else None)
            if usable:
                usable_ids = {id(item) for item in usable}
                joint_queue[:] = [
                    item for item in joint_queue if id(item) not in usable_ids
                ]

        estimator_compute = 0.0
        estimator_updated = [False for _ in range(dof)]
        for joint, item in enumerate(arrived):
            if item is not None:
                arrival_time, source_time, position, _ = item
                measurement = Measurement(
                    position=[position],
                    state_time=source_time,
                    available_time=arrival_time,
                    metadata={
                        "joint_id": str(group[joint]["joint_id"]),
                        "control_time": control_time,
                    },
                )
                try:
                    axis_posterior = estimators[joint].update(measurement)
                    estimator_updated[joint] = True
                except NonFiniteMeasurementError:
                    axis_current = command_state[joint : joint + 1]
                    axis_posterior = state_from_array(
                        axis_current,
                        state_time=control_time,
                        available_time=control_time,
                        method="invalid_startup_hold",
                        valid=False,
                        startup=True,
                        status="nonfinite_without_history",
                    )
                last_axis_posteriors[joint] = axis_posterior
                estimator_compute += float(axis_posterior.compute_time_us)
            elif last_axis_posteriors[joint] is None:
                axis_current = command_state[joint : joint + 1]
                last_axis_posteriors[joint] = state_from_array(
                    axis_current,
                    state_time=control_time,
                    available_time=control_time,
                    method="missing_startup_hold",
                    valid=False,
                    startup=True,
                    status="missing_measurement_without_history",
                )

        if any(item is None for item in last_axis_posteriors):
            raise RuntimeError("per-axis estimator initialization failed")
        axis_posteriors = [item for item in last_axis_posteriors if item is not None]
        try:
            last_posterior = synchronize_axis_posteriors(
                axis_posteriors, control_time=control_time
            )
        except ValueError as error:
            raise RuntimeError(
                f"causal per-axis synchronization failed: {error}"
            ) from error

        prediction_target_time = control_time + horizon_s
        propagation = max(0.0, prediction_target_time - last_posterior.state_time)
        prediction = predictor.predict(last_posterior, propagation)
        predictor_compute = float(prediction.compute_time_us)
        raw_target_state = select_target_components(prediction, pipeline["target_mode"])
        raw_target = raw_target_state.as_array()

        replanning = select_replanning_state(
            pipeline["measured_state_mode"],
            command_state,
            measured_plant_state,
            feedback_threshold,
        )
        replanning_state = replanning.state
        estimator_state_reset = any(
            updated and _posterior_state_reset(axis_posterior)
            for updated, axis_posterior in zip(estimator_updated, axis_posteriors)
        )
        governor_compute = 0.0
        governor_fallback_requested = False
        governor_fallback_applied = False
        governor_safety_guarantee = True
        governor_emergency_mode = False
        governor_reason = ""
        governor_status = "none"
        qp_iterations = None
        qp_status_category = None
        qp_solve_time_us = None
        qp_primal_residual = None
        qp_dual_residual = None
        qp_hessian_condition_number = None
        qp_constraint_condition_number = None
        target_projected = False
        executable: np.ndarray | None = None
        executable_time: float | None = None

        if pipeline["governor"] == "one_step":
            governed = governor.update(
                raw_target, control_time=control_time, current_state=replanning_state
            )
            executable = governed.executable_state
            executable_time = governed.target_time
            governor_compute = governed.compute_us
            governor_fallback_requested = bool(
                getattr(governed, "fallback_requested", governed.fallback)
            )
            governor_fallback_applied = bool(
                getattr(governed, "fallback_applied", governed.fallback)
            )
            governor_safety_guarantee = bool(
                getattr(governed, "safety_guarantee", not governed.fallback)
            )
            governor_emergency_mode = bool(getattr(governed, "emergency_mode", False))
            governor_reason = governed.fallback_reason
            governor_status = governed.solver_status
            follower_target = executable
        elif pipeline["governor"] == "jerk_qp":
            steps = governor.horizon_steps
            sequence_target_times = prediction_target_time + np.arange(steps) * dt
            durations = [
                max(0.0, target_time - last_posterior.state_time)
                for target_time in sequence_target_times
            ]
            # Reuse the already-computed first prediction, then account for every
            # additional horizon requested by the QP.  The outer tick timer has
            # always included these calls; the layer timer must do the same.
            predictions = [prediction]
            if steps > 1:
                predictions.extend(
                    predictor.predict_sequence(last_posterior, durations[1:])
                )
            predictor_compute += sum(
                float(item.compute_time_us) for item in predictions[1:]
            )
            component_states = [
                select_target_components(item, pipeline["target_mode"]).as_array()
                for item in predictions
            ]
            governed = governor.update(
                np.asarray(component_states),
                control_time=control_time,
                current_state=replanning_state,
            )
            executable = governed.executable_state
            executable_time = governed.target_time
            governor_compute = governed.compute_us
            governor_fallback_requested = bool(
                getattr(governed, "fallback_requested", governed.fallback)
            )
            governor_fallback_applied = bool(
                getattr(governed, "fallback_applied", governed.fallback)
            )
            governor_safety_guarantee = bool(
                getattr(governed, "safety_guarantee", not governed.fallback)
            )
            governor_emergency_mode = bool(getattr(governed, "emergency_mode", False))
            governor_reason = governed.fallback_reason
            governor_status = governed.solver_status
            qp_iterations = governed.iterations
            qp_status_category = getattr(governed, "qp_status_category", None)
            qp_solve_time_us = _finite_or_none(
                getattr(governed, "qp_solve_time_us", np.nan)
            )
            qp_primal_residual = _finite_or_none(
                getattr(governed, "qp_primal_residual", np.nan)
            )
            qp_dual_residual = _finite_or_none(
                getattr(governed, "qp_dual_residual", np.nan)
            )
            qp_hessian_condition_number = _finite_or_none(
                getattr(governed, "qp_hessian_condition_number", np.nan)
            )
            qp_constraint_condition_number = _finite_or_none(
                getattr(governed, "qp_constraint_condition_number", np.nan)
            )
            follower_target = executable
        elif pipeline["governor"] == "scalar_projection":
            projection_started = perf_counter_ns()
            follower_target, target_projected = scalar_project_target_state(
                raw_target, limits
            )
            governor_compute = (perf_counter_ns() - projection_started) / 1000.0
            executable = follower_target
            executable_time = raw_target_state.state_time
            governor_status = "scalar_projection"
        else:
            follower_target = raw_target

        followed = follower.update(
            follower_target,
            control_time=control_time,
            current_state=replanning_state,
        )
        command_state = followed.command_state
        plant_result = plant.update(command_state, command_time=followed.command_time)
        measured_plant_state = plant_result.measured_state
        plant_command_age = float(plant_result.delayed_command_age)
        plant_command_source_time = getattr(plant_result, "command_source_time", None)
        if plant_command_source_time is None:
            plant_command_source_time = float(followed.command_time) - plant_command_age
        plant_delay = float(
            getattr(
                plant_result,
                "configured_delay_s",
                pipeline.get("plant_parameters", {}).get("delay_s", 0.0),
            )
        )
        plant_saturated = np.broadcast_to(
            np.asarray(plant_result.saturated, dtype=bool), (dof,)
        )
        sampled_jerk = (command_state[:, 2] - last_command_acceleration) / dt
        last_command_acceleration = command_state[:, 2].copy()
        total_compute = (perf_counter_ns() - tick_started) / 1000.0
        deadline_miss = total_compute > dt * 1e6
        follower_fallback_requested = bool(
            getattr(followed, "fallback_requested", followed.fallback)
        )
        follower_fallback_applied = bool(
            getattr(followed, "fallback_applied", followed.fallback)
        )
        fallback_requested = governor_fallback_requested or follower_fallback_requested
        fallback_applied = governor_fallback_applied or follower_fallback_applied
        safety_guarantee = bool(
            governor_safety_guarantee
            and getattr(followed, "safety_guarantee", not followed.fallback)
        )
        emergency_mode = bool(
            governor_emergency_mode or getattr(followed, "emergency_mode", False)
        )
        requested_free_duration = getattr(
            followed, "requested_target_free_trajectory_duration", None
        )
        if requested_free_duration is None and not follower_fallback_applied:
            requested_free_duration = followed.free_trajectory_duration
        reasons = [
            reason for reason in (governor_reason, followed.fallback_reason) if reason
        ]
        fallback_reason = ";".join(reasons)
        if fallback_applied and not fallback_reason:
            fallback_reason = "unspecified_pipeline_fallback"
        if fallback_applied:
            fallback_count += 1
        if deadline_miss:
            deadline_miss_count += 1
        audit = followed.continuous_audit
        violation_vector = np.asarray(
            audit.get("violation_count", np.zeros(dof)), dtype=int
        )
        constraint_violation_count += int(np.sum(violation_vector))
        internal_jerk = np.asarray(
            audit.get("max_internal_jerk", np.full(dof, np.nan)), dtype=float
        )
        profile = followed.command_profile
        if profile is None:
            raise ValueError("follower result lacks an executable command profile")
        profile_boundaries_json = json.dumps(
            profile.segment_boundaries.tolist(), separators=(",", ":")
        )
        actual_command_algorithm = _actual_command_algorithm(followed)
        method_semantics = _method_semantics(pipeline)

        max_velocity = _audit_vector(audit, dof, "max_velocity")
        max_acceleration = _audit_vector(audit, dof, "max_acceleration")
        max_sampled_jerk = _audit_vector(audit, dof, "max_sampled_jerk")
        velocity_margin = _audit_vector(audit, dof, "velocity_margin")
        acceleration_margin = _audit_vector(audit, dof, "acceleration_margin")
        jerk_margin = _audit_vector(audit, dof, "jerk_margin")
        velocity_max_time = _audit_vector(audit, dof, "velocity_max_time")
        acceleration_max_time = _audit_vector(audit, dof, "acceleration_max_time")
        jerk_max_time = _audit_vector(audit, dof, "jerk_max_time")

        for joint, row in enumerate(group):
            _set(
                row,
                method_id=str(
                    pipeline.get(
                        "method_id",
                        "::".join(
                            (
                                str(pipeline["estimator"]),
                                str(pipeline["predictor"]),
                                f"h{float(pipeline['prediction_horizon_ms']):g}ms",
                                str(pipeline["target_mode"]),
                                str(pipeline["governor"]),
                                str(pipeline["follower"]),
                                str(pipeline["plant"]),
                            )
                        ),
                    )
                ),
                estimator_id=str(pipeline["estimator"]),
                predictor_id=str(pipeline["predictor"]),
                target_mode=str(pipeline["target_mode"]),
                governor_id=str(pipeline["governor"]),
                follower_id=str(pipeline["follower"]),
                plant_id=str(pipeline["plant"]),
                posterior_p=float(last_posterior.position[joint]),
                posterior_v=float(last_posterior.velocity[joint]),
                posterior_a=float(last_posterior.acceleration[joint]),
                posterior_state_time=float(last_posterior.state_time),
                posterior_available_time=float(last_posterior.available_time),
                posterior_axis_source_time=float(axis_posteriors[joint].state_time),
                posterior_axis_available_time=float(
                    axis_posteriors[joint].available_time
                ),
                measurement_sync_method=PER_AXIS_CAUSAL_SYNC,
                prediction_p=float(prediction.position[joint]),
                prediction_v=float(prediction.velocity[joint]),
                prediction_a=float(prediction.acceleration[joint]),
                prediction_time=float(prediction.state_time),
                prediction_horizon_ms=float(prediction.prediction_horizon * 1000.0),
                raw_target_p=float(raw_target[joint, 0]),
                raw_target_v=float(raw_target[joint, 1]),
                raw_target_a=float(raw_target[joint, 2]),
                raw_target_time=float(raw_target_state.state_time),
                executable_target_p=None
                if executable is None
                else float(executable[joint, 0]),
                executable_target_v=None
                if executable is None
                else float(executable[joint, 1]),
                executable_target_a=None
                if executable is None
                else float(executable[joint, 2]),
                executable_target_time=executable_time,
                executable_target_free_trajectory_duration=(
                    float(requested_free_duration)
                    if executable is not None
                    and requested_free_duration is not None
                    and np.isfinite(requested_free_duration)
                    else None
                ),
                command_p=float(command_state[joint, 0]),
                command_v=float(command_state[joint, 1]),
                command_a=float(command_state[joint, 2]),
                command_jerk=_finite_or_none(followed.command_jerk[joint]),
                acceleration_difference_jerk=float(sampled_jerk[joint]),
                sampled_jerk=float(sampled_jerk[joint]),
                new_jerk=(
                    float(followed.command_jerk[joint])
                    if pipeline["follower"] == "direct"
                    else None
                ),
                internal_trajectory_jerk=(
                    float(internal_jerk[joint])
                    if np.isfinite(internal_jerk[joint])
                    else None
                ),
                command_profile_kind=followed.command_profile_kind,
                command_profile_start_time=float(profile.start_time),
                command_profile_duration=float(profile.duration),
                command_profile_segment_boundaries_json=profile_boundaries_json,
                command_profile_segment_jerks_json=(
                    json.dumps(
                        profile.segment_jerks[:, joint].tolist(),
                        separators=(",", ":"),
                    )
                    if profile.exact
                    else None
                ),
                command_profile_segment_count=followed.command_profile_segment_count,
                command_profile_boundary_count=followed.command_profile_boundary_count,
                command_profile_source=profile.source,
                command_profile_exact=followed.command_profile_exact,
                command_endpoint_matches_profile=(
                    followed.command_endpoint_matches_profile
                ),
                command_first_jerk=(
                    None
                    if followed.command_first_jerk is None
                    else float(followed.command_first_jerk[joint])
                ),
                command_last_jerk=(
                    None
                    if followed.command_last_jerk is None
                    else float(followed.command_last_jerk[joint])
                ),
                command_internal_max_abs_jerk=(
                    None
                    if followed.command_internal_max_abs_jerk is None
                    else float(followed.command_internal_max_abs_jerk[joint])
                ),
                command_constant_jerk_exact=followed.command_constant_jerk_exact,
                command_profile_continuous_constraints_satisfied=(
                    followed.command_profile_continuous_constraints_satisfied
                ),
                native_follower=str(followed.native_follower),
                actual_command_algorithm=actual_command_algorithm,
                method_semantics=method_semantics,
                native_command_executed=bool(followed.native_command_executed),
                safety_shield_requested=bool(followed.safety_shield_requested),
                safety_shield_applied=bool(followed.safety_shield_applied),
                safety_shield_reason=str(followed.safety_shield_reason),
                fallback_controller=str(followed.fallback_controller),
                fallback_changes_algorithm=bool(followed.fallback_changes_algorithm),
                command_time=float(followed.command_time),
                plant_p=float(plant_result.true_state[joint, 0]),
                plant_v=float(plant_result.true_state[joint, 1]),
                plant_a=float(plant_result.true_state[joint, 2]),
                plant_measured_p=float(plant_result.measured_state[joint, 0]),
                plant_measured_v=float(plant_result.measured_state[joint, 1]),
                plant_measured_a=float(plant_result.measured_state[joint, 2]),
                plant_saturated=bool(plant_saturated[joint]),
                plant_command_source_time=float(plant_command_source_time),
                plant_command_age_s=plant_command_age,
                plant_delay_s=plant_delay,
                plant_status=str(plant_result.status),
                command_measured_delta_p=float(replanning.measured_delta[joint, 0]),
                command_measured_delta_v=float(replanning.measured_delta[joint, 1]),
                command_measured_delta_a=float(replanning.measured_delta[joint, 2]),
                command_measured_divergence=float(replanning.divergence[joint]),
                event_command_measured_divergence=bool(
                    replanning.divergence_exceeded[joint]
                ),
                feedback_correction=bool(replanning.correction_applied[joint]),
                feedback_correction_p=float(replanning.correction[joint, 0]),
                feedback_correction_v=float(replanning.correction[joint, 1]),
                feedback_correction_a=float(replanning.correction[joint, 2]),
                feedback_correction_reason=replanning.reason,
                limit_max_velocity=float(limits.max_velocity[joint]),
                limit_max_acceleration=float(limits.max_acceleration[joint]),
                limit_max_jerk=float(limits.max_jerk[joint]),
                current_p=float(replanning_state[joint, 0]),
                current_v=float(replanning_state[joint, 1]),
                current_a=float(replanning_state[joint, 2]),
                command_max_abs_velocity=_finite_or_none(max_velocity[joint]),
                command_max_abs_acceleration=_finite_or_none(max_acceleration[joint]),
                command_max_abs_jerk=(
                    float(internal_jerk[joint])
                    if np.isfinite(internal_jerk[joint])
                    else _finite_or_none(max_sampled_jerk[joint])
                ),
                target_projected=bool(target_projected or followed.target_projected),
                fallback_requested=bool(fallback_requested),
                fallback_applied=bool(fallback_applied),
                fallback=bool(fallback_applied),
                fallback_reason=fallback_reason if fallback_applied else "",
                safety_guarantee=safety_guarantee,
                emergency_mode=emergency_mode,
                solver_status=f"{governor_status}|{followed.solver_status}",
                qp_iterations=qp_iterations,
                qp_status_category=qp_status_category,
                qp_solve_time_us=qp_solve_time_us,
                qp_primal_residual=qp_primal_residual,
                qp_dual_residual=qp_dual_residual,
                qp_hessian_condition_number=qp_hessian_condition_number,
                qp_constraint_condition_number=qp_constraint_condition_number,
                deadline_miss=bool(deadline_miss),
                state_reset=estimator_state_reset,
                invalid_input=bool(
                    row.get("invalid_input", False) or not last_posterior.valid
                ),
                free_trajectory_duration=(
                    float(followed.free_trajectory_duration)
                    if np.isfinite(followed.free_trajectory_duration)
                    else None
                ),
                estimator_compute_us=estimator_compute,
                predictor_compute_us=predictor_compute,
                governor_compute_us=float(governor_compute),
                follower_compute_us=float(followed.compute_us),
                plant_compute_us=float(plant_result.compute_us),
                total_compute_us=float(total_compute),
            )
            recomputed = recompute_sample_feasibility(row)
            _set(
                row,
                raw_target_point_admissible=recomputed["raw_target_point_admissible"],
                raw_target_ruckig_admissible=recomputed["raw_target_ruckig_admissible"],
                executable_target_available=recomputed["executable_target_available"],
                executable_target_point_admissible=recomputed[
                    "executable_target_point_admissible"
                ],
                executable_target_stopping_viable=recomputed[
                    "executable_target_stopping_viable"
                ],
                executable_target_segment_feasible=recomputed[
                    "executable_target_segment_feasible"
                ],
                executable_target_t_free_le_dt=recomputed[
                    "executable_target_t_free_le_dt"
                ],
                command_t_free_le_dt=recomputed["command_t_free_le_dt"],
                command_segment_feasible=recomputed["command_segment_feasible"],
                command_stopping_viable=recomputed["command_stopping_viable"],
                command_next_step_exists=recomputed["command_next_step_exists"],
                command_continuous_constraints_satisfied=recomputed[
                    "command_continuous_constraints_satisfied"
                ],
                target_feasible=recomputed["raw_target_point_admissible"],
            )
            flags = set(
                filter(
                    None, str(row.get("event_flags", "")).replace(";", "|").split("|")
                )
            )
            if deadline_miss:
                flags.add("deadline_miss")
            if estimator_state_reset:
                flags.add("state_reset")
            if replanning.correction_applied[joint]:
                flags.add("feedback_correction")
            if replanning.divergence_exceeded[joint]:
                flags.add("command_measured_divergence")
            if plant_saturated[joint]:
                flags.add("plant_saturated")
            if row.get("invalid_input"):
                flags.add("invalid_input")
            row["event_flags"] = ";".join(sorted(flags))
            enriched.append(row)
            constraint_audits.append(
                {
                    "run_id": str(row["run_id"]),
                    "dataset_id": str(row["dataset_id"]),
                    "trajectory_id": str(row["trajectory_id"]),
                    "scenario_id": str(row["scenario_id"]),
                    "joint_id": str(row["joint_id"]),
                    "k": int(row["k"]),
                    "control_time": control_time,
                    "command_time": float(followed.command_time),
                    "audit_method": str(
                        audit.get(
                            "audit_method",
                            "analytic_constant_jerk"
                            if pipeline["follower"] == "direct"
                            else "fallback_endpoint_only",
                        )
                    ),
                    "audited_duration_s": float(audit.get("duration", dt)),
                    "audit_sample_count": int(audit.get("sample_count", 2)),
                    "max_abs_velocity": _finite_or_none(max_velocity[joint]),
                    "max_abs_acceleration": _finite_or_none(max_acceleration[joint]),
                    "max_sampled_jerk": _finite_or_none(max_sampled_jerk[joint]),
                    "max_new_jerk": (
                        float(abs(followed.command_jerk[joint]))
                        if pipeline["follower"] == "direct"
                        else None
                    ),
                    "max_internal_jerk": (
                        float(internal_jerk[joint])
                        if np.isfinite(internal_jerk[joint])
                        else None
                    ),
                    "velocity_margin": _finite_or_none(velocity_margin[joint]),
                    "acceleration_margin": _finite_or_none(acceleration_margin[joint]),
                    "jerk_margin": _finite_or_none(jerk_margin[joint]),
                    "velocity_max_time_s": _finite_or_none(velocity_max_time[joint]),
                    "acceleration_max_time_s": _finite_or_none(
                        acceleration_max_time[joint]
                    ),
                    "jerk_max_time_s": _finite_or_none(jerk_max_time[joint]),
                    "violation_count": int(violation_vector[joint]),
                    "fallback": bool(fallback_applied),
                }
            )

    # Catch an accidental schema mismatch before artifact writing.
    for row in enriched:
        missing = set(FIELD_NAMES) - set(row)
        if missing:
            raise ValueError(
                f"pipeline row missing canonical fields: {sorted(missing)}"
            )
    validate_samples(enriched)
    return PipelineRunResult(
        enriched,
        fallback_count,
        deadline_miss_count,
        constraint_violation_count,
        constraint_audits,
    )


__all__ = ["PipelineRunResult", "run_pipeline_rows"]
