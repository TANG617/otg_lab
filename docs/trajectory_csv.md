# Trajectory CSV Contract

轨迹 schema 版本为 `otg.trajectory.v1`，固定表头如下：

```text
sample_index,time_s,position_rad,velocity_rad_s,acceleration_rad_s2,jerk_rad_s3
```

`sample_index` 必须是连续整数，`time_s` 必须有限、严格递增且等间隔。
`position_rad` 必须完整且有限。速度、加速度和 jerk 通道各自只能整列有效或
整列空白；空白表示 unavailable，不表示零。

Reference 从 `sample_index=0,time_s=0` 开始。跟踪器在周期 `k` 消费目标并在
`time_s[k+1]` 产生 command，所以 command 文件通常从 `sample_index=1`
开始，共 `N-1` 行。

每个轨迹文件配套 `<stem>.meta.json`，记录 schema、轨迹 ID、kind、dt、通道
语义、来源或生成器参数，以及 CSV SHA-256。解析真值可填充 p/v/a/j；
position-only 记录轨迹的 v/a/j 保持空白。

非固定网格原始数据必须先显式转换。转换策略和源列映射写入 metadata；跟踪
核心不会猜测时间列或静默重采样。
