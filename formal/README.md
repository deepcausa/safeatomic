# safeatomic formal models

This directory contains TLA+ models for the abstract safeatomic protocol
and the results of running the TLC model checker against them.

These models verify the **abstract protocol**, not the Python implementation.
They are useful evidence that the protocol design is internally consistent
with respect to the listed invariants — they are **not** a proof that any
specific build of safeatomic on any specific operating system or filesystem
is correct. See "What these models do not prove" at the bottom of this
file, and `reports/MANIFEST.json` for the full disclaimer.

## Layout

```
formal/
├── README.md                       (this file)
├── SafeAtomicSmoke.tla / .cfg      (atomic replacement model)
├── SafeAtomicLock.tla / .cfg       (cooperative lock model)
├── SafeAtomicChecksum.tla / .cfg   (checksum sidecar model)
└── reports/
    ├── MANIFEST.json               (machine-readable summary of the last run)
    ├── 2026-05-19-safeatomic-smoke.txt
    ├── 2026-05-19-safeatomic-lock.txt
    └── 2026-05-19-safeatomic-checksum.txt
```

The `reports/` directory contains the **raw, unedited TLC stdout** from the
most recent committed run. Anyone with TLC installed can re-run the models
and compare their output to these reports. The numbers in
`reports/MANIFEST.json` are extracted from those raw outputs, not invented.

## Models

| Model                | LOC | States generated | Distinct states | Depth | Focus                                                     |
| -------------------- | --- | ---------------- | --------------- | ----- | --------------------------------------------------------- |
| `SafeAtomicSmoke`    |  64 |               51 |              15 |     5 | Atomic rename: no partial target is visible to a reader   |
| `SafeAtomicLock`     | 188 |               28 |               8 |     4 | Cooperative lock: exclusion, stale recovery, force        |
| `SafeAtomicChecksum` | 188 |             1548 |             259 |     9 | Sidecar integrity: no false `Match` under corruption      |

All three models pass under TLC 2.19 (tla2tools.jar v1.7.4). The exact
numbers above are also recorded in `reports/MANIFEST.json` and can be
verified against the raw report files.

## Reproducing the runs

The repeatable runner lives at `scripts/check-formal.sh` in the repository
root. From the repo root:

```sh
scripts/check-formal.sh
```

It will:

1. Locate the TLC wrapper at `~/.local/bin/tlc` (or use `TLC_JAR` env var
   pointing at a `tla2tools.jar`).
2. Run TLC against each `.tla` model in this directory.
3. Print a summary of the results.
4. **Not** overwrite the committed reports in `reports/` — those represent
   a single canonical run. To refresh them, run the script with the
   `--update-reports` flag.

The script exits non-zero if any model fails (TLC stdout does not contain
"No error has been found").

## Toolchain pinning

The committed reports were produced with:

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| TLC version   | 2.19 of 08 August 2024 (rev: 5a47802)                              |
| tla2tools tag | `v1.7.4` ("The Xenophanes release", 2024-08-05)                    |
| Size          | 2 274 532 bytes                                                    |
| SHA-256       | `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88` |
| Source        | https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4              |
| Java          | OpenJDK 21.0.11                                                    |

To verify a local copy:

```sh
sha256sum ~/.local/opt/tla+/tla2tools.jar
# expected: 936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88
```

Bumping the version requires editing this README, `reports/MANIFEST.json`,
and `scripts/check-formal.sh` together, plus regenerating the reports.

## Installing TLC locally

You need Java 11+ and `tla2tools.jar`. A minimal install at
`~/.local/opt/tla+/tla2tools.jar`, with a shell wrapper at
`~/.local/bin/tlc`, is sufficient:

```sh
mkdir -p ~/.local/opt/tla+ ~/.local/bin
curl -L \
  https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar \
  -o ~/.local/opt/tla+/tla2tools.jar
echo "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88  $HOME/.local/opt/tla+/tla2tools.jar" \
  | sha256sum -c -
cat > ~/.local/bin/tlc <<'EOF'
#!/usr/bin/env sh
exec java -cp "$HOME/.local/opt/tla+/tla2tools.jar" tlc2.TLC "$@"
EOF
chmod +x ~/.local/bin/tlc
```

Alternatively, set `TLC_JAR=/path/to/tla2tools.jar` and
`scripts/check-formal.sh` will invoke `java -cp "$TLC_JAR" tlc2.TLC`
directly.

## What `SafeAtomicSmoke` checks

The smallest model. It captures the **AtomicVisibility** guarantee for the
core write protocol: a reader observes either the old target or the new
target, never a partial write.

| Invariant                       | Meaning                                                            |
| ------------------------------- | ------------------------------------------------------------------ |
| `TypeInvariant`                 | All variables stay in their declared domains                       |
| `NoPartialTarget`               | The target file is never observed as `Partial` (only `Old`/`New`)  |
| `ReadReturnsCommittedVersion`   | A read returns one of `{NoneVal, Old, New}`, never `Partial`       |

Five actions: write a partial tmp, write a complete tmp, replace target,
read, crash. The crash action discards the in-flight tmp and leaves the
target untouched.

