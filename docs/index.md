# safeatomic — documentation

## Start here

- [Getting started](getting-started.md) — install, first write/read, checksum, doctor check
- [Guarantees](guarantees.md) — what AtomicVisibility, CrashDurability, WriterExclusion, and IntegrityDetection mean and when they hold
- [Supported environments](supported-environments.md) — Tier 1 / Tier 2 / NonTarget, and the safety policy
- [Doctor and environment inspection](doctor.md) — `inspect_guarantees` vs `doctor`, destructive probes, startup checks
- [API reference](api-reference.md) — all 43 public names, by category

## Formal protocol models

- [Formal models](formal-models.md) — what the TLA+ models cover, what they don't, and how to reproduce the checks
- [Why fsync\_policy was not adopted](fsync-policy-not-adopted.md) — ADR-0012 rationale

## Other resources

- [`README.md`](../README.md) — project overview, four guarantees, API surface, safety policy, environments
- [`examples/`](../examples/README.md) — eight runnable, self-contained examples
- [`formal/README.md`](../formal/README.md) — per-model invariants, TLC pinning, design insights
- [`CHANGELOG.md`](../CHANGELOG.md) — release history
