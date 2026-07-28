"""Small, deterministic I/O helpers for experiment run artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of a file without loading it all into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonable(value: Any) -> Any:
    """Convert experiment values to stable JSON-compatible objects."""

    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, np.ndarray):
        return [jsonable(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, Mapping):
        return {
            str(key): jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if callable(value):
        module = getattr(value, "__module__", "")
        name = getattr(value, "__qualname__", getattr(value, "__name__", repr(value)))
        return f"{module}.{name}" if module else name
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a value for hashing with stable key and whitespace choices."""

    return json.dumps(
        jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            jsonable(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return format(value, ".17g")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list, tuple, set, np.ndarray)):
        return json.dumps(
            jsonable(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return value


def write_rows_csv(
    path: str | Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str] | None = None,
) -> None:
    """Write tidy mapping rows with a deterministic union of columns."""

    materialized = [dict(row) for row in rows]
    if fieldnames is None:
        ordered: list[str] = []
        seen: set[str] = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    ordered.append(key)
                    seen.add(key)
        fieldnames = ordered
    columns = list(fieldnames)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        for row in materialized:
            writer.writerow({name: _csv_value(row.get(name)) for name in columns})


def utc_run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def collect_git_state(project_root: str | Path) -> dict[str, Any]:
    """Collect provenance without imposing a clean-worktree requirement."""

    root = Path(project_root)

    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return result.stdout.strip()

    status = run("status", "--porcelain")
    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": None if status is None else bool(status),
        "status_porcelain": [] if not status else status.splitlines(),
    }


def collect_environment() -> dict[str, Any]:
    versions: dict[str, str] = {}
    for package in ("numpy", "scipy", "osqp", "ruckig", "matplotlib"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            continue
    return {
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": sys.platform,
        "packages": versions,
    }


__all__ = [
    "canonical_json_bytes",
    "collect_environment",
    "collect_git_state",
    "jsonable",
    "sha256_file",
    "sha256_json",
    "utc_run_stamp",
    "write_json",
    "write_rows_csv",
]
