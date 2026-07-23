# V4 protocol decision log

This append-only log records decisions made before V4 test visibility. No entry
below reports a V4 test execution.

## D-001 — Base and historical lock verification

- Date: 2026-07-23
- Status: accepted pretest
- Base pulled main: `1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe`
- Evidence read: `config_lock_v3.json`, `EXPERIMENT_PROTOCOL_V3.md`,
  `V3_POSTREVIEW_ADDENDUM.md`, current V3 formal configs, and current
  same-information implementation.
- Decision: carry forward the last formal V3 validation selection without using
  V4 test: `local_poly_w5_d3_lag1`, `constant_jerk`, `H=0 ms`, 100 Hz,
  0.01 s minimum duration, and 4.1/8.2/4000 limits.
- Representation note: V3 serialized `nominal_dt` as
  `0.010000000000000009`; this differs from 0.01 only by floating-point
  representation and is copied exactly into canonical consumer objects.

## D-002 — H=0 interpretation

- Date: 2026-07-23
- Status: preregistered
- Decision: V4 isolates target-component value under the same upstream
  estimator/predictor and direct follower. It does not re-test future-position
  prediction. Existing Phase A oracle and prior evidence retain the
  future-reference-timing role. Validation/test cannot reselect H.

## D-003 — Explicit one-step defaults

- Date: 2026-07-23
- Status: preregistered
- Decision: formal configs spell out current one-step default weights and
  direct-follower `require_t_free_le_dt=true`, so later default drift cannot
  silently change the locked algorithm. P/PV/PVA may differ only by
  `target_mode`.

## D-004 — Statistical seeds and classification boundaries

- Date: 2026-07-23
- Status: preregistered
- Decision: fix primary/secondary/guardrail/subgroup bootstrap seeds at
  `2026072301`, `2026072302`, `2026072303`, and `2026072304`, respectively;
  use exactly 10,000 whole-trajectory paired resamples. A CI bound equal to
  zero is inconclusive; a lower bound equal to 0.05 is strongly material.
  Holm correction covers exactly S1-S5.

## D-005 — Dry-run repair boundary

- Date: 2026-07-23
- Status: preregistered
- Allowed repairs: crash, schema error, artifact omission, incorrect metric
  implementation, incorrect method identity, incorrect time alignment,
  packaging error, checksum error.
- Forbidden changes: estimator, predictor, horizon, governor, weights, limits,
  family distribution, primary metric, acceptance threshold, subgroup,
  target mode, test population.
- Every permitted repair must be appended here before lock.

## D-006 — Development-only Phase A compliance defect

- Date: 2026-07-23
- Severity: P0 prelock
- Observation: an early development invocation of the existing compatibility
  wrapper passively inspected two prohibited commercial-interface names via
  attribute checks. It did not install or call commercial functionality and
  did not enter a V4 formal pipeline.
- Decision: V4 formal execution must not invoke that wrapper and must not probe
  commercial interfaces. The V4 runner shall call
  `run_phase_a_p_only_compatibility` directly and emit only RMSE, lag, maximum
  error, native-execution rate, and unexpected-fallback rate.
- Lock condition: automated formal-import/config checks must prove the probe and
  wrapper are unreachable from V4 before `locked=true`.

## D-007 — Prelock status

- Date: 2026-07-23
- Status: designed, not locked
- Decision: `protocol_status_v4.json.status=designed_not_locked` and
  `config_lock_v4.json.locked=false`. Validation, exact environment/hash
  capture, and the clean lock commit are still required before any V4 test
  trajectory may be generated or viewed.

## D-008 — Managed-output cleanliness

- Date: 2026-07-23
- Severity: P0 prelock
- Observation: the preregistered validation path is inside
  `results/paper_evidence_v4/`; without an ignore rule, the mandatory
  validation canary would make the confirmation worktree dirty.
- Decision: ignore the complete V4 confirmation and development result roots,
  matching the established V3 managed-output pattern. Raw bundles remain local
  and are distributed through hashed release archives. After confirmation,
  bounded evidence is explicitly force-added for the results commit.
- Scientific impact: none; no method, data, threshold, estimand, subgroup, or
  test identity changed.

## D-009 — Oracle implementation identity

- Date: 2026-07-23
- Severity: P1 prelock clarification
- Observation: the canonical online sample runner requires an estimator object
  for timing and DoF initialization even when its existing offline oracle
  predictor supplies analytic truth.
