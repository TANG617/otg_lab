# Adversarial manuscript review

Review role: independent manuscript/evidence reviewer (Agent G)  
Review date: 2026-07-23  
Repository HEAD reviewed: `0da05918b68a9e0908f98d8cdfacb8e1fdcfdda4`  
Initial compiled PDF reviewed: `paper/build/main.pdf`, 26 pages, SHA-256
`8fcf2f135fbb78fb475cf9c3d01281c4dec34d03c586372c9ea5dc0c994d94c0`  
Post-review layout PDF inspected: 27 pages, SHA-256
`19ec0ce0cbbd3d4c25dc622fca517ef0b4707ec209a9213c5610fe8dd09029b3`

## Disposition

**No P0 finding. Three P1 findings were raised and closed. The runtime table,
figures, PDF, and logic lock were regenerated after the source fixes, and the
static evidence, claim-placement, generation, frozen-artifact, and LaTeX gates
passed. Four P2 findings do not independently block a Draft PR.**

The central paper is scientifically supportable after the P1 fixes. The
manuscript consistently positions the contribution as a timing, feasibility,
and executed-method-identity formulation with scoped command-model evidence. It
does not claim PVA superiority, corrected ordinary-Ruckig inferiority,
real-stream generalization, hardware safety, or global optimality. The
historical 77.38% comparison is placed only in the Discussion evidence-
correction subsection and carries the required fallback, identity,
reclassification, immutability, and no-rerun disclosures in one paragraph.

The initial Draft PR gate was **fail** because Figure 7 drew its deadline-miss
value from the wrong registered population, the manuscript did not obey the
literal section permissions in the locked claim registry, and Results floats
rendered inside the Discussion with overlapping labels. All three defects have
source-level remediations in the current worktree. The affected table, PDF,
and logic lock were subsequently regenerated and verified, so the
adversarial-review gate is now **pass** for a Draft PR.

## P0 findings

None.

## P1 findings

### P1-01 — Figure 7 silently combines three runtime/safety populations

**Exact locations**

- `paper/scripts/generate_figures.py:159-165`
- `paper/scripts/generate_figures.py:167-174`
- `paper/sections/06_results.tex:175-182`
- `paper/scripts/generate_tables.py:138-146`
- `paper/generated/tables/v3_runtime.tex:4-9`

**Evidence**

`generate_figures.py:159-164` selects `deadline_miss_rate` from
`v3.acceptance_rows`. The selected acceptance row has denominator **8,100**.
The same function then selects p99 and maximum from
`v3.direct_runtime_primary`, whose registered denominator is **30,199**
post-warm-up cycles. It also places the 8,100-cycle deadline rate in the left
panel beside 42,199-cycle command-audit quantities. The manuscript caption says
that the command audit uses 42,199 cycles and the runtime panel uses 30,199,
which does not describe the deadline bar's actual source or visual placement.

The plotted value happens to be zero in both runtime sources, but equality of
the displayed scalar does not make the populations interchangeable. This
contradicts locked decision D016 and the C10 requirement to retain a denominator
beside every zero event count.

The generated runtime table uses the correct 30,199-cycle primary benchmark for
p99 and maximum, but its deadline row reports only `0.000` and the text
`locked-test, after warm-up`; it does not print `0/30,199` or a numeric
denominator beside the zero.

**Exact required fix**

1. In `generate_figures.py`, stop taking the displayed deadline result from
   `acceptance_rows`. Use
   `direct_runtime_primary.runtime_deadline_miss_rate` and
   `direct_runtime_primary.timed_cycle_count`, or remove the deadline bar.
2. Keep the left panel entirely at the 42,199-cycle command-audit grain
   (violations, projections, and optionally fallbacks, each shown as
   `0/42,199`). Keep the right panel entirely at the 30,199-cycle primary
   runtime grain (p99, maximum, and `0/30,199` deadline misses).
3. If the 8,100-cycle repeated-subset acceptance population is retained
   anywhere, give it a separate panel/caption and call it a distinct repeated-
   subset population; do not substitute it for the primary runtime benchmark.
4. Change the runtime table to include a numeric denominator column or show
   `Deadline misses = 0/30,199`. Preserve the p99 and maximum as measurements
   from the same primary population.
5. Regenerate the figure/table and rerun number-provenance and caption checks.

**Remediation observed after review:** `generate_figures.py` no longer selects
the 8,100-cycle acceptance deadline row. Its left panel now contains only
42,199-cycle command quantities (violations, projected cycles, and fallback
cycles), and its right panel uses the primary 30,199-cycle runtime object with
explicit `deadline misses: 0 / 30199`. `generate_tables.py` has also been
changed to give the primary runtime rows a 30,199 denominator and to report
deadline misses as a count. The revised figure was regenerated and visually
inspected.

