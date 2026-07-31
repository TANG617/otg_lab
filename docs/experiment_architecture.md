# 可复用实验架构：从零创建、分析与发布

本文描述一套与具体代码库、编程语言和执行框架无关的实验架构。它关注的是
研究对象之间的边界、数据与产物合同、生命周期和可复现性，而不是某个包的
类名、命令行参数或目录实现。

这份文档可以直接复制到新的实验仓库。复制后只需要为当前项目补充一份
“实现映射”，说明本项目用哪些模块、命令和工具实现这里定义的抽象角色。

## 1. 文档边界

本文负责定义：

- 原始数据、规范数据、实验、分析、结果和 Release 之间的关系；
- Experiment 与 Analysis 的编号和责任边界；
- 声明、run、result、manifest、指标和可视化的最低合同；
- 从零建立新实验的推荐流程；
- 可复现性、人工复核、发布和长期归档的规则。

本文不负责定义：

- 具体包名、模块路径、类名和函数名；
- 具体 CLI、构建工具、虚拟环境或依赖版本；
- 某个算法、设备或业务领域的字段；
- 当前仓库有哪些实验以及每个实验的结论；
- 某个 GitHub Release 发布器的内部实现。

这些实现细节应放在项目 README、领域数据合同、实验 README 或单独的
`project_mapping` 文档中。这样架构原则可以稳定复用，实现则可以独立替换。

## 2. 核心术语

为了使文档适用于不同项目，下面的术语表示角色，不要求使用相同的目录名或
代码名称。

| 术语 | 含义 |
|---|---|
| Raw data | 从真实系统、外部项目或设备直接取得、未经解释性改写的数据 |
| Canonical data | 经过显式转换、满足稳定 schema、可被执行器重复读取的数据 |
| Experiment | 运行被测系统并产生新证据的一次独立研究 |
| Analysis | 固定消费已有证据，不重新运行被测系统的整理、比较或决策过程 |
| Arm | 实验中一个可独立执行的方法或参数组合 |
| Run | 一次声明已解析、环境已记录、结果已落盘的执行实例 |
| Result | 从 run 中经人工复核后晋升、值得长期保留的证据包 |
| Artifact | CSV、JSON、日志、图、报告、dashboard 数据等落盘文件 |
| Manifest | 描述声明、来源、环境、状态、文件和哈希的机器可读入口 |
| Release | result 的不可变远端归档及其轻量索引 |

本文用 `Exx` 表示 Experiment 编号，用 `Axx` 表示 Analysis 编号。这只是推荐
命名约定；其他项目可以替换编号前缀，只要“产生证据”和“消费证据”的身份不会
混淆。

## 3. 一页架构

```text
真实系统或外部项目
        |
        v
Raw data
  原样保存，不静默清洗
        |
        v  显式转换 + schema + provenance + hash
Canonical data
        |
        v
Experiment declaration
  问题、假设、因素、控制、输入、arm、窗口、指标
        |
        v
Execution adapter
  校验、执行、失败隔离、指标计算、产物落盘
        |
        v
Experiment run
  完整证据，可重建、可丢弃、不可静默改写
        |
        v  人工复核与晋升
Experiment result
        |
        +----------> Release
        |
        v
Analysis declaration
  固定具体来源、配对键、筛选、统计单位和决策规则
        |
        v
Analysis adapter
  来源校验、合并、统计、制图、报告和 provenance
        |
        v
Analysis run
        |
        v  人工复核与晋升
Analysis result
        |
        +----------> Release
```

核心规则：

1. Raw data 不变；所有会改变语义的操作都发生在显式转换阶段。
2. Experiment 产生证据，Analysis 消费固定证据。
3. 声明描述研究设计，执行器只实现通用机制。
4. run 保存完整事实，包括失败和 unavailable 状态。
5. run 完成不等于 result 可信；run 到 result 必须经过人工闸门。
6. result 是不可变发布单元，Release 是其远端归档。
7. 可视化只读取落盘 artifact，不成为唯一的科学计算实现。
8. 每个结论都必须能回到具体来源、声明、逐单位指标和文件哈希。

