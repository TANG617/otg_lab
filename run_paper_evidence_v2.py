#!/usr/bin/env python3
"""Thin entrypoint for the not-yet-locked paper-evidence v2 protocol."""

from run_paper_evidence import V2_PROTOCOL, main

if __name__ == "__main__":
    raise SystemExit(main(V2_PROTOCOL))
