# E-series Experiments

每轮探究位于独立目录：

```text
experiments/E01_topic/
  experiment.py
  README.md
```

`experiment.py` 通过 `build_experiment(project_root) -> ExperimentSpec`
构造声明，并完整填写问题、目标/假设、自变量、控制变量、输入 CSV、方法矩阵、
允许变化的字段、评估窗口、指标角色和比较关系。
自定义 estimator/predictor 等组件可以在实验模块中定义并通过
`ComponentSpec.factory` 注入，无需修改核心 registry。

约束或参数矩阵可以通过 `ExperimentCase` 为同一个基础方法声明多个独立
`RunConfig`。显式 case 的 `case_id` 会成为 artifact 目录名和 tidy 指标中的
`method_id`，manifest 另外保存基础方法 ID、因子值与完整运行配置。比较校验会
同时审计组件和 `run_config` 差异，实验必须在
`allowed_method_differences` 中明确允许可变化的配置路径。

二维全组合指标面可以使用 `FactorHeatmapSpec` 声明 row/column 因子、完整档位、
评估窗口、metric 和 baseline case。runner 会先写可独立审计的同名 CSV，再按
baseline ratio 或相对 baseline 的缩放差值生成 PNG/SVG；不声明
cases/heatmaps 的实验保持原有单配置行为。

运行：

```bash
otg-lab run E01_topic
```

运行产物位于
`experiments/<experiment-directory>/runs/<timestamp>__<spec_hash>/`。
使用 `runs` 是因为这里保存 manifest、输入副本、每种方法的
command/trace/profile/status，以及 tidy 指标、比较、失败表、报告和图等
完整运行记录。`runs/` 不进入版本控制，可作为临时工作区清理。单个方法失败
不阻断其他方法；必需方法失败会使命令最终返回非零。

确认值得长期保留的 run 使用以下命令提升到同一实验目录：

```bash
otg-lab publish-run \
  experiments/<experiment-directory>/runs/<timestamp>__<spec_hash>
```

提升结果位于
`experiments/<experiment-directory>/results/<timestamp>__<spec_hash>/`，
只包含 manifest、完整 `analysis/` 树、结果说明和逐文件 SHA-256；轻量索引
位于该实验的 `results/index.csv`。发布 GitHub Release 时只上传这份提升结果
的 ZIP 和外层 SHA-256，不上传完整 run。仓库顶层不创建 `results/`。

CI 或临时试跑可以使用 `--runs-root <path>` 覆盖该实验的 run 容器目录。

新实验可用以下命令生成：

```bash
otg-lab new-experiment E02 estimator_ablation
```

## E03–E06 P/PV/PVA 对比

这四组实验共享 `dt=10 ms`、V/A/J 限值 `4.1 / 8.2 / 4000`、无 governor、
ordinary Ruckig follower，以及 `quadratic_with_extremum`、`cubic`、`sine`
三条解析真值轨迹：

| 实验 | 方法矩阵 |
|---|---|
| E03 | `P[k+1]` baseline、`PVA_truth[k+1]` |
| E04 | baseline、PVA truth 上界、5 个严格因果差分方法 |
| E05 | `P[k+1]` baseline、`PV_truth[k+1]` |
| E06 | baseline、PV truth 上界、5 个严格因果差分方法 |

5 个差分方法是 backward O1/O2 estimator、延迟一拍的 centered O2 estimator，
以及 future backward O1/O2 predictor。estimator target 的 represented time
分别相对 command 落后 1/2 拍；predictor 直接表示 `k+1`，但只使用截至 `k`
的 position measurement。truth predictor 明确标记为 noncausal、offline-only。

主验收窗口为 `0.04–3.00 s`，使用未作 lag 对齐的 raw-time
`position_rmse`。`analysis/acceptance.csv` 给出逐轨迹结果，
`analysis/figures/rmse_ratio_vs_p.{png,svg}` 给出相对统一 P baseline 的 RMSE
ratio；ratio 小于 1 表示改善。

E04/E06 还会生成 `analysis/lag_comparison.csv` 和
`analysis/figures/lag_vs_p_and_truth.{png,svg}`。图中同时包含统一 P-only
baseline、分别与 E03/E05 等价的 PVA/PV truth，以及五种差分方法；CSV 另外给出
每种方法相对 P-only 和 truth 的 lag 差值。lag 仍只作诊断，不参与 primary
RMSE 对齐。

## E08 记录任务波形上的 PVA 迁移

E08 把 E04 的统一 `P[k+1]` baseline 和五个严格因果 PVA 差分方法原样迁移到
`recorded_tasks_simplified_with_velocity_limit`。输入保留记录 position 的行序，
并使用现有固定 10 ms canonical 时间轴；不保留 raw timestamp 抖动，也不提供
V/A/J truth。

