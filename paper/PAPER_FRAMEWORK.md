# Paper Framework and Paragraph-Level Blueprint

> Wave 1 authoring specification. This document defines the paper's argument,
> section order, paragraph responsibilities, and figure/table placements. It is
> not manuscript prose and must not be submitted to arXiv.

**中文使用说明：** 本文件是第二波英文 TeX 的逐段施工图，不是论文初稿。
英文标题、术语、公式和候选主张应尽量直接复用；每个表格中的 purpose 规定该段
必须完成的叙事任务，claims/assets 规定可使用的证据，最后一列是不得突破的表述
边界。第二波不得跳过这些约束自行扩充结论。

Companion specifications:

- [Claim–Evidence Matrix](CLAIM_EVIDENCE_MATRIX.md)
- [Authoring Constraints](AUTHORING_CONSTRAINTS.md)

## 0. Frozen paper identity

本节冻结论文身份与版本边界。除非先同步修改三份 Wave 1 规格，否则第二波不得
更换主标题、把 Ruckig 写进标题，或把尚未完成的真机实验包装成 v1 贡献。

### Working title

**Terminal-State Mismatch Causes Stop-and-Go in Jerk-Limited Online Trajectory
Generation: A Dimensionless Boundary and a Causal Velocity-Target Remedy**

### One-sentence thesis

When a sampled position stream is passed to a jerk-limited online trajectory
generator with zero terminal velocity and acceleration at every update, the
target-state contract turns continuous motion into repeated rest-to-rest
problems; the resulting stop-and-go regime has an analytic dimensionless
boundary, and a causally constructed matched velocity target restores the
constant-velocity continuation within the tested single-axis setting.

### Version contract

- **arXiv v1:** theory, single-axis software confirmation, causal ablation,
  declared synthetic robustness envelope, and one recorded-trajectory case
  study.
- **Later revision:** multi-axis hardware validation may be added as a new
  empirical section without changing the theory, claim IDs C1–C13, or the
  interpretation of the v1 results.
- arXiv v1 contains no empty hardware section, promised result, or `TODO`.

### Audience and paper type

- Primary audience: robotics and control researchers using online trajectory
  generation, streaming setpoints, jerk-limited interpolation, or command
  shaping.
- Secondary audience: real-time motion-control engineers who need to distinguish
  command semantics from solver tuning.
- Paper type: mechanism paper with an analytic boundary and systematic empirical
  confirmation, not a library tutorial and not a parameter-tuning report.
- Tentative arXiv placement: `cs.RO`, with `eess.SY` considered only after the
  final framing and bibliography are reviewed.

## 1. Scientific positioning

本节规定论文“是什么”和“不是什么”：主线是目标终端状态语义、无量纲边界与
因果修复，不是库接口教程，也不是 VAJ 调参报告。

### Problem statement

A position target can be continuous at the sampling instants while specifying a
discontinuous state contract. In the studied pipeline, each scheduled target
position is paired with terminal velocity and acceleration equal to zero. At low
reference speeds, a jerk-limited solver can complete this rest-to-rest move
within one control period and wait at the target, producing repeated
intra-period velocity pulses even when endpoint position error is small.

### Novelty statement

The paper's novelty is not the observation that a velocity target can be passed
to an OTG API. The contribution is the combination of:

1. an analytic, two-regime rest-to-rest reachability boundary;
2. a dimensionless collapse using \(q\) and \(\rho\), including the
   counter-intuitive monotonic effect of looser limits and longer periods;
3. a matched-velocity invariance argument that separates target-state semantics
   from position preview and duration heuristics;
4. confirmatory boundary, causal-ablation, frozen-holdout, and recorded-case
   evidence with explicit negative results.

### What the paper does not claim

- It does not prove that every OTG implementation selects the same internal
  trajectory.
- It does not prove closed-loop robot performance or multi-axis synchronization.
- It does not claim that Future-O1, local polynomial estimation, PV, PVA, or a
  particular VAJ tuple is universally optimal.
- It does not treat observed waveform lag as computation or communication
  latency.
- It does not claim that the recorded case is an independent population-level
  validation set.

