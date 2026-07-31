# E16 — Velocity causal ablation

E16 holds scheduled position, timing, constraints, and follower constant while
manipulating the target velocity.  It includes velocity-fraction dose controls,
wrong/random-sign controls, position lookahead, and minimum-duration baselines.

The primary interpretation uses exact velocity ripple and minimum within-cycle
speed together with rest-to-rest pulse incidence, so a non-zero terminal
velocity cannot make the experiment pass by definition alone.

```bash
uv run otg-lab run E16 --no-figures
OTG_CONFIRMATORY_PROFILE=smoke uv run otg-lab run E16 --no-figures
```

