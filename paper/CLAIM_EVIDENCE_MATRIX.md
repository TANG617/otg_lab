# Claim–Evidence Matrix

> Wave 1 source-of-truth for scientific claims. Claim IDs C1–C13 are stable
> interfaces for the paper framework, future TeX source, figures, tables, and
> artifact validation. A result may not enter the abstract or conclusion unless
> it appears here with an explicit scope and release status.

**中文使用说明：** 本文件是全部核心结论的唯一编辑索引。每个 C 编号都同时
规定英文可写主张、适用假设、证据文件、样本与指标、允许/禁止措辞以及发布门槛。
第二波不得在 TeX 中新增无编号核心结论，也不得越过 `release-blocked` 状态宣称
已有可发布复现证据。

Companion specifications:

- [Paper Framework](PAPER_FRAMEWORK.md)
- [Authoring Constraints](AUTHORING_CONSTRAINTS.md)

## 0. Status vocabulary

| Status | Meaning |
|---|---|
| `theory-to-typeset` | The statement and assumptions are fixed, but the formal LaTeX proof has not yet been typeset and independently checked. |
| `local-accepted` | The existing run completed and its acceptance artifact passed. |
| `local-analysis` | A deterministic local cross-analysis supports the result. |
| `release-blocked` | The source manifest records a dirty worktree or the evidence is not yet pinned as a release artifact. A clean-commit rerun/freeze is required before arXiv release claims. |
| `case-study-only` | Evidence is one recorded waveform and cannot support population-level generalization. |
| `negative-result` | The result constrains or falsifies a broader positive claim and must be reported. |

No current entry is labeled `release-ready`. Immediately before the Wave 1 paper
files were added, the repository was clean at HEAD
`19a9fd98f4e8f001ac57f8eb49f155059e06e6d7`; however, the paper-critical runs
below were generated from earlier dirty worktrees and have not been regenerated
at that baseline HEAD.

## 1. Evidence source registry

### Confirmatory experiments

| Evidence ID | Run ID | Spec hash | Git provenance | Principal artifacts and current SHA256 | Release state |
|---|---|---|---|---|---|
| E15 | `20260731T122024.548365Z__f1b47bf53809` | `f1b47bf53809896e8d87f51ed4f603c336c779edfc35e70ed8c16d73c230d8bd` | commit `625ced11f68873c323319896c983618e46a8a2af`, dirty | `boundary_grid.csv`: `549beee857515d03de04944a1131b0e9d7051948a1872ea84bc047265aafdc5a`; `holdout_thresholds.csv`: `2d354989bfb8f6fd734585d965fcd68f9658e5047139d2e8947d0587ebaf7c25`; `acceptance.json`: `6d77615ff73e1f44c9e1378bd0ee8d3e2c83709d23c263234d0dd502bd67f951` | `local-accepted`, `release-blocked` |
| E16 | `20260731T123148.469169Z__9190778c7d47` | `9190778c7d47d8f62152320ff19cda447b79e5336c0b0bd59fe7336598a2b3d1` | commit `625ced11f68873c323319896c983618e46a8a2af`, dirty | `causal_ablation.csv`: `e9ddd14ac14b27526649dbc3da75b328f86ed6ab65287242b81f61e3507346de`; `acceptance.json`: `3e2bd9866a7208a135537507f93643a4171142bd311a5bcd78155a78e8feae17` | `local-accepted`, `release-blocked` |
| E17 | `20260731T125216.168015Z__0b20a1d9f771` | `0b20a1d9f77151a1d8ce9ca2ee2250fc2af6ee255303b918c27332501492bb07` | commit `625ced11f68873c323319896c983618e46a8a2af`, dirty | `holdout_condition_summary.csv`: `b44e15a22f13f337c5bdbbe8087f066d9d6ed2b51c8f7b5175ee2962253e2db0`; `trajectory_comparison.csv`: `a630dd3f70a6b295246a6dafb606823af6f756271aa29fae6f991d60d798452d`; `recorded_timestamp_replay.csv`: `0f834165318d65269adcfe8395d65af7de3b5f4c799d8fc3a37aa2c0e627fcc0`; `acceptance.json`: `52e808aa711f990f663166d8b8e03b87aec5add876de431ef4f54e1386ceeeae` | `local-accepted`, `release-blocked` |

