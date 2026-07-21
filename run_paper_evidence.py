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
from otg_lab.reporting import build_final_result_artifacts
from otg_lab.schema import read_parquet

ROOT = Path(__file__).resolve().parent
RAW_ROOT = ROOT / "results" / "paper_evidence_v1" / "raw_runs"
FINAL_ROOT = ROOT / "results" / "paper_evidence_v1"
SELECTION_VALIDATION_ROOT = ROOT / "runs" / "paper_evidence_v1" / "selection-validation"
CONFIG_LOCK_PATH = ROOT / "config_lock.json"

LOCKED_SELECTION_SCHEMA_VERSION = "otg.locked-selection.v1"
LOCKED_SELECTION_REQUIRED_FIELDS = frozenset(
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

# Every suite below consumes the validation-selected estimator, predictor,
# horizon, or QP horizon.  Each config therefore carries the complete lock,
# even when one particular suite uses only a subset of its fields.
SELECTION_CONSUMER_CONFIGS = (
    "configs/locked_test_v1.yaml",
    "configs/acceleration.yaml",
    "configs/governor_infeasible.yaml",
    "configs/robustness.yaml",
    "configs/rate_study.yaml",
    "configs/multidof_plant.yaml",
)

CONFIRM_EXPERIMENTS = (
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


def _commit() -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _command() -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]


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
        raise SelectionLockError(f"locked selection is not canonical JSON: {error}") from error


def _validate_locked_selection(
    value: Any, *, source: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SelectionLockError(f"{source} has no complete locked_selection mapping")
    locked = dict(value)
    missing = LOCKED_SELECTION_REQUIRED_FIELDS - set(locked)
    extra = set(locked) - LOCKED_SELECTION_REQUIRED_FIELDS
    if missing or extra:
        raise SelectionLockError(
            f"{source} locked_selection schema differs: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if locked["schema_version"] != LOCKED_SELECTION_SCHEMA_VERSION:
        raise SelectionLockError(
            f"{source} locked_selection.schema_version must be "
            f"{LOCKED_SELECTION_SCHEMA_VERSION}"
        )
    if locked["selection_split"] != "validation":
        raise SelectionLockError(f"{source} selection_split must be validation")
    if locked["test_trajectory_count_seen"] != 0:
        raise SelectionLockError(
            f"{source} must record test_trajectory_count_seen=0"
        )
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
    if isinstance(qp_steps, bool) or not isinstance(qp_steps, int) or qp_steps < 1:
        raise SelectionLockError(f"{source} qp_horizon_steps must be a positive integer")
    if locked["minimum_duration_s"] != 0.01:
        raise SelectionLockError(f"{source} minimum_duration_s must equal 0.01")
    limits = locked["motion_limits"]
    if not isinstance(limits, Mapping) or dict(limits) != {
        "max_velocity": 4.1,
        "max_acceleration": 8.2,
        "max_jerk": 4000.0,
    }:
        raise SelectionLockError(f"{source} motion_limits differ from the formal limits")
    _canonical_selection_text(locked)
    return locked


def _selection_difference(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
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
) -> dict[str, Any]:
    observed_lock = _validate_locked_selection(observed, source=observed_source)
    expected_lock = _validate_locked_selection(expected, source=expected_source)
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
    return candidate.resolve() if candidate.is_absolute() else (repo_root / candidate).resolve()


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise SelectionLockError(f"missing {label}: {path}")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, Mapping):
        raise SelectionLockError(f"{label} must contain a JSON object: {path}")
    return dict(value)


def _load_committed_selection_lock(
    *,
    repo_root: Path = ROOT,
    config_lock_path: str | Path = "config_lock.json",
    consumer_config_paths: Sequence[str | Path] = SELECTION_CONSUMER_CONFIGS,
) -> dict[str, Any]:
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
        lock_manifest.get("locked_selection"), source=str(lock_path)
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
        )
    return expected


def _selection(config: dict[str, Any]) -> dict[str, Any]:
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
        locked, source=str(config.get("_source_path", "config"))
    )
    return {
        "estimator": normalized["estimator"],
        "estimator_parameters": dict(normalized["estimator_parameters"]),
        "predictor": normalized["predictor"],
        "horizon_ms": float(normalized["prediction_horizon_ms"]),
        "qp_horizon_steps": int(normalized["qp_horizon_steps"]),
    }


