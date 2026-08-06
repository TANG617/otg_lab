# E18 full-axis controller capture v1

This directory is the default location for the controller-internal capture used
by rebuilt E18. A formal capture has exactly four required files:

```text
data/full_axis_capture/
  capture_manifest.json
  calls.csv
  axis_states.csv
  raw_position_events.csv
```

The schema version is `e18.full_axis_capture.v1`. CSV headers are exact and
ordered: missing, additional, renamed, or reordered columns are rejected. All
numeric values must be finite unless a field is explicitly documented as
nullable. CSV booleans use `true` or `false` (the loader also accepts `1` or
`0`). Files may be UTF-8 or UTF-8 with BOM.

There is no separate `markers.csv` in v1. The `run_reset` and `analysis_valid`
markers are attached to the exact Ruckig call in `calls.csv` so callback order
cannot become ambiguous.

## `capture_manifest.json`

The manifest must contain at least these fields:

```json
{
  "schema_version": "e18.full_axis_capture.v1",
  "capture_kind": "controller_internal_full_axis",
  "dof": 2,
  "axis_names": ["axis_0", "right_axis"],
  "right_axis_index": 1,
  "future_o1_h_s": 0.01,
  "nominal_control_dt_s": 0.001,
  "ruckig": {
    "version": "0.17.3",
    "commit": "exact deployed Ruckig commit or source identity"
  },
  "build": {
    "platform": "exact controller platform and architecture",
    "compiler": "exact compiler and version",
    "floating_point_options": "exact optimization/FP flags"
  },
  "runs": [
    {"run_id": "no_001", "mode": "No"}
  ]
}
```

Contract:

- `dof` is at least 2; `axis_names` has exactly `dof` unique entries in Ruckig
  axis order, and `right_axis_index` selects the scored right axis.
- `future_o1_h_s` is exactly the deployed nominal `0.01 s`. It is not replaced
  by timestamp jitter.
- `nominal_control_dt_s` records the declared loop period. Per-call timing still
  comes from `calls.csv`.
- `ruckig.version` and `ruckig.commit` identify the deployed solver.
  `build.platform`, `build.compiler`, and `build.floating_point_options` record
  the controller build environment. Empty placeholder strings are invalid.
- Formal data sufficiency compares the controller Ruckig distribution version
  with the local installed version before parity. The full commit/build identity
  remains in the run manifest for provenance and mismatch diagnosis.
- Rebuilt E18 requires exactly one complete `No` run and ignores other declared
  modes for its controller/replay identity decision. Additional `Time`,
  `TimeIfNecessary`, and `Phase` runs may be included for the supplemental
  four-mode `validation_pipeline.py`; that older extension requires all four
  modes and verifies that their controlled inputs/configuration are identical
  apart from synchronization.

## `calls.csv`

One row represents one actual Ruckig invocation. Use this exact header:

```csv
run_id,mode,cycle_seq,call_seq,callback_source,active_event_seq,monotonic_time_s,wall_delta_time_s,ruckig_delta_time_s,run_reset,analysis_valid,result_code,result_name,trajectory_duration_s,trajectory_time_s,new_calculation,did_section_change,new_section,was_calculation_interrupted,calculation_duration_us,synchronization,control_interface,duration_discretization,minimum_duration_s
```

Field definitions:

- `run_id`: foreign key into manifest `runs`.
- `mode`: one of `No`, `Time`, `TimeIfNecessary`, `Phase`; it must equal that
  run's manifest mode.
- `cycle_seq`: controller-cycle sequence. It may repeat when a cycle performs
  more than one Ruckig call, but it may not move backwards.
- `call_seq`: contiguous, strictly increasing invocation sequence within a run.
  Target-callback calls, 1 ms loop calls, and any other real invocations are
  separate rows and must not be merged.
- `callback_source`: nonempty deployment label identifying why this invocation
  occurred.
- `active_event_seq`: the latest full-axis raw position event actually active at
  this call. It must agree with `raw_position_events.csv` and therefore makes
  target hold and same-cycle callback order explicit.
- `monotonic_time_s`: monotonic timestamp of this invocation.
- `wall_delta_time_s`: positive observed elapsed wall/monotonic time since the
  applicable previous controller call. It records scheduler behavior.
- `ruckig_delta_time_s`: positive delta time actually supplied/assigned to
  Ruckig for this invocation. It controls the local replay. Do not copy or infer
  it from `wall_delta_time_s`; both must be logged even when they are equal.
- `run_reset`: true exactly once per run, on its first recorded `call_seq`. The
  local closed loop initializes here and then executes every subsequent call.
- `analysis_valid`: false for garbage/warm-up calls and true for scored calls. It
  must become true at least once and may transition false-to-true only once.
  This flag changes scoring only; calls before it still affect local state.
- `result_code`, `result_name`: exact Ruckig return code and name.
- `trajectory_duration_s`, `trajectory_time_s`: post-call trajectory duration
  and current trajectory time.
- `new_calculation`, `did_section_change`, `new_section`, and
  `was_calculation_interrupted`: exact post-call calculation/section state.
  `calculation_duration_us` records nondeterministic wall compute time for
  diagnostics and is deliberately not parity-gated.
- `synchronization`: one of the four synchronization values and equal to
  `mode` for a formal run.
- `control_interface`: `Position` or `Velocity`.
- `duration_discretization`: `Continuous` or `Discrete`.
- `minimum_duration_s`: blank when no minimum duration was set; otherwise a
  finite positive number.

