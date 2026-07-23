# Paper Evidence V4 experiment protocol

This is the preregistered, narrow, exactly-once protocol for
`synthetic-feasible-v4`. Its sole scientific question is whether target
components P, PV, or PVA change position reference-following performance when
the estimator, predictor, horizon, one-step bounded-jerk governor, direct
executable follower, ideal plant, current-state policy, motion limits, input
position stream, population, fallback policy, and runtime policy are held
fixed. No V4 test trajectory has been generated or viewed at design status
`designed_not_locked`.

The base pulled `main` used for design is
`1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe`. V1/V2/V3 evidence, statuses,
locks, manifests, raw bundles, release assets, and checksum sidecars are
immutable historical evidence. V4 writes only under
`results/paper_evidence_v4/`; development dry-runs write only under
`results/paper_evidence_v4_development/`.

## Fixed scientific condition

- control rate 100 Hz; `DT=0.01 s`; minimum duration `0.01 s`;
- limits `|v|<=4.1 rad/s`, `|a|<=8.2 rad/s^2`, `|j|<=4000 rad/s^3`;
- ideal plant and `previous_command` current-state policy;
- initial position is the first reference position, with velocity and
  acceleration zero;
- sample schema `otg.sample.v3`; trajectory is the inferential unit;
- `target[k] -> command[k+1]`; metrics use analytic truth at `command_time`;
- estimator `local_poly_w5_d3_lag1`: window 5, degree 3, lag 1, nominal
  `DT=0.01 s` (lock serialization `0.010000000000000009`);
- predictor `constant_jerk`, no parameters, prediction horizon `H=0 ms`;
- one-step governor defaults are explicitly frozen in the formal configs;
- direct executable follower requires `T_free<=DT`; clean primary fallback,
  projection, and emergency rates must be zero.

These values agree with `config_lock_v3.json` and current code. H=0 does not
re-prove future-position prediction. It isolates target-component value while
Phase A oracle and existing prior evidence retain responsibility for
future-reference timing. Neither validation nor test may reselect the
estimator, predictor, H, governor, target mode, metric, population, or
threshold.

## Formal method scope

The primary matrix contains exactly:

1. `one_step_governed_p_direct`;
2. `one_step_governed_pv_direct`;
3. `one_step_governed_pva_direct`.

Their parsed pipeline objects must differ only in `target_mode` (`p`, `pv`,
`pva`). `validate_method_matrix_identity` or a stricter V4 validator must prove
this before any run and against actual sample rows.

Contextual secondary ordinary-Ruckig methods are
`deployed_p_only_ordinary_ruckig`, `predicted_p_ordinary_ruckig`,
`raw_predicted_pv_ordinary_ruckig`, and
`raw_predicted_pva_ordinary_ruckig`. They are unshielded and have no hidden
fallback. A native solve failure is a failed unit; the denominator remains
visible and incomplete pairs are unavailable for paired inference.

The oracle diagnostic contains `oracle_one_step_p_direct`,
`oracle_one_step_pv_direct`, and `oracle_one_step_pva_direct`. It uses the same
target time, governor, follower, plant, and limits. The locked estimator is
evaluated only to preserve timing and DoF plumbing; it is bypassed as a target
information source, and the prediction consumed by target construction is
replaced by analytic synthetic truth at that same target time. Every oracle
row is labelled `information_condition=offline_analytic_truth`,
`causal=false`, `deployable=false`, and `diagnostic_only=true`. Oracle evidence
cannot enter the primary comparison, deployable ranking, Holm family, or
selection.

Excluded work includes Ruckig Pro, `Trackig`, `Tracking`, commercial capability
detection/placeholders/baselines, new estimator/predictor/governor or QP/MPC
tuning, horizon or limit sweeps, real robot/HIL, new real-data collection, V3
reruns or edits, and result-dependent changes.

## Fresh data and manifest boundary

The generator preserves the existing six-family feasible synthetic
distribution, trajectory durations, truth construction, demand rules, limits,
and at least 1 kHz internal truth. Counts per family are train 20, validation
10, test 20, for totals 120/60/120. Each family test block has five trajectories
in each of `low`, `medium`, `high`, and `near_limit`.

