# OTG Lab

用于研究仅位置输入下的状态估计、未来参考生成和 Ruckig 实时约束跟踪。

## v3 冻结证据与复审状态

正式 v3 已冻结且未重跑。复审确认 direct one-step governor 的安全性证据和
artifact integrity 仍然有效，但发现 v3 中名为 ordinary Ruckig 的几个基线约
96% 控制周期实际执行了 one-step fallback。因此，冻结表中的 77.38% 只能作为
充分披露该混杂因素后的 exploratory regression，既不是 ordinary Ruckig
predicted-P 对 governed PVA 的确认性证据，也不是 clean same-follower P/PVA
消融。

本轮不执行 v4，也不保留确认性的 77.38% 主结论。若未来需要确认性
target-component 结论，必须用全新 v4 protocol、全新 test identity/seed 和
预锁定的 same-follower P/PV/PVA matrix 做一次 fresh confirmation。

- 人类可读披露：[V3_POSTREVIEW_ADDENDUM.md](V3_POSTREVIEW_ADDENDUM.md)
- 机器可读状态：[protocol_status_v3_postreview.json](protocol_status_v3_postreview.json)
- 原始冻结状态（未修改）：[protocol_status_v3.json](protocol_status_v3.json)
- 逐项修复回应：[REVIEW_RESPONSE.md](REVIEW_RESPONSE.md)
- canonical profile-aware schema：[DATA_DICTIONARY.md](DATA_DICTIONARY.md)

当前代码将 ordinary unshielded Ruckig、显式 viability-shielded Ruckig 和
direct constant-jerk 方法分开，并以实际 piecewise-constant-jerk prefix
审计 Ruckig 命令。代码与回归测试的修复发生在 v3 冻结之后，不会改写冻结的
v3 数值或把 development run 重新命名为 v3 confirmation。基础设施修复可
独立进入 reviewer assessment/merge；这不恢复 v3 主结论，也不自动合并 PR。

## 历史 Phase A 与目标状态实验

正式口径固定为：CSV 只读取 `value`，忽略 `elapsed time`、timestamp 和 topic；每行按 10 ms、100 Hz 处理；厂商约束为 `vmax=4.1 rad/s`、`amax=8.2 rad/s²`、`jmax=4000 rad/s³`。

### 三组 CSV PVAJ 与跟踪对比（development-only）

`data/simplified-tasks_no-velocity-limit.csv` 和
`data/simplified-tasks_velocity-limit.csv` 是两条简化轨迹。下面的命令在
相同固定 10 ms 网格、相同厂商约束和相同 ordinary Ruckig 方法下，将二者与
`plot_data.csv` 一起比较原始采样 PVAJ、跟踪轨迹、误差、可达性和计算时间；
主比较是“限速版 vs 不限速版”，当前 CSV 作为共同基线：

```bash
uv run --frozen python scripts/compare_csv_pvaj_tracking.py
uv run --frozen python scripts/build_csv_pvaj_tracking_report.py
uv run --frozen python scripts/validate_csv_pvaj_tracking_comparison.py
```

输出写入 `results/csv_pvaj_tracking_comparison/`，包括可复核指标、PNG/SVG
图、canonical report artifact、自包含 HTML 技术报告和独立数值复算。
该分析是描述性的开发诊断：限速版与不限速版的时长、幅值和轨迹形状不完全
配对，因此可以论证“较低 VAJ 与更好的跟踪相关”，但不能仅由这些观测轨迹
建立“速度限制单独导致跟踪改善”的因果结论。

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
- `data/simplified-tasks_no-velocity-limit.csv`：不限速简化轨迹
- `data/simplified-tasks_velocity-limit.csv`：限速简化轨迹
- `scripts/compare_csv_pvaj_tracking.py`：PVAJ、跟踪和窗口需求诊断
- `tests/`：差分、时间索引、投影、约束和 CSV 时间口径回归测试
- `results/`：正式结果与历史归档
- `runs/`：临时运行结果

`run_experiments.py`、`run_lookahead_sweep.py`、`run_middle_selected.py` 和 `run_central_limit_sweep.py` 保留为早期 estimator / lookahead / 历史图形探索，不再作为当前 P/PV/PVA 主证据链。
