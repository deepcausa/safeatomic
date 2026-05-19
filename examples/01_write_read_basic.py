"""01 — write_atomic / read_atomic: the core invariant.

A reader never observes a partial file. Either the previous state or the
new state is visible — never a half-written buffer.

Under the hood (ADR-0001):
  1. open <name>.<rand>.tmp in the same directory
  2. write payload + fsync(tmp_fd)
  3. os.replace(tmp, final)   <-- commit point
  4. fsync(parent_dir)        <-- directory entry durability

Run:
    python examples/01_write_read_basic.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from safeatomic import read_atomic, write_atomic


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "notes.txt"

        write_atomic(target, "line 1\nline 2\n")
        print("after 1st write:", read_atomic(target).splitlines())

        # Atomic overwrite. Concurrent readers never see a partial buffer.
        write_atomic(target, "new content\nanother line\nthird\n")
        print("after 2nd write:", read_atomic(target).splitlines())

        # Both str and Path are accepted. Bytes variants also exist
        # (write_atomic_bytes / read_atomic_bytes).
        print("on disk (bytes):", target.read_bytes())


if __name__ == "__main__":
    main()
