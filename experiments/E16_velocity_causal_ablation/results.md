# E16_velocity_causal_ablation

## 2026-07-31 local confirmatory execution

- Run: `20260731T123148.469169Z__9190778c7d47`（尚未复制到可发布 `results/`）
- 1,260/1,260 arms completed.
- Conditioned causal Future-O1 PV 与 oracle PV 的中位 exact ripple 均为 0；
  P-only 成功复现 stop-go。
- 错误符号/随机符号 controls 的中位 ripple 为 3.2007229859。
- 无 P-lookahead 或 minimum-duration 档位在所有 primary cells 上匹配 exact PV
  profile；`2dt` minimum duration 可显著削弱 stop-go，但仍有残余 ripple，
  不能称为 matched PV 等价方案。
- Raw Future-O1 未通过跨分支 P95 判据；浮点级 velocity deadband 是方法合同的
  必要组成。
- `acceptance.json: accepted=true`。
