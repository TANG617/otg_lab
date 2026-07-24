# V4 frozen-evidence audit

Audited at `2026-07-24T02:09:22Z` on
`paper/arxiv-stage-draft-v0` at
`8faedae1fe18111ad0329259b5618c06edf6020b`.

This is a read-only evidence audit. It did not run, resume, or repair a V3 or
V4 experiment, and it did not execute V5. No frozen V3/V4 path was changed.
All values below were re-read from the files currently reachable from `HEAD`;
prompt values were not treated as evidence.

## Audit disposition

**PASS, with the mandatory non-confirmatory boundary.**

The fresh, exactly-once V4 execution and statistical estimation completed over
the complete 120/120 primary paired denominator. The observed primary relative
RMSE difference is `0.8241228581049881` (82.4123% when rendered to four
percentage decimals), with frozen 95% interval
`[0.7827192996982065, 0.8614461930812837]`. Its statistical classification is
`strongly_material`.

The preregistered same-information audit nevertheless failed on five of 42,072
aligned cycles. All five failures are differences in the composite
`event_flags` field, all are `deadline_miss`-only differences, and all other
compared fields plus configuration identity passed on those rows. The direct
method-purity audit separately passed at 1.0 for P, PV, and PVA. Because the
same-information gate was frozen before test visibility, the narrower
diagnosis does not remove the five rows or restore confirmation.

The post-test protocol status is `failed_test_visible_frozen`; the effective
classification is `invalid_method_identity`; same-test rerun is prohibited;
raw-experiment resume is prohibited. The instrumented full Python runtime gate
also failed. The retained V4 effect is therefore an observed,
`strongly_material`, **non-confirmatory** result. It does not establish a PVA
performance benefit.

The exact permitted summary is:

> The V4 result was retained, but a failed validity gate prohibits a
> confirmatory performance claim.

## Git and exactly-once chain

| Role | Commit/ref | Verified relationship |
| --- | --- | --- |
| Pre-authorization scientific source | `53ee562ca2ce22be463b20d6aae8c584911c3dce` | Parent of authorization commit; equals `config_lock_v4.json` `/git/scientific_source_commit` |
| Exactly-once authorization source | `461fc560461b0a4726cbabdb97b2dbd4dc305e0a` | Direct child of scientific source; tagged by peeled `paper-evidence-v4-confirmation-source^{}` |
| Bounded-report archive repair | `9f152fcd69d8c889c0fa2b48368b20e2ad8c348f` | Direct child of authorization source; bounds high-volume reporting output without rerunning raw execution |
| Reporting-provenance repair | `8baece6b7051ccc231d9bb0362fd85e4aa5a94e5` | Child of `9f152fc`; recorded as root-index reporting commit |
| Bounded result | `f49b4ef1cacf8228c5d243353184acb8a7d02311` | Descendant of authorization source; peeled target of `paper-evidence-v4-461fc56^{}` |
| Report-only diagnostic aid | `b9301eaf36dc04f1abf662c42821eddfe8c3188a` | Direct child of bounded result; diff adds only `SAME_INFORMATION_FAILURE_ANALYSIS.md` and `same_information_failures.csv` |
| V4 merge to main | `c97e24dcfd6dd9146755235fa632e08932dc9a78` | Merge commit with `b9301ea` as second parent |
| Main-to-paper merge | `8faedae1fe18111ad0329259b5618c06edf6020b` | Current `HEAD`; contains `c97e24d` as second parent |

All ancestry checks returned success. The Git tree listing for
`results/paper_evidence_v4` plus `results/paper_evidence_v4_release` has the
same SHA-256 (`24854ebfd6829408a1a78687f496f61571a06594554da23d445818c1a2f6945c`)
at `f49b4ef`, `c97e24d`, and current `HEAD`. There is no current working-tree
diff under any frozen V3/V4 path.

The root `protocol_status_v4.json` is deliberately the frozen pretest record:
`status=locked_test_unseen`, `test_visible=false`, and its hash is retained in
the post-test record. It must not be mistaken for the outcome status. The
outcome record is `results/paper_evidence_v4/protocol_status_v4.json`, where
`status=failed_test_visible_frozen` and `test_visible=true`.

## Integrity ledger

### Protocol, lock, manifest, and status

