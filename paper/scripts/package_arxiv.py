#!/usr/bin/env python3
"""Create and clean-build the self-contained arXiv stage source bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
DIST = PAPER_ROOT / "dist"
ZIP_PATH = DIST / "arxiv_stage_source_v1.zip"
MANIFEST_PATH = DIST / "arxiv_stage_source_v1.manifest.json"
HASH_PATH = DIST / "arxiv_stage_source_v1.sha256"
TOP_FILES = (
    "main.tex",
    "metadata.tex",
    "macros.tex",
    "notation.tex",
    "references.bib",
    "main.bbl",
)
TREE_ROOTS = ("sections", "appendix")
PORTABLE_PROVENANCE_FILES = (
    "logic/claims.yaml",
    "logic/evidence_sources.yaml",
    "logic/logic_lock.json",
    "generated/generation_manifest.json",
    "generated/manifests/extracted_evidence.json",
    "generated/manifests/number_provenance.json",
    "generated/manifests/v4_table_provenance.json",
    "figures/generated/v4_paired_rmse_difference.provenance.json",
)
FIGURE_FILES = (
    "architecture.pdf",
    "timing.pdf",
    "derivative_timing.pdf",
    "governor_reachability.pdf",
    "phase_a_ablation.pdf",
    "csv_negative_result.pdf",
    "v3_direct_safety_runtime.pdf",
    "v4_paired_rmse_difference.pdf",
)
EXCLUDED_SUFFIXES = {
    ".aux",
    ".log",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".DS_Store",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_zip_member(
    archive: zipfile.ZipFile, source: Path, archive_name: str
) -> None:
    """Write one regular file with platform- and mtime-independent metadata."""
    info = zipfile.ZipInfo(archive_name, date_time=ZIP_TIMESTAMP)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(
        info,
        source.read_bytes(),
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


def verify_zip_metadata(archive: zipfile.ZipFile) -> None:
    """Reject host-derived metadata that would make the ZIP byte-unstable."""
    for info in archive.infolist():
        if info.date_time != ZIP_TIMESTAMP:
            raise RuntimeError(f"non-deterministic ZIP timestamp: {info.filename}")
        if info.create_system != 3 or info.external_attr != 0o100644 << 16:
            raise RuntimeError(f"non-deterministic ZIP permissions: {info.filename}")


def find_latexmk() -> str:
    found = shutil.which("latexmk")
    if found:
        return found
    candidates = sorted(
        (Path.home() / ".cache/codex-runtimes/codex-texlive/full/bin").glob(
            "*/latexmk"
        )
    )
    if candidates:
        return str(candidates[0])
    raise FileNotFoundError("latexmk is not available")


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def verify_clean_source() -> None:
    changed = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "paper"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()
    if changed:
        raise RuntimeError(
            "release packages require a committed paper source tree; "
            "tracked or untracked paper changes remain"
        )


def source_files() -> list[tuple[Path, Path]]:
    collected: list[tuple[Path, Path]] = []
    for name in TOP_FILES:
        path = PAPER_ROOT / name
        if not path.is_file():
            raise FileNotFoundError(path)
        collected.append((path, Path(name)))
    for name in PORTABLE_PROVENANCE_FILES:
        path = PAPER_ROOT / name
        if not path.is_file():
            raise FileNotFoundError(path)
        collected.append((path, Path(name)))
    for root_name in TREE_ROOTS:
        root = PAPER_ROOT / root_name
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.suffix in EXCLUDED_SUFFIXES:
                continue
            if path.suffix not in {".tex", ".pdf", ".json"}:
                continue
            collected.append((path, path.relative_to(PAPER_ROOT)))
    generated_files = [PAPER_ROOT / "generated/numbers.tex"]
    generated_files.extend(sorted((PAPER_ROOT / "generated/tables").glob("*.tex")))
    for path in generated_files:
        if not path.is_file():
            raise FileNotFoundError(path)
        collected.append((path, path.relative_to(PAPER_ROOT)))
    for name in FIGURE_FILES:
        path = PAPER_ROOT / "figures/generated" / name
        if not path.is_file():
            raise FileNotFoundError(path)
        collected.append((path, path.relative_to(PAPER_ROOT)))
    seen: set[Path] = set()
    unique: list[tuple[Path, Path]] = []
    for source, relative in collected:
        if relative not in seen:
            seen.add(relative)
            unique.append((source, relative))
    return unique


def clean_build(root: Path) -> dict:
    latexmk = find_latexmk()
    build = root / "build"
    build.mkdir()
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = "0"
    env["PATH"] = str(Path(latexmk).absolute().parent) + os.pathsep + env.get(
        "PATH", ""
    )
    command = [
        "latexmk",
        "-norc",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        "-outdir=build",
        "main.tex",
    ]
    executable_command = [latexmk, *command[1:]]
    result = subprocess.run(
        executable_command,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    pdf = build / "main.pdf"
    log = build / "main.log"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    bad_markers = (
        "There were undefined references",
        "There were undefined citations",
        "LaTeX Error: File",
    )
    if result.returncode or not pdf.is_file() or any(item in log_text for item in bad_markers):
        raise RuntimeError("arXiv clean build failed:\n" + result.stdout[-8000:])
    return {
        "command": command,
        "pdf_bytes": pdf.stat().st_size,
        "pdf_sha256": sha256(pdf),
    }


def main() -> int:
    verify_clean_source()
    DIST.mkdir(parents=True, exist_ok=True)
    files = source_files()
    with tempfile.TemporaryDirectory(prefix="otg-arxiv-source-") as temp:
        root = Path(temp) / "source"
        root.mkdir()
        entries: list[dict] = []
        for source, relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append(
                {
                    "path": relative.as_posix(),
                    "bytes": target.stat().st_size,
                    "sha256": sha256(target),
                }
            )
        if ZIP_PATH.exists():
            ZIP_PATH.unlink()
        with zipfile.ZipFile(
            ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for entry in entries:
                write_zip_member(archive, root / entry["path"], entry["path"])

    with tempfile.TemporaryDirectory(prefix="otg-arxiv-verify-") as temp:
        extracted = Path(temp) / "source"
        extracted.mkdir()
        with zipfile.ZipFile(ZIP_PATH) as archive:
            verify_zip_metadata(archive)
            members = archive.namelist()
            expected = [entry["path"] for entry in entries]
            if members != expected:
                raise RuntimeError("arXiv ZIP member list differs from its inventory")
            archive.extractall(extracted)
        for entry in entries:
            target = extracted / entry["path"]
            if not target.is_file() or sha256(target) != entry["sha256"]:
                raise RuntimeError(f"arXiv ZIP hash mismatch: {entry['path']}")
        build_result = clean_build(extracted)

    manifest = {
        "schema_version": "otg.arxiv-source-package.v2",
        "package_version": "v1",
        "package_filename": ZIP_PATH.name,
        "stage": "stage-draft-not-submitted",
        "source_commit": git("rev-parse", "HEAD"),
        "latest_main_commit": git("rev-parse", "origin/main"),
        "latest_main_merge_commit": (
            "8faedae1fe18111ad0329259b5618c06edf6020b"
        ),
        "v4_confirmation_source_commit": (
            "461fc560461b0a4726cbabdb97b2dbd4dc305e0a"
        ),
        "v4_bounded_result_commit": (
            "f49b4ef1cacf8228c5d243353184acb8a7d02311"
        ),
        "v4_report_only_reporting_repair_commit": (
            "8baece6b7051ccc231d9bb0362fd85e4aa5a94e5"
        ),
        "v4_report_only_aid_commit": (
            "b9301eaf36dc04f1abf662c42821eddfe8c3188a"
        ),
        "v4_release_tag": "paper-evidence-v4-461fc56",
        "v4_same_test_rerun_permitted": False,
        "v5_executed": False,
        "logic_lock_sha256": sha256(PAPER_ROOT / "logic/logic_lock.json"),
        "portable_provenance_files": list(PORTABLE_PROVENANCE_FILES),
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "file_count": len(entries),
        "uncompressed_bytes": sum(entry["bytes"] for entry in entries),
        "zip_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256(ZIP_PATH),
        "clean_build": build_result,
        "files": entries,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    HASH_PATH.write_text(f"{manifest['zip_sha256']}  {ZIP_PATH.name}\n", encoding="utf-8")
    print(
        f"wrote {ZIP_PATH.relative_to(REPO_ROOT)} "
        f"({manifest['file_count']} files, clean build passed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
