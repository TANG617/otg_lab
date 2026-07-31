# E13 — Joint P/PV/PVA stop-and-go comparison

E13 reruns the E07 operational P-only baseline, a scheduled `P[k+1]`
baseline, five PV finite-difference methods, and five matched PVA methods on
the complete E07 velocity × A/J matrix.

The primary window is the mature constant-velocity interval
`t = 0.5–2.5 s`. The key outcomes are exact-profile rest-to-rest pulse
fraction and stop-and-go event rate. Position RMSE, lag, velocity ripple,
profile exactness, and execution guardrails remain visible.

`analysis/joint_stop_go_surface.csv` is the source for A05. It reports the
reduction of each arm relative to the operational P-only baseline. Because the
mature reference has zero acceleration, matched PV/PVA equivalence is an
expected negative control: the demonstrated mechanism is the velocity target,
not an acceleration effect.

Run:

```bash
uv run otg-lab run E13
```
