# Adversarial logic review

Review role: Agent G, logic-lock pass only  
Review date: 2026-07-23  
Repository HEAD reviewed: `1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe`  
Scope: `paper/logic/`, the registered Phase A and frozen-v3 evidence, current
post-freeze method/profile code, and the literature matrix. No experiment was
run and no manuscript, logic, evidence, or frozen file was edited.

## Disposition

**No P0 finding. Seven P1 findings and five P2 findings remain. Do not issue
the logic lock until the P1 items are resolved and the claim registry,
human-readable matrix, outline, and decision log agree.**

The central scientific positioning is defensible: the current evidence
supports a timing/feasibility/method-identity paper, not PVA superiority,
ordinary-Ruckig inferiority, real-stream generalization, or robot safety. The
logic layer preserves the principal negative results and correctly demotes the
77.38% frozen comparison. The findings below are boundary failures that could
nevertheless recreate overclaiming in the manuscript.

## P1 findings

### P1-01 — E01 is scheduled in sections that `claims.yaml` forbids

**Evidence.** `claims.yaml` permits E01 only in
`discussion_evidence_correction`, `limitations`, and the two correction/
provenance appendices. In contrast, `02_argument_outline.md` includes E01 in
the Experimental Protocol (`§5`) and in Results `§6.8`. The human matrix says
“Discussion evidence correction or appendix,” while the task specification
also contemplates a dedicated evidence-correction subsection. These three
contracts are not isomorphic.

**Risk.** The 77.38% number can leak into a generic Protocol or Results section,
where it will read as a performance result even if called exploratory.

**Exact fix.**

1. Remove E01 from the claims used by Experimental Protocol in
   `02_argument_outline.md`; protocol may state only that frozen and
   post-review classifications are distinct, without the 77.38% estimate.
2. Choose one permitted home: either move `§6.8` to a named Discussion
   subsection, or add an explicit `results_evidence_correction` permission to
   both `claims.yaml` and `01_claim_evidence_matrix.md`.
3. Require the E01 passage to contain, in the same paragraph: 77.38% is an
   observed exploratory regression; `predicted_p` used algorithm-changing
   fallback on 40,510/42,199 cycles; it is not ordinary Ruckig or a
   same-follower ablation; confirmatory status was withdrawn; v3 bytes were
   unchanged; v3 was not rerun.
4. Record the permission change in `decision_log.md`.

### P1-02 — C06 and the CSV display plan conflate raw derivative targets with the executed projected pipeline

**Evidence.** The CSV PVA rows in
`target_state_ablation_metrics.csv` have
`target_projection_rate=0.3264355923435075`. Their position RMSE therefore
describes finite-difference estimates followed by the fixed target projection
and ordinary Ruckig, not unmodified raw PVA targets. Nevertheless:

- C06 says “unfiltered finite-difference targets” without saying that
  admissibility projection is applied when triggered;
- `06_scope_and_limitations.md` says “none of the raw finite-difference target
  conditions improves”;
- Figure 7 and Table 4 use “raw FD conditions/targets” for the tracking
  comparison.

“Unfiltered derivative estimate” and “unprojected executable target” are
different statements.

**Risk.** A reviewer can reasonably interpret the comparison as raw FD target
execution and attribute the negative result solely to differentiation, while
the actual causal chain also includes a projection that jointly scales
velocity and acceleration.

**Exact fix.**

1. Change C06 to: “On the single development CSV, none of the tested
   unfiltered finite-difference-derived conditions—using the protocol’s fixed
   admissibility projection when triggered—outperformed the P-only condition.”
2. In `claims.yaml`, `01_claim_evidence_matrix.md`,
   `06_scope_and_limitations.md`, and Figure 7/Table 4 plans, reserve **raw
   target** for pre-projection diagnostics and call the tracking rows
   **finite-difference-derived pipeline conditions**.
3. State that P and PV rows had zero projection while every tested PVA row had
   32.64% projection; do not present projection as a matched factor.
4. Keep the allowed inference negative and scoped: the complete tested
   pipelines failed to improve this trace; the data do not identify the
   isolated causal effect of the derivative estimator versus projection.

### P1-03 — C10 joins safety and runtime populations in one canonical event claim

**Evidence.** C10 says the direct condition completed “the recorded locked-test
command cycles without ... deadline miss.” Its support then combines 42,199
command-profile/invariant cycles with a separate 30,199-cycle post-warm-up
runtime population. The display plan is clearer than the canonical claim, but
the canonical sentence still invites a single-denominator reading.

The continuous V/A criteria also passed against a registered audit tolerance
of `-1e-8`; their observed minimum margins are exactly zero. The current claim
does not expose that tolerance or the lack of strict positive V/A margin.

**Risk.** The manuscript may report “0/42,199 deadline misses,” or imply strict
interior safety when the frozen observations touch the V/A limits.

**Exact fix.**

1. Rewrite C10 as two sentences: (a) 42,199 frozen command cycles had zero
   recorded continuous V/A/internal-J violations, projection, or fallback, and
   42,079/42,079 adjacent transitions were consistent; (b) the separately
   defined 30,199-cycle post-warm-up runtime population had zero 10-ms deadline
   misses, p99 874.91866 µs, and maximum 2,113.25 µs.
