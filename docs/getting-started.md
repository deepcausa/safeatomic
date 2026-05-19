# Getting started

## Installation

```bash
pip install safeatomic
```

To enable the ruamel YAML helpers (`atomic_yaml_dump_ruamel`,
`atomic_yaml_load_ruamel`) for comment and key-order preservation:

```bash
pip install safeatomic[ruamel]
```

Requirements: Python ≥ 3.12, POSIX-like operating system (Linux, macOS,
BSD).

---

## First write and read

```python
from safeatomic import write_atomic, read_atomic

write_atomic("config.json", '{"key": "value"}')
data = read_atomic("config.json")
```

`write_atomic` writes to a temporary file beside the target, fsyncs it,
replaces the target atomically (`os.replace`), and fsyncs the parent
directory. A concurrent reader sees either the previous file or the new
one — never a partial write.

For bytes:

```python
from safeatomic import write_atomic_bytes, read_atomic_bytes

write_atomic_bytes("blob.bin", b"\x00\x01\x02")
payload = read_atomic_bytes("blob.bin")
```

---

## Structured formats

```python
from safeatomic import atomic_json_dump, atomic_json_load
from safeatomic import atomic_yaml_dump, atomic_yaml_load
from safeatomic import atomic_toml_dump, atomic_toml_load

atomic_json_dump("settings.json", {"theme": "dark"})
cfg = atomic_json_load("settings.json")

atomic_yaml_dump("settings.yaml", {"theme": "dark"})
cfg = atomic_yaml_load("settings.yaml")

atomic_toml_dump("settings.toml", {"theme": "dark"})
cfg = atomic_toml_load("settings.toml")
```

Each helper is a thin wrapper that serialises the object, then calls
`write_atomic` / `read_atomic`. All atomicity and durability guarantees
are inherited.

---

## Integrity detection with checksums

```python
from safeatomic import write_atomic, read_atomic

# Write a file and save its SHA-256 sidecar next to it.
write_atomic("state.json", '{"counter": 42}', write_checksum=True)
# Produces: state.json  +  state.json.sha256

# Read and verify in one call.
data = read_atomic("state.json", check_checksum=True)
# Raises ChecksumMismatchError if the file diverges from its sidecar.
# Raises FileNotFoundError if the sidecar is absent.
```

`verify_checksum` checks without reading the full payload:

```python
from safeatomic import verify_checksum

ok = verify_checksum("state.json")   # True / False / raises FileNotFoundError
```

---

## Checking the environment before you start

Call `doctor` once at application startup to confirm the target path
behaves as expected:

```python
from safeatomic import doctor

report = doctor(
    "/data/state.json",
    destructive=True,                                  # run write probes
    require={"AtomicVisibility", "CrashDurability"},   # raise if missing
)
if not report.ok:
    raise RuntimeError(report.summary())
```

Without `destructive=True`, only the two always-on checks (parent exists,
parent writable) are performed; probe checks are reported as `unknown`.

See [Doctor and environment inspection](doctor.md) for the full
description of every probe.

---

## When to use safeatomic

safeatomic is the right choice when you need to persist plain files with
atomicity or durability properties but do not need a database. Common
patterns:

- Configuration files written by a background process and read by
  application startup.
- State checkpoints that must survive an abrupt process exit.
- Sidecar data files written alongside a main artefact.
- Log files or audit records where sidecar integrity verification is
  needed.

safeatomic is **not** appropriate when you need multi-record
transactions, queries, or schemas. Use SQLite or a database instead.
It is also not a distributed coordination primitive; cooperative locks
apply only within a single host. See
[Supported environments](supported-environments.md) for environment
limits.

---

## Next steps

- [Guarantees](guarantees.md) — understand exactly what each guarantee
  promises and where it stops.
- [Doctor and environment inspection](doctor.md) — how to use
  `inspect_guarantees` and `doctor` in production.
- [API reference](api-reference.md) — all 43 public names.
- [`examples/`](../examples/README.md) — eight runnable scripts covering
  every part of the API.
