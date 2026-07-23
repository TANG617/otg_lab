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


def annotations(text: str) -> set[str]:
    found: set[str] = set()
    for group in re.findall(r"(?m)^%\s*CLAIM:\s*([A-Z0-9,\s]+)$", text):
        found.update(item.strip() for item in group.split(",") if item.strip())
    return found


def main() -> int:
    claims = {str(item["claim_id"]): item for item in claim_entries()}
    errors: list[str] = []
    all_annotations: set[str] = set()
    all_text = ""
    for root in TEX_ROOTS:
        for path in sorted(root.glob("*.tex")):
            text = path.read_text(encoding="utf-8")
            all_text += "\n" + text
            ids = annotations(text)
            all_annotations |= ids
            unknown = sorted(ids - claims.keys())
            if unknown:
                errors.append(f"{path.name}: unknown claim IDs {', '.join(unknown)}")
            lowered = text.lower()
            for phrase in FORBIDDEN_PHRASES:
                if phrase in lowered:
                    errors.append(f"{path.name}: forbidden claim phrase {phrase!r}")

    for path, location_flag in (
        (ABSTRACT, "allowed_in_abstract"),
        (CONCLUSION, "allowed_in_conclusion"),
    ):
        ids = annotations(path.read_text(encoding="utf-8"))
        for claim_id in ids:
            if not bool(claims[claim_id].get(location_flag)):
                errors.append(f"{claim_id} is not allowed in {path.name}")

    sensitive = (
        ABSTRACT.read_text(encoding="utf-8")
        + "\n"
        + INTRODUCTION.read_text(encoding="utf-8")
        + "\n"
        + CONCLUSION.read_text(encoding="utf-8")
    ).lower()
    if "77.38" in sensitive or "\\vthreeexploratoryconfoundedimprovement" in sensitive:
        errors.append("confounded v3 improvement appears in a prohibited section")
    if "E01" in annotations(sensitive):
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
