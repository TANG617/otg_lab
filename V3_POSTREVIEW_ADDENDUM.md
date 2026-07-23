# V3 post-review addendum

## Scope and immutability

This addendum reclassifies one comparison in the already-frozen v3 evidence. It
does not alter the v3 protocol, test identity, samples, summaries, raw bundles,
or hashes, and v3 was not rerun. The frozen root of trust remains:

- confirmation source commit
  `cf3a517bc74236a4eb1b95c5b6eee952993a0837`;
- `EXPERIMENT_PROTOCOL_V3.md` SHA-256
  `25a273d2100e855019c3f416d0aa3c5f61df00772f86e7f15f489ce7deb39eb6`;
- `results/paper_evidence_v3/artifact_index.json` SHA-256
  `12393579515e144f8cb499144772471e3a0398d8d2e19bdff89ff0fa7c479933`;
- the original, unchanged `protocol_status_v3.json`.

`results/paper_evidence_v3/raw_runs/`, the existing v3 samples, numeric
summaries, and artifact checksums are immutable historical evidence. The
machine-readable companion to this addendum is
`protocol_status_v3_postreview.json`.

## What remains valid

The v3 direct-governor safety and integrity evidence remains valid within its
stated simulated protocol scope. In particular,
`one_step_governed_pva_direct` recorded 42,199 locked-test command cycles with
zero continuous velocity/acceleration/internal-jerk violations, zero fallback,
zero projection, and no runtime deadline miss. The direct command is a
constant-jerk action, so the one-step governor's constant-jerk audit is the
appropriate command model.

The artifact-integrity result also remains valid: v3 is frozen, indexed, and
checksummed. This addendum changes interpretation, not bytes. It does not
expand the evidence into a real-robot, deployment, or production-safety claim.

## Post-review baseline-semantics finding

The conditions named `deployed_p_only`, `predicted_p`,
`raw_predicted_pv`, and `scalar_projected_pva` were intended to represent
ordinary Ruckig. Their frozen locked-test fallback rates were, respectively:

| v3 method | fallback cycles | total cycles | fallback rate |
|---|---:|---:|---:|
| `deployed_p_only` | 40,513 | 42,199 | 96.0046% |
| `predicted_p` | 40,510 | 42,199 | 95.9975% |
| `raw_predicted_pv` | 40,482 | 42,199 | 95.9312% |
| `scalar_projected_pva` | 41,022 | 42,199 | 97.2108% |

Almost all of those replacements were labelled
`ruckig_command_not_one_step_reachable`. The frozen implementation sampled a
Ruckig trajectory at `DT`, reconstructed a single constant jerk from the
acceleration difference, and rejected the native endpoint when that single
segment did not reproduce it. That is not a valid audit of an ordinary Ruckig
prefix: a Ruckig control period may contain multiple piecewise-constant-jerk
segments. A legitimate native command was therefore commonly replaced by the
one-step bounded-jerk fallback.

The historical names do not change, because changing frozen v3 artifacts would
destroy their integrity. They must be interpreted using the exposed actual
fallback behavior above, not as pure ordinary-Ruckig identities.

## Consequence for the reported 77.38% result

The frozen comparison was
`one_step_governed_pva_direct` versus `predicted_p`. Its observed paired
position-RMSE improvement was 77.38% (frozen 95% interval
69.96%-84.44%). That number is preserved as an observed v3 result, but its
confirmatory classification is withdrawn:

- it is not confirmatory evidence for ordinary Ruckig predicted-P versus
  one-step-governed PVA, because `predicted_p` executed the fallback on about
  96% of cycles;
- it is not a clean same-follower P-versus-PVA ablation, because the two
  conditions do not share one follower/governor identity;
- it may be cited only as an exposed exploratory regression of the frozen
  mixed-baseline comparison, with this confound disclosed.

The same restriction applies to interpretations that depend on the affected v3
ordinary-Ruckig baselines. It does not invalidate the direct-governor safety
result or the v3 artifact-integrity result described above.

## Legacy compatibility: frozen result versus current code

At the frozen v3 source, the development CSV ordinary-P baseline did not
reproduce the Phase A reference: position RMSE was approximately `0.285547`
instead of `0.035187` (and maximum error was approximately `0.716719` instead
of `0.184528`). This is the frozen v3 compatibility failure and remains visible
in the unchanged v3 acceptance table and summaries.

The current PR code restores the unshielded ordinary-Ruckig Phase A regression
and tests it against the historical RMSE, lag, maximum-error, 100% native
execution, and 0% unexpected-fallback criteria. The current compatibility probe
records RMSE `0.035186991`, lag `0.070 s`, maximum error `0.184528428`, native
execution rate `1.0`, and unexpected fallback rate `0.0`. That repair is a code
regression result after the v3 freeze; it does not revise the frozen v3 status
and is not a v3 confirmation rerun.

The frozen v3 acceptance table separately contains three preregistered
development-CSV candidate failures. Post-review identified the
baseline-semantics confound as an additional issue that was not represented in
that preregistered gate.

## Versioning decision

This review cycle deliberately does **not** execute v4. Consequently, the
77.38% result is not retained as a confirmatory primary conclusion. The
profile-aware implementation, explicit unshielded/shielded/direct method
identities, and same-follower P/PV/PVA matrix are infrastructure repairs only
until evaluated under a new locked protocol.

Those infrastructure repairs are Ready for reviewer assessment/merge
independently of the frozen v3 claim classification. This addendum does not
change the GitHub PR state or merge the PR; the reviewer makes the final
decision.

If a confirmatory target-component conclusion is needed later, it requires a
fresh v4 protocol, fresh test identities and seeds with no v1/v2/v3
family/seed-pair overlap, a precommitted same-follower
`one_step_governed_p_direct` / `one_step_governed_pv_direct` /
`one_step_governed_pva_direct` comparison, and a single locked confirmation
after all selection and code changes are complete. No v1/v2/v3 test may be
reused for that claim.
