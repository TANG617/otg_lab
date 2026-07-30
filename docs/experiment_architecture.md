# 实验架构与从零创建指南

本文总结当前 OTG 实验项目中已经落地的架构经验，目标是让下一项研究可以从
明确的问题开始，沿用同一套数据、运行、分析、可视化和发布边界，而不是复制
某个旧实验后继续堆叠特例。

具体的 CSV 字段、trace 字段和指标公式仍以各自的契约文档为准：

- [轨迹 CSV 契约](trajectory_csv.md)
- [周期 trace 与 command profile 契约](trace_csv.md)
- [指标契约](metrics.md)
- [已有 E 系列实验说明](experiments.md)

## 1. 一页架构

项目不是原工程的缩小副本，而是把 OTG 问题从原工程中按数据契约提取出来：
真实项目数据先成为不可变的原始输入，再显式转换为实验核心能够稳定重放的
canonical CSV。实验只负责改变事先声明的变量，分析只消费已经落盘的证据。

```text
真实项目导出
  data/raw/                       原样保留，不在这里清洗或重采样
      |
      v  显式转换，记录来源、列映射、时间轴策略和 SHA-256
  data/trajectories/              canonical CSV + meta.json
      |
      v
  experiments/Exx/experiment.py  声明问题、变量、输入、方法、窗口和指标角色
      |
      v
  otg_lab runner                  统一加载、跟踪、审计、计算和落盘
      |
      v
  experiments/Exx/runs/<run-id>/ 完整、可审计、可丢弃的运行证据
      |
      v  人工复核并手动复制
  experiments/Exx/results/<run-id>/
      |
      +----> GitHub Release       完整 result ZIP + 外层 SHA-256
      |
      +----> analyses/Axx/        固定具体 result，做跨实验分析
                  |
                  v
              work/              可重建中间表和 provenance
                  |
                  v
              results/           经复核的最终表、图和分析 manifest
```

这套架构的核心规则是：

1. 原始数据不变，所有解释性转换都显式化。
2. E 系列产生证据，A 系列解释固定的证据；A 系列不重新运行 follower。
3. 实验声明与通用执行引擎分离，实验目录不复制 loader、跟踪循环或指标公式。
4. 一个 run 是追加式证据目录；失败也要落盘，不能只保留成功方法。
5. `runs/`/`work/` 是可重建暂存区，`results/` 是人工选择后的长期结果。
6. 简单结论用 CSV + PNG/SVG，复杂多维探索用读取同一 run 的 HoloViz。
7. 任何结论都应能回到具体 spec、输入哈希、方法状态、逐输入指标和来源 run。

## 2. 分层与依赖方向

| 层 | 目录 | 责任 | 不应承担 |
|---|---|---|---|
| 原始数据 | `data/raw/` | 原样保存真实项目导出 | 重采样、差分、静默改列 |
| 规范输入 | `data/trajectories/` | 固定网格 CSV、metadata、来源哈希 | 实验方法逻辑 |
| 实验核心 | `otg_lab/` | 公共类型、组件、runner、指标、I/O、发布 | 引用某个 E/A 编号 |
| 单项实验 | `experiments/Exx_*/` | 声明一个问题和允许变化的变量；必要时添加专用审计产物 | 重写公共跟踪链 |
| 跨实验分析 | `analyses/Axx_*/` | 固定来源、验证可比性、配对统计、形成结论 | 隐式选 `latest`、重新执行 OTG |
| 展示 | PNG/SVG、HoloViz | 把已落盘的表和 trace 变成可读证据 | 成为唯一的数据计算实现 |
| 发布 | `otg_lab/publishing.py` | 打包人工选中的 E 系列 result 并创建 Release | 替代运行前后的科学复核 |

依赖方向应保持单向：

```text
experiment declaration ──> otg_lab
analysis implementation ──> pinned E-series artifacts + otg_lab analysis helpers
dashboard               ──> one explicit run/result directory
otg_lab                 -X-> experiments/ or analyses/
```

判断代码放在哪里时，可以使用下面的规则：