## 2. Abstract blueprint

The final abstract must contain 180–220 English words and follow this six-part
order. This section specifies content only; it is not the final abstract.

| Block | Target length | Required content | Prohibited content |
|---|---:|---|---|
| A1: Context | 25–35 words | Streaming position commands and jerk-limited OTG; endpoint accuracy can hide intra-period stopping. | Product framing or library-specific blame. |
| A2: Mechanism | 30–40 words | Zero terminal derivatives create repeated rest-to-rest target-state contracts. | Calling the phenomenon generic feedback chattering. |
| A3: Theory | 35–45 words | State the two branches of \(v_{\rm crit}\), define \(q\) and \(\rho\), and identify \(\rho=1\). | Omitting the inactive velocity-limit and single-axis assumptions. |
| A4: Remedy | 25–35 words | Matched terminal velocity admits a zero-jerk constant-velocity continuation; mention causal fixed-grid construction. | “PV always eliminates artifacts.” |
| A5: Evidence | 45–55 words | E15 required/Sobol counts, E16 arms, E17 holdout envelope, and the recorded fixed-grid case. | Listing every early experiment or comparing different recorded waveforms as a causal gain. |
| A6: Scope | 20–30 words | State single-axis offline scope and the irregular-timestamp negative result or equivalent limitation. | Promising unreported hardware performance. |

The abstract may use at most four headline numbers. Preferred set:

- `2144/2144` required boundary cases and `128/128` Sobol holdout cases;
- `1260/1260` causal-ablation arms;
- `79.03%` median ripple reduction in the weakest E17 condition;
- `20.30%` recorded fixed-grid RMSE improvement, paired within the same waveform.

## 3. Contributions as they must appear in the Introduction

The contribution list must contain exactly four items:

1. **Analytic boundary.** Derive the maximum one-period rest-to-rest displacement
   for a jerk- and acceleration-limited third-order integrator, then express the
   stop-and-go boundary using \(q\) and \(\rho\).
2. **State-contract remedy.** Show that a matched terminal velocity admits the
   constant-velocity trajectory as a zero-jerk invariant continuation, and give
   a causal fixed-grid velocity construction under an explicit information
   contract.
3. **Mechanism-centered validation.** Confirm the boundary and distinguish the
   matched velocity mechanism from wrong velocity, position lookahead, and
   minimum-duration alternatives using E15 and E16.
4. **Bounded empirical generalization.** Evaluate frozen synthetic holdouts and a
   recorded fixed-grid case, including the failure of the current
   irregular-timestamp observer to improve the recorded replay.

Do not list VAJ tuning as a scientific contribution. It belongs to the recorded
engineering case study.

## 4. Formal analysis contract

### 4.1 System model and assumptions

Use the single-axis third-order integrator

\[
\dot p=v,\qquad \dot v=a,\qquad \dot a=j,
\]

with symmetric bounds

\[
|v|\le V,\qquad |a|\le A,\qquad |j|\le J.
\]

The main theorem additionally assumes:

- \(T>0\), \(A>0\), and \(J>0\);
- initial and terminal velocity and acceleration are zero;
- position constraints do not activate;
- the velocity bound does not activate; the explicit sufficient condition used
  in the theorem is \(V\ge 2v_{\rm crit}\), because the maximizing symmetric
  profile reaches peak velocity \(2v_{\rm crit}\);
- motion direction is handled by symmetry, so the derivation may use positive
  displacement and restore the sign afterward.

The theorem concerns reachability. Ruckig's selection and numerical behavior are
reported separately as empirical results.

### 4.2 Theorem 1 — one-period rest-to-rest boundary

For a move completed in period \(T\), define

\[
d_{\rm crit}=T v_{\rm crit},
\]

where

\[
v_{\rm crit}=
\begin{cases}
JT^2/32, & A\ge JT/4,\\[4pt]
AT/4-A^2/(2J), & A<JT/4.
\end{cases}
\]

The proof must show:

