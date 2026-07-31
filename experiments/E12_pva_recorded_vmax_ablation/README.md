# E12 — Recorded PVA runtime-Vmax ablation

E12 is the controlled experiment behind A03. It reruns the scheduled
`P[k+1]` baseline and the five E04 causal PVA finite-difference methods over:

- `recorded_tasks_original_no_velocity_limit`
- `recorded_tasks_simplified_no_velocity_limit`
- `recorded_tasks_simplified_with_velocity_limit`

Each method/input pair is executed at runtime `Vmax = 4.1` and `10 rad/s`,
while `Amax = 8.2 rad/s²` and `Jmax = 4000 rad/s³` remain fixed. This separates
the input's acquisition condition from the limit applied during replay.

The primary outcome is raw-time position RMSE from `t = 0.04 s`. The generated
`analysis/vmax_ablation.csv` reports PVA/P ratios and decomposes target
projection into raw velocity clipping, raw acceleration clipping, and the
directional stopping-envelope adjustment. Cause counts are non-exclusive.
`analysis/vmax_interactions.csv` contains the predeclared log-ratio interaction
used by A03.

Run from the repository root:

```bash
uv run otg-lab run E12
```
