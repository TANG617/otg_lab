# Draft status

## Identity

- Title: *From Position Samples to Executable Commands: Timing and Feasibility
  for Jerk-Limited Reference Following*
- Version: arXiv stage source v1
- Branch: `paper/arxiv-stage-draft-v0`
- Latest merged `main`:
  `c97e24dcfd6dd9146755235fa632e08932dc9a78`
- Latest-main merge commit:
  `8faedae1fe18111ad0329259b5618c06edf6020b`
- Canonical source: Git-managed LaTeX under `paper/`
- Status: integrated Draft PR source; final PDF/package QA passed

## Claims that survive the evidence audit

- Position-only moving-reference following through a state-to-state OTG has an
  explicit target-time/output-time contract.
- Estimation, future-reference generation, executable-target governance,
  following, and plant execution are distinct responsibilities.
- The scoped Phase A analytic, derivative, oracle, limit, and single
  development-CSV findings retain their existing boundaries.
- Frozen V3 supports only the separately denominated command-audit and
  measured-runtime observations; its historical performance comparison remains
  exploratory and confounded.
- V4 executed a fresh, exactly-once, same-follower, whole-trajectory locked
  comparison with a complete 120/120 primary paired denominator.
- The frozen V4 data contain a large observed PVA-versus-P
  trajectory-level-RMSE difference, but it is non-confirmatory. The protocol
  status is `failed_test_visible_frozen`, the statistical classification is
  `strongly_material`, and the effective classification remains
  `invalid_method_identity`.
- The same-information gate failed on five of 42,072 aligned cycles. Every
  difference was confined to the composite `event_flags` field and the only
  differing token was `deadline_miss`; all other compared fields,
  configuration identity, and direct-method purity passed. This diagnosis does
  not change the frozen gate.
- All three primary direct methods retained native direct execution and
  method-purity rate 1.0, with zero primary failures, fallbacks, and continuous
  constraint-audit failures within the frozen synthetic protocol.
- The lag point estimate did not indicate an average increase, but the
  preregistered lag-noninferiority margin was not established.
- The instrumented full Python V4 pipeline failed the preregistered hard-runtime
  gate. This is not a WCET result and does not rule out an isolated or compiled
  implementation.
- Harmful trajectories and rapid-reversal heterogeneity remain preserved;
  oracle evidence is offline/noncausal and ordinary-Ruckig context with an
  incomplete denominator is not substituted into the primary result.

The historical V3 value remains only in the named Discussion
evidence-correction paragraph with its confounded denominator and no-rerun
boundary. V3 and V4 commits, hashes, populations, and denominators remain
separate.

## Not established

- a confirmatory PVA performance benefit or P/PV/PVA superiority ranking;
- lag noninferiority for V4;
- hard real-time suitability, WCET, or 100 Hz portability;
- corrected ordinary-Ruckig inferiority or a complete contextual performance
  ranking;
- generalization from the one development CSV to independent real streams;
- real-robot closed-loop tracking, dynamics, torque, collision, certification,
  or production safety;
- worst-case execution time, hardware portability, global optimality, or
  arbitrary-disturbance recursive feasibility.

V4 completed the planned fresh attempt exactly once; it did not yield a
confirmatory result. The frozen V4 experiment was not rerun or resumed during
this paper update. Same-test rerun and raw-experiment resume remain prohibited.
No V5 experiment was executed and no V5 result exists.

## Release readiness

- Internal technical review: **ready**; adversarial and release QA passed with
  no open P0/P1/P2 finding.
- Public arXiv stage source: **technically ready, pending explicit author
  submission approval and confirmation of any applicable ORCID, funding,
  acknowledgement, disclosure, and venue-policy fields**.
- Final venue submission: **not ready**. Venue template, length, disclosure,
  and submission-policy requirements have not been selected or audited.
- Merge: **not authorized**. PR #2 must remain Draft and unmerged.

Final PDF pages/words/hashes and v1 ZIP/clean-build identities are recorded in
`QA_REPORT.md` and `SOURCE_PROVENANCE.md`. The audited arXiv payload is tied to
commit `db67b1ed7ca3b2196ecd0d52ac32a9a4deb9c745`; this repository-side status
file is intentionally outside that package.
