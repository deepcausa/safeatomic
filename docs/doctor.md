# Doctor and environment inspection

safeatomic provides two complementary ways to understand what guarantees
are available for a given path.

---

## `inspect_guarantees` — normative view

```python
from safeatomic import inspect_guarantees

report = inspect_guarantees("/data/state.json")
print(report.environment.filesystem_class)    # "local_posix_persistent"
print(report.environment.platform)            # "linux"
print(report.guarantees["AtomicVisibility"])  # "Guaranteed"
print(report.guarantees["CrashDurability"])   # "Guaranteed"
print(report.guarantees["WriterExclusion"])   # "Guaranteed"
print(report.guarantees["IntegrityDetection"])# "Guaranteed"
```

`inspect_guarantees` detects the environment (by calling
`detect_environment` on the path's parent directory) and returns the
normative guarantee matrix for that filesystem class. It performs no
I/O on the target path itself and does not write any probe files.

The result is a `GuaranteeReport` with:

| Attribute | Type | Description |
|---|---|---|
| `environment` | `Environment` | Detected platform, filesystem class, device ID |
| `guarantees` | `dict[str, str]` | Guarantee name → level string |

`detect_environment` is cached by `st_dev`. Repeated calls for the same
device are cheap.

---

## `doctor` — empirical view

```python
from safeatomic import doctor

report = doctor(
    "/data/state.json",
    destructive=True,
    require={"AtomicVisibility", "CrashDurability"},
)
if not report.ok:
    raise RuntimeError(report.summary())
```

`doctor` probes the parent directory of the given path by actually
performing the relevant syscalls. It is the appropriate tool for
application startup checks and for diagnosing unexpected failures.

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `path` | — | Path to the file (or target location) to probe |
| `destructive` | `False` | If `True`, run the six write probes in addition to the two always-on checks |
| `require` | `None` | Set of guarantee names that must be present; raises `UnsupportedEnvironmentError` if any are missing |

### Always-on checks (run even without `destructive=True`)

| Check | What it verifies |
|---|---|
| `parent_exists` | The parent directory of `path` exists |
| `parent_writable` | The process can write to the parent directory |

### Probe checks (run only with `destructive=True`)

| Check | What it verifies |
|---|---|
| `create_excl_0600` | `O_CREAT\|O_EXCL` creates a file with mode `0o600`; confirms atomic exclusive create |
| `fsync_file` | `fsync(2)` succeeds on a newly created file |
| `fsync_dir` | `fsync(2)` succeeds on the parent directory |
| `atomic_replace` | `os.replace` atomically replaces a file |
| `lock_sidecar` | A `.lock` JSON sidecar can be written and removed |
| `checksum_sidecar` | A `.sha256` sidecar can be written, verified, and removed |

All probe files use the prefix `.safeatomic-doctor-` and are removed in
a `finally` block. If a probe check fails due to a cleanup error, the
original probe result is still reported correctly.

### Return value: `DoctorReport`

| Attribute | Type | Description |
|---|---|---|
| `ok` | `bool` | `True` if all checks passed (or were `unknown`) and all `require` guarantees are present |
| `checks` | `dict[str, DoctorCheck]` | Check name → result |
| `environment` | `Environment` | Detected environment |
| `guarantees` | `dict[str, str]` | Same normative guarantee matrix as `inspect_guarantees` |

Each `DoctorCheck` has a `status` (`passed`, `failed`, `unknown`) and an
optional `detail` string.

`unknown` is the status for probe checks when `destructive=False`. It is
not a failure; it means "not tested".

`report.summary()` returns a human-readable string listing all checks
and their status.

---

## Usage patterns

### Application startup check

```python
from safeatomic import doctor, UnsupportedEnvironmentError

def startup_check(path: str) -> None:
    report = doctor(
        path,
        destructive=True,
        require={"AtomicVisibility", "CrashDurability", "WriterExclusion"},
    )
    if not report.ok:
        raise RuntimeError(
            f"safeatomic: environment check failed for {path!r}\n"
            + report.summary()
        )
```

Call this once during application initialisation, before any writes.

### Non-destructive environment query

```python
from safeatomic import doctor

report = doctor("/data/state.json")   # destructive=False
# Always-on checks run; probe checks are "unknown"
print(report.environment.filesystem_class)
print(report.guarantees)
```

Useful when you only need the normative guarantee matrix and cannot
afford any I/O on the target path.

### Diagnosing a suspected filesystem issue

```python
from safeatomic import doctor

report = doctor("/mnt/shared/state.json", destructive=True)
for name, check in report.checks.items():
    if check.status != "passed":
        print(f"  {name}: {check.status} — {check.detail}")
```

---

## `inspect_guarantees` vs `doctor` — when to use which

| | `inspect_guarantees` | `doctor` |
|---|---|---|
| Performs I/O? | No (reads `st_dev` only) | Yes (with `destructive=True`) |
| Source of truth | Normative matrix | Actual syscall behaviour |
| Appropriate for | Per-operation environment lookup | Startup check, diagnostics |
| Cost | Cheap (cached) | Higher (syscalls + file I/O) |

If you need a fast check before every write, use `inspect_guarantees`.
If you need confidence that the specific mount point actually behaves as
expected, use `doctor` with `destructive=True`.
