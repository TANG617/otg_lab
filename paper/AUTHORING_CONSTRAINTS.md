# Authoring Constraints

> Normative writing contract for the Markdown blueprint, future English TeX
> manuscript, figures, tables, abstract, captions, appendix, and arXiv package.
> Violations must be corrected before the Wave 2 source is considered complete.

**中文使用说明：** 本文件是第二波写作的强制合同。英文栏位给出最终论文应使用
的标准术语；中文说明用于审阅结论强度、统计单位、负结果披露、图表表达以及
arXiv 发布要求。若正文为了“更有冲击力”而突破这里的边界，应修改正文，而不是
弱化本合同。

Companion specifications:

- [Paper Framework](PAPER_FRAMEWORK.md)
- [Claim–Evidence Matrix](CLAIM_EVIDENCE_MATRIX.md)

## 0. Authority and conflict resolution

三份规格的职责不同：证据矩阵管事实，本文管措辞与方法边界，论文框架管叙事
顺序。任何冲突都应先在 Wave 1 文档中解决，不能留给 TeX 作者临场判断。

If the three Wave 1 documents disagree, resolve the conflict in this order:

1. `CLAIM_EVIDENCE_MATRIX.md` controls numerical values, evidence provenance,
   claim scope, and release status.
2. This document controls terminology, notation, evidence language, statistics,
   figures, citations, and arXiv constraints.
3. `PAPER_FRAMEWORK.md` controls narrative order, paragraph purpose, and
   figure/table placement.

Do not silently resolve a scientific conflict while writing TeX. Correct the
Wave 1 source-of-truth document first, then regenerate or revise downstream
content.

## 1. Language and voice

- Final manuscript language: English.
- Wave 1 specifications: English scientific content with Chinese or English
  explanatory constraints as needed.
- Voice: precise, mechanism-centered, and evidence-bounded.
- Prefer active scientific constructions: `We derive`, `E15 confirms`, `the
  recorded case shows`.
- Avoid marketing language: `breakthrough`, `dramatic`, `revolutionary`,
  `production-ready`, `unprecedented`, and `solves once and for all`.
- Use present tense for mathematics and document structure; past tense for
  completed experiments; present perfect only when it clarifies cumulative
  evidence.
- Never make implementation convenience the stated scientific novelty.

## 2. Controlled terminology

### Required terms

| Canonical term | Required meaning | Do not substitute with |
|---|---|---|
| `terminal-state mismatch` | Target position implies motion while target velocity/acceleration specify rest. | `bad target`, `solver glitch`, `tracking bug` |
| `target-state contract` | The complete terminal position/velocity/acceleration semantics supplied to the OTG. | `API mode` when discussing the scientific mechanism |
| `stop-and-go` | Repeated intra-period velocity pulses or near-zero intervals created by repeated rest-to-rest planning. | `chattering`, `stick-slip`, `oscillation` unless a separate definition is supplied |
| `P-only` | Position target with zero terminal velocity and acceleration in the studied configuration. | Bare `P` where it may be confused with pressure or probability |
| `matched velocity target` | Terminal velocity consistent with the intended reference motion. | `true velocity` unless ground truth is actually available |
| `Scheduled P` | The causal baseline using the already scheduled \(P[k+1]\) command. | `future position baseline`, `noncausal P` |
| `Future-O1` | The fixed-grid extrapolated first-order target-velocity construction defined in Lemma 1. | `future measurement derivative` |
| `exact profile` | The continuous/piecewise-polynomial trajectory returned or audited within a control interval. | `sampled output` if only endpoints are being discussed |
| `observed waveform lag` | Shift that minimizes position alignment error on the recorded waveform. | `latency`, `delay` without qualification |
| `integer lag` | Observed waveform lag restricted to the 10 ms sample grid. | `integer latency` |
| `sub-sample lag` | Quadratic interpolation around the best discrete alignment point. | `high-resolution latency` |
| `best tested` | Best eligible point among the explicitly evaluated grid under the stated rule. | `optimal`, `globally optimal` |
| `recorded case study` | Evidence from the single current recorded waveform. | `dataset validation`, `field validation`, `deployment proof` |
| `within the tested envelope` | Claim restricted to the stated parameter, trajectory, noise, and timing conditions. | `general`, `universal`, `robust in practice` |
| `native failure at the exact seam` | Ruckig 0.17.3 failure at \(q=1,\rho=1\) diagnostic coordinates. | `theorem failure`, `confirmed software bug` |

