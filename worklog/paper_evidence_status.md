# Paper Evidence v1 — Execution Board

Updated: 2026-07-21 (Asia/Shanghai)

## Frozen scope

- Repository: `TANG617/otg_lab`
- Base branch: `main`
- Verified latest base commit: `136842317b88b7819a6c726b057545531a916af3`
- Working branch: `agent/paper-evidence-v1`
- Formal limits: `vmax=4.1 rad/s`, `amax=8.2 rad/s²`, `jmax=4000 rad/s³`
- Formal control period: `DT=0.010 s`
- Formal Ruckig `minimum_duration=DT`; prediction horizon is independent.
- Results root: `results/paper_evidence_v1/` (historical result trees are immutable).

## Ownership and active work

| Area | Owner | File ownership | Status |
|---|---|---|---|
| Architecture/time semantics + estimators/predictors | Agent A | `otg_lab/{types,estimators,predictors,pipeline}.py`, focused tests | implemented; final QA pending |
| Dataset/schema/synthetic/fault/import | Agent B | `otg_lab/{schema,datasets,importers}.py`, data configs/manifests, focused tests | implemented; final QA pending |
| Metrics/statistics/artifact QA/figures/reporting | Agent H/I | `otg_lab/{metrics,statistics,artifacts,figures,reporting}.py`, focused tests | implemented; acceptance integration active |
| Governor/followers/plant/multi-DoF/runner | Lead | remaining `otg_lab/`, experiment CLI/config integration | implemented; final QA pending |
| Environment/CI/docs/clean runs/Git/PR | Lead | root config/docs, `.github/`, results | lock and formal runs pending |

Agents must not edit another owner's files without coordination. The lead reviews and integrates every area.

## Gates

- [x] Fetch and compare latest `origin/main`.
- [x] Confirm base commit and clean worktree.
- [x] Create isolated feature branch.
- [x] Complete baseline code/result/data audit.
- [x] Add reproducible environment and compatibility definitions.
- [x] Add explicit timed-state architecture and legacy wrapper.
- [x] Add canonical sample schema and validators.
- [x] Generate fixed train/validation/locked-test synthetic split (300 clean trajectories).
- [x] Implement causal estimators and separated predictors.
- [x] Implement one-step governor and short-horizon jerk QP.
- [x] Implement followers, timestamp replay, stress injection, plants, and n-DoF synchronization.
- [x] Add metrics, trajectory-level bootstrap statistics, constraint/runtime audits, and figures.
- [x] Pass unit/integration/causality/physics/recompute tests and CI smoke.
- [ ] Run train/pilot and validation; freeze locked parameters/config.
- [ ] Commit code/config and verify clean worktree.
- [ ] Run Phase A and all P0 confirmation experiments from clean commit.
- [ ] Independently recompute summaries and verify artifact hashes.
- [ ] Commit bounded canonical result artifacts.
- [ ] Push and open Draft PR with reproduction checklist and external blockers.

## Non-negotiable audit decisions

- Target and output semantics are always `target[k] -> command/output[k+1]` with physical timestamps stored explicitly.
- Unknown derivative truth remains null/NaN and is marked unavailable; it is never replaced by finite differences.
- Online components receive only data available at the current arrival/control time.
- Offline centered differences and oracle predictors are labeled noncausal upper bounds.
- Projection, fallback, invalid input, reset, solver failure, and deadline miss are sample-level events.
- Test trajectory IDs/seeds are locked before test execution and never used for method/horizon selection.
- The statistical unit is a complete trajectory.
- Hardware/HIL, Ruckig Pro Trackig, new real recordings, calibrated servo parameters, and external artifact storage are external-resource candidates; adapters and exact procedures remain in scope.

## Run ledger

No formal run is valid until the code/config commit is clean and the generated run manifest records `git_worktree_dirty=false`.

| Stage | Scope | Result |
|---|---|---|
| Base refresh | `git fetch origin main` | `origin/main` remains `136842317b88b7819a6c726b057545531a916af3`. |
| Development QA | full unit/integration/causality/physics/recompute suite, Ruff, diff check, and CI smoke | 212 passed; Ruff and diff check passed; smoke recorded 0 causality, constraint, fallback, and deadline failures. |
| Phase A preflight | config subset, analytic truth + CSV + next-cycle oracle | 7 runs, 0 failures; legacy regression rows within tolerance. |
| Diagnostic preflight | all 40 locked-test stop/reversal trajectories and all 6 chirps for one causal method | 0 failures; 376 local-event rows and 36 finite chirp-band rows. |
| Formal selection/test | pending clean code commit | Not yet run; no test data have been used for selection. |
