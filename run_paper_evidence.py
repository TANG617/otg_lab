#!/usr/bin/env python3
"""Execute and QA the versioned paper-evidence experiment program."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from otg_lab.acceleration import acceleration_case_matrix, acceleration_case_metadata
from otg_lab.artifacts import (
    ArtifactWriter,
    assert_clean_commit,
    sha256_file,
    validate_artifact_bundle,
)
from otg_lab.benchmarks import (
    build_acceleration_phase_map,
    evaluate_locked_estimator,
    evaluate_locked_predictor,
    lock_estimator_parameters,
    rank_estimator_grid,
    rank_prediction_horizons,
    ruckig_unconstrained_free_duration,
    run_estimator_grid,
    run_predictor_horizon_sweep,
    sampling_rate_dimensionless,
)
from otg_lab.config import load_config, write_resolved_config
from otg_lab.datasets import (
    default_stress_suite,
    deliberate_infeasible_suite,
    inject_timing,
    trajectory_to_rows,
    validate_fresh_locked_test_manifest,
)
from otg_lab.diagnostics import (
    governor_invariant_summaries,
    real_replay_diagnostics,
    robustness_fault_events,
    robustness_recovery_summaries,
    synthetic_chirp_frequency_response,
    synthetic_frequency_response,
    synthetic_local_delay,
)
from otg_lab.experiments import (
    ExperimentOutcome,
    combine_outcomes,
    locked_method,
    repeated_runtime_study,
    run_pipeline_matrix,
    same_information_methods,
    serializable_config,
    stressed_cases,
    synthetic_cases,
    write_experiment_bundle,
)
from otg_lab.importers import (
    empirical_jitter_from_csv,
    import_legacy_fixed_grid,
    import_timestamp_causal,
    simulate_arrival_replay,
)
from otg_lab.metrics import metrics_by_trajectory
from otg_lab.multidof import (
    PATTERNS,
    compute_multidof_tracking_diagnostics,
    generate_multidof_truth,
    multidof_to_rows,
)
from otg_lab.phase_a import run_phase_a
from otg_lab.qualification import select_qualified_qp
from otg_lab.reporting import build_final_result_artifacts
from otg_lab.schema import read_parquet

ROOT = Path(__file__).resolve().parent

LOCKED_SELECTION_COMMON_FIELDS = frozenset(
    {
        "schema_version",
        "selection_split",
        "test_trajectory_count_seen",
        "estimator",
        "estimator_id",
        "estimator_parameters",
        "downstream_estimators",
        "predictor",
        "prediction_horizon_ms",
        "prediction_objective",
        "qp_horizon_steps",
        "minimum_duration_s",
        "motion_limits",
    }
)


class EvidenceProtocol:
    """All version-specific paths and leakage policy for one evidence program."""

    __slots__ = (
        "version",
        "dataset_id",
        "entrypoint",
        "raw_root",
        "final_root",
        "selection_validation_root",
        "config_lock_path",
        "locked_selection_schema_version",
        "config_defaults",
        "confirm_experiments",
        "selection_consumer_configs",
        "default_split_manifest",
        "exposed_test_manifests",
        "require_fresh_locked_test",
        "protocol_document",
    )

    def __init__(
        self,
        *,
        version: str,
        dataset_id: str,
        entrypoint: Path,
        raw_root: Path,
        final_root: Path,
        selection_validation_root: Path,
        config_lock_path: Path,
        locked_selection_schema_version: str,
        config_defaults: tuple[tuple[str, str], ...],
        confirm_experiments: tuple[tuple[str, str, str], ...],
        selection_consumer_configs: tuple[str, ...],
        default_split_manifest: Path | None = None,
        exposed_test_manifests: tuple[Path, ...] = (),
        require_fresh_locked_test: bool = False,
        protocol_document: Path | None = None,
    ) -> None:
        self.version = version
        self.dataset_id = dataset_id
        self.entrypoint = entrypoint
        self.raw_root = raw_root
        self.final_root = final_root
        self.selection_validation_root = selection_validation_root
        self.config_lock_path = config_lock_path
        self.locked_selection_schema_version = locked_selection_schema_version
        self.config_defaults = config_defaults
        self.confirm_experiments = confirm_experiments
        self.selection_consumer_configs = selection_consumer_configs
        self.default_split_manifest = default_split_manifest
        self.exposed_test_manifests = exposed_test_manifests
        self.require_fresh_locked_test = require_fresh_locked_test
        self.protocol_document = protocol_document

    def config_for(self, command: str) -> str:
        defaults = dict(self.config_defaults)
        try:
            return defaults[command]
        except KeyError as error:
            raise SelectionLockError(
                f"protocol {self.version} has no config for {command!r}"
            ) from error


V1_CONFIG_DEFAULTS = (
    ("smoke", "configs/development.yaml"),
    ("selection-validation", "configs/validation.yaml"),
    ("validation", "configs/validation.yaml"),
    ("locked-test", "configs/locked_test_v1.yaml"),
    ("acceleration", "configs/acceleration.yaml"),
    ("governor-infeasible", "configs/governor_infeasible.yaml"),
    ("robustness", "configs/robustness.yaml"),
    ("rates", "configs/rate_study.yaml"),
    ("multidof", "configs/multidof_plant.yaml"),
    ("plant", "configs/multidof_plant.yaml"),
    ("real-replay", "configs/locked_test_v1.yaml"),
    ("phase-a", "configs/phase_a.yaml"),
)

V1_CONFIRM_EXPERIMENTS = (
    ("validation", "configs/validation.yaml", "validation"),
    ("locked-test", "configs/locked_test_v1.yaml", "locked_test"),
    ("acceleration", "configs/acceleration.yaml", "acceleration"),
    (
        "governor-infeasible",
        "configs/governor_infeasible.yaml",
        "governor_infeasible",
    ),
    ("robustness", "configs/robustness.yaml", "robustness"),
    ("rates", "configs/rate_study.yaml", "rate_study"),
    ("multidof", "configs/multidof_plant.yaml", "multidof"),
    ("plant", "configs/multidof_plant.yaml", "plant"),
    ("real-replay", "configs/locked_test_v1.yaml", "real_replay"),
    ("phase-a", "configs/phase_a.yaml", "phase_a"),
)

# Every suite below consumes the validation-selected estimator, predictor,
# horizon, or QP horizon.  Each config therefore carries the complete lock,
# even when one particular suite uses only a subset of its fields.
V1_SELECTION_CONSUMER_CONFIGS = (
    "configs/locked_test_v1.yaml",
    "configs/acceleration.yaml",
    "configs/governor_infeasible.yaml",
    "configs/robustness.yaml",
    "configs/rate_study.yaml",
    "configs/multidof_plant.yaml",
)

V1_PROTOCOL = EvidenceProtocol(
    version="v1",
    dataset_id="synthetic-feasible-v1",
    entrypoint=ROOT / "run_paper_evidence.py",
    raw_root=ROOT / "results" / "paper_evidence_v1" / "raw_runs",
    final_root=ROOT / "results" / "paper_evidence_v1",
    selection_validation_root=(
        ROOT / "runs" / "paper_evidence_v1" / "selection-validation"
    ),
    config_lock_path=ROOT / "config_lock.json",
    locked_selection_schema_version="otg.locked-selection.v1",
    config_defaults=V1_CONFIG_DEFAULTS,
    confirm_experiments=V1_CONFIRM_EXPERIMENTS,
    selection_consumer_configs=V1_SELECTION_CONSUMER_CONFIGS,
    default_split_manifest=ROOT / "split_manifest.json",
    protocol_document=ROOT / "EXPERIMENT_PROTOCOL.md",
)

# These paths are declarations only.  Phase 1 intentionally does not create or
# inspect any v2 manifest, lock, config, seed, or trajectory.


def _versioned_config_path(path: str, version: str) -> str:
    if path == "configs/locked_test_v1.yaml":
        return f"configs/locked_test_{version}.yaml"
    return path.removesuffix(".yaml") + f"_{version}.yaml"


def _v2_config_path(path: str) -> str:
    return _versioned_config_path(path, "v2")


V2_CONFIG_DEFAULTS = tuple(
    (
        command,
        path if command == "phase-a" else _v2_config_path(path),
    )
    for command, path in V1_CONFIG_DEFAULTS
)
V2_CONFIRM_EXPERIMENTS = tuple(
    (command, _v2_config_path(path), bundle)
    for command, path, bundle in V1_CONFIRM_EXPERIMENTS
    if command != "phase-a"
)
V2_SELECTION_CONSUMER_CONFIGS = tuple(
    _v2_config_path(path) for path in V1_SELECTION_CONSUMER_CONFIGS
)
V2_PROTOCOL = EvidenceProtocol(
    version="v2",
    dataset_id="synthetic-feasible-v2",
    entrypoint=ROOT / "run_paper_evidence_v2.py",
    raw_root=ROOT / "results" / "paper_evidence_v2" / "raw_runs",
    final_root=ROOT / "results" / "paper_evidence_v2",
    selection_validation_root=(
        ROOT / "runs" / "paper_evidence_v2" / "selection-validation"
    ),
    config_lock_path=ROOT / "config_lock_v2.json",
    locked_selection_schema_version="otg.locked-selection.v2",
    config_defaults=V2_CONFIG_DEFAULTS,
    confirm_experiments=V2_CONFIRM_EXPERIMENTS,
    selection_consumer_configs=V2_SELECTION_CONSUMER_CONFIGS,
    default_split_manifest=None,
    exposed_test_manifests=(ROOT / "split_manifest.json",),
    require_fresh_locked_test=True,
    protocol_document=ROOT / "EXPERIMENT_PROTOCOL_V2.md",
)


def _v3_config_path(path: str) -> str:
    return _versioned_config_path(path, "v3")


V3_CONFIG_DEFAULTS = tuple(
    (
        command,
        path if command == "phase-a" else _v3_config_path(path),
    )
    for command, path in V1_CONFIG_DEFAULTS
)
V3_CONFIRM_EXPERIMENTS = tuple(
    (command, _v3_config_path(path), bundle)
    for command, path, bundle in V1_CONFIRM_EXPERIMENTS
    if command != "phase-a"
)
V3_SELECTION_CONSUMER_CONFIGS = tuple(
    _v3_config_path(path) for path in V1_SELECTION_CONSUMER_CONFIGS
)
V3_PROTOCOL = EvidenceProtocol(
    version="v3",
    dataset_id="synthetic-feasible-v3",
    entrypoint=ROOT / "run_paper_evidence_v3.py",
    raw_root=ROOT / "results" / "paper_evidence_v3" / "raw_runs",
    final_root=ROOT / "results" / "paper_evidence_v3",
    selection_validation_root=(
        ROOT / "runs" / "paper_evidence_v3" / "selection-validation"
    ),
    config_lock_path=ROOT / "config_lock_v3.json",
    locked_selection_schema_version="otg.locked-selection.v3",
    config_defaults=V3_CONFIG_DEFAULTS,
    confirm_experiments=V3_CONFIRM_EXPERIMENTS,
    selection_consumer_configs=V3_SELECTION_CONSUMER_CONFIGS,
    default_split_manifest=None,
    exposed_test_manifests=(
        ROOT / "split_manifest.json",
        ROOT / "split_manifest_v2.json",
    ),
    require_fresh_locked_test=True,
    protocol_document=ROOT / "EXPERIMENT_PROTOCOL_V3.md",
)

# Public v1 aliases retained for scripts/tests importing the historical entrypoint.
RAW_ROOT = V1_PROTOCOL.raw_root
FINAL_ROOT = V1_PROTOCOL.final_root
SELECTION_VALIDATION_ROOT = V1_PROTOCOL.selection_validation_root
CONFIG_LOCK_PATH = V1_PROTOCOL.config_lock_path
LOCKED_SELECTION_SCHEMA_VERSION = V1_PROTOCOL.locked_selection_schema_version
LOCKED_SELECTION_REQUIRED_FIELDS = LOCKED_SELECTION_COMMON_FIELDS
SELECTION_CONSUMER_CONFIGS = V1_PROTOCOL.selection_consumer_configs
CONFIRM_EXPERIMENTS = V1_PROTOCOL.confirm_experiments

FINAL_MANAGED_OUTPUTS = (
    "summaries",
    "statistics",
    "figures",
    "manifests",
    "README.md",
    "FAILURE_ANALYSIS.md",
    "protocol_hash.txt",
    "artifact_index.json",
    "artifact_index.sha256",
)


class SelectionLockError(RuntimeError):
    """Raised when a formal command could silently diverge from its lock."""


_ACTIVE_CONFIRM_CAPABILITY: object | None = None
_LOGICAL_COMMAND: tuple[str, ...] | None = None


def _commit() -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _command() -> list[str]:
    if _LOGICAL_COMMAND is not None:
        return [sys.executable, *_LOGICAL_COMMAND]
    return [sys.executable, str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]


def _protocol(args: argparse.Namespace | None = None) -> EvidenceProtocol:
    value = (
        getattr(args, "evidence_protocol", V1_PROTOCOL)
        if args is not None
        else V1_PROTOCOL
    )
    if not isinstance(value, EvidenceProtocol):
        raise SelectionLockError("invalid evidence protocol binding")
    return value


def _split_manifest_path(
    config: Mapping[str, Any],
    *,
    protocol: EvidenceProtocol = V1_PROTOCOL,
    repo_root: Path = ROOT,
) -> str:
    """Resolve a manifest only through the active protocol profile."""

    data = config.get("data")
    if not isinstance(data, Mapping):
        raise SelectionLockError("config.data must be a mapping")
    path = data.get("split_manifest")
    if path is None and protocol.default_split_manifest is not None:
        return str(protocol.default_split_manifest)
    if not isinstance(path, str) or not path.strip():
        raise SelectionLockError(
            f"protocol {protocol.version} requires config.data.split_manifest; "
            "no cross-version default is permitted"
        )
    return str(_repo_path(path, repo_root=repo_root))


def _assert_clean_committed_file(
    path: str | Path,
    *,
    repo_root: Path = ROOT,
    expected_sha256: str | None = None,
) -> str:
    """Require a tracked file from the current clean commit and optional lock hash."""

    root = repo_root.resolve()
    target = Path(path).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise SelectionLockError(
            f"formal manifest must be inside the repository: {target}"
        ) from error
    state = assert_clean_commit(root)
    tracked = subprocess.run(
        ("git", "ls-files", "--error-unmatch", "--", relative.as_posix()),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise SelectionLockError(
            f"formal manifest is not tracked at commit {state.commit}: {relative}"
        )
    if not target.is_file():
        raise SelectionLockError(f"formal manifest is missing: {target}")
    observed = sha256_file(target)
    if expected_sha256 is not None and observed != expected_sha256:
        raise SelectionLockError(
            f"formal manifest hash mismatch for {relative}: "
            f"expected {expected_sha256}, observed {observed}"
        )
    return observed


def _assert_fresh_test_manifest(
    config: Mapping[str, Any],
    *,
    protocol: EvidenceProtocol,
    repo_root: Path = ROOT,
) -> None:
    """Fail before generation unless the fresh test manifest is clean and locked."""

    if not protocol.require_fresh_locked_test:
        return
    lock_path = protocol.config_lock_path
    _assert_clean_committed_file(lock_path, repo_root=repo_root)
    lock = _load_json_mapping(lock_path, label=f"{protocol.version} config lock")
    if lock.get("locked") is not True or str(lock.get("selection_status")) not in {
        "locked",
        "locked_after_validation",
    }:
        raise SelectionLockError(
            f"{lock_path} does not authorize test generation: selection must be "
            "committed with locked=true and selection_status=locked_after_validation"
        )
    synthetic = lock.get("synthetic_dataset")
    if not isinstance(synthetic, Mapping):
        raise SelectionLockError(
            f"{lock_path} must lock synthetic_dataset manifest provenance"
        )
    locked_path = synthetic.get("split_manifest")
    locked_hash = synthetic.get("split_manifest_sha256")
    if not isinstance(locked_path, str) or not isinstance(locked_hash, str):
        raise SelectionLockError(
            f"{lock_path} must contain split_manifest and split_manifest_sha256"
        )
    configured_path = Path(
        _split_manifest_path(config, protocol=protocol, repo_root=repo_root)
    ).resolve()
    expected_path = _repo_path(locked_path, repo_root=repo_root)
    if configured_path != expected_path:
        raise SelectionLockError(
            f"configured test manifest {configured_path} differs from lock {expected_path}"
        )
    _assert_clean_committed_file(
        configured_path,
        repo_root=repo_root,
        expected_sha256=locked_hash,
    )
    try:
        validate_fresh_locked_test_manifest(
            configured_path,
            exposed_manifest_paths=protocol.exposed_test_manifests,
        )
    except ValueError as error:
        raise SelectionLockError(f"fresh test manifest rejected: {error}") from error


def _synthetic_cases_for_config(
    config: Mapping[str, Any],
    split: str,
    *,
    sample_rate_hz: float,
    maximum: int | None,
    protocol: EvidenceProtocol = V1_PROTOCOL,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Generate only from the manifest committed for this protocol version."""

    if split not in {"train", "validation", "test"}:
        raise SelectionLockError(f"unsupported clean-data split: {split!r}")
    if split == "test":
        _assert_test_generation_capability(protocol)
        _assert_fresh_test_manifest(config, protocol=protocol)
    return synthetic_cases(
        split,
        sample_rate_hz=sample_rate_hz,
        maximum=maximum,
        manifest_path=_split_manifest_path(config, protocol=protocol),
        run_id=str(config["run_id"]),
    )


