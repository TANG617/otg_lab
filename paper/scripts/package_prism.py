#!/usr/bin/env python3
"""Create a milestone-only Prism review import bundle."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from package_arxiv import clean_build, git, verify_clean_source

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
    verify_clean_source()
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
    with tempfile.TemporaryDirectory(prefix="otg-prism-review-") as temp:
        extracted = Path(temp) / "source"
        extracted.mkdir()
        with zipfile.ZipFile(ZIP_PATH) as archive:
            members = archive.namelist()
            expected = [entry["path"] for entry in entries]
            if members != expected:
                raise RuntimeError("Prism ZIP member list differs from its source inventory")
            archive.extractall(extracted)
        for entry in entries:
            target = extracted / entry["path"]
            if not target.is_file() or sha256(target) != entry["sha256"]:
                raise RuntimeError(f"Prism ZIP hash mismatch: {entry['path']}")
        build_result = clean_build(extracted)

    source_commit = git("rev-parse", "HEAD")
    logic_lock_hash = sha256(PAPER_ROOT / "logic/logic_lock.json")
    manifest = {
        "schema_version": "otg.prism-review-package.v1",
        "canonical_source": "Git .tex files",
        "source_commit": source_commit,
        "logic_lock_sha256": logic_lock_hash,
        "review_milestone": "arxiv-stage-draft-v0",
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "file_count": len(entries),
        "zip_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256(ZIP_PATH),
        "verification": {
            "zip_member_hashes": "passed",
            "clean_build": build_result,
        },
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
