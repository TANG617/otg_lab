# E02 — Position-only acceleration–jerk limit sensitivity

E02 使用同一条记录轨迹和同一条纯位置目标跟踪链，探究加速度与 jerk 限值的
二维组合如何影响 raw-time position RMSE。实验只描述约束敏感性，不推断部署
最优参数。

## 固定输入与跟踪链

输入为：

```text
data/trajectories/recorded_tasks_original_no_velocity_limit.csv
```

它是
`data/raw/recorded_tasks/original_no_velocity_limit.csv`
按 `dt=10 ms` 转换得到的 canonical trajectory；source path、source SHA-256
和转换约定已记录在配套 metadata 中。

在线链固定为：

```text
PositionOnly → ZeroOrderHold → P → NoGovernor → ordinary Ruckig
```

在线阶段只读取 position。CSV 中不存在可供在线使用的 velocity、
acceleration 或 jerk truth；离线导数只用于输入难度诊断。

## 约束矩阵

速度上限始终固定为厂商值 `4.1 rad/s`。实验执行下面 35 个
`max acceleration × max jerk` 全组合：

| 因子 | 档位 |
| --- | --- |
| acceleration `[rad/s²]` | `4.1, 6, 8.2, 12, 16.4` |
| jerk `[rad/s³]` | `41, 200, 800, 1600, 3200, 4000, 8000` |

vendor 基准格为 `A=8.2 rad/s², J=4000 rad/s³`。`A=12/16.4` 和
`J=8000` 只是超出 vendor 点的敏感性探针，不能作为部署建议。

除 A/J 外，所有 case 均固定：

- `dt_s=0.01`
- `prediction_horizon_s=0.01`
- `minimum_duration_s=0.01`
- 初始 position 取 reference 首点，velocity/acceleration 为零
- `measurement_policy="position_only"`
- `failure_policy="record_and_continue"`

## 指标与图

`position_rmse` 是 primary metric，并始终在原始物理时间上计算，不做 lag
对齐。实验保留两个窗口：

- `full_overlap`：包含初始化阶段，供完整审计；
- `main_evaluation`：从 `t=0.03 s` 开始，是主热力图口径。

每个热力图单元格显示：

```text
当前 case 的 position RMSE / vendor case 的 position RMSE
```

颜色编码使用该 ratio 的 `log₂`，以 vendor 的 `0` 为中心。蓝色表示 RMSE
低于 vendor，橙色表示高于 vendor；颜色与数值均只说明本轨迹上的描述性
敏感性。

每个 case 还会保存 MAE、bias、P95、最大绝对误差、IAE、lag 诊断、输出与
连续 profile 约束、fallback、deadline 和 runtime 指标。

## 运行与产物

从项目根目录运行：

```bash
uv run otg-lab run E02
```

主要产物位于：

```text
experiments/E02_position_only_limit_sensitivity/runs/<run>/
  manifest.json
  methods/<case_id>/<input_id>/
    command.csv
    trace.csv
    command_profiles.csv
    status.json
  analysis/
    trajectory_metrics.csv
    comparisons.csv
    constraint_sensitivity_rmse.csv
    constraint_sensitivity_lag_ms.csv
    report.md
    figures/
      constraint_sensitivity_rmse.png
      constraint_sensitivity_rmse.svg
      constraint_sensitivity_lag_ms.png
      constraint_sensitivity_lag_ms.svg
```

`constraint_sensitivity_rmse.csv` 保存每格的绝对 RMSE、vendor RMSE、ratio、
`log₂(ratio)`、V/A/J、状态与样本数；PNG/SVG 只是该表的可视化。

`constraint_sensitivity_lag_ms.csv` 和同名图展示 lag 相对 vendor 的变化：
`Δlag_ms = 1000 × (lag_case − lag_vendor)`。正值表示比 vendor 更滞后，
负值表示滞后更小，vendor 单元格为 `0`。lag 是在 `±1 s` 搜索范围内，
以 `10 ms` 整数采样位移寻找 RMSE 最小值所得；它是时序诊断，不替代
raw-time position RMSE 主指标。
