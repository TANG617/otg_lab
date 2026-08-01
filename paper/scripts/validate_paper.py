#!/usr/bin/env python3
"""Validate scientific coverage, generated artifacts, and release gates."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

PAPER_ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise ValueError(message)


def validate_sources(profile: str) -> dict:
    manifest_path = PAPER_ROOT / "evidence" / "frozen" / profile / "artifact_manifest.json"
    if not manifest_path.is_file():
        fail(f"missing frozen evidence manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    claims = manifest.get("claims", {})
    if set(claims) != {f"C{index}" for index in range(1, 14)}:
        fail("frozen evidence does not map exactly C1--C13")
    for source in manifest["sources"]:
        for item in source["files"]:
            if not (PAPER_ROOT / item["frozen"]).is_file():
                fail(f"missing frozen file: {item['frozen']}")
    return manifest


def _strip_tex(text: str) -> str:
    text = re.sub(r"%.*", " ", text)
    text = re.sub(r"\\(?:cite|cref|ref|label|claimtag|input|includegraphics)(?:\[[^]]*\])?\{[^}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?", " ", text)
    text = re.sub(r"[{}$&_^~\\]", " ", text)
    return text


def validate_manuscript() -> None:
    section_paths = sorted((PAPER_ROOT / "sections").glob("[0-9][0-9]_*.tex"))
    if len(section_paths) != 11:
        fail(f"expected 11 numbered sections, found {len(section_paths)}")
    body = "\n".join(path.read_text(encoding="utf-8") for path in section_paths)
    for claim in [f"C{index}" for index in range(1, 14)]:
        if f"\\claimtag{{{claim}}}" not in body:
            fail(f"claim {claim} is not mapped in the manuscript")
    required_terms = [
        "terminal-state mismatch",
        "target-state contract",
        "stop-and-go",
        "matched velocity target",
        "observed waveform lag",
        "best tested",
        "recorded case study",
        "within the tested envelope",
    ]
    compiled_text = (PAPER_ROOT / "macros.tex").read_text(encoding="utf-8") + body
    for term in required_terms:
        if term not in compiled_text:
            fail(f"required controlled term is absent: {term}")
    for token in ["TODO", "FIXME", "TBD", "INSERT CITATION", "lorem ipsum"]:
        if token.lower() in body.lower() or token.lower() in (PAPER_ROOT / "main.tex").read_text(encoding="utf-8").lower():
            fail(f"forbidden placeholder token: {token}")

    words = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", _strip_tex(body))
    if not 7500 <= len(words) <= 9000:
        fail(f"main-text word count outside 7500--9000: {len(words)}")
    main = (PAPER_ROOT / "main.tex").read_text(encoding="utf-8")
    abstract_match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", main, re.S)
    if abstract_match is None:
        fail("abstract not found")
    abstract_words = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", _strip_tex(abstract_match.group(1)))
    if not 180 <= len(abstract_words) <= 220:
        fail(f"abstract word count outside 180--220: {len(abstract_words)}")

    figures = sorted((PAPER_ROOT / "figures" / "generated").glob("fig*.pdf"))
    tables = sorted((PAPER_ROOT / "tables" / "generated").glob("table*.tex"))
    if len(figures) != 6 or len(tables) != 4:
        fail(f"expected six figures and four tables; got {len(figures)} and {len(tables)}")

    bib = (PAPER_ROOT / "references.bib").read_text(encoding="utf-8")
    bib_keys = set(re.findall(r"@[A-Za-z]+\{([^,]+),", bib))
    if not 25 <= len(bib_keys) <= 40:
        fail(f"bibliography must contain 25--40 primary entries; found {len(bib_keys)}")
    cited = set()
    all_tex = main + body
    for group in re.findall(r"\\cite[a-zA-Z]*\{([^}]+)\}", all_tex):
        cited.update(item.strip() for item in group.split(","))
    missing = sorted(cited - bib_keys)
    if missing:
        fail(f"citation keys missing from bibliography: {missing}")


def validate_generated() -> None:
    summary_path = PAPER_ROOT / "generated" / "artifact_summary.json"
    numbers_path = PAPER_ROOT / "generated" / "numbers.tex"
    if not summary_path.is_file() or not numbers_path.is_file():
        fail("generated numbers or summary is missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    values = summary["values"]
    exact = {
        "e15_grid": 2144,
        "e15_sobol": 128,
        "e15_seam": 16,
        "e16_arms": 1260,
        "e17_work_conditions": 11,
        "e17_stress_conditions": 6,
        "e17_work_pairs": 1320,
        "e17_stress_pairs": 720,
        "e17_synthetic_count": 20,
        "deadline_miss_count": 1,
        "recorded_zero_target_count": 2,
    }
    for key, expected in exact.items():
        if values.get(key) != expected:
            fail(f"generated assertion failed for {key}: {values.get(key)} != {expected}")
    if not values["e17_stress_median"] < 0.5 or not values["e17_stress_minimum"] < 0:
        fail("E17 position-noise stress boundary is missing")
    if not values["recorded_min_nonzero_target"] > 1e-10:
        fail("recorded deadband equivalence audit failed")


def validate_pdf(pdf: Path) -> None:
    if not pdf.is_file():
        fail(f"PDF not found: {pdf}")
    info = subprocess.run(["pdfinfo", str(pdf)], check=True, capture_output=True, text=True).stdout
    page_match = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if page_match is None or int(page_match.group(1)) < 10:
        fail("compiled PDF has an implausible page count")
    fonts = subprocess.run(["pdffonts", str(pdf)], check=True, capture_output=True, text=True).stdout
    font_rows = [line for line in fonts.splitlines()[2:] if line.strip()]
    embedding = []
    for line in font_rows:
        match = re.search(r"\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$", line, re.I)
        if match is None:
            fail(f"could not parse pdffonts row: {line}")
        embedding.append(match.group(1).lower())
    if not font_rows or any(value != "yes" for value in embedding):
        fail("PDF contains a non-embedded font")
    text = subprocess.run(["pdftotext", str(pdf), "-"], check=True, capture_output=True, text=True).stdout
    for token in ["PROVISIONAL EVIDENCE DRAFT", "position noise", "irregular-timestamp", "References"]:
        if token not in text:
            fail(f"expected PDF text is absent: {token}")


def validate_release(profile: str, manifest: dict) -> None:
    if profile != "release":
        fail("formal release requires the release evidence profile")
    if not manifest.get("release_ready") or not manifest.get("generated_from_clean_git"):
        fail("release evidence is not clean and ready")
    if any(source.get("git_dirty") for source in manifest["sources"]):
        fail("release evidence contains dirty git provenance")
    metadata = PAPER_ROOT / "metadata.tex"
    if not metadata.is_file():
        fail("release metadata.tex is missing")
    content = metadata.read_text(encoding="utf-8")
    if "\\paperdraftfalse" not in content:
        fail("release metadata must disable draft mode")
    for placeholder in ["example.org", "Full Author Name", "withheld", "required for release"]:
        if placeholder.lower() in content.lower():
            fail(f"release metadata contains placeholder: {placeholder}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="provisional")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    manifest = validate_sources(args.profile)
    validate_generated()
    validate_manuscript()
    if args.pdf is not None:
        validate_pdf(args.pdf.resolve())
    if args.release:
        validate_release(args.profile, manifest)
    print("paper validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
