"""Environment detection for safeatomic v2.

Detects the runtime environment (platform, filesystem, capabilities) that
governs which guarantees the library can provide. The detection is cached
per device id (``st_dev``) because the relevant properties are stable for
a given mount.

This module is NOT part of the public API surface in itself; it is used
internally by :mod:`safeatomic._guarantees` (which exposes the user-facing
:func:`inspect_guarantees`).

Cross-refs:
- design/guarantees-formalization.md §7 (Environment vector)
- design/guarantees-formalization.md §9 (filesystem class matrix)
- design/implementation-discipline.md principles 3, 13
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from pathlib import Path
from typing import Final, Literal, NamedTuple

_MOUNT_LINE_MIN_FIELDS: Final[int] = 3
"""Minimum field count for a valid /proc/mounts line (device, mp, fstype)."""

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Capability = Literal["yes", "no", "unknown"]
"""Tri-state capability flag.

Replaces a boolean. Some environments cannot be probed without performing
the actual operation (e.g. fsync on certain virtualised disks), so the
honest answer is sometimes ``"unknown"`` rather than a guessed boolean.
"""

Platform = Literal[
    "linux",
    "darwin",
    "freebsd",
    "openbsd",
    "netbsd",
    "windows",
    "unknown",
]

FilesystemClass = Literal[
    "local_posix_persistent",  # ext4, xfs, btrfs, apfs, zfs, …
    "local_posix_memory",      # tmpfs, ramfs
    "network",                 # nfs, smbfs, cifs, sshfs, fuse-over-net
    "windows",                 # ntfs, refs
    "object_store",            # s3fs, gcsfuse via fuse
    "unknown",                 # could not classify
]

SymlinkPolicy = Literal["unspecified"]
"""v2.0 only declares ``unspecified``. Future versions may add
``follow_target``, ``replace_link``, ``reject_symlink``. The single-value
Literal makes the dimension explicit in the type system without committing
to a richer surface yet.
"""


class Environment(NamedTuple):
    """Snapshot of the runtime environment relevant to guarantees.

    Attributes:
        platform: Operating system family.
        filesystem: Filesystem type string as reported by the OS
            (``ext4``, ``nfs4``, ``tmpfs``, …). ``None`` if detection
            failed.
        filesystem_class: Categorical class derived from ``filesystem``
            (or from probing if ``filesystem is None``). See
            ``design/guarantees-formalization.md`` §7.
        supports_fsync_file: Whether ``fsync`` on a regular file is
            honoured by the storage stack.
        supports_fsync_dir: Whether ``fsync`` on a directory is
            supported (POSIX-only; required for AtomicVisibility on
            durability path).
        supports_atomic_replace: Whether ``os.replace`` is atomic on
            this filesystem class.
        symlink_policy: Always ``"unspecified"`` in v2.0.

    See ``design/guarantees-formalization.md`` §7 for full semantics.
    """

    platform: Platform
    filesystem: str | None
    filesystem_class: FilesystemClass
    supports_fsync_file: Capability
    supports_fsync_dir: Capability
    supports_atomic_replace: Capability
    symlink_policy: SymlinkPolicy


# ---------------------------------------------------------------------------
# Filesystem name -> class lookup table
# ---------------------------------------------------------------------------
#
# Names are matched case-insensitively against the lowercased fstype.
# When a name is not in the table, the class is ``"unknown"`` (subject
# to the POSIX-probing fallback in ``detect_environment``).
#
# This table is intentionally explicit rather than rule-based: a typo in
# /proc/mounts should not silently reclassify NTFS as POSIX-persistent.
# Cross-ref: design/guarantees-formalization.md §9 (observed environments).

_FILESYSTEM_CLASS: Final[dict[str, FilesystemClass]] = {
    # local persistent POSIX
    "ext2": "local_posix_persistent",
    "ext3": "local_posix_persistent",
    "ext4": "local_posix_persistent",
    "xfs": "local_posix_persistent",
    "btrfs": "local_posix_persistent",
    "zfs": "local_posix_persistent",
    "apfs": "local_posix_persistent",
    "hfs": "local_posix_persistent",
    "ufs": "local_posix_persistent",
    "f2fs": "local_posix_persistent",
    "jfs": "local_posix_persistent",
    "reiserfs": "local_posix_persistent",
    # container overlay layers; backing FS is typically POSIX-persistent
    # and rename/fsync work as expected, but writes go through the layer.
    "overlay": "local_posix_persistent",
    "overlayfs": "local_posix_persistent",
    # in-memory POSIX-shaped
    "tmpfs": "local_posix_memory",
    "ramfs": "local_posix_memory",
    "devtmpfs": "local_posix_memory",
    # network-attached
    "nfs": "network",
    "nfs3": "network",
    "nfs4": "network",
    "smbfs": "network",
    "smb3": "network",
    "cifs": "network",
    "sshfs": "network",
    "fuse.sshfs": "network",
    # Windows-native (Tier 3, NonTarget in v2.0)
    "ntfs": "windows",
    "ntfs3": "windows",
    "refs": "windows",
    # object stores via FUSE
    "fuse.s3fs": "object_store",
    "s3fs": "object_store",
    "fuse.gcsfuse": "object_store",
    "gcsfuse": "object_store",
}


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _detect_platform() -> Platform:  # noqa: PLR0911  # explicit enumeration is clearer than a lookup table for 6 cases
    """Return the current OS family as a :data:`Platform` literal.

    Honest mapping; ``"unknown"`` for anything not enumerated rather than
    a misleading guess. ``sys.platform`` strings are stable across Python
    versions.
    """
    sp = sys.platform
    if sp.startswith("linux"):
        return "linux"
    if sp == "darwin":
        return "darwin"
    if sp.startswith("freebsd"):
        return "freebsd"
    if sp.startswith("openbsd"):
        return "openbsd"
    if sp.startswith("netbsd"):
        return "netbsd"
    if sp in {"win32", "cygwin"}:
        return "windows"
    return "unknown"


# ---------------------------------------------------------------------------
# Filesystem detection (Linux: /proc/mounts; others: best-effort)
# ---------------------------------------------------------------------------


def _read_proc_mounts() -> list[tuple[str, str]] | None:
    """Parse ``/proc/mounts`` and return ``[(mountpoint, fstype), ...]``.

    Returns ``None`` if the file is not available (non-Linux, restricted
    container). The parser is intentionally tolerant: malformed lines are
    skipped rather than raised.
    """
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:  # noqa: PTH123
            lines = fh.readlines()
    except OSError:
        return None

    out: list[tuple[str, str]] = []
    for raw in lines:
        # Format: device mountpoint fstype options dump pass
        parts = raw.split()
        if len(parts) < _MOUNT_LINE_MIN_FIELDS:
            continue
        mountpoint, fstype = parts[1], parts[2]
        out.append((mountpoint, fstype.lower()))
    return out


def _detect_filesystem(path: Path) -> str | None:
    """Return the filesystem type string for ``path``, or ``None``.

    On Linux, parses ``/proc/mounts`` and picks the longest mountpoint
    that is a prefix of the resolved path. On non-Linux platforms,
    returns ``None`` (no portable detection without third-party
    dependencies). The caller must handle the ``None`` case by probing.
    """
    if _detect_platform() != "linux":
        return None

    mounts = _read_proc_mounts()
    if not mounts:
        return None

    try:
        resolved = path.resolve()
    except OSError:
        # Path does not exist yet; resolve the parent that does.
        parent = path
        while parent != parent.parent and not parent.exists():
            parent = parent.parent
        try:
            resolved = parent.resolve()
        except OSError:
            return None

    target = str(resolved)
    best_mount = ""
    best_fstype: str | None = None
    for mount, fstype in mounts:
        if (target == mount or target.startswith(mount.rstrip("/") + "/")) and len(
            mount,
        ) > len(best_mount):
            best_mount = mount
            best_fstype = fstype

    return best_fstype


# ---------------------------------------------------------------------------
# Capability probes
# ---------------------------------------------------------------------------
#
# These are conservative. They never write to ``path``; they create a
# transient file in the parent directory and remove it. If the probe
# itself cannot run (parent missing, permission denied), the capability
# is "unknown" — never silently "yes".


def _probe_fsync_file(parent: Path) -> Capability:
    """Probe whether ``fsync`` on a regular file works inside ``parent``."""
    if not parent.is_dir():
        return "unknown"
    probe = parent / f".safeatomic-probe-fsync-file-{os.getpid()}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
            with contextlib.suppress(OSError):
                probe.unlink()
    except OSError:
        return "no"
    return "yes"


def _probe_fsync_dir(parent: Path) -> Capability:
    """Probe whether ``fsync`` on a directory file descriptor works.

    Required for AtomicVisibility's durability path: after ``os.replace``,
    the parent directory must be fsync'd so the rename survives crash.
    """
    if not parent.is_dir():
        return "unknown"
    try:
        fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return "unknown"
    try:
        os.fsync(fd)
    except OSError:
        return "no"
    finally:
        os.close(fd)
    return "yes"


def _probe_atomic_replace(parent: Path) -> Capability:
    """Probe whether ``os.replace`` succeeds inside ``parent``.

    This does not prove atomicity (only a model checker or kernel audit
    can do that); it proves the syscall is wired and the filesystem
    accepts cross-name rename. False results downgrade the capability.
    """
    if not parent.is_dir():
        return "unknown"
    src = parent / f".safeatomic-probe-replace-src-{os.getpid()}"
    dst = parent / f".safeatomic-probe-replace-dst-{os.getpid()}"
    try:
        src.write_bytes(b"")
        # os.replace is precisely what we are probing here; do not lint
        # towards Path.replace because that masks the syscall identity.
        os.replace(src, dst)  # noqa: PTH105
    except OSError:
        # Cleanup whatever survived.
        for p in (src, dst):
            with contextlib.suppress(OSError):
                p.unlink()
        return "no"
    else:
        with contextlib.suppress(OSError):
            dst.unlink()
        return "yes"


# ---------------------------------------------------------------------------
# Cache keyed by st_dev
# ---------------------------------------------------------------------------
#
# Repeated detection of the same mount is wasteful and would dominate
# tight write loops. The cache is keyed by ``st_dev`` (the kernel's
# device id), which is stable for a mount across the process lifetime.
#
# A reentrant lock guards the dict for concurrent access; the detection
# functions themselves do I/O and must not be called under the lock.

_CACHE: dict[int, Environment] = {}
_CACHE_LOCK: threading.RLock = threading.RLock()


def _cache_get(st_dev: int) -> Environment | None:
    with _CACHE_LOCK:
        return _CACHE.get(st_dev)


def _cache_put(st_dev: int, env: Environment) -> None:
    with _CACHE_LOCK:
        _CACHE[st_dev] = env


def clear_cache() -> None:
    """Discard cached environment entries.

    Intended for tests. Production code does not need to call this;
    the cache key (``st_dev``) cannot stale within a process lifetime
    under normal conditions.
    """
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Public detection entry point
# ---------------------------------------------------------------------------


def detect_environment(path: str | os.PathLike[str]) -> Environment:
    """Detect the :class:`Environment` for the filesystem containing ``path``.

    The path itself does not need to exist; the parent directory chain is
    walked upward until an existing ancestor is found and used for
    ``st_dev`` and capability probing.

    Results are cached by ``st_dev``. Use :func:`clear_cache` to reset.

    Cross-ref: design/guarantees-formalization.md §7.
    """
    p = Path(path)

    # Walk up to find an existing ancestor for probing.
    probe_dir = p if p.exists() else p.parent
    while probe_dir != probe_dir.parent and not probe_dir.exists():
        probe_dir = probe_dir.parent

    try:
        st = probe_dir.stat()
    except OSError:
        # Cannot determine even the root. Return fully unknown environment.
        return Environment(
            platform=_detect_platform(),
            filesystem=None,
            filesystem_class="unknown",
            supports_fsync_file="unknown",
            supports_fsync_dir="unknown",
            supports_atomic_replace="unknown",
            symlink_policy="unspecified",
        )

    cached = _cache_get(st.st_dev)
    if cached is not None:
        return cached

    platform = _detect_platform()
    fstype = _detect_filesystem(p)

    parent_dir = probe_dir if probe_dir.is_dir() else probe_dir.parent
    fsync_file = _probe_fsync_file(parent_dir)
    fsync_dir = _probe_fsync_dir(parent_dir)
    atomic_replace = _probe_atomic_replace(parent_dir)

    fs_class = _classify_filesystem(
        fstype,
        platform=platform,
        supports_fsync_dir=fsync_dir,
        supports_atomic_replace=atomic_replace,
    )

    env = Environment(
        platform=platform,
        filesystem=fstype,
        filesystem_class=fs_class,
        supports_fsync_file=fsync_file,
        supports_fsync_dir=fsync_dir,
        supports_atomic_replace=atomic_replace,
        symlink_policy="unspecified",
    )
    _cache_put(st.st_dev, env)
    return env


def _classify_filesystem(
    fstype: str | None,
    *,
    platform: Platform,
    supports_fsync_dir: Capability,
    supports_atomic_replace: Capability,
) -> FilesystemClass:
    """Map a filesystem name to its :data:`FilesystemClass`.

    Strategy:
    1. If ``fstype`` is in the lookup table, use it.
    2. Otherwise, on Windows platform, classify as ``"windows"``.
    3. Otherwise, if probing detects POSIX semantics (fsync on dir and
       atomic replace both ``"yes"``), classify as
       ``"local_posix_persistent"``.
    4. Otherwise, ``"unknown"``.

    The POSIX-probing fallback is documented in
    ``design/guarantees-formalization.md`` §7: when the filesystem name
    cannot be determined but the probes succeed, the environment behaves
    as a local POSIX-persistent filesystem from safeatomic's perspective.
    """
    if fstype is not None:
        klass = _FILESYSTEM_CLASS.get(fstype)
        if klass is not None:
            return klass

    if platform == "windows":
        return "windows"

    if supports_fsync_dir == "yes" and supports_atomic_replace == "yes":
        return "local_posix_persistent"

    return "unknown"