2. Add the constraint audit threshold/tolerance and state that minimum V/A
   margins were zero, not strictly positive.
3. In every caption/table, print a denominator beside each zero count; never
   place the deadline zero in a row whose denominator is 42,199.
4. Clarify that `one_step_governed_pva_direct` is a frozen method label and
   that C10 does not attribute the safety observation to the use of PVA target
   components.

### P1-04 — Table 3 has no coherent metric/units contract

**Evidence.** `04_figures_and_tables_plan.md` makes Table 3 cover derivative
accuracy and position tracking, but its planned columns are only
“RMSE [rad]” and “max error [rad].” Velocity and acceleration derivative errors
have units rad/s and rad/s², while position-following errors have units rad.

**Risk.** The generated table can silently mix unlike quantities under one
column, violating the paper’s own unit and metric rules and making the
derivative result scientifically uninterpretable.

**Exact fix.** Split Table 3 into (A) derivative accuracy with separate
velocity-RMSE [rad/s] and acceleration-RMSE [rad/s²] columns and (B) position
following with position RMSE/max error [rad] and lag [ms]; alternatively use a
long-form `quantity`, `metric`, `value`, `unit` layout. Do not put derivative
and tracking RMSE in a single numeric column.

### P1-05 — C04 records the non-result but omits the endpoint-identity mechanism

**Evidence.** In Phase A, PV truth and PVA truth use the same terminal position
and velocity at time \(k\), differ only in terminal acceleration, have
`reachable_within_10ms_rate=1`, and are executed by the same ordinary-Ruckig
follower. The recorded position endpoints and lag are therefore numerically
identical. The logic correctly says this is not an equivalence claim, but it
does not require the manuscript to explain that the selected endpoint
position metric is structurally insensitive once both conditions reach the
same target \(p,v\) each tick.

**Risk.** “Acceleration added no value” can be read as an estimator or dynamics
finding rather than a result of this endpoint/evaluation construction in an
inactive regime.

**Exact fix.** Add to C04’s limitation, the Results boundary, and Figure 3
caption: both conditions reached the same target position and velocity at the
evaluated endpoint; acceleration differed but did not change the recorded
position endpoint under this protocol. Preserve “no observed position-metric
improvement,” and prohibit inference about acceleration estimation, internal
profile quality, control effort, or acceleration-active conditions.

### P1-06 — C11 uses the 96% fallback confound as if it quantitatively supports piecewise-profile semantics

**Evidence.** C11 is a structural statement: a Ruckig control-period prefix may
contain multiple constant-jerk segments and must be audited segment by segment.
The approximately 96% frozen fallback rate shows the consequence of a
historical single-jerk reachability test and the loss of baseline identity. It
does **not** prove that 96% of native prefixes contained multiple segments or
that any particular fallback was a true native profile violation.

`claims.yaml` currently places this fallback association inside C11’s “exact
quantitative support.” The literature matrix discusses Ruckig’s third-order
trajectory construction but does not explicitly bind the paper/profile source
to C11.

**Risk.** The correction can mutate into “96% of ordinary Ruckig profiles were
multi-segment” or “the corrected audit would have accepted 96%,” neither of
which was measured.

**Exact fix.**

1. Remove the fallback percentage from C11’s quantitative support; retain it
   under C12/E01 as method-identity/confounding evidence.
2. Support C11 with the current profile representation/tests and an explicit
   primary/official Ruckig source that establishes piecewise constant-jerk
   trajectory phases.
3. Add prohibited wording: “96% of prefixes were multi-segment,” “96% were
   falsely rejected solely because of an internal switch,” and “corrected
   ordinary Ruckig would pass or outperform.”
4. Keep the post-freeze/no-rerun wall.

### P1-07 — “Preregistered” in C03 exceeds the Phase A provenance record

**Evidence.** Repository history shows the three analytic reference families
were described before the aggregate result commit, which supports
**pre-specified**. However, the Phase A manifest records a dirty generation
worktree; the formal checked-in results and updated report arrived together;
non-P per-cycle outputs are absent; and no standalone frozen registration
record, timestamped protocol hash, or prospective acceptance plan is
registered for Phase A.

**Risk.** “Preregistered” communicates a stronger anti-selection procedure
than the available provenance demonstrates.

**Exact fix.** Replace “preregistered” with “pre-specified in the repository
before the checked-in aggregate result” in C03, the matrix, and all planned
prose. If the authors retain “preregistered,” register the exact pre-result
commit/path and document which references, metrics, evaluation window, and
analysis choices were fixed prospectively. Preserve the dirty-worktree and
no-raw-recomputation limitations in provenance.

## P2 findings

### P2-01 — C07 unnecessarily imports the OFAT evidence source