- 两个以上实验会复用，且语义稳定：放入 `otg_lab/`，并补公共测试。
- 只为一个实验构造方法：先用 `ComponentSpec.factory` 在该实验中注入。
- 只为一个实验生成额外审计表或静态图：使用 `ExperimentSpec.artifact_writer`。
- 读取多个已完成实验才能回答：新建 A 系列，不继续扩大某个 E 实验。
- 只改变展示方式：从 run 的 CSV 建视图，不回写算法结果。

## 3. 编号与目录语义

### 3.1 E 系列：一次独立实验

一个 E 编号只回答一个可以独立描述的问题：

```text
experiments/E12_topic/
  experiment.py          # 可执行声明
  README.md               # 问题、设计、运行和读取方式
  runs/                   # 忽略，不进入 Git
  results/
    index.csv             # Release 轻量索引，进入 Git
    <run-id>/             # 忽略，由 GitHub Release 保存大产物
  results.md              # 人工记录实验结论
```

不要用一个 E 编号同时承担“产生新数据”和“跨多个旧实验做最终选择”。前者属于
E，后者属于 A。

### 3.2 A 系列：固定来源后的分析

```text
analyses/A03_E08-E11_topic/
  analysis.yaml           # 精确来源、因子、筛选和来源要求
  analyze.py              # 稳定入口
  analysis_impl.py        # 配对、校验、表、图和报告逻辑
  work/                   # 忽略，可重建的合并表与 provenance
  results/                # 忽略；Release 保存最终表、图和 analysis manifest
    index.csv             # Release 轻量索引，进入 Git
  RESULTS.md              # 进入 Git；结论、证据、限制和复现命令
```

当前实现有意保留一个命名差异：

- E 系列的暂存目录字面上是 `runs/`；
- A 系列 collector 的暂存目录字面上是 `work/`，语义上相当于分析的可重建
  run workspace。

不要只在文档中把 A 的 `work/` 改称 `runs/`；若以后要统一名称，需要同时修改
collector、模板、`.gitignore` 和测试。

另一个容易混淆的名字是 E run 内的 `analysis/`。它是该次实验的自动分析产物，
不等于仓库顶层的 `analyses/Axx_*`。只有后者表示跨实验或最终决策分析。

## 4. 数据边界：真实数据也必须先规范化

真实项目数据的价值在于来源真实，不在于让实验核心兼容任意源格式。推荐保留
两份不同职责的数据：

```text
data/raw/<source-group>/<export>.csv
data/trajectories/<trajectory-id>.csv
data/trajectories/<trajectory-id>.meta.json
```

从 raw 转换到 canonical 时，必须决定并记录：

- 使用哪些源行和源列；
- 是否按源 timestamp、elapsed time，还是只按行序构造固定时间轴；
- 重采样、去重、裁剪、单位换算和缺失值策略；
- 哪些通道是真值，哪些通道不可用；
- raw 来源路径和哈希；
- canonical CSV 的 schema、`dt`、样本数和哈希。

position-only 数据的 V/A/J 列应保持整列空白。空白表示 unavailable，不能为
了通过下游代码而填零。离线有限差分得到的 `reference_derived.csv` 只用于输入
难度和诊断，不得进入在线 estimator/predictor，避免 truth leakage。

runner 会把 canonical 输入复制到 run 的 `inputs/<input-id>/reference.csv`，
再从这份 run-local 字节重新加载。这样解析轨迹和真实记录轨迹进入后续流程时
遵循完全相同的 loader 与哈希边界。

## 5. 实验声明是研究设计，不只是运行参数

`ExperimentSpec` 应在运行前完整回答下面的问题：

