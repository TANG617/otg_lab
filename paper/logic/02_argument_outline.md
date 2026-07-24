# Argument outline

This outline is a reasoning contract, not draft prose. Every section must
advance the central argument:

1. A position-only moving reference exposes a time and state-definition
   problem.
2. Ordinary state-to-state OTG solves a different semantic problem from moving
   reference following.
3. Reliable target state can help, but differentiation, prediction, and
   feasibility cannot be collapsed into one finite difference.
4. A layered, time-explicit formulation is required.
5. A stateful one-step executable-target governor provides dynamically
   consistent adjacent commands under its stated model.
6. Controlled evidence separates target-state value, estimator causality,
   prediction timing, and command feasibility.
7. A fresh same-follower V4 attempt produced a large observed RMSE difference,
   but the frozen preregistered validity/runtime failures keep the result
   non-confirmatory; real-stream performance and PVA superiority therefore
   remain unsupported.

Main-text word-count target: approximately 7,800 words, within the charter's
7,000--9,000 range.

## Abstract — 180--230 words

- **Section objective:** State the practical input/output mismatch, method,
  evidence that survives audit, negative evidence, and primary limitations.
- **Reader question:** What is the paper about, what did it construct, and what
  does the evidence actually support?
- **Input assumptions:** Position-only stream; state-to-state jerk-limited OTG;
  explicit next-cycle command; current Phase A, one development CSV, frozen
  synthetic v3 evidence, and the frozen V4 non-confirmatory attempt.
- **Claims used:** C01--C10, C12, C14--C17, C19, N01--N03, and E02
  qualitatively. C11, C18, E01, and every exact V4 percentage are forbidden,
  including implicit profile-correction language or the 77.38% number.
- **Evidence used:** `E_PHASE_A_TRACKING`, `E_PHASE_A_DERIVATIVES`,
  `E_PHASE_A_ORACLE`, `E_REAL_CSV_NEGATIVE`, `E_V3_DIRECT_SAFETY`,
  `E_V3_RUNTIME`, `E_V4_FRESH_LOCKED_TEST`,
  `E_V4_SAME_INFORMATION_FAILURE`, and `E_V4_RUNTIME_FAILURE`.
- **Figures/tables:** None.
- **Transition:** Motivate why streamed reference following needs a
  time-explicit treatment rather than another target-state heuristic.
- **Prohibited detours:** Literature priority, 77.38%, ordinary-Ruckig
  performance ranking, implementation history, hardware language, detailed
  equations.
- **Expected word count:** 200.

## 1. Introduction — 900 words

- **Section objective:** Define the engineering input, explain the semantic
  mismatch, make differentiation's limited role concrete, and enumerate the
  scoped contributions and non-contributions.
- **Reader question:** Why is a 100-Hz position stream not an ordinary
  state-to-state endpoint problem, and why is the paper useful without a PVA
  superiority result?
- **Input assumptions:** A new reference sample may arrive each tick; ordinary
  OTG returns a future state; reference and execution state are different
  inputs; V/A/J constraints apply to execution.
- **Claims used:** C01, C02, C03--C05, C09--C14, C16, C19, N01--N03.
- **Evidence used:** Phase A summary, current time/profile-aware interfaces,
  frozen direct/provenance summaries.
- **Figures/tables:** Figure 1 (layered architecture); optionally Figure 2
  preview at the end of the section.
- **Transition:** Having separated the paper's claims from performance
  overreach, situate the formulation among OTG, governors, estimation, and
  moving-reference methods.
- **Prohibited detours:** A results dump; claims of “first”; calling the
  development CSV representative; introducing E01; treating the observed V4
  effect as confirmed; adding excluded product-specific claims.
- **Expected word count:** 900.

## 2. Related work — 950 words

- **Section objective:** Organize prior work by problem role and identify the
  specific combination addressed here: position-only information, explicit
  clocks, executable targets, and method/evidence identity.
- **Reader question:** Which existing methods solve state-to-state OTG,
  moving-reference following, command governance, estimation, or predictive
  control, and where does this paper connect them?
- **Input assumptions:** Only verified primary literature and official API
  behavior from `05_literature_matrix.md`; absence of a cited method is not
  evidence of absence.
- **Claims used:** C01, C02, C05, C09, C11, C12. These frame distinctions; they
  do not claim novelty priority.
