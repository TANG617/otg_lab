# Cycle Trace and Command Profile Contracts

`trace.csv` 每行对应一个控制周期，记录：

- measurement、posterior、prediction、raw target、executable target 和 command；
- requested-target free trajectory duration 与实际 frozen trajectory duration；
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

## 因果性与时间审计

状态的 `*_time_s` 是该状态所代表的物理时间，`*_available_time_s` 是它最早
可被在线算法使用的时间。两者不能互换。差分实验另外记录：

- `posterior_startup`、`prediction_startup`、`raw_target_startup`，用于从
  V/A 真值误差中排除历史不足周期；
- `prediction_causal`、`prediction_offline_only` 和 `raw_target_causal`，
  显式区分在线方法与离线 truth oracle；
- `raw_target_latest_input_time_s`，记录构造该 target 时差分实际看到的最新
  position measurement；
- `raw_target_position_source` 和 `raw_target_derivative_source`，分别审计
  计划位置与 V/A 的来源；
- `raw_target_age_samples =
  (command_time_s - raw_target_time_s) / nominal_dt_s`。

对于统一在 cycle `k` 提交的 `command[k+1]`，future backward predictor 的
target age 为 0，backward estimator 为 1，延迟中心差分 estimator 为 2。
计划 `P[k+1]` 可以预先已知，但严格因果差分只能读取截至 cycle `k` 的 position
measurement，不能读取 reference V/A。
