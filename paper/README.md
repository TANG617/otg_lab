# Paper build and evidence contract

This directory contains the complete provisional English manuscript for
"Terminal-State Mismatch Causes Stop-and-Go in Jerk-Limited Online Trajectory
Generation." The current PDF is intentionally not release-ready: its evidence
files are hash-pinned, but their source manifests report dirty git worktrees.

## Rebuild the review PDF

From this directory:

```bash
make draft
make check
```

`make draft` freezes the declared evidence, regenerates all numbers, tables, and
vector figures, then compiles with both TeX Live/pdfLaTeX and Tectonic. The
review PDF is copied to
`../output/pdf/terminal_state_mismatch_provisional.pdf`.

`make arxiv-dry-run` additionally creates a whitelist-only review archive,
extracts it into an empty build directory, and compiles it there. The archive
is named `review-source-dry-run.tar.gz`; it is not an arXiv release package.

## Evidence profiles

`evidence/provisional.yaml` is the only active profile. It pins every run ID,
specification hash, git state, source path, and SHA256 used by C1--C13. The
freezing script refuses changed or missing sources. Artifact generation reads
only `evidence/frozen/provisional`, never a `latest` directory.

After clean reruns, copy `evidence/release.example.yaml` to `release.yaml` and
fill it with the clean E11--E17 and A03--A06 records using the same schema.
Release validation requires clean provenance, `release_ready: true`, and a
separate `metadata.tex` with real author and repository information.

## Metadata and publication

Draft author information is isolated in `metadata-draft.tex`. A release build
requires `metadata.tex`; use `metadata.example.tex` only as a field guide. The
build does not choose a code or data license and does not publish or push a
remote repository. Those are author decisions required before public release.
At release time, copy `CITATION.example.cff` to `CITATION.cff`, replace its
author, version, and repository fields from the same verified metadata record
used by `metadata.tex`, and validate it with GitHub's CFF validator. The example
is deliberately not treated as publishable citation metadata.

The formal `make arxiv` target is expected to fail for the provisional profile.
It packages only TeX sources, the generated BBL, generated PDF figures, and
generated TeX tables. Build logs, auxiliary files, the compiled manuscript,
SVG files, hidden directories, and shell-escape workflows are excluded.
