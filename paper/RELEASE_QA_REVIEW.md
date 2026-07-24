# Independent LaTeX, arXiv, and release QA

Date: 2026-07-24
Audited package-content commit:
`db67b1ed7ca3b2196ecd0d52ac32a9a4deb9c745`
Logic-lock SHA-256:
`e9e19edde3a7ed194f727b694224e286dddd06ae82fc0d08a1e432cbdfb35814`

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
- ZIP bytes: **247,314**
- ZIP SHA-256:
  `d1a045aeafb99035556937120fef5950fa4ad8019e46b7e5363e4ff620f59c1c`
- Extracted clean-build PDF: **753,685 bytes**, **33 pages**, SHA-256
  `f9ccfc9b41f605d8d9e3f9d7a1f134ace88006554ccfdc82f67b85f27330327a`
- Repository clean-build PDF: **753,697 bytes**, **33 pages**, SHA-256
  `f85103802fa760c536e0f5919cc93ba3d408ceed374b88a55a72dddaf142cb49`
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

## Final handoff

The audited v1 payload is tied to commit
`db67b1ed7ca3b2196ecd0d52ac32a9a4deb9c745`. This QA record is intentionally
outside the arXiv payload, so a later QA-record-only commit does not change the
audited ZIP. The release remains a staged draft and has not been submitted.
