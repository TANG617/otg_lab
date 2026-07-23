#!/usr/bin/env python3
"""Inventory generated paper artifacts and their source-backed hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
OUTPUT = PAPER_ROOT / "generated/generation_manifest.json"
ROOTS = (PAPER_ROOT / "generated", PAPER_ROOT / "figures/generated")
EVIDENCE = PAPER_ROOT / "generated/manifests/extracted_evidence.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = [
        path
        for root in ROOTS
        for path in root.rglob("*")
        if path.is_file() and path != OUTPUT
    ]
    payload = {
        "schema_version": "otg.paper-generation-manifest.v1",
        "generated_at": json.loads(EVIDENCE.read_text(encoding="utf-8"))[
            "generated_at"
        ],
        "files": [
            {
                "path": path.relative_to(PAPER_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in sorted(files)
        ],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("generated artifact manifest is stale")
        print("generation manifest verified")
        return 0
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} ({len(files)} generated files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
