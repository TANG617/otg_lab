"""Entry point for A01."""

from __future__ import annotations

from pathlib import Path

from otg_lab.cross_analysis import main

if __name__ == "__main__":
    raise SystemExit(main(default_config=Path(__file__).with_name("analysis.yaml")))
