"""Entry point for __ANALYSIS_ID__."""

from __future__ import annotations

import argparse
from pathlib import Path

from otg_lab.cross_analysis import prepare_analysis, write_prepared_analysis
from otg_lab.cross_analysis_reporting import create_analysis_run_directory


def main() -> int:
    parser = argparse.ArgumentParser(description="Run __ANALYSIS_ID__.")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    prepared = prepare_analysis(Path(__file__).with_name("analysis.yaml"))
    if arguments.check:
        print(
            f"{prepared.analysis_id}: validated {len(prepared.sources)} pinned "
            f"sources and {len(prepared.artifacts)} artifact schemas"
        )
        return 0
    run_directory = create_analysis_run_directory(prepared)
    write_prepared_analysis(prepared, run_directory / "work")
    print(
        f"{prepared.analysis_id}: scaffold collection written to "
        f"{run_directory}; add final tables, figures, RESULTS.md, and manifest"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
