# A04 — Recorded PV/PVA finite-difference selection

A04 combines the current E11 PV transfer with E12's controlled PVA
`Vmax=4.1` arms. It evaluates the Cartesian product of PV/PVA target
components and the five causal finite-difference stencils under one recorded
input and one VAJ setting.

```bash
uv run python analyses/A04_recorded_pv_pva_fd_selection/analyze.py --check
uv run python analyses/A04_recorded_pv_pva_fd_selection/analyze.py
```
