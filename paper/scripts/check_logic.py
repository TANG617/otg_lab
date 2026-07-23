#!/usr/bin/env python3
"""Validate the paper logic layer before manuscript generation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


PAPER_ROOT = Path(__file__).resolve().parents[1]
LOGIC_ROOT = PAPER_ROOT / "logic"
REQUIRED_LOGIC = (
    "README.md",
    "00_paper_charter.md",
    "01_claim_evidence_matrix.md",
    "02_argument_outline.md",
    "03_notation_and_timing.md",
    "04_figures_and_tables_plan.md",
    "05_literature_matrix.md",
    "06_scope_and_limitations.md",
    "07_writing_style.md",
    "08_open_questions.md",
    "evidence_sources.yaml",
    "evidence_inventory.md",
    "claims.yaml",
    "decision_log.md",
)
REQUIRED_CLAIMS = {
    "C01",
    "C02",
    "C03",
    "C04",
    "C05",
    "C06",
    "C07",
    "C08",
    "C09",
    "C10",
    "C11",
    "C12",
    "C13",
    "N01",
    "N02",
    "N03",
    "E01",
}
ALLOWED_STATUSES = {
    "confirmed_current",
    "confirmed_frozen_scope",
    "negative_current",
    "exploratory_confounded",
    "not_evaluated",
    "external_blocker",
}
REQUIRED_CLAIM_FIELDS = {
    "claim_id",
    "canonical_wording",
    "status",
    "evidence_source_ids",
    "exact_quantitative_support",
    "allowed_verbs",
    "prohibited_wording",
    "allowed_sections",
    "allowed_in_abstract",
    "allowed_in_conclusion",
    "requires_v4",
    "limitation_note",
}
FORBIDDEN_TITLE_WORDS = {
    "superior",
    "optimal",
    "high-performance",
    "real-robot",
    "deployment-ready",
    "breakthrough",
    "state-of-the-art",
}


def load_entries(path: Path, key: str) -> list[dict]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for candidate in (key, "sources" if key == "evidence_sources" else key):
            if isinstance(payload.get(candidate), list):
                return payload[candidate]
    raise ValueError(f"{path} must contain a list or a '{key}' list")


def check() -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_LOGIC:
        path = LOGIC_ROOT / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty logic file: {path.relative_to(PAPER_ROOT)}")

    for forbidden in ("manuscript.md", "full_paper.md"):
        if (PAPER_ROOT / forbidden).exists():
            errors.append(f"forbidden Markdown manuscript source exists: {forbidden}")
    review = PAPER_ROOT / "ADVERSARIAL_LOGIC_REVIEW.md"
    if not review.is_file() or review.stat().st_size == 0:
        errors.append("missing adversarial logic review")

    if errors:
        return errors

    claims = load_entries(LOGIC_ROOT / "claims.yaml", "claims")
    evidence = load_entries(LOGIC_ROOT / "evidence_sources.yaml", "evidence_sources")
    evidence_ids = {
        str(item.get("source_id", "")).strip() for item in evidence if isinstance(item, dict)
    }

    seen: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            errors.append("claim entry is not a mapping")
            continue
        missing = REQUIRED_CLAIM_FIELDS - set(claim)
        if missing:
            errors.append(
                f"claim {claim.get('claim_id', '<unknown>')} missing fields: "
                + ", ".join(sorted(missing))
            )
        claim_id = str(claim.get("claim_id", ""))
        if claim_id in seen:
            errors.append(f"duplicate claim ID: {claim_id}")
        seen.add(claim_id)
        if claim.get("status") not in ALLOWED_STATUSES:
            errors.append(f"{claim_id}: invalid status {claim.get('status')!r}")
        refs = claim.get("evidence_source_ids", [])
        if not isinstance(refs, list):
            errors.append(f"{claim_id}: evidence_source_ids must be a list")
        else:
            unknown = sorted(set(map(str, refs)) - evidence_ids)
            if unknown:
                errors.append(f"{claim_id}: unknown evidence IDs: {', '.join(unknown)}")

    missing_claims = REQUIRED_CLAIMS - seen
    if missing_claims:
        errors.append("missing required claims: " + ", ".join(sorted(missing_claims)))

    by_id = {str(item.get("claim_id")): item for item in claims if isinstance(item, dict)}
    if by_id.get("E01", {}).get("allowed_in_abstract") is not False:
        errors.append("E01 must be prohibited from the abstract")
    if by_id.get("E01", {}).get("allowed_in_conclusion") is not False:
        errors.append("E01 must be prohibited from the conclusion")
    if by_id.get("N03", {}).get("requires_v4") is not True:
        errors.append("N03 must explicitly require v4")

    charter = (LOGIC_ROOT / "00_paper_charter.md").read_text(encoding="utf-8")
    title_match = re.search(r"(?im)^selected title\s*:\s*(.+)$", charter)
    if not title_match:
        title_match = re.search(
            r"(?ims)^#+\s*selected title\s*\n+\s*\*\*(.+?)\*\*", charter
        )
    if not title_match:
        errors.append("paper charter must contain a machine-readable 'Selected title:' line")
    else:
        title = " ".join(title_match.group(1).strip().strip("*_").split())
        lowered = title.lower()
        bad = sorted(word for word in FORBIDDEN_TITLE_WORDS if word in lowered)
        if bad:
            errors.append("selected title contains forbidden terms: " + ", ".join(bad))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    errors = check()
    result = {"ok": not errors, "error_count": len(errors), "errors": errors}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
    else:
        print("logic check passed")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