| Path | SHA-256 | Evidence role |
| --- | --- | --- |
| `EXPERIMENT_PROTOCOL_V4.md` | `baad38320593695a4c231f1802faa3a48b4a32b318da841fda5b1354cd8b770e` | Frozen protocol |
| `V4_HYPOTHESES.md` | `50487997bca9ef4a35ddf82edfc0f064e6636413479014224c65b3af04e43f81` | Frozen hypotheses |
| `V4_STATISTICAL_DESIGN.json` | `63a8677591976c436b14e9afee059a7575fd47909c2f18c72a5f515127be2a6c` | Frozen estimands, bootstrap, guardrails |
| `V4_ACCEPTANCE_CRITERIA.json` | `9ed534c8268abd7fa6d1d55b3227e9b0160d7838e0d2bbabd23ab6914bf1fbbb` | Frozen validity and runtime gates |
| `V4_METHOD_MATRIX.json` | `e60c0e79483ac1327de15786c66efbc90b04d0379ee78e5e55ca83c32aea665e` | Declared same-follower method identities |
| `V4_PROTOCOL_DECISIONS.md` | `442f0f8ee8c48ff789e19a3c9bc8c623a6213bfbf944c1ff30a94c8e8ac717d0` | Protocol decisions |
| `config_lock_v4.json` | `d61b0f8596b04358c7bef6a1e43b6775b3dbb00020c2aca28d5d2cd4d9f6f3d3` | Locked configuration and freshness proof |
| `split_manifest_v4.json` | `1727505734c8026ed18d87123d5d5a8c02e2f201a33ea786fbcde2c9ab398796` | 300-identity manifest; 120 train, 60 validation, 120 test |
| `protocol_status_v4.json` | `c0c3d358c969dbb343ac05dc964075a514f37d8153ce47d6e4ca60a252de4909` | Frozen pretest status |
| `results/paper_evidence_v4/protocol_status_v4.json` | `48c98a81a76129a0fc2dd913aabb28bc9312d31a76a4283b27bf1fea9431a34b` | Frozen post-test status |
| `V4_AGENT_EXECUTION_AUDIT.md` | `2dd7433ca27a9a75197393c32c4d55bed85259106c79b96ec86a504cb6067d36` | Previsibility agent audit |

The lock records zero overlap with each of V1, V2, and V3 on all six checked
identity dimensions: trajectory ID, seed, family seed, dataset ID,
split identity, and namespace hash. The V4 population has six families and 120
test trajectories (20 per family; five per family/demand cell). The primary
bootstrap is paired by whole trajectory, uses 10,000 resamples, and seed
`2026072301`.

### Bounded result and release

| Path/asset | SHA-256 | Check |
| --- | --- | --- |
| `results/paper_evidence_v4/artifact_index.json` | `fd78eb559d039620ae1c6e06faac44ab6fc8dbff9208c05523b4efcab4a75a95` | Sidecar match; 152/152 records match hash and byte size; full disk coverage |
| `results/paper_evidence_v4/artifact_index.sha256` | `96fbd8d2dc165beca47b40dd2ecb8eb46f6ae1be7f095974cc69e1ae2c9b9582` | Contains the index digest above |
| `results/paper_evidence_v4/paper_handoff.json` | `d072cfdeb35cc5325ae7b8d5ae3e5ce69e7d19689200e6ba72efc13e15e7fff9` | Bounded paper handoff |
| `results/paper_evidence_v4/generated_numbers.tex` | `153ff6b1402272686ce947d4ef44d57d2d5402760a550394e806a00f5b5312ea` | Frozen V4 handoff macros; do not edit |
| `paper_evidence_v4_bounded-461fc56.zip` | `6208114f0358fab815e0ac79fac73d6a9ff66ca33d8c7128b5ae77d591daa7a8` | Archive bytes match sidecar and release inventory |
| `primary_locked_test_v4-461fc56.zip` | `af84fba1edc1f84b20fca1bbdc26f7fbcc05c2e0d6f4b2dcb711525971f1f11e` | Archive bytes match sidecar and release inventory |
| `same_information_failures.csv` | `dd9c89784766f85473159da6a5c0f072881e47828874fee7f17c7613cd86718f` | Report-only five-row aid |
| `SAME_INFORMATION_FAILURE_ANALYSIS.md` | `2144b449db3d189684833449b4686982b9156cf19db00dcc48360e6650287573` | Report-only interpretation aid |

