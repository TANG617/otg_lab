# E20 — PV Future-O1 acceleration-conditioned targets

E20 在 E18 的 `Synchronization.No` replay 上只改变 target conditioning。两个
E11 风格的 method ID 为：

```text
pv_pred_backward_o1_kp1
pv_pred_backward_o1_kp1_accel_projected
```

前者使用 raw target，后者使用 A-projected target；`conditioning_id` 分别为
`none` 和 `acceleration_projection`。实验在完整序列进入 1 ms replay 之前一次性
求解加速度约束投影。控制循环中不调用投影器、governor 或额外规划器。

对实际相邻 source event 间隔 `dt[i]`，投影曲线严格定义为：

```text
v[i+1] = v[i] + a[i] * dt[i]
p[i+1] = p[i] + 0.5 * (v[i] + v[i+1]) * dt[i]
|v[i]| <= 4.1 rad/s
|a[i]| <= 16.2 rad/s²
```

首个目标 P/V 与 E18 完全相同。目标函数使用按 `Vmax·10 ms` 和 `Vmax` 归一化
的 P/V L2 距离，使整条合法曲线尽量接近原 Future-O1 目标。QP 求解后再从首态
按区间加速度确定性重建 P/V，并用无容差的 V/A 超限计数和动力学等式残差进行
后验门禁。

E20 仍向 Ruckig 发送 PV target，target A 固定为 0；Ruckig 的
`V/A/J=4.1/16.2/4000`、1 ms 周期、零初态和 E18 主调用语义均不变。离线输入曲线
的 jerk 不在 E20 中约束，以便单独识别“加速度合法”这一项的效果。

凹陷同时在两个预声明窗口评估：最大 raw-position 正跳变前后
`[-30 ms, +40 ms]`，以及包含该事件的完整 source-position 单调上升区间。输出包括
target 投影逐区间审计、method metrics、method output trace、Ruckig output
约束审计和对比图。图例明确区分 `A-projected target P` 与
`replay output — A-projected target`；输出本身不称为 projected。
`target_recorded_replay_comparison.{png,svg}` 提供与 E18 同类的完整 reset 段
source、raw/A-projected target、recorded output 和两条 replay output 总览。
`dip_position_comparison.{png,svg}` 只显示凹陷窗口内的位置曲线，并直接标注两种
replay output 的最大回撤。

```bash
uv run otg-lab run E20
uv run otg-lab run E20 --no-figures
```
