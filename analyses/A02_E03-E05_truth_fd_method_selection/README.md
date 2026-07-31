# A02 — E03–E05 解析轨迹 Truth 与有限差分正确性验证

该分析只用于中间公式、因果性和 guardrail 正确性验证，不参与上线选型。
上线 PV/PVA 与差分对比只使用
`recorded_tasks_simplified_with_velocity_limit`。

A02 固定消费 E03–E05 的已发布 result，不重新运行 follower：

- E04 是 P baseline、PVA truth 和五种 PVA finite-difference 方法的解析
  场景化 readout 来源；
- E03 只复核 E04 内重复的 P/PVA truth，不增加样本量；
- E05 只作 PV truth 分量控制，不进入 FD 排名；
- E06 不在范围内，因此 A02 不声称完成 PV FD 方法选型。

决策先应用完整性、因果性、三轨迹 RMSE 与 full-overlap guardrail 硬门槛，
再在 worst-case truth gap ratio 和 worst-case absolute observed lag 上构造
Pareto 前沿。0/10/20 ms 场景仅选出解析验证代表，不使用任意加权总分，也不
外推为 recorded trajectory 的部署选择。

只校验且不写输出：

```bash
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py --check
```

生成中文报告、CSV、PNG/SVG 和确定性 provenance：

```bash
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py
```

来源 manifest 均标记 `git.dirty=true`，报告必须保留这一复现限制。
