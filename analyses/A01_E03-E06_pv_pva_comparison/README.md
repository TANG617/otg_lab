# A01 — E03–E06 P/PV/PVA 结果对比

## 目标

整理 E03–E06 的统一证据，回答：

> 在相同解析轨迹、时序、motion limits 和 follower 下，PV 与 PVA target
> 在 truth ceiling 和严格因果有限差分两种 derivative source 中分别有什么差异？

这里的 `A01` 是跨实验分析编号，不是新的 Ruckig 实验。

## 2 × 2 来源矩阵

| derivative source / method matrix | PVA | PV |
|---|---|---|
| truth | E03 | E05 |
| finite difference | E04 | E06 |

四个来源都包含同定义的 `P[k+1]` baseline。E04/E06 还包含各自 truth ceiling
和五个同族有限差分方法。

## 比较顺序

1. 先在每个实验内部读取相对其 P baseline 的改善和 guardrail；
2. truth 层按 `input_id` 对比 E03 PVA truth 与 E05 PV truth；
3. finite-difference 层按 `input_id + stencil family` 对比 E04 PVA 与 E06 PV；
4. `position_rmse` 使用 `main_evaluation` raw-time 值作为 primary readout；
5. `lag_s` 和 `lag_aligned_rmse` 只作诊断，不改变 primary 排名。

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

生成可重建的收集层：

```bash
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py
```

输出位于 `work/`：

```text
source_inventory.csv
combined_trajectory_metrics.csv
combined_comparisons.csv
provenance.json
```

这一步只统一来源和 schema；下一步才是在这些合并表之上建立 method-family
映射、配对统计、图表和最终报告。
