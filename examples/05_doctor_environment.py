"""05 — doctor: introspect the runtime environment.

``doctor(path)`` examines the filesystem hosting ``path`` and reports
what is actually supported. Useful before go-live to detect filesystems
that lie about fsync (tmpfs, some FUSE mounts) or that do not provide
atomic ``os.replace`` semantics.

ADR-0011: environment capabilities are probed per-path. Different
mountpoints can have different capabilities; do not cache globally.

Run:
    python examples/05_doctor_environment.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from safeatomic import doctor, inspect_guarantees


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        report = doctor(base)

        print("== Doctor report ==")
        print(f"path:       {report.path}")
        print(f"ok overall: {report.ok}")

        env = report.environment
        print()
        print("Environment:")
        print(f"  platform:                {env.platform}")
        print(f"  filesystem:              {env.filesystem}")
        print(f"  filesystem_class:        {env.filesystem_class}")
        print(f"  supports_fsync_file:     {env.supports_fsync_file}")
        print(f"  supports_fsync_dir:      {env.supports_fsync_dir}")
        print(f"  supports_atomic_replace: {env.supports_atomic_replace}")
        print(f"  symlink_policy:          {env.symlink_policy}")

        print()
        print("Individual checks:")
        for check in report.checks:
            print(f"  [{check.status:7s}] {check.name:32s} {check.detail}")

        print()
        print("== Guarantees ==")
        g = inspect_guarantees(base)
        for name, level in sorted(g.guarantees.items()):
            print(f"  {name:28s} -> {level}")


if __name__ == "__main__":
    main()
