from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from otg_lab import reporting

ROOT = Path(__file__).resolve().parents[1]
CLI_SPEC = importlib.util.spec_from_file_location(
    "paper_evidence_cli", ROOT / "run_paper_evidence.py"
)
if CLI_SPEC is None or CLI_SPEC.loader is None:
    raise RuntimeError("could not load run_paper_evidence.py")
cli = importlib.util.module_from_spec(CLI_SPEC)
sys.path.insert(0, str(ROOT))
try:
    CLI_SPEC.loader.exec_module(cli)
finally:
    sys.path.pop(0)


def _locked_selection() -> dict:
    return {
        "schema_version": "otg.locked-selection.v1",
        "selection_split": "validation",
        "test_trajectory_count_seen": 0,
        "estimator": "robust_ca_kf",
        "estimator_id": "robust_ca_kf_locked",
        "estimator_parameters": {
            "measurement_sigma": 0.0001,
            "jerk_spectral_density": 100.0,
        },
        "downstream_estimators": [
            {
                "estimator_id": "robust_ca_kf_locked",
                "method": "robust_ca_kf",
                "estimator": "robust_ca_kf",
                "params": {
                    "measurement_sigma": 0.0001,
                    "jerk_spectral_density": 100.0,
                },
                "estimator_parameters": {
                    "measurement_sigma": 0.0001,
                    "jerk_spectral_density": 100.0,
                },
                "selection_rank": 1,
                "selection_score": 0.0,
            },
            {
                "estimator_id": "ca_kf_locked",
                "method": "ca_kf",
                "estimator": "ca_kf",
                "params": {
                    "measurement_sigma": 0.0001,
                    "jerk_spectral_density": 100.0,
                },
                "estimator_parameters": {
                    "measurement_sigma": 0.0001,
                    "jerk_spectral_density": 100.0,
                },
                "selection_rank": 2,
                "selection_score": 0.1,
            },
        ],
        "predictor": "constant_acceleration",
        "prediction_horizon_ms": 20.0,
        "prediction_objective": "prediction_p_rmse at prediction_time",
        "qp_horizon_steps": 20,
        "minimum_duration_s": 0.01,
        "motion_limits": {
            "max_velocity": 4.1,
            "max_acceleration": 8.2,
            "max_jerk": 4000.0,
        },
    }


def _base_config(*, formal: bool, locked_selection: dict | None = None) -> dict:
    config = {
        "formal": formal,
        "require_clean": formal,
        "pipeline": {
            "estimator": "position_only",
            "estimator_parameters": {},
            "predictor": "zero_order_hold",
            "prediction_horizon_ms": 0.0,
        },
        "_source_path": "test-config.yaml",
    }
    if locked_selection is not None:
        config["locked_selection"] = locked_selection
    return config


def _write_consumer(path: Path, selection: dict, *, output_root: str | None = None) -> None:
    value = {
        "schema_version": 1,
        "formal": True,
        "run_id": f"test-{path.stem}",
        "locked_selection": selection,
    }
    if output_root is not None:
        value["output_root"] = output_root
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=True), encoding="utf-8")


def test_formal_selection_cannot_fall_back_to_pipeline_defaults() -> None:
    with pytest.raises(cli.SelectionLockError, match="cannot use pipeline defaults"):
        cli._selection(_base_config(formal=True))

    development = cli._selection(_base_config(formal=False))
    assert development == {
        "estimator": "position_only",
        "estimator_parameters": {},
        "predictor": "zero_order_hold",
        "horizon_ms": 0.0,
        "qp_horizon_steps": 20,
    }

    locked = cli._selection(
        _base_config(formal=True, locked_selection=_locked_selection())
    )
    assert locked["estimator"] == "robust_ca_kf"
    assert locked["horizon_ms"] == 20.0
    assert locked["qp_horizon_steps"] == 20


def test_committed_lock_must_match_every_consumer_exactly(tmp_path: Path) -> None:
    selection = _locked_selection()
    (tmp_path / "config_lock.json").write_text(
        json.dumps(
            {
                "locked": True,
                "selection_status": "locked_after_validation",
                "locked_selection": selection,
            }
        ),
        encoding="utf-8",
    )
    consumers = ("configs/one.yaml", "configs/two.yaml")
    for relative in consumers:
        _write_consumer(tmp_path / relative, selection)

    observed = cli._load_committed_selection_lock(
        repo_root=tmp_path,
        consumer_config_paths=consumers,
    )
    assert cli._canonical_selection_text(observed) == cli._canonical_selection_text(
        selection
    )

    changed = copy.deepcopy(selection)
    changed["prediction_horizon_ms"] = 20
    _write_consumer(tmp_path / consumers[1], changed)
    with pytest.raises(cli.SelectionLockError, match="prediction_horizon_ms"):
        cli._load_committed_selection_lock(
            repo_root=tmp_path,
            consumer_config_paths=consumers,
        )


def test_confirm_preflight_resolves_and_reserves_every_output(tmp_path: Path) -> None:
    config = tmp_path / "configs" / "formal.yaml"
    output_root = "results/paper_evidence_v1/raw_runs"
    _write_consumer(config, _locked_selection(), output_root=output_root)
    raw = tmp_path / output_root
    final = tmp_path / "results" / "paper_evidence_v1"
    paths = cli._confirm_output_paths(
        repo_root=tmp_path,
        raw_root=raw,
        final_root=final,
        experiments=(("locked-test", "configs/formal.yaml", "locked_test"),),
    )
    assert raw / "locked_test" in paths
    assert final / "artifact_index.json" in paths
    cli._assert_confirm_outputs_absent(paths)

    occupied = raw / "locked_test"
    occupied.mkdir(parents=True)
    with pytest.raises(cli.SelectionLockError, match="refuses to overwrite"):
        cli._assert_confirm_outputs_absent(paths)


