# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with one extension: weakening any documented guarantee is a major version
bump, even if no signatures change.

Sections used:

- `Added` — new features
- `Changed` — changes in existing functionality
- `Deprecated` — features marked for removal in a future version
- `Removed` — features removed in this version
- `Fixed` — bug fixes
- `Security` — vulnerabilities addressed
- `Guarantees` — *safeatomic-specific*; documents any change to the
  documented guarantee matrix, even if no other change accompanies it

## [Unreleased]

### Added

- CI: TLA+ model-checking of the three protocol specs.
  - New workflow `.github/workflows/formal.yml`. Triggers on pushes to
    `main` whose diff touches `formal/**`, `scripts/check-formal.sh` or
    the workflow itself, and on `workflow_dispatch`. Per-PR runs are
    intentionally not included to keep PR feedback fast.
  - The job downloads `tla2tools.jar` v1.7.4 from the official TLA+
    release, **verifies its SHA-256** against the value pinned in
    `formal/README.md`, caches it (cache key includes the SHA-256 so a
    version bump invalidates the cache), and runs
    `scripts/check-formal.sh` end to end.
  - TLC output is uploaded as the `tlc-output` artifact (30-day
    retention) and inlined into `$GITHUB_STEP_SUMMARY`.
  - The job times out at 10 minutes (TLC currently finishes in
    well under a minute for all three specs on GitHub-hosted runners).
- `README.md` shows a "Formal models (TLA+)" status badge.
- CI: code coverage measurement and reporting.
  - `pytest` now runs with `--cov --cov-report=term-missing:skip-covered
    --cov-report=xml --cov-report=json` on both Python 3.12 and 3.13.
  - Each matrix job writes a coverage summary to `$GITHUB_STEP_SUMMARY`
    showing percentage, covered/total statements, covered/total
    branches, missing lines, and partial branches.
  - The Python 3.12 job uploads `coverage.xml` as a workflow artifact
    (`coverage-xml`, 30-day retention, via `actions/upload-artifact`
    v7.0.1) and posts it to Codecov.
  - The Codecov upload uses `fail_ci_if_error: false` so a Codecov
    outage does not block CI.
- `README.md` now also shows a Codecov badge.

## [2.0.2] - 2026-05-19

First public release reaching PyPI. Documentation- and packaging-only
bump on top of 2.0.1 (which lived as a GitHub release only and was never
published to PyPI; that tag and release have been removed). No
source-code changes vs. 2.0.1; the public surface remains frozen at the
same 43 exported names asserted at import time by
`_EXPECTED_PUBLIC_NAMES = 43`.

### Added

- `docs/` user-facing guide:
  - `docs/index.md` — index linking every document and external resource
  - `docs/getting-started.md` — installation and first write/read
  - `docs/guarantees.md` — four primary and four supporting guarantees,
    composability and limits
  - `docs/supported-environments.md` — Tier 1/2/3 (NonTarget) matrix,
    safety policy, symlinks
  - `docs/doctor.md` — `inspect_guarantees` vs `doctor`, destructive
    probes, usage patterns
  - `docs/api-reference.md` — the 43 public names grouped by category,
    one sentence each, with cross-links to examples
- `examples/` runnable scripts (shipped in the sdist, excluded from the
  wheel): eight progressive scripts (`01_write_read_basic` through
  `08_config_safety_policy`) plus `examples/README.md` with the index
  table and an "honest surprises" section.
- Per-file ruff ignores for `examples/**` (`T20`, `D`, `S101`,
  `PLR2004`, `TRY003`, `TRY301`, `BLE001`) so example code can use
  `print` and `assert` without lint noise without relaxing rules
  elsewhere.

### Changed

- `[tool.hatch.build.targets.sdist]` now also includes `examples/**`.
  The wheel build target remains `src/safeatomic` only.
- `.github/workflows/publish.yml` verify step extended:
  - asserts the sdist contains `examples/README.md` and representative
    example scripts in addition to `formal/`, `docs/formal-models.md`
    and `scripts/check-formal.sh`;
  - asserts the wheel ships none of `formal/`, `examples/`, `tests/`,
    `scripts/` (previously only `formal/` was checked).
- `README.md` "Docs" link now points directly at `docs/index.md`.

### Guarantees

No change to the guarantee matrix. `__all__` remains frozen at 43 names.
No new exception classes. No behaviour change in `src/safeatomic/`.

## [2.0.1] - 2026-05-19 [withdrawn]

> **Not published to PyPI.** This version existed as a GitHub release
> only; the release and tag were deleted before publication when a
> packaging-verification bug was discovered. The fixes and additions
> below are included in 2.0.2.

