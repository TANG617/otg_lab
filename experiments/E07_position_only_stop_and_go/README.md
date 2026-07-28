# E07 — P-only Stop-and-Go Threshold and Severity

E07 只研究一种在线方法：

```text
PositionOnly → ZeroOrderHold → P → NoGovernor → ordinary Ruckig
```

实验不包含 PV/PVA，也不做 target-builder 对照。目标是回答：

1. 哪些参考速度和 A/J 限值组合会触发 stop-and-go；
2. 发生时每秒出现多少 pulse，周期内速度纹波有多大。

## 临界速度与 ρ

速度上限在本实验的低速范围内不活跃。单周期 rest-to-rest 的平均临界速度为：

```text
Jerk limited:         vcrit = J dt² / 32
Acceleration limited: vcrit = A dt / 4 - A² / (2J)
```

vendor 点 `dt=0.01 s, A=8.2 rad/s², J=4000 rad/s³` 得到
`vcrit=0.012095 rad/s`。每个 case 的实际单周期可达性比定义为：

```text
ρ = reference velocity / vcrit(A, J, dt)
```

输入文件名中的 `vendor_ratio` 只表示
`reference velocity / vendor vcrit`；实际 ρ 会随 limit scale 改变。

## 输入与实验矩阵

`inputs/` 中包含 20 条三秒恒速解析轨迹：

```text
p(t) = vref · t
v(t) = vref
a(t) = 0
j(t) = 0
```

vendor velocity ratio 为：

```text
0.125, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 0.9, 0.95,
1.0, 1.05, 1.1, 1.2, 1.5, 1.8, 2.0, 2.2, 3.0, 4.0
```

A/J limit scale 为 `0.25, 0.5, 1.0, 2.0`，总计 80 个 P-only run。

需要重新生成输入时：

```bash
uv run python experiments/E07_position_only_stop_and_go/generate_inputs.py
```

运行实验：

```bash
uv run otg-lab run E07
```

## 指标和产物

Primary incidence 指标：

- `rest_to_rest_pulse_fraction`
- `stop_go_event_rate_hz`

Exact-profile severity 指标：

- `profile_velocity_ripple_median`
- `profile_velocity_ripple_to_reference_median`
- `profile_velocity_ripple_to_reference_p95`
- `profile_peak_velocity_to_reference_median`

速度纹波是每周期 exact constant-jerk profile 的
`max velocity - min velocity`。内部速度极值由 `a+jτ=0` 解析求解；正式指标不使用
0.1 ms 显示采样。

除标准 manifest、command、trace、profiles 和 tidy metrics 外，E07 生成：

```text
analysis/
  stop_go_surface.csv
  figures/
    stop_go_phase_map.png/.svg
    stop_go_threshold_collapse.png/.svg
    stop_go_subcycle_velocity.png/.svg
```

phase map 左侧展示 pulse 是否发生，右侧展示连续的 normalized velocity ripple。
collapse plot 使用实际 ρ 汇总 occurrence 和 severity：ρ 对 occurrence 的预测
接近完全坍缩，但低于阈值后的 ripple 仍可能依赖具体 A/J 与绝对速度配置。亚周期
图只展示 P-only 在 vendor limits、`ρ=0.5, 1.0, 1.2` 时的 exact 连续速度。
每个 `<input_id>_position.png` 也使用 exact profile 在 0.1 ms 显示网格重建，
仅展示 `0.5–0.6 s`：上图是相对窗口起点的局部位移，下图是去除静态 lag 后的
centered position error，用于直接观察速度纹波积分形成的位置非线性。

## 验收解释

- `ρ≤0.95`：pulse fraction 至少 95%。
- `ρ≥1.05`：pulse fraction 至多 5%。
- `0.95<ρ<1.05`：边界诊断，不设 pulse 硬判据。
- 所有周期的 P target velocity/acceleration 必须为零。
- 所有 run 必须完成，exact profiles 完整，无 fallback、solver failure 或连续
  V/A/J violation。

严格恒速和确定性 Ruckig 会产生真实硬边界；E07 不平滑 pulse fraction。速度纹波
严重度用于展示边界两侧“导致多少”。