The checked-in inventory records the local packaging-time state
`local_release_ready=true` and `upload_performed=false`. Separately, the
current GitHub Release API reports 12/12 uploaded assets; this audit downloaded
both ZIPs, matched their release digests and sidecars, and passed both ZIP
integrity tests. The annotated evidence tag peels to `f49b4ef`, not to the
tag-object SHA itself.

### V3 separation and immutability

The read-only V3 verifier passed: frozen root-of-trust files match their
declared SHA-256 values and no V3 frozen path has a working-tree diff.

| V3 item | Value |
| --- | --- |
| Confirmation source | `cf3a517bc74236a4eb1b95c5b6eee952993a0837` |
| Protocol SHA-256 | `25a273d2100e855019c3f416d0aa3c5f61df00772f86e7f15f489ce7deb39eb6` |
| Artifact-index SHA-256 | `12393579515e144f8cb499144772471e3a0398d8d2e19bdff89ff0fa7c479933` |
| Original status SHA-256 | `f0eed71bb5d3b2ac06e00fa933a7b6108012cbcde3c7fc3476a4d99404e52692` |
| Postreview status SHA-256 | `bb2975b6d20d1b1e03c357e4f543ffaa176333b9c055ff76b87fa812df798d0b` |
| V3 observed relative difference | 77.38%, exposed exploratory/confounded and non-confirmatory |
| V4 observed relative difference | 82.4123%, fresh but validity-gated and non-confirmatory |

The V3 and V4 effects, denominators, commits, and classifications must remain
separate.

## Verified scientific values

### Primary and secondary comparisons

| Item | Frozen value | Interpretation |
| --- | --- | --- |
| PVA vs P paired denominator | 120 required; 120 available; zero excluded | Complete |
| P mean position RMSE | `0.13454637291591925` rad | Descriptive baseline mean |
| PVA mean position RMSE | `0.02366363152079231` rad | Descriptive candidate mean |
| Absolute P-minus-PVA RMSE difference | `0.11088274139512694` rad; 95% CI `[0.0885905136096922, 0.13513864379560364]` | Observed, non-confirmatory |
| Relative P-minus-PVA RMSE difference | `0.8241228581049881`; 95% CI `[0.7827192996982065, 0.8614461930812837]` | `strongly_material`, but effective `invalid_method_identity` |
| PV vs P (S1) | relative `0.012755676964619277`; CI `[0.009061801786987497, 0.017105794723427537]`; absolute `0.0017162300696766664` rad | Observed secondary result under the same failed V4 validity boundary |
| PVA vs PV (S2) | relative `0.8218504398645108`; CI `[0.7784933642607229, 0.859222703151343]`; absolute `0.10916651132545027` rad | Observed secondary result under the same failed V4 validity boundary |
| Max-error guardrail (S3) | candidate-minus-baseline relative `-0.8483532644381727`; CI `[-0.8780037025472179, -0.8142153644122212]` | Passes the frozen +5% upper-margin rule |
| Lag means | P `0.09800000000000002` s; PVA `0.09325` s | Point estimate does not indicate an average increase |
| Lag guardrail (S4) | PVA-minus-P `-0.004750000000000018` s; 95% CI `[-0.06758541666666666, 0.059418749999999985]` s | +10 ms noninferiority margin not established; do not claim improved or increased lag |
| Harmful PVA-vs-P trajectories | 5/120 = `0.041666666666666664`; Wilson CI `[0.017926716880109515, 0.09384085233703535]` | Retained negative cases |

The five harmful trajectories, worst first, are
`rapid_reversal__v4__test__007` (`+0.07509928902419732` rad candidate minus
baseline RMSE), `rapid_reversal__v4__test__010`
(`+0.05934759921752085`), `rapid_reversal__v4__test__005`
(`+0.04072601538581183`), `oscillatory__v4__test__019`
(`+0.026113396740727324`), and `rapid_reversal__v4__test__003`
(`+0.0074324550638615455`).

### Family and demand heterogeneity

These are descriptive subgroup estimates and do not override the failed V4
validity gate.