Current relative directories:

- `../experiments/E15_dimensionless_stop_go_boundary/runs/20260731T122024.548365Z__f1b47bf53809/`
- `../experiments/E16_velocity_causal_ablation/runs/20260731T123148.469169Z__9190778c7d47/`
- `../experiments/E17_causal_pv_robustness_holdout/runs/20260731T125216.168015Z__0b20a1d9f771/`

### Supporting analyses

| Evidence ID | Final analysis run used by this specification | Analysis spec hash | Source provenance | Principal output | Release state |
|---|---|---|---|---|---|
| A03 | `20260731T074834.240101Z__7d6775f3b7d4` | `7d6775f3b7d41520e30353d539eb6b4e002e7f0bdd107686a33b1f7a52bcc624` | commit `d9d445bde58b9dbfa7bbe612187310d50f34a41b`, dirty; E12 manifest SHA256 `f57ab3239e20ba64f0498ecb255e9a31e46c989eaeffa962b533fd80f0ee8085` | `attribution_decisions.csv`, SHA256 `28168caf66663aaf548b09871315a830bdaeb6a07465a61bedf834a841fef023` | `local-analysis`, `release-blocked` |
| A04 | `20260731T074834.815578Z__e26624438fe1` | `e26624438fe149060b6e544285d46984ae222078af16fa9d20b4c48d52703ee3` | commit `d9d445bde58b9dbfa7bbe612187310d50f34a41b`, dirty; E11/E12 manifests recorded in analysis manifest | `selection_scorecard.csv`, SHA256 `c8eae4bb45500acd9566e6c663e5325df3b9f95de892beccbd4f90a4026621cd` | `local-analysis`, `case-study-only`, `release-blocked` |
| A05 | `20260731T055058.392627Z__f3b68d67fff1` | `f3b68d67fff184a54cabdee393156920a0569badd20a33e505a4b8e1e012074b` | commit `d9d445bde58b9dbfa7bbe612187310d50f34a41b`, dirty; E13 manifest SHA256 `76fe5548c5e4e9c29162bbb78079aab2f18621a732a7b4c27337f21f801c3463` | `matched_pv_pva_equivalence.csv`, SHA256 `1c4f72a632ee6d397c563d7e6c9e9211d3cfc9b8c05881385f3eadfe3e588b12` | `local-analysis`, `release-blocked` |
| A06 | `20260731T075020.258932Z__4f093b5497df` | `4f093b5497df57cdfc2cdf7cf6dbdf239e549b29ad741454c7860303f28b53df` | commit `d9d445bde58b9dbfa7bbe612187310d50f34a41b`, dirty; E14 manifest SHA256 `7f4efbf3617e3292d573bda6604e8428201d2d29d202dec6a91d4ca4a33219f2` | `selected_lag_sensitivity.csv`, SHA256 `0359d06ca675df7f144076f483c000fc715a7b207b7cd774c7723f4cedf0438e` | `local-analysis`, `case-study-only`, `release-blocked` |

The A04 and A06 rows intentionally reference the later runs that include
sub-sample-lag analysis, rather than the older copied result directories that do
not contain the complete final scorecard.

## 2. Claim index

