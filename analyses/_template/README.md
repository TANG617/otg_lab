# __ANALYSIS_TITLE__

## 问题

填写一个精确的跨实验问题，并说明为什么必须跨实验回答。

## 来源矩阵

| source_id | 实验 | 固定 run/result | 分析角色 |
|---|---|---|---|
| `example_source` | E00 | `<run-id>` | 替换 |

来源必须在 `analysis.yaml` 中固定到具体目录。优先使用人工保留的
`experiments/<experiment>/results/<run-id>`；若探索阶段临时引用 `runs/`，
必须在最终结论前改为稳定的 result，或明确记录这一限制。

## 配对与统计单位

说明配对键、主要窗口、primary metric、baseline 和 guardrail。默认以
`input_id` 为统计单位，不把 method summary 的聚合均值当成独立样本。

## 运行

```bash
uv run python analyses/__ANALYSIS_DIRECTORY__/analyze.py --check
uv run python analyses/__ANALYSIS_DIRECTORY__/analyze.py
```

收集器只生成来源清单、合并 tidy tables 和 provenance。统计计算与制图应读取
`work/combined_*.csv`，不得绕开 `analysis.yaml` 重新隐式选择来源。

## 完成条件

- 所有来源固定且 manifest 校验通过；
- 跨实验控制变量经过逐项审计；
- 主要结论来自逐输入配对，而不是四个实验 summary mean 的横向相减；
- baseline 重复项没有被当成独立样本；
- 结论、限制和复现命令写入 `RESULTS.md`；
- 经复核的最终表和图复制到 `results/`。

`results/` 的生成文件由 Git 忽略，通过
`otg-lab publish-analysis analyses/__ANALYSIS_DIRECTORY__/results` 发布到
GitHub Release；`RESULTS.md` 与 `results/index.csv` 保留在 Git 中。
