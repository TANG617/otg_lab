"""Finish an interrupted E14 batch with smaller bounded-memory micro-shards."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

from run_sharded import _aggregate

from otg_lab.analysis import ComparisonSpec
from otg_lab.cli import load_experiment_spec
from otg_lab.experiment import run_experiment


def _run_case_ids(
    project_root: str,
    batch_root: str,
    execution_index: int,
    case_ids: tuple[str, ...],
) -> dict[str, Any]:
    root = Path(project_root)
    full = load_experiment_spec(root, "E14")
    requested = set(case_ids)
    cases = tuple(case for case in full.cases if case.case_id in requested)
    if len(cases) != len(requested):
        raise RuntimeError("micro-shard references unknown or duplicate case IDs")
    controlled = {
        **dict(full.controlled_variables),
        "execution_partition": {
            "execution_index": execution_index,
            "case_count": len(cases),
            "partition_rule": "remaining_full_case_order_micro_chunk",
        },
    }
    shard = replace(
        full,
        cases=cases,
        controlled_variables=controlled,
        description=(
            f"{full.description} Recovery micro-shard {execution_index}; "
            f"{len(cases)} cases."
        ),
        comparison_spec=ComparisonSpec(
            pairs=(),
            metric_ids=(),
            input_ids=(full.inputs[0].input_id,),
            window_ids=("main_evaluation", "full_overlap"),
            bootstrap_seed=None,
            bootstrap_repetitions=0,
        ),
    )
    result = run_experiment(
        shard,
        project_root=root,
        runs_root=(
            Path(batch_root)
            / "micro_shards"
            / f"micro_{execution_index:04d}"
        ),
        create_figures=False,
    )
    return {
        "shard_index": execution_index,
        "case_count": len(cases),
        "success": result.success,
        "failure_count": result.failure_count,
        "required_failure_count": result.required_failure_count,
        "run_directory": str(result.run_directory),
        "spec_hash": result.spec_hash,
    }


def _existing_results(
    batch_root: Path,
) -> tuple[list[dict[str, Any]], set[str]]:
    results: list[dict[str, Any]] = []
    covered: set[str] = set()
    manifest_paths = (
        *batch_root.glob("shards/shard_*/*/manifest.json"),
        *batch_root.glob("micro_shards/micro_*/*/manifest.json"),
    )
    for manifest_path in sorted(manifest_paths):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("status") != "completed"
            or int(manifest.get("required_failure_count", 0)) != 0
            or not (
                manifest_path.parent
                / "analysis"
                / "vaj_sensitivity.csv"
            ).is_file()
        ):
            continue
        case_ids = {
            str(case["case_id"])
            for case in manifest["resolved_experiment_spec"].get("cases", [])
        }
        if covered & case_ids:
            continue
        covered.update(case_ids)
        results.append(
            {
                "shard_index": len(results),
                "case_count": len(case_ids),
                "success": True,
                "failure_count": int(manifest.get("failure_count", 0)),
                "required_failure_count": 0,
                "run_directory": str(manifest_path.parent),
                "spec_hash": manifest.get("spec_hash", ""),
            }
        )
    return results, covered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--cases-per-shard", type=int, default=5)
    parser.add_argument("--workers", type=int, default=8)
    arguments = parser.parse_args()
    if arguments.cases_per_shard <= 0 or arguments.workers <= 0:
        parser.error("--cases-per-shard and --workers must be positive")
    project_root = Path(__file__).resolve().parents[2]
    batch_root = arguments.batch_root.resolve()
    if not batch_root.is_dir():
        parser.error("--batch-root must be an existing E14 batch")
    if (batch_root / "aggregate").exists():
        parser.error("aggregate already exists")

    results, covered = _existing_results(batch_root)
    full = load_experiment_spec(project_root, "E14")
    remaining = [
        case.case_id for case in full.cases if case.case_id not in covered
    ]
    chunks = [
        tuple(remaining[start : start + arguments.cases_per_shard])
        for start in range(0, len(remaining), arguments.cases_per_shard)
    ]
    print(
        f"reusing {len(covered)} cases; finishing {len(remaining)} cases "
        f"in {len(chunks)} micro-shards",
        flush=True,
    )
    base_index = 10000 + len(results)
    for wave_start in range(0, len(chunks), arguments.workers):
        wave = list(
            enumerate(
                chunks[wave_start : wave_start + arguments.workers],
                start=base_index + wave_start,
            )
        )
        with ProcessPoolExecutor(max_workers=len(wave)) as executor:
            futures = {
                executor.submit(
                    _run_case_ids,
                    str(project_root),
                    str(batch_root),
                    execution_index,
                    case_ids,
                ): execution_index
                for execution_index, case_ids in wave
            }
            for future in as_completed(futures):
                result = future.result()
                if not result["success"]:
                    raise RuntimeError(
                        f"micro-shard {result['shard_index']} has required failures"
                    )
                results.append(result)
                print(
                    f"micro-shard {result['shard_index']} completed",
                    flush=True,
                )
    aggregate = _aggregate(project_root, batch_root, results)
    print(f"E14 aggregate completed: {aggregate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
