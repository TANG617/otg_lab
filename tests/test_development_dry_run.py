from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "otg_development_dry_run",
    ROOT / "scripts" / "run_full_development_dry_run.py",
)
if SCRIPT_SPEC is None or SCRIPT_SPEC.loader is None:
    raise RuntimeError("could not load development dry-run script")
SCRIPT = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_parallel_job_count_must_be_positive(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="at least 1"):
        SCRIPT._positive_int(value)


def test_experiment_worker_uses_bundle_specific_atomic_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], Path]] = []

    def fake_run(arguments, *, protocol):
        calls.append((arguments, protocol.raw_root))
        return {"status": "complete"}

    monkeypatch.setattr(SCRIPT, "_run", fake_run)
    assert (
        SCRIPT._run_experiment(
            "locked-test", "configs/locked_test_v2.yaml", "locked_test", tmp_path
        )
        == "locked-test"
    )
    protocol = SCRIPT.build_dry_protocol(tmp_path)
    assert calls == [
        (
            [
                "locked-test",
                "--config",
                "configs/locked_test_v2.yaml",
                "--output",
                str(protocol.raw_root / "locked_test"),
            ],
            protocol.raw_root,
        )
    ]
