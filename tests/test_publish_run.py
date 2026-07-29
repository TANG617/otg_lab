from __future__ import annotations

import csv
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

import otg_lab.publishing as publishing
from otg_lab.cli import main
from otg_lab.publishing import (
    GitHubRelease,
    PublicationPlan,
    ReleaseAssets,
    prepare_release_assets,
    promote_run_result,
    publish_run,
    validate_run_for_publication,
)
from otg_lab.runio import sha256_file


def _git(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(project_root: Path, message: str) -> str:
    _git(
        project_root,
        "-c",
        "user.name=OTG Test",
        "-c",
        "user.email=otg@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(project_root, "rev-parse", "HEAD")


def _completed_run(tmp_path: Path) -> tuple[Path, Path, str]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _git(project_root, "init")
    (project_root / ".gitignore").write_text(
        "/experiments/*/runs/\n",
        encoding="utf-8",
    )
    (project_root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(project_root, "add", ".gitignore", "tracked.txt")
    commit = _commit(project_root, "test fixture")

    spec_hash = "a" * 64
    run_directory = (
        project_root
        / "experiments"
        / "E99_publication_fixture"
        / "runs"
        / f"20260729T120000.000000Z__{spec_hash[:12]}"
    )
    artifacts = {
        "analysis/acceptance_summary.md": "# Accepted\n\nResult passed.\n",
        "analysis/report.md": "# Report\n",
        "analysis/acceptance.csv": "metric,passed\nrmse,true\n",
        "analysis/trajectory_metrics.csv": "sample,value\n0,1\n1,2\n",
        "analysis/figures/overview.png": "not-a-real-png",
        "methods/method/input/trace.csv": "time,value\n0,1\n",
    }
    for relative_path, content in artifacts.items():
        path = run_directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    outputs = {
        relative_path: sha256_file(run_directory / relative_path)
        for relative_path in artifacts
    }
    manifest = {
        "schema_version": "otg.run_manifest.v1",
        "status": "completed",
        "spec_hash": spec_hash,
        "resolved_experiment_spec": {
            "experiment_id": "E99",
            "title": "E99 publication fixture",
        },
        "git": {
            "commit": commit,
            "branch": "main",
            "dirty": False,
            "status_porcelain": [],
        },
        "environment": {
            "python": "3.9",
            "packages": {"ruckig": "0.17.3"},
        },
        "failure_count": 0,
        "required_failure_count": 0,
        "outputs": outputs,
    }
    (run_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return project_root, run_directory, commit


def test_promotes_analysis_only_and_packages_results_deterministically(
    tmp_path: Path,
) -> None:
    project_root, run_directory, commit = _completed_run(tmp_path)
    plan = validate_run_for_publication(project_root, run_directory)

    result_directory = promote_run_result(plan)
    first = prepare_release_assets(
        plan,
        result_directory,
        tmp_path / "first",
    )
    second = prepare_release_assets(
        plan,
        result_directory,
        tmp_path / "second",
    )

    assert plan.git_commit == commit
    assert result_directory == (
        project_root
        / "experiments"
        / "E99_publication_fixture"
        / "results"
        / run_directory.name
    )
    assert (result_directory / "manifest.json").is_file()
    assert (result_directory / "RESULT.md").is_file()
    assert (result_directory / "SHA256SUMS").is_file()
    assert (result_directory / "analysis/trajectory_metrics.csv").is_file()
    assert not (result_directory / "methods").exists()
    assert first.result_archive_sha256 == second.result_archive_sha256
    assert first.result_file_count == 8

    archive_root = f"experiments/E99_publication_fixture/results/{run_directory.name}"
    with zipfile.ZipFile(first.result_archive) as archive:
        names = set(archive.namelist())
    assert f"{archive_root}/RESULT.md" in names
    assert f"{archive_root}/manifest.json" in names
    assert f"{archive_root}/analysis/trajectory_metrics.csv" in names
    assert not any("/methods/" in name for name in names)
    assert first.checksums_file.read_text(encoding="utf-8") == (
        f"{first.result_archive_sha256}  {first.result_archive.name}\n"
    )


def test_promoted_result_can_be_committed_and_reused_from_descendant_head(
    tmp_path: Path,
) -> None:
    project_root, run_directory, manifest_commit = _completed_run(tmp_path)
    plan = validate_run_for_publication(project_root, run_directory)
    result_directory = promote_run_result(plan)
    _git(project_root, "add", "experiments/E99_publication_fixture/results")
    descendant = _commit(project_root, "promote result")

    assert descendant != manifest_commit
    descendant_plan = validate_run_for_publication(
        project_root,
        run_directory,
    )
    assert descendant_plan.git_commit == manifest_commit
    assert promote_run_result(descendant_plan) == result_directory


def test_validation_rejects_dirty_current_worktree(tmp_path: Path) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    (project_root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dirty Git worktree"):
        validate_run_for_publication(project_root, run_directory)


def test_validation_rejects_run_recorded_from_dirty_worktree(
    tmp_path: Path,
) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    manifest_path = run_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["git"]["dirty"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="run was produced from a dirty"):
        validate_run_for_publication(project_root, run_directory)


def test_validation_rejects_changed_declared_output(
    tmp_path: Path,
) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    (run_directory / "analysis/report.md").write_text(
        "changed\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_run_for_publication(project_root, run_directory)


def test_existing_different_promoted_result_is_not_overwritten(
    tmp_path: Path,
) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    plan = validate_run_for_publication(project_root, run_directory)
    result_directory = promote_run_result(plan)
    (result_directory / "RESULT.md").write_text(
        "different\n",
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError, match="different contents"):
        promote_run_result(plan)


def test_asset_preparation_removes_partial_files_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    plan = validate_run_for_publication(project_root, run_directory)
    result_directory = promote_run_result(plan)
    output_directory = tmp_path / "broken-assets"

    def fail_archive(
        _result_directory: Path,
        _project_root: Path,
        destination: Path,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("partial", encoding="utf-8")
        raise RuntimeError("archive failure")

    monkeypatch.setattr(publishing, "_write_result_archive", fail_archive)
    with pytest.raises(RuntimeError, match="archive failure"):
        prepare_release_assets(
            plan,
            result_directory,
            output_directory,
        )
    assert list(output_directory.iterdir()) == []


def test_publish_run_records_promoted_release_after_remote_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    release = GitHubRelease(
        url="https://github.example/releases/e99",
        state="published",
        published_at="2026-07-29T12:30:00Z",
        runbuoy_run_id="019c-test-run",
    )

    def fake_create(
        _plan: PublicationPlan,
        _assets: ReleaseAssets,
        *,
        repository: str | None,
        draft: bool,
    ) -> GitHubRelease:
        assert repository == "owner/repository"
        assert draft is False
        return release

    monkeypatch.setattr(
        "otg_lab.publishing._create_github_release",
        fake_create,
    )
    result = publish_run(
        project_root,
        run_directory,
        repository="owner/repository",
        output_directory=tmp_path / "release-assets",
    )

    assert result.release == release
    assert result.result_directory.is_dir()
    with (project_root / "experiments/E99_publication_fixture/results/index.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["release_url"] == release.url
    assert rows[0]["release_state"] == "published"
    assert rows[0]["result_directory"].startswith(
        "experiments/E99_publication_fixture/results/"
    )
    assert rows[0]["result_archive_sha256"] == result.assets.result_archive_sha256


def test_runbuoy_preflight_reports_unreachable_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publishing.shutil, "which", lambda _name: "/runbuoy")

    def fake_run_command(
        args: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert args == ("runbuoy", "doctor", "--json")
        assert cwd == tmp_path
        assert check is False
        payload = {
            "ok": False,
            "ready": False,
            "checks": {"server_reachable": False},
        }
        return subprocess.CompletedProcess(
            args,
            1,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(publishing, "_run_command", fake_run_command)
    with pytest.raises(RuntimeError, match="server is unreachable"):
        publishing._require_runbuoy(tmp_path)


def test_runbuoy_uploads_only_two_result_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    plan = validate_run_for_publication(project_root, run_directory)
    result_directory = promote_run_result(plan)
    assets = prepare_release_assets(
        plan,
        result_directory,
        tmp_path / "assets",
    )
    observed: list[list[str]] = []

    def fake_run_command(
        args: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == project_root
        assert check is False
        observed.append(list(args))
        payload = {
            "ok": True,
            "run_id": "019c-structured-upload",
            "result": {"exit_code": 0, "status": "SUCCEEDED"},
        }
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(publishing, "_run_command", fake_run_command)
    run_id = publishing._runbuoy_upload(
        plan,
        assets,
        repository="owner/repository",
    )

    assert run_id == "019c-structured-upload"
    command = observed[0]
    assert command[command.index("--progress") + 1] == "structured"
    assert "--share-log-tail" not in command
    assert command.count("--asset") == 2
    assert str(assets.result_archive) in command
    assert not any("full.tar.gz" in argument for argument in command)


def test_asset_uploader_reports_two_completed_file_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress: list[tuple[int, int, str]] = []
    uploads: list[list[str]] = []

    def fake_emit(
        *,
        current: int,
        total: int,
        message: str,
    ) -> None:
        progress.append((current, total, message))

    def fake_run_command(
        args: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path
        assert check is True
        uploads.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(publishing, "_emit_upload_progress", fake_emit)
    monkeypatch.setattr(publishing, "_run_command", fake_run_command)
    assets = tuple(tmp_path / f"asset-{index}" for index in range(2))
    publishing._upload_release_assets(
        project_root=tmp_path,
        release_tag="exp-e99",
        asset_paths=assets,
        repository=None,
    )

    assert [item[0] for item in progress] == [0, 0, 1, 1, 2]
    assert all(item[1] == 2 for item in progress)
    assert len(uploads) == 2


def test_package_only_cli_promotes_results_without_release(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root, run_directory, _commit = _completed_run(tmp_path)
    output_directory = tmp_path / "package-only"

    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "publish-run",
            str(run_directory),
            "--package-only",
            "--output-dir",
            str(output_directory),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "promoted result:" in output
    assert "full=" not in output
    assert len(tuple(output_directory.glob("*-results.zip"))) == 1
    assert not tuple(output_directory.glob("*-full.tar.gz"))
    assert (output_directory / "SHA256SUMS").is_file()
    with (project_root / "experiments/E99_publication_fixture/results/index.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["release_state"] == "unpublished"
