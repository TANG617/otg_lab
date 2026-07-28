# E05 — PV truth trajectory tracking

E05 compares the common scheduled `P[k+1]` baseline with the offline,
noncausal analytic `[P,V][k+1]` target. Acceleration is explicitly zeroed at
the PV target builder.

The three analytic inputs, timing, motion limits, follower, main evaluation
window, metrics, and acceptance artifacts match E03.

```bash
uv run otg-lab run E05
```
