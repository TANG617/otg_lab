#!/usr/bin/env python3
"""Verify the frozen v3 root of trust and absence of working-tree edits."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    status = json.loads(
        (REPO_ROOT / "protocol_status_v3_postreview.json").read_text(encoding="utf-8")
    )
    frozen = status["frozen_source"]
    expected = {
        frozen["protocol_path"]: frozen["protocol_sha256"],
        frozen["artifact_index_path"]: frozen["artifact_index_sha256"],
        frozen["original_status_path"]: frozen["original_status_sha256"],
    }
    errors: list[str] = []
    for relative, expected_hash in expected.items():
        actual = digest(REPO_ROOT / relative)
        if actual != expected_hash:
            errors.append(f"{relative}: expected {expected_hash}, got {actual}")

    diff = subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            "--",
            "EXPERIMENT_PROTOCOL_V3.md",
            "protocol_status_v3.json",
            "protocol_status_v3_postreview.json",
            "V3_POSTREVIEW_ADDENDUM.md",
            "results/paper_evidence_v3",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if diff.returncode:
        errors.append("frozen paths have a working-tree diff")
    if errors:
        raise SystemExit("\n".join(errors))
    print("frozen v3 root of trust verified; no frozen-path working-tree diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
