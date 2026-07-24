# Independent LaTeX, arXiv, and release QA

Date: 2026-07-24
Audited package-content commit:
`d4d867caf8ddec7ec0abe627dabd7598d186632e`
Logic-lock SHA-256:
`7dc1c393ff7824855d3f01de15df6e3287f3dc5f362a50121cf73d8a4da6c518`

## Disposition

**Pass for the Draft PR and internal review.**

- Open P0 findings: **0**
- Open P1 findings: **0**
- Open P2 findings: **0**
- Draft-PR blockers from release QA: **none**
- Public-submission gate: explicit author approval plus confirmation of any
  applicable ORCID, funding, acknowledgement, disclosure, and venue-policy
  fields

The author name, independent-researcher affiliation, contact, and PDF author
field are supplied in `metadata.tex`. This review covers source/package
integrity, LaTeX portability, PDF layout, and release workflow. It does not
promote the frozen V4 observation beyond the non-confirmatory boundary fixed
by the logic lock.

## Independent verification

| Area | Final result |
|---|---|
| Logic and evidence gates | Logic validation and lock verification passed; frozen V3 remained immutable; frozen V4 verified 152 indexed artifacts with status `failed_test_visible_frozen`; no frozen experiment was run, resumed, repaired, or modified |
| Claim, number, and citation gates | 24 annotated claim IDs accepted; 53 protected generated values accepted; 16 bibliography entries present and cited |
| Generated artifacts | Number, table, figure, and generation-manifest checks passed; the generator verified 8 figure PDFs plus the V4 figure provenance sidecar, 11 generated table fragments, and a 25-file generation manifest |
| LaTeX log | 0 undefined references, 0 undefined citations, 0 duplicate labels, 0 missing files, 0 overfull boxes, and 0 LaTeX errors |
| PDF metadata | Title and author are populated; 33 US-letter pages; unencrypted; no JavaScript |
| Fonts | 45 font records; every font is embedded, subset, and Unicode-mapped; 0 Type 3 fonts |
| Visual QA | All 33 pages rendered and reviewed; no clipping, overlap, unexpected blank page, broken glyph, unreadable label, or displaced float |
| V4 presentation | Main V4 gate table and paired-difference figure on page 20 are legible and uncropped; all five Appendix F tables on pages 29--31 are legible and preserve failed gates, adverse cases, and denominators |
| arXiv clean build | The v1 ZIP was extracted to a fresh root, every member hash was verified, and `latexmk -norc -pdf -interaction=nonstopmode -halt-on-error -file-line-error -outdir=build main.tex` succeeded without repository inputs |
| Inventory and paths | Independent audit matched all 49 ZIP members to manifest byte counts and SHA-256 values; no absolute member, traversal member, or local absolute path was found |
| Version preservation | `arxiv_stage_source_v1.zip` was created without deleting or overwriting the earlier v0 ZIP |

## Final arXiv v1 package

- Source file count: **49**
- ZIP bytes: **247,268**
- ZIP SHA-256:
  `45fe715c9deb739e5dd365fee93c22b1059830d7eb4933d366d0b98274c386c7`
- Extracted clean-build PDF: **753,650 bytes**, **33 pages**, SHA-256
  `4d4169d7ab30f2534a5f1ad2956531a2f965c1858dd314763e36cd1d16dc9b97`
- Repository clean-build PDF: **753,662 bytes**, **33 pages**, SHA-256
  `184c494185c8ae3337a25853b93afc7a65ebb2b243880d1d1c8f760321eb0e1b`
- Preserved v0 ZIP SHA-256:
  `e317df3a1d396826ce74c13d8aa3f62cbc8592679a6a2fe94a217639d074ec1e`

The clean-build and repository PDFs have the same page count and inspected
layout; their byte hashes differ because PDF build metadata is created in
separate compilations. The v1 archive contains the modular manuscript,
Appendices A--F, bibliography material, generated numbers and tables, all
referenced vector figures, and portable claim/evidence/number/display
provenance. It excludes repository evidence trees, local build products, raw
experiment bundles, caches, backups, Git data, nested archives, and local
paths.

## Findings closed in this QA pass

### P1-01 -- Post-merge number provenance was stale

The first complete gate found that `number_provenance.json` still named the
pre-integration generator commit. The number values themselves were unchanged.
The provenance and generation manifest were regenerated against the integrated
source and committed before final package QA.

### P1-02 -- Derivative-timing panel labels overlapped plot borders

The first 33-page render showed that the second and third panel titles on page
7 touched the preceding plot border and the causal-status text was crossed by
the horizontal axis. The generator now adds vertical panel spacing and opaque
label backgrounds. The regenerated page was reviewed at higher resolution and
contains no overlap.

### P2-01 -- Matplotlib figures used Type 3 fonts

The figure generator now emits embedded CID TrueType fonts with Unicode maps.
All eight figure PDFs, the V4 figure provenance sidecar, and the generation
manifest were regenerated. Final `pdffonts` inspection reports 0 Type 3 fonts
and no missing embedding, subsetting, or Unicode mapping.

### P2-02 -- Earlier runtime table occupied a float-only page

The integrated manuscript no longer has that layout: page 19 contains the V3
figure, runtime table, interpretation text, and the next subsection. No
float-only or unexpected blank page remains.

### P1-03 -- Paper CI referenced local-only V4 bundle files

The first remote Paper run found two inputs that existed in the complete local
bundle but were absent from a clean Git checkout: the raw constraint-audit CSV
in the safety registry and the raw locked-run metadata in the V4 evidence-chain
table generator. The registry now consumes the tracked, hashed handoff safety
summary while retaining the upstream raw digest as publication provenance; the
table generator binds the tracked root index and exactly-once evidence
registration. A detached clean worktree passes every static gate. The portable
immutability check verifies the root index, sidecar, status, and every available
tracked artifact; the release QA additionally passed `--require-complete` with
all 152/152 local bundle artifacts byte-verified. The affected Appendix F page
was rerendered and inspected after regeneration.

## Final handoff

The audited v1 payload is tied to commit
`d4d867caf8ddec7ec0abe627dabd7598d186632e`. This QA record is intentionally
outside the arXiv payload, so a later QA-record-only commit does not change the
audited ZIP. The release remains a staged draft and has not been submitted.
