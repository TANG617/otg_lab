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

确认值得长期保留的 run，手动复制到同一实验的 `results/`：

```bash
cp -R \
  experiments/<experiment-directory>/runs/<timestamp>__<spec_hash> \
  experiments/<experiment-directory>/results/<timestamp>__<spec_hash>

otg-lab publish-run \
  experiments/<experiment-directory>/results/<timestamp>__<spec_hash>
```

命令会把复制后的目录原样打包，因此其中保留哪些文件由人工选择决定。当前
阶段不检查工作区或 manifest 的 dirty 状态，也不复核 manifest 的逐文件输出
哈希。`results/<run-id>/` 被 Git 忽略，轻量索引位于该实验的
`results/index.csv`；GitHub Release 只上传所选
结果的 ZIP 和外层 SHA-256。仓库顶层不创建 `results/`。每个实验根目录的
`results.md` 只预置实验目录名标题，正文留空供人工记录。

全部实验的结果可以批量发布：

```bash
otg-lab publish-results
```

命令按路径排序扫描 `experiments/E*/results/<run-id>/manifest.json`，跳过索引中
已经处于 `published` 或 `draft` 状态的结果，每个目录对应一个独立 Release。
整个批次由一个 RunBuoy Run 以已处理结果数/总结果数显示结构化进度；单项失败
会记录在本机日志中并继续处理，批次结束时返回非零。

CI 或临时试跑可以使用 `--runs-root <path>` 覆盖该实验的 run 容器目录。

新实验可用以下命令生成：

```bash
otg-lab new-experiment E02 estimator_ablation
```

## A-series analysis runs

A-series 不运行 follower，也不产生新的实验样本。正式执行会把固定的 Exx
artifact、派生表、图、结论和 v2 analysis manifest 写入：

```text
analyses/<analysis-directory>/runs/<timestamp>__<analysis_spec_hash>/
```

`--check` 只在内存中验证，不创建 run。确认一次分析值得保留后，与 E-series
相同地复制完整目录：

```bash
cp -R \
  analyses/<analysis-directory>/runs/<analysis-run-id> \
  analyses/<analysis-directory>/results/<analysis-run-id>

otg-lab publish-analysis \
  analyses/<analysis-directory>/results/<analysis-run-id>
```

每个 analysis run 自包含 `RESULTS.md`、`work/`、最终 CSV、PNG/SVG 和
`analysis_manifest.json`。父 `results/index.csv` 保存轻量发布记录；
`publish-results` 同时扫描 E-series 和 A-series 的嵌套 result 目录。

## E01 scheduled P-only baseline

E01 独立运行 scheduled `p_kp1_baseline`：

```text
PositionOnly → ZOH → scheduled P[k+1], V=A=0
→ NoGovernor → ordinary unshielded Ruckig
```

它使用三条解析轨迹及
`recorded_tasks_original_no_velocity_limit`、
`recorded_tasks_simplified_with_velocity_limit` 两条 recorded trajectory，
并保持 10 ms 时间参数、V/A/J 限值和指标角色。主窗口为 `t>=0.04 s` 到各输入
结束。这里的 scheduled `P[k+1]` 与 E02/E07 使用当前测量 `P[k]` 的
position-only 链不同。

E01 不做内部方法比较。解析轨迹只用于中间正确性验证。E01 另有两个严格分工的
recorded baseline：

- 当前实际上线 baseline：P-only、original no-velocity-limit input、
  `V/A/J=4.2/8.2/41`，用于最终报告描述 status quo；
- 实验 paired baseline：P-only、velocity-limit input、
  `V/A/J=4.1/8.2/4000`，继续作为 A04/A06 及 recorded 候选收益的分母。

新增 current-online arm 不改变 E11/E12 或其他实验的 baseline，也不把两个
waveform 之间的差异解释为因果收益。

Scheduled P 在本项目中是因果 baseline：周期 `t[k]` 的 follower 起始状态为
上一周期已经提交、当前生效的 PVA，同时下一拍 target command `P[k+1]`
已排程可用。改用 `P[k]` 表示另一种信息契约，会额外增加一拍 target age。

运行：

```bash
uv run otg-lab run E01
```

E03–E06 继续在各自 run 中包含一条与 E01 实验 paired baseline 同定义的解析
baseline arm；共享构造与测试负责防止方法和运行配置漂移。

## E03–E06 解析轨迹方法正确性验证

这四组实验共享 `dt=10 ms`、V/A/J 限值 `4.1 / 8.2 / 4000`、无 governor、
ordinary Ruckig follower，以及 `quadratic_with_extremum`、`cubic`、`sine`
三条解析真值轨迹。其 PV/PVA 结果仅用于中间方法正确性验证，不参与 recorded
上线选型或收益计算：

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

## E11 记录任务波形上的 PV 迁移（无 target A）

