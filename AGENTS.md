# AGENTS.md

A focused orientation file for any agent (LLM or human) opening this
repository for the first time. It complements `README.md`,
`CONTRIBUTING.md`, and the `docs/` tree without duplicating them.

If you only read one file before touching anything, read this one.

## What this project is

`safeatomic` is a **small, deliberately bounded** Python package for
plain-file persistence with explicit, composable, runtime-inspectable
guarantees. It targets local POSIX filesystems on Linux and macOS.

Scope is one whole file at a time. It is not a WAL, not a database,
not a distributed coordination primitive. See `README.md` § "What it
is, what it is not".

## Hard invariants — do not break these

These are contractual. Violating any of them is a major version event
and requires an ADR.

1. **`__all__` is frozen at exactly 43 names.** Asserted at import
   time by `_EXPECTED_PUBLIC_NAMES = 43` in
   `src/safeatomic/__init__.py`. Adding, renaming, or removing a
   public name without the assertion update **will not import**.
2. **The eight guarantees in `docs/guarantees.md` are the public
   contract.** Weakening any cell of the guarantee matrix is a major
   version bump per ADR-0006, even when signatures do not change.
3. **`CrashDurability` is always on for `write_atomic` and
   `move_atomic`.** There is no `fsync=False` shortcut. Opting out
   would defeat the library's core promise. See ADR-0011.
4. **Cross-device moves always raise
   `CrossDeviceAtomicityError`,** regardless of `safety` policy. The
   function name promises atomicity; silent fallback would lie. See
   ADR-0008.
5. **Symlink behaviour is `Unspecified` in v2.0** and may change in
   any minor release. Callers handle symlinks before calling in. See
   ADR-0010.
