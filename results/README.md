# Experiment Results

## 当前正式结果

- [`vendor_target_state_ablation/`](vendor_target_state_ablation/)：固定 10 ms、固定厂商 `4.1/8.2/4000` 的受控目标状态实验。包含 P/PV/PVA、解析真值/后向差分/离线中心差分/因果中心差分、next-cycle oracle、acceleration/jerk OFAT、完整指标和运行 manifest。

## 支持性研究

- `tracking_necessity/`：此前的 estimator、手工 lookahead、oracle 与工程风险研究。其中不同约束或同时改变多个变量的结果只作为背景材料。

## 历史归档

- `full/`、`selected-2/`、`middle-selected-2/`：早期图片归档。
- `middle-selected-2-rerun/`：历史中心差分图的可复现重跑。
- `estimator/`、`lookahead_sweep/`、`central_limit_sweep/`：开发过程探索。

历史目录之间可能使用不同 motion limits、导数缩放、lookahead 或 `minimum_duration`，不能与当前正式结果直接排名。引用任何结果时，应同时检查同目录 `run.json` 和指标行中的 limit 字段。
