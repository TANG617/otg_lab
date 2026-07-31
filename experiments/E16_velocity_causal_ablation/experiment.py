"""E16: matched velocity-component and alternative-remedy ablations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from otg_lab.confirmatory import (
    causal_pv_method,
    constant_velocity_trajectory,
    critical_reference_velocity,
    finish_compact_run,
    oracle_pv_method,
    p_only_method,
    scheduled_p_method,
    start_compact_run,
    summarize_tracking,
    tracking_config,
)
from otg_lab.experiment import ExperimentResult
from otg_lab.models import ComponentSpec, TrackingMethodSpec, Trajectory
from otg_lab.runio import write_json, write_rows_csv
from otg_lab.tracking import run_tracking
from otg_lab.types import TimedState

EXPERIMENT_ID = "E16"
SLUG = "velocity_causal_ablation"
DIRECTORY_NAME = f"{EXPERIMENT_ID}_{SLUG}"
TITLE = "Matched velocity-component causal ablation"
DURATION_S = 1.0
MAIN_START_S = 0.2
MAIN_END_S = 0.8
JERK_RAD_S3 = 4000.0
LAMBDA_VALUES = (-1.0, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25)


class VelocityScaledTargetBuilder:
    """Scale a scheduled oracle velocity without changing its position."""

    def __init__(
        self,
        trajectory: Trajectory,
        velocity_scale: float,
        *,
        random_sign: bool = False,
        seed: int = 1601,
    ) -> None:
        self.trajectory = trajectory
        self.velocity_scale = float(velocity_scale)
        self.random_sign = bool(random_sign)
        self.rng = np.random.default_rng(int(seed))

    def reset(self) -> None:
        self.rng = np.random.default_rng(1601)

    def _position_at(self, time_s: float) -> float:
        times = self.trajectory.time_s
        positions = self.trajectory.position_rad
        if time_s <= times[-1]:
            return float(np.interp(time_s, times, positions))
        velocity = float(self.trajectory.velocity_rad_s[-1])
        return float(positions[-1] + velocity * (time_s - times[-1]))

    def build(self, prediction: TimedState) -> TimedState:
        sign = (
            float(self.rng.choice((-1.0, 1.0)))
            if self.random_sign
            else 1.0
        )
        velocity = sign * self.velocity_scale * prediction.velocity
        return prediction.with_updates(
            position=[self._position_at(prediction.state_time)],
            velocity=velocity,
            acceleration=np.zeros(prediction.dof),
            jerk=None,
            method="scheduled_scaled_velocity",
            metadata={
                **dict(prediction.metadata),
                "target_components": "pv",
                "position_source": "reference_schedule_with_linear_tail",
                "derivative_source": "scaled_oracle_velocity",
                "velocity_scale": self.velocity_scale,
                "random_sign": self.random_sign,
                "latest_position_input_time_s": prediction.available_time,
            },
        )

    __call__ = build


class ScheduledPositionTargetBuilder:
    """Scheduled P target with explicit linear tail for lookahead controls."""

    def __init__(self, trajectory: Trajectory) -> None:
        self.trajectory = trajectory

    def reset(self) -> None:
        return None

    def build(self, prediction: TimedState) -> TimedState:
        times = self.trajectory.time_s
        if prediction.state_time <= times[-1]:
            position = float(
                np.interp(
                    prediction.state_time,
                    times,
                    self.trajectory.position_rad,
                )
            )
        else:
            position = float(
                self.trajectory.position_rad[-1]
                + self.trajectory.velocity_rad_s[-1]
                * (prediction.state_time - times[-1])
            )
        return prediction.with_updates(
            position=[position],
            velocity=np.zeros(prediction.dof),
            acceleration=np.zeros(prediction.dof),
            jerk=None,
            method="scheduled_position_with_linear_tail",
            metadata={
                **dict(prediction.metadata),
                "target_components": "p",
                "position_source": "reference_schedule_with_linear_tail",
                "derivative_source": "zero_by_target_builder",
                "latest_position_input_time_s": prediction.available_time,
            },
        )

    __call__ = build


def _scaled_builder_factory(
    trajectory: Trajectory,
    velocity_scale: float,
    random_sign: bool = False,
) -> VelocityScaledTargetBuilder:
    return VelocityScaledTargetBuilder(
        trajectory,
        velocity_scale,
        random_sign=random_sign,
    )


def _scheduled_position_factory(
    trajectory: Trajectory,
) -> ScheduledPositionTargetBuilder:
    return ScheduledPositionTargetBuilder(trajectory)


def _scaled_velocity_method(
    velocity_scale: float,
    *,
    random_sign: bool = False,
) -> TrackingMethodSpec:
    token = str(velocity_scale).replace("-", "m").replace(".", "p")
    method_id = "pv_random_sign" if random_sign else f"pv_lambda_{token}"
    return TrackingMethodSpec(
        method_id=method_id,
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("oracle", {"noncausal_diagnostic": True}),
        target_builder=ComponentSpec(
            "scaled_scheduled_velocity",
            {
                "velocity_scale": float(velocity_scale),
                "random_sign": bool(random_sign),
            },
            factory=_scaled_builder_factory,
        ),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        required=True,
    )


def _lookahead_method(steps: int) -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id=f"p_lookahead_h{steps}",
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec(
            "scheduled_position_tail",
            factory=_scheduled_position_factory,
        ),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        required=True,
    )


def _conditioned_causal_pv_method() -> TrackingMethodSpec:
    return TrackingMethodSpec(
        method_id="pv_future_o1_deadband",
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("future_backward_fd_o1"),
        target_builder=ComponentSpec(
            "scheduled_velocity_deadband",
            {
                "components": "pv",
                "time_source": "prediction_time",
                "absolute_tolerance_rad_s": 1e-10,
            },
        ),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        required=True,
    )


def _profile(name: str) -> dict[str, tuple[float, ...]]:
    normalized = str(name).strip().lower()
    if normalized == "smoke":
        return {
            "dt_s": (0.005, 0.01),
            "q": (0.5, 2.0),
            "rho": (0.5, 0.9, 1.1),
        }
    if normalized != "confirmatory":
        raise ValueError("profile must be smoke or confirmatory")
    return {
        "dt_s": (0.005, 0.01, 0.02),
        "q": (0.5, 0.95, 1.05, 2.0),
        "rho": (0.5, 0.9, 0.99, 1.01, 1.1),
    }


def _run_one(
    method: TrackingMethodSpec,
    *,
    family: str,
    dt_s: float,
    q_value: float,
    rho: float,
    prediction_horizon_steps: float = 1.0,
    minimum_duration_steps: float = 1.0,
) -> dict[str, Any]:
    acceleration = q_value * JERK_RAD_S3 * dt_s / 4.0
    critical_velocity = critical_reference_velocity(
        acceleration,
        JERK_RAD_S3,
        dt_s,
    )
    reference = constant_velocity_trajectory(
        rho * critical_velocity,
        dt_s,
        duration_s=DURATION_S,
    )
    config = tracking_config(
        dt_s=dt_s,
        acceleration_rad_s2=acceleration,
        jerk_rad_s3=JERK_RAD_S3,
        prediction_horizon_steps=prediction_horizon_steps,
        minimum_duration_steps=minimum_duration_steps,
    )
    tracking_run = run_tracking(reference, method, config)
    metrics = summarize_tracking(
        reference,
        tracking_run,
        config.limits,
        start_time_s=MAIN_START_S,
        end_time_s=MAIN_END_S,
        input_id="constant_velocity_probe",
    )
    return {
        "method_id": method.method_id,
        "family": family,
        "dt_s": dt_s,
        "q": q_value,
        "branch": "jerk_limited" if q_value >= 1.0 else "acceleration_limited",
        "rho": rho,
        "acceleration_rad_s2": acceleration,
        "jerk_rad_s3": JERK_RAD_S3,
        "critical_velocity_rad_s": critical_velocity,
        "prediction_horizon_steps": prediction_horizon_steps,
        "minimum_duration_steps": minimum_duration_steps,
        "minimum_duration_enabled": method.follower.params.get(
            "use_minimum_duration", True
        ),
        **metrics,
    }


def _write_figures(run_directory: Path, rows: list[dict[str, Any]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    selected = [
        row
        for row in rows
        if row["family"] == "velocity_fraction"
        and row["rho"] <= 0.9
        and row["profile_velocity_ripple_to_reference_median"] is not None
    ]
    grouped: dict[str, list[float]] = {}
    for row in selected:
        grouped.setdefault(row["method_id"], []).append(
            float(row["profile_velocity_ripple_to_reference_median"])
        )
    labels = sorted(grouped)
    figure, axis = plt.subplots(figsize=(9.0, 4.8), constrained_layout=True)
    axis.bar(
        np.arange(len(labels)),
        [float(np.median(grouped[label])) for label in labels],
        color="#4477AA",
    )
    axis.set_xticks(np.arange(len(labels)), labels, rotation=40, ha="right")
    axis.set_ylabel("median normalized velocity ripple")
    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    figure.savefig(figures / "velocity_fraction_ripple.png", dpi=200)
    figure.savefig(figures / "velocity_fraction_ripple.svg")
    plt.close(figure)


def run_confirmatory(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
    profile: str | None = None,
) -> ExperimentResult:
    profile_name = profile or os.environ.get(
        "OTG_CONFIRMATORY_PROFILE", "confirmatory"
    )
    declared = _profile(profile_name)
    run = start_compact_run(
        project_root,
        experiment_id=EXPERIMENT_ID,
        directory_name=DIRECTORY_NAME,
        title=TITLE,
        runs_root=runs_root,
        resolved_spec={
            "profile": profile_name,
            "matrix": declared,
            "velocity_scales": LAMBDA_VALUES,
            "lookahead_steps": (0, 1, 2, 5),
            "minimum_duration_steps": (0, 1, 2, 5),
        },
    )
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    core_methods = (
        (p_only_method(), "core"),
        (scheduled_p_method(), "core"),
        (oracle_pv_method(), "core"),
        (causal_pv_method(), "raw_causal_diagnostic"),
        (_conditioned_causal_pv_method(), "core"),
    )
    velocity_methods = tuple(
        (_scaled_velocity_method(value), "velocity_fraction")
        for value in LAMBDA_VALUES
    ) + ((_scaled_velocity_method(1.0, random_sign=True), "negative_control"),)

    for dt_s in declared["dt_s"]:
        for q_value in declared["q"]:
            for rho in declared["rho"]:
                for method, family in core_methods + velocity_methods:
                    row = _run_one(
                        method,
                        family=family,
                        dt_s=float(dt_s),
                        q_value=float(q_value),
                        rho=float(rho),
                    )
                    rows.append(row)
                    if not row["completed"]:
                        failures.append(row)

                for horizon in (0, 1, 2, 5):
                    row = _run_one(
                        _lookahead_method(horizon),
                        family="position_lookahead",
                        dt_s=float(dt_s),
                        q_value=float(q_value),
                        rho=float(rho),
                        prediction_horizon_steps=float(horizon),
                    )
                    rows.append(row)
                    if not row["completed"]:
                        failures.append(row)

                minimum_duration_methods = (
                    (
                        scheduled_p_method(
                            "p_min_duration_off",
                            use_minimum_duration=False,
                        ),
                        0.0,
                    ),
                    (scheduled_p_method("p_min_duration_1dt"), 1.0),
                    (scheduled_p_method("p_min_duration_2dt"), 2.0),
                    (scheduled_p_method("p_min_duration_5dt"), 5.0),
                )
                for method, duration_steps in minimum_duration_methods:
                    row = _run_one(
                        method,
                        family="minimum_duration",
                        dt_s=float(dt_s),
                        q_value=float(q_value),
                        rho=float(rho),
                        minimum_duration_steps=max(1.0, duration_steps),
                    )
                    row["minimum_duration_steps"] = duration_steps
                    rows.append(row)
                    if not row["completed"]:
                        failures.append(row)

    pulse_cells = [row for row in rows if row["rho"] <= 0.95]
    p_rows = [row for row in pulse_cells if row["method_id"] == "p_only_current"]
    correct_pv_rows = [
        row
        for row in pulse_cells
        if row["method_id"] in {"pv_oracle", "pv_future_o1_deadband"}
    ]
    raw_future_rows = [
        row for row in pulse_cells if row["method_id"] == "pv_future_o1"
    ]
    p_reproduced = all(
        row["rest_to_rest_pulse_fraction"] is not None
        and float(row["rest_to_rest_pulse_fraction"]) >= 0.95
        for row in p_rows
    )
    pv_eliminated = all(
        row["rest_to_rest_pulse_fraction"] is not None
        and float(row["rest_to_rest_pulse_fraction"]) <= 0.01
        and row["profile_velocity_ripple_to_reference_median"] is not None
        and float(row["profile_velocity_ripple_to_reference_median"]) <= 1e-9
        and row["profile_min_abs_velocity_to_reference_p05"] is not None
        and float(row["profile_min_abs_velocity_to_reference_p05"]) >= 0.99
        for row in correct_pv_rows
    )
    raw_future_eliminated = all(
        row["rest_to_rest_pulse_fraction"] is not None
        and float(row["rest_to_rest_pulse_fraction"]) <= 0.01
        and row["profile_velocity_ripple_to_reference_p95"] is not None
        and float(row["profile_velocity_ripple_to_reference_p95"]) <= 1e-4
        and row["profile_min_abs_velocity_to_reference_p05"] is not None
        and float(row["profile_min_abs_velocity_to_reference_p05"]) >= 0.99
        for row in raw_future_rows
    )
    wrong_rows = [
        row
        for row in pulse_cells
        if row["method_id"] in {"pv_lambda_m1p0", "pv_random_sign"}
    ]
    correct_ripple = np.median(
        [
            float(row["profile_velocity_ripple_to_reference_median"])
            for row in correct_pv_rows
            if row["profile_velocity_ripple_to_reference_median"] is not None
        ]
    )
    wrong_ripple = np.median(
        [
            float(row["profile_velocity_ripple_to_reference_median"])
            for row in wrong_rows
            if row["profile_velocity_ripple_to_reference_median"] is not None
        ]
    )
    def matches_exact_pv_profile(row: dict[str, Any]) -> bool:
        return bool(
            row["completed"]
            and row["rest_to_rest_pulse_fraction"] is not None
            and float(row["rest_to_rest_pulse_fraction"]) <= 0.01
            and row["profile_velocity_ripple_to_reference_p95"] is not None
            and float(row["profile_velocity_ripple_to_reference_p95"]) <= 1e-9
            and row["profile_min_abs_velocity_to_reference_p05"] is not None
            and float(row["profile_min_abs_velocity_to_reference_p05"]) >= 0.99
            and row["profile_near_zero_cycle_fraction"] is not None
            and float(row["profile_near_zero_cycle_fraction"]) <= 0.01
        )

    def matching_levels(family: str, field: str) -> list[float]:
        levels = sorted(
            {float(row[field]) for row in pulse_cells if row["family"] == family}
        )
        return [
            level
            for level in levels
            if all(
                matches_exact_pv_profile(row)
                for row in pulse_cells
                if row["family"] == family and float(row[field]) == level
            )
        ]

    lookahead_matches = matching_levels(
        "position_lookahead", "prediction_horizon_steps"
    )
    minimum_duration_matches = matching_levels(
        "minimum_duration", "minimum_duration_steps"
    )
    no_p_only_control_matches = not bool(
        lookahead_matches or minimum_duration_matches
    )
    acceptance = {
        "profile": profile_name,
        "run_count": len(rows),
        "completed_count": sum(bool(row["completed"]) for row in rows),
        "p_only_reproduced": p_reproduced,
        "correct_pv_eliminated_pulse_and_ripple": pv_eliminated,
        "raw_future_o1_eliminated_pulse_and_ripple": raw_future_eliminated,
        "wrong_control_median_ripple": float(wrong_ripple),
        "correct_pv_median_ripple": float(correct_ripple),
        "wrong_controls_worse": bool(wrong_ripple > correct_ripple + 1e-12),
        "position_lookahead_steps_matching_exact_pv_profile": lookahead_matches,
        "minimum_duration_steps_matching_exact_pv_profile": (
            minimum_duration_matches
        ),
        "pv_is_only_tested_exact_profile_remedy": no_p_only_control_matches,
        "accepted": bool(
            p_reproduced
            and pv_eliminated
            and wrong_ripple > correct_ripple + 1e-12
            and no_p_only_control_matches
            and not failures
        ),
    }
    write_rows_csv(run.run_directory / "causal_ablation.csv", rows)
    write_json(run.run_directory / "acceptance.json", acceptance)
    (run.run_directory / "acceptance_summary.md").write_text(
        "# E16 acceptance\n\n"
        f"- Profile: `{profile_name}`\n"
        f"- Completed: {acceptance['completed_count']}/{len(rows)}\n"
        f"- P-only reproduced: **{p_reproduced}**\n"
        f"- Correct PV eliminates pulse and ripple: **{pv_eliminated}**\n"
        f"- Raw Future-O1 passes all branches: **{raw_future_eliminated}**\n"
        f"- Wrong controls are worse: **{acceptance['wrong_controls_worse']}**\n"
        f"- P-lookahead steps matching exact PV profile: `{lookahead_matches}`\n"
        f"- Minimum-duration steps matching exact PV profile: "
        f"`{minimum_duration_matches}`\n"
        f"- PV is the only tested exact-profile remedy: "
        f"**{no_p_only_control_matches}**\n"
        f"- Accepted: **{acceptance['accepted']}**\n",
        encoding="utf-8",
    )
    if create_figures:
        _write_figures(run.run_directory, rows)
    return finish_compact_run(
        run,
        outputs={
            "causal_ablation": "causal_ablation.csv",
            "acceptance": "acceptance.json",
            "summary": "acceptance_summary.md",
        },
        failures=failures,
        required_failure_count=len(failures),
    )


if __name__ == "__main__":
    result = run_confirmatory(project_root=Path(__file__).resolve().parents[2])
    print(result.run_directory)
