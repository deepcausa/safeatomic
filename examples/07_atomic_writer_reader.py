"""07 — AtomicWriter / AtomicReader: object-oriented API.

``write_atomic`` is fire-and-forget: you pass the full payload, it
writes. For streaming or chunked writes, use ``AtomicWriter`` — a
context manager that holds the tmp-file fd until commit at ``__exit__``.
If an exception propagates out of the ``with`` block, the tmp file is
removed (rollback) and the target keeps its previous content.

Gotcha: ``AtomicWriter.write()`` accepts bytes only. Encode text
explicitly. This is a design choice to prevent accidental mixing of
text and binary data.

``AtomicReader`` is the snapshot reader: it opens the file at a fixed
point in time and returns bytes even if another publisher replaces the
file concurrently — the reader keeps reading from the old inode.

Run:
    python examples/07_atomic_writer_reader.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from safeatomic import AtomicReader, AtomicWriter, write_atomic


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "large.log"

        # Streaming write. Commit happens at __exit__.
        with AtomicWriter(target) as w:
            for i in range(5):
                w.write(f"line {i}\n".encode())
        print("after writer:")
        print(target.read_text())

        # Snapshot read.
        with AtomicReader(target) as r:
            snap = r.read()
        print("snapshot bytes decoded:", snap.decode("utf-8").splitlines())

        # Snapshot isolation: while the reader holds the old fd, an
        # external publisher's write_atomic does not corrupt the snapshot.
        with AtomicReader(target) as r:
            head = r.read(20)
            write_atomic(target, "NEW-PAYLOAD\n")
            tail = r.read()
            full_snapshot = head + tail
        print()
        print("snapshot from old inode:", full_snapshot.decode().splitlines())
        print("current on-disk content:", target.read_text().splitlines())

        # Rollback on exception inside writer.
        rollback_target = Path(tmp) / "rollback.txt"
        write_atomic(rollback_target, "initial_state\n")
        try:
            with AtomicWriter(rollback_target) as w:
                w.write(b"partial write\n")
                raise RuntimeError("simulated failure mid-write")
        except RuntimeError as exc:
            print()
            print(f"caught: {exc}")
        # Still "initial_state" because commit never happened.
        print(f"after rollback: {rollback_target.read_text().rstrip()!r}")


if __name__ == "__main__":
    main()
