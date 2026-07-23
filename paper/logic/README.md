# Paper logic layer

This directory records the scientific decisions that constrain the arXiv
stage draft. It is a planning and audit layer, not a second manuscript. The
formal manuscript source begins with `paper/main.tex` and the files under
`paper/sections/` and `paper/appendix/`.

## Purpose

The logic layer fixes, before prose is drafted:

- the paper question, scope, title, and contribution boundary;
- stable claim identifiers and the evidence allowed to support each claim;
- the argument order, notation, clock semantics, method names, and figure/table
  roles;
- negative results, confounds, external blockers, and prohibited
  interpretations;
- decisions that would require a new logic lock if changed.

It deliberately does not contain full section prose and must not be converted
wholesale into LaTeX.

## Files and ownership

| File | Role |
|---|---|
| `00_paper_charter.md` | Paper purpose, audience, contribution boundary, and selected title |
| `01_claim_evidence_matrix.md` | Human-readable claim registry and evidence gates |
| `02_argument_outline.md` | Section-by-section reasoning contract |
| `03_notation_and_timing.md` | Canonical symbols, clocks, state semantics, and terminology |
| `04_figures_and_tables_plan.md` | Scientific role and evidence boundary for every planned display |
| `05_literature_matrix.md` | Verified literature roles; maintained separately by the literature auditor |
| `06_scope_and_limitations.md` | Scientific scope and required limitations |
| `07_writing_style.md` | Manuscript language and typesetting rules |
| `08_open_questions.md` | Deferred questions that do not block the stage draft |
| `evidence_sources.yaml` | Machine-readable evidence-source inventory; maintained by the evidence auditor |
| `evidence_inventory.md` | Human-readable evidence-source audit; maintained by the evidence auditor |
| `claims.yaml` | Machine-readable canonical claims and section permissions |
| `decision_log.md` | Decisions affecting title, claims, scope, terminology, or evidence classification |
| `logic_lock.json` | Generated integrity lock; never hand-edited |

## Precedence and change control

When descriptions differ, the following precedence applies:

1. frozen source artifacts and their checksums;
2. post-review reclassification records;
3. `evidence_sources.yaml`;
4. `claims.yaml`;
5. this Markdown planning layer;
6. manuscript prose.

The frozen v3 artifacts remain byte-preserved. A change in interpretation does
not authorize a change to frozen files. Any later change to the selected title,
paper scope, canonical claim wording, claim status, allowed evidence, or
abstract/conclusion permission must be entered in `decision_log.md` and must
regenerate `logic_lock.json`.

## Scientific guardrails

- The paper is about causal timing, state definition, target feasibility,
  method identity, and constrained command execution for a position-only
  moving reference.
- It is not a confirmed PVA-over-P or PVA-over-PV performance paper.
- Analytic truth, offline preview, causal estimates, synthetic truth, the
  development CSV, frozen v3, and post-freeze regressions are different
  information/evidence conditions and must remain visibly distinct.
- A target diagnostic is not a measured robot quantity. A command profile is
  not a hardware trajectory. A simulated plant is not a robot.
- The frozen 77.38% mixed-baseline result is exploratory and confounded; it is
  excluded from the title, abstract, contributions, and conclusion.
- No v4, hardware, HIL, Ruckig Pro `Trackig`, or independent real locked test is
  implied by this draft.

## Logic-lock readiness

The logic layer is ready to lock only when:

- every claim in `claims.yaml` has a known evidence classification and explicit
  section permissions;
- every empirical quantity maps to an audited evidence source;
- all planned figures and tables state their forbidden interpretations;
- an adversarial reviewer has checked timing, causality, method identity,
  negative results, and the v3 reclassification;
- the generated lock records any unresolved external blocker without treating
  it as a blocker to drafting.