| ID | Short name | Type | Primary section | Primary evidence | Current status |
|---|---|---|---|---|---|
| C1 | P-only terminal-state mismatch | theorem interpretation + confirmatory | 1, 3, 4 | Theorem 1, E15, A05 | `theory-to-typeset`, `release-blocked` |
| C2 | Dimensionless \(\rho=1\) boundary | theorem + confirmatory | 4, 6 | Theorem 1, E15 | `local-accepted`, `release-blocked` |
| C3 | Matched-PV invariant remedy | proposition + confirmatory | 4, 7 | Proposition 1, E16 | `local-accepted`, `release-blocked` |
| C4 | Tested lookahead/duration alternatives are not exact equivalents | causal ablation | 7 | E16 | `local-accepted`, `release-blocked` |
| C5 | Future-O1 fixed-grid causality | lemma + algorithm contract | 3, 4 | Lemma 1, E16, A04 | `theory-to-typeset`, `release-blocked` |
| C6 | Holdout robustness in the declared work envelope | confirmatory holdout | 8 | E17 | `local-accepted`, `release-blocked` |
| C7 | Extension to tested nonconstant synthetic trajectories | confirmatory holdout | 8 | E17 | `local-accepted`, `release-blocked` |
| C8 | No extra constant-velocity benefit from matched acceleration | negative control | 7 | A05 | `local-analysis`, `release-blocked` |
| C9 | Fixed-grid recorded PV case-study improvement | engineering case study | 9 | A04 | `local-analysis`, `case-study-only`, `release-blocked` |
| C10 | Best-tested recorded VAJ configuration | engineering selection | 9 | A06 | `local-analysis`, `case-study-only`, `release-blocked` |
| C11 | Runtime Vmax does not explain the tested PVA degradation | bounded negative result | 9 or 10 | A03 | `negative-result`, `release-blocked` |
| C12 | Deadband is required by the tested numerical contract | implementation observation | 7, 10 | E16 | `local-accepted`, `release-blocked` |
| C13 | Current irregular-timestamp observer does not improve recorded replay | negative result | 8–10 | E17 | `negative-result`, `release-blocked` |

## 3. Detailed claim contracts

以下 C1–C13 是稳定接口。英文 `Proposed English claim` 是第二波正文句子的
上限，不要求逐字照抄；中文审阅时应重点检查 assumptions、allowed wording 与
forbidden wording，防止把局部实验结果扩写成通用结论。

### C1 — P-only terminal-state mismatch

- **Proposed English claim:** “For a moving position stream, repeatedly pairing
  each position target with zero terminal velocity and acceleration changes the
  command from a continuation problem into a sequence of rest-to-rest problems;
  whenever the per-tick move is reachable, the tested OTG exhibits intra-period
  stop-and-go despite accurate sampled endpoints.”
- **Type:** theorem interpretation plus empirical mechanism confirmation.
- **Assumptions and scope:** single axis; symmetric jerk/acceleration bounds;
  velocity limit inactive for the analytic statement; scheduled positions form
  a locally constant-speed sequence; P-only means terminal \(v=a=0\).
- **Primary evidence:** Theorem 1 and Corollary 1; E15 exact-profile
  classification.
- **Secondary evidence:** A05/E13 pulse-region comparison; representative E16
  P-only profiles.
- **Run / hash:** E15 registry row; A05 registry row.
- **Sample / metrics / current value:** E15 completes 2,144 required grid cells;
  A05 finds all 37 P-only pulse coordinates for every tested PV/PVA stencil and
  removes their pulse fraction/event rate in the corresponding comparisons.
- **Paper mapping:** Sections 1, 3, 4, and 6; Fig. 1; Fig. 2.
- **Allowed wording:** `terminal-state mismatch`, `repeated rest-to-rest target
  contract`, `the tested solver exhibits`.
- **Forbidden wording:** `Ruckig bug`, `all OTGs necessarily use the same
  profile`, `endpoint error proves smooth motion`.
- **Clean rerun required:** yes, E15 and the selected A05 supporting artifact.
- **arXiv release status:** `theory-to-typeset`, `release-blocked`.

### C2 — Dimensionless \(\rho=1\) boundary

- **Proposed English claim:** “Under the stated one-axis reachability
  assumptions, the one-period rest-to-rest boundary is
  \(v_{\rm crit}=JT^2/32\) for \(q\ge1\) and
  \(v_{\rm crit}=AT/4-A^2/(2J)\) for \(q<1\); with
  \(\rho=|v_{\rm ref}|/v_{\rm crit}\), the transition occurs at
  \(\rho=1\), and Ruckig 0.17.3 confirms this boundary away from the exact
  numerical seam.”