| 字段 | 需要表达的设计意图 |
|---|---|
| `question` | 这次实验唯一要回答的问题 |
| `hypothesis` | 可证伪假设，或明确写成诊断目标 |
| `independent_variables` | 真正允许改变的研究因素 |
| `controlled_variables` | `dt`、输入语义、限值、时间可用性、follower 等固定条件 |
| `allowed_method_differences` | baseline/candidate 之间允许不同的精确字段路径 |
| `inputs` | 规范 CSV、metadata、是否 required |
| `methods` | estimator → predictor → target → governor → follower 组件链 |
| `run_config` | 通用执行限制、步长、预测时域和失败策略 |
| `cases` | 同一基础方法在不同参数组合下的可执行 arm |
| `windows` | 预先声明的评估范围；必须包含 `full_overlap` |
| `metric_roles` | primary、secondary、guardrail、diagnostic 四类角色 |
| `comparison_spec` | baseline/candidate、输入、窗口、指标和 bootstrap 设置 |
| `input_gate` | 输入物理超限是阻断，还是只记录 |
| `artifact_writer` | 实验专用但仍从统一运行产物生成的审计表和图 |

### Method 与 Case 的选择

- 方法的组件链不同：定义多个 `TrackingMethodSpec`。
- 组件链相同，只改变 A/J 限值、窗口长度等运行配置：定义
  `ExperimentCase`，在 `factors` 中保留每个因素。
- 二维完整参数面：用 cases 表达执行矩阵；需要静态总览时再声明
  `FactorHeatmapSpec`。

case 的 `case_id` 会进入 artifact 路径和 tidy 指标的 `method_id`。因此它必须
稳定、可读，并包含足以区分 arm 的因子，而不是运行时序号。

### 指标角色要在看到结果前确定

- primary：直接判断假设的主指标，尽量少。
- secondary：补充效果大小和不同误差侧面。
- guardrail：任何改善都不能违反的安全、完整性和可靠性约束。
- diagnostic：解释机制，但不能事后替代 primary。

比较必须使用完整配对。某个 required 方法缺失时，保留
`unavailable_incomplete_pair`，不要删除失败输入后重新计算一个看似完整的
排名。跨输入推断应从逐输入 tidy rows 开始，不能把 `method_summary.csv` 的
聚合均值再次当成独立样本。

## 6. 一个 run 的证据合同

run ID 采用：

```text
<UTC timestamp>__<前 12 位 spec hash>
```

时间戳区分重复执行，spec hash 说明声明是否相同。完整目录大致为：

```text
runs/<run-id>/
  manifest.json
  inputs/<input-id>/
    reference.csv
    reference.meta.json
    reference_derived.csv
    reference_derived.meta.json
  methods/<method-or-case-id>/<input-id>/
    command.csv
    command.meta.json
    trace.csv
    command_profiles.csv
    status.json
  analysis/
    reference_metrics.csv
    trajectory_metrics.csv
    method_summary.csv
    comparisons.csv
    failures.csv
    report.md
    figures/
    <experiment-specific audit artifacts>
```

`manifest.json` 是 run 的入口，至少保存：

- 完整 resolved `ExperimentSpec` 与 `spec_hash`；
- Git commit、branch、dirty 状态和具体 dirty 文件；
- Python、平台和关键包版本；
- 输入来源、run-local 输入哈希、样本数和 `dt`；
- 每个方法/case 的 fingerprint、完成状态和有效周期；
- run 完成时所有输出文件的 SHA-256；
- `failure_count` 和 `required_failure_count`。

失败是证据的一部分。某个方法失败时，已完成 prefix、`status.json`、失败层和
原因仍应保存；非 required 方法失败可以让其他 arm 继续，required 方法失败
会使整个实验返回非零。

run 完成后应视为不可变。尤其不要在生成 manifest 后静默修改 CSV 或覆盖图。
如果某项派生 artifact 要成为 run 完整性的一部分，应通过 `artifact_writer`
在 runner 计算最终输出哈希前生成。运行后另建的 dashboard 应只读 run；若把
新的静态文件人工加入 result，必须明确它不在原 manifest 的 `outputs` 哈希
集合中。

## 7. 从 0 创建一个 E 系列实验

### 第一步：先写最小研究设计

在写代码前用四句话固定边界：