### Acronyms

- Define `online trajectory generation (OTG)` on first use.
- Define P, PV, and PVA together in the problem formulation; do not assume a
  reader knows the project shorthand.
- Define VAJ as the tuple of velocity, acceleration, and jerk limits only where
  the recorded engineering study begins.
- Use `RMSE` only after defining position RMSE and its evaluation window.
- Do not introduce project-only experiment acronyms in the abstract.

## 3. Fixed notation

| Symbol | Meaning | Units / constraints |
|---|---|---|
| \(p,v,a,j\) | position, velocity, acceleration, jerk state | rad, rad/s, rad/s², rad/s³ in recorded examples; generic units in theory |
| \(V,A,J\) | symmetric magnitude limits | positive scalars; \(|v|\le V\), \(|a|\le A\), \(|j|\le J\) |
| \(T\) | control period | seconds; use \(T\), not a mixture of \(T\), \(h\), and \(\Delta t\) in the theory |
| \(k\) | discrete control-cycle index | integer |
| \(\tau\) | continuous time within one control interval | \([0,T]\) |
| \(p_k\) | current position state at cycle \(k\) | state, not target |
| \(P[k]\) | scheduled reference/command position at index \(k\) | preserve uppercase P for the discrete command sequence |
| \(v_{\rm ref}\) | locally constant intended reference velocity | signed; use absolute value in \(\rho\) |
| \(d_{\rm crit}\) | maximum one-period rest-to-rest displacement | \(T v_{\rm crit}\) |
| \(v_{\rm crit}\) | critical mean speed corresponding to \(d_{\rm crit}\) | piecewise formula in Theorem 1 |
| \(q\) | acceleration/jerk regime coordinate | \(4A/(JT)\) |
| \(\rho\) | reference-to-critical speed ratio | \(|v_{\rm ref}|/v_{\rm crit}\) |
| \(\lambda\) | E16 target-velocity scale | \(v^\star=\lambda v_{\rm ref}\) for the relevant ablation |
| \(\widehat V[k+1]\) | estimated target velocity assigned to the next scheduled position | Future-O1 or named observer output |

Notation rules:

- Do not use \(dt\), \(h\), or \(\Delta t\) for the main control period after
  \(T\) is introduced. The implementation appendix may map `h=T` once.
- Use uppercase \(V,A,J\) for limits and lowercase \(v,a,j\) for states.
- Use \(P[k]\) for command samples and \(p(t)\) for the continuous state.
- Use \(\rho=1\), never an unexplained numerical threshold such as
  `speed/limit ratio = 1`.
- State whether a velocity quantity is signed or an absolute magnitude.
- Use `rad` units only for the recorded single-axis data; keep the theorem
  unit-agnostic.

## 4. Claim-strength grammar

本节控制“证据允许使用多强的动词”。理论、确定性网格、合成留出和单条 recorded
轨迹不是同一证据层级，最终英文正文必须让读者从句子本身看出这一差别。

Use verbs according to evidence type:

| Evidence type | Allowed verbs | Disallowed escalation |
|---|---|---|
| Analytic theorem | `proves`, `implies`, `establishes`, but only inside stated assumptions | `shows in all OTGs`, `guarantees hardware behavior` |
| Feasible continuation | `admits`, `is feasible`, `establishes existence` | `the solver must select`, `is uniquely optimal` |
| Deterministic grid | `confirms on the tested grid`, `matches`, `reproduces` | `statistically proves`, `universally validates` |
| Frozen synthetic holdout | `passes the declared holdout`, `generalizes within the tested envelope` | `generalizes to robots`, `real-world robust` |
| Recorded single case | `improves on this case`, `is the selected candidate for this waveform` | `improves deployment`, `general recorded-data superiority` |
| Negative result | `does not support`, `rejects the tested attribution`, `fails to improve this replay` | `proves impossible`, `never works` |

