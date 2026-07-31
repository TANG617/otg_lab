"""Run E14 in bounded-memory parallel shards and build one aggregate result."""

from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

from otg_lab.analysis import ComparisonSpec
from otg_lab.cli import load_experiment_spec
from otg_lab.experiment import run_experiment
from otg_lab.runio import (
    collect_environment,
    collect_git_state,
    sha256_json,
    utc_run_stamp,
    write_json,
    write_rows_csv,
)


def _run_shard(
    project_root: str,
    batch_root: str,
    shard_index: int,
    shard_count: int,
) -> dict[str, Any]:
    root = Path(project_root)
    full = load_experiment_spec(root, "E14")
    cases = tuple(
        case
        for index, case in enumerate(full.cases)
        if index % shard_count == shard_index
    )
    controlled = {
        **dict(full.controlled_variables),
        "execution_partition": {
            "shard_index": shard_index,
            "shard_count": shard_count,
            "case_count": len(cases),
            "partition_rule": "full_case_index_mod_shard_count",
        },
    }
    shard = replace(
        full,
        cases=cases,
        controlled_variables=controlled,
        description=(
            f"{full.description} Execution shard {shard_index + 1}/"
            f"{shard_count}; aggregate analysis restores the declared full grid."
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
    runs_root = Path(batch_root) / "shards" / f"shard_{shard_index:03d}"
    result = run_experiment(
        shard,
        project_root=root,
        runs_root=runs_root,
        create_figures=False,
    )
    return {
        "shard_index": shard_index,
        "case_count": len(cases),
        "success": result.success,
        "failure_count": result.failure_count,
        "required_failure_count": result.required_failure_count,
        "run_directory": str(result.run_directory),
        "spec_hash": result.spec_hash,
    }


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RuntimeError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def _completed_shard(
    batch_root: Path,
    shard_index: int,
) -> dict[str, Any] | None:
    shard_root = batch_root / "shards" / f"shard_{shard_index:03d}"
    for manifest_path in sorted(
        shard_root.glob("*/manifest.json"),
        reverse=True,
    ):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("status") == "completed"
            and int(manifest.get("required_failure_count", 0)) == 0
            and (
                manifest_path.parent
                / "analysis"
                / "vaj_sensitivity.csv"
            ).is_file()
        ):
            case_count = len(
                manifest["resolved_experiment_spec"].get("cases", [])
            )
            return {
                "shard_index": shard_index,
                "case_count": case_count,
                "success": True,
                "failure_count": int(manifest.get("failure_count", 0)),
                "required_failure_count": 0,
                "run_directory": str(manifest_path.parent),
                "spec_hash": manifest.get("spec_hash", ""),
            }
    return None


def _aggregate(
    project_root: Path,
    batch_root: Path,
    shard_results: list[dict[str, Any]],
) -> Path:
    from experiments.E14_pv_pva_vaj_fine_sensitivity.experiment import (
        _recommendation_rows,
    )

    full = load_experiment_spec(project_root, "E14")
    aggregate = batch_root / "aggregate"
    analysis = aggregate / "analysis"
    analysis.mkdir(parents=True, exist_ok=False)
    expected_fields: list[str] | None = None
    surface: list[dict[str, str]] = []
    shard_manifests = []
    for item in sorted(shard_results, key=lambda row: int(row["shard_index"])):
        run_directory = Path(str(item["run_directory"]))
        fields, rows = _read_rows(
            run_directory / "analysis" / "vaj_sensitivity.csv"
        )
        if expected_fields is None:
            expected_fields = fields
        elif fields != expected_fields:
            raise RuntimeError("E14 shard VAJ artifact headers differ")
        surface.extend(rows)
        manifest = json.loads(
            (run_directory / "manifest.json").read_text(encoding="utf-8")
        )
        shard_manifests.append(
            {
                **item,
                "manifest_status": manifest.get("status"),
                "manifest_spec_hash": manifest.get("spec_hash"),
            }
        )
    case_ids = {row["case_id"] for row in surface}
    expected_case_ids = {case.case_id for case in full.cases}
    if len(surface) != 1280 or case_ids != expected_case_ids:
        raise RuntimeError(
            "E14 aggregate is incomplete: "
            f"rows={len(surface)}, missing={len(expected_case_ids - case_ids)}, "
            f"extra={len(case_ids - expected_case_ids)}"
        )
    write_rows_csv(
        analysis / "vaj_sensitivity.csv",
        surface,
        fieldnames=expected_fields,
    )
    recommendations = _recommendation_rows(surface)
    write_rows_csv(
        analysis / "vaj_recommendations.csv",
        recommendations,
    )
    first_manifest = json.loads(
        (
            Path(str(shard_results[0]["run_directory"])) / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    manifest = {
        "schema_version": "otg.run_manifest.v1",
        "status": "completed",
        "spec_hash": sha256_json(full.as_dict()),
        "resolved_experiment_spec": full.as_dict(),
        "git": collect_git_state(project_root),
        "environment": collect_environment(),
        "inputs": first_manifest.get("inputs", {}),
        "methods": {},
        "outputs": {
            "vaj_sensitivity": "analysis/vaj_sensitivity.csv",
            "vaj_recommendations": "analysis/vaj_recommendations.csv",
        },
        "failure_count": sum(
            int(item["failure_count"]) for item in shard_results
        ),
        "required_failure_count": sum(
            int(item["required_failure_count"]) for item in shard_results
        ),
        "aggregation": {
            "type": "bounded_memory_parallel_shards",
            "shard_count": len(shard_results),
            "surface_row_count": len(surface),
            "shards": shard_manifests,
        },
    }
    write_json(aggregate / "manifest.json", manifest)
    write_json(
        batch_root / "shard_index.json",
        {
            "schema_version": "otg.e14_shards.v1",
            "aggregate_directory": str(aggregate),
            "shards": shard_manifests,
        },
    )
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, default=64)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-root", type=Path, default=None)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse completed shards in an existing --batch-root.",
    )
    arguments = parser.parse_args()
    if arguments.shards <= 0 or arguments.workers <= 0:
        parser.error("--shards and --workers must be positive")
    project_root = Path(__file__).resolve().parents[2]
    default_root = (
        Path(__file__).resolve().parent
        / "sharded_runs"
        / f"{utc_run_stamp()}__full_grid"
    )
    batch_root = (arguments.batch_root or default_root).resolve()
    if arguments.resume:
        if not batch_root.is_dir():
            parser.error("--resume requires an existing --batch-root")
    else:
        batch_root.mkdir(parents=True, exist_ok=False)

    results = [
        result
        for index in range(arguments.shards)
        if (result := _completed_shard(batch_root, index)) is not None
    ]
    completed_indexes = {int(result["shard_index"]) for result in results}
    if completed_indexes:
        print(f"resuming {len(completed_indexes)} completed shards", flush=True)
    pending = [
        index
        for index in range(arguments.shards)
        if index not in completed_indexes
    ]
    # Recreate the pool after each worker-sized wave. A shard retains hundreds
    # of thousands of trace dictionaries until its process exits; recycling
    # processes here prevents cross-wave allocator growth from exhausting RAM.
    for wave_start in range(0, len(pending), arguments.workers):
        wave = pending[wave_start : wave_start + arguments.workers]
        with ProcessPoolExecutor(max_workers=len(wave)) as executor:
            futures = {
                executor.submit(
                    _run_shard,
                    str(project_root),
                    str(batch_root),
                    index,
                    arguments.shards,
                ): index
                for index in wave
            }
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(
                    f"shard {int(result['shard_index']) + 1}/"
                    f"{arguments.shards} completed: success={result['success']}",
                    flush=True,
                )
    failures = [result for result in results if not result["success"]]
    if failures:
        write_json(batch_root / "failed_shards.json", failures)
        print(f"E14 sharded run failed in {len(failures)} shards")
        return 1
    aggregate = _aggregate(project_root, batch_root, results)
    print(f"E14 aggregate completed: {aggregate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
