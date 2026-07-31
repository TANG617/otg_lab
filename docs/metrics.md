# Metric Contract

每个指标由稳定的 `metric_id` 和 `definition_version` 定义。公式、单位、优化
方向、所需数据、适用条件、时间对齐和缺失策略都属于指标定义；实验只负责把
指标分配为 primary、secondary、guardrail 或 diagnostic。

指标文件采用 tidy schema，一行只存一个
`input × method × window × metric_id`。核心指标组包括：

- raw-time position RMSE、MAE、bias、P95、max 和 IAE；
- signed observed lag 与 lag-aligned RMSE。`lag_s` 保留为整数采样移位
  诊断；`lag_subsample_s` 对最优整数点及其相邻两点的 MSE 作局部二次插值，
  用于检查 10 ms 网格的分辨率敏感性。面向选型的跨实验分析可以派生
  lower-is-better 的 `|lag|`，并与 raw-time position RMSE 作为
  co-primary。两个 lag-aligned RMSE 都只作诊断，不替代 raw-time RMSE；
- v/a/jerk、平滑度、限值裕量和连续 profile 约束；
- stop-go exact-profile 指标：周期内 peak/ripple、最小绝对速度相对 reference
  的 P05，以及速度穿过或接近零的周期比例。最小速度由 piecewise-constant jerk
  profile 的端点和内部速度极值解析计算，不用稀疏采样点替代；
- estimator/predictor 真值误差、target distortion、fallback 和 solver 状态；
- 分组件及总 runtime 分布与 deadline miss。

Position-only 输入会通过版本固定的离线二阶中心差分生成
`reference_derived.csv`。这些值标记为 `analysis_estimate`，只能用于目标难度
描述，不能作为在线方法真值。

方法比较要求完整配对。缺失任一必需方法时，比较状态为
`unavailable_incomplete_pair`，不会删除失败轨迹后做 complete-case 推断。
配对 bootstrap 只在实验明确声明 seed 和重采样次数时执行。

E17 的不规则 measurement 仍落在固定 control tick 上。每个 trace row 明确保存
measurement state time、availability time、是否沿用旧样本以及是否发生 source
dropout；任何 estimator 看到的 measurement 都必须满足
`available_time <= control_time`。因此 source timestamp jitter 与控制周期 jitter
是两个不同概念，当前实验只改变前者。
