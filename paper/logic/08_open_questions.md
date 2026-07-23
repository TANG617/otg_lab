# Open questions

These questions are intentionally deferred. None is a reason to leave the
stage draft incomplete, weaken negative evidence, add speculative results, or
run an unauthorized experiment.

## Scientific confirmation

### OQ01 — Fresh same-follower P/PV/PVA confirmation

- **Question:** Does target velocity or acceleration improve performance under
  one shared direct follower/governor identity on a fresh locked population?
- **Why open:** v3 mixes follower identity and fallback; Phase A uses analytic
  truth and ordinary Ruckig rather than a locked direct same-follower matrix.
- **Required future evidence:** Fresh v4 protocol, new test identities/seeds
  with no v1/v2/v3 reuse, precommitted
  `one_step_governed_p_direct`/`pv_direct`/`pva_direct` comparison, one locked
  confirmation.
- **Current manuscript treatment:** N03 limitation; no superiority claim.

### OQ02 — Multiple independent real traces

- **Question:** Do filtered/estimated target states and the governor generalize
  beyond the single development CSV?
- **Why open:** Only one development trace exists and it has no derivative
  truth.
- **Required future evidence:** Independent position streams, frozen split,
  declared clock semantics, no tuning on the locked set, whole-trajectory
  reporting, and retained failures.
- **Current manuscript treatment:** Report C06/C07 as negative development
  evidence and N01 as the boundary.

### OQ03 — Acceleration-active regimes

- **Question:** When does reliable acceleration add value beyond velocity?
- **Why open:** The three Phase A references are smooth and show no PVA-over-PV
  position benefit.
- **Required future evidence:** Predefined acceleration-active yet admissible
  references and a same-follower comparison; avoid post-hoc reference
  selection.
- **Current manuscript treatment:** C04 non-result, not a claim that
  acceleration is useless.

## External/system validation

### OQ04 — Hardware and HIL

- **Question:** How do compute jitter, sensing, actuator dynamics, tracking
  error, and plant mismatch affect execution?
- **Why open:** No HIL or robot data.
- **Required future evidence:** Hardware/HIL protocol, synchronized command and
  feedback clocks, declared controller/plant, fault policy, and safety review.
- **Current manuscript treatment:** N02; use command-generation language.

### OQ05 — Plant-model validation

- **Question:** Which feedback/current-state policy remains effective under
  realistic plant delay and model mismatch?
- **Why open:** Current command-model invariants assume the stated
  triple-integrator execution semantics.
- **Required future evidence:** Independently identified plant, delay/noise
  regimes, command-measured divergence, feedback-correction analysis, and
  stability/safety scope.
- **Current manuscript treatment:** Method assumption and Discussion item.

### OQ06 — Multi-joint real limits

- **Question:** How do joint-specific limits, synchronization, kinematic path
  error, and coupled constraints affect the formulation?
- **Why open:** Phase A is single-axis; synthetic multidof evidence is not a
  real robot constraint study.
- **Required future evidence:** Application-specific joint/path constraints,
  synchronized clocks, and a protocol that distinguishes per-axis command
  legality from path/collision feasibility.
- **Current manuscript treatment:** Scope limitation; no extrapolation.

### OQ07 — Ruckig Pro Tracking Interface

- **Question:** How does official `Trackig` perform under the same information
  conditions and limits?
- **Why open:** Pro API access/measurement is absent.
- **Required future evidence:** Licensed/available version, exact API behavior,
  same target/reference inputs, native execution identity, and fair runtime and
  following metrics.
- **Current manuscript treatment:** Related-work/system alternative only;
  never “required.”

## Publication and metadata

### OQ08 — Venue selection

- **Question:** Which venue best matches the final balance of methodology,
  systems, and experimental confirmation?
- **Why open:** Depends on whether future same-follower, real, or hardware
  evidence is added.
- **Current manuscript treatment:** arXiv primary suggestion `cs.RO`, secondary
  `eess.SY`; no venue-specific class or claims.

### OQ09 — Author metadata

- **Question:** Final authors, order, affiliations, email addresses, ORCIDs,
  acknowledgements, and funding disclosures.
- **Why open:** Not present in the scientific repository.
- **Required action:** Manual author confirmation before public release.
- **Current manuscript treatment:** Explicit placeholders in `metadata.tex`;
  never fabricate metadata.

## Non-blocking rule

The current draft must remain complete from Abstract through Conclusion with
these questions unresolved. Use a bounded statement of missing evidence rather
than TODO prose. Only author/affiliation metadata is a manual public-release
blocker; unresolved scientific questions limit claims but do not block an
internally reviewable arXiv stage draft.

