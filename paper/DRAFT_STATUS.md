# Draft status

## Identity

- Title: *From Position Samples to Executable Commands: Timing and Feasibility
  for Jerk-Limited Reference Following*
- Version: arXiv stage draft v0
- Branch: `paper/arxiv-stage-draft-v0`
- Canonical source: Git-managed LaTeX under `paper/`
- Status: suitable for internal review and a Draft pull request

## Claims that survive the evidence audit

- Position-only moving-reference following through a state-to-state OTG has an
  explicit target-time/output-time contract.
- Estimation, future-reference generation, executable-target governance,
  following, and plant execution are distinct responsibilities.
- Under the fixed Phase A protocol on three smooth analytic references,
  reliable analytic velocity reduces P-only position error and lag.
- Under the same protocol, PVA truth does not improve the recorded position
  metric over PV truth; this is a scoped non-result, not an equivalence claim
  or a finding about acceleration estimation.
- Centered derivatives require a future sample at zero delay, or incur an
  explicit delay when made causal.
- None of the tested unfiltered finite-difference-derived pipelines improves
  the one development CSV under the fixed projection/follower pipeline.
- The stateful direct governor has a conditional, model-scoped V/A/J and
  viability construction.
- Frozen synthetic v3 supports only the separately denominated command-audit
  and measured-runtime observations stated in C10.
- Native ordinary Ruckig, viability-shielded Ruckig, direct constant-jerk
  execution, and algorithm-changing fallback remain distinct method
  identities.

The historical 77.38% value is retained only in the named Discussion
evidence-correction paragraph as an exploratory, confounded regression. The
same paragraph discloses the 40,510/42,199 fallback denominator, withdrawal of
confirmatory status, unchanged v3 bytes, and no v3 rerun.

## Not established

- PVA superiority over P or PV under a fresh same-follower locked design;
- corrected ordinary-Ruckig inferiority or a performance ranking;
- generalization from the one development CSV to independent real streams;
- real-robot closed-loop tracking, dynamics, torque, collision, or production
  safety;
- worst-case execution time, hardware portability, global optimality, or
  arbitrary-disturbance recursive feasibility.

No v4 experiment was run. No new experiment is required for this paper's
current methodology/system-formulation scope; a future v4 would be a separate
confirmatory study, not a prerequisite for this stage draft.

## Release readiness

- Internal technical review: **ready**.
- Public arXiv stage source: **technically ready, pending explicit author
  submission approval**. Author name, affiliation, email, and PDF author
  metadata are supplied; confirm any applicable ORCID, funding, and
  acknowledgement fields before submission.
- Final venue submission: **not ready**. Venue template, length, disclosure,
  and submission-policy requirements have not been selected or audited.
- Merge: **not authorized**. The branch is intended for a Draft PR only.
