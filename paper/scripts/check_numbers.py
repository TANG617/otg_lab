#!/usr/bin/env python3
"""Reject hand-copied empirical result values in manuscript prose."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = PAPER_ROOT / "generated/manifests/number_provenance.json"
REPO_ROOT = PAPER_ROOT.parent
REQUIRED_V4_MACROS = {
    "VFourTestTrajectoryCount",
    "VFourPrimarySampleCountPerMethod",
    "VFourPrimaryPairedTrajectoryCount",
    "VFourPrimaryRelativeImprovement",
    "VFourPrimaryRelativeCILow",
    "VFourPrimaryRelativeCIHigh",
    "VFourPrimaryAbsoluteImprovement",
    "VFourPrimaryAbsoluteCILow",
    "VFourPrimaryAbsoluteCIHigh",
    "VFourBootstrapResampleCount",
    "VFourHarmfulCount",
    "VFourHarmfulDenominator",
    "VFourSameInformationFailureCount",
    "VFourSameInformationAuditCycleCount",
    "VFourSameInformationFailurePercent",
    "VFourPMeanLagMS",
    "VFourPVAMeanLagMS",
    "VFourPRuntimePNinetyNineUS",
    "VFourPVRuntimePNinetyNineUS",
    "VFourPVARuntimePNinetyNineUS",
    "VFourPDeadlineMissCount",
    "VFourPVDeadlineMissCount",
    "VFourPVADeadlineMissCount",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    payload = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    errors: list[str] = []
    missing_v4_macros = sorted(REQUIRED_V4_MACROS - payload["macros"].keys())
    if missing_v4_macros:
        errors.append("missing required V4 macros: " + ", ".join(missing_v4_macros))

    for macro, item in payload["macros"].items():
        for field in ("row_selector", "field_selector", "rounding_rule"):
            if not str(item.get(field, "")).strip():
                errors.append(f"{macro}: missing {field}")
        sources = item.get("sources", [])
        if not sources:
            errors.append(f"{macro}: missing source path/hash")
            continue
        for source in sources:
            if not source.get("path") or not source.get("sha256"):
                errors.append(f"{macro}: incomplete source path/hash")
                continue
            path = REPO_ROOT / source["path"]
            if not path.is_file():
                errors.append(f"{macro}: source missing: {source['path']}")
            elif digest(path) != source["sha256"]:
                errors.append(f"{macro}: source hash mismatch: {source['path']}")
        if macro.startswith("VFour") and not all(
            str(source_id).startswith("E_V4_")
            for source_id in item.get("source_ids", [])
        ):
            errors.append(f"{macro}: V4 macro lacks an E_V4_* source id")

    protected: dict[str, str] = {}
    for macro, item in payload["macros"].items():
        value = str(item["formatted_value"])
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value) and len(value) >= 4:
            protected[value] = macro

    for root in (PAPER_ROOT / "sections", PAPER_ROOT / "appendix"):
        for path in root.glob("*.tex"):
            text = re.sub(r"(?m)^%.*$", "", path.read_text(encoding="utf-8"))
            for value, macro in protected.items():
                if re.search(rf"(?<![\d.]){re.escape(value)}(?![\d.])", text):
                    errors.append(
                        f"{path.name}: hardcoded generated value {value}; use \\{macro}"
                    )
            for forbidden, macro in (
                (r"(?<![\d.])82\.41(?:23)?(?!\d)", "VFourPrimaryRelativeImprovement"),
                (
                    r"(?<![\d,])(?:42,072|42072)(?!\d)",
                    "VFourSameInformationAuditCycleCount",
                ),
            ):
                if re.search(forbidden, text):
                    errors.append(
                        f"{path.name}: hardcoded protected V4 result; use \\{macro}"
                    )
    for path in (PAPER_ROOT / "sections").glob("*.tex"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?<!\d)77\.38(?!\d)", text):
            errors.append(f"{path.name}: hardcoded confounded 77.38 value")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"number check passed ({len(protected)} protected generated values)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
