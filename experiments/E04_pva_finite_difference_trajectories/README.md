# E04 — Causal PVA finite-difference tracking

E04 compares one common scheduled `P[k+1]` baseline, a noncausal PVA truth
ceiling, and five causal finite-difference PVA methods:

- endpoint backward O1 and O2 estimators at `k` (one-sample target age);
- delayed centered O2 estimator at `k−1` (two-sample target age);
- backward O1 and O2 predictors for `k+1`, using only positions through `k`.

Every P/V/A target is internally time-coherent. Startup rows use zero V/A
until the full stencil is available and are excluded from pipeline truth-error
metrics. The primary window is `0.04–3.00 s`.

```bash
uv run otg-lab run E04
```

Scientific acceptance requires every candidate to beat the common P baseline
on all three analytic trajectories without guardrail regression.

The run also writes `analysis/lag_comparison.csv` and
`analysis/figures/lag_vs_p_and_truth.{png,svg}`. The lag figure includes the
E03-equivalent PVA truth target and P-only baseline beside all five causal
finite-difference methods.

The derivation, assumptions, truncation-error orders, and E04-specific target
semantics of the one-step O1/O2 extrapolators are documented in
[`INTERPOLATION_METHODS.md`](INTERPOLATION_METHODS.md).
