# Final QA report

Date: 2026-07-24
Branch: `paper/arxiv-stage-draft-v0`
Disposition: **pending final committed build and integrated adversarial QA**
Submission status: Draft PR only; public submission and merge are not
authorized.

## Audited identities

- Latest audited `main` / V4 merge commit:
  `c97e24dcfd6dd9146755235fa632e08932dc9a78`.
- Merge commit that brought latest `main` into the paper branch:
  `8faedae1fe18111ad0329259b5618c06edf6020b`.
- Final paper source commit: `PENDING_FINAL_COMMIT`.
- Logic-lock SHA-256: `PENDING_FINAL_BUILD`.
- Evidence-registry SHA-256: `PENDING_FINAL_BUILD`.
- Human evidence-inventory SHA-256: `PENDING_FINAL_BUILD`.
- Generation-manifest SHA-256: `PENDING_FINAL_BUILD`.
- Number-provenance manifest SHA-256: `PENDING_FINAL_BUILD`.
- V4 table-provenance manifest SHA-256: `PENDING_FINAL_BUILD`.
- V4 figure-provenance manifest SHA-256: `PENDING_FINAL_BUILD`.

## V4 immutable evidence audit

- Confirmation source:
  `461fc560461b0a4726cbabdb97b2dbd4dc305e0a`.
- Bounded result:
  `f49b4ef1cacf8228c5d243353184acb8a7d02311`.
- Report-only reporting-provenance repair:
  `8baece6b7051ccc231d9bb0362fd85e4aa5a94e5`.
- Report-only same-information aid:
  `b9301eaf36dc04f1abf662c42821eddfe8c3188a`.
- Release tag: `paper-evidence-v4-461fc56`.
- Protocol SHA-256:
  `baad38320593695a4c231f1802faa3a48b4a32b318da841fda5b1354cd8b770e`.
- Split-manifest SHA-256:
  `1727505734c8026ed18d87123d5d5a8c02e2f201a33ea786fbcde2c9ab398796`.
- Config-lock SHA-256:
  `d61b0f8596b04358c7bef6a1e43b6775b3dbb00020c2aca28d5d2cd4d9f6f3d3`.
- Root artifact-index SHA-256:
  `fd78eb559d039620ae1c6e06faac44ab6fc8dbff9208c05523b4efcab4a75a95`.
- Root artifact-index sidecar SHA-256:
  `96fbd8d2dc165beca47b40dd2ecb8eb46f6ae1be7f095974cc69e1ae2c9b9582`.
- Result-status SHA-256:
  `48c98a81a76129a0fc2dd913aabb28bc9312d31a76a4283b27bf1fea9431a34b`.
- Same-information reviewer-aid SHA-256:
  `dd9c89784766f85473159da6a5c0f072881e47828874fee7f17c7613cd86718f`.

The frozen result remains `failed_test_visible_frozen` /
`strongly_material` / `invalid_method_identity`. V4 was executed exactly once;
this paper update did not rerun or resume it. Same-test rerun and raw resume
remain prohibited. No V5 experiment was executed and no V5 result exists.

The same-information failure is preserved as five composite event-flag entries
among 42,072 aligned cycles, with `deadline_miss` as the only differing token.
The narrower diagnosis does not restore confirmation. Direct-method purity and
the scoped synthetic safety gate passed; lag noninferiority was not established
and the full instrumented Python runtime gate failed.

Published V4 release ZIP identities:

- bounded ZIP SHA-256:
  `6208114f0358fab815e0ac79fac73d6a9ff66ca33d8c7128b5ae77d591daa7a8`;
- primary locked-test ZIP SHA-256:
  `af84fba1edc1f84b20fca1bbdc26f7fbcc05c2e0d6f4b2dcb711525971f1f11e`;
- release-asset inventory SHA-256:
  `450008a2f80e1f31af64fd0a359244b6855b1df70a862c23b66135096e8e39d3`.

## Final manuscript artifact

- Local PDF SHA-256: `PENDING_FINAL_BUILD`.
- Local PDF bytes: `PENDING_FINAL_BUILD`.
- PDF pages: `PENDING_FINAL_BUILD`.
- Main-section word count: `PENDING_FINAL_BUILD`.
- Appendix word count: `PENDING_FINAL_BUILD`.
- Total words: `PENDING_FINAL_BUILD`.
- Referenced figures: `PENDING_FINAL_BUILD`.
- Referenced tables: `PENDING_FINAL_BUILD`.
- Bibliography entries/cited entries: `PENDING_FINAL_BUILD`.
- Registered claims and status counts: `PENDING_FINAL_BUILD`.
- Open P0/P1/P2 findings: `PENDING_FINAL_BUILD`.

These fields deliberately do not reuse v0 PDF metrics or hashes.

## Build and static QA

The canonical final sequence is:

```text
make -C paper logic-check
make -C paper evidence
make -C paper tables
make -C paper figures
make -C paper pdf
make -C paper check
make -C paper arxiv-source
```

Completed before the final committed build:

- V4 top-level bounded extraction and check: **pass**;
- V4 evidence-audit JSON parse, 13-ID registration, and stale-source removal:
  **pass**;
- V3 immutability: **pass**;
- V4 indexed-artifact and frozen-path immutability: **pass** (152 indexed
  artifacts);
- v1 package-script syntax and inventory resolution, including Appendix F,
  V4 generated tables, V4 figure, and portable provenance: **pass**;
- claim placement/boundary, protected-number, and citation checks: **pass**
  (24 annotated claim IDs, 53 protected generated values, 16/16 cited
  bibliography entries);
- Ruff for `paper/scripts` and repository diff whitespace check: **pass**;
- full integrated logic-lock/PDF/LaTeX-log QA:
  `PENDING_FINAL_BUILD`;
- local full PDF compile: `PENDING_FINAL_BUILD`;
- GitHub Actions Paper workflow: `PENDING_CI`.

The paper workflow is path-scoped to V3/V4 evidence and paper inputs. It runs
only extraction, generation, immutable-evidence checks, LaTeX, and packaging;
it does not run or resume V3/V4 and does not execute V5.

## Clean arXiv v1 source package

The committed-source packager must create the ZIP, extract it into a new
temporary directory, compare ordered members and every member hash, and compile
the extracted root with no repository access.

- `dist/arxiv_stage_source_v1.zip` file count:
  `PENDING_FINAL_BUILD`.
- arXiv v1 ZIP SHA-256: `PENDING_FINAL_BUILD`.
- arXiv v1 ZIP bytes: `PENDING_FINAL_BUILD`.
- Sidecar manifest SHA-256: `PENDING_FINAL_BUILD`.
- Clean-package PDF SHA-256: `PENDING_FINAL_BUILD`.
- Clean-package PDF bytes: `PENDING_FINAL_BUILD`.
- Clean-package compile: `PENDING_FINAL_BUILD`.

The v1 package does not overwrite or impersonate
`arxiv_stage_source_v0.zip`. It includes Appendix F, V4 tables/figure, and
portable claim/evidence/number/display provenance.

## Review disposition

The paper is not ready for final release until the main thread commits the
integrated source, runs the complete sequence above, replaces every
`PENDING_FINAL_BUILD` / `PENDING_FINAL_COMMIT` value, closes all P0/P1 findings,
and records the resulting CI run. PR #2 must remain Draft and unmerged.
