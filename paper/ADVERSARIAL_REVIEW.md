# Adversarial scientific review

Review role: Agent F, adversarial scientific reviewer

Review date: 2026-07-24

Repository baseline: `8faedae1fe18111ad0329259b5618c06edf6020b`

Merged main evidence baseline: `c97e24dcfd6dd9146755235fa632e08932dc9a78`

Scope: the complete uncommitted `paper/` integration diff, the frozen V4
handoff and bounded statistics, the report-only same-information aid, and the
V3/V4 separation. No experiment was run, resumed, or modified.

## Disposition

**Pass after remediation: no open P0 or P1 finding.**

Four P1 findings were raised and closed in the reviewed worktree. The current
manuscript reports the V4 estimate as a large observed but non-confirmatory
effect, keeps the failed preregistered validity gate dispositive, and does not
claim PVA superiority. The statistical classification
`strongly_material`, effective classification `invalid_method_identity`, and
protocol status `failed_test_visible_frozen` remain distinct. Runtime and lag
failures, all harmful trajectories, rapid-reversal heterogeneity, incomplete
ordinary-Ruckig S5 evidence, and the offline oracle boundary are visible.

The two pre-V4 adversarial documents were replaced by this current review and
the companion logic review. They no longer present a pending-execution
conclusion as the current state.

## Findings and remediation

### P0 findings

None.

### P1-01 — The adversarial records described the pre-V4 manuscript

**Initial locations**

- `paper/ADVERSARIAL_REVIEW.md`
- `paper/ADVERSARIAL_LOGIC_REVIEW.md`

**Risk.** The documents named Agent G, reviewed only Phase A/V3, and carried
dispositions from before the merged V4 evidence. Leaving those dispositions in
the release would contradict the manuscript and defeat the requested
adversarial gate.

**Fix.** Both files were replaced with reviews of the current V4 integration.
The current dispositions explicitly cover the 15 required questions and the
three-part V4 classification.

**Status:** closed.

### P1-02 — Exact V4 effect blocks did not carry all three local qualifiers

**Initial locations**

- `paper/sections/06_results.tex`, primary observed-result paragraph
- `paper/appendix/F_v4_confirmation_attempt.tex`, primary observation

**Risk.** Both blocks said `observed`, and nearby material disclosed the failed
gate, but the exact-effect paragraphs did not themselves consistently contain
all of `observed`, failed validity gate, and `non-confirmatory`. A reader could
lift the number without its controlling classification.

**Fix.** The Results paragraph now calls the secondary and primary estimates
non-confirmatory observations under the failed validity gate. Appendix F now
states immediately after the exact estimate that the preregistered validity
gate failed and the effect is retained as non-confirmatory, not as a
comparative benefit. `check_claims.py` now enforces these three terms in every
paragraph containing the exact V4 primary-effect macro.

**Status:** closed.

### P1-03 — The ordinary-Ruckig limitation did not explicitly name S5

**Initial locations**

- `paper/sections/06_results.tex`, V4 interpretation boundary
- `paper/appendix/F_v4_confirmation_attempt.tex`, contextual analyses

**Risk.** The text correctly said that the ordinary comparison was incomplete,
but a reviewer could not directly connect that limitation to the frozen S5
comparison.

**Fix.** Results now names the contextual ordinary-Ruckig S5 comparison as
unavailable for paired inference because its denominator is incomplete.
Appendix F identifies S5 as predicted-P versus raw-predicted-PVA, with an
incomplete PVA denominator. It continues to prohibit complete-case deletion or
substitution into the primary analysis. The claim checker now requires the S5,
incomplete, and unavailable boundary.

**Status:** closed.

### P1-04 — The Conclusion's oracle sentence was too easy to read causally

**Initial location**

- `paper/sections/08_conclusion.tex`, evidence summary

**Risk.** “Confirms the timing contract” followed the word `oracle` but omitted
the noncausal qualifier in the Conclusion itself. The Protocol, Results,
Discussion, and Appendix were already explicit, but the strongest summary
should not require backtracking to recover the boundary.

**Fix.** The Conclusion now calls it a **noncausal next-cycle analytic oracle**
and an **indexing sanity control**. It makes no online, deployable, or causal
performance claim.

**Status:** closed.

### P2 findings

None from this scientific-boundary pass. Final PDF layout, package hashes, and
remote CI are separate release-QA gates and cannot be inferred from source
review alone.

## Required 15-question review

