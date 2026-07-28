# E08 — Recorded-task PVA finite-difference transfer

E08 applies the five causal PVA finite-difference methods from E04 to one
recorded task position waveform and compares them with the same scheduled
`P[k+1]` baseline.

The recorded position samples are not smoothed and the E04 derivative methods
are unchanged. Before ordinary unshielded Ruckig, E08 now projects target V/A
that exceed the configured admissible range. Position is never changed. Raw
targets are retained separately, so both the derivative output and every
projection remain auditable.

## Input and time base

The declared experiment input is:

```text
data/trajectories/recorded_tasks_simplified_with_velocity_limit.csv
```

Its metadata traces the position samples to:

```text
data/raw/recorded_tasks/simplified_with_velocity_limit.csv
```

The canonical file contains 7,673 position samples on a fixed 10 ms grid, for
76.72 s of offline replay. Position values preserve source row order exactly.
The raw `elapsed time` and `timestamp` columns are provenance only: their local
2.47–21.75 ms sampling jitter is not replayed. Velocity, acceleration, and jerk
columns remain unavailable rather than being filled with zero or offline
estimates.

The filename `with_velocity_limit` describes the acquisition condition. E08
independently applies the E04 execution limits `V/A/J = 4.1 / 8.2 / 4000`.

## Method matrix

All methods consume position-only measurements. The scheduled position is
declared available one step ahead, exactly as in E04.

| Method | Derivative source | Represented target | Age at command |
|---|---|---:|---:|
| `p_kp1_baseline` | zero V/A | `P[k+1]` | 0 samples |
| `pva_est_backward_o1_k` | backward O1 estimator | `PVA[k]` | 1 sample |
| `pva_est_backward_o2_k` | backward O2 estimator | `PVA[k]` | 1 sample |
| `pva_est_centered_o2_km1` | delayed centered O2 estimator | `PVA[k−1]` | 2 samples |
| `pva_pred_backward_o1_kp1` | future backward O1 predictor | `PVA[k+1]` | 0 samples |
| `pva_pred_backward_o2_kp1` | future backward O2 predictor | `PVA[k+1]` | 0 samples |

There is no `pva_truth_kp1` arm because the recorded input has no derivative
truth. Offline derivatives in `reference_derived.csv` remain input-difficulty
diagnostics and are never exposed to the online methods.

All runs use:

```text
dt = prediction horizon = minimum duration = 0.01 s
V/A/J limits = 4.1 / 8.2 / 4000
governor = configured-limit V/A projection
follower = ordinary unshielded Ruckig
failure policy = record and continue
```

Projection first clips acceleration and velocity to the configured maxima. If
nonzero acceleration would still violate Ruckig's directional jerk-limited
stopping envelope, velocity is tightened to that boundary. All six arms are
required because projected targets are expected to remain executable.

## Metrics and acceptance

The primary metric is raw-time `position_rmse` from `t=0.04 s` through the end
of the waveform. Secondary position metrics are MAE, P95 absolute error,
maximum absolute error, and IAE. Lag remains diagnostic and never replaces the
raw-time primary metric.

A PVA method passes transfer only when it:

1. completes all 7,672 tracking cycles;
2. has main-window position RMSE below `p_kp1_baseline`;
3. does not regress the declared constraint, fallback, solver, or deadline
   guardrails.

An incomplete method is still classified as `not_transferable_incomplete`.
Its partial command remains auditable, but prefix RMSE is not reported or used
for ranking. A completed candidate is compared over the full declared window.

## Full raw-target audit

E08 separately replays only the registered estimator, predictor, and scheduled
target-builder components over the full input. This diagnostic does not call a
governor or follower, so it continues to expose the unprojected target values
and their original feasibility.

The run adds:

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

`raw_target_scan.csv` is the per-cycle source for the full-waveform raw V/A
limit and Ruckig-admissibility summary. `acceptance.csv` records projection
count/rate and the first projection cycle; position RMSE remains blank only if
a method is incomplete. In both figures, a small `×` marks every projected
cycle and a larger `×` emphasizes the first projection. Any genuine later
execution failure remains separately visible.

## Run

From the project root:

```bash
uv run otg-lab run E08
```

Read `acceptance_summary.md` for the scientific transfer result,
`acceptance.csv` for projection statistics, and `failures.csv` / each
`status.json` for any unexpected execution failure.

## Interactive dashboard

Build the bounded dashboard artifact for any completed E08 run:

```bash
uv run python \
  experiments/E08_pva_finite_difference_recorded_tracking/build_interactive_dashboard.py \
  experiments/E08_pva_finite_difference_recorded_tracking/runs/<run-id>
```

The generated `analysis/interactive/artifact.json` contains downsampled
position/error overview curves while retaining every target-projection event.
Package that validated artifact with the Data Analytics portable-artifact
builder to obtain a self-contained HTML dashboard.

E08 is an offline simulation using a real task position waveform. It is not a
closed-loop robot test with measured plant feedback, disturbances, or the
original timestamp jitter.
