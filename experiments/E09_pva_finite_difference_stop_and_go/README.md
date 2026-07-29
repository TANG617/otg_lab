# E09 — PVA finite-difference stop-and-go suppression

E09 reruns E07's exact P-only method as an internal baseline and compares it
with the five causal PVA finite-difference methods from E04:

| Method | Represented target | Age at command |
|---|---:|---:|
| `position_zoh_p_ruckig` | `P[k], V=A=0` | 0 samples |
| `pva_est_backward_o1_k` | `PVA[k]` | 1 sample |
| `pva_est_backward_o2_k` | `PVA[k]` | 1 sample |
| `pva_est_centered_o2_km1` | `PVA[k−1]` | 2 samples |
| `pva_pred_backward_o1_kp1` | `PVA[k+1]` | 0 samples |
| `pva_pred_backward_o2_kp1` | `PVA[k+1]` | 0 samples |

The purpose is to reproduce E07's P-only stop-and-go threshold in the same run,
determine whether causal velocity and acceleration targets eliminate those
exact rest-to-rest pulses, and compare the five stencils directly with the
baseline.

## Inputs and matrix

E09 directly reuses E07's 20 three-second constant-velocity analytic inputs:

```text
p(t) = vref · t
v(t) = vref
a(t) = 0
j(t) = 0
```

The vendor velocity ratios are:

```text
0.125, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 0.9, 0.95,
1.0, 1.05, 1.1, 1.2, 1.5, 1.8, 2.0, 2.2, 3.0, 4.0
```

Acceleration and jerk limits use the E07 scales `0.25, 0.5, 1.0, 2.0`.
The complete matrix is:

```text
(1 P-only baseline + 5 finite differences) × 4 A/J scales × 20 inputs = 480 runs
```

All six methods use:

```text
dt = prediction horizon = minimum duration = 0.01 s
Vmax = 4.1 rad/s
measurement = position only
governor = none
follower = ordinary unshielded Ruckig
```

The baseline uses `PositionOnly → ZOH → P(V=A=0) → ordinary Ruckig`. The five
finite-difference candidates use scheduled position at the represented target
time and PVA targets. The main evaluation window is `0.5–2.5 s`, well after
every finite-difference startup stencil has matured.

## Interpretation of ρ

E07's single-cycle critical velocity is derived for rest-to-rest P-only
targets:

```text
Jerk limited:         vcrit = J dt² / 32
Acceleration limited: vcrit = A dt / 4 - A² / (2J)
```

E09 retains

```text
ρ_E07 = reference velocity / E07 P-only critical velocity
```

as the baseline threshold coordinate and as a common comparison coordinate.
`ρ_E07=1` is not a theoretical PVA finite-difference boundary.

## Metrics and acceptance

Primary stop-and-go metrics:

- `rest_to_rest_pulse_fraction`
- `stop_go_event_rate_hz`

Exact-profile severity:

- `profile_velocity_ripple_median`
- `profile_velocity_ripple_to_reference_median`
- `profile_velocity_ripple_to_reference_p95`

Tracking diagnostics include raw-time position RMSE and lag. Every main-window
target is also audited for:

- baseline velocity and acceleration equal to zero;
- finite-difference velocity equal to the constant reference velocity and
  acceleration equal to zero;
- causal availability;
- no startup state;
- target age equal to `0` for P-only and `1, 1, 2, 0, 0` for the differences.

The P-only baseline passes when it completes, has correct zero-V/A target
semantics and guardrails, and reproduces E07's threshold: pulse fraction at
least `0.95` for `ρ_E07≤0.95`, and at most `0.05` for `ρ_E07≥1.05`; the
boundary interval is diagnostic. A finite-difference run passes when it
completes, has zero pulse fraction and event rate, normalized median ripple no
greater than `1e-9`, normalized P95 ripple no greater than `1e-4`, exact
complete profiles, correct target semantics, and no declared profile,
fallback, or solver guardrail regression. Median and P95 use separate
tolerances so rare high-order finite-difference numerical tails remain visible
without being misclassified as rest-to-rest stop-and-go.

For exact linear positions, all five methods reconstruct `V=vref, A=0` after
startup. Their expected stop-and-go result is therefore zero. The differences
reduce to three observable target-age groups: one sample, two samples, and zero
samples.

## Artifacts

In addition to the standard manifest, commands, traces, exact profiles, and
tidy metrics, E09 writes:

```text
analysis/
  stop_go_surface.csv
  stop_go_method_comparison.csv
  acceptance_summary.md
  figures/
    stop_go_method_comparison.png/.svg
    stop_go_exact_velocity_comparison.png/.svg
    <input_id>_position.png
    by_method/
      <method_id>/
        stop_go_phase_map.png/.svg
        e07_rho_response.png/.svg
        stop_go_subcycle_velocity.png/.svg
        positions/
          <input_id>_position.png
```

Each of the six method directories contains a complete E07-style figure suite.
The root comparison figures place P-only and all five differences in the same
view. Root `<input_id>_position.png` files compare all six methods at vendor
limits; per-method position files compare the four A/J scales.

## Run

From the project root:

```bash
uv run otg-lab run E09
```

Read `analysis/acceptance_summary.md` first, then use
`stop_go_surface.csv` for the complete 480-run audit and
`stop_go_method_comparison.csv` for the method-by-limit summary.
