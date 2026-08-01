# OTG Lab

`otg_lab` 是一个面向单轴实时轨迹跟踪实验的 CSV-first 工具箱。所有目标轨迹
与输出命令都使用同一份固定网格 CSV 契约；估计、预测、目标构造、约束处理和
Ruckig 跟随器可以独立组合。

核心数据流：

```text
trajectory.csv
  -> reference analysis
  -> estimator / predictor / target / governor / follower
  -> command.csv + trace.csv + command_profiles.csv
  -> tracking analysis + comparisons
```

## 快速开始

安装开发环境：

```bash
uv sync --extra dev
```

运行与 E03–E06 同定义的 scheduled P-only 基线：

```bash
uv run otg-lab run E01
```

通过 RunBuoy 一次性串行运行全部 E 系列实验：

```bash
uv run python scripts/run_all_experiments.py
```

创建下一轮实验：

```bash
uv run otg-lab new-experiment E02 estimator_ablation
```

运行测试和静态检查：

```bash
uv run pytest
uv run ruff check .
```

## 发布实验产物

每个实验预建一个 `results/` 目录。完成实验后，手动把值得保留的完整 run
复制进去。`results/<run-id>/` 会被 Git 忽略；只有实验根目录的
`results.md` 和 `results/index.csv` 进入版本控制：

```bash
cp -R \
  experiments/E08_pva_finite_difference_recorded_tracking/runs/<run-id> \
  experiments/E08_pva_finite_difference_recorded_tracking/results/<run-id>
```

`publish-run` 直接打包复制后的目录，不要求当前 Git 工作区或原始 run 的
manifest 是 clean，也不重新校验 manifest 中的逐文件输出哈希：

```bash
uv run otg-lab publish-run \
  experiments/E08_pva_finite_difference_recorded_tracking/results/<run-id>
```

Release 只生成并上传所选目录的 `results.zip` 及其 `SHA256SUMS`，并更新该
实验的 `results/index.csv`。复制进所选目录的 trace、command、profiles 等
内容也会原样进入 ZIP。仓库顶层不创建 `results/`。每个实验根目录另有只含
标题的 `results.md`，供后续手工记录结论。实验 Release 不会被标为软件仓库的
`Latest`。两个资产由
RunBuoy 以结构化 `0/2` 至 `2/2 files`
进度显示；完整命令、路径、日志和凭据不会发送到手机。若上传失败，Release
保持草稿状态，`unpublished` 索引记录仍会保留。

先在本地保留并检查打包结果、但不访问 GitHub：

```bash
uv run otg-lab publish-run \
  experiments/E08_pva_finite_difference_recorded_tracking/results/<run-id> \
  --package-only --output-dir /tmp/otg-release
```

可用 `--draft` 创建草稿 Release，或用 `--repo OWNER/REPO` 显式指定远端。

跨实验分析采用与 E 系列对称的本地生命周期。`analyze.py` 先写入
`analyses/<analysis-directory>/runs/<analysis-run-id>/`；人工复核后将完整
目录复制到 `results/<analysis-run-id>/`。run/result 生成文件被 Git 忽略，
根目录的 `RESULTS.md` 和 `results/index.csv` 进入版本控制：

```bash
cp -R \
  analyses/A01_E03-E06_pv_pva_comparison/runs/<analysis-run-id> \
  analyses/A01_E03-E06_pv_pva_comparison/results/<analysis-run-id>

uv run otg-lab publish-analysis \
  analyses/A01_E03-E06_pv_pva_comparison/results/<analysis-run-id>
```

每个 analysis result 自包含 `RESULTS.md`、`work/`、manifest、表和图。
Analysis Release 不包含父目录的轻量 `index.csv` 或 `.gitkeep`。

当前 recorded/stop-and-go/VAJ 决策链可分别重建：

```bash
uv run python analyses/A03_recorded_pva_velocity_limit_attribution/analyze.py
uv run python analyses/A04_recorded_pv_pva_fd_selection/analyze.py
uv run python analyses/A05_stop_go_p_pv_pva_improvement/analyze.py
uv run python analyses/A06_pv_pva_vaj_fine_selection/analyze.py
```

