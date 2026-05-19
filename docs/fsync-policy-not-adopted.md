# Why safeatomic has no `fsync_policy` kwarg

**Status:** Informative. Pointer to the binding ADR in the design corpus.
**Date:** 2026-07

## Short answer

safeatomic does **not** expose a Redis-style `fsync_policy`
parameter (`per_write` / `per_second` / `never`) on its write
entry points. This is deliberate and is recorded as **ADR-0012**
in the design corpus (`safeatomic-project/adr/0012-fsync-policy-deferred-to-wal.md`).

## What safeatomic does today

Every successful write performs both fsyncs unconditionally:

- **Step 7** of the v2 write protocol: `os.fsync(tmp_fd)` — the
  new file content reaches stable storage before the rename.
- **Step 12**: `os.fsync(parent_dir_fd)` — the directory entry
  change reaches stable storage after the rename.

There is no kwarg to skip either fsync.

## What the `safety` kwarg does (it is **not** `fsync_policy`)

`safety: Literal["strict", "warn", "best_effort"]` (default
`"strict"`) governs the library's **response to** failure of the
parent-directory fsync, and to unsupported environments. It does
**not** govern fsync frequency, which is fixed.

The two axes do not intersect in safeatomic v2 because frequency
is not a choice the library exposes. See ADR-0011 for the
parent-dir fsync failure dispatch.

## Why no `fsync_policy`

safeatomic operates one-shot. Redis's `appendfsync` policy
chooses between fsyncing every appended record, group-committing
every second, or letting the OS decide — all three semantics
presuppose a streaming append loop. A single `write_atomic(path,
payload)` call has nothing to amortise over: `per_write` and
`per_second` collapse to the same behaviour, and only `never`
has a distinct meaning. Shipping a three-value enum where two
values are aliases of each other on this library's call shape
would be misleading vocabulary.

The full rationale, including the rejected alternatives and the
forward-looking sketch for a sibling WAL primitive where
`fsync_policy` is the correct surface, is in:

- `safeatomic-project/adr/0012-fsync-policy-deferred-to-wal.md`
  (binding decision).
- `safeatomic-project/notes/fsync-policy-and-durability-window.md`
  (informative; sketches `DurabilityWindow(policy)` as a TLA+
  refinement for the future WAL primitive's model — **not** for
  safeatomic's).

## If you genuinely want "no fsync" for dev/test

Today's path is intentional friction:

1. Run against a tmpfs target (data is in RAM; fsync is cheap).
2. Use `safety="best_effort"` so unsupported-environment checks
   do not fire.

There is no `fsync=False` shortcut. Adding one would make it
trivial to copy-paste a dev configuration into production. If
this changes in a future minor release, it will be under a
deliberate ADR with a guarded surface, not as a casual kwarg.
