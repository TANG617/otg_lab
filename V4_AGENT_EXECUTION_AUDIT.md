# V4 Agent Execution Audit

Recorded before any V4 trajectory generation or test visibility. “Test used”
means a fresh manifest-listed V4 test trajectory was generated, executed, or
inspected; identity-only manifest validation and fabricated unit-test fixtures
do not count as test use.

| Agent | Recorded completion time (Asia/Shanghai) | Status | Commit(s) | Owned artifacts | Test used | Test visible | Notes |
|---|---|---|---|---|---:|---:|---|
| A — Protocol and Freshness | 2026-07-23T22:22:25+08:00 | complete | `00e8f0b`, `a7e530d`, `dcc2731` | `EXPERIMENT_PROTOCOL_V4.md`, hypotheses, statistical design, acceptance criteria, decision log, protocol state/lock and exactly-once state machine | no | no | Protocol and authorization scheme reviewed by the primary agent; no trajectory execution. |
| B — Method Identity | 2026-07-23T23:03:13+08:00 | complete | `5bbbde0`, `dcc2731` | `V4_METHOD_MATRIX.json`, effective-configuration identity, purity, same-information, target-zeroing, ordinary/oracle identity audits | no | no | Effective post-merge parameters and policies are hashed and compared with the canonical matrix. |
| C — Data and Split | 2026-07-23T22:04:26+08:00 | complete | `ecb5f51` | `split_manifest_v4.json`, namespace history, freshness checker and balance/overlap tests | no | no | Namespace and identity-only manifest were committed before any V4 trajectory generation; all six overlap checks are zero. |
| D — Experiment Execution | 2026-07-23T23:03:13+08:00 | complete | `dcc2731` | narrow V4 CLI/runner, capability gate, dry-run/validation/confirm/report-only paths, environment and clean-output gates | no | no | Agent performed static checks only. The primary agent ran unit/integration tests, not a V4 experiment. |
| E — Metrics and Statistics | 2026-07-23T23:03:13+08:00 | complete | `bc1e397`, `dcc2731` | whole-trajectory estimands, bootstrap/Holm/guardrails/subgroups/harm/worst-case and independent raw-to-table recomputation | no | no | Includes adversarial mutation tests and explicit unavailable/incomplete-denominator handling. |
| F — Artifact and Reproducibility | 2026-07-23T23:03:13+08:00 | complete | `930c06e`, `65f46e6`, `dcc2731` | raw validation, profile/metric recomputation, complete indexes, negative preservation, V3 immutability, deterministic release packages | no | no | V3 fixed-reference path set and bytes are both checked; archives are revalidated before packaging. |
| G — Adversarial Reviewer | 2026-07-23T23:03:13+08:00 | complete with terminal tool-response error | review-only; fixes integrated in `dcc2731` | leakage, denominator, method identity, failure preservation, report-only, statistics, handoff and release audit findings | no | no | Did not implement the main workflow. All actionable findings delivered before the final tool-response error were independently reproduced or covered by tests. |
| H — Paper Evidence Handoff | 2026-07-23T23:03:13+08:00 | complete | `dcc2731` | source-backed contextual tables, JSON/Markdown/TeX handoff, eight figures, representative-selection sidecars and claim gates | no | no | Supports negative, invalid and nullable unavailable outcomes without changing the statistical result. |

Primary-agent review status at record creation:

- implementation diff reviewed;
- V4-focused test suite passed after two test-only regressions were corrected;
- repository-wide suite reached 440 passed with one stale wrapper-source assertion,
  which was corrected and rechecked;
- no V4 dry-run, validation canary, locked test, or oracle trajectory had been
  generated at this point;
- fresh test trajectory count seen remained zero.
