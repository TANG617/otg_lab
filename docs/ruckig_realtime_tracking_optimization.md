# 仅位置输入下的 Ruckig 实时轨迹跟踪

> 来源：[飞书 Wiki 文档](https://psi-robot.feishu.cn/wiki/IgBlwaZf6i1F6jkcj8ucKUIqnSb?from=from_copylink)
>
> 当前实验口径：单关节、100 Hz；CSV 只读取 `value`，忽略 `elapsed time`、timestamp 和 topic，相邻两行固定按 10 ms 处理。
>
> 厂商固定约束：$v_{max}=4.1\,\mathrm{rad/s}$、$a_{max}=8.2\,\mathrm{rad/s^2}$、$j_{max}=4000\,\mathrm{rad/s^3}$。
>
> 名称说明：产品名称是 **Ruckig Tracking Interface**，实际 API 类名拼作 `Trackig`。本文用“tracking-aware follower”指代包括 `Trackig`、reference governor、jerk-QP/MPC 在内的通用能力。

本文回答三个问题：可靠的 velocity/acceleration 对普通 Ruckig 有多少价值；后向差分、离线中心差分和可在线运行的中心差分有什么区别；在固定厂商约束下，当前场景为什么仍需要面向移动参考的约束跟随能力。

## 摘要

仅有位置输入时，任务应拆成三层，而不是把一次差分直接等同于实时轨迹跟踪：

```text
Position samples
      ↓
当前状态估计器（State Estimator）
      ↓
未来参考生成器（Future Reference Generator）
      ↓
约束轨迹跟随器（Constrained Trajectory Follower）
      ↓
下一周期可执行的 p / v / a
```

在固定 10 ms、固定 `4.1/8.2/4000`、无 lookahead、相同初态和相同目标投影下，新受控实验得到以下结论：

1. **可靠 velocity 明确有价值。** 三条无噪声解析曲线上，PV/PVA 解析真值相对 P 将 position RMSE 降低 74.65%～87.74%，并把 40～80 ms 整体滞后统一降到 10 ms。
2. **本组平滑解析曲线没有显示 PVA 比 PV 更优。** acceleration 不是普遍无用；这里只能说明在当前低动态、一步可达场景中，可靠 velocity 已足以让普通 Ruckig 到达当前目标状态。
3. **离线中心差分很准，但不是实时算法。** 它使用 $p_{k+1}$。相对后向差分，解析曲线 velocity RMSE 降低约 58～92 倍、acceleration RMSE 降低约 201～316 倍。
4. **因果中心差分不能继承全部离线收益。** 延迟一拍后传播到当前时刻可显著改善 velocity；由于没有 jerk 模型，acceleration 仍属于上一采样时刻，其解析 RMSE 与后向差分相同。
5. **CSV 上未滤波差分没有胜过 P。** P 为 RMSE `0.03519 rad`、lag `70 ms`；差分方法为 `0.03874～0.07856 rad`、`70～160 ms`。这说明 estimator 质量是导数发挥价值的前置条件。
6. **CSV 的主要冲突是 acceleration target 不可行。** 三种 PVA 差分方法都有 32.64% 的原始目标需要投影；原始二阶差分峰值为 `280.09 rad/s²`，约为厂商 acceleration 限制的 34.2 倍。
7. **当前厂商 jerk 已远离历史低 jerk 瓶颈。** CSV 的 P 基线在 `j=41` 时 RMSE 是厂商点的 2.29 倍；从 `j=4000` 增至 `8000` 仅改善约 0.9%，lag 不变。提高 acceleration 也会降低部分误差，但超出厂商限制的点不能成为部署建议。
8. **需要的是 tracking-aware 能力，不是已经证明某个商业 API 必需。** 下一周期解析 oracle 在普通 `Ruckig.update()` 上达到 0 ms lag 和数值误差量级，证明关键是生成正确未来状态。Ruckig Pro `Trackig` 是优先评估的低集成成本 baseline，但当前 Community `0.17.3` 没有该接口，本项目尚未实测 Pro。

正式数据、图表和复现说明见 [`results/vendor_target_state_ablation`](../results/vendor_target_state_ablation/README.md)。许可、供应链和工程风险见 [Ruckig Tracking 必要性与工程风险评估](ruckig_tracking_necessity.md)。

## 1. 任务定义与架构

### 1.1 输入、输出与时间语义

上游每 10 ms 提供一个新位置 $p_k$。CSV 实验直接定义：

$$
t_k=k\,DT,\qquad DT=0.01\,\mathrm{s}
$$

`elapsed time` 和其他时间列不参与当前实验。该结论只适用于“每行固定 10 ms”这一约定，不应与按原始时间戳重采样的另一种实验混用。

系统三层可写为：

$$
\hat{x}_k=\operatorname{EstimateState}(p_{0:k})
$$

$$
\bar{x}_{k+H}=\operatorname{GenerateFutureReference}(\hat{x}_k,H)
$$

$$
x^{cmd}_{k+1}=\operatorname{FollowConstrained}
(x^{cmd}_k,\bar{x}_{k+H},v_{max},a_{max},j_{max})
$$

其中 $x=[p,v,a]$。三层职责分别是：

| 层 | 输入 | 输出 | 责任 |
| --- | --- | --- | --- |
| State Estimator | 当前和历史位置 | 同一目标时刻的 $\hat p,\hat v,\hat a$ | 因果恢复状态并抑制微分噪声 |
| Future Reference Generator | 当前估计与预测模型，或已知未来序列 | $t+H$ 的未来位置或完整状态 | 决定目标属于哪个未来时刻 |
| Constrained Trajectory Follower | 当前执行状态与未来参考 | 下一周期可执行状态 | 维持状态连续并满足 VAJ 约束 |

严格在线时，未来参考只能由模型预测；完整 CSV 已知时，可以直接 preview 未来样本或离线优化。这两类信息条件必须分开评价。

### 1.2 普通 Ruckig 为什么会追赶旧目标

[Ruckig 原论文](https://www.roboticsproceedings.org/rss17/p015.html)处理受 velocity、acceleration 和 jerk 限制的状态到状态问题：从当前完整状态到达一个固定目标状态。当前普通接口每周期只执行新轨迹的第一个 10 ms，然后用下一目标重新规划。

若在周期 $k$ 把属于当前时刻的 $x_k$ 设为终态，`update()` 返回的是一个控制周期后的输出。因此本实验显式记录：

```text
target[k] → output[k+1]
```

即使 $x_k$ 完全准确，输出也自然比参考晚一个周期。若目标还不能在 10 ms 内到达，重复设置当前移动目标会形成更长的滚动追赶。

官方 Tracking 教程也将这一问题作为 Tracking Interface 的动机：对移动信号直接重复普通 target 会滞后，Tracking Interface 通过目标预测降低滞后。[官方 Tracking 教程](https://docs.ruckig.com/tutorial.html#tracking-interface)

### 1.3 “需要 tracking”应如何理解

当前证据支持的是：系统需要可靠 estimator、未来参考和 tracking-aware constrained follower 的组合。它不支持以下更强命题：

- Pro `Trackig` 已在本项目上优于所有替代方案；
- 只要把差分 $v/a$ 送入普通 Ruckig 就完成了 tracking；
- 放宽厂商 acceleration/jerk 就是可接受的优化；
- 离线中心差分可以直接部署到严格在线链路。

`Trackig`、stateful reference governor 和 jerk-QP/MPC 都能承担第三层。Pro 的优势是与现有 Ruckig 集成路径短；它是不是最终方案需要直接实验。

## 2. 受控实验设计

### 2.1 固定变量

| 项目 | 正式值 |
| --- | ---: |
| 控制周期 | 10 ms |
| 最大 velocity | 4.1 rad/s |
| 最大 acceleration | 8.2 rad/s² |
| 最大 jerk | 4000 rad/s³ |
| lookahead | 0 ms |
| `minimum_duration` | 10 ms |
| CSV 输入列 | 仅 `value` |
| CSV 原始样本 | 1936 行，19.35 s |
| 解析曲线运动段 / 静止收敛段 | 3 s / 2 s |
| 目标时序 | `target[k] → output[k+1]` |
| 求解接口 | 普通 `Ruckig.update()` |

基线固定厂商限制。OFAT 章节只为解释敏感性而临时改变一个约束；这些点不是部署候选。

### 2.2 解析参考

三条参考均用七次 time scaling。令 $T=3\,\mathrm{s}$、$\tau=t/T$：

$$
h(\tau)=35\tau^4-84\tau^5+70\tau^6-20\tau^7,
\qquad s(t)=T h(\tau)
$$

空间函数为二次、三次和正弦：

$$
f_q(s)=0.5(s-1.5)^2,
\quad f_c(s)=0.12(s-1.5)^3,
\quad f_s(s)=0.37\sin(2\pi s/3)
$$

解析 $p/v/a$ 通过链式法则计算，不由数值差分生成。参考峰值全部位于厂商限制内：

| 参考 | max velocity | max acceleration | max sampled jerk |
| --- | ---: | ---: | ---: |
| Quadratic | 1.506 | 4.785 | 13.466 |
| Cubic | 0.592 | 1.471 | 7.533 |
| Sine | 1.695 | 6.365 | 40.073 |

### 2.3 P / PV / PVA 和导数来源

| ID | target | 导数来源 | 信息条件 |
| --- | --- | --- | --- |
| P | $[p_k,0,0]$ | 无 | 在线基线 |
| PV truth | $[p_k,v_k^*,0]$ | 解析真值 | 解析曲线 oracle |
| PVA truth | $[p_k,v_k^*,a_k^*]$ | 解析真值 | 解析曲线 oracle |
| PV/PVA backward | $[p_k,\hat v_k,0/\hat a_k]$ | 历史后向差分 | 在线，但时间戳混合 |
| PV/PVA center offline | $[p_k,\hat v_k,0/\hat a_k]$ | 标准中心差分 | 非因果，使用一个未来样本 |
| PV/PVA center causal | $[p_k,\hat v_k,0/\hat a_k]$ | delay-1 中心估计并传播 | 在线补充 |

P 在每种导数来源的图中重复作为共同基线，但指标表只保存一次。解析数据运行 9 种方法；没有导数真值的 CSV 运行 7 种方法。

### 2.4 三种差分的时间戳

历史后向差分为：

$$
\hat v_k^{BW}=\frac{p_k-p_{k-1}}{DT},\qquad
\hat a_k^{BW}=\frac{p_k-2p_{k-1}+p_{k-2}}{DT^2}
$$

一阶项近似 $t_k-DT/2$，二阶项近似 $t_k-DT$。把 $p_k$、$\hat v_k^{BW}$、$\hat a_k^{BW}$ 直接组成一个 target 会混合时间戳。

标准离线中心差分为：

$$
\hat v_k^{CD}=\frac{p_{k+1}-p_{k-1}}{2DT},\qquad
\hat a_k^{CD}=\frac{p_{k+1}-2p_k+p_{k-1}}{DT^2}
$$

它在内部采样点对齐到 $t_k$，但读取 $p_{k+1}$，所以只能作为离线诊断基线。

可在线运行的中心方案在收到 $p_k$ 后先估计 $t_{k-1}$：

$$
\hat v_{k-1}=\frac{p_k-p_{k-2}}{2DT},\qquad
\hat a_{k-1}=\frac{p_k-2p_{k-1}+p_{k-2}}{DT^2}
$$

再用常 acceleration 传播到 $t_k$：

$$
\hat v_k=\hat v_{k-1}+\hat a_{k-1}DT,
\qquad \hat a_k=\hat a_{k-1}
$$

target position 直接锚定最新的 $p_k$。该方案对 velocity 做了一拍补偿，但 acceleration 仍没有向前传播的 jerk 信息。

### 2.5 投影和指标分层

实验先保存原始 $[p,v,a]$ target，再检查 Ruckig target-state 必要可行条件；不可行时只等比例缩放 velocity/acceleration，position 不变。最终结果同时报告：

- **Estimator 层**：解析曲线的 velocity/acceleration RMSE、bias、最大误差；CSV 不虚构导数真值。
- **Target 层**：原始可行率、投影率、投影造成的 velocity/acceleration RMSE。
- **Tracking 层**：position RMSE、MAE、最大误差、全局 lag、对齐后 RMSE。
- **Reachability 层**：冻结当前 target 后的轨迹时长和 10 ms 内可达率。
- **Constraint 层**：输出 velocity、acceleration、`OutputParameter.new_jerk` 和离散 sampled jerk。

`output_max_new_jerk` 是每次实际执行后 `out.new_jerk` 的样本峰值，不是冻结整段 `trajectory` 内部 jerk 的全局峰值。`output_max_sampled_jerk` 是相邻输出 acceleration 的差分；两者也不能与 target acceleration 的差分混用。

## 3. 实验结果

### 3.1 差分本身是否准确

下表为解析真值上的导数 RMSE：

| 数据 | 方法 | velocity RMSE | acceleration RMSE |
| --- | --- | ---: | ---: |
| Quadratic | Backward | 1.2136e-2 | 7.9125e-2 |
|  | Center offline | 1.3188e-4 | 2.5001e-4 |
|  | Center causal | 2.6375e-4 | 7.9125e-2 |
| Cubic | Backward | 4.5758e-3 | 4.1940e-2 |
|  | Center offline | 6.9905e-5 | 1.7604e-4 |
|  | Center causal | 1.3980e-4 | 4.1940e-2 |
| Sine | Backward | 1.4593e-2 | 1.5033e-1 |
|  | Center offline | 2.5058e-4 | 7.4708e-4 |
|  | Center causal | 5.0113e-4 | 1.5033e-1 |

![解析参考上的差分误差](../results/vendor_target_state_ablation/derivative_sources.png)

[完整导数指标](../results/vendor_target_state_ablation/derivative_source_metrics.csv)

离线中心差分明显优于后向差分，但其优势包含未来样本。因果中心方案的 velocity 仍接近真值；acceleration 与后向差分具有相同的一拍时间偏差，因此 RMSE 相同。这是“中点差分”在线化时最容易被忽略的区别。

### 3.2 从 P 到 PV/PVA：可靠导数的价值

| 数据 | P：RMSE / lag | PV truth：RMSE / lag | PVA truth：RMSE / lag |
| --- | ---: | ---: | ---: |
| Quadratic | 0.075667 / 80 ms | 0.009275 / 10 ms | 0.009275 / 10 ms |
| Cubic | 0.013624 / 40 ms | 0.003453 / 10 ms | 0.003453 / 10 ms |
| Sine | 0.049705 / 70 ms | 0.006750 / 10 ms | 0.006750 / 10 ms |

![目标状态消融总览](../results/vendor_target_state_ablation/ablation_summary.png)

PV/PVA 相对 P 的改善说明，移动参考的终端 velocity 信息能显著减少普通 Ruckig 的停停走走语义。PVA 与 PV 在 position 指标上相同，只是当前实验的负结果：这三条参考平滑且均一步可达，不能外推为 acceleration target 在急停、强换向或更紧约束下也无价值。

即使使用当前时刻解析真值，PV/PVA 仍有精确的一周期延迟。下一周期 oracle 把 $x_{k+1}$ 作为周期 $k$ 的 target：

| 数据 | RMSE | 最大误差 | lag | 10 ms 可达率 |
| --- | ---: | ---: | ---: | ---: |
| Quadratic | 4.48e-16 | 7.55e-15 | 0 ms | 100% |
| Cubic | 6.12e-11 | 1.06e-9 | 0 ms | 100% |
| Sine | 2.68e-14 | 4.62e-13 | 0 ms | 100% |

[Oracle 指标](../results/vendor_target_state_ablation/oracle_sanity_metrics.csv)

该控制实验验证了图和指标的时间索引，也隔离出 future reference 的作用。它是完美预测上界，不代表现实 estimator 已经能够得到 $x_{k+1}$。

### 3.3 解析曲线中的差分来源

以 Sine 为例：

| 方法 | RMSE | lag | 对齐后 RMSE |
| --- | ---: | ---: | ---: |
| P | 0.049705 | 70 ms | 0.015612 |
| PV / PVA truth | 0.006750 | 10 ms | 约 0 |
| PV backward | 0.007833 | 10 ms | 0.001317 |
| PVA backward | 0.008252 | 10 ms | 0.001787 |
| PV / PVA center offline | 0.006750 | 10 ms | 约 0 |
| PV / PVA center causal | 0.006750 | 10 ms | 约 0 |

![Sine 上的 P/PV/PVA 和导数来源](../results/vendor_target_state_ablation/target_state_ablation_sine.png)

平滑解析输入下，即使导数估计存在小误差，约束跟随输出仍可能与真值方案非常接近。因此只看 position 曲线会掩盖 estimator 差异；需要同时查看 3.1 节的导数误差。Backward 在 Quadratic/Sine 上略差，Cubic 的运动更温和，所有导数方案在当前精度下相同。

### 3.4 CSV：差分噪声、PVA 收益与目标不可行

CSV 没有 velocity/acceleration ground truth，只能比较最终 tracking 和 target feasibility：

| 方法 | RMSE | 最大误差 | lag | 10 ms 可达率 | 投影率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| P | **0.035187** | **0.184528** | **70 ms** | 8.74% | 0% |
| PV backward | 0.063978 | 0.327129 | 110 ms | 19.56% | 0% |
| PVA backward | 0.038741 | 0.240492 | **70 ms** | 20.49% | 32.64% |
| PV center offline | 0.067225 | 0.332870 | 130 ms | 23.07% | 0% |
| PVA center offline | 0.044701 | 0.260779 | 80 ms | **27.11%** | 32.64% |
| PV center causal | 0.078561 | 0.466188 | 160 ms | 17.64% | 0% |
| PVA center causal | 0.044109 | 0.243132 | 80 ms | 19.45% | 32.64% |

![CSV 上的 P/PV/PVA 和导数来源](../results/vendor_target_state_ablation/target_state_ablation_csv.png)

[完整 tracking 指标](../results/vendor_target_state_ablation/target_state_ablation_metrics.csv)

有三个需要同时保留的观察：

1. P 的 RMSE 最低。未经滤波的差分会把真实位置流的局部变化放大成不稳定 target，可靠导数在解析数据上的收益不能直接迁移到 CSV。
2. 在相同导数源内，加入 acceleration 后，PVA 相对 PV 的 RMSE 改善 33.51%～43.85%。因此不能简单说 acceleration 无用；它在普通 Ruckig 的终态语义中确实改变了结果。
3. PVA 改善的一部分发生在 target 投影之后。三种 PVA 的原始 acceleration 峰值均为 `280.09 rad/s²`，原始 target sampled jerk 为 `44460.85 rad/s³`；32.64% 的目标被等比例缩放。投影造成的 acceleration RMSE 为 `30.37 rad/s²`。

这些数值不是机器人真实 acceleration/jerk，也不是 Ruckig 输出超限；它们是从 position-only CSV 构造的原始 target 诊断量。所有最终输出的 velocity、acceleration 和 `new_jerk` 都满足各自实验约束。

因此当前 CSV 结果的正确结论是：**直接有限差分不是足够可靠的实时 estimator**。下一轮应比较带宽明确的因果 estimator，并把 estimator error、future-reference error 和 follower error 分开记录。

### 3.5 Acceleration 和 jerk 的影响

OFAT 固定 $v_{max}=4.1$：

- acceleration：`4.1, 6.0, 8.2, 12.0, 16.4`，jerk 固定 4000；
- jerk：`41, 200, 800, 1600, 3200, 4000, 8000`，acceleration 固定 8.2。

表中为 position RMSE：

| 场景 / 方法 | a=4.1 | a=8.2 | a=16.4 | j=41 | j=4000 | j=8000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sine PV truth | 0.252167 | 0.006750 | 0.006750 | 0.227594 | 0.006750 | 0.006750 |
| Sine PVA truth | 0.187335 | 0.006750 | 0.006750 | 0.006750 | 0.006750 | 0.006750 |
| CSV P | 0.066178 | 0.035187 | 0.019318 | 0.080557 | 0.035187 | 0.034868 |
| CSV PVA center causal | 0.084552 | 0.044109 | 0.022871 | 0.259658 | 0.044109 | 0.043879 |

![Acceleration/Jerk 对 RMSE 的单因素影响](../results/vendor_target_state_ablation/constraint_sensitivity_rmse.png)

![Acceleration/Jerk 对 lag 的单因素影响](../results/vendor_target_state_ablation/constraint_sensitivity_best_lag_ms.png)

[完整 OFAT 指标](../results/vendor_target_state_ablation/limit_sensitivity_metrics.csv)

结果应这样解释：

- Sine 的真实 acceleration 峰值为 `6.365 rad/s²`，所以 `a=4.1` 本身低于参考需求；此时的恶化不能归因于 estimator。
- CSV 在更高 acceleration 下普遍改善，说明厂商 `a=8.2` 是当前跟踪误差的重要活动约束。但 12/16.4 超出厂商限制，不能作为“更合适的部署值”。
- `j=41` 对多数滚动终态方法过紧，显著增加 RMSE 和 lag。Sine PVA truth 在 `j=41` 仍保持不变，是因为参考 sampled jerk 峰值只有 `40.073`，并且 PVA 保留了正确 acceleration；PV 把终端 acceleration 强制为 0，反而需要更强 jerk 反复调整。
- `j=4000→8000` 对 CSV 仅改善约 0.5%～4.1%，lag 基本不变。当前场景没有证据支持修改厂商 jerk。
- 个别方法对约束并非单调，因为每周期都在替换带不同 $p/v/a$ 的终态，Ruckig 会切换轨迹剖面。OFAT 不能导出跨数据集的“最佳 acceleration/jerk”。

## 4. 对实时方案的含义

### 4.1 Estimator 和 lookahead 是两类责任

用户提出的两部分理解是正确的，但还应显式保留第三层 follower：

1. **Estimator**：从仅有的 $p_{0:k}$ 估计属于同一时刻的 $\hat p_k,\hat v_k,\hat a_k$；
2. **Future Reference Generator / lookahead**：预测 $t_k+H$ 的目标参考；
3. **Constrained Follower**：从当前执行状态生成满足 VAJ 的下一周期命令。

Estimator 解决“现在状态是什么”，lookahead 解决“应该追哪个未来时刻”，follower 解决“如何在约束内执行”。把三者混在一个函数里，会无法判断失败来自微分噪声、预测偏差、目标不可行还是 follower 行为。

### 4.2 推荐的数据流

```text
p[k]
  └─ StateEstimator.update(p[k])
       └─ x_hat[k] = [p_hat, v_hat, a_hat]
            └─ FutureReference.predict(x_hat[k], H)
                 └─ x_ref[k+H]
                      └─ ConstrainedFollower.update(x_ref[k+H])
                           └─ x_cmd[k+1]
```

严格在线中心差分可以作为最小 baseline，但 CSV 结果说明它不应成为唯一 estimator。后续 estimator 候选可以包括带低通的局部多项式、Alpha-Beta-Gamma、常 acceleration/jerk Kalman Filter 或 tracking differentiator；比较时必须固定 follower、lookahead 和运动约束。

如果 `Trackig` 内部使用 prediction model，上游就不应再次预测同一段未来。可以让 estimator 只输出当前同步状态，由 `Trackig` 负责 prediction；也可以关闭内部预测，让统一 Future Reference Generator 负责。必须固定唯一预测责任方。

### 4.3 为什么仍应评估 Ruckig Tracking

当前实验形成了完整逻辑链：

1. P 在平滑解析输入上产生 40～80 ms lag；
2. 可靠 velocity 把 lag 降到接口固有的 10 ms；
3. next-cycle oracle 又把 10 ms 降到 0；
4. CSV 的直接差分却无法可靠提供这个未来状态；
5. 厂商 acceleration 限制又使大量原始 PVA target 不可行。

所以系统需要同时处理“状态估计”“未来目标”和“受限跟随”。Ruckig Pro `Trackig` 的接口正对这一任务，适合作为首个独立 baseline；但 governor 或 MPC 也可以实现同一能力。必要的是功能，不是未经实验的产品结论。

## 5. 下一阶段验证

固定同一 CSV、初态、estimator、future-reference 责任方和 `4.1/8.2/4000`，只改变 follower：

1. 普通 Ruckig + P；
2. 普通 Ruckig + 同步 estimator 的当前 PV/PVA；
3. 普通 Ruckig + 统一的一周期或短时域 future reference；
4. Pro `Trackig`，`reactiveness=0`；
5. `TrackigMode::Fast` 与 `Optimized` 的少量预注册参数点；
6. Stateful reference governor；
7. 若前述方案不足，再评估短时域 jerk-QP/MPC；
8. 完整 CSV 已知时，单独做 offline preview 上界。

统一记录：

- position RMSE、MAE、最大误差、lag、对齐后 RMSE；
- 平滑段、换向段、急停段的局部误差；
- estimator error 和 future-reference prediction error；
- raw target 可行率、投影率和投影失真；
- 输出 VAJ、错误返回和 fallback；
- 单周期计算时间 P50/P99/max，并在目标硬件评估 WCET。

10 ms 是完整控制周期的截止时间，不能全部分配给规划器。生产候选还需验证多关节同步、力矩、负载、碰撞和下游控制器内部 limiter。

## 6. 结论边界

- 当前只有单关节运动学，没有机器人动力学和实机闭环。
- CSV 固定按每行 10 ms；当前结果有意忽略 `elapsed time`。
- CSV 没有 velocity/acceleration 真值，不能用差分间的一致性代替 ground truth。
- 离线中心差分使用未来样本，只是诊断基线。
- 因果中心差分采用常 acceleration 传播，没有 jerk estimator。
- 全局 lag 以 10 ms 网格搜索，是粗粒度描述，不代替频域或局部延迟分析。
- OFAT 没有估计 acceleration/jerk 交互，也不用于修改厂商限制。
- target projection 是本实验的一种显式策略；其他 reference governor 可能产生不同折中。
- `out.new_jerk` 和 sampled jerk 都不是冻结轨迹内部 jerk 的完整证明。
- 当前调用普通 Ruckig Community `0.17.3`，没有直接 Pro `Trackig` 数据。

## 7. 复现

```bash
.venv/bin/python run_target_state_ablation.py \
  --mode all \
  --output-dir results/vendor_target_state_ablation

.venv/bin/python -m unittest discover -s tests -v
```

关键产物：

- [运行 manifest](../results/vendor_target_state_ablation/run.json)
- [正式 tracking 指标](../results/vendor_target_state_ablation/target_state_ablation_metrics.csv)
- [导数误差指标](../results/vendor_target_state_ablation/derivative_source_metrics.csv)
- [Oracle 指标](../results/vendor_target_state_ablation/oracle_sanity_metrics.csv)
- [OFAT 指标](../results/vendor_target_state_ablation/limit_sensitivity_metrics.csv)
- [结果目录说明](../results/vendor_target_state_ablation/README.md)

Manifest 记录固定数据口径、扫描网格、输入和核心代码 SHA-256、Git 状态以及 Python/NumPy/Matplotlib/Ruckig 版本。

## 附录：历史实验定位

早期结果保留为研发轨迹，不再与正式受控实验并列排名：

| 历史结果 | 主要问题 | 当前定位 |
| --- | --- | --- |
| `full/`、`selected-2/` | 生成代码和精确参数不完整 | 图片归档 |
| `middle-selected-2/` | 两次 `gradient`、未来样本、导数缩放/裁剪和不同限制同时变化 | 说明历史中心差分图的来源 |
| 历史 `j=41` | jerk 远低于当前厂商 4000 | 低 jerk 敏感性背景 |
| `4.1/16/3200` | 同时放宽 acceleration、收紧 jerk | 开发过程配置 |
| 旧 estimator/lookahead 扫描 | 同时改变 estimator、lookahead 和 `minimum_duration` | 候选生成与探索，不是严格 P/PV/PVA 消融 |

新目录 `vendor_target_state_ablation/` 不是旧图片的逐字节复现，而是在统一限制、统一时间语义和统一指标下重建关键问题。

## 参考资料

- Berscheid, L. & Kröger, T., [Jerk-limited Real-time Trajectory Generation with Arbitrary Target States](https://www.roboticsproceedings.org/rss17/p015.html), RSS 2021.
- Ruckig, [Tracking Interface 教程](https://docs.ruckig.com/tutorial.html#tracking-interface).
- Ruckig, [`Trackig` API](https://docs.ruckig.com/classruckig_1_1Trackig.html).
- Ruckig, [在线 Tracking 示例](https://docs.ruckig.com/example_14.html)与[离线 Tracking 示例](https://docs.ruckig.com/example_15.html).
- Gerelli, O. & Guarino Lo Bianco, C., [A Discrete-Time Filter for the On-Line Generation of Trajectories with Bounded Velocity, Acceleration, and Jerk](https://fileadmin.cs.lth.se/ai/Proceedings/ICRA2010/MainConference/data/papers/0271.pdf), ICRA 2010.
- Lange, F. & Albu-Schäffer, A., [Path-Accurate Online Trajectory Generation for Jerk-Limited Industrial Robots](https://elib.dlr.de/101288/), IEEE RA-L 2016.
- Stellato, B. et al., [OSQP: An Operator Splitting Solver for Quadratic Programs](https://web.stanford.edu/~boyd/papers/osqp.html), Mathematical Programming Computation, 2020.
