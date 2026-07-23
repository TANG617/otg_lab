#!/usr/bin/env python3
"""Run the complete evidence pipeline on exposed v2 data as development QA."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import run_paper_evidence as cli
    from otg_lab.artifacts import assert_clean_commit
finally:
    sys.path.pop(0)

DEFAULT_DRY_ROOT = ROOT / "runs" / "paper_evidence_v3-development-dry-run"


def build_dry_protocol(dry_root: Path) -> cli.EvidenceProtocol:
    resolved = dry_root.resolve()
    return cli.EvidenceProtocol(
        version="v2",
        dataset_id="synthetic-feasible-v2-exposed-development-dry-run",
        entrypoint=ROOT / "scripts" / "run_full_development_dry_run.py",
        raw_root=resolved / "raw_runs",
        final_root=resolved / "final",
        selection_validation_root=resolved / "selection-validation",
        config_lock_path=ROOT / "config_lock_v2.json",
        locked_selection_schema_version="otg.locked-selection.v2",
        config_defaults=cli.V2_CONFIG_DEFAULTS,
        confirm_experiments=cli.V2_CONFIRM_EXPERIMENTS,
        selection_consumer_configs=cli.V2_SELECTION_CONSUMER_CONFIGS,
        default_split_manifest=None,
        exposed_test_manifests=(),
        require_fresh_locked_test=False,
        protocol_document=ROOT / "EXPERIMENT_PROTOCOL_V2.md",
    )


def _run(
    arguments: list[str], *, protocol: cli.EvidenceProtocol
) -> dict[str, Any]:
    parser = cli.build_parser(protocol)
    parsed = parser.parse_args(arguments)
    if arguments[0] == "validation":
        parsed.confirmation_run = True
    previous = cli._LOGICAL_COMMAND
    cli._LOGICAL_COMMAND = (str(Path(__file__).resolve()),)
    try:
        result = parsed.function(parsed)
    finally:
        cli._LOGICAL_COMMAND = previous
    if not isinstance(result, dict):
        raise RuntimeError(f"dry-run command returned a non-mapping: {arguments[0]}")
    print(json.dumps({"command": arguments[0], "status": "complete"}, sort_keys=True))
    return result


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _full_commit(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("must be a full lowercase 40-character commit")
    return value


def _status(
    *,
    raw_commit: str,
    reporting_commit: str,
    completed: list[str],
    bundle_count: int,
    partial_stage_canary: bool,
    parallel_jobs: int | None,
    report_only_resume: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "otg.development-dry-run.v2",
        "status": "complete_nonconfirmatory",
        "source_commit": raw_commit,
        "reporting_commit": reporting_commit,
        "dataset": "synthetic-feasible-v2-exposed",
        "v3_test_generated": False,
        "completed": completed,
        "bundle_count": bundle_count,
        "partial_stage_canary": partial_stage_canary,
        "parallel_jobs": parallel_jobs,
        "report_only_resume": report_only_resume,
    }


def _write_status(dry_root: Path, status: dict[str, Any]) -> None:
    status_path = dry_root / "development_dry_run_status.json"
    status_path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(status, indent=2, sort_keys=True))


def _resume_report(
    *,
    dry_root: Path,
    protocol: cli.EvidenceProtocol,
    raw_commit: str,
    reporting_commit: str,
) -> dict[str, Any]:
    if not protocol.raw_root.is_dir():
        raise FileNotFoundError(
            f"development dry-run raw bundles are missing: {protocol.raw_root}"
        )
    _run(
        [
            "report",
            "--raw-results",
            str(protocol.raw_root),
            "--output-root",
            str(protocol.final_root),
            "--expected-run-commit",
            raw_commit,
        ],
        protocol=protocol,
    )
    completed = [
        *(command for command, _, _ in protocol.confirm_experiments),
        "qa",
        "report",
    ]
    status = _status(
        raw_commit=raw_commit,
        reporting_commit=reporting_commit,
        completed=completed,
        bundle_count=len(protocol.confirm_experiments),
        partial_stage_canary=False,
        parallel_jobs=None,
        report_only_resume=True,
    )
    _write_status(dry_root, status)
    return status


def _run_experiment(
    command: str, config: str, bundle: str, dry_root: Path
) -> str:
    """Run one independent bundle in a spawned worker process."""

    protocol = build_dry_protocol(dry_root)
    _run(
        [
            command,
            "--config",
            config,
            "--output",
            str(protocol.raw_root / bundle),
        ],
        protocol=protocol,
    )
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_DRY_ROOT)
    parser.add_argument(
        "--stage",
        action="append",
        choices=[command for command, _, _ in cli.V2_CONFIRM_EXPERIMENTS],
        help="repeat to run only selected stages in protocol order",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="validate selected bundles without requiring the full report contract",
    )
    parser.add_argument(
        "--jobs",
        type=_positive_int,
        default=1,
        help="independent experiment bundles to run concurrently (default: 1)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="resume QA/report packaging from an existing complete raw bundle tree",
    )
    parser.add_argument(
        "--expected-run-commit",
        type=_full_commit,
        help="exact clean commit recorded by every existing raw bundle",
    )
    args = parser.parse_args()
    state = assert_clean_commit(ROOT)
    dry_root = args.output_root.resolve()
    protocol = build_dry_protocol(dry_root)
    if args.report_only:
        if args.stage or args.skip_report:
            parser.error("--report-only cannot be combined with --stage or --skip-report")
        if args.expected_run_commit is None:
            parser.error("--report-only requires --expected-run-commit")
        _resume_report(
            dry_root=dry_root,
            protocol=protocol,
            raw_commit=args.expected_run_commit,
            reporting_commit=state.commit,
        )
        return 0
    if args.expected_run_commit is not None:
        parser.error("--expected-run-commit is valid only with --report-only")
    if dry_root.exists():
        raise FileExistsError(
            f"refusing to overwrite development dry-run outputs: {dry_root}"
        )
    selected = set(args.stage or ())
    experiments = tuple(
        experiment
        for experiment in protocol.confirm_experiments
        if not selected or experiment[0] in selected
    )
    if not experiments:
        raise ValueError("development dry-run selected no stages")
    if not args.skip_report and len(experiments) != len(protocol.confirm_experiments):
        raise ValueError("a partial development dry-run requires --skip-report")

    worker_count = min(args.jobs, len(experiments))
    if worker_count == 1:
        completed = [
            _run_experiment(command, config, bundle, dry_root)
            for command, config, bundle in experiments
        ]
    else:
        # Spawned processes keep each command's module globals and solver state
        # isolated.  Every bundle has a distinct atomic output destination.
        context = multiprocessing.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=worker_count, mp_context=context
        ) as executor:
            futures = [
                executor.submit(
                    _run_experiment, command, config, bundle, dry_root
                )
                for command, config, bundle in experiments
            ]
            completed = [future.result() for future in futures]

    qa = _run(
        ["qa", "--results", str(protocol.raw_root)], protocol=protocol
    )
    if int(qa.get("bundle_count", -1)) != len(experiments):
        raise RuntimeError("development dry-run QA did not validate every bundle")
    completed.append("qa")

    if not args.skip_report:
        _run(
            [
                "report",
                "--raw-results",
                str(protocol.raw_root),
                "--output-root",
                str(protocol.final_root),
                "--expected-run-commit",
                state.commit,
            ],
            protocol=protocol,
        )
        completed.append("report")
    status = _status(
        raw_commit=state.commit,
        reporting_commit=state.commit,
        completed=completed,
        bundle_count=len(experiments),
        partial_stage_canary=len(experiments) != len(protocol.confirm_experiments),
        parallel_jobs=worker_count,
        report_only_resume=False,
    )
    _write_status(dry_root, status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
