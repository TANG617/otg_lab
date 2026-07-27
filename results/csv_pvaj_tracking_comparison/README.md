# Three-CSV comparison artifact index

The primary reader artifact is `report.html`, generated from the validated
canonical payload in `artifact.json`.

Supporting evidence:

- `metric_comparison.csv`: exact current/no-limit/velocity-limit values and
  all three pairwise percentage changes.
- `raw_pvaj_metrics.csv`: max, P99, and RMS fixed-grid PVAJ.
- `tracking_metrics.csv`: full target-state method metrics.
- `window_diagnostics.csv`: non-overlapping one-second demand/error rows.
- `raw_pvaj_comparison.{png,svg}`: faceted raw PVAJ traces.
- `tracking_trajectory_comparison.{png,svg}`: faceted P-only tracking traces.
- `run.json`: input hashes, package versions, conventions, and conclusion state.
- `report_source.sqlite`: queryable snapshot used by report-native charts/cards.
- `source_notes.json`: report-structure mapping, chart map, and omissions.
- `validation.json`: independent PVAJ/tracking recomputation and claim QA.

Reproduce the evidence and canonical report payload:

```bash
uv run --frozen python scripts/compare_csv_pvaj_tracking.py
uv run --frozen python scripts/build_csv_pvaj_tracking_report.py
uv run --frozen python scripts/validate_csv_pvaj_tracking_comparison.py
```

The primary comparison is velocity-limit versus no-velocity-limit, with the
current CSV retained as context. This is development-only descriptive
evidence: the lower VAJ demand is associated with better tracking, but the
recordings are not paired on duration and geometry. It does not modify frozen
V3/V4 confirmation artifacts or claims.
