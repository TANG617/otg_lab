"""Configuration loading, resolution, validation, and hashing."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "formal": False,
    "run_id": "paper-evidence-development",
    "seed": 20260721,
    "output_root": "runs/paper_evidence_v1",
    "limits": {
        "max_velocity": 4.1,
        "max_acceleration": 8.2,
        "max_jerk": 4000.0,
    },
    "control": {"dt": 0.01, "minimum_duration": 0.01},
    "data": {
        "split_manifest": "split_manifest.json",
        "split": "development",
        "sample_rate_hz": 100,
        "max_trajectories": None,
        "families": "all",
        "stress_scenarios": [],
    },
    "pipeline": {
        "estimator": "ca_kf",
        "estimator_parameters": {},
        "predictor": "constant_acceleration",
        "predictor_parameters": {},
        "prediction_horizon_ms": 20,
        "target_mode": "pva",
        "governor": "one_step",
        "governor_parameters": {},
        "follower": "direct",
        "plant": "ideal",
        "plant_parameters": {},
        "measured_state_mode": "previous_command",
    },
    "artifacts": {
        "write_samples": True,
        "write_figures": True,
        "compression": "zstd",
        "representative_trace_rule": ["median", "p90", "worst"],
    },
    "statistics": {"bootstrap_resamples": 10000, "confidence": 0.95},
    "runtime": {"warmup_cycles": 100, "repetitions": 5},
    "diagnostics": {
        "recovery_output_field": "command_p",
        "recovery_tolerance_rad": 0.01,
        "recovery_hold_samples": 3,
        "pre_fault_window_s": 0.1,
    },
}


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(dict(result[key]), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ConfigError("unsupported config schema_version")
    limits = config["limits"]
    for key in ("max_velocity", "max_acceleration", "max_jerk"):
        value = float(limits[key])
        if value <= 0.0:
            raise ConfigError(f"limits.{key} must be positive")
    control = config["control"]
    dt = float(control["dt"])
    minimum_duration = float(control["minimum_duration"])
    if dt <= 0.0 or minimum_duration <= 0.0:
        raise ConfigError("control periods must be positive")
    if config.get("formal") and abs(dt - 0.01) > 1e-15:
        raise ConfigError("formal primary config must use DT=10 ms")
    if config.get("formal") and abs(minimum_duration - dt) > 1e-15:
        raise ConfigError("formal minimum_duration must equal DT")
    if config.get("formal"):
        required = (4.1, 8.2, 4000.0)
        actual = (
            float(limits["max_velocity"]),
            float(limits["max_acceleration"]),
            float(limits["max_jerk"]),
        )
        if actual != required:
            raise ConfigError(f"formal primary limits must be {required}, got {actual}")
    pipeline = config["pipeline"]
    horizon_ms = float(pipeline["prediction_horizon_ms"])
    if horizon_ms < 0.0:
        raise ConfigError("prediction horizon cannot be negative")
    if pipeline["target_mode"] not in {"p", "pv", "pva"}:
        raise ConfigError("target_mode must be p, pv, or pva")
    if pipeline["governor"] not in {"none", "scalar_projection", "one_step", "jerk_qp"}:
        raise ConfigError("unknown governor")
    if pipeline["follower"] not in {"direct", "ruckig"}:
        raise ConfigError("unknown follower")
    if (
        pipeline["governor"] in {"none", "scalar_projection"}
        and pipeline["follower"] == "direct"
    ):
        raise ConfigError("raw/scalar targets require the Ruckig follower")
    if pipeline["plant"] not in {"ideal", "delayed_servo"}:
        raise ConfigError("unknown plant")
    if pipeline["measured_state_mode"] not in {
        "previous_command",
        "measured",
        "hybrid",
    }:
        raise ConfigError("unknown measured_state_mode")
    rate = float(config["data"]["sample_rate_hz"])
    if rate <= 0.0:
        raise ConfigError("sample_rate_hz must be positive")
    if abs(rate * dt - 1.0) > 1e-9:
        raise ConfigError("control.dt and data.sample_rate_hz disagree")
    if not config.get("run_id"):
        raise ConfigError("run_id cannot be empty")
    diagnostics = config["diagnostics"]
    if diagnostics["recovery_output_field"] not in {"command_p", "plant_p"}:
        raise ConfigError(
            "diagnostics.recovery_output_field must be command_p or plant_p"
        )
    if float(diagnostics["recovery_tolerance_rad"]) < 0.0:
        raise ConfigError("diagnostics.recovery_tolerance_rad must be non-negative")
    if int(diagnostics["recovery_hold_samples"]) < 1:
        raise ConfigError("diagnostics.recovery_hold_samples must be positive")
    if float(diagnostics["pre_fault_window_s"]) <= 0.0:
        raise ConfigError("diagnostics.pre_fault_window_s must be positive")


def load_config(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, Mapping):
        raise ConfigError("config root must be a mapping")
    config = _deep_merge(DEFAULT_CONFIG, raw)
    config["_source_path"] = str(source.resolve())
    validate_config(config)
    return config


def config_hash(config: Mapping[str, Any]) -> str:
    serializable = {
        key: value for key, value in config.items() if not key.startswith("_")
    }
    payload = json.dumps(
        serializable, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_resolved_config(config: Mapping[str, Any], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        key: value for key, value in config.items() if not key.startswith("_")
    }
    output.write_text(
        yaml.safe_dump(serializable, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    return output


__all__ = [
    "ConfigError",
    "DEFAULT_CONFIG",
    "config_hash",
    "load_config",
    "validate_config",
    "write_resolved_config",
]
