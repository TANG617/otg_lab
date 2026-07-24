# V4 preregistered hypotheses

Status: designed before V4 test visibility. Statistical unit: whole trajectory.
The authoritative machine-readable definitions are in
`V4_STATISTICAL_DESIGN.json`.

## Primary hypothesis

For all 120 locked test trajectories, let `RMSE_P(i)` and `RMSE_PVA(i)` be
position RMSE from `one_step_governed_p_direct` and
`one_step_governed_pva_direct`, respectively. Both are evaluated against
analytic truth at `command_time`; `target[k]` produces `command[k+1]`.

The primary estimand is the ratio of means

```text
R = (mean_i(RMSE_P(i)) - mean_i(RMSE_PVA(i))) / mean_i(RMSE_P(i)).
```

The directional hypotheses are `H0: R <= 0` and `H1: R > 0`. Inference is a
whole-trajectory paired percentile bootstrap with exactly 10,000 resamples,
fixed seed `2026072301`, and a two-sided 95% interval. Every manifest test ID
must be present in both methods; no trajectory or failed/harmful unit may be
deleted.

The nested result flags are:

- `confirmed_positive`: lower bound > 0;
- `practically_material`: confirmed positive and point estimate >= 0.05;
- `strongly_material`: lower bound >= 0.05;
- `inconclusive`: interval contains 0, including a bound equal to 0;
- `confirmed_harmful`: upper bound < 0.

The single machine label uses the precedence defined in the statistical
design. A valid nonpositive result is a formal negative result, not an
experiment failure.

## Guardrail hypotheses

For PVA versus P, max-error noninferiority passes only when the upper 95%
bootstrap bound of

```text
(mean(max_error_PVA) - mean(max_error_P)) / mean(max_error_P)
```

is at most 0.05. Lag noninferiority passes only when the upper 95% bootstrap
bound of `mean(lag_PVA - lag_P)` is at most 0.01 s. Seed `2026072303` is fixed
for these paired 10,000-resample intervals. These gates do not change the
primary RMSE value. Both must pass before using wording that improvement occurs
without material lag or peak-error degradation.

## Secondary hypotheses

The preregistered secondary family is:

- S1: PV versus P position RMSE;
- S2: PVA versus PV position RMSE;
- S3: PVA versus P position maximum absolute error;
- S4: PVA versus P lag;
- S5: unshielded ordinary-Ruckig predicted PVA versus predicted P position
  RMSE, contextual only.

Each reports absolute and relative difference, Cohen `dz`, a two-sided paired
t-test p-value, Holm-adjusted p-value over S1-S5, a paired 10,000-resample
percentile interval, and harmful-trajectory rate. Base bootstrap seed is
`2026072302`; comparison seeds follow S1-S5 order. An incomplete ordinary
Ruckig pair is `unavailable`, with the full attempted/completed/failed
denominator retained; complete-case inference is forbidden.

## Preregistered subgroups

All subgroup analyses are secondary:

- each of the six reference families (expected n=20 each);
- each demand stratum `low`, `medium`, `high`, `near_limit` (expected n=30
  each);
- acceleration-active: demand in `{high, near_limit}` and family in
  `{piecewise_constant_jerk, stop_and_go, rapid_reversal,
  boundary_grazing}` (expected n=40).

Subgroup membership is determined only by manifest/truth metadata. Each reports
count, effect, interval, and harmful rate, plus family/demand heterogeneity and
the worst-family effect. No post-test subgroup is allowed, and a favorable
subgroup cannot override an adverse or inconclusive overall result.

## Interpretation boundary for H=0

The locked prediction horizon is 0 ms because V4 isolates the independent
value of target components under identical upstream information and an
identical follower. It does not re-test whether future-position prediction is
valuable. Existing Phase A oracle and other prior evidence retain responsibility
for future-reference timing. V4 validation and test may not select another
estimator, predictor, horizon, method, target mode, or threshold.

Oracle rows are offline, noncausal, nondeployable diagnostics and never enter a
primary claim or selection. Cross-architecture and ordinary-Ruckig comparisons
are contextual secondary evidence only.