- an upper-bound/optimality argument (via the displacement integration kernel
  and the extreme admissible jerk/acceleration structure, or an equivalent
  maximum-principle argument) establishing that no admissible rest-to-rest
  profile exceeds the stated displacement; a constructive S-curve alone is not
  sufficient;
- in the jerk-limited branch, four equal jerk segments produce peak
  acceleration \(JT/4\), peak velocity \(JT^2/16\), and displacement
  \(JT^3/32\);
- in the acceleration-limited branch, jerk ramps last \(A/J\), the acceleration
  plateau fills the remaining half-period, peak velocity is
  \(AT/2-A^2/J\), and displacement is
  \(AT^2/4-A^2T/(2J)\);
- symmetry converts the half-profile velocity area into total displacement;
- all smaller absolute displacements are reachable by an admissible profile.

The body gives the profile structure and result. The full segment-by-segment
integration belongs in Appendix A.

### 4.3 Corollary 1 — dimensionless stop-and-go boundary

Define

\[
q=\frac{4A}{JT},\qquad
\rho=\frac{|v_{\rm ref}|}{v_{\rm crit}}.
\]

For constant-speed targets separated by
\(|\Delta p|=|v_{\rm ref}|T\):

- \(\rho\le1\) means the P-only zero-terminal-derivative target is reachable
  rest-to-rest within one period;
- \(\rho>1\) means that one-period terminal rest is not reachable under the
  theorem assumptions.

The manuscript must say that E15 confirms how Ruckig 0.17.3 realizes this
boundary. The theorem alone must not be used to assert a universal solver output
policy.

### 4.4 Corollary 2 — monotonic design implication

Within the theorem assumptions, \(v_{\rm crit}\) is nondecreasing with available
acceleration, jerk, and period. Therefore, for a fixed low reference speed,
looser dynamic limits or a longer control period may lower \(\rho\) and enlarge
the rest-to-rest stop-and-go region. Present this as a counter-intuitive command
semantics result, not as a recommendation to tighten limits blindly.

### 4.5 Proposition 1 — matched-PV invariant continuation

If \(|v_{\rm ref}|\le V\), the current state is
\((p_k,v_{\rm ref},0)\), and the next target is
\((p_k+v_{\rm ref}T,v_{\rm ref},0)\), then

\[
p(\tau)=p_k+v_{\rm ref}\tau,\quad
v(\tau)=v_{\rm ref},\quad
a(\tau)=j(\tau)=0
\]

is an admissible continuation. This establishes existence of the invariant
trajectory; E16 separately establishes that the tested solver reproduces its
exact profile in all primary cells.

### 4.6 Lemma 1 — Future-O1 causality on the fixed grid

Under the information contract that scheduled \(P[k+1]\) is available at cycle
\(k\), while \(P[k],P[k-1],P[k-2]\) are already known, define

\[
\widehat V[k+1]
=\frac{2P[k]-3P[k-1]+P[k-2]}{T}.
\]

The construction reads no future measurement and is exact for an affine
position sequence. State explicitly that this causality result does not imply
noise robustness, irregular-timestamp validity, or universal optimality.

The paper must include the following logical timeline (typeset graphically or
as an algorithm): at the start of cycle \(k\), the scheduler has already issued
\(P[k+1]\); the observer reads only scheduled targets through index \(k+1\) and
history through \(k-2\); it then forms the terminal velocity paired with that
scheduled target; the OTG produces the command for cycle \(k\rightarrow k+1\).
Thus \(P[k+1]\) is previewed command data, not a measurement arriving from the
future.

## 5. Narrative and evidence hierarchy

Use evidence in this order:

1. analytic model and proof;
2. E15 confirmatory boundary validation;
3. E16 causal mechanism ablation;
4. E17 frozen holdout within a declared synthetic work envelope;
5. A04/A06 recorded fixed-grid case study;
6. A03/A05 and earlier experiments as supporting diagnostics or negative
   controls.

Do not narrate E01–E17 chronologically. Experiment IDs may appear in the
protocol, appendix, provenance table, and artifact documentation; the main prose
must be organized by hypotheses.

## 6. Paragraph-level manuscript blueprint

