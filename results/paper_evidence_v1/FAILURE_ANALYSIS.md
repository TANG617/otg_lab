# Failure and fallback analysis

This file is a generated technical status artifact. Failed trajectory runs remain in the completion denominator and are excluded from numeric metric tables; locked-test paired inference requires the complete predeclared set.

| bundle | failures | fallbacks | completion | status |
|---|---:|---:|---:|---|
| acceleration | 0 | 820 | 100.000% | no_failures |
| governor_infeasible | 0 | 921 | 100.000% | no_failures |
| locked_test | 0 | 237498 | 100.000% | no_failures |
| multidof | 0 | 144 | 100.000% | no_failures |
| phase_a | 0 | 0 | unavailable | no_failures |
| plant | 0 | 36631 | 100.000% | no_failures |
| rate_study | 0 | 3495 | 100.000% | no_failures |
| real_replay | 0 | 1424 | 100.000% | no_failures |
| robustness | 0 | 7107 | 100.000% | no_failures |
| validation | 0 | 60622 | 100.000% | no_failures |

Detailed bounded counts are in `summaries/failure_type_counts.csv`; raw event rows remain in their independently hashed run bundles.

## Scientific acceptance failures

Required Section 16 component criteria: 18; passed: 11; failed or unavailable: 7.

| criterion | observed | operator | threshold | status | attribution |
|---|---:|---|---:|---|---|
| continuous_velocity_margin_nonnegative | -0.0204014740182 | >= | -1e-08 | fail | governor/follower |
| continuous_vaj_violation_count_zero | 276 | == | 0 | fail | governor/follower |
| nonfallback_point_admissibility_100pct | 0.999976488844 | rate== | 1 | fail | governor |
| runtime_total_max_below_5ms | 5516.25 | < | 5000 | fail | estimator/prediction/governor/follower/plant |
| csv_candidate_rmse_target | 0.390577360938 | <= | 0.02991 | fail | information_condition |
| csv_candidate_lag_target | 0.46 | <= | 0.03 | fail | information_condition |
| csv_candidate_max_error_target | 0.960671953214 | <= | 0.184528 | fail | information_condition |

The attribution column is a bounded technical localization, not a causal claim. Layer evidence below preserves competing explanations.

## Deduplicated fallback results

| method | reason | fallback cycles | total cycles | rate |
|---|---|---:|---:|---:|
| deployed_p_only | __all__ | 0 | 42809 | 0 |
| predicted_p | __all__ | 0 | 42809 | 0 |
| raw_predicted_pv | __all__ | 0 | 42809 | 0 |
| scalar_projected_pva | __all__ | 129 | 42809 | 0.00301338503586 |
| scalar_projected_pva | ruckig_exception | 129 | 42809 | 0.00301338503586 |
| one_step_governed_pva_direct | __all__ | 276 | 42809 | 0.0064472424023 |
| one_step_governed_pva_direct | no_feasible_one_step_jerk;target_not_one_step_reachable | 276 | 42809 | 0.0064472424023 |
| one_step_governed_pva_ruckig | __all__ | 0 | 42809 | 0 |
| jerk_qp_pva_direct | __all__ | 38478 | 42809 | 0.898829685347 |
| jerk_qp_pva_direct | free_duration_solver_exception | 21 | 42809 | 0.000490551052349 |
| jerk_qp_pva_direct | qp_continuous_postcheck_failed | 240 | 42809 | 0.00560629774113 |
| jerk_qp_pva_direct | qp_continuous_postcheck_failed;free_duration_solver_exception | 5 | 42809 | 0.000116797869607 |
| jerk_qp_pva_direct | qp_infeasible_or_failed;target_not_one_step_reachable | 602 | 42809 | 0.0140624635007 |
| jerk_qp_pva_direct | qp_timeout | 37162 | 42809 | 0.868088486066 |
| jerk_qp_pva_direct | qp_timeout;free_duration_solver_exception | 156 | 42809 | 0.00364409353173 |
| jerk_qp_pva_direct | qp_timeout;target_not_one_step_reachable | 292 | 42809 | 0.00682099558504 |
| jerk_qp_pva_ruckig | __all__ | 38617 | 42809 | 0.902076666122 |
| jerk_qp_pva_ruckig | qp_continuous_postcheck_failed | 174 | 42809 | 0.00406456586232 |
| jerk_qp_pva_ruckig | qp_timeout | 38418 | 42809 | 0.897428110911 |
| jerk_qp_pva_ruckig | ruckig_exception | 25 | 42809 | 0.000583989348034 |

## Layered evidence ledger

| stage | metric | observed | negative evidence | interpretation |
|---|---|---:|---|---|
| estimator | estimator_p_rmse | 3.37176144248e-07 | False | Magnitude is diagnostic; the protocol predeclares no estimator RMSE threshold. |
| prediction | prediction_p_rmse | 8.42940362439e-08 | False | Correct-future-time prediction magnitude; no retrospective threshold. |
| governor | projection_rate | 0 | False | Any projection is negative evidence against the zero-projection criterion. |
| follower | continuous_violation_count | 276 | True | Direct-follower continuous audit over every canonical command cycle. |
| plant | plant_position_rmse | 0 | False | Ideal clean-test plant diagnostic; delayed-plant evidence remains in its raw bundle. |
| information_condition | estimated_pva_harmful_rate | 0.508928571429 | True | Same-future estimated PVA worse than PV; all harmful trajectories are retained. |
| follower | chirp_max_abs_gain_error | 2.58657171697 | False | Maximum /gain-1/ across candidate chirp bands and joints. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |
| follower | chirp_max_abs_phase_delay_s | 0.977525282179 | False | Maximum absolute chirp phase delay; no retrospective threshold. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |
| follower | chirp_max_abs_group_delay_s | 2.10872732113 | False | Maximum absolute chirp group delay; no retrospective threshold. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |
| follower | chirp_max_abs_local_delay_s | 0.05 | False | Maximum absolute metadata-windowed local delay; no retrospective threshold. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |

Machine-readable sources are `summaries/acceptance_criteria.csv`, `summaries/fallback_summary.csv`, and `summaries/evidence_ledger.csv`.
