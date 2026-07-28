"""E02: Position-only acceleration/jerk constraint sensitivity.

The experiment holds the vendor velocity limit fixed and evaluates the full
Cartesian product of declared acceleration and jerk levels.  Every case uses
the same PositionOnly → ZOH → P → ordinary Ruckig chain, so the only executable
differences are the two declared motion-limit fields.
"""

from __future__ import annotations

from pathlib import Path

from otg_lab.analysis import (
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
)
from otg_lab.experiment import (
    ExperimentCase,
    ExperimentInput,
    ExperimentSpec,
    FactorHeatmapSpec,
    InputGate,
)
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
)

INPUT_ID = "recorded_tasks_original_no_velocity_limit"
BASE_METHOD_ID = "position_zoh_p_ruckig"

MAX_VELOCITY_RAD_S = 4.1
ACCELERATION_LEVELS_RAD_S2 = (4.1, 6.0, 8.2, 12.0, 16.4)
JERK_LEVELS_RAD_S3 = (
    41.0,
    200.0,
    800.0,
    1600.0,
    3200.0,
    4000.0,
    8000.0,
)
VENDOR_ACCELERATION_RAD_S2 = 8.2
VENDOR_JERK_RAD_S3 = 4000.0

PRIMARY = ("position_rmse",)
SECONDARY = (
    "position_mae",
    "position_bias",
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
    "deadline_miss_rate",
)
_ASSIGNED = set(PRIMARY + SECONDARY + GUARDRAIL)
DIAGNOSTIC = tuple(
    metric_id
    for metric_id in DEFAULT_TRACKING_METRIC_IDS
    if metric_id not in _ASSIGNED
    and metric_id not in {"settled", "settle_time_s"}
)


def _value_token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def _case_id(acceleration: float, jerk: float) -> str:
    return f"a{_value_token(acceleration)}_j{_value_token(jerk)}"


VENDOR_CASE_ID = _case_id(
    VENDOR_ACCELERATION_RAD_S2,
    VENDOR_JERK_RAD_S3,
)


def _run_config(acceleration: float, jerk: float) -> RunConfig:
    return RunConfig(
        limits=MotionLimits(
            max_velocity_rad_s=MAX_VELOCITY_RAD_S,
            max_acceleration_rad_s2=acceleration,
            max_jerk_rad_s3=jerk,
        ),
        minimum_duration_s=0.01,
        prediction_horizon_s=0.01,
        measurement_policy="position_only",
        failure_policy="record_and_continue",
        dt_s=0.01,
    )


def _cases() -> tuple[ExperimentCase, ...]:
    return tuple(
        ExperimentCase(
            case_id=_case_id(acceleration, jerk),
            method_id=BASE_METHOD_ID,
            run_config=_run_config(acceleration, jerk),
            factors={
                "max_acceleration_rad_s2": acceleration,
                "max_jerk_rad_s3": jerk,
            },
            description=(
                "PositionOnly → ZOH → P → ordinary unshielded Ruckig; "
                f"A={acceleration:g} rad/s², J={jerk:g} rad/s³"
            ),
        )
        for acceleration in ACCELERATION_LEVELS_RAD_S2
        for jerk in JERK_LEVELS_RAD_S3
    )


