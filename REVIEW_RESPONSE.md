# PR #1 review response

This response records the implemented repairs, the one-time formal v3
confirmation, and the subsequent baseline-semantics review. The infrastructure
repair is Ready for reviewer assessment/merge, but the affected v3 primary
claim is not confirmatory: 15 of 18 frozen preregistered required component
criteria passed, all three development-CSV candidate targets failed, and
post-review found a separate confound in the conditions named as ordinary
Ruckig. No threshold, failed trajectory, historical artifact, or negative
result was removed.

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

## Post-review ordinary-Ruckig finding: point-by-point response

1. **Root cause reproduced.** The frozen v3 `RuckigFollower` treated the
   acceleration difference between the current state and
   `trajectory.at_time(DT)` as one constant jerk for the whole period. A native
   Ruckig prefix may cross one or more jerk-switch boundaries during that
   period, so failure of that reconstruction does not make the native endpoint
   unreachable.
2. **Execution model repaired.** The current command model distinguishes exact
   direct/emergency constant-jerk profiles from exact Ruckig
   piecewise-constant-jerk prefixes. Endpoint and continuous V/A/J audits use
   the executed profile; acceleration-difference jerk is retained only as a
   sampled diagnostic and is not presented as Ruckig internal jerk.
3. **Follower behavior repaired.** An unshielded ordinary-Ruckig method now
   executes the audited native prefix or reports a method failure. It does not
   silently replace a non-constant native prefix with the one-step controller.
   A Ruckig method that permits replacement is explicitly labelled
   safety-shielded and records the requested/applied shield, reason, replacement
   controller, native-execution status, and actual algorithm.
4. **Method identities separated.** The experimental matrix now separates
   unshielded ordinary Ruckig, safety-shielded Ruckig, and direct one-step
   methods. Its P/PV/PVA target-component comparison uses the same estimator,
   predictor, horizon, one-step governor, direct follower, and plant. Formal
   comparison rejects mixed method identity.
5. **Phase A compatibility restored in current code.** The ordinary-Ruckig
   compatibility runner uses the historical fixed-grid indexing and native
   state feedback, with regression criteria for RMSE, lag, maximum error, 100%
   native execution, and 0% unexpected fallback. This repair occurred after
   v3 froze and does not revise its recorded values.
6. **Schema and recomputation upgraded.** `otg.sample.v3` records the exact
   command profile and explicit native/shield/fallback identity. V2 artifacts
   remain readable through compatibility migration without inventing profile
   evidence.
7. **Frozen v3 reclassified, not rewritten.** In the locked-test fallback
   table, `deployed_p_only`, `predicted_p`, `raw_predicted_pv`, and
   `scalar_projected_pva` were replaced on 96.00%, 96.00%, 95.93%, and 97.21%
   of cycles. Their existing names and numbers remain immutable. The detailed
   disclosure is in `V3_POSTREVIEW_ADDENDUM.md`; the machine-readable status is
   `protocol_status_v3_postreview.json`.
8. **No v4 was executed.** The observed 77.38% v3 result is no longer retained
   as a confirmatory primary conclusion. A future confirmatory target-component
   claim requires a fresh, prelocked v4 with new identities, seeds, and
   family/seed pairs, and a one-shot same-follower P/PV/PVA test.

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

Direct constant-jerk followers validate before committing, and a failed direct
candidate causes an actual safety-controller action to be generated and
validated through the same path. `fallback_applied=true` therefore describes
the command that was executed, not a status-only label.

The current ordinary-Ruckig path instead audits the native frozen-trajectory
prefix with its piecewise-constant-jerk profile. Unshielded methods cannot
silently change algorithms; shielded methods explicitly record the replacement
controller and actual algorithm. Solver exceptions, profile-audit failures, and
follower/governor memory synchronization retain explicit failure semantics.

The fresh locked test contains 42,199 primary direct-candidate command cycles:
continuous V/A/internal-J violations are zero, fallback and projection are
zero, nonfallback point/one-step/sequence rates are 100%, and runtime deadline
misses are zero. Independent sample recomputation verified all 1,012,776 rows.

## Finding 4: canonical schema and recomputation

