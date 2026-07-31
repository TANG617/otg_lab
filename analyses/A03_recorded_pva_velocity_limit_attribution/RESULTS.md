# A03 — Recorded PVA 劣化与 velocity-limit 归因

> 证据角色：这是 original recorded waveform 上的归因诊断，不参与上线
> PV/PVA 排名或收益计算。上线比较只使用 velocity-limit recorded
> trajectory。

## 结论

原始 `original_no_velocity_limit` 轨迹上，五种 PVA 的 raw-time position
RMSE 在 `Vmax=4.1` 和放宽后的 `Vmax=10` 下都高于 scheduled P baseline：
**true**。但两种 Vmax 下每个方法的 PVA/P 比值完全一致，
预注册的 log-ratio interaction 均为 0。Scheduled P 的 observed lag 是
**60 ms**，五种 PVA 是 **110–160 ms**；放宽 Vmax 后 lag 也逐方法完全不变。
`Vmax=10` 对 velocity/stopping mechanism 非绑定。因而“PVA 的 RMSE/lag
劣化由 runtime velocity limit 导致”的归因结论为：
**拒绝**。

也就是说，这组实验只说明 original waveform 内部的 Vmax 干预不改变
RMSE/lag 关系，不能把该结论用于 velocity-limit waveform 的 PV/PVA
排序。主要实际干预来自 acceleration clipping；它在两个 Vmax 条件下保持
不变。采集时是否限速属于不同输入曲线的观察性标签，三条曲线长度和形状不同，
因此不能把跨文件差异解释为采集限速的单因素因果效应。

## Original 轨迹 RMSE–lag 逐方法结果

| 方法 | RMSE/P @4.1 | RMSE/P @10 | lag @4.1 ms | lag @10 ms | \|lag\| excess vs P | lag interaction ms | 归因 |
|---|---|---|---|---|---|---|---|
| Backward O1 | 1.984580 | 1.984580 | 110 | 110 | +50 | 0 | rejected_runtime_velocity_limit |
| Backward O2 | 2.506971 | 2.506971 | 160 | 160 | +100 | 0 | rejected_runtime_velocity_limit |
| Centered O2 | 2.226767 | 2.226767 | 140 | 140 | +80 | 0 | rejected_runtime_velocity_limit |
| Future O1 | 2.251172 | 2.251172 | 130 | 130 | +70 | 0 | rejected_runtime_velocity_limit |
| Future O2 | 2.359525 | 2.359525 | 140 | 140 | +80 | 0 | rejected_runtime_velocity_limit |

## Original 轨迹投影机制合计

| Vmax | velocity clip | acceleration clip | stopping envelope |
|---|---|---|---|
| 4.1 | 8 | 4212 | 0 |
| 10 | 0 | 4212 | 0 |

## 判定规则

- co-primary outcomes：`t>=0.04 s` 的 raw-time position RMSE 与
  `|observed lag|`；
- 同一输入、同一 Vmax 下以 scheduled `P[k+1]` 为 baseline；
- 只有 relaxed Vmax 非绑定、limited Vmax 实际绑定且 RMSE 或 lag 随干预改善，
  才支持相应的 velocity-limit 归因；
- observed lag 是 10 ms 整数采样移位诊断，不是 wall-clock latency；
- 本分析不进入上线 scorecard、Pareto 或收益计算；
- deadline 仅报告，不作为因果交互的替代指标；
- 所有 36 arms 均通过完成性、约束、投影重构和 executable-target admissibility
  完整性门槛。

来源：E12 的 36-arm controlled rerun。该 run 的 manifest 记录 dirty worktree，
因此结论具有确定性本地复现证据，但不是 clean-commit release 证明。
