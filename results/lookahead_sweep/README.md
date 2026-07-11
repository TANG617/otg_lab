# CSV prediction-lookahead sweep

This experiment scans CA-KF and alpha-beta-gamma prediction horizons of 50,
60, 100, 150, 200, 250, and 300 ms. Each horizon compares:

- full predicted `p/v/a` versus predicted position with `v=a=0`;
- Ruckig `minimum_duration = lookahead` versus `minimum_duration = 10 ms`.

## 250 ms result

| Estimator | Target | Ruckig minimum duration | Output RMSE | Best lag | Aligned RMSE | Max error | Prediction RMSE | Reachable within 250 ms | Duration P50 | Duration P90 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CA-KF | full `p/v/a` | 250 ms | 0.27242 | +160 ms | 0.26291 | 1.05761 | 0.15658 | 16.6% | 466 ms | 896 ms |
| CA-KF | full `p/v/a` | 10 ms | 0.20645 | +10 ms | 0.20643 | 0.83584 | 0.15658 | 14.6% | 459 ms | 912 ms |
| CA-KF | position only | 250 ms | 0.17026 | -10 ms | 0.17022 | 0.66826 | 0.15658 | 12.5% | 496 ms | 805 ms |
| CA-KF | position only | 10 ms | 0.16440 | -30 ms | 0.16391 | 0.64801 | 0.15658 | 11.4% | 487 ms | 794 ms |
| ABG | full `p/v/a` | 250 ms | 0.19065 | +130 ms | 0.17992 | 0.78028 | 0.16809 | 18.4% | 474 ms | 888 ms |
| ABG | full `p/v/a` | 10 ms | 0.19307 | +20 ms | 0.19287 | 0.79093 | 0.16809 | 13.6% | 468 ms | 902 ms |
| ABG | position only | 250 ms | **0.15609** | -50 ms | **0.15446** | 0.64498 | 0.16809 | 12.1% | 489 ms | 799 ms |
| ABG | position only | 10 ms | 0.16960 | +40 ms | 0.16880 | **0.63401** | 0.16809 | 12.0% | 488 ms | 819 ms |

The best 250 ms configuration still has much higher output RMSE than the best
sweep result: CA-KF with a 60 ms position prediction and a matched 60 ms
minimum duration reaches RMSE 0.06150 with +130 ms lag.

The near-zero or negative best-lag values at 250 ms do not indicate accurate
tracking. The lag-aligned RMSE remains high, showing that long-horizon
constant-acceleration extrapolation leads or overshoots the reference.

## Files

- `lookahead_sweep_metrics.csv`: all 56 configurations and metrics
- `lookahead_sweep.png`: RMSE, global lag, and reachability versus horizon
- `prediction_error.png`: estimator-only future-position prediction error
- `lookahead_250_tracking.png`: direct comparison of the eight 250 ms cases

Run from the repository root with:

```bash
.venv/bin/python run_lookahead_sweep.py
```
