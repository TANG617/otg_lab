#!/usr/bin/env python3
"""Check manuscript claim annotations and evidence-boundary language."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

PAPER_ROOT = Path(__file__).resolve().parents[1]
CLAIMS_PATH = PAPER_ROOT / "logic/claims.yaml"
ABSTRACT = PAPER_ROOT / "sections/00_abstract.tex"
INTRODUCTION = PAPER_ROOT / "sections/01_introduction.tex"
CONCLUSION = PAPER_ROOT / "sections/08_conclusion.tex"
TEX_ROOTS = [PAPER_ROOT / "sections", PAPER_ROOT / "appendix"]
FILE_LOCATIONS = {
    "00_abstract.tex": "abstract",
    "01_introduction.tex": "introduction",
    "02_related_work.tex": "related_work",
    "03_problem_formulation.tex": "problem_formulation",
    "04_method.tex": "method",
    "05_experimental_protocol.tex": "experimental_protocol",
    "06_results.tex": "results",
    "07_discussion.tex": "discussion",
    "08_conclusion.tex": "conclusion",
    "A_governor_derivation.tex": "appendix_governor_derivation",
    "B_experiment_details.tex": "appendix_experiment_details",
    "C_negative_results.tex": "appendix_negative_results",
    "D_evidence_provenance.tex": "appendix_evidence_provenance",
    "E_reproducibility.tex": "appendix_reproducibility",
}

FORBIDDEN_PHRASES = {
    "state of the art",
    "state-of-the-art",
    "deployment ready",
    "deployment-ready",
    "production safe",
    "universally optimal",
    "real-robot improvement",
    "pva is superior",
    "pva outperforms p",
    "pva outperforms pv",
    "tracking is necessary",
}


def claim_entries() -> list[dict]:
    payload = yaml.safe_load(CLAIMS_PATH.read_text(encoding="utf-8"))
    return payload["claims"] if isinstance(payload, dict) else payload


def annotation_occurrences(text: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for match in re.finditer(r"(?m)^%\s*CLAIM:\s*([A-Z0-9, ]+)\s*$", text):
        found.extend(
            (item.strip(), match.start())
            for item in match.group(1).split(",")
            if item.strip()
        )
    return found


def annotations(text: str) -> set[str]:
    found: set[str] = set()
    found.update(claim_id for claim_id, _ in annotation_occurrences(text))
    return found


def annotation_location(path: Path, text: str, offset: int, claim_id: str) -> str:
    location = FILE_LOCATIONS[path.name]
    if path.name != "07_discussion.tex" or claim_id != "E01":
        return location

    correction_start = text.find(r"\subsection{Evidence correction")
    if correction_start < 0:
        return location
    correction_end = text.find(r"\subsection", correction_start + 1)
    if correction_start <= offset and (
        correction_end < 0 or offset < correction_end
    ):
        return "discussion_evidence_correction"
    return location


def main() -> int:
    claims = {str(item["claim_id"]): item for item in claim_entries()}
    errors: list[str] = []
    all_annotations: set[str] = set()
    all_text = ""
    for root in TEX_ROOTS:
        for path in sorted(root.glob("*.tex")):
            text = path.read_text(encoding="utf-8")
            all_text += "\n" + text
            occurrences = annotation_occurrences(text)
            ids = {claim_id for claim_id, _ in occurrences}
            all_annotations |= ids
            unknown = sorted(ids - claims.keys())
            if unknown:
                errors.append(f"{path.name}: unknown claim IDs {', '.join(unknown)}")
            for claim_id, offset in occurrences:
                if claim_id not in claims:
                    continue
                location = annotation_location(path, text, offset, claim_id)
                allowed = set(claims[claim_id].get("allowed_sections", []))
                if location not in allowed:
                    errors.append(
                        f"{path.name}: {claim_id} is not allowed in {location}"
                    )
            lowered = text.lower()
            for phrase in FORBIDDEN_PHRASES:
                if phrase in lowered:
                    errors.append(f"{path.name}: forbidden claim phrase {phrase!r}")

    for claim_id, claim in claims.items():
        allowed = set(claim.get("allowed_sections", []))
        for location, flag in (
            ("abstract", "allowed_in_abstract"),
            ("conclusion", "allowed_in_conclusion"),
        ):
            if (location in allowed) != bool(claim.get(flag)):
                errors.append(
                    f"{claim_id}: {flag} disagrees with allowed_sections"
                )

    for path, location_flag in (
        (ABSTRACT, "allowed_in_abstract"),
        (CONCLUSION, "allowed_in_conclusion"),
    ):
        ids = annotations(path.read_text(encoding="utf-8"))
        for claim_id in ids:
            if not bool(claims[claim_id].get(location_flag)):
                errors.append(f"{claim_id} is not allowed in {path.name}")

    sensitive_source = (
        ABSTRACT.read_text(encoding="utf-8")
        + "\n"
        + INTRODUCTION.read_text(encoding="utf-8")
        + "\n"
        + CONCLUSION.read_text(encoding="utf-8")
    )
    sensitive = sensitive_source.lower()
    if "77.38" in sensitive or "\\vthreeexploratoryconfoundedimprovement" in sensitive:
        errors.append("confounded v3 improvement appears in a prohibited section")
    if "E01" in annotations(sensitive_source):
        errors.append("E01 appears in abstract, introduction, or conclusion")

    result_sections = [
        PAPER_ROOT / "sections/06_results.tex",
        PAPER_ROOT / "sections/07_discussion.tex",
    ]
    for path in result_sections:
        text = path.read_text(encoding="utf-8")
        if "% CLAIM:" not in text:
            errors.append(f"{path.name}: no claim annotations")

    unsupported_ids = {
        key
        for key, value in claims.items()
        if value["status"] in {"not_evaluated", "external_blocker"}
    }
    unsupported_verbs = re.compile(r"\b(demonstrate[sd]?|prove[sd]?|establish(?:es|ed)?)\b", re.I)
    for match in re.finditer(r"(?ms)^%\s*CLAIM:\s*([^\n]+)\n(.*?)(?=^%\s*CLAIM:|\Z)", all_text):
        ids = {item.strip() for item in match.group(1).split(",")}
        if ids & unsupported_ids and unsupported_verbs.search(match.group(2)):
            errors.append(
                "unsupported claim block uses demonstrate/prove/establish: "
                + ",".join(sorted(ids & unsupported_ids))
            )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"claim check passed ({len(all_annotations)} annotated claim IDs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
