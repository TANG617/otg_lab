# Decision log

Decisions in this file affect title, scope, claim wording/classification,
terminology, or evidence use. Later changes require a new dated entry rather
than rewriting history, followed by regeneration of `logic_lock.json`.

## D001 — Repository baseline

- **Date:** 2026-07-23
- **Decision:** Build the stage-draft logic layer on branch
  `paper/arxiv-stage-draft-v0` from repository HEAD
  `1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe`.
- **Reason:** This is the actual checked-out baseline; historical commit
  references are evidence provenance, not an assumed current HEAD.
- **Consequence:** The logic lock records the actual source HEAD. Frozen v3
  source/protocol/artifact commits remain separately identified.

## D002 — Scientific position

- **Date:** 2026-07-23
- **Decision:** Position the paper as methodology + system formulation +
  controlled empirical study of causal timing and executable command
  generation for a position-only moving reference.
- **Reason:** Current evidence strongly supports time semantics, layered
  responsibility, controlled Phase A observations, a one-step construction,
  direct-command audits, and evidence identity; it does not support a general
  PVA performance claim.
- **Rejected framing:** “PVA outperforms P/PV,” “governor beats ordinary
  Ruckig,” or real-robot/deployment paper.

## D003 — Selected title

- **Date:** 2026-07-23
- **Decision:** Select **From Position Samples to Executable Commands: Timing
  and Feasibility for Jerk-Limited Reference Following**.
- **Reason:** It names the actual input/output and organizing issues without
  claiming superiority, hardware, global optimality, or novelty priority.
- **Alternatives retained:** The two other conservative candidates remain in
  `00_paper_charter.md`.

## D004 — Stable claim registry

- **Date:** 2026-07-23
- **Decision:** Freeze IDs C01--C13, N01--N03, and E01 with canonical wording
  and section permissions in `claims.yaml`.
- **Reason:** Stable IDs allow LaTeX annotations and automatic gates to survive
  wording edits.
- **Consequence:** IDs are never renumbered. A changed scientific proposition
  receives a new ID or a logged canonical-wording change.

## D005 — Phase A interpretation

- **Date:** 2026-07-23
- **Decision:** Treat analytic PV/PVA truth as oracle information conditions
  that isolate target-component value, not position-only estimator results.
- **Reason:** Analytic derivatives are unavailable from the online
  position-only stream without an estimator/model.
- **Consequence:** C03 is limited to reliable analytic velocity on three
  references; C04 is a scoped non-result, not an acceleration-uselessness
  claim.

## D006 — Development CSV interpretation

- **Date:** 2026-07-23
- **Decision:** Preserve the fixed-grid finite-difference outcome as negative
  development evidence and the acceleration values as raw target diagnostics.
- **Reason:** The trace has no derivative truth and is not an independent
  locked real dataset.
- **Consequence:** C06/C07 may appear in the abstract with the single-trace and
  target-diagnostic boundaries; N01 accompanies all generalization discussion.

## D007 — Future-reference oracle interpretation

- **Date:** 2026-07-23
- **Decision:** Describe the next-cycle analytic oracle as a noncausal,
  near-zero-error, zero-grid-lag sanity control that isolates timing.
- **Reason:** It uses one future sample and therefore cannot be represented as
  an online predictor.
- **Consequence:** C08 can support the timing formulation but not deployability
  or ordinary-Ruckig optimality.

## D008 — Governor guarantee boundary

- **Date:** 2026-07-23
- **Decision:** Claim only construction/enforcement under the stated
  triple-integrator, viable-state, exact constant-jerk, known-limit, and
  feedback-mode assumptions.
- **Reason:** The current implementation provides analytic interval and
  invariant postchecks but no theorem for arbitrary disturbances, all initial
  states, or application-level safety.
- **Consequence:** Use “constructs,” “enforces under the stated model,” and
  “verifies”; prohibit global optimality, unconditional recursive feasibility,
  and certification language.

## D009 — Current profile-aware infrastructure

- **Date:** 2026-07-23
- **Decision:** Use `E_CURRENT_PROFILE_AWARE_INFRASTRUCTURE` only for command
  semantics, method identity, formal construction, and audit capability. Its
  fresh comparative performance status is `not_evaluated`.
- **Reason:** Current code/tests were produced after the v3 freeze and no new
  locked experiment was run.
- **Consequence:** C09/C11/C12 may use the current implementation as structural
  support, but no performance claim may use it.

## D010 — Ruckig prefix semantics

