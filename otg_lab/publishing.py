"""Package manually selected experiment results and publish them."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runio import sha256_file

_INDEX_COLUMNS = (
    "experiment_id",
    "run_id",
    "spec_hash",
    "git_commit",
    "release_tag",
    "release_url",
    "release_state",
    "result_directory",
    "result_archive_sha256",
    "published_at",
)
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class PublicationPlan:
    """A selected result and its proposed GitHub Release metadata."""

    project_root: Path
    experiment_directory: Path
    result_directory: Path
    run_id: str
    experiment_id: str
    experiment_title: str
    spec_hash: str
    git_commit: str
    release_tag: str
    release_title: str
    manifest: Mapping[str, Any]
    declared_output_count: int


@dataclass(frozen=True)
class ReleaseAssets:
    """A selected result directory and the assets derived only from it."""

    result_directory: Path
    result_archive: Path
    checksums_file: Path
    result_archive_sha256: str
    result_file_count: int


@dataclass(frozen=True)
class GitHubRelease:
    """The remote release created by ``gh``."""

    url: str
    state: str
    published_at: str
    runbuoy_run_id: str


@dataclass(frozen=True)
class PublicationResult:
    """Result returned after packaging or publishing a selected result."""

    plan: PublicationPlan
    assets: ReleaseAssets
    release: GitHubRelease | None
    result_directory: Path


@dataclass(frozen=True)
class BatchPublicationResult:
    """RunBuoy-monitored outcome for a batch of selected results."""

    runbuoy_run_id: str
    result_count: int
    exit_code: int


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(args),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeError(f"could not run {args[0]!r}: {error}") from error
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"{' '.join(args)} failed{suffix}")
    return result


def _git(project_root: Path, *args: str) -> str:
    return _run_command(
        ("git", *args),
        cwd=project_root,
    ).stdout.strip()


def _load_manifest(result_directory: Path) -> dict[str, Any]:
    path = result_directory / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"run manifest was not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid run manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("manifest.json must contain an object")
    return value


def _default_release_tag(
    experiment_id: str,
    run_id: str,
    spec_hash: str,
) -> str:
    stamp = run_id.split("__", 1)[0].split(".", 1)[0].lower()
    return f"exp-{experiment_id.lower()}-{stamp}-{spec_hash[:12]}"


def _validate_release_tag(project_root: Path, tag: str) -> None:
    if not tag or tag.startswith("-"):
        raise ValueError(f"invalid release tag: {tag!r}")
    result = _run_command(
        ("git", "check-ref-format", f"refs/tags/{tag}"),
        cwd=project_root,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"invalid release tag: {tag!r}")


def validate_run_for_publication(
    project_root: str | Path,
    result_directory: str | Path,
    *,
    release_tag: str | None = None,
    release_title: str | None = None,
) -> PublicationPlan:
    """Recognize one manually selected ``Exx/results/<run-id>`` directory."""

    root = Path(project_root).resolve()
    result = Path(result_directory)
    if not result.is_absolute():
        result = root / result
    result = result.resolve()
    if not result.is_dir():
        raise FileNotFoundError(f"result directory was not found: {result}")

    repository_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if repository_root != root:
        raise ValueError(
            f"project root {root} is not the Git repository root {repository_root}"
        )

    manifest = _load_manifest(result)
    if manifest.get("schema_version") != "otg.run_manifest.v1":
        raise ValueError("unsupported or missing run manifest schema_version")

    git_state = manifest.get("git")
    recorded_commit = (
        git_state.get("commit") if isinstance(git_state, Mapping) else None
    )
    manifest_commit = (
        recorded_commit
        if isinstance(recorded_commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", recorded_commit)
        else _git(root, "rev-parse", "HEAD")
    )

    spec_hash = manifest.get("spec_hash")
    if not isinstance(spec_hash, str) or not _SHA256.fullmatch(spec_hash):
        raise ValueError("manifest spec_hash is missing or invalid")

    resolved_spec = manifest.get("resolved_experiment_spec")
    if not isinstance(resolved_spec, Mapping):
        raise ValueError("manifest resolved_experiment_spec is missing")
    experiment_id = resolved_spec.get("experiment_id")
    if not isinstance(experiment_id, str) or not _SAFE_IDENTIFIER.fullmatch(
        experiment_id
    ):
        raise ValueError("manifest experiment_id is missing or unsafe")
    experiment_title = resolved_spec.get("title")
    if not isinstance(experiment_title, str) or not experiment_title.strip():
        experiment_title = experiment_id
    if not _SAFE_IDENTIFIER.fullmatch(result.name):
        raise ValueError(f"unsafe result directory name: {result.name!r}")
    experiment_directory = result.parent.parent
    expected_experiments_root = root / "experiments"
    if (
        result.parent.name != "results"
        or experiment_directory.parent != expected_experiments_root
        or not experiment_directory.name.startswith(f"{experiment_id}_")
    ):
        raise ValueError(
            "result must be located at "
            "experiments/<experiment-directory>/results/<run-id>"
        )

    outputs = manifest.get("outputs")
    declared_output_count = len(outputs) if isinstance(outputs, Mapping) else 0
    if not _directory_files(result):
        raise ValueError(f"result directory is empty: {result}")

    tag = release_tag or _default_release_tag(
        experiment_id,
        result.name,
        spec_hash,
    )
    _validate_release_tag(root, tag)
    title = release_title or (f"{experiment_id} experiment result {spec_hash[:12]}")
    return PublicationPlan(
        project_root=root,
        experiment_directory=experiment_directory,
        result_directory=result,
        run_id=result.name,
        experiment_id=experiment_id,
        experiment_title=experiment_title.strip(),
        spec_hash=spec_hash,
        git_commit=manifest_commit,
        release_tag=tag,
        release_title=title,
        manifest=manifest,
        declared_output_count=declared_output_count,
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _directory_files(directory: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(
                f"result directories must not contain symbolic links: {path}"
            )
        if path.is_file() and path.name != ".DS_Store":
            files.append(path)
    return tuple(files)


def _result_directory(plan: PublicationPlan) -> Path:
    return plan.result_directory


def _write_result_archive(
    result_directory: Path,
    project_root: Path,
    destination: Path,
) -> None:
    archive_prefix = result_directory.relative_to(project_root).as_posix()
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for path in _directory_files(result_directory):
            relative = path.relative_to(result_directory).as_posix()
            archive.writestr(
                _zip_info(f"{archive_prefix}/{relative}"),
                path.read_bytes(),
                compresslevel=6,
            )


def prepare_release_assets(
    plan: PublicationPlan,
    result_directory: str | Path,
    output_directory: str | Path,
) -> ReleaseAssets:
    """Create a deterministic archive from one manually selected result."""

    result = Path(result_directory).resolve()
    expected_result = _result_directory(plan).resolve()
    if result != expected_result or not result.is_dir():
        raise ValueError(
            f"result directory must match the selected result {expected_result}"
        )
    output = Path(output_directory).resolve()
    try:
        output.relative_to(result)
    except ValueError:
        pass
    else:
        raise ValueError(
            "release output directory must be outside the result directory"
        )
    output.mkdir(parents=True, exist_ok=True)

    asset_stem = f"{plan.experiment_id}-{plan.run_id}"
    result_archive = output / f"{asset_stem}-results.zip"
    checksums_file = output / "SHA256SUMS"
    targets = (result_archive, checksums_file)
    existing = [path for path in targets if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite release assets: "
            + ", ".join(str(path) for path in existing)
        )

    try:
        _write_result_archive(
            result,
            plan.project_root,
            result_archive,
        )
        result_hash = sha256_file(result_archive)
        checksums_file.write_text(
            f"{result_hash}  {result_archive.name}\n",
            encoding="utf-8",
        )
    except Exception:
        for path in targets:
            path.unlink(missing_ok=True)
        raise

    return ReleaseAssets(
        result_directory=result,
        result_archive=result_archive,
        checksums_file=checksums_file,
        result_archive_sha256=result_hash,
        result_file_count=len(_directory_files(result)),
    )


def _acceptance_summary(plan: PublicationPlan) -> str:
    path = plan.result_directory / "analysis" / "acceptance_summary.md"
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf-8").strip()
    if len(content) > 20_000:
        return content[:20_000].rstrip() + "\n\n[Summary truncated.]"
    return content


def _release_notes(plan: PublicationPlan, assets: ReleaseAssets) -> str:
    manifest = plan.manifest
    lines = [
        f"# {plan.experiment_title}",
        "",
        "## Provenance",
        "",
        f"- Run: `{plan.run_id}`",
        f"- Spec SHA-256: `{plan.spec_hash}`",
        f"- Git commit: `{plan.git_commit}`",
        f"- Declared outputs in manifest: `{plan.declared_output_count}`",
        f"- Files in selected result: `{assets.result_file_count}`",
        f"- Failure count: `{manifest.get('failure_count', 0)}`",
        "",
        "## Assets",
        "",
        f"- `{assets.result_archive.name}`: selected `results/<run-id>/` directory",
        "- `SHA256SUMS`: result archive integrity hash",
    ]
    acceptance = _acceptance_summary(plan)
    if acceptance:
        lines.extend(["", "## Experiment summary", "", acceptance])
    lines.append("")
    return "\n".join(lines)


def _json_command(
    args: Sequence[str],
    *,
    cwd: Path,
) -> Mapping[str, Any]:
    result = _run_command(args, cwd=cwd)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{args[0]} returned invalid JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{args[0]} returned a non-object JSON response")
    return value


def _require_runbuoy(project_root: Path) -> None:
    if shutil.which("runbuoy") is None:
        raise RuntimeError("RunBuoy CLI is required to monitor Release asset uploads")
    doctor_result = _run_command(
        ("runbuoy", "doctor", "--json"),
        cwd=project_root,
        check=False,
    )
    try:
        doctor = json.loads(doctor_result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"RunBuoy doctor returned invalid JSON: {error}") from error
    if not isinstance(doctor, Mapping):
        raise RuntimeError("RunBuoy doctor returned a non-object JSON response")
    if doctor.get("ready") is not True:
        checks = doctor.get("checks")
        if isinstance(checks, Mapping) and checks.get("server_reachable") is False:
            raise RuntimeError(
                "RunBuoy server is unreachable; run `runbuoy doctor --json` for details"
            )
        failed_checks = (
            sorted(name for name, passed in checks.items() if passed is False)
            if isinstance(checks, Mapping)
            else []
        )
        detail = f": {', '.join(failed_checks)}" if failed_checks else ""
        raise RuntimeError(
            f"RunBuoy is not ready{detail}; run `runbuoy doctor --json` for details"
        )
    capabilities = _json_command(
        ("runbuoy", "capabilities", "--json"),
        cwd=project_root,
    )
    progress_modes = capabilities.get("progress_modes")
    if not isinstance(progress_modes, list) or "structured" not in progress_modes:
        raise RuntimeError("installed RunBuoy does not support structured progress")


def _emit_upload_progress(
    *,
    current: int,
    total: int,
    message: str,
) -> None:
    _run_command(
        (
            "runbuoy",
            "emit",
            "progress",
            "--current",
            str(current),
            "--total",
            str(total),
            "--unit",
            "files",
            "--phase",
            "Uploading GitHub assets",
            "--message",
            message,
        ),
        cwd=Path.cwd(),
    )


def _emit_batch_progress(
    *,
    current: int,
    total: int,
    message: str,
) -> None:
    _run_command(
        (
            "runbuoy",
            "emit",
            "progress",
            "--current",
            str(current),
            "--total",
            str(total),
            "--unit",
            "results",
            "--phase",
            "Publishing experiment results",
            "--message",
            message,
        ),
        cwd=Path.cwd(),
    )


def _upload_release_assets(
    *,
    project_root: Path,
    release_tag: str,
    asset_paths: Sequence[Path],
    repository: str | None,
    emit_progress: bool = True,
) -> None:
    total = len(asset_paths)
    if total <= 0:
        raise ValueError("at least one release asset is required")
    if emit_progress:
        _emit_upload_progress(
            current=0,
            total=total,
            message=f"Preparing {total} release assets",
        )
    repo_args = ("--repo", repository) if repository else ()
    for index, path in enumerate(asset_paths, start=1):
        if emit_progress:
            _emit_upload_progress(
                current=index - 1,
                total=total,
                message=f"Uploading asset {index} of {total}",
            )
        _run_command(
            (
                "gh",
                "release",
                "upload",
                release_tag,
                str(path),
                *repo_args,
            ),
            cwd=project_root,
        )
        if emit_progress:
            _emit_upload_progress(
                current=index,
                total=total,
                message=f"Uploaded asset {index} of {total}",
            )
        print(f"uploaded asset {index}/{total}", flush=True)


def _runbuoy_upload(
    plan: PublicationPlan,
    assets: ReleaseAssets,
    *,
    repository: str | None,
) -> str:
    command = [
        "runbuoy",
        "run",
        "--json",
        "--non-interactive",
        "--wait",
        "--title",
        "GitHub experiment asset upload",
        "--progress",
        "structured",
        "--",
        sys.executable,
        "-m",
        "otg_lab.publishing",
        "upload-assets",
        "--project-root",
        str(plan.project_root),
        "--tag",
        plan.release_tag,
        "--asset",
        str(assets.result_archive),
        "--asset",
        str(assets.checksums_file),
    ]
    if repository:
        command.extend(("--repo", repository))
    completed = _run_command(
        command,
        cwd=plan.project_root,
        check=False,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"RunBuoy returned invalid JSON: {error}") from error
    if not isinstance(result, Mapping):
        raise RuntimeError("RunBuoy returned a non-object JSON response")
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("RunBuoy did not return an upload Run ID")
    run_result = result.get("result")
    if (
        result.get("ok") is not True
        or not isinstance(run_result, Mapping)
        or run_result.get("exit_code") != 0
    ):
        raise RuntimeError(
            "RunBuoy-monitored Release asset upload failed; "
            f"inspect it with `runbuoy status {run_id}`"
        )
    return run_id


def _create_github_release(
    plan: PublicationPlan,
    assets: ReleaseAssets,
    *,
    repository: str | None,
    draft: bool,
    monitor_upload: bool = True,
) -> GitHubRelease:
    if shutil.which("gh") is None:
        raise RuntimeError("GitHub CLI 'gh' is required to create a GitHub Release")
    if monitor_upload:
        _require_runbuoy(plan.project_root)
    repo_args = ("--repo", repository) if repository else ()
    _run_command(
        ("gh", "auth", "status"),
        cwd=plan.project_root,
    )
    existing = _run_command(
        (
            "gh",
            "release",
            "view",
            plan.release_tag,
            "--json",
            "url",
            *repo_args,
        ),
        cwd=plan.project_root,
        check=False,
    )
    if existing.returncode == 0:
        raise FileExistsError(
            f"GitHub Release already exists for tag {plan.release_tag!r}"
        )

    notes_path = assets.checksums_file.parent / "RELEASE_NOTES.md"
    notes_path.write_text(
        _release_notes(plan, assets),
        encoding="utf-8",
    )
    command = [
        "gh",
        "release",
        "create",
        plan.release_tag,
        "--target",
        plan.git_commit,
        "--title",
        plan.release_title,
        "--notes-file",
        str(notes_path),
        "--latest=false",
        "--draft",
        *repo_args,
    ]
    _run_command(command, cwd=plan.project_root)
    if monitor_upload:
        runbuoy_run_id = _runbuoy_upload(
            plan,
            assets,
            repository=repository,
        )
    else:
        _upload_release_assets(
            project_root=plan.project_root,
            release_tag=plan.release_tag,
            asset_paths=(assets.result_archive, assets.checksums_file),
            repository=repository,
            emit_progress=False,
        )
        runbuoy_run_id = ""
    if not draft:
        _run_command(
            (
                "gh",
                "release",
                "edit",
                plan.release_tag,
                "--draft=false",
                "--latest=false",
                *repo_args,
            ),
            cwd=plan.project_root,
        )

    viewed = _run_command(
        (
            "gh",
            "release",
            "view",
            plan.release_tag,
            "--json",
            "url,createdAt,publishedAt,isDraft",
            *repo_args,
        ),
        cwd=plan.project_root,
        check=False,
    )
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if viewed.returncode == 0:
        try:
            metadata = json.loads(viewed.stdout)
        except json.JSONDecodeError:
            metadata = {}
    else:
        metadata = {}
    url = metadata.get("url")
    if not isinstance(url, str) or not url:
        raise RuntimeError(
            "GitHub Release was created, but its URL could not be determined"
        )
    is_draft = metadata.get("isDraft", draft)
    timestamp = metadata.get("publishedAt") or metadata.get("createdAt") or now
    return GitHubRelease(
        url=url,
        state="draft" if is_draft else "published",
        published_at=str(timestamp),
        runbuoy_run_id=runbuoy_run_id,
    )


def _read_index(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _INDEX_COLUMNS:
            raise ValueError(
                f"unexpected results index columns in {path}: {reader.fieldnames}"
            )
        return [dict(row) for row in reader]


def _write_index(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_INDEX_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _upsert_result_record(
    plan: PublicationPlan,
    assets: ReleaseAssets,
    release: GitHubRelease | None,
) -> None:
    index_path = plan.experiment_directory / "results" / "index.csv"
    rows = _read_index(index_path)
    record = {
        "experiment_id": plan.experiment_id,
        "run_id": plan.run_id,
        "spec_hash": plan.spec_hash,
        "git_commit": plan.git_commit,
        "release_tag": plan.release_tag,
        "release_url": "" if release is None else release.url,
        "release_state": "unpublished" if release is None else release.state,
        "result_directory": assets.result_directory.relative_to(
            plan.project_root
        ).as_posix(),
        "result_archive_sha256": assets.result_archive_sha256,
        "published_at": "" if release is None else release.published_at,
    }
    matching_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row["experiment_id"] == plan.experiment_id
            and row["run_id"] == plan.run_id
        ),
        None,
    )
    duplicate_tag = next(
        (
            row
            for row in rows
            if row["release_tag"] == plan.release_tag
            and (
                row["experiment_id"] != plan.experiment_id
                or row["run_id"] != plan.run_id
            )
        ),
        None,
    )
    if duplicate_tag is not None:
        raise FileExistsError(
            f"results index already uses release tag {plan.release_tag!r}"
        )
    if matching_index is None:
        rows.append(record)
    else:
        existing = rows[matching_index]
        immutable_fields = (
            "spec_hash",
            "git_commit",
            "release_tag",
            "result_directory",
            "result_archive_sha256",
        )
        mismatches = [
            field for field in immutable_fields if existing[field] != record[field]
        ]
        if mismatches:
            raise ValueError(
                "existing result index entry conflicts in fields: "
                + ", ".join(mismatches)
            )
        rows[matching_index] = record
    rows.sort(
        key=lambda row: (
            row["experiment_id"],
            row["run_id"],
        )
    )
    _write_index(index_path, rows)


def discover_result_directories(
    project_root: str | Path,
) -> tuple[Path, ...]:
    """Return manually selected results not already published or held as drafts."""

    root = Path(project_root).resolve()
    experiments_root = root / "experiments"
    if not experiments_root.is_dir():
        return ()

    selected: list[Path] = []
    for experiment_directory in sorted(experiments_root.glob("E*")):
        if not experiment_directory.is_dir():
            continue
        results_directory = experiment_directory / "results"
        if not results_directory.is_dir():
            continue
        indexed = _read_index(results_directory / "index.csv")
        already_released = {
            row["run_id"]
            for row in indexed
            if row["release_state"] in {"published", "draft"}
        }
        for candidate in sorted(results_directory.iterdir()):
            if (
                candidate.is_dir()
                and candidate.name not in already_released
                and (candidate / "manifest.json").is_file()
            ):
                selected.append(candidate.resolve())
    return tuple(selected)


def publish_run(
    project_root: str | Path,
    result_directory: str | Path,
    *,
    repository: str | None = None,
    release_tag: str | None = None,
    release_title: str | None = None,
    draft: bool = False,
    package_only: bool = False,
    output_directory: str | Path | None = None,
    monitor_upload: bool = True,
) -> PublicationResult:
    """Package one manually selected result and optionally publish it."""

    if package_only and output_directory is None:
        raise ValueError("--package-only requires --output-dir")
    plan = validate_run_for_publication(
        project_root,
        result_directory,
        release_tag=release_tag,
        release_title=release_title,
    )

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if output_directory is None:
        temporary = tempfile.TemporaryDirectory(prefix="otg-lab-publish-")
        asset_directory = Path(temporary.name)
    else:
        asset_directory = Path(output_directory)
        if not asset_directory.is_absolute():
            asset_directory = plan.project_root / asset_directory
    try:
        assets = prepare_release_assets(
            plan,
            plan.result_directory,
            asset_directory,
        )
        _upsert_result_record(plan, assets, None)
        if package_only:
            return PublicationResult(
                plan=plan,
                assets=assets,
                release=None,
                result_directory=plan.result_directory,
            )
        release = _create_github_release(
            plan,
            assets,
            repository=repository,
            draft=draft,
            monitor_upload=monitor_upload,
        )
        _upsert_result_record(plan, assets, release)
        return PublicationResult(
            plan=plan,
            assets=assets,
            release=release,
            result_directory=plan.result_directory,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()


def _publish_all_results_worker(
    project_root: str | Path,
    *,
    repository: str | None,
    draft: bool,
) -> int:
    """Publish every selected result while emitting honest batch progress."""

    root = Path(project_root).resolve()
    results = discover_result_directories(root)
    total = len(results)
    if total == 0:
        print("no unpublished experiment results found")
        return 0

    _emit_batch_progress(
        current=0,
        total=total,
        message=f"Preparing {total} selected results",
    )
    failures = 0
    for index, result_directory in enumerate(results, start=1):
        _emit_batch_progress(
            current=index - 1,
            total=total,
            message=f"Publishing result {index} of {total}",
        )
        try:
            result = publish_run(
                root,
                result_directory,
                repository=repository,
                draft=draft,
                monitor_upload=False,
            )
        except Exception as error:
            failures += 1
            print(
                f"failed {result_directory}: {type(error).__name__}: {error}",
                file=sys.stderr,
                flush=True,
            )
        else:
            release_url = result.release.url if result.release is not None else ""
            print(
                f"published {index}/{total}: {result_directory} {release_url}",
                flush=True,
            )
        _emit_batch_progress(
            current=index,
            total=total,
            message=f"Processed result {index} of {total}",
        )
    if failures:
        print(f"batch completed with {failures} failure(s)", file=sys.stderr)
        return 2
    print(f"batch published {total} result(s)")
    return 0


def publish_all_results(
    project_root: str | Path,
    *,
    repository: str | None = None,
    draft: bool = False,
) -> BatchPublicationResult:
    """Publish every selected result under one RunBuoy structured-progress run."""

    root = Path(project_root).resolve()
    results = discover_result_directories(root)
    if not results:
        raise ValueError(
            "no unpublished results found under experiments/E*/results/<run-id>"
        )
    _require_runbuoy(root)
    command = [
        "runbuoy",
        "run",
        "--json",
        "--non-interactive",
        "--wait",
        "--title",
        "GitHub experiment batch publish",
        "--progress",
        "structured",
        "--",
        sys.executable,
        "-m",
        "otg_lab.publishing",
        "publish-results-worker",
        "--project-root",
        str(root),
    ]
    if repository:
        command.extend(("--repo", repository))
    if draft:
        command.append("--draft")
    completed = _run_command(
        command,
        cwd=root,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"RunBuoy returned invalid JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise RuntimeError("RunBuoy returned a non-object JSON response")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("RunBuoy did not return a batch Run ID")
    run_result = payload.get("result")
    exit_code = (
        run_result.get("exit_code")
        if isinstance(run_result, Mapping)
        else completed.returncode
    )
    if not isinstance(exit_code, int):
        exit_code = completed.returncode
    return BatchPublicationResult(
        runbuoy_run_id=run_id,
        result_count=len(results),
        exit_code=exit_code,
    )


__all__ = [
    "BatchPublicationResult",
    "GitHubRelease",
    "PublicationPlan",
    "PublicationResult",
    "ReleaseAssets",
    "discover_result_directories",
    "prepare_release_assets",
    "publish_all_results",
    "publish_run",
    "validate_run_for_publication",
]


def _publishing_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m otg_lab.publishing",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload-assets")
    upload.add_argument("--project-root", required=True)
    upload.add_argument("--tag", required=True)
    upload.add_argument("--asset", action="append", required=True)
    upload.add_argument("--repo", default=None)
    batch = commands.add_parser("publish-results-worker")
    batch.add_argument("--project-root", required=True)
    batch.add_argument("--repo", default=None)
    batch.add_argument("--draft", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "upload-assets":
        try:
            _upload_release_assets(
                project_root=Path(args.project_root).resolve(),
                release_tag=args.tag,
                asset_paths=tuple(Path(path).resolve() for path in args.asset),
                repository=args.repo,
            )
        except Exception as error:
            print(
                f"asset upload failed: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            return 2
        return 0
    if args.command == "publish-results-worker":
        try:
            return _publish_all_results_worker(
                Path(args.project_root).resolve(),
                repository=args.repo,
                draft=args.draft,
            )
        except Exception as error:
            print(
                f"batch publish failed: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            return 2
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_publishing_cli())
