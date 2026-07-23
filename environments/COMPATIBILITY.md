# Ruckig compatibility environments

The formal environment is locked by `uv.lock` and uses Python 3.9 with
Ruckig 0.17.3. The comparison environment is resolved independently from
`community-requirements.in` and uses Ruckig 0.19.4. Neither compatibility
probe downloads test data; both consume only repository-owned deterministic
references.

Ruckig 0.17.3 is distributed as an sdist. Its build metadata still uses the
legacy `cmake.targets` key, so cold builds first use the hashed
`build-requirements.lock.txt` environment to compile the hashed source in
`ruckig-source.lock.txt`. The following frozen sync removes build-only packages
from the exact runtime environment.

## Rebuild and run

```bash
uv pip compile environments/build-requirements.in \
  --python-version 3.9 --generate-hashes \
  --custom-compile-command "uv pip compile environments/build-requirements.in --python-version 3.9 --generate-hashes --output-file environments/build-requirements.lock.txt" \
  --output-file environments/build-requirements.lock.txt
uv venv .venv --python 3.9
uv pip install --python .venv/bin/python --require-hashes \
  -r environments/build-requirements.lock.txt
uv pip install --python .venv/bin/python --require-hashes \
  --no-build-isolation --no-deps \
  -r environments/ruckig-source.lock.txt
uv sync --extra dev --frozen
PYTHONPATH=. .venv/bin/python scripts/run_ruckig_compatibility.py \
  --output environments/compatibility-results/ruckig-0.17.3.json

uv pip compile environments/community-requirements.in \
  --python-version 3.9 --generate-hashes \
  --custom-compile-command "uv pip compile environments/community-requirements.in --python-version 3.9 --generate-hashes --output-file environments/community-requirements.lock.txt" \
  --output-file environments/community-requirements.lock.txt
uv venv .venv-community --python 3.9
uv pip install --python .venv-community/bin/python --require-hashes \
  -r environments/community-requirements.lock.txt
PYTHONPATH=. .venv-community/bin/python scripts/run_ruckig_compatibility.py \
  --output environments/compatibility-results/ruckig-0.19.4.json
```

`PYTHONPATH=.` is intentional: `scripts/run_ruckig_compatibility.py` also
probes the repository's legacy top-level modules, which are not part of the
`otg_lab` package declaration.

`--output` is optional. Omitting it writes the same JSON probe result to
stdout, for example:

```bash
uv run python scripts/run_ruckig_compatibility.py
```

## Recorded run: 2026-07-21, macOS arm64

- Repository baseline: `136842317b88b7819a6c726b057545531a916af3`
- Resolver/installer: uv 0.11.19
- Formal `uv.lock` SHA-256:
  `5ce6b6f7bd8c8575c98053918fab3c97e8e00063f7932d9b62aa5994d985c323`
- Legacy build requirements lock SHA-256:
  `b0500c8d0a2b6138a9d527c5ab2fd5ecda0aa6d340a14237864cce448a98b77f`
- Legacy Ruckig source lock SHA-256:
  `f2ea21b1e1b2303ada59d931ae390e921192b863f226527c20e081142fd45cfc`
- Community requirements lock SHA-256:
  `03cd0292c4a29384fb4dafab12589865527f5807394b6c9207eebbcf382ae5d6`
- Python: 3.9.6 in both isolated interpreters
- Formal environment: NumPy 2.0.2, Ruckig 0.17.3
- Community environment: NumPy 2.0.2, Ruckig 0.19.4
- P, PV-truth, and PVA-truth RMSE values were bit-for-bit identical.
- All three command-position SHA-256 digests were identical.
- Governor jerk, executable state, direct follower state, ordinary Ruckig
  follower state, and free duration were identical.
- Both probes reported zero continuous constraint violations.
- `Trackig` and `Tracking` were absent in both open-source Python modules.
- A clean Debian bookworm container then built the hashed 0.17.3 sdist with
  Python 3.9.25, completed the frozen sync, removed the build-only toolchain,
  and passed the 330-row causal smoke with no fallback or constraint violation.
  Its platform-specific command trace SHA-256 was
  `c299d4edffe8cd1d8de5ef1cd11a00a72f98ef02946deefb89af7d665257cfd7`.

Negative result retained for audit: invoking the compatibility script without
`PYTHONPATH=.` failed immediately with `ModuleNotFoundError: otg_runner`.
The documented command and CI smoke set the repository root explicitly; no
scientific result was discarded or silently repaired.

A second negative result was reproduced in a clean Python 3.9 Linux container:
an unseeded isolated build selected `scikit-build-core>=0.10`, which rejects
`cmake.targets`. `uv sync` does not expose build constraints, so CI now builds
the sdist once with the exact 0.9.10 backend, then performs the required frozen
sync and tests the resulting runtime normally.

The version-named JSON files under `compatibility-results/` are the complete
raw probe outputs. `comparison.json` records the exact equality checks and the
negative invocation results in machine-readable form.
