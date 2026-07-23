# arXiv stage paper

This directory is a self-contained LaTeX project for the stage draft
*From Position Samples to Executable Commands: Timing and Feasibility for
Jerk-Limited Reference Following*. The canonical manuscript source is
`main.tex`, `sections/*.tex`, and `appendix/*.tex`. The Markdown files under
`logic/` record the locked argument, claim/evidence boundaries, notation, and
review decisions; they are not a second manuscript.

The project intentionally reports only registered current or frozen evidence.
Its build does not rerun the frozen v3 experiment and must never invoke
`run_paper_evidence_v3.py confirm` or a v4 experiment. Evidence extraction and
generation scripts may read repository evidence, but the packaged LaTeX source
is self-contained and does not access files above this directory.

## Toolchain

The supported build uses PDFLaTeX, BibTeX, `natbib`, and `latexmk`. A current
TeX Live installation, Python 3, and `uv` are required for the default
commands. Set `PYTHON=python3` when invoking Make if the active Python
environment already contains the locked project dependencies. No shell
escape, network access, downloaded fonts, SVG conversion, or
publisher-specific class is used.

Run commands from this directory:

```sh
make logic-check
make evidence
make figures
make tables
make pdf
make check
```

`make pdf` writes `build/main.pdf` and refreshes the root `main.bbl` for source
submission. `make static-check` runs source, claim, citation, number,
provenance, immutable-v3, and local-path checks without compiling the
manuscript. `make check` adds a full PDF build, LaTeX-log QA, and a
clean-build arXiv package.

Generated results are never copied into prose by hand:

- `generated/numbers.tex` contains result macros with machine-readable
  provenance.
- `generated/tables/*.tex` contains source-backed table fragments.
- `figures/generated/*.pdf` contains generated or verified vector figures.
- `generated/generation_manifest.json` inventories generated outputs.

Regenerate these artifacts only through the scripts exposed by the Makefile.
Do not edit frozen evidence or generated fragments manually.

## Packages

```sh
make arxiv-source
```

The arXiv target creates `dist/arxiv_stage_source_v0.zip`, its manifest, and
SHA-256 file, then verifies compilation from a fresh temporary extraction. The
ZIP is a stage-draft source package, not a submitted or accepted manuscript.

## Publication metadata

`metadata.tex` contains the author-supplied name, affiliation, contact, and
PDF-author fields. Any later ORCID, funding, acknowledgement, or identity
change must be supplied and reviewed by the author; the build does not infer
publication metadata.
