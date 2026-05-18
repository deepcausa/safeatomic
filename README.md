# safeatomic

> An atomic file persistence library for Python.
> **Choose your guarantees. Compose them. Inspect them at runtime.**

`safeatomic` provides four orthogonal, opt-in guarantees for plain-file
persistence — atomic visibility, crash durability, writer exclusion, and
integrity detection — with each guarantee formally documented, composable
per call, and inspectable at runtime against your actual filesystem.

It sits between primitive `Path.write_text()` and full-fledged databases.

```python
from safeatomic import write_atomic, read_atomic, atomic_yaml_dump

# Atomic write: a reader observes either the old or new content, never partial.
# Cooperative writer lock and parent-directory fsync are on by default.
write_atomic("config.json", '{"key": "value"}')

# Atomic read with integrity check: raises ChecksumMismatchError if the
# on-disk file diverges from its sidecar.
data = read_atomic("config.json", check_checksum=True)

# Format helpers compose atomic write with JSON / YAML / TOML.
atomic_yaml_dump("settings.yaml", {"theme": "dark"})
```

## The four guarantees

| # | Guarantee | What it answers |
|---|---|---|
| 1 | **AtomicVisibility** | "Will a concurrent reader ever see a half-written file?" |
| 2 | **CrashDurability** | "If the process or machine dies after my write returned, will the data survive?" |
| 3 | **WriterExclusion** | "Can two writers race and produce a logically interleaved result?" |
| 4 | **IntegrityDetection** | "Will I notice if the bytes on disk silently differ from what I wrote?" |

Each guarantee is opt-in, with safe defaults. `safeatomic` is not a lock
library, not a fsync wrapper, not a checksum tool — it is **one library
where these four concerns are composable with a single API**.

```python
# Atomic visibility only (no lock, no checksum)
write_atomic("cache.json", data, concurrency="none")

# Add cooperative writer exclusion (default)
write_atomic("config.json", data)

# Add integrity detection via sidecar checksum
write_atomic("state.json", data, write_checksum=True)

# Combine all four
write_atomic("critical.json", data, concurrency="lock", write_checksum=True)
```

`CrashDurability` is always on for `write_atomic` (file + parent-directory
fsync); opting out would defeat the library's core promise.

## Inspect guarantees at runtime

Every guarantee has a documented level per environment
(`Guaranteed | BestEffort | NonTarget | Unsupported`) and is queryable:

```python
from safeatomic import inspect_guarantees

report = inspect_guarantees("/data/state.json")
print(report.environment.filesystem_class)        # "local_posix_persistent"
print(report.guarantees["AtomicVisibility"])      # "Guaranteed"
print(report.guarantees["CrashDurability"])       # "Guaranteed"
print(report.guarantees["WriterExclusion"])       # "Guaranteed"
print(report.guarantees["IntegrityDetection"])    # "Guaranteed"
```

`inspect_guarantees` returns the **normative** view: given the detected
filesystem class, which guarantees does the matrix promise? It is cheap
enough to call before every operation.

For an **empirical** view that actually exercises the syscalls — useful
at application startup or for diagnostics — use `doctor`:

```python
from safeatomic import doctor

report = doctor(
    "/data/state.json",
    destructive=True,                                  # run write probes
    require={"AtomicVisibility", "CrashDurability"},   # required guarantees
)
if not report.ok:
    raise RuntimeError(report.summary())
```

`doctor` probes the parent directory (existence, writability, exclusive
create with `0o600`, `fsync` on file and directory, `os.replace`, JSON
sidecar round-trip, checksum sidecar round-trip). Probe files use the
`.safeatomic-doctor-` prefix and are cleaned up in `finally`. Without
`destructive=True`, probe-only checks are skipped and reported as
`unknown` — the report still gives you the matrix view.

## Safety policy

Every operation accepts a `safety` keyword to control how the library
reacts when the environment cannot provide the requested guarantees:

```python
write_atomic(path, data, safety="strict")       # default: raise UnsupportedEnvironmentError
write_atomic(path, data, safety="warn")         # execute, emit UnsupportedEnvironmentWarning
write_atomic(path, data, safety="best_effort")  # execute silently (caller takes responsibility)
```

`move_atomic` always refuses cross-device moves
(`CrossDeviceAtomicityError`), regardless of `safety` — the function name
promises atomicity, and silent fallback would break that.

## Supported environments

