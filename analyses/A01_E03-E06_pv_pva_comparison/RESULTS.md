# A01 — 解析轨迹 PV/PVA 配对正确性审计

> 证据角色：仅用于中间方法正确性验证，不参与 recorded trajectory
> 的上线 PV/PVA 选型，也不能据此声明收益。

## 技术摘要

- 本分析只检查解析轨迹上，同一 estimator/predictor、输入、约束和 follower
  下加入 acceleration target（PV → PVA）后的配对行为。
- 证据包含 1 组 truth 配对和 5 组同名有限差分配对；三条解析轨迹均按
  `input_id` 独立保留。
- E01 的独立 `p_kp1_baseline` 只用于复现审计；E03–E06 内部 baseline
  继续作为同次运行的配对坐标，E01 不增加样本量。
- A01 不进行上线方法排名，不评价有限差分与 truth ceiling 的距离，也不向
  recorded trajectory 外推。
- 来源 run 均 completed 且记录同一 commit，但 manifest 标记
  `git.dirty=true`，因此结论应作为可审计的固定结果分析，而不是 clean-build
  完全复现证明。

## Position RMSE 的逐输入配对

主窗口为 `main_evaluation = 0.04–3.00 s`。`improvement` 已按
lower-is-better 方向计算为 `PV - PVA`；正值表示同一方法族中 PVA 的 RMSE
更低。Truth 数值接近机器精度，因此不报告不稳定的相对比值。

| 方法族 | 轨迹 | PV RMSE | PVA RMSE | PVA-PV | 改善 |
|---|---|---|---|---|---|
| Truth k+1 | quadratic_with_extremum | 4.246e-16 | 4.493e-16 | 2.476e-17 | -2.476e-17 |
| Truth k+1 | cubic | 6.109e-11 | 6.126e-11 | 1.750e-13 | -1.750e-13 |
| Truth k+1 | sine | 2.682e-14 | 2.683e-14 | 7.864e-18 | -7.864e-18 |
| Estimator backward O1 | quadratic_with_extremum | 0.010050 | 0.010202 | 1.526e-04 | -1.526e-04 |
| Estimator backward O1 | cubic | 0.003459 | 0.003459 | -4.537e-12 | 4.537e-12 |
| Estimator backward O1 | sine | 0.007846 | 0.008266 | 4.199e-04 | -4.199e-04 |
| Estimator backward O2 | quadratic_with_extremum | 0.009291 | 0.009291 | 3.263e-14 | -3.263e-14 |
| Estimator backward O2 | cubic | 0.003459 | 0.003459 | 4.458e-14 | -4.458e-14 |
| Estimator backward O2 | sine | 0.006762 | 0.006762 | 1.044e-13 | -1.044e-13 |
| Estimator centered O2 | quadratic_with_extremum | 0.018580 | 0.018580 | 2.229e-14 | -2.229e-14 |
| Estimator centered O2 | cubic | 0.006918 | 0.006918 | 2.009e-14 | -2.009e-14 |
| Estimator centered O2 | sine | 0.013520 | 0.013520 | 1.551e-13 | -1.551e-13 |
| Predictor backward O1 | quadratic_with_extremum | 0.001001 | 0.001297 | 2.963e-04 | -2.963e-04 |
| Predictor backward O1 | cubic | 2.410e-09 | 2.512e-09 | 1.027e-10 | -1.027e-10 |
| Predictor backward O1 | sine | 0.001232 | 0.001724 | 4.922e-04 | -4.922e-04 |
| Predictor backward O2 | quadratic_with_extremum | 6.700e-10 | 6.711e-10 | 1.037e-12 | -1.037e-12 |
| Predictor backward O2 | cubic | 7.900e-10 | 7.943e-10 | 4.328e-12 | -4.328e-12 |
| Predictor backward O2 | sine | 9.908e-06 | 5.642e-10 | -9.908e-06 | 9.908e-06 |

## 全指标审计

完整逐指标结果见 `results/pv_pva_metric_pairs.csv`。正文按指标语义分组，
而不是将不同单位或角色压缩成一个总分。

| 指标域 | 窗口 | 指标数 | 可比较行 | 不可用行 |
|---|---|---|---|---|
| lag | full_overlap | 3 | 54 | 0 |
| lag | main_evaluation | 3 | 54 | 0 |
| limits | full_overlap | 15 | 216 | 54 |
| limits | main_evaluation | 10 | 126 | 54 |
| other | full_overlap | 5 | 90 | 0 |
| other | main_evaluation | 3 | 54 | 0 |
| runtime/reliability | full_overlap | 30 | 540 | 0 |
| smoothness/dynamics | full_overlap | 13 | 180 | 54 |
| smoothness/dynamics | main_evaluation | 12 | 162 | 54 |
| stop-go | full_overlap | 5 | 90 | 0 |
| stop-go | main_evaluation | 5 | 90 | 0 |
| target-state error | full_overlap | 15 | 234 | 36 |
| target-state error | main_evaluation | 13 | 234 | 0 |
| tracking | full_overlap | 7 | 126 | 0 |
| tracking | main_evaluation | 7 | 126 | 0 |

## Guardrail 与缺失通道

`results/guardrail_summary.csv` 保留 limit violation、profile constraint、
fallback、solver failure、deadline miss 以及 jerk channel 的原始状态。输出
command jerk 不可用的行继续标记为 unavailable，不解释为“零违规”。

## 图表

- `results/pv_pva_position_rmse.png/.svg`：六个方法族的逐轨迹 PV/PVA
  配对点图，使用对数轴。
- `results/pva_effect_on_position_rmse.png/.svg`：五组有限差分的
  direction-aware RMSE 相对变化；正值表示 PVA 优于 PV。

图表只展示配对效应，不构成跨方法排名。

## 限制

- 只有三条单轴、平滑、无噪声、100 Hz 解析轨迹，不计算 p-value、置信区间
  或统计推广。
- 本分析不是上线证据；上线比较只允许使用 velocity-limit recorded
  trajectory。
- `full_overlap` 仅用于 whole-run guardrail/diagnostic；tracking 主结论使用
  `main_evaluation`。
- Truth RMSE 接近数值精度，只比较绝对值和绝对差。
- 当前来源来自 dirty worktree。

## 复现

```bash
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py --check
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py
```
