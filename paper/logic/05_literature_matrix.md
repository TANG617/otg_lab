# Literature matrix

## Purpose and review rule

This matrix supports a conservative related-work section for **From Position
Samples to Executable Commands: Timing and Feasibility for Jerk-Limited
Reference Following**. It is a literature-planning and evidence-boundary file,
not manuscript prose.

Only primary papers, DOI/publisher records, author or institutional full-text
copies, and official Ruckig documentation were used. Search-result snippets and
the repository's existing notes were treated only as leads. DOI metadata was
cross-checked against Crossref on 2026-07-23. “Full text read” means that the
paper or documentation itself was inspected beyond its abstract, with attention
to the problem statement, assumptions, method, and stated limitations. A “no”
does not make a source unusable for bibliographic context, but it prevents that
source from carrying a core novelty or technical claim.

No source below supports a “first,” “no prior work,” state-of-the-art, universal
optimality, hardware-safety, or PVA-superiority claim.

## Coverage of the ten required topics

| Required topic | Primary coverage | Coverage boundary |
|---|---|---|
| 1. Ruckig arbitrary-target-state OTG | `BerscheidKroeger2021Ruckig` | Complete state-to-state OTG with V/A/J constraints; not position-only moving-reference estimation. |
| 2. Official Ruckig Tracking Interface behavior | `RuckigTrackingTutorial`, `RuckigTrackigAPI` | Official product/API behavior only; not an academic novelty source and not experimental evidence for this paper. |
| 3. Discrete-time bounded V/A/J trajectory filters | `GerelliLoBianco2010DiscreteFilter` | Closest academic neighbor to a sampled third-order reference filter; its input assumptions and objective differ from this paper's layered position-only formulation. |
| 4. Online minimum-jerk / jerk-limited generation | `MacfarlaneCroft2003JerkBounded`, `HaschkeEtAl2008Online`, `CanaliEtAl2014MinimumJerk` | Establishes a broad online jerk-limited/minimum-jerk lineage; does not establish this paper's priority. |
| 5. Path-accurate online trajectory generation | `LangeAlbuSchaeffer2016PathAccurate` | Known desired path and path-accuracy objective, rather than a causally arriving position-only stream. |
| 6. Reference / command governors | `KolmanovskyEtAl2014GovernorTutorial`, `GaroneEtAl2017GovernorSurvey` | General add-on constraint management for a nominal closed loop; stronger plant/invariance claims are not transferred to this paper's kinematic one-step construction. |
| 7. Constrained MPC / jerk-QP | `GhazaeiArdakaniEtAl2015MPC` | Receding-horizon constrained optimization with a jerk-input kinematic model; adjacent alternative, not the method implemented here. |
| 8. Causal numerical differentiation and local polynomial estimation | `SavitzkyGolay1964`, `VermaEtAl2024Differentiation` | Local-polynomial differentiation and explicit real-time causal/noncausal timing; this paper's centered-difference timing statement is also directly derivable from sample indices. |
| 9. Alpha--Beta--Gamma and Kalman state estimation | `Kalata1984TrackingIndex`, `Kalman1960Filtering`, `SahoMasugi2015ABG` | Estimator foundations and fixed-gain tracking; this paper evaluates estimator roles but claims no estimator novelty. |
| 10. Moving-target or reference-following methods | `SahoMasugi2015ABG`, `GhazaeiArdakaniEtAl2015MPC`, `RuckigTrackingTutorial` | Moving-target estimation, target-updated robot motion, and official tracking prediction provide relevant neighbors; none combines exactly this paper's timing, governance, execution, and evidence-audit scope. |

## Academic primary sources

### `BerscheidKroeger2021Ruckig`

- **Verified title:** *Jerk-limited Real-time Trajectory Generation with
  Arbitrary Target States*
