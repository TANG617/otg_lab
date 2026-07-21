# Paper evidence v1: technical artifact index

This directory contains bounded, generated evidence artifacts. Raw run bundles are intentionally external to the committed result layer and are referenced by SHA-256 through `artifact_index.json`.

## Validation contract

- Independently verified raw bundles: 10
- Bundle checks: run manifest, schema hooks, artifact index coverage, SHA-256 registry, CSV row counts, and independent metric recomputation
- Locked-test denominator: 120 whole trajectories
- Paired bootstrap: 10,000 trajectory resamples; candidate minus baseline; Holm adjustment over the predeclared secondary family
- Incomplete pairs: rejected in formal mode; no complete-case deletion
- Representative trace ranking method: `one_step_governed_pva_direct`
- Strict Section 16 criteria: 18; failed or unavailable: 7

## Artifact layout

- `summaries/`: bounded figure inputs, acceptance, fallback, layer evidence, frequency/event diagnostics, and QA
- `statistics/`: 8 paired comparisons and 96 method intervals
- `figures/`: 14 deterministic PNG/SVG categories
- `manifests/`: raw validation inventory, chart map, and statistical design
- `FAILURE_ANALYSIS.md`: completion/failure/fallback status
- `artifact_index.json`: SHA-256 inventory and raw-bundle roots of trust
- `artifact_index.sha256`: digest of the root index

All physical tracking plots preserve `target[k] -> output[k+1]` timing; offline oracle studies remain separately labelled in their raw bundles.
