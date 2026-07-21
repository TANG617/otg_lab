# Reproducibility

## Environments

The primary legacy-compatible environment is Python 3.9 with Ruckig 0.17.3 and exact package versions in `pyproject.toml`/`uv.lock`. The isolated current Community compatibility environment uses Ruckig 0.19.4 from `environments/community-requirements.lock.txt`.

```bash
uv sync --extra dev --frozen --python 3.9
uv run pytest -q

uv venv .venv-community --python 3.9
uv pip install --python .venv-community/bin/python \
  -r environments/community-requirements.lock.txt
```

Do not reuse either environment for the other Ruckig version. The compatibility runner records the interpreter and package version in separate manifests.

## Standard workflow

```bash
# Fast development-only pipeline and schema check.
uv run python run_paper_evidence.py smoke --config configs/development.yaml

# Train/validation-only selection; test identities are rejected by the APIs.
# This deliberately writes outside the formal raw_runs tree.
uv run python run_paper_evidence.py selection-validation \
  --config configs/validation.yaml

# Inspect the exact object that must be committed as the lock.
uv run python -m json.tool \
  runs/paper_evidence_v1/selection-validation/locked_selection.json

# After the lock steps below, this is the sole formal confirmation entrypoint.
uv run python run_paper_evidence.py confirm

# Independent recomputation and checksum verification.
uv run python run_paper_evidence.py qa \
  --results results/paper_evidence_v1/raw_runs
```

`selection-validation` is the only pre-lock selection command. Its default output is `runs/paper_evidence_v1/selection-validation/`, not `results/paper_evidence_v1/raw_runs/validation/`. The latter name is reserved for the clean confirmation run. Neither command overwrites an existing output directory.

Copy the complete `locked_selection.json` object verbatim into:

- `config_lock.json` as `locked_selection`, with `locked: true` and `selection_status: locked_after_validation`;
- `configs/locked_test_v1.yaml`;
- `configs/acceleration.yaml`;
- `configs/governor_infeasible.yaml`;
- `configs/robustness.yaml`;
- `configs/rate_study.yaml`;
- `configs/multidof_plant.yaml`.

Do not retype or round numeric values. The CLI compares canonical JSON, including numeric types, so `20` and `20.0` are deliberately different locks. Commit these files, then require `git status --short` to be empty.

Before `confirm`, all ten formal raw bundle paths and all managed final-report paths must be absent. The command performs a read-only preflight and refuses to delete, overwrite, or resume anything. If an earlier attempt exists, inspect it and explicitly archive or remove only the named run directories before retrying.

`confirm` first creates `raw_runs/validation`, reloads every committed lock consumer, and compares the newly selected object exactly with both `config_lock.json` and all consuming suite configs. Any difference stops execution before the locked test. It then runs the remaining suites, independent bundle QA, and final report generation. Calling the `validation` subcommand directly is rejected so a pre-lock result cannot accidentally occupy the formal path.

The exact commands actually executed are stored in run manifests. Full runs refuse dirty worktrees unless an explicit development flag is present. Formal run manifests record the commit, branch, dirty state, dependency versions, configuration hash, data/split hashes, machine/runtime metadata, and seeds.

Numerically negative outcomes, explicit fallbacks, and solver failures are valid findings and remain in their denominators and failure artifacts. They do not waive the artifact contract: a missing bundle, missing required table, invalid checksum, dirty-run manifest, incomplete locked-test denominator, or absent figure input makes `qa`/`report` fail closed.

## Phase A

```bash
uv run python run_paper_evidence.py phase-a \
  --config configs/phase_a.yaml
```

This writes only under `results/paper_evidence_v1/raw_runs/phase_a/`; it never overwrites `results/vendor_target_state_ablation/`.

## Determinism and artifact storage

Random generators use fixed per-trajectory/per-scenario seeds. OSQP uses deterministic settings and one thread. BLAS/thread environment is captured. Runtime values are inherently machine-specific and are not compared bitwise; numerical state/metric artifacts use documented tolerances.

Canonical summaries, selected figures, manifests, configs, checksums, and small traces are committed. Large Parquet runs may remain external, but their SHA-256, size, exact generation command, and relative rebuild location must appear in the artifact index. No result file is authoritative without a checksum.
