# A03 — Recorded PVA velocity-limit attribution

A03 consumes one exact E12 run and answers two distinct questions:

1. Is PVA worse than scheduled P on the original recorded curve?
2. Does intervening on runtime `Vmax` change that PVA/P relationship?

The analysis does not treat `with_velocity_limit` in a filename as a causal
variable. Instead, it uses the within-input `Vmax=4.1` versus `Vmax=10`
intervention and audits velocity clipping, acceleration clipping, and the
stopping envelope separately.

This is an attribution diagnostic on the original recorded waveform only. It
is not eligible for deployment PV/PVA ranking or benefit claims; those use only
`recorded_tasks_simplified_with_velocity_limit`.

```bash
uv run python analyses/A03_recorded_pva_velocity_limit_attribution/analyze.py --check
uv run python analyses/A03_recorded_pva_velocity_limit_attribution/analyze.py
```