核心机理的确认性证据由 E15–E17 重建。它们复用相同 tracking engine 与版本化
指标，但对大规模相图只保存预先声明的聚合行和 sentinel 审计信息：

```bash
uv run otg-lab run E15 --no-figures
uv run otg-lab run E16 --no-figures
uv run otg-lab run E17 --no-figures
```

E15 检查无量纲 stop-go 边界，E16 做 velocity component、lookahead 与
minimum-duration 的因果消融，E17 用开发/留出种子、测量噪声、量化、源时间戳
抖动、延迟、丢包和合成非恒速轨迹验证因果 PV。真机多轴不包含在这些单轴离线
结论中，必须作为后续独立验证层补充。

## Clean release evidence refresh

在最终实验、分析和论文生成代码已经提交，且 git status --porcelain 为空的
隔离 worktree 中，可一次性重跑 E11--E17 与 A03--A06：

    uv run python scripts/run_clean_release_refresh.py

该入口会先执行完整 pytest 与 Ruff 检查；E14 固定使用 64 shards / 8 workers
的有界内存路径，并额外重跑 A06 所需的三个 lag-resolution 坐标。A03--A06
使用位于已忽略 runs/ 目录中的临时配置绑定本轮新产物，
allow_dirty_git=false，不会修改已追踪的 canonical analysis.yaml 或
RESULTS.md。运行期间每个 manifest 都必须保持同一 HEAD 且
git.dirty=false。完成后命令会输出位于 Git metadata 目录中的 state.json，
供生成 paper/evidence/release.yaml 和执行 provisional/release 数值差异审查；
它不会绕过该发布门。

A03–A06 均固定 exact run/aggregate 路径，不解析 `latest`；`--check` 只验证来源、
完整性和预注册决策，不写分析产物。

批量发布所有尚未发布的实验与分析结果：

```bash
uv run otg-lab publish-results
```

命令同时扫描 `experiments/E*/results/<run-id>/manifest.json` 和
`analyses/A*/results/<analysis-run-id>/analysis_manifest.json`，跳过各自
`results/index.csv` 中已经标记为 `published` 或 `draft` 的结果，并为每个
剩余结果创建一个独立 GitHub Release。单个结果失败不会阻断后续结果，但批次
最终会返回非零。可附加 `--repo OWNER/REPO` 或 `--draft`。

整个批次只创建一个 RunBuoy Run，以真实的
`processed results / total results` 展示结构化进度；手机端不接收路径、命令
参数或日志。命令结束后会输出 Run ID，以及本机可用的 `runbuoy status`、
`runbuoy logs` 和 `runbuoy attach` 命令。

## Python API

公开数据流由以下函数组成：

```python
load_trajectory_csv(...)
write_trajectory_csv(...)
generate_analytic_trajectory(...)
analyze_reference(...)
run_tracking(...)
analyze_tracking(...)
compare_methods(...)
run_experiment(...)
```

每个函数只接收单轴公开类型或版本化 spec。落盘方法产物可通过
`load_tracking_run_artifacts()` 在新进程中重建，再交给
`analyze_tracking()` 独立重算指标。

## 设计边界

- 只支持单轴轨迹。
- 核心只接受严格等间隔、严格递增的时间网格。
- 控制 tick 始终固定；E17 可在每个 tick 注入带独立 state/availability time 的
  不规则、延迟或保持测量，用于验证 estimator 的因果性。
- 规范输出是 follower 实际提交的 command，不再设置第二层执行仿真。
- 解析轨迹也必须先写为规范 CSV，再通过公共 loader 进入实验。
- position-only 输入的离线导数只用于输入分析，不会写回 truth 或泄漏给在线
  estimator/predictor。
- 运行产物写入对应实验的
  `experiments/<experiment>/runs/<timestamp>__<spec_hash>/` 并默认忽略；
  输入 CSV 和实验代码进入版本控制。

详细契约见：

- [可复用实验架构：从零创建、分析与发布](docs/experiment_architecture.md)
- [轨迹 CSV](docs/trajectory_csv.md)
- [周期 trace 与 profile](docs/trace_csv.md)
- [指标](docs/metrics.md)
- [E 系列实验](docs/experiments.md)
