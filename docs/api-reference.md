# API reference

All 43 public names exported from `safeatomic`. Internal modules
(underscore-prefixed) are not part of the public contract.

See [`examples/`](../examples/README.md) for runnable scripts that
demonstrate every category.

---

## IO core (7)

### `write_atomic(path, data, *, encoding="utf-8", safety="strict", concurrency="lock", write_checksum=False, preserve_metadata=False, retries=0, delay=0.05)`

Write `data` (str) atomically to `path`: temp file, fsync, `os.replace`,
fsync parent directory. See example `01_write_read_basic.py`.

### `write_atomic_bytes(path, data, *, safety="strict", concurrency="lock", write_checksum=False, preserve_metadata=False, retries=0, delay=0.05)`

Byte-oriented variant of `write_atomic`; `data` must be `bytes`.

### `read_atomic(path, *, encoding="utf-8", check_checksum=False, safety="strict")`

Read `path` atomically and return a `str`; raises `ChecksumMismatchError`
if `check_checksum=True` and the sidecar digest does not match. See
example `01_write_read_basic.py`.

### `read_atomic_bytes(path, *, check_checksum=False, safety="strict")`

Byte-oriented variant of `read_atomic`; returns `bytes`.

### `move_atomic(src, dst, *, safety="strict", preserve_metadata=False)`

Atomically rename `src` to `dst` via `os.replace`; always raises
`CrossDeviceAtomicityError` on cross-device moves, regardless of
`safety`.

### `AtomicWriter(path, *, safety="strict", concurrency="lock", write_checksum=False, preserve_metadata=False)`

Context manager for streaming atomic writes; `write(data: bytes)` appends
bytes, commit happens on `__exit__`. See example `07_atomic_writer_reader.py`.

### `AtomicReader(path, *, check_checksum=False, safety="strict")`

Context manager that opens a snapshot of `path` at entry; the snapshot
is stable for the lifetime of the context. See example
`07_atomic_writer_reader.py`.

---

## Locks (9)

Cooperative whole-file locks implemented via `O_CREAT|O_EXCL` on a `.lock`
sidecar. Not kernel-level mandatory locks. Cross-host exclusion is not
provided. See example `04_locks.py`.

### `try_acquire_lock(path, *, safety="strict") -> bool`

Attempt to acquire the lock for `path`; returns `True` on success,
`False` if already locked.

### `release_lock(path)`

Release the lock held by the current process; raises `LockError` if this
process does not hold it.

### `force_release_lock(path)`

Remove the lock sidecar unconditionally; use only when normal release is
impossible (e.g. after a crash).

### `is_locked(path) -> bool`

Return `True` if a lock sidecar exists for `path`.

### `inspect_lock(path) -> LockInfo`

Return a `LockInfo` dataclass with the lock metadata: `pid`, `hostname`,
`session_hash`, `timestamp`, `version`.

### `get_lock_age(path) -> float`

Return the age of the current lock in seconds.

### `is_stale_lock(path) -> bool | None`

Return `True` if the locking PID is no longer alive (same host only);
return `None` if the lock was written by a different host.

### `release_stale_lock(path)`

Release the lock if it is stale; raises `LockError` if the lock is still
held by a live process, or if the host is different.

### `LockInfo`

Dataclass with fields `pid: int`, `hostname: str`, `session_hash: str`,
`timestamp: str`, `version: str`.

---

## Checksum (6)

Sidecar digests compatible with GNU coreutils (`sha256sum`). The sidecar
format is `<hex-digest>  <basename>\nalgo=<algo>\ntimestamp=<iso8601>`.
See example `03_checksum.py`.

### `write_checksum_file(path, *, algo="sha256")`

Write a checksum sidecar next to `path` based on its current contents.

### `verify_checksum(path) -> bool`

Return `True` if the digest matches, `False` on genuine mismatch; raises
`FileNotFoundError` if the sidecar is absent. Does **not** raise
`ChecksumMismatchError`.

### `compute_hash_file(path, *, algo="sha256") -> str`

Return the hex digest of `path`.

### `compute_hash_data(data: bytes, *, algo="sha256") -> str`

Return the hex digest of the given bytes.

### `get_checksum_info(path) -> ChecksumInfo`

Return the `ChecksumInfo` dataclass from the sidecar: `digest`, `algo`,
`timestamp`, `filename`.

### `ChecksumInfo`

Dataclass with fields `digest: str`, `algo: str`, `timestamp: str`,
`filename: str`.

---

## Formats (8)