- **Type:** theorem plus confirmatory experiment.
- **Assumptions and scope:** all Theorem 1 assumptions; constant-speed target
  increments; analytic equality separated from floating-point solver behavior.
- **Primary evidence:** Theorem 1, Corollaries 1–2, E15.
- **Secondary evidence:** earlier E07/E09/A05 stop-and-go surfaces, used only as
  diagnostics.
- **Run / hash / files:** E15 registry row; `boundary_grid.csv`,
  `holdout_thresholds.csv`, and `acceptance.json`.
- **Sample / metrics / current value:** 2,144/2,144 required cases; 128/128 Sobol
  holdouts; maximum \(|\widehat\rho-1|=0.0001953125\) or 0.0195%; 0 required
  failures; 16/16 native failures at \(q=1,\rho=1\) retained as diagnostic seam
  cases.
- **Paper mapping:** Sections 4 and 6; Figs. 2–3; Table 1; Appendix A/C.
- **Allowed wording:** `analytic reachability boundary`, `dimensionless collapse`,
  `confirmed away from the exact seam`.
- **Forbidden wording:** `universal phase transition for all solvers`, `perfect
  numerical agreement`, `the 16 failures are irrelevant`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-accepted`, `release-blocked`.

### C3 — Matched-PV invariant remedy

- **Proposed English claim:** “A target velocity matched to a feasible
  constant-speed reference admits a zero-acceleration, zero-jerk invariant
  continuation, and matched/oracle PV reproduces that exact continuation in all
  tested E16 primary cells.”
- **Type:** proposition plus confirmatory causal ablation.
- **Assumptions and scope:** current state lies on the constant-speed reference;
  \(|v_{\rm ref}|\le V\); target position and velocity are mutually consistent;
  single-axis E16 parameter range.
- **Primary evidence:** Proposition 1 and E16.
- **Secondary evidence:** A05 pulse-region elimination.
- **Run / hash / files:** E16 registry row; `causal_ablation.csv` and
  `acceptance.json`.
- **Sample / metrics / current value:** 1,260/1,260 arms completed; conditioned
  causal Future-O1 PV and oracle PV median exact ripple both 0; P-only reproduces
  stop-and-go; wrong/random controls have median ripple 3.2007229859.
- **Paper mapping:** Sections 4 and 7; Figs. 1 and 4; Table 2.
- **Allowed wording:** `admits an invariant continuation`, `only tested
  exact-profile remedy`, `reproduced in all tested primary cells`.
- **Forbidden wording:** `PV is the unique possible remedy`, `PV is optimal for
  every trajectory`, `the proposition proves solver selection`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-accepted`, `release-blocked`.

### C4 — Tested position lookahead and minimum duration are not exact equivalents

- **Proposed English claim:** “None of the tested position-lookahead or
  minimum-duration settings matched the exact matched-PV profile in every E16
  primary cell, although a two-period minimum duration substantially reduced
  the stop-and-go ripple.”
- **Type:** causal ablation with bounded negative conclusion.
- **Assumptions and scope:** lookahead levels 0/1/2/5 steps; minimum-duration
  levels off/1/2/5 periods; E16 \(T,q,\rho\) grid only.
- **Primary evidence:** E16.
- **Secondary evidence:** analytic distinction between position endpoint preview
  and terminal velocity consistency.
- **Run / hash / files:** E16 registry row.
- **Sample / metrics / current value:** no listed lookahead step and no listed
  minimum-duration step appears in `*_matching_exact_pv_profile`; \(2T\) leaves
  residual ripple despite substantial mitigation.
- **Paper mapping:** Section 7; Fig. 4; Table 2.
- **Allowed wording:** `not equivalent in the tested grid`, `reduced but did not
  eliminate exact-profile mismatch`.