```text
问题：改变 X 是否会影响 Y？
假设：在输入集合 I 和窗口 W 上，candidate 的 primary 指标优于 baseline。
控制：除 X 外，dt、输入、限值、follower 和可用信息完全相同。
门槛：所有 required arm 完整，且 guardrail 不回退。
```

如果一句问题必须出现多个“并且还要”，通常应该拆为多个 E，或把最终选择放到
A。

### 第二步：创建目录

```bash
uv run otg-lab new-experiment E12 descriptive_slug
```

命令会从 `experiments/_template` 创建 `experiment.py`、`README.md`、
`results.md` 和 `results/index.csv`。随后替换模板中的示例方法和问题，不要
保留无关 arm。

### 第三步：准备输入

1. 将真实导出原样放入 `data/raw/`。
2. 用独立、可测试的转换逻辑生成 canonical CSV 和 metadata。
3. 通过公共 loader 回读，确认固定网格、单位、通道语义和哈希。
4. 在实验 `controlled_variables` 中明确 raw 与 canonical 路径及时间轴策略。

### 第四步：声明方法、case、窗口和比较

优先复用 `otg_lab` 中已有组件。实验专用组件可以通过
`ComponentSpec.factory` 注入，但仍要遵守公共输入/输出类型。若一段逻辑开始
被第二个实验复制，应提取到核心模块并加独立测试。

只在 `allowed_method_differences` 中开放真正的自变量。runner 会审计方法组件
和 case `run_config` 的差异；这能阻止“本来只比较 estimator，却同时改变
follower 或限值”的污染。

### 第五步：添加分层测试

至少覆盖：

- spec 可以构造并通过差异校验；
- 新组件的数值语义、启动期、因果时间和失败行为；
- 小输入上的端到端 run；
- 专用 artifact writer 的 schema、关键行数和不变量；
- 若有 HoloViz，renderer-neutral data loader 与 `--check`。

常用检查：

```bash
uv run pytest
uv run ruff check .
uv run otg-lab list
uv run otg-lab run E12 --no-figures
```

测试或 CI 中用 `--runs-root <temporary-path>`，不要污染正式实验目录。

### 第六步：正式运行并复核

```bash
uv run otg-lab run E12
```

复核顺序建议固定为：

1. `manifest.json.status == completed`；
2. `required_failure_count == 0`；
3. Git commit/dirty 状态符合本轮证据要求；
4. 输入路径、哈希、样本数和 `dt` 正确；
5. 每个 required method/case 的 `status.json` 完整；
6. `failures.csv` 没有未解释的失败；
7. primary 比较是完整配对且窗口正确；
8. guardrail 全部读取为 available 并通过；
9. 专用审计表与静态图使用同一批落盘数据；
10. `report.md`、实验 README 和人工结论没有超出数据可支持的范围。

同一个 spec 可以重复运行。不要使用 `latest` 作为长期引用；人工选择具体
`run-id`。

### 第七步：晋升到 result

只有人工复核通过的 run 才复制到 `results/`：

```bash
cp -R \
  experiments/E12_descriptive_slug/runs/<run-id> \
  experiments/E12_descriptive_slug/results/<run-id>
```

复制是有意设置的人工闸门。`runs/` 的“完成”表示程序执行完成，
`results/` 的“存在”表示研究者认为它值得长期保留。

先做本地确定性打包检查：

```bash
uv run otg-lab publish-run \
  experiments/E12_descriptive_slug/results/<run-id> \
  --package-only \
  --output-dir /tmp/otg-release
```

确认 ZIP 内容和 `SHA256SUMS` 后再发布：

```bash
uv run otg-lab publish-run \
  experiments/E12_descriptive_slug/results/<run-id>
```

或批量发布所有尚未发布的 E 系列 result：

```bash
uv run otg-lab publish-results
```

## 8. 从 E 系列建立 A 系列分析

当问题需要组合多个 E run 时：

```bash
cp -R analyses/_template analyses/A03_E08-E11_topic
```

在 `analysis.yaml` 中固定每个具体来源：

```yaml
sources:
  - source_id: e08_pva_recorded
    experiment_id: E08
    source_directory: experiments/E08_topic/results/<exact-run-id>
    factors:
      target_components: PVA
```

