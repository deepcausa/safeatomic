# Formal protocol models

safeatomic ships small TLA+ models that describe the **abstract core
protocol** the library implements. They are checked with the TLC model
checker and the raw checker output is committed under
[`formal/reports/`](../formal/reports/).

## What the models cover

| Model                | Concern                                                        |
| -------------------- | -------------------------------------------------------------- |
| `SafeAtomicSmoke`    | Atomic replacement: no partial target visible to a reader      |
| `SafeAtomicLock`     | Cooperative lock lifecycle: exclusion, stale recovery, force   |
| `SafeAtomicChecksum` | Two-phase target + sidecar write and read-verify integrity     |

Full per-model details, invariants, and the design insights TLC surfaced
during development are documented in [`formal/README.md`](../formal/README.md).

## What the models do **not** cover

This is the part that matters most for honest claims. The models do
**not** verify:

- the Python implementation under `src/safeatomic/`;
- operating systems (Linux, macOS, BSD, Windows);
- filesystems (ext4, xfs, btrfs, tmpfs, apfs, ntfs, nfs, smb);
- third-party serializers (PyYAML, ruamel.yaml, tomli_w, json);
- hardware durability (fsync honesty, write barriers, drive cache);
- deployment environments (containers, VMs, network mounts);
- absence of implementation bugs;
- security against a malicious process with write access.

The protocol-level claim is bounded:

> The abstract core protocol was model-checked under documented
> assumptions. No counter-example was found for the listed invariants
> within the bounded state space TLC explored under these configurations.

This is **not** "safeatomic is formally verified". It is "the protocol
the library tries to implement passes a model check; the implementation
of that protocol in Python is exercised by the test suite, and the
environment that hosts it is probed at runtime via `doctor()` and
`inspect_guarantees()`".

That three-layer structure — model fixes the contract, tests exercise
the implementation against it, runtime probes check the environment —
is the entire claim. Each layer is independently inspectable.

## Reproducing the checks

The repeatable runner is at [`scripts/check-formal.sh`](../scripts/check-formal.sh).
From the repo root:

```sh
scripts/check-formal.sh
```

It runs TLC against every `.tla` file in `formal/` and exits non-zero if
any model fails. By default it writes its output to a temporary
directory so the committed reports under `formal/reports/` are not
disturbed; pass `--update-reports` to refresh those instead.

You need Java 11+ and `tla2tools.jar`. The pinned version, its SHA-256,
and the installation snippet are documented in
[`formal/README.md`](../formal/README.md#installing-tlc-locally).

## Reports

The committed run at `formal/reports/` includes:

- `MANIFEST.json` — machine-readable summary (toolchain versions,
  jar SHA-256, per-model invariants, states generated, distinct states,
  depth, result).
- One `.txt` file per model — the **raw, unedited TLC stdout** from
  the canonical run. These are the numbers `MANIFEST.json` is derived
  from; they are not curated.

The reports can be regenerated locally with `scripts/check-formal.sh
--update-reports`. A diff against the committed copy is the strongest
evidence that the protocol still passes on a different machine.

## Wheel packaging

The `formal/` directory and `scripts/check-formal.sh` are included in
the **source distribution (sdist)** but **excluded from the wheel**.
Users who `pip install safeatomic` do not get TLA+ files; users who
clone the repository or download the sdist do. This keeps the runtime
install lean while preserving full transparency for inspection.

The exclusion is enforced in `pyproject.toml` under
`[tool.hatch.build.targets.wheel]` (`packages = ["src/safeatomic"]`,
which restricts the wheel to the Python package). See the comments
there for the explicit rationale.

## Governance

The decision to treat TLA+ as **a source of discipline, not decoration**
is recorded in the project's design corpus as ADR-0007. The models are
maintained alongside the code: a change to the protocol must be
reflected in the model (and produce a new report), or the change is
rejected at review. The corollary — "stop and ask when the corpus is
silent" — applies symmetrically: a model change must come with a
corresponding code change, or it gets rejected.
