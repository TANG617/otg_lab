# Paper charter

## Stage purpose

Create a complete English arXiv stage draft that formalizes how a
position-only moving-reference stream is converted into a next-cycle
executable command under velocity, acceleration, and jerk limits. The paper
will unify causal timing, layered system responsibilities, target feasibility,
profile-aware execution, method identity, and evidence provenance. It will
report the controlled and frozen evidence that exists, including negative
results, the v3 baseline confound, and the frozen non-confirmatory V4
same-follower attempt, without waiting for hardware data.

This is a methodology + system formulation + controlled empirical study.

## Target readers

- robotics and motion-control researchers using online trajectory generation;
- engineers integrating state-to-state OTG with streamed moving references;
- researchers in reference/command governance, causal state estimation, and
  constrained sampled-data execution;
- reviewers concerned with reproducibility, baseline identity, and evidence
  correction.

The assumed reader understands sampled control and motion constraints but
should not need prior knowledge of this repository.

## Questions this stage can answer

1. What source, availability, control, target, command, and measurement times
   must be distinguished when only position samples are available?
2. Why is repeated state-to-state OTG toward a current moving target
   semantically different from following a moving reference?
3. Which responsibilities belong to the estimator, future-reference
   generator, executable-target governor, follower, and plant?
4. Under the fixed Phase A protocol, what is the isolated value of reliable
   analytic target velocity, and what did analytic acceleration fail to add?
5. Why are zero-delay centered differences noncausal, and what explicit delay
   makes a centered estimate causal?
6. What do the development-CSV negative results establish about unfiltered
   finite differences and target acceleration admissibility?
7. How does a stateful one-step bounded-jerk governor construct adjacent
   executable commands under its stated triple-integrator model?
8. How should a Ruckig control-period prefix and native/shielded/fallback
   method identity be executed and audited?
9. Which safety, runtime, reproducibility, and artifact-integrity observations
   remain supported by the frozen synthetic v3 protocol?
10. What did the fresh whole-trajectory V4 same-follower attempt observe, why
    did its preregistered confirmation gate fail, and which descriptive
    statements remain scientifically permissible?

## Questions this stage cannot answer

- Whether PVA has a confirmatory performance benefit over P or PV. V4
  observed a large difference but did not establish that claim.
- Whether the proposed governor outperforms a correctly executed ordinary
  Ruckig baseline.
- Whether the result generalizes across independent real position streams.
- Whether Ruckig Pro `Trackig` is necessary or superior; it was not measured.
- Whether the system improves real-robot tracking or is deployment/production
  safe.
- Whether torque, actuator dynamics, collision, communication faults, model
  uncertainty, or arbitrary disturbances satisfy a safety certificate.
- Whether the governor is globally optimal or recursively feasible outside its
  stated model, initial viability set, and assumptions.

## Main contributions

1. **Time-explicit problem formulation (C01, C05).** Define the causal mapping
   from position samples available by control tick \(k\) and the current
   execution state to a command at \(k+1\), keeping source and availability
   times distinct.
2. **Layered responsibility and feasibility taxonomy (C02).** Separate state
   estimation, future-reference generation, executable-target governance,
   constrained following, and plant execution; distinguish point
   admissibility, one-step reachability, sequence consistency, and stopping
   viability.
3. **One-step executable-target construction (C09).** Formulate a stateful
   bounded-jerk governor that selects a constant jerk and integrates it exactly
   to an adjacent command while enforcing the stated V/A/J and viability
   conditions.
4. **Controlled evidence on target-state information (C03--C08).** Quantify
   the value of reliable analytic velocity, retain the PV/PVA non-result,
   expose estimator causality, report a next-cycle oracle, and preserve the
   development-CSV finite-difference and feasibility negative results.
5. **Execution semantics and method identity (C10--C12).**
   Define profile-aware Ruckig-prefix auditing, distinguish ordinary,
   shielded, direct, and fallback algorithms, and report the frozen synthetic
   direct-governor evidence within its actual scope.
6. **Evidence governance under a failed confirmation gate (C14--C19, N03,
   E02).** Preserve the fresh exactly-once V4 attempt, its complete
   denominator, large observed non-confirmatory effect, five
   `deadline_miss`-only composite event-flag failures, safety/method-purity
   passes, lag uncertainty, and Python runtime failure without promoting the
   result to a confirmatory performance claim.

Checksums, immutable-source verification, generated-number provenance, and
machine-readable claim gates (C13) are reproducibility practices supporting
all contributions; they are not presented as an algorithmic contribution.

## Non-contributions

- No new experiment, v3/V4 rerun or raw resume, or V5 execution during paper
  integration.
- No claim of PVA superiority, ordinary-Ruckig inferiority, or universal
  benefit from derivative targets.
- No new globally optimal controller, MPC theorem, or arbitrary-disturbance
  recursive-feasibility proof.
- No real-robot, HIL, torque, dynamics, collision, certification, production,
  or deployment result.
- No independent real-data generalization study.
- No evaluation of the Ruckig Tracking Interface or Pro API class `Trackig`.
- No novelty priority claim such as “first” or “the first.”

## Evidence base

The draft may use only evidence registered in `evidence_sources.yaml`. The
planned classes are:

- current Phase A analytic tracking, derivative, oracle, and limit-sensitivity
  artifacts;
- current development-CSV negative and target-diagnostic artifacts;
- frozen v3 direct-governor safety and runtime summaries;
- frozen v3 artifact-integrity and denominator records;
- the post-review reclassification of the confounded frozen comparison;
- frozen V4 protocol, complete primary denominator, observed statistical
  estimate, same-information failure, safety/method identity, lag, runtime,
  harmful-trajectory, subgroup, contextual, and artifact-integrity records;
- post-freeze current profile-aware and method-identity infrastructure;
- post-freeze ordinary-Ruckig compatibility regression, clearly separated from
  v3 confirmation.

Empirical numbers must be generated from these sources rather than copied from
this planning layer into manuscript prose.

## Intended length and shape

- Main text: approximately 7,000--9,000 English words.
- Abstract: 180--230 words.
- Main contributions: 4--6 items.
- Appendices: governor details, experiment details, negative/corrective
  evidence, provenance, and reproducibility as needed.
- Planned main displays: Figures 1--8 and Tables 1--7; at most one V4 figure is
  in the main text and all exact V4 performance displays disclose the failed
  preregistered gate and non-confirmatory status.

## Suggested arXiv classification

- Primary: `cs.RO` (Robotics).
- Secondary: `eess.SY` (Systems and Control), subject to final author choice.

This is a classification suggestion, not a venue or novelty claim.

## Conservative title candidates

1. **From Position Samples to Executable Commands: Timing and Feasibility for
   Jerk-Limited Reference Following**
2. **Causal Position-Only Reference Following with One-Step-Reachable
   Jerk-Limited Commands**
3. **From Moving Position References to Executable Target States: A
   Timing-Aware Study of Online Trajectory Generation**

## Selected title

**From Position Samples to Executable Commands: Timing and Feasibility for Jerk-Limited Reference Following**

The selected title names the actual input, output, and two organizing
questions. It does not imply performance superiority, hardware deployment,
global optimality, or novelty priority. “Position samples” also avoids implying
that the single development CSV is a representative real-stream corpus.