def test_confirm_managed_outputs_match_report_transaction_contract() -> None:
    assert cli.FINAL_MANAGED_OUTPUTS == reporting._MANAGED_OUTPUTS


def test_parser_exposes_independent_selection_validation_flow() -> None:
    parser = cli.build_parser()
    selection = parser.parse_args(["selection-validation"])
    assert Path(selection.output) == cli.SELECTION_VALIDATION_ROOT
    assert not Path(selection.output).is_relative_to(cli.RAW_ROOT)

    formal = parser.parse_args(["validation"])
    assert formal.output is None
    assert formal.confirmation_run is False

    report = parser.parse_args(
        ["report", "--expected-run-commit", "a" * 40]
    )
    assert report.expected_run_commit == "a" * 40


def test_validation_search_design_is_read_from_config() -> None:
    config = {
        "selection": {
            "downstream_estimators": 2,
            "horizons_ms": [0, 15],
            "stress_horizons_ms": [30, 90],
            "qp_horizon_steps": [7, 11],
        },
        "matrix": {"predictors": ["zero_order_hold", "constant_velocity"]},
    }
    design = cli._validation_selection_design(config)
    assert design == {
        "predictors": ("zero_order_hold", "constant_velocity"),
        "primary_horizons_ms": (0.0, 15.0),
        "stress_horizons_ms": (30.0, 90.0),
        "positive_horizons_ms": (15.0, 30.0, 90.0),
        "downstream_estimators": 2,
        "qp_horizon_steps": (7, 11),
    }


def test_validation_search_design_rejects_ambiguous_cells() -> None:
    config = {
        "selection": {
            "downstream_estimators": 3,
            "horizons_ms": [0, 20],
            "stress_horizons_ms": [20, 80],
            "qp_horizon_steps": [10, 20],
        },
        "matrix": {"predictors": ["constant_acceleration"]},
    }
    with pytest.raises(cli.SelectionLockError, match="disjoint"):
        cli._validation_selection_design(config)


def test_validation_ranking_receives_only_its_declared_split() -> None:
    mixed = pd.DataFrame(
        [
            {"split": "train", "trajectory_id": "train-1", "score": 1.0},
            {
                "split": "validation",
                "trajectory_id": "validation-1",
                "score": 2.0,
            },
        ]
    )

    selected = cli._metrics_for_declared_split(
        mixed, "validation", context="estimator ranking"
    )

    assert selected["trajectory_id"].tolist() == ["validation-1"]
    assert selected["split"].tolist() == ["validation"]
    with pytest.raises(cli.SelectionLockError, match="test cannot"):
        cli._metrics_for_declared_split(mixed, "test", context="estimator ranking")
    with pytest.raises(cli.SelectionLockError, match="no 'pilot' rows"):
        cli._metrics_for_declared_split(mixed, "pilot", context="estimator ranking")


def test_public_formal_validation_directs_user_to_selection_only_flow() -> None:
    args = argparse.Namespace(
        command="validation",
        confirmation_run=False,
        config="configs/validation.yaml",
        output=None,
    )
    with pytest.raises(cli.SelectionLockError, match="selection-validation"):
        cli.command_validation(args)


def test_selection_validation_cannot_write_into_formal_raw_tree() -> None:
    args = argparse.Namespace(
        command="selection-validation",
        config="configs/validation.yaml",
        output=str(cli.RAW_ROOT / "selection-validation"),
    )
    with pytest.raises(cli.SelectionLockError, match="outside the formal raw_runs"):
        cli.command_validation(args)


def test_confirm_validates_before_any_test_and_rechecks_emitted_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _locked_selection()
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(cli, "_confirm_output_paths", lambda: ())
    monkeypatch.setattr(cli, "_assert_confirm_outputs_absent", lambda paths: None)
    monkeypatch.setattr(cli, "_load_committed_selection_lock", lambda: selection)
    monkeypatch.setattr(
        cli, "_load_json_mapping", lambda path, label: copy.deepcopy(selection)
    )
    monkeypatch.setattr(
        cli, "_run_evidence_subcommand", lambda arguments: commands.append(tuple(arguments))
    )

    result = cli.command_confirm(argparse.Namespace())

    assert commands[0] == (
        "validation",
        "--config",
        "configs/validation.yaml",
        "--confirmation-run",
    )
    assert commands[1][0] == "locked-test"
    assert result["completed"][:3] == [
        "validation",
        "selection-lock-verified",
        "locked-test",
    ]


def test_confirm_stops_after_validation_when_emitted_lock_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _locked_selection()
    changed = copy.deepcopy(selection)
    changed["predictor"] = "constant_velocity"
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(cli, "_confirm_output_paths", lambda: ())
    monkeypatch.setattr(cli, "_assert_confirm_outputs_absent", lambda paths: None)
    monkeypatch.setattr(cli, "_load_committed_selection_lock", lambda: selection)
    monkeypatch.setattr(cli, "_load_json_mapping", lambda path, label: changed)
    monkeypatch.setattr(
        cli, "_run_evidence_subcommand", lambda arguments: commands.append(tuple(arguments))
    )

    with pytest.raises(cli.SelectionLockError, match="predictor"):
        cli.command_confirm(argparse.Namespace())
    assert len(commands) == 1
    assert commands[0][0] == "validation"
