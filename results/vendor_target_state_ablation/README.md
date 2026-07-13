# 厂商约束下的目标状态消融实验

这是当前正式结果集。它在统一时间轴和统一运动约束下，隔离比较目标中是否包含 position、velocity、acceleration，以及 velocity/acceleration 的来源。

## 固定口径

- 单关节，单位为 rad、rad/s、rad/s²、rad/s³。
- `DT=10 ms`，100 Hz；CSV 只读取 `value`，忽略 `elapsed time`、timestamp 和 topic，每行固定代表 10 ms。
- 厂商基线固定为 `vmax=4.1`、`amax=8.2`、`jmax=4000`。
- 无 lookahead；`minimum_duration=10 ms`。
- 周期 `k` 把属于 `t[k]` 的 target 传入普通 `Ruckig.update()`，返回状态记为 `output[k+1]`。
- 原始 target 与约束投影后的 target 分开保存和统计。

`run.json` 是参数与数据口径的最终依据。

## 实验矩阵

| ID | 目标状态 | 导数来源 | 因果性 | 适用数据 |
| --- | --- | --- | --- | --- |
| `p` | `[p,0,0]` | 无 | 在线 | 全部 |
| `pv_truth` | `[p,v*,0]` | 解析真值 | oracle | 解析曲线 |
| `pva_truth` | `[p,v*,a*]` | 解析真值 | oracle | 解析曲线 |
| `pv_backward` | `[p,v̂,0]` | 历史后向差分 | 在线 | 全部 |
| `pva_backward` | `[p,v̂,â]` | 历史后向差分 | 在线 | 全部 |
| `pv_central_offline` | `[p,v̂,0]` | 标准中心差分 | 非因果，使用一个未来样本 | 全部 |
| `pva_central_offline` | `[p,v̂,â]` | 标准中心差分 | 非因果，使用一个未来样本 | 全部 |
| `pv_central_causal` | `[p,v̂,0]` | 延迟一拍的中心估计并传播到当前时刻 | 在线补充 | 全部 |
| `pva_central_causal` | `[p,v̂,â]` | 延迟一拍的中心估计并传播到当前时刻 | 在线补充 | 全部 |

解析真值只用于隔离“可靠导数有没有价值”，不代表仅靠位置可以在线获得真值。离线中心差分也不是可部署方案；它用于区分差分公式误差和在线因果性代价。

## 关键结果

解析参考本身均位于厂商限制内。加入可靠 velocity 后，三个解析数据的滞后都降到由接口时序决定的 10 ms；在本组低动态平滑曲线上，PVA 相对 PV 没有进一步降低 position error。

| 数据 | P：RMSE / lag | PV truth：RMSE / lag | PVA truth：RMSE / lag |
| --- | ---: | ---: | ---: |
| Quadratic | 0.07567 / 80 ms | 0.00928 / 10 ms | 0.00928 / 10 ms |
| Cubic | 0.01362 / 40 ms | 0.00345 / 10 ms | 0.00345 / 10 ms |
| Sine | 0.04971 / 70 ms | 0.00675 / 10 ms | 0.00675 / 10 ms |

下一周期解析 oracle 的三个数据均为 0 ms lag，最大 position error 不超过 `1.06e-9 rad`。这验证了 `target[k] -> output[k+1]` 的索引约定，也说明普通 Ruckig 能执行动态一致、一步可达的未来状态。

CSV 没有 velocity/acceleration 真值。所有直接差分方案都未超过 P 基线：

| CSV 方法 | RMSE | lag | 原始 target 投影率 |
| --- | ---: | ---: | ---: |
| P | **0.03519** | **70 ms** | 0% |
| PV backward | 0.06398 | 110 ms | 0% |
| PVA backward | 0.03874 | **70 ms** | 32.64% |
| PV center offline | 0.06723 | 130 ms | 0% |
| PVA center offline | 0.04470 | 80 ms | 32.64% |
| PV center causal | 0.07856 | 160 ms | 0% |
| PVA center causal | 0.04411 | 80 ms | 32.64% |

CSV 的三点二阶差分峰值为 `280.09 rad/s²`；所有 PVA 差分方法都有 32.64% 的原始目标状态需要投影。由此不能得出“导数无用”，只能得出“未滤波差分不是可靠的在线 target-state estimator”。

OFAT 只解释约束敏感性：CSV 的 P 基线在 `j=41` 时 RMSE 是厂商点的 2.29 倍、lag 多 120 ms；`j=8000` 相对厂商 `4000` 仅为 0.99 倍、lag 不变。提高 `amax` 会明显降低误差，但这些点超出厂商限制，不能作为部署参数建议。

## 图表与数据

| 文件 | 内容 |
| --- | --- |
| `ablation_summary.png/.svg` | 所有方法相对 P 的 RMSE 和 lag 总览 |
| `derivative_sources.png/.svg` | 后向、离线中心、因果中心相对解析导数的误差 |
| `target_state_ablation_*.png/.svg` | 每个数据集的 position 与 signed error |
| `constraint_sensitivity_rmse.png/.svg` | acceleration/jerk 单因素 RMSE 敏感性 |
| `constraint_sensitivity_best_lag_ms.png/.svg` | acceleration/jerk 单因素 lag 敏感性 |
| `target_state_ablation_metrics.csv` | 34 条正式基线指标 |
| `derivative_source_metrics.csv` | 12 条解析导数误差指标 |
| `oracle_sanity_metrics.csv` | 3 条下一周期 oracle 控制实验 |
| `reference_peak_metrics.csv` | 3 条解析参考峰值 |
| `limit_sensitivity_metrics.csv` | 408 条 OFAT 指标 |

`output_max_new_jerk` 是每次执行后 `OutputParameter.new_jerk` 的样本峰值，不是冻结整段 `trajectory` 内部 jerk 的全局峰值；`output_max_sampled_jerk` 则是相邻输出 acceleration 的差分，两者不能混用。

## 复现与验证

```bash
.venv/bin/python run_target_state_ablation.py \
  --mode all \
  --output-dir results/vendor_target_state_ablation

.venv/bin/python -m unittest discover -s tests -v
```

本结果使用普通 `Ruckig.update()`，没有实测 Ruckig Pro `Trackig`。它可以支持“需要可靠 estimator、未来参考和面向移动目标的约束跟随机制”的判断，不能单独证明某个商业 API 已经最优或必需。