## 4. 解耦模型：策略、合同、适配器、研究实例

架构分成四层。

| 层 | 内容 | 变化频率 |
|---|---|---|
| 策略层 | 编号、生命周期、完整配对、人工晋升、发布规则 | 最低 |
| 合同层 | 数据 schema、声明 schema、metric rows、manifest、artifact layout | 较低 |
| 适配器层 | loader、runner、metric engine、renderer、publisher | 中等 |
| 研究实例层 | 某个 Exx/Axx 的问题、方法、输入、参数和结论 | 最高 |

依赖方向应保持单向：

```text
研究实例 ──> 合同
适配器   ──> 合同
策略     ──> 合同语义

合同     -X-> 某个具体实验
执行核心 -X-> 某个具体分析
展示层   -X-> 被测系统内部状态
```

一个新框架只要实现合同要求的端口，就可以替换旧框架，而不改变 Experiment、
Analysis、run/result 或 Release 的语义。

## 5. 推荐目录蓝图

目录名可以调整，但每种职责必须有唯一归属。

```text
project/
  data/
    raw/
    canonical/

  contracts/
    data/
    metrics/
    manifests/

  adapters/
    execution/
    analysis/
    visualization/
    publication/

  experiments/
    _template/
    E01_topic/
      definition.*
      README.md
      runs/
        <run-id>/
      results/
        <result-id>/
      results.md
      results.index.*

  analyses/
    _template/
    A01_topic/
      definition.*
      README.md
      runs/
        <analysis-run-id>/
      results/
        <result-id>/
      RESULTS.md
      results.index.*

  docs/
    experiment_architecture.md
    project_mapping.md
```

如果现有分析工具使用 `work/`、`cache/` 或平铺的 `results/`，可以通过适配器
映射到上面的抽象：

- `work/` 或 `cache/` 等价于 Analysis 的可重建 run workspace；
- 平铺 `results/` 等价于一个已选中的 result bundle；
- 映射必须写入 manifest 或索引，不能依赖阅读者猜测。

推荐新项目让 Experiment 与 Analysis 都采用 `runs/<id> → results/<id>`，
这样晋升、打包和发布可以共享同一套状态机。

## 6. 框架必须提供的端口

“端口”表示能力合同，不表示必须使用面向对象、Python 或某个接口语法。

| 端口 | 输入 | 输出 | 关键保证 |
|---|---|---|---|
| Data normalizer | raw data + conversion definition | canonical data + metadata | 转换显式、可重放、可校验 |
| Definition validator | experiment/analysis declaration | resolved declaration | 字段完整、引用有效、差异受控 |
| Arm executor | canonical input + resolved arm | raw execution artifacts + status | 失败隔离、状态完整、无隐式输入 |
| Metric evaluator | canonical reference + execution artifacts | tidy metric rows | 定义版本固定、缺失语义明确 |
| Analysis collector | pinned result sources + selection | combined rows + provenance | 禁止隐式 latest、来源可验证 |
| Artifact store | artifacts + metadata | append-only run directory | 原子创建、稳定路径、哈希可追踪 |
| Renderer | bounded artifact data | PNG/SVG/dashboard | 不改变科学结果 |
| Publisher | reviewed result bundle | archive + checksum + index record | 确定性、不可覆盖、可发现 |

端口之间通过落盘合同或稳定的数据结构交互。不要让 Analysis 或 dashboard 直接
调用执行器内部的私有对象；否则历史 result 会随代码重构而失去可读性。

## 7. 数据合同

### 7.1 Raw data

Raw data 是来源证据，不是方便执行器读取的工作文件。

最低要求：

- 保留原始字节或可验证的原始对象标识；
- 记录来源、采集时间、导出方式和访问范围；
- 不在 raw 目录做重采样、单位换算、差分或列覆盖；
- 若因隐私或体积不能入库，保留定位信息和内容哈希；
- 不用“清洗后的 raw”覆盖原文件。

