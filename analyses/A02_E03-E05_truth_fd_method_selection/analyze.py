"""Entry point for the complete A02 analysis."""

from __future__ import annotations

import argparse

from analysis_impl import run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the A02 truth-versus-FD method-selection analysis.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate sources and decisions without writing outputs.",
    )
    arguments = parser.parse_args()
    return run(check_only=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
