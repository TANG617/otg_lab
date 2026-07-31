# E01 — Scheduled P-only baseline

最新本地审计 run：
`runs/20260731T080632.652054Z__8949ebe60515`。

该 run 完成三条解析轨迹、两条 recorded trajectory 和两个 P-only limits
case。解析轨迹只用于方法正确性验证。

| baseline 角色 | recorded input | VAJ | position RMSE rad | integer lag | sub-sample lag |
|---|---|---:|---:|---:|---:|
| **当前实际上线 baseline** | `recorded_tasks_original_no_velocity_limit` | **4.2/8.2/41** | **0.0772111911** | **180 ms** | **180.768 ms** |
| 实验 paired baseline | `recorded_tasks_simplified_with_velocity_limit` | 4.1/8.2/4000 | 0.0029509965 | 20 ms | 21.029 ms |

Current-online baseline 的 lag-aligned RMSE 在
160/170/180/190/200 ms 分别为
0.0323856/0.0315724/0.0312682/0.0314917/0.0322346 rad，因此 180 ms 是
±200 ms 搜索区间内部的局部最小值。

该 current-online 数值是对实际上线配置的离线 replay，不是生产 telemetry。
它执行完整，且 velocity/acceleration/profile constraint、fallback、solver
failure 和 deadline miss 均为 0。

Velocity-limit recorded 的 command trace 与 E11 同输入、同方法的
`p_kp1_baseline` 逐字节一致；E11/E12 同次运行 baseline 仍是候选收益计算的
正式配对坐标，E01 不增加样本量。Current-online baseline 与候选使用不同
waveform 和 VAJ，因此其数值只描述 status quo，不能直接计算因果收益。

`integer lag` 是可直接观测的 10 ms 网格最优移位；`sub-sample lag` 是该
最优点及其相邻两点的 MSE 二次插值，只用于分辨率敏感性，不等同于新增采样
信息或 wall-clock latency。