- **Evidence used:** Verified bibliography and official Ruckig documentation;
  no experiment artifacts.
- **Figures/tables:** Table 2 (method taxonomy and information condition), if
  it improves comparison clarity.
- **Transition:** Convert the thematic distinctions into one explicit
  sampled-time problem.
- **Prohibited detours:** Paper-by-paper summaries; blogs as algorithm
  evidence; unverified DOI/metadata; “no prior work”; claims that official
  tutorials prove novelty; portraying `Trackig` as evaluated.
- **Expected word count:** 950.

## 3. Problem formulation — 1,050 words

- **Section objective:** Formalize
  \(p^{\mathrm{ref}}_{0:k},x_k^{\mathrm{cur}}\mapsto
  x_{k+1}^{\mathrm{cmd}}\), the causal information set, clocks, state roles,
  constraints, feasibility predicates, and evaluation clock.
- **Reader question:** Which physical time does each state represent, when is
  it available, and what does “feasible” mean at each boundary?
- **Input assumptions:** Nominal control grid \(t_k=kDT\); arrival/source times
  may differ; reference is the evaluation signal; future prediction is not
  truth; CSV derivatives are unavailable.
- **Claims used:** C01, C02, C05, C08, C11, C12, N01.
- **Evidence used:** `E_CURRENT_PROFILE_AWARE_INFRASTRUCTURE`,
  `E_PHASE_A_DERIVATIVES`, `E_PHASE_A_ORACLE`; data dictionary for field
  semantics.
- **Figures/tables:** Figure 2 (timeline); Figure 4 (derivative timing);
  Table 1 (notation/time semantics).
- **Transition:** The predicates establish what the executable-target method
  must construct and what the follower must preserve.
- **Prohibited detours:** Treating `minimum_duration` as a deadline; collapsing
  source time into arrival time; calling a predictor oracle; using CSV
  derivatives as truth; assuming command equals measured state.
- **Expected word count:** 1,050.

## 4. Method — 1,400 words

- **Section objective:** Specify interfaces and the one-step governor, then
  define direct and ordinary-Ruckig execution, profile auditing, shielding,
  and fallback identities.
- **Reader question:** How does the pipeline turn a causal posterior and future
  reference into a dynamically consistent next-cycle command, and how is that
  command actually audited?
- **Input assumptions:** Triple-integrator command model; known positive
  \(DT,v_{\max},a_{\max},j_{\max}\); finite inputs; a viable current state for
  the invariant-preserving path; explicitly selected feedback mode.
- **Claims used:** C02, C05, C09, C11, C12.
- **Evidence used:** Current estimators, predictors, governors, constraints,
  followers, typed profiles, and schema; current code supports construction,
  not a fresh empirical performance claim.
- **Figures/tables:** Figure 1 (interfaces); Figure 5 (governor construction);
  Table 2 (method taxonomy).
- **Transition:** The method definitions determine the information conditions,
  baselines, audits, and denominators required in the experiments.
- **Prohibited detours:** Global optimality; arbitrary-disturbance recursive
  feasibility; certified safety; claiming current post-freeze code was run as
  v3; treating an average acceleration-difference jerk as internal Ruckig
  jerk; silently changing algorithms via fallback.
- **Expected word count:** 1,400.

### 4.1 State-estimator interface

- Posterior carries represented state time and availability time.
- Delayed estimates remain delayed unless a stated motion model propagates
  them.
- Position anchoring, reset, and plant-feedback correction are separate events.

### 4.2 Future-reference generator

- Prediction time is explicit; horizon and estimator delay are separate.
- Analytic/offline future access is labelled oracle, never online prediction.

### 4.3 Target-component selection

- P, PV, and PVA define the components exposed to the downstream method, not
  follower identities.
- The same-follower requirement is used by V4; its satisfaction does not
  override the separately failed preregistered same-information gate.

### 4.4 One-step bounded-jerk executable-target governor

- Present exact constant-jerk integration.
- Define the weighted tracking/regularization objective as an implementation
  choice, not global optimality.
- Intersect V/A/J, stopping-viability, and next-step-existence conditions.
- Distinguish normal invariant-preserving action, explicit safe fallback, and
  best-effort emergency recovery.

### 4.5 Followers and command profiles