以下表格按最终英文正文的段落顺序给出施工约束。段落 ID 只用于写作和审计，不
进入最终论文；第二波若合并相邻段落，仍必须保留两者的结论、证据和范围限定。

### 1. Introduction

Target: 850–1,050 words, five paragraphs plus the contribution list.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| I1 | Open with a continuous low-speed position stream whose sampled endpoints are tracked but whose intra-period motion repeatedly stops. Explain why endpoint RMSE can miss the artifact. | C1; Fig. 1 | Call it `stop-and-go`, not feedback chattering or stick-slip. Lead to target-state semantics. |
| I2 | Explain P-only terminal-state mismatch: every command includes zero target velocity/acceleration although the reference is moving. | C1, C3 | Do not blame Ruckig; present a modeling/interface mismatch. Lead to the unresolved boundary question. |
| I3 | State the research questions: when is a rest-to-rest step reachable in one tick, which dimensionless variables govern it, and whether velocity matching—not preview alone—removes it. | C2–C5 | Questions precede results; do not reveal tuning details. |
| I4 | Summarize evidence with the minimum headline numbers: E15, E16, E17, and the paired recorded case. Include the irregular-timestamp negative result in one clause. | C2–C7, C9, C13 | Use `within the tested envelope` and `recorded case study`. |
| I5 | State scope: single axis, offline solver execution, no plant dynamics, no multi-axis synchronization, no wall-clock latency claim. | Scope contract | This limitation must appear on page 1. |
| I6 | Present exactly the four contributions in Section 3 above. | C1–C9 | End by previewing the paper organization. |

### 2. Related Work

Target: 650–850 words, four thematic paragraphs. References are placeholders by
topic in Wave 1 and must be replaced by verified primary sources in Wave 2.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| R1 | Review time-optimal and jerk-limited OTG, boundary-state specification, and representative systems such as Reflexxes and Ruckig. | Citation topic RW-OTG | Do not imply prior solvers omit terminal derivatives; identify the unexplored streaming contract. |
| R2 | Review S-curve motion and third-order reachability. Position Theorem 1 as a streaming-target boundary specialization and dimensionless interpretation. | Theorem 1; RW-SCURVE | Do not claim the bang-bang profile itself is new. |
| R3 | Review preview, command shaping, reference governors, and receding-horizon command generation. | C4; RW-PREVIEW | Explain why position preview and terminal velocity semantics are distinct interventions. |
| R4 | Review finite-difference and model-based derivative estimation under noise and irregular sampling. | C5–C7, C13; RW-DIFF | End with the need to state the information contract and empirical work envelope. |

### 3. Problem Formulation and Information Contract

Target: 800–1,000 words, six paragraphs.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| P1 | Define the third-order system, \(V/A/J\) constraints, control period \(T\), and one-axis notation. | Formal contract | Keep velocity-bound inactivity separate from the general model. |
| P2 | Define the receding online pipeline: the follower starts from the previous output state and receives a scheduled target for the current interval. | C1 | A timeline inset may be included in Fig. 1. |
| P3 | Define target contracts P, PV, and PVA. For P-only, explicitly write \((p^\star,0,0)\); for matched PV, write \((p^\star,v^\star,0)\). | C1, C3, C8 | Do not identify PVA with truth unless acceleration truth exists. |
| P4 | Define the information set and distinguish scheduled \(P[k+1]\) from a future measurement. State the Future-O1 startup history requirement. | C5; Lemma 1 | Scheduled P is causal under this system contract. |
| P5 | Define exact-profile stop-and-go metrics: pulse fraction, near-zero fraction, velocity ripple, event rate, and evaluation window. | C1–C7; Table 1 | Endpoint RMSE is secondary for mechanism validation. |
| P6 | Define recorded metrics: raw-time position RMSE, integer lag, sub-sample lag, projection, guardrails, and deadline. | C9–C13 | Both lag measures are waveform alignment diagnostics, not wall-clock latency. Transition to theory. |

### 4. Dimensionless Analysis and Matched-Velocity Invariance

