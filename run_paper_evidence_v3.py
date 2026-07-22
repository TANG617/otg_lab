#!/usr/bin/env python3
"""Thin entrypoint for the fresh paper-evidence v3 protocol."""

from run_paper_evidence import V3_PROTOCOL, main

if __name__ == "__main__":
    raise SystemExit(main(V3_PROTOCOL))
