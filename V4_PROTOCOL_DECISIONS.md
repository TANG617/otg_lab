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