- Direct follower executes the governor's constant-jerk action.
- Ordinary Ruckig executes its native piecewise-constant-jerk prefix.
- Exact exposed profiles are integrated segment-by-segment; sampled fallback
  leaves internal jerk unavailable rather than inventing it.

### 4.6 Native, shielded, and fallback identities

- Record declared method, native generator, actually executed algorithm,
  shield decision, and fallback controller.
- Algorithm-changing fallback changes the experimental method identity.

## 5. Experimental protocol — 900 words

- **Section objective:** Separate five evidence programs and fix the clocks,
  limits, information conditions, statistics, and freeze boundaries for each.
- **Reader question:** Which comparisons are controlled, which are oracle or
  development-only, and which observations are frozen?
- **Input assumptions:** Phase A uses \(DT=10\) ms and limits 4.1/8.2/4000 in
  rad-derived units; fixed-grid CSV reads each row as 10 ms; v3 and V4 are
  immutable and not rerun; post-freeze compatibility is a separate regression.
- **Claims used:** C03--C08, C10, C13--C19, N01, N03. The protocol
  distinguishes frozen design, statistical classification, effective
  classification, and post-review aids without stating E01 or its estimate.
- **Evidence used:** All Phase A/current CSV sources; frozen v3 direct,
  runtime, integrity, and confound sources; all registered V4 sources;
  post-freeze compatibility source.
- **Figures/tables:** Tables 2--5; no confounded comparison as a main figure.
- **Transition:** Results answer one predeclared scientific question at a time
  using only the matching evidence class.
- **Prohibited detours:** Pooling current/frozen/post-freeze numbers; calling
  Phase A oracle conditions deployable; using OFAT limits as recommendations;
  retroactively turning v3 into a clean same-follower ablation; treating V4's
  exactly-once execution or large effect as confirmation.
- **Expected word count:** 900.

### 5.1 Phase A analytic references

- Three smooth analytic references pre-specified in the repository before the
  checked-in aggregate result, with P/PV-truth/PVA-truth.
- Derivative timing variants and next-cycle analytic oracle remain separately
  labelled.

### 5.2 Development CSV

- One 1,936-row development trace under fixed 10-ms row semantics.
- No velocity/acceleration truth; derivatives are estimates/diagnostics.

### 5.3 Frozen synthetic v3

- Preserve 120-trajectory/42,199-cycle denominators, frozen identity, and
  immutable artifacts.
- Use direct-governor safety/runtime and integrity, not affected baseline
  performance, as primary evidence.

### 5.4 Post-freeze compatibility and profile correction

- Report current regression only as infrastructure/compatibility evidence.
- Explicitly state that no v3 samples or summaries were regenerated.

### 5.5 Fresh V4 same-follower confirmation attempt

- Fresh identities/seeds with zero V1/V2/V3 overlap; six families and 120
  locked test trajectories; primary direct P/PV/PVA pipelines differ only in
  `target_mode`.
- Same estimator, predictor, horizon, governor, follower, plant, limits, and
  population; whole-trajectory pairs; 10,000 bootstrap resamples.
- Exactly-once raw execution at confirmation source `461fc560`; report-only
  packaging/diagnosis is separate; no raw rerun or resume.
- Offline oracle is noncausal/nondeployable and ordinary Ruckig is contextual
  only.
- Freeze boundary: `failed_test_visible_frozen`; statistical classification:
  `strongly_material`; effective classification:
  `invalid_method_identity`.

## 6. Results — 1,350 words

- **Section objective:** Answer each empirical question with result,
  interpretation, and boundary; preserve negative and corrective evidence.
- **Reader question:** What did reliable state information, causal timing,
  future timing, and executable command construction change under each scoped
  protocol?
- **Input assumptions:** Generated numbers/tables are authoritative; every
  result paragraph maps to a Claim ID and evidence source.
- **Claims used:** C03--C08, C10--C19, N01, N03, E02. E01 is excluded from
  Results.
- **Evidence used:** Source-backed generated artifacts only.
- **Figures/tables:** Figures 3, 4, 6, 7 and at most one V4 main figure;
  Tables 3--5 plus one V4 gate/status table. Remaining V4 displays are
  appendix-only.
- **Transition:** The discussion synthesizes why the supported formulation
  matters despite unresolved comparative performance.
