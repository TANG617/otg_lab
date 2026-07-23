#!/usr/bin/env python3
"""Narrow, version-pinned entry point for the V4 confirmation program."""

import os
import sys
import types
from importlib import import_module
from importlib.machinery import ModuleSpec
from pathlib import Path

# This must precede importing ``otg_lab``: its package initializer imports
# NumPy-backed pipeline modules, so setting thread policy later is too late.
for _name in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_name] = "1"

# Avoid executing ``otg_lab.__init__``: it eagerly imports the online pipeline,
# which is forbidden on the report-only path.  A minimal package shell still
# gives ordinary submodules correct relative-import semantics.
_package_root = Path(__file__).resolve().parent / "otg_lab"
_package = types.ModuleType("otg_lab")
_package.__path__ = [str(_package_root)]
_package.__package__ = "otg_lab"
_package.__file__ = str(_package_root / "__init__.py")
_package.__spec__ = ModuleSpec("otg_lab", loader=None, is_package=True)
sys.modules["otg_lab"] = _package

main = import_module("otg_lab.v4_runner").main


if __name__ == "__main__":
    raise SystemExit(main())
