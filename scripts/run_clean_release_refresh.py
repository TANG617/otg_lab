#!/usr/bin/env python3
"""Run the complete clean evidence refresh without editing tracked configs.

The runner executes E11--E17, using the bounded-memory sharded path for E14,
creates the three-case E14 lag-resolution supplement, and then rebuilds
A03--A06 from temporary, ignored configs pinned to the new runs.

It deliberately does not create paper/evidence/release.yaml: the scientific
comparison between provisional and clean evidence remains a separate review
gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from otg_lab.cross_analysis_reporting import analysis_spec_hash
from otg_lab.runio import utc_run_stamp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
E14_DIRECTORY = (
    PROJECT_ROOT / "experiments/E14_pv_pva_vaj_fine_sensitivity"
)
ANALYSIS_DIRECTORIES = {
    "A03": PROJECT_ROOT
    / "analyses/A03_recorded_pva_velocity_limit_attribution",
    "A04": PROJECT_ROOT / "analyses/A04_recorded_pv_pva_fd_selection",
    "A05": PROJECT_ROOT / "analyses/A05_stop_go_p_pv_pva_improvement",
    "A06": PROJECT_ROOT / "analyses/A06_pv_pva_vaj_fine_selection",
}
E14_SUPPLEMENT_CASES = (
    "pv_pred_backward_o1_kp1__v1_a8p2_j3200",
    "pv_pred_backward_o1_kp1__v4p1_a8p2_j3200",
    "pv_pred_backward_o1_kp1__v4p1_a8p2_j4000",
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _assert_clean(expected_head: str) -> None:
    head = _git("rev-parse", "HEAD")
    if head != expected_head:
        raise RuntimeError(f"HEAD changed during refresh: {head} != {expected_head}")
    status = _git("status", "--porcelain", "--untracked-files=all")
    if status:
        raise RuntimeError(
            "clean evidence refresh requires an empty Git status; found:\n" + status
        )


def _clean_subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # A caller's smoke-profile setting must never silently weaken E15--E17.
    environment.pop("OTG_CONFIRMATORY_PROFILE", None)
    return environment


def _run(command: Sequence[str]) -> None:
    print("+ " + shlex.join(command), flush=True)
    subprocess.run(
        tuple(command),
        cwd=PROJECT_ROOT,
        env=_clean_subprocess_environment(),
        check=True,
    )


def _experiment_directory(experiment_id: str) -> Path:
    matches = sorted(
        directory
        for directory in (PROJECT_ROOT / "experiments").glob(
            f"{experiment_id}_*"
        )
        if directory.is_dir()
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one directory for {experiment_id}, found {len(matches)}"
        )
    return matches[0]


def _manifest_paths(experiment_id: str) -> set[Path]:
    return set(
        _experiment_directory(experiment_id).glob("runs/*/manifest.json")
    )


def _read_json(path: Path) -> Mapping[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise RuntimeError(f"expected a JSON object: {path}")
    return loaded


def _validate_clean_manifest(path: Path, expected_head: str) -> None:
    manifest = _read_json(path)
    if manifest.get("status") != "completed":
        raise RuntimeError(f"incomplete manifest: {path}")
    git = manifest.get("git")
    if not isinstance(git, Mapping):
        raise RuntimeError(f"manifest has no Git provenance: {path}")
    if git.get("commit") != expected_head or git.get("dirty") is not False:
        raise RuntimeError(
            f"manifest is not clean at {expected_head}: {path}"
        )


def _relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def _run_regular_experiment(experiment_id: str, expected_head: str) -> Path:
    before = _manifest_paths(experiment_id)
    _run(
        (
            sys.executable,
            "-m",
            "otg_lab.cli",
            "run",
            experiment_id,
            "--no-figures",
        )
    )
    after = _manifest_paths(experiment_id)
    created = sorted(after - before)
    if len(created) != 1:
        raise RuntimeError(
            f"{experiment_id} created {len(created)} new manifests; expected one"
        )
    manifest_path = created[0]
    _validate_clean_manifest(manifest_path, expected_head)
    _assert_clean(expected_head)
    return manifest_path.parent


def _run_e14_full_grid(
    *,
    expected_head: str,
    batch_root: Path,
    shards: int,
    workers: int,
    resume: bool,
) -> Path:
    command = [
        sys.executable,
        str(E14_DIRECTORY / "run_sharded.py"),
        "--shards",
        str(shards),
        "--workers",
        str(workers),
        "--batch-root",
        str(batch_root),
    ]
    if resume:
        command.append("--resume")
    _run(command)
    aggregate = batch_root / "aggregate"
    _validate_clean_manifest(aggregate / "manifest.json", expected_head)
    _assert_clean(expected_head)
    return aggregate


def _run_e14_supplement(expected_head: str, batch_root: Path) -> Path:
    # The helper is intentionally imported only here because its module uses
    # sibling imports when executed as a standalone recovery utility.
    sys.path.insert(0, str(E14_DIRECTORY))
    try:
        from finish_microsharded import _run_case_ids

        result = _run_case_ids(
            str(PROJECT_ROOT),
            str(batch_root),
            0,
            E14_SUPPLEMENT_CASES,
        )
    finally:
        sys.path.pop(0)
    if result.get("success") is not True:
        raise RuntimeError(f"E14 selected-setting supplement failed: {result}")
    run_directory = Path(str(result["run_directory"])).resolve()
    _validate_clean_manifest(run_directory / "manifest.json", expected_head)
    _assert_clean(expected_head)
    return run_directory


def _load_analysis_module(path: Path, analysis_id: str) -> ModuleType:
    name = f"_otg_clean_refresh_{analysis_id.lower()}_{_stamp().replace('.', '_')}"
    specification = importlib.util.spec_from_file_location(
        name,
        path / "analysis_impl.py",
    )
    if specification is None or specification.loader is None:
        raise ImportError(f"cannot import analysis implementation: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _write_clean_analysis_config(
    analysis_id: str,
    *,
    source_directories: Mapping[str, Path],
    supplemental_directory: Path | None,
    run_stamp: str,
) -> Path:
    analysis_directory = ANALYSIS_DIRECTORIES[analysis_id]
    canonical_path = analysis_directory / "analysis.yaml"
    config = yaml.safe_load(canonical_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError(f"invalid canonical analysis config: {canonical_path}")
    requirements = config.setdefault("source_requirements", {})
    requirements["allow_dirty_git"] = False
    sources = config.get("sources")
    if not isinstance(sources, list):
        raise RuntimeError(f"analysis sources are not a list: {canonical_path}")
    unseen = set(source_directories)
    for source in sources:
        if not isinstance(source, dict):
            raise RuntimeError(f"invalid analysis source: {canonical_path}")
        source_id = str(source.get("source_id", ""))
        if source_id in source_directories:
            source["source_directory"] = _relative(
                source_directories[source_id]
            )
            unseen.remove(source_id)
    if unseen:
        raise RuntimeError(
            f"{analysis_id} did not declare source IDs: {sorted(unseen)}"
        )
    if supplemental_directory is not None:
        supplemental = config.get("supplemental_evidence")
        if not isinstance(supplemental, dict):
            raise RuntimeError(f"{analysis_id} lacks supplemental_evidence")
        supplemental["source_directory"] = _relative(supplemental_directory)

    config_directory = (
        analysis_directory / "runs" / f"_clean_config_{run_stamp}"
    )
    config_directory.mkdir(parents=True, exist_ok=False)
    config["project_root"] = os.path.relpath(
        PROJECT_ROOT,
        config_directory,
    )
    config_path = config_directory / "analysis.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return config_path


def _run_analysis(
    analysis_id: str,
    *,
    expected_head: str,
    source_directories: Mapping[str, Path],
    supplemental_directory: Path | None,
    run_stamp: str,
) -> Path:
    analysis_directory = ANALYSIS_DIRECTORIES[analysis_id]
    config_path = _write_clean_analysis_config(
        analysis_id,
        source_directories=source_directories,
        supplemental_directory=supplemental_directory,
        run_stamp=run_stamp,
    )
    # The temporary config lives below an ignored runs/ directory. Confirm that
    # creating it did not weaken the clean provenance condition.
    _assert_clean(expected_head)
    module = _load_analysis_module(analysis_directory, analysis_id)
    module.CONFIG_PATH = config_path
    # Redirect the human-readable rolling RESULTS.md away from its tracked
    # canonical location. The immutable run still contains its own RESULTS.md.
    module.ANALYSIS_DIRECTORY = config_path.parent
    allocated: list[Path] = []

    def allocate(prepared: Any) -> Path:
        run_id = f"{utc_run_stamp()}__{analysis_spec_hash(prepared)[:12]}"
        run_directory = analysis_directory / "runs" / run_id
        run_directory.mkdir(parents=True, exist_ok=False)
        shutil.copy2(config_path, run_directory / "analysis.clean.yaml")
        allocated.append(run_directory)
        return run_directory

    module.create_analysis_run_directory = allocate
    if module.run(check_only=True) != 0:
        raise RuntimeError(f"{analysis_id} source check failed")
    if module.run(check_only=False) != 0:
        raise RuntimeError(f"{analysis_id} analysis failed")
    if len(allocated) != 1:
        raise RuntimeError(
            f"{analysis_id} allocated {len(allocated)} runs; expected one"
        )
    run_directory = allocated[0]
    manifest_path = run_directory / "analysis_manifest.json"
    _validate_clean_manifest(manifest_path, expected_head)
    manifest = _read_json(manifest_path)
    for source in manifest.get("sources", []):
        if not isinstance(source, Mapping) or source.get("git_dirty") is not False:
            raise RuntimeError(
                f"{analysis_id} retained a dirty source: {source}"
            )
    _assert_clean(expected_head)
    return run_directory


def _state_directory(run_stamp: str) -> Path:
    declared = Path(_git("rev-parse", "--git-path", "otg-clean-refresh"))
    root = declared if declared.is_absolute() else PROJECT_ROOT / declared
    directory = root.resolve() / run_stamp
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e14-shards", type=int, default=64)
    parser.add_argument("--e14-workers", type=int, default=8)
    parser.add_argument(
        "--e14-batch-root",
        type=Path,
        default=None,
        help="explicit E14 batch directory (must not exist unless --resume-e14)",
    )
    parser.add_argument(
        "--resume-e14",
        action="store_true",
        help="resume completed shards in an existing --e14-batch-root",
    )
    parser.add_argument(
        "--skip-quality-checks",
        action="store_true",
        help="skip pytest and Ruff; not recommended for release evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.e14_shards <= 0 or arguments.e14_workers <= 0:
        raise SystemExit("--e14-shards and --e14-workers must be positive")
    if arguments.resume_e14 and arguments.e14_batch_root is None:
        raise SystemExit("--resume-e14 requires --e14-batch-root")

    expected_head = _git("rev-parse", "HEAD")
    _assert_clean(expected_head)
    run_stamp = _stamp()
    state_directory = _state_directory(run_stamp)
    state_path = state_directory / "state.json"
    state: dict[str, Any] = {
        "schema_version": "otg.clean_release_refresh.v1",
        "status": "running",
        "git_commit": expected_head,
        "run_stamp": run_stamp,
        "experiments": {},
        "analyses": {},
    }
    _write_state(state_path, state)

    try:
        if not arguments.skip_quality_checks:
            _run((sys.executable, "-m", "pytest"))
            _run((sys.executable, "-m", "ruff", "check", "."))
            _assert_clean(expected_head)

        experiment_runs: dict[str, Path] = {}
        for experiment_id in ("E11", "E12", "E13"):
            experiment_runs[experiment_id] = _run_regular_experiment(
                experiment_id,
                expected_head,
            )
            state["experiments"][experiment_id] = _relative(
                experiment_runs[experiment_id]
            )
            _write_state(state_path, state)

        e14_batch = (
            arguments.e14_batch_root.resolve()
            if arguments.e14_batch_root is not None
            else E14_DIRECTORY
            / "sharded_runs"
            / f"{run_stamp}__clean_full_grid"
        )
        if arguments.resume_e14:
            if not e14_batch.is_dir():
                raise RuntimeError("--resume-e14 batch root does not exist")
        elif e14_batch.exists():
            raise RuntimeError(f"E14 batch root already exists: {e14_batch}")
        experiment_runs["E14"] = _run_e14_full_grid(
            expected_head=expected_head,
            batch_root=e14_batch,
            shards=arguments.e14_shards,
            workers=arguments.e14_workers,
            resume=arguments.resume_e14,
        )
        state["experiments"]["E14"] = _relative(experiment_runs["E14"])
        _write_state(state_path, state)

        e14_supplement_root = (
            E14_DIRECTORY
            / "sharded_runs"
            / f"{run_stamp}__clean_selected_lag"
        )
        e14_supplement = _run_e14_supplement(
            expected_head,
            e14_supplement_root,
        )
        state["experiments"]["E14_selected_lag"] = _relative(e14_supplement)
        _write_state(state_path, state)

        for experiment_id in ("E15", "E16", "E17"):
            experiment_runs[experiment_id] = _run_regular_experiment(
                experiment_id,
                expected_head,
            )
            state["experiments"][experiment_id] = _relative(
                experiment_runs[experiment_id]
            )
            _write_state(state_path, state)

        analysis_sources = {
            "A03": {"e12_vmax_ablation": experiment_runs["E12"]},
            "A04": {
                "e11_pv_recorded": experiment_runs["E11"],
                "e12_pva_vmax_ablation": experiment_runs["E12"],
            },
            "A05": {"e13_joint_stop_go": experiment_runs["E13"]},
            "A06": {"e14_fine_vaj": experiment_runs["E14"]},
        }
        for analysis_id in ("A03", "A04", "A05", "A06"):
            analysis_run = _run_analysis(
                analysis_id,
                expected_head=expected_head,
                source_directories=analysis_sources[analysis_id],
                supplemental_directory=(
                    e14_supplement if analysis_id == "A06" else None
                ),
                run_stamp=run_stamp,
            )
            state["analyses"][analysis_id] = _relative(analysis_run)
            _write_state(state_path, state)

        _assert_clean(expected_head)
        state["status"] = "completed"
        _write_state(state_path, state)
    except Exception as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        _write_state(state_path, state)
        print(f"clean refresh failed; checkpoint: {state_path}", file=sys.stderr)
        raise

    print("clean evidence refresh completed")
    print(f"HEAD: {expected_head}")
    print(f"state: {state_path}")
    for group in ("experiments", "analyses"):
        for identifier, directory in state[group].items():
            print(f"{identifier}: {directory}")
    print(
        "Next gate: generate paper/evidence/release.yaml from this state, "
        "compare it with provisional evidence, and only then run make release."
    )
    return 0


def _entrypoint() -> int:
    try:
        return main()
    except Exception as error:
        print(
            f"clean-release-refresh: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