### 7.2 Canonical data

Canonical data 是执行器与领域输入之间的防腐层。schema 应稳定、版本化，并且
不依赖 raw 文件的偶然格式。

每份 canonical 数据至少配套：

- `schema_version`；
- 稳定的 `input_id`；
- 字段、类型、单位和缺失值语义；
- 采样、排序、时间轴或索引规则；
- raw 来源定位和哈希；
- 转换定义或转换器版本；
- canonical 内容哈希；
- 行数、时间范围或其他基本统计；
- 已知限制。

空值、零、未观测和不适用必须是不同语义。执行器不能通过填零来掩盖缺少的
输入通道。

### 7.3 转换定义

raw → canonical 的转换应独立于 Experiment。转换定义至少回答：

- 选择哪些源对象、行和列；
- 如何排序、去重、裁剪和过滤；
- 是否重采样，如何处理时间抖动；
- 如何换算单位；
- 如何处理缺失、异常和重复值；
- 哪些派生字段只允许用于离线分析；
- 如何验证转换结果。

同一 canonical 输入应能够被多个 Experiment 复用，而不需要重新解释 raw。

## 8. Experiment 声明合同

Experiment 声明是研究设计的机器可读版本。它不应只是运行参数集合。

推荐字段：

| 字段 | 语义 |
|---|---|
| `schema_version` | 声明合同版本 |
| `experiment_id` | 稳定实验编号 |
| `title` | 可读标题 |
| `question` | 唯一研究问题 |
| `hypothesis` | 可证伪假设或明确诊断目标 |
| `independent_factors` | 真正允许变化的因素 |
| `controlled_factors` | 必须保持一致的条件 |
| `allowed_differences` | baseline 与 candidate 间允许不同的精确路径 |
| `inputs` | canonical 输入及 required 状态 |
| `arms` | 方法或参数组合 |
| `evaluation_windows` | 预先声明的评估范围 |
| `metrics` | 指标及其角色 |
| `comparisons` | baseline/candidate、配对键和统计规则 |
| `failure_policy` | required、继续执行和不可用状态规则 |
| `artifact_requirements` | 必须生成的表、图、报告和审计文件 |

语言无关的示例：

```yaml
schema_version: experiment.v1
experiment_id: E01
title: Example controlled comparison
question: Does factor X improve outcome Y under control set C?
hypothesis: Candidate lowers the primary metric without guardrail regression.

independent_factors:
  - method

controlled_factors:
  sampling_interval: 0.01
  input_policy: fixed
  execution_policy: unchanged

allowed_differences:
  - arm.method

inputs:
  - input_id: input_01
    artifact: data/canonical/input_01.csv
    required: true

arms:
  - arm_id: baseline
    method: baseline_method
    required: true
  - arm_id: candidate
    method: candidate_method
    required: true

evaluation_windows:
  - window_id: primary_window
    start: 0.1
    end: 1.0
  - window_id: full_overlap

metrics:
  primary:
    - outcome_rmse
  secondary:
    - outcome_mae
  guardrail:
    - constraint_violation_count
  diagnostic:
    - observed_lag

comparisons:
  - baseline_arm: baseline
    candidate_arm: candidate
    windows:
      - primary_window
    metrics:
      - outcome_rmse
      - constraint_violation_count
```

声明可以存为 YAML、JSON、代码或数据库记录，但 resolved declaration 必须以
稳定、可哈希的形式写入 run manifest。

### Arm 与 Case

使用两层身份可以避免方法语义与参数矩阵混在一起：

- Method：算法或处理链的稳定身份；
- Arm/Case：Method 在一组具体参数和控制条件下的一次可执行配置。

同一 Method 在多个参数档位运行时，每个 Arm 都要有稳定 ID。Arm ID 应表达
关键因素，不要使用只在当次进程中有意义的序号。