- **Date:** 2026-07-23
- **Decision:** Treat an ordinary Ruckig control-period prefix as a
  piecewise-constant-jerk profile, integrated/audited per segment when exact
  segments are exposed. If exact internal jerk is unavailable, record it as
  unavailable rather than infer it from endpoint acceleration.
- **Reason:** Compressing a multi-segment prefix to one acceleration-difference
  jerk invalidated the historical endpoint audit.
- **Consequence:** `acceleration_difference_jerk`, `new_jerk`, and internal
  profile jerk remain different quantities.

## D011 — Method identity

- **Date:** 2026-07-23
- **Decision:** Ordinary unshielded Ruckig, viability-shielded Ruckig, direct
  constant-jerk execution, and algorithm-changing fallback are distinct method
  identities.
- **Reason:** Scientific comparisons depend on what generated the executed
  command, not only the requested target or historical method name.
- **Consequence:** Tables and prose report native generator, actual command
  algorithm, shield, and fallback identity separately.

## D012 — Frozen v3 evidence retained

- **Date:** 2026-07-23
- **Decision:** Retain frozen direct-command continuous-constraint,
  projection/fallback, runtime, denominator, and artifact-integrity evidence
  within the synthetic v3 scope.
- **Reason:** Post-review baseline confounding does not affect the separately
  recorded direct constant-jerk profile audits or frozen provenance.
- **Consequence:** C10/C13 remain `confirmed_frozen_scope`; zero counts always
  carry denominators and never become universal safety probabilities.

## D013 — Frozen 77.38% reclassification

- **Date:** 2026-07-23
- **Decision:** Classify E01 as `exploratory_confounded`.
- **Reason:** `predicted_p` executed algorithm-changing fallback on
  40,510/42,199 cycles (95.9975%), so the frozen comparison is neither pure
  ordinary Ruckig nor a same-follower P/PVA ablation.
- **Consequence:** The value is forbidden in the title, abstract,
  contributions, and conclusion. If used, it appears only in a Discussion
  evidence-correction subsection or appendix with the confound, withdrawn
  confirmation, immutable artifacts, and no-rerun statement.

## D014 — No v3 rerun and no v4

- **Date:** 2026-07-23
- **Decision:** Do not rerun v3 and do not execute v4 in this drafting cycle.
- **Reason:** The task is to create an evidence-bounded stage draft from
  existing audited results.
- **Consequence:** N03 remains unresolved. A future confirmation requires new
  identities/seeds and a precommitted same-follower direct P/PV/PVA matrix.

## D015 — Manuscript-source boundary

- **Date:** 2026-07-23
- **Decision:** Keep Markdown as logic/claim/notation/display planning only;
  `.tex` is the sole formal manuscript source from the first prose draft.
- **Reason:** Avoid a divergent Markdown manuscript and lossy
  Markdown-to-LaTeX conversion workflow.
- **Consequence:** No `manuscript.md`, section-by-section Markdown prose, or
  full-document conversion script is created.

## D016 — Runtime denominator separation

- **Date:** 2026-07-23
- **Decision:** Preserve the evidence auditor's source-specific frozen runtime
  benchmark population separately from the 42,199 command-safety cycles.
- **Reason:** Command-cycle and runtime-repetition populations answer different
  questions and must not be merged.
- **Consequence:** C10 records 42,199 direct command cycles for invariants and
  30,199 post-warm-up cycles for the selected frozen runtime aggregate
  (p99 874.91866 µs, max 2,113.25 µs, zero 10-ms deadline misses). Generated
  tables must select the registered runtime source consistently.

## D017 — Adversarial logic-review corrections

- **Date:** 2026-07-23
- **Decision:** Accept all P1 and P2 corrections in
  `ADVERSARIAL_LOGIC_REVIEW.md`: E01 is restricted to a Discussion
  evidence-correction passage or appendix; C06 names complete
  finite-difference-derived pipelines under the fixed projection policy; C10
  keeps the 42,199-cycle safety and 30,199-cycle runtime populations separate;
  C04 is an endpoint-identity observation only; C11 does not infer
  multi-segment prevalence from fallback counts; Phase A is “pre-specified in
  the repository,” not preregistered; C08 is a sanity control; and C13 is a
  reproducibility practice rather than an algorithmic contribution.
- **Reason:** The review found no P0 defect but identified wording and display
  designs that could overstate causal isolation, combine denominators, or turn
  provenance practice into a scientific-method claim.
- **Consequence:** Claims, scope, notation, outline, and display plans carry
  the same boundaries before prose drafting and logic lock.