**Gate:** closed after regeneration and verification of the table, figure,
number provenance, generation manifest, and compiled PDF.

### P1-02 — The manuscript violates the locked `allowed_sections` contract, and the checker does not detect it

**Exact locations**

- `paper/logic/claims.yaml:4-7` (registry is declared the source of truth)
- `paper/logic/claims.yaml:130-143` (C04 placement)
- `paper/logic/claims.yaml:201-215` (C06 placement)
- `paper/logic/claims.yaml:356-370` (C10 placement)
- `paper/logic/claims.yaml:466-476` (C13 placement)
- `paper/sections/01_introduction.tex:28`
- `paper/sections/01_introduction.tex:94`
- `paper/sections/02_related_work.tex:6`
- `paper/sections/02_related_work.tex:33`
- `paper/sections/02_related_work.tex:54`
- `paper/sections/05_experimental_protocol.tex:41`
- `paper/sections/05_experimental_protocol.tex:126`
- `paper/appendix/B_experiment_details.tex:6`
- `paper/appendix/B_experiment_details.tex:68`
- `paper/appendix/B_experiment_details.tex:86`
- `paper/appendix/C_negative_results.tex:6`
- `paper/appendix/C_negative_results.tex:21`
- `paper/appendix/D_evidence_provenance.tex:4`
- `paper/scripts/check_claims.py:51-72`

**Evidence**

The locked registry says that it defines claim wording **and section
permissions**, but the actual annotations exceed those permissions. Examples:

- the Introduction annotates C04, C06, C07, C08, C10, and N03 although those
  claims do not permit `introduction`;
- Related Work annotates C01, C05, and C09 although those claims do not permit
  `related_work`;
- Post-freeze Protocol text annotates C11/C12 although those claims do not
  permit `experimental_protocol`;
- Appendix B uses C03/C04/C05/C08/C10/C13/N03, but the registry defines no
  `appendix_experiment_details` permission for any of them;
- Appendix C uses C04 and N01 without `appendix_negative_results` permission;
- Appendix D uses C13 without `appendix_evidence_provenance` permission.

The E01 passage itself is correctly located in the named Discussion evidence-
correction subsection; it should remain a subsection-specific exception rather
than be widened to generic Discussion.

`check_claims.py:51-63` checks only whether IDs are known and whether a short
global phrase list is absent. Lines 65-72 enforce location only for the
abstract and conclusion. The checker therefore prints `claim check passed`
despite the literal registry violations.

This is not merely a cosmetic annotation problem: the logic lock is described
as the machine-readable contract that prevents claim leakage. A source-of-truth
contract that the manuscript violates and CI cannot evaluate is not locked in
practice.

**Exact required fix**

1. Define one canonical path/subsection-to-permission mapping, including
   `discussion_evidence_correction` and each appendix.
2. For every current annotation, either:
   - remove IDs that the local prose does not actually assert;
   - add the specific location to `allowed_sections` when repetition is
     deliberate and scientifically appropriate; or
   - move the claim to an already permitted location.
3. Keep E01 restricted to the specifically labelled evidence-correction
   subsection or permitted appendix; do not solve this finding by allowing E01
   in generic Discussion/Results/Protocol.
4. Extend `check_claims.py` to evaluate every annotation against
   `allowed_sections`, not only `allowed_in_abstract` and
   `allowed_in_conclusion`.
5. If `claims.yaml` changes, rerun `check_logic.py`, regenerate
   `logic_lock.json`, and commit the new lock with the manuscript correction.

**Remediation observed after review:** the registry now contains deliberate
permissions for the actual appendix and summary locations; overbroad
Introduction annotations were narrowed; and `check_claims.py` now maps every
manuscript/appendix file to a section permission and treats only E01 in the
named correction subsection as `discussion_evidence_correction`. The revised
checker passes all 17 annotated IDs.

**Gate:** closed after rebuilding `logic_lock.json`; the claim checker and
logic-lock verifier both pass.

### P1-03 — Results figures and tables render inside the Discussion, and Figure 7 is not legible at manuscript scale

**Exact locations**

- `paper/sections/06_results.tex:175-206`
- `paper/main.tex:58-60`
- `paper/scripts/generate_figures.py:166-176`
- compiled PDF page 18

**Evidence**