def _canonical_selection_text(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise SelectionLockError(
            f"locked selection is not canonical JSON: {error}"
        ) from error


def _validate_locked_selection(
    value: Any,
    *,
    source: str,
    protocol: EvidenceProtocol = V1_PROTOCOL,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SelectionLockError(f"{source} has no complete locked_selection mapping")
    locked = dict(value)
    required = LOCKED_SELECTION_COMMON_FIELDS | (
        {"qp_baseline_status"} if protocol.version != "v1" else set()
    )
    missing = required - set(locked)
    extra = set(locked) - required
    if missing or extra:
        raise SelectionLockError(
            f"{source} locked_selection schema differs: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if locked["schema_version"] != protocol.locked_selection_schema_version:
        raise SelectionLockError(
            f"{source} locked_selection.schema_version must be "
            f"{protocol.locked_selection_schema_version}"
        )
    if locked["selection_split"] != "validation":
        raise SelectionLockError(f"{source} selection_split must be validation")
    if locked["test_trajectory_count_seen"] != 0:
        raise SelectionLockError(f"{source} must record test_trajectory_count_seen=0")
    for field in ("estimator", "estimator_id", "predictor", "prediction_objective"):
        if not isinstance(locked[field], str) or not locked[field]:
            raise SelectionLockError(f"{source} {field} must be a non-empty string")
    if not isinstance(locked["estimator_parameters"], Mapping):
        raise SelectionLockError(f"{source} estimator_parameters must be a mapping")
    downstream = locked["downstream_estimators"]
    if (
        not isinstance(downstream, Sequence)
        or isinstance(downstream, (str, bytes))
        or not 2 <= len(downstream) <= 3
        or any(not isinstance(item, Mapping) for item in downstream)
    ):
        raise SelectionLockError(
            f"{source} downstream_estimators must contain the locked top 2 or top 3"
        )
    ranks = [item.get("selection_rank") for item in downstream]
    if ranks != list(range(1, len(downstream) + 1)):
        raise SelectionLockError(
            f"{source} downstream estimator ranks must be contiguous from 1"
        )
    horizon = locked["prediction_horizon_ms"]
    if isinstance(horizon, bool) or not isinstance(horizon, (int, float)):
        raise SelectionLockError(f"{source} prediction_horizon_ms must be numeric")
    if not np.isfinite(float(horizon)) or float(horizon) < 0.0:
        raise SelectionLockError(
            f"{source} prediction_horizon_ms must be finite and non-negative"
        )
    qp_steps = locked["qp_horizon_steps"]
    qp_status = str(locked.get("qp_baseline_status", "qualified"))
    if qp_status not in {"qualified", "unqualified"}:
        raise SelectionLockError(
            f"{source} qp_baseline_status must be qualified or unqualified"
        )
    if qp_status == "unqualified":
        if protocol.version == "v1" or qp_steps is not None:
            raise SelectionLockError(
                f"{source} unqualified QP must have qp_horizon_steps=null"
            )
    elif isinstance(qp_steps, bool) or not isinstance(qp_steps, int) or qp_steps < 1:
        raise SelectionLockError(
            f"{source} qualified qp_horizon_steps must be a positive integer"
        )
    if locked["minimum_duration_s"] != 0.01:
        raise SelectionLockError(f"{source} minimum_duration_s must equal 0.01")
    limits = locked["motion_limits"]
    if not isinstance(limits, Mapping) or dict(limits) != {
        "max_velocity": 4.1,
        "max_acceleration": 8.2,
        "max_jerk": 4000.0,
    }:
        raise SelectionLockError(
            f"{source} motion_limits differ from the formal limits"
        )
    _canonical_selection_text(locked)
    return locked


def _selection_difference(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> list[str]:
    fields = sorted(set(left) | set(right))
    return [
        field
        for field in fields
        if _canonical_selection_text({"value": left.get(field)})
        != _canonical_selection_text({"value": right.get(field)})
    ]


def _assert_same_locked_selection(
    observed: Any,
    expected: Any,
    *,
    observed_source: str,
    expected_source: str,
    protocol: EvidenceProtocol = V1_PROTOCOL,
) -> dict[str, Any]:
    observed_lock = _validate_locked_selection(
        observed, source=observed_source, protocol=protocol
    )
    expected_lock = _validate_locked_selection(
        expected, source=expected_source, protocol=protocol
    )
    if _canonical_selection_text(observed_lock) != _canonical_selection_text(
        expected_lock
    ):
        fields = _selection_difference(observed_lock, expected_lock)
        raise SelectionLockError(
            f"locked selection mismatch between {observed_source} and "
            f"{expected_source}; differing top-level fields={fields}"
        )
    return expected_lock


def _repo_path(path: str | Path, *, repo_root: Path = ROOT) -> Path:
    candidate = Path(path)
    return (
        candidate.resolve()
        if candidate.is_absolute()
        else (repo_root / candidate).resolve()
    )


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise SelectionLockError(f"missing {label}: {path}")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, Mapping):
        raise SelectionLockError(f"{label} must contain a JSON object: {path}")
    return dict(value)


def _tracked_implementation_paths(repo_root: Path = ROOT) -> frozenset[str]:
    """Return the exact tracked Python implementation scope for a formal lock."""

    result = subprocess.run(
        ("git", "ls-files", "-z", "--", "otg_lab", "target_state_experiment.py"),
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    paths = {
        value.decode()
        for value in result.stdout.split(b"\0")
        if value and value.decode().endswith(".py")
    }
    if not paths:
        raise SelectionLockError("tracked implementation scope is empty")
    return frozenset(paths)


def _locked_protocol_input_hashes(lock: Mapping[str, Any]) -> dict[str, str]:
    """Flatten every protocol/config/code input covered by a formal lock."""

    try:
        protocol = lock["protocol"]
        entrypoints = lock["entrypoints"]
        development = lock["development_config"]
        synthetic = lock["synthetic_dataset"]
        formal = lock["formal_config_sha256"]
        implementation = lock["implementation_files_sha256"]
        workflow = lock["workflow_files_sha256"]
        data_files = lock["data_files_sha256"]
        wrapper_path = entrypoints.get("protocol_wrapper", entrypoints.get("v2_wrapper"))
        wrapper_hash = entrypoints.get(
            "protocol_wrapper_sha256", entrypoints.get("v2_wrapper_sha256")
        )
        if not isinstance(wrapper_path, str) or not isinstance(wrapper_hash, str):
            raise KeyError("protocol_wrapper")
        pairs: dict[str, str] = {
            str(protocol["path"]): str(protocol["sha256"]),
            str(entrypoints["authoritative_implementation"]): str(
                entrypoints["authoritative_implementation_sha256"]
            ),
            wrapper_path: wrapper_hash,
            str(development["path"]): str(development["sha256"]),
            str(synthetic["config"]): str(synthetic["config_sha256"]),
            str(synthetic["generator"]): str(synthetic["generator_sha256"]),
            str(synthetic["split_manifest"]): str(synthetic["split_manifest_sha256"]),
        }
    except (KeyError, TypeError) as error:
        raise SelectionLockError(
            "config lock is missing complete protocol input provenance"
        ) from error
    if not isinstance(formal, Mapping) or not formal:
        raise SelectionLockError("config lock has no formal_config_sha256 mapping")
    if not isinstance(implementation, Mapping) or not implementation:
        raise SelectionLockError(
            "config lock has no implementation_files_sha256 mapping"
        )
    if not isinstance(workflow, Mapping) or not workflow:
        raise SelectionLockError("config lock has no workflow_files_sha256 mapping")
    if not isinstance(data_files, Mapping) or not data_files:
        raise SelectionLockError("config lock has no data_files_sha256 mapping")
    for path, digest in formal.items():
        pairs[str(path)] = str(digest)
    for path, digest in implementation.items():
        pairs[str(path)] = str(digest)
    for path, digest in workflow.items():
        pairs[str(path)] = str(digest)
    for path, digest in data_files.items():
        pairs[str(path)] = str(digest)
    for path, digest in pairs.items():
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise SelectionLockError(f"invalid locked SHA-256 for {path!r}")
    return pairs


def _verify_locked_protocol_inputs(
    *,
    protocol: EvidenceProtocol,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    """Verify the complete committed formal protocol tree at runtime."""

    lock_path = _repo_path(protocol.config_lock_path, repo_root=repo_root)
    _assert_clean_committed_file(lock_path, repo_root=repo_root)
    lock = _load_json_mapping(lock_path, label=f"{protocol.version} config lock")
    if lock.get("locked") is not True or str(lock.get("selection_status")) not in {
        "locked",
        "locked_after_validation",
    }:
        raise SelectionLockError(
            f"{lock_path} is not a completed selection lock; test access denied"
        )
    implementation = lock.get("implementation_files_sha256")
    if not isinstance(implementation, Mapping):
        raise SelectionLockError("config lock has no implementation file scope")
    expected_scope = _tracked_implementation_paths(repo_root)
    observed_scope = frozenset(str(path) for path in implementation)
    if observed_scope != expected_scope:
        missing = sorted(expected_scope - observed_scope)
        extra = sorted(observed_scope - expected_scope)
        raise SelectionLockError(
            "implementation hash scope differs from the exact tracked Python set: "
            f"missing={missing}, extra={extra}"
        )
    formal = lock.get("formal_config_sha256")
    if not isinstance(formal, Mapping):
        raise SelectionLockError("config lock has no formal config hash scope")
    expected_formal_scope = frozenset(
        {
            protocol.config_for("validation"),
            *protocol.selection_consumer_configs,
        }
    )
    observed_formal_scope = frozenset(str(path) for path in formal)
    if observed_formal_scope != expected_formal_scope:
        missing = sorted(expected_formal_scope - observed_formal_scope)
        extra = sorted(observed_formal_scope - expected_formal_scope)
        raise SelectionLockError(
            "formal config hash scope differs from the registered suite: "
            f"missing={missing}, extra={extra}"
        )
    workflow = lock.get("workflow_files_sha256")
    if not isinstance(workflow, Mapping):
        raise SelectionLockError("config lock has no workflow file hash scope")
    expected_workflow_scope = frozenset({".gitignore"})
    observed_workflow_scope = frozenset(str(path) for path in workflow)
    if observed_workflow_scope != expected_workflow_scope:
        missing = sorted(expected_workflow_scope - observed_workflow_scope)
        extra = sorted(observed_workflow_scope - expected_workflow_scope)
        raise SelectionLockError(
            "workflow hash scope differs from the exact confirmation support set: "
            f"missing={missing}, extra={extra}"
        )
    data_files = lock.get("data_files_sha256")
    if not isinstance(data_files, Mapping):
        raise SelectionLockError("config lock has no data file hash scope")
    expected_data_scope = frozenset(
        {
            "plot_data.csv",
            *(
                str(Path(path).resolve().relative_to(repo_root.resolve()))
                for path in protocol.exposed_test_manifests
            ),
        }
    )
    observed_data_scope = frozenset(str(path) for path in data_files)
    if observed_data_scope != expected_data_scope:
        missing = sorted(expected_data_scope - observed_data_scope)
        extra = sorted(observed_data_scope - expected_data_scope)
        raise SelectionLockError(
            "data hash scope differs from the exact formal/exposed input set: "
            f"missing={missing}, extra={extra}"
        )
    for relative, expected in _locked_protocol_input_hashes(lock).items():
        _assert_clean_committed_file(
            _repo_path(relative, repo_root=repo_root),
            repo_root=repo_root,
            expected_sha256=expected,
        )
    return lock


def _assert_test_generation_capability(protocol: EvidenceProtocol) -> None:
    """Reject direct fresh-test helper calls outside an active confirm call."""

    if not protocol.require_fresh_locked_test:
        return
    if _ACTIVE_CONFIRM_CAPABILITY is None:
        raise SelectionLockError(
            f"{protocol.version} test generation is available only inside the "
            "active one-shot confirm workflow"
        )


def _require_confirmation_context(
    args: argparse.Namespace,
    *,
    protocol: EvidenceProtocol,
) -> None:
    """Fail before config loading or trajectory generation on direct test calls."""

    if not protocol.require_fresh_locked_test:
        return
    provided = getattr(args, "confirmation_capability", None)
    if (
        not bool(getattr(args, "confirmation_run", False))
        or provided is None
        or _ACTIVE_CONFIRM_CAPABILITY is None
        or provided is not _ACTIVE_CONFIRM_CAPABILITY
    ):
        raise SelectionLockError(
            f"{protocol.version} test-consuming commands may run only inside "
            "command_confirm"
        )
    if getattr(args, "output", None) is not None:
        raise SelectionLockError(
            f"{protocol.version} confirm forbids per-command --output overrides"
        )
    expected_config = protocol.config_for(str(args.command))
    if _repo_path(str(args.config)).resolve() != _repo_path(expected_config).resolve():
        raise SelectionLockError(
            f"{protocol.version} confirm requires the registered config "
            f"{expected_config!r}"
        )
    _verify_locked_protocol_inputs(protocol=protocol)
    _load_committed_selection_lock(protocol=protocol)


def _load_committed_selection_lock(
    *,
    repo_root: Path = ROOT,
    config_lock_path: str | Path | None = None,
    consumer_config_paths: Sequence[str | Path] | None = None,
    protocol: EvidenceProtocol = V1_PROTOCOL,
) -> dict[str, Any]:
    if config_lock_path is None:
        try:
            config_lock_path = protocol.config_lock_path.relative_to(ROOT)
        except ValueError:
            config_lock_path = protocol.config_lock_path.name
    if consumer_config_paths is None:
        consumer_config_paths = protocol.selection_consumer_configs
    lock_path = _repo_path(config_lock_path, repo_root=repo_root)
    lock_manifest = _load_json_mapping(lock_path, label="config lock")
    if lock_manifest.get("locked") is not True:
        raise SelectionLockError(
            "selection is not locked; run `uv run python run_paper_evidence.py "
            "selection-validation`, copy the emitted lock verbatim into every "
            "formal consumer config and config_lock.json, then commit it"
        )
    status = str(lock_manifest.get("selection_status", ""))
    if status not in {"locked", "locked_after_validation"}:
        raise SelectionLockError(
            "config_lock.json selection_status must be locked or "
            "locked_after_validation"
        )
    expected = _validate_locked_selection(
        lock_manifest.get("locked_selection"), source=str(lock_path), protocol=protocol
    )
    for configured_path in consumer_config_paths:
        path = _repo_path(configured_path, repo_root=repo_root)
        if not path.is_file():
            raise SelectionLockError(f"missing locked formal config: {path}")
        config = load_config(path)
        if not bool(config.get("formal") or config.get("require_clean")):
            raise SelectionLockError(
                f"selection consumer config is not clean-run protected: {path}"
            )
        _assert_same_locked_selection(
            config.get("locked_selection"),
            expected,
            observed_source=str(path),
            expected_source=str(lock_path),
            protocol=protocol,
        )
    return expected


def _selection(
    config: dict[str, Any], *, protocol: EvidenceProtocol = V1_PROTOCOL
) -> dict[str, Any]:
    locked = config.get("locked_selection")
    requires_lock = bool(config.get("formal") or config.get("require_clean"))
    if locked is None and requires_lock:
        source = str(config.get("_source_path", "formal config"))
        raise SelectionLockError(
            f"{source} cannot use pipeline defaults for a formal run; run "
            "`uv run python run_paper_evidence.py selection-validation`, commit "
            "the complete lock, and use `confirm`"
        )
    pipeline = config["pipeline"]
    if locked is None:
        return {
            "estimator": pipeline["estimator"],
            "estimator_parameters": pipeline.get("estimator_parameters", {}),
            "predictor": pipeline["predictor"],
            "horizon_ms": float(pipeline["prediction_horizon_ms"]),
            "qp_horizon_steps": 20,
        }
    normalized = _validate_locked_selection(
        locked,
        source=str(config.get("_source_path", "config")),
        protocol=protocol,
    )
    return {
        "estimator": normalized["estimator"],
        "estimator_parameters": dict(normalized["estimator_parameters"]),
        "predictor": normalized["predictor"],
        "horizon_ms": float(normalized["prediction_horizon_ms"]),
        "qp_horizon_steps": (
            None
            if normalized["qp_horizon_steps"] is None
            else int(normalized["qp_horizon_steps"])
        ),
        "qp_baseline_status": str(normalized.get("qp_baseline_status", "qualified")),
    }


def _output(args: argparse.Namespace, name: str, config: dict[str, Any]) -> Path:
    if args.output:
        return Path(args.output).resolve()
    return (
        ROOT / str(config.get("output_root", _protocol(args).raw_root)) / name
    ).resolve()


def _csv_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert research frames to rectangular, non-NaN CSV records."""

    records = []
    for raw in frame.to_dict(orient="records"):
        row = {}
        for field, value in raw.items():
            if isinstance(value, (dict, list, tuple)):
                row[field] = json.dumps(value, sort_keys=True, separators=(",", ":"))
            elif (
                value is None
                or value is pd.NA
                or (isinstance(value, (float, np.floating)) and not np.isfinite(value))
            ):
                row[field] = "unavailable"
            elif isinstance(value, np.generic):
                row[field] = value.item()
            else:
                row[field] = value
        records.append(row)
    return records


def _qualification_failure_reasons_text(reasons: Sequence[str]) -> str:
    """Serialize an empty qualified-result diagnostic without an ambiguous CSV cell."""

    return ";".join(reasons) if reasons else "none"


def _estimator_parameter_grid() -> list[dict[str, Any]]:
    grid: list[dict[str, Any]] = [
        {"method": "position_only", "id": "position_only", "params": {}},
        {
            "method": "raw_backward_difference",
            "id": "raw_backward_difference",
            "params": {},
        },
        {
            "method": "delay_one_centered_difference",
            "id": "delay_one_centered_difference",
            "params": {},
        },
    ]
    for window in (5, 7, 9, 11):
        for degree in (2, 3):
            grid.append(
                {
                    "method": "local_poly",
                    "id": f"local_poly_w{window}_d{degree}_lag1",
                    "params": {
                        "window": window,
                        "degree": degree,
                        "lag_samples": 1,
                    },
                }
            )
    grid.append(
        {
            "method": "alpha_beta_gamma",
            "id": "alpha_beta_gamma_default",
            "params": {"alpha": 0.401, "beta": 0.11528, "gamma": 0.009504},
        }
    )
    for sigma in (1e-6, 1e-4, 1e-2):
        for spectral_density in (10.0, 100.0, 1000.0):
            label = f"s{sigma:g}_q{spectral_density:g}"
            parameters = {
                "measurement_sigma": sigma,
                "jerk_spectral_density": spectral_density,
            }
            grid.append(
                {"method": "ca_kf", "id": f"ca_kf_{label}", "params": parameters}
            )
            for innovation_limit in (2.5, 3.0):
                grid.append(
                    {
                        "method": "robust_ca_kf",
                        "id": f"robust_ca_kf_{label}_i{innovation_limit:g}",
                        "params": {
                            **parameters,
                            "innovation_sigma_limit": innovation_limit,
                        },
                    }
                )
    for snap_density in (100.0, 1000.0, 10000.0):
        grid.append(
            {
                "method": "cj_kf",
                "id": f"cj_kf_q{snap_density:g}",
                "params": {
                    "measurement_sigma": 1e-4,
                    "snap_spectral_density": snap_density,
                },
            }
        )
    for frequency in (1.0, 2.0, 4.0):
        grid.append(
            {
                "method": "jerk_limited_differentiator",
                "id": f"jerk_limited_f{frequency:g}",
                "params": {"frequency_hz": frequency},
            }
        )
    return grid


def _validation_selection_design(config: Mapping[str, Any]) -> dict[str, Any]:
    try:
        selection = config["selection"]
        matrix = config["matrix"]
    except KeyError as error:
        raise SelectionLockError(
            f"validation config is missing section {error.args[0]!r}"
        ) from error
    if not isinstance(selection, Mapping) or not isinstance(matrix, Mapping):
        raise SelectionLockError(
            "validation selection/matrix sections must be mappings"
        )

    allowed_predictors = {
        "zero_order_hold",
        "constant_velocity",
        "constant_acceleration",
        "constant_jerk",
        "local_polynomial",
    }
    raw_predictors = matrix.get("predictors")
    if (
        not isinstance(raw_predictors, Sequence)
        or isinstance(raw_predictors, (str, bytes))
        or not raw_predictors
        or any(not isinstance(value, str) or not value for value in raw_predictors)
    ):
        raise SelectionLockError("matrix.predictors must be a non-empty string list")
    predictors = tuple(str(value) for value in raw_predictors)
    if len(set(predictors)) != len(predictors):
        raise SelectionLockError("matrix.predictors contains duplicates")
    unsupported_predictors = set(predictors) - allowed_predictors
    if unsupported_predictors:
        raise SelectionLockError(
            "validation matrix contains unsupported/non-deployable predictors: "
            f"{sorted(unsupported_predictors)}"
        )

    def horizons(field: str, *, required: bool) -> tuple[float, ...]:
        raw = selection.get(field)
        if (
            not isinstance(raw, Sequence)
            or isinstance(raw, (str, bytes))
            or (required and not raw)
        ):
            raise SelectionLockError(f"selection.{field} must be a non-empty list")
        values: list[float] = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SelectionLockError(f"selection.{field} must be numeric")
            numeric = float(value)
            if not np.isfinite(numeric) or numeric < 0.0:
                raise SelectionLockError(
                    f"selection.{field} must contain finite non-negative values"
                )
            values.append(numeric)
        if len(set(values)) != len(values) or values != sorted(values):
            raise SelectionLockError(f"selection.{field} must be unique and increasing")
        return tuple(values)

    primary_horizons = horizons("horizons_ms", required=True)
    stress_horizons = horizons("stress_horizons_ms", required=True)
    if set(primary_horizons) & set(stress_horizons):
        raise SelectionLockError(
            "primary and stress prediction horizons must be disjoint"
        )
    if stress_horizons[0] <= primary_horizons[-1]:
        raise SelectionLockError(
            "stress prediction horizons must lie above the primary selection range"
        )
    positive_horizons = tuple(
        value for value in (*primary_horizons, *stress_horizons) if value > 0.0
    )
    if not positive_horizons:
        raise SelectionLockError(
            "validation design needs a positive prediction horizon"
        )

    top_k = selection.get("downstream_estimators")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k not in {2, 3}:
        raise SelectionLockError("selection.downstream_estimators must be 2 or 3")
    raw_qp_steps = selection.get("qp_horizon_steps")
    if (
        not isinstance(raw_qp_steps, Sequence)
        or isinstance(raw_qp_steps, (str, bytes))
        or not raw_qp_steps
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in raw_qp_steps
        )
    ):
        raise SelectionLockError(
            "selection.qp_horizon_steps must be a non-empty positive integer list"
        )
    qp_horizon_steps = tuple(int(value) for value in raw_qp_steps)
    if len(set(qp_horizon_steps)) != len(qp_horizon_steps):
        raise SelectionLockError("selection.qp_horizon_steps contains duplicates")

    return {
        "predictors": predictors,
        "primary_horizons_ms": primary_horizons,
        "stress_horizons_ms": stress_horizons,
        "positive_horizons_ms": positive_horizons,
        "downstream_estimators": int(top_k),
        "qp_horizon_steps": qp_horizon_steps,
    }


def _flatten_cases(
    cases: list[tuple[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    return [row for _, rows in cases for row in rows]


def _metrics_for_declared_split(
    metrics: pd.DataFrame, split: str, *, context: str
) -> pd.DataFrame:
    """Return an explicit selection slice before invoking strict rankers.

    A grid may be evaluated on train and validation together for diagnostics,
    while a ranker is deliberately allowed to see only its declared selection
    split.  Keeping this boundary explicit prevents both accidental train-row
    rejection and accidental cross-split ranking.
    """

    if split == "test":
        raise SelectionLockError(f"{context}: test cannot be a selection split")
    if split not in {"train", "validation"}:
        raise SelectionLockError(
            f"{context}: selection split must be train or validation, got {split!r}"
        )
    if "split" not in metrics:
        raise SelectionLockError(f"{context}: metrics have no split column")
    selected = metrics.loc[metrics["split"].astype(str).eq(split)].copy()
    if selected.empty:
        observed = sorted(set(metrics["split"].astype(str)))
        raise SelectionLockError(
            f"{context}: no {split!r} rows; observed splits={observed}"
        )
    return selected


def _write_bundle(
    path: Path,
    config: dict[str, Any],
    outcome: ExperimentOutcome,
    *,
    split: str,
    rates: list[float],
    source: str,
    policy: str,
    extra_csv: dict[str, list[dict[str, Any]]] | None = None,
    extra_json: dict[str, Any] | None = None,
    extra_parquet: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    return write_experiment_bundle(
        path,
        config,
        outcome,
        command=_command(),
        repo_root=ROOT,
        split=split,
        sample_rates_hz=rates,
        source=source,
        selection_policy=policy,
        expected_commit=_commit(),
        require_clean=bool(config.get("require_clean", config.get("formal", False))),
        extra_csv=extra_csv,
        extra_json=extra_json,
        extra_parquet=extra_parquet,
    )


def _same_information_methods_for_lock(
    chosen: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the primary matrix without promoting an unqualified QP baseline."""

    qp_status = str(chosen.get("qp_baseline_status", "qualified"))
    qp_steps = chosen.get("qp_horizon_steps")
    if qp_status == "qualified" and (
        isinstance(qp_steps, bool) or not isinstance(qp_steps, int) or qp_steps < 1
    ):
        raise SelectionLockError("qualified QP baseline lacks a locked horizon")
    methods = same_information_methods(
        estimator=str(chosen["estimator"]),
        estimator_parameters=dict(chosen["estimator_parameters"]),
        predictor=str(chosen["predictor"]),
        horizon_ms=float(chosen["horizon_ms"]),
        qp_horizon_steps=int(qp_steps) if qp_status == "qualified" else 1,
    )
    if qp_status == "unqualified":
        methods = [
            method
            for method in methods
            if str(method["pipeline"].get("governor")) != "jerk_qp"
        ]
    return methods


def command_smoke(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    config = load_config(args.config)
    cases = _synthetic_cases_for_config(
        config,
        "validation",
        sample_rate_hz=100.0,
        maximum=1,
        protocol=protocol,
    )
    chosen = _selection(config, protocol=protocol)
    outcome = run_pipeline_matrix(
        cases,
        config,
        [
            locked_method(
                **{
                    key: value
                    for key, value in chosen.items()
                    if key != "qp_horizon_steps"
                }
            )
        ],
    )
    path = _output(args, "ci-smoke", config)
    return write_experiment_bundle(
        path,
        config,
        outcome,
        command=_command(),
        repo_root=ROOT,
        split="validation",
        sample_rates_hz=[100.0],
        source=protocol.dataset_id,
        selection_policy="first frozen validation trajectory; smoke only",
        require_clean=False,
    )


def command_validation(args: argparse.Namespace) -> dict[str, Any]:
    """Run train/validation-only parameter and horizon selection."""

    protocol = _protocol(args)
    selection_only = getattr(args, "command", None) == "selection-validation"
    confirmation_run = bool(getattr(args, "confirmation_run", False))
    if not selection_only and not confirmation_run:
        raise SelectionLockError(
            "the formal validation bundle is created only inside one-time `confirm`; "
            "before locking, run `uv run python run_paper_evidence.py "
            "selection-validation --config configs/validation.yaml`"
        )
    if selection_only:
        if not getattr(args, "output", None):
            args.output = str(protocol.selection_validation_root)
        requested_output = Path(args.output).resolve()
        if requested_output == protocol.raw_root or requested_output.is_relative_to(
            protocol.raw_root
        ):
            raise SelectionLockError(
                "selection-validation output must be outside the formal raw_runs tree"
            )
    else:
        _load_committed_selection_lock(protocol=protocol)

    config = load_config(args.config)
    selection_design = _validation_selection_design(config)
    train_rows = _flatten_cases(
        _synthetic_cases_for_config(
            config,
            "train",
            sample_rate_hz=100.0,
            maximum=None,
            protocol=protocol,
        )
    )
    validation_cases = _synthetic_cases_for_config(
        config,
        "validation",
        sample_rate_hz=100.0,
        maximum=None,
        protocol=protocol,
    )
    validation_rows = _flatten_cases(validation_cases)
    selection_rows = [*train_rows, *validation_rows]

    estimator_metrics = run_estimator_grid(
        selection_rows,
        _estimator_parameter_grid(),
        selection_splits=("train", "validation"),
    )
    validation_estimator_metrics = _metrics_for_declared_split(
        estimator_metrics,
        "validation",
        context="estimator ranking",
    )
    estimator_ranking = rank_estimator_grid(
        validation_estimator_metrics,
        selection_splits=("validation",),
    )
    estimator_lock = lock_estimator_parameters(
        estimator_ranking,
        top_k=selection_design["downstream_estimators"],
    )

    predictor_metrics = run_predictor_horizon_sweep(
        validation_rows,
        estimator_lock,
        predictor_grid=selection_design["predictors"],
        horizons_ms=selection_design["primary_horizons_ms"],
        stress_horizons_ms=selection_design["stress_horizons_ms"],
        selection_splits=("validation",),
    )
    predictor_ranking = rank_prediction_horizons(
        predictor_metrics,
        selection_splits=("validation",),
    )
    primary_estimator = estimator_lock["locked_estimators"][0]
    eligible = predictor_ranking[
        predictor_ranking["estimator_id"]
        .astype(str)
        .eq(str(primary_estimator["estimator_id"]))
        & predictor_ranking["eligible_for_selection"].astype(bool)
    ].copy()
    eligible = eligible.sort_values(
        ["objective_mean", "configured_horizon_ms", "predictor_id"],
        kind="stable",
    )
    if eligible.empty:
        raise RuntimeError("validation produced no deployable predictor/horizon cell")
    selected_prediction = eligible.iloc[0]
    selected_predictor = str(selected_prediction["predictor_id"])
    selected_horizon_ms = float(selected_prediction["configured_horizon_ms"])

    t_free_metrics, t_free_samples = evaluate_locked_predictor(
        validation_rows,
        [primary_estimator],
        [{"method": selected_predictor, "id": selected_predictor, "params": {}}],
        horizons_ms=selection_design["positive_horizons_ms"],
        free_duration_fn=ruckig_unconstrained_free_duration,
        return_canonical_rows=True,
    )

    qp_methods = []
    for horizon_steps in selection_design["qp_horizon_steps"]:
        method = locked_method(
            estimator=str(primary_estimator["estimator"]),
            estimator_parameters=dict(primary_estimator["estimator_parameters"]),
            predictor=selected_predictor,
            horizon_ms=selected_horizon_ms,
            method_id=f"jerk_qp_n{int(horizon_steps)}",
        )
        method["pipeline"].update(
            {
                "governor": "jerk_qp",
                "governor_parameters": {"horizon_steps": int(horizon_steps)},
                "follower": "direct",
            }
        )
        qp_methods.append(method)
    qp_outcome = run_pipeline_matrix(validation_cases, config, qp_methods)
    qp_metrics = pd.DataFrame(
        metrics_by_trajectory(
            qp_outcome.samples,
            motion_limits=config["limits"],
        )
    )
    audit_violations = (
        pd.DataFrame(qp_outcome.constraint_audits)
        .groupby("method_id", as_index=False)["violation_count"]
        .sum()
    )
    qp_ranking = (
        qp_metrics.groupby("method", as_index=False)
        .agg(
            n_trajectories=("trajectory_id", "nunique"),
            mean_position_rmse=("position_rmse", "mean"),
            mean_fallback_rate=("fallback_rate", "mean"),
            mean_total_p99_us=("total_p99_us", "mean"),
        )
        .merge(audit_violations, left_on="method", right_on="method_id")
        .sort_values(
            [
                "violation_count",
                "mean_fallback_rate",
                "mean_position_rmse",
                "mean_total_p99_us",
                "method",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )
    qp_samples_by_method = {
        method_id: [
            row for row in qp_outcome.samples if str(row.get("method_id")) == method_id
        ]
        for method_id in sorted(
            {str(row.get("method_id")) for row in qp_outcome.samples}
        )
    }
    qp_gate = select_qualified_qp(qp_samples_by_method)
    qualification_by_method = {
        method_id: result.as_dict()
        for method_id, result in qp_gate.qualifications.items()
    }
    qp_ranking.insert(0, "rank", np.arange(1, len(qp_ranking) + 1))
    qp_ranking["qualification_status"] = qp_ranking["method"].map(
        lambda method_id: qualification_by_method[str(method_id)]["qp_baseline_status"]
    )
    qp_ranking["qualification_failure_reasons"] = qp_ranking["method"].map(
        lambda method_id: _qualification_failure_reasons_text(
            qualification_by_method[str(method_id)]["failure_reasons"]
        )
    )
    if protocol.version == "v1":
        # Historical v1 selection remains byte-compatible regression evidence.
        selected_qp_method = str(qp_ranking.iloc[0]["method"])
        qp_baseline_status = qp_gate.qp_baseline_status
    else:
        selected_qp_method = qp_gate.selected_method_id
        qp_baseline_status = qp_gate.qp_baseline_status
    qp_ranking["selected"] = (
        qp_ranking["method"].astype(str).eq(str(selected_qp_method))
        if selected_qp_method is not None
        else False
    )
    selected_qp_steps = (
        None
        if selected_qp_method is None
        else int(str(selected_qp_method).rsplit("n", 1)[-1])
    )

    selected_method = locked_method(
        estimator=str(primary_estimator["estimator"]),
        estimator_parameters=dict(primary_estimator["estimator_parameters"]),
        predictor=selected_predictor,
        horizon_ms=selected_horizon_ms,
        method_id="validation_selected_one_step_direct",
    )
    selected_outcome = run_pipeline_matrix(validation_cases, config, [selected_method])
    outcome = combine_outcomes([selected_outcome, qp_outcome])
    locked_selection = {
        "schema_version": protocol.locked_selection_schema_version,
        "selection_split": "validation",
        "test_trajectory_count_seen": 0,
        "estimator": str(primary_estimator["estimator"]),
        "estimator_id": str(primary_estimator["estimator_id"]),
        "estimator_parameters": dict(primary_estimator["estimator_parameters"]),
        "downstream_estimators": estimator_lock["locked_estimators"],
        "predictor": selected_predictor,
        "prediction_horizon_ms": selected_horizon_ms,
        "prediction_objective": "prediction_p_rmse at prediction_time",
        "qp_horizon_steps": selected_qp_steps,
        "minimum_duration_s": 0.01,
        "motion_limits": dict(config["limits"]),
    }
    if protocol.version != "v1":
        locked_selection["qp_baseline_status"] = qp_baseline_status
    bundle_name = "selection-validation" if selection_only else "validation"
    output_path = _output(args, bundle_name, config)
    report = _write_bundle(
        output_path,
        config,
        outcome,
        split="validation",
        rates=[100.0],
        source=f"{protocol.dataset_id} train and validation only",
        policy="all 120 train and 60 validation trajectories; locked test excluded",
        extra_csv={
            "estimator_grid_metrics.csv": _csv_records(estimator_metrics),
            "estimator_validation_ranking.csv": _csv_records(estimator_ranking),
            "predictor_horizon_metrics.csv": _csv_records(predictor_metrics),
            "predictor_horizon_ranking.csv": _csv_records(predictor_ranking),
            "qp_validation_metrics.csv": _csv_records(qp_metrics),
            "qp_validation_ranking.csv": _csv_records(qp_ranking),
            "t_free_rho_validation_metrics.csv": _csv_records(t_free_metrics),
        },
        extra_json={
            "locked_selection.json": locked_selection,
            "selection_design.json": {
                "estimator_grid": _estimator_parameter_grid(),
                "predictors": list(selection_design["predictors"]),
                "primary_horizons_ms": list(selection_design["primary_horizons_ms"]),
                "stress_horizons_ms": list(selection_design["stress_horizons_ms"]),
                "downstream_estimators": selection_design["downstream_estimators"],
                "qp_horizon_steps": list(selection_design["qp_horizon_steps"]),
            },
            "qp_qualification.json": {
                "schema_version": "otg.qp-qualification.v1",
                "qp_baseline_status": qp_baseline_status,
                "selected_method_id": selected_qp_method,
                "candidates": qualification_by_method,
            },
        },
        extra_parquet={"t_free_rho_samples.parquet": t_free_samples},
    )
    result = {**report, "locked_selection": locked_selection}
    if selection_only:
        result.update(
            {
                "workflow_status": "selection_only_no_locked_test_run",
                "locked_selection_artifact": str(output_path / "locked_selection.json"),
                "next_step": (
                    "copy locked_selection.json verbatim into config_lock.json and "
                    "every formal selection-consumer config; commit and clean the "
                    "worktree; then run confirm"
                ),
            }
        )
    return result


def command_locked_test(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    _require_confirmation_context(args, protocol=protocol)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    cases = _synthetic_cases_for_config(
        config,
        "test",
        sample_rate_hz=100.0,
        maximum=None,
        protocol=protocol,
    )
    test_rows = _flatten_cases(cases)
    downstream_estimators = config.get("locked_selection", {}).get(
        "downstream_estimators",
        [
            {
                "method": chosen["estimator"],
                "estimator": chosen["estimator"],
                "estimator_id": chosen["estimator"],
                "params": chosen["estimator_parameters"],
            }
        ],
    )
    estimator_layer_metrics, estimator_layer_samples = evaluate_locked_estimator(
        test_rows,
        downstream_estimators,
        return_canonical_rows=True,
    )
    predictor_layer_metrics, predictor_layer_samples = evaluate_locked_predictor(
        test_rows,
        downstream_estimators,
        [{"method": chosen["predictor"], "id": chosen["predictor"], "params": {}}],
        horizons_ms=[chosen["horizon_ms"]],
        free_duration_fn=ruckig_unconstrained_free_duration,
        return_canonical_rows=True,
    )
    primary_methods = _same_information_methods_for_lock(chosen)
    methods = list(primary_methods)
    for rank, estimator_spec in enumerate(downstream_estimators[1:], start=2):
        secondary = _same_information_methods_for_lock(
            {
                **chosen,
                "estimator": str(estimator_spec["estimator"]),
                "estimator_parameters": dict(
                    estimator_spec.get(
                        "estimator_parameters", estimator_spec.get("params", {})
                    )
                ),
            }
        )
        for method in secondary:
            method["method_id"] = (
                f"estimator_rank_{rank}::{estimator_spec['estimator_id']}::"
                f"{method['method_id']}"
            )
        methods.extend(secondary)
    outcome = run_pipeline_matrix(cases, config, methods)
    primary_method_ids = {str(method["method_id"]) for method in primary_methods}
    primary_samples = [
        row for row in outcome.samples if str(row["method_id"]) in primary_method_ids
    ]
    governed_primary_samples = [
        row for row in primary_samples if str(row.get("governor_id")) != "none"
    ]
    frequency_samples = [
        row
        for row in primary_samples
        if str(row.get("reference_variant")) == "multi_sine"
    ]
    chirp_samples = [
        row for row in primary_samples if str(row.get("reference_variant")) == "chirp"
    ]
    local_delay_samples = [
        row
        for row in primary_samples
        if str(row.get("reference_variant")) in {"stop_and_go", "rapid_reversal"}
    ]
    runtime_samples, runtime_summaries = repeated_runtime_study(
        _synthetic_cases_for_config(
            config,
            "test",
            sample_rate_hz=100.0,
            maximum=6,
            protocol=protocol,
        ),
        config,
        primary_methods,
        repetitions=int(config["runtime"]["repetitions"]),
        warmup_cycles=int(config["runtime"]["warmup_cycles"]),
    )
    return _write_bundle(
        _output(args, "locked_test", config),
        config,
        outcome,
        split="test",
        rates=[100.0],
        source=protocol.dataset_id,
        policy="all 120 frozen test trajectories; no selection",
        extra_csv={
            "estimator_locked_test_metrics.csv": _csv_records(estimator_layer_metrics),
            "predictor_locked_test_metrics.csv": _csv_records(predictor_layer_metrics),
            "runtime_repetition_samples.csv": runtime_samples,
            "runtime_repetition_summary.csv": runtime_summaries,
            "governor_invariants.csv": governor_invariant_summaries(
                governed_primary_samples,
                motion_limits=config["limits"],
            ),
            "frequency_response.csv": synthetic_frequency_response(
                frequency_samples,
                output_field="command_p",
            ),
            "chirp_frequency_response.csv": synthetic_chirp_frequency_response(
                chirp_samples,
                output_field="command_p",
            ),
            "local_event_delay.csv": synthetic_local_delay(
                local_delay_samples,
                output_field="command_p",
            ),
        },
        extra_parquet={
            "estimator_layer_samples.parquet": estimator_layer_samples,
            "predictor_layer_samples.parquet": predictor_layer_samples,
        },
    )


def command_governor_infeasible(args: argparse.Namespace) -> dict[str, Any]:
    """Exercise governors on a separately labelled, deliberately invalid suite."""

    protocol = _protocol(args)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    negative_dataset_id = f"synthetic-deliberate-infeasible-{protocol.version}"
    suite = deliberate_infeasible_suite()
    cases = [
        (
            trajectory.trajectory_id,
            trajectory_to_rows(
                trajectory,
                sample_rate_hz=100.0,
                run_id=str(config["run_id"]),
                dataset_id=negative_dataset_id,
                session_id="governor-negative-suite",
            ),
        )
        for trajectory in suite
    ]
    common = {
        "estimator": "position_only",
        "estimator_parameters": {},
        "predictor": "oracle",
        "predictor_parameters": {},
        "prediction_horizon_ms": 10.0,
        "target_mode": "pva",
        "plant": "ideal",
        "plant_parameters": {},
        "measured_state_mode": "previous_command",
    }
    methods = [
        {
            "method_id": "infeasible_raw_ruckig",
            "pipeline": {**common, "governor": "none", "follower": "ruckig"},
        },
        {
            "method_id": "infeasible_scalar_ruckig",
            "pipeline": {
                **common,
                "governor": "scalar_projection",
                "follower": "ruckig",
            },
        },
        {
            "method_id": "infeasible_one_step_direct",
            "pipeline": {**common, "governor": "one_step", "follower": "direct"},
        },
        {
            "method_id": "infeasible_one_step_ruckig",
            "pipeline": {**common, "governor": "one_step", "follower": "ruckig"},
        },
    ]
    if chosen.get("qp_baseline_status", "qualified") == "qualified":
        methods.extend(
            [
                {
                    "method_id": "infeasible_qp_direct",
                    "pipeline": {
                        **common,
                        "governor": "jerk_qp",
                        "governor_parameters": {
                            "horizon_steps": int(chosen["qp_horizon_steps"])
                        },
                        "follower": "direct",
                    },
                },
                {
                    "method_id": "infeasible_qp_ruckig",
                    "pipeline": {
                        **common,
                        "governor": "jerk_qp",
                        "governor_parameters": {
                            "horizon_steps": int(chosen["qp_horizon_steps"])
                        },
                        "follower": "ruckig",
                    },
                },
            ]
        )
    outcome = run_pipeline_matrix(cases, config, methods)
    governed_samples = [
        row for row in outcome.samples if str(row.get("governor_id")) != "none"
    ]
    invariant_rows = governor_invariant_summaries(
        governed_samples,
        motion_limits=config["limits"],
    )
    return _write_bundle(
        _output(args, "governor_infeasible", config),
        config,
        outcome,
        split="infeasible",
        rates=[100.0],
        source=f"{negative_dataset_id}; governor negative suite only",
        policy="complete four-case negative suite; isolated from estimator/predictor selection",
        extra_csv={"governor_invariants.csv": invariant_rows},
        extra_json={
            "infeasible_suite_manifest.json": {
                "schema_version": "otg.infeasible-suite.v1",
                "dataset_id": negative_dataset_id,
                "isolated_from_clean_benchmark": True,
                "cases": [
                    {
                        "trajectory_id": trajectory.trajectory_id,
                        "seed": trajectory.seed,
                        "scenario_id": trajectory.reference_variant,
                    }
                    for trajectory in suite
                ],
            }
        },
    )


def command_acceleration(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    _require_confirmation_context(args, protocol=protocol)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    study = config["acceleration_study"]
    designed_cases = acceleration_case_matrix(
        phases=study["phases"],
        r_a_values=study["r_a"],
        r_j_values=study["r_j"],
        directions=study["directions"],
    )
    configured_maximum = config["data"].get("max_trajectories")
    if configured_maximum is not None and int(configured_maximum) != len(
        designed_cases
    ):
        raise ValueError(
            "acceleration data.max_trajectories must equal the complete design size"
        )
    metadata = {
        case.trajectory.trajectory_id: acceleration_case_metadata(case)
        for case in designed_cases
    }
    cases = [
        (
            case.trajectory.trajectory_id,
            trajectory_to_rows(
                case.trajectory,
                sample_rate_hz=100.0,
                run_id=str(config["run_id"]),
                dataset_id="synthetic-acceleration-active-v1",
                session_id="synthetic-acceleration-active",
            ),
        )
        for case in designed_cases
    ]
    declared_conditions = tuple(str(value) for value in study["conditions"])
    required_conditions = {
        f"{time_mode}_{target_mode}"
        for time_mode in ("current", "next_cycle")
        for target_mode in ("p", "pv", "pva")
    }
    if (
        len(declared_conditions) != len(required_conditions)
        or set(declared_conditions) != required_conditions
    ):
        raise ValueError(
            "acceleration conditions must contain the complete unique current/"
            "next-cycle P/PV/PVA design"
        )
    methods = []
    for condition in declared_conditions:
        time_mode, target_mode = condition.rsplit("_", 1)
        horizon_ms = 0.0 if time_mode == "current" else 1000.0 * config["control"]["dt"]
        methods.append(
            {
                "method_id": f"oracle_{time_mode}_{target_mode}",
                "pipeline": {
                    "estimator": "position_only",
                    "estimator_parameters": {},
                    "predictor": "oracle",
                    "predictor_parameters": {},
                    "prediction_horizon_ms": horizon_ms,
                    "target_mode": target_mode,
                    "governor": "none",
                    "governor_parameters": {},
                    "follower": "ruckig",
                    "plant": "ideal",
                    "plant_parameters": {},
                    "measured_state_mode": "previous_command",
                },
            }
        )
    oracle_outcome = run_pipeline_matrix(cases, config, methods)
    estimated_common = {
        "estimator": chosen["estimator"],
        "estimator_parameters": chosen["estimator_parameters"],
        "predictor": chosen["predictor"],
        "predictor_parameters": {},
        "prediction_horizon_ms": chosen["horizon_ms"],
        "governor": "none",
        "governor_parameters": {},
        "follower": "ruckig",
        "plant": "ideal",
        "plant_parameters": {},
        "measured_state_mode": "previous_command",
    }
    estimated_methods = [
        {
            "method_id": "estimated_same_future_pv",
            "pipeline": {**estimated_common, "target_mode": "pv"},
        },
        {
            "method_id": "estimated_same_future_pva",
            "pipeline": {**estimated_common, "target_mode": "pva"},
        },
    ]
    estimated_outcome = run_pipeline_matrix(cases, config, estimated_methods)
    outcome = combine_outcomes([oracle_outcome, estimated_outcome])
    metrics = pd.DataFrame(
        metrics_by_trajectory(oracle_outcome.samples, motion_limits=config["limits"])
    )
    enriched = []
    for row in metrics.to_dict(orient="records"):
        method_id = str(row["method"])
        time_mode = "next_cycle" if "next_cycle" in method_id else "current"
        horizon_ms = 10.0 if time_mode == "next_cycle" else 0.0
        target_mode = method_id.rsplit("_", 1)[-1]
        case_metadata = metadata[str(row["trajectory_id"])]
        enriched.append(
            {
                **row,
                **case_metadata,
                "target_time_mode": time_mode,
                "configured_horizon_ms": horizon_ms,
                "target_mode": target_mode,
                "predictor_id": "oracle_future_state_offline",
                "future_position_key": (
                    f"{row['trajectory_id']}:{time_mode}:{horizon_ms:g}ms"
                ),
            }
        )
    enriched_frame = pd.DataFrame(enriched)
    phase_map, pairs = build_acceleration_phase_map(
        enriched_frame,
        expected_r_a=tuple(float(value) for value in study["r_a"]),
        expected_r_j=tuple(float(value) for value in study["r_j"]),
        return_pairs=True,
    )
    estimated_metrics = pd.DataFrame(
        metrics_by_trajectory(estimated_outcome.samples, motion_limits=config["limits"])
    )
    estimated_pairs = estimated_metrics.pivot(
        index=[
            "dataset_id",
            "session_id",
            "trajectory_id",
            "scenario_id",
            "reference_family",
        ],
        columns="method",
        values=["position_rmse", "lag_s", "position_max_abs_error"],
    ).reset_index()
    estimated_pairs.columns = [
        "_".join(str(part) for part in column if str(part))
        if isinstance(column, tuple)
        else str(column)
        for column in estimated_pairs.columns
    ]
    estimated_pair_records = []
    for row in estimated_pairs.to_dict(orient="records"):
        metadata_row = metadata[str(row["trajectory_id"])]
        pv_rmse = float(row["position_rmse_estimated_same_future_pv"])
        pva_rmse = float(row["position_rmse_estimated_same_future_pva"])
        relative_defined = pv_rmse > np.finfo(float).tiny
        estimated_pair_records.append(
            {
                **row,
                **metadata_row,
                "configured_horizon_ms": chosen["horizon_ms"],
                "estimator": chosen["estimator"],
                "predictor": chosen["predictor"],
                "pva_vs_pv_rmse_improvement": (
                    (pv_rmse - pva_rmse) / pv_rmse
                    if relative_defined
                    else "unavailable_zero_pv_baseline"
                ),
                "pva_vs_pv_absolute_rmse_difference": pva_rmse - pv_rmse,
                "relative_improvement_defined": relative_defined,
                "acceleration_target_harmful": bool(pva_rmse > pv_rmse),
            }
        )
    return _write_bundle(
        _output(args, "acceleration", config),
        config,
        outcome,
        split="test",
        rates=[100.0],
        source="analytic acceleration-active >=2 kHz truth; offline oracle labelled",
        policy="complete 7 phase x 4 r_a x 4 r_j x 2 direction matrix",
        extra_csv={
            "acceleration_oracle_metrics.csv": _csv_records(enriched_frame),
            "acceleration_phase_map.csv": _csv_records(phase_map),
            "acceleration_pv_pva_pairs.csv": _csv_records(pairs),
            "acceleration_estimated_metrics.csv": _csv_records(estimated_metrics),
            "acceleration_estimated_pv_pva_pairs.csv": estimated_pair_records,
        },
    )


def command_robustness(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    _require_confirmation_context(args, protocol=protocol)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    maximum = config["data"].get("max_trajectories")
    _assert_fresh_test_manifest(config, protocol=protocol)
    cases = stressed_cases(
        "test",
        default_stress_suite(seed=91000),
        sample_rate_hz=100.0,
        maximum=maximum,
        manifest_path=_split_manifest_path(config, protocol=protocol),
        run_id=str(config["run_id"]),
    )
    empirical_jitter = empirical_jitter_from_csv(
        ROOT / "plot_data.csv", expected_dt_s=0.01
    )
    for trajectory_id, rows in _synthetic_cases_for_config(
        config,
        "test",
        sample_rate_hz=100.0,
        maximum=maximum,
        protocol=protocol,
    ):
        empirical_rows = inject_timing(
            rows,
            seed=91080,
            empirical_jitter_s=empirical_jitter,
            scenario_id="empirical_plot_data_jitter",
        )
        cases.append((f"{trajectory_id}::empirical_plot_data_jitter", empirical_rows))
    all_methods = _same_information_methods_for_lock(chosen)
    methods = [
        method
        for method in all_methods
        if method["method_id"] in {"deployed_p_only", "one_step_governed_pva_direct"}
    ]
    outcome = run_pipeline_matrix(cases, config, methods)
    diagnostic_config = config["diagnostics"]
    diagnostic_arguments = {
        "output_field": str(diagnostic_config["recovery_output_field"]),
        "recovery_tolerance": float(diagnostic_config["recovery_tolerance_rad"]),
        "recovery_hold_samples": int(diagnostic_config["recovery_hold_samples"]),
        "pre_fault_window_s": float(diagnostic_config["pre_fault_window_s"]),
    }
    return _write_bundle(
        _output(args, "robustness", config),
        config,
        outcome,
        split="test",
        rates=[100.0],
        source=f"{protocol.dataset_id} with fixed replayable stress realizations",
        policy="family-balanced frozen prefix; all 26 predeclared stress scenarios",
        extra_csv={
            "robustness_fault_events.csv": robustness_fault_events(
                outcome.samples, **diagnostic_arguments
            ),
            "robustness_recovery_summary.csv": robustness_recovery_summaries(
                outcome.samples, **diagnostic_arguments
            ),
        },
    )


def command_rates(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    _require_confirmation_context(args, protocol=protocol)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    outcomes = []
    rates = [float(value) for value in config["rate_study"]["sample_rates_hz"]]
    maximum = config["data"].get("max_trajectories")
    for rate in rates:
        rate_config = copy.deepcopy(config)
        rate_config["formal"] = False
        rate_config["control"]["dt"] = 1.0 / rate
        rate_config["control"]["minimum_duration"] = 1.0 / rate
        rate_config["data"]["sample_rate_hz"] = rate
        cases = _synthetic_cases_for_config(
            config,
            "test",
            sample_rate_hz=rate,
            maximum=maximum,
            protocol=protocol,
        )
        for _, rows in cases:
            for row in rows:
                row["scenario_id"] = f"rate_{rate:g}hz"
        outcomes.append(
            run_pipeline_matrix(
                cases,
                rate_config,
                [
                    locked_method(
                        estimator=chosen["estimator"],
                        estimator_parameters=chosen["estimator_parameters"],
                        predictor=chosen["predictor"],
                        horizon_ms=chosen["horizon_ms"],
                    )
                ],
            )
        )
    outcome = combine_outcomes(outcomes)
    return _write_bundle(
        _output(args, "rate_study", config),
        config,
        outcome,
        split="test",
        rates=rates,
        source="independent resampling from >=1 kHz synthetic truth at each rate",
        policy="family-balanced frozen test prefix; rate is generalization only",
        extra_csv={
            "dimensionless_rate_constraints.csv": _csv_records(
                sampling_rate_dimensionless(
                    rates,
                    max_velocity=config["limits"]["max_velocity"],
                    max_acceleration=config["limits"]["max_acceleration"],
                    max_jerk=config["limits"]["max_jerk"],
                    primary_rate_hz=100.0,
                )
            )
        },
    )


def command_multidof(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    _require_confirmation_context(args, protocol=protocol)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    cases = []
    for dof in config["multidof"]["dofs"]:
        for pattern_index, pattern in enumerate(config["multidof"]["patterns"]):
            seed = 20260721 + 100 * int(dof) + pattern_index
            truth = generate_multidof_truth(int(dof), pattern, seed=seed)
            rows = multidof_to_rows(
                truth,
                sample_rate_hz=100.0,
                run_id=str(config["run_id"]),
            )
            cases.append((rows[0]["trajectory_id"], rows))
    methods = [
        locked_method(
            estimator=chosen["estimator"],
            estimator_parameters=chosen["estimator_parameters"],
            predictor=chosen["predictor"],
            horizon_ms=chosen["horizon_ms"],
        )
    ]
    outcome = run_pipeline_matrix(cases, config, methods)
    multidof_diagnostics = compute_multidof_tracking_diagnostics(outcome.samples)
    runtime_samples, runtime_summaries = repeated_runtime_study(
        cases,
        config,
        methods,
        repetitions=int(config["runtime"]["repetitions"]),
        warmup_cycles=int(config["runtime"]["warmup_cycles"]),
    )
    return _write_bundle(
        _output(args, "multidof", config),
        config,
        outcome,
        split="test",
        rates=[100.0],
        source="synchronized analytic multi-DoF synthetic truth",
        policy=f"complete predeclared DoF x pattern matrix: {list(PATTERNS)}",
        extra_csv={
            "runtime_repetition_samples.csv": runtime_samples,
            "runtime_repetition_summary.csv": runtime_summaries,
            "multidof_aligned_samples.csv": _csv_records(
                pd.DataFrame(multidof_diagnostics.aligned_samples)
            ),
            "multidof_per_joint_metrics.csv": _csv_records(
                pd.DataFrame(multidof_diagnostics.per_joint)
            ),
            "multidof_per_cycle_metrics.csv": _csv_records(
                pd.DataFrame(multidof_diagnostics.per_cycle)
            ),
            "multidof_summary.csv": _csv_records(
                pd.DataFrame(multidof_diagnostics.summary)
            ),
        },
    )


def _plant_methods(
    config: dict[str, Any], chosen: dict[str, Any]
) -> list[dict[str, Any]]:
    study = config["plant_study"]
    declared_plants = tuple(str(value) for value in study["plants"])
    unsupported = set(declared_plants) - {"ideal", "delayed_servo"}
    if unsupported:
        raise ValueError(f"unsupported plant study entries: {sorted(unsupported)}")
    parameter_sets: list[tuple[str, str, dict[str, float]]] = []
    if "ideal" in declared_plants:
        parameter_sets.append(("ideal", "ideal", {}))
    if "delayed_servo" in declared_plants:
        for bandwidth, damping, delay_ms in product(
            study["bandwidth_hz"],
            study["damping_ratio"],
            study["delay_ms"],
        ):
            bandwidth_value = float(bandwidth)
            damping_value = float(damping)
            delay_value = float(delay_ms)
            label = f"servo_b{bandwidth_value:g}_z{damping_value:g}_d{delay_value:g}"
            parameter_sets.append(
                (
                    label,
                    "delayed_servo",
                    {
                        "bandwidth_hz": bandwidth_value,
                        "damping_ratio": damping_value,
                        "delay_s": delay_value / 1000.0,
                    },
                )
            )
    methods = []
    for label, plant, parameters in parameter_sets:
        for feedback in config["plant_study"]["feedback_modes"]:
            method = locked_method(
                estimator=chosen["estimator"],
                estimator_parameters=chosen["estimator_parameters"],
                predictor=chosen["predictor"],
                horizon_ms=chosen["horizon_ms"],
                method_id=f"{label}::{feedback}",
            )
            method["pipeline"].update(
                {
                    "plant": plant,
                    "plant_parameters": {
                        **parameters,
                        "position_noise_sigma": 1e-5 if plant != "ideal" else 0.0,
                        "seed": 20260721,
                    },
                    "measured_state_mode": feedback,
                }
            )
            methods.append(method)
    return methods


def command_plant(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    _require_confirmation_context(args, protocol=protocol)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    configured_maximum = config["data"].get("max_trajectories")
    maximum = None if configured_maximum is None else int(configured_maximum)
    cases = _synthetic_cases_for_config(
        config,
        "test",
        sample_rate_hz=100.0,
        maximum=maximum,
        protocol=protocol,
    )
    outcome = run_pipeline_matrix(cases, config, _plant_methods(config, chosen))
    plant_diagnostics = compute_multidof_tracking_diagnostics(
        outcome.samples,
        output_field="plant_p",
    )
    return _write_bundle(
        _output(args, "plant", config),
        config,
        outcome,
        split="test",
        rates=[100.0],
        source="synthetic truth with transparent ideal/delayed-servo sensitivity models",
        policy="family-balanced frozen test prefix; complete predeclared plant factorial",
        extra_csv={
            "plant_reference_per_joint_metrics.csv": _csv_records(
                pd.DataFrame(plant_diagnostics.per_joint)
            ),
            "plant_reference_per_cycle_metrics.csv": _csv_records(
                pd.DataFrame(plant_diagnostics.per_cycle)
            ),
            "plant_reference_summary.csv": _csv_records(
                pd.DataFrame(plant_diagnostics.summary)
            ),
        },
    )


def _fresh_real_replay_config(
    config: Mapping[str, Any], *, protocol: EvidenceProtocol
) -> dict[str, Any]:
    """Return a development-labelled effective config without mutating the lock."""

    effective = copy.deepcopy(dict(config))
    effective["run_id"] = f"paper-evidence-{protocol.version}-real-replay"
    data = effective.get("data")
    if not isinstance(data, Mapping):
        raise SelectionLockError(
            f"{protocol.version} real-replay config has no data mapping"
        )
    effective["data"] = {**data, "split": "development"}
    return effective


def _assert_fresh_real_replay_provenance(
    config: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    *,
    protocol: EvidenceProtocol,
) -> None:
    run_id = str(config.get("run_id", ""))
    data = config.get("data")
    configured_split = data.get("split") if isinstance(data, Mapping) else None
    expected_run_id = f"paper-evidence-{protocol.version}-real-replay"
    if run_id != expected_run_id or configured_split != "development":
        raise SelectionLockError(
            f"{protocol.version} real-replay effective config is mislabelled"
        )
    expected_methods = frozenset({"deployed_p_only", "one_step_governed_pva_direct"})
    observed_methods = frozenset(str(row.get("method_id")) for row in samples)
    if observed_methods != expected_methods:
        raise SelectionLockError(
            f"{protocol.version} real-replay method population differs from the "
            "locked design"
        )
    if any(
        str(row.get("run_id")) != f"{run_id}::{row.get('method_id')}"
        or str(row.get("split")) != "development"
        for row in samples
    ):
        raise SelectionLockError(
            f"{protocol.version} real-replay samples differ from resolved-config "
            "provenance"
        )


def command_real_replay(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    config = load_config(args.config)
    chosen = _selection(config, protocol=protocol)
    if protocol.version != "v1":
        config = _fresh_real_replay_config(config, protocol=protocol)
    csv_path = ROOT / "plot_data.csv"
    legacy = import_legacy_fixed_grid(csv_path, run_id=str(config["run_id"]))
    timestamp, _ = import_timestamp_causal(csv_path, run_id=str(config["run_id"]))
    arrival = simulate_arrival_replay(
        csv_path,
        run_id=str(config["run_id"]),
        seed=81001,
        base_delay_s=0.002,
        jitter_std_s=0.001,
        drop_probability=0.01,
    ).rows
    cases = [
        ("plot-data-legacy", legacy),
        ("plot-data-timestamp", timestamp),
        ("plot-data-arrival", arrival),
    ]
    all_methods = _same_information_methods_for_lock(chosen)
    methods = [
        method
        for method in all_methods
        if method["method_id"] in {"deployed_p_only", "one_step_governed_pva_direct"}
    ]
    outcome = run_pipeline_matrix(cases, config, methods)
    if protocol.version != "v1":
        _assert_fresh_real_replay_provenance(
            config, outcome.samples, protocol=protocol
        )
    replay_diagnostics = real_replay_diagnostics(outcome.samples)
    return _write_bundle(
        _output(args, "real_replay", config),
        config,
        outcome,
        split="development",
        rates=[100.0],
        source="plot_data.csv; three isolated replay semantics; derivative truth unavailable",
        policy="single available development trace; never used for locked selection",
        extra_csv={"real_replay_diagnostics.csv": replay_diagnostics},
    )


def command_phase_a(args: argparse.Namespace) -> dict[str, Any]:
    if _protocol(args).version != "v1":
        raise SelectionLockError(
            "Phase A is retained as v1 historical negative evidence and is not "
            "a fresh-protocol confirmation command"
        )
    config = load_config(args.config)
    resolved = serializable_config(config)
    path = _output(args, "phase_a", config)
    writer = ArtifactWriter(
        path,
        run_id=str(config["run_id"]),
        command=_command(),
        resolved_config=resolved,
        repo_root=ROOT,
        expected_commit=_commit(),
        require_clean=bool(config.get("formal", False)),
        manifest_extra={
            "historical_results_overwritten": False,
            "target_output_contract": "target[k] -> output[k+1]",
        },
    )
    phase_a_design = config["phase_a"]
    run_phase_a(
        writer.root,
        include_sensitivity=True,
        method_ids=phase_a_design["methods"],
        acceleration_limits=phase_a_design["acceleration_limits"],
        jerk_limits=phase_a_design["jerk_limits"],
    )
    resolved_path = write_resolved_config(
        resolved, writer.root / "resolved_config.yaml"
    )
    writer.register(resolved_path, role="resolved_config")
    writer.write_json(
        "data_manifest.json",
        {
            "schema_version": "otg.data-manifest.v1",
            "source": "legacy analytic references and plot_data.csv development trace",
            "split": "development",
            "future_oracle": "offline_only",
            "truth_derivatives_for_plot_data": "unavailable",
        },
        role="data_manifest",
    )
    writer.write_json(
        "method_matrix.json",
        {
            "schema_version": "otg.method-matrix.v1",
            "phase_a": resolved["phase_a"],
            "limits": resolved["limits"],
            "control": resolved["control"],
            "target_output_contract": "target[k] -> output[k+1]",
        },
        role="expanded_method_matrix",
    )
    phase_samples = read_parquet(writer.root / "samples.parquet", validate=True)
    population = {}
    for row in phase_samples:
        key = (
            str(row["dataset_id"]),
            str(row["session_id"]),
            str(row["trajectory_id"]),
            str(row["scenario_id"]),
        )
        population.setdefault(
            key,
            {
                "dataset_id": key[0],
                "session_id": key[1],
                "trajectory_id": key[2],
                "scenario_id": key[3],
                "split": str(row["split"]),
                "seed": int(row["seed"]),
                "source_kind": str(row["source_kind"]),
            },
        )
    writer.write_json(
        "split_manifest.json",
        {
            "schema_version": "otg.suite-population.v1",
            "source": "Phase A analytic references and plot_data development trace",
            "split": "development",
            "selection_unit": "whole trajectory",
            "parent_split_manifest": {
                "path": "split_manifest.json",
                "sha256": sha256_file(ROOT / "split_manifest.json"),
                "applicable_to_population": False,
            },
            "population_count": len(population),
            "population": [population[key] for key in sorted(population)],
        },
        role="split_manifest",
    )
    for name, role in (
        ("samples.parquet", "canonical_samples"),
        ("failures.csv", "failures"),
        ("constraint_audit.csv", "continuous_constraint_audit"),
        ("phase_a_metrics.csv", "phase_a_metrics"),
        ("legacy_vs_clean_regression.csv", "legacy_regression"),
        ("phase_a_summary.json", "phase_a_summary"),
    ):
        writer.register(writer.root / name, role=role)
    writer.write_recomputed_metrics(
        max_lag_s=1.0,
        motion_limits=config["limits"],
    )
    writer.finalize()
    return validate_artifact_bundle(
        writer.root,
        expected_commit=_commit(),
        verify_recomputation=True,
        recompute_arguments={"max_lag_s": 1.0, "motion_limits": config["limits"]},
    )


def command_qa(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.results).resolve()
    bundles = sorted(
        path.parent
        for path in root.rglob("artifact_index.json")
        if (path.parent / "run.json").is_file()
    )
    if not bundles:
        raise RuntimeError(f"no artifact bundles found below {root}")
    reports = []
    for bundle in bundles:
        reports.append(
            validate_artifact_bundle(
                bundle,
                verify_recomputation=True,
                recompute_arguments={
                    "max_lag_s": 1.0,
                    "motion_limits": {
                        "max_velocity": 4.1,
                        "max_acceleration": 8.2,
                        "max_jerk": 4000.0,
                    },
                },
            )
        )
    return {"bundle_count": len(reports), "bundles": reports}


def command_report(args: argparse.Namespace) -> dict[str, Any]:
    protocol = _protocol(args)
    reporting_state = assert_clean_commit(ROOT)
    protocol_document = protocol.protocol_document or ROOT / "EXPERIMENT_PROTOCOL.md"
    if protocol.require_fresh_locked_test:
        lock = _verify_locked_protocol_inputs(protocol=protocol)
        protocol_metadata = lock.get("protocol")
        if not isinstance(protocol_metadata, Mapping) or not isinstance(
            protocol_metadata.get("path"), str
        ):
            raise SelectionLockError(
                f"{protocol.version} lock has no protocol document path"
            )
        protocol_document = _repo_path(str(protocol_metadata["path"]))
    expected_run_commit = getattr(args, "expected_run_commit", None)
    if expected_run_commit is None:
        expected_run_commit = reporting_state.commit
    return build_final_result_artifacts(
        Path(args.raw_results).resolve(),
        Path(args.output_root).resolve(),
        required_bundles=tuple(
            bundle_name for _, _, bundle_name in protocol.confirm_experiments
        ),
        protocol_version=protocol.version,
        protocol_path=protocol_document,
        expected_commit=str(expected_run_commit),
        reporting_git_commit=reporting_state.commit,
        generation_command=_command(),
    )


def _confirm_output_paths(
    *,
    repo_root: Path = ROOT,
    raw_root: Path | None = None,
    final_root: Path | None = None,
    experiments: Sequence[tuple[str, str, str]] | None = None,
    protocol: EvidenceProtocol = V1_PROTOCOL,
) -> tuple[Path, ...]:
    if raw_root is None:
        raw_root = protocol.raw_root
    if final_root is None:
        final_root = protocol.final_root
    if experiments is None:
        experiments = protocol.confirm_experiments
    paths: list[Path] = []
    for subcommand, configured_path, bundle_name in experiments:
        config_path = _repo_path(configured_path, repo_root=repo_root)
        if not config_path.is_file():
            raise SelectionLockError(
                f"confirm is missing config for {subcommand}: {config_path}"
            )
        config = load_config(config_path)
        if not bool(config.get("formal") or config.get("require_clean")):
            raise SelectionLockError(
                f"confirm config is not clean-run protected: {config_path}"
            )
        configured_root = _repo_path(str(config["output_root"]), repo_root=repo_root)
        if configured_root != raw_root.resolve():
            raise SelectionLockError(
                f"confirm config {config_path} output_root resolves to "
                f"{configured_root}, expected {raw_root.resolve()}"
            )
        paths.append(raw_root.resolve() / bundle_name)
    paths.extend(final_root.resolve() / name for name in FINAL_MANAGED_OUTPUTS)
    if len(paths) != len(set(paths)):
        raise SelectionLockError("confirm output contract contains duplicate paths")
    return tuple(paths)


def _assert_confirm_outputs_absent(paths: Sequence[str | Path]) -> None:
    existing = sorted(
        str(Path(path).resolve()) for path in paths if Path(path).exists()
    )
    if existing:
        preview = "\n  - ".join(existing)
        raise SelectionLockError(
            "one-time confirm refuses to overwrite or resume existing formal outputs. "
            "Inspect and explicitly archive/remove these paths before retrying:\n  - "
            + preview
        )


def _run_evidence_subcommand(
    arguments: Sequence[str],
    *,
    protocol: EvidenceProtocol = V1_PROTOCOL,
    confirmation_capability: object | None = None,
) -> None:
    global _LOGICAL_COMMAND
    command_arguments = list(arguments)
    if confirmation_capability is not None:
        if (
            _ACTIVE_CONFIRM_CAPABILITY is None
            or confirmation_capability is not _ACTIVE_CONFIRM_CAPABILITY
        ):
            raise SelectionLockError(
                "confirmation dispatch requires capability activated by command_confirm"
            )
        if "--confirmation-run" not in command_arguments:
            command_arguments.append("--confirmation-run")
        parsed = build_parser(protocol).parse_args(command_arguments)
        parsed.confirmation_run = True
        parsed.confirmation_capability = confirmation_capability
        previous_command = _LOGICAL_COMMAND
        _LOGICAL_COMMAND = (str(protocol.entrypoint), *command_arguments)
        try:
            parsed.function(parsed)
        finally:
            _LOGICAL_COMMAND = previous_command
        return
    subprocess.run(
        [sys.executable, str(protocol.entrypoint), *command_arguments],
        cwd=ROOT,
        check=True,
    )


def _execute_confirm(
    protocol: EvidenceProtocol, confirmation_capability: object
) -> dict[str, Any]:
    completed = []
    validation_command, validation_config, _ = protocol.confirm_experiments[0]
    _run_evidence_subcommand(
        (
            validation_command,
            "--config",
            validation_config,
            "--confirmation-run",
        ),
        protocol=protocol,
        confirmation_capability=confirmation_capability,
    )
    completed.append(validation_command)

    # Re-read every committed consumer after validation, then compare the exact
    # emitted JSON object.  This is deliberately byte-type strict after
    # canonical JSON normalization: 20 and 20.0 are not interchangeable locks.
    committed_selection = _load_committed_selection_lock(protocol=protocol)
    observed_selection = _load_json_mapping(
        protocol.raw_root / "validation" / "locked_selection.json",
        label="confirmation validation locked selection",
    )
    _assert_same_locked_selection(
        observed_selection,
        committed_selection,
        observed_source=str(protocol.raw_root / "validation" / "locked_selection.json"),
        expected_source=str(protocol.config_lock_path),
        protocol=protocol,
    )
    completed.append("selection-lock-verified")

    for subcommand, config, _ in protocol.confirm_experiments[1:]:
        _run_evidence_subcommand(
            (subcommand, "--config", config),
            protocol=protocol,
            confirmation_capability=confirmation_capability,
        )
        completed.append(subcommand)

    _run_evidence_subcommand(
        ("qa", "--results", str(protocol.raw_root)), protocol=protocol
    )
    completed.append("qa")
    _run_evidence_subcommand(
        (
            "report",
            "--raw-results",
            str(protocol.raw_root),
            "--output-root",
            str(protocol.final_root),
        ),
        protocol=protocol,
    )
    completed.append("report")
    return {"completed": completed}


def command_confirm(args: argparse.Namespace) -> dict[str, Any]:
    global _ACTIVE_CONFIRM_CAPABILITY
    protocol = _protocol(args)
    if protocol.version == "v2":
        status_path = ROOT / "protocol_status_v2.json"
        if status_path.is_file():
            status = _load_json_mapping(status_path, label="v2 protocol status")
            if status.get("status") == "failed_nonconfirmatory_frozen":
                raise SelectionLockError(
                    "v2 confirmation is frozen after test-visible failure; same-test "
                    "resume/rerun is forbidden and a fresh v3 protocol is required"
                )
    # This preflight happens before validation and, critically, before any test
    # manifest can be loaded or any test trajectory can be generated.
    assert_clean_commit(ROOT)
    _assert_confirm_outputs_absent(_confirm_output_paths(protocol=protocol))
    if protocol.require_fresh_locked_test:
        _verify_locked_protocol_inputs(protocol=protocol)
    _load_committed_selection_lock(protocol=protocol)
    if _ACTIVE_CONFIRM_CAPABILITY is not None:
        raise SelectionLockError("a confirmation workflow is already active")
    confirmation_capability = object()
    _ACTIVE_CONFIRM_CAPABILITY = confirmation_capability
    try:
        return _execute_confirm(protocol, confirmation_capability)
    finally:
        _ACTIVE_CONFIRM_CAPABILITY = None


def build_parser(
    protocol: EvidenceProtocol = V1_PROTOCOL,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(evidence_protocol=protocol)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def experiment(
        name: str,
        function,
        default_config: str,
        *,
        default_output: str | None = None,
    ):
        item = subparsers.add_parser(name)
        item.add_argument("--config", default=default_config)
        item.add_argument("--output", default=default_output)
        item.add_argument(
            "--confirmation-run", action="store_true", help=argparse.SUPPRESS
        )
        item.set_defaults(function=function)
        return item

    experiment("smoke", command_smoke, protocol.config_for("smoke"))
    experiment(
        "selection-validation",
        command_validation,
        protocol.config_for("selection-validation"),
        default_output=str(protocol.selection_validation_root),
    )
    experiment("validation", command_validation, protocol.config_for("validation"))
    experiment("locked-test", command_locked_test, protocol.config_for("locked-test"))
    experiment(
        "acceleration", command_acceleration, protocol.config_for("acceleration")
    )
    experiment(
        "governor-infeasible",
        command_governor_infeasible,
        protocol.config_for("governor-infeasible"),
    )
    experiment("robustness", command_robustness, protocol.config_for("robustness"))
    experiment("rates", command_rates, protocol.config_for("rates"))
    experiment("multidof", command_multidof, protocol.config_for("multidof"))
    experiment("plant", command_plant, protocol.config_for("plant"))
    experiment("real-replay", command_real_replay, protocol.config_for("real-replay"))
    experiment("phase-a", command_phase_a, protocol.config_for("phase-a"))
    qa = subparsers.add_parser("qa")
    qa.add_argument("--results", default=str(protocol.raw_root))
    qa.set_defaults(function=command_qa)
    report = subparsers.add_parser("report")
    report.add_argument("--raw-results", default=str(protocol.raw_root))
    report.add_argument("--output-root", default=str(protocol.final_root))
    report.add_argument(
        "--expected-run-commit",
        default=None,
        help=(
            "explicit clean raw-bundle commit when derived reporting is generated "
            "by a later clean commit"
        ),
    )
    report.set_defaults(function=command_report)
    confirm = subparsers.add_parser("confirm")
    confirm.set_defaults(function=command_confirm)
    return parser


def main(protocol: EvidenceProtocol = V1_PROTOCOL) -> int:
    args = build_parser(protocol).parse_args()
    report = args.function(args)
    print(yaml.safe_dump(report, sort_keys=True, allow_unicode=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