| Family | n | Relative P-minus-PVA RMSE difference (95% CI) | Harmful |
| --- | ---: | --- | ---: |
| stationary endpoint | 20 | `0.9296242708543058` (`[0.9152840773013023, 0.9401575127959939]`) | 0 |
| oscillatory | 20 | `0.7279511232191243` (`[0.6437159867031718, 0.7845970851398057]`) | 1 |
| piecewise constant jerk | 20 | `0.8992038465296268` (`[0.8757437760556668, 0.9187155392376354]`) | 0 |
| stop and go | 20 | `0.9536624204573112` (`[0.9496171388550058, 0.9574140738376854]`) | 0 |
| rapid reversal | 20 | `0.325215368369773` (`[-0.15732055517259266, 0.6591642438626073]`) | 4 |
| boundary grazing | 20 | `0.3663512400187826` (`[0.3246403589142358, 0.40326392855759524]`) | 0 |

The recorded family-effect range is `0.6284470520875383`, and rapid reversal
is the weakest family.

| Demand stratum | n | Relative P-minus-PVA RMSE difference (95% CI) | Harmful |
| --- | ---: | --- | ---: |
| low | 30 | `0.818096612968805` (`[0.756673556206015, 0.8729261207770234]`) | 0 |
| medium | 30 | `0.8255704099347552` (`[0.7445702387800663, 0.8917835830798081]`) | 1 |
| high | 30 | `0.8573097713213357` (`[0.7936830070003842, 0.9078667694262185]`) | 1 |
| near limit | 30 | `0.8002185456444051` (`[0.7112847618428051, 0.8719687381364232]`) | 3 |

The recorded demand-effect range is `0.05709122567693059`.

### Method purity, same information, safety, and runtime

Each primary direct method has 120 trajectories, 42,072 cycles,
`actual_algorithm_set=direct_executable`, native-execution rate 1.0,
method-purity rate 1.0, zero algorithm transitions, zero unexpected fallback,
and zero shield applications. Completion is 120/120 with zero primary
trajectory failures for each of P, PV, and PVA. The bounded fallback-events
table has zero data rows, and the handoff safety gate reports zero continuous
constraint, invariant, sample-gate, or unexplained-NaN failures.

This passed direct-method-purity result does not cancel the separate failed
same-information gate. The five failed aligned cycles are:

| Root CSV row | Trajectory | k | Differing method field(s) | Differing token |
| ---: | --- | ---: | --- | --- |
| 14,172 | `piecewise_constant_jerk__v4__test__001` | 154 | PVA `event_flags` | `deadline_miss` |
| 15,107 | `piecewise_constant_jerk__v4__test__003` | 86 | PV and PVA `event_flags` | `deadline_miss` |
| 26,318 | `rapid_reversal__v4__test__015` | 215 | PV `event_flags` | `deadline_miss` |
| 32,536 | `stationary_endpoint__v4__test__013` | 254 | PV and PVA `event_flags` | `deadline_miss` |
| 32,876 | `stationary_endpoint__v4__test__014` | 260 | PV `event_flags` | `deadline_miss` |

Thus the failure rate is `5 / 42072 = 0.0001188438866693747`, or
`0.011884388667%`. Configuration identity passed on all 42,072 rows. The five
differences do not show different estimator or predictor input.

The runtime gate required five repetitions, 100 discarded warmup cycles per
trajectory, total P99 strictly below 1,000 us, total maximum strictly below
5,000 us, and zero deadline-miss rate. The aggregate handoff values are:

| Method | Timed cycles | Aggregate P99 (us) | Maximum (us) | Deadline misses | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| P direct | 150,360 | `1924.8922200000002` | `37061.625` | 15 | fail |
| PV direct | 150,360 | `2998.994720000001` | `68623.792` | 31 | fail |
| PVA direct | 150,360 | `2801.2176600000007` | `76379.542` | 16 | fail |

These values establish only that the instrumented full Python V4 pipeline
failed the locked runtime criteria. They do not show that an isolated or
compiled implementation can never meet 100 Hz.

### Context-only methods

Ordinary Ruckig is contextual secondary evidence. The P-only and predicted-P
conditions completed 120/120. Raw predicted-PV completed 116/120 (four
failures), and raw predicted-PVA completed 108/120 (12 failures). Consequently
S5 is `unavailable_incomplete_denominator`; complete-case paired inference is
not permitted.