In the compiled artifact, Section 7 and subsection 7.1 begin on page 17.
Figure 7 and Table 6, both belonging to Results subsection 6.5, then appear on
page 18 between Discussion subsections 7.1 and 7.2. This makes the evidence
displays read as if they were part of the Discussion and separates them from
the Results prose that defines their populations.

The Figure 7 rendering also has overlapping x-axis labels
(`projection`/`deadline-miss`) and the right-panel y-axis label intrudes into
the inter-panel area. The population error in P1-01 makes this more than a
typographic nuisance: the visual grouping itself encourages the wrong
denominator reading.

**Exact required fix**

1. After correcting P1-01, increase figure width or use constrained layout,
   shorten/rotate category labels, and reserve enough inter-panel spacing for
   the runtime y-axis label.
2. Prevent Results floats from crossing the Section 7 boundary. A direct fix is
   to load `placeins` and insert `\FloatBarrier` at the end of
   `sections/06_results.tex`; an explicit Results float page before Discussion
   is also acceptable.
3. Recompile from a clean build and visually inspect at least the Results/
   Discussion boundary pages at the final PDF scale.

**Remediation verified after review:** `main.tex` now loads
`\usepackage[section]{placeins}`. In the regenerated 27-page PDF, Tables 4--6
and Figure 7 remain on Results pages 17--18, and Section 7 begins on page 19.
The revised Figure 7 labels are readable at manuscript scale and its
denominators are explicit.

**Gate:** closed.

## P2 findings

### P2-01 — Figure 4's generated predicate is narrower than its label and caption

**Exact locations**

- `paper/scripts/generate_figures.py:140-150`
- `paper/sections/04_method.tex:109-118`

The generated green set is selected only by endpoint
`abs(a1) <= 8.2` and `abs(v1) <= 4.1` for the hard-coded origin state,
10-ms period, and jerk sweep. The legend calls it the “one-step executable
set,” while the caption attributes the accepted set to continuous checks,
stopping viability, and next-step existence. Those additional predicates are
not evaluated by the figure script, and the black point and fixed slice are
not defined in the caption.

**Exact recommended fix:** either compute the displayed mask with the actual
segment, stopping-viability, and next-step predicates, or rename it
“endpoint-V/A-admissible constant-jerk image.” State the fixed current state,
period, V/A/J limits, and black-point meaning in the caption.

**Remediation:** the display and caption now use the narrower endpoint-V/A
language, state the fixed slice and limits, define the black point, and reserve
the additional acceptance predicates for the surrounding method text.

### P2-02 — The LaTeX log checker cannot count overfull boxes

**Exact locations**

- `paper/scripts/check_latex_log.py:30`
- `paper/build/main.log:715-740`

The regex `r"Overfull \[hv]box"` matches a literal `[` rather than the
backslash in `Overfull \hbox`. The checker reports zero overfull boxes even
though the reviewed log contains an overfull hbox of 1.0216 pt and several
underfull boxes associated with Table 1.

**Exact recommended fix:** use a pattern that matches TeX's literal backslash,
for example `r"Overfull \\[hv]box"` after a unit test with a representative
log line. Decide and
document whether small overfull boxes warn or fail. The current Table 1 is
readable, but its aggressive word breaking should be improved with adjusted
column widths or `tabularx`.

### P2-03 — Figure/table numbering and scientific-number typography can be polished

**Exact locations**

- `paper/generated/numbers.tex:16`
- `paper/sections/06_results.tex:53-55`
- compiled PDF pages 14 and 17-18

`\AnalyticPVPVAMaxRMSEDifference` is emitted as text-style `1.73e-14`, which
renders less cleanly than a proper scientific number. Several large counts are
also rendered without thousands separators.

**Exact recommended fix:** emit SI-compatible numeric macros and use `\num{}` /
`\SI{}` so the PDF prints `1.73 \times 10^{-14}` and grouped counts. This is a
presentation improvement; the rounded value itself is source-consistent.

### P2-04 — Human publication metadata remain placeholders

**Exact locations**

- `paper/metadata.tex:7-15`
- `paper/logic/logic_lock.json:45-48`

The PDF visibly contains “Author name pending,” “Affiliation pending,” and
“Email pending.” The logic lock already records this as an unresolved human
blocker.

**Exact recommended fix:** retain the placeholders for the Draft PR if author
metadata are intentionally withheld, but require named author, affiliation,
contact metadata, and any required ORCID/funding/acknowledgement disclosures
before public arXiv submission. This does not independently block the Draft PR.

## Claim and evidence audit

### Passed boundaries

