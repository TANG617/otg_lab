"""E01：CSV-first 实验基础设施的端到端验收。

这个实验的目的不是比较两种方法谁更好，而是确认下面这条核心数据流已经
完整、可复现并且可以独立审计：

    规范 reference CSV
    → 输入轨迹分析
    → 可组合跟踪方法
    → command / trace / command_profiles CSV
    → tidy 指标与方法比较

因此，E01 同时放入记录轨迹和解析轨迹，并让它们经过完全相同的 loader、
跟踪循环和分析流程。这里的比较结果只用于检查基础设施，不应被解释为方法
优劣的科学结论。
"""

from __future__ import annotations

from pathlib import Path

from otg_lab.analysis import (
    DEFAULT_TRACKING_METRIC_IDS,
    ComparisonSpec,
    EvaluationWindow,
    MethodPair,
)
from otg_lab.experiment import ExperimentInput, ExperimentSpec, InputGate
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
)

# 指标角色由实验声明，指标公式则统一来自版本化 MetricSpec 注册表。
#
# primary：E01 最核心的端到端跟踪输出。这里选择 raw-time position RMSE，
# 强制比较同一物理时刻的 reference 与 command，不用时间平移掩盖相位误差。
PRIMARY = ("position_rmse",)

# secondary：从不同角度拆解位置跟踪误差，便于发现 RMSE 变化究竟来自
# 系统偏差、少量尖峰，还是全程累积误差。
SECONDARY = (
    "position_mae",
    "position_bias",
    "position_p95_abs_error",
    "position_max_abs_error",
    "position_iae",
)

# guardrail：方法即使位置误差较小，也不能以违反运动学约束、频繁 fallback
# 或超过控制周期 deadline 为代价。离散 command 和连续 profile 都要审计。
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

# 除已分配指标外，其余默认指标自动归入 diagnostic，避免新增统一指标后
# E01 无意中漏掉诊断覆盖。settling 指标被明确排除，因为 E01 的四条输入
# 没有声明 terminal-hold/step-like 窗口，计算“稳定时间”没有明确语义。
_ASSIGNED = set(PRIMARY + SECONDARY + GUARDRAIL)
DIAGNOSTIC = tuple(
    metric_id
    for metric_id in DEFAULT_TRACKING_METRIC_IDS
    if metric_id not in _ASSIGNED
    and metric_id not in {"settled", "settle_time_s"}
)


