from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "sync_runs_results.sh"
)
DEFAULT_REMOTE_URI = "cos://psi-user-data-1351596430/litang/mc/otg-lab"


def _make_project(root: Path) -> None:
    for collection, name in (
        ("experiments", "E01_example"),
        ("analyses", "A01_example"),
    ):
        entity = root / collection / name
        (entity / "runs/run-one").mkdir(parents=True)
        (entity / "runs/run-one/artifact.txt").write_text(
            "artifact\n", encoding="utf-8"
        )
        (entity / "results").mkdir()
        (entity / "results/index.csv").write_text("id\n", encoding="utf-8")


def _make_fake_coscli(root: Path) -> tuple[Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    args_path = root / "coscli-args"
    executable = bin_dir / "coscli"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\0' \"$@\" > \"${FAKE_COSCLI_ARGS_PATH}\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, args_path


def _run(
    tmp_path: Path,
    *args: str,
    project: bool = True,
    with_coscli: bool = True,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    project_dir = tmp_path / "project"
    if project:
        _make_project(project_dir)

    env = os.environ.copy()
    env["COSCLI_SNAPSHOT_PATH"] = str(tmp_path / "snapshot")
    env["COSCLI_FAIL_OUTPUT_PATH"] = str(tmp_path / "failures")
    env["COSCLI_PROCESS_LOG_PATH"] = str(tmp_path / "process")
    args_path = tmp_path / "unused-coscli-args"
    if with_coscli:
        bin_dir, args_path = _make_fake_coscli(tmp_path)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["FAKE_COSCLI_ARGS_PATH"] = str(args_path)
    else:
        env["PATH"] = "/usr/bin:/bin"
    if extra_env:
        env.update(extra_env)

    completed = subprocess.run(
        [str(SCRIPT_PATH), *args],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return completed, project_dir, args_path


def _coscli_args(args_path: Path) -> list[str]:
    return [
        value.decode()
        for value in args_path.read_bytes().split(b"\0")
        if value
    ]


def _flag_value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_download_is_default_and_uses_protected_scope(tmp_path: Path) -> None:
    completed, project_dir, args_path = _run(
        tmp_path,
        "--project-dir",
        str(tmp_path / "project"),
    )

    assert completed.returncode == 0, completed.stderr
    args = _coscli_args(args_path)
    assert args[:3] == ["sync", DEFAULT_REMOTE_URI, str(project_dir)]
    assert _flag_value(args, "--snapshot-path") == str(tmp_path / "snapshot")
    assert _flag_value(args, "--fail-output-path") == str(tmp_path / "failures")
    assert _flag_value(args, "--process-log-path") == str(tmp_path / "process")
    assert _flag_value(args, "--log-path") == str(tmp_path / "process")
    assert _flag_value(args, "--routines") == "8"
    assert _flag_value(args, "--thread-num") == "8"
    assert "--disable-log" not in args
    assert "--skip-dir" not in args
    assert "--delete" not in args
    assert "--force" not in args

    scope = re.compile(_flag_value(args, "--include"))
    assert scope.search("/repo/experiments/E01_example/runs/run/artifact.csv")
    assert scope.search("/repo/experiments/E01_example/results/index.csv")
    assert scope.search("/repo/analyses/A07_example/runs/run/report.html")
    assert scope.search("/repo/analyses/A07_example/results/.gitkeep")
    assert not scope.search("/repo/experiments/_template/results/index.csv")
    assert not scope.search("/repo/experiments/E14_example/sharded_runs/1/a.csv")
    assert not scope.search("/repo/experiments/E01_example/results.md")
    assert not scope.search("/repo/analyses/example/results/run/a.csv")


def test_project_directory_defaults_to_script_parent(tmp_path: Path) -> None:
    completed, _, args_path = _run(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert _coscli_args(args_path)[:3] == [
        "sync",
        DEFAULT_REMOTE_URI,
        str(SCRIPT_PATH.parents[1]),
    ]


def test_upload_uses_overrides_and_forwards_exclude(tmp_path: Path) -> None:
    remote_uri = "cos://team/archive/otg-lab/"
    completed, project_dir, args_path = _run(
        tmp_path,
        "--upload",
        "--project-dir",
        str(tmp_path / "project"),
        "--remote-uri",
        remote_uri,
        "--",
        "--exclude",
        ".*\\.tmp$",
        extra_env={"COSCLI_ROUTINES": "3", "COSCLI_THREAD_NUM": "5"},
    )

    assert completed.returncode == 0, completed.stderr
    args = _coscli_args(args_path)
    assert args[:3] == ["sync", str(project_dir), remote_uri.rstrip("/")]
    assert _flag_value(args, "--routines") == "3"
    assert _flag_value(args, "--thread-num") == "5"
    assert args[args.index("--exclude") + 1] == ".*\\.tmp$"
    assert "--skip-dir" in args
    assert "--delete" not in args
    assert args[-2] == "--include"


def test_remote_uri_environment_override(tmp_path: Path) -> None:
    remote_uri = "cos://team/backups/otg-lab"
    completed, _, args_path = _run(
        tmp_path,
        "--project-dir",
        str(tmp_path / "project"),
        extra_env={"OTG_LAB_RUN_RESULTS_COS_URI": remote_uri},
    )

    assert completed.returncode == 0, completed.stderr
    assert _coscli_args(args_path)[1] == remote_uri


def test_mirror_upload_requires_confirmation_and_adds_delete(tmp_path: Path) -> None:
    project_arg = ("--project-dir", str(tmp_path / "project"))
    rejected, _, _ = _run(tmp_path, "--mirror-upload", *project_arg)
    assert rejected.returncode != 0
    assert "rerun with --yes" in rejected.stderr

    confirmed_root = tmp_path / "confirmed"
    confirmed_root.mkdir()
    completed, project_dir, args_path = _run(
        confirmed_root,
        "--mirror-upload",
        "--yes",
        "--project-dir",
        str(confirmed_root / "project"),
    )

    assert completed.returncode == 0, completed.stderr
    args = _coscli_args(args_path)
    assert args[:3] == ["sync", str(project_dir), DEFAULT_REMOTE_URI]
    assert "--skip-dir" in args
    assert "--delete" in args
    assert "--force" in args
    assert args.index("--delete") < args.index("--include")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--download", "--upload"), "choose only one mode"),
        (("--remote-uri", "cos://bucket"), "non-empty prefix"),
        (("--upload", "--", "--include", ".*"), "extra --include"),
        (("--upload", "--", "--include=.*"), "extra --include"),
        (("--upload", "--", "--delete"), "only allowed via --mirror-upload"),
        (("--upload", "--delete"), "pass extra coscli flags after --"),
    ],
)
def test_rejects_unsafe_or_invalid_arguments(
    tmp_path: Path,
    arguments: tuple[str, ...],
    message: str,
) -> None:
    completed, _, _ = _run(
        tmp_path,
        *arguments,
        "--project-dir",
        str(tmp_path / "project"),
    )

    assert completed.returncode != 0
    assert message in completed.stderr


def test_rejects_missing_coscli(tmp_path: Path) -> None:
    completed, _, _ = _run(
        tmp_path,
        "--project-dir",
        str(tmp_path / "project"),
        with_coscli=False,
    )

    assert completed.returncode != 0
    assert "coscli is not installed" in completed.stderr


@pytest.mark.parametrize("option", ["--remote-uri", "--project-dir"])
def test_rejects_options_without_values(tmp_path: Path, option: str) -> None:
    completed, _, _ = _run(tmp_path, option)

    assert completed.returncode != 0
    assert f"{option} requires a value" in completed.stderr


def test_rejects_invalid_project_and_snapshot_paths(tmp_path: Path) -> None:
    missing, _, _ = _run(
        tmp_path,
        "--project-dir",
        str(tmp_path / "missing"),
        project=False,
    )
    assert missing.returncode != 0
    assert "project directory does not exist" in missing.stderr

    nested_root = tmp_path / "nested"
    nested_root.mkdir()
    nested_snapshot = nested_root / "project/.snapshot"
    nested, _, _ = _run(
        nested_root,
        "--project-dir",
        str(nested_root / "project"),
        extra_env={"COSCLI_SNAPSHOT_PATH": str(nested_snapshot)},
    )
    assert nested.returncode != 0
    assert "must be outside the project directory" in nested.stderr


def test_help_documents_defaults_without_coscli(tmp_path: Path) -> None:
    completed, _, _ = _run(tmp_path, "--help", project=False, with_coscli=False)

    assert completed.returncode == 0
    assert DEFAULT_REMOTE_URI in completed.stdout
    assert "~/.cache/coscli-snapshot/otg-lab-run-results-<mode>" in (
        completed.stdout
    )
    assert "~/.cache/coscli-output/otg-lab-run-results-<mode>/failures" in (
        completed.stdout
    )
