# Evidence inventory

Audit basis: repository `TANG617/otg_lab`, branch
`paper/arxiv-stage-draft-v0`, HEAD
`8faedae1fe18111ad0329259b5618c06edf6020b`, audited on
2026-07-24 UTC. No Phase A, v3, or V4 experiment was rerun or resumed during
this paper audit. V4 had already executed exactly once and entered main as
frozen evidence.

The machine-readable source registry is
[`evidence_sources.yaml`](evidence_sources.yaml). Exact numeric selectors and
verification records are in [`evidence_audit.json`](evidence_audit.json).

## Audit conclusion

The repository supports a paper centered on timing semantics, layered
estimation/prediction/governance/execution, controlled Phase A target-state
ablation, current exact bounded-jerk construction, and frozen synthetic
direct-method safety/runtime evidence. A fresh V4 same-follower test now adds a
large observed PVA-over-P difference, but it does not support a confirmatory
PVA performance conclusion: protocol status is
`failed_test_visible_frozen`, statistical classification is
`strongly_material`, and effective classification is
`invalid_method_identity`.

The V4 same-information audit failed on 5/42,072 aligned cycles. All five
differences are composite `event_flags` entries whose only differing token is
`deadline_miss`; all other compared fields, shared configuration identity, and
direct-method purity passed. This narrows the diagnosis but does not alter the
frozen gate. The full instrumented Python runtime gate also failed.

The frozen v3 performance point estimate of 77.38% is byte-preserved but
scientifically confounded: the condition named `predicted_p` executed a
one-step replacement fallback on 40,510 of 42,199 cycles (95.9975%). It is
therefore only an exposed exploratory regression of a mixed baseline. The
frozen direct-method safety result is separable from that confound and remains
valid within the recorded synthetic triple-integrator protocol.

## Source inventory