Target: 1,250–1,550 words, eight paragraphs.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| A1 | State the fixed-period rest-to-rest reachability problem and all theorem assumptions. | C1, C2 | Say `reachable`, not `selected by every solver`. |
| A2 | Derive the jerk-limited four-segment profile and \(JT^3/32\). | Theorem 1; Fig. 2 | Show the continuity point \(A=JT/4\). |
| A3 | Derive the acceleration-limited ramps, plateau, and \(AT^2/4-A^2T/(2J)\). | Theorem 1; Fig. 2 | Defer full integration to Appendix A. |
| A4 | State Theorem 1 and explain \(d_{\rm crit}=Tv_{\rm crit}\). | C2 | No empirical Ruckig language inside the theorem. |
| A5 | Introduce \(q\) and \(\rho\); state Corollary 1 and the \(\rho=1\) boundary. | C2; Fig. 2 | Mention equality is analytically valid but numerically delicate. |
| A6 | State Corollary 2 and discuss why larger A/J or longer T can enlarge the stop-and-go region. | C2 | Do not turn the monotonicity observation into a controller-tuning prescription. |
| A7 | State Proposition 1 and contrast the zero-jerk matched-PV continuation with P-only terminal rest. | C3; Fig. 1 | Existence is analytic; solver reproduction is empirical. |
| A8 | State Lemma 1, prove affine-sequence exactness, and delimit fixed-grid causality. | C5 | Transition from analytic predictions to falsifiable experiments. |

### 5. Experimental Protocol

Target: 800–1,000 words, six paragraphs plus Table 1.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| E1 | Identify Ruckig 0.17.3 as the tested implementation and describe exact piecewise-jerk profile auditing. | C1–C4 | Tested implementation, not theory scope. |
| E2 | Describe E15 parameterization over direction, \(T\), \(J\), \(q\), and \(\rho\); distinguish 2,144 required from 16 exact-seam diagnostics and 128 Sobol holdouts. | C2; Table 1 | Do not count diagnostic failures as required failures. |
| E3 | Describe E16 arms: velocity scales, wrong/random controls, lookahead, minimum duration, oracle, raw and conditioned Future-O1. | C3, C4, C12; Table 1 | Define `only tested exact-profile remedy`. |
| E4 | Describe E17 development-only observer selection, frozen 30-seed holdout, 11 predeclared work-envelope conditions, 6 out-of-envelope stress conditions, and 20 synthetic trajectories. | C6, C7, C13; Table 1 | Seed × configuration is the paired unit; do not claim 14,280 independent tasks. The position-noise 0.25 stress condition must be reported as missing the predeclared threshold. |
| E5 | Describe the recorded fixed-grid trajectory: one axis, 7,673 points, 76.72 s, 10 ms grid, and \(t\ge0.04\) s evaluation. | C9–C11 | Call it one case study. |
| E6 | State decision rules: deterministic grids use acceptance criteria rather than p-values; holdout uses paired units; RMSE and absolute lag are co-primary without cross-unit scalarization. | All empirical claims | Transition to boundary results in hypothesis order. |

### 6. Dimensionless Boundary Validation

Target: 600–800 words, four paragraphs.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| B1 | Show phase collapse across \(T,J,q\), and direction against \(\rho\). | C2; Fig. 2 | Report 2,144/2,144 required cases. |
| B2 | Report Sobol threshold recovery and maximum \(|\widehat\rho-1|=0.0001953125\). | C2; Fig. 3 | Express also as 0.0195%; do not round to zero. |
| B3 | Report the 16/16 \(q=1,\rho=1\) Ruckig native failures as an exact-seam numerical observation. | C2; Fig. 3 or Appendix C | Do not call it a proven library bug or hide it in a footnote. |
| B4 | Interpret E15 as confirmation that the tested implementation realizes the analytic boundary away from the seam. | C2 | Transition from `when` stop-and-go occurs to `which intervention` removes it. |

### 7. Causal Remedy and Ablations