### 指标角色

指标角色必须在看到正式结果前确定：

- Primary：直接判断假设，数量尽量少；
- Secondary：补充效果大小和误差结构；
- Guardrail：任何改善都不能破坏的约束；
- Diagnostic：解释机制，不可事后替代 Primary。

指标定义至少固定公式、单位、方向、适用条件、时间对齐、缺失策略和定义版本。

## 9. Tidy metric 合同

推荐一行只表达一个最小统计单位上的一个指标：

```text
source_id
input_id
arm_id
window_id
metric_id
metric_version
value
unit
direction
role
status
sample_count
notes
```

`status` 与 `value` 同样重要。推荐至少区分：

- `available`
- `unavailable_missing_input`
- `unavailable_incomplete_arm`
- `unavailable_incomplete_pair`
- `unavailable_not_applicable`
- `failed_metric_evaluation`

不要用空行、零值或删除记录代替不可用状态。

比较必须保持完整配对。任一 required Arm 在某个统计单位上不可用时，该配对
保持 unavailable，不能删除失败单位后重新得到一个表面完整的排名。

## 10. Run 证据合同

推荐 run ID 同时包含时间和声明指纹：

```text
<UTC timestamp>__<definition-hash-prefix>
```

时间戳区分相同声明的重复执行，hash 说明研究设计是否发生变化。hash 应基于
resolved declaration，而不是源文件路径或对象内存地址。

Experiment run 的推荐结构：

```text
runs/<run-id>/
  manifest.json
  inputs/
    <input-id>/
      canonical_copy.*
      metadata.*
  arms/
    <arm-id>/
      <input-id>/
        output.*
        trace.*
        status.json
  evaluation/
    metrics.*
    comparisons.*
    failures.*
    report.md
    figures/
```

Analysis run 的推荐结构：

```text
runs/<analysis-run-id>/
  manifest.json
  sources/
    inventory.*
    provenance.*
  combined/
    selected_rows.*
  evaluation/
    paired_rows.*
    decision_tables.*
    validation.*
    report.md
    figures/
```

### Manifest 最低字段

```yaml
schema_version: run_manifest.v1
run_id: ...
run_kind: experiment | analysis
status: running | completed | failed
definition:
  resolved: ...
  sha256: ...
source_control:
  revision: ...
  dirty: ...
environment:
  runtime: ...
  dependencies: ...
inputs:
  - id: ...
    locator: ...
    sha256: ...
units:
  - id: ...
    required: true
    status: ...
failures:
  total: ...
  required: ...
outputs:
  relative/path:
    sha256: ...
    size_bytes: ...
```

manifest 先以 `running` 写入，执行结束后再原子更新为 `completed` 或
`failed`。即使进程中断，已有目录也能被识别为未完成 run。

run 完成后应视为 append-only。若需要追加正式 artifact，应生成新的 run，或
通过有版本的补充 manifest 明确记录；不能静默覆盖原文件。

## 11. Experiment 与 Analysis 的边界

使用下面的判断：

- 需要再次运行被测系统、模拟器、设备或核心算法：Experiment。
- 只读取一个已完成 result 并生成新的解释或展示：Analysis 或 Visualization。
- 读取多个已完成 result 做比较、归因或选型：Analysis。
- 只是修正已有图的样式，不改变派生数据：Visualization。
- 改变统计单位、筛选、指标或决策规则：新的 Analysis run。

Analysis 必须固定到具体 result 或具体 run ID，禁止使用隐式 `latest`。来源
声明至少保存：

- `source_id`；
- 来源种类和稳定 ID；
- 精确 artifact locator；
- 来源 manifest hash；
- 在当前分析中的因素和角色；
- 允许或拒绝 dirty、failed、partial 来源的策略。

Analysis 不应重新调用被测系统，也不应修改来源目录。它只生成新的派生证据和
provenance。

## 12. 统一生命周期

Experiment 与 Analysis 共用同一状态机：

