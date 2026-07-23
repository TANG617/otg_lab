# Independent arXiv release QA

Date: 2026-07-23  
Audited package-content commit: recorded after the author-metadata and
review-integration removal payload is committed
Logic-lock SHA-256:
`a4577407bf4a625f5af25f08f9c74cb189034dbb24c5a8586a3348e164014981`

## Disposition

**Pass for Draft PR and internal review.**

- Open P0 findings: **0**
- Open P1 findings: **0**
- Open P2 findings: **2**
- Draft-PR blockers from release QA: **none**
- Public submission gate: explicit author approval plus confirmation of any
  applicable ORCID, funding, acknowledgement, and venue-policy fields

Author name, independent-researcher affiliation, contact, and the PDF author
field are supplied in `metadata.tex`. This review is limited to
source/package integrity, LaTeX portability, and release workflow; it does
not promote any evidence beyond the claim boundaries recorded by the logic
lock.

## Independent verification

| Area | Result |
|---|---|
| Logic and evidence gates | `check_logic`, lock verification, bounded evidence extraction, number/table/figure checks, generation-manifest check, and frozen-v3 immutability check pass |
| Claim, number, and citation gates | 17 annotated claim IDs accepted; 29 protected result values accepted; 16 bibliography entries present, cited, and represented in `main.bbl` |
| LaTeX log | 0 undefined references, 0 undefined citations, 0 duplicate labels, 0 missing files, 0 overfull boxes, and 0 LaTeX errors |
| Figures and tables | All 7 manuscript figures exist and are referenced; all 5 generated table fragments are referenced; the manuscript contains 7 table environments |
| Generated provenance | The generation manifest has 16 entries and matches every declared generated artifact |
| arXiv clean build | The standalone packager creates the ZIP first, extracts it to a fresh root with isolated HOME/TEXMF, verifies every member hash, and compiles without repository inputs |
| CI portability | The path-scoped workflow does not run v3/v4, explicitly installs `siunitx`, scalable EC fonts, and `rg`, and invokes the complete paper gate from a normal Git checkout |
| Frozen runtime extraction | The generator reads the committed `Q_V3_DIRECT_RUNTIME_PRIMARY` record in `logic/evidence_audit.json`, not an ignored release-bundle CSV |
| Cross-platform CSV parsing | Every registered CSV uses Pandas round-trip float precision; all 61 formatted numeric macros and units remain stable |
| Reproducible ZIP metadata | Every member uses timestamp `1980-01-01 00:00:00`, Unix origin, regular-file mode `0644`, deflate compression, and empty extra/comment fields |
| Git hygiene | `paper/build/` and `paper/dist/` are ignored; the archive contains no Git data, build output, cache, temporary file, raw experiment bundle, or nested release archive |

## arXiv package audit

The updated file count, ZIP SHA-256, clean-build PDF identity, and audited
package-content commit are recorded after the author-metadata and
review-integration removal payload is committed and packaged.

The source root contains `main.tex`, author metadata, macros, notation,
`references.bib`, `main.bbl`, all sections and appendices, generated numeric
and table source, and the 7 referenced vector figures. It excludes local PDF
builds, raw evidence, auxiliary LaTeX files, caches, backups, `.DS_Store`,
private paths, and high-confidence secrets. The adjacent manifest records a
portable `latexmk ... -outdir=build main.tex` command without a local absolute
path.

## Closed P1 findings

### P1-01 — Linux CI omitted the package that provides `siunitx`

The workflow now installs `texlive-science` explicitly.

### P1-02 — Release packaging was not independently portable or ZIP-first

The packager rejects tracked or untracked paper changes, records the Git
commit and logic-lock hash, creates the ZIP first, extracts it to a fresh
temporary root, verifies the exact inventory and every member hash, and
clean-builds that extraction.

### P1-03 — Evidence extraction depended on an ignored frozen runtime CSV

Runtime extraction now consumes the committed, independently recomputed
`Q_V3_DIRECT_RUNTIME_PRIMARY` record in `logic/evidence_audit.json`. The
record retains evidence ID `E_V3_RUNTIME`, the original raw-source path and
selector, and verification status `verified_raw_recomputation`. Reported
cycle count, p50, p90, p99, maximum, and deadline-miss rate match the locally
retained frozen benchmark row.

### P1-04 — Default CSV float parsing was not byte-stable across platforms

All registered CSV inputs now use `float_precision="round_trip"`. Compared
with the pre-fix release, `generated/numbers.tex`, generated tables and
figures, manuscript sources, `main.bbl`, and all 61 formatted macro
value/unit pairs are unchanged.

### P1-05 — Minimal Ubuntu TeX and utility dependencies were incomplete

The workflow explicitly installs the complete `cm-super` font set and
`ripgrep`, so PDFLaTeX microtype expansion and the local-path audit both run
on Ubuntu.

### P1-06 — Latexmk versions place a committed bibliography differently

The PDF gate copies `build/main.bbl` back when that file exists, and otherwise
requires the committed root `main.bbl` consumed directly by older Latexmk.
The independent citation gate checks all 16 bibliography entries and
citations before compilation.

### P1-07 — ZIP metadata inherited local source mtimes

The packager serializes every regular file with a fixed timestamp, Unix
origin, and mode `0644`, then rejects unexpected ZIP metadata before
extraction. Repeated builds with identical members are byte-reproducible.

## Open P2 findings

### P2-01 — Small runtime table occupies a float-only page

Page 18 contains only Table 6 and substantial whitespace. There is no
clipping, overlap, or cross-section float, so this is typographic polish
rather than a correctness or submission blocker.

Before public posting, either combine the two frozen-v3 tables, move the
runtime table next to its discussion, or move it to the experiment-details
appendix; then recheck float order and page count.

### P2-02 — Generated plots use embedded Type 3 fonts

All fonts are embedded and the PDFs compile correctly, but Matplotlib plot
text uses Type 3 fonts and lacks Unicode maps.

Before public posting, set Matplotlib `pdf.fonttype = 42`, regenerate the
7 figures and generation manifest, and repeat package QA.

## Final handoff condition

After committing the payload, create and independently verify the arXiv ZIP.
Record the payload commit and hashes in this report and the provenance
records. A final QA-record-only commit may then be followed by one manifest
regeneration so the ignored sidecar identifies the final branch HEAD without
changing the deterministic ZIP payload.