E11 是 E08 的 PV 对照实验：输入、固定 10 ms 时间轴、P-only baseline、
执行限值、configured-limit projection、ordinary unshielded Ruckig、评估窗口
和验收规则均保持一致，只把五种差分候选从 scheduled PVA target 换成 E06 的
scheduled PV target。

PV target builder 在每个周期显式写入 `A=0`。差分 estimator/predictor 内部仍可
计算 acceleration 作为诊断状态，但该值不会进入 raw target 或 executable
target。Ruckig 的 `Amax=8.2 rad/s²` 仍然是运动约束，不能因 target 没有 A 而
删除。

运行：

```bash
uv run otg-lab run E11
```

主窗口为 `t≥0.04 s` 到记录结束。E11 复用 recorded-transfer 审计产物；
`analysis/raw_target_scan.csv` 和 `raw_target_feasibility.csv` 中保留
acceleration 字段，以逐周期验证所有 target acceleration 都严格为零。

## E12 Recorded PVA 的 runtime-Vmax 因果消融

E12 将输入采集条件与运行时限制拆开：三条 recorded position-only 输入分别
运行 scheduled P baseline 和五种 PVA 差分方法，并对每个 arm 干预
`Vmax=4.1/10 rad/s`，固定 `A/J=8.2/4000`，共 36 runs。

```bash
uv run otg-lab run E12 --no-figures
```

`vmax_ablation.csv` 分解 velocity clipping、acceleration clipping 和
stopping-envelope 调整；`vmax_interactions.csv` 给出同一输入内
`log(PVA/P @ 4.1) - log(PVA/P @ 10)`。只有 relaxed Vmax 非绑定且 ratio
随干预改善，才能把 PVA/P 差异归因到 runtime velocity limit。

## E13 P/PV/PVA 联合 stop-and-go 矩阵

E13 在 E07 的 20 条恒速输入和 4 档 A/J scale 上联合重跑 operational P、
scheduled P、五种 PV 与五种 PVA，共 960 arms。primary window 为成熟期
`0.5–2.5 s`。

```bash
uv run otg-lab run E13 --no-figures
```

`joint_stop_go_surface.csv` 同时报告 exact rest-to-rest pulse、event rate、
velocity ripple、RMSE、lag 与 guardrails。matched PV/PVA 是 A=0 的 negative
control，用来区分“加入 velocity target 的改善”与“acceleration component 的
额外改善”。

## E14 Selected PV/PVA 的 fine VAJ sensitivity

E14 固定 A04 选出的 Future-O1 stencil，对 matched PV/PVA 执行完整
`8 V × 8 A × 10 J` 网格，共 1,280 full-waveform arms。best tested setting
在 RMSE–`|lag|` Pareto 与 10/20 ms 档位内选择；同时输出 1%
near-optimal 和更低限值优先的 nondominated 子集。最小值若落在任一网格
边界，必须标记为 `boundary_censored`。新 run 同时写出 integer `lag_s` 和
局部二次插值的 `lag_subsample_s`；旧 compact aggregate 未保留完整 trace，
不能事后重建全 1,280-case sub-sample Pareto。

单进程运行：

```bash
uv run otg-lab run E14 --no-figures
```

有界内存并行运行与完整聚合：

```bash
uv run python \
  experiments/E14_pv_pva_vaj_fine_sensitivity/run_sharded.py \
  --shards 64 --workers 8
```

并行方式把完整 case index 按模数切片；每个 shard 都生成独立 completed
manifest，最后验证 1,280 个唯一 case 全部存在，再写出 aggregate manifest、
`vaj_sensitivity.csv` 和 `vaj_recommendations.csv`。

当本机内存或磁盘不足以保留完整 shard trace 时，可先紧凑保存已经完成的 surface
行，再用 isolated subprocess 逐 case 收尾：

```bash
uv run python \
  experiments/E14_pv_pva_vaj_fine_sensitivity/finish_compact.py \
  --batch-root experiments/E14_pv_pva_vaj_fine_sensitivity/sharded_runs/<batch> \
  --prune-intermediates --workers 2
```

每个 case 在独立进程执行，因此 native Ruckig crash 只会把该坐标标成
`eligible=false`，并写入 `native_crash_audit.jsonl`，不会中断其余网格。
`--prune-intermediates` 只删除指定 E14 batch 下已经紧凑保存、可重建的 shard
`inputs/methods/trace` 中间产物；正式 aggregate 保留完整 1,280 行表面和来源摘要。

## E15 无量纲 stop-go 边界

E15 将一周期 P-only rest-to-rest 位移写成两个无量纲量：
`q = 4A/(J dt)` 与 `rho = |v_ref|/v_crit(A,J,dt)`。其中
`v_crit = J dt^2/32`（jerk-limited，`q>=1`），否则
`v_crit = A dt/4 - A^2/(2J)`。实验跨 `dt`、`J`、`q`、方向与 `rho`
运行 ordinary Ruckig，并用 exact command profile 检查每周期是否接近零速度。

