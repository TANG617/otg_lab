# E10 — Projected PVA acceleration–jerk limit sensitivity

E10 把 E04 的五个严格因果 PVA 有限差分方法分别放到 E02 的
`acceleration × jerk` 全因子矩阵上，评估每种方法在逐 case
configured-limit projection 下的描述性限值敏感性。

## 输入与方法

实验使用两条固定 10 ms、position-only 的记录轨迹：

```text
data/trajectories/recorded_tasks_original_no_velocity_limit.csv
data/trajectories/recorded_tasks_simplified_with_velocity_limit.csv
```

两条输入都没有 V/A/J truth。PVA 表示送给 follower 的 target 包含
position、velocity 和 acceleration，并不表示在线方法能读取记录导数。

五种方法分别是：

| Method | Represented target | Age at command |
|---|---:|---:|
| `pva_est_backward_o1_k` | `PVA[k]` | 1 sample |
| `pva_est_backward_o2_k` | `PVA[k]` | 1 sample |
| `pva_est_centered_o2_km1` | `PVA[k−1]` | 2 samples |
| `pva_pred_backward_o1_kp1` | `PVA[k+1]` | 0 samples |
| `pva_pred_backward_o2_kp1` | `PVA[k+1]` | 0 samples |

不包含 P-only baseline 或 noncausal PVA truth。每种方法只与自己的 vendor
限值 case 比较。

## 限值矩阵与投影

速度上限固定为 `4.1 rad/s`。每种方法执行以下 35 个 A×J 组合：

| 因子 | 档位 |
|---|---|
| acceleration `[rad/s²]` | `4.1, 6, 8.2, 12, 16.4` |
| jerk `[rad/s³]` | `41, 200, 800, 1600, 3200, 4000, 8000` |

vendor 格为 `A=8.2 rad/s², J=4000 rad/s³`。完整矩阵为：

```text
5 methods × 35 limit cases × 2 inputs = 350 runs
```

记录 position 的有限差分会产生大量超出这些 A/J 档位的 raw PVA target。
E10 因此沿用 E08 的 configured-limit projection，并按每个 case 的实际限值：

1. 保持 target position 不变；
2. 将 acceleration 和 velocity 限制到当前 case 的最大值；
3. 根据当前 jerk 限值收紧 velocity，使 target 满足 Ruckig 的方向性停止包络。

raw target 与 projected target 均保留。投影率和 distortion 是解释 sensitivity
的必要诊断，不是失败。E10 测量的是完整的
`finite difference → per-case projection → ordinary Ruckig` 链，不能解释成
未经处理 PVA 的纯 follower sensitivity。

## 指标与 sensitivity

所有方法使用：

```text
dt = prediction horizon = minimum duration = 0.01 s
measurement = position only
governor = configured-limit projection for the current case
follower = ordinary unshielded Ruckig
failure policy = record and continue
```

主窗口从 `t=0.04 s` 开始，排除所有差分 stencil 的启动期；
`full_overlap` 保留完整审计。

Primary metric 为未经 lag 对齐的 raw-time `position_rmse`。对每种方法分别计算：

```text
RMSE ratio(A,J) = RMSEmethod(A,J) / RMSEmethod(8.2,4000)
Δlag_ms(A,J) = 1000 × [lagmethod(A,J) − lagmethod(8.2,4000)]
```

RMSE 热图颜色使用 `log₂(ratio)`，格内标注 ratio。lag 只是诊断，不替代
raw-time RMSE。secondary position metrics 为 MAE、bias、P95、最大绝对误差
和 IAE；输出/profile 约束、fallback、solver 和 deadline 单独报告。
确定性完整性门限使用 sampled output V/A 与 exact-profile V/A/J；离散
command 没有独立 output jerk 样本，因此对应 metric 保留为 unavailable，
不重复替代 exact-profile jerk 审计。deadline 受运行主机影响，仅报告而不进入
确定性完整性门限。

任何不完整 case 的 RMSE 和 lag 均标为 `N/A`，不使用 prefix 结果。
高于 vendor 的 A/J 档位只是描述性探针，不构成部署建议。

## 运行与产物

从项目根目录运行：

```bash
uv run otg-lab run E10
```

除标准 manifest、trace、command、profile、metric 和 comparison 产物外，
E10 写出：

```text
analysis/
  pva_limit_sensitivity.csv
  method_sensitivity_summary.csv
  raw_target_scan.csv
  raw_target_feasibility.csv
  acceptance_summary.md
  by_method/<method_id>/<input_id>/
    constraint_sensitivity_rmse.csv
    constraint_sensitivity_lag_ms.csv
    constraint_sensitivity_projection_rate.csv
  figures/
    recorded_tasks_original_no_velocity_limit_position.png
    recorded_tasks_simplified_with_velocity_limit_position.png
    by_method/<method_id>/<input_id>/
      constraint_sensitivity_rmse.png/.svg
      constraint_sensitivity_lag_ms.png/.svg
      constraint_sensitivity_projection_rate.png/.svg
```

`pva_limit_sensitivity.csv` 是 350-run 总表；每个
`<method_id>/<input_id>` 目录中的三套 CSV/图只包含该输入上该方法的 35 个
case，并以同一输入上该方法自己的 vendor 格为基准。两条输入之间不交叉
归一化。`method_sensitivity_summary.csv` 因此包含 10 行
`input × method` 描述性汇总。