Oracle results are offline analytic-truth diagnostics:
`causal=false`, `deployable=false`, and `diagnostic_only=true`. The 120/120
oracle PV-vs-P relative difference is `0.014046700050337825`; the 120/120
oracle PVA-vs-PV relative difference is `0.7938045249248603`; and the
acceleration-active PVA-vs-PV diagnostic is `0.7671291023087495` over 40/40.
None is online or primary evidence.

## Publication source selectors

CSV row numbers below are one-based physical file lines, including the header
as line 1. Stable predicates must be checked in addition to row numbers so a
future paper generator fails closed if row order changes. JSON selectors use
RFC 6901-style pointers. All sources are checked-in frozen protocol or bounded
evidence; do not read replacement values from a release ZIP.

| Paper datum | Source path (SHA-256) | Stable row / JSON selector | Field | Paper rendering rule |
| --- | --- | --- | --- | --- |
| V4 test trajectory count | `split_manifest_v4.json` (`1727505734c8026ed18d87123d5d5a8c02e2f201a33ea786fbcde2c9ab398796`) | `/trajectories`, predicate `split=="test"` | array count = 120 | Integer |
| Required primary denominator | `V4_STATISTICAL_DESIGN.json` (`63a8677591976c436b14e9afee059a7575fd47909c2f18c72a5f515127be2a6c`) | `/primary` | `denominator_required` = 120 | Integer |
| Available primary denominator | `results/paper_evidence_v4/statistics/primary_comparison.csv` (`b59acd3230e4ddb452d5f754ec0f024795d509fa846de51924eadee110f7285c`) | row 2; `trajectory_id=="boundary_grazing__v4__test__000"` | `paired_trajectory_count` = 120 | Integer; render `120/120` with required denominator |
| Primary relative observed difference | same primary CSV | same row 2 | `overall_relative_improvement` = `0.8241228581049881` | Multiply by 100; fixed 4 decimals = `82.4123%` in Results, fixed 2 = `82.41%` in compact table; always adjacent to observed/non-confirmatory/failed-gate wording |
| Primary relative CI | same primary CSV | same row 2 | `overall_relative_improvement_ci_low/high` | Multiply by 100; fixed 2 decimals = `[78.27%, 86.14%]` unless a table declares more precision |
| Primary absolute observed difference | same primary CSV | same row 2 | `overall_absolute_improvement` | Fixed 4 decimals in rad = `0.1109` |
| Primary absolute CI | same primary CSV | same row 2 | `overall_absolute_improvement_ci_low/high` | Fixed 4 decimals in rad = `[0.0886, 0.1351]` |
| Statistical classification | same primary CSV | same row 2 | `primary_result_classification` = `strongly_material` | Verbatim token |
| Effective classification | `results/paper_evidence_v4/protocol_status_v4.json` (`48c98a81a76129a0fc2dd913aabb28bc9312d31a76a4283b27bf1fea9431a34b`) | `/primary_result_classification` | `invalid_method_identity` | Verbatim token; never replace |
| Protocol status | same post-test status JSON | `/status` | `failed_test_visible_frozen` | Verbatim token |
| Same-test rerun | same post-test status JSON | `/same_test_rerun_permitted` | `false` | Boolean; prose “prohibited” |
| PV-vs-P relative observed difference | `results/paper_evidence_v4/statistics/secondary_comparisons.csv` (`e995abdb75244dccacbf4957de008521fef94c2bb10e1608ee27d8bd53f995f0`) | row 2; `comparison_id=="S1"` | `relative_improvement` and CI fields | Multiply by 100; fixed 2 decimals if published |
| PVA-vs-PV relative observed difference | same secondary CSV | row 3; `comparison_id=="S2"` | `relative_improvement` and CI fields | Multiply by 100; fixed 2 decimals if published |
| Max-error guardrail | same secondary CSV | row 4; `comparison_id=="S3"` | `relative_difference`, CI | Multiply by 100; fixed 2 decimals; label guardrail pass, not primary benefit |
| P mean lag | `results/paper_evidence_v4/statistics/summary_metrics.csv` (`e96773769b8311bd36c43c26e737d87b9dfc505b807cff9e06e797a4b939fc92`) | row 7; P direct and `metric=="lag_s"` | `mean` | Multiply seconds by 1,000; fixed 2 decimals = `98.00 ms` |
| PVA mean lag | same summary CSV | row 13; PVA direct and `metric=="lag_s"` | `mean` | Multiply seconds by 1,000; fixed 2 decimals = `93.25 ms` |
| Lag difference and CI | secondary CSV | row 5; `comparison_id=="S4"` | `absolute_difference`, CI | Multiply seconds by 1,000; fixed 2 decimals; say noninferiority not established |
| Harmful count/denominator | `results/paper_evidence_v4/statistics/harmful_trajectory_rate.csv` (`9fb94bb7ba40cb7e5bb11854d790131e6b38c017261947019f8195748d00bbf6`) | row 2; `comparison_id=="PVA_vs_P_position_RMSE"` | `harmful_count`, `denominator`, `harmful_rate` | Integers as `5/120`; rate ×100 fixed 2 = `4.17%` |
| Worst five trajectories | `results/paper_evidence_v4/statistics/worst_five_trajectories.csv` (`dc26117b58332269c0bba7cb2eb455126e6b14601aa8199e38b15bd16e14ea91`) | rows 2–6; `rank` 1–5 | IDs and candidate-minus-baseline RMSE | RMSE fixed 4 rad if rendered; retain all five |
| Family effects | `results/paper_evidence_v4/statistics/family_effects.csv` (`cfc26e28dee1ab1d9671da3e9f482a1ee4daf38a34428b9db29153448853da85`) | rows 2–7; key `comparison_id` | relative/CI/harm fields | Effect and CI ×100, fixed 2%; harmful as integer/n=20 |
| Demand effects | `results/paper_evidence_v4/statistics/demand_stratum_effects.csv` (`6ed0730ca4a804d973eef4d8f91970d47b587ab2c4451d4a94fd79c5bf55178a`) | rows 2–5; key `comparison_id` | relative/CI/harm fields | Effect and CI ×100, fixed 2%; harmful as integer/n=30 |
| Direct purity P/PV/PVA | `results/paper_evidence_v4/statistics/method_identity_summary.csv` (`c29a6fe4eda54ea0146c45453c157882ab7647c8c2c985b028ad98060e890803`) | rows 3–5; primary direct method IDs | `method_purity_rate`, `native_execution_rate`, counts | Rate fixed 1 decimal = `1.0`; counts integer |
| Primary completion | `results/paper_evidence_v4/statistics/completion_summary.csv` (`58f3b09d053667526e7d7bf24c35fce9322784e7adb56226f77e873a85941573`) | rows 3–5; primary direct method IDs | attempted/successful/failed | Integers; each `120/120`, zero failed |
| Same-information denominator/failures | `results/paper_evidence_v4/statistics/same_information_audit.csv` (`ec46bf920912179020ceaa0eeedcfe447026a5ec1a341b82ef71b83d99ba1a8e`) | all 42,072 rows; filter `audit_passed==False` | row count and failed-row count | `5/42,072`; percent = count/denominator×100, fixed 6 = `0.011884%` |
| Same-information token diagnosis | `same_information_failures.csv` (`dd9c89784766f85473159da6a5c0f072881e47828874fee7f17c7613cd86718f`) | rows 2–6; five cycle keys | `differing_tokens` | Verbatim `deadline_miss`; diagnostic does not alter gate |
| Runtime aggregate | `results/paper_evidence_v4/paper_handoff.json` (`d072cfdeb35cc5325ae7b8d5ae3e5ce69e7d19689200e6ba72efc13e15e7fff9`) | `/runtime_gates/methods/0..2`, keyed by `method` | `total_p99_us`, `total_max_us`, `deadline_miss_count`, `timed_cycle_count`, `passed` | P99/max fixed 3 us; counts integer; disclose gate fail |
| Ordinary completion | `results/paper_evidence_v4/statistics/ordinary_ruckig_completion.csv` (`32612fcdfd6619e0354d48f28463c9416df0cad15ef11540fbe39f0d98ac9993`) | rows 2–5 keyed by `method` | attempted/completed/failed/status | Integers and verbatim status; never infer S5 from incomplete subset |
| Oracle context | `results/paper_evidence_v4/statistics/oracle_pv_vs_p.csv` (`440c0a6cc971800a5c0dd5dc49e984a6a15b26026cc8b2f837ce1bcc381f0a48`), `oracle_pva_vs_pv.csv` (`ab79a2b14b3c1f0cddba2db0ba416243227df0229f0b4422198c19e9eefe086b`), `oracle_acceleration_active_effect.csv` (`078f32630d6cdc516befb80bdd70671058580538fa65aa7558bb8b2e60b05b3b`) | row 2 in each, keyed by `comparison_id` | relative effect, n, causal/deployable/diagnostic flags | Effect ×100 fixed 2 only if needed; always label offline, noncausal, nondeployable |
| Root artifact integrity | `results/paper_evidence_v4/artifact_index.json` (`fd78eb559d039620ae1c6e06faac44ab6fc8dbff9208c05523b4efcab4a75a95`) | `/artifacts` | 152 records | Integer `152/152`; no scientific effect inference |

