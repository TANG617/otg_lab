#!/usr/bin/env python3
"""Create the content-addressed paper logic lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from check_logic import LOGIC_ROOT, PAPER_ROOT, REQUIRED_LOGIC, check, load_entries


REPO_ROOT = PAPER_ROOT.parent
LOCK_PATH = LOGIC_ROOT / "logic_lock.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def selected_title() -> str:
    charter = (LOGIC_ROOT / "00_paper_charter.md").read_text(encoding="utf-8")
    match = re.search(r"(?im)^selected title\s*:\s*(.+)$", charter)
    if not match:
        match = re.search(
            r"(?ims)^#+\s*selected title\s*\n+\s*\*\*(.+?)\*\*", charter
        )
    if not match:
        raise ValueError("selected title not found after logic validation")
    return " ".join(match.group(1).strip().strip("*_").split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    errors = check()
    if errors:
        raise SystemExit("logic validation failed:\n" + "\n".join(errors))

    claims = load_entries(LOGIC_ROOT / "claims.yaml", "claims")
    files = {
        name: sha256(LOGIC_ROOT / name)
        for name in REQUIRED_LOGIC
        if name != "logic_lock.json"
    }
    source_head = git("rev-parse", "HEAD")
    source_main = git("rev-parse", "origin/main")
    status_groups: dict[str, list[str]] = {}
    for claim in claims:
        status_groups.setdefault(str(claim["status"]), []).append(str(claim["claim_id"]))
    for values in status_groups.values():
        values.sort()

    payload = {
        "schema_version": "otg.paper-logic-lock.v1",
        "latest_source_repository_commit": source_main,
        "working_source_commit": source_head,
        "logic_files": files,
        "adversarial_logic_review_hash": sha256(
            PAPER_ROOT / "ADVERSARIAL_LOGIC_REVIEW.md"
        ),
        "evidence_source_inventory_hash": files["evidence_sources.yaml"],
        "claims_yaml_hash": files["claims.yaml"],
        "title": selected_title(),
        "paper_scope": (
            "methodology + system formulation + controlled empirical study; "
            "position-only, jerk-limited reference following"
        ),
        "confirmed_claim_ids": sorted(
            status_groups.get("confirmed_current", [])
            + status_groups.get("confirmed_frozen_scope", [])
            + status_groups.get("negative_current", [])
        ),
        "exploratory_claim_ids": status_groups.get("exploratory_confounded", []),
        "unsupported_claim_ids": sorted(
            status_groups.get("not_evaluated", [])
            + status_groups.get("external_blocker", [])
        ),
        "v3_artifact_index_hash": sha256(
            REPO_ROOT / "results/paper_evidence_v3/artifact_index.json"
        ),
        "v3_postreview_status_hash": sha256(
            REPO_ROOT / "protocol_status_v3_postreview.json"
        ),
        "logic_locked_timestamp": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "lock_status": "locked",
        "unresolved_blockers": [
            "author, affiliation, email, and ORCID metadata",
            "fresh v4 same-follower confirmation (outside current scope)",
            "independent real-stream and hardware evaluation (outside current scope)",
        ],
    }

    if args.verify:
        if not LOCK_PATH.is_file():
            raise SystemExit("logic lock is missing")
        current = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        comparable_keys = {
            "logic_files",
            "adversarial_logic_review_hash",
            "evidence_source_inventory_hash",
            "claims_yaml_hash",
            "title",
            "v3_artifact_index_hash",
            "v3_postreview_status_hash",
            "lock_status",
        }
        mismatches = [
            key for key in comparable_keys if current.get(key) != payload.get(key)
        ]
        if mismatches:
            raise SystemExit("logic lock is stale: " + ", ".join(sorted(mismatches)))
        print("logic lock verified")
        return 0

    LOCK_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {LOCK_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
