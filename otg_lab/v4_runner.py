"""Fail-closed execution coordinator for the one-shot V4 experiment.

The module intentionally imports trajectory and pipeline code only inside
execution functions.  In particular, ``report_only_resume`` cannot import or
call any experiment-stage implementation.

Git authorization avoids an impossible self-hash: ``config_lock_v4.json``
precommits an immutable ``refs/tags/...`` name.  After the final lock commit is
created that ref is made to point at it.  Confirmation requires the ref to
resolve to the current 40-character HEAD and separately verifies every locked
file hash.  The lock therefore never pretends to contain the hash of the commit
which contains the lock itself.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib
import json
import os
import platform
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config_lock_v4.json"
STATUS_PATH = ROOT / "protocol_status_v4.json"
MANIFEST_PATH = ROOT / "split_manifest_v4.json"
METHOD_MATRIX_PATH = ROOT / "V4_METHOD_MATRIX.json"
STATISTICAL_DESIGN_PATH = ROOT / "V4_STATISTICAL_DESIGN.json"
VALIDATION_CONFIG_PATH = ROOT / "configs/v4_validation.yaml"
LOCKED_CONFIG_PATH = ROOT / "configs/v4_locked_test.yaml"
ORACLE_CONFIG_PATH = ROOT / "configs/v4_oracle_diagnostic.yaml"

DEVELOPMENT_ROOT = ROOT / "results/paper_evidence_v4_development"
RESULTS_ROOT = ROOT / "results/paper_evidence_v4"
RELEASE_ROOT = ROOT / "results/paper_evidence_v4_release"
VALIDATION_ROOT = RESULTS_ROOT / "raw_runs/validation"
VALIDATION_ORACLE_ROOT = RESULTS_ROOT / "raw_runs/validation_oracle_diagnostic"
LOCKED_TEST_ROOT = RESULTS_ROOT / "raw_runs/locked_test"
ORACLE_ROOT = RESULTS_ROOT / "raw_runs/oracle_diagnostic"
RUNTIME_STATUS_PATH = RELEASE_ROOT / "protocol_status_v4_runtime.json"
TEST_VISIBLE_SENTINEL = RELEASE_ROOT / ".v4_test_visible"
RESULT_STATUS_PATH = RESULTS_ROOT / "protocol_status_v4.json"

CONFIRMATION_HEAD_REF = "refs/tags/paper-evidence-v4-confirmation-source"
HISTORICAL_MANIFESTS = (
    ROOT / "split_manifest.json",
    ROOT / "split_manifest_v2.json",
    ROOT / "split_manifest_v3.json",
)
V3_IMMUTABLE_PATHS = (
    ROOT / "config_lock_v3.json",
    ROOT / "EXPERIMENT_PROTOCOL_V3.md",
    ROOT / "V3_POSTREVIEW_ADDENDUM.md",
    ROOT / "protocol_status_v3.json",
    ROOT / "protocol_status_v3_postreview.json",
    ROOT / "results/paper_evidence_v3/artifact_index.json",
)
FORMAL_CONFIGS = (
    VALIDATION_CONFIG_PATH,
    LOCKED_CONFIG_PATH,
    ORACLE_CONFIG_PATH,
)
_THREAD_ENVIRONMENT = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


class V4RunnerError(RuntimeError):
    """Base V4 execution error."""


class V4PreflightError(V4RunnerError):
    """A formal precondition was not met."""


class ConfirmationCapabilityError(V4RunnerError):
    """A test-consuming helper was called outside ``confirm``."""


class ReportOnlyError(V4RunnerError):
    """The constrained report-only path was not admissible."""


class _ConfirmationCapability:
    __slots__ = ("_nonce",)

    def __init__(self, nonce: object) -> None:
        if nonce is not _CAPABILITY_CONSTRUCTOR_NONCE:
            raise ConfirmationCapabilityError("capability may only be created by confirm")
        self._nonce = object()

    def __reduce__(self) -> Any:
        raise TypeError("V4 confirmation capability is intentionally non-serializable")


_CAPABILITY_CONSTRUCTOR_NONCE = object()
_ACTIVE_CONFIRMATION_CAPABILITY: _ConfirmationCapability | None = None


def _require_capability(capability: object) -> None:
    if (
        _ACTIVE_CONFIRMATION_CAPABILITY is None
        or capability is not _ACTIVE_CONFIRMATION_CAPABILITY
    ):
        raise ConfirmationCapabilityError(
            "V4 test data are accessible only inside the formal confirm command"
        )


def _run_git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def _head() -> str:
    value = _run_git("rev-parse", "HEAD")
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise V4PreflightError("git HEAD did not resolve to a 40-character commit")
    return value


def _require_clean() -> None:
    if _run_git("status", "--porcelain=v1", "--untracked-files=all"):
        raise V4PreflightError("formal V4 operation requires a clean worktree")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise V4PreflightError(f"cannot read {path.relative_to(ROOT)}: {error}") from error
    if not isinstance(value, dict):
        raise V4PreflightError(f"{path.relative_to(ROOT)} must contain an object")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise V4PreflightError(f"{path.relative_to(ROOT)} must contain a mapping")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _enforce_runtime_policy() -> dict[str, Any]:
    for name in _THREAD_ENVIRONMENT:
        os.environ[name] = "1"
    affinity_count: int | None = None
    affinity_policy = "unavailable_on_platform"
    if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        available = sorted(os.sched_getaffinity(0))
        if not available:
            raise V4PreflightError("CPU affinity API returned an empty CPU set")
        os.sched_setaffinity(0, {available[0]})
        affinity_count = len(os.sched_getaffinity(0))
        if affinity_count != 1:
            raise V4PreflightError("failed to pin the formal V4 process to one CPU")
        affinity_policy = "single_cpu_when_supported"
    return {
        "thread_count": 1,
        "thread_environment": {name: os.environ[name] for name in _THREAD_ENVIRONMENT},
        "cpu_affinity_policy": affinity_policy,
        "cpu_affinity_count": affinity_count,
    }


def _capture_environment() -> dict[str, Any]:
    from importlib import metadata

    runtime = _enforce_runtime_policy()
    numpy = importlib.import_module("numpy")
    blas_config = getattr(numpy.__config__, "CONFIG", {})
    try:
        blas = json.loads(json.dumps(blas_config, sort_keys=True, default=str))
    except (TypeError, ValueError):
        blas = {"description": str(blas_config)}
    try:
        cpu = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        cpu = platform.processor() or platform.machine()
    packages = {}
    for distribution in ("ruckig", "numpy", "scipy", "pandas", "pyarrow", "osqp"):
        try:
            packages[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError as error:
            raise V4PreflightError(
                f"required formal dependency is not installed: {distribution}"
            ) from error
    return {
        "capture_status": "complete_after_validation",
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "packages": packages,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "cpu": cpu,
        "blas": blas,
        **runtime,
    }


def _verify_environment(lock: Mapping[str, Any]) -> dict[str, Any]:
    observed = _capture_environment()
    if lock.get("environment") != observed:
        raise V4PreflightError("formal runtime environment differs from config lock")
    return observed


def _canonical_selection(lock: Mapping[str, Any]) -> Mapping[str, Any]:
    selection = lock.get("locked_selection")
    if not isinstance(selection, Mapping):
        raise V4PreflightError("config lock lacks locked_selection")
    for path in FORMAL_CONFIGS:
        config_selection = _load_yaml(path).get("locked_selection")
        if config_selection != selection:
            raise V4PreflightError(
                f"canonical locked_selection differs in {path.relative_to(ROOT)}"
            )
    return selection


def _verify_hashes(lock: Mapping[str, Any]) -> None:
    locked = lock.get("confirmation_file_hashes")
    if not isinstance(locked, Mapping) or not locked:
        raise V4PreflightError("lock lacks non-empty confirmation_file_hashes")
    for relative, expected in locked.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise V4PreflightError("locked hashes must map paths to SHA-256 strings")
        path = ROOT / relative
        if not path.is_file() or _sha256(path) != expected:
            raise V4PreflightError(f"locked hash mismatch: {relative}")

    v3 = lock.get("v3_immutable_hashes")
    if not isinstance(v3, Mapping):
        raise V4PreflightError("lock lacks V3 immutable hashes")
    for path in V3_IMMUTABLE_PATHS:
        relative = path.relative_to(ROOT).as_posix()
        if v3.get(relative) != _sha256(path):
            raise V4PreflightError(f"frozen V3 evidence changed: {relative}")
    redundant_mappings = (
        lock.get("formal_configs"),
        lock.get("design_material_observed_hashes"),
        lock.get("historical_manifest_hashes"),
        lock.get("workflow_hashes"),
        lock.get("implementation_hash_coverage", {}).get("hashes"),
    )
    for mapping in redundant_mappings:
        if not isinstance(mapping, Mapping) or not mapping:
            raise V4PreflightError("config lock contains an incomplete hash section")
        for relative, expected in mapping.items():
            path = ROOT / str(relative)
            if not path.is_file() or expected != _sha256(path):
                raise V4PreflightError(f"redundant lock hash mismatch: {relative}")
    if lock.get("method_matrix", {}).get("sha256") != _sha256(METHOD_MATRIX_PATH):
        raise V4PreflightError("method-matrix lock hash is inconsistent")
    if lock.get("dataset", {}).get("manifest_sha256") != _sha256(MANIFEST_PATH):
        raise V4PreflightError("manifest lock hash is inconsistent")
    if lock.get("dataset", {}).get("generator_sha256") != _sha256(
        ROOT / "scripts/generate_split_manifest_v4.py"
    ):
        raise V4PreflightError("manifest-generator lock hash is inconsistent")
    entrypoints = lock.get("entrypoints", {})
    authoritative = entrypoints.get("authoritative_runner", {})
    if (
        authoritative.get("path") != "run_paper_evidence_v4.py"
        or authoritative.get("sha256")
        != _sha256(ROOT / "run_paper_evidence_v4.py")
        or authoritative.get("status") != "complete"
    ):
        raise V4PreflightError("authoritative V4 runner declaration is inconsistent")


def _verify_authorizing_ref(lock: Mapping[str, Any]) -> str:
    git_lock = lock.get("git")
    if not isinstance(git_lock, Mapping):
        raise V4PreflightError("config lock lacks git authorization")
    reference = git_lock.get("confirmation_head_ref")
    if reference != CONFIRMATION_HEAD_REF or not str(reference).startswith("refs/tags/"):
        raise V4PreflightError("lock must precommit the immutable confirmation tag ref")
    try:
        authorized = _run_git("rev-parse", "--verify", f"{reference}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise V4PreflightError("confirmation authorization ref does not exist") from error
    if re.fullmatch(r"[0-9a-f]{40}", authorized) is None or authorized != _head():
        raise V4PreflightError("confirmation authorization ref must resolve exactly to HEAD")
    scientific_source = git_lock.get("scientific_source_commit")
    if (
        not isinstance(scientific_source, str)
        or re.fullmatch(r"[0-9a-f]{40}", scientific_source) is None
    ):
        raise V4PreflightError("lock lacks an exact scientific_source_commit")
    parent = _run_git("rev-parse", "HEAD^")
    if parent != scientific_source:
        raise V4PreflightError("authorization HEAD parent differs from scientific source")
    allowed = set(git_lock.get("authorization_commit_allowed_paths", ()))
    if allowed != {"config_lock_v4.json", "protocol_status_v4.json"}:
        raise V4PreflightError("authorization commit path policy is not canonical")
    changed = set(
        filter(
            None,
            _run_git(
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "HEAD^",
                "HEAD",
            ).splitlines(),
        )
    )
    if not changed or "config_lock_v4.json" not in changed or not changed <= allowed:
        raise V4PreflightError(
            f"authorization commit changed non-authorized paths: {sorted(changed)}"
        )
    return authorized


def _verify_no_forbidden_v4_capability() -> None:
    tokens = ("track" + "ig", "track" + "ing", "ruckig_" + "pro", "ruckig " + "pro")
    for path in (METHOD_MATRIX_PATH, *FORMAL_CONFIGS):
        lowered = path.read_text(encoding="utf-8").lower()
        if any(token in lowered for token in tokens):
            raise V4PreflightError(
                f"forbidden capability declaration in {path.relative_to(ROOT)}"
            )
    pending = [ROOT / "otg_lab/v4_methods.py", ROOT / "otg_lab/v4_runner.py"]
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if node.level and not module.startswith("otg_lab"):
                    module = "otg_lab." + module
                imported.append(module.lower())
                imported.extend(alias.name.lower() for alias in node.names)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                imported.append(node.args[0].value.lower())
        import_tokens = (*tokens, "license")
        if any(any(token in name for token in import_tokens) for name in imported):
            raise V4PreflightError(
                f"forbidden V4 implementation import in {path.relative_to(ROOT)}"
            )
        for module in imported:
            if not module.startswith("otg_lab."):
                continue
            relative = module.removeprefix("otg_lab.").replace(".", "/")
            candidate = ROOT / "otg_lab" / f"{relative}.py"
            if candidate.is_file() and candidate not in visited:
                pending.append(candidate)


def _verify_no_prior_test_state() -> None:
    candidates = (
        LOCKED_TEST_ROOT,
        ORACLE_ROOT,
        RUNTIME_STATUS_PATH,
        TEST_VISIBLE_SENTINEL,
        RELEASE_ROOT,
        ROOT / "runs/paper_evidence_v4/locked_test",
        ROOT / "runs/paper_evidence_v4/test",
        ROOT / ".cache/paper_evidence_v4",
    )
    existing = [
        (
            path.relative_to(ROOT).as_posix()
            if path.is_relative_to(ROOT)
            else str(path)
        )
        for path in candidates
        if path.exists()
    ]
    if RESULTS_ROOT.exists():
        for path in RESULTS_ROOT.rglob("*"):
            relative = path.relative_to(RESULTS_ROOT)
            parts = relative.parts
            allowed_validation = (
                parts == ("raw_runs",)
                or (
                    len(parts) >= 2
                    and parts[0] == "raw_runs"
                    and parts[1]
                    in {"validation", "validation_oracle_diagnostic"}
                )
            )
            if not allowed_validation:
                existing.append(path.relative_to(ROOT).as_posix())
    for cache_root in (ROOT / "runs", ROOT / ".cache"):
        if cache_root.exists():
            existing.extend(
                path.relative_to(ROOT).as_posix()
                for path in cache_root.rglob("*")
                if "paper_evidence_v4" in path.as_posix().lower()
            )
    results_parent = ROOT / "results"
    if results_parent.exists():
        existing.extend(
            path.relative_to(ROOT).as_posix()
            for path in results_parent.glob("paper-evidence-v4-*")
        )
    existing = sorted(set(existing))
    if existing:
        raise V4PreflightError(f"prior V4 test output/cache blocks confirmation: {existing}")


def _validate_pretest_evidence(*, require_clean: bool) -> dict[str, Any]:
    artifacts = importlib.import_module("otg_lab.v4_artifacts")
    validation = artifacts.validate_raw_bundle(
        VALIDATION_ROOT,
        expected_commit=None,
        bundle_kind="validation",
        require_clean=require_clean,
    )
    validation_oracle = artifacts.validate_raw_bundle(
        VALIDATION_ORACLE_ROOT,
        expected_commit=None,
        bundle_kind="oracle_diagnostic",
        require_clean=require_clean,
    )
    v3_immutability = artifacts.check_v3_immutability(ROOT)
    return {
        "validation": validation,
        "validation_oracle": validation_oracle,
        "v3_immutability": v3_immutability,
    }


def _validate_development_evidence() -> dict[str, Any]:
    artifacts = importlib.import_module("otg_lab.v4_artifacts")
    return {
        "estimated": artifacts.validate_raw_bundle(
            DEVELOPMENT_ROOT / "estimated",
            bundle_kind="validation",
            require_clean=False,
        ),
        "oracle": artifacts.validate_raw_bundle(
            DEVELOPMENT_ROOT / "oracle_diagnostic",
            bundle_kind="oracle_diagnostic",
            require_clean=False,
        ),
    }


def _validate_independent_statistics(artifacts: Any) -> Mapping[str, Any]:
    report = artifacts.validate_statistical_artifacts(
        RESULTS_ROOT,
        raw_metrics_path=LOCKED_TEST_ROOT / "metrics_by_trajectory.csv",
        manifest_path=MANIFEST_PATH,
        statistical_design_path=STATISTICAL_DESIGN_PATH,
    )
    if (
        report.get("all_independent_statistical_recomputations_verified")
        is not True
    ):
        raise V4RunnerError("independent V4 statistical recomputation failed")
    return report


def _verify_post_test_immutability(expected_head: str) -> dict[str, Any]:
    """Freeze on any tracked-code/config/V3 drift after test visibility."""

    _require_clean()
    if _head() != expected_head:
        raise V4PreflightError("HEAD changed after V4 test visibility")
    lock = _load_json(LOCK_PATH)
    if _verify_authorizing_ref(lock) != expected_head:
        raise V4PreflightError("confirmation authorization changed after test visibility")
    _verify_hashes(lock)
    _canonical_selection(lock)
    _verify_environment(lock)
    _verify_no_forbidden_v4_capability()
    artifacts = importlib.import_module("otg_lab.v4_artifacts")
    return artifacts.check_v3_immutability(ROOT)


def _verify_report_only_code_state(raw_commit: str) -> dict[str, Any]:
    _require_clean()
    reporting_commit = _head()
    allowed_reporting_changes = {
        "otg_lab/v4_artifacts.py",
        "otg_lab/v4_contextual.py",
        "otg_lab/v4_handoff.py",
    }
    changed: set[str] = set()
    if reporting_commit != raw_commit:
        try:
            _run_git("merge-base", "--is-ancestor", raw_commit, reporting_commit)
        except subprocess.CalledProcessError as error:
            raise ReportOnlyError("reporting commit is not a descendant of raw commit") from error
        changed = set(
            filter(
                None,
                _run_git(
                    "diff",
                    "--name-only",
                    f"{raw_commit}..{reporting_commit}",
                ).splitlines(),
            )
        )
        if not changed or not changed <= allowed_reporting_changes:
            raise ReportOnlyError(
                f"report-only commit contains non-reporting changes: {sorted(changed)}"
            )
    lock = _load_json(LOCK_PATH)
    git_lock = lock.get("git", {})
    reference = git_lock.get("confirmation_head_ref")
    if reference != CONFIRMATION_HEAD_REF:
        raise ReportOnlyError("report-only lock has the wrong confirmation ref")
    if _run_git("rev-parse", "--verify", f"{reference}^{{commit}}") != raw_commit:
        raise ReportOnlyError("confirmation ref no longer resolves to raw commit")
    if _run_git("rev-parse", f"{raw_commit}^") != git_lock.get(
        "scientific_source_commit"
    ):
        raise ReportOnlyError("raw commit parent is not the locked scientific source")
    _canonical_selection(lock)
    _verify_environment(lock)
    _verify_no_forbidden_v4_capability()
    hashes = lock.get("confirmation_file_hashes", {})
    if not isinstance(hashes, Mapping) or not hashes:
        raise ReportOnlyError("config lock lacks confirmation hashes")
    for relative, expected in hashes.items():
        if relative in allowed_reporting_changes:
            continue
        path = ROOT / str(relative)
        if not path.is_file() or _sha256(path) != expected:
            raise ReportOnlyError(f"non-reporting locked source changed: {relative}")
    artifacts = importlib.import_module("otg_lab.v4_artifacts")
    v3 = artifacts.check_v3_immutability(ROOT)
    return {
        "reporting_commit": reporting_commit,
        "changed_reporting_paths": sorted(changed),
        "v3_immutability": v3,
    }


def verify_confirmation_preflight() -> dict[str, Any]:
    """Run every non-test formal gate and return its evidence."""

    _require_clean()
    lock = _load_json(LOCK_PATH)
    if lock.get("locked") is not True:
        raise V4PreflightError("config_lock_v4.json is not locked")
    prelock = lock.get("prelock_state", {})
    if (
        prelock.get("development_dry_run_complete") is not True
        or prelock.get("validation_canary_complete") is not True
    ):
        raise V4PreflightError("completed dry-run and validation are not locked")
    if prelock.get("test_trajectory_count_seen") != 0:
        raise V4PreflightError("lock does not attest test_trajectory_count_seen=0")
    if any(prelock.get(key) is True for key in ("test_executed", "test_viewed", "test_visible")):
        raise V4PreflightError("lock reports prior V4 test visibility")
    status = _load_json(STATUS_PATH)
    if (
        status.get("status") != "locked_test_unseen"
        or status.get("test_visible") is not False
        or status.get("test_trajectory_count_seen") != 0
    ):
        raise V4PreflightError("protocol status is not locked_test_unseen")
    authorized = _verify_authorizing_ref(lock)
    _verify_hashes(lock)
    _canonical_selection(lock)
    environment = _verify_environment(lock)
    _verify_no_forbidden_v4_capability()
    _verify_no_prior_test_state()
    if not VALIDATION_ROOT.is_dir() or not VALIDATION_ORACLE_ROOT.is_dir():
        raise V4PreflightError("completed validation and validation-oracle bundles are required")

    from otg_lab.v4_freshness import validate_manifest_paths
    from otg_lab.v4_methods import load_v4_method_matrix

    freshness = validate_manifest_paths(
        MANIFEST_PATH, historical_manifest_paths=HISTORICAL_MANIFESTS
    )
    load_v4_method_matrix(METHOD_MATRIX_PATH)

    pretest_evidence = _validate_pretest_evidence(require_clean=True)
    return {
        "head": authorized,
        "freshness": freshness,
        "validation_report": pretest_evidence["validation"],
        "validation_oracle_report": pretest_evidence["validation_oracle"],
        "v3_immutability": pretest_evidence["v3_immutability"],
        "locked_hash_count": len(lock["confirmation_file_hashes"]),
        "environment": environment,
    }


def _tracked_python_paths() -> list[Path]:
    values = _run_git("ls-files", "*.py").splitlines()
    return [ROOT / value for value in values if value]


def prepare_lock() -> dict[str, Any]:
    """Return completed lock content; never mutate the preregistration file."""

    _require_clean()
    if LOCKED_TEST_ROOT.exists() or ORACLE_ROOT.exists() or TEST_VISIBLE_SENTINEL.exists():
        raise V4PreflightError("lock preparation is pretest-only")
    if not VALIDATION_ROOT.is_dir() or not VALIDATION_ORACLE_ROOT.is_dir():
        raise V4PreflightError(
            "validation and validation-oracle must complete before lock preparation"
        )
    if not (DEVELOPMENT_ROOT / "estimated").is_dir() or not (
        DEVELOPMENT_ROOT / "oracle_diagnostic"
    ).is_dir():
        raise V4PreflightError("completed exposed V3 development dry-run is required")
    status = _load_json(STATUS_PATH)
    if (
        status.get("status") != "locked_test_unseen"
        or status.get("test_visible") is not False
        or status.get("test_trajectory_count_seen") != 0
    ):
        raise V4PreflightError(
            "commit the pretest locked_test_unseen status before preparing authorization"
        )
    development_evidence = _validate_development_evidence()
    pretest_evidence = _validate_pretest_evidence(require_clean=True)
    lock = copy.deepcopy(_load_json(LOCK_PATH))
    _canonical_selection(lock)
    from otg_lab.v4_freshness import validate_manifest_paths
    from otg_lab.v4_methods import load_v4_method_matrix

    freshness = validate_manifest_paths(
        MANIFEST_PATH, historical_manifest_paths=HISTORICAL_MANIFESTS
    )
    load_v4_method_matrix(METHOD_MATRIX_PATH)
    paths = set(_tracked_python_paths())
    paths.update(
        {
            ROOT / ".gitignore",
            ROOT / "EXPERIMENT_PROTOCOL_V4.md",
            ROOT / "V4_HYPOTHESES.md",
            STATISTICAL_DESIGN_PATH,
            ROOT / "V4_ACCEPTANCE_CRITERIA.json",
            ROOT / "V4_PROTOCOL_DECISIONS.md",
            METHOD_MATRIX_PATH,
            MANIFEST_PATH,
            ROOT / "scripts/generate_split_manifest_v4.py",
            STATUS_PATH,
            *FORMAL_CONFIGS,
            *HISTORICAL_MANIFESTS,
            *V3_IMMUTABLE_PATHS,
        }
    )
    paths.discard(LOCK_PATH)  # HEAD/tag authorization locks the self-referential file.
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise V4PreflightError(f"cannot hash missing lock inputs: {missing}")
    hashes = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in sorted(paths, key=lambda item: item.as_posix())
    }
    lock["locked"] = True
    lock["selection_status"] = "locked_after_validation_test_unseen"
    lock.setdefault("git", {})["confirmation_head_ref"] = CONFIRMATION_HEAD_REF
    lock["git"]["scientific_source_commit"] = _head()
    lock["git"]["scientific_source_commit_status"] = "complete_pre_authorization_HEAD"
    lock["git"]["authorization_scheme"] = "precommitted_ref_resolves_to_exact_HEAD"
    lock["git"]["locked_commit"] = None
    lock["prelock_state"].update(
        {
            "development_dry_run_complete": True,
            "validation_canary_complete": True,
            "test_trajectory_generation_performed": False,
            "test_executed": False,
            "test_viewed": False,
            "test_visible": False,
            "test_trajectory_count_seen": 0,
        }
    )
    lock["environment"] = _capture_environment()
    lock["confirmation_file_hashes"] = hashes
    lock["implementation_hash_coverage"] = {
        "all_tracked_python_files_required": True,
        "hashes": {
            path.relative_to(ROOT).as_posix(): _sha256(path)
            for path in _tracked_python_paths()
        },
        "status": "complete",
    }
    lock["method_matrix"]["sha256"] = _sha256(METHOD_MATRIX_PATH)
    lock["method_matrix"]["hash_status"] = "complete"
    lock["dataset"]["manifest_sha256"] = _sha256(MANIFEST_PATH)
    lock["dataset"]["manifest_hash_status"] = "complete"
    lock["dataset"]["generator_sha256"] = _sha256(
        ROOT / "scripts/generate_split_manifest_v4.py"
    )
    lock["dataset"]["generator_hash_status"] = "complete"
    lock["formal_configs"] = {
        path.relative_to(ROOT).as_posix(): _sha256(path) for path in FORMAL_CONFIGS
    }
    design_paths = (
        ROOT / "EXPERIMENT_PROTOCOL_V4.md",
        ROOT / "V4_HYPOTHESES.md",
        STATISTICAL_DESIGN_PATH,
        ROOT / "V4_ACCEPTANCE_CRITERIA.json",
        ROOT / "V4_PROTOCOL_DECISIONS.md",
        STATUS_PATH,
    )
    lock["design_material_observed_hashes"] = {
        path.relative_to(ROOT).as_posix(): _sha256(path) for path in design_paths
    }
    lock["historical_manifest_hashes"] = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in HISTORICAL_MANIFESTS
    }
    lock["v3_immutable_hashes"] = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in V3_IMMUTABLE_PATHS
    }
    lock["workflow_hashes"] = {".gitignore": _sha256(ROOT / ".gitignore")}
    lock["entrypoints"] = {
        "authoritative_runner": {
            "path": "run_paper_evidence_v4.py",
            "sha256": _sha256(ROOT / "run_paper_evidence_v4.py"),
            "status": "complete",
        },
        "v4_wrapper": {
            "path": "run_paper_evidence_v4.py",
            "sha256": _sha256(ROOT / "run_paper_evidence_v4.py"),
            "status": "complete",
        },
        "execution_core": {
            "path": "otg_lab/v4_runner.py",
            "sha256": _sha256(ROOT / "otg_lab/v4_runner.py"),
            "status": "complete",
        },
        "formal_confirmation_command": (
            "uv run python run_paper_evidence_v4.py confirm"
        ),
    }
    lock["freshness_proof"] = freshness
    lock["validation_lock_proof"] = {
        "development_estimated_artifact_index_sha256": _sha256(
            DEVELOPMENT_ROOT / "estimated/artifact_index.json"
        ),
        "development_oracle_artifact_index_sha256": _sha256(
            DEVELOPMENT_ROOT / "oracle_diagnostic/artifact_index.json"
        ),
        "development_checksums_verified": bool(
            development_evidence["estimated"]["checksums_verified"]
            and development_evidence["oracle"]["checksums_verified"]
        ),
        "validation_artifact_index_sha256": _sha256(
            VALIDATION_ROOT / "artifact_index.json"
        ),
        "validation_oracle_artifact_index_sha256": _sha256(
            VALIDATION_ORACLE_ROOT / "artifact_index.json"
        ),
        "validation_checksums_verified": bool(
            pretest_evidence["validation"]["checksums_verified"]
        ),
        "validation_oracle_checksums_verified": bool(
            pretest_evidence["validation_oracle"]["checksums_verified"]
        ),
        "independent_recomputation_verified": True,
    }
    v3_proof = pretest_evidence["v3_immutability"]
    lock["v3_exhaustive_immutability_proof"] = {
        "frozen_reference_commit": v3_proof["frozen_reference_commit"],
        "tracked_file_count": v3_proof["tracked_file_count"],
        "all_tracked_files_byte_identical_to_frozen_reference": v3_proof[
            "all_tracked_files_byte_identical_to_frozen_reference"
        ],
        "remote_archive": v3_proof["remote_archive"],
    }
    lock["self_reference_resolution"] = {
        "config_lock_sha256_embedded": False,
        "reason": "a file cannot truthfully contain the hash of its containing commit",
        "authorization": "the precommitted immutable tag ref must equal exact HEAD",
    }
    return lock


def _execution_api() -> dict[str, Any]:
    """Load execution-only dependencies. Never call this from report-only."""

    config = importlib.import_module("otg_lab.config")
    experiments = importlib.import_module("otg_lab.experiments")
    methods = importlib.import_module("otg_lab.v4_methods")
    return {
        "load_config": config.load_config,
        "synthetic_cases": experiments.synthetic_cases,
        "run_pipeline_matrix": experiments.run_pipeline_matrix,
        "combine_outcomes": experiments.combine_outcomes,
        "write_experiment_bundle": experiments.write_experiment_bundle,
        "repeated_runtime_study": experiments.repeated_runtime_study,
        "primary_method_specs": methods.primary_method_specs,
        "secondary_method_specs": methods.secondary_method_specs,
        "oracle_method_specs": methods.oracle_method_specs,
        "audit_primary_rows": methods.audit_primary_rows,
        "primary_purity_by_trajectory": methods.primary_purity_by_trajectory,
        "audit_same_information_rows": methods.audit_same_information_rows,
        "audit_target_component_zeroing": methods.audit_target_component_zeroing,
        "audit_ordinary_rows": methods.audit_ordinary_rows,
        "audit_oracle_rows": methods.audit_oracle_rows,
    }


def _config(api: Mapping[str, Any], path: Path, *, run_id: str, manifest: Path) -> dict[str, Any]:
    value = api["load_config"](path)
    value["run_id"] = run_id
    value["data"]["split_manifest"] = str(manifest)
    value["data"]["max_trajectories"] = None
    return value


def _cases(
    api: Mapping[str, Any],
    *,
    split: str,
    manifest: Path,
    run_id: str,
    capability: object | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    if split == "test":
        _require_capability(capability)
    return api["synthetic_cases"](
        split,
        sample_rate_hz=100.0,
        maximum=None,
        manifest_path=manifest,
        run_id=run_id,
    )


def _extra_audits(api: Mapping[str, Any], outcome: Any) -> dict[str, Sequence[Mapping[str, Any]]]:
    rows = outcome.samples
    same_information = api["audit_same_information_rows"](
        rows, executed_method_matrix=outcome.method_matrix
    )
    if any("audit_passed" not in row for row in same_information):
        raise V4RunnerError("same-information producer lacks canonical audit_passed")
    return {
        "method_identity_sample_audit.csv": api["audit_primary_rows"](rows),
        "method_identity_by_trajectory.csv": api[
            "primary_purity_by_trajectory"
        ](rows),
        "same_information_audit.csv": same_information,
        "target_component_zeroing_audit.csv": api["audit_target_component_zeroing"](rows),
        "ordinary_ruckig_method_identity.csv": api["audit_ordinary_rows"](rows),
    }


def _write_bundle(
    api: Mapping[str, Any],
    *,
    root: Path,
    config: Mapping[str, Any],
    outcome: Any,
    split: str,
    source: str,
    extra_csv: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    extra_json: Mapping[str, Any] | None = None,
    expected_commit: str | None = None,
    require_clean: bool,
    command: Sequence[str],
) -> Mapping[str, Any]:
    return api["write_experiment_bundle"](
        root,
        config,
        outcome,
        command=command,
        repo_root=ROOT,
        split=split,
        sample_rates_hz=(100.0,),
        source=source,
        selection_policy="frozen_v4_no_test_selection",
        expected_commit=expected_commit,
        require_clean=require_clean,
        extra_csv=extra_csv,
        extra_json=extra_json,
    )


def run_development_dry_run() -> dict[str, Any]:
    """Run the V4 pipeline only on fixed, exposed V3 validation identities."""

    _enforce_runtime_policy()
    if DEVELOPMENT_ROOT.exists():
        raise FileExistsError(f"refusing to overwrite {DEVELOPMENT_ROOT}")
    api = _execution_api()
    matrix = importlib.import_module("otg_lab.v4_methods").load_v4_method_matrix()
    config = _config(
        api,
        VALIDATION_CONFIG_PATH,
        run_id="paper-evidence-v4-development-v3-validation-exposed",
        manifest=ROOT / "split_manifest_v3.json",
    )
    config["formal"] = False
    config["data"]["split"] = "validation"
    cases = _cases(
        api,
        split="validation",
        manifest=ROOT / "split_manifest_v3.json",
        run_id=config["run_id"],
    )
    primary = api["run_pipeline_matrix"](cases, config, api["primary_method_specs"](matrix))
    ordinary = api["run_pipeline_matrix"](
        cases, config, api["secondary_method_specs"](matrix)
    )
    estimated = api["combine_outcomes"]((primary, ordinary))
    estimated_root = DEVELOPMENT_ROOT / "estimated"
    estimated_report = _write_bundle(
        api,
        root=estimated_root,
        config=config,
        outcome=estimated,
        split="v3_validation_exposed",
        source="synthetic-feasible-v3 exposed validation; development only",
        extra_csv=_extra_audits(api, estimated),
        extra_json={"confirmatory": False, "test_population": "exposed_v3_validation"},
        require_clean=False,
        command=("uv", "run", "python", "run_paper_evidence_v4.py", "dry-run"),
    )
    oracle_config = _config(
        api,
        ORACLE_CONFIG_PATH,
        run_id="paper-evidence-v4-development-oracle-v3-validation-exposed",
        manifest=ROOT / "split_manifest_v3.json",
    )
    oracle_config["formal"] = False
    oracle_config["data"]["split"] = "validation"
    oracle = api["run_pipeline_matrix"](
        cases, oracle_config, api["oracle_method_specs"](matrix)
    )
    oracle_report = _write_bundle(
        api,
        root=DEVELOPMENT_ROOT / "oracle_diagnostic",
        config=oracle_config,
        outcome=oracle,
        split="v3_validation_exposed",
        source="offline analytic truth; exposed development data",
        extra_csv={"oracle_method_identity.csv": api["audit_oracle_rows"](oracle.samples)},
        extra_json={"oracle_information_condition.json": _oracle_labels()},
        require_clean=False,
        command=("uv", "run", "python", "run_paper_evidence_v4.py", "dry-run"),
    )
    return {"estimated": estimated_report, "oracle": oracle_report}


def run_validation_canary() -> dict[str, Any]:
    """Run fixed V4 train+validation identities; no test helper is reachable."""

    _enforce_runtime_policy()
    _require_clean()
    if VALIDATION_ROOT.exists() or VALIDATION_ORACLE_ROOT.exists():
        raise FileExistsError("refusing to overwrite existing V4 validation output")
    if LOCKED_TEST_ROOT.exists() or ORACLE_ROOT.exists() or TEST_VISIBLE_SENTINEL.exists():
        raise V4PreflightError("validation is pretest-only")
    api = _execution_api()
    methods_module = importlib.import_module("otg_lab.v4_methods")
    matrix = methods_module.load_v4_method_matrix()
    config = _config(
        api,
        VALIDATION_CONFIG_PATH,
        run_id="paper-evidence-v4-validation-canary",
        manifest=MANIFEST_PATH,
    )
    config["data"]["split"] = "train+validation"
    cases = [
        *_cases(api, split="train", manifest=MANIFEST_PATH, run_id=config["run_id"]),
        *_cases(api, split="validation", manifest=MANIFEST_PATH, run_id=config["run_id"]),
    ]
    primary = api["run_pipeline_matrix"](cases, config, api["primary_method_specs"](matrix))
    ordinary = api["run_pipeline_matrix"](
        cases, config, api["secondary_method_specs"](matrix)
    )
    estimated = api["combine_outcomes"]((primary, ordinary))
    validation_report = _write_bundle(
        api,
        root=VALIDATION_ROOT,
        config=config,
        outcome=estimated,
        split="train+validation",
        source="synthetic-feasible-v4 pretest train and validation only",
        extra_csv=_extra_audits(api, estimated),
        extra_json={"test_trajectory_count_seen": 0, "selection_permitted": False},
        require_clean=True,
        command=("uv", "run", "python", "run_paper_evidence_v4.py", "validation"),
    )
    oracle_config = _config(
        api,
        ORACLE_CONFIG_PATH,
        run_id="paper-evidence-v4-validation-oracle-diagnostic",
        manifest=MANIFEST_PATH,
    )
    oracle_config["data"]["split"] = "train+validation"
    oracle = api["run_pipeline_matrix"](
        cases, oracle_config, api["oracle_method_specs"](matrix)
    )
    oracle_report = _write_bundle(
        api,
        root=VALIDATION_ORACLE_ROOT,
        config=oracle_config,
        outcome=oracle,
        split="train+validation",
        source="synthetic-feasible-v4 pretest offline analytic truth diagnostic",
        extra_csv={"oracle_method_identity.csv": api["audit_oracle_rows"](oracle.samples)},
        extra_json={"oracle_information_condition.json": _oracle_labels()},
        require_clean=True,
        command=("uv", "run", "python", "run_paper_evidence_v4.py", "validation"),
    )
    return {"validation": validation_report, "oracle": oracle_report}


def _generate_v4_test_cases(
    capability: object, api: Mapping[str, Any], config: Mapping[str, Any]
) -> list[tuple[str, list[dict[str, Any]]]]:
    _require_capability(capability)
    return _cases(
        api,
        split="test",
        manifest=MANIFEST_PATH,
        run_id=str(config["run_id"]),
        capability=capability,
    )


def _run_phase_a_regression() -> dict[str, Any]:
    from otg_runner import run_phase_a_p_only_compatibility
    from target_state_experiment import csv_reference

    reference = csv_reference(ROOT / "plot_data.csv")
    result = run_phase_a_p_only_compatibility(
        reference.position, original_count=reference.original_count
    )
    if not all(result["acceptance_criteria"].values()):
        raise V4PreflightError("Phase A P-only compatibility regression failed")
    return {
        "compatibility_metrics": result["compatibility_metrics"],
        "acceptance_criteria": result["acceptance_criteria"],
        "target_timing": result["target_timing"],
    }


def _oracle_labels() -> dict[str, Any]:
    return {
        "information_condition": "offline_analytic_truth",
        "causal": False,
        "deployable": False,
        "diagnostic_only": True,
        "excluded_from_primary": True,
    }


def _mark_test_visible(head: str) -> None:
    value = {
        "schema_version": "otg.v4-runtime-status.v1",
        "status": "confirmation_running_test_visible",
        "test_visible": True,
        "test_trajectory_count_seen": 0,
        "confirmation_source_commit": head,
        "same_test_rerun_permitted": False,
    }
    _atomic_json(RUNTIME_STATUS_PATH, value)
    _atomic_text(TEST_VISIBLE_SENTINEL, f"{head}\nconfirmation_running_test_visible\n")


def _freeze(head: str, error: BaseException) -> None:
    _atomic_json(
        RUNTIME_STATUS_PATH,
        {
            "schema_version": "otg.v4-runtime-status.v1",
            "status": "failed_test_visible_frozen",
            "test_visible": True,
            "confirmation_source_commit": head,
            "same_test_rerun_permitted": False,
            "next_confirmation_protocol": "v5",
            "failure_type": type(error).__name__,
            "failure_message": str(error),
        },
    )


def _write_result_status(
    *,
    head: str,
    effective_classification: str,
    statistical_classification: str,
    report_only_permitted: bool,
) -> str:
    if effective_classification in {
        "invalid_method_identity",
        "invalid_safety_gate",
        "unavailable_incomplete_denominator",
    }:
        status = "failed_test_visible_frozen"
    elif effective_classification in {"inconclusive", "confirmed_harmful"}:
        status = "complete_negative"
    else:
        status = "complete_confirmatory"
    payload = {
        "schema_version": "otg.v4-runtime-status.v1",
        "status": status,
        "test_visible": True,
        "confirmation_source_commit": head,
        "same_test_rerun_permitted": False,
        "raw_experiment_resume_permitted": False,
        "report_only_resume_permitted": report_only_permitted,
        "primary_result_classification": effective_classification,
        "statistical_classification": statistical_classification,
        "frozen_preregistration_status_path": "protocol_status_v4.json",
        "frozen_preregistration_status_sha256": _sha256(STATUS_PATH),
    }
    _atomic_json(RUNTIME_STATUS_PATH, payload)
    _atomic_json(RESULT_STATUS_PATH, payload)
    return status


def _mark_report_only_failure(head: str, error: BaseException) -> None:
    _atomic_json(
        RUNTIME_STATUS_PATH,
        {
            "schema_version": "otg.v4-runtime-status.v1",
            "status": "confirmation_running_test_visible",
            "test_visible": True,
            "confirmation_source_commit": head,
            "same_test_rerun_permitted": False,
            "raw_experiment_resume_permitted": False,
            "report_only_resume_permitted": True,
            "reporting_failure_type": type(error).__name__,
            "reporting_failure_message": str(error),
        },
    )


def _run_locked_confirmation(
    capability: object, *, head: str, phase_a: Mapping[str, Any]
) -> dict[str, Any]:
    _require_capability(capability)
    api = _execution_api()
    methods_module = importlib.import_module("otg_lab.v4_methods")
    matrix = methods_module.load_v4_method_matrix()
    config = _config(
        api,
        LOCKED_CONFIG_PATH,
        run_id="paper-evidence-v4-locked-test",
        manifest=MANIFEST_PATH,
    )
    cases = _generate_v4_test_cases(capability, api, config)

    primary_methods = api["primary_method_specs"](matrix)
    primary = api["run_pipeline_matrix"](cases, config, primary_methods)
    runtime_samples, runtime_summary, runtime_failures = api["repeated_runtime_study"](
        cases, config, primary_methods, repetitions=5, warmup_cycles=100
    )
    ordinary = api["run_pipeline_matrix"](
        cases, config, api["secondary_method_specs"](matrix)
    )
    estimated = api["combine_outcomes"]((primary, ordinary))
    extra_csv = dict(_extra_audits(api, estimated))
    extra_csv.update(
        {
            "runtime_repeated_samples.csv": runtime_samples,
            "runtime_repeated_summary.csv": runtime_summary,
            "runtime_repeated_failures.csv": runtime_failures,
        }
    )
    locked_report = _write_bundle(
        api,
        root=LOCKED_TEST_ROOT,
        config=config,
        outcome=estimated,
        split="test",
        source="synthetic-feasible-v4 first-visible locked test",
        extra_csv=extra_csv,
        extra_json={"phase_a_regression.json": dict(phase_a)},
        expected_commit=head,
        require_clean=True,
        command=("uv", "run", "python", "run_paper_evidence_v4.py", "confirm"),
    )

    oracle_config = _config(
        api,
        ORACLE_CONFIG_PATH,
        run_id="paper-evidence-v4-oracle-diagnostic",
        manifest=MANIFEST_PATH,
    )
    oracle = api["run_pipeline_matrix"](
        cases, oracle_config, api["oracle_method_specs"](matrix)
    )
    oracle_report = _write_bundle(
        api,
        root=ORACLE_ROOT,
        config=oracle_config,
        outcome=oracle,
        split="test",
        source="synthetic-feasible-v4 offline analytic truth diagnostic",
        extra_csv={"oracle_method_identity.csv": api["audit_oracle_rows"](oracle.samples)},
        extra_json={"oracle_information_condition.json": _oracle_labels()},
        expected_commit=head,
        require_clean=True,
        command=("uv", "run", "python", "run_paper_evidence_v4.py", "confirm"),
    )
    return {"locked_test": locked_report, "oracle": oracle_report}


def confirm() -> dict[str, Any]:
    """Perform the only formal, exactly-once V4 confirmation."""

    global _ACTIVE_CONFIRMATION_CAPABILITY
    _enforce_runtime_policy()
    if _ACTIVE_CONFIRMATION_CAPABILITY is not None:
        raise V4PreflightError("a confirmation is already active")
    preflight = verify_confirmation_preflight()
    head = str(preflight["head"])
    phase_a = _run_phase_a_regression()
    capability = _ConfirmationCapability(_CAPABILITY_CONSTRUCTOR_NONCE)
    _ACTIVE_CONFIRMATION_CAPABILITY = capability
    test_visible = False
    report_only_eligible = False
    try:
        _mark_test_visible(head)
        test_visible = True
        raw = _run_locked_confirmation(capability, head=head, phase_a=phase_a)
        _verify_post_test_immutability(head)
        statistics = importlib.import_module("otg_lab.v4_statistics")
        statistical_result = statistics.analyze_v4_confirmation(
            locked_test_root=LOCKED_TEST_ROOT,
            oracle_root=ORACLE_ROOT,
            results_root=RESULTS_ROOT,
            manifest_path=MANIFEST_PATH,
            statistical_design_path=STATISTICAL_DESIGN_PATH,
        )
        contextual = importlib.import_module("otg_lab.v4_contextual")
        contextual_result = contextual.generate_v4_contextual_tables(
            results_root=RESULTS_ROOT,
            locked_test_root=LOCKED_TEST_ROOT,
            oracle_root=ORACLE_ROOT,
            report_only=False,
        )
        artifacts = importlib.import_module("otg_lab.v4_artifacts")
        statistical_validation = _validate_independent_statistics(artifacts)
        _atomic_json(
            RESULTS_ROOT / "manifests/pre_report_statistical_validation.json",
            statistical_validation,
        )
        _verify_post_test_immutability(head)
        report_only_eligible = True
        source_hashes = {
            **_load_json(LOCK_PATH)["confirmation_file_hashes"],
            **contextual_result["source_hashes"],
            **contextual_result["table_hashes"],
        }
        handoff_module = importlib.import_module("otg_lab.v4_handoff")
        handoff = handoff_module.generate_v4_handoff(
            RESULTS_ROOT,
            head,
            source_hashes,
            report_only=False,
        )
        _atomic_json(
            RESULTS_ROOT / "manifests/preregistration_status_v4.json",
            _load_json(STATUS_PATH),
        )
        effective_classification = str(
            handoff["primary_result_classification"]
        )
        result_status = _write_result_status(
            head=head,
            effective_classification=effective_classification,
            statistical_classification=str(
                handoff.get(
                    "statistical_classification",
                    statistical_result.get("primary_result_classification"),
                )
            ),
            report_only_permitted=True,
        )
        final = artifacts.finalize_v4_results(
            results_root=RESULTS_ROOT,
            locked_test_root=LOCKED_TEST_ROOT,
            oracle_root=ORACLE_ROOT,
            raw_commit=head,
            phase_a_result=phase_a,
            confirmation_context={
                "preflight": preflight,
                "statistics": statistical_result,
                "contextual": contextual_result,
                "statistical_validation": statistical_validation,
                "handoff": handoff,
            },
            report_only=False,
            protocol_path=ROOT / "EXPERIMENT_PROTOCOL_V4.md",
            config_lock_path=LOCK_PATH,
            status_path=RUNTIME_STATUS_PATH,
            release_dir=RELEASE_ROOT / f"paper-evidence-v4-{head[:7]}",
            generation_command=(
                "uv",
                "run",
                "python",
                "run_paper_evidence_v4.py",
                "confirm",
            ),
        )
        _verify_post_test_immutability(head)
        return {
            "raw": raw,
            "statistics": statistical_result,
            "contextual": contextual_result,
            "statistical_validation": statistical_validation,
            "handoff": handoff,
            "status": result_status,
            "final": final,
        }
    except BaseException as error:
        if test_visible:
            try:
                if report_only_eligible:
                    _mark_report_only_failure(head, error)
                else:
                    _freeze(head, error)
            except BaseException as freeze_error:
                error.add_note(f"also failed to persist frozen status: {freeze_error}")
        raise
    finally:
        _ACTIVE_CONFIRMATION_CAPABILITY = None


def report_only_resume(raw_commit: str) -> Mapping[str, Any]:
    """Rebuild bounded reporting without importing any experiment-stage module."""

    if re.fullmatch(r"[0-9a-f]{40}", raw_commit) is None:
        raise ReportOnlyError("report-only requires an exact lowercase 40-character raw commit")
    runtime_status = _load_json(RUNTIME_STATUS_PATH)
    if (
        runtime_status.get("report_only_resume_permitted") is not True
        or runtime_status.get("raw_experiment_resume_permitted") is not False
        or runtime_status.get("confirmation_source_commit") != raw_commit
    ):
        raise ReportOnlyError("runtime status does not authorize downstream-only resume")
    try:
        code_state = _verify_report_only_code_state(raw_commit)
        artifacts = importlib.import_module("otg_lab.v4_artifacts")
        validation = artifacts.validate_report_only_inputs(
            results_root=RESULTS_ROOT,
            raw_commit=raw_commit,
            locked_test_root=LOCKED_TEST_ROOT,
            oracle_root=ORACLE_ROOT,
        )
        if not (RESULTS_ROOT / "statistics").is_dir():
            raise ReportOnlyError(
                "report-only requires already completed immutable statistics"
            )
        statistical_validation = _validate_independent_statistics(artifacts)
        contextual = importlib.import_module("otg_lab.v4_contextual")
        contextual_result = contextual.generate_v4_contextual_tables(
            results_root=RESULTS_ROOT,
            locked_test_root=LOCKED_TEST_ROOT,
            oracle_root=ORACLE_ROOT,
            report_only=True,
        )
        _atomic_json(
            RESULTS_ROOT / "manifests/preregistration_status_v4.json",
            _load_json(STATUS_PATH),
        )
        artifacts.validate_statistical_artifacts(RESULTS_ROOT)
        locked_source_hashes = _load_json(LOCK_PATH)["confirmation_file_hashes"]
        source_hashes = {
            **locked_source_hashes,
            **contextual_result["source_hashes"],
            **contextual_result["table_hashes"],
        }
        for relative in code_state["changed_reporting_paths"]:
            source_hashes[f"raw_commit:{relative}"] = locked_source_hashes[relative]
            source_hashes[f"reporting_commit:{relative}"] = _sha256(ROOT / relative)
        handoff_module = importlib.import_module("otg_lab.v4_handoff")
        handoff = handoff_module.generate_v4_handoff(
            RESULTS_ROOT,
            raw_commit,
            source_hashes,
            report_only=True,
        )
        status = _write_result_status(
            head=raw_commit,
            effective_classification=str(handoff["primary_result_classification"]),
            statistical_classification=str(handoff["statistical_classification"]),
            report_only_permitted=True,
        )
        final = artifacts.finalize_v4_results(
            results_root=RESULTS_ROOT,
            locked_test_root=LOCKED_TEST_ROOT,
            oracle_root=ORACLE_ROOT,
            raw_commit=raw_commit,
            report_only=True,
            validation_report=validation,
            reporting_commit=str(code_state["reporting_commit"]),
            release_dir=(
                RELEASE_ROOT / f"paper-evidence-v4-{raw_commit[:7]}-report-only"
            ),
            protocol_path=ROOT / "EXPERIMENT_PROTOCOL_V4.md",
            config_lock_path=LOCK_PATH,
            status_path=RUNTIME_STATUS_PATH,
            generation_command=(
                "uv",
                "run",
                "python",
                "run_paper_evidence_v4.py",
                "report-only",
                raw_commit,
            ),
        )
        return {
            "code_state": code_state,
            "validation": validation,
            "statistical_validation": statistical_validation,
            "contextual": contextual_result,
            "handoff": handoff,
            "status": status,
            "final": final,
        }
    except BaseException as error:
        try:
            _mark_report_only_failure(raw_commit, error)
        except BaseException as status_error:
            error.add_note(f"also failed to retain report-only status: {status_error}")
        raise


def qa() -> Mapping[str, Any]:
    """Read-only V4 QA for either locked-pretest or completed raw evidence."""

    if LOCKED_TEST_ROOT.exists() or ORACLE_ROOT.exists():
        artifacts = importlib.import_module("otg_lab.v4_artifacts")
        return {
            "locked_test": artifacts.validate_raw_bundle(
                LOCKED_TEST_ROOT, bundle_kind="locked_test", require_clean=False
            ),
            "oracle": artifacts.validate_raw_bundle(
                ORACLE_ROOT, bundle_kind="oracle_diagnostic", require_clean=False
            ),
        }
    return verify_confirmation_preflight()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_paper_evidence_v4.py",
        description="Narrow V4 development, validation, lock, confirmation, and QA CLI.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("dry-run")
    subcommands.add_parser("validation")
    subcommands.add_parser("prep-lock")
    subcommands.add_parser("confirm")
    report = subcommands.add_parser("report-only")
    report.add_argument("raw_commit")
    subcommands.add_parser("qa")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "dry-run":
        result = run_development_dry_run()
    elif arguments.command == "validation":
        result = run_validation_canary()
    elif arguments.command == "prep-lock":
        result = prepare_lock()
    elif arguments.command == "confirm":
        result = confirm()
    elif arguments.command == "report-only":
        result = report_only_resume(arguments.raw_commit)
    elif arguments.command == "qa":
        result = qa()
    else:  # pragma: no cover
        raise AssertionError(arguments.command)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


__all__ = [
    "ConfirmationCapabilityError",
    "CONFIRMATION_HEAD_REF",
    "DEVELOPMENT_ROOT",
    "LOCKED_TEST_ROOT",
    "ORACLE_ROOT",
    "RESULTS_ROOT",
    "RUNTIME_STATUS_PATH",
    "TEST_VISIBLE_SENTINEL",
    "V4PreflightError",
    "confirm",
    "main",
    "prepare_lock",
    "qa",
    "report_only_resume",
    "run_development_dry_run",
    "run_validation_canary",
    "verify_confirmation_preflight",
]
