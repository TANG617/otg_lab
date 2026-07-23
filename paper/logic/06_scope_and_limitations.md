# Scope and limitations

Limitations are part of the scientific result and must appear near affected
claims, not only in a terminal paragraph. This file defines the minimum
disclosures for the stage draft.

## Scope summary

The paper covers a causal, time-explicit command-generation problem:

\[
p_{0:k}^{\mathrm{ref}},x_k^{\mathrm{cur}}
\longmapsto x_{k+1}^{\mathrm{cmd}}
\]

under velocity, acceleration, and jerk limits. Its empirical scope consists
of:

- a single-axis Phase A study on three smooth analytic references;
- one development-only position CSV under fixed-grid row semantics;
- immutable synthetic v3 evidence, used primarily for the direct
  constant-jerk method's command-constraint/runtime observations and artifact
  integrity;
- current post-freeze profile-aware/method-identity infrastructure and a
  compatibility regression, neither of which is a fresh locked performance
  experiment.

## Required limitations

### Single-axis Phase A

The P/PV/PVA target-state ablation uses one degree of freedom and three smooth,
low-dynamic analytic references. Reliable velocity truth reduces
position-following error and lag under this protocol, but the result does not
establish behavior for coupled joints, singularities, kinematic paths,
multi-axis synchronization, actuator dynamics, discontinuous references, or
other limits.

The absence of observed PVA-over-PV improvement is a condition-specific
non-result. It is not proof that acceleration is useless or that PV and PVA are
equivalent in acceleration-active regimes.

### Synthetic v3

Frozen v3 is simulated. C10 covers recorded command-profile V/A/internal-J
audits, governor invariants, fallback/projection counts, and deadline flags
within the stated synthetic protocol. It does not establish:

- real-robot safety or tracking;
- universal failure probabilities;
- real-time performance on a different machine/runtime;
- dynamics, torque, thermal, communication, collision, or environment safety;
- performance superiority over a correctly executed ordinary-Ruckig method.

### Single development CSV

Only one real-origin position trace is present, and it is development-only. It
cannot support independent generalization or a confirmatory real-stream
comparison. Its negative result is still reportable: under the fixed protocol,
none of the tested finite-difference-derived pipeline conditions improves the
P-only RMSE under the fixed admissibility-projection policy. P and PV require
zero projection, while each PVA condition projects 32.64% of requested
targets; this design therefore cannot isolate estimator error from the
projection intervention.

The trace must not be called a “real test set,” “representative real data,” or
evidence of real-robot behavior.

### Fixed-grid CSV semantics

The reported CSV experiment ignores timestamp/elapsed-time fields and assigns
one 10-ms period per row. Conclusions therefore apply only to that fixed-grid
replay. They do not validate irregular-sampling estimators, resampling,
transport jitter handling, or the source system's true clock.

Any future timestamp-aware replay is a different experiment and may not be
merged into the current numbers.

### No derivative truth in the CSV

The CSV provides position only. Velocity and acceleration obtained through
finite differences, filters, or models are estimates. Raw acceleration peaks,
projection rates, and admissibility flags are target diagnostics, not
ground-truth motion, measured robot acceleration, or proof of an executed
constraint violation.

“Ground truth” is reserved for analytic/synthetic sources with registered truth
fields.

### No independent real locked test

There is no independent real-trajectory locked confirmation. The paper may
report development evidence and specify the required future design, but it may
not claim real-stream generalization, robust real-world improvement, or an
externally validated performance effect.

### No hardware or HIL

No hardware-in-the-loop or physical-robot experiment was run. The manuscript
must use “reference following,” “command generation,” and “constrained
execution.” “Robot tracking,” “controller,” and “closed-loop tracking” require
a context that explicitly says they are future/application concepts rather
than current evidence.

### No torque, dynamics, or collision proof

The command model is kinematic/triple-integrator V/A/J. It does not prove
torque feasibility, actuator bandwidth, structural limits, tracking under
plant-model error, contact safety, collision avoidance, or certified
functional safety. Stopping viability is a directional command-state envelope,
not an application-level emergency-stop certificate.

