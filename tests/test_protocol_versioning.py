from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from otg_lab import datasets, reporting
from otg_lab.artifacts import assert_clean_commit, sha256_file
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

V3_GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "split_manifest_v3_generator", ROOT / "scripts" / "generate_split_manifest_v3.py"
)
if V3_GENERATOR_SPEC is None or V3_GENERATOR_SPEC.loader is None:
    raise RuntimeError("could not load v3 split generator")
v3_generator = importlib.util.module_from_spec(V3_GENERATOR_SPEC)
V3_GENERATOR_SPEC.loader.exec_module(v3_generator)


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
        config_defaults=(
            ("validation", "configs/validation_v2.yaml"),
            ("locked-test", "configs/locked_test_v2.yaml"),
        ),
        confirm_experiments=(
            ("locked-test", "configs/locked_test_v2.yaml", "locked_test"),
        ),
        selection_consumer_configs=("configs/locked_test_v2.yaml",),
        exposed_test_manifests=(tmp_path / "split_manifest.json",),
        require_fresh_locked_test=True,
    )


def _git_blob_sha256(commit: str, relative: str) -> str:
    """Hash a frozen source file from its recorded commit, not the live tree."""

    payload = subprocess.run(
        ("git", "show", f"{commit}:{relative}"),
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(payload).hexdigest()


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


def test_v3_entrypoint_is_thin_and_paths_do_not_alias_exposed_protocols() -> None:
    wrapper = (ROOT / "run_paper_evidence_v3.py").read_text(encoding="utf-8")
    assert "from run_paper_evidence import V3_PROTOCOL, main" in wrapper
    assert "synthetic_cases" not in wrapper
    assert cli.V3_PROTOCOL.raw_root not in {
        cli.V1_PROTOCOL.raw_root,
        cli.V2_PROTOCOL.raw_root,
    }
    assert cli.V3_PROTOCOL.config_lock_path.name == "config_lock_v3.json"
    assert cli.V3_PROTOCOL.exposed_test_manifests == (
        ROOT / "split_manifest.json",
        ROOT / "split_manifest_v2.json",
    )
    parser = cli.build_parser(cli.V3_PROTOCOL)
    locked = parser.parse_args(["locked-test"])
    assert locked.config == "configs/locked_test_v3.yaml"
    assert locked.evidence_protocol is cli.V3_PROTOCOL


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
        "locked": True,
        "selection_status": "locked_after_validation",
        "synthetic_dataset": {
            "split_manifest": "split_manifest_v2.json",
            "split_manifest_sha256": "b" * 64,
        },
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


def test_v2_governor_negative_dataset_label_matches_locked_design() -> None:
    design = yaml.safe_load(
        (ROOT / "configs/synthetic_dataset_v2.yaml").read_text(encoding="utf-8")
    )
    assert design["deliberate_infeasible"]["dataset_id"] == (
        f"synthetic-deliberate-infeasible-{cli.V2_PROTOCOL.version}"
    )


def test_v2_report_uses_exact_protocol_bundle_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "assert_clean_commit",
        lambda root: type("State", (), {"commit": "a" * 40})(),
    )
    monkeypatch.setattr(
        cli,
        "_verify_locked_protocol_inputs",
        lambda protocol: {"protocol": {"path": "EXPERIMENT_PROTOCOL_V2.md"}},
    )

    def capture(raw_root, output_root, **kwargs):
        observed.update(kwargs)
        return {"status": "captured"}

    monkeypatch.setattr(cli, "build_final_result_artifacts", capture)
    result = cli.command_report(
        Namespace(
            raw_results=str(tmp_path / "raw"),
            output_root=str(tmp_path / "final"),
            expected_run_commit=None,
            evidence_protocol=cli.V2_PROTOCOL,
        )
    )

    assert result == {"status": "captured"}
    assert observed["required_bundles"] == tuple(
        bundle_name for _, _, bundle_name in cli.V2_PROTOCOL.confirm_experiments
    )
    assert "phase_a" not in observed["required_bundles"]
    assert observed["required_bundles"] == reporting.DEFAULT_RAW_BUNDLES[:-1]
    assert observed["protocol_version"] == "v2"
    assert observed["protocol_path"] == ROOT / "EXPERIMENT_PROTOCOL_V2.md"


