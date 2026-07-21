# Paper Evidence v2 Experiment Protocol

This is the executable protocol for `synthetic-feasible-v2`. It governs
code/config locking, the completed development selection, and a future one-shot
confirmation; it does not claim that v2 test trajectories or results currently
exist.

## Protocol state

- `synthetic-feasible-v1` test is exposed and is regression-only.
- `split_manifest_v2.json` locks 120 train, 60 validation, and 120 test
  identities across the same six reference families.
- The v2 seed generator uses a new deterministic SHA-256 namespace and does not
  consult v1 seeds while generating candidates.
- The freshness audit compares v2 test identity/family-seed pairs with every
  exposed v1 train/validation/test entry and must pass before test generation.
- At pre-lock, no v2 trajectory had been generated, rendered, opened, or run.
- Selection validation subsequently generated only train/validation trajectories;
  no v2 test trajectory has been generated, rendered, opened, or run.

## Fixed deployment condition

- Control and sample rate: 100 Hz (`DT = 10 ms`).
- Ruckig minimum duration: 10 ms.
- Limits: `|v| <= 4.1`, `|a| <= 8.2`, `|j| <= 4000`.
- Canonical sample schema: `otg.sample.v2`.
- Test selection unit and bootstrap unit: whole trajectory.

## Split and leakage policy

Only `train` and `validation` are legal selection splits. Test rows are rejected
by estimator, predictor/horizon, governor, follower, QP qualification, plot
selection, and representative-trace selection code.

The v2 manifest is generated and audited with:

```bash
uv run python scripts/generate_split_manifest_v2.py --check
```

The command validates manifest structure and freshness without generating any
trajectory. A renamed trajectory with an exposed family/seed pair is rejected.
The manifest and its SHA-256 must be tracked in the same clean commit as this
protocol, every v2 config, the generator, and `config_lock_v2.json` before test
generation is permitted.

## Ordered workflow

1. Finish implementation and all unit/property/regression tests.
2. Mark v1 locked test as exposed regression evidence only.
3. Generate and audit only the v2 seed/split manifest.
4. Commit generator, manifest, this protocol, validation design, acceptance
   criteria, and pre-lock config; require a clean worktree.
5. Run `selection-validation` using only v2 train/validation trajectories.
6. Lock estimator, predictor, horizon, governor parameters, and QP status into
   every v2 consumer config and `config_lock_v2.json`.
7. Commit the complete lock and again require a clean worktree.
8. Run v2 `confirm` once. Confirmation rechecks the clean commit, exact manifest
   hash, every locked code/config hash, v1 overlap guard, exact selection lock,
   and empty output destinations before granting an ephemeral in-process object
   capability that permits any test trajectory to be generated. The capability
   has no CLI/string representation and is cleared in a `finally` block.
9. Never change a method and rerun the same v2 test. Any method change after
   test visibility requires v3 with a fresh manifest.

## Validation selection

Estimator ranking uses validation metrics after train/validation grid execution.
Predictor and horizon ranking use validation only. No `pilot`, `test`, faulted
test, or v1 regression row is a legal selection input.

QP candidates use the validation-only gate below. A QP cell is qualified only
when all conditions hold:

- fallback rate `<= 5%`;
- continuous V/A/J violation count `= 0`;
- nonfallback terminal stopping viability `= 100%`;
- governor runtime P99 `< 1 ms`;
- no runtime at or beyond the 10 ms deadline.

If no cell qualifies, the lock records `qp_baseline_status: unqualified` and
`qp_horizon_steps: null`. No QP method may enter the primary locked-test matrix;
validation diagnostics remain available as a negative result.

The completed validation run at clean commit `40dfb313c669b46e56055242895c97050bc4e79d`
locked `local_poly_w5_d3_lag1`, `constant_jerk`, a `0 ms` prediction horizon,
and `jerk_qp_n20`. The run recorded `test_trajectory_count_seen: 0`. QP-20
qualified with a 0.0005942653 fallback rate, zero continuous violations, 100%
nonfallback terminal viability, 386.77 microsecond governor-runtime P99, and
zero 10 ms deadline misses. QP-10 remains a recorded negative qualification
result because it incurred one 10 ms deadline miss.

## Locked-test acceptance criteria

For every cycle counted as safety-guaranteed, command state must equal exact
constant-jerk integration from the actual current state and continuous V/A/J
violations must be zero. Nonfallback command segment feasibility, terminal
stopping viability, sequence consistency, and any protocol-required
`T_free <= DT` condition must each be 100%.

Primary results retain the complete trajectory denominator, negative results,
overall paired bootstrap, family/demand/sample-rate strata, family effects,
worst-family effect, harmful rate, and worst trajectories. The v1 exposed
regression and development CSV are never confirmatory evidence.

## Fail-closed conditions

Confirmation stops before test generation if the worktree is dirty, a v2 config
or lock is absent, the manifest is untracked, its hash differs, any exposed test
identity or family/seed overlaps, selection locks differ, an unqualified QP is
present in the primary matrix, or a managed output already exists.

All test-consuming v2 subcommands (`locked-test`, `acceleration`, `robustness`,
`rates`, `multidof`, and `plant`) reject direct invocation before loading their
config. They require the non-serializable object held only while `confirm` calls
the subcommand, the exact registered config path, no `--output` override, a
committed completed selection lock, and runtime verification of every hash
recorded in `config_lock_v2.json`.
The internal v2 test-case helper independently rejects calls without that
capability.

The runtime lock covers an exact key set: every tracked Python file under
`otg_lab/` plus `target_state_experiment.py`, both evidence entrypoints, every
formal/development config, protocol, generator, dataset config, and split
manifest. It also hashes the exact `.gitignore` workflow file; the complete v2
managed output tree is ignored so one confirmed bundle cannot make the clean
worktree gate reject the next bundle. The exact external data scope hashes both
`plot_data.csv` (empirical-jitter and development replay input) and the exposed
v1 `split_manifest.json` used by the freshness guard. Missing or extra
implementation/workflow/data keys fail closed. In-process suite
execution records each logical subcommand in `run.json`, then clears both the
logical-command context and capability even when a suite raises.

The authoritative implementation is `run_paper_evidence.py`; v2 enters only
through the thin `run_paper_evidence_v2.py` profile wrapper. Formal sample
execution is `run_pipeline_rows`; the public `TrackingPipeline` is a thin
single-cycle facade sharing the same replanning rule and v2 meanings for absent
executable targets, safe fallback validity, and command/plant state.

Legacy Phase A remains v1 historical negative evidence. Its ordinary-Ruckig
endpoint semantics cannot represent a v2 constant-jerk command, so the v2
entrypoint rejects `phase-a` and v2 `confirm` excludes it. `real-replay` remains
in confirm as a development-only legacy CSV diagnostic, never as confirmatory
test evidence; its effective resolved config, samples, and data manifest all use
the unique `paper-evidence-v2-real-replay` run identity and `development` split.
The separate four-case governor negative suite is labelled
`synthetic-deliberate-infeasible-v2`, matching the locked v2 dataset design;
it remains isolated from clean selection and confirmatory test denominators.