### Frozen v3 baseline confound

The v3 conditions historically named as ordinary Ruckig executed an
algorithm-changing one-step fallback on approximately 96% of cycles. The
`predicted_p` baseline in the 77.38% comparison used fallback on
40,510/42,199 cycles (95.9975%). Therefore the comparison is:

- not a pure ordinary-Ruckig comparison;
- not a same-follower P/PVA ablation;
- not confirmatory evidence for PVA benefit.

If retained, the value appears only in a Discussion evidence-correction
subsection or appendix and is described as an exposed exploratory regression
of a mixed/confounded baseline. The fallback denominator, withdrawn
confirmatory classification, unchanged frozen artifacts, and no-rerun status
must appear in the same passage.

This confound does not erase the separately supported direct-command safety
observation or artifact-integrity evidence.

### No v4

No v4 experiment is authorized or included. Existing v1/v2/v3 locked test
identities, seeds, and family/seed pairs may not be reused to confirm N03.
Absence of v4 does not block the stage draft; it bounds the claims.

### No confirmed same-follower P/PV/PVA performance claim

Phase A changes target components within ordinary Ruckig under analytic truth,
while the affected v3 comparison changes/mixes follower/governor identity.
Neither design is a fresh locked same-follower direct P/PV/PVA comparison.
PVA-over-P and PVA-over-PV performance benefits therefore remain unresolved.

### No Ruckig Pro `Trackig` measurement

The Ruckig Tracking Interface and API class `Trackig` may be discussed from
verified official documentation and literature. They were not measured in
this project. The paper must not state that `Trackig` is necessary, superior,
available in the tested Community binding, or responsible for any result.

### Post-freeze correction is not a v3 rerun

Current code restores profile-aware ordinary-Ruckig command semantics and a
Phase A compatibility regression. These are post-freeze infrastructure/code
observations. They do not alter frozen v3 bytes, reclassify current code as the
frozen method, or provide a corrected locked comparative outcome.

## Assumption boundary for the one-step governor

The method-level guarantee is conditional on:

- the stated triple-integrator command model;
- positive known \(DT,v_{\max},a_{\max},j_{\max}\);
- finite raw inputs;
- a current state inside the specified viability set for the normal
  invariant-preserving path;
- exact execution of the selected constant jerk over the command interval;
- the explicitly selected previous-command/measured/hybrid current-state
  policy;
- no unmodelled disturbance between construction and execution.

Emergency recovery from an already nonviable state is best-effort and cannot
inherit the normal safety guarantee. These conditions prohibit claims of
global optimality, arbitrary-disturbance recursive feasibility, safety
certification, or production readiness.

## Interpretation wall

| Evidence permits | Evidence does not permit |
|---|---|
| Reliable analytic velocity helped three tested references | A position-only estimator will necessarily provide useful velocity |
| PVA truth added no position benefit over PV truth here | Acceleration is useless |
| Tested finite-difference-derived pipelines failed under a fixed projection policy on one fixed-grid development trace | Derivatives fail on real data generally, or estimator and projection effects were isolated |
| Raw target acceleration estimates exceeded \(a_{\max}\) | A robot or executed command violated acceleration |
| Frozen direct commands recorded zero audited events | Zero risk, certified safety, or universal recursive feasibility |
| Current code can represent/audit piecewise Ruckig profiles | Corrected ordinary Ruckig was rerun in v3 or lost a comparison |
| Frozen 77.38% remains a historical observation | Confirmed PVA or direct-governor superiority |
| Checksums and denominators preserve provenance | Reproducibility repairs comparison confounding |

## Minimum placement in the manuscript

- Abstract: one development CSV; synthetic-only direct evidence; no fresh
  same-follower or hardware confirmation.
- Introduction: non-contributions.
- Protocol: fixed-grid, truth availability, current/frozen/post-freeze, and no
  v4.
- Every Results subsection: local boundary sentence.
- Discussion: all limitations above, including why the paper remains valuable.
- Conclusion: fresh same-follower and independent real/robot evidence are still
  required before performance or deployment claims.
