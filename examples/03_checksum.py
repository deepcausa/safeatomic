"""03 — Checksum sidecars: corruption / bit-rot detection.

When you call ``write_atomic(..., write_checksum=True)``, in addition to
the data file a sidecar ``<name>.sha256`` is written with the digest of
the payload. The sidecar itself is written through the same atomic
protocol.

Then ``verify_checksum(path) -> bool`` and ``get_checksum_info(path)``
let you inspect the relationship between the file and its digest.

TLA+ insight (informs the API): ``verify_checksum`` reflects the state
NOW. A True result earlier does NOT imply True later if an out-of-band
mutation happened in between.

Run:
    python examples/03_checksum.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from safeatomic import (
    compute_hash_data,
    compute_hash_file,
    get_checksum_info,
    verify_checksum,
    write_atomic,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "payload.bin"

        # Write data + sidecar atomically.
        write_atomic(target, "important content", write_checksum=True)
        print("verify (right after write):", verify_checksum(target))

        info = get_checksum_info(target)
        assert info is not None
        print(f"  algo:      {info.algo}")
        print(f"  hash:      {info.hash[:16]}...")
        print(f"  timestamp: {info.timestamp}")

        # Out-of-band mutation simulates bit-rot or external tampering.
        target.write_bytes(b"tampered")
        print("verify (after tampering):", verify_checksum(target))

        # ad-hoc digests, without a sidecar
        print("hash of in-memory bytes:", compute_hash_data(b"hello world")[:16] + "...")
        print("hash of file on disk:   ", compute_hash_file(target)[:16] + "...")


if __name__ == "__main__":
    main()
