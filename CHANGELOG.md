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

- *(nothing yet — v2.0 development in progress)*

---

## v1.x → v2.0 migration

v2.0 is a clean rewrite of v1 and is **not API-compatible**. v1.x consumers
should pin to the `v1.0.0` tag on the legacy repository until they choose
to migrate.

Highlights of the differences (full migration guide in `docs/migration-v1-to-v2.md`
when v2.0 ships):

- No more `safeatomic.atomic` module. Import everything from `safeatomic`.
- Public API restricted to 36 explicitly listed names. Internal symbols
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

[Unreleased]: https://example.invalid/safeatomic/compare/v2.0.0...HEAD
