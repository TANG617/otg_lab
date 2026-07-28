# Cycle Trace and Command Profile Contracts

`trace.csv` 每行对应一个控制周期，记录：

- measurement、posterior、prediction、raw target、executable target 和 command；
- 每个状态所代表的物理时间及可用时间；
- 周期开始时的 command state，用于独立重构连续 profile；
- estimator、predictor、target builder、governor、follower 的稳定 ID；
- solver、fallback、组件 reset、失败层和各组件/总 runtime。

不适用的字段写为空值，不以零代替。方法失败时保留已完成周期并在
`status.json` 写入失败层、原因、有效周期数和方法 fingerprint。

`command_profiles.csv` 每行是一段可执行 constant-jerk profile：

```text
profile_id,cycle_index,segment_index,start_time_s,end_time_s,jerk_rad_s3,exact
```

连续 V/A/J 审计只依赖 exact profile。若 Ruckig binding 无法暴露完整分段，
相关连续 jerk 指标必须标为 unavailable，不能用采样加速度差冒充内部 jerk。
`load_tracking_run_artifacts()` 可在新进程中从 command、trace、profile 和
status 文件重建 `TrackingRun`，再交给 `analyze_tracking()` 重算指标。