除完整网格外，E15 使用 Sobol 留出参数和二分搜索估计每个配置的
`rho_hat`。验收要求两种动力学分支均复现：`rho<1` 为 stop-go、`rho>1`
离开 stop-go，且留出阈值相对解析边界误差不超过 2%。

`q=1, rho=1` 同时落在动力学公式切换与行为阈值上，是单独声明的 exact-seam
诊断点，不参与两侧分类验收。若 native solver 在该测度零交点失败，run 必须
保留失败坐标并报告 diagnostic failure count；除该交点外的完整网格与 Sobol
留出仍全部是 required，不能据此豁免。

```bash
uv run otg-lab run E15 --no-figures
```

## E16 Velocity component 的因果消融

E16 固定 E15 的恒速机理探针，同时改变 target velocity coefficient、速度符号、
P-only lookahead 与 Ruckig minimum duration。正控制为 scheduled P，oracle PV
只作非因果上界；Future-O1 是只使用截至当前周期 position history 的因果 PV。
错误符号与错误倍率是 negative controls。

E16 同时保留 raw Future-O1 与一个仅消除浮点级速度抖动的 deadband arm。后者
不会平滑可观测运动，只把绝对变化不超过 `1e-10 rad/s` 的 target velocity
保持为上一值；两者的差异用于审计 native solver 对 microscopic terminal-state
扰动的敏感性。验收要求 matched causal/oracle PV 在 `q<1` 与 `q>=1` 均消除
P-only pulse，而 P-lookahead 或 minimum-duration 变化不能复制 matched PV 的
exact profile 结果。

```bash
uv run otg-lab run E16 --no-figures
```

## E17 因果 PV 的开发/留出鲁棒性

E17 只在 development seeds 上比较 backward difference、alpha-beta、CA-KF 与
local-polynomial observer；选择规则和 RMSE guardrail 固定后，再一次性评估
未参与选型的 holdout seeds。控制周期仍是严格固定网格，measurement 则带独立
`state_time` 和 `available_time`，可注入：

- position noise 与 encoder quantization；
- 不规则 source timestamp；
- 一或两拍可用性延迟；
- sample dropout 与 sample-and-hold。

Primary 是相对 scheduled P 的 exact-profile velocity-ripple reduction；位置
guardrail 使用“RMSE excess / 一周期 reference displacement”，避免在恒速 P
端点 RMSE 为数值零时形成退化 ratio。最终验证另外包含 constant、ramp、sine、
chirp、reversal 五类合成轨迹。项目 recorded waveform 的 raw timestamp replay
只作同任务诊断，不能代替独立任务、真机或多轴 holdout。

总体 paired bootstrap 之外，每个 work-envelope condition 必须单独满足：中位
ripple reduction 不低于 50%、改善配对比例不低于 90%、中位 RMSE excess 不高于
0.10 个单周期位移。每条合成 holdout 还必须逐条达到至少 50% ripple reduction、
无执行 guardrail 失败，且 RMSE excess 不高于 `1e-9 rad`。因此总体中位数不能
掩盖某一噪声或时序条件退化。

```bash
uv run otg-lab run E17 --no-figures
```

## E18 Sync.No recorded/replay 一致性

E18 现在只验证一件事：记录的 `Synchronization.No + PV Future-O1` 输出与同配置
回放是否完全一致。默认输入改为实验目录中的 `data/raw/none.csv`，读取约 10 ms
raw position、约 1 ms `values[0]` 输出和只用于时序审计的 `values[4]` echo。

累计 CSV 按 source gap 大于 1 s 分段并选择最后一段。本地从该段 reset 的
P/V/A=`0/0/0` 开始完整执行；前 3 s 只从评分中剔除，不能改变状态传播。首个
真实输出与零初态、`J=4000`、1 ms 的 `J·dt³/6` 在约 `1.85e-17 rad` 内一致。

统一 `method_id=pv_pred_backward_o1_kp1`。Future-O1 固定 `h=10 ms`，前两点使用 raw P/V0，成熟后产生预测 P/V，target A
恒为 0。Ruckig 固定 1 ms、`Synchronization.No`、
`V/A/J=4.1/16.2/4000`，无 governor、projection 或 minimum duration。真实缺失
tick 不插值，本地仍连续运行。

position-only logger 无法证明真实调用顺序，因此探索性回放同时保留三种声明：

- `update_target_callback_and_control_loop`：持久化 update，target callback 与 control loop
  分开调用；这是部署主假设；
- `update_control_loop_only`：只在 1 ms loop update；
- `calculate_control_loop_only`：旧式逐拍 calculate 诊断。