No source code changes vs. 2.0.0. This bump shipped the TLA+ models,
model-checking runner, and supporting documentation alongside the
package, and re-pointed project URLs at the canonical public
repository.

### Added

- `formal/` directory shipped in the source distribution (excluded from
  the wheel): three TLA+ models (`SafeAtomicSmoke.tla`,
  `SafeAtomicLock.tla`, `SafeAtomicChecksum.tla`) with their `.cfg`
  files, a `formal/README.md` orientation note, and
  `formal/reports/MANIFEST.json` plus raw TLC stdout reports from the
  canonical run (TLC 2.19, tla2tools v1.7.4
  sha256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`,
  OpenJDK 21.0.11). The MANIFEST records what each model proves and
  explicitly lists what model-checking does **not** verify.
- `scripts/check-formal.sh` — POSIX shell runner for the three models.
  Honours `TLC_JAR` env var or `~/.local/bin/tlc` wrapper; writes to a
  temp directory by default; `--update-reports` overwrites the committed
  reports for canonical refresh. Documented exit codes.
- `docs/formal-models.md` — overview of the three-layer evidence stack
  (models fix the protocol contract; tests exercise the implementation;
  runtime probes inspect the environment). Explicit "not formally
  verified" framing.
- `docs/fsync-policy-not-adopted.md` — pointer document explaining why
  safeatomic does not expose a Redis-style `fsync_policy` knob, and where
  that decision is recorded (ADR-0012 in the private design corpus).

### Changed

- `[project.urls]` re-pointed at `https://github.com/deepcausa/safeatomic`
  (Homepage, Repository, Issues, Changelog).
- `[tool.hatch.build.targets.sdist]` now also includes `formal/**` and
  `scripts/**`. The wheel build target remains `src/safeatomic` only; a
  comment in `pyproject.toml` documents this asymmetry.
- README §"Formal model" replaced by §"Formal protocol models" that
  points at the local `formal/` directory and `docs/formal-models.md`,
  and explicitly states the wheel does not ship these models.
- `tests/test_tla.py` reads the local `formal/` directory under the repo
  root (no longer a sibling design corpus path), accepts a `TLC_JAR` env
  var as an alternative to the `~/.local/bin/tlc` wrapper, and skips
  cleanly when TLC or Java are unavailable.

### Guarantees

No change to the guarantee matrix. `__all__` remains frozen at 43 names
(asserted at import time by `_EXPECTED_PUBLIC_NAMES = 43`). No new
exception classes. No behaviour change in `src/safeatomic/`.

## [2.0.0] - 2026-05-18

First stable v2 release. Public API frozen at 43 explicitly exported names.
All guarantees documented in `design/guarantees-formalization.md` and the
ADR series under `safeatomic-project/adr/`.

### Added

- ADR-0008: late-EXDEV normalisation. `move_atomic` wraps the final
  `os.replace` and translates any `OSError(errno.EXDEV)` into
  `CrossDeviceAtomicityError(src, dst)` with `__cause__` set to the
  original `OSError` for diagnostics. Uniform across `safety="strict"`,
  `"warn"`, and `"best_effort"`. No copy/delete fallback.
- ADR-0009: missing checksum sidecar is `FileNotFoundError`. Both
  `verify_checksum(path)` and `read_atomic(path, check_checksum=True)`
  raise `FileNotFoundError` when the `.sha256` sidecar is absent.
  `ChecksumMismatchError` is reserved for genuine mismatches with real
  hex digests on both sides. No new exception class.
- ADR-0010: `SymlinkPolicy = Unspecified` recorded as a binding public
  contract. Callers with symlink-sensitive workloads must resolve
  (`Path.resolve(strict=True)`) or reject (`Path.is_symlink()`) before
  invoking write/read/move APIs. Adopting any policy in the future is a
  major-version change.
- ADR-0011: parent-directory `fsync` failure dispatch. `_fsync_dir` now
  routes failures through `safety`:
  - `strict` re-raises the raw `OSError`.
  - `warn` emits `UnsupportedEnvironmentWarning` and continues.
  - `best_effort` is silent (DEBUG-level log only).

  No rollback under any branch (the `os.replace` already succeeded; the
  previous content is gone). No new exception class.
- Regression test module `tests/test_regressions_v2_0_bugfixes.py`
  pinning the four pre-release bugfixes and the three new parent-fsync
  dispatch branches (+3 tests vs. pre-closure).

### Changed

- `_fsync_dir` (private) split into `_fsync_dir` + internal
  `_fsync_dir_handle_failure(directory, exc, safety, *, stage)`. Three
  call sites (`_write_core` step 12, `move_atomic` step 6,
  `AtomicWriter.commit`) now forward the effective `safety` policy.
- `atomic_json_dump`, `atomic_yaml_dump`, and `atomic_yaml_dump_ruamel`
  now accept an explicit `encoding=` keyword and honour
  `safeatomic_config(encoding=...)` symmetrically with the loader
  counterparts. Previously the dumpers ignored `safeatomic_config`.
- README §Safety policy extended with four subsections (late-EXDEV,
  parent-dir fsync, checksum sidecars, symbolic links) cross-referencing
  ADRs 0008–0011.
- `decisions-from-review.md`, `failure-model.md`,
  `guarantees-formalization.md`, and `api-v2-proposal.md` annotated
  with the four new ADR cross-references.

### Fixed

- `AtomicWriter.__exit__` no longer commits after explicit
  `.abort()` / `.commit()`. New `_aborted` flag short-circuits the
  context-manager exit when the user already finalised the writer
  explicitly (no double-commit, no commit-after-abort).
- `move_atomic` no longer leaks raw `OSError(EXDEV)` from the final
  `os.replace` step. See ADR-0008.
- `atomic_*_dump` helpers no longer ignore the `encoding` set via
  `safeatomic_config(...)`. Loader/dumper symmetry restored.
- `verify_checksum(path)` with absent sidecar now raises
  `FileNotFoundError` (previously surfaced inconsistently).
  `read_atomic(check_checksum=True)` with absent sidecar likewise raises
  `FileNotFoundError`. See ADR-0009.

### Guarantees

- `CrossDeviceSafety`: uniform across all `safety` levels — raises
  `CrossDeviceAtomicityError`, never silently copies (ADR-0008).
- `IntegrityDetection`: `FileNotFoundError` (absence) and
  `ChecksumMismatchError` (genuine mismatch) are disjoint and aligned
  across `verify_checksum` and `read_atomic` (ADR-0009).
- `CrashDurability`: parent-directory fsync failure dispatched via
  `safety`. No silent durability-loss under `safety="strict"`
  (ADR-0011).
- `SymlinkPolicy`: explicitly `Unspecified`. Callers MUST pre-resolve or
  reject (ADR-0010).
- `__all__` frozen at 43 names. No new exception classes were added in
  this release.

### Migration notes (vs. pre-release behaviour, not vs. v1.x)

- Callers under `safety="strict"` may now observe `OSError` propagating
  from parent-directory fsync that was previously silently swallowed.
- Callers catching `ChecksumMismatchError` for absent sidecars must add
  `FileNotFoundError` to their except clauses.
- Callers relying on raw `EXDEV` from `move_atomic` must catch
  `CrossDeviceAtomicityError` (or its parent `AtomicityError`).

---

## v1.x → v2.0 migration

v2.0 is a clean rewrite of v1 and is **not API-compatible**. v1.x consumers
should pin to the `v1.0.0` tag on the legacy repository until they choose
to migrate.

Highlights of the differences (full migration guide in `docs/migration-v1-to-v2.md`
when v2.0 ships):

- No more `safeatomic.atomic` module. Import everything from `safeatomic`.
- Public API restricted to 43 explicitly listed names. Internal symbols
  (constants, helpers) are no longer accessible.
- New `safety` keyword on every write/read/move operation, defaulting to
  `"strict"` (fail-closed on unsupported filesystems).
- New public exception hierarchy under `SafeAtomicError`.
- New inspection API: `inspect_guarantees(path) -> GuaranteeReport`.
- `read_atomic` parameter renamed from `verify_checksum` to
  `check_checksum`. No deprecation alias.
- `atomic_write` alias removed (use `write_atomic`).
- `move_atomic_force` removed (use `move_atomic(force=True)`).
- `lock_info_pretty` removed (use `str(LockInfo)`).
- `try_acquire_lock(force=...)` removed (use `force_release_lock` then
  `try_acquire_lock`).
- XML and Pickle helpers removed; deferred to v2.1 with security review.
- Minimum Python version raised to 3.12.

[Unreleased]: https://github.com/deepcausa/safeatomic/compare/v2.0.2...HEAD
[2.0.2]: https://github.com/deepcausa/safeatomic/releases/tag/v2.0.2
[2.0.1]: https://github.com/deepcausa/safeatomic/compare/v2.0.0...v2.0.2
[2.0.0]: https://github.com/deepcausa/safeatomic/releases/tag/v2.0.0
