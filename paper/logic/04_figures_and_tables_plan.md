# Figures and tables plan

Every display must be generated or copied from a source-backed, checksummed
artifact. Main-text captions must state the information condition and scope
well enough to stand alone. Figures must be grayscale-readable,
color-blind-friendly, use explicit units, and be exported as PDF vector
graphics when practical. SVG may be a generation intermediate but is not an
arXiv submission format.

The frozen v3 mixed-baseline performance comparison is not a main performance
figure.

## Figures

### Figure 1 — Layered, time-explicit architecture

- **Scientific question:** Which state and responsibility belongs to the
  estimator, future-reference generator, executable-target governor, follower,
  and plant?
- **Claims:** C01, C02, C12.
- **Source evidence:** Current typed interfaces and data dictionary;
  `E_CURRENT_PROFILE_AWARE_INFRASTRUCTURE`.
- **Information condition:** Conceptual causal online path; optional
  oracle/preview branch must be visually separate.
- **Evidence time class:** Current, post-freeze infrastructure.
- **Placement:** Main, Introduction/Method.
- **Planned visual:** Left-to-right blocks with separate arrows for
  \(p^{\mathrm{ref}}\) and \(x^{\mathrm{cur/meas}}\); each edge labelled with
  represented time and availability; native/shield/fallback split at follower.
- **Caption takeaway:** Converting a position stream into an executable command
  is a layered state/time transformation, not a single differentiation step.
- **Forbidden interpretation:** The diagram is a validated robot control stack,
  a performance comparison, or proof of layer independence.

### Figure 2 — Target-to-output timeline

- **Scientific question:** Which time is represented by the posterior,
  prediction, requested/executable target, command, and later measurement?
- **Claims:** C01, C05.
- **Source evidence:** Phase A `target[k] -> output[k+1]` contract,
  `E_PHASE_A_TRACKING`; current clock schema.
- **Information condition:** Causal online path plus explicitly dashed
  noncausal/oracle preview.
- **Evidence time class:** Current.
- **Placement:** Main, Problem Formulation.
- **Planned visual:** Two horizontal axes: represented/source time and
  availability/control time. Show \(p_k\) arrival, delayed posterior,
  \(t_{k+H}\) prediction, \(x_{k+1}^{target}\), command interval, and
  \(x_{k+1}^{meas}\).
- **Caption takeaway:** `target[k]` is consumed at tick \(k\), whereas the
  returned/executed endpoint belongs to \(k+1\); estimator delay and prediction
  horizon are separate.
- **Forbidden interpretation:** Every real system has exactly one-cycle lag;
  a target is necessarily reachable in one cycle; command equals measurement.

### Figure 3 — Phase A analytic P/PV/PVA ablation

- **Scientific question:** What is the isolated position-following value of
  reliable target velocity and acceleration on the three smooth analytic
  references?
- **Claims:** C03, C04.
- **Source evidence:** `E_PHASE_A_TRACKING`,
  `target_state_ablation_metrics.csv`.
- **Information condition:** Analytic truth oracle components; no position-only
  estimator claim.
- **Evidence time class:** Current.
- **Placement:** Main, Results.
- **Planned visual:** Per-reference grouped RMSE and best-lag panels for P,
  PV-truth, PVA-truth; absolute values and units, same scale where legible.
- **Caption takeaway:** Reliable analytic velocity reduces RMSE and lag versus
  P, while analytic acceleration adds no observed position benefit beyond PV
  under these tested conditions.
- **Forbidden interpretation:** PVA is superior; finite-difference velocity
  will help the CSV; acceleration is universally useless; differences are
  hardware measurements.

### Figure 4 — Derivative timing and causality

- **Scientific question:** What state time does each backward/centered formula
  estimate, and when can it be available?
- **Claims:** C05.
- **Source evidence:** `E_PHASE_A_DERIVATIVES`; formulas and metadata from
  `derivative_source_metrics.csv`.