- **Prohibited detours:** Headlining 77.38%; ranking current ordinary Ruckig
  against the direct method; hiding CSV or acceptance failures; saying
  “significant” without a defined statistical test; treating zero counts as
  universal guarantees; presenting the V4 effect without observed,
  failed-validity-gate, and non-confirmatory language.
- **Expected word count:** 1,350.

### 6.1 Derivative accuracy and causality

- Question: what information does each finite-difference timestamp require?
- Claims/evidence: C05 / `E_PHASE_A_DERIVATIVES`.
- Boundary: formula accuracy and online availability are separate.

### 6.2 Value of reliable target velocity

- Question: does reliable analytic velocity help under clean smooth
  conditions?
- Claims/evidence: C03 / `E_PHASE_A_TRACKING`.
- Boundary: analytic truth is not an estimator result.

### 6.3 Limits of acceleration conclusions

- Question: does analytic acceleration add position-following benefit here?
- Claims/evidence: C04 / `E_PHASE_A_TRACKING`.
- Boundary: PV and PVA reached the same requested endpoint position/velocity;
  differing target acceleration did not change the recorded position endpoint.
  This is not evidence about acceleration-estimator quality, internal profile
  quality, control effort, universal usefulness, or equivalence.

### 6.4 Future-reference oracle

- Question: what happens when the target state belongs to the next output time?
- Claims/evidence: C08 / `E_PHASE_A_ORACLE`.
- Boundary: noncausal one-future-sample, one-step-reachable,
  near-zero-error/zero-grid-lag protocol sanity control, not a general bound.

### 6.5 Development-CSV negative evidence

- Question: do raw differences improve the only development trace, and are the
  raw targets admissible?
- Claims/evidence: C06, C07, N01 / `E_REAL_CSV_NEGATIVE`.
- Boundary: position metrics describe the full finite-difference-derived
  pipeline, including fixed admissibility projection when triggered and
  ordinary Ruckig. P/PV project 0%; each PVA condition projects 32.64%.
  Projection is not matched, so estimator and projection effects are not
  isolated. Raw targets are pre-projection diagnostics only.

### 6.6 Executable-target safety and runtime

- Question: were the frozen direct commands continuously legal, and did the
  separately defined runtime population meet its recorded deadline?
- Claims/evidence: C10 / `E_V3_DIRECT_SAFETY`, `E_V3_RUNTIME`.
- Boundary: safety counts use 42,199 command cycles and the \(-10^{-8}\)
  registered margin tolerance (observed V/A minima are zero); runtime counts
  use 30,199 post-warm-up cycles. The frozen PVA-named label does not attribute
  safety to PVA components.

### 6.7 Ordinary-Ruckig profile correction

- Question: why was the historical constant-jerk audit invalid for a native
  Ruckig prefix?
- Claims/evidence: C11, C12 / current profile-aware infrastructure plus
  post-review finding.
- Boundary: current repair is not a v3 rerun or comparative result.

### 6.8 Fresh V4 same-follower attempt

- Question: did target-component conditioning change trajectory-level RMSE in
  the fresh same-follower test?
- Observed result: complete 120/120 primary pairs contain a large observed
  PVA-versus-P RMSE difference; secondary P-versus-PV and PV-versus-PVA
  comparisons, five harmful trajectories, and rapid-reversal heterogeneity
  remain visible.
- Validity assessment: method purity/synthetic safety passed, but five of
  42,072 composite `event_flags` entries differed only by `deadline_miss`;
  lag noninferiority was not established; the full Python runtime gate failed.
- Interpretation boundary: the five rows cannot be removed, the diagnosis
  does not show estimator/predictor information differed, and effective
  classification `invalid_method_identity` makes the effect
  non-confirmatory.
- Claims/evidence: C14--C19, N03, E02 / all registered `E_V4_*` sources.

## 7. Discussion — 850 words

- **Section objective:** Explain the practical implications, why the current
  contribution remains valuable, and which evidence is still needed.
- **Reader question:** What should a system designer infer, and what must not be
  inferred?
- **Input assumptions:** No new results; all causal explanations are tied to
  the formulation or stated as hypotheses.
- **Claims used:** C01--C19, N01--N03, E01 only as evidence correction, and
  E02 only as a non-confirmatory result.
- **Evidence used:** Same audited sources as Results; no new numeric analysis.
- **Figures/tables:** Table 6 (claim/evidence boundaries) if needed.
- **Transition:** The conclusion can now summarize supported claims without
  reviving corrected or unresolved performance statements.
