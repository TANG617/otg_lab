# E14 — Fine PV/PVA VAJ sensitivity

E14 refines the recorded-task constraint search to a full three-dimensional
grid for matched Future-O1 PV and PVA targets:

- `V`: 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.1 rad/s
- `A`: 2.0, 3.0, 4.1, 5.0, 6.0, 7.0, 7.5, 8.2 rad/s²
- `J`: 41, 100, 200, 400, 800, 1200, 1600, 2400, 3200, 4000 rad/s³

This is 640 cases per target-component family and 1,280 total runs on
`recorded_tasks_simplified_with_velocity_limit`.

Each new run retains both the 10 ms-grid `lag_s` and the local quadratic
`lag_subsample_s` sensitivity estimate. Existing compact aggregates that
predate this field remain valid for integer-lag selection; their full
sub-sample Pareto surface cannot be reconstructed without replaying the
missing traces.

Eligibility requires complete execution, exact constraint guardrails, exact
projection reconstruction, and admissible executable targets. Deadline is
reported but not used as a hard scientific gate. The best tested setting is
the eligible raw-time RMSE minimum. A 1% near-optimal set and its
limit-efficient nondominated subset are also reported. If the minimum lies on
any grid edge, the result is explicitly marked `boundary_censored`.

Low-limit arms are diagnostic rather than runner-blocking. If ordinary Ruckig
cannot complete one tested setting, its failure cycle remains in the shard
artifacts and its surface row is `eligible=false` with no prefix RMSE. Other
settings and the aggregate remain valid.

```bash
uv run otg-lab run E14 --no-figures
```

For the full grid, prefer the bounded-memory runner documented in
`docs/experiments.md`; it recycles worker processes after every wave and can
resume completed shards with `--resume --batch-root <existing-batch>`.
On a memory- or disk-constrained machine, `finish_compact.py` preserves
completed surface rows, optionally prunes batch-local trace intermediates, and
isolates every remaining native call in its own subprocess.
