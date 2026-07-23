#!/usr/bin/env python3
"""Create a milestone-only Prism review import bundle."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
DIST = PAPER_ROOT / "dist"
ZIP_PATH = DIST / "prism_import_v0.zip"
MANIFEST_PATH = DIST / "prism_import_v0.manifest.json"
HASH_PATH = DIST / "prism_import_v0.sha256"
ROOT_FILES = (
    "main.tex",
    "metadata.tex",
    "macros.tex",
    "notation.tex",
    "references.bib",
    "main.bbl",
)
TREES = ("sections", "appendix", "generated", "figures/generated", "logic", "prism")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    files: list[Path] = [PAPER_ROOT / name for name in ROOT_FILES]
    for tree in TREES:
        files.extend(path for path in (PAPER_ROOT / tree).rglob("*") if path.is_file())
    files = sorted(
        {
            path
            for path in files
            if path.is_file()
            and path.suffix in {".tex", ".bib", ".bbl", ".pdf", ".md", ".yaml", ".json"}
            and not path.name.startswith(".")
        }
    )
    DIST.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "path": path.relative_to(PAPER_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in files
    ]
    with zipfile.ZipFile(
        ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path, entry in zip(files, entries):
            archive.write(path, entry["path"])
    manifest = {
        "schema_version": "otg.prism-review-package.v1",
        "canonical_source": "Git .tex files",
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "file_count": len(entries),
        "zip_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256(ZIP_PATH),
        "files": entries,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    HASH_PATH.write_text(f"{manifest['zip_sha256']}  {ZIP_PATH.name}\n", encoding="utf-8")
    print(f"wrote {ZIP_PATH.relative_to(REPO_ROOT)} ({len(entries)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
