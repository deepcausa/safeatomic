"""04 — File locks: serialize cooperating writers.

``write_atomic`` is atomic with respect to readers, but if two writers
fire simultaneously the second one wins (last-writer-wins). Use the lock
API when you need mutual exclusion between cooperating writers.

Mechanism: ``flock(2)`` on a sidecar ``<name>.lock`` file. Advisory lock
— it only protects against processes that opt in.

Gotcha: ``write_atomic`` defaults to ``concurrency='lock'``, which tries
to acquire the same lock itself. If you already hold the lock via
``try_acquire_lock``, you MUST pass ``concurrency='none'`` to
``write_atomic`` or it raises ``LockError``. The library does not fake
reentrancy silently — this is intentional.

Run:
    python examples/04_locks.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from safeatomic import (
    get_lock_age,
    inspect_lock,
    is_locked,
    release_lock,
    try_acquire_lock,
    write_atomic,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "shared-counter.txt"
        write_atomic(target, "0\n")

        # retries=0 means a single attempt.
        if not try_acquire_lock(target):
            raise RuntimeError("could not acquire lock")
        print("lock acquired")

        info = inspect_lock(target)
        print(f"  pid={info.pid} hostname={info.hostname} alive={info.alive}")
        print(f"  is_locked={is_locked(target)}")

        # Work protected by the lock. concurrency='none' is REQUIRED
        # because we are already holding the lock — see module docstring.
        current = int(target.read_text().strip())
        write_atomic(target, f"{current + 1}\n", concurrency="none")
        print(f"counter now: {target.read_text().strip()}")

        age = get_lock_age(target)
        if age is not None:
            print(f"lock age before release: {age:.4f}s")

        release_lock(target)
        print(f"after release: is_locked={is_locked(target)}")

        # Second acquire attempt while already held returns False.
        assert try_acquire_lock(target)
        assert not try_acquire_lock(target), "second acquire must fail"
        release_lock(target)
        print("re-acquire test ok")


if __name__ == "__main__":
    main()
