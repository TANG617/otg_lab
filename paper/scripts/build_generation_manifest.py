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
NUMBER_PROVENANCE = (
    PAPER_ROOT / "generated/manifests/number_provenance.json"
)
V4_TABLE_PROVENANCE = (
    PAPER_ROOT / "generated/manifests/v4_table_provenance.json"
)
V4_FIGURE_PROVENANCE = (
    PAPER_ROOT
    / "figures/generated/v4_paired_rmse_difference.provenance.json"
)


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
    provenance_paths = (
        NUMBER_PROVENANCE,
        V4_TABLE_PROVENANCE,
        V4_FIGURE_PROVENANCE,
    )
    missing_provenance = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in provenance_paths
        if not path.is_file()
    ]
    if missing_provenance:
        raise FileNotFoundError(
            "missing generation provenance:\n"
            + "\n".join(missing_provenance)
        )

    number_provenance = json.loads(
        NUMBER_PROVENANCE.read_text(encoding="utf-8")
    )
    table_provenance = json.loads(
        V4_TABLE_PROVENANCE.read_text(encoding="utf-8")
    )
    figure_provenance = json.loads(
        V4_FIGURE_PROVENANCE.read_text(encoding="utf-8")
    )
    consumed_sources: dict[str, dict[str, object]] = {}

    def register_source(item: dict[str, object]) -> None:
        path_text = str(item["path"])
        record = {
            "path": path_text,
            "sha256": str(item["sha256"]),
        }
        if "bytes" in item:
            record["bytes"] = int(item["bytes"])
        previous = consumed_sources.get(path_text)
        if previous is not None and previous["sha256"] != record["sha256"]:
            raise ValueError(f"conflicting source hashes for {path_text}")
        consumed_sources[path_text] = record

    for macro in number_provenance["macros"].values():
        for source in macro["sources"]:
            register_source(source)
    for source in table_provenance["sources"].values():
        register_source(source)
    for source in figure_provenance["sources"]:
        register_source(source)

    for record in consumed_sources.values():
        source_path = REPO_ROOT / str(record["path"])
        if not source_path.is_file():
            raise FileNotFoundError(
                f"generated artifact source is missing: {record['path']}"
            )
        actual = digest(source_path)
        if actual != record["sha256"]:
            raise ValueError(
                f"generated artifact source hash mismatch: {record['path']}"
            )
        if "bytes" in record and source_path.stat().st_size != record["bytes"]:
            raise ValueError(
                f"generated artifact source size mismatch: {record['path']}"
            )

    payload = {
        "schema_version": "otg.paper-generation-manifest.v2",
        "generated_at": json.loads(EVIDENCE.read_text(encoding="utf-8"))[
            "generated_at"
        ],
        "provenance_records": [
            {
                "path": path.relative_to(PAPER_ROOT).as_posix(),
                "sha256": digest(path),
            }
            for path in provenance_paths
        ],
        "consumed_sources": [
            consumed_sources[path] for path in sorted(consumed_sources)
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
