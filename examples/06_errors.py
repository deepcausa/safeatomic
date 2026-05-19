"""06 — Error hierarchy.

Four typed exceptions plus one warning. All exceptions inherit from
``SafeAtomicError`` (which inherits from ``Exception``):

    SafeAtomicError                       root; catch this for any
                                          semantic failure of the lib
      |- ChecksumMismatchError            sidecar disagrees with payload
      |- CrossDeviceAtomicityError        move_atomic detected EXDEV
      |- LockError                        lock acquisition / release
      `- UnsupportedEnvironmentError      strict mode + missing capability

``UnsupportedEnvironmentWarning`` is the warn-mode cognate (inherits from
``Warning``, not from ``SafeAtomicError``).

Run:
    python examples/06_errors.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from safeatomic import (
    ChecksumMismatchError,
    CrossDeviceAtomicityError,
    LockError,
    SafeAtomicError,
    UnsupportedEnvironmentError,
    UnsupportedEnvironmentWarning,
    read_atomic,
    write_atomic,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # (a) ChecksumMismatchError on tampered file with check_checksum=True.
        p = base / "corrupt-demo.txt"
        write_atomic(p, "original", write_checksum=True)
        p.write_bytes(b"tampered")
        try:
            read_atomic(p, check_checksum=True)
        except ChecksumMismatchError as exc:
            print(f"(a) caught ChecksumMismatchError: {exc}")

        # (b) Hierarchy via issubclass.
        print()
        print("(b) hierarchy:")
        for cls in (
            ChecksumMismatchError,
            CrossDeviceAtomicityError,
            LockError,
            UnsupportedEnvironmentError,
        ):
            print(f"    {cls.__name__:30s} <= SafeAtomicError: {issubclass(cls, SafeAtomicError)}")
        print(
            f"    UnsupportedEnvironmentWarning  <= Warning:        "
            f"{issubclass(UnsupportedEnvironmentWarning, Warning)}"
        )

        # (c) Recommended pattern: catch the root.
        print()
        print("(c) catching the root SafeAtomicError:")
        p2 = base / "another.txt"
        write_atomic(p2, "x", write_checksum=True)
        p2.write_bytes(b"y")
        try:
            read_atomic(p2, check_checksum=True)
        except SafeAtomicError as exc:
            print(f"    concrete type: {type(exc).__name__}")
            print(f"    message:       {exc}")


if __name__ == "__main__":
    main()
