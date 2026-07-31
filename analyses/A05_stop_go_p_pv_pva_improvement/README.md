# A05 — Stop-and-go P/PV/PVA improvement

A05 consumes E13's joint 960-arm matrix. It evaluates PV/PVA only on
coordinates where the operational E07 P baseline exhibits a rest-to-rest
pulse, then checks matched PV/PVA equivalence over the full mature
constant-velocity surface.

```bash
uv run python analyses/A05_stop_go_p_pv_pva_improvement/analyze.py --check
uv run python analyses/A05_stop_go_p_pv_pva_improvement/analyze.py
```