```text
declared
   |
   v
validated
   |
   v
running
   |
   +------> failed
   |
   v
completed
   |
   v  人工科学复核
reviewed
   |
   v  从 runs 复制或原子晋升
promoted
   |
   v  确定性打包与校验
packaged
   |
   v
published
```

各状态的语义：

| 状态 | 保证 |
|---|---|
| declared | 问题与声明已存在 |
| validated | schema、引用和受控差异通过静态校验 |
| running | manifest 已建立，执行尚未结束 |
| failed | 失败已落盘，不代表目录无价值 |
| completed | required 执行完成，机器检查通过 |
| reviewed | 人工确认数据、比较、guardrail 和结论范围 |
| promoted | 选中 run 已进入 results |
| packaged | 归档和校验和已生成并复核 |
| published | 远端 Release 和轻量索引已写入 |

`completed` 不能自动跳过 `reviewed`。人工晋升不是低效步骤，而是区分“程序运行
成功”和“证据值得长期保存”的必要边界。

## 13. 从零创建 Experiment

### 13.1 固定问题

先写四句话：

```text
问题：改变 X 是否影响 Y？
假设：在输入集合 I 和窗口 W 上，Candidate 改善 Primary。
控制：除 X 外，环境、输入、约束和执行政策一致。
门槛：所有 required Arm 完整，且 Guardrail 不回退。
```

一句问题包含多个互不依赖的“并且”时，优先拆成多个 Experiment，再用 Analysis
组合结论。

### 13.2 分配稳定身份

建立：

- Experiment ID 与可读 slug；
- input ID；
- Method ID；
- Arm/Case ID；
- window ID；
- metric ID 与版本。

稳定 ID 不应包含绝对路径、机器名、进程号或临时序号。

### 13.3 准备 canonical 输入

1. 保存 raw 数据；
2. 编写独立转换定义；
3. 生成 canonical artifact 与 metadata；
4. 用独立校验器重新读取；
5. 检查 schema、单位、排序、时间轴、空值、行数和 hash；
6. 在 Experiment 声明中只引用 canonical artifact。

### 13.4 声明受控比较

明确：

- 哪些因素变化；
- 哪些因素必须相同；
- baseline 与 candidate 允许不同的字段路径；
- required Arm；
- 失败后是否继续其他 Arm；
- Primary、Guardrail、窗口和完整配对规则。

执行前校验所有 Arm 的实际差异。这样可以阻止本想比较算法，却同时改变输入、
约束或执行政策的污染。

### 13.5 建立最小测试

至少覆盖：

- 声明 schema 和引用校验；
- canonical 输入 round-trip；
- 单 Arm 的最小执行；
- 失败 Arm 的状态和 prefix artifact；
- metric 定义与 unavailable 状态；
- baseline/candidate 完整配对；
- manifest、hash 和目录布局；
- 专用 artifact 的字段、不变量和确定性；
- dashboard 数据模型与无服务渲染检查。

### 13.6 Dry run

使用小输入、临时 artifact root 或关闭昂贵展示的方式完成端到端 dry run。Dry
run 仍应生成完整 manifest，以验证的重点不是数值结论，而是证据链能否闭合。

### 13.7 正式运行

正式运行时：

- 创建新的 run ID，拒绝覆盖已有目录；
- 先写 `running` manifest；
- 每个 Arm 独立落盘状态；
- 单个非 required Arm 失败不阻断其他 Arm；
- required 失败使 run 最终失败；
- 计算指标和比较；
- 生成必须 artifact；
- 写输出 hash；
- 原子结束 manifest。

### 13.8 人工复核

建议按固定顺序：

1. manifest 状态和 required failure；
2. source revision 与 dirty 状态；
3. 输入 locator、hash、schema、行数和时间范围；
4. 每个 required Arm 的状态与有效范围；
5. failures 是否都有解释；
6. Primary 是否使用预声明窗口；
7. comparison 是否完整配对；
8. Guardrail 是否 available 且通过；
9. 表、图和报告是否读取同一批 artifact；
10. 结论是否超出实验条件和统计单位。

