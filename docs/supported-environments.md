# Supported environments

safeatomic classifies filesystems into tiers. The tier determines which
guarantees the library can provide and whether `safety="strict"`
(the default) will allow the operation.

---

## Tier 1 — tested, full guarantees

| Platform | Filesystems |
|---|---|
| Linux | ext4, xfs, btrfs, tmpfs |
| macOS | APFS |

On these environments, `AtomicVisibility`, `CrashDurability`, and
`WriterExclusion` are `Guaranteed`. `IntegrityDetection` is `Guaranteed`
when `write_checksum=True`.

Tier 1 is the only environment where the full test suite is run as part
of CI.

---

## Tier 2 — expected to work, not CI-tested

| Platform | Notes |
|---|---|
| FreeBSD | Expected `Guaranteed` for all four guarantees |
| OpenBSD | Expected `Guaranteed` for all four guarantees |
| NetBSD | Expected `Guaranteed` for all four guarantees |

These platforms satisfy the POSIX contracts (`rename(2)`, `fsync(2)`,
`O_CREAT|O_EXCL`) that the library relies on. They are not covered by
the CI matrix. If you encounter a regression, please open an issue with
the output of `doctor(path, destructive=True)`.

---

## Tier 3 — NonTarget

| Environment | Reason |
|---|---|
| Windows / NTFS / ReFS | `rename` is not atomic under concurrent opens; locking semantics differ |
| NFS | `rename` atomicity, `fsync` durability, and `O_EXCL` creation are not guaranteed |
| SMB / CIFS | Same issues as NFS |
| Object stores (S3, GCS, Azure Blob) | No POSIX rename primitive |

`NonTarget` means the library explicitly does not target these
environments. Under `safety="strict"` (default), an
`UnsupportedEnvironmentError` is raised before any I/O happens when
`detect_environment` classifies the path as `network`, `windows`, or
`object_store`.

There is no plan to add Tier 1 support for these environments.

---

## The safety policy

Every operation accepts a `safety` keyword:

```python
write_atomic(path, data, safety="strict")       # default — raise on unsupported env
write_atomic(path, data, safety="warn")         # execute, emit UnsupportedEnvironmentWarning
write_atomic(path, data, safety="best_effort")  # execute silently (caller's responsibility)
```

`safety` is **not** configurable via `safeatomic_config`; it must be
explicit at every call site. This is intentional: guarantee-affecting
choices should be visible in the code, not hidden behind a thread-local.

`move_atomic` always refuses cross-device moves regardless of `safety`
— `CrossDeviceAtomicityError` is always raised when `EXDEV` is returned
by the kernel. Silent fallback to copy+delete would break the atomicity
promise.

---

## Detecting the environment

```python
from safeatomic import inspect_guarantees, doctor

# Normative view: what does the matrix promise for this filesystem class?
report = inspect_guarantees("/data/state.json")
print(report.environment.filesystem_class)   # e.g. "local_posix_persistent"
print(report.environment.platform)           # e.g. "linux"

# Empirical view: do the syscalls actually work?
report = doctor("/data/state.json", destructive=True)
print(report.ok)
print(report.summary())
```

`detect_environment` is cached by `st_dev` (the device number of the
path's parent directory). Repeated calls for the same device are cheap.

See [Doctor and environment inspection](doctor.md) for the full probe
list.

---

## Symlinks

As of v2.0, symlink behaviour is **unspecified**. The behaviour of
`write_atomic`, `move_atomic`, `read_atomic`, and the format helpers
when the target or any path component is a symlink is not part of the
public contract and may change.

Callers with symlink-sensitive workloads must resolve or reject symlinks
before calling into safeatomic:

```python
from pathlib import Path

p = Path(user_supplied_path)
resolved = p.resolve(strict=True)
if p.is_symlink():
    raise ValueError(f"symlinks not allowed: {p}")
write_atomic(resolved, data)
```
