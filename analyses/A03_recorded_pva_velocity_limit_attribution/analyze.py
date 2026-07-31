"""Entry point for A03."""

from __future__ import annotations

import argparse

from analysis_impl import run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the A03 recorded PVA velocity-limit attribution.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the pinned source and decisions without writing outputs.",
    )
    arguments = parser.parse_args()
    return run(check_only=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
