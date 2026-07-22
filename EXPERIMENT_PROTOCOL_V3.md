# Paper Evidence v3 Experiment Protocol

This is the executable protocol for `synthetic-feasible-v3`. It replaces the
failed, test-visible v2 confirmation with an independently seeded locked test.
It preserves the scientific design and acceptance thresholds; the v3 test must
not be used to repair, select, qualify, or tune the implementation.

## Why v3 is required

The one-shot v2 confirmation at clean commit
`364c44770a90996f04c4ab70402c165b165c7462` completed validation and locked
test, then failed while recomputing the acceleration summary. The failure was a
packaging/metric-availability defect, not an algorithmic selection result. The
v2 test was nevertheless generated and viewed, so v2 is permanently frozen as
non-confirmatory. Its immutable inventory is
`evidence_failures/v2_confirm_364c447.json`; `protocol_status_v2.json` forbids
resume or rerun.

The packaging fix omits a group-level reachability-subset metric when some
trajectories have no eligible nonfallback duration, while retaining explicit
evaluated-count and evaluated-fraction metrics over the full denominator.
Experiment bundles are now staged, independently recomputed and validated, and
only then atomically promoted to their final destination.

## Protocol state and freshness

- v1 and v2 manifests are exposed and may be used only for regression or
  development dry-runs.
- `split_manifest_v3.json` locks 120 train, 60 validation, and 120 test
  identities across six preregistered reference families.
- The deterministic v3 generator uses a v3-only SHA-256 namespace created after
  v2 was frozen. It does not consult exposed seeds while generating candidates.
- The freshness gate compares every v3 test identity and family/seed pair with
  every train, validation, and test entry in both exposed manifests.
- Creating or checking the manifest never generates a trajectory.
- Before the completed selection lock is committed, no v3 test trajectory may
  be generated, rendered, executed, or viewed.

Audit the manifest without generating trajectories:

```bash
uv run python scripts/generate_split_manifest_v3.py --check
```

## Fixed deployment condition

- Control and primary sample rate: 100 Hz (`DT = 10 ms`).
- Ruckig minimum duration: 10 ms.
- Limits: `|v| <= 4.1`, `|a| <= 8.2`, `|j| <= 4000`.
- Canonical sample schema: `otg.sample.v2`.
- Selection, reporting, and bootstrap unit: whole trajectory.
- No result-dependent change to limits, method matrix, QP gate, acceptance
  criteria, or test population is allowed.

## Ordered workflow

1. Freeze v2 and inventory all visible v2 outputs.
2. Fix and unit-test metric availability and atomic artifact publication.
3. Execute the complete nine-bundle pipeline on exposed v2 data as a labelled,
   non-confirmatory development dry-run. This may reveal packaging defects only;
   it may not change the method design or v3 seeds.
4. Commit the v3 generator, manifest, protocol, configs, and acceptance criteria
   with `locked=false`; require a clean worktree.
5. Run v3 selection validation using train/validation only.
6. Copy the emitted lock verbatim into every formal consumer config and complete
   `config_lock_v3.json`, including exact code/config/data hashes.
7. Commit the completed lock and again require a clean worktree.
8. Run `uv run python run_paper_evidence_v3.py confirm` exactly once. It must
   perform validation, compare the emitted selection to the committed lock, run
   the full test/negative/robustness/rate/multi-DoF/plant/development-replay
   matrix, independently QA every bundle, and build final results.
9. If v3 fails after test visibility, freeze it. Do not resume or rerun the same
   test, and do not adjust methods to the result.

The first development dry-run completed six bundles, then exposed a packaging
defect in repeated multi-DoF runtime accounting: the preregistered locked method
fails closed for `12 DoF × different_frequency` because the synchronized
ordinary-Ruckig free duration exceeds `DT`. The constant-jerk command remains
dynamically integrated and continuously safe, but it does not satisfy the
separate Ruckig-executability requirement. This is retained as a negative
result. The repair does not change the method, threshold, trajectory, or seed;
it preserves failed runtime units and their complete denominators instead of
aborting before the bundle can publish. The canonical schema now records
`command_t_free_le_dt` separately from requested-executable reachability.
The second dry-run reached multi-DoF bundle staging and then correctly refused
publication because its trajectory-level runtime failure has no cycle index.
The artifact contract now explicitly permits `k=null` for that dedicated
failure table, matching the existing primary `failures.csv` semantics; no
numeric result or experimental design changed.
The third dry-run showed the same nullable contract was missing from the
basename-selected read-back validator. The validator now has a symmetric
dedicated hook and its regression test performs both write and read-back
validation. Before another complete dry-run, the full-size downstream
`multidof`, `plant`, and `real-replay` stages are run as a separate exposed-v2
canary so another packaging defect cannot waste the completed upstream hours.

## Selection and QP qualification

Only train and validation are legal selection splits. Estimator ranking,
predictor/horizon ranking, governor configuration, QP qualification, plot
selection, and representative-trace selection reject test input.

The validation estimator/predictor/horizon/QP grids are unchanged from the
preregistered v2 design. A QP cell qualifies only if all gates hold:

- fallback rate `<= 5%`;
- continuous V/A/J violation count `= 0`;
- nonfallback terminal stopping viability `= 100%`;
- governor runtime P99 `< 1 ms`;
- 10 ms deadline miss count `= 0`.

If no QP cell qualifies, the lock records `qp_baseline_status: unqualified` and
`qp_horizon_steps: null`; no QP enters the primary comparison. Negative QP
diagnostics remain in the validation artifact.

## Locked-test acceptance criteria

For every safety-guaranteed cycle, the recorded command state must equal exact
constant-jerk integration from the actual current state and continuous V/A/J
violations must be zero. Nonfallback command segment feasibility, terminal
stopping viability, sequence consistency, and any protocol-required
`T_free <= DT` condition must each be 100%.

Primary results retain complete trajectory denominators, all failed and harmful
trajectories, overall paired bootstrap, family/demand/sample-rate strata,
family effects, worst-family effect, heterogeneity, harmful rate, and the worst
five trajectories. A negative result remains a valid result.

The real CSV is development-only. It must retain estimator, prediction,
governor, follower, and fallback diagnostics and cannot support real-robot or
deployment-generalization claims.

## Fail-closed confirmation gate

Every test-consuming v3 command rejects direct CLI invocation before config
loading. Only `command_confirm` may create the non-serializable in-process
capability required for test generation. Confirmation also requires:

- a clean committed worktree;
- no pre-existing managed v3 output;
- the exact registered config paths and no per-command output overrides;
- a completed selection lock identical in all consumer configs;
- exact SHA-256 coverage of every tracked Python implementation file,
  authoritative entrypoint, v3 wrapper, protocol, formal/development config,
  dataset config, generator, manifest, `.gitignore`, `plot_data.csv`, and both
  exposed manifests;
- zero v3/exposed identity or family-seed overlap.

The capability and logical command context are cleared in `finally` blocks.
Each experiment bundle remains invisible at its final path until checksum,
schema, feasibility, and independent metric recomputation validation all pass.

The authoritative implementation is `run_paper_evidence.py`; v3 uses only the
thin `run_paper_evidence_v3.py` profile wrapper. Legacy Phase A remains v1-only
negative evidence and is excluded. `real-replay` is retained only as a
development diagnostic. The deliberate-infeasible suite is isolated under
`synthetic-deliberate-infeasible-v3` and never enters clean denominators.