| Source ID | Class | Lifecycle | Primary scientific role | Principal boundary |
|---|---|---|---|---|
| `E_PHASE_A_TRACKING` | `confirmed_current` | checked-in Phase A output | P/PV-truth/PVA-truth analytic ablation | deterministic three-reference, single-joint result; historical dirty generation worktree |
| `E_PHASE_A_DERIVATIVES` | `confirmed_current` | checked-in Phase A output + current semantics tests | derivative accuracy and causality | CSV has no derivative truth |
| `E_PHASE_A_ORACLE` | `confirmed_current` | checked-in Phase A output | next-cycle timing sanity control | noncausal oracle, not deployable |
| `E_PHASE_A_LIMITS` | `confirmed_current` | checked-in Phase A output | acceleration/jerk OFAT sensitivity | no interaction or deployment recommendation |
| `E_REAL_CSV_NEGATIVE` | `negative_current` | development-only | raw finite-difference negative result and target diagnostics | one fixed-grid CSV, no independent test |
| `E_V3_DIRECT_SAFETY` | `confirmed_frozen_scope` | frozen v3 | direct constant-jerk V/A/J, reachability, fallback, and projection audit | synthetic ideal-plant scope only |
| `E_V3_RUNTIME` | `confirmed_frozen_scope` | frozen v3 | measured compute distribution and deadline counts | measured runtime is not WCET |
| `E_V3_ARTIFACT_INTEGRITY` | `confirmed_frozen_scope` | frozen v3 + post-review interpretation | checksums, denominators, failures, and release provenance | integrity does not validate an interpretation |
| `E_V3_CONFOUNDED_COMPARISON` | `exploratory_confounded` | frozen v3, reclassified post-review | exposed 77.38% mixed-baseline regression | discussion/evidence-correction/appendix only |
| `E_V3_ACCELERATION_NEGATIVE` | `confirmed_frozen_scope` | frozen v3 negative evidence | estimated same-future PVA-versus-PV diagnostic | not a universal acceleration result |
| `E_POSTFREEZE_RUCKIG_COMPATIBILITY` | `confirmed_current` | post-freeze regression record | restored Phase A P-only ordinary-Ruckig compatibility | not a v3 rerun or fresh locked test |
| `E_CURRENT_PROFILE_AWARE_INFRASTRUCTURE` | `confirmed_current` | post-freeze current code | method identity, profile, timing, and exact-kinematics semantics | code/test evidence, not fresh performance evidence |
| `E_V4_PROTOCOL` | `confirmed_frozen_scope` | pretest locked protocol | fresh/exactly-once/same-follower design and gates | protocol does not establish benefit |
| `E_V4_FRESH_LOCKED_TEST` | `confirmed_frozen_scope` | frozen V4 | 120/120 whole-trajectory execution and freshness | synthetic only; final status is failed-test-visible-frozen |
| `E_V4_PRIMARY_OBSERVED_EFFECT` | `nonconfirmatory_frozen` | frozen V4 | observed PVA-versus-P RMSE estimate | failed validity gate prohibits confirmatory/causal benefit |
| `E_V4_METHOD_PURITY` | `confirmed_frozen_scope` | frozen V4 | native direct identity and purity | purity pass does not repair same-information failure |
| `E_V4_SAME_INFORMATION_FAILURE` | `nonconfirmatory_frozen` | frozen gate + report-only aid | 5/42,072 deadline-miss-only composite-flag diagnosis | no deletion, gate edit, or rerun |
| `E_V4_SAFETY` | `confirmed_frozen_scope` | frozen V4 | completion/fallback/continuous-constraint audit | synthetic execution evidence, not hardware safety |
| `E_V4_LAG_GUARDRAIL` | `nonconfirmatory_frozen` | frozen V4 | lag noninferiority assessment | neither lag improvement nor increase established |
| `E_V4_RUNTIME_FAILURE` | `confirmed_frozen_scope` | frozen V4 | instrumented full-Python runtime gate | not WCET or a compiled/isolated limit |
| `E_V4_HARMFUL_TRAJECTORIES` | `nonconfirmatory_frozen` | frozen V4 | adverse primary rows retained | no favorable-only reporting |
| `E_V4_SUBGROUPS` | `nonconfirmatory_frozen` | frozen V4 | family/demand descriptive heterogeneity | descriptive secondary only |
| `E_V4_ORDINARY_CONTEXT` | `confirmed_frozen_scope` | frozen V4 | contextual ordinary-Ruckig identity/completion | S5 incomplete at 108/120; not primary |
| `E_V4_ORACLE_CONTEXT` | `confirmed_frozen_scope` | frozen V4 | target-component diagnostic | offline, noncausal, nondeployable |
| `E_V4_ARTIFACT_INTEGRITY` | `confirmed_frozen_scope` | frozen bounded tree | commit/hash/status/no-rerun chain | integrity does not restore confirmation |

## Verified quantitative candidates

### Phase A: reliable velocity and the acceleration non-result

All values below are selected from
`results/vendor_target_state_ablation/target_state_ablation_metrics.csv` with
`experiment=baseline`. The evaluation begins at sample 3 and stops before
sample 301 for each analytic reference.

| Reference | P RMSE (rad) | PV-truth RMSE (rad) | PVA-truth RMSE (rad) | P lag (ms) | PV/PVA lag (ms) | P-to-PV RMSE reduction |
|---|---:|---:|---:|---:|---:|---:|
| quadratic with extremum | 0.075666572587 | 0.009275317577 | 0.009275317577 | 80 | 10 | 87.7419% |
| cubic | 0.013624217608 | 0.003453279162 | 0.003453279162 | 40 | 10 | 74.6534% |
| sine | 0.049705339994 | 0.006750305942 | 0.006750305942 | 70 | 10 | 86.4194% |