Target: 750–950 words, five paragraphs plus Table 2.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| C7.1 | Present velocity-scale dose response and identify \(\lambda=1\) as the matched condition. | C3; Fig. 4 | Use exact-profile metrics, not endpoint velocity alone. |
| C7.2 | Compare matched/oracle PV with wrong-sign and random-sign velocity; report median ripple 0 versus 3.2007229859. | C3 | Say matched PV is the only tested exact-profile remedy. |
| C7.3 | Report that no tested position-lookahead or minimum-duration level matches exact PV in all primary cells; explain the residual benefit of \(2T\) minimum duration. | C4; Fig. 4; Table 2 | Do not claim no possible preview or duration controller can ever work. |
| C7.4 | Report raw Future-O1 P95 failure and the fixed \(10^{-10}\) rad/s deadband contract. | C12 | Describe a Ruckig 0.17.3 numerical interaction, not universal necessity. |
| C7.5 | Use A05 as a negative control: matched PV and PVA are primary-equivalent in constant velocity because reference acceleration is zero. | C8; Table 2 or Appendix C | Do not say PVA is generally useless. Transition to perturbed/nonconstant references. |

### 8. Robustness Within the Tested Envelope

Target: 700–900 words, five paragraphs plus Table 4.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| H1 | Explain development selection of `pv_local_poly` and freezing before holdout evaluation. | C6 | Do not treat recorded replay as selection-independent. |
| H2 | Report 1,320/1,320 paired work-envelope improvements and 11/11 passing conditions. | C6; Fig. 5 | Pairing unit is seed × configuration. |
| H3 | Lead with the weakest condition: position noise at 0.1 step, 79.03% median reduction, 56.74% worst cell, 120/120 improvements. | C6; Fig. 5 | Do not report only the near-100% global median. |
| H4 | Report 20/20 constant/ramp/sine/chirp/reversal trajectories, worst ripple reduction 98.67%, maximum RMSE excess \(8.88\times10^{-17}\) rad. | C7; Fig. 5 | Call them synthetic trajectory families, not robot tasks. |
| H5 | State what has generalized—mechanism suppression within the declared single-axis envelope—and what has not. | C6, C7 | Transition to the recorded case and its different information contract. |

### 9. Recorded-Trajectory Case Study

Target: 700–900 words, five paragraphs plus Table 3 and Fig. 6.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| D1 | Define the fixed-grid recorded comparison and its paired Scheduled-P baseline at vendor \(V/A/J=4.1/8.2/4000\). | C9 | Keep current-online original waveform separate. |
| D2 | Report PV Future-O1: RMSE 0.0023518269 versus 0.0029509965 rad, 20.30% reduction, integer lag 20→10 ms, sub-sample lag 21.029→9.554 ms. Report the one offline-host deadline miss over 7,672 cycles only as a guardrail and report the recorded-target audit showing that the \(10^{-10}\) rad/s deadband leaves the stored targets and metrics unchanged (two exact zeros; minimum nonzero magnitude \(2.7055\times10^{-9}\) rad/s). | C9, C12; Fig. 6; Table 3 | One trajectory; lag is not latency. The deadline observation is not a schedulability result. |
| D3 | Report PVA Future-O1 as a negative result: RMSE 0.0035362433 rad and 13.976 ms sub-sample lag. | C9 | Do not generalize PVA underperformance beyond the case. |
| D4 | Present A06 as second-stage engineering selection: PV Future-O1 with 4.1/8.2/3200, RMSE 0.0021286588, 10 ms integer lag, 9.740 ms sub-sample lag, zero projection, and 27.87% paired RMSE reduction versus P. | C10; Table 3 | `Best tested`; A is on the grid boundary; note the 0.186 ms sub-sample trade-off versus J=4000. |
| D5 | Report the irregular-timestamp negative result: local-poly 0.0033076103 versus P 0.0029509965 rad; fixed-step Future-O1 rejects the irregular horizon. | C13; Table 4 | This is not an independent holdout and does not overturn the fixed-grid case. Transition to limitations. |

### 10. Discussion and Limitations