- **Information condition:** Backward and delayed-centered are causal; standard
  centered-at-\(k\) uses one future sample and is noncausal.
- **Evidence time class:** Current.
- **Placement:** Main, Problem Formulation/Results.
- **Planned visual:** Three sample-stencil timelines. Distinguish stencil
  centre, output label, availability, one-sample group delay, and optional
  propagation.
- **Caption takeaway:** Centering accuracy does not remove availability delay;
  a zero-delay centered label at \(k\) requires \(p_{k+1}\).
- **Forbidden interpretation:** Offline centered differences are deployable at
  zero delay; the 10-ms group delay equals end-to-end reference-following lag.

### Figure 5 — One-step bounded-jerk governor

- **Scientific question:** How is a raw future target converted into an
  adjacent command that satisfies the stated command-model invariants?
- **Claims:** C09.
- **Source evidence:** Current governor/constraint implementation and tests;
  `E_CURRENT_PROFILE_AWARE_INFRASTRUCTURE`.
- **Information condition:** Current/raw target available at tick \(k\);
  triple-integrator constant-jerk command model.
- **Evidence time class:** Current, post-freeze construction; no fresh locked
  performance evaluation.
- **Placement:** Main, Method.
- **Planned visual:** Raw-target objective → feasible jerk intervals →
  selected \(j_k\) → exact integration → continuous V/A/J audit → stopping
  viability/next-step existence. Show fallback/emergency branches explicitly.
- **Caption takeaway:** The governor selects an allowed jerk and constructs the
  endpoint through exact dynamics instead of clipping P/V/A independently.
- **Forbidden interpretation:** Global optimality, arbitrary-disturbance
  recursive feasibility, certified safety, or superior tracking performance.

### Figure 6 — Frozen v3 direct-command constraints and runtime

- **Scientific question:** Did the frozen direct condition record continuous
  V/A/internal-J violations, fallback/projection, or deadline misses within its
  synthetic protocol?
- **Claims:** C10.
- **Source evidence:** `E_V3_DIRECT_SAFETY`, `E_V3_RUNTIME`; frozen constraint,
  governor-invariant, fallback, and runtime summaries.
- **Information condition:** Frozen synthetic v3 direct constant-jerk method.
- **Evidence time class:** Frozen.
- **Placement:** Main, Results.
- **Planned visual:** Compact two-panel audit: continuous maxima/margins against
  limits and runtime distribution/threshold. Denominators printed directly.
- **Caption takeaway:** Across 42,199 frozen locked-test command cycles the
  direct condition recorded zero continuous constraint violation, projection,
  or fallback; the separately defined runtime population recorded no 10-ms
  deadline miss.
- **Forbidden interpretation:** Production/hardware safety, zero future failure
  probability, real-time on all machines, or performance superiority over
  ordinary Ruckig.

### Figure 7 — Development CSV pipeline negative result and target diagnostics

- **Scientific question:** Do the tested finite-difference-derived pipeline
  conditions improve the P baseline on the single fixed-grid development
  trace under the fixed admissibility-projection policy, and what target
  conflict appears?
- **Claims:** C06, C07, N01.
- **Source evidence:** `E_REAL_CSV_NEGATIVE`; Phase A CSV metric rows.
- **Information condition:** One position-only development CSV; fixed 10-ms row
  grid; no derivative truth. Offline-centered rows are visibly labelled
  noncausal.
- **Evidence time class:** Current.
- **Placement:** Main, Results.
- **Planned visual:** RMSE/lag by method plus raw differentiated-acceleration
  distribution or rate beyond \(a_{\max}\); P baseline visually neutral.
- **Caption takeaway:** None of the tested finite-difference-derived pipeline
  conditions improves P under the fixed projection policy. P and PV require
  no projection, whereas every PVA variant projects 32.64% of requested
  targets because the acceleration diagnostics frequently exceed the
  configured bound; this design cannot isolate estimator error from the
  projection intervention.
