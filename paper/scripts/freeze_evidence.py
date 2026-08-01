#!/usr/bin/env python3
"""Validate and freeze the minimal paper evidence set."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

PAPER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PAPER_ROOT.parent
SCHEMA_VERSION = "paper.evidence.v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_profile(profile: str) -> tuple[Path, dict[str, Any]]:
    config_path = PAPER_ROOT / "evidence" / f"{profile}.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"evidence profile not found: {config_path}")
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{config_path} must use schema {SCHEMA_VERSION}")
    if loaded.get("profile") != profile:
        raise ValueError("profile name does not match evidence filename")
    expected_claims = {f"C{index}" for index in range(1, 14)}
    claims = loaded.get("claims")
    if not isinstance(claims, dict) or set(claims) != expected_claims:
        raise ValueError("evidence profile must map exactly C1--C13")
    return config_path, loaded


def _safe_project_path(relative: str) -> Path:
    path = (PROJECT_ROOT / relative).resolve()
    path.relative_to(PROJECT_ROOT)
    return path


def _safe_frozen_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    return path


def validate_source_record(source: dict[str, Any], manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_run = source["run_id"]
    actual_run = manifest.get("run_id")
    if actual_run is not None and actual_run != expected_run:
        raise ValueError(
            f"{source['evidence_id']} run mismatch: {actual_run} != {expected_run}"
        )
    git = manifest.get("git", {})
    if git.get("commit") != source["git_commit"]:
        raise ValueError(f"{source['evidence_id']} git commit mismatch")
    if bool(git.get("dirty")) != bool(source["git_dirty"]):
        raise ValueError(f"{source['evidence_id']} dirty flag mismatch")
    if source["kind"] == "confirmatory_experiment" or source["evidence_id"] == "E11":
        if manifest.get("spec_hash") != source["spec_hash"]:
            raise ValueError(f"{source['evidence_id']} spec hash mismatch")


def freeze(profile: str, require_release: bool) -> Path:
    config_path, config = load_profile(profile)
    if require_release:
        if not config.get("release_ready") or not config.get("generated_from_clean_git"):
            raise ValueError("release evidence must declare release_ready and clean git")
        dirty = [s["evidence_id"] for s in config["sources"] if s.get("git_dirty")]
        if dirty:
            raise ValueError(f"release evidence contains dirty sources: {dirty}")

    frozen_root = PAPER_ROOT / "evidence" / "frozen" / profile
    frozen_root.mkdir(parents=True, exist_ok=True)
    declared_destinations: set[str] = set()
    manifest_records: list[dict[str, Any]] = []
    source_ids: set[str] = set()

    for source in config["sources"]:
        evidence_id = str(source["evidence_id"])
        if evidence_id in source_ids:
            raise ValueError(f"duplicate evidence_id: {evidence_id}")
        source_ids.add(evidence_id)
        first_manifest: Path | None = None
        frozen_files: list[dict[str, Any]] = []
        for item in source["files"]:
            relative_source = str(item["source"])
            relative_destination = str(item["destination"])
            if relative_destination in declared_destinations:
                raise ValueError(f"duplicate frozen destination: {relative_destination}")
            declared_destinations.add(relative_destination)
            source_path = _safe_project_path(relative_source)
            destination_path = _safe_frozen_path(frozen_root, relative_destination)
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            actual_sha = sha256(source_path)
            expected_sha = str(item["sha256"])
            if actual_sha != expected_sha:
                raise ValueError(
                    f"source hash mismatch for {relative_source}: {actual_sha} != {expected_sha}"
                )
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination_path)
            if sha256(destination_path) != expected_sha:
                raise RuntimeError(f"frozen copy hash mismatch: {destination_path}")
            if destination_path.name in {"manifest.json", "analysis_manifest.json"}:
                first_manifest = destination_path
            frozen_files.append(
                {
                    "source": relative_source,
                    "frozen": destination_path.relative_to(PAPER_ROOT).as_posix(),
                    "sha256": expected_sha,
                    "bytes": destination_path.stat().st_size,
                }
            )
        if first_manifest is None:
            raise ValueError(f"{evidence_id} has no frozen manifest")
        validate_source_record(source, first_manifest)
        manifest_records.append(
            {
                "evidence_id": evidence_id,
                "kind": source["kind"],
                "run_id": source["run_id"],
                "spec_hash": source["spec_hash"],
                "git_commit": source["git_commit"],
                "git_dirty": bool(source["git_dirty"]),
                "files": frozen_files,
            }
        )

    referenced = {item for values in config["claims"].values() for item in values}
    missing = sorted(referenced - source_ids)
    if missing:
        raise ValueError(f"claims reference missing evidence IDs: {missing}")

    artifact_manifest = {
        "schema_version": "paper.frozen_evidence.v1",
        "profile": profile,
        "release_ready": bool(config.get("release_ready")),
        "generated_from_clean_git": bool(config.get("generated_from_clean_git")),
        "profile_config": config_path.relative_to(PAPER_ROOT).as_posix(),
        "profile_sha256": sha256(config_path),
        "claims": config["claims"],
        "sources": manifest_records,
    }
    output = frozen_root / "artifact_manifest.json"
    output.write_text(
        json.dumps(artifact_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="provisional")
    parser.add_argument("--require-release", action="store_true")
    args = parser.parse_args()
    output = freeze(args.profile, args.require_release)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