禁止使用 `latest`。每个 source 的因素要显式写入，collector 会将它们作为
`factor_*` 列加入合并后的 tidy tables。

执行：

```bash
uv run python analyses/A03_E08-E11_topic/analyze.py --check
uv run python analyses/A03_E08-E11_topic/analyze.py
```

`--check` 应验证来源存在、manifest 状态、commit/dirty 要求、artifact schema
和分析不变量。实际分析先形成 `work/source_inventory.csv`、
`combined_*.csv` 与 `provenance.json`，统计和制图统一消费这些表或等价的
in-memory prepared data，不能绕开 `analysis.yaml` 再扫描目录。

最终 A 结果应包含：

- 来源验证表；
- 逐输入/逐方法的配对明细；
- guardrail 汇总；
- 支撑结论的 CSV；
- PNG/SVG 及图表字段映射；
- `RESULTS.md` 中的结论、限制和复现命令；
- `analysis_manifest.json` 中的配置、来源 artifact 和输出哈希。

`publish-run` 接受 `experiments/<experiment>/results/<run-id>`；
`publish-analysis` 接受 `analyses/Axx/results/`。A Release 同时打包根目录的
`RESULTS.md` 和 `results/` 生成产物；两类大结果都由 Release 管理，Git 只保存
各自的轻量索引和结论文档。`publish-results` 在一个 RunBuoy 批次中扫描两类
尚未发布的结果。

## 9. 可视化策略

可视化的职责是解释落盘证据，不是重新定义数据。

| 情况 | 输出 | 约定 |
|---|---|---|
| 单指标、少量方法、二维静态关系 | CSV + PNG/SVG | PNG 便于预览，SVG 便于论文和无损缩放 |
| 二维完整参数面 | CSV + PNG/SVG heatmap | 每个像素/单元必须能回到 tidy row |
| 长时间序列、多方法、事件叠加、联动筛选 | HoloViz | 显式读取一个 run/result；支持缩放、选择和表格审计 |
| 无服务器分享 | 有界静态 artifact/HTML | 明确采样策略，离散异常事件不得丢失 |

### HoloViz 的推荐拆分

E08 提供了可复用的结构：

```text
dashboard_data.py
  只读取并规范化 run CSV
  不依赖 Panel/HoloViews
  可用项目主 Python 和 pytest 验证

build_holoviz_dashboard.py
  只负责 DataFrame、控件、联动图、表格和服务
  用 inline script metadata 固定独立 Python 与可视化依赖
```

这种拆分有几个好处：

- 数据语义可以脱离 renderer 单测；
- 主实验仍维持 Python 3.9 和 Ruckig 依赖，不被 dashboard 升级牵连；
- HoloViz 可以使用 Python 3.11 与独立锁定版本；
- 同一 normalized data model 可以同时生成 server dashboard 和便携快照。

启动方式沿用 E08：

```bash
uv run --python 3.11 --script \
  experiments/E08_pva_finite_difference_recorded_tracking/build_holoviz_dashboard.py \
  experiments/E08_pva_finite_difference_recorded_tracking/results/<run-id> \
  --check

uv run --python 3.11 --script \
  experiments/E08_pva_finite_difference_recorded_tracking/build_holoviz_dashboard.py \
  experiments/E08_pva_finite_difference_recorded_tracking/results/<run-id> \
  --show
```

新 dashboard 应满足：

- 接收明确的 `run_directory`，不在内部搜索最新 run；
- 只读取 run-local reference、method artifacts 和 analysis tables；
- 曲线可以按显示分辨率动态降采样，但投影、失败、限值触发等离散事件必须全量
  保留；
- hover、表格和坐标轴保留单位、method ID、sample/cycle 和原始数值；
- 颜色之外再使用线型、marker 或标签，避免颜色成为唯一编码；
- 提供无持久 server 的 `--check`，使 CI 能验证所有 tab 和数据关系；
- 页面明确区分离线固定网格回放与真实闭环机器人实验。

