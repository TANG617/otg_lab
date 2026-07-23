#!/usr/bin/env python3
"""Compare a Prism export directory with the canonical paper tree."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PAPER_ROOT / "dist/prism_import_v0.manifest.json"
SOURCE_SUFFIXES = {".tex", ".bib", ".bbl", ".pdf", ".md", ".yaml", ".json"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    args = parser.parse_args()
    if not args.export_dir.is_dir():
        raise SystemExit(f"not a directory: {args.export_dir}")
    if not MANIFEST.is_file():
        raise SystemExit("Prism package manifest is missing; run make prism-package")

    export_root = args.export_dir.resolve()
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = {entry["path"]: entry["sha256"] for entry in payload["files"]}
    errors: list[str] = []
    for relative, expected_hash in expected.items():
        canonical = PAPER_ROOT / relative
        exported = export_root / relative
        if not canonical.is_file() or sha256(canonical) != expected_hash:
            errors.append(f"stale package manifest entry: {relative}")
        elif not exported.is_file():
            errors.append(f"missing from Prism export: {relative}")
        elif sha256(exported) != expected_hash:
            errors.append(f"changed in Prism export: {relative}")

    exported_source_files = {
        path.relative_to(export_root).as_posix()
        for path in export_root.rglob("*")
        if path.is_file()
        and path.suffix in SOURCE_SUFFIXES
        and ".git" not in path.parts
        and "build" not in path.parts
    }
    for relative in sorted(exported_source_files - expected.keys()):
        errors.append(f"unexpected source file in Prism export: {relative}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Prism export matches {len(expected)} manifest entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