def build_experiment(project_root: Path) -> ExperimentSpec:
    """构造 E01 的完整声明，供 CLI/runner 加载并解析。

    参数中的 ``project_root`` 是所有实验构造函数保持一致的公开签名。本实验
    只声明相对输入路径，真正的绝对路径由 runner 统一解析，所以这里无需直接
    使用它。这样 experiment.py 不会绑定某一台机器的本地目录。
    """

    del project_root

    # 方法 A：最小基线链。
    #
    # 1. position_only estimator：在线阶段只接收位置测量，不会读取解析 CSV
    #    中的 velocity/acceleration/jerk truth。
    # 2. zero_order_hold predictor：预测时保持当前估计位置不变。
    # 3. p target builder：只向下游提供位置目标。
    # 4. none governor：不在 follower 前额外修改目标。
    # 5. ordinary ruckig follower：在运动约束内生成下一周期 command；名称中
    #    明确 ordinary/unshielded，防止把其他算法隐藏在 Ruckig 内部。
    position_zoh = TrackingMethodSpec(
        method_id="position_zoh_p_ruckig",
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zero_order_hold"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("ruckig"),
        description="PositionOnly → ZOH → P → ordinary unshielded Ruckig",
    )

    # 方法 B：用于覆盖更完整组件组合的基础设施验证链。
    #
    # 1. local_poly：使用最近 5 个位置样本拟合三次多项式，估计当前 p/v/a；
    #    lag_samples=0 表示估计时刻就是当前周期，不人为增加样本延迟。
    # 2. constant_jerk：以恒定 jerk 模型预测到 prediction_horizon_s。
    # 3. pva：把预测的 position/velocity/acceleration 都交给 governor。
    # 4. one_step：把 raw target 转成下一周期物理可执行的状态。
    # 5. direct：直接执行 governor 给出的状态，并输出可重构连续约束的
    #    command profile。
    local_poly = TrackingMethodSpec(
        method_id="local_poly_cj_pva_direct",
        estimator=ComponentSpec(
            "local_poly",
            {"window": 5, "degree": 3, "lag_samples": 0},
        ),
        predictor=ComponentSpec("constant_jerk"),
        target_builder=ComponentSpec("pva"),
        governor=ComponentSpec("one_step"),
        follower=ComponentSpec("direct"),
        description=(
            "LocalPolynomial → ConstantJerk → PVA → "
            "OneStepGovernor → DirectFollower"
        ),
    )

    # 四条输入覆盖两类来源：
    # - recorded_tasks_original_no_velocity_limit：原始任务序列的真实记录值按
    #   10 ms 固定采样转换得到；
    # - 其余三条：解析生成器先写规范 CSV，再由统一 loader 回读。
    #
    # 即便解析轨迹带有真实 v/a/j，RunConfig 的 position_only measurement
    # policy 仍保证在线算法只看到位置；导数 truth 仅供离线分析使用。
    input_ids = (
        "recorded_tasks_original_no_velocity_limit",
        "quadratic_with_extremum",
        "cubic",
        "sine",
    )

    return ExperimentSpec(
        # experiment_id 用于 CLI 选择实验；slug 用于形成可读的目录名称。
        experiment_id="E01",
        slug="refactor_baseline",
        title="E01 CSV-first refactor baseline",

        # question / hypothesis 描述的是“基础设施是否完整工作”，不是某个算法
        # 是否显著优于另一个算法。
        question=(
            "Can recorded and analytic references traverse one strict CSV "
            "loader, one composable tracking loop, and one tidy analysis "
            "pipeline while producing independently auditable artifacts?"
        ),
        hypothesis=(
            "All four required references and both required method "
            "compositions complete with N−1 aligned commands and complete "
            "command/trace/profile/status artifacts."
        ),
        description=(
            "Infrastructure validation only; comparisons are descriptive and "
            "must not be interpreted as scientific method rankings."
        ),

        # 只有跟踪方法组合是自变量。其余采样、输入策略、时间参数和运动约束
        # 都属于控制变量，并会进入 resolved spec 与 manifest。
        independent_variables=("tracking_method_composition",),
        controlled_variables={
            "axis_count": 1,
            "dt_s": 0.01,
            "fixed_grid": True,
            "measurement_policy": "position_only",
            "initial_state_policy": "reference_position_zero_derivatives",
            "prediction_horizon_s": 0.01,
            "minimum_duration_s": 0.01,
            "limits": {
                "max_velocity_rad_s": 4.1,
                "max_acceleration_rad_s2": 8.2,
                "max_jerk_rad_s3": 4000.0,
            },
        },

        # 消融校验器会检查参与比较的方法只能在这些字段上不同。E01 的两条链
        # 刻意覆盖五类可替换组件，因此五个路径都需要显式允许。
        allowed_method_differences=(
            "estimator",
            "predictor",
            "target_builder",
            "governor",
            "follower",
        ),

        # 路径相对于项目根目录解析。required=True 表示任一输入或对应必需方法
        # 失败都会使整个实验最终以非零状态结束，同时已完成产物仍会保留。
        inputs=tuple(
            ExperimentInput(
                input_id,
                f"data/trajectories/{input_id}.csv",
                required=True,
            )
            for input_id in input_ids
        ),
        methods=(position_zoh, local_poly),

        # controlled_variables 是面向实验审计的声明；RunConfig 是跟踪引擎实际
        # 执行的配置。两处数值故意保持一致，runner 可据此检查实验没有暗改。
        #
        # prediction_horizon_s 与 minimum_duration_s 是两个独立概念；E01 只是
        # 恰好都取一个采样周期。record_and_continue 保证单方法失败不会阻止
        # 其他方法运行，失败信息会写入 status.json / failures.csv。
        run_config=RunConfig(
            limits=MotionLimits(
                max_velocity_rad_s=4.1,
                max_acceleration_rad_s2=8.2,
                max_jerk_rad_s3=4000.0,
            ),
            minimum_duration_s=0.01,
            prediction_horizon_s=0.01,
            measurement_policy="position_only",
            failure_policy="record_and_continue",
            dt_s=0.01,
        ),

        # 每个指标只出现一个角色。角色决定报告中的阅读优先级，不改变统一的
        # 指标公式、单位、方向或缺失值策略。
        metric_roles={
            "primary": PRIMARY,
            "secondary": SECONDARY,
            "guardrail": GUARDRAIL,
            "diagnostic": DIAGNOSTIC,
        },

        # full_overlap 保留启动阶段，用于端到端契约与时间对齐验收；
        # main_evaluation 从 50 ms 开始，单独观察 estimator 启动后的表现。
        windows=(
            EvaluationWindow("full_overlap"),
            EvaluationWindow("main_evaluation", start_time_s=0.05),
        ),

        # E01 只声明一对描述性比较，并仅在所有输入、两种方法均完整时生成。
        # 比较使用 full_overlap 和 primary/secondary/guardrail；diagnostic 保留
        # 在逐方法指标表中。bootstrap 显式关闭，因为 E01 不做统计推断。
        comparison_spec=ComparisonSpec(
            pairs=(
                MethodPair(
                    baseline_method_id="position_zoh_p_ruckig",
                    candidate_method_id="local_poly_cj_pva_direct",
                    comparison_id="e01_pipeline_pair",
                ),
            ),
            metric_ids=PRIMARY + SECONDARY + GUARDRAIL,
            input_ids=input_ids,
            window_ids=("full_overlap",),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),

        # 输入超过 E01 的运动限值时仍继续跟踪：违规会在 reference_metrics
        # 中报告。这里只有 CSV 结构/时间轴等契约错误会阻止该输入进入跟踪。
        input_gate=InputGate(block_on_limit_violation=False),
    )
