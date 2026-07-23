from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "otg_development_dry_run",
    ROOT / "scripts" / "run_full_development_dry_run.py",
)
if SCRIPT_SPEC is None or SCRIPT_SPEC.loader is None:
    raise RuntimeError("could not load development dry-run script")
SCRIPT = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_parallel_job_count_must_be_positive(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="at least 1"):
        SCRIPT._positive_int(value)


@pytest.mark.parametrize("value", ["abc", "a" * 39, "A" * 40])
def test_report_resume_requires_full_lowercase_commit(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="full lowercase"):
        SCRIPT._full_commit(value)


def test_experiment_worker_uses_bundle_specific_atomic_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], Path]] = []

    def fake_run(arguments, *, protocol):
        calls.append((arguments, protocol.raw_root))
        return {"status": "complete"}

    monkeypatch.setattr(SCRIPT, "_run", fake_run)
    assert (
        SCRIPT._run_experiment(
            "locked-test", "configs/locked_test_v2.yaml", "locked_test", tmp_path
        )
        == "locked-test"
    )
    protocol = SCRIPT.build_dry_protocol(tmp_path)
    assert calls == [
        (
            [
                "locked-test",
                "--config",
                "configs/locked_test_v2.yaml",
                "--output",
                str(protocol.raw_root / "locked_test"),
            ],
            protocol.raw_root,
        )
    ]


def test_report_resume_reuses_raw_commit_and_records_reporting_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = SCRIPT.build_dry_protocol(tmp_path)
    protocol.raw_root.mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_run(arguments, *, protocol):
        calls.append(arguments)
        return {"status": "complete"}

    monkeypatch.setattr(SCRIPT, "_run", fake_run)
    raw_commit = "a" * 40
    reporting_commit = "b" * 40

    status = SCRIPT._resume_report(
        dry_root=tmp_path,
        protocol=protocol,
        raw_commit=raw_commit,
        reporting_commit=reporting_commit,
    )

    assert calls == [
        [
            "report",
            "--raw-results",
            str(protocol.raw_root),
            "--output-root",
            str(protocol.final_root),
            "--expected-run-commit",
            raw_commit,
        ]
    ]
    assert status["source_commit"] == raw_commit
    assert status["reporting_commit"] == reporting_commit
    assert status["report_only_resume"] is True
    assert status["parallel_jobs"] is None
    assert status["completed"][-2:] == ["qa", "report"]
    assert (tmp_path / "development_dry_run_status.json").is_file()
