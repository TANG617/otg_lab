#!/usr/bin/env python3
"""Run the complete evidence pipeline on exposed v2 data as development QA."""

from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args()
    state = assert_clean_commit(ROOT)
    dry_root = args.output_root.resolve()
    protocol = build_dry_protocol(dry_root)
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

    completed = []
    for command, config, bundle in experiments:
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
        completed.append(command)

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
    status = {
        "schema_version": "otg.development-dry-run.v1",
        "status": "complete_nonconfirmatory",
        "source_commit": state.commit,
        "dataset": "synthetic-feasible-v2-exposed",
        "v3_test_generated": False,
        "completed": completed,
        "bundle_count": len(experiments),
        "partial_stage_canary": len(experiments) != len(protocol.confirm_experiments),
    }
    status_path = dry_root / "development_dry_run_status.json"
    status_path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