Every empirical claim sentence must make at least one of the following visible in
the sentence or its immediately preceding sentence:

- tested implementation/version;
- tested parameter envelope;
- held-out unit;
- recorded-case limitation;
- negative-result scope.

## 5. Theory/evidence separation

### Theorem rules

- Theorem 1 proves a maximum reachable displacement under stated assumptions.
- Corollary 1 maps a constant-speed position increment to the dimensionless
  boundary.
- Corollary 2 states nondecreasing dependence of \(v_{\rm crit}\) on available
  A/J/T within the piecewise model.
- Proposition 1 proves existence of a zero-jerk matched-PV continuation.
- Lemma 1 proves causality and affine exactness under the fixed-grid information
  contract.

### What theory does not establish

- the internal profile selected by every solver;
- floating-point behavior at \(q=1,\rho=1\);
- observer performance under noise or irregular timestamps;
- multi-axis synchronization;
- plant, actuator, friction, communication, or feedback behavior;
- the optimality of Future-O1 or any VAJ setting.

When a paragraph moves from theorem to solver behavior, use an explicit bridge,
for example:

> The analysis establishes reachability; we next test whether Ruckig 0.17.3
> realizes the predicted transition under the registered experiment contract.

Do not combine analytic and empirical statements into a single unqualified
`therefore` clause.

## 6. Experimental and statistical contract

### Units of evidence

- E15 required grid cells are deterministic configurations, not random
  replicates. Report coverage and classification, not p-values.
- E15 Sobol points are out-of-grid numerical holdouts for threshold recovery,
  not robot-task samples.
- E16 arms are deterministic mechanism interventions. Report exact-profile
  acceptance and bounded comparisons.
- E17 uses seed × configuration pairs for work-envelope comparisons. Do not use
  the 14,280 output rows as an independent sample size.
- E17 has 11 predeclared work-envelope conditions and 6 separately labeled
  out-of-envelope stress conditions. Never pool them into a 17-condition claim
  of confirmatory robustness.
- The 20 E17 trajectory families are synthetic trajectory cases.
- The recorded input is one trajectory and one axis. Its 7,673 time points are
  not 7,673 independent deployment tasks.

### Required metric distinctions

- Mechanism claims require at least one intra-period metric:
  - velocity ripple;
  - pulse fraction;
  - near-zero fraction;
  - stop-and-go event rate;
  - exact-profile match.
- Endpoint position RMSE alone cannot support a stop-and-go conclusion.
- Recorded selection uses raw-time position RMSE and absolute observed waveform
  lag as co-primary metrics.
- Integer and sub-sample lag must both be reported when a retained trace supports
  both; neither is wall-clock latency.
- Projection, constraint violation, fallback, solver failure, run completeness,
  and deadline are guardrails, not substitutes for tracking metrics.
- Deadline measurements from the offline host are not evidence of target-machine
  real-time schedulability.

### Comparisons

- Causal gains require a paired denominator with the same waveform,
  preprocessing, evaluation window, and relevant constraints.
- Do not compute a gain from the current-online original/no-velocity-limit
  waveform to the velocity-limited candidate waveform.
- Do not combine RMSE and lag into a weighted scalar score because their units
  differ.
- State when a selection is Pareto-based, budget-based, or based on the lowest
  eligible RMSE within the best lag tier.
- Distinguish `zero projection` from `no constraint pressure`; an unused limit
  does not demonstrate that every stopping constraint is irrelevant.

## 7. Required negative and diagnostic results

The following must appear in the body or an explicitly cited appendix:

1. E15: 16/16 native failures at the exact
   \(q=1,\rho=1\) regime/behavior seam, with zero required off-seam failures.
2. E16: raw Future-O1 fails the preregistered cross-branch P95 criterion before
   the \(10^{-10}\) rad/s deadband is applied.
