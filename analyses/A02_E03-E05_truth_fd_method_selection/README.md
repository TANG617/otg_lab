# A02 — E03–E05 Truth 与有限差分方法选型

A02 固定消费 E03–E05 的已发布 result，不重新运行 follower：

- E04 是 P baseline、PVA truth 和五种 PVA finite-difference 方法的唯一正式
  排名来源；
- E03 只复核 E04 内重复的 P/PVA truth，不增加样本量；
- E05 只作 PV truth 分量控制，不进入 FD 排名；
- E06 不在范围内，因此 A02 不声称完成 PV FD 方法选型。

决策先应用完整性、因果性、三轨迹 RMSE 与 full-overlap guardrail 硬门槛，
再在 worst-case truth gap ratio 和 worst-case absolute observed lag 上构造
Pareto 前沿。0/10/20 ms 场景在预算内选择最小 worst-case gap 的方法，不使用
任意加权总分。

只校验且不写输出：

```bash
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py --check
```

生成中文报告、CSV、PNG/SVG 和确定性 provenance：

```bash
uv run python analyses/A02_E03-E05_truth_fd_method_selection/analyze.py
```

来源 manifest 均标记 `git.dirty=true`，报告必须保留这一复现限制。
