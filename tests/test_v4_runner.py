"""Gate-level tests for the narrow V4 coordinator.

These tests never construct a trajectory or execute the experiment pipeline.
"""

from __future__ import annotations

import pickle
import types
from pathlib import Path

import pytest

from otg_lab import v4_runner


def test_cli_has_only_narrow_commands_and_confirm_has_no_overrides() -> None:
    parser = v4_runner._parser()
    action = next(
        action for action in parser._actions if action.dest == "command"
    )
    assert set(action.choices) == {
        "dry-run",
        "validation",
        "prep-lock",
        "confirm",
        "report-only",
        "qa",
    }
    confirm = action.choices["confirm"]
    assert [item.dest for item in confirm._actions if item.dest != "help"] == []


def test_test_generator_rejects_missing_capability_before_api_use() -> None:
    touched = False

    def forbidden(**_kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("trajectory generator was reached")

    with pytest.raises(v4_runner.ConfirmationCapabilityError):
        v4_runner._generate_v4_test_cases(
            object(), {"synthetic_cases": forbidden}, {"run_id": "never"}
        )
    assert touched is False


def test_generic_case_helper_cannot_bypass_test_capability() -> None:
    with pytest.raises(v4_runner.ConfirmationCapabilityError):
        v4_runner._cases(
            {"synthetic_cases": lambda **_kwargs: []},
            split="test",
            manifest=Path("unused.json"),
            run_id="never",
        )


def test_same_information_audit_uses_artifact_canonical_pass_field() -> None:
    api = {
        "audit_primary_rows": lambda _rows: [],
        "primary_purity_by_trajectory": lambda _rows: [],
        "audit_same_information_rows": lambda _rows, **_kwargs: [
            {
                "trajectory_id": "case",
                "k": 0,
                "audit_passed": True,
                "configuration_identity_passed": True,
            }
        ],
        "audit_target_component_zeroing": lambda _rows: [],
        "audit_ordinary_rows": lambda _rows: [],
    }
    outcome = types.SimpleNamespace(samples=[], method_matrix=[])
    rows = v4_runner._extra_audits(api, outcome)["same_information_audit.csv"]
    assert rows[0]["audit_passed"] is True


def test_confirm_clears_capability_when_test_stage_fails(monkeypatch) -> None:
    pickle_rejected = False

    def fail_with_capability(capability, **_kwargs):
        nonlocal pickle_rejected
        with pytest.raises(TypeError):
            pickle.dumps(capability)
        pickle_rejected = True
        raise RuntimeError("boom")

    monkeypatch.setattr(
        v4_runner,
        "verify_confirmation_preflight",
        lambda: {"head": "a" * 40},
    )
    monkeypatch.setattr(v4_runner, "_run_phase_a_regression", lambda: {})
    monkeypatch.setattr(v4_runner, "_mark_test_visible", lambda _head: None)
    monkeypatch.setattr(
        v4_runner,
        "_run_locked_confirmation",
        fail_with_capability,
    )
    monkeypatch.setattr(v4_runner, "_freeze", lambda *_args: None)

    with pytest.raises(RuntimeError, match="boom"):
        v4_runner.confirm()
    assert pickle_rejected is True
    assert v4_runner._ACTIVE_CONFIRMATION_CAPABILITY is None


def test_confirm_freezes_when_source_drifts_after_raw(monkeypatch) -> None:
    frozen = []
    monkeypatch.setattr(
        v4_runner,
        "verify_confirmation_preflight",
        lambda: {"head": "a" * 40},
    )
    monkeypatch.setattr(v4_runner, "_run_phase_a_regression", lambda: {})
    monkeypatch.setattr(v4_runner, "_mark_test_visible", lambda _head: None)
    monkeypatch.setattr(
        v4_runner,
        "_run_locked_confirmation",
        lambda *_args, **_kwargs: {"raw": "complete"},
    )
    monkeypatch.setattr(
        v4_runner,
        "_verify_post_test_immutability",
        lambda _head: (_ for _ in ()).throw(
            v4_runner.V4PreflightError("HEAD changed after V4 test visibility")
        ),
    )
    monkeypatch.setattr(
        v4_runner,
        "_freeze",
        lambda head, error: frozen.append((head, str(error))),
    )
    with pytest.raises(v4_runner.V4PreflightError, match="HEAD changed"):
        v4_runner.confirm()
    assert frozen == [("a" * 40, "HEAD changed after V4 test visibility")]
    assert v4_runner._ACTIVE_CONFIRMATION_CAPABILITY is None


def test_preflight_rejects_existing_locked_output(monkeypatch, tmp_path: Path) -> None:
    existing = tmp_path / "locked_test"
    existing.mkdir()
    monkeypatch.setattr(v4_runner, "LOCKED_TEST_ROOT", existing)
    monkeypatch.setattr(v4_runner, "ORACLE_ROOT", tmp_path / "oracle")
    monkeypatch.setattr(v4_runner, "RUNTIME_STATUS_PATH", tmp_path / "status")
    monkeypatch.setattr(v4_runner, "TEST_VISIBLE_SENTINEL", tmp_path / "sentinel")
    with pytest.raises(v4_runner.V4PreflightError, match="blocks confirmation"):
        v4_runner._verify_no_prior_test_state()


def test_preflight_rejects_unknown_result_but_allows_validation(
    monkeypatch, tmp_path: Path
) -> None:
    results = tmp_path / "results/paper_evidence_v4"
    (results / "raw_runs/validation").mkdir(parents=True)
    (results / "raw_runs/validation/artifact_index.json").write_text(
        "{}", encoding="utf-8"
    )
    (results / "statistics").mkdir()
    monkeypatch.setattr(v4_runner, "ROOT", tmp_path)
    monkeypatch.setattr(v4_runner, "RESULTS_ROOT", results)
    monkeypatch.setattr(
        v4_runner, "LOCKED_TEST_ROOT", results / "raw_runs/locked_test"
    )
    monkeypatch.setattr(
        v4_runner, "ORACLE_ROOT", results / "raw_runs/oracle_diagnostic"
    )
    monkeypatch.setattr(v4_runner, "RUNTIME_STATUS_PATH", results / ".status")
    monkeypatch.setattr(v4_runner, "TEST_VISIBLE_SENTINEL", results / ".sentinel")
    with pytest.raises(v4_runner.V4PreflightError, match="statistics"):
        v4_runner._verify_no_prior_test_state()


def test_clean_gate_rejects_dirty_worktree(monkeypatch) -> None:
    monkeypatch.setattr(
        v4_runner,
        "_run_git",
        lambda *_arguments: " M otg_lab/v4_runner.py",
    )
    with pytest.raises(v4_runner.V4PreflightError, match="clean worktree"):
        v4_runner._require_clean()


def test_hash_gate_rejects_changed_locked_file(monkeypatch, tmp_path: Path) -> None:
    locked = tmp_path / "locked.txt"
    locked.write_text("changed", encoding="utf-8")
    v3_files = []
    for index in range(len(v4_runner.V3_IMMUTABLE_PATHS)):
        path = tmp_path / f"v3-{index}.txt"
        path.write_text(str(index), encoding="utf-8")
        v3_files.append(path)
    monkeypatch.setattr(v4_runner, "ROOT", tmp_path)
    monkeypatch.setattr(v4_runner, "V3_IMMUTABLE_PATHS", tuple(v3_files))
    lock = {
        "confirmation_file_hashes": {"locked.txt": "0" * 64},
        "v3_immutable_hashes": {
            path.name: v4_runner._sha256(path) for path in v3_files
        },
    }
    with pytest.raises(v4_runner.V4PreflightError, match="hash mismatch"):
        v4_runner._verify_hashes(lock)


def test_v3_hash_gate_is_separate_and_fail_closed(
    monkeypatch, tmp_path: Path
) -> None:
    locked = tmp_path / "locked.txt"
    locked.write_text("stable", encoding="utf-8")
    frozen = tmp_path / "frozen.txt"
    frozen.write_text("changed", encoding="utf-8")
    monkeypatch.setattr(v4_runner, "ROOT", tmp_path)
    monkeypatch.setattr(v4_runner, "V3_IMMUTABLE_PATHS", (frozen,))
    lock = {
        "confirmation_file_hashes": {
            "locked.txt": v4_runner._sha256(locked)
        },
        "v3_immutable_hashes": {"frozen.txt": "0" * 64},
    }
    with pytest.raises(v4_runner.V4PreflightError, match="frozen V3"):
        v4_runner._verify_hashes(lock)


def test_confirmation_ref_must_equal_exact_head(monkeypatch) -> None:
    values = {
        ("rev-parse", "--verify", f"{v4_runner.CONFIRMATION_HEAD_REF}^{{commit}}"):
            "a" * 40,
        ("rev-parse", "HEAD"): "b" * 40,
    }
    monkeypatch.setattr(v4_runner, "_run_git", lambda *args: values[args])
    lock = {"git": {"confirmation_head_ref": v4_runner.CONFIRMATION_HEAD_REF}}
    with pytest.raises(v4_runner.V4PreflightError, match="exactly to HEAD"):
        v4_runner._verify_authorizing_ref(lock)


def test_forbidden_formal_capability_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    method = tmp_path / "matrix.json"
    method.write_text('{"method": "ruckig_' + 'pro"}', encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("method: community\n", encoding="utf-8")
    monkeypatch.setattr(v4_runner, "METHOD_MATRIX_PATH", method)
    monkeypatch.setattr(v4_runner, "FORMAL_CONFIGS", (config,))
    monkeypatch.setattr(v4_runner, "ROOT", tmp_path)
    with pytest.raises(v4_runner.V4PreflightError, match="forbidden"):
        v4_runner._verify_no_forbidden_v4_capability()


@pytest.mark.parametrize("raw_commit", ["abc", "A" * 40, "g" * 40, "0" * 39])
def test_report_only_requires_exact_raw_commit(raw_commit: str) -> None:
    with pytest.raises(v4_runner.ReportOnlyError):
        v4_runner.report_only_resume(raw_commit)


def test_independent_statistics_must_explicitly_pass() -> None:
    artifacts = types.SimpleNamespace(
        validate_statistical_artifacts=lambda *_args, **_kwargs: {
            "all_independent_statistical_recomputations_verified": None
        }
    )
    with pytest.raises(v4_runner.V4RunnerError, match="independent"):
        v4_runner._validate_independent_statistics(artifacts)


def test_report_only_never_loads_execution_api(monkeypatch, tmp_path: Path) -> None:
    raw_commit = "a" * 40
    fake = types.SimpleNamespace(
        validate_report_only_inputs=lambda **kwargs: {
            "raw_commit": kwargs["raw_commit"]
        },
        finalize_v4_results=lambda **kwargs: {
            "report_only": kwargs["report_only"]
        },
        validate_statistical_artifacts=lambda *_args, **_kwargs: {
            "all_independent_statistical_recomputations_verified": True
        },
    )
    fake_contextual = types.SimpleNamespace(
        generate_v4_contextual_tables=lambda **_kwargs: {
            "generated": True,
            "source_hashes": {},
            "table_hashes": {},
        }
    )
    fake_handoff = types.SimpleNamespace(
        generate_v4_handoff=lambda *_args, **_kwargs: {
            "primary_result_classification": "inconclusive",
            "statistical_classification": "inconclusive",
        }
    )
    fake_statistics = types.SimpleNamespace(
        analyze_v4_confirmation=lambda **_kwargs: {"rebuilt": True}
    )
    monkeypatch.setattr(
        v4_runner.importlib,
        "import_module",
        lambda name: {
            "otg_lab.v4_artifacts": fake,
            "otg_lab.v4_contextual": fake_contextual,
            "otg_lab.v4_handoff": fake_handoff,
            "otg_lab.v4_statistics": fake_statistics,
        }[name],
    )
    monkeypatch.setattr(
        v4_runner,
        "_execution_api",
        lambda: (_ for _ in ()).throw(AssertionError("execution API loaded")),
    )
    monkeypatch.setattr(v4_runner, "RESULTS_ROOT", tmp_path)
    (tmp_path / "statistics").mkdir()
    monkeypatch.setattr(
        v4_runner,
        "_load_json",
        lambda path: (
            {
                "report_only_resume_permitted": True,
                "raw_experiment_resume_permitted": False,
                "confirmation_source_commit": raw_commit,
            }
            if path == v4_runner.RUNTIME_STATUS_PATH
            else {"confirmation_file_hashes": {"source.py": "0" * 64}}
        ),
    )
    monkeypatch.setattr(v4_runner, "_head", lambda: "b" * 40)
    monkeypatch.setattr(
        v4_runner,
        "_verify_report_only_code_state",
        lambda _raw: {
            "reporting_commit": "b" * 40,
            "changed_reporting_paths": [],
        },
    )
    monkeypatch.setattr(v4_runner, "_write_result_status", lambda **_kwargs: "complete_negative")
    result = v4_runner.report_only_resume(raw_commit)
    assert result["final"] == {"report_only": True}


def test_dry_and_validation_use_fixed_manifests_in_source() -> None:
    source = Path(v4_runner.__file__).read_text(encoding="utf-8")
    assert 'ROOT / "split_manifest_v3.json"' in source
    assert 'split="validation"' in source
    assert 'split="train"' in source
    assert 'split="test"' in source
    assert "repetitions=5, warmup_cycles=100" in source
    assert 'run_paper_evidence_v4.py", "confirm"' in source
    assert 'run_paper_evidence_v4.py", "validation"' in source


def test_phase_a_calls_function_directly_not_compatibility_script() -> None:
    source = Path(v4_runner.__file__).read_text(encoding="utf-8")
    assert "run_phase_a_p_only_compatibility" in source
    assert "run_ruckig_compatibility.py" not in source


def test_wrapper_sets_threads_and_shell_package_before_otg_import() -> None:
    source = (v4_runner.ROOT / "run_paper_evidence_v4.py").read_text(
        encoding="utf-8"
    )
    assert source.index('os.environ[_name] = "1"') < source.index(
        'import_module("otg_lab.v4_runner")'
    )
    assert source.index('sys.modules["otg_lab"] = _package') < source.index(
        'import_module("otg_lab.v4_runner")'
    )