3. E16: tested position lookahead and minimum duration do not reproduce exact PV
   in all primary cells, although \(2T\) minimum duration helps.
4. A05: matched PVA offers no additional primary stop-and-go benefit over PV in
   constant velocity because the matched acceleration is zero.
5. A04: PVA Future-O1 is worse than Scheduled P in RMSE on the recorded case.
6. A06: the selected acceleration lies on the tested grid boundary, and J=3200
   trades 0.186 ms sub-sample lag for lower RMSE relative to J=4000.
7. A03: relaxing runtime Vmax does not change the measured PVA/P relationship in
   the original-waveform attribution test.
8. E17: current local-poly processing does not improve the recorded
   irregular-timestamp replay; fixed-step Future-O1 rejects that contract.
9. E17: the out-of-envelope position-noise 0.25-step stress condition misses the
   predeclared reduction threshold (45.13% median, -10.63% worst cell, 119/120
   paired improvements).
10. A04: the single offline-host deadline miss over 7,672 cycles is a guardrail,
    not a real-time schedulability result; the recorded-target deadband audit
    changes neither stored targets nor metrics at \(10^{-10}\) rad/s.

These results cannot be hidden only in code, supplementary data, or review
responses.

## 8. Numerical-value policy

- During Wave 1, `CLAIM_EVIDENCE_MATRIX.md` is the sole editorial source of
  headline numbers.
- During Wave 2, frozen CSV/JSON artifacts become the computational source of
  truth; TeX values must be generated into one numbers file or table fragments.
- Do not copy the same literal number into multiple TeX section files.
- Preserve enough digits to reproduce reported ratios, but use reader-facing
  precision consistently:
  - RMSE: 7–10 significant decimal places in tables, 3–4 significant digits in
    prose unless the comparison depends on more;
  - percentage improvement: two decimals;
  - integer lag: integer milliseconds;
  - sub-sample lag: three decimal milliseconds;
  - boundary error: both decimal and percentage form once;
  - near-machine-zero RMSE excess: scientific notation.
- Never round 0.0001953125 to zero or 0.186 ms to “no change.”
- Use a leading zero for decimals and a minus sign only for signed differences;
  write `20.30% reduction` rather than `-20.30% improvement` in prose.
- Every generated table must include units in column headers.

## 9. Figure and table contract

### Figures

- Final scientific figures are vector PDF. PNG is allowed only when raster data
  genuinely requires it.
- Existing SVG files may guide content but must not be compiled through an SVG
  package or shell escape.
- Use one consistent, color-vision-safe palette across all figures:
  - P-only: neutral dark gray;
  - matched PV: blue;
  - PVA: orange;
  - theory boundary: black solid or dashed;
  - negative/failure observations: vermilion or patterned markers.
- Do not encode a scientific category by color alone; pair color with line style,
  marker, or direct label.
- Minimum final printed text size: 7 pt; preferred axis and legend text: 8–9 pt.
- Captions must state the comparison, metric, unit, sample/configuration count,
  and principal limitation. A caption must be interpretable without searching
  the body for the experiment ID.
- Show \(\rho=1\), acceptance lines, or zero-reference lines explicitly where
  they define the conclusion.
- Exact seam failures and worst E17 condition must be visually identifiable.
- Avoid dense full-duration recorded plots with overlapping estimators. Use a
  representative local window plus a separate error panel.

### Tables

- Put units in headers and explain eligibility/selection marks in notes.
- Bold only the selected or theoretically distinguished row, not every favorable
  number.
- Use `best tested`, `paired baseline`, and `negative result` labels directly in
  tables when applicable.
- Do not place the current-online original-waveform baseline in the same causal
  improvement block as the velocity-limited paired comparison. If retained for
  context, separate it visually and state that waveform and VAJ both differ.
- Large matrices belong in the appendix; the body uses hypothesis-level
  summaries.

## 10. Citation contract

Wave 1 defines citation slots only. Wave 2 must verify every bibliographic item
against a primary or official source before creating BibTeX.

