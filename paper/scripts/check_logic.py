#!/usr/bin/env python3
"""Validate the paper logic layer before manuscript generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
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
    "C14",
    "C15",
    "C16",
    "C17",
    "C18",
    "C19",
    "N01",
    "N02",
    "N03",
    "E01",
    "E02",
}
ALLOWED_STATUSES = {
    "confirmed_current",
    "confirmed_frozen_scope",
    "negative_current",
    "exploratory_confounded",
    "nonconfirmatory_frozen",
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
REQUIRED_V4_EVIDENCE = {
    "E_V4_PROTOCOL",
    "E_V4_FRESH_LOCKED_TEST",
    "E_V4_PRIMARY_OBSERVED_EFFECT",
    "E_V4_METHOD_PURITY",
    "E_V4_SAME_INFORMATION_FAILURE",
    "E_V4_SAFETY",
    "E_V4_LAG_GUARDRAIL",
    "E_V4_RUNTIME_FAILURE",
    "E_V4_HARMFUL_TRAJECTORIES",
    "E_V4_SUBGROUPS",
    "E_V4_ORDINARY_CONTEXT",
    "E_V4_ORACLE_CONTEXT",
    "E_V4_ARTIFACT_INTEGRITY",
}
REQUIRED_V4_EVIDENCE_FIELDS = {
    "source_id",
    "path",
    "git_commit",
    "sha256",
    "evidence_class",
    "temporal_class",
    "test_visibility",
    "causal_noncausal",
    "deployability",
    "exact_denominator",
    "status",
    "allowed_scientific_use",
    "forbidden_interpretation",
    "publication_section_permissions",
}
EXPECTED_V4_CLAIM_STATUSES = {
    "C14": "confirmed_frozen_scope",
    "C15": "nonconfirmatory_frozen",
    "C16": "confirmed_frozen_scope",
    "C17": "confirmed_frozen_scope",
    "C18": "nonconfirmatory_frozen",
    "C19": "confirmed_frozen_scope",
    "N03": "nonconfirmatory_frozen",
    "E02": "nonconfirmatory_frozen",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    missing_v4_evidence = sorted(REQUIRED_V4_EVIDENCE - evidence_ids)
    if missing_v4_evidence:
        errors.append(
            "missing required V4 evidence IDs: " + ", ".join(missing_v4_evidence)
        )
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id", ""))
        if source_id not in REQUIRED_V4_EVIDENCE:
            continue
        missing = REQUIRED_V4_EVIDENCE_FIELDS - set(item)
        if missing:
            errors.append(
                f"{source_id} missing V4 evidence fields: "
                + ", ".join(sorted(missing))
            )
        for key in (
            "allowed_scientific_use",
            "forbidden_interpretation",
            "publication_section_permissions",
        ):
            if not isinstance(item.get(key), list) or not item.get(key):
                errors.append(f"{source_id}: {key} must be a non-empty list")
        hashes = item.get("sha256", {})
        if not isinstance(hashes, dict) or not hashes:
            errors.append(f"{source_id}: sha256 must be a non-empty mapping")
            continue
        for relative, expected in hashes.items():
            source_path = REPO_ROOT / str(relative)
            if not source_path.is_file():
                errors.append(f"{source_id}: missing hashed source {relative}")
            elif sha256(source_path) != str(expected):
                errors.append(f"{source_id}: SHA-256 mismatch for {relative}")

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
    if by_id.get("N03", {}).get("requires_v4") is not False:
        errors.append("N03 must record requires_v4=false after completed V4")
    if by_id.get("N03", {}).get("requires_future_v5_for_confirmation") is not True:
        errors.append("N03 must require a future V5 for confirmation")
    n03_evidence = set(map(str, by_id.get("N03", {}).get("evidence_source_ids", [])))
    required_n03_evidence = {
        "E_V4_FRESH_LOCKED_TEST",
        "E_V4_PRIMARY_OBSERVED_EFFECT",
        "E_V4_SAME_INFORMATION_FAILURE",
        "E_V4_RUNTIME_FAILURE",
    }
    if n03_evidence != required_n03_evidence:
        errors.append("N03 must reference the four required V4 evidence sources")
    for claim_id, expected_status in EXPECTED_V4_CLAIM_STATUSES.items():
        if by_id.get(claim_id, {}).get("status") != expected_status:
            errors.append(f"{claim_id} must have status {expected_status}")

    evidence_payload = yaml.safe_load(
        (LOGIC_ROOT / "evidence_sources.yaml").read_text(encoding="utf-8")
    )
    audit = evidence_payload.get("audit", {}) if isinstance(evidence_payload, dict) else {}
    expected_audit = {
        "v4_executed": True,
        "v4_same_test_rerun": False,
        "v4_raw_experiment_resumed": False,
        "v4_protocol_status": "failed_test_visible_frozen",
        "v4_statistical_classification": "strongly_material",
        "v4_effective_classification": "invalid_method_identity",
    }
    for field, expected in expected_audit.items():
        if audit.get(field) != expected:
            errors.append(f"evidence audit {field} must be {expected!r}")

    runtime_status_path = REPO_ROOT / "results/paper_evidence_v4/protocol_status_v4.json"
    runtime_status = json.loads(runtime_status_path.read_text(encoding="utf-8"))
    if runtime_status.get("status") != "failed_test_visible_frozen":
        errors.append("V4 runtime protocol status must remain failed_test_visible_frozen")
    if runtime_status.get("primary_result_classification") != "invalid_method_identity":
        errors.append("V4 effective classification must remain invalid_method_identity")
    if runtime_status.get("statistical_classification") != "strongly_material":
        errors.append("V4 statistical classification must remain strongly_material")
    if runtime_status.get("same_test_rerun_permitted") is not False:
        errors.append("V4 same-test rerun must remain prohibited")
    if runtime_status.get("raw_experiment_resume_permitted") is not False:
        errors.append("V4 raw experiment resume must remain prohibited")

    active_logic_files = (
        "00_paper_charter.md",
        "01_claim_evidence_matrix.md",
        "02_argument_outline.md",
        "04_figures_and_tables_plan.md",
        "06_scope_and_limitations.md",
        "08_open_questions.md",
        "evidence_inventory.md",
        "claims.yaml",
    )
    active_logic = "\n".join(
        (LOGIC_ROOT / name).read_text(encoding="utf-8") for name in active_logic_files
    ).lower()
    stale_phrases = (
        "no fresh same-follower locked test exists",
        "there is no fresh v4",
        "no v4 experiment is authorized or included",
        "fresh same-follower confirmation remains unavailable",
    )
    for phrase in stale_phrases:
        if phrase in active_logic:
            errors.append(f"stale pre-V4 statement remains in active logic: {phrase!r}")

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
