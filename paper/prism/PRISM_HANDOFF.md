# Prism handoff

## Canonical-source rule

Prism is a milestone review and collaboration environment. It is not a second
simultaneously writable manuscript. The canonical source is the `.tex` tree in
Git, together with its content-addressed logic and generated evidence
manifests.

A Prism import package may be made only from a recorded Git commit:

```sh
make prism-package
```

Record the source commit, logic-lock hash, package SHA-256, and review
milestone before import. The package intentionally excludes raw experiment
bundles, Git history, caches, secrets, local paths, release archives, and
temporary Prism exports.

## Reviewing an export

Never unpack a Prism export over the canonical checkout. Use a new temporary
directory and compare it:

```sh
python3 scripts/compare_prism_export.py /absolute/path/to/unpacked-export
```

Review the resulting diff against the claim/evidence matrix. Apply accepted
changes deliberately to the Git source; do not bulk-copy the export. In
particular, reject changes that strengthen a claim, introduce an unregistered
number, weaken a negative result, blur current/frozen/post-freeze scope, or
change native/shielded/fallback method identity without an evidence and logic
review.

After merging accepted edits:

1. update `logic/decision_log.md` if title, scope, claim wording, or evidence
   classification changed;
2. rebuild and verify the logic lock when such a logic change occurred;
3. regenerate any affected source-backed artifacts;
4. run `make check`;
5. record the completed Prism milestone and the new canonical Git commit.

Only the reviewed Git result may seed the next Prism import. A Prism export is
never, by itself, a release source.
