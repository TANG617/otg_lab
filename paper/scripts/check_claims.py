#!/usr/bin/env python3
"""Check manuscript claim annotations and evidence-boundary language."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

PAPER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PAPER_ROOT.parent
CLAIMS_PATH = PAPER_ROOT / "logic/claims.yaml"
ABSTRACT = PAPER_ROOT / "sections/00_abstract.tex"
INTRODUCTION = PAPER_ROOT / "sections/01_introduction.tex"
CONCLUSION = PAPER_ROOT / "sections/08_conclusion.tex"
RESULTS = PAPER_ROOT / "sections/06_results.tex"
DISCUSSION = PAPER_ROOT / "sections/07_discussion.tex"
V4_APPENDIX = PAPER_ROOT / "appendix/F_v4_confirmation_attempt.tex"
ADVERSARIAL_DOCS = (
    PAPER_ROOT / "ADVERSARIAL_REVIEW.md",
    PAPER_ROOT / "ADVERSARIAL_LOGIC_REVIEW.md",
)
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
    "F_v4_confirmation_attempt.tex": "appendix_v4_confirmation_attempt",
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
    "v4 demonstrated",
    "the v4 experiment failed",
    "v4 was inconclusive",
    "v4 is inconclusive",
    "removing deadline_miss restores confirmation",
}

V4_REQUIRED_CLAIM_STATUS = {
    "C14": "confirmed_frozen_scope",
    "C15": "nonconfirmatory_frozen",
    "C16": "confirmed_frozen_scope",
    "C17": "confirmed_frozen_scope",
    "C18": "nonconfirmatory_frozen",
    "E02": "nonconfirmatory_frozen",
}
STALE_V4_PHRASES = (
    "fresh same-follower confirmation remains unavailable",
    "fresh same-follower locked confirmation remains unavailable",
    "fresh pva superiority has not been tested",
    "no v4 experiment is part of this paper",
    "neither a fresh same-follower p/pv/pva test nor a v4 experiment is included",
    "v4 has not been run",
    "v4 was not run",
    "v4 remains unavailable",
)


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

    abstract_text = ABSTRACT.read_text(encoding="utf-8")
    abstract_lower = abstract_text.lower()
    introduction_text = INTRODUCTION.read_text(encoding="utf-8")
    conclusion_text = CONCLUSION.read_text(encoding="utf-8")
    conclusion_lower = conclusion_text.lower()
    manuscript_text = all_text
    manuscript_lower = manuscript_text.lower()
    adversarial_text = "\n".join(
        path.read_text(encoding="utf-8") for path in ADVERSARIAL_DOCS
    )
    stale_scan_text = manuscript_lower + "\n" + adversarial_text.lower()

    for phrase in STALE_V4_PHRASES:
        if phrase in stale_scan_text:
            errors.append(f"stale pre-V4 statement remains: {phrase!r}")

    n03 = claims.get("N03", {})
    if n03.get("requires_v4") is not False:
        errors.append("N03 must set requires_v4=false after the completed V4 attempt")
    if n03.get("status") != "nonconfirmatory_frozen":
        errors.append("N03 must have status nonconfirmatory_frozen")
    for claim_id, expected_status in V4_REQUIRED_CLAIM_STATUS.items():
        claim = claims.get(claim_id)
        if claim is None:
            errors.append(f"required V4 claim is missing: {claim_id}")
        elif claim.get("status") != expected_status:
            errors.append(
                f"{claim_id} must have status {expected_status}, "
                f"not {claim.get('status')!r}"
            )
    c19 = claims.get("C19")
    if c19 is None:
        errors.append("required V4 claim is missing: C19")
    elif c19.get("status") not in {"negative_current", "confirmed_frozen_scope"}:
        errors.append("C19 must be a scoped negative runtime observation")

    exact_v4_tokens = (
        "82.41",
        "82.4123",
        r"\VFourPrimaryRelativeImprovement",
    )
    if any(token.lower() in abstract_lower for token in exact_v4_tokens):
        errors.append("abstract contains the exact V4 effect")
    if not re.search(r"\b(withhold|non[- ]confirmatory|no confirmatory)\b", abstract_lower):
        errors.append("abstract must explicitly withhold the V4 confirmatory claim")
    for term, error in (
        ("same-information", "abstract must disclose the failed V4 validity audit"),
        ("runtime", "abstract must disclose the failed V4 runtime gate"),
        ("hardware", "abstract must retain the V4 hardware limitation"),
        ("real", "abstract must retain the real-data limitation"),
    ):
        if term not in abstract_lower:
            errors.append(error)
    if any(token.lower() in conclusion_lower for token in exact_v4_tokens):
        errors.append("conclusion contains the exact V4 effect")

    metadata = (PAPER_ROOT / "metadata.tex").read_text(encoding="utf-8").lower()
    if "82.41" in metadata or "82.4123" in metadata:
        errors.append("title metadata contains the exact V4 effect")
    contribution_start = introduction_text.lower().find("the paper makes")
    contribution_end = introduction_text.find(r"\end{enumerate}", contribution_start)
    if contribution_start >= 0 and contribution_end >= 0:
        contribution_text = introduction_text[
            contribution_start:contribution_end
        ].lower()
        if any(token.lower() in contribution_text for token in exact_v4_tokens):
            errors.append("contribution list contains the exact V4 effect")

    if re.search(r"\bpva\s+(?:improved|improves)\s+tracking\b", manuscript_lower):
        errors.append("unqualified PVA-improved-tracking claim is prohibited")
    if re.search(r"\bpva\s+(?:increased|improved)\s+lag\b", manuscript_lower):
        errors.append("V4 lag must not be described as increased or improved")
    if re.search(
        r"(?:algorithm|method).{0,80}(?:impossible|cannot|can never).{0,40}100\s*hz",
        manuscript_lower,
        re.DOTALL,
    ):
        errors.append("runtime result is overgeneralized to algorithmic impossibility")
    if re.search(
        r"(?:estimator|predictor)(?:\s+\w+){0,5}\s+(?:information|input)\s+differ",
        manuscript_lower,
    ):
        errors.append("V4 must not claim estimator/predictor information differed")
    if re.search(
        r"\bv5\b.{0,80}\b(?:result|observed|demonstrat|establish|confirm)",
        manuscript_lower,
        re.DOTALL,
    ):
        errors.append("a V5 result is claimed even though no V5 was executed")

    v4_results_text = RESULTS.read_text(encoding="utf-8")
    v4_results = v4_results_text.lower()
    required_result_terms = {
        "observed": "V4 exact-effect result must say observed",
        "non-confirmatory": "V4 exact-effect result must say non-confirmatory",
        "failed": "V4 exact-effect result must disclose a failed gate",
        "deadline_miss": "V4 same-information diagnosis must name deadline_miss",
        "composite": "V4 same-information diagnosis must identify a composite field",
        "invalid_method_identity": "V4 effective classification must be explicit",
    }
    for term, error in required_result_terms.items():
        if term not in v4_results:
            errors.append(error)
    if "validity gate" not in v4_results and "same-information gate" not in v4_results:
        errors.append("V4 exact-effect result must identify the failed validity gate")

    for path in (RESULTS, V4_APPENDIX):
        text = path.read_text(encoding="utf-8")
        effect_blocks = [
            block
            for block in re.split(r"\n\s*\n", text)
            if r"\VFourPrimaryRelativeImprovement" in block
        ]
        if not effect_blocks:
            errors.append(f"{path.name}: no exact V4 primary-effect block")
            continue
        for block in effect_blocks:
            lowered = block.lower()
            if "observed" not in lowered:
                errors.append(
                    f"{path.name}: exact V4 effect lacks an adjacent observed qualifier"
                )
            if not re.search(r"\bnon[- ]confirmatory\b", lowered):
                errors.append(
                    f"{path.name}: exact V4 effect lacks an adjacent "
                    "non-confirmatory qualifier"
                )
            if not re.search(
                r"(?:failed?.{0,30}(?:validity|same-information).{0,20}gate|"
                r"(?:validity|same-information).{0,20}gate.{0,30}failed?)",
                lowered,
                re.DOTALL,
            ):
                errors.append(
                    f"{path.name}: exact V4 effect lacks an adjacent failed-gate "
                    "disclosure"
                )

    v4_appendix_lower = V4_APPENDIX.read_text(encoding="utf-8").lower()
    discussion_lower = DISCUSSION.read_text(encoding="utf-8").lower()
    for term, error in (
        ("harmful", "V4 Results must retain harmful trajectories"),
        ("rapid reversal", "V4 Results must retain rapid-reversal heterogeneity"),
        ("lag noninferiority was not established", "V4 Results must disclose lag failure"),
        ("hard-runtime", "V4 Results must disclose runtime failure"),
        ("deadline_miss", "V4 Results must identify the only differing token"),
    ):
        if term not in v4_results:
            errors.append(error)
    if not all(
        term in v4_results + "\n" + v4_appendix_lower
        for term in ("s5", "incomplete", "unavailable")
    ):
        errors.append("ordinary-Ruckig S5 must remain incomplete and unavailable")
    if not all(
        term in v4_appendix_lower
        for term in ("oracle", "offline", "noncausal", "nondeployable")
    ):
        errors.append("V4 oracle evidence must remain offline/noncausal/nondeployable")
    if not all(
        term in discussion_lower
        for term in ("v5", "new test set", "preregistered audit")
    ):
        errors.append("only a fresh V5 may use a revised preregistered audit")
    if "does not retroactively validate v4" not in discussion_lower:
        errors.append("future V5 audit guidance must not reclassify frozen V4")

    for block in re.split(r"\n\s*\n", manuscript_text):
        lowered_block = block.lower()
        has_v3_effect = (
            "77.38" in lowered_block
            or r"\vthreeexploratoryconfoundedimprovement" in block
        )
        has_v4_effect = any(
            token.lower() in lowered_block for token in exact_v4_tokens
        )
        if has_v3_effect and has_v4_effect:
            errors.append("V3 and V4 effect values are mixed in one prose block")

    protocol_status_path = (
        REPO_ROOT / "results/paper_evidence_v4/protocol_status_v4.json"
    )
    if not protocol_status_path.is_file():
        errors.append("V4 runtime protocol status is missing")
    else:
        protocol_status = json.loads(protocol_status_path.read_text(encoding="utf-8"))
        if protocol_status.get("status") != "failed_test_visible_frozen":
            errors.append("V4 status must remain failed_test_visible_frozen")
        if protocol_status.get("same_test_rerun_permitted") is not False:
            errors.append("V4 same-test rerun permission must remain false")
        if protocol_status.get("primary_result_classification") != "invalid_method_identity":
            errors.append("V4 effective classification must remain invalid_method_identity")
        if protocol_status.get("statistical_classification") != "strongly_material":
            errors.append("V4 statistical classification must remain strongly_material")

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