The maximum absolute PV-truth/PVA-truth RMSE difference across these three
rows is `1.7316443418069483e-14 rad`. This supports a tested-condition
non-result: adding analytic acceleration did not improve position RMSE or lag
over analytic velocity. It does not establish equivalence outside the tested
references.

The analytic reference peaks are all below the configured
`4.1 / 8.2 / 4000` limits:

| Reference | max \|v\| (rad/s) | max \|a\| (rad/s²) | max sampled \|j\| (rad/s³) |
|---|---:|---:|---:|
| quadratic with extremum | 1.506168013580 | 4.785156250000 | 13.465995800214 |
| cubic | 0.591730070429 | 1.471206765196 | 7.533271984544 |
| sine | 1.695151035999 | 6.364585446405 | 40.073043946225 |

Allowed wording: “observed,” “reduced under the tested conditions,” and
“did not improve within numerical precision.” Prohibited wording:
“universally proves,” “statistically significant,” or “PVA superiority.”

### Phase A derivative timing

`centered_fd_offline` is marked `causal=False`, `future_samples=1`, and
`native_delay_samples=0`. `centered_fd_causal_delay1` is marked
`causal=True`, `future_samples=0`, and `native_delay_samples=1`. Current source
and regression tests confirm that changing samples after index \(k\) cannot
change the causal centered estimate through \(k\).

The offline centered derivative may be used as a noncausal numerical
diagnostic. It may not be presented as a zero-delay online estimator.

### Phase A next-cycle oracle

All three `oracle_next_cycle` rows are noncausal (`future_samples=1`), have
zero grid-searched lag, zero target projection, and
`reachable_within_10ms_rate=1`.

| Reference | RMSE (rad) | max error (rad) | lag (ms) |
|---|---:|---:|---:|
| quadratic with extremum | 4.478563363489276e-16 | 7.549516567451064e-15 | 0 |
| cubic | 6.115772274568255e-11 | 1.0557457408300053e-09 | 0 |
| sine | 2.6784562716995654e-14 | 4.623686125093956e-13 | 0 |

The largest maximum error is `1.0557457408300053e-09 rad`. This is a
timing/indexing sanity control, not an online-prediction result.

### Development CSV negative result

The trace has 1,936 input rows. Only `value` is used; elapsed time,
timestamps, and topics are intentionally ignored and each row is treated as
10 ms. The evaluation interval is `[3,1936)`. There is no velocity or
acceleration truth.

| Method | Causal status | RMSE (rad) | lag (ms) | max error (rad) | raw-target projection rate |
|---|---|---:|---:|---:|---:|
| P | causal | 0.035186991002 | 70 | 0.184528428490 | 0 |
| PV backward | causal | 0.063978322134 | 110 | 0.327128994740 | 0 |
| PVA backward | causal | 0.038740991464 | 70 | 0.240492424248 | 0.326435592344 |
| PV centered offline | noncausal, one future sample | 0.067225267104 | 130 | 0.332870454246 | 0 |
| PVA centered offline | noncausal, one future sample | 0.044700668497 | 80 | 0.260779275486 | 0.326435592344 |
| PV centered causal delay-1 | causal | 0.078560711089 | 160 | 0.466187994138 | 0 |
| PVA centered causal delay-1 | causal | 0.044108542245 | 80 | 0.243132011779 | 0.326435592344 |

Every tested unfiltered finite-difference target has higher RMSE than P under
this protocol. The three PVA finite-difference rows have raw target
feasibility `0.6735644076564925`, projection rate
`0.3264355923435075`, and raw differentiated acceleration peak
`280.09102085992384 rad/s²`. These are reference-target diagnostics, not
measured robot acceleration or plant safety violations.

Allowed conclusion: unfiltered finite differences did not improve this
development trace and often produced inadmissible acceleration targets.
Forbidden conclusion: derivatives are useless or the result generalizes to
independent real trajectories.

### Phase A OFAT boundaries

For the CSV P baseline:

- `jmax=41`: RMSE `0.08055747717788837 rad`, lag `190 ms`;
- `jmax=4000`: RMSE `0.03518699100246076 rad`, lag `70 ms`;
- `jmax=8000`: RMSE `0.03486781722632228 rad`, lag `70 ms`.

Thus the `jmax=41` RMSE is 2.2894 times the vendor-point result and lag is
120 ms greater. Doubling jerk from 4000 to 8000 yields an RMSE ratio of
0.9909 with unchanged grid lag. These are OFAT observations, not recommended
deployment parameters.

### Frozen v3 direct safety

The audit independently streamed the raw locked-test tables, rather than
copying the generated prose summary.

| Quantity | Verified value |
|---|---:|
| complete direct trajectories | 120 |
| direct command cycles | 42,199 |
| continuous V/A/internal-jerk violations | 0 |
| fallback events | 0 |
| projection count | 0 |
| governor deadline misses | 0 |
| follower deadline misses | 0 |
| total/recorded deadline misses | 0 |
| max continuous \|v\| | 4.1 rad/s |
| max continuous \|a\| | 8.2 rad/s² |
| max internal \|j\| | 1061.0631508836043 rad/s³ |
| minimum velocity margin | 0 rad/s |
| minimum acceleration margin | 0 rad/s² |
| minimum jerk margin | 2938.936849116396 rad/s³ |
| one-step-reachable nonfallback commands | 42,199 / 42,199 |
| adjacent sequence-consistent transitions | 42,079 / 42,079 |

The command-cycle denominator includes the initial recorded command sample in
each trajectory; adjacent-transition denominators are therefore lower by one
per trajectory. The claim is limited to the frozen single-joint synthetic
protocol and exact constant-jerk command model. It establishes neither plant
dynamics safety nor production safety.

### Frozen v3 direct runtime

The primary benchmark filters the locked-test `samples.parquet` to
`method_id=one_step_governed_pva_direct` and `k>=100`, leaving 30,199 timed
cycles:

| Statistic | End-to-end runtime |
|---|---:|
| P50 | 785.750 µs |
| P90 | 804.666 µs |
| P99 | 874.91866 µs |
| P99.9 | 1522.043882 µs |
| maximum | 2113.25 µs |
| 10 ms deadline misses | 0 |

Five repeated six-trajectory subsets each contain 1,620 cycles. Their P99
values range from `871.07119` to `881.18854 µs`, maxima range from
`942.125` to `1291.0 µs`, and all recorded zero deadline misses.

These are measured distributions on one recorded Python experiment
environment, not a worst-case execution-time proof.

### Frozen v3 confounded comparison

The point estimate is reproducible from the 120 paired trajectory rows:

- baseline mean RMSE: `0.17766167971631203 rad`;
- candidate mean RMSE: `0.04019145979209921 rad`;
- observed relative improvement: `0.7737753022695921`;
- frozen 95% paired bootstrap interval:
  `[0.6996349531461117, 0.8443749153621228]`;
- expected, observed, and paired trajectory count: 120; excluded: 0.

The actual baseline execution invalidates the intended confirmatory
interpretation:

| Named condition | fallback cycles / total | fallback rate |
|---|---:|---:|
| `deployed_p_only` | 40,513 / 42,199 | 96.0046% |
| `predicted_p` | 40,510 / 42,199 | 95.9975% |
| `raw_predicted_pv` | 40,482 / 42,199 | 95.9312% |
| `scalar_projected_pva` | 41,022 / 42,199 | 97.2108% |

For `predicted_p`, 40,508 fallbacks were labelled
`ruckig_command_not_one_step_reachable` and two were labelled
`ruckig_command_no_viable_next_step`. The frozen implementation incorrectly
compressed a potentially multi-segment Ruckig prefix into one average-jerk
  segment. Current code repairs that audit, but no new locked comparison of
  that repaired ordinary-Ruckig path was run; V4's primary matrix instead uses
  the same direct follower for P/PV/PVA. Consequently, 77.38% may appear only
  as an exposed exploratory,
