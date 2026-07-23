#!/usr/bin/env python3
"""Inventory generated paper artifacts and their source-backed hashes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
OUTPUT = PAPER_ROOT / "generated/generation_manifest.json"
ROOTS = (PAPER_ROOT / "generated", PAPER_ROOT / "figures/generated")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    files = [
        path
        for root in ROOTS
        for path in root.rglob("*")
        if path.is_file() and path != OUTPUT
    ]
    payload = {
        "schema_version": "otg.paper-generation-manifest.v1",
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "files": [
            {
                "path": path.relative_to(PAPER_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in sorted(files)
        ],
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} ({len(files)} generated files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
