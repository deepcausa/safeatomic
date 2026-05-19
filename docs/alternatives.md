# Alternatives

`safeatomic` is for plain-file persistence when you need explicit
guarantees. It sits between primitive file writes and full databases.

This page is a deliberately fair comparison. Most of the tools listed
here are excellent at what they do. The question is not "which is
best", it is "which one fits the artefact and the failure model".

## Summary

Use `safeatomic` when the data naturally lives in one plain file and
the main risk is corruption during write, crash, concurrent cooperative
writers, or silent byte drift.

Use a database when you need queries, transactions across multiple
records, indexes, multiple writers, or relational/analytical workloads.

Use a lock library when locking is the only problem.

Use a KV engine when keyed performance is the primary goal.

## Decision table

| Need | Use | Why |
|---|---|---|
| Disposable file write | `Path.write_text()` | Simplest, no guarantee work needed |
| One-off atomic replacement by hand | `tempfile` + `os.replace` | Fine if you know fsync / dir-fsync / EXDEV details |
| Plain-file config / state with explicit guarantees | `safeatomic` | Atomic visibility, crash durability, cooperative lock, checksum |
| Only cross-process lock | `filelock` / `portalocker` | Lock libraries solve locking, not full persistence |
| Multi-record transactions / SQL / queries | SQLite | Real database semantics |
| Analytical queries / dataframes / columnar | DuckDB / Polars / Parquet | Query and columnar workloads |
| High-performance embedded KV | LMDB / RocksDB / `rocksdict` | Mature KV engines |
| Append-only event log | JSONL with care, or a real WAL | safeatomic is whole-file persistence, not a log |

---

## `Path.write_text()` / `open(..., "w")`

The first line of every Python file-writing tutorial. Perfect when the
file is disposable or can be rebuilt from another source of truth.

It is not a persistence protocol. If a process dies after truncating
the file but before writing the full replacement, readers may see
missing, truncated, or partial contents. There is no lock, no
checksum, no runtime capability inspection. The bytes the kernel sees
in its page cache are not the bytes on stable storage until `fsync`.

**Choose `safeatomic` when:**

- the file is configuration, state, or a checkpoint;
- process or machine crash must not leave a corrupted file;
- concurrent readers exist;
- callers want old-or-new visibility, never partial.

---

## Hand-rolled `tempfile` + `os.replace`

The classic recipe is well-known:

```python
# Simplified — production code needs more.
tmp = path.with_suffix(".tmp")
tmp.write_bytes(payload)
os.fsync(open(tmp, "rb").fileno())
os.replace(tmp, path)
# ... and now also fsync the parent directory
```

This works. It is also easy to get *almost* right.

Common missing pieces:

- `fsync` on the parent directory after `os.replace` (otherwise the
  directory entry change may not be durable);
- correct handling of `EXDEV` when `src` and `dst` are on different
  filesystems (`os.replace` is not atomic across devices);
- no cleanup of the temp file if the process dies mid-write;
- no lock, so two writers can race;
- no checksum sidecar, so silent corruption is invisible;
- no environment check, so the same code "succeeds" on NFS where the
  guarantees do not hold.

`safeatomic` exists so application code does not need to re-implement
this protocol — and remember every edge case — at every call site. It
also normalizes EXDEV into `CrossDeviceAtomicityError` rather than
silently copying.

---

## `python-atomicwrites`

The closest historical comparison for atomic replacement in Python.
A small, focused library that does the replace-via-rename pattern well.

`safeatomic` has a broader contract:

- runtime guarantee inspection (`inspect_guarantees`, `doctor`);
- cooperative writer exclusion as part of the same call;
- checksum sidecars and verified reads;
- format helpers for JSON / YAML / TOML;
- explicit `safety` policy for unsupported environments;
- documented formal protocol models (TLA+).

These are different scopes, not a quality judgement. If your need is
*"replace this file atomically, that's it"*, `atomicwrites` is a fine
choice. If your need is *"give me a full persistence contract with a
visible failure model"*, `safeatomic` is broader.

---

## `filelock` / `portalocker`

Cross-process locking, and nothing else. Excellent at that single job.

Lock libraries do not write the file safely. They do not call `fsync`.
They do not do atomic replace. They do not detect integrity drift.

You can compose a lock library with a hand-rolled atomic write to get
roughly what `safeatomic` provides — that is, in fact, what
`safeatomic` does internally for the lock part — but you take on the
combination yourself.

`safeatomic` locks are **cooperative whole-file locks** on a single
host. They are not kernel-level mandatory locks, and they do not
provide cross-host exclusion. For distributed coordination, use a
database, etcd, consul, or redis.

**Choose `safeatomic` when:** the problem is the full persistence
path, not only mutual exclusion.

**Choose `filelock` / `portalocker` when:** you already have a write
strategy and just need cross-process locking.

---

## SQLite

