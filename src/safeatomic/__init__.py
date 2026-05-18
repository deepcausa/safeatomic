"""safeatomic — atomic file persistence with composable, inspectable guarantees.

safeatomic is base-layer infrastructure for systems that store state on
plain files. It sits between :func:`pathlib.Path.write_text` and a real
database. The library does not promise database semantics; it promises
*specific*, *observable* file-system guarantees that you opt into.

Four composable headline guarantees
-----------------------------------

- **AtomicVisibility**: a reader sees either the old file or the new
  file, never a torn write.
- **CrashDurability**: a successful write is durable across a power loss
  (within the assumption that ``fsync`` is honoured by the storage stack).
- **WriterExclusion**: cooperative whole-file locking prevents two
  callers using safeatomic from overlapping writes.
- **IntegrityDetection**: an optional ``.sha256`` sidecar lets a reader
  detect post-write corruption.

Each guarantee is opt-in via call-site kwargs and is observable at
runtime through :func:`inspect_guarantees` (normative matrix lookup) and
:func:`doctor` (empirical probes).

See ``README.md`` and the design corpus in the ``safeatomic-project``
internal repository for the full formalisation, including supporting
guarantees (ReaderConsistency, StaleRecovery, MetadataPreservation,
CrossDeviceSafety), the four ``GuaranteeLevel`` values, and the
``filesystem_class`` matrix.

The public API is exactly 43 names. Anything not in :data:`__all__` is
internal and may change without notice.
"""

from __future__ import annotations

__version__ = "2.0.0"

# ---------------------------------------------------------------------------
# Re-exports (43 public names)
# ---------------------------------------------------------------------------

from typing import Final

from safeatomic._capabilities import Environment
from safeatomic._checksum import (
    ChecksumInfo,
    compute_hash_data,
    compute_hash_file,
    get_checksum_info,
    verify_checksum,
    write_checksum_file,
)
from safeatomic._config import safeatomic_config
from safeatomic._doctor import DoctorCheck, DoctorReport, doctor
from safeatomic._exceptions import (
    ChecksumMismatchError,
    CrossDeviceAtomicityError,
    LockError,
    SafeAtomicError,
    UnsupportedEnvironmentError,
    UnsupportedEnvironmentWarning,
)
from safeatomic._formats_json import atomic_json_dump, atomic_json_load
from safeatomic._formats_toml import atomic_toml_dump, atomic_toml_load
from safeatomic._formats_yaml import (
    atomic_yaml_dump,
    atomic_yaml_dump_ruamel,
    atomic_yaml_load,
    atomic_yaml_load_ruamel,
)
from safeatomic._guarantees import GuaranteeReport, inspect_guarantees
from safeatomic._io_core import (
    AtomicReader,
    AtomicWriter,
    move_atomic,
    read_atomic,
    read_atomic_bytes,
    write_atomic,
    write_atomic_bytes,
)
from safeatomic._locks import (
    LockInfo,
    force_release_lock,
    get_lock_age,
    inspect_lock,
    is_locked,
    is_stale_lock,
    release_lock,
    release_stale_lock,
    try_acquire_lock,
)

_EXPECTED_PUBLIC_NAMES: Final[int] = 43

__all__ = [  # noqa: RUF022  # ordered by category, not alphabetically
    # IO core (7)
    "AtomicReader",
    "AtomicWriter",
    "move_atomic",
    "read_atomic",
    "read_atomic_bytes",
    "write_atomic",
    "write_atomic_bytes",
    # Locks (9)
    "LockInfo",
    "force_release_lock",
    "get_lock_age",
    "inspect_lock",
    "is_locked",
    "is_stale_lock",
    "release_lock",
    "release_stale_lock",
    "try_acquire_lock",
    # Checksum (6)
    "ChecksumInfo",
    "compute_hash_data",
    "compute_hash_file",
    "get_checksum_info",
    "verify_checksum",
    "write_checksum_file",
    # Formats (8)
    "atomic_json_dump",
    "atomic_json_load",
    "atomic_toml_dump",
    "atomic_toml_load",
    "atomic_yaml_dump",
    "atomic_yaml_dump_ruamel",
    "atomic_yaml_load",
    "atomic_yaml_load_ruamel",
    # Guarantees (3)
    "Environment",
    "GuaranteeReport",
    "inspect_guarantees",
    # Doctor (3)
    "DoctorCheck",
    "DoctorReport",
    "doctor",
    # Config (1)
    "safeatomic_config",
    # Exceptions + Warnings (6)
    "ChecksumMismatchError",
    "CrossDeviceAtomicityError",
    "LockError",
    "SafeAtomicError",
    "UnsupportedEnvironmentError",
    "UnsupportedEnvironmentWarning",
]

# Invariant: __all__ must contain exactly 43 names (frozen v2.0 contract).
# Breakdown: 7 IO + 9 Locks + 6 Checksum + 8 Formats + 3 Guarantees + 3 Doctor
# + 1 Config + 6 Exceptions/Warnings = 43.
# See design/api-v2-proposal.md §1 and adr/0005-public-api-surface.md.
assert len(__all__) == _EXPECTED_PUBLIC_NAMES, (  # noqa: S101
    f"__all__ has {len(__all__)} names; expected {_EXPECTED_PUBLIC_NAMES}"
)
