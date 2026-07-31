# A02 — 解析轨迹 Truth/Finite Difference 正确性验证

> 证据角色：以下“选择”仅是解析验证场景的代表方法，不是上线选型。上线
> PV/PVA 与差分结论只使用 velocity-limit recorded trajectory。

## 解析验证结论

- **解析验证代表：`pva_pred_backward_o2_kp1`（Future O2）**。它通过
  guardrail，在三条轨迹上 observed lag 的整数采样诊断均为 0 ms，并具有
  最小的 worst-case truth gap ratio。
- **禁止外推场景代表：`pva_est_backward_o2_k`**
  （Backward O2）。本次固定 run 中纳入或排除 `deadline_miss_rate` 不改变该选择。
- Future O1 是 0 ms 的保守候选，但它相对 Future O2 的噪声优势只来自公式
  系数，不是 E03–E05 的有噪声实证。
- Centered O2 的 V 白噪声增益最低，但 target age 与当前 observed lag 均为
  20 ms，不适合作为低延迟 tracking 首选。

这是一项解析正确性场景化 readout，不建立任意加权总分，也不覆盖 recorded
trajectory 的部署证据。

## 证据角色

解析验证的 15 行 scorecard 只使用 E04 内的 5 种 FD × 3 条轨迹。E03 仅复核
E04 的 P baseline/PVA truth 重复结果；E05 只作 PV truth 分量控制，不进入
排名。E01 的独立 `p_kp1_baseline` 与 E03–E05 的 P baseline 已逐指标验证
等价，只作复现审计，不增加样本量。来源均 completed、同一 commit，但
manifest 记录 `git.dirty=true`，因此不是 clean-build 完全复现证据。

## RMSE–lag 摘要

Primary 是 `main_evaluation = 0.04–3.00 s` 的 raw-time
`position_rmse`：

```text
RMSE ratio = RMSE_method / RMSE_P
truth gap ratio = (RMSE_method - RMSE_truth) / (RMSE_P - RMSE_truth)
```

没有计算病态的 `RMSE_method / RMSE_truth`。Observed lag 保留符号，决策
使用绝对值；它是整数采样移位后的输出相位诊断，不是 wall-clock latency。
`lag_aligned_rmse` 只诊断最佳移位后的剩余波形误差，不进入 primary 排名。

| 方法 | worst RMSE/P | worst truth gap | worst \|lag\| ms | target age | 正式 eligible | Pareto |
|---|---|---|---|---|---|---|
| Backward O1 | 0.3369 | 0.3369 | 10.0 | 10.0 ms | true | false |
| Backward O2 | 0.3369 | 0.3369 | 10.0 | 10.0 ms | true | false |
| Centered O2 | 0.6737 | 0.6737 | 20.0 | 20.0 ms | true | false |
| Future O1 | 0.0395 | 0.0395 | 0 | 0 ms | true | false |
| Future O2 | 7.736e-08 | 7.139e-08 | 0 | 0 ms | true | true |

## 解析验证场景

| 场景 | lag 预算 | deadline 纳入 | 选择 | worst truth gap |
|---|---|---|---|---|
| 默认严格实时 | 0 ms | true | pva_pred_backward_o2_kp1 | 7.139e-08 |
| 一拍容忍 | 10 ms | true | pva_pred_backward_o2_kp1 | 7.139e-08 |
| 两拍容忍 | 20 ms | true | pva_pred_backward_o2_kp1 | 7.139e-08 |
| 禁止外推 | 20 ms | true | pva_est_backward_o2_k | 0.3369 |
| 禁止外推（忽略 deadline sensitivity） | 20 ms | false | pva_est_backward_o2_k | 0.3369 |

解析验证门槛要求三轨迹完整、因果、RMSE ratio `< 1`，且 full-overlap 下
velocity/acceleration violation、profile constraint、fallback、solver failure
和 deadline miss 均不劣于 P baseline。缺失任何必需 guardrail 即不合格。

## 五种有限差分方法

五种方法都只读取截至当前可用的位置样本，均无显式低通平滑。Future
predictor 内部的位置外推会被 scheduled `P[k+1]` 覆盖，因此 E04 的实际方法
差异主要来自 V/A。

### Backward O1 — `pva_est_backward_o1_k`

- 公式：`v[k]=(x[k]-x[k-1])/h; a[k]=(x[k]-2x[k-1]+x[k-2])/h²`
- 阶数/历史：O1；3 个位置样本。
- Target age：1 拍
  （10 ms）。
- 启动：前 2 个位置样本不足时 V/A 置零。
- 白噪声增益：V 为 1.414 σ/h，A 为
  2.449 σ/h²。
