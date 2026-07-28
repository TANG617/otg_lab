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

运行：

```bash
otg-lab run E01_topic
```

运行产物位于
`experiments/<experiment-directory>/runs/<timestamp>__<spec_hash>/`。
使用 `runs` 是因为这里保存 manifest、输入副本、每种方法的
command/trace/profile/status，以及 tidy 指标、比较、失败表、报告和图等
完整运行记录，而不只是最终 `results`。单个方法失败不阻断其他方法；必需
方法失败会使命令最终返回非零。

CI 或临时试跑可以使用 `--runs-root <path>` 覆盖该实验的 run 容器目录。

新实验可用以下命令生成：

```bash
otg-lab new-experiment E02 estimator_ablation
```