The current canonical schema is `otg.sample.v3`. It retains the v2 separation
of raw-target, executable-target, and committed-command feasibility, and adds
profile-aware execution semantics: exact segment boundaries and jerks, profile
endpoint matching, profile extrema, native follower and actual algorithm,
shield request/application, and algorithm-changing fallback identity.
`command_constant_jerk_exact` is applicable to constant-jerk profiles and null
for Ruckig piecewise profiles. `sampled_jerk` is a deprecated alias of
`acceleration_difference_jerk`; neither is Ruckig internal-profile jerk.

The frozen v3 protocol artifacts were written with `otg.sample.v2` and remain
unchanged. Compatibility loading extends those rows with unknown/null v3
profile and method-identity evidence rather than manufacturing it. New profile
claims therefore require new v3-schema rows.

At the formal v3 lock, final QA independently recomputed every then-available
feasibility field plus trajectory and summary metrics from canonical Parquet.
The locked bundle still has 25 indexed artifacts and 24 verified checksum
entries; post-review changes interpretation of the affected comparison, not
artifact integrity.

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

`protocol_status_v3.json` freezes the completed result and remains unchanged.
`protocol_status_v3_postreview.json` adds the post-review classification without
modifying that original status. The v3 test was not rerun and must not be rerun
for another confirmatory claim. A reviewer may reconstruct the artifact with
the recorded command, but such a reconstruction is not a second independent
confirmation.

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

The frozen table reports a 77.38% paired position-RMSE improvement for
`one_step_governed_pva_direct` versus `predicted_p`, with a 95% interval of
69.96%-84.44%. The observation and all negative strata are retained, but the
comparison is now classified only as an exposed exploratory regression:
`predicted_p` executed the one-step fallback on about 96% of cycles. It is
neither confirmatory ordinary-Ruckig-versus-governed-PVA evidence nor a clean
same-follower P-versus-PVA ablation. The frozen lag, maximum-error, and
rapid-reversal outputs remain available under the same classification.

The one available 19.38 s CSV remains development-only. In frozen v3, its
ordinary-P baseline also failed historical compatibility (RMSE approximately
`0.285547` versus `0.035187`). Separately, the candidate fails all three strict
preregistered targets: RMSE `0.21996 > 0.02991`, lag `0.12 > 0.03`, and maximum
error `0.66831 > 0.184528`. Those three candidate failures account for the
15/18 required-component result; the post-review semantics confound was not
represented in the preregistered gate. Current code restores the compatibility
regression, but that post-freeze code result does not change frozen v3. No
real-stream generalization, deployment benefit, or production-safety claim is
made.

## Verification and cleanup

- Unit/integration/property/adversarial suite at the formal lock: 331 passed.
- Current post-review full suite: 355 passed; repository-wide Ruff and diff
  checks passed.
- The Phase A compatibility probe passed with Ruckig 0.17.3 and 0.19.4 at
  RMSE `0.035186991`, lag `0.070 s`, maximum error `0.184528428`, native
  execution rate `1.0`, and unexpected fallback rate `0.0`.
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
- [x] Profile-aware schema v3 and asynchronous timestamp semantics tested.
- [x] v1/v2 exposure is explicit; v3 freshness and no-test-selection guards pass.
- [x] Frozen v3 post-review classification added without rerunning or rewriting it.
- [x] Unshielded, shielded, and direct method identities are explicit.
- [x] QP qualification gate passes without exceeding the control budget.
- [x] Complete trajectory denominators and stratified negative results retained.
- [x] Primary locked-test sample evidence is publicly downloadable and hashed.
- [x] CI, artifact checksums, root index, and full locked-bundle recomputation pass.
- [ ] Three preregistered development-CSV candidate criteria pass (currently failed).
- [ ] A fresh confirmatory same-follower P/PV/PVA locked test exists (v4 not run).
- [ ] Independent real locked test and robot/HIL evidence exist (external blockers).

Disposition: the code and experiment infrastructure repairs are Ready for
reviewer assessment/merge. The affected frozen v3 primary comparison remains
non-confirmatory, no v4 was executed, and this response does not authorize or
perform an automatic merge; the reviewer makes the final decision.