| Slot | Required topic | Minimum evidence |
|---|---|---|
| RW-OTG | Online, time-optimal, and jerk-limited trajectory generation | Original Reflexxes and Ruckig papers plus any directly relevant third-order OTG work |
| RW-SCURVE | S-curve motion profiles and jerk-bounded reachability | Primary control/trajectory-planning sources, not only vendor tutorials |
| RW-STREAM | Streaming setpoints, receding target updates, and interpolation | Primary robotics/control systems literature |
| RW-PREVIEW | Preview control, command shaping, and reference governors | Foundational or directly relevant primary papers |
| RW-DIFF | Numerical differentiation, tracking differentiators, local polynomial methods, and irregular sampling | Original methods or authoritative technical sources |
| RW-MULTIAXIS | Multi-axis synchronization and constrained coordinated motion | Used mainly to delimit future hardware validation |
| SW-RUCKIG | Tested solver version and semantics | Official Ruckig paper/documentation and exact version record |

Citation rules:

- Do not fabricate titles, authors, venues, DOIs, arXiv IDs, or years.
- Prefer original methods over survey-only support; a survey may supplement but
  not replace a foundational citation.
- Cite Ruckig for implementation context, not as proof of the analytic theorem.
- Cite established bang-bang/S-curve results, then state precisely which
  dimensionless streaming interpretation is contributed here.
- Avoid unsupported novelty language such as `for the first time`. Use `to our
  knowledge` only after a documented search and retain the search record.
- Target 25–40 verified references for arXiv v1.

## 11. Section-specific writing rules

### Title and abstract

- Keep `Terminal-State Mismatch` and `Dimensionless Boundary` in the title.
- The title may mention jerk-limited OTG but not Ruckig, hardware, or multi-axis
  validation.
- Abstract contains no experiment IDs unless essential; use descriptions and
  counts instead.
- Abstract must include one explicit single-axis or tested-envelope qualifier.

### Introduction

- Page 1 must state the absence of plant and multi-axis hardware validation.
- Contributions must be the four items frozen in `PAPER_FRAMEWORK.md`.
- Do not include the final VAJ tuple in the contribution list.

### Theory

- Put all assumptions before Theorem 1.
- Demonstrate continuity of the two branches at \(A=JT/4\).
- Use `reachability boundary` for the theorem and `observed transition` for E15.
- Explain why a larger feasible motion envelope may make per-tick terminal rest
  easier, without recommending limit tightening as a general fix.

### Experiments and results

- Organize by hypotheses, not E01–E17 chronology.
- Version-pin Ruckig 0.17.3 and record the environment in the appendix.
- Report counts with their role: required, diagnostic, Sobol, arm, row, paired
  cell, condition, seed, or trajectory.
- Put the weakest E17 condition in the main prose.
- Give negative results equal editorial visibility to positive claims they
  delimit.

### Discussion and conclusion

- The discussion must distinguish interface semantics from solver tuning.
- The conclusion contains no new metric, parameter, citation, or claim.
- Future work may mention multi-axis hardware, plant dynamics, irregular timing,
  and independent recorded tasks, but cannot describe an unexecuted protocol as
  a result.

## 12. arXiv and LaTeX content constraints for Wave 2

- Upload-package `main.tex` must be at the package root.
- Use a conservative standard two-column `article` wrapper for arXiv v1, with
  section bodies isolated so a venue class can replace the wrapper later.
- Use only packages available in the selected arXiv TeX environment; do not rely
  on local TeX Live 2026-only behavior.
- Include all custom macros/styles, source `.bib`, and matching `main.bbl`.
- Do not use `\today`; use an explicit version date only if intentionally
  displayed.
- Do not use shell escape, `minted`, runtime SVG conversion, hidden cache
  directories, or external network resources.
- Upload only required `.tex`, `.bib`, `.bbl`, `.sty`, PDF/PNG figures, and
  generated table fragments. Exclude scripts, raw experiments, logs, auxiliary
  files, backups, and unused figures from the arXiv bundle.
