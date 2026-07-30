from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_all_experiments.py"
)
SPEC = importlib.util.spec_from_file_location("run_all_experiments", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
run_all_experiments = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_all_experiments
SPEC.loader.exec_module(run_all_experiments)


def _experiment(root: Path, name: str) -> None:
    directory = root / "experiments" / name
    directory.mkdir(parents=True)
    (directory / "experiment.py").write_text("", encoding="utf-8")


def test_discover_experiments_is_sorted_and_excludes_non_experiments(
    tmp_path: Path,
) -> None:
    _experiment(tmp_path, "E11_latest")
    _experiment(tmp_path, "E02_second")
    _experiment(tmp_path, "E01_first")
    _experiment(tmp_path, "_template")
    _experiment(tmp_path, "A01_analysis")
    (tmp_path / "experiments/E03_missing_entrypoint").mkdir()

    assert run_all_experiments.discover_experiments(tmp_path) == (
        "E01_first",
        "E02_second",
        "E11_latest",
    )


def test_worker_continues_after_failure_and_emits_processed_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiments = ("E01_first", "E02_second", "E03_third")
    monkeypatch.setattr(
        run_all_experiments,
        "discover_experiments",
        lambda _root: experiments,
    )
    monkeypatch.setattr(run_all_experiments.shutil, "which", lambda name: name)
    progress: list[tuple[int, int, str]] = []
    monkeypatch.setattr(
        run_all_experiments,
        "emit_progress",
        lambda _root, *, current, total, message: progress.append(
            (current, total, message)
        ),
    )
    attempts: list[str] = []

    def fake_run_experiment(_root: Path, experiment: str) -> int:
        attempts.append(experiment)
        return 7 if experiment == "E02_second" else 0

    monkeypatch.setattr(
        run_all_experiments,
        "run_experiment",
        fake_run_experiment,
    )

    exit_code = run_all_experiments.run_worker(tmp_path)

    assert exit_code == run_all_experiments.FAILURE_EXIT_CODE
    assert attempts == list(experiments)
    assert [item[0] for item in progress] == [0, 0, 1, 1, 2, 2, 3]
    assert all(item[1] == len(experiments) for item in progress)
    assert all("E0" not in item[2] for item in progress)


def test_runbuoy_preflight_reports_unreachable_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_all_experiments.shutil, "which", lambda _name: "ok")

    def fake_completed(
        args: tuple[str, ...],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        assert args == ("runbuoy", "doctor", "--json")
        assert cwd == tmp_path
        return subprocess.CompletedProcess(
            args,
            1,
            stdout=json.dumps(
                {
                    "ready": False,
                    "checks": {"server_reachable": False},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(
        run_all_experiments,
        "_completed_command",
        fake_completed,
    )

    with pytest.raises(RuntimeError, match="server is unreachable"):
        run_all_experiments.require_runbuoy(tmp_path)


def test_start_uses_one_safe_structured_runbuoy_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiments = ("E01_first", "E02_second")
    monkeypatch.setattr(
        run_all_experiments,
        "discover_experiments",
        lambda _root: experiments,
    )
    monkeypatch.setattr(
        run_all_experiments,
        "require_runbuoy",
        lambda _root: None,
    )
    observed: list[str] = []

    def fake_completed(
        args: list[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path
        observed.extend(args)
        payload = {
            "ok": True,
            "run_id": "019c-experiment-suite",
            "result": {"exit_code": 2, "status": "FAILED"},
        }
        return subprocess.CompletedProcess(
            args,
            2,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(
        run_all_experiments,
        "_completed_command",
        fake_completed,
    )

    result = run_all_experiments.start_monitored_run(tmp_path)

    assert result.run_id == "019c-experiment-suite"
    assert result.experiment_count == 2
    assert result.exit_code == 2
    assert observed[observed.index("--title") + 1] == "OTG experiment suite"
    assert observed[observed.index("--progress") + 1] == "structured"
    assert "--wait" in observed
    assert "--share-log-tail" not in observed
    separator = observed.index("--")
    assert observed[separator + 1 :] == [
        sys.executable,
        str(SCRIPT_PATH),
        "--worker",
    ]
