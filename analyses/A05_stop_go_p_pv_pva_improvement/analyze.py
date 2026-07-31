"""Entry point for A05."""

from __future__ import annotations

import argparse

from analysis_impl import run


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A05 stop-and-go analysis.")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    return run(check_only=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
