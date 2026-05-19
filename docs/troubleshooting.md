# Troubleshooting

A short field guide to errors and surprises you may hit with
`safeatomic`. Most are intentional: `safeatomic` refuses ambiguity
loudly rather than corrupting your data silently.

If something here does not match what you see, please open an
[environment report](https://github.com/deepcausa/safeatomic/issues/new/choose)
with the output of `doctor()` and `inspect_guarantees()`.

## Quick diagnostic

If anything is unexpected, run this first:

```python
from safeatomic import doctor, inspect_guarantees

print(doctor("/path/to/your/file_or_directory", destructive=True))
print(inspect_guarantees("/path/to/your/file_or_directory"))
```

The output identifies the filesystem class, platform, and which of the
eight guarantees are `guaranteed`, `best_effort`, `nontarget`, or
`unsupported` for that exact location.

---

## `UnsupportedEnvironmentError`

```text
safeatomic.UnsupportedEnvironmentError: filesystem_class='network'
is a NonTarget environment under safety='strict'
```

**Cause.** The target path lives on a filesystem `safeatomic` does
not support with strict guarantees: NFS, SMB / CIFS, Windows
filesystems, object stores mounted as files, or an unknown class.

**Why it is raised.** Under `safety="strict"` (the default),
`safeatomic` refuses to run on filesystems where the documented
guarantees do not hold. Silently degrading would mean lying about
atomicity and durability.

**Fixes, in order of preference:**

1. **Move the file to a Tier 1 location** — Linux `ext4`/`xfs`/`btrfs`/`tmpfs`
   or macOS `APFS`. This is the only fix that gets you the full
   guarantee set.
2. **Lower the safety policy explicitly.** If you accept the loss of
   strict guarantees:
   ```python
   write_atomic(path, data, safety="warn")        # warn and continue
   write_atomic(path, data, safety="best_effort") # silent attempt
   ```
   `safety="warn"` emits `UnsupportedEnvironmentWarning` and proceeds.
   `safety="best_effort"` runs the same protocol without warnings, on
   the understanding that the environment may not honor it.
3. **Verify the classification** with `doctor(path).filesystem_class`.
   If it says `unknown` and you believe it should be `local_posix_persistent`,
   that is a bug — please report it with the `doctor()` output.

See [supported-environments.md](supported-environments.md) for the
full tier list.

---

## `LockError`: lock is held by another process

```text
safeatomic.LockError: lock at /path/file.json.lock is held by
pid=12345 on host='workhorse' (acquired 14s ago)
```

**Cause.** Another process holds the cooperative writer lock for this
file. `safeatomic` does not break locks held by live processes.

**Diagnose:**

```python
from safeatomic import inspect_lock, get_lock_age, is_stale_lock

print(inspect_lock(path))      # LockInfo(pid, hostname, session_hash, timestamp)
print(get_lock_age(path))      # timedelta
print(is_stale_lock(path))     # True if the holder is gone
```

**Fixes:**

- **Wait** if the holder is legitimate and active.
- **Same host, dead holder:** `release_stale_lock(path)` releases the
  lock only if `os.kill(pid, 0)` shows the holder is gone. Safe.
- **Cross-host:** `safeatomic` cannot verify liveness across hosts.
  `is_stale_lock` returns `unknown` semantics for non-local hostnames.
  Treat cross-host locks as live and do not force-release them. If you
  must, `force_release_lock(path)` exists but bypasses safety — only
  use it when you know the holder is gone.
- **Reentrant lock surprise:** if you call `write_atomic` from inside
  a context that already holds the lock, you will hit `LockError`.
  Either restructure the call or pass `concurrency="none"` on the
  inner call (you have already serialized at the outer level).

---

## `ChecksumMismatchError` vs `FileNotFoundError` (sidecar absent)

These two errors are distinct on purpose.

### `ChecksumMismatchError`

```text
safeatomic.ChecksumMismatchError: path=/data/state.json
expected=sha256:ab12... actual=sha256:cd34...
```

**Cause.** The file's bytes do not match the digest stored in the
sidecar `.checksum` file. Possible explanations: silent corruption,
a write that bypassed `safeatomic`, an interrupted external editor,
or a tool that rewrote the file without updating the sidecar.

**Fix:**

- The file is suspect. Do not blindly trust it.
- If you have a backup or a way to regenerate, prefer that.
- If you must accept the current bytes as truth, refresh the sidecar:
  `write_checksum_file(path)`.
- Investigate why the mismatch happened. A `ChecksumMismatchError` on
  a path written only by `safeatomic` indicates either a bug or
  storage-layer corruption — both worth reporting.

### `FileNotFoundError` on the sidecar

```text
FileNotFoundError: [Errno 2] /data/state.json.checksum
```

**Cause.** `read_atomic(..., check_checksum=True)` was called but no
sidecar exists. This is not a corruption; it just means the file was
not written with `write_checksum=True`.

**Fix:** either create the sidecar with `write_checksum_file(path)`,
or read without `check_checksum=True`.

The two errors are kept separate so callers can distinguish *"the
data is suspect"* from *"there is no integrity record to check against"*.

---

## `CrossDeviceAtomicityError`

```text
safeatomic.CrossDeviceAtomicityError: src=/tmp/x.json
dst=/var/lib/app/x.json (different devices)
```

**Cause.** `move_atomic` was called with `src` and `dst` on different
filesystems. `os.replace` is not atomic across devices (it returns
`EXDEV`), and a copy+delete fallback would silently break the atomic
visibility guarantee.

**Why it is always raised.** Unlike unsupported environments,
`EXDEV` is raised **regardless of `safety=`**. There is no
"best effort" version of cross-device atomic move that is honest;
either it is atomic or it is two operations.

**Fix:**

- Stage the file in the destination's filesystem first, then move
  within that filesystem. Common pattern:
  ```python
  staging = Path("/var/lib/app/.staging")
  staging.mkdir(exist_ok=True)
  tmp = staging / "x.json"
  write_atomic(tmp, data)
  move_atomic(tmp, "/var/lib/app/x.json")  # same device, atomic
  ```
- If atomicity is not required, do a plain `shutil.move` and accept
  that for a brief window the destination may not exist.

---

## Parent-directory fsync surprises

`safeatomic` always fsyncs the parent directory after `os.replace`,
because the directory entry change is itself a piece of state that
must be durable. This can produce surprises:

- **Permission errors on the parent.** If the parent directory is
  not writable (no `+w` for current uid/gid), `safeatomic` cannot
  fsync it. Under `safety="strict"` this is a hard failure. Run
  `doctor(parent_dir).checks` to see which always-on check failed.
- **Slow writes on spinning disks.** Each `write_atomic` is at least
  two fsyncs (file + parent dir). If a workload calls `write_atomic`
  thousands of times per second on a non-SSD, throughput will be
  dominated by `fsync` latency. That is a workload mismatch, not a
  bug — consider a database or an append log.
- **`fsync_dir` reported as `best_effort` or `unsupported`.** On
  some filesystems (older NFS, some FUSE), directory fsync is a
  no-op or silently ignored. `doctor()` detects this and reports it.
- **ADR-0011** describes the rationale and corner cases for parent
  fsync. The policy will not be relaxed; opting out would defeat
  `CrashDurability` for renames.

---

## `safety` policy summary

```text
safety="strict"      (default)
  Refuses NonTarget environments. Raises UnsupportedEnvironmentError.

safety="warn"
  Emits UnsupportedEnvironmentWarning, proceeds with best effort.
  Use when you know your environment is borderline and you accept the risk.

safety="best_effort"
  Silent. Same protocol, no warnings. Use only when you have already
  classified the environment and do not need to be told again.
```

`safety` does not change the **protocol** — only what happens when
the environment cannot honor it.

---

## Symlinks

**v2.0 policy: unspecified.** `safeatomic` does not document or
guarantee any particular behavior when the target path is a symlink
or contains symlinked path components.

In practice, `os.replace` follows the final symlink and replaces the
target, but this is implementation-dependent. If you need defined
behavior, resolve the path first:

```python
resolved = path.resolve(strict=False)
write_atomic(resolved, data)
```

A symlink-specific guarantee may be added in a future version. Until
then, treat symlinked targets as outside the supported contract.

---

## When `doctor(destructive=True)` is appropriate

The non-destructive `doctor(path)` checks two always-on properties:
`parent_exists` and `parent_writable`. That is fast and safe.

`doctor(path, destructive=True)` additionally writes and deletes
small probe files (prefix `.safeatomic-doctor-*`) to verify:

- `create_excl_0600` — `O_CREAT|O_EXCL` works and respects mode bits
- `fsync_file` — file fsync returns without error
- `fsync_dir` — directory fsync returns without error
- `atomic_replace` — `os.replace` succeeds
- `lock_sidecar` — `.lock` sidecar creation works
- `checksum_sidecar` — `.checksum` sidecar creation works

**When to run with `destructive=True`:**

- diagnosing a real bug, especially one you intend to report;
- onboarding a new deployment target;
- writing a test that asserts an environment is suitable.

**When not to:**

- on every call in production (it touches the filesystem);
- on a directory you do not own or do not want files briefly created in.

The probe files are cleaned up on success; if `doctor` is killed
mid-probe, a `.safeatomic-doctor-*` file may remain. It is safe to
delete manually.

---

## Reporting issues

If you hit something that is not in this guide, please open an issue.
Include:

- `safeatomic` version (`pip show safeatomic`);
- Python version, OS, filesystem (`df -T`, `mount`, `stat -f`);
- output of `doctor(path, destructive=True)`;
- output of `inspect_guarantees(path)`;
- the smallest possible reproducer;
- whether the path is on NFS / SMB / a container volume / external storage.

The bug-report and environment-report templates ask for exactly
these fields.
