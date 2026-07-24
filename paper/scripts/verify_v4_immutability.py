#!/usr/bin/env python3
"""Verify that the bounded, frozen V4 evidence remains byte-identical."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
RESULT_ROOT = REPO_ROOT / "results/paper_evidence_v4"

ROOT_HASHES = {
    "EXPERIMENT_PROTOCOL_V4.md": (
        "baad38320593695a4c231f1802faa3a48b4a32b318da841fda5b1354cd8b770e"
    ),
    "split_manifest_v4.json": (
        "1727505734c8026ed18d87123d5d5a8c02e2f201a33ea786fbcde2c9ab398796"
    ),
    "config_lock_v4.json": (
        "d61b0f8596b04358c7bef6a1e43b6775b3dbb00020c2aca28d5d2cd4d9f6f3d3"
    ),
    "protocol_status_v4.json": (
        "c0c3d358c969dbb343ac05dc964075a514f37d8153ce47d6e4ca60a252de4909"
    ),
    "results/paper_evidence_v4/artifact_index.json": (
        "fd78eb559d039620ae1c6e06faac44ab6fc8dbff9208c05523b4efcab4a75a95"
    ),
    "results/paper_evidence_v4/artifact_index.sha256": (
        "96fbd8d2dc165beca47b40dd2ecb8eb46f6ae1be7f095974cc69e1ae2c9b9582"
    ),
    "same_information_failures.csv": (
        "dd9c89784766f85473159da6a5c0f072881e47828874fee7f17c7613cd86718f"
    ),
    "SAME_INFORMATION_FAILURE_ANALYSIS.md": (
        "2144b449db3d189684833449b4686982b9156cf19db00dcc48360e6650287573"
    ),
    "V4_AGENT_EXECUTION_AUDIT.md": (
        "2dd7433ca27a9a75197393c32c4d55bed85259106c79b96ec86a504cb6067d36"
    ),
}

FROZEN_PATHS = (
    "EXPERIMENT_PROTOCOL_V4.md",
    "V4_HYPOTHESES.md",
    "V4_STATISTICAL_DESIGN.json",
    "V4_ACCEPTANCE_CRITERIA.json",
    "V4_METHOD_MATRIX.json",
    "V4_PROTOCOL_DECISIONS.md",
    "config_lock_v4.json",
    "split_manifest_v4.json",
    "protocol_status_v4.json",
    "results/paper_evidence_v4",
    "same_information_failures.csv",
    "SAME_INFORMATION_FAILURE_ANALYSIS.md",
    "V4_AGENT_EXECUTION_AUDIT.md",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="fail unless all 152 indexed artifacts are present for byte verification",
    )
    args = parser.parse_args()

    for relative, expected in ROOT_HASHES.items():
        path = REPO_ROOT / relative
        if not path.is_file():
            fail(f"missing frozen V4 file: {relative}")
        actual = sha256(path)
        if actual != expected:
            fail(f"frozen V4 hash mismatch: {relative}: {actual} != {expected}")

    index = json.loads((RESULT_ROOT / "artifact_index.json").read_text(encoding="utf-8"))
    artifacts = index.get("artifacts", [])
    if index.get("artifact_count") != 152 or len(artifacts) != 152:
        fail("V4 artifact index must retain all 152 bounded artifacts")
    verified_count = 0
    missing_untracked: list[str] = []
    for artifact in artifacts:
        relative = str(artifact["path"])
        path = RESULT_ROOT / relative
        if not path.is_file():
            repository_relative = path.relative_to(REPO_ROOT).as_posix()
            tracked = (
                subprocess.run(
                    ["git", "ls-files", "--error-unmatch", repository_relative],
                    cwd=REPO_ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            )
            if tracked:
                fail(f"tracked indexed V4 artifact is missing: {relative}")
            missing_untracked.append(relative)
            continue
        if path.stat().st_size != int(artifact["bytes"]):
            fail(f"indexed V4 artifact size mismatch: {relative}")
        actual = sha256(path)
        if actual != artifact["sha256"]:
            fail(f"indexed V4 artifact hash mismatch: {relative}")
        verified_count += 1
    if args.require_complete and missing_untracked:
        fail(
            "complete V4 bundle required, but indexed artifacts are unavailable: "
            + ", ".join(missing_untracked)
        )

    status = json.loads(
        (RESULT_ROOT / "protocol_status_v4.json").read_text(encoding="utf-8")
    )
    required_status = {
        "status": "failed_test_visible_frozen",
        "primary_result_classification": "invalid_method_identity",
        "statistical_classification": "strongly_material",
        "same_test_rerun_permitted": False,
        "raw_experiment_resume_permitted": False,
    }
    for key, expected in required_status.items():
        if status.get(key) != expected:
            fail(f"V4 protocol status changed: {key}={status.get(key)!r}")

    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "origin/main", "--", *FROZEN_PATHS],
        cwd=REPO_ROOT,
        text=True,
    ).splitlines()
    if changed:
        fail("frozen V4 paths differ from latest main: " + ", ".join(changed))

    print(
        "V4 immutability verified "
        f"({len(artifacts)} indexed identities; {verified_count} present artifacts "
        f"byte-verified; {len(missing_untracked)} untracked bundle artifacts "
        f"unavailable; status {required_status['status']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
