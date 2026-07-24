#!/usr/bin/env python3
"""Perform bounded static QA on natbib citations and BibTeX metadata."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]
BIB = PAPER_ROOT / "references.bib"


def main() -> int:
    errors: list[str] = []
    bib_text = BIB.read_text(encoding="utf-8")
    if "CITATION_NEEDED" in bib_text:
        errors.append("CITATION_NEEDED remains in references.bib")
    entries = list(
        re.finditer(
            r"(?ms)@\w+\s*\{\s*([^,\s]+)\s*,(.*?)(?=^\s*@|\Z)", bib_text
        )
    )
    keys = [match.group(1) for match in entries]
    duplicate_keys = [key for key, count in Counter(keys).items() if count > 1]
    if duplicate_keys:
        errors.append("duplicate BibTeX keys: " + ", ".join(duplicate_keys))

    titles: list[str] = []
    dois: list[str] = []
    for match in entries:
        key, body = match.group(1), match.group(2)
        fields = {
            name.lower(): value.strip()
            for name, value in re.findall(
                r"(?ms)(\w+)\s*=\s*[{\"](.+?)[}\"]\s*,?", body
            )
        }
        for required in ("title", "author", "year"):
            if required not in fields:
                errors.append(f"{key}: missing {required}")
        if "title" in fields:
            titles.append(re.sub(r"[^a-z0-9]+", "", fields["title"].lower()))
        if "doi" in fields:
            doi = fields["doi"].lower().replace("https://doi.org/", "")
            if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
                errors.append(f"{key}: malformed DOI {fields['doi']!r}")
            dois.append(doi)
        if not ({"doi", "url", "eprint"} & fields.keys()):
            errors.append(f"{key}: no DOI, URL, or eprint identifier")

    for label, values in (("title", titles), ("DOI", dois)):
        duplicates = [value for value, count in Counter(values).items() if count > 1]
        if duplicates:
            errors.append(f"duplicate {label}: " + ", ".join(duplicates))

    cited: set[str] = set()
    for root in (PAPER_ROOT / "sections", PAPER_ROOT / "appendix"):
        for path in root.glob("*.tex"):
            text = path.read_text(encoding="utf-8")
            if "CITATION_NEEDED" in text:
                errors.append(f"{path.name}: CITATION_NEEDED remains")
            for group in re.findall(r"\\cite\w*\{([^}]+)\}", text):
                cited.update(item.strip() for item in group.split(","))
    unknown = sorted(cited - set(keys))
    if unknown:
        errors.append("unknown citation keys: " + ", ".join(unknown))
    uncited = sorted(set(keys) - cited)
    if uncited:
        errors.append("uncited bibliography entries: " + ", ".join(uncited))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"citation check passed ({len(keys)} entries, {len(cited)} cited)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
