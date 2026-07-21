from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from otg_lab import datasets
from otg_lab.artifacts import sha256_file
from otg_lab.config import load_config

ROOT = Path(__file__).resolve().parents[1]
CLI_SPEC = importlib.util.spec_from_file_location(
    "paper_evidence_protocol_cli", ROOT / "run_paper_evidence.py"
)
if CLI_SPEC is None or CLI_SPEC.loader is None:
    raise RuntimeError("could not load run_paper_evidence.py")
cli = importlib.util.module_from_spec(CLI_SPEC)
sys.path.insert(0, str(ROOT))
try:
    CLI_SPEC.loader.exec_module(cli)
finally:
    sys.path.pop(0)

GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "split_manifest_v2_generator", ROOT / "scripts" / "generate_split_manifest_v2.py"
)
if GENERATOR_SPEC is None or GENERATOR_SPEC.loader is None:
    raise RuntimeError("could not load v2 split generator")
generator = importlib.util.module_from_spec(GENERATOR_SPEC)
GENERATOR_SPEC.loader.exec_module(generator)


def _locked_selection_v2(*, status: str, horizon: int | None) -> dict:
    from test_cli_locking import _locked_selection

    value = copy.deepcopy(_locked_selection())
    value["schema_version"] = "otg.locked-selection.v2"
    value["qp_baseline_status"] = status
    value["qp_horizon_steps"] = horizon
    return value


def _protocol_for_tmp(tmp_path: Path) -> object:
    return cli.EvidenceProtocol(
        version="v2",
        dataset_id="synthetic-feasible-v2",
        entrypoint=tmp_path / "run_paper_evidence_v2.py",
        raw_root=tmp_path / "results" / "paper_evidence_v2" / "raw_runs",
        final_root=tmp_path / "results" / "paper_evidence_v2",
        selection_validation_root=(
            tmp_path / "runs" / "paper_evidence_v2" / "selection-validation"
        ),
        config_lock_path=tmp_path / "config_lock_v2.json",
        locked_selection_schema_version="otg.locked-selection.v2",
        config_defaults=(("locked-test", "configs/locked_test_v2.yaml"),),
        confirm_experiments=(
            ("locked-test", "configs/locked_test_v2.yaml", "locked_test"),
        ),
        selection_consumer_configs=("configs/locked_test_v2.yaml",),
        exposed_test_manifests=(tmp_path / "split_manifest.json",),
        require_fresh_locked_test=True,
    )


def test_v2_entrypoint_is_thin_and_profile_paths_do_not_alias_v1() -> None:
    wrapper = (ROOT / "run_paper_evidence_v2.py").read_text(encoding="utf-8")
    assert "from run_paper_evidence import V2_PROTOCOL, main" in wrapper
    assert "synthetic_cases" not in wrapper
    assert cli.V2_PROTOCOL.raw_root != cli.V1_PROTOCOL.raw_root
    assert cli.V2_PROTOCOL.config_lock_path.name == "config_lock_v2.json"
    parser = cli.build_parser(cli.V2_PROTOCOL)
    locked = parser.parse_args(["locked-test"])
    assert locked.config == "configs/locked_test_v2.yaml"
    assert locked.evidence_protocol is cli.V2_PROTOCOL


def test_v1_manifest_default_is_profile_scoped_and_v2_has_no_fallback() -> None:
    config = {"data": {}}
    assert cli._split_manifest_path(config, protocol=cli.V1_PROTOCOL) == str(
        ROOT / "split_manifest.json"
    )
    with pytest.raises(cli.SelectionLockError, match="requires.*split_manifest"):
        cli._split_manifest_path(config, protocol=cli.V2_PROTOCOL)


def test_v2_lock_records_qp_status_and_unqualified_qp_is_not_primary() -> None:
    unqualified = _locked_selection_v2(status="unqualified", horizon=None)
    normalized = cli._validate_locked_selection(
        unqualified, source="unit", protocol=cli.V2_PROTOCOL
    )
    chosen = {
        "estimator": normalized["estimator"],
        "estimator_parameters": normalized["estimator_parameters"],
        "predictor": normalized["predictor"],
        "horizon_ms": normalized["prediction_horizon_ms"],
        "qp_horizon_steps": normalized["qp_horizon_steps"],
        "qp_baseline_status": normalized["qp_baseline_status"],
    }
    methods = cli._same_information_methods_for_lock(chosen)
    assert methods
    assert all(method["pipeline"].get("governor") != "jerk_qp" for method in methods)

    qualified = _locked_selection_v2(status="qualified", horizon=20)
    cli._validate_locked_selection(qualified, source="unit", protocol=cli.V2_PROTOCOL)
    invalid = copy.deepcopy(unqualified)
    invalid["qp_horizon_steps"] = 20
    with pytest.raises(cli.SelectionLockError, match="must have.*null"):
        cli._validate_locked_selection(invalid, source="unit", protocol=cli.V2_PROTOCOL)


def test_fresh_manifest_rejects_any_exposed_seed_even_after_id_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {
        "dataset_id": "synthetic-feasible-v2",
        "trajectories": [
            {
                "trajectory_id": "renamed-v2-test",
                "family": "oscillatory",
                "split": "test",
                "seed": 617,
                "demand_stratum": "high",
                "locked": True,
            }
        ],
    }
    exposed = {
        "dataset_id": "synthetic-feasible-v1",
        "trajectories": [
            {
                "trajectory_id": "oscillatory__test__004",
                "family": "oscillatory",
                "split": "validation",
                "seed": 617,
                "demand_stratum": "high",
                "locked": False,
            }
        ],
    }
    manifests = {"candidate": candidate, "exposed": exposed}
    monkeypatch.setattr(
        datasets, "load_split_manifest", lambda path: manifests[str(path)]
    )
    with pytest.raises(ValueError, match="reuses exposed trajectories"):
        datasets.validate_fresh_locked_test_manifest(
            "candidate", exposed_manifest_paths=["exposed"]
        )