- **Forbidden wording:** `lookahead can never solve stop-and-go`, `minimum
  duration is ineffective`, `all horizon-based controllers are excluded`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-accepted`, `release-blocked`.

### C5 — Fixed-grid Future-O1 is causal under the scheduled-target contract

- **Proposed English claim:** “When \(P[k+1]\) is a scheduled command available
  at cycle \(k\), the Future-O1 target velocity
  \(\widehat V[k+1]=(2P[k]-3P[k-1]+P[k-2])/T\) uses only available position
  history, reads no future measurement, and is exact for affine position
  sequences.”
- **Type:** lemma and algorithm/information contract.
- **Assumptions and scope:** fixed uniform period; required startup history;
  scheduled command availability is part of the system contract; affine
  exactness does not imply noise or irregular-grid optimality.
- **Primary evidence:** Lemma 1 by algebra.
- **Secondary evidence:** E16 conditioned Future-O1 exact profiles; A04 fixed-grid
  implementation.
- **Run / hash / files:** E16 and A04 registry rows.
- **Sample / metrics / current value:** algebraic affine exactness; E16 conditioned
  causal Future-O1 median ripple 0; A04 fixed-grid case uses 10 ms spacing.
- **Paper mapping:** Sections 3, 4, and 7; Fig. 1; Appendix B.
- **Allowed wording:** `causal under the stated scheduled-target information
  contract`, `fixed-grid affine exactness`.
- **Forbidden wording:** `future measurement`, `causal under every deployment
  architecture`, `valid for arbitrary timestamps`.
- **Clean rerun required:** yes for empirical support; no for the algebraic lemma.
- **arXiv release status:** `theory-to-typeset`, empirical support
  `release-blocked`.

### C6 — Robustness within the declared synthetic work envelope

- **Proposed English claim:** “After development-only method selection, the
  frozen `pv_local_poly` observer reduced exact-profile ripple in all 1,320
  paired E17 work-envelope holdout cells and passed every one of the 11 declared
  condition-level acceptance checks.”
- **Type:** confirmatory frozen holdout.
- **Assumptions and scope:** single-axis simulated constant-speed cells; selected
  method frozen before 30 new seeds; 11 work-envelope conditions; seed ×
  configuration is the paired unit.
- **Primary evidence:** E17 holdout.
- **Secondary evidence:** E17 development scorecard only for documenting method
  selection, not for estimating holdout performance.
- **Run / hash / files:** E17 registry row; `selection_scorecard.csv`,
  `robustness_cells.csv`, `holdout_condition_summary.csv`.
- **Sample / metrics / current value:** development 2,380 rows; holdout 14,280
  rows; 1,320 work-envelope pairs; 11/11 conditions pass; all pairs improve. The
  weakest condition, `position_noise=0.1 step`, has 79.03% median reduction,
  56.74% worst-cell reduction, and 120/120 improvements. The unrounded artifact
  values are 0.7902618755412852 and 0.5673642904155980, respectively. Six
  separately labeled out-of-envelope stress conditions contribute 720 paired
  cells. In the position-noise 0.25-step stress condition the median reduction
  is 45.13%, the worst cell is -10.63%, and 119/120 pairs improve, so this
  condition does not meet the predeclared work-envelope threshold.
- **Paper mapping:** Section 8; Fig. 5; Tables 1 and 4.
- **Allowed wording:** `within the declared work envelope`, `frozen holdout`,
  `paired seed/configuration cells`.
- **Forbidden wording:** `1,320 independent robot tasks`, `general robot
  robustness`, `all real noise conditions`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-accepted`, `release-blocked`.

### C7 — Extension to tested nonconstant synthetic trajectories

- **Proposed English claim:** “The selected causal PV method reduced ripple on
  each of 20 tested constant, ramp, sine, chirp, and reversal trajectories,
  with a worst trajectory-level reduction of 98.67% and negligible RMSE excess
  in the model-consistent replay.”