IDs are `<family>__v4__<split>__<index>` and dataset ID is
`synthetic-feasible-v4`. The V4-only SHA-256 namespace is committed before any
trajectory generation. Namespace attempts are append-only in
`v4_seed_namespace_history.json`; a namespace may change only on an exact
historical seed collision. Manifest generation may derive identities but may
not instantiate, render, or run trajectories.

Before lock, train and validation may be generated; test may not. Freshness
must compare V4 with all V1/V2/V3 entries for trajectory ID, seed, family-seed,
dataset ID, split identity, and namespace hash. Every overlap count must be
zero, with negative tests for each overlap class.

## Pretest workflow and permitted repairs

First, the complete V4 pipeline is dry-run on exposed V3 train/validation and,
only if necessary, exposed V3 test labelled development-only. It exercises
primary, ordinary, oracle, incomplete-secondary handling, samples, statistics,
figures, packaging, checksums, and independent recomputation. Its artifacts
never enter V4 confirmation or paper tables.

Dry-run repairs are limited to crashes, schema errors, artifact omissions,
incorrect metric implementation, incorrect method identity, incorrect time
alignment, packaging errors, and checksum errors. Every repair is logged in
`V4_PROTOCOL_DECISIONS.md`. Dry-run evidence cannot change estimator,
predictor, H, governor, weights, limits, family distribution, primary metric,
acceptance threshold, subgroup, target mode, or test population.

Next, the validation canary runs all 120 train and 60 validation trajectories
through primary, contextual ordinary, and oracle matrices. It may confirm
pipeline/artifact/statistics/runtime integrity, method identity, absence of
unexpected fallback on clean inputs, and descriptive variance/CI width. It may
not select any scientific condition or delete an adverse family.

Only after all P0/P1 pretest defects are fixed does the completed lock record
the exact clean scientific source commit, environment versions, hashes,
canonical selection, `locked=true`, and `test_trajectory_count_seen=0`. The
parsed `locked_selection` object in all three formal configs must be deeply
identical to the lock. A dedicated authorization commit may change only the
lock and root status; its parent must be the scientific source commit. The
precommitted immutable ref
`refs/tags/paper-evidence-v4-confirmation-source` is then created at that
authorization commit. This resolvable ref avoids the impossible claim that a
file can contain the SHA of the commit whose tree includes that same file.
The authorization commit, ref, and clean worktree precede test visibility.

## Statistical analysis

The primary comparison, guardrails, secondary S1-S5 family, fixed seeds,
10,000-resample paired percentile bootstrap, Holm correction, effect sizes,
subgroups, harmful rate, worst-five rule, boundary classifications, and plot
selection are normative in `V4_STATISTICAL_DESIGN.json`. The acceptance gates
are normative in `V4_ACCEPTANCE_CRITERIA.json`.

No trajectory may be deleted. Primary inference requires the exact 120
manifest IDs in both P and PVA. A missing/failed primary pair yields
`unavailable_incomplete_denominator`, not complete-case analysis. All negative,
harmful, failed-family, failed-guardrail, failure, fallback, and runtime-outlier
evidence is retained.

Representative trajectories are selected mechanically: closest to median P
RMSE, closest to median paired P-minus-PVA improvement, worst PVA RMSE, maximum
PVA harm, and manifest index 0 for each family, with trajectory-ID tie breaks.
Figures are grayscale-readable, color-blind friendly, show full denominators,
use `command_time`, and label locked synthetic, oracle, and ordinary contexts.

## Exactly-once state machine

Allowed states are:

```text
designed_not_locked
validation_running
locked_test_unseen
confirmation_running_test_visible
complete_confirmatory
complete_negative
failed_test_visible_frozen
failed_pretest_repairable
```

The only formal command is:

```bash
uv run python run_paper_evidence_v4.py confirm
```

Every test-consuming internal command requires a non-serializable in-process
capability created only by `confirm` and cleared in `finally`. Direct calls and
serialized/replayed capabilities are rejected.

