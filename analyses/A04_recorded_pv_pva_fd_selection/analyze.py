"""Entry point for A04."""

from __future__ import annotations

import argparse

from analysis_impl import run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the A04 recorded PV/PVA finite-difference selection.",
    )
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    return run(check_only=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