Target: 750–950 words, six paragraphs.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| L1 | Reframe the result as target-state semantics: a correct endpoint can coexist with an incorrect continuous-motion contract. | C1–C3 | Avoid API-tutorial language. |
| L2 | Discuss the practical implication of \(q,\rho\) and the counter-intuitive effect of looser limits or slower update rates. | C2 | Do not prescribe tight limits without closed-loop analysis. |
| L3 | Explain what the ablations exclude and what they do not exclude: tested lookahead/duration alternatives are not exact equivalents, but broader controllers remain possible. | C3, C4 | Preserve bounded causal language. |
| L4 | Discuss numerical seam and deadband as implementation-specific evidence. | C2, C12 | Version-pin Ruckig 0.17.3. |
| L5 | Discuss observer and recorded limits, including noise, irregular timestamps, one waveform, lack of independent recorded holdout, and PVA context dependence. | C8–C13 | Negative results must remain visible. |
| L6 | State missing external validity: single axis, offline open-loop solver, no plant dynamics, no coupling/synchronization, no target-machine deadline or wall-clock latency. Describe multi-axis hardware as future validation only. | Scope contract | No unreported hardware claim. Transition to a narrow conclusion. |

### 11. Conclusion

Target: 180–250 words, three paragraphs.

| ID | Purpose and required content | Claims / assets | Strength, prohibition, and transition |
|---|---|---|---|
| Q1 | Restate the terminal-state mismatch and why endpoint metrics can miss it. | C1 | No new result. |
| Q2 | Restate the analytic boundary and matched-PV continuation, followed by bounded empirical confirmation. | C2–C7 | Keep theory and solver evidence grammatically separate. |
| Q3 | State the engineering lesson: specify derivative targets consistent with intended motion and validate the information contract; close with single-axis scope. | C5, C9, C13 | Do not repeat VAJ tuning details or promise hardware results. |

## 7. Figure specifications

| Figure | Required panels and message | Primary source | Placement |
|---|---|---|---|
| Fig. 1 | `(a)` scheduled-target timeline; `(b)` P-only versus matched-PV terminal contracts; `(c)` representative exact \(p/v/a/j\) profiles. One visual must establish that endpoint position accuracy can coexist with intra-period stopping. | Newly generated from an E16 or A05 representative exact-profile case. | Introduction / Problem Formulation |
| Fig. 2 | `(a)` normalized \(v_{\rm crit}\) branches versus \(q\); `(b)` E15 phase classification in \((q,\rho)\), with \(\rho=1\) theory line. | E15 `boundary_grid.csv`. | Section 4 or 6 |
| Fig. 3 | Empirical \(\widehat\rho\) versus theoretical threshold plus Sobol \(\widehat\rho-1\) inset; exact seam points visibly marked. | E15 `holdout_thresholds.csv` and boundary grid. | Section 6 |
| Fig. 4 | Velocity coefficient dose response, wrong/random controls, position lookahead, and minimum-duration controls; raw/conditioned Future-O1 inset if legible. | E16 `causal_ablation.csv`. | Section 7 |
| Fig. 5 | Condition-wise median and worst-cell ripple reduction for the 11 work-envelope and 6 out-of-envelope stress conditions, synthetic-family summary, and acceptance reference line. Explicitly highlight the position-noise 0.25 threshold failure. | E17 `holdout_condition_summary.csv`, `trajectory_comparison.csv`. | Section 8 |
| Fig. 6 | `(a)` local recorded position/error window comparing P and PV; `(b)` RMSE–sub-sample-lag Pareto; `(c)` optional VAJ sensitivity inset. | E11 retained trace, A04 final scorecard, A06 final selected sensitivity. | Section 9 |

All figures must be regenerated from frozen numerical artifacts as vector PDF.
Existing SVG files are visual references, not arXiv-ready sources.

## 8. Table specifications