### 13.9 晋升与发布

复核通过后，将整个 run 原样复制或原子晋升到
`results/<result-id>/`。不要挑选性删除失败、trace 或 provenance 来减小结果
体积；需要精简发布包时，应由显式 packaging policy 决定并记录。

先本地生成归档与校验和，检查内容后再创建远端 Release。

## 14. 从零创建 Analysis

### 14.1 判断是否真的需要新的 Analysis

新建 Analysis 的典型原因：

- 跨多个 Experiment 比较；
- 使用新的统计单位或配对键；
- 做方法选型、归因、敏感性或 Pareto 分析；
- 将多个 guardrail 与 Primary 组合成决策；
- 原 Experiment 的自动报告不足以回答新的问题。

仅改变颜色、标题或布局，不需要新的 Analysis；仅修复算法实现，则需要新的
Experiment run，不能在 Analysis 中补跑。

### 14.2 固定来源

Analysis 声明必须列出精确来源，不能扫描“最近一次成功结果”。每个来源先完成
以下检查：

- manifest schema 可识别；
- run/result 状态符合策略；
- 来源哈希与 locator 一致；
- 需要的 artifact 存在；
- schema 兼容；
- revision、dirty 和环境差异已接受或明确阻断；
- 控制变量可比较。

### 14.3 明确统计单位

先确定独立统计单位，再写聚合：

- input、task、subject、device、seed 或 session 中哪个是独立单位；
- baseline/candidate 通过哪些键配对；
- 同一 baseline 在多个来源中重复出现时如何去重；
- 缺失一个 Arm 时如何保持 unavailable；
- 是否需要 bootstrap，以及 seed 和重复次数。

不要把已经聚合的 summary mean 当成新的独立样本。

### 14.4 分离收集与结论

Analysis run 推荐分两阶段：

1. 收集阶段：生成 source inventory、combined tidy rows 和 provenance；
2. 决策阶段：只读取收集后的稳定表，完成配对、统计、图和报告。

决策阶段不能绕过声明重新扫描来源目录。

### 14.5 Analysis 完成条件

- 来源固定且验证通过；
- 配对明细可审计；
- Primary、Guardrail 与 decision rule 预先声明；
- unavailable 没有被过滤；
- 每张图能回到确定的源表和字段；
- 报告写明结论、限制和不可外推范围；
- analysis manifest 固定配置、来源和输出 hash；
- 经复核的 Analysis run 晋升到 Analysis result；
- result 可独立打包和发布。

## 15. 可视化策略

可视化的选择取决于信息结构，不取决于当前团队最熟悉的库。

| 数据关系 | 推荐输出 | 要求 |
|---|---|---|
| 单指标、少量方法、简单二维关系 | CSV + PNG/SVG | PNG 便于预览，SVG 便于无损复用 |
| 完整二维参数面 | CSV + PNG/SVG heatmap | 每个单元可回到 tidy row |
| 长时间序列、多方法、事件叠加 | HoloViz | 缩放、筛选、联动和明细表 |
| 多维敏感性、分面和条件筛选 | HoloViz | 控件反映声明因素，不隐式改数据 |
| 无服务器分享 | 有界 HTML 或静态 artifact | 明确采样，关键离散事件全量保留 |

### 15.1 静态图

每张静态图应同时拥有：

- 对应的 source CSV；
- 字段映射或 chart specification；
- 标题、单位、窗口、baseline 和方向；
- PNG 与 SVG；
- 确定性排序和配色；
- 图中 unavailable 的显式表达。

静态图生成器只读取 run/result artifact，不直接读取 raw 数据。

### 15.2 HoloViz 适配器

复杂 dashboard 推荐拆成三层：