def build_experiment(project_root: Path) -> ExperimentSpec:
    del project_root

    method = TrackingMethodSpec(
        method_id=BASE_METHOD_ID,
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        description="PositionOnly → ZOH → P → ordinary unshielded Ruckig",
    )
    cases = _cases()
    comparison_pairs = tuple(
        MethodPair(
            baseline_method_id=VENDOR_CASE_ID,
            candidate_method_id=case.case_id,
            comparison_id=f"{case.case_id}_vs_vendor",
        )
        for case in cases
        if case.case_id != VENDOR_CASE_ID
    )

    return ExperimentSpec(
        experiment_id="E02",
        slug="position_only_limit_sensitivity",
        title="E02 Position-only acceleration–jerk limit sensitivity",
        question=(
            "How does raw-time position tracking RMSE change across the "
            "declared acceleration × jerk constraint surface when velocity, "
            "input, timing, and the Position-only tracking chain are fixed?"
        ),
        hypothesis=(
            "Tighter acceleration and jerk limits increase raw-time position "
            "RMSE relative to the vendor configuration, while higher values "
            "reduce lag only as descriptive sensitivity probes."
        ),
        description=(
            "Full-factor sensitivity experiment only. Values above vendor "
            "acceleration or jerk limits are diagnostic probes and must not "
            "be interpreted as deployment recommendations."
        ),
        independent_variables=(
            "max_acceleration_rad_s2",
            "max_jerk_rad_s3",
        ),
        controlled_variables={
            "axis_count": 1,
            "dt_s": 0.01,
            "fixed_grid": True,
            "measurement_policy": "position_only",
            "initial_state_policy": "reference_position_zero_derivatives",
            "prediction_horizon_s": 0.01,
            "minimum_duration_s": 0.01,
            "max_velocity_rad_s": MAX_VELOCITY_RAD_S,
            "tracking_method": (
                "PositionOnly → ZOH → P → NoGovernor → ordinary Ruckig"
            ),
            "vendor_reference": {
                "max_acceleration_rad_s2": VENDOR_ACCELERATION_RAD_S2,
                "max_jerk_rad_s3": VENDOR_JERK_RAD_S3,
            },
        },
        allowed_method_differences=(
            "run_config.limits.max_acceleration_rad_s2",
            "run_config.limits.max_jerk_rad_s3",
        ),
        inputs=(
            ExperimentInput(
                INPUT_ID,
                f"data/trajectories/{INPUT_ID}.csv",
                required=True,
                description=(
                    "Canonical fixed-grid conversion of "
                    "data/raw/recorded_tasks/"
                    "original_no_velocity_limit.csv"
                ),
            ),
        ),
        methods=(method,),
        run_config=_run_config(
            VENDOR_ACCELERATION_RAD_S2,
            VENDOR_JERK_RAD_S3,
        ),
        metric_roles={
            "primary": PRIMARY,
            "secondary": SECONDARY,
            "guardrail": GUARDRAIL,
            "diagnostic": DIAGNOSTIC,
        },
        windows=(
            EvaluationWindow("full_overlap"),
            EvaluationWindow("main_evaluation", start_time_s=0.03),
        ),
        comparison_spec=ComparisonSpec(
            pairs=comparison_pairs,
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=(INPUT_ID,),
            window_ids=("main_evaluation",),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
        input_gate=InputGate(block_on_limit_violation=False),
        cases=cases,
        factor_heatmaps=(
            FactorHeatmapSpec(
                figure_id="constraint_sensitivity_rmse",
                input_id=INPUT_ID,
                metric_id="position_rmse",
                window_id="main_evaluation",
                row_factor="max_acceleration_rad_s2",
                row_levels=ACCELERATION_LEVELS_RAD_S2,
                column_factor="max_jerk_rad_s3",
                column_levels=JERK_LEVELS_RAD_S3,
                baseline_case_id=VENDOR_CASE_ID,
                title=(
                    "Acceleration–jerk motion-limit sensitivity · "
                    "position RMSE"
                ),
                subtitle=(
                    "V fixed at 4.1 rad/s · RMSE for t ≥ 0.03 s · each cell "
                    "relative to vendor A=8.2, J=4000; no deployment optimum "
                    "is inferred"
                ),
                row_label="Max acceleration limit [rad/s²]",
                column_label="Max jerk limit [rad/s³]",
            ),
            FactorHeatmapSpec(
                figure_id="constraint_sensitivity_lag_ms",
                input_id=INPUT_ID,
                metric_id="lag_s",
                window_id="main_evaluation",
                row_factor="max_acceleration_rad_s2",
                row_levels=ACCELERATION_LEVELS_RAD_S2,
                column_factor="max_jerk_rad_s3",
                column_levels=JERK_LEVELS_RAD_S3,
                baseline_case_id=VENDOR_CASE_ID,
                title=(
                    "Acceleration–jerk motion-limit sensitivity · "
                    "position lag"
                ),
                subtitle=(
                    "V fixed at 4.1 rad/s · best integer-shift lag for "
                    "t ≥ 0.03 s · each cell is case − vendor (70 ms); "
                    "positive means more delay"
                ),
                row_label="Max acceleration limit [rad/s²]",
                column_label="Max jerk limit [rad/s³]",
                comparison_mode="difference",
                display_multiplier=1000.0,
                colorbar_label="Lag Δ vs vendor [ms]",
            ),
        ),
    )