## What `SafeAtomicLock` checks

Models the cooperative file-lock lifecycle with abstract process
identifiers (no numeric PIDs, no `os.kill`).

| Invariant                              | Meaning                                                                |
| -------------------------------------- | ---------------------------------------------------------------------- |
| `TypeInvariant`                        | All variables remain in their declared domains                         |
| `AtMostOneLiveLocalOwner`              | A live local lock has not been stale- or force-released in this cycle  |
| `StaleReleaseOnlyWhenStale`            | Stale-recovery always leaves the lock `Free`                           |
| `ForceReleaseIsAdministrativeOverride` | Administrative override always leaves the lock `Free`                  |
| `CorruptLockIsNotStaleRecovery`        | Corrupt locks cannot be reclaimed via stale-recovery (need ForceRelease) |
| `RemoteLockNotDeclaredPidStale`        | Remote locks are never declared stale by local PID-liveness logic      |

**Design insight surfaced during model checking.** An earlier draft used
three boolean flags (`released`, `staleReleased`, `forceReleased`) that
accumulated across lock cycles. TLC found that `ForceRelease` followed by
`AcquireLocalLock` left a live lock with `forceReleased = TRUE`, violating
the invariant. The fix was a single `lastRelease` enum that records how
the *current* acquisition cycle ended and resets at the start of every
new acquisition. This is a real constraint on the implementation:
release-mode metadata must be scoped to one lock-file epoch, not persisted
across re-acquisitions.

## What `SafeAtomicChecksum` checks

Models the two-phase write (target then sidecar) and read-verify against
the sidecar.

| Invariant                            | Meaning                                                              |
| ------------------------------------ | -------------------------------------------------------------------- |
| `TypeInvariant`                      | All variables remain in their declared domains                       |
| `ChecksumMatchImpliesHashConsistent` | A `Match` result implies the verified (read, sidecar) pair matched   |
| `CorruptionDetectedAsMismatch`       | A corrupt target read always produces `Mismatch`, never `Match`      |
| `NoFalseMatchForWrongHash`           | A wrong sidecar hash never produces `Match`                          |

**Design insight surfaced during model checking.** The initial invariant
compared the live `checksum` variable against `verifyResult`. TLC found a
trace where `ReadTarget` → `WriteTargetNew` → `VerifyChecksum` →
`WriteChecksumNew` was a valid interleaving: verify ran against
`(Old, OldHash)` (a true match), but by the time the invariant was
checked the sidecar had advanced to `NewHash`. The fix is to snapshot
the `(lastRead, checksum)` pair at `VerifyChecksum` time into
`verifiedTarget` / `verifiedChecksum` and write invariants against those
snapshots. This reflects a real implementation constraint: the sidecar
value must be captured atomically with the read, not re-read after
verification.

**Semantics of `verify_checksum()`.** `verify_checksum()` is an assertion
about the **pair observed at the moment of verification** — the bytes
that were read and the sidecar value that was read together with them.
It is **not** an assertion about future states of the file or sidecar.
After verification returns `Match`, the underlying target or sidecar may
change (concurrent writer, corruption, force-release); the result still
correctly describes what was observed at that point in time.
Implementations must therefore treat `(data_bytes, sidecar_value)` as a
captured pair from a single observation, never as two independent
re-reads.

**Concurrency scope.** `SafeAtomicChecksum` does **not** model real
concurrency between multiple writers. It models **abstract interleavings**
of target writes, sidecar writes, corruption events, reads, and
verifications as a single non-deterministic sequence. This is sufficient
to surface dangerous orderings between target and sidecar updates, but it
does not exercise multi-process race conditions, shared-memory
contention, or the lock protocol from `SafeAtomicLock`. Concurrent-writer
guarantees live in the lock model, not here.

## What these models do not prove

- **Filesystem atomicity.** `os.rename()` is assumed to be atomic; the
  model does not verify this against any specific kernel or filesystem.
- **Hash collision resistance.** `WrongHash`, `OldHash`, and `NewHash`
  are opaque model values. Cryptographic properties of SHA-256 are not
  modelled.
- **Numeric PID liveness.** `LocalPid` and `RemotePid` are symbolic
  tokens. `os.getpid()`, `os.kill(pid, 0)`, and PID reuse are not
  modelled.
- **Network partition or clock skew.** Remote vs. local is a binary
  distinction; split-brain, NTP drift, and partial connectivity are
  absent.
- **Python implementation correctness.** These models describe an
  abstract protocol. No Python code is verified.
- **POSIX fsync ordering.** Write ordering between target and sidecar is
  modelled as sequential steps, not as barriers or journal commits.
- **Concurrent writers.** A single writer per epoch is assumed; the
  models do not explore interleaved concurrent writes to the same
  target.
- **Storage lies.** Hardware that silently drops writes (per the
  `storage_lie` element of the crash model) is out of scope by
  assumption.

For the binding governance record of what safeatomic does and does not
promise, see the project's design corpus (ADR-0007: TLA+ as a source of
discipline, not decoration).