```text
artifact_loader
  读取一个明确的 run/result
  校验 schema 和引用
  不依赖可视化框架

dashboard_model
  生成 renderer-neutral tables、series、events 和 filters
  可以独立单测

holoviz_adapter
  Panel / Param / HoloViews / hvPlot / Bokeh
  只负责控件、联动、绘制和服务
```

HoloViz 依赖可以使用独立环境和独立版本锁，不应迫使实验执行环境升级。适配器
接收显式 artifact directory，不搜索 `latest`，也不调用被测系统。

连续曲线可以为显示做动态降采样；失败、投影、告警、限值触发和状态变化等
离散事件必须全量保留。dashboard 应提供无持久服务器的构建检查，以便 CI
验证所有视图和数据关系。

dashboard 中的关键派生量若支撑科学结论，应先成为有 schema、有 hash 的
artifact；不能只存在于浏览器回调或 hover 中。

## 16. 发布合同

Experiment result 与 Analysis result 应实现同一个 Publication Target 合同：

```text
target_kind
target_id
result_id
result_directory
manifest
human_report
index_path
release_tag
```

发布器应：

- 只接受已经晋升的 result；
- 校验 result 目录边界和 manifest schema；
- 拒绝 symlink 和不安全路径；
- 生成确定性归档；
- 生成归档 SHA-256；
- 拒绝静默覆盖已有资产或 tag；
- 创建独立 Release；
- 更新轻量 index；
- 失败时保留可诊断状态。

发布器不应：

- 自动决定哪个 run 科学上值得保留；
- 把 `completed` 自动当成 `reviewed`；
- 隐式选择 `latest`；
- 静默忽略失败或 unavailable artifact；
- 修改 result 内容后仍沿用旧 manifest；
- 把发布成功描述成结论正确。

Release 是大结果的远端归档。Git 中建议只保留：

- 声明、代码和转换定义；
- 人工结论；
- 轻量索引；
- result ID、definition hash、revision、Release URL、archive hash 和时间。

## 17. 完整性与可复现性

可复现不是“同一台机器能再次运行”，而是能够回答：

- 当时运行的 resolved declaration 是什么；
- 输入来自哪里，内容是否相同；
- 哪个 revision 和环境执行了它；
- 哪些 Arm 成功、失败或不可用；
- 指标定义和窗口是什么；
- Analysis 精确消费了哪些 result；
- 发布包是否仍是当时复核的文件。

推荐完整性链：

```text
raw hash
  -> canonical hash
  -> definition hash
  -> run manifest + output hashes
  -> result manifest
  -> archive hash
  -> Release index
  -> analysis source hashes
  -> analysis output hashes
```

dirty revision 不是绝对禁止，但必须作为显式政策：

- 严格证据：dirty 直接阻断；
- 探索证据：允许 dirty，但 manifest 记录差异，报告保留限制；
- 不允许发布器静默把 dirty run 描述成 clean proof。

## 18. 代码放置决策

| 问题 | 放置位置 |
|---|---|
| 是否属于领域稳定 schema？ | 合同层 |
| 是否被两个以上 Experiment 复用？ | 公共适配器或核心能力 |
| 是否只定义某个 Exx 的因素与 Arm？ | Experiment 实例 |
| 是否只生成某个 Exx 的专用审计表？ | Experiment artifact adapter |
| 是否需要读取多个已有 result？ | Analysis 实例 |
| 是否只改变图形交互？ | Visualization adapter |
| 是否改变指标公式或缺失语义？ | 新 metric contract 版本 |
| 是否改变打包介质或远端服务？ | Publication adapter |

提取公共逻辑时，不要让公共层反向引用 Experiment ID。研究实例可以依赖公共
能力，公共能力不能知道某个 Exx/Axx。

## 19. 常见反模式