## 10. 发布与完整性边界

当前 GitHub Release 发布器会：

- 只接受人工复制到 `experiments/Exx_*/results/<run-id>` 的目录；
- 将所选目录原样打成确定性 ZIP；
- 生成外层 `SHA256SUMS`；
- 创建独立 Release，并更新该实验的 `results/index.csv`；
- 忽略 `.DS_Store`，拒绝 symlink；
- 不把实验 Release 标成软件仓库的 `Latest`。

当前发布器不会：

- 要求 run 对应 clean worktree；
- 重新计算并核对 manifest 中每个 `outputs` 哈希；
- 判断 scientific conclusion 是否正确；
- 自动选择哪个 run 值得发布。

因此 publish 是保存闸门，不是科学正确性闸门。建议在晋升 result 前检查 dirty
状态和 manifest 哈希；如果接受 dirty run，必须在 `results.md` 或后续 A 报告
中保留该限制。

Release 是大结果的远端归档，Git 中仍要保留能够发现它的轻量信息：

- 实验代码和 README；
- `results.md`；
- `results/index.csv` 中的 run ID、spec hash、commit、Release URL、ZIP hash
  和发布时间。

## 11. 常见反模式

- **复制旧实验的 runner。** 会使 CSV、失败处理和指标公式逐实验漂移。
- **直接把 raw CSV 喂给核心。** 会把时间轴和缺失值假设藏在算法内部。
- **事后才决定 primary metric 或窗口。** 容易形成只报告有利结果的选择偏差。
- **比较时同时改变未声明的配置。** 应由 `allowed_method_differences` 阻断。
- **失败 arm 不落盘。** 会把不完整性伪装成候选表现良好。
- **用 prefix RMSE 给未完成方法排名。** 标准比较必须保持 unavailable。
- **用 summary mean 当样本。** 统计单位应回到配对的 `input_id` 或更底层实体。
- **分析扫描 `latest`。** 目录内容变化后无法复现旧结论。
- **图直接读取原始工程数据。** 图应读取 run 中已固定、已审计的 CSV。
- **dashboard 同时做科学计算和展示。** 关键派生量应先成为有 schema 的表。
- **run 完成后覆盖文件。** 会让 manifest 哈希与真实目录失配。
- **认为上传成功等于实验可信。** Release 只保证资产被归档。

## 12. 可复用的完成清单

### 新 E 实验

- [ ] 一个精确问题和可证伪假设
- [ ] raw 数据原样保存，canonical 转换有 metadata 和哈希
- [ ] 自变量、控制变量和允许差异完整声明
- [ ] method/case ID 稳定且能表达 arm
- [ ] primary/secondary/guardrail/diagnostic 在运行前确定
- [ ] full-overlap 和主要窗口语义明确
- [ ] required 方法、失败政策和完整配对规则明确
- [ ] 核心组件、artifact writer 和端到端小输入有测试
- [ ] 静态图有对应 CSV；HoloViz 有独立 data loader 与 `--check`
- [ ] 正式 run 的 manifest、状态、哈希和 guardrail 已人工复核
- [ ] 只把选中的 run 复制到 results 并发布
- [ ] `results.md` 与 `results/index.csv` 已更新

### 新 A 分析

- [ ] 每个来源固定到具体 result 目录
- [ ] 来源 commit、dirty、status 和 schema 要求显式
- [ ] 分析因子、配对键、统计单位、窗口和 baseline 明确
- [ ] `--check` 不写输出也能完成来源与决策校验
- [ ] `work/` 可删除后重建
- [ ] 最终表不是从 summary mean 伪造样本
- [ ] guardrail 和不可用状态没有被过滤掉
- [ ] 每张图都有来源表、字段映射、PNG 和 SVG
- [ ] `RESULTS.md` 写明结论、限制和复现命令
- [ ] `analysis_manifest.json` 固定配置、来源与输出哈希

坚持这些边界后，新实验的主要工作会回到研究设计本身：明确改变什么、控制
什么、用什么证据判断，而不是重新搭一套运行和结果管理基础设施。
