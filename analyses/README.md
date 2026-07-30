# Cross-experiment analyses

`analyses/` 保存跨实验整理与对比，不占用 E-series 实验编号。编号使用
`A01`、`A02`……；目录名同时写出来源范围和主题，例如：

```text
analyses/A01_E03-E06_pv_pva_comparison/
```

每个分析目录分成三层：

```text
analysis.yaml   # 问题、固定来源、来源因子、筛选条件
analyze.py      # 统一入口，只做可审计的数据收集
work/           # 可重建的中间产物，不进入版本控制
results/        # 人工复核后保留的分析结果
RESULTS.md      # 结论、限制和复现信息
```

## 边界

- E-series 回答一个独立研究问题，并产生自己的 run。
- A-series 不重新运行 follower，也不修改来源实验。
- 每个来源必须固定到具体 run/result 目录，禁止使用隐式的 `latest`。
- 跨实验计算以 `trajectory_metrics.csv` 的逐输入 tidy row 为基础；
  `method_summary.csv` 只适合展示，不能把已聚合均值当成独立样本。
- 内部 P baseline 是配对坐标。同一 baseline 出现在多个实验中时，不得把它们
  当成多份独立观测。
- `work/` 可以随时重建；只有经过检查的表、图和报告才进入 `results/`。

## 从模板创建

```bash
cp -R analyses/_template analyses/A02_topic
```

然后替换模板占位符，在 `analysis.yaml` 中固定每个来源目录，并运行：

```bash
uv run python analyses/A02_topic/analyze.py --check
uv run python analyses/A02_topic/analyze.py
```

第一条命令只校验配置、manifest 和必需 artifact；第二条命令写入 `work/`：

```text
source_inventory.csv
combined_trajectory_metrics.csv
combined_comparisons.csv
provenance.json
```

这些文件是后续统计、制图和写报告的统一输入，不代表分析已经得出结论。
