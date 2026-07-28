# E-series Experiments

每轮探究位于独立目录：

```text
experiments/E01_topic/
  experiment.py
  README.md
```

`experiment.py` 通过 `build_experiment(project_root) -> ExperimentSpec`
构造声明，并完整填写问题、目标/假设、自变量、控制变量、输入 CSV、方法矩阵、
允许变化的字段、评估窗口、指标角色和比较关系。
自定义 estimator/predictor 等组件可以在实验模块中定义并通过
`ComponentSpec.factory` 注入，无需修改核心 registry。

约束或参数矩阵可以通过 `ExperimentCase` 为同一个基础方法声明多个独立
`RunConfig`。显式 case 的 `case_id` 会成为 artifact 目录名和 tidy 指标中的
`method_id`，manifest 另外保存基础方法 ID、因子值与完整运行配置。比较校验会
同时审计组件和 `run_config` 差异，实验必须在
`allowed_method_differences` 中明确允许可变化的配置路径。

二维全组合指标面可以使用 `FactorHeatmapSpec` 声明 row/column 因子、完整档位、
评估窗口、metric 和 baseline case。runner 会先写可独立审计的同名 CSV，再按
baseline ratio 或相对 baseline 的缩放差值生成 PNG/SVG；不声明
cases/heatmaps 的实验保持原有单配置行为。

运行：

```bash
otg-lab run E01_topic
```

运行产物位于
`experiments/<experiment-directory>/runs/<timestamp>__<spec_hash>/`。
使用 `runs` 是因为这里保存 manifest、输入副本、每种方法的
command/trace/profile/status，以及 tidy 指标、比较、失败表、报告和图等
完整运行记录，而不只是最终 `results`。单个方法失败不阻断其他方法；必需
方法失败会使命令最终返回非零。

CI 或临时试跑可以使用 `--runs-root <path>` 覆盖该实验的 run 容器目录。

新实验可用以下命令生成：

```bash
otg-lab new-experiment E02 estimator_ablation
```

## E03–E06 P/PV/PVA 对比

这四组实验共享 `dt=10 ms`、V/A/J 限值 `4.1 / 8.2 / 4000`、无 governor、
ordinary Ruckig follower，以及 `quadratic_with_extremum`、`cubic`、`sine`
三条解析真值轨迹：

| 实验 | 方法矩阵 |
|---|---|
| E03 | `P[k+1]` baseline、`PVA_truth[k+1]` |
| E04 | baseline、PVA truth 上界、5 个严格因果差分方法 |
| E05 | `P[k+1]` baseline、`PV_truth[k+1]` |
| E06 | baseline、PV truth 上界、5 个严格因果差分方法 |

5 个差分方法是 backward O1/O2 estimator、延迟一拍的 centered O2 estimator，
以及 future backward O1/O2 predictor。estimator target 的 represented time
分别相对 command 落后 1/2 拍；predictor 直接表示 `k+1`，但只使用截至 `k`
的 position measurement。truth predictor 明确标记为 noncausal、offline-only。

主验收窗口为 `0.04–3.00 s`，使用未作 lag 对齐的 raw-time
`position_rmse`。`analysis/acceptance.csv` 给出逐轨迹结果，
`analysis/figures/rmse_ratio_vs_p.{png,svg}` 给出相对统一 P baseline 的 RMSE
ratio；ratio 小于 1 表示改善。

E04/E06 还会生成 `analysis/lag_comparison.csv` 和
`analysis/figures/lag_vs_p_and_truth.{png,svg}`。图中同时包含统一 P-only
baseline、分别与 E03/E05 等价的 PVA/PV truth，以及五种差分方法；CSV 另外给出
每种方法相对 P-only 和 truth 的 lag 差值。lag 仍只作诊断，不参与 primary
RMSE 对齐。

## E08 记录任务波形上的 PVA 迁移

E08 把 E04 的统一 `P[k+1]` baseline 和五个严格因果 PVA 差分方法原样迁移到
`recorded_tasks_simplified_with_velocity_limit`。输入保留记录 position 的行序，
并使用现有固定 10 ms canonical 时间轴；不保留 raw timestamp 抖动，也不提供
V/A/J truth。

实验继续使用 E04 的 V/A/J 限值 `4.1 / 8.2 / 4000` 和 ordinary unshielded
Ruckig，不做输入平滑。执行前增加显式 configured-limit projection：position
保持不变，V/A 超限时投影到当前配置允许域；raw target 仍原样写入审计产物。
六个方法均为 runner 层面的 required 方法。

运行：

```bash
uv run otg-lab run E08
```

主窗口为 `t≥0.04 s` 到记录结束。候选只有完整运行后才能计算相对 P-only 的
raw-time position RMSE；若投影后仍有方法不完整，其标准比较标为
`unavailable_incomplete_pair`，禁止用 prefix RMSE 排名。

除标准产物外，E08 写出逐周期 `raw_target_scan.csv`、方法级
`raw_target_feasibility.csv`、`acceptance.csv` 和
`acceptance_summary.md`。raw-target 扫描只重放已注册的
estimator/predictor/target-builder，不调用 governor 或 follower，用于审计
整条记录波形上投影前的 target V/A 与 Ruckig admissibility。跟踪图和 raw
target 图均以小叉号标出所有发生投影的周期，并用大叉号强调首次投影位置。