- **Forbidden interpretation:** Derivatives never help; robot acceleration
  violated a limit; this trace establishes real-data generalization; offline
  centered is causal.

### Figure 8 — Evidence reclassification (optional)

- **Scientific question:** How did the exposed fallback identity change the
  permitted interpretation of the frozen comparison?
- **Claims:** C11--C13, E01, N03.
- **Source evidence:** `E_V3_CONFOUNDED_COMPARISON`,
  `E_V3_ARTIFACT_INTEGRITY`, post-review status.
- **Information condition:** Historical mixed baseline; not a pure ordinary
  Ruckig or same-follower comparison.
- **Evidence time class:** Frozen observation plus post-review
  reclassification; bytes unchanged.
- **Placement:** Appendix only.
- **Planned visual:** Evidence-state flow from frozen observation → exposed
  actual algorithm/fallback → withdrawn confirmation → permitted exploratory
  use.
- **Caption takeaway:** Artifact immutability preserves the numeric observation
  while baseline identity restricts its inference.
- **Forbidden interpretation:** The 77.38% is confirmatory, v3 was rerun, the
  corrected implementation confirms a replacement result, or all v3 evidence
  is invalid.

## Tables

### Table 1 — Notation and time semantics

- **Scientific question:** What does each state represent and when is it
  available?
- **Claims:** C01, C02, C05.
- **Source evidence:** `03_notation_and_timing.md`, current schema/data
  dictionary.
- **Information condition:** Mixed conceptual table; oracle rows explicitly
  marked.
- **Evidence time class:** Current.
- **Placement:** Main, Problem Formulation.
- **Columns:** Symbol; role; represented time; available time; causal
  information; may contain truth?
- **Caption takeaway:** State time and availability time are independent
  metadata.
- **Forbidden interpretation:** All rows exist in every dataset; CSV has
  derivative truth.

### Table 2 — Method taxonomy and information condition

- **Scientific question:** Which combinations differ by target components,
  future information, governor, follower, profile, shield, and fallback?
- **Claims:** C02, C05, C11, C12.
- **Source evidence:** Current method/schema definitions; Phase A protocol;
  frozen method metadata where used.
- **Information condition:** One row per declared/executed identity, not
  collapsed labels.
- **Evidence time class:** Current/frozen rows separated.
- **Placement:** Main, Related Work/Protocol.
- **Columns:** Method label; P/PV/PVA; derivative/predictor source; causal?;
  governor; native follower; profile kind; shield allowed?; fallback changes
  algorithm?; evidence class.
- **Caption takeaway:** Target components and executed follower identity are
  separate experimental factors.
- **Forbidden interpretation:** Frozen ordinary-Ruckig-named rows are pure
  ordinary Ruckig; shielded and unshielded are interchangeable.

### Table 3a — Analytic-reference derivative accuracy

- **Scientific question:** What derivative errors and timing labels apply to
  the analytic-reference estimators?
- **Claims:** C05.
- **Source evidence:** `E_PHASE_A_DERIVATIVES`.
- **Information condition:** Each row labels analytic truth, causal estimate,
  noncausal centered estimate, or next-cycle oracle.
- **Evidence time class:** Current.
- **Placement:** Main, Results; detailed derivative rows may move to appendix.
- **Columns:** Reference; estimator; causal?; future samples; represented-time
  delay [ms]; velocity RMSE [rad/s]; acceleration RMSE [rad/s²].
- **Caption takeaway:** Derivative accuracy and represented-time delay are
  estimator quantities, not position-following outcomes.
- **Forbidden interpretation:** Derivative RMSE has position units; the
  estimator delay equals end-to-end following lag.

### Table 3b — Analytic-reference position following

- **Scientific question:** What position-following outcomes occur for P,
  analytic truth components, timing variants, and the next-cycle oracle?
- **Claims:** C03, C04, C08.
- **Source evidence:** `E_PHASE_A_TRACKING`, `E_PHASE_A_ORACLE`.
- **Information condition:** Each row labels analytic truth, causal estimate,
  noncausal centered estimate, or next-cycle oracle.
