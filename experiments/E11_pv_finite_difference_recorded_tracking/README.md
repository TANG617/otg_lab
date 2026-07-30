# E11 — Recorded-task PV finite-difference transfer

E11 is the PV counterpart of E08. It transfers the five causal
finite-difference methods from E06 to the same recorded position waveform and
compares them with the scheduled `P[k+1]` baseline.

The important ablation is that the target contains no acceleration component:
the scheduled target builder passes P/V and explicitly writes `A=0` on every
cycle. The finite-difference estimators and predictors may still calculate
acceleration internally, but it is diagnostic state only and is never sent to
Ruckig as target acceleration.

## Input and execution

The required input is:

```text
data/trajectories/recorded_tasks_simplified_with_velocity_limit.csv
```

It contains 7,673 recorded position samples on a fixed 10 ms grid. No velocity,
acceleration, or jerk truth is synthesized. The raw elapsed-time and timestamp
columns are provenance only and their sampling jitter is not replayed.

All methods use:

```text
dt = prediction horizon = minimum duration = 0.01 s
V/A/J limits = 4.1 / 8.2 / 4000
measurement = position only
target = PV with A fixed to zero
governor = configured-limit projection
follower = ordinary unshielded Ruckig
failure policy = record and continue
```

The acceleration limit remains a Ruckig motion constraint even though target
acceleration is absent. The governor preserves position, conditions velocity
when needed, and passes the zero target acceleration through unchanged.

## Method matrix

| Method | Derivative source | Represented target | Age at command |
|---|---|---:|---:|
| `p_kp1_baseline` | zero V/A | `P[k+1]` | 0 samples |
| `pv_est_backward_o1_k` | backward O1 estimator | `PV[k], A=0` | 1 sample |
| `pv_est_backward_o2_k` | backward O2 estimator | `PV[k], A=0` | 1 sample |
| `pv_est_centered_o2_km1` | delayed centered O2 estimator | `PV[k−1], A=0` | 2 samples |
| `pv_pred_backward_o1_kp1` | future backward O1 predictor | `PV[k+1], A=0` | 0 samples |
| `pv_pred_backward_o2_kp1` | future backward O2 predictor | `PV[k+1], A=0` | 0 samples |

There is no `pv_truth_kp1` arm because the recorded input has no derivative
truth.

## Metrics and artifacts

The primary metric is raw-time `position_rmse` from `t=0.04 s` through the end
of the waveform. A candidate passes only if it completes the full recording,
improves RMSE versus `p_kp1_baseline`, and introduces no declared guardrail
regression. Prefix RMSE is never used for an incomplete run.

In addition to the standard run artifacts, E11 writes:

```text
analysis/
  raw_target_scan.csv
  raw_target_feasibility.csv
  acceptance.csv
  acceptance_summary.md
  figures/
    recorded_position_tracking.png/.svg
    raw_target_feasibility.png/.svg
```

`raw_target_scan.csv` is the direct audit that target acceleration is zero for
all six arms before the governor. The feasibility table retains acceleration
columns so this invariant is machine-checkable rather than implied by the
experiment name.

## Run

From the project root:

```bash
uv run otg-lab run E11
```

E11 is an offline simulation using a recorded task position waveform. It is
not a closed-loop robot test and does not reproduce the source timestamp
jitter.
