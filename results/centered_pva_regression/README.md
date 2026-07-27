# Centered-difference PVA regression

This directory reproduces the production-semantics centered-difference target:
arrival `k` emits P/V/A evaluated at `k-1`, V/A are independently hard-clamped,
and the target is passed to the ordinary-Ruckig runner.

Primary outputs:

- `report.html`: standalone Chinese technical report.
- `artifact.json`: validated canonical Data Analytics report payload.
- `artifact_package.tar.gz`: exported report runtime package.
- `tracking_metrics.csv`: same-reference method metrics.
- `mechanism_decomposition.csv`: ordered delay/derivative/clamp ablations.
- `timestamp_sensitivity.csv`: CSV-timestamp derivative sensitivity.
- `validation.json`: independent numeric and structural validation.

Reproduce from the repository root:

```bash
uv run --frozen python scripts/analyze_centered_pva_regression.py
uv run --frozen python scripts/build_centered_pva_regression_report.py
uv run --frozen python scripts/validate_centered_pva_regression.py   --mcp-validation-passed
```

The final validation flag records that `artifact.json` was separately accepted
by the Data Analytics artifact validator. CSV timestamp is an unverified proxy
for `JointState.header.stamp`, not part of the primary fixed-10-ms result.