6. **`fsync_policy={per_write, per_second, never}` is not adopted.**
   It belongs to a future WAL primitive (see the
   [`datawal`](https://github.com/deepcausa/datawal) sibling
   project), not to one-shot atomic writes. See ADR-0012 (in the
   private design corpus) and `docs/fsync-policy-not-adopted.md`.
7. **`safeatomic_config` scopes exactly four keys:** `encoding`,
   `checksum_algo`, `retries`, `delay`. Guarantee-affecting kwargs
   (`safety`, `concurrency`, `preserve_metadata`, `write_checksum`)
   stay explicit at call sites by design.

## What "honest" looks like here

The library prefers **failing loudly** over silent best-effort:

- Lock not acquired? `LockError`. No silent reentrancy.
- Cross-device replace impossible? `CrossDeviceAtomicityError`. No
  silent copy+delete fallback.
- Filesystem not recognised under `safety='strict'`?
  `UnsupportedEnvironmentError` before any I/O.
- Parent-directory fsync fails post-replace? Re-raise under
  `safety='strict'`; the file is visible, but `CrashDurability` is
  **not** confirmed and the caller learns that.
- Checksum sidecar absent? `FileNotFoundError`, distinct from
  `ChecksumMismatchError`. Absence is not corruption.

`safety='warn'` emits `UnsupportedEnvironmentWarning` and proceeds.
`safety='best_effort'` proceeds silently. `safety='strict'` is the
default. See ADR-0011.

## Honest API surprises (observed from running the examples)

These trip up newcomers regardless of how good the docstrings are:

1. **`write_atomic` defaults to `concurrency='lock'`.** If you
   already hold a lock via `try_acquire_lock(...)`, you must pass
   `concurrency='none'` or `write_atomic` will try to lock again and
   raise `LockError`. There is no implicit reentrancy.
2. **`try_acquire_lock(path, ...)` returns `bool`,** not a lock
   handle. To inspect the on-disk lock state use `inspect_lock(path)
   -> LockInfo`.
3. **The kwarg is `check_checksum`,** not `require_checksum`.
4. **`AtomicWriter.write()` accepts bytes only** — no `mode=` or
   `encoding=` kwargs. Encoding is the caller's job before the
   `.write()`.
5. **`safeatomic_config` is a context manager** decorated with
   `@contextmanager`. Use it as `with safeatomic_config(...): ...`.
6. **Explicit kwargs at call sites always win** over
   `safeatomic_config` scope. See `docs/api-reference.md` and ADR-0005.

## Layout

```
src/safeatomic/             # source — 15 files, all underscore-prefixed
                            # except __init__.py exporting the 43 names
tests/                      # 462 tests, organised by surface area
docs/                       # user-facing documentation (10 .md files)
examples/                   # 8 runnable scripts + README, in sdist not wheel
formal/                     # 3 TLA+ models + .cfg + reports, in sdist not wheel
  reports/MANIFEST.json     # toolchain pinning + state counts
  reports/2026-05-19-*.txt  # raw TLC stdout from canonical runs
scripts/check-formal.sh     # local TLA+ runner with TLC_JAR support
.github/workflows/
  ci.yml                    # lint+type + tests 3.12 + 3.13 + coverage
  formal.yml                # TLA+ on push to main + workflow_dispatch
  publish.yml               # release-triggered PyPI publish
  dependabot.yml            # github-actions ecosystem, weekly Monday
```

There is also a **private** companion corpus at
`apps/safeatomic-project/` containing the full ADR set (0001–0012),
formal model originals, decisions-from-review.md, and conversations.
It is referenced from public docs as `safeatomic-project/...` but is
**not** part of the published artefact and may not be public.

## Toolchain pinning

- Python: `>=3.12` declared in `pyproject.toml`; CI matrix runs 3.12
  and 3.13.
- Ruff, mypy, pytest: versions floated in `[dev,test]` extras.
- TLA+: `tla2tools.jar` v1.7.4, SHA-256
  `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`,
  Java 21 (temurin). Pinned in `formal/reports/MANIFEST.json` and in
  `.github/workflows/formal.yml`.
- All GitHub Actions are SHA-pinned to specific tagged releases.
  Dependabot raises PRs grouped into a single weekly run.

## The release flow

See [`docs/release-process.md`](docs/release-process.md) for the full
procedure. Short version:

1. Bump `version` in `pyproject.toml` and add an entry to
   `CHANGELOG.md` under a new `[X.Y.Z]` section.
2. Open a `release/vX.Y.Z` branch, PR to `main`, wait for the three
   required status checks (`Lint + type-check`, `Tests (Python 3.12)`,
   `Tests (Python 3.13)`), squash-merge.
3. Create a GitHub Release pointing at the new `main` HEAD with tag
   `vX.Y.Z`. The publish workflow triggers on `release: published`.
4. The `pypi` environment requires manual approval before the
   publish job runs. Approve via the Actions UI. After approval the
   `pypa/gh-action-pypi-publish` action uploads the artefacts.
5. Verify the artefact landed: `pip install safeatomic==X.Y.Z` in a
   clean venv, smoke-test `__version__`, `len(__all__) == 43`,
   round-trip.

The `publish.yml` build step verifies in-Python (no shell pipes) that:
- the sdist ships `formal/` (3 `.tla`), `examples/README.md`, two
  representative scripts, `docs/formal-models.md`,
  `scripts/check-formal.sh`;
- the wheel ships none of `formal/`, `examples/`, `tests/`,
  `scripts/`.

## Branch protection on `main`

- PR required before merging
- Linear history required
- Three status checks must pass: `Lint + type-check`,
  `Tests (Python 3.12)`, `Tests (Python 3.13)`
- Bypass is not allowed, even for admins. The only path is "open a
  branch, push, PR, squash-merge".
- Squash-merge is the only enabled merge method (no merge commits, no
  rebase merges). The PR title + body becomes the squash commit.

`AGENTS.md` notes the policy because the constraint is real: trying
to `git push main` directly returns a remote-side rejection. Do not
try to bypass.

## Family of repos

`safeatomic` is part of a small family of local persistence primitives:

- [`safeatomic-rs`](https://github.com/deepcausa/safeatomic-rs) — Rust
  sibling crate with low-level filesystem primitives. **Not** a
  binding, **not** a 1:1 port. Different surface, same engineering
  values.
- [`datawal`](https://github.com/deepcausa/datawal) — experimental
  Rust record store for append-only frames, recovery, KV projection.
  This is where the future of `fsync_policy` belongs (ADR-0012).

Cross-linked from `README.md` § "Related projects", `docs/index.md`,
and `docs/alternatives.md`.

## Don'ts

- Do not modify `__all__` without updating `_EXPECTED_PUBLIC_NAMES`
  and the matching test in `tests/test_package_metadata.py`.
- Do not add `fsync=False`, `fsync_policy`, or any flag that lets a
  caller skip the durability fsyncs in `write_atomic` /
  `move_atomic`.
- Do not introduce silent best-effort fallbacks in functions whose
  name implies atomicity or durability.
- Do not couple `safeatomic_config` to guarantee-affecting kwargs.
- Do not add support for Windows / NFS / SMB / object stores under
  `safety='strict'` without an ADR.
- Do not commit `tools/`, `dev/`, or any local scratch directory.
  `.gitignore` already excludes both.

## Where to ask "is this in scope?"

- For new behaviour: open a GitHub issue with the rationale.
- For internal questions / ADR-class decisions: write a short note in
  the private `apps/safeatomic-project/notes/` corpus and link from
  the issue.
- For "I broke the build, how do I unbreak it?":
  [`docs/development.md`](docs/development.md) is the local-CI
  reproduction recipe.
