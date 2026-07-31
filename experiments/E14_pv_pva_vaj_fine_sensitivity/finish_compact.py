"""Finish E14 sequentially with constant memory and compact artifacts only."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from experiment import _recommendation_rows

from otg_lab.cli import load_experiment_spec
from otg_lab.runio import (
    collect_environment,
    collect_git_state,
    sha256_json,
    write_json,
    write_rows_csv,
)


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RuntimeError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def _collect_completed_surfaces(
    batch_root: Path,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, Any]]]:
    fields: list[str] | None = None
    by_case: dict[str, dict[str, str]] = {}
    provenance: list[dict[str, Any]] = []
    patterns = (
        "shards/shard_*/*/analysis/vaj_sensitivity.csv",
        "micro_shards/micro_*/*/analysis/vaj_sensitivity.csv",
    )
    for pattern in patterns:
        for path in sorted(batch_root.glob(pattern)):
            manifest_path = path.parents[1] / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                manifest.get("status") != "completed"
                or int(manifest.get("required_failure_count", 0)) != 0
            ):
                continue
            current_fields, rows = _read_rows(path)
            if fields is None:
                fields = current_fields
            elif current_fields != fields:
                raise RuntimeError("completed E14 surface headers differ")
            new_count = 0
            for row in rows:
                case_id = row["case_id"]
                if case_id in by_case:
                    continue
                by_case[case_id] = row
                new_count += 1
            provenance.append(
                {
                    "run_directory": str(manifest_path.parent),
                    "spec_hash": manifest.get("spec_hash"),
                    "failure_count": manifest.get("failure_count", 0),
                    "input_row_count": len(rows),
                    "new_unique_case_count": new_count,
                }
            )
    if fields is None:
        raise RuntimeError("no completed E14 shard surface was found")
    return fields, list(by_case.values()), provenance


def _load_compact_rows(
    path: Path,
    expected_fields: list[str],
) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    fields, rows = _read_rows(path)
    if fields != expected_fields:
        raise RuntimeError("compact resume CSV header changed")
    return rows


def _safe_prune_intermediates(batch_root: Path) -> None:
    expected_parent = (
        Path(__file__).resolve().parent / "sharded_runs"
    ).resolve()
    resolved = batch_root.resolve()
    if resolved.parent != expected_parent or not resolved.name.endswith(
        "__full_grid"
    ):
        raise RuntimeError(f"refusing to prune unexpected batch path: {resolved}")
    for name in ("shards", "micro_shards"):
        target = resolved / name
        if target.is_dir():
            shutil.rmtree(target)


def _write_aggregate(
    project_root: Path,
    batch_root: Path,
    full: Any,
    fields: list[str],
    rows: list[dict[str, Any]],
    provenance: list[dict[str, Any]],
) -> Path:
    by_case = {str(row["case_id"]): row for row in rows}
    expected = {case.case_id for case in full.cases}
    if len(rows) != len(by_case) or set(by_case) != expected:
        raise RuntimeError(
            "compact E14 aggregate is incomplete: "
            f"rows={len(rows)}, unique={len(by_case)}, "
            f"missing={len(expected - set(by_case))}"
        )
    aggregate = batch_root / "aggregate"
    if aggregate.exists():
        raise FileExistsError(f"aggregate already exists: {aggregate}")
    analysis = aggregate / "analysis"
    analysis.mkdir(parents=True)
    ordered = [by_case[case.case_id] for case in full.cases]
    write_rows_csv(
        analysis / "vaj_sensitivity.csv",
        ordered,
        fieldnames=fields,
    )
    write_rows_csv(
        analysis / "vaj_recommendations.csv",
        _recommendation_rows(ordered),
    )
    crash_source = batch_root / "compact_work" / "native_crash_audit.jsonl"
    crash_count = 0
    if crash_source.is_file():
        shutil.copy2(crash_source, analysis / "native_crash_audit.jsonl")
        with crash_source.open(encoding="utf-8") as handle:
            crash_count = sum(1 for line in handle if line.strip())
    failure_count = sum(
        str(row.get("completed", "")).strip().lower() != "true"
        for row in ordered
    )
    manifest = {
        "schema_version": "otg.run_manifest.v1",
        "status": "completed",
        "spec_hash": sha256_json(full.as_dict()),
        "resolved_experiment_spec": full.as_dict(),
        "git": collect_git_state(project_root),
        "environment": collect_environment(),
        "inputs": {},
        "methods": {},
        "outputs": {
            "vaj_sensitivity": "analysis/vaj_sensitivity.csv",
            "vaj_recommendations": "analysis/vaj_recommendations.csv",
            **(
                {"native_crash_audit": "analysis/native_crash_audit.jsonl"}
                if crash_count
                else {}
            ),
        },
        "failure_count": failure_count,
        "required_failure_count": 0,
        "aggregation": {
            "type": "compacted_shards_plus_constant_memory_sequential",
            "surface_row_count": len(ordered),
            "reused_source_count": len(provenance),
            "diagnostic_incomplete_case_count": failure_count,
            "isolated_native_crash_case_count": crash_count,
            "intermediate_shard_internals_pruned": True,
            "sources": provenance,
        },
    }
    write_json(aggregate / "manifest.json", manifest)
    return aggregate


def _native_crash_row(
    fields: list[str],
    case: Any,
    *,
    total_cycles: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {field: "" for field in fields}
    row.update(
        {
            "input_id": "recorded_tasks_simplified_with_velocity_limit",
            "method_id": case.method_id,
            "target_components": (
                "PV" if case.method_id.startswith("pv_") else "PVA"
            ),
            "case_id": case.case_id,
            "max_velocity_rad_s": (
                case.run_config.limits.max_velocity_rad_s
            ),
            "max_acceleration_rad_s2": (
                case.run_config.limits.max_acceleration_rad_s2
            ),
            "max_jerk_rad_s3": case.run_config.limits.max_jerk_rad_s3,
            "is_vendor_setting": False,
            "completed": False,
            "valid_cycles": 0,
            "total_cycles": total_cycles,
            "eligible": False,
        }
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument(
        "--prune-intermediates",
        action="store_true",
        help="Delete batch-local shard internals after compacting their surface rows.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        choices=(1, 2),
        default=2,
        help="Number of isolated case subprocesses (bounded to 1 or 2).",
    )
    arguments = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    batch_root = arguments.batch_root.resolve()
    if not batch_root.is_dir():
        parser.error("--batch-root must be an existing E14 batch")

    work = batch_root / "compact_work"
    work.mkdir(exist_ok=True)
    compacted_path = work / "completed_shard_surface.csv"
    remaining_path = work / "sequential_surface.csv"
    provenance_path = work / "compacted_shard_provenance.json"

    if compacted_path.is_file():
        fields, completed_rows = _read_rows(compacted_path)
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    else:
        fields, completed_rows, provenance = _collect_completed_surfaces(
            batch_root
        )
        write_rows_csv(compacted_path, completed_rows, fieldnames=fields)
        write_json(provenance_path, provenance)
    if arguments.prune_intermediates:
        _safe_prune_intermediates(batch_root)
        print("pruned batch-local shard internals after compacting surfaces")

    sequential_rows = _load_compact_rows(remaining_path, fields)
    by_case = {
        str(row["case_id"]): row for row in (*completed_rows, *sequential_rows)
    }
    full = load_experiment_spec(project_root, "E14")
    total_cycles = 7672
    remaining = [case for case in full.cases if case.case_id not in by_case]
    print(
        f"reusing {len(by_case)} compact rows; running {len(remaining)} cases "
        "sequentially with constant memory",
        flush=True,
    )
    mode = "a" if remaining_path.is_file() else "w"
    crash_audit_path = work / "native_crash_audit.jsonl"
    helper = Path(__file__).resolve().with_name("run_one_compact_case.py")
    with remaining_path.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        if mode == "w":
            writer.writeheader()
        completed_in_run = 0
        for wave_start in range(0, len(remaining), arguments.workers):
            wave = remaining[wave_start : wave_start + arguments.workers]
            processes = []
            for slot, case in enumerate(wave):
                isolated_output = work / f"isolated_case_result_{slot}.json"
                isolated_output.unlink(missing_ok=True)
                process = subprocess.Popen(
                    (
                        sys.executable,
                        str(helper),
                        "--case-id",
                        case.case_id,
                        "--output",
                        str(isolated_output),
                    ),
                    cwd=project_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                processes.append((case, isolated_output, process))
            for case, isolated_output, process in processes:
                _stdout, stderr = process.communicate()
                if process.returncode == 0 and isolated_output.is_file():
                    row = json.loads(
                        isolated_output.read_text(encoding="utf-8")
                    )
                else:
                    row = _native_crash_row(
                        fields,
                        case,
                        total_cycles=total_cycles,
                    )
                    with crash_audit_path.open("a", encoding="utf-8") as audit:
                        audit.write(
                            json.dumps(
                                {
                                    "case_id": case.case_id,
                                    "returncode": process.returncode,
                                    "stderr_tail": stderr[-2000:],
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            + "\n"
                        )
                writer.writerow(row)
                handle.flush()
                by_case[case.case_id] = row
                completed_in_run += 1
                if (
                    completed_in_run % 10 == 0
                    or completed_in_run == len(remaining)
                ):
                    print(
                        f"compact E14 progress: {len(by_case)}/1280 cases",
                        flush=True,
                    )

    aggregate = _write_aggregate(
        project_root,
        batch_root,
        full,
        fields,
        list(by_case.values()),
        provenance,
    )
    print(f"E14 aggregate completed: {aggregate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
