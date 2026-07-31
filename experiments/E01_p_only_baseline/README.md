# E01 — Scheduled P-only trajectory baseline

E01 是独立的 P-only 基准审计。它记录三条解析轨迹以及 original、
velocity-limit 两条 recorded trajectory 在两种明确区分的配置下，只提供
下一拍 position target 时的跟踪表现。

E01 不再承担 CSV-first 基础设施验收职责，也不包含 PV/PVA、governor 或
direct follower 方法。解析轨迹只用于中间正确性验证。Original recorded 的
`4.2/8.2/41` arm 是最终报告的当前实际上线 baseline；上线 PV/PVA 实验收益
仍只允许使用 velocity-limit recorded 的原 `4.1/8.2/4000` paired baseline。

## Target 语义

唯一方法为 `p_kp1_baseline`：

```text
PositionOnly
→ ZeroOrderHold
→ scheduled P[k+1], V=A=0
→ NoGovernor
→ ordinary unshielded Ruckig
```

每个控制周期的 follower 起始 PVA 是上一周期已经提交、在当前时刻生效的
command state；position-only estimator 同时接收当前 position 样本。target
builder 从预先声明的 reference schedule 读取已可用的 `P[k+1]`，并显式把
target velocity 和 acceleration 设为零，随后由普通 Ruckig 提交
`command[k+1]`。

因此 scheduled P baseline 在“`t[k]` 时已经知道下一拍 target command
`P[k+1]`”的项目契约下是因果的。把它改成 `P[k]` 会变成另一种无下一拍排程
能力的契约，并额外增加一拍 target age；不应用来修正当前 baseline 的 lag。

这里的 scheduled `P[k+1]` 与旧 E01/E02 的 measured `P[k]` 不是同一种语义。
`measurement_policy="position_only"` 表示 estimator 不会读取解析 CSV 中的
velocity、acceleration 或 jerk truth；已知的下一拍 position schedule 是实验
单独声明的受控条件。

## 输入、证据角色与两套 baseline

E01 包含五条输入：

- `quadratic_with_extremum`
- `cubic`
- `sine`
- `recorded_tasks_original_no_velocity_limit`
- `recorded_tasks_simplified_with_velocity_limit`

前三条与 E03–E06 相同，只验证实现和方法语义。两条 recorded input 的角色
不同：

| baseline 角色 | input | VAJ | 用途 |
|---|---|---:|---|
| 当前实际上线 baseline | `recorded_tasks_original_no_velocity_limit` | 4.2/8.2/41 | 报告现状基线 |
| 实验 paired baseline | `recorded_tasks_simplified_with_velocity_limit` | 4.1/8.2/4000 | A04/A06 既有相对收益分母 |

新增 current-online case 不改变其他实验的 baseline，也不把跨 waveform 的
数值差异解释为单因素收益。两个 case 共享以下非限值配置：

| 参数 | 值 |
|---|---:|
| `dt_s` | `0.01 s` |
| `prediction_horizon_s` | `0.01 s` |
| `minimum_duration_s` | `0.01 s` |
| governor | none |
| follower | ordinary unshielded Ruckig |

方法、解析输入、指标和运行配置由 E01 与 E03–E06 的共享构造代码生成，测试
会阻止这些定义发生漂移。E03–E06 仍各自保留同定义的 baseline arm，以便在
同一次 run 内完成解析正确性比较。Recorded PV/PVA 的 paired baseline 仍取自
E11/E12 同次运行；E01 的 current-online case 只补充报告现状，不改变这些
配对结果或增加其样本量。

## 指标与窗口

`position_rmse` 是 primary metric，使用原始物理时间，不做 lag 对齐。
secondary、guardrail 和 diagnostic 指标与 E03–E06 完全一致。

- `full_overlap`：完整重叠区间；
- `main_evaluation`：`t>=0.04 s` 到各输入结束。解析轨迹自然结束于 3.00 s，
  recorded trajectory 使用完整记录。

E01 只有一种 P-only 方法语义，但有两个 limits case，因此不声明内部 method
pair。`comparisons.csv` 为空表；
后续 E01/E03–E06 横向分析应从逐轨迹 `trajectory_metrics.csv` 按相同
`input_id`、`window_id` 和 `metric_id` 配对。

## 运行与产物

从项目根目录运行：

```bash
uv run otg-lab run E01
```

也可以使用完整名称：

```bash
uv run otg-lab run E01_p_only_baseline
```

产物位于：

```text
experiments/E01_p_only_baseline/runs/<utc_timestamp>__<spec_hash>/
  manifest.json
  inputs/<input_id>/
  methods/
    p_kp1_baseline/<input_id>/
    p_kp1_current_online_v4p2_a8p2_j41/<input_id>/
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
```

一次有效的 baseline run 应让两个 case 都完成五条输入；每条长度为 `N` 的
reference 产生 `N-1` 个正确对齐的 command，并生成可独立审计的 trace、
profile、status 和 tidy metrics。
