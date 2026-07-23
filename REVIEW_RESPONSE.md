# PR #1 review response

This response records the implemented repairs and the one-time formal v3
confirmation. The PR must remain Draft: 15 of 18 preregistered required
component criteria passed, while all three development-CSV candidate targets
failed. No threshold, failed trajectory, or negative result was removed.

## Root of trust and accessible evidence

- Formal source commit: `cf3a517bc74236a4eb1b95c5b6eee952993a0837`.
- Formal command: `uv run python run_paper_evidence_v3.py confirm` (executed once).
- Root index: `results/paper_evidence_v3/artifact_index.json`.
- Root-index SHA-256:
  `12393579515e144f8cb499144772471e3a0398d8d2e19bdff89ff0fa7c479933`.
- Primary locked-test archive:
  <https://github.com/TANG617/otg_lab/releases/download/pr-1-v3-evidence-cf3a517/primary_locked_test_v3.zip>.
- Archive size: 253,777,047 bytes. SHA-256:
  `3f63ff81e708925c4d8c55616585e9b9925c43e1f59ede637e418944b39b8da2`.
- The release also contains the checksum sidecar and archive manifest. The ZIP
  contains all 11 minimum requested artifacts, including `samples.parquet`,
  the complete constraint/fallback tables, config, split, run provenance, and
  artifact checksums.

The nine complete raw matrices total approximately 1.6 GB and remain local and
rebuildable. The release claim is limited to the primary locked-test matrix.

## Findings 1-3: governor viability and executable follower semantics

The shared `otg_lab/constraints.py` implementation now defines exact
constant-jerk integration, point/segment feasibility, direction-dependent
stopping viability, and analytic viable-jerk intervals. The stopping set is

```text
|v| <= vmax, |a| <= amax,
a > 0  => v + a^2/(2*jmax) <= vmax,
a < 0  => v - a^2/(2*jmax) >= -vmax.
```

The selected jerk must preserve the segment limits, terminal stopping set, and
existence of a subsequent safe discrete step. No production constraint uses a
grid approximation.

The impossible `previous_state + zero jerk` hold was removed. Every emitted
command is integrated from the actual current state and executed jerk. An
already nonviable measured state enters explicit emergency recovery with
`safety_guarantee=false`; formal-mode inability to validate recovery fails
closed instead of fabricating a command.

Both direct and ordinary-Ruckig followers now validate before committing. A
failed candidate causes an actual safety-controller action to be generated and
validated through the same path. `fallback_applied=true` therefore describes
the command that was executed, not a status-only label. Solver exceptions,
free-duration failures, and follower/governor memory synchronization use the
same semantics.

The fresh locked test contains 42,199 primary direct-candidate command cycles:
continuous V/A/internal-J violations are zero, fallback and projection are
zero, nonfallback point/one-step/sequence rates are 100%, and runtime deadline
misses are zero. Independent sample recomputation verified all 1,012,776 rows.

## Finding 4: canonical schema and recomputation

The canonical schema is `otg.sample.v2`. It separates raw-target,
executable-target, and committed-command feasibility, including
`command_next_step_exists`, and makes fallback request/application, safety
guarantee, and emergency state explicit. `target_feasible` is retained only as
a validated deprecated alias of `raw_target_point_admissible`.

The data dictionary, Arrow metadata, validators, metrics, diagnostics, figures,
and migration path were updated together. The final QA independently
recomputed every available feasibility field plus trajectory and summary
metrics from canonical Parquet. The locked bundle has 25 indexed artifacts and
24 verified checksum entries.

## Finding 5: exposed confirmation sets and protocol versioning

The v1 results remain immutable historical negative/regression evidence. Its
`oscillatory__test__004` failure is retained as a deterministic regression and
does not enter current confirmatory inference.

The first v2 confirmation failed during artifact recomputation after the v2
test became visible. It was frozen and inventoried in `protocol_status_v2.json`;
the same split was not reused.

The replacement `synthetic-feasible-v3` split was committed before test
execution. It has 120 train, 60 validation, and 120 test trajectories. The
freshness audit found zero trajectory-identity overlap and zero family/seed
overlap against all v1/v2 entries. Selection used train/validation only and
records `test_trajectory_count_seen=0`. The estimator, predictor, horizon,
governor parameters, QP status, exact code/config hashes, and six consumer
configs were locked before the single clean v3 confirmation.

`protocol_status_v3.json` freezes the completed result. The v3 test must not be
rerun for another confirmatory claim. A reviewer may reconstruct the artifact
with the recorded command, but such a reconstruction is not a second
independent confirmation.

## Finding 6: jerk-QP baseline