- **Type:** confirmatory synthetic-trajectory holdout.
- **Assumptions and scope:** 20 generated single-axis trajectories in the E17
  specification; open-loop solver replay; model-consistent conditions.
- **Primary evidence:** E17 `trajectory_comparison.csv` and
  `trajectory_holdout.csv`.
- **Secondary evidence:** E17 acceptance artifact.
- **Run / hash / files:** E17 registry row.
- **Sample / metrics / current value:** 20/20 pass; worst ripple reduction
  0.9867176835; maximum RMSE excess \(8.88185\times10^{-17}\) rad.
- **Paper mapping:** Section 8; Fig. 5; Table 4.
- **Allowed wording:** `tested synthetic trajectory families`, `nonconstant
  references within the tested envelope`.
- **Forbidden wording:** `20 robot tasks`, `closed-loop trajectory
  generalization`, `arbitrary nonconstant motion`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-accepted`, `release-blocked`.

### C8 — Matched acceleration adds no primary benefit in constant velocity

- **Proposed English claim:** “For mature constant-velocity segments, where the
  matched reference acceleration is zero, matched PV and PVA are equivalent in
  all four primary stop-and-go metrics to \(10^{-12}\) across the tested
  coordinates.”
- **Type:** analytic negative control plus empirical analysis.
- **Assumptions and scope:** mature constant-speed window; matched acceleration
  exactly zero; A05/E13 matrix; equivalence applies to primary stop-and-go
  metrics, not every secondary floating-point value.
- **Primary evidence:** A05 matched PV/PVA equivalence.
- **Secondary evidence:** the analytic fact \(a_{\rm ref}=0\).
- **Run / hash / files:** A05 registry row; `matched_pv_pva_equivalence.csv`.
- **Sample / metrics / current value:** 80 matched coordinates per stencil; 960
  total arms; maximum primary stop-and-go difference 0 and equivalence tolerance
  \(10^{-12}\); secondary differences are retained separately.
- **Paper mapping:** Section 7; Table 2 or Appendix C.
- **Allowed wording:** `no additional constant-velocity primary benefit`,
  `equivalent because the matched acceleration is zero`.
- **Forbidden wording:** `PVA is useless`, `acceleration targets never help`,
  `bitwise equivalent on every metric`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-analysis`, `release-blocked`.

### C9 — Fixed-grid recorded PV case-study improvement

- **Proposed English claim:** “On the single fixed-grid velocity-limited recorded
  trajectory, PV with Future-O1 reduced position RMSE by 20.30% relative to the
  paired Scheduled-P baseline while reducing integer waveform lag from 20 to
  10 ms.”
- **Type:** engineering case study.
- **Assumptions and scope:** one single-axis waveform; 7,673 points; 76.72 s;
  fixed 10 ms grid; \(t\ge0.04\) s window; common
  \(V/A/J=4.1/8.2/4000\); paired waveform and preprocessing.
- **Primary evidence:** final A04 analysis.
- **Secondary evidence:** E11/E12 source validation and retained traces.
- **Run / hash / files:** A04 registry row; final `selection_scorecard.csv`.
- **Sample / metrics / current value:** Scheduled P RMSE 0.0029509965 rad,
  integer lag 20 ms, sub-sample lag 21.029 ms; PV Future-O1 RMSE 0.0023518269
  rad, integer lag 10 ms, sub-sample lag 9.554 ms; RMSE ratio 0.796960. PVA
  Future-O1 RMSE 0.0035362433 rad and sub-sample lag 13.976 ms is a case-specific
  negative result.
- **Guardrail and numerical audit:** one offline-host deadline miss occurred in
  7,672 evaluated cycles and is not evidence about real-time schedulability. On
  the stored recorded target sequence, the \(10^{-10}\) rad/s deadband is
  metric-equivalent: two targets are exactly zero and the minimum nonzero target
  magnitude is \(2.7054553\times10^{-9}\) rad/s.
- **Paper mapping:** Section 9; Fig. 6; Table 3.
- **Allowed wording:** `on the recorded fixed-grid case`, `paired improvement`,
  `observed waveform lag`.
