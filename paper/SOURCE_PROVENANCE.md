# Source provenance

## Repository and paper anchors

- Repository: `TANG617/otg_lab`
- Paper branch: `paper/arxiv-stage-draft-v0`
- Latest audited `main` and V4 merge commit:
  `c97e24dcfd6dd9146755235fa632e08932dc9a78`
- Merge commit that brought latest `main` into the paper branch:
  `8faedae1fe18111ad0329259b5618c06edf6020b`
- Final paper source commit: `PENDING_FINAL_COMMIT`
- Logic-lock SHA-256: `PENDING_FINAL_BUILD`
- Evidence-registry SHA-256: `PENDING_FINAL_BUILD`
- Human evidence-inventory SHA-256: `PENDING_FINAL_BUILD`

The logic lock binds the charter, claim registry, evidence registry, argument
outline, notation/timing contract, scope, literature matrix, decision log, and
adversarial logic review. `claims.yaml` is the machine-readable wording and
section-permission source of truth.

## V4 immutable history

V4 has distinct immutable execution/result epochs and report-only follow-ups:

| Role | Commit |
| --- | --- |
| exactly-once confirmation source | `461fc560461b0a4726cbabdb97b2dbd4dc305e0a` |
| bounded frozen result | `f49b4ef1cacf8228c5d243353184acb8a7d02311` |
| report-only reporting-provenance repair | `8baece6b7051ccc231d9bb0362fd85e4aa5a94e5` |
| report-only same-information aid | `b9301eaf36dc04f1abf662c42821eddfe8c3188a` |

The annotated evidence tag `paper-evidence-v4-461fc56` peels to the bounded
result commit. The annotated confirmation tag
`paper-evidence-v4-confirmation-source` peels to the confirmation source.

The locked test was executed exactly once before this paper update. The raw
execution and statistical estimate completed and then froze with:

- protocol status: `failed_test_visible_frozen`;
- statistical classification: `strongly_material`;
- effective classification: `invalid_method_identity`;
- same-test rerun permitted: `false`;
- raw experiment resume permitted: `false`;
- confirmatory PVA performance claim permitted: `false`.

This paper update did not run or resume V3 or V4. It did not execute V5, and no
V5 result exists. The report-only aid preserves all five frozen composite
event-flag failures; it does not change the gate, algorithm outputs, raw
artifacts, or result classification.

## V4 checked-in digests

| Object | SHA-256 |
| --- | --- |
| `EXPERIMENT_PROTOCOL_V4.md` | `baad38320593695a4c231f1802faa3a48b4a32b318da841fda5b1354cd8b770e` |
| `V4_HYPOTHESES.md` | `50487997bca9ef4a35ddf82edfc0f064e6636413479014224c65b3af04e43f81` |
| `V4_STATISTICAL_DESIGN.json` | `63a8677591976c436b14e9afee059a7575fd47909c2f18c72a5f515127be2a6c` |
| `V4_ACCEPTANCE_CRITERIA.json` | `9ed534c8268abd7fa6d1d55b3227e9b0160d7838e0d2bbabd23ab6914bf1fbbb` |
| `V4_METHOD_MATRIX.json` | `e60c0e79483ac1327de15786c66efbc90b04d0379ee78e5e55ca83c32aea665e` |
| `V4_PROTOCOL_DECISIONS.md` | `442f0f8ee8c48ff789e19a3c9bc8c623a6213bfbf944c1ff30a94c8e8ac717d0` |
| `config_lock_v4.json` | `d61b0f8596b04358c7bef6a1e43b6775b3dbb00020c2aca28d5d2cd4d9f6f3d3` |
| `split_manifest_v4.json` | `1727505734c8026ed18d87123d5d5a8c02e2f201a33ea786fbcde2c9ab398796` |
| preregistered `protocol_status_v4.json` | `c0c3d358c969dbb343ac05dc964075a514f37d8153ce47d6e4ca60a252de4909` |
| result `protocol_status_v4.json` | `48c98a81a76129a0fc2dd913aabb28bc9312d31a76a4283b27bf1fea9431a34b` |
| V4 root `artifact_index.json` | `fd78eb559d039620ae1c6e06faac44ab6fc8dbff9208c05523b4efcab4a75a95` |
| V4 root `artifact_index.sha256` | `96fbd8d2dc165beca47b40dd2ecb8eb46f6ae1be7f095974cc69e1ae2c9b9582` |
| `paper_handoff.json` | `d072cfdeb35cc5325ae7b8d5ae3e5ce69e7d19689200e6ba72efc13e15e7fff9` |
| canonical `same_information_audit.csv` | `ec46bf920912179020ceaa0eeedcfe447026a5ec1a341b82ef71b83d99ba1a8e` |
| `same_information_failures.csv` | `dd9c89784766f85473159da6a5c0f072881e47828874fee7f17c7613cd86718f` |
| `SAME_INFORMATION_FAILURE_ANALYSIS.md` | `2144b449db3d189684833449b4686982b9156cf19db00dcc48360e6650287573` |
| `V4_AGENT_EXECUTION_AUDIT.md` | `2dd7433ca27a9a75197393c32c4d55bed85259106c79b96ec86a504cb6067d36` |

