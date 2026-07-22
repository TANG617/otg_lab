#!/usr/bin/env python3
"""Run the complete evidence pipeline on exposed v2 data as development QA."""

from __future__ import annotations

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

DRY_ROOT = ROOT / "runs" / "paper_evidence_v3-development-dry-run"
RAW_ROOT = DRY_ROOT / "raw_runs"
FINAL_ROOT = DRY_ROOT / "final"

DRY_PROTOCOL = cli.EvidenceProtocol(
    version="v2",
    dataset_id="synthetic-feasible-v2-exposed-development-dry-run",
    entrypoint=ROOT / "scripts" / "run_full_development_dry_run.py",
    raw_root=RAW_ROOT,
    final_root=FINAL_ROOT,
    selection_validation_root=DRY_ROOT / "selection-validation",
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


def _run(arguments: list[str]) -> dict[str, Any]:
    parser = cli.build_parser(DRY_PROTOCOL)
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
    state = assert_clean_commit(ROOT)
    if DRY_ROOT.exists():
        raise FileExistsError(
            f"refusing to overwrite development dry-run outputs: {DRY_ROOT}"
        )

    completed = []
    for command, config, bundle in DRY_PROTOCOL.confirm_experiments:
        _run(
            [
                command,
                "--config",
                config,
                "--output",
                str(RAW_ROOT / bundle),
            ]
        )
        completed.append(command)

    qa = _run(["qa", "--results", str(RAW_ROOT)])
    if int(qa.get("bundle_count", -1)) != len(DRY_PROTOCOL.confirm_experiments):
        raise RuntimeError("development dry-run QA did not validate every bundle")
    completed.append("qa")

    _run(
        [
            "report",
            "--raw-results",
            str(RAW_ROOT),
            "--output-root",
            str(FINAL_ROOT),
            "--expected-run-commit",
            state.commit,
        ]
    )
    completed.append("report")
    status = {
        "schema_version": "otg.development-dry-run.v1",
        "status": "complete_nonconfirmatory",
        "source_commit": state.commit,
        "dataset": "synthetic-feasible-v2-exposed",
        "v3_test_generated": False,
        "completed": completed,
        "bundle_count": len(DRY_PROTOCOL.confirm_experiments),
    }
    status_path = DRY_ROOT / "development_dry_run_status.json"
    status_path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