- **Forbidden wording:** `deployment-wide improvement`, `20 ms latency reduced
  to 10 ms`, `Future-O1 is generally optimal`.
- **Clean rerun required:** yes, including retained trace and final scorecard.
- **arXiv release status:** `local-analysis`, `case-study-only`,
  `release-blocked`.

### C10 — Best-tested recorded VAJ configuration

- **Proposed English claim:** “For the same recorded waveform and Future-O1 PV
  method, \(V/A/J=4.1/8.2/3200\) is the current best-tested deployment setting:
  it achieves RMSE 0.0021286588 rad, 10 ms integer waveform lag, 9.740 ms
  sub-sample lag, and zero target projection.”
- **Type:** engineering selection.
- **Assumptions and scope:** A06 tested grid; current recorded waveform only;
  Future-O1 PV fixed; co-primary RMSE and absolute lag; no cross-unit scalar
  score.
- **Primary evidence:** final A06 analysis and E14 aggregate.
- **Secondary evidence:** A04 baseline and three-point lag-sensitivity replay.
- **Run / hash / files:** A06 registry row; `best_tested_settings.csv`,
  `selected_lag_sensitivity.csv`, and `near_optimal_frontier.csv`.
- **Sample / metrics / current value:** 640 PV and 640 PVA grid settings;
  4.1/8.2/3200 has zero projection and RMSE 0.0021286588 rad; relative paired P
  reduction is 27.87%. Versus vendor J=4000, RMSE is 9.49% lower, integer lag is
  unchanged, and sub-sample lag is 0.186 ms higher. The selected A lies on the
  tested grid boundary.
- **Paper mapping:** Section 9; Table 3; optional Fig. 6 inset; Appendix C.
- **Allowed wording:** `best tested`, `deployment candidate for this waveform`,
  `light RMSE–sub-sample-lag trade-off`.