- **复制旧实验的执行循环。** 通用失败处理和 artifact schema 会逐实验漂移。
- **让核心直接兼容所有 raw 格式。** 来源解释会渗入算法。
- **把代码类名写进架构规范。** 更换框架会迫使重写研究原则。
- **只保存成功 Arm。** 失败选择偏差无法审计。
- **用零值代替 unavailable。** 下游会把缺失当成真实观测。
- **用 prefix 指标排名未完成 Arm。** 完整性被伪装成性能。
- **事后选择 Primary 或窗口。** 容易产生选择性报告。
- **Analysis 使用 `latest`。** 历史结论无法复现。
- **把 summary mean 当独立样本。** 统计单位被重复计数。
- **图或 dashboard 重新实现科学计算。** 表、报告和展示可能不一致。
- **run 完成后覆盖 artifact。** manifest hash 与真实内容失配。
- **发布器自动选择 result。** 人工科学复核边界消失。
- **Release 成功等于结论可信。** 归档完整性和科学正确性被混为一谈。

## 20. 完成清单

### 20.1 新 Experiment

- [ ] 一个精确问题和可证伪假设
- [ ] Raw data 原样保存
- [ ] Canonical 转换有 schema、provenance 和 hash
- [ ] 自变量、控制变量和允许差异完整声明
- [ ] Method 与 Arm ID 稳定
- [ ] Primary、Secondary、Guardrail、Diagnostic 预先确定
- [ ] 窗口、配对键和统计单位明确
- [ ] required Arm 与失败政策明确
- [ ] 声明、输入、执行、指标和 manifest 有测试
- [ ] dry run 证据链闭合
- [ ] 正式 run 未覆盖旧目录
- [ ] failures 与 unavailable 状态完整保留
- [ ] 静态图有 source CSV；HoloViz 有独立数据模型
- [ ] 人工复核已完成
- [ ] 只把选中的 run 晋升到 results
- [ ] 本地归档和 checksum 已检查
- [ ] Release 与轻量 index 已更新

### 20.2 新 Analysis

- [ ] 问题必须通过消费已有证据回答
- [ ] 每个来源固定到具体 result/run
- [ ] 来源 manifest、hash、revision 和 dirty 政策明确
- [ ] 控制变量可比性经过审计
- [ ] 配对键与独立统计单位明确
- [ ] 重复 baseline 没有被当成额外样本
- [ ] unavailable 没有被过滤
- [ ] 收集阶段与决策阶段分离
- [ ] 每张图能回到确定的 source table
- [ ] 报告包含结论、证据、限制和复现方式
- [ ] analysis manifest 固定来源与输出 hash
- [ ] Analysis run 经人工复核后晋升
- [ ] Analysis result 可独立归档和发布

### 20.3 新框架适配

- [ ] 架构文档中没有具体包名、类名或固定 CLI
- [ ] 项目实现映射单独维护
- [ ] Data normalizer 端口已实现
- [ ] Definition validator 端口已实现
- [ ] Arm executor 与 Metric evaluator 已实现
- [ ] Experiment/Analysis manifest 使用稳定 schema
- [ ] run/result 生命周期一致
- [ ] Renderer 只读取 artifact
- [ ] Publisher 同时支持 Experiment 与 Analysis result
- [ ] 旧 result 不依赖当前进程的私有对象才能读取

## 21. 项目实现映射应如何编写

将本架构落到具体仓库时，另建一份短文档，只回答：

| 抽象角色 | 当前项目实现 |
|---|---|
| Canonical schema | 哪份领域合同 |
| Definition format | YAML、JSON、代码或数据库 |
| Definition validator | 哪个入口 |
| Experiment executor | 哪个入口 |
| Analysis collector | 哪个入口 |
| Artifact root | 实际路径 |
| Static renderer | 实际工具 |
| HoloViz adapter | 实际入口 |
| Publisher | 实际入口 |
| Release index | 实际路径和 schema |

项目 README 放安装和常用命令；Experiment/Analysis README 放本实例的运行与
读取方式；领域合同放具体字段；实现映射放模块与命令。本文件只保留跨项目稳定
的架构语义。

这样更换执行框架、目录实现或发布工具时，只需要更新适配器和实现映射，不需要
重写实验设计、证据合同和生命周期原则。
