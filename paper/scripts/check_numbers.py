#!/usr/bin/env python3
"""Reject hand-copied empirical result values in manuscript prose."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PAPER_ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = PAPER_ROOT / "generated/manifests/number_provenance.json"


def main() -> int:
    payload = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    protected: dict[str, str] = {}
    for macro, item in payload["macros"].items():
        value = str(item["formatted_value"])
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value) and len(value) >= 4:
            protected[value] = macro

    errors: list[str] = []
    for root in (PAPER_ROOT / "sections", PAPER_ROOT / "appendix"):
        for path in root.glob("*.tex"):
            text = re.sub(r"(?m)^%.*$", "", path.read_text(encoding="utf-8"))
            for value, macro in protected.items():
                if re.search(rf"(?<![\d.]){re.escape(value)}(?![\d.])", text):
                    errors.append(
                        f"{path.name}: hardcoded generated value {value}; use \\{macro}"
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