- **Prohibited detours:** Deployment advice; claims that PVA is harmful in
  general; claiming ordinary Ruckig cannot follow moving references; treating
  `Trackig` as necessary; minimizing the negative CSV or v3 confound.
- **Expected word count:** 850.

Required topics:

- estimator quality as a prerequisite;
- future-reference timing versus target components;
- why PVA may be harmful when acceleration estimates are noisy/inadmissible;
- acceleration-active regimes not represented by the Phase A references;
- ordinary state-to-state OTG versus moving-reference following;
- profile semantics and direct versus second-stage shaping;
- native/shielded/fallback identity;
- real-time evidence scope and hardware dependence;
- negative CSV result and fixed-grid limitation;
- why V4's large observed effect cannot substitute for a passed validity gate;
- `deadline_miss` as the only differing composite event token, without
  claiming estimator/predictor information differed;
- method-purity/safety passes, lag noninferiority failure, Python runtime
  failure, harmful trajectories, and rapid-reversal weakness;
- future V5 layered gates: exogenous/internal validity, performance
  guardrails, and deployment/runtime, without reinterpreting V4;
- independent real/hardware studies.

### 7.1 Evidence correction and method identity

- **Question:** Which frozen interpretations survive the exposed baseline
  identity?
- **Claims/evidence:** C12, C13, E01, N03 /
  `E_V3_CONFOUNDED_COMPARISON`, `E_V3_ARTIFACT_INTEGRITY`.
- **Required single-paragraph disclosure:** 77.38% is an observed exploratory
  regression; `predicted_p` used algorithm-changing fallback on
  40,510/42,199 cycles; the comparison is neither ordinary Ruckig nor a
  same-follower ablation; confirmatory status was withdrawn; v3 bytes were
  unchanged; and v3 was not rerun.
- **Boundary:** This named Discussion subsection or a correction/provenance
  appendix is E01's only home.

## 8. Conclusion — 300 words

- **Section objective:** Summarize only confirmed/scoped findings and close with
  explicit work still required before performance or deployment claims.
- **Reader question:** What can be retained as the paper's durable conclusion?
- **Input assumptions:** No new evidence, citations, or numbers.
- **Claims used:** C01--C14, C16, C17, C19, and N01--N03 as marked in the
  claim matrix. C15, C18, E01, E02, and exact V4 percentages are forbidden;
  N03 carries the conclusion boundary.
- **Evidence used:** No source beyond already reported results.
- **Figures/tables:** None.
- **Transition:** End; no future-work catalog beyond the decisive confirmation
  and evaluation requirements.
- **Prohibited detours:** 77.38%; PVA superiority; ordinary-Ruckig ranking;
  “real robot,” “deployment ready,” “production safe,” “state of the art,” or
  “first.”
- **Expected word count:** 300.

Required V4 conclusion sentence:

> A fresh locked attempt produced a large observed RMSE difference but did not
> establish a confirmatory PVA benefit because the preregistered validity and
> runtime gates were not all satisfied.

## Appendices — outside main-word target

### Appendix A: Governor derivation

- Expand the jerk-interval, continuous extrema, stopping-envelope, and
  next-step-existence construction.
- State assumptions before guarantees; do not introduce global optimality.

### Appendix B: Experiment details

- Preserve exact protocol values, selectors, denominators, and information
  conditions without repeating the Results narrative.

### Appendix C: Negative results and evidence correction

- Retain the development-CSV failures, v3 acceptance failures, fallback rates,
  and E01 correction.

### Appendix D: Evidence provenance

- Map claims/numbers/displays to source IDs, paths, commits, and hashes.

### Appendix E: Reproducibility

- Explain whole-trajectory denominators, recomputation, frozen identities,
  packaging, and the separation of v3 from post-freeze code.

### Appendix F: Frozen V4 confirmation attempt

- Protocol/commit/hash table and exactly-once timeline.
- Primary observed result and complete gate table.
- Five-row same-information diagnosis, harmful trajectories, family/demand
  effects, and runtime table.
- Ordinary-Ruckig incomplete denominator and offline-oracle boundary.
- V3 immutability, V4 no-rerun/no-resume status, and V5-only future
  confirmation boundary.
