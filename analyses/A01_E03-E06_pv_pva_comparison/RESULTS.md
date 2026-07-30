# A01 — E03–E06 P/PV/PVA 结果对比

## 结论

待完成配对分析后填写。

## 主要证据

待填写：

- truth：PVA vs PV；
- finite difference：同 stencil 的 PVA vs PV；
- 各方法相对统一 P baseline 的 raw-time position RMSE ratio；
- guardrail 与 lag diagnostics。

## 限制

- 当前四个来源 manifest 均记录 `git.dirty=true`；
- truth 方法是 offline、noncausal ceiling，不能直接作为在线部署结论。

## 复现

```bash
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py --check
uv run python analyses/A01_E03-E06_pv_pva_comparison/analyze.py
```