- **C01/C02 timing:** target time, posterior represented time, availability
  time, future-objective time, executable-target time, command time, and
  measurement/plant time are kept distinct. The manuscript does not turn the
  one-cycle API convention into a universal physical-lag claim.
- **C03/C04 analytic evidence:** velocity benefit is limited to three
  pre-specified smooth analytic references under one fixed follower protocol.
  PV/PVA equality is correctly described as a position-endpoint non-result, not
  acceleration irrelevance or estimator equivalence.
- **C05 derivative timing:** offline-centered and delayed-causal centered
  derivatives are distinguished; derivative group delay is not equated with
  end-to-end best lag.
- **C06/C07 CSV evidence:** every tracking statement names the complete finite-
  difference-derived/projection/follower pipeline. P/PV zero projection and PVA
  32.64% projection are disclosed, and raw acceleration is called a target
  diagnostic rather than robot or command acceleration.
- **C08 oracle:** the next-cycle analytic state is a noncausal, one-future-
  sample, near-zero-error sanity control rather than a deployable predictor or
  general bound.
- **C09 method:** current-state selection, exact constant-jerk integration,
  continuous V/A/J checks, stopping viability, next-step existence, fallback,
  and nonviable-state emergency behavior match the reviewed implementation
  boundary. No global-minimizer or arbitrary-disturbance theorem is claimed.
- **C10 prose/tables:** outside the defective Figure 7 path, the prose keeps the
  42,199 command-audit, 42,079 transition, and 30,199 primary runtime
  populations distinct; the `-1e-8` acceptance tolerance and zero V/A margins
  are disclosed.
- **C11/C12 identity:** exact Ruckig segments, sampled fallback limitations,
  average acceleration-difference jerk, native ordinary Ruckig, shielded
  Ruckig, direct execution, and algorithm-changing fallback are not collapsed.
- **E01:** the 77.38% value appears only in the named Discussion correction
  passage. That paragraph states 40,510/42,199 fallback cycles (95.9975%), the
  algorithm change, non-ordinary/non-same-follower status, withdrawn
  confirmation, unchanged v3 bytes, and no v3 rerun.
- **N01-N03:** the single development trace, lack of independent real
  confirmation, lack of hardware/dynamics/torque/collision evidence, and lack
  of a fresh same-follower confirmation are present in the abstract, body, and
  conclusion at appropriate scientific scope.

### Literature audit

The related-work section uses the 16 registered bibliography entries and does
not make a novelty-priority claim. Primary literature supports jerk-limited
trajectory generation, governor theory, predictive optimization, filtering,
and numerical differentiation. Official Ruckig sources are used narrowly for
current Tracking Interface/API behavior, with an explicit statement that
`Trackig` was not evaluated and is not shown to be necessary or superior.
There are no unresolved citations or references in the reviewed PDF.

## Frozen-state and no-new-experiment audit

Repository-state checks support the drafting constraint:

- `paper/scripts/verify_v3_immutability.py` reports that the frozen v3 root of
  trust is verified and that there is no frozen-path working-tree diff;
- `git diff` contains no tracked change under
  `results/paper_evidence_v3/`, `protocol_status_v3.json`,
  `protocol_status_v3_postreview.json`, `EXPERIMENT_PROTOCOL_V3.md`, or
  `V3_POSTREVIEW_ADDENDUM.md`;
- the frozen v3 root artifact index remains
  `12393579515e144f8cb499144772471e3a0398d8d2e19bdff89ff0fa7c479933`;
- the paper tree records only the explicitly labelled post-freeze compatibility
  regression as new numerical execution.

Repository state cannot prove that an unrecorded command was never invoked, but
there is no artifact, hash, status, identity, or worktree evidence of a v3
rerun or v4 experiment. No v3/v4 experiment was run during this review.

## Final Draft PR gate

**Gate result at last verification: fail pending generated-table/PDF
regeneration for P1-01 and logic-lock regeneration for P1-02. P1-03 is
closed.**

After those fixes, rerun:

1. logic/claim validation and logic-lock verification;
2. evidence extraction, number/table/figure generation, and provenance checks;
3. a clean LaTeX build with citation/reference/log checks;
4. visual inspection of all 26 pages, especially the Results/Discussion
   boundary; and
5. frozen-v3 immutability verification.

Once the two stale generated artifacts are rebuilt and the revised checks pass,
all three P1 findings are closed without weakening the evidence boundaries, and
no scientific claim blocker identified by this review remains for a **Draft**
PR. Human metadata and the missing independent/fresh experiments remain
declared release/scientific-scope limitations rather than reasons to expand the
current experiment program.