- Decision: retain the locked estimator only as timing/DoF plumbing in oracle
  runs, bypass its numerical posterior as a target-information source, and
  replace the prediction consumed by target construction with analytic truth
  at the same target time. The oracle sidecar must mark every row noncausal,
  offline-only, nondeployable, diagnostic-only, and excluded from primary.
- Scientific impact: this makes the declared diagnostic match the executed
  method; it does not alter any causal primary method.

## D-010 — Resolvable Git authorization without self-reference

- Date: 2026-07-23
- Severity: P0 prelock
- Observation: a tracked lock file cannot contain the SHA-1 of the same commit
  whose tree includes that file; requiring `HEAD` to equal such a literal is a
  cryptographic self-reference with no practical fixed point.
- Decision: the completed lock records the clean scientific source commit. A
  dedicated child authorization commit may change only `config_lock_v4.json`
  and `protocol_status_v4.json`. The precommitted immutable ref
  `refs/tags/paper-evidence-v4-confirmation-source` is created at that child,
  and confirm requires the ref to resolve exactly to `HEAD`, requires
  `HEAD^` to equal the recorded scientific source commit, verifies the
  two-path authorization diff, and verifies every scientific/config hash.
- Scientific impact: none; the scheme strengthens exact-HEAD authorization
  without making an impossible self-hash claim.

## D-011 — Preserve explicitly non-applicable profile-feasibility cycles

- Date: 2026-07-23
- Severity: permitted prelock dry-run schema/metric repair
- Observation: the first exposed-V3-validation dry-run completed execution but
  correctly refused bundle promotion while recomputing trajectory summaries.
  An inexact Community-Ruckig piecewise profile can make
  `command_segment_feasible` or
  `command_continuous_constraints_satisfied` explicitly non-applicable on an
  isolated cycle. The metric reducer incorrectly required these two optional
  profile fields to be all-or-none over the entire trajectory.
- Decision: retain every cycle and compute each affected boolean rate only on
  its explicitly evaluated cycles, while emitting an evaluated fraction and
  unavailable count. Missingness within one synchronized multi-DoF cycle
  remains a hard error. Primary direct-executable methods are still required
  to have complete values at every cycle by the V4 handoff validity gate.
- Scientific impact: none. No estimator, predictor, horizon, governor,
  follower, limit, target mode, trajectory, metric definition, threshold,
  subgroup, seed, or test identity changed. The failed attempt promoted no
  bundle and used only exposed V3 validation identities.

## D-012 — Permit empty annotations in passing identity-audit rows

- Date: 2026-07-23
- Severity: permitted prelock dry-run artifact-schema repair
- Observation: the second exposed-V3-validation dry-run passed execution,
  metric reduction, and every method-identity gate, then correctly refused
  bundle promotion because a passing audit row represents “no failed fields”
  as an empty CSV annotation. The generic CSV validator treated that explicit
  pass annotation as unexplained missingness.
- Decision: for the five V4 identity-audit tables only, declare
  `failed_fields` and, where present, `failed_configuration_fields` as
  optional annotations. Their corresponding boolean pass columns remain
  mandatory, and any failed row still carries its nonempty field names.
- Scientific impact: none. No estimator, predictor, horizon, governor,
  follower, limit, target mode, trajectory, metric definition, threshold,
  subgroup, seed, or test identity changed. The failed attempt promoted no
  bundle and used only exposed V3 validation identities.

## D-013 — Preserve analytic-profile sampled-jerk non-applicability

- Date: 2026-07-23
- Severity: permitted prelock dry-run artifact-schema repair
- Observation: the third exposed-V3-validation dry-run passed all earlier
  gates, then correctly refused bundle promotion during bundle-level schema
  validation. Exact parsed Community-Ruckig piecewise-constant-jerk profiles
  report analytic internal jerk and intentionally leave the separate
  acceleration-difference sampled-jerk diagnostic unavailable. The validator
  already recognized this contract for `analytic_profile_extrema`, but omitted
  the equivalent `analytic_ruckig_piecewise_constant_jerk` method label.
- Decision: recognize `max_sampled_jerk` as the sole optional fallback-value
  field for both exact analytic profile labels. Analytic internal jerk,
  velocity/acceleration extrema, margins, violation counts, and the audit
  method remain mandatory.
- Scientific impact: none. No estimator, predictor, horizon, governor,
  follower, limit, target mode, trajectory, metric definition, threshold,
  subgroup, seed, or test identity changed. The failed attempt promoted no
  bundle and used only exposed V3 validation identities.