def test_fresh_test_guard_checks_clean_locked_manifest_before_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol_for_tmp(tmp_path)
    candidate = tmp_path / "split_manifest_v2.json"
    lock = {
        "synthetic_dataset": {
            "split_manifest": "split_manifest_v2.json",
            "split_manifest_sha256": "b" * 64,
        }
    }
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_assert_clean_committed_file",
        lambda path, **kwargs: events.append(("clean", Path(path).name)) or "b" * 64,
    )
    monkeypatch.setattr(cli, "_load_json_mapping", lambda path, label: lock)
    monkeypatch.setattr(
        cli,
        "validate_fresh_locked_test_manifest",
        lambda path, exposed_manifest_paths: events.append(
            ("overlap", tuple(Path(item).name for item in exposed_manifest_paths))
        ),
    )

    cli._assert_fresh_test_manifest(
        {"data": {"split_manifest": str(candidate)}},
        protocol=protocol,
        repo_root=tmp_path,
    )

    assert events == [
        ("clean", "config_lock_v2.json"),
        ("clean", "split_manifest_v2.json"),
        ("overlap", ("split_manifest.json",)),
    ]


def test_v2_confirm_excludes_legacy_phase_a() -> None:
    commands = {command for command, _, _ in cli.V2_PROTOCOL.confirm_experiments}
    assert "phase-a" not in commands
    assert "real-replay" in commands


def test_checked_in_v2_manifest_matches_generator_and_all_exposed_entries() -> None:
    path = ROOT / "split_manifest_v2.json"
    checked_in = json.loads(path.read_text(encoding="utf-8"))
    assert checked_in == generator.build_manifest()
    datasets.validate_split_manifest(checked_in)
    datasets.validate_fresh_locked_test_manifest(
        path, exposed_manifest_paths=(ROOT / "split_manifest.json",)
    )
    counts = {
        split: sum(row["split"] == split for row in checked_in["trajectories"])
        for split in ("train", "validation", "test")
    }
    assert counts == {"train": 120, "validation": 60, "test": 120}
    assert all(
        row["locked"] == (row["split"] == "test") for row in checked_in["trajectories"]
    )


def test_v2_configs_are_explicit_prelock_consumers() -> None:
    config_names = {
        path for _, path in cli.V2_PROTOCOL.config_defaults if path.endswith("_v2.yaml")
    }
    assert "configs/phase_a_v2.yaml" not in config_names
    for relative in sorted(config_names):
        raw = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        assert raw["protocol_version"] == "v2"
        assert raw["data"]["split_manifest"] == "split_manifest_v2.json"
        assert "locked_selection" not in raw
        resolved = load_config(ROOT / relative)
        assert "paper_evidence_v2" in str(resolved["output_root"])


def test_v2_prelock_hashes_and_no_test_execution_claims() -> None:
    lock = json.loads((ROOT / "config_lock_v2.json").read_text(encoding="utf-8"))
    assert lock["locked"] is False
    assert lock["selection_status"] == "pending_validation"
    assert lock["selection_policy"]["locked_selection"] is None
    assert lock["prelock_state"]["trajectory_generation_performed"] is False
    assert lock["prelock_state"]["test_executed"] is False
    assert lock["prelock_state"]["test_viewed"] is False
    assert lock["freshness_audit"]["status"] == "passed"
    assert lock["freshness_audit"]["exposed_scope"] == (
        "all_v1_train_validation_test_entries"
    )

    locked_files = {
        lock["protocol"]["path"]: lock["protocol"]["sha256"],
        lock["entrypoints"]["authoritative_implementation"]: lock["entrypoints"][
            "authoritative_implementation_sha256"
        ],
        lock["entrypoints"]["v2_wrapper"]: lock["entrypoints"]["v2_wrapper_sha256"],
        lock["development_config"]["path"]: lock["development_config"]["sha256"],
        lock["synthetic_dataset"]["config"]: lock["synthetic_dataset"]["config_sha256"],
        lock["synthetic_dataset"]["generator"]: lock["synthetic_dataset"][
            "generator_sha256"
        ],
        lock["synthetic_dataset"]["split_manifest"]: lock["synthetic_dataset"][
            "split_manifest_sha256"
        ],
        **lock["formal_config_sha256"],
    }
    for relative, expected in locked_files.items():
        assert sha256_file(ROOT / relative) == expected


def test_clean_committed_manifest_guard_rejects_dirty_file(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "config", "user.email", "protocol-test@example.invalid"),
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Protocol Test"),
        cwd=tmp_path,
        check=True,
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"locked": True}), encoding="utf-8")
    subprocess.run(("git", "add", "manifest.json"), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "commit", "-q", "-m", "lock manifest"), cwd=tmp_path, check=True
    )
    digest = cli.sha256_file(manifest)
    assert (
        cli._assert_clean_committed_file(
            manifest, repo_root=tmp_path, expected_sha256=digest
        )
        == digest
    )

    manifest.write_text(json.dumps({"locked": False}), encoding="utf-8")
    with pytest.raises(Exception, match="clean worktree"):
        cli._assert_clean_committed_file(
            manifest, repo_root=tmp_path, expected_sha256=digest
        )
