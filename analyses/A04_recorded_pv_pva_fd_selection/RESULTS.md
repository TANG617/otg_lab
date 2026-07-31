# A04 — Recorded tracking 的 PV/PVA 与差分方法选型

## 选型结论

在 velocity-limited recorded input、`V/A/J=4.1/8.2/4000`、`t>=0.04 s`
的同一窗口下，RMSE 与 `|observed lag|` 作为 co-primary。Scheduled P 的
RMSE/lag 是 **0.0029509965 rad / 20 ms**；
亚采样 lag 敏感性为 **21.029 ms**。
10 ms 与 20 ms 两档均选择
**PV + Future O1**
（`pv_pred_backward_o1_kp1`），其 RMSE/lag 是
**0.0023518269 rad /
10 ms**，亚采样 lag 为
**9.554 ms**。

该方法将 RMSE 降低 **20.30%**，
同时把 lag 从 20 ms 降到
10 ms；亚采样敏感性从
21.029 ms 降到
9.554 ms，并在 RMSE–lag 平面支配
其他候选。PVA + Future O1 的整数 lag 虽同为
10 ms，但亚采样 lag 为
13.976 ms，RMSE
为 0.0035362433 rad，未形成可接受交换。
额外施加本机
deadline gate 后的敏感性选型发生变化或无严格候选；
deadline 不参与联合选型或任意加权总分。PVA 的五种方法全部不如各自同条件
P baseline。PVA 在 4/5 个 stencil 上优于 matched PV，
但这些改善都不足以击败 P；唯一击败 P 的 PV Future O1 加入 A 后反而退化。
10/20 ms 场景选择一致。

## 10-arm scorecard

| 分量 | 差分 | RMSE rad | RMSE/P | integer lag ms | sub-sample lag ms | Δ\|sub-sample lag\| vs P | Pareto | core guardrail | deadline | 选择 |
|---|---|---|---|---|---|---|---|---|---|---|
| PV | Backward O1 | 0.0037271275 | 1.263006 | 20 | 19.502 | -1.528 | false | true | true |  |
| PV | Backward O2 | 0.0046041568 | 1.560204 | 30 | 25.963 | +4.934 | false | true | true |  |
| PV | Centered O2 | 0.0057204239 | 1.938472 | 40 | 35.571 | +14.541 | false | true | true |  |
| PV | Future O1 | 0.0023518269 | 0.796960 | 10 | 9.554 | -11.475 | true | true | false | 10/20 ms |
| PV | Future O2 | 0.0038723474 | 1.312217 | 20 | 17.028 | -4.001 | false | true | true |  |
| PVA | Backward O1 | 0.0033400056 | 1.131823 | 20 | 18.556 | -2.474 | false | true | true |  |
| PVA | Backward O2 | 0.004113132 | 1.393811 | 20 | 23.987 | +2.957 | false | true | true |  |
| PVA | Centered O2 | 0.0043423894 | 1.471499 | 30 | 28.752 | +7.722 | false | true | false |  |
| PVA | Future O1 | 0.0035362433 | 1.198322 | 10 | 13.976 | -7.053 | false | true | true |  |
| PVA | Future O2 | 0.003063527 | 1.038133 | 10 | 14.260 | -6.770 | false | true | true |  |

## Matched PV/PVA

| 差分 | PV ratio | PVA ratio | PVA−PV | PV integer lag | PVA integer lag | PV sub-sample lag | PVA sub-sample lag |
|---|---|---|---|---|---|---|---|
| Backward O1 | 1.263006 | 1.131823 | -0.131183 | 20 | 20 | 19.502 | 18.556 |
| Backward O2 | 1.560204 | 1.393811 | -0.166393 | 30 | 20 | 25.963 | 23.987 |
| Centered O2 | 1.938472 | 1.471499 | -0.466973 | 40 | 30 | 35.571 | 28.752 |
| Future O1 | 0.796960 | 1.198322 | +0.401361 | 10 | 10 | 9.554 | 13.976 |
| Future O2 | 1.312217 | 1.038133 | -0.274084 | 20 | 10 | 17.028 | 14.260 |

## 决策规则

- hard gate：同一 `main_evaluation` 窗口的 RMSE 和 signed lag 均 available，
  且所有可定义的 constraint/fallback/solver guardrail 为零；
- primary gate 不含本机调度抖动；strict sensitivity 额外要求
  `deadline_miss_rate=0`；
- primary gate 后先计算 `(RMSE/P, |sub-sample lag|)` Pareto 前沿，再分别施加
  `|lag|<=10 ms` 和 `|lag|<=20 ms` 场景预算；不对不同单位加权求和；
- 同时保留 10 ms 网格的 integer lag；sub-sample lag 是整数最优点相邻
  MSE 的局部二次插值，两者都不是 wall-clock latency；
- E11 与 E12 内部重复的 scheduled P baseline 已逐 metric 校验等价；
- 输入只有一条 recorded waveform，因此该选择是当前轨迹的部署候选，不外推为
  普遍最优差分公式。
