# OTG Lab

用于研究仅位置输入下的状态估计、未来参考生成和 Ruckig 实时约束跟踪。

## 当前正式实验

正式口径固定为：CSV 只读取 `value`，忽略 `elapsed time`、timestamp 和 topic；每行按 10 ms、100 Hz 处理；厂商约束为 `vmax=4.1 rad/s`、`amax=8.2 rad/s²`、`jmax=4000 rad/s³`。

运行 P / PV / PVA、解析真值 / 后向差分 / 离线中心差分 / 因果中心差分，以及 acceleration / jerk 单因素敏感性实验：

```bash
.venv/bin/python run_target_state_ablation.py \
  --mode all \
  --output-dir results/vendor_target_state_ablation
```

正式结果说明见 [results/vendor_target_state_ablation/README.md](results/vendor_target_state_ablation/README.md)，技术结论见 [docs/ruckig_realtime_tracking_optimization.md](docs/ruckig_realtime_tracking_optimization.md)。敏感性扫描只解释约束影响，不用于修改厂商限值。

运行回归测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 输出约定

不指定 `--output-dir` 时，入口脚本会在 `runs/` 下创建带时间和参数的目录，例如：

```text
runs/20260713-180000__vendor-target-state-ablation__dt-10ms__vmax-4.1__amax-8.2__jmax-4000__mode-all/
```

每次运行都有 `run.json`。正式实验的 manifest 还记录 CSV 行数、输入与核心代码 SHA-256、Git 状态以及 Python / NumPy / Matplotlib / Ruckig 版本。`runs/` 是可丢弃目录；值得保留的结果放到 `results/`。

## 核心文件

- `run_target_state_ablation.py`：正式目标状态消融、oracle 和 OFAT 入口
- `target_state_experiment.py`：解析参考、固定 10 ms CSV loader、差分方法和实验矩阵
- `target_state_plotting.py`：正式静态图表
- `otg_runner.py`：目标状态投影、普通 Ruckig 循环和指标
- `plot_data.csv`：CSV position 输入；正式实验只使用 `value`
- `tests/`：差分、时间索引、投影、约束和 CSV 时间口径回归测试
- `results/`：正式结果与历史归档
- `runs/`：临时运行结果

`run_experiments.py`、`run_lookahead_sweep.py`、`run_middle_selected.py` 和 `run_central_limit_sweep.py` 保留为早期 estimator / lookahead / 历史图形探索，不再作为当前 P/PV/PVA 主证据链。