Thin wrappers that serialise/deserialise and delegate to
`write_atomic` / `read_atomic`. All atomicity and durability guarantees
are inherited. See example `02_json_toml_yaml.py`.

### `atomic_json_dump(path, obj, *, indent=2, **kwargs)`

Serialise `obj` to JSON and write atomically to `path`.

### `atomic_json_load(path, **kwargs) -> object`

Read `path` and deserialise as JSON.

### `atomic_yaml_dump(path, obj, **kwargs)`

Serialise `obj` to YAML (PyYAML) and write atomically.

### `atomic_yaml_load(path, **kwargs) -> object`

Read `path` and deserialise as YAML (PyYAML).

### `atomic_yaml_dump_ruamel(path, obj, **kwargs)`

Serialise `obj` to YAML (ruamel.yaml) for comment and key-order
preservation. Requires `pip install safeatomic[ruamel]`.

### `atomic_yaml_load_ruamel(path, **kwargs) -> object`

Read `path` and deserialise as YAML (ruamel.yaml). Requires
`pip install safeatomic[ruamel]`.

### `atomic_toml_dump(path, obj, **kwargs)`

Serialise `obj` to TOML and write atomically.

### `atomic_toml_load(path, **kwargs) -> object`

Read `path` and deserialise as TOML.

---

## Guarantees (3)

See [Guarantees](guarantees.md) and [Doctor and environment inspection](doctor.md).
See example `05_doctor_environment.py`.

### `inspect_guarantees(path) -> GuaranteeReport`

Return the normative guarantee matrix for the filesystem class detected
at `path`'s parent directory. No I/O on `path` itself; cached by
`st_dev`.

### `GuaranteeReport`

NamedTuple with `environment: Environment` and
`guarantees: dict[str, str]`.

### `Environment`

NamedTuple with `filesystem_class: str`, `platform: str`,
`device: int`. Values for `filesystem_class`:
`local_posix_persistent`, `local_posix_memory`, `network`,
`windows`, `object_store`, `unknown`.

---

## Doctor (3)

See [Doctor and environment inspection](doctor.md). See example
`05_doctor_environment.py`.

### `doctor(path, *, destructive=False, require=None) -> DoctorReport`

Probe the parent directory of `path`; with `destructive=True`, run all
six write probes. With `require`, raise `UnsupportedEnvironmentError`
if any listed guarantee is not `Guaranteed`.

### `DoctorReport`

Dataclass with `ok: bool`, `checks: dict[str, DoctorCheck]`,
`environment: Environment`, `guarantees: dict[str, str]`, and a
`summary() -> str` method.

### `DoctorCheck`

Dataclass with `status: str` (`"passed"`, `"failed"`, `"unknown"`) and
`detail: str | None`.

---

## Config (1)

See example `08_config_safety_policy.py`.

### `safeatomic_config(**kwargs)`

Context manager that sets scoped defaults for the current
`contextvars.Context`. Allowed keys: `encoding`, `checksum_algo`,
`retries`, `delay`. Guarantee-affecting kwargs (`safety`,
`concurrency`, `preserve_metadata`, `write_checksum`) cannot be set
via config; they must be explicit at every call site. Explicit call-site
kwargs always override the scoped default.

```python
with safeatomic_config(encoding="utf-16", checksum_algo="sha512"):
    write_atomic(path, data)          # uses utf-16 + sha512 defaults
    write_atomic(path, data, encoding="ascii")   # ascii wins here
```

---

## Exceptions and warnings (6)

See example `06_errors.py`.

### `SafeAtomicError`

Base class for all safeatomic exceptions. Catch this to handle any
library error in one place.

### `UnsupportedEnvironmentError(SafeAtomicError)`

Raised (under `safety="strict"`) when the detected environment cannot
provide the requested guarantees.

### `ChecksumMismatchError(SafeAtomicError)`

Raised by `read_atomic(check_checksum=True)` when the digest of the
file does not match the sidecar. Attributes: `path`, `expected`,
`actual`.

### `CrossDeviceAtomicityError(SafeAtomicError)`

Always raised by `move_atomic` when `EXDEV` is returned by the kernel.
Attributes: `src`, `dst`. `__cause__` is set to the original `OSError`.

### `LockError(SafeAtomicError)`

Raised when a lock operation fails: acquiring an already-held lock,
releasing a lock not held by this process, or attempting to release a
still-live lock via `release_stale_lock`.

### `UnsupportedEnvironmentWarning(UserWarning)`

Emitted (under `safety="warn"`) when the environment cannot provide the
requested guarantees but execution is allowed to continue.
