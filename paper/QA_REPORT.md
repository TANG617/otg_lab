# Final QA report

Date: 2026-07-24
Branch: `paper/arxiv-stage-draft-v0`
Disposition: **pass for internal review and Draft PR**
Submission status: Draft PR only; public submission and merge are not
authorized.

## Audited identities

- Latest audited `main` / V4 merge commit:
  `c97e24dcfd6dd9146755235fa632e08932dc9a78`.
- Merge commit that brought latest `main` into the paper branch:
  `8faedae1fe18111ad0329259b5618c06edf6020b`.
- Audited package-content source commit:
  `d4d867caf8ddec7ec0abe627dabd7598d186632e`.
- Logic-lock SHA-256:
  `7dc1c393ff7824855d3f01de15df6e3287f3dc5f362a50121cf73d8a4da6c518`.
- Evidence-registry SHA-256:
  `41042f88dbf9e40dfc87ed6194325278f6ff2e25fc4c04b9f80036c8d3a63c1b`.
- Human evidence-inventory SHA-256:
  `1df9941c3a7460bcbe48430f25b7991acd90090bdcf01a576b5d7ce448a36862`.
- Generation-manifest SHA-256:
  `f6a255628d1093f9e6cd4dfd85850ee315b08cbbc8af01f3250e2f297f2a7ad1`.
- Number-provenance manifest SHA-256:
  `10bb27f7d3b162f96e26caee0dd91c8ab488994f7175c54c3bdf471953a103fd`.
- V4 table-provenance manifest SHA-256:
  `4b08558aad8e0ff6b09beb48604243b369308dd370132d10a854eb492074ee36`.
- V4 figure-provenance manifest SHA-256:
  `34148e43d30046367a1f20f8152fbd38b397fc11009be4d4be3004b437bd905c`.

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

- Local PDF SHA-256:
  `184c494185c8ae3337a25853b93afc7a65ebb2b243880d1d1c8f760321eb0e1b`.
- Local PDF bytes: `753,662`.
- PDF pages: `33` US-letter pages.
- Main-section TeXcount: 7,329 prose + 172 headers + 513
  caption/outside-text words = `8,014`.
- Appendix TeXcount: 2,421 prose + 94 headers + 148
  caption/outside-text words = `2,663`.
- Total TeXcount: `10,677`.
- Abstract TeXcount: `237` prose words.
- Referenced figures: `8`.
- Referenced tables: `13`.
- Bibliography entries/cited entries: `16/16`.
- Registered claims: `24` (8 `confirmed_current`, 6
  `confirmed_frozen_scope`, 4 `nonconfirmatory_frozen`, 3
  `negative_current`, and one each `exploratory_confounded`,
  `external_blocker`, and `not_evaluated`).
- Open P0/P1/P2 findings: `0/0/0`.

These are v1 identities and do not reuse v0 PDF metrics or hashes.

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
- full integrated logic-lock/PDF/LaTeX-log QA: **pass**;
- local full PDF compile: **pass**;
- independent 33-page visual and font audit: **pass**;
- GitHub Actions Paper workflow: configured as the authoritative remote PR
  check; its live result is retained by GitHub rather than copied into this
  source ledger.

The paper workflow is path-scoped to V3/V4 evidence and paper inputs. It runs
only extraction, generation, immutable-evidence checks, LaTeX, and packaging;
it does not run or resume V3/V4 and does not execute V5.

## Clean arXiv v1 source package

The committed-source packager must create the ZIP, extract it into a new
temporary directory, compare ordered members and every member hash, and compile
the extracted root with no repository access.

- `dist/arxiv_stage_source_v1.zip` file count: `49`.
- arXiv v1 ZIP SHA-256:
  `45fe715c9deb739e5dd365fee93c22b1059830d7eb4933d366d0b98274c386c7`.
- arXiv v1 ZIP bytes: `247,268`.
- Sidecar manifest SHA-256:
  `4611440267db5c841e8c4ea2b52a80af4edf166286b97de1941a80572d8dc738`.
- Sidecar checksum-file SHA-256:
  `24107c5245128b6a567b1abae5eb8245bc4a934c288c2571712466a9578037d9`.
- Clean-package PDF SHA-256:
  `4d4169d7ab30f2534a5f1ad2956531a2f965c1858dd314763e36cd1d16dc9b97`.
- Clean-package PDF bytes: `753,650`.
- Clean-package PDF pages: `33`.
- Clean-package compile and independent member/hash/path audit: **pass**.

The v1 package does not overwrite or impersonate
`arxiv_stage_source_v0.zip`. It includes Appendix F, V4 tables/figure, and
portable claim/evidence/number/display provenance.

## Review disposition

The integrated source, adversarial review, clean package, visual inspection,
and release QA are complete with no open P0/P1/P2 finding. Public submission
still requires explicit author approval and confirmation of any applicable
ORCID, funding, acknowledgement, disclosure, and venue-policy fields. PR #2
must remain Draft and unmerged.