- **Tier 1** (tested, full guarantees): Linux + ext4/xfs/btrfs/tmpfs;
  macOS + apfs
- **Tier 2** (expected to work, untested): FreeBSD, OpenBSD, NetBSD
- **Tier 3** (`NonTarget`): Windows / NTFS / ReFS, NFS, SMB

Under `safety="strict"` (default), unrecognised or `NonTarget`
filesystems raise `UnsupportedEnvironmentError` before any I/O happens.

## Requirements

- Python ≥ 3.12
- POSIX-like operating system

## Installation

```bash
pip install safeatomic
```

To enable the ruamel YAML helpers (`atomic_yaml_dump_ruamel`,
`atomic_yaml_load_ruamel`) for comment-and-order preservation:

```bash
pip install safeatomic[ruamel]
```

## API surface

The full public API is 43 names exported from `safeatomic`. Internal
modules are underscore-prefixed and are **not** part of the public
contract.

- **IO core (7):** `AtomicWriter`, `AtomicReader`, `write_atomic`, `write_atomic_bytes`, `read_atomic`, `read_atomic_bytes`, `move_atomic`
- **Locks (9):** `try_acquire_lock`, `release_lock`, `force_release_lock`, `is_locked`, `inspect_lock`, `get_lock_age`, `is_stale_lock`, `release_stale_lock`, `LockInfo`
- **Checksum (6):** `compute_hash_file`, `compute_hash_data`, `verify_checksum`, `write_checksum_file`, `get_checksum_info`, `ChecksumInfo`
- **Formats (8):** `atomic_json_dump`/`atomic_json_load`, `atomic_yaml_dump`/`atomic_yaml_load`, `atomic_yaml_dump_ruamel`/`atomic_yaml_load_ruamel` (require `[ruamel]` extra), `atomic_toml_dump`/`atomic_toml_load`
- **Guarantees (3):** `inspect_guarantees`, `GuaranteeReport`, `Environment`
- **Doctor (3):** `doctor`, `DoctorReport`, `DoctorCheck`
- **Config (1):** `safeatomic_config` — `ContextVar`-backed defaults for `encoding`, `checksum_algo`, `retries`, `delay`. Guarantee-affecting kwargs (`safety`, `concurrency`, `preserve_metadata`, `write_checksum`) cannot be set via config and must remain explicit at call sites.
- **Exceptions + warnings (6):** `SafeAtomicError`, `UnsupportedEnvironmentError`, `UnsupportedEnvironmentWarning`, `ChecksumMismatchError`, `CrossDeviceAtomicityError`, `LockError`

See [`docs/`](docs/) for the full reference.

## Formal model

The core protocol — atomic replacement, cooperative lock lifecycle,
and checksum-sidecar verification — is model-checked with TLA+ under
documented filesystem and runtime assumptions. The models live in the
companion `safeatomic-project/formal/` directory and verify the
abstract protocol only. They do **not** verify the Python
implementation, the operating system, the filesystem, hardware, the
serializers, or any deployment environment. `os.replace` atomicity,
`fsync` durability, and PID semantics are assumptions of the model,
not theorems about your machine.

In practice this means: TLA+ fixes the contract; the test suite
exercises the implementation against that contract; `doctor()` and
`inspect_guarantees()` report the actual capabilities of the specific
path you are using.

## What it is not

- **Not a database.** No queries, no schema, no multi-record transactions.
- **Not a drop-in replacement for `python-atomicwrites`.** Different API
  surface, different scope, different guarantees.
- **Not a distributed coordination primitive.** Locks are cooperative
  whole-file locks on a single host.

The scope of the lock model is summarised as:

> safeatomic provides cooperative whole-file coordination,
> not database concurrency control.

For cross-host coordination, use a database or a distributed lock manager
(etcd, consul, redis). For multi-record transactions, use sqlite.

## Versioning

[Semantic Versioning](https://semver.org/) with one extension: **weakening
any documented guarantee is a major version bump**, even when no
signatures change. See [`CHANGELOG.md`](CHANGELOG.md).

Deprecated symbols live for at least one major version cycle before
removal.

## Logging

The library uses `logging.getLogger("safeatomic")` for diagnostics. It
does not configure a handler; consumers configure logging as they wish.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Security

See [`SECURITY.md`](SECURITY.md) for the vulnerability reporting policy.

## License

[MIT](LICENSE)
