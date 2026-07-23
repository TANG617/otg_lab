# Independent arXiv and Prism release QA

Date: 2026-07-23  
Audited source commit: `3e0e90bd580e9b26c4bdd2f52f64be28fb67bfa4`
Logic-lock SHA-256:
`a4577407bf4a625f5af25f08f9c74cb189034dbb24c5a8586a3348e164014981`

## Disposition

**Pass for Draft PR, internal review, and Prism milestone import.**

- Open P0 findings: **0**
- Open P1 findings: **0**
- Open P2 findings: **3**
- Draft-PR blockers from release QA: **none**
- Remaining public-release blocker: the intentional author, affiliation, and
  contact placeholders in `metadata.tex`

This disposition is limited to source/package integrity, LaTeX portability,
and release workflow. It does not promote synthetic, development-trace, or
post-freeze evidence beyond the claim boundaries recorded by the logic lock.

## Independent verification

The following checks were repeated independently after the release fixes were
committed:

| Area | Result |
|---|---|
| Logic and evidence gates | `check_logic`, lock verification, bounded evidence extraction, number/table/figure checks, generation-manifest check, and frozen-v3 immutability check all passed |
| Claim, number, and citation gates | 17 annotated claim IDs accepted; 29 protected result values accepted; 16 bibliography entries present, cited, and represented in `main.bbl` |
| LaTeX log | 0 undefined references, 0 undefined citations, 0 duplicate labels, 0 missing files, 0 overfull boxes, and 0 LaTeX errors |
| Local manuscript build | PDFLaTeX/BibTeX build produced 27 pages; root `main.bbl` is byte-identical to the build output |
| Figures and tables | All 7 manuscript figures exist and are referenced; all 5 generated table fragments are referenced; the manuscript contains 7 table environments |
| Generated provenance | The generation manifest has 16 entries and matches every generated artifact; no declared generated artifact is missing from the Prism bundle |
| arXiv clean build | Standalone packager created the ZIP first, extracted it to a fresh root with an isolated home/TEXMF, verified every member hash, and compiled without repository inputs |
| Prism round trip | All 55 manifest entries matched the canonical committed files; `compare_prism_export.py` accepted a fresh extraction |
| CI portability | The dedicated paper workflow is path-scoped, does not run v3/v4, installs `texlive-science` for `siunitx`, and invokes the complete paper gate from a normal Git checkout |
| Frozen runtime extraction | The generator reads the committed `Q_V3_DIRECT_RUNTIME_PRIMARY` record in `logic/evidence_audit.json`, not an ignored release-bundle CSV; its source ID remains `E_V3_RUNTIME`, and its values independently match the local frozen benchmark row |
| Cross-platform CSV parsing | Every registered CSV is parsed with Pandas round-trip float precision; compared with the pre-fix release, all 61 formatted numeric macros, generated tables, figures, manuscript source, and `main.bbl` are unchanged |
| Git hygiene | `paper/build/` and `paper/dist/` are ignored; neither archive contains Git data, build outputs, caches, temporary files, raw experiment bundles, or a nested release archive |

The independently reproduced clean-package PDF is 588,752 bytes with SHA-256
`4978745c05dded74213e0ad594e875279c0d492fcf7aac34ecf606461219bb7f`.
Its `.fls` contains no input from the repository checkout or a user TEXMF
tree.

## Package audit

### arXiv stage source

- File count: **33**
- ZIP SHA-256:
  `fe6ff88da6e34bd7fde272a7da6d98c01c8ad9cb2b8a17cf99ae35ae9513ca5b`
- Root contains `main.tex`, metadata/macros/notation, `references.bib`,
  `main.bbl`, all sections and appendices, generated numeric/table source, and
  the 7 referenced vector figures.
- The archive has no unused image, local PDF build, raw evidence, auxiliary
  LaTeX file, cache, backup, `.DS_Store`, private path, or high-confidence
  secret.
- The sidecar hash, manifest ZIP hash, actual ZIP hash, member inventory, and
  per-member hashes agree.
- The manifest records the audited commit and logic-lock hash and serializes a
  portable `latexmk ... -outdir=build main.tex` command without a local
  absolute path.

### Prism review import

- File count: **55**
- ZIP SHA-256:
  `6be307b97cb4ec1231b53056a8fb47b4606083eacb76792205f86507e2233466`
- Required manuscript source, bibliography, figures, generated tables,
  evidence/logic records, `PRISM_HANDOFF.md`, and review prompts are present.
- Git history, build/dist trees, scripts, raw experiment bundles, local paths,
  high-confidence secrets, and the former unreferenced correction figure are
  absent.
- The included generation manifest is internally complete: every one of its
  16 declared paths exists in the bundle with the declared hash.
- ZIP member order/inventory, per-member hashes, sidecar hash, source commit,
  logic-lock hash, clean-build result, and the canonical round-trip comparison
  all agree.

## Closed P1 findings