## Evidence-source registry instructions

These are the audited minimum semantics for the corresponding
`evidence_sources.yaml` entries. They are instructions to the registry owner;
this audit does not edit that shared logic file.

| ID | Source and commit | Temporal / visibility / causal / deployability | Exact denominator and status | Allowed use and sections | Forbidden interpretation |
| --- | --- | --- | --- | --- | --- |
| `E_V4_PROTOCOL` | Protocol/design/acceptance/matrix/lock at `461fc56` | pretest locked; test unseen; protocol, not an effect; N/A deployability | 120-test design; locked/exactly once | Protocol, appendix, provenance | Do not claim a result from protocol text |
| `E_V4_FRESH_LOCKED_TEST` | `split_manifest_v4.json`, `config_lock_v4.json`, post-test status at `f49b4ef` | fresh; test becomes visible; causal primary method declarations; synthetic deployability only | 120 test trajectories; `failed_test_visible_frozen` | Protocol, Results denominator, appendix | No real-data or hardware confirmation |
| `E_V4_PRIMARY_OBSERVED_EFFECT` | primary CSV and handoff at `f49b4ef` | post-test frozen; causal method declarations but invalid effective study classification; primary methods marked deployable in synthetic protocol | 120/120; statistical `strongly_material`, effective `invalid_method_identity` | Results, Discussion, V4 appendix; Abstract qualitative only | No confirmed/causal/superiority performance claim; no exact Abstract percentage |
| `E_V4_METHOD_PURITY` | method identity CSV at `f49b4ef` | post-test frozen; causal primary methods; synthetic only | 360 trajectory-method rows and 42,072 cycles/method; purity 1.0; pass | Results, method/safety appendix | Does not cure the same-information failure or confirm tracking benefit |
| `E_V4_SAME_INFORMATION_FAILURE` | root audit at `f49b4ef`; aids at `b9301ea` | post-test frozen/report-only diagnosis; composite runtime event; N/A deployability | 5/42,072; gate fail | Results, Discussion, appendix, provenance | Do not delete/reclassify rows, restore confirmation, or claim estimator/predictor inputs differed |
| `E_V4_SAFETY` | completion, fallback, constraint, handoff safety at `f49b4ef` | post-test frozen; causal primary execution; synthetic only | 120/120/method; zero primary failure/fallback/continuous violation; pass | Results, safety discussion, appendix | No hardware or production safety claim; no tracking superiority |
| `E_V4_LAG_GUARDRAIL` | S4 secondary row at `f49b4ef` | post-test frozen; causal comparison; synthetic only | 120/120; noninferiority fail | Results, Discussion, appendix | Do not write “lag improved” or “lag increased” |
| `E_V4_RUNTIME_FAILURE` | handoff runtime gate and runtime CSV at `f49b4ef` | post-test frozen; instrumented Python execution; deployment gate | 150,360 timed cycles/method; fail for all methods | Results, Discussion, appendix | Does not prove an isolated/compiled implementation impossible at 100 Hz |
| `E_V4_HARMFUL_TRAJECTORIES` | harmful-rate and worst-five CSVs at `f49b4ef` | post-test frozen; same effective invalidity; synthetic only | 5/120; retained | Results, Discussion, appendix | Do not omit harmful rows or generalize a population harm rate |
| `E_V4_SUBGROUPS` | family/demand CSVs at `f49b4ef` | post-test descriptive secondary; synthetic only | family n=20 each; demand n=30 each | Results/Discussion secondary text, appendix | No subgroup rescues the primary validity failure |
| `E_V4_ORDINARY_CONTEXT` | ordinary completion/metrics at `f49b4ef` | post-test contextual secondary; causal methods where completed; contextual deployability only | P 120/120, predicted-P 120/120, PV 116/120, PVA 108/120; S5 unavailable | Appendix/context paragraph | No complete-pair S5 inference; no primary conclusion |
| `E_V4_ORACLE_CONTEXT` | oracle CSVs/matrix at `f49b4ef` | post-test; offline analytic truth; noncausal; nondeployable | 120/120 main diagnostics; 40/40 acceleration-active | Appendix and clearly labelled diagnostic discussion | No online, deployable, primary, or causal evidence |
| `E_V4_ARTIFACT_INTEGRITY` | root index/release inventory at `f49b4ef` | post-test frozen; provenance; N/A causal/deployability | 152/152 indexed artifacts; pass | Provenance, reproducibility, appendix | Artifact integrity does not validate the performance claim |

