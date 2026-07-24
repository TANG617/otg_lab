# Two-CSV comparison artifact index

The primary reader artifact is `report.html`, generated from the validated
canonical payload in `artifact.json`.

Supporting evidence:

- `metric_comparison.csv`: exact current/new aggregate values and deltas.
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
.venv/bin/python scripts/compare_csv_pvaj_tracking.py
.venv/bin/python scripts/build_csv_pvaj_tracking_report.py
.venv/bin/python scripts/validate_csv_pvaj_tracking_comparison.py
```

This is development-only descriptive evidence. It does not modify frozen
V3/V4 confirmation artifacts or claims.
