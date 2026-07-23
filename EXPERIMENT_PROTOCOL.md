# Paper Evidence v1 Experiment Protocol

This is an executable technical protocol, not a paper manuscript. Its purpose is to prevent timing, selection, and artifact ambiguities while producing the evidence bundle under `results/paper_evidence_v1/`.

## Fixed deployment condition

The primary experiment uses one synchronized Ruckig instance for all active joints with:

- `max_velocity = 4.1 rad/s`
- `max_acceleration = 8.2 rad/s²`
- `max_jerk = 4000 rad/s³`
- `DT = 0.010 s`
- `minimum_duration = DT`

Prediction horizon is an estimator/predictor experiment variable and never changes `minimum_duration`. Position reference at physical time is the primary tracking ground truth. Derivative truth is available only for generated analytic/integrated references.

## Physical time contract

`source_time` is the physical timestamp attached to a measurement. `arrival_time` is when the sample becomes visible to online code. `control_time` is the control tick. A posterior represents `posterior_state_time` and is not usable before `posterior_available_time`. A prediction represents exactly `prediction_time = posterior_state_time + prediction_horizon`; availability does not move forward. A raw target preserves the prediction's physical target time. A governor emits an executable target for exactly `control_time + DT`. A follower consumes `target[k]` and produces `command[k+1]` at `control_time + DT`. The plant state is stored at the command time.

Plots use these timestamps without cosmetic shifts. Lag-aligned metrics are secondary diagnostics and never replace raw-time error.

## Causal pipeline

```text
measurements available by control tick k
  -> Estimator.update(measurement): posterior only
  -> Predictor.predict[_sequence](posterior, H)
  -> raw target component selection (P / PV / PVA)
  -> optional executable governor
  -> direct or ordinary synchronized Ruckig follower
  -> command state at k+1
  -> ideal or delayed-servo plant and next measured state
```

Online objects cannot access later array entries. Each estimator is subjected to a future-mutation test. `oracle` and offline centered difference are noncausal upper bounds and are excluded from deployable rankings.

## Data and split lock

Synthetic truth is generated on a grid no coarser than 1 ms and then sampled independently at each requested rate. The fixed `split_manifest.json` contains 20 train, 10 validation, and 20 locked-test trajectories in each of six families. Test identities and seeds are never passed to parameter or horizon selection.

The recorded `plot_data.csv` has one trajectory and no derivative truth. Three distinct imports are maintained:

1. `legacy_fixed_grid`: read `value` only and assign 10 ms per row.
2. `timestamp_resampled`: validate actual source timestamps and causally hold the latest available sample on a 100 Hz grid.
3. `arrival_time_simulation`: retain source/arrival/control clocks and simulate delay, jitter, and loss.

Offline interpolation may be evaluated only with an `oracle` label.

## Development, selection, and confirmation sequence

1. Run unit tests and tiny smoke experiments.
2. Generate train/pilot and validation artifacts.
3. Select estimator parameters from estimator-layer validation metrics only.
4. Select prediction horizon from validation prediction/tracking metrics under a fixed estimator, predictor, target mode, governor, limits, plant, and `minimum_duration=DT`.
5. Select QP horizon from validation feasibility, error, and compute metrics.
6. Write a fully resolved locked config and commit all code/config.
7. Verify the worktree is clean.
8. Run locked test without selection or plot filtering.
9. Independently recompute summaries from Parquet and verify checksums.

Any post-test method change requires a new versioned protocol/results directory.

## Required matrices

Phase A repeats P, analytic PV/PVA, backward FD, offline centered, causal delay-one centered, next-cycle oracle, acceleration OFAT, and jerk OFAT under the fixed primary limits. It preserves the historical directory and adds per-sample artifacts plus a legacy-versus-clean regression report.

Estimator validation covers PositionOnly, raw backward difference, delay-one centered difference, causal local polynomial (windows 5/7/9/11 and degrees 2/3 with explicit lag), alpha-beta-gamma, CA-KF, robust CA-KF, constant-jerk KF when stable, and jerk-limited differentiator. Two or three estimators are locked for downstream experiments.

Prediction horizons are `0/10/20/40/50/60 ms`, with `80/100/150 ms` stress. Predictors are ZOH, CV, CA, CJ, local-polynomial propagation, and labelled oracle. Target component comparisons hold information constant and change only P/PV/PVA construction.

Follower comparisons include deployed P-only, predicted P, raw PV, scalar-projected PVA, one-step governed PVA direct, one-step governed PVA through ordinary Ruckig, jerk-QP direct, and semantically valid jerk-QP through Ruckig. Trackig is included only if a real Pro API/license is detected.

Independent suites cover acceleration-active references, noise, quantization, timing/faults, combined stress, 50/100/200/500 Hz, 1/3/6/7/12 DoF scalability, ideal plant, delayed servo, and previous-command/measured/hybrid feedback.

## Governor invariants

The one-step governor uses the exact constant-jerk equations. Its quadratic objective normalizes position, velocity, and acceleration tracking error by per-cycle physical scales and includes jerk and delta-jerk regularization. The feasible jerk domain checks jerk, endpoint acceleration, endpoint velocity, and the exact interior velocity extremum when acceleration crosses zero. Raw targets are never overwritten; distortion is a separate output.

The jerk-QP uses the same triple-integrator model, hard sampled V/A/J constraints, deterministic OSQP settings, a fixed horizon, warm start, and first-step execution. Every first step receives an exact continuous postcheck. Nonfinite input, solver exception, timeout, infeasibility, and postcheck failure fall back explicitly to the one-step governor.

For every non-fallback executable target, tests and artifacts audit dynamic consistency, point admissibility, exact one-step reachability, ordinary Ruckig `T_free <= DT`, continuous constraints, and recursive execution.

## Metrics and statistics

The statistical unit is one full trajectory. Primary tracking metrics are raw-time position RMSE, MAE, P95 absolute error, maximum error, IAE, settle time, and lag. Frequency and event diagnostics include gain/phase/group delay and local reversal/stop delay.

Estimator and prediction errors are evaluated at the state/prediction physical timestamp against synthetic truth. Governor distortion, jerk, delta jerk, feasibility, fallback, and free duration are separate. Sampled acceleration differences, `new_jerk`, and internal frozen-trajectory jerk are not conflated.

Paired comparisons use 10,000 trajectory bootstrap resamples for 95% intervals, report absolute and relative differences plus paired standardized effect, and use Holm correction for secondary comparisons. Per-sample values are never treated as independent observations.

Runtime warm-up precedes timed repetitions; plotting and artifact I/O are disabled. Component and total P50/P90/P99/P99.9/max plus deadline misses are stored with CPU/OS/Python/BLAS/Ruckig/OSQP/thread metadata.

## Continuous constraint audit

For Ruckig trajectories, accessible profile boundaries are included and the full duration is sampled at no more than 0.1 ms spacing. Profile jerk values provide internal jerk where exposed. Constant-jerk governor segments use analytic acceleration and velocity extrema. Each audit stores maxima, margins, violation counts, and occurrence information per joint.

## Failure and negative-result policy

Invalid input, projection, fallback, reset, solver status, deadline miss, and constraint violation are sample-level records. Failed trajectories remain in the locked dataset and summary denominator. Empty tables, unexplained nonfinite outputs, silent clipping, retrospective method selection, and test-driven tuning invalidate a run. Failure to meet the proposed-governor acceptance thresholds is retained and assigned to the estimator, predictor, governor, follower, plant, or information condition using layer-specific metrics.