- 优点：无外推、历史较短、理论噪声增益低于同 lag 的 O2。
- 缺点：V/A 仅 O(h)，截断误差较大。

### Backward O2 — `pva_est_backward_o2_k`

- 公式：`v[k]=(3x[k]-4x[k-1]+x[k-2])/(2h); a[k]=(2x[k]-5x[k-1]+4x[k-2]-x[k-3])/h²`
- 阶数/历史：O2；4 个位置样本。
- Target age：1 拍
  （10 ms）。
- 启动：前 3 个位置样本不足时 V/A 置零。
- 白噪声增益：V 为 2.550 σ/h，A 为
  6.782 σ/h²。
- 优点：同为 10 ms target age，平滑数据上的算法精度更高。
- 缺点：噪声增益和启动历史更高；deadline 结果需随固定 run 审计。

### Centered O2 — `pva_est_centered_o2_km1`

- 公式：`v[k-1]=(x[k]-x[k-2])/(2h); a[k-1]=(x[k]-2x[k-1]+x[k-2])/h²`
- 阶数/历史：O2；3 个位置样本。
- Target age：2 拍
  （20 ms）。
- 启动：需 3 点；结果属于 k-1，到 k 才可用。
- 白噪声增益：V 为 0.707 σ/h，A 为
  2.449 σ/h²。
- 优点：无外推；五种方法中 V 白噪声增益最低。
- 缺点：20 ms target age，raw-time tracking 延迟最大。

### Future O1 — `pva_pred_backward_o1_kp1`

- 公式：`v[k+1]=(2x[k]-3x[k-1]+x[k-2])/h; a[k+1]=(x[k]-2x[k-1]+x[k-2])/h²`
- 阶数/历史：O1；3 个位置样本。
- Target age：0 拍
  （0 ms）。
- 启动：前 2 个位置样本不足时 V/A 置零。
- 白噪声增益：V 为 3.742 σ/h，A 为
  2.449 σ/h²。
- 优点：零 target age、历史较短；相对 future O2 更保守。
- 缺点：仍属一步外推，且会放大位置噪声。

### Future O2 — `pva_pred_backward_o2_kp1`

- 公式：`v[k+1]=(5x[k]-8x[k-1]+3x[k-2])/(2h); a[k+1]=(3x[k]-8x[k-1]+7x[k-2]-2x[k-3])/h²`
- 阶数/历史：O2；4 个位置样本。
- Target age：0 拍
  （0 ms）。
- 启动：前 3 个位置样本不足时 V/A 置零。
- 白噪声增益：V 为 4.950 σ/h，A 为
  11.225 σ/h²。
- 优点：当前平滑、无噪声、等间隔数据上 raw-time RMSE 最低。
- 缺点：对噪声、量化、抖动和突变最敏感，且启动历史较长。


## PV truth 分量控制

下表只回答“理想 V 已知后，理想 A 是否仍增加位置收益”，不进入 FD
scorecard。

| 轨迹 | P baseline RMSE | PV truth RMSE | PVA truth RMSE |
|---|---|---|---|
| quadratic_with_extremum | 0.0669 | 4.246e-16 | 4.493e-16 |
| cubic | 0.0103 | 6.109e-11 | 6.126e-11 |
| sine | 0.0436 | 2.682e-14 | 2.683e-14 |

## 图表与审计文件

- `results/rmse_ratio_by_input.png/.svg`：逐轨迹 RMSE ratio，对数轴，P
  baseline = 1。
- `results/lag_by_input.png/.svg`：observed lag 与 target age 分列编码。
- `results/rmse_lag_pareto.png/.svg`：formal eligibility、Pareto 前沿与
  0/10/20 ms lag budget。
- `results/truth_fd_metric_pairs.csv`：全指标原值、状态和 truth/P 对照。
- `results/method_input_scorecard.csv`：正式 15 行决策坐标。
- `results/guardrail_summary.csv`：硬门槛逐方法逐轨迹审计。

## 限制

- 只有三条确定性、单轴、平滑、无噪声、100 Hz 轨迹，不计算 p-value、
  置信区间或统计推广。
- 0 ms 只有整数采样分辨率，不代表无亚采样相位误差。
- 噪声、量化、时间抖动、突变、多轴、不同采样率和不同 horizon 均未实证。
- A02 不使用 E06，不能外推为 PV finite-difference 方法选型。
- A02 不参与上线选型；上线对比只允许使用 velocity-limit recorded
  trajectory。
- output jerk channel 的 unavailable 状态不是“零违规”。

## 复现

```bash
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py --check
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py
```
