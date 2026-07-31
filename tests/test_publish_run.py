from __future__ import annotations

import csv
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import otg_lab.publishing as publishing
from otg_lab.cli import main
from otg_lab.publishing import (
    GitHubRelease,
    PublicationPlan,
    ReleaseAssets,
    prepare_release_assets,
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


def _selected_result(tmp_path: Path) -> tuple[Path, Path, str]:
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
    result_directory = (
        project_root
        / "experiments"
        / "E99_publication_fixture"
        / "results"
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
        path = result_directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    outputs = {
        relative_path: sha256_file(result_directory / relative_path)
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
    (result_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return project_root, result_directory, commit


def _selected_analysis_result(tmp_path: Path) -> tuple[Path, Path, str]:
    project_root = tmp_path / "analysis-project"
    project_root.mkdir()
    _git(project_root, "init")
    (project_root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(project_root, "add", "tracked.txt")
    commit = _commit(project_root, "analysis fixture")

    analysis_directory = project_root / "analyses/A01_fixture"
    results_directory = analysis_directory / "results"
    run_id = "20260730T120000.000000Z__cccccccccccc"
    result_directory = results_directory / run_id
    result_directory.mkdir(parents=True)
    (analysis_directory / "RESULTS.md").write_text(
        "# A01 fixture results\n\nConclusion.\n",
        encoding="utf-8",
    )
    (result_directory / "RESULTS.md").write_text(
        "# A01 fixture results\n\nConclusion.\n",
        encoding="utf-8",
    )
    (result_directory / "table.csv").write_text(
        "metric,value\nrmse,1\n",
        encoding="utf-8",
    )
    (results_directory / ".gitkeep").write_text("\n", encoding="utf-8")
    (results_directory / "index.csv").write_text(
        ",".join(publishing._INDEX_COLUMNS) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "otg.cross_analysis.result_manifest.v2",
        "status": "completed",
        "analysis_id": "A01",
        "run_id": run_id,
        "analysis_spec_hash": "c" * 64,
        "config_path": "analyses/A01_fixture/analysis.yaml",
        "config_sha256": "c" * 64,
        "git": {"commit": commit, "dirty": False},
        "environment": {},
        "sources": [],
        "input_artifacts": [],
        "outputs": [
            {
                "path": "RESULTS.md",
                "sha256": "d" * 64,
                "size_bytes": 35,
            },
            {
                "path": "table.csv",
                "sha256": sha256_file(result_directory / "table.csv"),
                "size_bytes": (result_directory / "table.csv").stat().st_size,
            },
        ],
    }
    (result_directory / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return project_root, result_directory, commit


def test_packages_manually_selected_result_deterministically(
    tmp_path: Path,
) -> None:
    project_root, result_directory, commit = _selected_result(tmp_path)
    plan = validate_run_for_publication(project_root, result_directory)

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
        / result_directory.name
    )
    assert (result_directory / "manifest.json").is_file()
    assert (result_directory / "analysis/trajectory_metrics.csv").is_file()
    assert (result_directory / "methods/method/input/trace.csv").is_file()
    assert first.result_archive_sha256 == second.result_archive_sha256
    assert first.result_file_count == 7

    archive_root = (
        f"experiments/E99_publication_fixture/results/{result_directory.name}"
    )
    with zipfile.ZipFile(first.result_archive) as archive:
        names = set(archive.namelist())
    assert f"{archive_root}/manifest.json" in names
    assert f"{archive_root}/analysis/trajectory_metrics.csv" in names
    assert f"{archive_root}/methods/method/input/trace.csv" in names
    assert first.checksums_file.read_text(encoding="utf-8") == (
        f"{first.result_archive_sha256}  {first.result_archive.name}\n"
    )


def test_packages_analysis_results_with_conclusion_and_without_index(
    tmp_path: Path,
) -> None:
    project_root, result_directory, commit = _selected_analysis_result(tmp_path)
    plan = publishing.validate_analysis_for_publication(
        project_root,
        result_directory,
    )
    assets = prepare_release_assets(
        plan,
        result_directory,
        tmp_path / "analysis-assets",
    )

    assert plan.artifact_kind == "analysis"
    assert plan.experiment_id == "A01"
    assert plan.git_commit == commit
    assert plan.run_id == result_directory.name
    with zipfile.ZipFile(assets.result_archive) as archive:
        names = set(archive.namelist())
    archive_root = f"analyses/A01_fixture/results/{result_directory.name}"
    assert f"{archive_root}/RESULTS.md" in names
    assert f"{archive_root}/table.csv" in names
    assert f"{archive_root}/analysis_manifest.json" in names
    assert "analyses/A01_fixture/results/index.csv" not in names
    assert "analyses/A01_fixture/results/.gitkeep" not in names


def test_validation_allows_dirty_current_worktree(tmp_path: Path) -> None:
    project_root, result_directory, _commit = _selected_result(tmp_path)
    (project_root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    plan = validate_run_for_publication(project_root, result_directory)

    assert plan.result_directory == result_directory


def test_validation_allows_run_recorded_from_dirty_worktree(
    tmp_path: Path,
) -> None:
    project_root, result_directory, _commit = _selected_result(tmp_path)
    manifest_path = result_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["git"]["dirty"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = validate_run_for_publication(project_root, result_directory)

    assert plan.manifest["git"]["dirty"] is True


def test_validation_allows_changed_declared_output(
    tmp_path: Path,
) -> None:
    project_root, result_directory, _commit = _selected_result(tmp_path)
    (result_directory / "analysis/report.md").write_text(
        "changed\n",
        encoding="utf-8",
    )

    plan = validate_run_for_publication(project_root, result_directory)
    assets = prepare_release_assets(plan, result_directory, tmp_path / "assets")

    with zipfile.ZipFile(assets.result_archive) as archive:
        archived = archive.read(
            "experiments/E99_publication_fixture/"
            f"results/{result_directory.name}/analysis/report.md"
        )
    assert archived == b"changed\n"


def test_validation_rejects_result_still_under_runs(tmp_path: Path) -> None:
    project_root, result_directory, _commit = _selected_result(tmp_path)
    runs_directory = result_directory.parent.parent / "runs" / result_directory.name
    runs_directory.parent.mkdir()
    result_directory.rename(runs_directory)

    with pytest.raises(ValueError, match="results/<run-id>"):
        validate_run_for_publication(project_root, runs_directory)


def test_asset_preparation_removes_partial_files_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, result_directory, _commit = _selected_result(tmp_path)
    plan = validate_run_for_publication(project_root, result_directory)
    output_directory = tmp_path / "broken-assets"

    def fail_archive(
        _plan: PublicationPlan,
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


def test_publish_run_records_selected_release_after_remote_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, result_directory, _commit = _selected_result(tmp_path)
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
        monitor_upload: bool,
    ) -> GitHubRelease:
        assert repository == "owner/repository"
        assert draft is False
        assert monitor_upload is True
        return release

    monkeypatch.setattr(
        "otg_lab.publishing._create_github_release",
        fake_create,
    )
    result = publish_run(
        project_root,
        result_directory,
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
    project_root, result_directory, _commit = _selected_result(tmp_path)
    plan = validate_run_for_publication(project_root, result_directory)
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


def test_package_only_cli_packages_selected_result_without_release(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root, result_directory, _commit = _selected_result(tmp_path)
    output_directory = tmp_path / "package-only"

    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "publish-run",
            str(result_directory),
            "--package-only",
            "--output-dir",
            str(output_directory),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "selected E99:" in output
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


def test_discovers_only_unpublished_result_directories(tmp_path: Path) -> None:
    project_root, result_directory, commit = _selected_result(tmp_path)
    ignored = result_directory.parent / "notes"
    ignored.mkdir()

    assert publishing.discover_result_directories(project_root) == (result_directory,)

    publishing._write_index(
        result_directory.parent / "index.csv",
        (
            {
                "experiment_id": "E99",
                "run_id": result_directory.name,
                "spec_hash": "a" * 64,
                "git_commit": commit,
                "release_tag": "exp-e99",
                "release_url": "https://github.example/releases/e99",
                "release_state": "published",
                "result_directory": result_directory.relative_to(
                    project_root
                ).as_posix(),
                "result_archive_sha256": "b" * 64,
                "published_at": "2026-07-30T12:00:00Z",
            },
        ),
    )

    assert publishing.discover_result_directories(project_root) == ()


def test_discovers_analysis_result_and_skips_published_snapshot(
    tmp_path: Path,
) -> None:
    project_root, result_directory, _commit = _selected_analysis_result(tmp_path)
    plan = publishing.validate_analysis_for_publication(
        project_root,
        result_directory,
    )

    assert publishing.discover_result_directories(project_root) == (result_directory,)

    publishing._write_index(
        result_directory.parent / "index.csv",
        (
            {
                "experiment_id": plan.experiment_id,
                "run_id": plan.run_id,
                "spec_hash": plan.spec_hash,
                "git_commit": plan.git_commit,
                "release_tag": plan.release_tag,
                "release_url": "https://github.example/releases/a01",
                "release_state": "published",
                "result_directory": result_directory.relative_to(
                    project_root
                ).as_posix(),
                "result_archive_sha256": "e" * 64,
                "published_at": "2026-07-30T12:00:00Z",
            },
        ),
    )

    assert publishing.discover_result_directories(project_root) == ()


def test_batch_worker_continues_after_failure_and_emits_result_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = (
        tmp_path / "experiments/E01_one/results/result-one",
        tmp_path / "experiments/E02_two/results/result-two",
    )
    attempts: list[Path] = []
    progress: list[tuple[int, int, str]] = []

    monkeypatch.setattr(
        publishing,
        "discover_result_directories",
        lambda _root: results,
    )

    def fake_publish(
        _root: Path,
        result_directory: Path,
        *,
        repository: str | None,
        draft: bool,
        monitor_upload: bool,
    ) -> SimpleNamespace:
        assert repository == "owner/repository"
        assert draft is False
        assert monitor_upload is False
        attempts.append(result_directory)
        if result_directory == results[1]:
            raise RuntimeError("upload failed")
        return SimpleNamespace(
            release=SimpleNamespace(url="https://github.example/releases/e01")
        )

    def fake_emit(*, current: int, total: int, message: str) -> None:
        progress.append((current, total, message))

    monkeypatch.setattr(publishing, "publish_result", fake_publish)
    monkeypatch.setattr(publishing, "_emit_batch_progress", fake_emit)

    exit_code = publishing._publish_all_results_worker(
        tmp_path,
        repository="owner/repository",
        draft=False,
    )

    assert exit_code == 2
    assert attempts == list(results)
    assert [item[0] for item in progress] == [0, 0, 1, 1, 2]
    assert all(item[1] == 2 for item in progress)
    assert all("/" not in item[2] for item in progress)


def test_batch_publish_starts_one_safe_structured_runbuoy_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = (
        tmp_path / "experiments/E01_one/results/result-one",
        tmp_path / "experiments/E02_two/results/result-two",
    )
    observed: list[str] = []
    monkeypatch.setattr(
        publishing,
        "discover_result_directories",
        lambda _root: results,
    )
    monkeypatch.setattr(publishing, "_require_runbuoy", lambda _root: None)

    def fake_run_command(
        args: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path
        assert check is False
        observed.extend(args)
        payload = {
            "ok": True,
            "run_id": "019c-batch-publish",
            "result": {"exit_code": 0, "status": "SUCCEEDED"},
        }
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(publishing, "_run_command", fake_run_command)
    result = publishing.publish_all_results(
        tmp_path,
        repository="owner/repository",
        draft=True,
    )

    assert result.runbuoy_run_id == "019c-batch-publish"
    assert result.result_count == 2
    assert result.exit_code == 0
    assert observed[observed.index("--progress") + 1] == "structured"
    assert observed[observed.index("--title") + 1] == (
        "GitHub experiment batch publish"
    )
    assert "--share-log-tail" not in observed
    assert "publish-results-worker" in observed
    assert "--draft" in observed


def test_publish_results_cli_prints_local_runbuoy_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "otg_lab.cli.publish_all_results",
        lambda *_args, **_kwargs: publishing.BatchPublicationResult(
            runbuoy_run_id="019c-batch-publish",
            result_count=3,
            exit_code=0,
        ),
    )

    exit_code = main(
        [
            "--project-root",
            str(tmp_path),
            "publish-results",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "runbuoy status 019c-batch-publish" in output
    assert "runbuoy logs 019c-batch-publish" in output
    assert "runbuoy attach 019c-batch-publish" in output