实验继续使用 E04 的 V/A/J 限值 `4.1 / 8.2 / 4000` 和 ordinary unshielded
Ruckig，不做输入平滑。执行前增加显式 configured-limit projection：position
保持不变，V/A 超限时投影到当前配置允许域；raw target 仍原样写入审计产物。
六个方法均为 runner 层面的 required 方法。

运行：

```bash
uv run otg-lab run E08
```

主窗口为 `t≥0.04 s` 到记录结束。候选只有完整运行后才能计算相对 P-only 的
raw-time position RMSE；若投影后仍有方法不完整，其标准比较标为
`unavailable_incomplete_pair`，禁止用 prefix RMSE 排名。

除标准产物外，E08 写出逐周期 `raw_target_scan.csv`、方法级
`raw_target_feasibility.csv`、`acceptance.csv` 和
`acceptance_summary.md`。raw-target 扫描只重放已注册的
estimator/predictor/target-builder，不调用 governor 或 follower，用于审计
整条记录波形上投影前的 target V/A 与 Ruckig admissibility。跟踪图和 raw
target 图均以小叉号标出所有发生投影的周期，并用大叉号强调首次投影位置。

## E09 P-only baseline 与五种 PVA 差分的 stop-and-go 对比

E09 把 E07 原始 `PositionOnly → ZOH → P(V=A=0) → ordinary Ruckig`
作为内部 baseline，并将 E04 的五个严格因果 PVA 差分方法放入同一个恒速
stop-and-go 矩阵。实验直接复用 E07 的 20 条三秒恒速解析输入和
`0.25, 0.5, 1, 2` 四档 A/J scale，共运行：

```text
(1 P-only baseline + 5 finite differences) × 4 A/J scales × 20 inputs = 480 runs
```

六种方法均使用 position-only measurement、无 governor 和 ordinary
unshielded Ruckig；baseline 使用 P-only 零导数 target，五种候选使用
scheduled PVA target。主窗口为 `0.5–2.5 s`，此时所有差分 stencil 已成熟。

`ρ_E07` 只用于与 E07 的 P-only critical velocity 对齐，不能解释为 PVA 方法
的硬阈值。E09 直接检查 exact rest-to-rest pulse、event rate、周期内速度纹波，
并审计 baseline 的 `V=A=0` 及五种方法的 target age `1/1/2/0/0`、
因果性和成熟期 `V=vref, A=0`。baseline 需复现 E07 在 `ρ_E07=1`
附近的 stop-and-go 阈值，五种 PVA 方法则需消除 stop-and-go。

运行：

```bash
uv run otg-lab run E09
```

`analysis/stop_go_surface.csv` 保存完整 480-run 结果，
`stop_go_method_comparison.csv` 和 `acceptance_summary.md` 给出方法级汇总。
baseline 与每种差分方法分别生成一套 E07 风格图片，根目录另外生成六方法
stop-and-go、exact velocity 和逐输入 position 对比图。

## E10 五种 projected PVA 方法的 A/J sensitivity

E10 使用 `recorded_tasks_original_no_velocity_limit` 与
`recorded_tasks_simplified_with_velocity_limit` 两条 10 ms position-only
输入，固定 `Vmax=4.1 rad/s`，并使用 5 档 acceleration × 7 档 jerk 的完整
矩阵。实验不加入 P-only 或 truth arm，而是将 E04 的五个严格因果 PVA
差分方法分别执行 35 个 case，共 `5 × 35 × 2 = 350` runs。

记录 position 的 raw PVA 有限差分 target 在该矩阵上经常不可执行，因此每个
case 都通过 E08 共用的 `configured_limit_projection`，按该 case 的 A/J
限制 V/A 并满足方向性 jerk stopping envelope。position 不变，raw target、
projected target、投影率和 distortion 全部保留。E10 的结果必须解释成
limit-conditioned/projected PVA sensitivity，而不是未经处理 target 的纯
Ruckig follower sensitivity。

运行：

```bash
uv run otg-lab run E10
```

主窗口为 `t≥0.04 s`。每种方法独立以自己的 `A=8.2, J=4000` case 为 baseline，
生成 RMSE ratio、lag Δ 和 projection-rate 三套 sensitivity CSV/PNG/SVG；
每个输入也保持独立，不存在跨输入、跨方法或相对 E02 P-only 的归一化。
总表 `analysis/pva_limit_sensitivity.csv` 保存全部 350 个执行结果，
`raw_target_feasibility.csv` 则逐输入、逐方法、逐限值审计投影前 target。