- The release PDF must contain no `TODO`, `TBD`, placeholder author, blank figure,
  missing citation, or empty hardware section.
- Build with both `latexmk`/pdfLaTeX and bundled Tectonic, then clean-room compile
  the extracted arXiv archive.

## 13. Metadata policy

The following metadata is currently unknown and may remain listed in Wave 1
planning notes, but must be supplied before the final PDF and arXiv archive are
created:

- author order and spelling;
- affiliations and corresponding-author email;
- ORCID identifiers if used;
- acknowledgments and funding statements;
- conflict-of-interest statement if required by the target venue;
- code/data release URL, tag, license, and citation record;
- final arXiv primary/cross-list categories;
- preprint license selection.

Wave 2 may use visibly marked local draft macros for missing metadata during
development, but the release gate must fail until all such fields are resolved.
No placeholder may appear in the arXiv PDF.

## 14. v1/v2 hardware boundary

### arXiv v1

- Single-axis offline/open-loop OTG evidence only.
- Recorded evidence is one case study.
- No claim about joint synchronization, plant tracking, communication delay,
  computation latency, deadline feasibility on target hardware, or closed-loop
  stability.
- Hardware is mentioned only as a limitation and planned external-validity test.

### Later multi-axis hardware revision

Hardware evidence may add a new empirical section after the recorded case study
and before Discussion. It must measure, at minimum:

- per-axis RMSE and maximum error;
- integer/sub-sample alignment only if the signal supports them;
- cross-axis synchronization error;
- velocity, acceleration, and jerk constraint violations;
- target projection, solver failures, fallback, and run completeness;
- control-period jitter, deadline misses, and true wall-clock latency;
- paired P-only versus matched-PV trials under identical tasks and machine
  conditions;
- multiple velocities, directions, and loads plus at least one failure case.

Hardware results may add new claim IDs beginning at C14. They must not rewrite
C1–C13 retroactively or be used to erase v1 limitations.

## 15. Automated and manual review checklist

### Terminology checks

- Search for bare `latency` and verify that it means measured wall-clock latency;
  otherwise replace with `observed waveform lag`.
- Search for `optimal`, `universal`, `general`, `guarantee`, `always`, and
  `eliminate`; each occurrence requires an explicit analytic assumption or
  tested-envelope qualifier.
- Search for `Ruckig bug`, `chattering`, and `stick-slip`; none should remain
  unless directly quoted and scientifically distinguished.
- Search for all C1–C13 references and verify that each resolves to the matrix.

### Numerical checks

- Recompute or validate every headline number from frozen artifacts.
- Verify E15 required/diagnostic/Sobol counts independently.
- Verify E16 1,260 arm count and matched/wrong-control summary.
- Verify E17 row, pair, condition, seed, and trajectory counts are not conflated.
- Verify recorded gains use the paired velocity-limited baseline.
- Verify A06 retains `best tested` and the 0.186 ms trade-off.

### Scientific checks

- Every theorem assumption appears before the statement.
- Every solver behavior is attributed to a versioned empirical test.
- Every positive generalization has a matching scope statement.
- C11 and C13 remain visible.
- No hardware, multi-axis, or production claim appears in v1.

### Presentation checks

- All figures are legible at final column width and in grayscale.
- Captions include metric, unit, count, and limitation.
- Tables use consistent precision and units.
- No figure/table duplicates a result without adding a distinct explanatory
  function.
- Abstract, Introduction contributions, Discussion, and Conclusion use the same
  four-contribution framing.

## 16. Wave 1 acceptance gate

Wave 1 is complete when:

- the title, 11-section order, theorem/proposition/lemma numbering, C1–C13 claim
  IDs, six figures, and four tables match across all three specifications;
- every numerical statement in the paper framework is present with provenance in
  the claim matrix;
- all prohibited extrapolations are explicitly blocked here;
- dirty-run provenance and clean-rerun requirements are visible;
- no `.tex`, figure, table fragment, experiment output, or source-code change has
  been created as part of Wave 1.
