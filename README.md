# OTG Lab

`otg_lab` 是一个面向单轴实时轨迹跟踪实验的 CSV-first 工具箱。所有目标轨迹
与输出命令都使用同一份固定网格 CSV 契约；估计、预测、目标构造、约束处理和
Ruckig 跟随器可以独立组合。

核心数据流：

```text
trajectory.csv
  -> reference analysis
  -> estimator / predictor / target / governor / follower
  -> command.csv + trace.csv + command_profiles.csv
  -> tracking analysis + comparisons
```

## 快速开始

安装开发环境：

```bash
uv sync --extra dev
```

运行 E01 基线：

```bash
uv run otg-lab run E01_refactor_baseline
```

创建下一轮实验：

```bash
uv run otg-lab new-experiment E02 estimator_ablation
```

运行测试和静态检查：

```bash
uv run pytest
uv run ruff check .
```

## Python API

公开数据流由以下函数组成：

```python
load_trajectory_csv(...)
write_trajectory_csv(...)
generate_analytic_trajectory(...)
analyze_reference(...)
run_tracking(...)
analyze_tracking(...)
compare_methods(...)
run_experiment(...)
```

每个函数只接收单轴公开类型或版本化 spec。落盘方法产物可通过
`load_tracking_run_artifacts()` 在新进程中重建，再交给
`analyze_tracking()` 独立重算指标。

## 设计边界

- 只支持单轴轨迹。
- 核心只接受严格等间隔、严格递增的时间网格。
- 规范输出是 follower 实际提交的 command，不再设置第二层执行仿真。
- 解析轨迹也必须先写为规范 CSV，再通过公共 loader 进入实验。
- position-only 输入的离线导数只用于输入分析，不会写回 truth 或泄漏给在线
  estimator/predictor。
- 运行产物写入对应实验的
  `experiments/<experiment>/runs/<timestamp>__<spec_hash>/` 并默认忽略；
  输入 CSV 和实验代码进入版本控制。

详细契约见：

- [轨迹 CSV](docs/trajectory_csv.md)
- [周期 trace 与 profile](docs/trace_csv.md)
- [指标](docs/metrics.md)
- [E 系列实验](docs/experiments.md)
