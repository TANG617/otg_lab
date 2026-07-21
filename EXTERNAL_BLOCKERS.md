# External Resource Blockers

The core synthetic, real-trace replay, Community Ruckig, robustness, rate, multi-DoF, simulated plant, statistics, and artifact QA experiments do not depend on these resources.

## New independent real robot trajectories

- Required resource: authorized recordings from at least 30 independent trajectories, 15 minutes, and 3 sessions.
- Why: the repository contains only one 19.38 s development trace, so it cannot support an independent real-data locked test.
- Expected input: CSV/Parquet/ROS bag with session and trajectory identity, joint identity, position value, source timestamp, arrival timestamp when available, units, controller rate, hardware model, and consent/provenance metadata.
- Command: `python scripts/collect_csv.py --help` for direct CSV recording. Convert a collected CSV or ROS 2 bag with `python scripts/convert_rosbag.py --input INPUT --output OUTPUT.parquet --dataset-id DATASET --session-id SESSION --trajectory-id TRAJECTORY`; the converter validates and writes the canonical schema directly.
- Expected output: canonical Parquet plus data-quality report and session-level split proposal. Unknown derivative truth remains null.
- Safety prerequisites: authorized system/operator, configured joint/velocity/acceleration/jerk limits, emergency stop, workspace clearance, and no automatic limit relaxation.
- Remaining matrix: locked real generalization across smooth, reversal, stop/go, high-frequency, and near-limit trajectories.

## Robot or HIL execution

- Required resource: an authorized robot/HIL endpoint and safety supervisor.
- Why: simulation cannot establish hardware timing or servo behavior.
- Command: use the recorder adapter after setting the site-specific transport; no autonomous connection command is supplied by this repository.
- Expected output: reference, command, measured state, controller status, source/arrival/control times, safety events, hardware/control-mode metadata.
- Safety prerequisites: human approval, validated low-speed trajectories, limits fixed at or below the protocol, emergency stop, and site procedures.
- Remaining matrix: predefined safe 1-/multi-joint trajectories under measured-state replanning.

## Ruckig Pro Trackig

- Required resource: a valid Ruckig Pro installation/license exposing the actual Trackig API.
- Why: the installed Community builds do not provide licensed Trackig; simulating its output would be invalid.
- Detection/command: the compatibility runner imports and records exposed Ruckig symbols; when licensed, run the Trackig adapter smoke and formal matrix in the isolated vendor environment.
- Expected input/output: the same timestamped position reference and canonical sample schema, with a single declared prediction responsibility.
- Remaining matrix: Trackig comparisons against ordinary Ruckig, one-step governor, and jerk-QP under identical estimator/information/plant conditions.

## Calibrated servo parameters

- Required resource: identified bandwidth, damping, transport delay, saturation, and measurement-noise parameters for the target robot.
- Why: the included delayed plant is a transparent sensitivity model, not a claim about a specific robot.
- Expected input: versioned JSON/YAML with identification data provenance and uncertainty.
- Expected output: parameter-specific simulated/HIL plant comparison.
- Remaining matrix: nominal and uncertainty-band plant runs.

## External large-artifact storage

- Required resource: repository-approved LFS, release, or object-store location.
- Why: complete raw multi-matrix Parquet can exceed a reviewable Git diff.
- Expected output: immutable URL/version, SHA-256, size, generation command, and retention policy in `artifact_index.json`.
- Remaining matrix: none computationally; without storage, raw runs stay local/rebuildable while bounded canonical evidence is committed.
