# E03 — PVA truth trajectory tracking

E03 compares a scheduled `P[k+1]` target with zero derivatives against the
offline, noncausal analytic `[P,V,A][k+1]` target.

Inputs are `quadratic_with_extremum`, `cubic`, and `sine`. All methods use
`dt=10 ms`, the same V/A/J limits, no governor, and ordinary unshielded
Ruckig. The primary readout is raw-time position RMSE over `0.04–3.00 s`;
lag alignment is diagnostic only.

Run from the repository root:

```bash
uv run otg-lab run E03
```

The run writes the standard trace/profile/metric artifacts plus
`analysis/acceptance.csv` and `analysis/figures/rmse_ratio_vs_p.png/.svg`.
