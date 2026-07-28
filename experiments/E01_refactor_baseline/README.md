# E01 — CSV-first refactor baseline

E01 是新架构的基础设施验收实验。它回答的问题是：记录轨迹和解析轨迹能否
经过同一套严格 CSV 接口、同一个跟踪循环和同一套指标系统，生成完整且可独立
复核的产物。

E01 **不用于判断两种跟踪方法谁更优**。报告中的方法差值只用于确认比较管线
可以正常工作，不应作为算法优劣的科学结论。

## 一次运行的数据流

```text
data/trajectories/<input_id>.csv
        │
        ├─ load_trajectory_csv：校验 header、索引、固定 dt 和通道完整性
        │
        ├─ analyze_reference：在跟踪前独立分析输入
        │
        └─ run_tracking：逐周期运行可组合方法
               │
               ├─ command.csv
               ├─ trace.csv
               ├─ command_profiles.csv
               └─ status.json
                        │
                        └─ analyze_tracking / compare_methods
                               ├─ trajectory_metrics.csv
                               ├─ method_summary.csv
                               ├─ comparisons.csv
                               └─ report.md
```

每个控制周期使用 `target[k]` 产生 `command[k+1]`，所以长度为 `N` 的
reference 正常情况下会得到 `N-1` 个 command 样本；不会在 reference 末尾
之外额外生成一个点。

## 输入轨迹

| `input_id` | 来源 | 在线算法能看到的通道 | 离线分析可用信息 |
| --- | --- | --- | --- |
| `recorded_tasks_original_no_velocity_limit` | 原始任务序列、未启用速度限制的记录值，按 10 ms 固定采样转换 | position | position；v/a/j 由分析器另行估计 |
| `quadratic_with_extremum` | 解析生成器 | position | 解析 p/v/a/j truth |
| `cubic` | 解析生成器 | position | 解析 p/v/a/j truth |
| `sine` | 解析生成器 | position | 解析 p/v/a/j truth |

四条输入都从规范 CSV 经过统一 loader。三条解析轨迹虽然在 CSV 中保存真实
导数，但 E01 的 `measurement_policy="position_only"`，因此 estimator 和
predictor 不会读取这些导数。它们只供微分一致性检查和离线诊断使用，避免
truth 泄漏进在线算法。

输入的 position 必须有限，时间轴必须严格递增并保持 `dt=0.01 s`。CSV
结构或时间轴错误会阻止跟踪；超出 E01 运动限值则只记录到输入指标中，不会
阻止实验，因为 `block_on_limit_violation=False`。

## 方法 A：`position_zoh_p_ruckig`

```text
PositionOnly → ZeroOrderHold → P → NoGovernor → ordinary Ruckig
```

- `PositionOnly`：把当期位置测量作为 posterior，不估计真实导数。
- `ZeroOrderHold`：预测到下一目标时刻时保持位置不变。
- `P` target builder：只构造位置目标。
- `NoGovernor`：不在 follower 前附加目标修正。
- `ordinary Ruckig`：使用运动限值生成下一周期 command。这里明确是普通、
  不带 viability shield 的 Ruckig，避免组件内部发生隐藏算法替换。

这条链是最小基线，主要用于覆盖“只有位置目标 + 普通 Ruckig follower”的
标准路径。

## 方法 B：`local_poly_cj_pva_direct`

```text
LocalPolynomial → ConstantJerk → PVA → OneStepGovernor → DirectFollower
```

- `LocalPolynomial`：使用最近 5 个位置样本拟合三次多项式，估计当前
  position、velocity 和 acceleration；`lag_samples=0` 表示估计对齐当前
  周期。
- `ConstantJerk`：用恒定 jerk 状态模型预测到独立声明的 prediction
  horizon。
- `PVA` target builder：把预测得到的 p/v/a 都交给 governor。
- `OneStepGovernor`：将 raw target 转换为一个控制周期后物理可执行的状态。
- `DirectFollower`：执行 governor 的结果，并输出精确的分段 profile，供
  连续速度、加速度和 jerk 约束复算。

这条链不是“推荐算法”，而是为了让 E01 覆盖 estimator、predictor、target
builder、governor 和 follower 五类可插拔组件。

## 时间参数与运动限值

E01 固定以下控制变量：

