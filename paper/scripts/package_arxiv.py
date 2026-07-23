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
ZIP_PATH = DIST / "arxiv_stage_source_v0.zip"
MANIFEST_PATH = DIST / "arxiv_stage_source_v0.manifest.json"
HASH_PATH = DIST / "arxiv_stage_source_v0.sha256"
TOP_FILES = (
    "main.tex",
    "metadata.tex",
    "macros.tex",
    "notation.tex",
    "references.bib",
    "main.bbl",
)
TREE_ROOTS = ("sections", "appendix", "generated", "figures/generated")
EXCLUDED_SUFFIXES = {
    ".aux",
    ".log",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".DS_Store",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def source_files() -> list[tuple[Path, Path]]:
    collected: list[tuple[Path, Path]] = []
    for name in TOP_FILES:
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
    command = [
        latexmk,
        "-norc",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        f"-outdir={build}",
        "main.tex",
    ]
    result = subprocess.run(
        command,
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
        build_result = clean_build(root)
        if ZIP_PATH.exists():
            ZIP_PATH.unlink()
        with zipfile.ZipFile(
            ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for entry in entries:
                archive.write(root / entry["path"], entry["path"])

    manifest = {
        "schema_version": "otg.arxiv-source-package.v1",
        "stage": "stage-draft-not-submitted",
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