| Table | Required rows / columns | Primary source |
|---|---|---|
| Table 1 | E15/E16/E17/A04/A06; hypothesis role, design dimensions, statistical unit, counts, split, primary metrics, acceptance rule. | Experiment manifests and acceptance JSON. |
| Table 2 | P-only, matched PV, oracle PV, wrong/random velocity, position lookahead, minimum duration, raw Future-O1, conditioned Future-O1, matched PVA; exact-profile match and bounded interpretation. | E16 and A05. |
| Table 3 | Scheduled P, PV Future-O1 vendor, PVA Future-O1 vendor, PV Future-O1 4.1/8.2/3200; waveform, VAJ, RMSE, integer lag, sub-sample lag, projection. | A04/A06. |
| Table 4 | E17 11-condition work envelope, 6 stress diagnostics, synthetic families, and recorded irregular-timestamp replay; independence status, metric, result, supported claim, unsupported extrapolation. | E17. |

## 9. Appendix plan

- **Appendix A — Full proof:** segment timings, integrated states, continuity at
  \(q=1\), reachability of smaller displacement, and sign symmetry.
- **Appendix B — Metrics and algorithms:** exact-profile sampling, stop-and-go
  metrics, lag definitions, Future-O1 startup/deadband, and local-poly method.
- **Appendix C — Full experiment matrices:** E15 coordinates and seam outcomes,
  E16 all arm families, E17 conditions/seeds, A04 stencils, and A06 VAJ grid.
- **Appendix D — Reproducibility:** solver/package versions, run IDs, spec hashes,
  artifact hashes, clean-rerun status, and commands after the evidence freeze.

## 10. Page and word budget

| Component | Target English words | Expected two-column space |
|---|---:|---:|
| Abstract | 180–220 | 0.25 page |
| Introduction | 850–1,050 | 1.1–1.4 pages |
| Related Work | 650–850 | 0.8–1.0 page |
| Problem Formulation | 800–1,000 | 1.0–1.2 pages |
| Analysis | 1,250–1,550 | 1.5–2.0 pages |
| Protocol | 800–1,000 | 1.0–1.2 pages |
| Results Sections 6–9 | 2,750–3,500 total | 4.0–5.0 pages including figures |
| Discussion | 750–950 | 0.9–1.2 pages |
| Conclusion | 180–250 | 0.25–0.35 page |

Target body length is 7,500–9,000 words and approximately 11–13 two-column
pages including references but excluding appendices. If a venue later imposes a
shorter limit, preserve Sections 1, 3, 4, 6, and 7; move detailed protocols,
secondary recorded tuning, and complete matrices to the appendix.

## 11. Anticipated reviewer objections and required responses

| Objection | Required response in the paper |
|---|---|
| “Providing target velocity is obvious.” | The novelty is the analytic transition boundary, dimensionless collapse, invariant-state interpretation, and controlled mechanism ablation—not discovering an API field. |
| “The theorem does not prove Ruckig output.” | Agree explicitly: the theorem proves reachability and matched continuation; E15/E16 empirically characterize Ruckig 0.17.3. |
| “Terminal velocity makes the metric circular.” | Evaluate the entire exact profile using ripple, pulse fraction, near-zero fraction, and event rate; terminal state alone is not the outcome. |
| “The dataset is synthetic.” | Bound the claim to the declared single-axis work envelope and label recorded evidence as a single case study. |
| “The seam failures contradict the boundary.” | Report them as native numerical behavior exactly at the analytic regime/behavior seam; required off-seam classification remains complete. |
| “The observer result is cherry-picked.” | Include the recorded irregular-timestamp failure and state that fixed-grid and irregular-grid contracts require separate selection. |
| “Lag is latency.” | Define integer and sub-sample lag as waveform alignment; state that target-machine wall-clock latency remains unmeasured. |
| “No hardware or multi-axis experiment.” | Make single-axis offline scope explicit on page 1 and treat hardware as future external-validity work, not as a hidden completed contribution. |

## 12. Wave 1 completion gate

This framework is ready for TeX conversion only when:

- every claim reference resolves to C1–C13 in the companion matrix;
- every numerical result is labeled as current local evidence or clean-release
  evidence;
- all six figures and four tables have a unique narrative purpose;
- no paragraph asks the TeX author to invent a scientific conclusion;
- the terminology and prohibited-claim rules match
  `AUTHORING_CONSTRAINTS.md`;
- clean-commit reruns of release-critical experiments remain an explicit arXiv
  gate rather than being silently assumed complete.