The right choice when the data is relational or when you need real
transactions across multiple records. SQLite is one of the most
tested pieces of software ever written, and it is almost certainly the
correct answer for *"structured mutable state shared between
processes"*.

`safeatomic` deliberately does not provide:

- SQL or queries;
- schemas, indexes, joins, foreign keys;
- multi-record atomic transactions;
- prepared statements;
- multi-writer database concurrency;
- rollback.

`safeatomic` is for files that should remain files.

**Choose `safeatomic` when:**

- the file is a single YAML / TOML / JSON config or state document;
- the artefact must remain a plain file (human-editable, version-controllable, readable by other tools);
- there is no SQL or query layer in the design.

**Choose SQLite when:** anything in the previous list of negatives
matters to you.

---

## DuckDB / Parquet / Polars

The analytical / dataframe stack. Excellent for `group_by`, joins,
aggregations, large columnar data, and reading mixed-format datasets.

They are not a replacement for the small persistence protocol around a
single `config.toml` or `state.json`. If your next operation on the
data is `group_by`, use the data stack. If your next operation is
*"write this state file without corrupting it"*, use `safeatomic`.

Note also: Parquet files written by these tools are themselves files
that benefit from atomic replacement and integrity checks. `safeatomic`
can be the layer that writes Parquet artefacts produced by Polars or
DuckDB safely, while the analytical work happens in those engines.

---

## LMDB / RocksDB / `rocksdict`

Serious embedded key-value engines. Better fit when key-value
performance, large datasets, or memory-mapped access patterns are the
primary goal.

These store data in an opaque format. You do not get a human-readable
JSON / YAML / TOML file at the end. That is a feature for performance
and a non-feature for *"the artefact itself is meaningful, other tools
or humans may read it directly"*.

**Choose `safeatomic` when:** the artefact is itself meaningful — a
configuration file, an exported state document, a single checkpoint
that another tool consumes.

**Choose LMDB / RocksDB / `rocksdict` when:** keyed lookup performance
or large embedded storage matters more than the file format.

---

## JSONL append logs

`safeatomic` is not an append-only log. It replaces whole files
atomically. If your workload is *"append one record at a time forever"*,
do not model it by repeatedly rewriting a growing file with
`write_atomic` — that scales poorly and provides the wrong semantics.

Use one of:

- a careful JSONL writer with explicit recovery semantics;
- a real write-ahead log;
- an embedded log-structured store;
- a queue if records are events.

`safeatomic` can still be useful for **rotation**: when the log is
sealed and replaced by a new active file, the rotation step is a
whole-file replacement and is exactly what `write_atomic` is for.

---

## When `safeatomic` is the right tool

Choose `safeatomic` when:

- the durable artefact is a plain file;
- readers should see either the old or the new complete content, never a partial write;
- the write should survive process or machine crash under documented `fsync` assumptions;
- multiple cooperating writers may race;
- checksum sidecars are useful for drift detection;
- the environment should be inspected at runtime via `inspect_guarantees()` or `doctor()`;
- JSON / YAML / TOML helpers are convenient without adopting a database.

## When `safeatomic` is the wrong tool

Do not use `safeatomic` when:

- you need SQL, joins, indexes, or multi-record transactions — use SQLite;
- you need analytical queries or columnar workloads — use DuckDB / Polars / Parquet;
- you need keyed performance over many records — use LMDB / RocksDB / `rocksdict`;
- you need cross-host or distributed locking — use a database, etcd, consul, or redis;
- the filesystem is NFS, SMB, Windows, or an object store and strict guarantees matter — `safeatomic` will refuse under `safety="strict"`;
- the data is an append-only event stream rather than a whole-file state artefact;
- the file is disposable and `Path.write_text()` is enough.

---

## Final decision snippet

If you are unsure:

- Use `Path.write_text()` for disposable files.
- Use `safeatomic` for critical plain-file state.
- Use SQLite for transactional structured state.
- Use DuckDB / Parquet / Polars for analysis.
- Use LMDB / RocksDB / `rocksdict` for high-performance keyed storage.
- Use `filelock` / `portalocker` if locking is the entire problem.

---

## References

- [Python `pathlib`](https://docs.python.org/3/library/pathlib.html)
- [`python-atomicwrites`](https://github.com/untitaker/python-atomicwrites)
- [`filelock`](https://github.com/tox-dev/filelock)
- [`portalocker`](https://github.com/wolph/portalocker)
- [SQLite](https://www.sqlite.org/)
- [DuckDB](https://duckdb.org/)
- [LMDB](https://www.symas.com/lmdb)
- [`rocksdict`](https://github.com/Congyuwang/RocksDict)
- safeatomic docs:
  [Guarantees](guarantees.md) ·
  [Doctor](doctor.md) ·
  [Supported environments](supported-environments.md) ·
  [Formal models](formal-models.md)