| # | Adversarial question | Answer and evidence | Outcome |
|---:|---|---|---|
| 1 | Is V4 presented anywhere as a confirmatory performance gain? | No. Abstract withholds a confirmatory claim; Results and Appendix call the exact effect observed and non-confirmatory under the failed validity gate; Conclusion says it did not establish a benefit. | Pass |
| 2 | Does the Abstract induce the reader to see only the large effect? | No. It gives no 82.41% value, places the audit and runtime failures in the same sentence, withholds confirmation, and immediately retains synthetic/real/hardware limits. | Pass |
| 3 | Are statistical, effective, and protocol classifications distinguished? | Yes: `strongly_material`, `invalid_method_identity`, and `failed_test_visible_frozen`, respectively. Protocol and Appendix explain that the effective classification controls the claim. | Pass |
| 4 | Are the five failures accurately limited to `deadline_miss`? | Yes. Results and Appendix report 5/42,072 aligned composite `event_flags` failures; the five-row appendix table shows `deadline_miss` as the sole differing token, with all other fields/configuration identity passing. | Pass |
| 5 | Is there an erroneous estimator/predictor-information difference claim? | No. The manuscript says the failure does **not** show different estimator or predictor information and that their compared fields passed. | Pass |
| 6 | Is the runtime failure disclosed? | Yes. Abstract, Protocol, Results, main table, Discussion, Appendix, and Conclusion disclose that the instrumented full Python pipeline failed the hard-runtime gate. The text does not generalize this to compiled impossibility. | Pass |
| 7 | Is failed lag noninferiority disclosed? | Yes. Results, the main table, Discussion, and Appendix state that noninferiority was not established and avoid both beneficial and harmful directional claims. | Pass |
| 8 | Are harmful trajectories and rapid-reversal heterogeneity retained? | Yes. Results retain 5/120 harmful trajectories; the appendix family/worst-case table retains all five cases, and rapid reversal is identified as the weakest family with an interval crossing zero. | Pass |
| 9 | Is oracle evidence treated as online evidence? | No. It is consistently offline, noncausal, nondeployable, and diagnostic; the Conclusion now calls it a noncausal indexing sanity control. | Pass |
| 10 | Is incomplete ordinary-Ruckig S5 treated as complete? | No. S5 is explicitly unavailable for paired inference; the contextual table preserves the 120/120 predicted-P baseline, 116/120 raw-PV completion, and 108/120 raw-PVA completion without complete-case inference. | Pass |
| 11 | Are V3 and V4 mixed? | No. The Protocol and provenance appendix separate commits, statuses, denominators, and roles. The 77.38% V3 correction remains in its dedicated Discussion subsection; V4's 82.4123% observation remains in the V4 result/appendix. | Pass |
| 12 | Does the Conclusion restore PVA superiority? | No. It contains no exact V4 percentage or superiority wording and says the attempt did not establish a confirmatory benefit. | Pass |
| 13 | Does a current statement still say that the V4 attempt is pending? | No. The two outdated adversarial dispositions were replaced. Historical decision D014 is explicitly marked historical and superseded for current state by D018--D022. | Pass |
| 14 | Is audit redesign confined to a future V5? | Yes. Discussion requires a fresh V5 protocol and new test set for a revised preregistered audit, and expressly says this cannot retroactively validate V4. No V5 result is reported. | Pass |
| 15 | Are real-data and hardware limitations retained? | Yes. Abstract, Introduction, Protocol, Discussion, Conclusion, and Appendix state that the sole real CSV is development-only and that there is no independent real-stream, hardware, dynamics/torque, collision, deployment, or production-safety evidence. | Pass |

## Protected-number and forbidden-wording audit

- The V4 exact percentage is absent from the title, Abstract, contribution
  list, and Conclusion.
- Formal manuscript V4 results use generated macros/tables; no protected V4
  result is hand-copied into `sections/*.tex` or `appendix/*.tex`.
- V3's 77.38% and V4's 82.4123% are not mixed in one prose block.
- No claim uses superiority, demonstration, improved-tracking,
  improved/increased-lag, or deployment-readiness wording for V4.
- No claim says that the methods received different estimator or predictor
  information.
- No claim treats deletion of the five `deadline_miss` rows as a repair.
- No claim reports a V5 result or authorizes a V4 rerun/resume.
- No excluded product-specific content was introduced.

## Verification record

The scientific review directly inspects the frozen sources:

- `results/paper_evidence_v4/protocol_status_v4.json`
- `results/paper_evidence_v4/statistics/primary_comparison.csv`
- `results/paper_evidence_v4/statistics/secondary_comparisons.csv`
- `results/paper_evidence_v4/statistics/family_effects.csv`
- `results/paper_evidence_v4/statistics/harmful_trajectory_rate.csv`
- `results/paper_evidence_v4/statistics/runtime_benchmark.csv`
- `results/paper_evidence_v4/statistics/method_identity_summary.csv`
- `results/paper_evidence_v4/statistics/same_information_audit.csv`
- `results/paper_evidence_v4/statistics/ordinary_ruckig_completion.csv`
- `results/paper_evidence_v4/statistics/oracle_target_component_metrics.csv`
- `same_information_failures.csv`
- `SAME_INFORMATION_FAILURE_ANALYSIS.md`

Focused claim and number checks must pass in the final regenerated tree. A
temporary failure caused by concurrently stale generated manifests is not a
scientific pass and must be cleared by the final evidence-generation/QA run.
This review does not claim final PDF, arXiv package, or remote-CI completion.