def _output(args: argparse.Namespace, name: str, config: dict[str, Any]) -> Path:
    if args.output:
        return Path(args.output).resolve()
    return (ROOT / str(config.get("output_root", RAW_ROOT)) / name).resolve()


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
        raise SelectionLockError("validation selection/matrix sections must be mappings")

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
            raise SelectionLockError(
                f"selection.{field} must be unique and increasing"
            )
        return tuple(values)

    primary_horizons = horizons("horizons_ms", required=True)
    stress_horizons = horizons("stress_horizons_ms", required=True)
    if set(primary_horizons) & set(stress_horizons):
        raise SelectionLockError("primary and stress prediction horizons must be disjoint")
    if stress_horizons[0] <= primary_horizons[-1]:
        raise SelectionLockError(
            "stress prediction horizons must lie above the primary selection range"
        )
    positive_horizons = tuple(
        value
        for value in (*primary_horizons, *stress_horizons)
        if value > 0.0
    )
    if not positive_horizons:
        raise SelectionLockError("validation design needs a positive prediction horizon")

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


def command_smoke(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    cases = synthetic_cases("validation", sample_rate_hz=100.0, maximum=1)
    chosen = _selection(config)
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
        source="synthetic-feasible-v1",
        selection_policy="first frozen validation trajectory; smoke only",
        require_clean=False,
    )


def command_validation(args: argparse.Namespace) -> dict[str, Any]:
    """Run train/validation-only parameter and horizon selection."""

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
            args.output = str(SELECTION_VALIDATION_ROOT)
        requested_output = Path(args.output).resolve()
        if requested_output == RAW_ROOT or requested_output.is_relative_to(RAW_ROOT):
            raise SelectionLockError(
                "selection-validation output must be outside the formal raw_runs tree"
            )
    else:
        _load_committed_selection_lock()

    config = load_config(args.config)
    selection_design = _validation_selection_design(config)
    train_rows = _flatten_cases(
        synthetic_cases("train", sample_rate_hz=100.0, maximum=None)
    )
    validation_cases = synthetic_cases("validation", sample_rate_hz=100.0, maximum=None)
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
    qp_ranking.insert(0, "rank", np.arange(1, len(qp_ranking) + 1))
    qp_ranking["selected"] = qp_ranking["rank"].eq(1)
    selected_qp_steps = int(str(qp_ranking.iloc[0]["method"]).split("n")[-1])

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
        "schema_version": "otg.locked-selection.v1",
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
    bundle_name = "selection-validation" if selection_only else "validation"
    output_path = _output(args, bundle_name, config)
    report = _write_bundle(
        output_path,
        config,
        outcome,
        split="validation",
        rates=[100.0],
        source="synthetic-feasible-v1 train and validation only",
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
                "primary_horizons_ms": list(
                    selection_design["primary_horizons_ms"]
                ),
                "stress_horizons_ms": list(
                    selection_design["stress_horizons_ms"]
                ),
                "downstream_estimators": selection_design["downstream_estimators"],
                "qp_horizon_steps": list(selection_design["qp_horizon_steps"]),
            },
        },
        extra_parquet={"t_free_rho_samples.parquet": t_free_samples},
    )
    result = {**report, "locked_selection": locked_selection}
    if selection_only:
        result.update(
            {
                "workflow_status": "selection_only_no_locked_test_run",
                "locked_selection_artifact": str(
                    output_path / "locked_selection.json"
                ),
                "next_step": (
                    "copy locked_selection.json verbatim into config_lock.json and "
                    "every formal selection-consumer config; commit and clean the "
                    "worktree; then run confirm"
                ),
            }
        )
    return result


