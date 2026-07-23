# Canonical Sample Data Dictionary

The machine-readable schema is `otg_lab.schema.FIELD_SPECS` with version
`otg.sample.v3`. Parquet is written with Arrow field-level availability
metadata. Null means unavailable or not applicable; it does not mean zero.
V2 input is extended through the explicit v2-to-v3 compatibility path, which
leaves profile and method-identity evidence unknown rather than inventing it.
V1 input still requires `read_parquet(..., migrate_v1=True)` (or the named
migration helpers). An old table is never silently presented as if it had
recorded v3 profile evidence.

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
| `command_jerk` | Follower-provided per-cycle jerk quantity retained for compatibility; the profile fields below are authoritative for execution and constraint claims. |
| `acceleration_difference_jerk` | `(command_a-current_a)/DT`; a sampled acceleration-difference diagnostic. It is not an actual Ruckig internal jerk when the prefix has multiple segments. |
| `sampled_jerk` | Deprecated validated alias of `acceleration_difference_jerk`. |
| `new_jerk` | Direct constant-jerk command when that direct value is available; otherwise null. |
| `internal_trajectory_jerk` | Maximum/representative jerk from the continuously audited command profile when available; otherwise null. |
| `command_profile_kind` | `constant_jerk`, `ruckig_piecewise_constant_jerk`, or `emergency_constant_jerk`. The kind selects the endpoint/audit semantics. |
| `command_profile_start_time`, `command_profile_duration` | Start within the source trajectory's local time and prefix duration. The stored segment boundaries are profile-relative. |
| `command_profile_segment_boundaries_json`, `command_profile_segment_jerks_json` | Ordered accessible boundaries (including `0` and duration), plus the jerk for each intervening segment when the profile is exact. An inexact sampled profile may retain accessible boundaries while jerk JSON remains null. For an exact profile there is one more boundary than jerk. |
| `command_profile_segment_count`, `command_profile_boundary_count` | Number of jerk segments and number of internal switching boundaries, respectively. |
| `command_profile_source` | Origin of the executed profile, such as a frozen native Ruckig trajectory or a direct constant-jerk controller. |
| `command_profile_exact` | The stored segments can exactly reconstruct the executed prefix. False/unknown profiles cannot support exact profile-recomputation claims. |
| `command_endpoint_matches_profile` | Segment-by-segment evaluation at the profile duration matches `command_{p,v,a}`. A Ruckig endpoint is never checked by compressing the prefix to one jerk. |
| `command_first_jerk`, `command_last_jerk`, `command_internal_max_abs_jerk` | First segment jerk, last segment jerk, and maximum absolute jerk over all stored segments. |
| `command_constant_jerk_exact` | True for an exact one-segment direct/emergency constant-jerk command. Null/not applicable for a Ruckig piecewise profile; null must not trigger fallback. |
| `command_profile_continuous_constraints_satisfied` | Analytic segment-by-segment V/A/J audit result for the executed profile. |
| `native_follower`, `native_command_executed` | Declared native command generator and whether its command, rather than a replacement algorithm, was executed this cycle. |
| `actual_command_algorithm` | Controller/follower that actually produced the committed command on this cycle. |
| `method_semantics` | Declared identity: `ordinary_ruckig_unshielded`, `safety_shielded_ruckig`, `direct_constant_jerk`, or `mixed`. |
| `safety_shield_requested`, `safety_shield_applied`, `safety_shield_reason` | Whether the method permits/requests the explicit shield, whether it changed this command, and why. An unshielded ordinary-Ruckig row cannot apply a shield. |
| `fallback_controller`, `fallback_changes_algorithm` | Replacement controller identity and whether fallback changed the executed algorithm. An algorithm-changing fallback requires `native_command_executed=false`. |
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
| `free_trajectory_duration`, `command_t_free_le_dt` | Ordinary-Ruckig free duration from `current_{p,v,a}` to the actually committed `command_{p,v,a}`, and whether that committed-command duration is no longer than `dt_control`. It is a separate solve from `current` to the requested executable target. The requested-target duration must never be copied into this field or used for this flag, including when a fallback command is committed. |
| `command_segment_feasible` | The actually committed command endpoint matches its exact profile and that profile respects continuous V/A/J limits. For legacy non-profile constant-jerk rows only, the value may be reconstructed from the single-segment equations. |
| `command_stopping_viable` | Actually committed terminal command lies in the stopping/viability envelope. |
| `command_next_step_exists` | Actually committed terminal command admits at least one analytic constant-jerk action over the next control period that preserves segment constraints and the stopping envelope. This is a direct-governor/viability-shield property and a diagnostic for unshielded ordinary Ruckig; failure alone must not silently replace an unshielded native Ruckig prefix. |
| `command_continuous_constraints_satisfied` | Recorded continuous command extrema are within the per-sample limits. |
| `target_feasible` | **Deprecated alias** for `raw_target_point_admissible` only. The validator and Arrow `alias_for` metadata require exact equality; it never denotes executable or command feasibility. |
| `legacy_target_feasible_v1` | Original ambiguous v1 value retained only by migration. It is not copied into a current feasibility result. |
| `target_projected` | Historical scalar target projection occurred. Governors report distortion instead and leave this false. |
| `fallback_requested`, `fallback_applied`, `fallback_reason` | A candidate failed and requested safety handling; a fallback action was actually committed; and its stable reason. `fallback_applied=true` requires a reason. Use `fallback_changes_algorithm` and `fallback_controller` to distinguish explicit replacement from within-method handling. |
| `fallback` | Deprecated compatibility alias for `fallback_applied`. It can no longer mean a status-only fallback. |
| `safety_guarantee`, `emergency_mode` | For unshielded ordinary Ruckig, `safety_guarantee=true` is limited to executed-prefix endpoint/profile consistency and continuous V/A/J legality; it does not imply recursive viability. Direct and safety-shielded methods may additionally claim the recursive invariant when their committed command passes the stopping/next-step checks. `emergency_mode=true` denotes best-effort recovery and cannot claim a safety guarantee. |
| `solver_status` | Native/normalized governor and follower solver status. Ruckig follower status keeps requested-target free solve, native-prefix solve/audit, and committed-command free solve provenance separate. |
| `qp_iterations` | Native short-horizon QP iteration count; null outside QP runs. |
| `qp_status_category` | Stable QP outcome: `qp_solved`; one of the six qualification failure classes `qp_time_limit_reached`, `qp_max_iter_reached`, `qp_primal_infeasible`, `qp_dual_infeasible`, `qp_numerical_failure`, `qp_postcheck_failed`; or the pre-solver conditions `qp_invalid_input` / `qp_solver_unavailable`. Failure classes are never collapsed into a generic timeout. |
| `qp_solve_time_us`, `qp_primal_residual`, `qp_dual_residual` | Native OSQP solve-only time and final primal/dual residuals; nullable when the backend fails before returning solver information and always null outside QP runs. |
| `qp_hessian_condition_number`, `qp_constraint_condition_number` | Condition numbers of the fixed dimensionless QP matrices used for scaling QA; null outside QP runs or if setup never completed. |
| `deadline_miss` | End-to-end online compute exceeded the control-period deadline. |
| `state_reset`, `invalid_input` | Explicit estimator reset and invalid-input decisions; neither is inferred from plant feedback correction. |
| `free_trajectory_duration` | Frozen ordinary-Ruckig duration with no minimum duration for the actually committed follower endpoint when available. Trajectory reachability aggregates exclude fallback cycles, but the sample-level duration and `command_t_free_le_dt` remain recorded on fallback cycles. This endpoint diagnostic is not a substitute for auditing the executed piecewise profile. Predictor-layer raw-target `T_free/H` is produced separately by the benchmark freeze solver. |
| `*_compute_us`, `total_compute_us` | Component and online-chain elapsed time, excluding plots and artifact I/O. |