Preflight requires a clean worktree, the immutable confirmation ref resolving
exactly to HEAD, HEAD's parent equalling the locked scientific source commit
with only the two authorized metadata files changed, `locked=true`,
`test_trajectory_count_seen=0`, the required locked validation bundle, absent
locked-test/oracle/formal-report outputs, matching hashes, zero freshness
overlap, identical consumer selection, unchanged V3 frozen hashes, no
prohibited method/import/config, no override, no test cache, and no prior V4
test artifact.

After revalidating the lock, confirm generates the test for the first time,
sets `test_visible=true`, runs primary then ordinary then oracle, writes
per-sample artifacts and metrics, computes and independently recomputes
statistics, builds a bounded report and evidence ZIP, then writes status, root
index, and SHA-256. Each bundle uses atomic staging and is promoted only after
checksum, schema, independent profile/feasibility/metric/statistics checks.

Once any V4 test trajectory is generated or viewed, algorithm, data, metric, or
raw-stage failure is terminal: preserve evidence, set
`failed_test_visible_frozen`, do not resume or rerun, and use V5 for another
confirmation. A protocol-complete nonpositive hypothesis result is
`complete_negative`, not a raw-stage failure.

## Narrow report-only resume

Report-only resume is allowed only after the complete immutable primary raw
bundle, verified sample checksums, and independent recomputation exist, and the
failure is strictly final Markdown/figure/ZIP packaging. It requires the exact
40-character raw commit and full raw revalidation. The resume path cannot call
the generator or pipeline, alter raw artifacts or numbers, or execute any
experiment stage. No other resume is allowed.

## Identity, safety, runtime, and evidence gates

Every primary cycle must attest direct constant-jerk semantics, direct native
execution, no shield, no algorithm-changing fallback, exact constant-jerk
profile and endpoint, and continuous constraints. Each primary trajectory
requires method purity 1.0.

The same-information audit compares every trajectory/input cycle. Measurement,
estimator/posterior, predictor/prediction, H, governor, follower, plant,
initial-state policy, current-state policy, control time, limits, and raw target
position must match. P zeros target v/a; PV uses predicted v and zeros a; PVA
uses predicted v/a. Controllers may naturally develop different endogenous
current states after issuing different commands.

Primary validity requires 120 attempted and completed per method, zero failed,
paired denominator 120, zero projection/fallback/emergency/continuous
violations/unexplained NaN/hidden replacement, and 100% purity, endpoint match,
point admissibility, stopping viability, next-step existence, `T_free<=DT`,
and sequence consistency. Failure retains data but prohibits a conforming
confirmatory performance claim.

Runtime is an independent five-repetition study, dropping 100 warmup cycles per
trajectory, recording each synchronized cycle once and excluding plotting,
disk I/O, and hashing. Estimator, predictor, governor, follower, plant, and
total times are retained with CPU/OS/Python/Ruckig/BLAS/thread/affinity
metadata. Candidate gates are total P99 <1 ms, max <5 ms, and zero deadline
misses. Outliers are never deleted and locked test is never rerun.

Ordinary-Ruckig identity/profile outputs distinguish internal piecewise jerk
from acceleration-difference diagnostics. Phase A compatibility remains a
regression gate with RMSE about `0.035186991`, lag `0.070 s`, maximum error
about `0.184528428`, native execution 100%, and unexpected fallback 0%; it is
not a V4 test result.

## Artifact and claim disposition

Required raw bundles include resolved config, manifests, method/expected-unit
matrices, samples Parquet, metrics, constraints, runtime, failures, fallbacks,
completion, indexes, and checksums. Root outputs include all preregistered
statistics, audits, oracle/ordinary diagnostics, figures, paper handoff,
generated TeX inputs, release-ready archives, and a complete root index.

`complete_confirmatory` means protocol integrity and interpretable unused-test
inference; it does not mean PVA helped. `complete_negative` preserves an
inconclusive or harmful result. Claim wording is generated from the locked
classification and guardrails. Oracle never supports an online claim, and a
favorable subgroup never overrides the overall result.