def command_locked_test(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    chosen = _selection(config)
    cases = synthetic_cases("test", sample_rate_hz=100.0, maximum=None)
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
    primary_methods = same_information_methods(**chosen)
    methods = list(primary_methods)
    for rank, estimator_spec in enumerate(downstream_estimators[1:], start=2):
        secondary = same_information_methods(
            estimator=str(estimator_spec["estimator"]),
            estimator_parameters=dict(
                estimator_spec.get(
                    "estimator_parameters", estimator_spec.get("params", {})
                )
            ),
            predictor=chosen["predictor"],
            horizon_ms=chosen["horizon_ms"],
            qp_horizon_steps=chosen["qp_horizon_steps"],
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
        row
        for row in primary_samples
        if str(row.get("reference_variant")) == "chirp"
    ]
    local_delay_samples = [
        row
        for row in primary_samples
        if str(row.get("reference_variant"))
        in {"stop_and_go", "rapid_reversal"}
    ]
    runtime_samples, runtime_summaries = repeated_runtime_study(
        synthetic_cases("test", sample_rate_hz=100.0, maximum=6),
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
        source="synthetic-feasible-v1",
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

    config = load_config(args.config)
    chosen = _selection(config)
    suite = deliberate_infeasible_suite()
    cases = [
        (
            trajectory.trajectory_id,
            trajectory_to_rows(
                trajectory,
                sample_rate_hz=100.0,
                run_id=str(config["run_id"]),
                dataset_id="synthetic-deliberate-infeasible-v1",
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
        source="synthetic-deliberate-infeasible-v1; governor negative suite only",
        policy="complete four-case negative suite; isolated from estimator/predictor selection",
        extra_csv={"governor_invariants.csv": invariant_rows},
        extra_json={
            "infeasible_suite_manifest.json": {
                "schema_version": "otg.infeasible-suite.v1",
                "dataset_id": "synthetic-deliberate-infeasible-v1",
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
    config = load_config(args.config)
    chosen = _selection(config)
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
        horizon_ms = (
            0.0 if time_mode == "current" else 1000.0 * config["control"]["dt"]
        )
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
    config = load_config(args.config)
    chosen = _selection(config)
    maximum = config["data"].get("max_trajectories")
    cases = stressed_cases(
        "test",
        default_stress_suite(seed=91000),
        sample_rate_hz=100.0,
        maximum=maximum,
    )
    empirical_jitter = empirical_jitter_from_csv(
        ROOT / "plot_data.csv", expected_dt_s=0.01
    )
    for trajectory_id, rows in synthetic_cases(
        "test", sample_rate_hz=100.0, maximum=maximum
    ):
        empirical_rows = inject_timing(
            rows,
            seed=91080,
            empirical_jitter_s=empirical_jitter,
            scenario_id="empirical_plot_data_jitter",
        )
        cases.append((f"{trajectory_id}::empirical_plot_data_jitter", empirical_rows))
    all_methods = same_information_methods(**chosen)
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
        source="synthetic-feasible-v1 with fixed replayable stress realizations",
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
    config = load_config(args.config)
    chosen = _selection(config)
    outcomes = []
    rates = [float(value) for value in config["rate_study"]["sample_rates_hz"]]
    maximum = config["data"].get("max_trajectories")
    for rate in rates:
        rate_config = copy.deepcopy(config)
        rate_config["formal"] = False
        rate_config["control"]["dt"] = 1.0 / rate
        rate_config["control"]["minimum_duration"] = 1.0 / rate
        rate_config["data"]["sample_rate_hz"] = rate
        cases = synthetic_cases("test", sample_rate_hz=rate, maximum=maximum)
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
    config = load_config(args.config)
    chosen = _selection(config)
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
    config = load_config(args.config)
    chosen = _selection(config)
    configured_maximum = config["data"].get("max_trajectories")
    maximum = None if configured_maximum is None else int(configured_maximum)
    cases = synthetic_cases("test", sample_rate_hz=100.0, maximum=maximum)
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


def command_real_replay(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    chosen = _selection(config)
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
    all_methods = same_information_methods(**chosen)
    methods = [
        method
        for method in all_methods
        if method["method_id"] in {"deployed_p_only", "one_step_governed_pva_direct"}
    ]
    outcome = run_pipeline_matrix(cases, config, methods)
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
    reporting_state = assert_clean_commit(ROOT)
    expected_run_commit = getattr(args, "expected_run_commit", None)
    if expected_run_commit is None:
        expected_run_commit = reporting_state.commit
    return build_final_result_artifacts(
        Path(args.raw_results).resolve(),
        Path(args.output_root).resolve(),
        expected_commit=str(expected_run_commit),
        reporting_git_commit=reporting_state.commit,
        generation_command=_command(),
    )


def _confirm_output_paths(
    *,
    repo_root: Path = ROOT,
    raw_root: Path = RAW_ROOT,
    final_root: Path = FINAL_ROOT,
    experiments: Sequence[tuple[str, str, str]] = CONFIRM_EXPERIMENTS,
) -> tuple[Path, ...]:
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
    existing = sorted(str(Path(path).resolve()) for path in paths if Path(path).exists())
    if existing:
        preview = "\n  - ".join(existing)
        raise SelectionLockError(
            "one-time confirm refuses to overwrite or resume existing formal outputs. "
            "Inspect and explicitly archive/remove these paths before retrying:\n  - "
            + preview
        )


def _run_evidence_subcommand(arguments: Sequence[str]) -> None:
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *arguments],
        cwd=ROOT,
        check=True,
    )


def command_confirm(args: argparse.Namespace) -> dict[str, Any]:
    del args
    _assert_confirm_outputs_absent(_confirm_output_paths())
    _load_committed_selection_lock()

    completed = []
    validation_command, validation_config, _ = CONFIRM_EXPERIMENTS[0]
    _run_evidence_subcommand(
        (
            validation_command,
            "--config",
            validation_config,
            "--confirmation-run",
        )
    )
    completed.append(validation_command)

    # Re-read every committed consumer after validation, then compare the exact
    # emitted JSON object.  This is deliberately byte-type strict after
    # canonical JSON normalization: 20 and 20.0 are not interchangeable locks.
    committed_selection = _load_committed_selection_lock()
    observed_selection = _load_json_mapping(
        RAW_ROOT / "validation" / "locked_selection.json",
        label="confirmation validation locked selection",
    )
    _assert_same_locked_selection(
        observed_selection,
        committed_selection,
        observed_source=str(RAW_ROOT / "validation" / "locked_selection.json"),
        expected_source=str(CONFIG_LOCK_PATH),
    )
    completed.append("selection-lock-verified")

    for subcommand, config, _ in CONFIRM_EXPERIMENTS[1:]:
        _run_evidence_subcommand((subcommand, "--config", config))
        completed.append(subcommand)

    _run_evidence_subcommand(("qa", "--results", str(RAW_ROOT)))
    completed.append("qa")
    _run_evidence_subcommand(
        (
            "report",
            "--raw-results",
            str(RAW_ROOT),
            "--output-root",
            str(FINAL_ROOT),
        )
    )
    completed.append("report")
    return {"completed": completed}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
        item.set_defaults(function=function)
        return item

    experiment("smoke", command_smoke, "configs/development.yaml")
    experiment(
        "selection-validation",
        command_validation,
        "configs/validation.yaml",
        default_output=str(SELECTION_VALIDATION_ROOT),
    )
    validation = experiment(
        "validation", command_validation, "configs/validation.yaml"
    )
    validation.add_argument(
        "--confirmation-run", action="store_true", help=argparse.SUPPRESS
    )
    experiment("locked-test", command_locked_test, "configs/locked_test_v1.yaml")
    experiment("acceleration", command_acceleration, "configs/acceleration.yaml")
    experiment(
        "governor-infeasible",
        command_governor_infeasible,
        "configs/governor_infeasible.yaml",
    )
    experiment("robustness", command_robustness, "configs/robustness.yaml")
    experiment("rates", command_rates, "configs/rate_study.yaml")
    experiment("multidof", command_multidof, "configs/multidof_plant.yaml")
    experiment("plant", command_plant, "configs/multidof_plant.yaml")
    experiment("real-replay", command_real_replay, "configs/locked_test_v1.yaml")
    experiment("phase-a", command_phase_a, "configs/phase_a.yaml")
    qa = subparsers.add_parser("qa")
    qa.add_argument("--results", default=str(RAW_ROOT))
    qa.set_defaults(function=command_qa)
    report = subparsers.add_parser("report")
    report.add_argument("--raw-results", default=str(RAW_ROOT))
    report.add_argument("--output-root", default=str(FINAL_ROOT))
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


def main() -> int:
    args = build_parser().parse_args()
    report = args.function(args)
    print(yaml.safe_dump(report, sort_keys=True, allow_unicode=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