`command_stopping_viable` and `command_next_step_exists` remain independent
diagnostics on unshielded ordinary-Ruckig rows. They do not broaden that
method's `safety_guarantee`, which covers only the executed prefix described
above. Direct and safety-shielded methods may use both diagnostics as
additional evidence for a recursive viability guarantee.

## Provenance and stress realization

`source_kind`, `reference_family`, `reference_variant`, `scenario_id`, and `truth_available` prevent synthetic/real/oracle mixing. `reference_frequency_spec_json` persists exact discrete excitation tones for sine/multi-sine references and start/end frequency plus duration for chirps; frequency-response diagnostics never infer the designed frequencies from FFT leakage bins. Deliberately infeasible governor cases use `source_kind=synthetic_deliberate_infeasible`, `split=infeasible`, their own dataset ID, and a named infeasibility scenario; they cannot enter the clean estimator benchmark. Measurement availability/validity and all injected noise, quantization, jitter, delay, drop, duplicate, timestamp regression, future-source clock anomaly, outlier, nonfinite, and impossible-jump fields preserve the exact realization. `event_future_source_time=true` means a source timestamp was later than the event's availability and the runner rejected it; it is never fed to an estimator. `event_flags` is a stable semicolon-separated summary; typed event columns remain authoritative.

## Null and nonfinite rules

- Real position traces have null derivative truth.
- Missing measurements are null, not a held value; a causal resampler may separately set `event_held=true` and store the held measurement.
- Migrated v2 rows have null v3 profile and method-identity fields unless the old artifact genuinely contained enough information; null does not prove a constant-jerk profile or native execution.
- `command_constant_jerk_exact` is null for Ruckig piecewise profiles because the predicate is not applicable, not because the profile failed.
- NaN/infinity is allowed only in deliberately injected measurement fields with `event_nonfinite=true` and must result in an explicit estimator policy/fallback record.
- State, target, command, plant, timing, truth, and compute fields may not contain unexplained NaN/infinity.

## Statistical report availability

The final `otg.paper-evidence-report.v2` statistical tables retain every
predeclared paired trajectory and stratum even when a relative quantity is not
mathematically finite. `relative_point_defined` states whether the observed
relative point estimate has a nonzero baseline mean;
`relative_interval_defined` separately states whether every bootstrap draw has
a nonzero baseline mean. `relative_status` distinguishes a zero observed mean
from a zero bootstrap-resample mean. When a flag is false, only its associated
relative fields are null. The absolute difference and interval, paired sample
counts, effect-size availability, p-value, trajectory outcomes, and harmful
strata remain populated. Null relative fields never mean zero and never permit
complete-case deletion.