C07’s raw acceleration peak, 8.2-rad/s² bound, feasibility rate, and projection
rate all exist in `E_REAL_CSV_NEGATIVE`. Remove `E_PHASE_A_LIMITS` from C07
unless an OFAT conclusion is actually stated. This prevents a reader or
generator from pooling the 408-row mixed causal/noncausal limit sweep into a
single-trace target-diagnostic claim.

### P2-02 — The abstract outline implicitly admits C11 despite its explicit ban

`02_argument_outline.md` says “C11 may be implicit” in the abstract, while
`claims.yaml` sets `allowed_in_abstract: false`. Remove C11 and its implicit
content from the abstract plan; method identity can be covered by C12 without
describing the profile correction there.

### P2-03 — The causal information set duplicates current state

`03_notation_and_timing.md` defines \(\mathcal I_k\) to already include
\(x_k^{\mathrm{cur}}\), then writes the mapping input as
\((\mathcal I_k,x_k^{\mathrm{cur}})\). Include current state in exactly one
place so later proofs do not accidentally treat it as a second independent
input.

### P2-04 — “Upper bound” in C08 is needlessly strong and directionally ambiguous

The next-cycle analytic oracle is a useful protocol-specific sanity control,
and zero RMSE is a numerical performance ceiling in colloquial language, but
“upper bound” is ambiguous for an error metric whose mathematical optimum is a
lower bound. Prefer “near-zero-error, zero-grid-lag oracle sanity control” or
“protocol-specific numerical ceiling on tracking performance.” Always state
`future_samples=1`, `causal=false`, and one-step reachability of all three
oracle rows.

### P2-05 — The main contribution wording can overstate C13

`00_paper_charter.md` groups execution semantics, method identity, and
“auditable evidence” as a main contribution. Keep C13 as a reproducibility
practice/result rather than an algorithmic scientific contribution. The
artifacts preserve denominators and expose failures; they do not independently
validate the corrected interpretation or repair the confounded comparison.

## Claim-by-claim adversarial verdict

| Claim | Verdict | Required boundary |
|---|---|---|
| C01 | Supportable | Interface/index convention, not universal physical lag |
| C02 | Supportable | Conceptual decomposition, not causal independence |
| C03 | Supportable after P1-07 | Analytic truth, three deterministic references, same ordinary follower |
| C04 | Supportable after P1-05 | Position endpoint non-result only; no equivalence or acceleration-uselessness |
| C05 | Supportable | Future-sample requirement; group delay is not end-to-end lag |
| C06 | Supportable after P1-02 | Full FD-derived/projection/follower pipeline; one development trace |
| C07 | Supportable | Raw target diagnostics only; not command/plant/robot acceleration |
| C08 | Supportable after P2-04 | Noncausal one-future-sample oracle; protocol-specific |
| C09 | Supportable | Conditional triple-integrator invariant construction; emergency mode is best effort |
| C10 | Supportable after P1-03 | Separate safety/runtime denominators and audit tolerance |
| C11 | Supportable after P1-06 | Structural profile semantics; no frozen corrected outcome |
| C12 | Supportable | Taxonomy only; no performance ranking |
| C13 | Supportable | Provenance controls do not validate inference |
| N01 | Required and correctly scoped | One development trace is not generalization |
| N02 | Required and correctly scoped | No hardware/HIL/torque/collision/production evidence |
| N03 | Required and correctly scoped | No fresh same-follower locked confirmation |
| E01 | Correctly reclassified; placement inconsistent | Correction-only use with same-passage disclosure |

## Negative-result and identity audit

- C04, C06, C07, N01--N03, the three failed/unavailable acceptance criteria,
  and E01 are present; no principal negative result is suppressed at the logic
  layer.
- The logic correctly distinguishes ordinary unshielded Ruckig,
  viability-shielded Ruckig, direct constant-jerk execution, and
  algorithm-changing fallback.
- The frozen `predicted_p` condition must never be shortened to “ordinary
  Ruckig” in a result sentence or display row.
- The post-freeze compatibility record restores only a Phase A P-only
  regression. It is neither a v3 rerun nor a corrected locked comparison.
- Frozen direct safety evidence is separable from the baseline confound, but it
  is not evidence that PVA components improve safety or tracking.

## Frozen-change verification

At review time:

- `git diff --name-only` contained no tracked change under
  `results/paper_evidence_v3/`, `protocol_status_v3.json`,
  `protocol_status_v3_postreview.json`, `EXPERIMENT_PROTOCOL_V3.md`, or
  `V3_POSTREVIEW_ADDENDUM.md`;
- repository status showed only the new untracked `paper/` tree;
- the registered v3 root artifact-index SHA-256 remained
  `12393579515e144f8cb499144772471e3a0398d8d2e19bdff89ff0fa7c479933`;
- no v3 confirmation command or v4 experiment was run during this review.

## Logic-lock gate

Gate result: **conditional fail pending P1 resolution**.

After fixes, rerun the logic checker and regenerate the lock. The lock should
hash the corrected `claims.yaml`, human matrix, outline, notation, display
plan, evidence registry, decision log, and this review. P2 items may be
deferred only if they are explicitly recorded as draft-status debt and cannot
affect claim permissions, units, or evidence identity.