def test_v2_report_provenance_names_and_hashes_the_locked_protocol() -> None:
    lock = json.loads((ROOT / "config_lock_v2.json").read_text(encoding="utf-8"))
    status = json.loads((ROOT / "protocol_status_v2.json").read_text(encoding="utf-8"))
    readme = reporting.technical_readme(
        protocol_version="v2",
        bundle_count=9,
        expected_test_trajectory_count=120,
        ranking_method="one_step_governed_pva_direct",
        comparison_count=1,
        ci_count=1,
        acceptance_required_count=1,
        acceptance_failure_count=0,
    )
    protocol_bytes = subprocess.run(
        (
            "git",
            "show",
            f"{status['confirmation_source_commit']}:{lock['protocol']['path']}",
        ),
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert readme.startswith("# Paper evidence v2: technical artifact index")
    assert hashlib.sha256(protocol_bytes).hexdigest() == lock["protocol"]["sha256"]
    assert lock["protocol"]["path"] == "EXPERIMENT_PROTOCOL_V2.md"


def test_v2_failed_confirmation_is_frozen_and_inventoried() -> None:
    status = json.loads((ROOT / "protocol_status_v2.json").read_text(encoding="utf-8"))
    inventory_path = ROOT / status["failure_inventory"]
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    assert status["status"] == "failed_nonconfirmatory_frozen"
    assert status["test_was_generated_and_viewed"] is True
    assert status["same_test_rerun_permitted"] is False
    assert status["resume_permitted"] is False
    assert inventory["source_commit"] == status["confirmation_source_commit"]
    assert inventory["status"] == status["status"]
    assert inventory["file_count"] == status["inventoried_file_count"]
    assert inventory["total_byte_size"] == status["inventoried_total_byte_size"]
    assert inventory["file_inventory_sha256"] == status["file_inventory_sha256"]
    assert sha256_file(inventory_path) == status["failure_inventory_sha256"]
    assert {bundle["name"]: bundle["status"] for bundle in inventory["bundles"]} == (
        {"acceleration": "partial", "locked_test": "complete", "validation": "complete"}
    )


def test_v2_real_replay_effective_config_and_samples_are_development_only() -> None:
    locked_config = load_config(ROOT / "configs/locked_test_v2.yaml")
    effective = cli._fresh_real_replay_config(
        locked_config, protocol=cli.V2_PROTOCOL
    )
    samples = [
        {
            "run_id": f"{effective['run_id']}::{method_id}",
            "method_id": method_id,
            "split": effective["data"]["split"],
        }
        for method_id in (
            "deployed_p_only_ordinary_ruckig",
            "one_step_governed_pva_direct",
        )
    ]

    cli._assert_fresh_real_replay_provenance(
        effective, samples, protocol=cli.V2_PROTOCOL
    )

    assert locked_config["run_id"] == "paper-evidence-v2-locked-test"
    assert locked_config["data"]["split"] == "test"
    assert effective["run_id"] == "paper-evidence-v2-real-replay"
    assert effective["data"]["split"] == "development"
    with pytest.raises(cli.SelectionLockError, match="samples differ"):
        cli._assert_fresh_real_replay_provenance(
            effective,
            [{**sample, "split": "test"} for sample in samples],
            protocol=cli.V2_PROTOCOL,
        )


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


def test_checked_in_v3_manifest_is_fresh_against_all_exposed_entries() -> None:
    path = ROOT / "split_manifest_v3.json"
    checked_in = json.loads(path.read_text(encoding="utf-8"))
    assert checked_in == v3_generator.build_manifest()
    datasets.validate_split_manifest(checked_in)
    datasets.validate_fresh_locked_test_manifest(
        path,
        exposed_manifest_paths=(
            ROOT / "split_manifest.json",
            ROOT / "split_manifest_v2.json",
        ),
    )
    counts = {
        split: sum(row["split"] == split for row in checked_in["trajectories"])
        for split in ("train", "validation", "test")
    }
    assert counts == {"train": 120, "validation": 60, "test": 120}
    assert all(
        row["locked"] == (row["split"] == "test") for row in checked_in["trajectories"]
    )


def test_v3_configs_are_versioned_and_carry_completed_selection_lock() -> None:
    lock = json.loads((ROOT / "config_lock_v3.json").read_text(encoding="utf-8"))
    assert lock["locked"] is True
    assert lock["selection_status"] == "locked_after_validation"
    config_names = {
        path for _, path in cli.V3_PROTOCOL.config_defaults if path.endswith("_v3.yaml")
    }
    assert config_names == {
        "configs/development_v3.yaml",
        "configs/validation_v3.yaml",
        "configs/locked_test_v3.yaml",
        "configs/acceleration_v3.yaml",
        "configs/governor_infeasible_v3.yaml",
        "configs/robustness_v3.yaml",
        "configs/rate_study_v3.yaml",
        "configs/multidof_plant_v3.yaml",
    }
    for relative in sorted(config_names):
        raw = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        assert raw["protocol_version"] == "v3"
        assert raw["data"]["split_manifest"] == "split_manifest_v3.json"
        if relative in cli.V3_PROTOCOL.selection_consumer_configs:
            assert raw["locked_selection"] == lock["locked_selection"]
        else:
            assert "locked_selection" not in raw
        resolved = load_config(ROOT / relative)
        expected_root = "runs/paper_evidence_v3" if not (
            raw.get("formal") or raw.get("require_clean")
        ) else "results/paper_evidence_v3"
        assert expected_root in str(resolved["output_root"])


def test_v3_completed_lock_hashes_and_no_test_execution_claims() -> None:
    lock = json.loads((ROOT / "config_lock_v3.json").read_text(encoding="utf-8"))
    canonical = cli._canonical_selection_text(lock["locked_selection"])

    assert lock["selection_policy"]["locked_selection_sha256"] == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert lock["prelock_state"]["trajectory_generation_scope"] == (
        "train_and_validation_only"
    )
    assert lock["prelock_state"]["test_trajectory_generation_performed"] is False
    assert lock["prelock_state"]["test_executed"] is False
    assert lock["prelock_state"]["test_viewed"] is False
    assert lock["locked_selection"]["test_trajectory_count_seen"] == 0
    assert lock["qp_qualification"]["qp_baseline_status"] == "qualified"
    assert lock["freshness_audit"]["trajectory_id_overlap_count"] == 0
    assert lock["freshness_audit"]["family_seed_overlap_count"] == 0

    locked_files = cli._locked_protocol_input_hashes(lock)
    assert set(locked_files) == {
        "EXPERIMENT_PROTOCOL_V3.md",
        "run_paper_evidence.py",
        "run_paper_evidence_v3.py",
        "configs/development_v3.yaml",
        "configs/synthetic_dataset_v3.yaml",
        "scripts/generate_split_manifest_v3.py",
        "split_manifest_v3.json",
        ".gitignore",
        "plot_data.csv",
        "split_manifest.json",
        "split_manifest_v2.json",
        *lock["implementation_files_sha256"],
        *lock["formal_config_sha256"],
    }
    source_commit = "cf3a517bc74236a4eb1b95c5b6eee952993a0837"
    for relative, expected in locked_files.items():
        assert _git_blob_sha256(source_commit, relative) == expected


def test_v3_postreview_status_reclassifies_without_mutating_frozen_status() -> None:
    frozen_path = ROOT / "protocol_status_v3.json"
    postreview = json.loads(
        (ROOT / "protocol_status_v3_postreview.json").read_text(encoding="utf-8")
    )

    assert postreview["status"] == "frozen_postreview_reclassified"
    assert postreview["immutability"]["v3_rerun_performed"] is False
    assert postreview["immutability"]["raw_bundles_modified"] is False
    assert postreview["immutability"]["numeric_summaries_modified"] is False
    assert postreview["postreview_classification"]["primary_77_38_percent_claim"] == (
        "not_confirmatory"
    )
    assert postreview["versioning_decision"]["execute_v4_in_this_review_cycle"] is False
    assert sha256_file(frozen_path) == postreview["frozen_source"][
        "original_status_sha256"
    ]


def test_v3_completed_confirmation_is_frozen_and_auditable() -> None:
    status = json.loads((ROOT / "protocol_status_v3.json").read_text(encoding="utf-8"))

    assert status["status"] == "confirmation_complete_acceptance_failed_frozen"
    assert status["confirmation_source_commit"] == (
        "cf3a517bc74236a4eb1b95c5b6eee952993a0837"
    )
    assert status["formal_confirmation_count"] == 1
    assert status["test_was_generated_and_viewed"] is True
    assert status["same_test_rerun_permitted"] is False
    assert status["resume_permitted"] is False
    assert status["raw_bundle_count"] == 9
    assert status["bounded_artifact_count"] == 68
    assert status["locked_test_trajectory_count"] == 120
    assert status["locked_test_sample_count"] == 1_012_776
    assert status["required_component_pass_count"] == 15
    assert status["required_component_failure_count"] == 3
    assert status["required_component_criteria"] == 18
    assert status["merge_gate"]["status"] == "blocked_keep_draft"
    assert status["primary_evidence"]["archive_sha256"] == (
        "3f63ff81e708925c4d8c55616585e9b9925c43e1f59ede637e418944b39b8da2"
    )

    validation = reporting.validate_root_artifact_index(
        ROOT / status["results_root"],
        expected_commit=status["confirmation_source_commit"],
    )
    assert validation["artifact_count"] == status["bounded_artifact_count"]
    assert validation["raw_bundle_count"] == status["raw_bundle_count"]
    assert validation["artifact_index_sha256"] == status["artifact_index_sha256"]


def test_v2_configs_carry_the_exact_completed_selection_lock() -> None:
    lock = json.loads((ROOT / "config_lock_v2.json").read_text(encoding="utf-8"))
    config_names = {
        path for _, path in cli.V2_PROTOCOL.config_defaults if path.endswith("_v2.yaml")
    }
    assert "configs/phase_a_v2.yaml" not in config_names
    for relative in sorted(config_names):
        raw = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        assert raw["protocol_version"] == "v2"
        assert raw["data"]["split_manifest"] == "split_manifest_v2.json"
        if relative in cli.V2_PROTOCOL.selection_consumer_configs:
            assert raw["locked_selection"] == lock["locked_selection"]
        else:
            assert "locked_selection" not in raw
        resolved = load_config(ROOT / relative)
        assert "paper_evidence_v2" in str(resolved["output_root"])


def test_v2_completed_lock_hashes_and_no_test_execution_claims() -> None:
    lock = json.loads((ROOT / "config_lock_v2.json").read_text(encoding="utf-8"))
    status = json.loads((ROOT / "protocol_status_v2.json").read_text(encoding="utf-8"))
    assert lock["locked"] is True
    assert lock["selection_status"] == "locked_after_validation"
    canonical = cli._canonical_selection_text(lock["locked_selection"])
    assert (
        lock["selection_policy"]["locked_selection_sha256"]
        == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    assert lock["prelock_state"]["trajectory_generation_performed"] is True
    assert lock["prelock_state"]["test_trajectory_generation_performed"] is False
    assert lock["prelock_state"]["test_executed"] is False
    assert lock["prelock_state"]["test_viewed"] is False
    assert lock["locked_selection"]["test_trajectory_count_seen"] == 0
    assert lock["qp_qualification"]["qp_baseline_status"] == "qualified"
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
        **lock["implementation_files_sha256"],
        **lock["formal_config_sha256"],
        **lock["workflow_files_sha256"],
        **lock["data_files_sha256"],
    }
    assert len(cli._locked_protocol_input_hashes(lock)) == 44
    assert len(locked_files) == 44
    for relative, expected in locked_files.items():
        committed_bytes = subprocess.run(
            ("git", "show", f"{status['confirmation_source_commit']}:{relative}"),
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert hashlib.sha256(committed_bytes).hexdigest() == expected


def test_v2_managed_outputs_do_not_dirty_a_confirmation_worktree(
    tmp_path: Path,
) -> None:
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
    (tmp_path / ".gitignore").write_text(
        (ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "sentinel.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        ("git", "add", ".gitignore", "sentinel.txt"), cwd=tmp_path, check=True
    )
    subprocess.run(
        ("git", "commit", "-q", "-m", "lock workflow"), cwd=tmp_path, check=True
    )
    for relative in (
        "results/paper_evidence_v2/raw_runs/validation/run.json",
        "results/paper_evidence_v2/raw_runs/locked_test/run.json",
        "results/paper_evidence_v2/artifact_index.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("generated\n", encoding="utf-8")

    state = assert_clean_commit(tmp_path)

    assert state.dirty is False


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


@pytest.mark.parametrize(
    ("command", "function"),
    (
        ("locked-test", cli.command_locked_test),
        ("acceleration", cli.command_acceleration),
        ("robustness", cli.command_robustness),
        ("rates", cli.command_rates),
        ("multidof", cli.command_multidof),
        ("plant", cli.command_plant),
    ),
)
def test_v2_test_consumers_reject_direct_calls_before_config_load(
    command: str, function, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda path: pytest.fail("config loaded before confirmation guard"),
    )
    args = Namespace(
        command=command,
        config="configs/development_v2.yaml",
        output=None,
        evidence_protocol=cli.V2_PROTOCOL,
        confirmation_run=False,
        confirmation_capability=None,
    )
    with pytest.raises(cli.SelectionLockError, match="inside command_confirm"):
        function(args)


def test_v2_test_helper_rejects_generation_without_confirm_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "run_id": "must-not-run",
        "data": {"split_manifest": "split_manifest_v2.json"},
    }
    monkeypatch.setattr(
        cli,
        "synthetic_cases",
        lambda *args, **kwargs: pytest.fail("test trajectory generator was reached"),
    )
    with pytest.raises(cli.SelectionLockError, match="one-shot confirm"):
        cli._synthetic_cases_for_config(
            config,
            "test",
            sample_rate_hz=100.0,
            maximum=None,
            protocol=cli.V2_PROTOCOL,
        )


def test_v2_confirmation_context_rejects_output_override_before_lock_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = object()
    monkeypatch.setattr(cli, "_ACTIVE_CONFIRM_CAPABILITY", capability)
    monkeypatch.setattr(
        cli,
        "_verify_locked_protocol_inputs",
        lambda **kwargs: pytest.fail("lock checked after forbidden output override"),
    )
    args = Namespace(
        command="locked-test",
        config="configs/locked_test_v2.yaml",
        output="somewhere-else",
        confirmation_run=True,
        confirmation_capability=capability,
        evidence_protocol=cli.V2_PROTOCOL,
    )
    with pytest.raises(cli.SelectionLockError, match="output overrides"):
        cli._require_confirmation_context(args, protocol=cli.V2_PROTOCOL)


def test_runtime_protocol_hash_verification_rejects_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol_for_tmp(tmp_path)
    lock = {
        "locked": True,
        "selection_status": "locked_after_validation",
        "implementation_files_sha256": {"otg_lab/a.py": "a" * 64},
        "formal_config_sha256": {
            "configs/validation_v2.yaml": "a" * 64,
            "configs/locked_test_v2.yaml": "a" * 64,
        },
        "workflow_files_sha256": {".gitignore": "a" * 64},
        "data_files_sha256": {
            "plot_data.csv": "a" * 64,
            "split_manifest.json": "a" * 64,
        },
    }
    checked: list[tuple[str, str | None]] = []
    monkeypatch.setattr(cli, "_load_json_mapping", lambda path, label: lock)
    monkeypatch.setattr(
        cli,
        "_locked_protocol_input_hashes",
        lambda value: {"configs/locked_test_v2.yaml": "a" * 64},
    )
    monkeypatch.setattr(
        cli,
        "_tracked_implementation_paths",
        lambda root: frozenset({"otg_lab/a.py"}),
    )

    def reject(path, **kwargs):
        checked.append((Path(path).name, kwargs.get("expected_sha256")))
        if kwargs.get("expected_sha256") is not None:
            raise cli.SelectionLockError("formal manifest hash mismatch")
        return "b" * 64

    monkeypatch.setattr(cli, "_assert_clean_committed_file", reject)
    with pytest.raises(cli.SelectionLockError, match="hash mismatch"):
        cli._verify_locked_protocol_inputs(protocol=protocol, repo_root=tmp_path)
    assert checked[-1] == ("locked_test_v2.yaml", "a" * 64)


def test_runtime_protocol_lock_requires_exact_implementation_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol_for_tmp(tmp_path)
    lock = {
        "locked": True,
        "selection_status": "locked_after_validation",
        "implementation_files_sha256": {"otg_lab/a.py": "a" * 64},
    }
    monkeypatch.setattr(cli, "_load_json_mapping", lambda path, label: lock)
    monkeypatch.setattr(
        cli, "_assert_clean_committed_file", lambda *args, **kwargs: "a" * 64
    )
    monkeypatch.setattr(
        cli,
        "_tracked_implementation_paths",
        lambda root: frozenset({"otg_lab/a.py", "otg_lab/b.py"}),
    )
    with pytest.raises(cli.SelectionLockError, match="exact tracked Python set"):
        cli._verify_locked_protocol_inputs(protocol=protocol, repo_root=tmp_path)


def test_confirmation_dispatch_cannot_activate_self_signed_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = object()
    monkeypatch.setattr(
        cli,
        "build_parser",
        lambda protocol: pytest.fail("self-signed capability reached dispatch"),
    )
    with pytest.raises(cli.SelectionLockError, match="activated by command_confirm"):
        cli._run_evidence_subcommand(
            ("locked-test", "--config", "configs/locked_test_v2.yaml"),
            protocol=cli.V2_PROTOCOL,
            confirmation_capability=capability,
        )
    assert cli._ACTIVE_CONFIRM_CAPABILITY is None
    assert cli._LOGICAL_COMMAND is None


def test_command_confirm_clears_capability_when_suite_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "assert_clean_commit", lambda root: object())
    monkeypatch.setattr(cli, "_confirm_output_paths", lambda **kwargs: ())
    monkeypatch.setattr(cli, "_assert_confirm_outputs_absent", lambda paths: None)
    monkeypatch.setattr(cli, "_load_committed_selection_lock", lambda **kwargs: {})

    def fail(protocol, capability):
        assert cli._ACTIVE_CONFIRM_CAPABILITY is capability
        raise RuntimeError("suite failed")

    monkeypatch.setattr(cli, "_execute_confirm", fail)
    with pytest.raises(RuntimeError, match="suite failed"):
        cli.command_confirm(Namespace(evidence_protocol=cli.V1_PROTOCOL))
    assert cli._ACTIVE_CONFIRM_CAPABILITY is None
    with pytest.raises(cli.SelectionLockError, match="active one-shot confirm"):
        cli._assert_test_generation_capability(cli.V2_PROTOCOL)


def test_failed_v2_status_blocks_resume_before_any_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "assert_clean_commit",
        lambda root: pytest.fail("preflight reached after frozen status"),
    )
    with pytest.raises(cli.SelectionLockError, match="fresh v3"):
        cli.command_confirm(Namespace(evidence_protocol=cli.V2_PROTOCOL))


def test_active_confirmation_records_logical_subcommand_and_clears_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = object()
    observed: dict[str, object] = {}

    def capture(parsed: Namespace) -> None:
        observed["command"] = cli._command()

    parser = type(
        "Parser",
        (),
        {"parse_args": lambda self, arguments: Namespace(function=capture)},
    )()
    monkeypatch.setattr(cli, "_ACTIVE_CONFIRM_CAPABILITY", capability)
    monkeypatch.setattr(cli, "build_parser", lambda protocol: parser)
    cli._run_evidence_subcommand(
        ("locked-test", "--config", "configs/locked_test_v2.yaml"),
        protocol=cli.V2_PROTOCOL,
        confirmation_capability=capability,
    )
    assert observed["command"][-4:] == [
        "locked-test",
        "--config",
        "configs/locked_test_v2.yaml",
        "--confirmation-run",
    ]
    assert cli._LOGICAL_COMMAND is None