### P1-01 — Linux CI omitted the package that provides `siunitx`

**Initial risk:** The workflow used `--no-install-recommends` and did not
install `texlive-science`, so Ubuntu could fail on `siunitx.sty`.

**Fix verified:** `texlive-science` is explicitly installed in the paper
workflow.

### P1-02 — Release packaging was not independently portable or ZIP-first

**Initial risk:** The arXiv manifest exposed local executable/temp paths,
standalone execution could find `latexmk` without finding sibling
`pdflatex`, and the smoke build occurred before ZIP creation rather than from
an extracted archive.

**Fix verified:** Both packagers reject tracked or untracked paper changes,
record the Git commit and logic-lock hash, create the ZIP first, extract it to
a new temporary root, verify the exact member inventory and every hash, and
clean-build that extraction. The tool-bin directory is added to the child
PATH while the recorded command remains portable.

### P1-03 — Prism provenance and comparison were incomplete

**Initial risk:** The Prism package was not commit-bound, did not clean-build
or verify its archive, and the original comparison operated on the full paper
tree rather than the package manifest. Excluding the unused correction figure
then left one dangling entry in the included generation manifest.

**Fix verified:** Prism now uses the same clean-source and extracted-build
guards as arXiv, records commit/logic-lock/milestone metadata, compares against
the package manifest, omits the unused figure from canonical generation, and
ships a complete generation manifest.

### P1-04 — Evidence extraction depended on an ignored frozen runtime CSV

**Initial risk:** A developer checkout retained
`results/paper_evidence_v3/raw_runs/locked_test/runtime_benchmark.csv`, but a
normal GitHub Actions checkout did not. The paper's extraction check could
therefore pass locally and fail in CI despite unchanged frozen evidence.

**Fix verified:** Runtime extraction now consumes the single committed,
independently recomputed `Q_V3_DIRECT_RUNTIME_PRIMARY` record in
`logic/evidence_audit.json`. The record retains source ID `E_V3_RUNTIME`, the
original raw-source path and selector, verification status
`verified_raw_recomputation`, and the same reported cycle count, p50, p90,
p99, maximum, and deadline-miss rate. An independent comparison against the
locally retained frozen benchmark row was exact for all reported values; the
unused p99.9 diagnostic differed only by \(3.9\times10^{-12}\)
microseconds from floating-point representation. Static extraction and the
full release packages then passed without reading the ignored CSV.

### P1-05 — Default CSV float parsing was not byte-stable across platforms

**Initial risk:** Pandas' default high-precision CSV parser can resolve the
last few bits of a decimal float differently across libc/platform
combinations. The scientific values and displayed rounding were unchanged,
but the full-precision extracted-evidence JSON and downstream provenance
hashes could differ between a developer machine and GitHub Actions.

**Fix verified:** All registered CSV inputs now use
`float_precision="round_trip"`, preserving the IEEE value represented by each
source decimal string before the final JSON serialization. Static generation
and provenance checks pass at the audited commit. Against commit
`940c9a6ad6fae22702a9288a32167e01fe22ab8f`, an independent comparison found
no change in `generated/numbers.tex`, any generated table or figure, any
manuscript/appendix source, or `main.bbl`; all 61 macro `formatted_value` and
unit pairs are identical. Both clean-package builds also reproduce the same
27-page PDF SHA-256 as before.

## Open P2 findings

### P2-01 — Small runtime table occupies a float-only page

**Observation:** Page 18 contains only Table 6 and substantial whitespace.
There is no clipping, overlap, or cross-section float, so this is typographic
polish rather than a correctness or submission blocker.

**Exact fix:** Before a public arXiv posting, combine the two frozen-v3 tables
as subtables, move the compact runtime table adjacent to its discussion, or
move it to the experiment-details appendix; then recheck float order and page
count.

### P2-02 — Generated plots use embedded Type 3 fonts

**Observation:** All fonts are embedded and the PDFs compile correctly, but
Matplotlib plot text is represented with Type 3 fonts and lacks Unicode maps.

**Exact fix:** Set Matplotlib `pdf.fonttype = 42` (and `ps.fonttype = 42` if
PostScript output is ever added), regenerate the 7 figures, rebuild the
generation manifest, and rerun package QA.

### P2-03 — Prism comparison reports changed files but not unified text diffs

**Observation:** The comparison safely detects missing, changed, unexpected,
and stale-manifest files by hash, but a changed text file is reported only by
path. Reviewers need a second diff command to inspect the edit.

**Exact fix:** For changed UTF-8 text entries, emit a `difflib.unified_diff`
against the committed canonical file; retain hash-only reporting for binary
PDFs.

## Final handoff condition

This review file and the contemporaneous QA/provenance hash updates are
outside the two release ZIP allow-lists. After committing those Markdown
records, regenerate both ignored package manifests once so their
`source_commit` fields identify the final branch HEAD. The ZIP payload hashes
should remain unchanged unless an included source file changes.