- **Authors:** Lars Berscheid; Torsten Kröger
- **Year / venue:** 2021; *Proceedings of Robotics: Science and Systems XVII*
- **DOI / arXiv:** DOI
  [10.15607/RSS.2021.XVII.015](https://doi.org/10.15607/RSS.2021.XVII.015);
  arXiv [2105.04830](https://arxiv.org/abs/2105.04830)
- **Primary URL:** [RSS proceedings page](https://roboticsproceedings.org/rss17/p015.html)
- **Topic:** arbitrary-target-state, third-order, time-optimal OTG
- **Exact role in paper:** define ordinary Ruckig's academic problem class and
  place complete target states \([p,v,a]\) and V/A/J limits in the OTG
  literature.
- **Supported statement:** Ruckig computes jerk-limited, time-synchronized
  trajectories from complete initial states to arbitrary complete target states
  under velocity, acceleration, and jerk limits.
- **Distinction from current work:** the Ruckig paper starts with supplied
  current and target states. The present work asks how a causal position-only
  reference stream becomes a time-stamped future state and an adjacent
  executable command, while separating estimation, prediction, governance,
  following, and execution.
- **Full text read:** yes; official RSS paper inspected.
- **Uncertainty:** low for metadata and problem definition. Do not infer
  Tracking Interface behavior from this paper.

### `GerelliLoBianco2010DiscreteFilter`

- **Verified title:** *A Discrete-Time Filter for the On-Line Generation of
  Trajectories with Bounded Velocity, Acceleration, and Jerk*
- **Authors:** Oscar Gerelli; Corrado Guarino Lo Bianco
- **Year / venue:** 2010; *2010 IEEE International Conference on Robotics and
  Automation (ICRA)*, pp. 3989--3994
- **DOI / arXiv:** DOI
  [10.1109/ROBOT.2010.5509712](https://doi.org/10.1109/ROBOT.2010.5509712);
  no arXiv ID found
- **Primary URL:** [DOI record](https://doi.org/10.1109/ROBOT.2010.5509712)
- **Topic:** nonlinear discrete-time third-order trajectory/reference filter
- **Exact role in paper:** closest literature anchor for stateful sampled-data
  filtering with bounded first, second, and third derivatives.
- **Supported statement:** a discrete-time filter can track a reference while
  constructing trajectories bounded in velocity, acceleration, and jerk, with
  explicit sampled-time dynamics.
- **Distinction from current work:** its formal input includes a reference and
  its first two derivatives and its objective is minimum-time reference
  filtering. The present system begins with position samples only, makes
  estimator and prediction timing explicit, and separately audits
  one-step-reachable command construction and follower identity.
- **Full text read:** yes; full ICRA paper inspected.
- **Uncertainty:** low for the filter formulation; medium when comparing
  viability details, because terminology and guarantee sets are not identical.

### `MacfarlaneCroft2003JerkBounded`

- **Verified title:** *Jerk-Bounded Manipulator Trajectory Planning: Design for
  Real-Time Applications*
- **Authors:** Sonja E. Macfarlane; Elizabeth A. Croft
- **Year / venue:** 2003; *IEEE Transactions on Robotics and Automation* 19(1),
  42--52
- **DOI / arXiv:** DOI
  [10.1109/TRA.2002.807548](https://doi.org/10.1109/TRA.2002.807548); no arXiv
  ID found
- **Primary URL:** [DOI record](https://doi.org/10.1109/TRA.2002.807548)
- **Topic:** online jerk-bounded manipulator trajectories using quintic
  segments
- **Exact role in paper:** establish that bounded-jerk online robot trajectory
  construction predates Ruckig and includes polynomial/blending approaches.
- **Supported statement:** the paper presents an online, jerk-bounded method
  based on concatenated fifth-order polynomials and reports simulation and
  industrial-robot experiments.
- **Distinction from current work:** it plans and blends waypoint trajectories;
  it does not formulate causal derivative estimation from a position-only
  stream or the target-time/output-time contract studied here.
- **Full text read:** no; verified publisher/author-institution metadata and
  abstract only.
- **Uncertainty:** low for metadata and abstract-level description; medium for
  detailed algorithm comparisons. Use only for historical context, not a core
  novelty boundary.

### `HaschkeEtAl2008Online`

- **Verified title:** *On-Line Planning of Time-Optimal, Jerk-Limited
  Trajectories*
- **Authors:** Robert Haschke; Erik Weitnauer; Helge Ritter
- **Year / venue:** 2008; *2008 IEEE/RSJ International Conference on
  Intelligent Robots and Systems (IROS)*, pp. 3248--3253
- **DOI / arXiv:** DOI
  [10.1109/IROS.2008.4650924](https://doi.org/10.1109/IROS.2008.4650924); no
  arXiv ID found
- **Primary URL:** [author-group publication page](https://ni.www.techfak.uni-bielefeld.de/pubs/2993)
- **Topic:** synchronized time-optimal third-order online planning
- **Exact role in paper:** show the pre-Ruckig lineage of real-time V/A/J
  state-to-state planning from arbitrary initial conditions.
- **Supported statement:** the method generates synchronized jerk-limited
  trajectories under velocity, acceleration, and jerk limits and allows
  replanning from arbitrary initial states; the paper assumes zero final
  velocity and acceleration.
- **Distinction from current work:** its target is a terminal rest state. The
  present problem concerns a continuously moving reference whose state is not
  directly observed and an adjacent executable command at each cycle.
- **Full text read:** yes; author-hosted full paper inspected.
- **Uncertainty:** low.

### `CanaliEtAl2014MinimumJerk`

- **Verified title:** *Minimum-Jerk Online Planning by a Mathematical
  Programming Approach*
- **Authors:** Federico Canali; Corrado Guarino Lo Bianco; Marco Locatelli
- **Year / venue:** 2014; *Engineering Optimization* 46(6), 763--783
- **DOI / arXiv:** DOI
  [10.1080/0305215X.2013.806916](https://doi.org/10.1080/0305215X.2013.806916);
  no arXiv ID found
- **Primary URL:** [publisher page](https://www.tandfonline.com/doi/abs/10.1080/0305215X.2013.806916)
- **Topic:** constrained online minimum-jerk planning
- **Exact role in paper:** distinguish minimum-jerk optimization from
  time-optimal jerk-limited state-to-state generation and from this paper's
  feasibility-first one-step governor.
- **Supported statement:** the paper formulates a constrained minimum-jerk
  online planning problem with time, distance, velocity, and acceleration
  constraints.
- **Distinction from current work:** the present governor is not claimed to
  minimize jerk or another horizon objective; it constructs a feasible
  constant-jerk next step under stated viability conditions.
- **Full text read:** no; publisher abstract and bibliographic record inspected.
- **Uncertainty:** low for metadata and abstract-level scope; medium for solver
  details. Non-core contextual citation only.

### `LangeAlbuSchaeffer2016PathAccurate`

- **Verified title:** *Path-Accurate Online Trajectory Generation for
  Jerk-Limited Industrial Robots*
- **Authors:** Friedrich Lange; Alin Albu-Schäffer
- **Year / venue:** 2016; *IEEE Robotics and Automation Letters* 1(1), 82--89
- **DOI / arXiv:** DOI
  [10.1109/LRA.2015.2506899](https://doi.org/10.1109/LRA.2015.2506899); no
  arXiv ID found
- **Primary URL:** [DLR institutional record and full text](https://elib.dlr.de/101288/)
- **Topic:** path-accurate, jerk-limited online trajectory generation
- **Exact role in paper:** separate path preservation along a known desired
  path from moving-reference state reconstruction and one-step execution.
- **Supported statement:** the method uses forward scaling and backtracking to
  generate jerk-limited commands that preserve the desired axis-space path,
  and it was demonstrated at a sampled industrial-robot interface.
- **Distinction from current work:** it assumes a desired path whose shape
  should be retained. The current work receives only a causal sequence of
  positions and does not claim path accuracy, torque feasibility, or robot
  experiments.
- **Full text read:** yes; institutional author manuscript inspected.
- **Uncertainty:** low.

### `KolmanovskyEtAl2014GovernorTutorial`

- **Verified title:** *Reference and Command Governors: A Tutorial on Their
  Theory and Automotive Applications*
- **Authors:** Ilya Kolmanovsky; Emanuele Garone; Stefano Di Cairano
- **Year / venue:** 2014; *2014 American Control Conference*, pp. 226--241
- **DOI / arXiv:** DOI
  [10.1109/ACC.2014.6859176](https://doi.org/10.1109/ACC.2014.6859176); no
  arXiv ID found
- **Primary URL:** [MERL publication page and full text](https://www.merl.com/publications/TR2014-119)
- **Topic:** reference/command governors and constraint enforcement
- **Exact role in paper:** define governors as add-on reference supervisors
  and frame the current executable-target layer as a specialized kinematic
  governor rather than a new estimator or OTG solver.
- **Supported statement:** reference and command governors modify references
  supplied to a nominal closed loop to enforce state/control constraints, with
  designs based on prediction, admissible sets, or optimization.
- **Distinction from current work:** classical governor guarantees concern a
  specified plant/closed-loop model and admissible sets. The present result is
  limited to a sampled triple-integrator command model and does not inherit
  plant-level robustness, invariance, or production-safety guarantees.
- **Full text read:** yes; MERL full paper inspected.
- **Uncertainty:** low.

### `GaroneEtAl2017GovernorSurvey`

- **Verified title:** *Reference and Command Governors for Systems with
  Constraints: A Survey on Theory and Applications*
- **Authors:** Emanuele Garone; Stefano Di Cairano; Ilya Kolmanovsky
- **Year / venue:** 2017; *Automatica* 75, 306--328
- **DOI / arXiv:** DOI
  [10.1016/j.automatica.2016.08.013](https://doi.org/10.1016/j.automatica.2016.08.013);
  no arXiv ID found
- **Primary URL:** [publisher page](https://www.sciencedirect.com/science/article/pii/S0005109816303715)
- **Topic:** reference/command governor taxonomy and applications
- **Exact role in paper:** support the broad taxonomy and distinguish
  reference governance from MPC and low-pass filtering.
- **Supported statement:** the survey characterizes reference and command
  governors as add-on constraint-management schemes that modify a reference
  when necessary, and surveys linear, nonlinear, robust, distributed, and
  related variants.
- **Distinction from current work:** the current one-step governor is one
  narrow kinematic construction; the manuscript must not imply that it
  subsumes the governor literature or carries its stronger closed-loop
  guarantees.
- **Full text read:** no; publisher abstract and extended web preview
  inspected. The full 2014 tutorial above was read for technical grounding.
- **Uncertainty:** low for taxonomy and metadata; do not cite this source for a
  theorem-specific statement without reading the relevant full section.

### `GhazaeiArdakaniEtAl2015MPC`

- **Verified title:** *Real-Time Trajectory Generation Using Model Predictive
  Control*
- **Authors:** M. Mahdi Ghazaei Ardakani; Björn Olofsson; Anders Robertsson;
  Rolf Johansson
- **Year / venue:** 2015; *2015 IEEE Conference on Automation Science and
  Engineering (CASE)*, pp. 942--948
- **DOI / arXiv:** DOI
  [10.1109/CoASE.2015.7294220](https://doi.org/10.1109/CoASE.2015.7294220); no
  arXiv ID found
- **Primary URL:** [Lund University record and full text](https://lup.lub.lu.se/search/publication/719dec89-76c5-44a4-8ae2-108a77964a80)
- **Topic:** constrained MPC, jerk-input kinematic model, moving-target motion
- **Exact role in paper:** provide the principal optimization-based alternative
  to a one-step governor and an example in which updated target estimates
  trigger replanning.
- **Supported statement:** the paper formulates real-time point-to-point
  trajectory generation as constrained receding-horizon optimization and
  evaluates it in an industrial-robot ball-catching scenario; its kinematic
  model uses jerk as the manipulated input.
- **Distinction from current work:** this paper does not solve a prediction
  horizon QP or claim MPC optimality. Its direct governor chooses one adjacent
  executable command and separately records estimator, predictor, follower,
  and fallback identities.
- **Full text read:** yes; institutional full paper inspected.
- **Uncertainty:** low.

### `SavitzkyGolay1964`

- **Verified title:** *Smoothing and Differentiation of Data by Simplified
  Least Squares Procedures*
- **Authors:** Abraham Savitzky; Marcel J. E. Golay
- **Year / venue:** 1964; *Analytical Chemistry* 36(8), 1627--1639
- **DOI / arXiv:** DOI
  [10.1021/ac60214a047](https://doi.org/10.1021/ac60214a047); no arXiv ID
- **Primary URL:** [ACS publisher page](https://pubs.acs.org/doi/10.1021/ac60214a047)
- **Topic:** local-polynomial least-squares smoothing and differentiation
- **Exact role in paper:** cite the foundation of polynomial-window derivative
  estimates used as estimator baselines.
- **Supported statement:** local polynomial least-squares procedures can
  produce smoothed signal and derivative estimates.
- **Distinction from current work:** the current contribution is not a new
  Savitzky--Golay estimator. It emphasizes which sample times are used,
  whether future data are required, and how estimator delay composes with
  next-cycle command timing.
- **Full text read:** no; publisher metadata and first page inspected.
- **Uncertainty:** low for bibliographic and foundational attribution; do not
  attribute this paper's causal/noncausal timing taxonomy to the 1964 paper.

### `VermaEtAl2024Differentiation`

- **Verified title:** *Real-Time Numerical Differentiation of Sampled Data
  Using Adaptive Input and State Estimation*
- **Authors:** Shashank Verma; Sneha Sanjeevini; E. Dogan Sumer; Dennis S.
  Bernstein
- **Year / venue:** 2024; *International Journal of Control* 97(12),
  2962--2974
- **DOI / arXiv:** DOI
  [10.1080/00207179.2024.2313046](https://doi.org/10.1080/00207179.2024.2313046);
  arXiv [2308.08074](https://arxiv.org/abs/2308.08074)
- **Primary URL:** [arXiv record and full text](https://arxiv.org/abs/2308.08074)
- **Topic:** causal numerical differentiation, real-time availability, and
  adaptive state/input estimation
- **Exact role in paper:** support explicit treatment of future samples,
  estimate availability, delay, backward differences, and centered
  local-polynomial differentiation in real-time comparisons.
- **Supported statement:** the paper distinguishes causal methods, which use
  only data available through the estimated time, from noncausal methods that
  require future samples; it also accounts explicitly for availability delay
  in real-time error metrics.
- **Distinction from current work:** it proposes an adaptive differentiator and
  evaluates simulated signals/vehicle data. The present work uses simpler
  estimators as controlled information conditions and focuses on downstream
  prediction, target feasibility, and command execution.
- **Full text read:** yes; journal author copy and arXiv full text inspected.
- **Uncertainty:** low. Its convention includes computation availability; the
  manuscript must define its own \(k\)-indexed availability convention rather
  than copying delay counts without translation.

### `Kalata1984TrackingIndex`

- **Verified title:** *The Tracking Index: A Generalized Parameter for
  Alpha--Beta and Alpha--Beta--Gamma Target Trackers*
- **Authors:** Paul R. Kalata
- **Year / venue:** 1984; *IEEE Transactions on Aerospace and Electronic
  Systems* AES-20(2), 174--182
- **DOI / arXiv:** DOI
  [10.1109/TAES.1984.310438](https://doi.org/10.1109/TAES.1984.310438); no
  arXiv ID found
- **Primary URL:** [DOI record](https://doi.org/10.1109/TAES.1984.310438)
- **Topic:** alpha--beta and alpha--beta--gamma fixed-gain tracking
- **Exact role in paper:** historical foundation for the alpha--beta--gamma
  estimator family used as a lightweight causal baseline.
- **Supported statement:** the paper develops a tracking-index parameterization
  for alpha--beta and alpha--beta--gamma target trackers.
- **Distinction from current work:** the present work neither derives optimal
  fixed gains nor claims tracker novelty; it records the estimator's
  information condition and downstream effect.
- **Full text read:** no; DOI metadata and verified secondary bibliographic
  cross-check only.
- **Uncertainty:** low for metadata and broad role, medium for any
  theorem-specific claim. Prefer the full-text Saho--Masugi source below for
  equations or detailed ABG behavior.

### `Kalman1960Filtering`

- **Verified title:** *A New Approach to Linear Filtering and Prediction
  Problems*
- **Authors:** Rudolf E. Kalman
- **Year / venue:** 1960; *Journal of Basic Engineering* 82(1), 35--45
- **DOI / arXiv:** DOI
  [10.1115/1.3662552](https://doi.org/10.1115/1.3662552); no arXiv ID
- **Primary URL:** [DOI record](https://doi.org/10.1115/1.3662552)
- **Topic:** recursive linear state estimation and prediction
- **Exact role in paper:** foundational citation for constant-acceleration or
  constant-jerk Kalman estimator baselines.
- **Supported statement:** linear state-transition and observation models with
  second-order statistics admit a recursive optimal linear filtering and
  prediction formulation under the paper's assumptions.
- **Distinction from current work:** Kalman filtering addresses state
  estimation; it does not by itself define the future reference to follow,
  enforce target reachability, select an executable jerk, or audit a follower's
  profile.
- **Full text read:** yes; original paper inspected.
- **Uncertainty:** low. Optimality language must retain the linear/statistical
  assumptions and must not be transferred to robust estimator variants.

### `SahoMasugi2015ABG`

- **Verified title:** *Performance Analysis of Alpha--Beta--Gamma Tracking
  Filters Using Position and Velocity Measurements*
- **Authors:** Kenshi Saho; Masao Masugi
- **Year / venue:** 2015; *EURASIP Journal on Advances in Signal Processing*
  2015, article 35
- **DOI / arXiv:** DOI
  [10.1186/s13634-015-0220-3](https://doi.org/10.1186/s13634-015-0220-3); no
  arXiv ID found
- **Primary URL:** [open-access publisher full text](https://link.springer.com/article/10.1186/s13634-015-0220-3)
- **Topic:** fixed-gain kinematic target tracking and ABG prediction/update
- **Exact role in paper:** provide a readable primary definition of ABG
  prediction/update structure, its constant-acceleration model, and its
  smoothing/tracking trade-off.
- **Supported statement:** alpha--beta--gamma filters recursively predict and
  update position, velocity, and acceleration using a constant-acceleration
  motion model and fixed gains; target jerk creates a tracking-bias trade-off.
- **Distinction from current work:** the cited paper studies tracking-filter
  estimation performance, including position/velocity measurements. This
  paper's stream is position-only and its main contribution is the
  estimator-to-command timing and feasibility pipeline, not an improved ABG
  filter.
- **Full text read:** yes; open-access full text inspected.
- **Uncertainty:** low.

## Official software documentation (not academic novelty evidence)

### `RuckigTrackingTutorial`

- **Verified title:** *Ruckig Tutorial: Tracking Interface*
- **Author / organization:** Ruckig project
- **Year / venue:** no publication year stated; official Ruckig 0.19.4
  documentation, accessed 2026-07-23
- **DOI / arXiv:** none
- **Primary URL:** [official tutorial](https://docs.ruckig.com/tutorial.html#tracking-interface)
- **Topic:** official Tracking Interface motivation and behavior
- **Exact role in paper:** describe, with product-appropriate attribution, the
  documented reason for prediction in `Trackig` and the fact that the interface
  is a Pro feature.
- **Supported statement:** the official tutorial says that repeatedly passing a
  moving signal's current state as an ordinary target causes catch-up lag and
  that the Tracking Interface predicts ahead, using constant-acceleration
  prediction by default, to follow the signal.
- **Distinction from current work:** documentation describes a product
  interface that was not available or measured in this project. It does not
  prove current-paper novelty, necessity, superiority, or performance.
- **Full text read:** yes; complete relevant tutorial section inspected.
- **Uncertainty:** low for documented version 0.19.4 behavior; implementation
  internals, licensing behavior beyond the page, and other versions remain
  outside scope.

### `RuckigTrackigAPI`

- **Verified title:** *ruckig::Trackig Class Template Reference*
- **Author / organization:** Ruckig project
- **Year / venue:** no publication year stated; official Ruckig 0.19.4 API
  documentation, accessed 2026-07-23
- **DOI / arXiv:** none
- **Primary URL:** [official API reference](https://docs.ruckig.com/classruckig_1_1Trackig.html)
- **Topic:** `Trackig` target-state, prediction-model, look-ahead, and update
  API
- **Exact role in paper:** verify the exact API spelling `Trackig` and document
  that online updates accept a complete target state and expose a prediction
  model.
- **Supported statement:** the API reference identifies `Trackig` as the online
  tracking class, accepts `TargetState`, and documents a prediction model whose
  default is constant-acceleration integration.
- **Distinction from current work:** this is software documentation, not a
  peer-reviewed algorithm description and not evidence that Pro `Trackig`
  would improve the present dataset or follower comparisons.
- **Full text read:** yes; class reference inspected.
- **Uncertainty:** low for the documented public API; algorithmic details not
  present in the API page remain unknown.

## Synthesis and allowed novelty language

The literature supports the following conservative synthesis:

1. Complete-state jerk-limited OTG, online jerk-limited/minimum-jerk planning,
   discrete-time derivative-bounded filters, path-accurate OTG, governors, MPC,
   and recursive state estimators all have substantial prior art.
2. Official Ruckig documentation itself identifies prediction as the mechanism
   used by its Tracking Interface to reduce moving-signal catch-up lag, but
   official documentation is not academic priority evidence and this project
   has no Pro measurement.
3. The defensible distinction is therefore a scoped combination and audit
   problem: explicit source/availability/target/command time semantics for
   position-only samples; separation of estimation, prediction, executable
   governance, following, and plant roles; one-step bounded-jerk command
   construction; profile-aware execution semantics; and evidence/method
   identity correction.

Allowed phrases include “we formulate,” “we separate,” “we study under the
tested conditions,” and “our scope differs in.” Prohibited phrases include
“first,” “unprecedented,” “no prior method,” and any implication that the
one-step governor subsumes reference governors, MPC, or Ruckig Tracking.

## Remaining literature gaps and uncertainty

- No peer-reviewed full algorithm description for the current Pro `Trackig`
  implementation was located; official documentation is the only verified
  source used for its behavior.
- A full text was not available during this pass for
  `MacfarlaneCroft2003JerkBounded`, `CanaliEtAl2014MinimumJerk`,
  `GaroneEtAl2017GovernorSurvey`, `SavitzkyGolay1964`, or
  `Kalata1984TrackingIndex`. These entries are restricted to contextual or
  foundational attribution; none carries the paper's core novelty boundary.
- The constrained-MPC source uses jerk as a manipulated input and linear
  constraints, but it is not identical to this repository's experimental
  jerk-QP. The manuscript should call it an adjacent formulation, not the same
  algorithm.
- The search does not justify a comprehensive priority claim across all
  sampled-data safety filters, command filters, predictive servoing methods, or
  proprietary industrial motion generators.
- Relevant literature does not remove the empirical gaps already recorded in
  the paper charter: no independent real locked test, no hardware/HIL, no Pro
  `Trackig` measurement, and no fresh same-follower P/PV/PVA confirmation.

## `CITATION_NEEDED` gates

- `CITATION_NEEDED_TRACKIG_INTERNALS`: any statement about undocumented
  `Trackig` optimization internals, formal guarantees, or behavior outside
  official version 0.19.4 documentation.
- `CITATION_NEEDED_JERK_QP_IDENTITY`: any statement that the repository's
  jerk-QP is the same algorithm as a cited MPC or trajectory-optimization
  method, rather than merely an adjacent formulation.
- `CITATION_NEEDED_PRIORITY`: any use of “first,” “no prior work,” or an
  exhaustive novelty claim. The present search is not a systematic-review
  protocol and cannot support such wording.
