# Metric Contract

每个指标由稳定的 `metric_id` 和 `definition_version` 定义。公式、单位、优化
方向、所需数据、适用条件、时间对齐和缺失策略都属于指标定义；实验只负责把
指标分配为 primary、secondary、guardrail 或 diagnostic。

指标文件采用 tidy schema，一行只存一个
`input × method × window × metric_id`。核心指标组包括：

- raw-time position RMSE、MAE、bias、P95、max 和 IAE；
- lag 与 lag-aligned RMSE（只作诊断）；
- v/a/jerk、平滑度、限值裕量和连续 profile 约束；
- estimator/predictor 真值误差、target distortion、fallback 和 solver 状态；
- 分组件及总 runtime 分布与 deadline miss。

Position-only 输入会通过版本固定的离线二阶中心差分生成
`reference_derived.csv`。这些值标记为 `analysis_estimate`，只能用于目标难度
描述，不能作为在线方法真值。

方法比较要求完整配对。缺失任一必需方法时，比较状态为
`unavailable_incomplete_pair`，不会删除失败轨迹后做 complete-case 推断。
配对 bootstrap 只在实验明确声明 seed 和重采样次数时执行。
