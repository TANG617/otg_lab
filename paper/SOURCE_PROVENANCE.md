# Source provenance

## Repository anchors

- Repository: `TANG617/otg_lab`
- Latest source-repository commit audited for paper evidence:
  `1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe`
- Paper package-content commit audited before the contemporaneous
  QA-record-only changes:
  `223f2d77b2dbc82fcc467c6e4aace63620bd9227`
- Paper branch: `paper/arxiv-stage-draft-v0`
- Logic lock:
  `paper/logic/logic_lock.json`,
  SHA-256 `a4577407bf4a625f5af25f08f9c74cb189034dbb24c5a8586a3348e164014981`

The logic lock binds the charter, claim registry, evidence registry, argument
outline, notation/timing contract, scope, literature matrix, decision log, and
adversarial logic review. `claims.yaml` is the machine-readable wording and
section-permission source of truth.

## Evidence registry

`paper/logic/evidence_sources.yaml` registers 12 sources by stable evidence ID,
repository-relative paths, hashes, temporal class, permitted uses, and
forbidden uses. Its SHA-256 is
`2f79d066e1f6b4b563319ebc0740741b26f5d1669ce2bb9a34ed506d61baec6e`.
The accompanying human inventory has SHA-256
`3a4a6a0da0c28ee7db0c9816cfa081293f08303549e0fddc9c0254694f445283`.

The registry separates:

- current Phase A analytic tracking, derivative, oracle, limit, and
  development-CSV evidence;
- frozen v3 direct-command, runtime, artifact-integrity, and confounded
  historical-comparison evidence;
- current post-freeze profile-aware infrastructure and the one permitted
  Phase A P-only compatibility regression.

These classes are not pooled. Development evidence is not promoted to locked
real-stream evidence, and post-freeze implementation checks do not replace
frozen experiment outputs.

The full frozen runtime CSV remains part of the registered release bundle.
For reproducible extraction in an ordinary Git checkout and CI, the paper
pipeline consumes the exact primary direct-method row independently recomputed
and committed in `logic/evidence_audit.json`; that audit records its selector,
source ID, verification status, and the original frozen-source relationship.

## Generated manuscript layer

The bounded extraction and rendering sequence is:

```text
paper/scripts/extract_evidence.py
paper/scripts/generate_numbers.py
paper/scripts/generate_tables.py
paper/scripts/generate_figures.py
paper/scripts/build_generation_manifest.py
```

The generated layer contains 61 numeric macros, 5 table fragments, and
7 vector PDFs. The manuscript references all 7 PDFs.
`number_provenance.json` records, for every macro, evidence IDs, source paths
and hashes, selector/calculation, units, raw value, formatted value, rounding
rule, and the last generator-script commit. Static checks reject stale output
and protected numeric literals copied directly into prose.

## Frozen-artifact boundary

The full original `results/paper_evidence_v3/` SHA-256 inventory was captured
before paper work and rechecked after generation; every entry matched.
`verify_v3_immutability.py` also reports no frozen-path working-tree diff.
Paper scripts read frozen evidence but never rewrite it.

- Phase A experiments rerun for this paper: no.
- v3 rerun: no.
- v4 executed: no.
- Post-freeze compatibility regression: yes, limited to the already registered
  Phase A P-only ordinary-Ruckig compatibility check.

## Release artifacts

- `dist/arxiv_stage_source_v0.zip`: 33 files,
  SHA-256 `5228fa6b9baa987d44f4781c686fbd55032f81516c6fd04e587be9de54635f09`.
- `dist/prism_import_v0.zip`: 55 files,
  SHA-256 `5b3ba0b6b481e350ce2d9806dd5c44583466a7cdbbbfcd536e250518621ffd05`.

Each adjacent manifest records the source commit, logic-lock hash, member
inventory, member hashes, ZIP hash, and portable clean-build result. Each
packager validates only after ZIP creation by extracting into a fresh
temporary root, verifying the member inventory and hashes, and compiling that
extracted source. The Prism milestone is `arxiv-stage-draft-v0`; Git remains
the canonical source for all future edits.
