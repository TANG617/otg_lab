# __EXPERIMENT_TITLE__

由 `otg-lab new-experiment __EXPERIMENT_ID__ __EXPERIMENT_SLUG__` 创建。

编辑 `experiment.py` 中的研究问题、变量、控制条件、规范 CSV 输入、方法矩阵、
指标角色、评估窗口和比较关系。实验目录不得重新实现 CSV loader、跟踪循环或
指标公式。

运行产物默认写入本目录的 `runs/<timestamp>__<spec_hash>/`；`runs/` 不进入
版本控制。它包含 manifest、方法原始记录和分析产物，是完整但可丢弃的运行
工作区。

确认值得长期保留的 run，手动复制到本目录的 `results/<run-id>/`，再执行
`otg-lab publish-run results/<run-id>`。命令会原样打包所选目录，不检查当前
工作区或 manifest 的 dirty 状态；GitHub Release 只上传结果 ZIP 及其
SHA-256，并更新 `results/index.csv`。`results/<run-id>/` 被 Git 忽略；
根目录的 `results.md` 只预置实验名标题，正文留空供人工记录。仓库级
`otg-lab publish-results` 会扫描所有实验尚未发布的结果，并用一个 RunBuoy
Run 展示批次进度。
