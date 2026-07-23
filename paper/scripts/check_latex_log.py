#!/usr/bin/env python3
"""Fail on unresolved references, citations, labels, figures, or TeX errors."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PAPER_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", nargs="?", default="build/main.log")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    path = PAPER_ROOT / args.log
    text = path.read_text(encoding="utf-8", errors="replace")
    checks = {
        "undefined_references": len(
            re.findall(r"(?:LaTeX Warning: Reference .* undefined|There were undefined references)", text)
        ),
        "undefined_citations": len(
            re.findall(r"(?:Citation .* undefined|There were undefined citations)", text)
        ),
        "duplicate_labels": len(re.findall(r"multiply defined", text)),
        "missing_files": len(re.findall(r"(?:File .* not found|LaTeX Error: File)", text)),
        "overfull_boxes": len(re.findall(r"Overfull \\[hv]box", text)),
        "latex_errors": len(re.findall(r"(?m)^!", text)),
    }
    fatal = sum(
        checks[key]
        for key in (
            "undefined_references",
            "undefined_citations",
            "duplicate_labels",
            "missing_files",
            "latex_errors",
        )
    )
    result = {"ok": fatal == 0, **checks}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("LaTeX log QA:", ", ".join(f"{k}={v}" for k, v in checks.items()))
    if fatal:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