mixed/confounded regression in Discussion, Evidence Correction, or an
appendix. It is forbidden in the title, abstract, contribution list, and
principal conclusion.

### Frozen acceleration negative evidence

The acceleration diagnostic contains 224 same-future estimated PV/PVA pairs.
`acceleration_target_harmful=True` for 220 and false for four, a harmful rate
of `0.9821428571428571`. Mean position RMSE is
`0.019252032271195256 rad` for estimated PV and
`0.020286205277966096 rad` for estimated PVA. This preserves a negative
observation under the recorded information condition; it does not prove that
truth acceleration or all acceleration targets are harmful.

### Post-freeze P-only compatibility

`protocol_status_v3_postreview.json` records:

- RMSE `0.03518699100246076 rad`;
- lag `0.07 s`;
- max error `0.18452842848970727 rad`;
- native execution rate `1.0`;
- unexpected fallback rate `0.0`.

This restores the Phase A P-only ordinary-Ruckig regression after the v3
freeze. It is not a v3 rerun. The result record predates the final
`e774c74` requested-versus-committed-duration provenance change, and this
audit did not execute the compatibility probe at HEAD.

### Frozen V4 same-follower attempt

V4 executed once at confirmation source commit
`461fc560461b0a4726cbabdb97b2dbd4dc305e0a`; bounded results were published at
`f49b4ef1cacf8228c5d243353184acb8a7d02311`; the same-information reviewer aid
is report-only commit `b9301eaf36dc04f1abf662c42821eddfe8c3188a`.
These provenance epochs must not be collapsed.

The primary direct P/PV/PVA matrix used six families with 20 locked test
trajectories per family. All primary methods completed 120/120 trajectories
and each recorded 42,072 cycles with direct method-purity rate 1.0.

The 120/120 PVA-versus-P data contain an observed relative RMSE improvement of
`0.8241228581049881` (95% paired-bootstrap CI
`[0.7827192996982065,0.8614461930812837]`) and absolute improvement
`0.11088274139512694 rad`. This estimate is statistically
`strongly_material`, but scientifically non-confirmatory because the
preregistered same-information gate failed. The effective classification is
`invalid_method_identity`.

The failure is narrow but binding:

- 5/42,072 aligned cycles failed the composite `event_flags` comparison;
- `deadline_miss` is the only differing token;
- configuration identity and every other compared field passed;
- direct-method purity and the synthetic safety gate passed;
- removing or ignoring the five entries is prohibited.

Five of 120 primary trajectories were harmful under the frozen direction;
four are `rapid_reversal` and one is `oscillatory`. `rapid_reversal` is also
the weakest family-level descriptive result. These adverse rows remain
published.

The lag point estimate did not indicate an average increase, but the 95% CI
upper bound exceeded the preregistered 10-ms worsening margin, so lag
noninferiority was not established. The full instrumented Python runtime gate
failed for every direct method. These results do not prove that lag improved,
that lag increased, or that an isolated/compiled implementation can never meet
100 Hz.

Ordinary Ruckig is contextual secondary evidence only. Its raw-PVA arm
completed 108/120, so S5 formal paired inference is unavailable. The oracle
arm is offline, noncausal, nondeployable, diagnostic-only, and excluded from
the primary conclusion.

Allowed V4 performance wording:

> The V4 result was retained, but a failed validity gate prohibits a
> confirmatory performance claim.

## Integrity and provenance checks

- `artifact_index.json` SHA-256 is
  `12393579515e144f8cb499144772471e3a0398d8d2e19bdff89ff0fa7c479933`,
  matching its sidecar and both v3 status records.
- All 68 bounded artifact paths were present and matched the SHA-256 values
  in the root index.
- All nine raw-bundle `artifact_index.json` paths were present and matched the
  root hashes stored in the root index.
