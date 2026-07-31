# A06 — PV/PVA fine VAJ sensitivity

## 当前轨迹的选择

A04 选出的 primary 方法是 `PV + Future O1`。在 E14 的
`8 V × 8 A × 10 J = 640` 个 PV 设置中，RMSE 与 `|observed lag|` 作为
co-primary，不使用跨单位加权总分。最低 eligible RMSE 且处于最小 lag 档的
限值效率代表点是：

**V/A/J = 1 / 8.2 / 3200**，
RMSE = **0.0021286588 rad**，
lag = **10 ms**，
是 vendor `4.1/8.2/4000` 的
**0.905109×**。

最佳 PV 点落在 A 网格边界，因此只能称为“best tested setting”，不能声称连续参数空间的全局最优。

## 上线推荐与限值效率点

在 `A/J=8.2/3200` 下，测试过的 `V=1, 1.25, 1.5, 2, 3, 4.1`
具有完全相同的 RMSE **0.0021286588 rad**
和 lag **10 ms**。

上线采用 **V/A/J = 4.1/8.2/3200**：它保持 vendor Vmax，与限值效率点的
RMSE/lag 完全相同，且 projection count 为 0。`1/8.2/3200` 仅作为较低限值
的等性能代表点；它发生 6/7672 次 velocity projection。

| 角色 | V | A | J | RMSE rad | lag ms | projection |
|---|---|---|---|---|---|---|
| 上线推荐 | 4.1 | 8.2 | 3200 | 0.0021286588 | 10 | 0 |
| 限值效率等价点 | 1 | 8.2 | 3200 | 0.0021286588 | 10 | 6 |

### Lag 分辨率补充

完整 1,280-case compact aggregate 只保留 10 ms 网格的 integer lag，不能在
缺少 trace 的情况下重建整个 sub-sample Pareto。为验证最终配置，E14 对
`1/8.2/3200`、`4.1/8.2/3200` 与 vendor `4.1/8.2/4000` 做了同代码、
同输入的三点补充 replay：

| 角色 | VAJ | RMSE rad | integer lag ms | sub-sample lag ms | projection |
|---|---|---|---|---|---|
| limit_efficient_equivalent | 1/8.2/3200 | 0.0021286588 | 10 | 9.740 | 6 |
| deployment_recommended | 4.1/8.2/3200 | 0.0021286588 | 10 | 9.740 | 0 |
| vendor_reference | 4.1/8.2/4000 | 0.0023518269 | 10 | 9.554 | 0 |

两个 J=3200 点的 sub-sample lag 同为 9.740 ms，因此“保持 Vmax=4.1
不牺牲 RMSE/lag”的判断在亚采样口径下不变。相对 vendor J=4000，J=3200
的 RMSE 下降 9.49%，integer lag 不变，但 sub-sample lag 增加 0.186 ms；
因此亚采样口径下二者构成轻微 trade-off，而不是严格支配。该补充不重新排名
其余 1,277 cases。

## PV/PVA tested minima

| 分量 | V | A | J | RMSE | lag ms | projection | vs vendor | eligible | 边界 |
|---|---|---|---|---|---|---|---|---|---|
| PV | 1 | 8.2 | 3200 | 0.0021286588 | 10 | 6 | 0.905109 | 639/640 | A |
| PVA | 1 | 7.5 | 3200 | 0.0033910493 | 10 | 69 | 0.958941 | 560/640 | interior |

## 解释边界

- co-primary metrics：当前 velocity-limited recorded trajectory 上
  `t>=0.04 s` 的 raw-time position RMSE 与 `|observed lag|`；
- eligible：完整执行、constraint/fallback/solver 为零、projection 可重构、
  executable target admissible；deadline 仅报告；
- “near-optimal”定义为 RMSE 不超过 tested minimum 的 1%；
- RMSE–lag Pareto 后分别检查 10/20 ms 时延档位；两个档位选择一致；
- near-optimal limit frontier 同时把更低 RMSE、`|lag|` 和更低 V/A/J
  视作更优；
- 完整 surface 的 observed lag 是 10 ms 整数采样移位诊断；最终三点另有
  局部二次插值的 sub-sample 敏感性。两者都不是 wall-clock latency；
- 该选择只适用于当前轨迹和 A04 的 Future-O1 stencil；尚未通过其他 recorded
  trajectory 的 holdout，不能升级为通用 VAJ 默认值。
