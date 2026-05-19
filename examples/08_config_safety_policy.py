"""08 — Safety policy + safeatomic_config (scoped defaults).

Part 1 — safety policy (ADR-0011)
---------------------------------
The runtime environment does not always support every guarantee. tmpfs
files disappear at reboot; some FUSE mounts have non-atomic
``os.replace``. The ``safety`` policy controls the library's response
when a requested guarantee is not available:

  'strict'      raise UnsupportedEnvironmentError. Default. Fails fast,
                ideal for production.
  'warn'        emit UnsupportedEnvironmentWarning and continue. Useful
                in dev / CI.
  'best_effort' silent. Use only in known-limited environments.

Part 2 — safeatomic_config (context manager)
--------------------------------------------
Scopes default keyword arguments for a block of calls. Only four keys
are scopeable: ``encoding``, ``checksum_algo``, ``retries``, ``delay``.
By design ``safety``, ``concurrency``, ``preserve_metadata`` and
``write_checksum`` are NOT scopeable — they must be visible at the call
site.

Explicit keyword arguments at the call site always win over the
scoped default (principle 14).

Run:
    python examples/08_config_safety_policy.py
"""

from __future__ import annotations

import tempfile
import warnings
from pathlib import Path

from safeatomic import (
    UnsupportedEnvironmentWarning,
    safeatomic_config,
    write_atomic,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # --- Part 1: safety policies per-call ---
        p = base / "note.txt"

        try:
            write_atomic(p, "ok\n")  # safety='strict' implicit
            print("safety='strict' (default) — passed")
        except Exception as exc:
            print(f"safety='strict' raised {type(exc).__name__}: {exc}")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            write_atomic(p, "second\n", safety="warn")
            env_warnings = [
                w for w in caught if issubclass(w.category, UnsupportedEnvironmentWarning)
            ]
            print(f"safety='warn' — captured {len(env_warnings)} env warning(s)")

        write_atomic(p, "third\n", safety="best_effort")
        print("safety='best_effort' — completed quietly")

        # --- Part 2: safeatomic_config for scoped defaults ---
        print()
        print("== safeatomic_config: scoped defaults ==")

        p2 = base / "scoped.txt"
        write_atomic(p2, "default scope", write_checksum=True)
        sidecar_sha256 = p2.with_suffix(p2.suffix + ".sha256")
        print(f"outside block: sha256 sidecar exists? {sidecar_sha256.exists()}")

        with safeatomic_config(checksum_algo="sha512", retries=3, delay=0.05):
            write_atomic(p2, "scoped to sha512", write_checksum=True)
            sidecar_sha512 = p2.with_suffix(p2.suffix + ".sha512")
            print(f"inside  block: sha512 sidecar exists? {sidecar_sha512.exists()}")

        # Explicit-wins demonstration.
        with safeatomic_config(checksum_algo="sha512"):
            write_atomic(p2, "override", write_checksum=True, checksum_algo="sha256")
            print(
                "explicit checksum_algo='sha256' overrode scoped 'sha512': "
                f"sha256 sidecar exists? {sidecar_sha256.exists()}"
            )


if __name__ == "__main__":
    main()
