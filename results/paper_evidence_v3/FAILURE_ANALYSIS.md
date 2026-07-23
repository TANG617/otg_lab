# Failure and fallback analysis

This file is a generated technical status artifact. Failed trajectory runs remain in the completion denominator and are excluded from numeric metric tables; locked-test paired inference requires the complete predeclared set.

| bundle | failures | fallbacks | completion | status |
|---|---:|---:|---:|---|
| acceleration | 0 | 107986 | 100.000% | no_failures |
| governor_infeasible | 0 | 1235 | 100.000% | no_failures |
| locked_test | 0 | 488712 | 100.000% | no_failures |
| multidof | 1 | 0 | 96.667% | failures_observed |
| plant | 0 | 35324 | 100.000% | no_failures |
| rate_study | 0 | 0 | 100.000% | no_failures |
| real_replay | 0 | 5260 | 100.000% | no_failures |
| robustness | 0 | 217437 | 100.000% | no_failures |
| validation | 0 | 317 | 100.000% | no_failures |

Detailed bounded counts are in `summaries/failure_type_counts.csv`; raw event rows remain in their independently hashed run bundles.

## Scientific acceptance failures

Required Section 16 component criteria: 18; passed: 15; failed or unavailable: 3.

| criterion | observed | operator | threshold | status | attribution |
|---|---:|---|---:|---|---|
| csv_candidate_rmse_target | 0.219957578552 | <= | 0.02991 | fail | information_condition |
| csv_candidate_lag_target | 0.12 | <= | 0.03 | fail | information_condition |
| csv_candidate_max_error_target | 0.668309925016 | <= | 0.184528 | fail | information_condition |

The attribution column is a bounded technical localization, not a causal claim. Layer evidence below preserves competing explanations.

## Deduplicated fallback results

| method | reason | fallback cycles | total cycles | rate |
|---|---|---:|---:|---:|
| deployed_p_only | __all__ | 40513 | 42199 | 0.960046446598 |
| deployed_p_only | ruckig_command_no_viable_next_step | 1 | 42199 | 2.36972440105e-05 |
| deployed_p_only | ruckig_command_not_one_step_reachable | 40512 | 42199 | 0.960022749354 |
| predicted_p | __all__ | 40510 | 42199 | 0.959975354866 |
| predicted_p | ruckig_command_no_viable_next_step | 2 | 42199 | 4.7394488021e-05 |
| predicted_p | ruckig_command_not_one_step_reachable | 40508 | 42199 | 0.959927960378 |
| raw_predicted_pv | __all__ | 40482 | 42199 | 0.959311832034 |
| raw_predicted_pv | ruckig_command_no_viable_next_step | 2 | 42199 | 4.7394488021e-05 |
| raw_predicted_pv | ruckig_command_not_one_step_reachable | 40480 | 42199 | 0.959264437546 |
| scalar_projected_pva | __all__ | 41022 | 42199 | 0.9721083438 |
| scalar_projected_pva | ruckig_command_not_one_step_reachable | 41022 | 42199 | 0.9721083438 |
| one_step_governed_pva_direct | __all__ | 0 | 42199 | 0 |
| one_step_governed_pva_ruckig | __all__ | 183 | 42199 | 0.00433659565393 |
| one_step_governed_pva_ruckig | ruckig_command_not_one_step_reachable | 183 | 42199 | 0.00433659565393 |
| jerk_qp_pva_direct | __all__ | 27 | 42199 | 0.000639825588284 |
| jerk_qp_pva_direct | qp_postcheck_failed | 27 | 42199 | 0.000639825588284 |
| jerk_qp_pva_ruckig | __all__ | 27 | 42199 | 0.000639825588284 |
| jerk_qp_pva_ruckig | qp_postcheck_failed | 27 | 42199 | 0.000639825588284 |

## Layered evidence ledger

| stage | metric | observed | negative evidence | interpretation |
|---|---|---:|---|---|
| estimator | estimator_p_rmse | 3.49816722203e-06 | False | Magnitude is diagnostic; the protocol predeclares no estimator RMSE threshold. |
| prediction | prediction_p_rmse | 3.49816722203e-06 | False | Correct-future-time prediction magnitude; no retrospective threshold. |
| governor | projection_rate | 0 | False | Any projection is negative evidence against the zero-projection criterion. |
| follower | continuous_violation_count | 0 | False | Direct-follower continuous audit over every canonical command cycle. |
| plant | plant_position_rmse | 0 | False | Ideal clean-test plant diagnostic; delayed-plant evidence remains in its raw bundle. |
| information_condition | estimated_pva_harmful_rate | 0.982142857143 | True | Same-future estimated PVA worse than PV; all harmful trajectories are retained. |
| follower | chirp_max_abs_gain_error | 5.64395671219 | False | Maximum /gain-1/ across candidate chirp bands and joints. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |
| follower | chirp_max_abs_phase_delay_s | 1.04549198071 | False | Maximum absolute chirp phase delay; no retrospective threshold. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |
| follower | chirp_max_abs_group_delay_s | 5.31838211022 | False | Maximum absolute chirp group delay; no retrospective threshold. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |
| follower | chirp_max_abs_local_delay_s | 0.05 | False | Maximum absolute metadata-windowed local delay; no retrospective threshold. 36 band-joint rows retained in summaries/chirp_frequency_response.csv. |

Machine-readable sources are `summaries/acceptance_criteria.csv`, `summaries/fallback_summary.csv`, and `summaries/evidence_ledger.csv`.