## Mandatory wording boundaries

- The observed 82.4123% figure may appear only with an adjacent disclosure
  that it is observed, non-confirmatory, and validity-gated.
- Abstract and Conclusion should not contain the exact V4 percentage.
- Do not say V4 was inconclusive: the statistical estimate is strongly
  material; the effective result is validity-gated non-confirmatory.
- Do not say the V4 experiment failed. Raw execution and estimation completed;
  the confirmation validity gate failed after test visibility.
- Do not say estimator or predictor information differed. The five frozen
  differences are composite `event_flags`, specifically `deadline_miss`.
- Do not remove the five rows, revise the V4 audit definition, resume V4, or
  rerun the same test. Any revised confirmation requires fresh V5 evidence.
- Do not convert the lag point estimate into either an “improved” or
  “increased” lag claim.
- Do not convert the runtime failure into a universal algorithmic impossibility
  claim.
- Do not treat ordinary-Ruckig S5 incomplete subsets or oracle truth as primary
  evidence.
- Do not mix V3’s 77.38% exposed/confounded observation with V4’s 82.4123%
  fresh-but-invalid observation.
- Do not add excluded product-specific material.

## Reproduction commands used by this audit

These commands perform integrity checks only; none runs an experiment.

```sh
git merge-base --is-ancestor 461fc560461b0a4726cbabdb97b2dbd4dc305e0a f49b4ef1cacf8228c5d243353184acb8a7d02311
git merge-base --is-ancestor f49b4ef1cacf8228c5d243353184acb8a7d02311 b9301eaf36dc04f1abf662c42821eddfe8c3188a
git merge-base --is-ancestor b9301eaf36dc04f1abf662c42821eddfe8c3188a c97e24dcfd6dd9146755235fa632e08932dc9a78
git merge-base --is-ancestor c97e24dcfd6dd9146755235fa632e08932dc9a78 HEAD
python3 -c 'from otg_lab.v4_artifacts import verify_root_artifact_index; print(verify_root_artifact_index("results/paper_evidence_v4"))'
python3 paper/scripts/verify_v3_immutability.py
shasum -a 256 results/paper_evidence_v4/artifact_index.json
shasum -a 256 results/paper_evidence_v4_release/paper-evidence-v4-461fc56-report-only/*.zip
git diff --exit-code -- EXPERIMENT_PROTOCOL_V3.md protocol_status_v3.json protocol_status_v3_postreview.json V3_POSTREVIEW_ADDENDUM.md results/paper_evidence_v3
git diff --exit-code -- EXPERIMENT_PROTOCOL_V4.md V4_HYPOTHESES.md V4_STATISTICAL_DESIGN.json V4_ACCEPTANCE_CRITERIA.json V4_METHOD_MATRIX.json V4_PROTOCOL_DECISIONS.md config_lock_v4.json split_manifest_v4.json protocol_status_v4.json results/paper_evidence_v4 results/paper_evidence_v4_release
```
