# E18 synchronization-mode right-axis snapshots

These four files are unmodified rolling CSV snapshots captured from the deployed
controller while changing global Ruckig synchronization. Rebuilt E18 uses
`none.csv` for its primary exploratory recorded/replay comparison. The other three files
remain supplemental synchronization observations. None is a formal full-axis
parity capture.

| File | Declared mode | SHA-256 |
|---|---|---|
| `none.csv` | `No` | `9808c80ead58e315f79089a90d0bce599bf312ba3314a6882b48ff4f746654f0` |
| `time.csv` | `Time` | `e22fae77236ff5d1d08d705716942eb4a063d107c73075edbae100c00ac907af` |
| `time_if_necessary.csv` | `TimeIfNecessary` | `75d61190e6471466f894c64270a74818c2592658878874e93effec99f36a701c` |
| `phase.csv` | `Phase` | `3d023ac774c2b2075eac0de950781b3075cbfe6dbe85b699dc9fc52895bfaa42` |

Every file uses this raw logger header:

```csv
elapsed time,timestamp,topic,value
```

- `elapsed time`: logger monotonic/elapsed time in seconds;
- `timestamp`: source timestamp in seconds;
- `/mc/ik/joint_states.position[$right_joint_id]`: right-axis raw position
  input, in radians;
- `...interface_values[$right_joint_id].values[0]`: right-axis Ruckig output
  position, in radians;
- `...interface_values[$right_joint_id].values[4]`: right-axis target-position
  echo, in radians, for timing audit only.

The snapshots are cumulative: later files contain earlier source segments.
The exploratory loader splits source position on gaps greater than 1 second,
selects only the final segment associated with the file's declared mode, and
marks the first 3 seconds of that segment as garbage/unscored. It includes the
last source target's nominal hold interval when reporting output coverage. This
windowing never supplies an initialization state and never makes the files
formally evaluable.

The files do not contain other axes, current/target/output velocity or
acceleration, exact call/callback order, per-axis limits/options, reset and
analysis-valid controller markers, or the deployed Ruckig build identity.
Consequently, rebuilt E18 can generate right-axis overlays, errors and call-
semantics diagnostics from `none.csv`, but its formal No-only parity status is
`not_evaluable`. The supplemental `validation_pipeline.py` likewise blocks all
synchronization ranking and P-only/PV analysis when these files are the best
available input.
