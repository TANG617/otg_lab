# A06 — Fine PV/PVA VAJ selection

A06 consumes E14's aggregated 1,280-arm full grid. It selects the raw-time
RMSE minimum for A04's PV Future-O1 method, reports the matched PVA surface,
constructs a 1% near-optimal limit-efficient frontier, and flags any grid-edge
optimum as censored.

```bash
uv run python analyses/A06_pv_pva_vaj_fine_selection/analyze.py --check
uv run python analyses/A06_pv_pva_vaj_fine_selection/analyze.py
```