The root index enumerates 152 bounded artifacts. The canonical
same-information audit contains 42,072 aligned cycles; the five failures are
limited to composite `event_flags`, with `deadline_miss` as the only differing
token. All other compared fields, configuration identity, and primary
direct-method purity passed. This narrows the diagnosis without changing the
frozen gate.

## Published V4 release digests

GitHub release `paper-evidence-v4-461fc56` records these API-provided asset
digests:

| Release asset | SHA-256 |
| --- | --- |
| `paper_evidence_v4_bounded-461fc56.zip` | `6208114f0358fab815e0ac79fac73d6a9ff66ca33d8c7128b5ae77d591daa7a8` |
| bounded ZIP manifest | `5f1a7575d7daf8817945da7c8dd426fe9974b0afd60b19c28b3b2c1848807d33` |
| `primary_locked_test_v4-461fc56.zip` | `af84fba1edc1f84b20fca1bbdc26f7fbcc05c2e0d6f4b2dcb711525971f1f11e` |
| primary ZIP manifest | `bf53343f2608fef7b0ac95ad8ee57deed98cab98dfb872d15f96c662970992ec` |
| `release_asset_inventory.json` | `450008a2f80e1f31af64fd0a359244b6855b1df70a862c23b66135096e8e39d3` |

The release ZIPs are immutable publication evidence. Paper scripts consume the
bounded artifacts checked into `main`; they do not download, unpack, rewrite,
or recompute raw experiment outputs from those release ZIPs.

## Evidence registry

The V4 registry has 13 stable IDs:

`E_V4_PROTOCOL`, `E_V4_FRESH_LOCKED_TEST`,
`E_V4_PRIMARY_OBSERVED_EFFECT`, `E_V4_METHOD_PURITY`,
`E_V4_SAME_INFORMATION_FAILURE`, `E_V4_SAFETY`,
`E_V4_LAG_GUARDRAIL`, `E_V4_RUNTIME_FAILURE`,
`E_V4_HARMFUL_TRAJECTORIES`, `E_V4_SUBGROUPS`,
`E_V4_ORDINARY_CONTEXT`, `E_V4_ORACLE_CONTEXT`, and
`E_V4_ARTIFACT_INTEGRITY`.

Every entry records source path, commit, SHA-256, temporal class, test
visibility, causal/noncausal boundary, deployment boundary, allowed use,
forbidden interpretation, exact denominator, status, and publication-section
permissions. The registry keeps V3 and V4 paths, hashes, epochs, and
denominators separate.

## Generated and portable provenance

The bounded extraction and rendering sequence is:

```text
paper/scripts/extract_evidence.py
paper/scripts/generate_numbers.py
paper/scripts/generate_tables.py
paper/scripts/generate_figures.py
paper/scripts/build_generation_manifest.py
```

`extracted_evidence.json` now exposes V4 as a top-level object with the commit
chain, exactly-once/no-rerun/no-resume/no-V5 state, terminal classifications,
primary observed estimate, gate disposition, narrow same-information
diagnosis, method purity, safety, runtime, harmful cases, subgroup context,
ordinary-Ruckig incompleteness, oracle boundary, and artifact integrity.

The v1 source package carries the claim/evidence registries and logic lock,
extracted evidence, number/table/figure provenance, the generation manifest,
Appendices D--F, and the generated V4 table and figure. This repository-side
source ledger and `QA_REPORT.md` are intentionally excluded from the ZIP:
including a ledger that contains the final ZIP hash would create an impossible
self-hash recursion. The final ZIP/manifest/PDF identities are recorded only in
the repository-side QA ledger after packaging. The packager verifies member
order and hashes and compiles the extracted source in a fresh temporary
directory without repository access.

## Final package identities

- `dist/arxiv_stage_source_v1.zip`: `PENDING_FINAL_BUILD`
- `dist/arxiv_stage_source_v1.manifest.json`: `PENDING_FINAL_BUILD`
- `dist/arxiv_stage_source_v1.sha256`: `PENDING_FINAL_BUILD`
- clean-package PDF SHA-256: `PENDING_FINAL_BUILD`

The earlier `arxiv_stage_source_v0` artifacts are not overwritten or
represented as v1 outputs.
