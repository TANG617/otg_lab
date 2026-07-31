# E15_dimensionless_stop_go_boundary

## 2026-07-31 local confirmatory execution

- Run: `20260731T122024.548365Z__f1b47bf53809`（尚未复制到可发布 `results/`）
- Required grid: 2,144/2,144 completed; Sobol holdout: 128/128 completed.
- 两侧 classification 全部通过；`max |rho_hat - 1| = 0.0001953125`
  （0.0195%）。
- `q=1, rho=1` 的 16/16 exact-seam stress cells 在 Ruckig 0.17.3 中失败，
  作为 diagnostic 原样保留；required failures 为 0。
- `acceptance.json: accepted=true`。
