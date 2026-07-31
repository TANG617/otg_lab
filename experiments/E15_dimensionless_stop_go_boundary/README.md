# E15 — Dimensionless stop-and-go boundary

E15 independently varies `dt`, acceleration, jerk, absolute scale, direction,
and reference speed.  It covers both branches of the one-cycle rest-to-rest
critical velocity and evaluates a locked Sobol holdout by empirical bisection.

```bash
uv run otg-lab run E15 --no-figures
```

For a fast structural check:

```bash
OTG_CONFIRMATORY_PROFILE=smoke uv run otg-lab run E15 --no-figures
```

Primary artifacts are `boundary_grid.csv`, `holdout_thresholds.csv`, and
`acceptance.json`.  Deterministic grid cells are not treated as statistical
replicates.

The exact `q=1, rho=1` coordinate is the intersection of the dynamics-regime
seam and the behavioral boundary. It remains a diagnostic native-solver stress
point rather than a classifier for either side. Any failure there stays visible
in `failures.csv`; every other grid coordinate and every Sobol holdout remains
required.
