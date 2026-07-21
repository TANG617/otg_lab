## Scope

This PR adds the complete executable evidence platform for the causal position-only OTG study. It does not add a manuscript. It starts from verified `origin/main` commit `136842317b88b7819a6c726b057545531a916af3`.

## Architecture and timing contract

- Explicit `Measurement -> Estimator -> Predictor -> Governor -> Follower -> Plant` layers.
- Canonical physical timing is `target[k] -> command/output[k+1]`; state, availability, prediction, executable-target, command, and plant timestamps are stored separately.
- Formal limits are 4.1 rad/s, 8.2 rad/s², and 4000 rad/s³ at DT=10 ms.
- Ruckig `minimum_duration=DT`; prediction horizon remains an independent quantity.
- Unknown real derivative truth stays null; noncausal centered/oracle methods are labelled upper bounds.

## Data and frozen selection

- 300 constrained synthetic trajectories across six families: 120 train, 60 validation, 120 locked test.
- High-resolution analytic P/V/A/J truth is resampled independently; split IDs and seeds are committed.
- One available 1,936-row CSV trace is explicitly development-only because it does not meet the real locked-test requirement.
- Validation selected local polynomial w5/d3/lag1, constant-jerk prediction, H=0 ms, and a 10-step jerk QP without seeing test trajectories.

## Experiment matrix

- Estimator and horizon selection, same-information P/PV/PVA ablations, one-step and short-horizon QP governors, direct and ordinary-Ruckig followers.
- Phase A compatibility/regression, 224-case acceleration phase study, deliberate-infeasible governor negatives, 26-scenario robustness, 50/100/200/500 Hz rate study, 1/3/6/7/12 DoF synchronization, delayed-servo/feedback factorial, real timestamp replay, repeated runtime, frequency/chirp/local-delay diagnostics.
- Exact/sampled continuous constraint audits and sample-level fallback/failure attribution.

## Reproduction

```bash
uv sync --frozen --extra research
uv run pytest -q
uv run ruff check .
uv run python run_paper_evidence.py selection-validation --config configs/validation.yaml
# Copy the emitted lock verbatim, commit, and require a clean worktree.
uv run python run_paper_evidence.py confirm
```

The formal raw bundles in this PR were generated from clean commit `75fcc3af43a6dfdba9d5d6ca33e965d460efcacb`. A later clean reporting-only fix generated the bounded final layer from commit `9f5075473dd621663e139ccf42f500d5c29c1c89`; both commits are recorded in the root index.

## Verification and results

- 218 tests pass; Ruff, `git diff --check`, and the deterministic CI smoke pass.
- 10/10 raw bundles pass schema, manifest, checksum, and independent metric recomputation checks with `git_worktree_dirty=false`.
- 120/120 locked trajectories are present for every paired inference; 8 comparisons, 96 confidence intervals, and 112 denominator-completeness rows are available.
- The bounded result layer contains 63 indexed artifacts and 14 deterministic PNG/SVG figure categories. See `results/paper_evidence_v1/artifact_index.json`.
- The acceptance table has 23 records: 11 pass, 8 fail, and 4 reported. Of the 18 strict component criteria, 11 pass and 7 fail; the eighth failure is the overall roll-up.

Scientific results are retained whether positive or negative. The primary paired RMSE improvement is 55.89%, with a positive 95% lower interval endpoint of 27.52%, and absolute lag/max error are not worse. However, only 11/18 strict component criteria pass. The most important engineering failure is concentrated in `oscillatory__test__004`: one nonfallback target among 42,533 is outside the stopping envelope, after which fallback freezes a boundary state and produces 276 velocity violations, with a worst continuous velocity margin of -0.0204 rad/s. Acceleration and jerk component criteria still pass. This prevents any production- or safety-acceptance claim for the governor.

The observed runtime maximum is 5.516 ms and fails the predeclared `<5 ms` criterion, although only 1/7,825 post-warmup cycles exceeds 5 ms, p99 is 484.668 us, and no 10 ms deadline is missed. All three strict candidate targets also fail on the single legacy development CSV. The jerk-QP has roughly 90% fallback, dominated by timeouts. These are reported as negative evidence, not hidden or re-tuned away.

## External blockers

- No independent real locked test: requires at least 30 authorized trajectories, 15 minutes, and 3 sessions.
- No robot/HIL endpoint or calibrated target-servo parameters.
- No licensed Ruckig Pro Trackig API; Community Ruckig compatibility is covered.
- No approved LFS/release/object store for the 1.6 GB raw bundles; bounded artifacts and raw root hashes are committed instead.

Exact acquisition commands, safety prerequisites, expected inputs/outputs, and remaining matrices are in `EXTERNAL_BLOCKERS.md`.

## Review checklist

- [x] Causal timing and no-test-selection guards are tested.
- [x] Formal config and split lock are committed and hash-checked.
- [x] Every raw bundle comes from one clean commit and independently recomputes.
- [x] Statistical unit is the whole trajectory; incomplete pairs are rejected.
- [x] Figures are deterministic and generated from bounded source tables.
- [x] Positive and negative acceptance outcomes are both published.
- [x] Raw outputs are ignored and referenced through SHA-256 roots of trust.
- [ ] Independent real robot/HIL and licensed Trackig evidence await external resources.