Within each run, `monotonic_time_s` is strictly increasing. No call may be
missing from the reset-through-end sequence, including calls where
`analysis_valid=false`.

## `axis_states.csv`

One row represents one axis at one Ruckig invocation. Use this exact header:

```csv
run_id,call_seq,axis_index,axis_name,current_position_rad,current_velocity_rad_s,current_acceleration_rad_s2,target_position_rad,target_velocity_rad_s,target_acceleration_rad_s2,output_position_rad,output_velocity_rad_s,output_acceleration_rad_s2,output_jerk_rad_s3,max_velocity_rad_s,min_velocity_rad_s,max_acceleration_rad_s2,min_acceleration_rad_s2,max_jerk_rad_s3,min_jerk_rad_s3,enabled,per_dof_synchronization,per_dof_control_interface,independent_min_duration_s
```

Field definitions:

- `run_id`, `call_seq`: foreign key to the exact row in `calls.csv`.
- `axis_index`, `axis_name`: Ruckig axis index and the matching manifest name.
  Every call has exactly one row for every index from `0` through `dof - 1`.
- `current_position_rad`, `current_velocity_rad_s`,
  `current_acceleration_rad_s2`: state passed into Ruckig before the call.
- `target_position_rad`, `target_velocity_rad_s`,
  `target_acceleration_rad_s2`: actual deployment target passed into Ruckig,
  not a reconstructed or echoed substitute.
- `output_position_rad`, `output_velocity_rad_s`,
  `output_acceleration_rad_s2`, `output_jerk_rad_s3`: complete state returned by
  that invocation. Output jerk is required even though the formal P/V/A gate
  tolerances do not use it; it is retained for configuration and constraint
  diagnosis.
- `max_velocity_rad_s`, `max_acceleration_rad_s2`, `max_jerk_rad_s3`: finite,
  positive per-axis limits actually used by the call.
- `min_velocity_rad_s`, `min_acceleration_rad_s2`: nullable. Leave the CSV cell
  blank when the corresponding optional Ruckig minimum was unset; otherwise
  record the finite negative value. Blank means the local engine uses the
  Ruckig default symmetric lower limit (`-max`).
- `min_jerk_rad_s3`: required and currently must equal
  `-max_jerk_rad_s3`; the pinned Ruckig 0.17 Python input exposes symmetric jerk
  only, so an asymmetric capture is rejected as not evaluable.
- `enabled`: actual per-axis enabled flag.
- `per_dof_synchronization`: blank if no per-DoF override was supplied;
  otherwise one of `No`, `Time`, `TimeIfNecessary`, `Phase`.
- `per_dof_control_interface`: blank if no per-DoF override was supplied;
  otherwise `Position` or `Velocity`.
- `independent_min_duration_s`: per-axis independent minimum duration returned
  by Ruckig.

For either per-DoF option, a call must leave the field blank for all axes or
populate it for all axes; partially logged per-DoF configuration is rejected.
All full-axis rows are required for every call, including reset/warm-up calls
and disabled axes.

## `raw_position_events.csv`

One event has one row per axis. Use this exact header:

```csv
run_id,event_seq,applied_call_seq,axis_index,axis_name,monotonic_time_s,position_rad
```

Field definitions:

- `run_id`: foreign key into manifest `runs`.
- `event_seq`: contiguous raw-position-event sequence within the run.
- `applied_call_seq`: exact Ruckig call at which this event first became active.
  Multiple events may map to a call if that is what the callback executed; the
  sequence must never move backwards.
- `axis_index`, `axis_name`, `position_rad`: one raw position for every manifest
  axis, in Ruckig axis order.
- `monotonic_time_s`: event timestamp; all axis rows for an event have the same
  timestamp and `applied_call_seq`.

The reset call must have a complete initial full-axis position event. At every
call, `calls.csv.active_event_seq` must equal the latest event whose
`applied_call_seq` has been reached. This is what lets the local target builder
reproduce startup, full P/V target hold, and extra callback-triggered Ruckig
calls without guessing from timestamps.

## Capture and scoring invariants

- Start recording before reset and retain every call until run end. Never crop
  away controller garbage data before export.
- Mark the first trustworthy scored call with `analysis_valid=true`; keep it
  true thereafter. Local simulation still starts from the recorded reset state
  and propagates through all earlier calls.
- Record all axes, all raw position events, all Ruckig calls, and all per-axis
  rows without interpolation or reconstruction. A missing call, event axis, or
  axis-state row makes the capture `not_evaluable` rather than approximately
  comparable.
- For rebuilt E18, capture one complete No run. If the supplemental four-mode
  study is also planned, reset all four modes to the same initial state and keep
  input events, callback order, effective Ruckig timing, interfaces,
  discretization, minimum duration, enabled flags, and per-axis constraints
  identical. Change synchronization only. Wall timing is still recorded for
  diagnostics and may contain ordinary scheduler jitter across runs.
- Do not use the current `data/raw/*.csv` snapshots as formal input. They remain
  exploratory right-axis position observations and deliberately cannot pass the
  full-axis data-sufficiency gate.

Once these invariants pass, rebuilt E18 applies target-builder, solver-step, and
closed-loop parity to No only. Any failed or unevaluable gate writes its first
mismatch; the experiment run itself still completes successfully. The separate
`validation_pipeline.py` applies the same gates to all four modes before its
optional synchronization ranking and P-only/PV analysis.