| 参数 | 值 | 含义 |
| --- | ---: | --- |
| `dt_s` | 0.01 s | reference 和控制循环的固定采样周期 |
| `prediction_horizon_s` | 0.01 s | predictor 向前预测的物理时间 |
| `minimum_duration_s` | 0.01 s | follower 生成运动段的最小时长 |
| `max_velocity_rad_s` | 4.1 | 单轴速度限值 |
| `max_acceleration_rad_s2` | 8.2 | 单轴加速度限值 |
| `max_jerk_rad_s3` | 4000 | 单轴 jerk 限值 |

prediction horizon 和 minimum duration 是独立参数；E01 只是恰好都设为一个
采样周期。`controlled_variables` 用于实验审计和 manifest，`RunConfig`
则是跟踪引擎真正执行的配置，两处数值应保持一致。

## 指标角色

指标公式不写在 E01 中，而由统一、版本化的 `MetricSpec` 注册表提供。E01
只决定要使用哪些指标，以及它们在本实验中的阅读优先级：

- `primary`：`position_rmse`，在原始物理时间上比较 reference 和 command。
- `secondary`：MAE、bias、P95、最大绝对误差和 IAE，用于拆解误差来源。
- `guardrail`：离散输出与连续 profile 的约束违规、fallback 和 deadline
  miss，防止只看跟踪误差。
- `diagnostic`：其余适用的默认跟踪、pipeline 和 runtime 指标。

settling 指标没有启用，因为四条输入未声明 terminal-hold/step-like 窗口，
此时“稳定时间”没有可靠语义。lag 与 lag-aligned 指标即使出现在 diagnostic
中，也只能帮助理解时间延迟，不能替代 raw-time primary 指标。

## 评估窗口与方法比较

E01 生成两个评估窗口：

- `full_overlap`：使用 reference 与 command 的完整重叠区间，包含 estimator
  启动阶段，是 E01 的主要契约验收窗口。
- `main_evaluation`：从 `t=0.05 s` 开始到轨迹结束，便于单独观察短暂启动期
  之后的表现。

方法比较只使用 `full_overlap`，覆盖 primary、secondary 和 guardrail 指标。
如果某个输入上任一方法不完整，该方法对不会进行 complete-case 比较，状态
应为 `unavailable_incomplete_pair`。

E01 显式设置 `bootstrap_repetitions=0`，不计算配对 bootstrap 置信区间，也
不生成显著性结论。

## 运行与产物

从项目根目录运行：

```bash
otg-lab run E01_refactor_baseline
```

产物写入当前实验自己的 `runs/`。这里使用 `runs` 而不是 `results`，因为目录
保存的是完整运行记录（manifest、输入副本、逐周期 trace、status 和分析），
并非只有最终汇总结果。

```text
experiments/E01_refactor_baseline/runs/<utc_timestamp>__<spec_hash>/
  manifest.json
  inputs/<input_id>/
    reference.csv
    reference.meta.json
    reference_derived.csv
  methods/<method_id>/<input_id>/
    command.csv
    trace.csv
    command_profiles.csv
    status.json
  analysis/
    reference_metrics.csv
    trajectory_metrics.csv
    method_summary.csv
    comparisons.csv
    failures.csv
    report.md
    figures/
```

阅读产物时可以按下面的顺序：

1. 先看 `manifest.json`，确认 resolved spec、输入哈希、组件参数和运行环境。
2. 再看 `status.json` 与 `failures.csv`，确认每个方法/输入是否完整。
3. 用 `trace.csv` 定位单周期的 measurement、posterior、prediction、target、
   command、solver/fallback 和 runtime。
4. 用 `command_profiles.csv` 复核采样点之间的连续约束。
5. 最后看 tidy 指标、方法摘要和描述性比较。

`failure_policy="record_and_continue"` 表示单个方法失败后仍运行其他方法，并
保留已经产生的 trace 和 status；但 E01 的输入与方法均为必需项，所以任一
必需运行失败都会让实验最终以非零状态退出。

## 成功标准

E01 通过需要满足：

- 四条规范输入都完成输入分析；
- 两条方法链在四条输入上都产生 `N-1` 个正确对齐的 command；
- command、trace、profile 和 status 产物完整且 schema 校验通过；
- 连续 profile 可以独立重算运动约束；
- 仅凭落盘 CSV、metadata 和 manifest 可以在新进程中重算指标；
- manifest、tidy metrics、方法摘要、比较、失败表、报告和图均已生成；
- 组件 ID 与 trace 能证明没有隐藏算法替换。

如果这些条件成立，E01 证明的是新 CSV-first 实验基础设施已经连通，而不是
其中某一种跟踪方法在科学意义上更优秀。
