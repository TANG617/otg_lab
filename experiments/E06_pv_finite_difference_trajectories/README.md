# E06 — Causal PV finite-difference tracking

E06 uses the same baseline, truth ceiling, and five causal difference methods
as E04, but the target builder supplies only P/V and explicitly zeros A.
The differentiators still calculate acceleration so its estimation error
remains auditable.

The primary window is `0.04–3.00 s`; every candidate must improve raw-time
position RMSE on all three analytic trajectories without guardrail regression.

```bash
uv run otg-lab run E06
```

`analysis/lag_comparison.csv` records absolute lag plus lag deltas against
both P-only and the E05-equivalent PV truth target. The same comparison is
rendered as `analysis/figures/lag_vs_p_and_truth.{png,svg}`.
