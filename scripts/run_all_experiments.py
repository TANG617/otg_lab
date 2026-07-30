#!/usr/bin/env python3
"""Run every declared E-series experiment under one RunBuoy Run."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNBUOY_TITLE = "OTG experiment suite"
RUNBUOY_PHASE = "Running experiment suite"
EXPERIMENT_DIRECTORY = re.compile(r"^E[0-9]{2,}_.+$")
FAILURE_EXIT_CODE = 2


@dataclass(frozen=True)
class MonitoredRun:
    """The locally queryable identity and final result of a RunBuoy Run."""

    run_id: str
    experiment_count: int
    exit_code: int


def project_root() -> Path:
    """Return the repository root independent of the caller's working directory."""

    return Path(__file__).resolve().parents[1]


def discover_experiments(root: Path) -> tuple[str, ...]:
    """Find declared E-series experiment directories in deterministic order."""

    experiments_root = root / "experiments"
    if not experiments_root.is_dir():
        return ()
    return tuple(
        directory.name
        for directory in sorted(experiments_root.iterdir())
        if directory.is_dir()
        and EXPERIMENT_DIRECTORY.fullmatch(directory.name)
        and (directory / "experiment.py").is_file()
    )


def _completed_command(
    args: Sequence[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _json_object(
    completed: subprocess.CompletedProcess[str],
    *,
    command_name: str,
) -> Mapping[str, Any]:
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        detail = completed.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"{command_name} returned invalid JSON{suffix}"
        ) from error
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"{command_name} returned a non-object JSON response")
    return payload


def require_runbuoy(root: Path) -> None:
    """Require a ready, paired RunBuoy with structured progress support."""

    if shutil.which("runbuoy") is None:
        raise RuntimeError("RunBuoy CLI is required to monitor the experiment suite")

    doctor_result = _completed_command(
        ("runbuoy", "doctor", "--json"),
        cwd=root,
    )
    doctor = _json_object(doctor_result, command_name="RunBuoy doctor")
    if doctor.get("ready") is not True:
        checks = doctor.get("checks")
        if isinstance(checks, Mapping) and checks.get("server_reachable") is False:
            raise RuntimeError(
                "RunBuoy server is unreachable; "
                "run `runbuoy doctor --json` for details"
            )
        failed_checks = (
            sorted(name for name, passed in checks.items() if passed is False)
            if isinstance(checks, Mapping)
            else []
        )
        detail = f": {', '.join(failed_checks)}" if failed_checks else ""
        raise RuntimeError(
            "RunBuoy is not ready"
            f"{detail}; run `runbuoy doctor --json` for details"
        )

    capabilities_result = _completed_command(
        ("runbuoy", "capabilities", "--json"),
        cwd=root,
    )
    if capabilities_result.returncode != 0:
        raise RuntimeError("RunBuoy capabilities check failed")
    capabilities = _json_object(
        capabilities_result,
        command_name="RunBuoy capabilities",
    )
    progress_modes = capabilities.get("progress_modes")
    if not isinstance(progress_modes, list) or "structured" not in progress_modes:
        raise RuntimeError(
            "installed RunBuoy does not support structured progress"
        )


def emit_progress(
    root: Path,
    *,
    current: int,
    total: int,
    message: str,
) -> None:
    """Emit a safe current/total update from inside the monitored worker."""

    completed = subprocess.run(
        [
            "runbuoy",
            "emit",
            "progress",
            "--current",
            str(current),
            "--total",
            str(total),
            "--unit",
            "experiments",
            "--phase",
            RUNBUOY_PHASE,
            "--message",
            message,
        ],
        cwd=root,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("failed to emit RunBuoy experiment progress")


def run_experiment(root: Path, experiment: str) -> int:
    """Run one experiment while keeping its complete output local."""

    try:
        completed = subprocess.run(
            ["uv", "run", "otg-lab", "run", experiment],
            cwd=root,
            check=False,
        )
    except OSError as error:
        print(
            f"{experiment} failed to start: {type(error).__name__}: {error}",
            file=sys.stderr,
            flush=True,
        )
        return FAILURE_EXIT_CODE
    return completed.returncode


def run_worker(root: Path) -> int:
    """Run every experiment, continuing after failures with honest progress."""

    experiments = discover_experiments(root)
    total = len(experiments)
    if total <= 0:
        raise RuntimeError("no E-series experiments were found")
    if shutil.which("uv") is None:
        raise RuntimeError("uv is required to run the experiment suite")

    emit_progress(
        root,
        current=0,
        total=total,
        message=f"Preparing {total} experiments",
    )
    failures: list[tuple[str, int]] = []
    for index, experiment in enumerate(experiments, start=1):
        emit_progress(
            root,
            current=index - 1,
            total=total,
            message=f"Running experiment {index} of {total}",
        )
        print(f"==> [{index}/{total}] {experiment}", flush=True)
        exit_code = run_experiment(root, experiment)
        if exit_code != 0:
            failures.append((experiment, exit_code))
            print(
                f"<== [{index}/{total}] {experiment} failed "
                f"with exit code {exit_code}",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(f"<== [{index}/{total}] {experiment} completed", flush=True)
        emit_progress(
            root,
            current=index,
            total=total,
            message=f"Processed experiment {index} of {total}",
        )

    if failures:
        print(
            f"experiment suite completed with {len(failures)} failure(s):",
            file=sys.stderr,
        )
        for experiment, exit_code in failures:
            print(
                f"  {experiment}: exit code {exit_code}",
                file=sys.stderr,
            )
        return FAILURE_EXIT_CODE

    print(f"experiment suite completed: {total}/{total} succeeded")
    return 0


def start_monitored_run(root: Path) -> MonitoredRun:
    """Start the worker under one waiting RunBuoy structured-progress Run."""

    experiments = discover_experiments(root)
    if not experiments:
        raise RuntimeError("no E-series experiments were found")
    require_runbuoy(root)

    command = [
        "runbuoy",
        "run",
        "--json",
        "--non-interactive",
        "--wait",
        "--title",
        RUNBUOY_TITLE,
        "--progress",
        "structured",
        "--",
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
    ]
    completed = _completed_command(command, cwd=root)
    payload = _json_object(completed, command_name="RunBuoy")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("RunBuoy did not return a Run ID")

    result = payload.get("result")
    exit_code = (
        result.get("exit_code")
        if isinstance(result, Mapping)
        else completed.returncode
    )
    if not isinstance(exit_code, int):
        exit_code = completed.returncode
    return MonitoredRun(
        run_id=run_id,
        experiment_count=len(experiments),
        exit_code=exit_code,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run every E-series experiment with RunBuoy progress",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = project_root()
    try:
        if args.worker:
            return run_worker(root)
        result = start_monitored_run(root)
    except (OSError, RuntimeError) as error:
        print(
            f"run-all-experiments: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return FAILURE_EXIT_CODE

    print(
        f"RunBuoy experiment suite: {result.run_id} "
        f"experiments={result.experiment_count} exit_code={result.exit_code}"
    )
    print("Local inspection commands:")
    print(f"  runbuoy status {result.run_id}")
    print(f"  runbuoy logs {result.run_id}")
    print(f"  runbuoy attach {result.run_id}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