- **Forbidden wording:** `global optimum`, `universal VAJ default`, `strictly
  dominates J=4000 on every metric`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-analysis`, `case-study-only`,
  `release-blocked`.

### C11 — Runtime Vmax does not explain the tested PVA degradation

- **Proposed English claim:** “On the original recorded waveform used for the
  attribution test, relaxing runtime \(V_{\max}\) from 4.1 to 10 rad/s changes
  neither the method-wise PVA/P RMSE ratios nor observed lags, rejecting runtime
  velocity saturation as the explanation for the measured PVA degradation in
  that experiment.”
- **Type:** bounded negative causal-attribution result.
- **Assumptions and scope:** original recorded waveform; E12 36-arm controlled
  rerun; identical preprocessing except Vmax; does not enter the velocity-limit
  waveform ranking.
- **Primary evidence:** final A03 analysis.
- **Secondary evidence:** E12 source manifest.
- **Run / hash / files:** A03 registry row; `attribution_decisions.csv` and
  `projection_mechanism_summary.csv`.
- **Sample / metrics / current value:** all 36 arms complete; log-ratio interaction
  is zero for all five methods; lag interaction is 0 ms; velocity clips change
  8→0 while acceleration clips remain 4,212 in both conditions.
- **Paper mapping:** Section 9 or 10; Appendix C if space is limited.
- **Allowed wording:** `rejects the tested runtime-Vmax attribution`, `within the
  original-waveform intervention`.
- **Forbidden wording:** `velocity limits never cause PVA degradation`, `proves
  acceleration clipping is the sole cause`, cross-waveform causal comparison.
- **Clean rerun required:** yes.
- **arXiv release status:** `negative-result`, `release-blocked`.

### C12 — Deadband is part of the tested numerical method contract

- **Proposed English claim:** “In Ruckig 0.17.3, floating-point-scale variation in
  raw Future-O1 terminal velocity causes the method to fail the preregistered
  P95 exact-ripple criterion, whereas applying the specified
  \(10^{-10}\) rad/s deadband restores the tested exact-profile behavior.”
- **Type:** implementation-specific numerical observation.
- **Assumptions and scope:** E16 implementation, Ruckig 0.17.3, current floating
  point environment, constant-speed synthetic cells, specified P95 criterion.
- **Primary evidence:** E16 raw versus conditioned Future-O1 arms.
- **Secondary evidence:** E16 acceptance artifact.
- **Run / hash / files:** E16 registry row.
- **Sample / metrics / current value:** raw Future-O1 does not eliminate pulse and
  ripple under the full acceptance rule; conditioned Future-O1 median exact
  ripple is 0.
- **Recorded-case audit:** applying the same \(10^{-10}\) rad/s threshold to the
  stored recorded target sequence changes no nonzero target and therefore no
  recorded metric; this supports implementation consistency, not mathematical
  necessity.
- **Paper mapping:** Sections 7 and 10; Fig. 4 inset; Appendix B.
- **Allowed wording:** `required by the tested numerical contract`, `version- and
  implementation-specific interaction`.
- **Forbidden wording:** `universal mathematical necessity`, `proven Ruckig
  defect`, `all estimators require this threshold`.
- **Clean rerun required:** yes.
- **arXiv release status:** `local-accepted`, `release-blocked`.

### C13 — Current irregular-timestamp observer does not improve recorded replay

- **Proposed English claim:** “The E17 timestamp-aware local-polynomial method
  does not improve the existing recorded raw-timestamp replay: its RMSE is
  0.0033076103 rad versus 0.0029509965 rad for Scheduled P, while fixed-step
  Future-O1 correctly rejects the irregular-horizon contract.”
- **Type:** negative result.
- **Assumptions and scope:** existing recorded source only; raw timestamps;
  recorded replay is explicitly not an independent holdout; current local-poly
  configuration only.
- **Primary evidence:** E17 `recorded_timestamp_replay.csv`.
- **Secondary evidence:** E17 acceptance artifact and A04 fixed-grid case for
  contract contrast.
- **Run / hash / files:** E17 registry row.
- **Sample / metrics / current value:** Scheduled P RMSE 0.0029509965 rad;
  local-poly RMSE 0.0033076103 rad; Future-O1 status is rejected because the
  horizon is irregular.
- **Paper mapping:** Sections 8, 9, and 10; Table 4.
- **Allowed wording:** `does not improve this recorded replay`, `not an
  independent holdout`, `fixed-grid and irregular-grid contracts require
  separate selection`.
- **Forbidden wording:** `PV fails on recorded data`, `local polynomial
  estimation is generally inferior`, omitting the negative result.
- **Clean rerun required:** yes.
- **arXiv release status:** `negative-result`, `release-blocked`.

## 4. Claim-to-artifact release gate

Before any claim is upgraded to `release-ready`:

1. rerun E15–E17 at a clean, tagged commit;
2. rerun or re-freeze the A03–A06 evidence actually used in the paper;
3. copy only compact paper-facing CSV/JSON artifacts into a tracked evidence
   directory;
4. record source run ID, commit, spec hash, file SHA256, and generation script for
   every figure and table;
5. compare regenerated acceptance summaries and headline metrics against this
   matrix, treating any difference as a paper-blocking review item;
6. update this registry rather than silently replacing numbers in prose.

## 5. Consistency audit checklist

- C1–C13 each has one proposed English claim, one scope statement, one primary
  evidence source, and one target section.
- Theorems use only analytic assumptions; solver outcomes use explicit empirical
  verbs such as `observed`, `confirmed`, or `reproduced`.
- E15 counts distinguish 2,144 required cases from 16 diagnostic seam cases.
- E17 counts distinguish rows, paired cells, conditions, seeds, trajectories, and
  recorded replay.
- C9 and C10 use a common velocity-limited waveform denominator; the
  current-online original waveform is never used to compute their causal gain.
- C11 and C13 remain visible negative results.
- `observed waveform lag` is never renamed `latency`.
- No entry is called `release-ready` until the clean-evidence gate is completed.
