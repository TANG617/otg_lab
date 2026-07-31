# A01 — E03–E06 解析轨迹 PV/PVA 配对正确性审计

## 目标

固定使用 E03–E06 的已发布解析轨迹 result，回答：

> 在 estimator/predictor、输入、约束和 follower 相同的条件下，增加
> acceleration target（PV → PVA）如何影响结果？

这里的 `A01` 是跨实验分析编号，不是新的 Ruckig 实验。

该分析只用于中间方法正确性验证，不参与 recorded trajectory 的上线
PV/PVA 选型，不提供上线收益数字。上线比较的唯一输入是
`recorded_tasks_simplified_with_velocity_limit`。

## 2 × 2 来源矩阵

| derivative source / method matrix | PVA | PV |
|---|---|---|
| truth | E03 | E05 |
| finite difference | E04 | E06 |

四个来源都包含同定义的 `P[k+1]` baseline。E04/E06 还包含各自 truth ceiling
和五个同族有限差分方法。

## 分析边界

- truth 层按 `input_id` 对比 E03 PVA truth 与 E05 PV truth；
- finite-difference 层按同名方法族对比 E04 PVA 与 E06 PV；
- `position_rmse` 使用 `main_evaluation` raw-time 值作为核心 readout；
- 全部现有指标进入逐轨迹配对审计表；
- 不做上线有限差分方法排名，也不评价 FD 与 truth 的距离；
- 不把解析轨迹结论外推到 recorded trajectory。

不能把四份 baseline summary mean 当成四个独立样本。正式横向计算必须从
`combined_trajectory_metrics.csv` 的逐输入 row 配对。

## 当前来源状态

`analysis.yaml` 已固定到当前四个 completed source：

- E03–E06 都引用各自 `results/` 中的精确 result；
- 四个 manifest 记录同一个 Git commit；
- 四个 manifest 都记录 `git.dirty=true`，因此当前适合探索性整理，不能把
  “工作区干净”写成证据。

## 运行

只校验：

```bash
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py --check
```

生成完整分析：

```bash
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py
```

输出写入新的 `runs/<analysis-run-id>/`，包括 run-local `RESULTS.md`、CSV、
`validation.md`、`chart_map.md`、确定性 manifest，以及 PNG/SVG 图表。
`runs/<analysis-run-id>/work/` 保留统一收集层：

```text
source_inventory.csv
combined_trajectory_metrics.csv
provenance.json
```

`--check` 只在内存中执行来源、重复结果和配对完整性校验，不创建 analysis
run。复核通过后把完整 run 复制到 `results/<analysis-run-id>/`。
