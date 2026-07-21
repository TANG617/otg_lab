# Real Data Gap

Repository audit on 2026-07-21 found one source trace, `plot_data.csv`, with 1,936 rows from one topic and one apparent session/trajectory. Its elapsed time is strictly increasing from 6.596 s to 25.978 s (19.382 s total); median source interval is 9.992 ms, observed range 2.465–21.752 ms. No ROS bag, MCAP, DB3, Parquet, or additional source CSV was found inside the authorized workspace.

This is below the protocol target of 30 trajectories, 15 minutes, and 3 sessions. It is therefore a development/regression trace, not the sole locked real test. Its `velocity/acceleration/jerk` truth fields are null. Fixed-grid and timestamp-aware interpretations are separate datasets.

The repository includes collector/converter adapters and canonical validation. Collection must be performed only on an authorized system under the safety prerequisites in `EXTERNAL_BLOCKERS.md`. Synthetic data is never labelled real.
