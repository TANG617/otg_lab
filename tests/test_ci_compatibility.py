from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "environments" / "compatibility-results"
SMOKE_SPEC = importlib.util.spec_from_file_location(
    "otg_ci_smoke", ROOT / "scripts" / "run_ci_smoke.py"
)
if SMOKE_SPEC is None or SMOKE_SPEC.loader is None:
    raise RuntimeError("could not load scripts/run_ci_smoke.py")
SMOKE_MODULE = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(SMOKE_MODULE)
run_smoke = SMOKE_MODULE.run_smoke


def _load(name: str) -> dict:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def test_recorded_ruckig_probes_match_except_for_version() -> None:
    legacy = _load("ruckig-0.17.3.json")
    community = _load("ruckig-0.19.4.json")
    assert legacy.pop("ruckig_version") == "0.17.3"
    assert community.pop("ruckig_version") == "0.19.4"
    assert legacy == community


def test_compatibility_report_hashes_raw_probe_files() -> None:
    report = _load("comparison.json")
    for key, filename in (
        ("baseline", "ruckig-0.17.3.json"),
        ("candidate", "ruckig-0.19.4.json"),
    ):
        digest = hashlib.sha256((RESULTS / filename).read_bytes()).hexdigest()
        assert report[key]["source_file_sha256"] == digest
    assert all(report["comparison"].values())
    assert report["negative_results"]


def test_dependency_and_ci_contracts_are_explicit() -> None:
    legacy = (ROOT / "environments" / "legacy-requirements.lock.txt").read_text()
    community = (ROOT / "environments" / "community-requirements.lock.txt").read_text()
    ruckig_source = (ROOT / "environments" / "ruckig-source.lock.txt").read_text()
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    build_requirements = (
        ROOT / "environments" / "build-requirements.lock.txt"
    ).read_text()
    assert "ruckig==0.17.3" in legacy
    assert "ruckig==0.19.4" in community
    assert "--hash=sha256:" in legacy and "--hash=sha256:" in community
    assert "scikit-build-core==0.9.10" in build_requirements
    assert "ruckig==0.17.3" in ruckig_source
    assert "--hash=sha256:" in ruckig_source
    for required in (
        'python-version: "3.9"',
        "fetch-depth: 0",
        "environments/build-requirements.lock.txt",
        "environments/ruckig-source.lock.txt",
        "--require-hashes",
        "uv sync --extra dev --frozen",
        "uv run --frozen ruff check .",
        "uv run --frozen python -m pytest -q",
        "scripts/run_ci_smoke.py",
    ):
        assert required in workflow


def test_minimal_end_to_end_smoke_is_causal_and_constrained() -> None:
    result = run_smoke()
    assert result["trajectory"]["row_count"] > 1
    checks = result["checks"]
    assert checks["causality_violation_count"] == 0
    assert checks["constraint_violation_count"] == 0
    assert len(result["command_trace_sha256"]) == 64
