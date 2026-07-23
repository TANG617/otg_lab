# Final QA report

Date: 2026-07-23  
Branch: `paper/arxiv-stage-draft-v0`  
Disposition: **pass for internal review, Prism import, and Draft PR**  
Manual blocker: author, affiliation, and contact metadata must be supplied
before public submission.

## Audited identities

- Latest audited source-repository commit:
  `1d5cba1b3e8072bcf2a9a40492e044d2af4cf9fe`.
- Package-content commit used for the clean release checks, before the
  contemporaneous QA-record-only commit:
  `705b423b7e948e6be4fba03d3813226c052dd082`.
- Logic-lock SHA-256:
  `a4577407bf4a625f5af25f08f9c74cb189034dbb24c5a8586a3348e164014981`.
- Evidence registry SHA-256:
  `2f79d066e1f6b4b563319ebc0740741b26f5d1669ce2bb9a34ed506d61baec6e`.
- Human-readable evidence inventory SHA-256:
  `3a4a6a0da0c28ee7db0c9816cfa081293f08303549e0fddc9c0254694f445283`.
- Generation manifest SHA-256:
  `75c16af3adc36e64408ad42c049528706d474ae65773d5d86268413014de7bf3`.
- Number-provenance manifest SHA-256:
  `52c0fe2e842a269085d0928892914341c9c53619e52e775476ffafa9a9c2a3d6`.

## Manuscript artifact

- Local PDF: 27 US-letter pages, 588,763 bytes, SHA-256
  `fd4025e5b3a9aa5c86b2adee75966971eaeec54ac818639616872622a20fb100`.
- TeXcount for the nine main sections: 6,193 prose words, 150 header words,
  and 437 caption/outside-text words; sum count 6,915.
- TeXcount for the five appendices: 1,745 prose words, 65 header words, and
  17 caption/outside-text words; sum count 1,883.
- Referenced displays: 7 figures and 7 tables. The canonical generator emits
  exactly those 7 figure PDFs.
- Bibliography: 16 entries; all 16 are cited.
- Generated numeric layer: 61 macros with per-macro provenance; the manuscript
  checker protects 29 reported result values against hand-copying.

## Build and static QA

The canonical build used TeX Live 2026, pdfTeX
`3.141592653-2.6-1.40.29`, Latexmk 4.88, BibTeX, and `plainnat`:

```text
make -C paper check PYTHON="../.venv/bin/python" \
  LATEXMK="latexmk"
```

The release manifests serialize the portable clean-build command as
`latexmk -norc -pdf -interaction=nonstopmode -halt-on-error
-file-line-error -outdir=build main.tex`; they contain no local absolute path.

Results:

- logic, logic-lock, evidence extraction, generated numbers, generated tables,
  generated figures, generation-manifest, frozen-v3, claim-placement,
  citation, local-path, and LaTeX-log gates: pass;
- claim annotations: 17 known IDs, with every file/subsection checked against
  the registry's `allowed_sections`;
- claim statuses: 8 `confirmed_current`, 2 `confirmed_frozen_scope`,
  3 `negative_current`, 1 `exploratory_confounded`, 2 `not_evaluated`, and
  1 `external_blocker`;
- registered evidence sources: 12;
- undefined references: 0; undefined citations: 0; duplicate labels: 0;
  missing files: 0; overfull boxes: 0; LaTeX errors: 0;
- underfull boxes: 8, confined to the compact related-work taxonomy table and
  accepted as non-blocking typography;
- forbidden/unsupported claim phrases: 0; forbidden local paths in release
  source: 0;
- Ruff: full repository pass;
- repository test suite: 359 passed, 31 warnings;
- GitHub Actions: the path-scoped Paper workflow runs the locked extraction,
  LaTeX, package, and artifact-upload gates without rerunning v3 or v4;
  Paper run `29991314799` and CI run `29991314818` passed at the audited
  package-content commit;
- original frozen-v3 SHA-256 inventory: every entry rechecked successfully;
  frozen-path working-tree diff: none;
- no Phase A rerun, no v3 rerun, and no v4 experiment.

## Clean release packages

The packagers first created each ZIP, then extracted it to a new temporary
directory, compared the member list and every member hash, and compiled the
extracted root.

- arXiv stage source: 33 files,
  SHA-256 `a725c89419aef4faa4ef46a2bb47e8cbbdc9d73e8de1a9a1b8ef86fd0d03b2b0`.
- Prism review import: 55 files,
  SHA-256 `c816a6d71b2f4e34c74960c9fd7571eeb4371819e876d2e5dc9297ea4c5d025b`.
- Both extracted packages produced a 588,752-byte PDF with SHA-256
  `4978745c05dded74213e0ad594e875279c0d492fcf7aac34ecf606461219bb7f`.
- Both final sidecar manifests are regenerated from the final branch HEAD and
  record the same logic-lock hash.

## Review disposition

The adversarial manuscript review found no P0 issue. Its three P1 issues were
closed: safety/runtime populations and zero-event denominators are separated,
all claim locations are machine-checked, and Results floats no longer cross
into Discussion. Release QA has no remaining Draft-PR blocker. The placeholders
in `metadata.tex` are intentional for this stage but block public submission.
