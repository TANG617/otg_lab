#!/usr/bin/env python3
"""Create a whitelist-only review or arXiv source archive."""

from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]


def files_for_package(dry_run: bool) -> list[Path]:
    metadata = PAPER_ROOT / ("metadata-draft.tex" if dry_run else "metadata.tex")
    paths = [
        PAPER_ROOT / "main.tex",
        PAPER_ROOT / "paper_style.tex",
        PAPER_ROOT / "macros.tex",
        metadata,
        PAPER_ROOT / "generated" / "numbers.tex",
        PAPER_ROOT / "references.bib",
    ]
    paths.extend(sorted((PAPER_ROOT / "sections").glob("*.tex")))
    paths.extend(sorted((PAPER_ROOT / "figures" / "generated").glob("*.pdf")))
    paths.extend(sorted((PAPER_ROOT / "tables" / "generated").glob("*.tex")))
    bbl = PAPER_ROOT / "build" / "texlive" / "main.bbl"
    if bbl.is_file():
        paths.append(bbl)
    else:
        raise FileNotFoundError("TeX Live main.bbl is required before packaging")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    return paths


def archive_name(path: Path) -> str:
    if path.name == "metadata-draft.tex":
        return "metadata-draft.tex"
    if path.name == "metadata.tex":
        return "metadata.tex"
    if path.name == "main.bbl":
        return "main.bbl"
    return path.relative_to(PAPER_ROOT).as_posix()


def package(output: Path, dry_run: bool) -> Path:
    files = files_for_package(dry_run)
    forbidden_suffixes = {".aux", ".log", ".out", ".synctex.gz", ".svg"}
    for path in files:
        if path.suffix.lower() in forbidden_suffixes or path.name == "main.pdf":
            raise ValueError(f"forbidden arXiv source file: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            info = archive.gettarinfo(str(path), arcname=archive_name(path))
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with path.open("rb") as handle:
                archive.addfile(info, handle)

    cleanroom = PAPER_ROOT / "build" / ("arxiv-dry-run" if dry_run else "arxiv-release")
    if cleanroom.exists():
        shutil.rmtree(cleanroom)
    cleanroom.mkdir(parents=True)
    with tarfile.open(output, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or not member.isfile():
                raise ValueError(f"unsafe or non-file archive member: {member.name}")
        # Python 3.11 does not yet expose extractall(filter=...). The archive is
        # produced immediately above from a fixed whitelist, and the explicit
        # member checks preserve clean-room extraction on that runtime.
        archive.extractall(cleanroom)
    manifest = {
        "archive": output.relative_to(PAPER_ROOT).as_posix(),
        "dry_run": dry_run,
        "cleanroom": cleanroom.relative_to(PAPER_ROOT).as_posix(),
        "files": [archive_name(path) for path in files],
    }
    (output.with_suffix(output.suffix + ".json")).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return cleanroom


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cleanroom = package(args.output.resolve(), args.dry_run)
    print(cleanroom)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
