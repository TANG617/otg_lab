# Adversarial logic review

Review role: Agent F, claim/evidence boundary pass

Review date: 2026-07-24

Repository baseline: `8faedae1fe18111ad0329259b5618c06edf6020b`

Scope: the current claim registry and logic layer as consumed by the complete
V4-integrated LaTeX manuscript. No logic-registry entry or frozen evidence was
changed by this review.

## Disposition

**Pass after manuscript/checker remediation: no open P0 or P1 finding.**

The current logic layer no longer describes V4 as unevaluated. It treats V4 as
a fresh, exactly-once, frozen result with a statistically strong observed
difference and a failed confirmatory-validity disposition:

- protocol status: `failed_test_visible_frozen`;
- statistical classification: `strongly_material`;
- effective classification: `invalid_method_identity`;
- publication status: `nonconfirmatory_frozen`;
- same-test rerun: prohibited;
- future confirmation: fresh V5 only.

This replaces the pre-V4 logic-review disposition. Historical D014 remains
only as a dated record of the earlier drafting cycle; D018 explicitly marks it
historical, and D019--D022 define the current evidence state.

## Contract audit

### Claim status and placement

- N03 now describes the completed fresh V4 attempt and has
  `requires_v4: false`, `requires_future_v5_for_confirmation: true`, and
  `nonconfirmatory_frozen` status.
- C14 records the fresh 120/120 whole-trajectory design without claiming
  benefit.
- C15/E02 permit the observed effect only with failed-gate and
  non-confirmatory language.
- C16 retains all five composite `event_flags` failures and the
  `deadline_miss`-only diagnosis without reclassifying the gate.
- C17 limits purity/safety observations to the frozen synthetic command model.
- C18 forbids both “improved lag” and “increased lag.”
- C19 limits runtime inference to the instrumented full Python pipeline.
- The exact V4 effect is forbidden in title, contribution headline, Abstract,
  and Conclusion.

### Evidence-role separation

- V3 and V4 have separate evidence IDs, commits, hashes, denominators, statuses,
  and publication roles.
- The V3 77.38% regression remains `exploratory_confounded` and cannot support
  ordinary-Ruckig or same-follower inference.
- The V4 82.4123% observed estimate remains statistically material but
  effectively invalid for confirmation.
- Same-information failure localization is report-only reviewer aid; it cannot
  delete rows, rewrite the preregistration, or restore confirmation.
- Ordinary-Ruckig S5 is contextual and unavailable for complete-pair inference.
- Oracle evidence is offline, noncausal, nondeployable, and diagnostic only.

### Future-study boundary

The logic permits audit redesign only in a fresh V5 with a new test set and a
new preregistered definition. The proposed separation of exogenous,
estimator/predictor, execution, runtime, and plant flags is prospective. It is
not a post-hoc reinterpretation of V4, and no V5 result exists.

## Adversarial traceability questions

| # | Logic-lock question | Result |
|---:|---|---|
| 1 | Can any current claim promote V4 to a confirmatory gain? | No; C15, N03, and E02 prohibit it. |
| 2 | Can the Abstract headline the exact effect? | No; exact V4 numbers are disallowed and the failure/withhold language is mandatory. |
| 3 | Are protocol/statistical/effective statuses conflated? | No; all three are separately locked. |
| 4 | Can the five failures be generalized beyond `deadline_miss`? | No; C16 fixes the narrow composite-token diagnosis. |
| 5 | Can the audit be described as different estimator/predictor input? | No; this interpretation is explicitly forbidden. |
| 6 | Can runtime failure be omitted from the V4 disposition? | No; C19 and N03 require it. |
| 7 | Can lag be called beneficial or harmful? | No; C18 permits only “noninferiority not established.” |
| 8 | Can harmful/rapid-reversal cases be dropped? | No; frozen denominators and subgroup evidence retain them. |
| 9 | Can oracle results support an online claim? | No; the evidence registry forbids causal/deployable use. |
| 10 | Can ordinary S5 be analyzed as complete? | No; its incomplete denominator fixes it as contextual/unavailable. |
| 11 | Can V3's historical percentage support V4? | No; the claims and evidence IDs are disjoint. |
| 12 | Can Conclusion state superiority? | No; C15/E02 are disallowed there and N03 permits only the negative conclusion. |
| 13 | Can current logic describe V4 as still pending? | No; D018--D022 and N03 define the completed frozen state. |
| 14 | Can the V4 audit definition be revised post hoc? | No; only a fresh V5 may preregister a revised audit. |
| 15 | Can synthetic/CSV results imply real or hardware validation? | No; N01/N02 and C17 retain those limitations. |

## Findings

### P0 findings

None.

### P1 findings

The companion manuscript review raised four P1 findings. They were all closed
without changing the logic registry:

1. both adversarial documents were updated from their pre-V4 state;
2. exact-effect paragraphs now carry observed/failed-gate/non-confirmatory
   qualifiers locally;
3. ordinary-Ruckig S5 is explicitly named as incomplete and unavailable;
4. the Conclusion calls the oracle noncausal and diagnostic.

### P2 findings

None from this logic-boundary pass.

## Gate

The logic is acceptable for the V4-integrated draft once the normal pipeline
regenerates `logic_lock.json` so it binds this updated review file and all
claim/evidence checks pass. This review does not authorize editing frozen V3/V4
evidence, rerunning V4, executing V5, publishing a confirmatory benefit, or
merging the Draft PR.
