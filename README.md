# OTG Lab

A small collection of experiments for studying position-only state estimation
and real-time tracking with Ruckig.

## Run

```bash
.venv/bin/python run_experiments.py
```

The script runs the synthetic and CSV experiments, writes plots to
`results/estimator/`, and saves the full metric table as
`results/estimator/realtime_metrics.csv`.

To reproduce the CSV prediction-lookahead sweep:

```bash
.venv/bin/python run_lookahead_sweep.py
```

This scans 50–300 ms prediction horizons for CA-KF and ABG, compares full
`p/v/a` targets with predicted-position-only targets, and tests both
`minimum_duration = lookahead` and a 10 ms minimum duration. Results are
written to `results/lookahead_sweep/`.

## Files

- `run_experiments.py`: experiment parameters, reference curves, and entry point
- `run_lookahead_sweep.py`: CSV lookahead and terminal-state sweep
- `otg_runner.py`: target-state projection, Ruckig loop, and metrics
- `estimators.py`: position-only state estimators
- `plotting.py`: plots and CSV output
- `plot_data.csv`: recorded position input used by the CSV experiment
- `results/`: current and historical experiment result groups

Edit the constants and `default_estimators()` list directly when trying a new
OTG configuration or estimator.
