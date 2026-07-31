# Cross-experiment analyses

`analyses/` 保存跨实验整理与对比，不占用 E-series 实验编号。编号使用
`A01`、`A02`……；目录名同时写出来源范围和主题，例如：

```text
analyses/A01_E03-E06_pv_pva_comparison/
```

每个分析目录采用与 E-series 对称的 run/result 分层：

```text
analysis.yaml            # 问题、固定来源、来源因子、筛选条件
analyze.py               # 校验、派生计算、制图的统一入口
runs/<run-id>/           # 一次完整分析；包含 work、表、图、manifest、RESULTS
results/<run-id>/        # 人工复核后保留的 analysis run
results/index.csv        # 发布索引
RESULTS.md               # 当前选型结论的轻量镜像
```

## 边界

- E-series 回答一个独立研究问题，并产生自己的 run。
- A-series 不重新运行 follower，也不修改来源实验。
- 每个来源必须固定到具体 run/result 目录，禁止使用隐式的 `latest`。
- 跨实验计算以 `trajectory_metrics.csv` 的逐输入 tidy row 为基础；
  `method_summary.csv` 只适合展示，不能把已聚合均值当成独立样本。
- 内部 P baseline 是配对坐标。同一 baseline 出现在多个实验中时，不得把它们
  当成多份独立观测。
- `runs/<run-id>/work/` 是该次分析的固定收集层，和最终表、图一起自包含。
- `runs/`、`results/<run-id>/` 不进入 Git；根 `RESULTS.md` 和
  `results/index.csv` 作为轻量记录进入版本控制。

## 从模板创建

```bash
cp -R analyses/_template analyses/A02_topic
```

然后替换模板占位符，在 `analysis.yaml` 中固定每个来源目录，并运行：

```bash
uv run python analyses/A02_topic/analyze.py --check
uv run python analyses/A02_topic/analyze.py
```

第一条命令只校验配置、manifest 和必需 artifact；第二条命令创建
`runs/<analysis-run-id>/`，其中 `work/` 包含：

```text
source_inventory.csv
combined_trajectory_metrics.csv
combined_comparisons.csv
provenance.json
```

这些文件是该 analysis run 内统计、制图和写报告的统一输入。run 通过人工复核
后，按与 E-series 相同的方式复制到 results：

```bash
cp -R \
  analyses/A02_topic/runs/<analysis-run-id> \
  analyses/A02_topic/results/<analysis-run-id>
```

## 发布结果

单独发布一个完成的分析：

```bash
uv run otg-lab publish-analysis \
  analyses/A01_E03-E06_pv_pva_comparison/results/<analysis-run-id>
```

Release 包含所选 result 内自包含的 `RESULTS.md`、`work/`、manifest、表和图；
不包含父目录的轻量 `index.csv` 或 `.gitkeep`。仓库级
`uv run otg-lab publish-results` 会把尚未发布的 E-series 与 A-series 结果
放在同一个 RunBuoy 批次中处理。