三个 execution 均报告零偏移 RMSE、MAE、bias、P95、最大误差和逐点误差；±20 ms lag
不替代主指标。主图同时展示 source position、raw target P、recorded output 和
replay output，另有相对 source 与 replay-recorded 的误差面板；同时生成启动调用
语义、最大误差局部和三 execution 误差 figure。科学差异不会让实验运行失败。

```bash
uv run otg-lab run E18
uv run otg-lab run E18 --no-figures
```

### No-only 正式门禁

同一 E18 run 会检查 `data/full_axis_capture`。正式输入仍是
`capture_manifest.json`、`calls.csv`、`axis_states.csv`、
`raw_position_events.csv`，但只要求一个完整 `mode=No` run。其他同步模式可以
存在，但不再阻塞 E18，也不参与 identity decision。

门禁依次为 target-builder parity、solver-step parity、closed-loop parity。replay
必须从唯一 `run_reset` 执行每个 call，包括垃圾窗口和 target callback；
`analysis_valid` 只控制评分。P/V/A/duration 逐点阈值为
`1e-12 / 1e-10 / 1e-8 / 1e-12`，result、调用来源和 calculation/section 状态
必须完全相同。只有三个 gate 都通过才报告 `formal_parity_passed`。

当前 `none.csv` 只有右轴 position，所以可以生成探索性误差和图，但正式状态只能
是 `formal_parity_not_evaluable`。同步排名和 P-only/PV 分析不属于重建后 E18。
旧 `0801.csv` 回放保留为 `run_legacy_0801_replay`；原四同步模式扩展仍保留在
`validation_pipeline.py`，仅供后续独立研究。

## E19 PV Future-O1 replay Amax sensitivity

E19 复用 E18 的 `none.csv`、最后 reset 段、`method_id=pv_pred_backward_o1_kp1`
和主 execution `update_target_callback_and_control_loop`。每个 `case_id=amax_*`
都从零 P/V/A 完整重放该段，唯一自变量是 Ruckig `max_acceleration`；
`Synchronization.No`、1 ms、target A=0、
`V=4.1 rad/s` 和 `J=4000 rad/s³` 全部固定。

扫描网格为 `16.2, 16.4, ..., 40.6, 48.6, 64.8 rad/s²`，共 125 个案例。
数据中评分区间内最大的正向 raw-position 跳变是分析锚点。原凹陷窗口固定为锚点
前 30 ms 到后 40 ms；第二窗口从锚点向两侧扩展到 raw position 不再单调非减，
用来检查提高 A 后凹陷是否只是移到了上升段的其他位置。

两个窗口都报告最大 position drawdown、峰谷时刻、最小速度和负速度持续时间。
`1e-12 rad` 是数值消失阈值，`0.1 mrad` 是工程阈值。只有完整上升区间通过才允许
报告全局消失；仅原窗口通过时统一报告 `focal_eliminated_but_transferred`。
recorded output 只作为 `Amax=16.2` 观测参考，不代表提高 Amax 后的反事实。

```bash
uv run otg-lab run E19
uv run otg-lab run E19 --no-figures
```

## E20 PV Future-O1 acceleration-conditioned targets

E20 复用 E18 的 `none.csv`、Future-O1 PV、`Synchronization.No`、1 ms 控制周期、
零初态、主 execution 和 `V/A/J=4.1/16.2/4000`。两个 method ID 为
`pv_pred_backward_o1_kp1` 与 `pv_pred_backward_o1_kp1_accel_projected`，对应
`conditioning_id=none/acceleration_projection`。唯一干预发生在 replay 之前：对
完整 PV 事件序列一次性求解离线投影，使实际相邻事件间隔上的曲线满足

```text
v[i+1] = v[i] + a[i] * dt[i]
p[i+1] = p[i] + 0.5 * (v[i] + v[i+1]) * dt[i]
|v[i]| <= 4.1 rad/s, |a[i]| <= 16.2 rad/s²
```

投影不进入 1 ms 控制循环，也不增加 runtime governor。Ruckig target A 仍固定为
0，因此接口仍是 PV；E20 不约束离线输入曲线的 jerk，以单独检验加速度合法化的
贡献。QP 后从固定首态确定性重建整条曲线，只有无 V/A 超限且动力学等式残差通过
门禁才运行配对 replay。图例使用 `replay output — raw target` 和
`replay output — A-projected target`，不会把 output 本身称为 projected。
`target_recorded_replay_comparison.{png,svg}` 覆盖完整 reset 段；现有
`target_and_output_comparison.{png,svg}` 保留为凹陷窗口 P/V 细节图；
`dip_position_comparison.{png,svg}` 则只画该窗口的位置并标注最大回撤。

```bash
uv run otg-lab run E20
uv run otg-lab run E20 --no-figures
```
