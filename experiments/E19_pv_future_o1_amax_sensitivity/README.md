# E19 — PV Future-O1 replay Amax sensitivity

E19 varies only replay Ruckig Amax inherited from E18's primary
`Synchronization.No` execution. The `method_id` is fixed to
`pv_pred_backward_o1_kp1`; the raw `none.csv`, PV target,
1 ms control period, zero P/V/A reset, `V=4.1 rad/s`, `J=4000 rad/s³`, and
`update_target_callback_and_control_loop` execution remain fixed.

The declared Amax grid is `16.2, 16.4, ..., 40.6, 48.6, 64.8 rad/s²`
(125 cases), with IDs such as `amax_16p2`. Every case is replayed from reset through the complete E18 source
segment; only the critical rising episode is retained as a combined trace.

The largest positive raw-position jump defines the focal event. E19 reports
maximum position drawdown in both:

- the original dip window, from 30 ms before to 40 ms after that event; and
- the maximal contiguous nondecreasing raw-position interval around it.

`1e-12 rad` is the numerical no-dip tolerance and `0.1 mrad` is the engineering
tolerance. Passing the focal window while failing the full rising episode is
reported as **focal eliminated but transferred**, not global elimination.
The recorded output is plotted only as an `Amax=16.2` observational reference;
it is not treated as a counterfactual for higher Amax values.

```bash
uv run otg-lab run E19
uv run otg-lab run E19 --no-figures
```
