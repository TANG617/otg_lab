# E17 — Causal PV robustness and frozen holdout

E17 separates five development seeds from locked confirmatory seeds.  The
development split selects one causal PV implementation by a predeclared
ripple/RMSE/guardrail rule; the selected method is then evaluated under
position noise, quantization, timestamp jitter, delay, and dropouts.

The experiment also evaluates 20 deterministic synthetic holdout trajectories
and replays the existing recorded task with its raw irregular timestamps.  The
recorded replay remains a diagnostic because all current recorded files derive
from the same task acquisition.

Acceptance is condition-wise as well as aggregate: each declared work-envelope
condition must pass its own ripple and RMSE thresholds, and every synthetic
trajectory must improve ripple without an RMSE or execution-guardrail
regression. The recorded replay is never counted as an independent task
holdout.

```bash
uv run otg-lab run E17 --no-figures
OTG_CONFIRMATORY_PROFILE=smoke uv run otg-lab run E17 --no-figures
```

Statistical units are parameter/trajectory × frozen seed, never control cycles.
