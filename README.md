# OTG Lab

A small collection of experiments for studying position-only state estimation
and real-time tracking with Ruckig.

## Run

```bash
python run_experiments.py
```

The script runs the synthetic and CSV experiments, writes plots to
`results/estimator/`, and saves the full metric table as
`results/estimator/realtime_metrics.csv`.

## Files

- `run_experiments.py`: experiment parameters, reference curves, and entry point
- `otg_runner.py`: target-state projection, Ruckig loop, and metrics
- `estimators.py`: position-only state estimators
- `plotting.py`: plots and CSV output
- `plot_data.csv`: recorded position input used by the CSV experiment
- `results/`: current and historical experiment result groups

Edit the constants and `default_estimators()` list directly when trying a new
OTG configuration or estimator.
