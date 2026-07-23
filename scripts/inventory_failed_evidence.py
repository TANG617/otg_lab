#!/usr/bin/env python3
"""Create a deterministic, fail-closed inventory for a failed evidence run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"manifest is not an object: {path}")
    return value


def build_inventory(
    root: Path,
    *,
    expected_commit: str,
    failed_stage: str,
    exception_type: str,
    exception_message: str,
    verified_complete_bundles: tuple[str, ...],
) -> dict[str, Any]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError(f"failed evidence root is missing: {resolved}")
    if len(expected_commit) != 40 or any(
        character not in "0123456789abcdef" for character in expected_commit
    ):
        raise ValueError("expected_commit must be a lowercase 40-character SHA")

    files = []
    for path in sorted(item for item in resolved.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(resolved).as_posix(),
                "byte_size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    if not files:
        raise ValueError("failed evidence root contains no files")

    bundles = []
    raw_root = resolved / "raw_runs"
    for bundle_root in sorted(item for item in raw_root.iterdir() if item.is_dir()):
        run_path = bundle_root / "run.json"
        if not run_path.is_file():
            raise ValueError(f"bundle has no run manifest: {bundle_root}")
        run = _read_manifest(run_path)
        if run.get("git_commit") != expected_commit:
            raise ValueError(
                f"bundle commit differs from failure commit: {bundle_root}"
            )
        name = bundle_root.name
        complete = (bundle_root / "artifact_index.json").is_file() and (
            bundle_root / "artifact_checksums.json"
        ).is_file()
        bundles.append(
            {
                "name": name,
                "status": "complete" if complete else "partial",
                "independent_recomputation_verified": name in verified_complete_bundles,
                "run_id": run.get("run_id"),
                "git_commit": run.get("git_commit"),
                "git_worktree_dirty": run.get("git_worktree_dirty"),
                "resolved_config_sha256": run.get("resolved_config_sha256"),
                "command": run.get("command"),
                "artifact_row_counts": run.get("artifact_row_counts"),
            }
        )
    observed_complete = {
        str(bundle["name"]) for bundle in bundles if bundle["status"] == "complete"
    }
    if set(verified_complete_bundles) != observed_complete:
        raise ValueError(
            "verified complete bundle declaration differs from on-disk completion"
        )

    canonical_files = json.dumps(
        files, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return {
        "schema_version": "otg.failed-confirmation-inventory.v1",
        "protocol_version": "v2",
        "status": "failed_nonconfirmatory_frozen",
        "root": root.as_posix(),
        "source_commit": expected_commit,
        "failed_stage": failed_stage,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "resume_permitted": False,
        "same_test_rerun_permitted": False,
        "next_protocol": "v3_fresh_test_required",
        "file_count": len(files),
        "total_byte_size": sum(int(row["byte_size"]) for row in files),
        "file_inventory_sha256": hashlib.sha256(canonical_files).hexdigest(),
        "bundles": bundles,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--failed-stage", required=True)
    parser.add_argument("--exception-type", required=True)
    parser.add_argument("--exception-message", required=True)
    parser.add_argument("--verified-complete-bundle", action="append", default=[])
    args = parser.parse_args()

    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite failure inventory: {output}")
    inventory = build_inventory(
        Path(args.root),
        expected_commit=str(args.expected_commit),
        failed_stage=str(args.failed_stage),
        exception_type=str(args.exception_type),
        exception_message=str(args.exception_message),
        verified_complete_bundles=tuple(
            sorted(str(value) for value in args.verified_complete_bundle)
        ),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    print(
        json.dumps(
            {
                key: inventory[key]
                for key in (
                    "status",
                    "source_commit",
                    "file_count",
                    "total_byte_size",
                    "file_inventory_sha256",
                    "next_protocol",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
