# __EXPERIMENT_TITLE__

由 `otg-lab new-experiment __EXPERIMENT_ID__ __EXPERIMENT_SLUG__` 创建。

编辑 `experiment.py` 中的研究问题、变量、控制条件、规范 CSV 输入、方法矩阵、
指标角色、评估窗口和比较关系。实验目录不得重新实现 CSV loader、跟踪循环或
指标公式。

运行产物默认写入本目录的 `runs/<timestamp>__<spec_hash>/`；`runs/` 不进入
版本控制。它包含完整运行记录，因此统一使用 `runs`，不另建含义重叠的
`results`。
