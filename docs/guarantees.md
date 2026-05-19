# Guarantees

safeatomic provides four main guarantees and four supporting guarantees.
Each has a documented level per environment:

| Level | Meaning |
|---|---|
| `Guaranteed` | The library provides this on qualifying environments. |
| `BestEffort` | The library attempts it; the environment may silently not provide it. |
| `NonTarget` | Explicitly out of scope; the library will warn or raise. |
| `Unsupported` | The environment cannot provide this; the library refuses to operate by default. |

---

## The four main guarantees

### AtomicVisibility

A reader observing the target path sees either the previous complete
file or the new complete file — never a partial or interleaved write.

Achieved by writing to a temporary file beside the target (same
filesystem), then using `os.replace` for the final rename. `os.replace`
maps to `rename(2)` on POSIX, which is atomic with respect to readers on
local POSIX filesystems.

**Level on local POSIX persistent (ext4, xfs, btrfs, apfs):** `Guaranteed`  
**Level on tmpfs / ramfs:** `Guaranteed`  
**Level on network filesystems (NFS, SMB):** `NonTarget`  
**Level on object stores:** `NonTarget`

### CrashDurability

After `write_atomic` returns, the new content survives an abrupt process
exit or machine restart. The file and the parent directory are both
fsynced before the function returns.

Two-phase fsync: (1) file is fsynced before `os.replace`; (2) the parent
directory is fsynced after `os.replace` to durably record the new
directory entry.

If the final directory fsync fails, the behaviour depends on `safety`:
- `strict` (default): re-raises the `OSError`. Content is already
  visible; CrashDurability is **not** confirmed.
- `warn`: emits `UnsupportedEnvironmentWarning` and returns normally.
- `best_effort`: silent.

**Level on local POSIX persistent:** `Guaranteed`  
**Level on tmpfs / ramfs:** `BestEffort` (kernel may not persist across
reboot by design)  
**Level on network filesystems:** `NonTarget`

### WriterExclusion

No two `write_atomic` callers with `concurrency='lock'` (the default)
can produce interleaved results. The lock is acquired before the
temporary file is opened and released after the parent-directory fsync.

Locks are **cooperative**: processes that do not use safeatomic, or that
call with `concurrency='none'`, are not prevented from writing.  
Locks are whole-file: they apply to the target path as a unit. No
byte-range or record locking.  
Locks are local to one host: cross-host exclusion is not provided.

The lock mechanism uses `O_CREAT|O_EXCL` on a `.lock` sidecar, which is
atomic on local POSIX filesystems.

**Level on local POSIX persistent:** `Guaranteed` (among cooperative callers)  
**Level on network filesystems:** `NonTarget`

### IntegrityDetection

If the on-disk bytes differ from what was written, the discrepancy can
be detected at read time via the sidecar checksum.

Opt-in at write time (`write_checksum=True`) and at read time
(`check_checksum=True` on `read_atomic`, or `verify_checksum`). A
sidecar file (`<name>.sha256` by default) is written atomically beside
the target and holds the digest, algorithm, and timestamp.

`verify_checksum` returns `bool`. `read_atomic(check_checksum=True)`
raises `ChecksumMismatchError` on a genuine mismatch and
`FileNotFoundError` if the sidecar is absent. Absence and mismatch are
distinct failure modes.

**Level when `write_checksum=True`:** `Guaranteed` (integrity detection
is available)  
**Level otherwise:** `NonTarget` (no sidecar written, no detection
possible)

---

## The four supporting guarantees

### ReaderConsistency

`AtomicReader` gives a snapshot: the bytes it opened remain consistent
for the lifetime of the context manager, regardless of concurrent
writers.

### StaleRecovery

A lock sidecar whose PID is no longer alive (checked via
`os.kill(pid, 0)`) can be identified as stale via `is_stale_lock` and
released via `release_stale_lock`. Cross-host staleness (different
hostname in the sidecar) is reported as `unknown`, not as stale or live.

### MetadataPreservation

`write_atomic(..., preserve_metadata=True)` copies the original file's
mode bits and ownership to the replacement before the rename, so the
target's metadata does not change.

### CrossDeviceSafety

`move_atomic` always refuses a cross-device move
(`CrossDeviceAtomicityError`). The function name promises atomicity;
silent fallback to a copy+delete would break that. `os.replace` is not
atomic across devices.

---

## Composability

Guarantees are opt-in per call. Any combination is valid:

```python
# AtomicVisibility + CrashDurability only
write_atomic(path, data, concurrency="none")

# Add WriterExclusion (default)
write_atomic(path, data)

# Add IntegrityDetection
write_atomic(path, data, write_checksum=True)

# All four
write_atomic(path, data, concurrency="lock", write_checksum=True)
```

`CrashDurability` is always on for `write_atomic`. Opting out would
defeat the core promise.

---

## Limits

- Guarantees apply only to **cooperative callers** using safeatomic.
  External processes that write to the same path without the library are
  not covered.
- Guarantees hold on the supported environments listed in
  [Supported environments](supported-environments.md). Network
  filesystems, object stores, and Windows are explicitly `NonTarget`.
- Symlink behaviour is unspecified as of v2.0. Resolve or reject
  symlinks before calling into safeatomic.
- These guarantees describe the **abstract protocol**. The protocol was
  model-checked under documented assumptions (see
  [Formal models](formal-models.md)). The assumptions include
  `os.replace` atomicity, `fsync` durability, and PID-namespace
  semantics — properties of the host OS and filesystem, not of the
  library.