QP statuses are separated into wall-clock limit, maximum iterations, primal or
dual infeasibility, numerical failure, postcheck failure, solved, and other.
Scaling, persistent solver setup/update, primal/dual warm start, deterministic
settings, detailed residual/timing telemetry, the shared first-jerk safety
filter, and a conservative terminal set are implemented.

Validation selected `jerk_qp_n20` as qualified: fallback rate
`0.001177740700755717`, continuous violations 0, nonfallback terminal
viability 1.0, P99 `297.08323 us`, and deadline misses 0. The locked-test QP
fallback rate is `0.000639825588284` (27/42,199), all attributed to explicit
`qp_postcheck_failed`; it is not hidden or called a timeout.

## Finding 7: asynchronous multi-joint time

Each axis retains its own posterior source and availability time. Causal
propagation synchronizes axes at control time without replacing their
provenance with `max(source_time)`. Future source times are rejected as clock
anomalies. Tests cover independent axis jitter, delay, drop, duplicate,
timestamp regression, future samples, and source-time retention.

The formal multi-DoF matrix retains one fail-closed case: 12 DoF with
`different_frequency` raises `InvariantViolationError` because the safety
fallback cannot satisfy `command_t_free_exceeds_dt`. Completion is 29/30
(96.67%); the failed unit is kept in the denominator and no unsafe command is
emitted. This is negative robustness/scalability evidence, not an acceptance
failure hidden from the report.

## Finding 8: raw-evidence access

The primary locked-test evidence package is published as the prerelease asset
listed above. It was built deterministically from the checksummed formal bundle,
schema-validated, and passed `unzip -t`. Full non-primary raw matrices remain a
documented storage blocker; bounded summaries, figures, manifests, and all nine
raw artifact-index roots are checked into the PR.

## Statistical and real-data findings

Every 112 denominator-completeness row is complete. All eight paired
comparisons use 120/120 whole trajectories with zero exclusions and 10,000
bootstrap resamples. Overall, family, demand-stratum, sample-rate, heterogeneity,
harmful-rate, and worst-five outputs retain negative strata and trajectories.

The primary one-step direct candidate improves paired position RMSE by 77.38%
versus predicted P; its 95% interval is 69.96%-84.44%. Absolute lag and maximum
error are not worse overall. Rapid-reversal contains harmful trajectories and
is explicitly retained.

The one available 19.38 s CSV remains development-only. The candidate fails all
three strict preregistered targets: RMSE `0.21996 > 0.02991`, lag
`0.12 > 0.03`, and maximum error `0.66831 > 0.184528`. These failures account
for the 15/18 required-component result and block Ready-for-review status. No
real-stream generalization, deployment benefit, or production-safety claim is
made.

## Verification and cleanup

- Unit/integration/property/adversarial suite at the formal lock: 331 passed.
- Ruff and both GitHub CI runs at the lock commit: passed.
- Formal raw bundles: 9/9 clean-commit manifests and independent validation.
- Final layer: 68 indexed bounded artifacts, 14 PNG/SVG figure categories.
- Root index, sidecar, primary ZIP, and ZIP contents: independently checked.
- Obsolete `worklog/draft_pr_body.md` and
  `worklog/paper_evidence_status.md` were removed.

The validation API must be called with the preregistered limits:

```bash
uv run python -c 'from otg_lab.artifacts import validate_artifact_bundle; print(validate_artifact_bundle("results/paper_evidence_v3/raw_runs/locked_test", expected_commit="cf3a517bc74236a4eb1b95c5b6eee952993a0837", verify_recomputation=True, require_complete_feasibility=True, recompute_arguments={"max_lag_s": 1.0, "motion_limits": {"max_velocity": 4.1, "max_acceleration": 8.2, "max_jerk": 4000.0}}))'
```

Calling that low-level API without `motion_limits` uses generic metric defaults
and is not the formal report validation path.

## Final reviewer checklist

- [x] Shared analytic viability and discrete next-step invariant implemented.
- [x] Impossible hold and status-only follower fallback removed.
- [x] Schema v2 and asynchronous timestamp semantics independently tested.
- [x] v1/v2 exposure is explicit; v3 freshness and no-test-selection guards pass.
- [x] QP qualification gate passes without exceeding the control budget.
- [x] Complete trajectory denominators and stratified negative results retained.
- [x] Primary locked-test sample evidence is publicly downloadable and hashed.
- [x] CI, artifact checksums, root index, and full locked-bundle recomputation pass.
- [ ] Three preregistered legacy-CSV criteria pass (currently failed).
- [ ] Independent real locked test and robot/HIL evidence exist (external blockers).

Disposition: keep PR #1 Draft and do not merge.
