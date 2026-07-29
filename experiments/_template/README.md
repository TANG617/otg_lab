# __EXPERIMENT_TITLE__

由 `otg-lab new-experiment __EXPERIMENT_ID__ __EXPERIMENT_SLUG__` 创建。

编辑 `experiment.py` 中的研究问题、变量、控制条件、规范 CSV 输入、方法矩阵、
指标角色、评估窗口和比较关系。实验目录不得重新实现 CSV loader、跟踪循环或
指标公式。

运行产物默认写入本目录的 `runs/<timestamp>__<spec_hash>/`；`runs/` 不进入
版本控制。它包含 manifest、方法原始记录和分析产物，是完整但可丢弃的运行
工作区。

确认值得长期保留的 run 使用 `otg-lab publish-run <run-directory>` 提升到
本目录的 `results/<run-id>/`。提升结果只保留 manifest 和完整 `analysis/`
树，并在 `results/index.csv` 建立轻量索引；GitHub Release 也只上传该结果
压缩包及其 SHA-256，不归档整个 `runs/`。
