# safeatomic — examples

Runnable, self-contained examples of the public API. Each script writes
to a fresh `tempfile.TemporaryDirectory`, prints what it does, and
exits. Nothing leaks outside the temp directory.

## Index

| #   | Script                          | Concept                                                  |
| --- | ------------------------------- | -------------------------------------------------------- |
| 01  | `01_write_read_basic.py`        | `write_atomic` / `read_atomic` — the atomicity invariant |
| 02  | `02_json_toml_yaml.py`          | Structured-format helpers: JSON, TOML, YAML              |
| 03  | `03_checksum.py`                | Sidecar digests, `verify_checksum`, ad-hoc hashing       |
| 04  | `04_locks.py`                   | Cooperative writer locks and the `concurrency='none'` rule |
| 05  | `05_doctor_environment.py`      | `doctor()` and `inspect_guarantees()` for the runtime FS |
| 06  | `06_errors.py`                  | Exception hierarchy under `SafeAtomicError`              |
| 07  | `07_atomic_writer_reader.py`    | `AtomicWriter` / `AtomicReader` — streaming + snapshot    |
| 08  | `08_config_safety_policy.py`    | `safety` policy + `safeatomic_config` scoped defaults    |

## How to run

```bash
pip install safeatomic
python examples/01_write_read_basic.py
# ...
python examples/08_config_safety_policy.py
```

All scripts are independent. You can run them in any order.

## Honest surprises

A few API points are intentionally narrow. Documented here so they do
not bite you.

- **`AtomicWriter.write()` accepts bytes only.** Encode text explicitly.
  No `mode=` or `encoding=` kwargs on the writer. This is by design — it
  prevents accidental mixing of text and binary content.

- **Holding a lock changes the `write_atomic` call.** The default
  `concurrency='lock'` makes `write_atomic` try to acquire the same lock
  itself. If you already hold the lock via `try_acquire_lock`, you MUST
  pass `concurrency='none'` to `write_atomic`, or it raises `LockError`.
  No silent reentrancy.

- **`try_acquire_lock` returns `bool`.** Inspect lock state separately
  with `inspect_lock(path) -> LockInfo`.

- **Read-keyword is `check_checksum`, not `require_checksum`.** Both
  `read_atomic` and `AtomicReader` use `check_checksum=False` by default.

- **`safeatomic_config` is a context manager that scopes four keys
  only:** `encoding`, `checksum_algo`, `retries`, `delay`. It does NOT
  scope `safety`, `concurrency`, `preserve_metadata`, or
  `write_checksum` — those must be visible at the call site (principle
  14). Explicit keyword arguments at the call site always win over the
  scoped default.

- **`verify_checksum` reflects the state NOW.** A previous `True` does
  not imply a later `True` if an out-of-band mutation has occurred.

## See also

- `docs/formal-models.md` — overview of the TLA+ models in `formal/`
- `docs/fsync-policy-not-adopted.md` — why `fsync_policy` is not a
  safeatomic concern (see ADR-0012 in the companion design corpus)
- `CHANGELOG.md` — release history
