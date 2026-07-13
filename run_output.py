"""Create consistently named directories for disposable experiment runs."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"


def _slug(value):
    text = str(value).strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9.-]+", "-", text)
    return text.strip("-") or "none"


def prepare_run_directory(experiment, parameters, output_dir=None):
    """Create an output directory and record the parameters for the run."""
    created_at = datetime.now().astimezone()
    if output_dir is None:
        name_parts = [created_at.strftime("%Y%m%d-%H%M%S"), _slug(experiment)]
        name_parts.extend(
            f"{_slug(name)}-{_slug(value)}" for name, value in parameters.items()
        )
        base = RUNS_DIR / "__".join(name_parts)
        output_dir = base
        duplicate = 2
        while True:
            try:
                output_dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                output_dir = Path(f"{base}__repeat-{duplicate:02d}")
                duplicate += 1
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "experiment": experiment,
        "created_at": created_at.isoformat(timespec="seconds"),
        "parameters": dict(parameters),
    }
    (output_dir / "run.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_dir