- The frozen protocol SHA-256 is
  `25a273d2100e855019c3f416d0aa3c5f61df00772f86e7f15f489ce7deb39eb6`.
- The original frozen status SHA-256 is
  `f0eed71bb5d3b2ac06e00fa933a7b6108012cbcde3c7fc3476a4d99404e52692`.
- The release archive is recorded as 253,777,047 bytes with SHA-256
  `3f63ff81e708925c4d8c55616585e9b9925c43e1f59ede637e418944b39b8da2`.
  This audit did not download or independently hash the external archive.
- V3 completed one formal confirmation and is frozen. No rerun or resume is
  permitted. Fifteen of 18 required criteria passed; all three failures are
  development-CSV candidate criteria.
- The multi-DoF bundle retains one fail-closed
  `12 DoF × different_frequency` trajectory failure; its completion rate is
  0.9666666666666667. It must not be described as a fully successful bundle.
- V4 root artifact-index SHA-256 is
  `fd78eb559d039620ae1c6e06faac44ab6fc8dbff9208c05523b4efcab4a75a95`;
  the sidecar SHA-256 is
  `96fbd8d2dc165beca47b40dd2ecb8eb46f6ae1be7f095974cc69e1ae2c9b9582`.
- V4 preregistration status SHA-256 is
  `c0c3d358c969dbb343ac05dc964075a514f37d8153ce47d6e4ca60a252de4909`;
  config-lock SHA-256 is
  `d61b0f8596b04358c7bef6a1e43b6775b3dbb00020c2aca28d5d2cd4d9f6f3d3`;
  split-manifest SHA-256 is
  `1727505734c8026ed18d87123d5d5a8c02e2f201a33ea786fbcde2c9ab398796`.
- V4 final protocol status is `failed_test_visible_frozen`; same-test
  rerun=false and raw experiment resume=false.

## Current implementation evidence

Current HEAD implements:

- separate state time and availability time;
- separate posterior, prediction, raw target, executable target, command, and
  measured plant state;
- exact constant-jerk integration and analytic velocity extrema;
- point V/A admissibility, stopping viability, one-step reachability, and
  next-step existence as distinct predicates;
- exact or explicitly sampled command profiles;
- piecewise-constant-jerk native Ruckig-prefix auditing;
- separate requested-target and committed-command free durations;
- explicit unshielded ordinary Ruckig, viability-shielded Ruckig, and direct
  constant-jerk method identities;
- native-command and algorithm-changing-fallback provenance.

This is evidence for method and data semantics only. No current-head locked
experiment evaluates the repaired profile-aware ordinary-Ruckig comparison.

## Missing or non-independent sources

1. Phase A lacks retained per-cycle output for methods other than the current
   P-only compatibility probe. Aggregate Phase A numbers are checked-in and
   internally consistent, but most cannot be recomputed from raw command
   samples.
2. Phase A `run.json` records `git_worktree_dirty=true`; exact historical
   generator hashes are present, but the corresponding byte-identical
   generator snapshot is not reconstructed at current HEAD.
3. There is only one development CSV, interpreted on a fixed 10 ms grid, with
   no derivative truth and no independent real locked test.
4. There are no hardware, HIL, torque, collision, or plant-model-validation
   artifacts supporting real-robot or production-safety claims.
5. A fresh V4 same-follower P/PV/PVA test exists, but its frozen failed
   validity/runtime gates prohibit confirmatory performance claims. A new
   confirmation would require a fresh V5 protocol/test set and a
   preregistered layered audit definition.
6. There is no measured Ruckig Pro `Trackig` experiment.
7. The externally released primary ZIP digest is recorded but was not
   independently verified in this local audit.

Any manuscript number not present in the numeric candidate ledger, or not
extracted by an exact source/row/field selector, must be treated as
`CITATION_NEEDED`/`EVIDENCE_NEEDED` rather than copied into the paper.
