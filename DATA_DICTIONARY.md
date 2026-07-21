# Canonical Sample Data Dictionary

The machine-readable schema is `otg_lab.schema.FIELD_SPECS` with version `otg.sample.v2`. Parquet is written with Arrow field-level availability metadata. Null means unavailable; it does not mean zero. V1 input is accepted only through the explicit `migrate_samples_v1_to_v2` / `read_parquet(..., migrate_v1=True)` path; an old table is never silently relabelled as v2.

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
| `posterior_axis_source_time`, `posterior_axis_available_time` | Per-joint posterior provenance before synchronization. These values may differ across joints and are never replaced by their maximum. |
| `measurement_sync_method` | The explicit synchronization rule. The runner uses `per_axis_estimator_ca_propagation_to_control_time`: one estimator per joint followed by causal constant-acceleration propagation to the common control time. |

## Reference, measurement, and posterior

| Field group | Meaning |
|---|---|
| `p_ref` | Position reference used for primary tracking evaluation. |
| `v_ref_truth`, `a_ref_truth`, `j_ref_truth` | Genuine synthetic/analytic truth only; null for the real CSV. |
| `p_meas`, `v_meas`, `a_meas` | Delivered sensor values. Missing sensor channels remain null. |
| `posterior_{p,v,a}` | Estimator posterior, never a future prediction. |
| `posterior_state_time` | Physical time represented by the synchronized vector posterior. Under per-axis propagation this is `control_time`; the older per-axis times remain in `posterior_axis_source_time`. |
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
| `current_{p,v,a}` | Exact replanning state from which the recorded executable/command segment starts. |
| `limit_max_velocity`, `limit_max_acceleration`, `limit_max_jerk` | Per-joint limits copied into each online sample so feasibility can be independently recomputed without consulting a run summary. |
| `command_max_abs_velocity`, `command_max_abs_acceleration`, `command_max_abs_jerk` | Per-joint extrema from the command segment's continuous audit. |

## Feasibility, failures, and timing

| Field | Meaning |
|---|---|
| `raw_target_point_admissible` | Raw requested target satisfies the point velocity and acceleration bounds. |
| `raw_target_ruckig_admissible` | Raw requested target additionally satisfies the direction-dependent jerk-limited stopping envelope used for a Ruckig target. |
| `executable_target_available` | A distinct governor/projection output is present in `executable_target_{p,v,a}`. False for a no-governor raw-target follower. |
| `executable_target_point_admissible` | Executable target satisfies point velocity/acceleration limits. |
| `executable_target_stopping_viable` | Executable target satisfies the direction-dependent terminal stopping envelope. |
| `executable_target_segment_feasible` | The constant-jerk segment from `current_{p,v,a}` to the executable target is dynamically consistent and respects continuous V/A/J limits. |
| `executable_target_free_trajectory_duration`, `executable_target_t_free_le_dt` | Ordinary-Ruckig free duration for the requested executable target, and whether a finite successful solve is no longer than `dt_control`. A missing/failed free solve yields false, not an invented duration. |
| `command_segment_feasible` | Actually committed command is dynamically reachable from `current_{p,v,a}` over `dt_control` and its segment respects V/A/J limits. |
| `command_stopping_viable` | Actually committed terminal command lies in the stopping/viability envelope. |
| `command_continuous_constraints_satisfied` | Recorded continuous command extrema are within the per-sample limits. |
| `target_feasible` | **Deprecated alias** for `raw_target_point_admissible` only. The validator and Arrow `alias_for` metadata require exact equality; it never denotes executable or command feasibility. |
| `legacy_target_feasible_v1` | Original ambiguous v1 value retained only by migration. It is not copied into a v2 feasibility result. |
| `target_projected` | Historical scalar target projection occurred. Governors report distortion instead and leave this false. |
| `fallback_requested`, `fallback_applied`, `fallback_reason` | A candidate failed and requested safety handling; a different safety action was actually committed; and its stable reason. `fallback_applied=true` requires a reason. |
| `fallback` | Deprecated compatibility alias for `fallback_applied`. It can no longer mean a status-only fallback. |
| `safety_guarantee`, `emergency_mode` | Whether the committed action retains the formal invariant and whether the current state required best-effort emergency recovery. Emergency mode cannot claim a safety guarantee. |
| `solver_status` | Native/normalized governor and follower solver status. |
| `qp_iterations` | Native short-horizon QP iteration count; null outside QP runs. |
| `qp_status_category` | Stable QP outcome: `qp_solved`; one of the six qualification failure classes `qp_time_limit_reached`, `qp_max_iter_reached`, `qp_primal_infeasible`, `qp_dual_infeasible`, `qp_numerical_failure`, `qp_postcheck_failed`; or the pre-solver conditions `qp_invalid_input` / `qp_solver_unavailable`. Failure classes are never collapsed into a generic timeout. |
| `qp_solve_time_us`, `qp_primal_residual`, `qp_dual_residual` | Native OSQP solve-only time and final primal/dual residuals; nullable when the backend fails before returning solver information and always null outside QP runs. |
| `qp_hessian_condition_number`, `qp_constraint_condition_number` | Condition numbers of the fixed dimensionless QP matrices used for scaling QA; null outside QP runs or if setup never completed. |
| `deadline_miss` | End-to-end online compute exceeded the control-period deadline. |
| `state_reset`, `invalid_input` | Explicit estimator reset and invalid-input decisions; neither is inferred from plant feedback correction. |
| `free_trajectory_duration` | Frozen ordinary-Ruckig duration with no minimum duration for the follower target actually named by trajectory metrics. Fallback cycles are excluded. Predictor-layer raw-target `T_free/H` is produced separately by the benchmark freeze solver. |
| `*_compute_us`, `total_compute_us` | Component and online-chain elapsed time, excluding plots and artifact I/O. |

## Provenance and stress realization

`source_kind`, `reference_family`, `reference_variant`, `scenario_id`, and `truth_available` prevent synthetic/real/oracle mixing. `reference_frequency_spec_json` persists exact discrete excitation tones for sine/multi-sine references and start/end frequency plus duration for chirps; frequency-response diagnostics never infer the designed frequencies from FFT leakage bins. Deliberately infeasible governor cases use `source_kind=synthetic_deliberate_infeasible`, `split=infeasible`, their own dataset ID, and a named infeasibility scenario; they cannot enter the clean estimator benchmark. Measurement availability/validity and all injected noise, quantization, jitter, delay, drop, duplicate, timestamp regression, future-source clock anomaly, outlier, nonfinite, and impossible-jump fields preserve the exact realization. `event_future_source_time=true` means a source timestamp was later than the event's availability and the runner rejected it; it is never fed to an estimator. `event_flags` is a stable semicolon-separated summary; typed event columns remain authoritative.

## Null and nonfinite rules

- Real position traces have null derivative truth.
- Missing measurements are null, not a held value; a causal resampler may separately set `event_held=true` and store the held measurement.
- NaN/infinity is allowed only in deliberately injected measurement fields with `event_nonfinite=true` and must result in an explicit estimator policy/fallback record.
- State, target, command, plant, timing, truth, and compute fields may not contain unexplained NaN/infinity.
