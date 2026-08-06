# E18 — Sync.No recorded/replay PV Future-O1 一致性

E18 现在只回答一个主问题：同一段 `PV Future-O1` 输入、零 P/V/A 初态和
`V/A/J=4.1/16.2/4000` 下，记录的 `Synchronization.No` 输出与 Ruckig replay 是否表现
一致。同步模式排名与 P-only/PV 方法比较不再属于 E18 的通过条件。

```bash
uv run otg-lab run E18
uv run otg-lab run E18 --no-figures
```

## 当前右轴回放

默认读取未经修改的 `data/raw/none.csv`，严格使用三个 topic：

- `/mc/ik/joint_states.position[$right_joint_id]`：约 10 ms raw position；
- `...interface_values[$right_joint_id].values[0]`：约 1 ms 真机输出位置；
- `...interface_values[$right_joint_id].values[4]`：raw target echo，仅用于时序审计。

CSV 是累计 logger 快照。loader 按 source gap 大于 1 s 分段并选择最后一段。本地
始终从该段 reset 的 `P/V/A=0/0/0` 开始完整运行；前 3 s 只从评分中排除，不会
裁剪输入、跳过调用或重新初始化。首条真机输出为
`6.666666666481822e-7 rad`，与零初态、`J=4000 rad/s³`、1 ms 的
`J·dt³/6` 相差约 `1.85e-17 rad`，因此初态假设有直接启动段证据。

Future-O1 固定使用名义 `h=10 ms`：前两点采用 raw P、V=0，成熟后构造预测
P/V，target A 恒为 0，并在 source event 之间保持完整 PV。Ruckig 固定使用
1 ms、单轴 `Synchronization.No`，不设置 governor、projection 或
`minimum_duration`。本地连续执行日志缺失的 tick；真实输出不插值，只在唯一
最近 1 ms tick 上比较。

### 调用语义诊断

当前 position-only logger 没有记录真实 `call_seq/callback_source`，所以 E18
保留三个预先声明的本地假设：

统一方法 ID 为 `pv_pred_backward_o1_kp1`。三种 execution ID 为：

1. `update_target_callback_and_control_loop`：持久化 `Ruckig.update()`；target callback 和
   1 ms control loop 是独立调用。它是部署主假设。
2. `update_control_loop_only`：只在 1 ms loop 调用持久化 `update()`。
3. `calculate_control_loop_only`：每个 1 ms tick 重新 `calculate()`，用于与旧离线
   语义对照。

三个变体都报告零偏移 RMSE、MAE、bias、P95、最大误差和逐点误差。不会因为某个
诊断变体的 RMSE 最小，就把它数据驱动地改成部署真值。±20 ms lag 仅用于诊断，
不替代零偏移主指标。`1e-12 rad` 只判断数值恒等，不是工程接受阈值。

当前 CSV 会生成：

- `raw_target_events.csv`、`execution_call_trace.csv`、`execution_output_trace.csv`；
- `recorded_replay_comparison.csv`、`execution_metrics.csv`、`replay_lag_scan.csv`；
- `target_echo_audit.csv`、`data_quality.json`；
- `target_recorded_replay_comparison` 三联主图：source position、raw target P、
  recorded output、replay output，以及相对 source 和 replay-recorded 的误差；
- 全程 overlay/error、启动调用语义、最大误差局部和三变体误差图。

这些产物被明确标记为 `formal_parity_eligible=false`。它们可以定位差异，但不能
单独证明求解器完全一致。

## 正式 No-only 一致性门禁

正式门禁读取 `data/full_axis_capture` 中控制器内部的四个文件：

```text
capture_manifest.json
calls.csv
axis_states.csv
raw_position_events.csv
```

E18 只要求一个完整 `mode=No` run；同一 capture 中可以包含其他模式，但它们既不
是 E18 的前置条件，也不参与 E18 的 identity decision。依次执行：

```text
data sufficiency
  → target-builder parity
  → solver-step parity
  → closed-loop parity
```

- target builder 逐调用、逐轴比较部署实际 target P/V/A；
- solver step 注入真机该调用的 current/target/约束，隔离单步求解；
- closed loop 只从 `run_reset` 初态开始，之后完全传播 replay output；
- `analysis_valid` 只控制评分，warm-up/垃圾调用仍完整执行；
- P/V/A/duration 逐点阈值为 `1e-12 / 1e-10 / 1e-8 / 1e-12`；result、调用来源、
  calculation/section 离散状态必须完全相同。

只有这三个 No gate 全部通过，E18 才报告 `formal_parity_passed`。当前没有该 capture
时，实验运行仍成功完成并报告 `formal_parity_not_evaluable`，同时保存缺失项和
首个可诊断差异。不会生成 synchronization 排名或 P-only/PV 结论。

完整采集 schema 见
[`data/full_axis_capture/README.md`](data/full_axis_capture/README.md)。

## 保留的历史与扩展入口

旧 `0801.csv` 的单轴 `calculate()` 回放保留为
`experiment.run_legacy_0801_replay`，不再是 `otg-lab run E18` 的默认含义。四同步
模式门禁、同步反事实和 P/PV 消融仍保留在 `validation_pipeline.py`，作为后续扩展
研究入口，不参与重建后 E18 的 No-mode 一致性结论。

```bash
uv run python \
  experiments/E18_pv_future_o1_recorded_replay_consistency/validation_pipeline.py \
  --no-figures
```
