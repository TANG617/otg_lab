# Canonical Sample Data Dictionary

The machine-readable schema is `otg_lab.schema.FIELD_SPECS` with version `otg.sample.v1`. Parquet is written with Arrow field-level availability metadata. Null means unavailable; it does not mean zero.

## Identity and clocks

| Field | Meaning |
|---|---|
| `run_id`, `method_id`, `dataset_id`, `session_id`, `trajectory_id`, `joint_id` | Stable run/method/source/trajectory/joint identity. |
| `split` | `train`, `validation`, locked `test`, `development`, or `infeasible`. |
| `seed`, `k` | Fixed trajectory seed and control sample index. |
| `source_time` | Physical measurement time. |
| `arrival_time` | First time the online pipeline may consume the measurement. |
| `control_time` | Current controller tick. |
| `dt_actual`, `dt_control` | Source interval and configured controller period. |

## Reference, measurement, and posterior

| Field group | Meaning |
|---|---|
| `p_ref` | Position reference used for primary tracking evaluation. |
| `v_ref_truth`, `a_ref_truth`, `j_ref_truth` | Genuine synthetic/analytic truth only; null for the real CSV. |
| `p_meas`, `v_meas`, `a_meas` | Delivered sensor values. Missing sensor channels remain null. |
| `posterior_{p,v,a}` | Estimator posterior, never a future prediction. |
| `posterior_state_time` | Physical time represented by the posterior. |
| `posterior_available_time` | Time at which the posterior was available. |

## Prediction, target, command, and plant

| Field group | Meaning |
|---|---|
| `prediction_{p,v,a}` | Predictor output evaluated at `prediction_time`. |
| `prediction_time`, `prediction_horizon_ms` | Explicit future timestamp and propagation horizon. |
| `raw_target_{p,v,a}`, `raw_target_time` | Requested target before feasibility handling. |
| `executable_target_{p,v,a}`, `executable_target_time` | Governor output for the next control tick. |
| `command_{p,v,a}`, `command_time` | Actually issued command state at the explicit next-cycle physical time. |
| `command_jerk` | Follower-provided per-cycle jerk quantity retained for compatibility; use the semantic-specific fields below for constraint claims. |
| `sampled_jerk` | `(command_a[k+1]-command_a[k])/DT`; a sampled acceleration difference, never relabelled as a frozen-profile maximum. |
| `new_jerk` | Direct constant-jerk command or an actual Ruckig `OutputParameter.new_jerk` when that native value is exposed; otherwise null. |
| `internal_trajectory_jerk` | Maximum/representative jerk from the continuously audited frozen trajectory when exposed; otherwise null. |
| `plant_{p,v,a}` | True simulated plant state; hardware values require a hardware-labelled dataset. |
| `plant_measured_{p,v,a}` | Measurement returned by the simulated plant, including configured noise; distinct from true plant state. |
| `plant_saturated`, `plant_status` | Per-joint saturation flag and plant update status. |
| `plant_command_source_time`, `plant_command_age_s`, `plant_delay_s` | Provenance and age of the command actually acting on the plant, plus configured delay. |
| `command_measured_delta_{p,v,a}`, `command_measured_divergence` | Difference between prior command state and measured feedback, plus its max-component norm. |
| `event_command_measured_divergence` | Feedback divergence exceeded the configured correction threshold. |
| `feedback_correction`, `feedback_correction_{p,v,a}`, `feedback_correction_reason` | Whether measured/hybrid replanning changed the current state, the applied correction, and the decision rule. This is not an estimator reset. |

## Feasibility, failures, and timing

| Field | Meaning |
|---|---|
| `target_feasible` | Result of the appropriate point/one-step feasibility audit. |
| `target_projected` | Historical scalar target projection occurred. Governors report distortion instead and leave this false. |
| `fallback`, `fallback_reason` | Explicit fallback event and stable reason code. |
| `solver_status` | Native/normalized governor and follower solver status. |
| `qp_iterations` | Native short-horizon QP iteration count; null outside QP runs. |
| `deadline_miss` | End-to-end online compute exceeded the control-period deadline. |
| `state_reset`, `invalid_input` | Explicit estimator reset and invalid-input decisions; neither is inferred from plant feedback correction. |
| `free_trajectory_duration` | Frozen ordinary-Ruckig duration with no minimum duration for the follower target actually named by trajectory metrics. Fallback cycles are excluded. Predictor-layer raw-target `T_free/H` is produced separately by the benchmark freeze solver. |
| `*_compute_us`, `total_compute_us` | Component and online-chain elapsed time, excluding plots and artifact I/O. |

## Provenance and stress realization

`source_kind`, `reference_family`, `reference_variant`, `scenario_id`, and `truth_available` prevent synthetic/real/oracle mixing. `reference_frequency_spec_json` persists exact discrete excitation tones for sine/multi-sine references and start/end frequency plus duration for chirps; frequency-response diagnostics never infer the designed frequencies from FFT leakage bins. Deliberately infeasible governor cases use `source_kind=synthetic_deliberate_infeasible`, `split=infeasible`, their own dataset ID, and a named infeasibility scenario; they cannot enter the clean estimator benchmark. Measurement availability/validity and all injected noise, quantization, jitter, delay, drop, duplicate, timestamp regression, outlier, nonfinite, and impossible-jump fields preserve the exact realization. `event_flags` is a stable semicolon-separated summary; typed event columns remain authoritative.

## Null and nonfinite rules

- Real position traces have null derivative truth.
- Missing measurements are null, not a held value; a causal resampler may separately set `event_held=true` and store the held measurement.
- NaN/infinity is allowed only in deliberately injected measurement fields with `event_nonfinite=true` and must result in an explicit estimator policy/fallback record.
- State, target, command, plant, timing, truth, and compute fields may not contain unexplained NaN/infinity.