- **Evidence time class:** Current.
- **Placement:** Main, Results.
- **Columns:** Reference; target condition; causal?; future samples; position
  RMSE [rad]; maximum position error [rad]; best grid lag [ms].
- **Caption takeaway:** Reliable target-state information and correct future
  timing answer different questions.
- **Forbidden interpretation:** Oracle conditions are deployable; PVA beats
  PV; best grid lag is estimator delay.

### Table 4 — Development CSV pipeline results

- **Scientific question:** What happened to the P/PV/PVA
  finite-difference-derived pipeline conditions under the fixed
  admissibility-projection policy on the only development trace?
- **Claims:** C06, C07, N01.
- **Source evidence:** `E_REAL_CSV_NEGATIVE`.
- **Information condition:** Position-only, fixed-grid, no derivative truth;
  offline-centered labelled noncausal.
- **Evidence time class:** Current.
- **Placement:** Main, Results.
- **Columns:** Target components; derivative source; causal?; RMSE [rad]; lag
  [ms]; max error [rad]; raw target projection rate; raw max acceleration
  [rad/s²].
- **Caption takeaway:** All tested finite-difference-derived pipeline
  conditions have higher RMSE than P. P/PV projection is zero while all PVA
  variants project 32.64% of targets, so estimator and projection effects are
  not causally separable in this design.
- **Forbidden interpretation:** Real robot measurement, independent test,
  generalization, or proof that derivatives are useless.

### Table 5 — Frozen v3 direct-command safety and runtime

- **Scientific question:** What exact denominator and zero/nonzero counts
  support C10?
- **Claims:** C10.
- **Source evidence:** `E_V3_DIRECT_SAFETY`, `E_V3_RUNTIME`,
  `E_V3_ARTIFACT_INTEGRITY`.
- **Information condition:** Synthetic frozen direct constant-jerk method.
- **Evidence time class:** Frozen.
- **Placement:** Main, Results.
- **Columns:** Population/scope; denominator; audit tolerance; max/min margin;
  violation count; projection/fallback count; runtime p99/max; deadline
  misses. Safety/invariant and runtime rows remain separate populations.
- **Caption takeaway:** The direct condition met the recorded invariant and
  timing checks over their explicit frozen populations.
- **Forbidden interpretation:** Universal safety probability, hardware
  deadline proof, or same-follower performance benefit.

### Table 6 — Claim/evidence boundaries and limitations

- **Scientific question:** Which conclusions are confirmed, negative,
  exploratory/confounded, or unevaluated?
- **Claims:** C03, C04, C06--C13, N01--N03, E01.
- **Source evidence:** Claim registry and all audited source classifications.
- **Information condition:** Summary only; no new aggregation.
- **Evidence time class:** Current/frozen/post-freeze visibly separated.
- **Placement:** Main Discussion or appendix if space is limited.
- **Columns:** Claim ID; status; population/information condition; permitted
  conclusion; forbidden extension; next evidence required.
- **Caption takeaway:** Strong formulation and scoped execution evidence
  coexist with unresolved comparative and real/hardware performance claims.
- **Forbidden interpretation:** All listed claims have equal evidential
  strength; E01 is a primary result; limitations are planned experiments.

## Display-generation constraints

- Numbers in Tables 3--5 and Figures 3, 6, and 7 must come from generated data
  products, never hand-entered plotting constants.
- Frozen v3 figures must verify source hashes. A new rendering from bounded
  frozen data is labelled “post-hoc visualization of frozen data.”
- Figure 6 may not combine the direct safety evidence with a confounded
  ordinary-Ruckig performance curve.
- Figure 7 uses “raw target acceleration estimate/diagnostic,” never
  “acceleration truth” or “robot acceleration.”
- Every main caption contains dataset class, information condition,
  current/frozen/post-freeze status, and the decisive boundary.
