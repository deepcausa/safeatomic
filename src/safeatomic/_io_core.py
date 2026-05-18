"""Core atomic I/O primitives for safeatomic v2.

Implements the seven public IO names:

- :func:`write_atomic` / :func:`write_atomic_bytes`
- :func:`read_atomic` / :func:`read_atomic_bytes`
- :func:`move_atomic`
- :class:`AtomicWriter`
- :class:`AtomicReader`

Protocol references
-------------------

- design/implementation-discipline.md principles 1-3, 5, 8-10
- design/failure-model.md (fault matrix, errno mapping)
- design/guarantees-formalization.md §2, §6, §9

Write protocol (14 steps, non-negotiable)
-----------------------------------------

1. Resolve path.
2. Safety gate (skip for ``best_effort``).
3. Acquire lock if ``concurrency == "lock"``; ``LockError`` if contended.
4. Generate tmp via :func:`~safeatomic._paths.tmp_path_for`.
5. ``os.open(tmp, O_CREAT | O_EXCL | O_WRONLY, 0o600)``.
6. Write data (encode str -> bytes with ``encoding``).
7. ``os.fsync(fd)``.
8. ``os.close(fd)``.
9. ``shutil.copystat(target, tmp)`` if ``preserve_metadata`` and target exists;
   log warning on ``OSError`` (best-effort only).
10. Same-device defensive check.
11. ``os.replace(tmp, target)`` - atomic visibility point.
12. fsync parent directory; suppress failure, log warning only.
13. Write checksum sidecar if ``write_checksum``; wrap failure in
    :exc:`~safeatomic._exceptions.SafeAtomicError`.
14. Release lock in ``finally`` if ``concurrency == "lock"``.

Cleanup semantics
-----------------

- Steps 4-10 fail -> unlink tmp (suppress ``OSError``), re-raise.
- Step 11 fails -> unlink tmp (suppress ``OSError``), re-raise.
- Step 12 fails -> file IS visible; log warning only, no removal.
- Step 13 fails -> file IS visible; raise per contract, no removal.
- ``release_lock`` always runs in ``finally``.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Final

from safeatomic._config import _UNSET, _Unset, resolve_config
from safeatomic._constants import (
    DEFAULT_CONCURRENCY,
    DEFAULT_SAFETY,
    ConcurrencyPolicy,
    SafetyPolicy,
)
from safeatomic._exceptions import (
    ChecksumMismatchError,
    CrossDeviceAtomicityError,
    LockError,
    SafeAtomicError,
    UnsupportedEnvironmentError,
    UnsupportedEnvironmentWarning,
)
from safeatomic._logging import logger
from safeatomic._paths import tmp_path_for

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import PathLike
    from typing import BinaryIO

# ---------------------------------------------------------------------------
# Module-level guarantee-set constants
# ---------------------------------------------------------------------------

_REQUIRED_WRITE_NONE: Final[frozenset[str]] = frozenset({"AtomicVisibility", "CrashDurability"})
_REQUIRED_WRITE_LOCK: Final[frozenset[str]] = frozenset(
    {"AtomicVisibility", "CrashDurability", "WriterExclusion"}
)
_REQUIRED_WRITE_CHECKSUM: Final[frozenset[str]] = frozenset(
    {"AtomicVisibility", "CrashDurability", "IntegrityDetection"}
)
_REQUIRED_READ: Final[frozenset[str]] = frozenset({"ReaderConsistency"})
_REQUIRED_READ_CHECKSUM: Final[frozenset[str]] = frozenset(
    {"ReaderConsistency", "IntegrityDetection"}
)
_REQUIRED_MOVE: Final[frozenset[str]] = frozenset({"AtomicVisibility", "CrossDeviceSafety"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_safety(
    path: Path,
    required: frozenset[str],
    safety: SafetyPolicy,
    *,
    stacklevel: int = 3,
) -> None:
    """Gate the operation against the guarantee report for *path*.

    Imports :func:`~safeatomic._guarantees.inspect_guarantees` locally to
    avoid import cycles between ``_io_core`` and ``_guarantees``.

    Args:
        path: Filesystem path to inspect.
        required: Set of guarantee names that must be at level
            ``"guaranteed"`` to pass under ``strict`` policy.
        safety: Caller-supplied safety policy.
        stacklevel: Forwarded to :func:`warnings.warn`; callers that wrap
            this helper should increase it by one per frame.

    Raises:
        UnsupportedEnvironmentError: Under ``strict`` if any required
            guarantee is not at level ``"guaranteed"``.

    Emits:
        UnsupportedEnvironmentWarning: Under ``warn`` if any required
            guarantee is not at level ``"guaranteed"``.
    """
    if safety == "best_effort":
        return

    # Local import to break the _io_core -> _guarantees import cycle.
    from typing import cast  # noqa: PLC0415

    from safeatomic._guarantees import GuaranteeName, inspect_guarantees  # noqa: PLC0415

    report = inspect_guarantees(path)

    # Cast str names to GuaranteeName for the typed Mapping.get() call.
    # Unknown guarantee names (not in the Literal set) safely return None
    # from .get(), which != "guaranteed", so they appear in the failure
    # list - the correct conservative behaviour.
    failed = [
        f"{name}={report.guarantees.get(cast('GuaranteeName', name))}"
        for name in sorted(required)
        if report.guarantees.get(cast("GuaranteeName", name)) != "guaranteed"
    ]

    if not failed:
        return

    detail = "; ".join(failed)
    msg = (
        f"environment at {path} does not provide required guarantees: {detail} "
        f"({report.environment.filesystem_class!r} filesystem)"
    )

    if safety == "warn":
        warnings.warn(msg, UnsupportedEnvironmentWarning, stacklevel=stacklevel)
    else:  # strict
        raise UnsupportedEnvironmentError(msg)


def _fsync_dir(directory: Path) -> None:
    """Fsync a directory, suppressing all failures with a warning.

    Per the write protocol step 12: the file is already visible at this
    point; we must not remove it. Failure here is logged as a warning only.
    """
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        logger.warning("fsync_dir: could not open directory %s for fsync: %s", directory, exc)
        return
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        logger.warning("fsync_dir: fsync failed on %s: %s", directory, exc)
    finally:
        with contextlib.suppress(OSError):
            os.close(dir_fd)


def _write_bytes_to_fd(fd: int, data: bytes) -> None:
    """Write *data* to an open file descriptor, handling partial writes."""
    view = memoryview(data)
    written = 0
    total = len(data)
    while written < total:
        n = os.write(fd, view[written:])
        written += n


def _resolve(path: str | PathLike[str]) -> Path:
    """Coerce *path* to an absolute :class:`~pathlib.Path`."""
    return Path(path).resolve()


def _raise_cross_device(src: Path, dst: Path) -> None:
    """Raise CrossDeviceAtomicityError for src/dst on different devices."""
    raise CrossDeviceAtomicityError(src=src, dst=dst)


# ---------------------------------------------------------------------------
# Write helpers (factored out to keep _write_core within complexity budget)
# ---------------------------------------------------------------------------


def _write_open_tmp(tmp: Path) -> int:
    """Open *tmp* with O_CREAT|O_EXCL|O_WRONLY, mode 0o600.

    Returns the file descriptor.
    """
    return os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)


def _write_copystat(target: Path, tmp: Path, *, preserve_metadata: bool) -> None:
    """Copy metadata from *target* to *tmp* if *preserve_metadata* and *target* exists.

    Logs a warning on failure; never raises.
    """
    if preserve_metadata and target.exists():
        try:
            shutil.copystat(target, tmp)
        except OSError as exc:
            logger.warning(
                "write_atomic: copystat(%s, %s) failed (metadata not preserved): %s",
                target,
                tmp,
                exc,
            )


def _write_device_check(tmp: Path, target: Path) -> None:
    """Raise CrossDeviceAtomicityError if *tmp* and *target* are on different devices.

    Defensive check: tmp is always derived from target.parent, so this
    should never trigger in practice. Errors in stat are silently ignored
    (let os.replace surface a better error).
    """
    try:
        tmp_dev = tmp.stat().st_dev
        parent_dev = target.parent.stat().st_dev
    except OSError:
        return
    if tmp_dev != parent_dev:
        _raise_cross_device(tmp, target)


def _write_checksum_sidecar(target: Path, checksum_algo: str) -> None:
    """Write the checksum sidecar for *target*, wrapping failures as SafeAtomicError."""
    from safeatomic._checksum import write_checksum_file  # noqa: PLC0415

    try:
        write_checksum_file(target, algo=checksum_algo)
    except Exception as exc:
        msg = f"write_atomic: failed to write checksum sidecar for {target}: {exc}"
        raise SafeAtomicError(msg) from exc


def _acquire_lock(
    target: Path,
    *,
    session: str | None,
    retries: int,
    delay: float,
    safety: SafetyPolicy,
) -> None:
    """Try to acquire the cooperative lock for *target*; raise LockError if contended."""
    from safeatomic._locks import try_acquire_lock  # noqa: PLC0415

    acquired = try_acquire_lock(
        target,
        session=session,
        retries=retries,
        delay=delay,
        safety=safety,
    )
    if not acquired:
        msg = f"could not acquire lock for {target}: file is locked by another writer"
        raise LockError(msg)


def _release_lock_suppress(target: Path) -> None:
    """Release the cooperative lock for *target*, suppressing all errors."""
    from safeatomic._locks import release_lock  # noqa: PLC0415

    with contextlib.suppress(Exception):
        release_lock(target)


# ---------------------------------------------------------------------------
# write_atomic / write_atomic_bytes
# ---------------------------------------------------------------------------


def _write_core(
    target: Path,
    data: bytes,
    *,
    concurrency: ConcurrencyPolicy,
    preserve_metadata: bool,
    write_checksum: bool,
    checksum_algo: str,
    retries: int,
    delay: float,
    session: str | None,
    safety: SafetyPolicy,
    required: frozenset[str],
) -> None:
    """Shared implementation for write_atomic and write_atomic_bytes.

    All path/type coercion and required-guarantee composition are done by
    the public wrappers before calling this function.
    """
    # Step 2: safety gate.
    _check_safety(target.parent, required, safety, stacklevel=4)

    # Step 3: acquire lock.
    if concurrency == "lock":
        _acquire_lock(target, session=session, retries=retries, delay=delay, safety=safety)

    tmp: Path | None = None
    fd: int | None = None
    try:
        # Step 4: generate tmp path.
        tmp = tmp_path_for(target)

        # Step 5: open tmp with O_CREAT | O_EXCL | O_WRONLY, mode 0o600.
        fd = _write_open_tmp(tmp)

        # Step 6: write data.
        _write_bytes_to_fd(fd, data)

        # Step 7: fsync the file fd.
        os.fsync(fd)

        # Step 8: close fd.
        os.close(fd)
        fd = None

        # Step 9: copy metadata (best-effort).
        _write_copystat(target, tmp, preserve_metadata=preserve_metadata)

        # Step 10: same-device defensive check.
        _write_device_check(tmp, target)

        # Step 11: atomic replace - visibility point.
        tmp.replace(target)
        tmp = None  # successfully placed; do not unlink on further errors

    except BaseException:
        # Cleanup for steps 4-10 and step 11 abort path.
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()
        raise

    finally:
        # Step 14: always release lock.
        if concurrency == "lock":
            _release_lock_suppress(target)

    # Step 12: fsync parent dir (file IS visible; errors are warnings only).
    _fsync_dir(target.parent)

    # Step 13: write checksum sidecar if requested.
    if write_checksum:
        _write_checksum_sidecar(target, checksum_algo)


def write_atomic(
    path: str | PathLike[str],
    data: str,
    *,
    encoding: str | _Unset = _UNSET,
    concurrency: ConcurrencyPolicy = DEFAULT_CONCURRENCY,
    preserve_metadata: bool = True,
    write_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    retries: int | _Unset = _UNSET,
    delay: float | _Unset = _UNSET,
    session: str | None = None,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> None:
    """Write *data* atomically to *path* (text variant).

    The target file is replaced atomically via ``os.replace``. Readers
    always see either the old file or the new file, never a partial write.

    Guarantee sets (see design/guarantees-formalization.md §2):

    - ``concurrency="none"``: AtomicVisibility + CrashDurability
    - ``concurrency="lock"``: + WriterExclusion
    - ``write_checksum=True``: + IntegrityDetection

    Args:
        path: Destination file path (need not exist).
        data: Text content to write.
        encoding: Encoding used to serialise *data* to bytes.
            Defaults to ``"utf-8"``.
        concurrency: ``"lock"`` (default) acquires a cooperative lock
            before writing; ``"none"`` skips locking.
        preserve_metadata: If ``True`` (default) and *path* already
            exists, copy its mode and timestamps to the replacement
            file via ``shutil.copystat``. Failures are logged and
            silently ignored (best-effort).
        write_checksum: Write a SHA-256 sidecar after a successful
            replace. Failure raises :exc:`SafeAtomicError` (the file
            is already visible at that point).
        checksum_algo: Hash algorithm for the sidecar.
            Defaults to ``"sha256"``.
        retries: Number of additional lock-acquisition attempts.
        delay: Seconds between lock-acquisition attempts.
        session: Optional caller-supplied session identifier for the
            lock payload. Only its digest is stored on disk.
        safety: Safety policy. ``"strict"`` (default) raises
            :exc:`UnsupportedEnvironmentError` if the required
            guarantees are not available. ``"warn"`` emits a warning.
            ``"best_effort"`` skips the gate.

    Raises:
        LockError: When ``concurrency="lock"`` and the lock cannot be
            acquired after all retries.
        CrossDeviceAtomicityError: If tmp and target land on different
            devices (defensive check; should not occur in practice).
        SafeAtomicError: If ``write_checksum=True`` and the sidecar
            write fails after a successful replace.
        UnsupportedEnvironmentError: Under ``safety="strict"`` when
            required guarantees are not available.
        OSError: For all other I/O failures.
    """
    # Step 0: resolve config (explicit > ContextVar > hard-coded default).
    encoding, checksum_algo, retries, delay = resolve_config(
        encoding=encoding,
        checksum_algo=checksum_algo,
        retries=retries,
        delay=delay,
    )

    # Step 1: resolve path.
    target = _resolve(path)
    raw = data.encode(encoding)

    # Compose required guarantee set.
    required = _REQUIRED_WRITE_NONE
    if concurrency == "lock":
        required = required | _REQUIRED_WRITE_LOCK
    if write_checksum:
        required = required | _REQUIRED_WRITE_CHECKSUM

    _write_core(
        target,
        raw,
        concurrency=concurrency,
        preserve_metadata=preserve_metadata,
        write_checksum=write_checksum,
        checksum_algo=checksum_algo,
        retries=retries,
        delay=delay,
        session=session,
        safety=safety,
        required=required,
    )


def write_atomic_bytes(
    path: str | PathLike[str],
    data: bytes,
    *,
    concurrency: ConcurrencyPolicy = DEFAULT_CONCURRENCY,
    preserve_metadata: bool = True,
    write_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    retries: int | _Unset = _UNSET,
    delay: float | _Unset = _UNSET,
    session: str | None = None,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> None:
    """Write *data* atomically to *path* (bytes variant).

    Identical to :func:`write_atomic` except *data* must be :class:`bytes`
    and there is no ``encoding`` parameter.

    Args:
        path: Destination file path (need not exist).
        data: Binary content to write.
        concurrency: ``"lock"`` (default) or ``"none"``.
        preserve_metadata: Copy mode/timestamps from existing target.
        write_checksum: Write SHA-256 sidecar after successful replace.
        checksum_algo: Hash algorithm for the sidecar.
        retries: Additional lock-acquisition attempts.
        delay: Seconds between retries.
        session: Caller session identifier (digest only stored).
        safety: Safety policy gate.

    Raises:
        LockError: Lock contention after all retries.
        CrossDeviceAtomicityError: Cross-device tmp/target.
        SafeAtomicError: Checksum sidecar write failure.
        UnsupportedEnvironmentError: Under ``safety="strict"``.
        OSError: Other I/O failures.
    """
    # Step 0: resolve config.
    _encoding, checksum_algo, retries, delay = resolve_config(
        encoding=_UNSET,
        checksum_algo=checksum_algo,
        retries=retries,
        delay=delay,
    )

    # Step 1: resolve path.
    target = _resolve(path)

    # Compose required guarantee set.
    required = _REQUIRED_WRITE_NONE
    if concurrency == "lock":
        required = required | _REQUIRED_WRITE_LOCK
    if write_checksum:
        required = required | _REQUIRED_WRITE_CHECKSUM

    _write_core(
        target,
        data,
        concurrency=concurrency,
        preserve_metadata=preserve_metadata,
        write_checksum=write_checksum,
        checksum_algo=checksum_algo,
        retries=retries,
        delay=delay,
        session=session,
        safety=safety,
        required=required,
    )


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def _read_verify_checksum(target: Path, checksum_algo: str) -> str:
    """Load checksum sidecar and return the expected hash.

    Aligned with ``verify_checksum`` standalone: a missing or unreadable
    sidecar raises :exc:`FileNotFoundError`, not
    :exc:`ChecksumMismatchError`. The two surfaces (read-with-checksum
    and standalone verify) share the same error contract for the
    "sidecar absent" condition.

    Raises:
        FileNotFoundError: If the checksum sidecar is missing or
            unreadable. The data file itself is not affected.
    """
    from safeatomic._checksum import get_checksum_info  # noqa: PLC0415
    from safeatomic._paths import checksum_path  # noqa: PLC0415

    info = get_checksum_info(target)
    if info is None:
        sidecar = checksum_path(target)
        msg = f"checksum sidecar not found: {sidecar}"
        raise FileNotFoundError(msg)
    _ = checksum_algo  # acknowledged: algo is encoded inside the sidecar
    return info.hash


def _read_compare_hash(target: Path, content: bytes, expected: str, checksum_algo: str) -> None:
    """Raise ChecksumMismatchError if *content* does not match *expected* hash."""
    from safeatomic._checksum import compute_hash_data  # noqa: PLC0415

    actual = compute_hash_data(content, algo=checksum_algo)
    if actual != expected:
        raise ChecksumMismatchError(
            path=target,
            expected=expected,
            actual=actual,
        )


# ---------------------------------------------------------------------------
# read_atomic / read_atomic_bytes
# ---------------------------------------------------------------------------


def _read_core(
    target: Path,
    *,
    check_checksum: bool,
    checksum_algo: str,
    safety: SafetyPolicy,
    required: frozenset[str],
) -> bytes:
    """Shared implementation for read_atomic and read_atomic_bytes."""
    # Step 1: safety gate.
    _check_safety(target, required, safety, stacklevel=4)

    # Step 2: get sidecar info if checksum requested.
    sidecar_hash: str | None = None
    if check_checksum:
        sidecar_hash = _read_verify_checksum(target, checksum_algo)

    # Step 3: read bytes.
    content = target.read_bytes()

    # Step 4: verify checksum.
    if check_checksum and sidecar_hash is not None:
        _read_compare_hash(target, content, sidecar_hash, checksum_algo)

    # Step 5: return bytes (caller decodes for text variant).
    return content


def read_atomic(
    path: str | PathLike[str],
    *,
    encoding: str | _Unset = _UNSET,
    check_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> str:
    """Read *path* atomically and return its content as a string.

    The checksum is computed over the raw bytes before decoding, so the
    digest matches what was recorded by :func:`write_atomic`.

    Guarantee sets:

    - Default: ReaderConsistency
    - ``check_checksum=True``: + IntegrityDetection

    Args:
        path: Source file path.
        encoding: Text encoding. Defaults to ``"utf-8"``.
        check_checksum: Verify against the checksum sidecar before
            returning. Raises :exc:`ChecksumMismatchError` on mismatch;
            raises :exc:`FileNotFoundError` if the sidecar is absent
            (same contract as standalone :func:`verify_checksum`).
        checksum_algo: Hash algorithm. Defaults to ``"sha256"``.
        safety: Safety policy gate.

    Returns:
        File content decoded with *encoding*.

    Raises:
        ChecksumMismatchError: If ``check_checksum=True`` and the digest
            does not match.
        FileNotFoundError: If ``check_checksum=True`` and the checksum
            sidecar is missing (aligned with ``verify_checksum``).
        UnsupportedEnvironmentError: Under ``safety="strict"``.
        OSError: I/O failures.
    """
    # Step 0: resolve config.
    encoding, checksum_algo, _retries, _delay = resolve_config(
        encoding=encoding,
        checksum_algo=checksum_algo,
        retries=_UNSET,
        delay=_UNSET,
    )

    target = _resolve(path)
    required = _REQUIRED_READ_CHECKSUM if check_checksum else _REQUIRED_READ
    raw = _read_core(
        target,
        check_checksum=check_checksum,
        checksum_algo=checksum_algo,
        safety=safety,
        required=required,
    )
    return raw.decode(encoding)


def read_atomic_bytes(
    path: str | PathLike[str],
    *,
    check_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> bytes:
    """Read *path* atomically and return its content as bytes.

    Args:
        path: Source file path.
        check_checksum: Verify against the checksum sidecar.
        checksum_algo: Hash algorithm. Defaults to ``"sha256"``.
        safety: Safety policy gate.

    Returns:
        Raw file content.

    Raises:
        ChecksumMismatchError: Checksum digest mismatch.
        FileNotFoundError: If ``check_checksum=True`` and the checksum
            sidecar is missing.
        UnsupportedEnvironmentError: Under ``safety="strict"``.
        OSError: I/O failures.
    """
    # Step 0: resolve config.
    _encoding, checksum_algo, _retries, _delay = resolve_config(
        encoding=_UNSET,
        checksum_algo=checksum_algo,
        retries=_UNSET,
        delay=_UNSET,
    )

    target = _resolve(path)
    required = _REQUIRED_READ_CHECKSUM if check_checksum else _REQUIRED_READ
    return _read_core(
        target,
        check_checksum=check_checksum,
        checksum_algo=checksum_algo,
        safety=safety,
        required=required,
    )


# ---------------------------------------------------------------------------
# move_atomic
# ---------------------------------------------------------------------------


def move_atomic(
    src: str | PathLike[str],
    dst: str | PathLike[str],
    *,
    force: bool = False,
    preserve_metadata: bool = True,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> None:
    """Move *src* to *dst* atomically.

    Uses ``os.replace`` (POSIX-atomic rename). Cross-device moves always
    raise :exc:`CrossDeviceAtomicityError`, regardless of the ``safety``
    setting, because cross-device rename falls back to copy-then-delete
    which breaks AtomicVisibility.

    Guarantee sets: AtomicVisibility + CrossDeviceSafety.

    Args:
        src: Source file path.
        dst: Destination file path.
        force: If ``True``, overwrite *dst* if it exists. If ``False``
            (default), raise :exc:`FileExistsError` when *dst* exists.
        preserve_metadata: Accepted for API uniformity; has no effect in
            v2.0 because ``os.replace`` is used directly (metadata is
            intrinsic to the inode rename). Reserved for future use.
        safety: Safety policy gate applied on *dst*'s parent.

    Raises:
        CrossDeviceAtomicityError: *src* and *dst* are on different
            devices. Always raised, regardless of ``safety``.
        FileExistsError: *dst* exists and ``force=False``.
        UnsupportedEnvironmentError: Under ``safety="strict"``.
        OSError: Other I/O failures.
    """
    # preserve_metadata accepted for API uniformity; no-op in v2.0 for move.
    _ = preserve_metadata

    # Step 1: resolve.
    src_path = _resolve(src)
    dst_path = _resolve(dst)

    # Step 2: safety gate on dst parent.
    _check_safety(dst_path.parent, _REQUIRED_MOVE, safety, stacklevel=2)

    # Step 3: same-device check - CrossDeviceAtomicityError ALWAYS.
    try:
        src_dev = src_path.stat().st_dev
        dst_dev = dst_path.parent.stat().st_dev
    except FileNotFoundError:
        # src missing or dst parent missing: let os.replace surface the
        # error with a natural OSError rather than CrossDeviceAtomicityError.
        src_dev = dst_dev = 0

    if src_dev != dst_dev:
        _raise_cross_device(src_path, dst_path)

    # Step 4: check force flag.
    if dst_path.exists() and not force:
        msg = f"destination already exists: {dst_path}"
        raise FileExistsError(errno.EEXIST, msg, str(dst_path))

    # Step 5: atomic replace. Normalise raw OSError(EXDEV) into
    # CrossDeviceAtomicityError - the pre-stat may have returned 0 on
    # FileNotFoundError (deferred path), and the kernel can still surface
    # EXDEV on rename. Contract: move_atomic NEVER leaks EXDEV.
    try:
        src_path.replace(dst_path)
    except OSError as err:
        if err.errno == errno.EXDEV:
            raise CrossDeviceAtomicityError(src=src_path, dst=dst_path) from err
        raise

    # Step 6: fsync dst parent dir.
    _fsync_dir(dst_path.parent)


# ---------------------------------------------------------------------------
# AtomicWriter
# ---------------------------------------------------------------------------


class AtomicWriter:
    """Context manager for streaming atomic writes.

    Use when you need fine-grained control over the write loop (e.g. large
    files, progressive serialisation). For simple one-shot writes prefer
    :func:`write_atomic` or :func:`write_atomic_bytes`.

    The file is written to a private tmp path (same directory as target)
    and atomically replaced on :meth:`commit`. The tmp is unlinked on
    :meth:`abort` or on context exit with an exception.

    Usage (bytes mode)::

        with AtomicWriter("/var/lib/app/blob") as w:
            for chunk in stream:
                w.write(chunk)
        # file replaced atomically on __exit__

    Note: :meth:`write` always accepts :class:`bytes`. For text, encode
    before calling :meth:`write`.

    Args:
        path: Destination file path.
        concurrency: ``"lock"`` or ``"none"``.
        preserve_metadata: Copy mode/timestamps from existing target.
        write_checksum: Write SHA-256 sidecar after commit.
        checksum_algo: Hash algorithm for the sidecar.
        retries: Lock-acquisition retries.
        delay: Seconds between lock retries.
        session: Caller session identifier for the lock payload.
        safety: Safety policy gate.
    """

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        concurrency: ConcurrencyPolicy = DEFAULT_CONCURRENCY,
        preserve_metadata: bool = True,
        write_checksum: bool = False,
        checksum_algo: str | _Unset = _UNSET,
        retries: int | _Unset = _UNSET,
        delay: float | _Unset = _UNSET,
        session: str | None = None,
        safety: SafetyPolicy = DEFAULT_SAFETY,
    ) -> None:
        # Resolve config at construction (explicit > ContextVar > default).
        _encoding, resolved_algo, resolved_retries, resolved_delay = resolve_config(
            encoding=_UNSET,
            checksum_algo=checksum_algo,
            retries=retries,
            delay=delay,
        )

        self._target = _resolve(path)
        self._concurrency = concurrency
        self._preserve_metadata = preserve_metadata
        self._write_checksum = write_checksum
        self._checksum_algo = resolved_algo
        self._retries = resolved_retries
        self._delay = resolved_delay
        self._session = session
        self._safety = safety

        self._tmp: Path | None = None
        self._fd: int | None = None
        self._committed: bool = False
        self._aborted: bool = False

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> AtomicWriter:
        """Open the tmp file and acquire lock if needed."""
        # Safety gate.
        required = _REQUIRED_WRITE_NONE
        if self._concurrency == "lock":
            required = required | _REQUIRED_WRITE_LOCK
        if self._write_checksum:
            required = required | _REQUIRED_WRITE_CHECKSUM
        _check_safety(self._target.parent, required, self._safety, stacklevel=2)

        # Acquire lock if needed.
        if self._concurrency == "lock":
            _acquire_lock(
                self._target,
                session=self._session,
                retries=self._retries,
                delay=self._delay,
                safety=self._safety,
            )

        # Generate tmp and open.
        tmp = tmp_path_for(self._target)
        try:
            fd = _write_open_tmp(tmp)
        except BaseException:
            # Open failed; release lock if we acquired one.
            if self._concurrency == "lock":
                _release_lock_suppress(self._target)
            raise

        self._tmp = tmp
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Commit on clean exit; abort on exception.

        If :meth:`abort` was already called inside the ``with`` block,
        the auto-commit is skipped (no double action, no RuntimeError).
        Same for explicit :meth:`commit`.
        """
        if exc_type is None:
            if not self._committed and not self._aborted:
                self.commit()
        else:
            self.abort()

    # ------------------------------------------------------------------
    # Public write surface
    # ------------------------------------------------------------------

    def write(self, data: bytes) -> int:
        """Write *data* to the tmp file.

        Args:
            data: Bytes to write. Text strings are not accepted; encode
                them before calling this method.

        Returns:
            Number of bytes written.

        Raises:
            TypeError: If *data* is not :class:`bytes`.
            RuntimeError: If called after :meth:`commit` or :meth:`abort`.
            OSError: If the underlying write fails.
        """
        if self._fd is None:
            msg = "AtomicWriter.write() called on a closed or uncommitted writer"
            raise RuntimeError(msg)
        _write_bytes_to_fd(self._fd, data)
        return len(data)

    def flush(self) -> None:
        """Fsync the tmp file descriptor.

        Raises:
            RuntimeError: If called after :meth:`commit` or :meth:`abort`.
            OSError: fsync failure.
        """
        if self._fd is None:
            msg = "AtomicWriter.flush() called on a closed writer"
            raise RuntimeError(msg)
        os.fsync(self._fd)

    @property
    def fileno(self) -> int:
        """The raw file descriptor for the tmp file.

        Raises:
            RuntimeError: If the writer is not open.
        """
        if self._fd is None:
            msg = "AtomicWriter.fileno accessed on a closed writer"
            raise RuntimeError(msg)
        return self._fd

    # ------------------------------------------------------------------
    # Commit / abort
    # ------------------------------------------------------------------

    def commit(self) -> None:
        """Finalise the write and atomically replace the target.

        Steps:

        - fsync and close the tmp fd.
        - ``shutil.copystat`` if ``preserve_metadata`` (best-effort).
        - ``Path.replace(target)`` - visibility point.
        - fsync parent directory (warnings only).
        - Write checksum sidecar if requested.
        - Release lock.

        Raises:
            RuntimeError: If called more than once.
            SafeAtomicError: Checksum sidecar write failure.
            OSError: Other I/O failures.
        """
        if self._committed:
            msg = "AtomicWriter.commit() already called"
            raise RuntimeError(msg)
        if self._fd is None or self._tmp is None:
            msg = "AtomicWriter.commit() called before __enter__ or after abort"
            raise RuntimeError(msg)

        fd = self._fd
        tmp = self._tmp

        try:
            # fsync + close fd.
            os.fsync(fd)
            os.close(fd)
            self._fd = None

            # copystat (best-effort).
            _write_copystat(self._target, tmp, preserve_metadata=self._preserve_metadata)

            # Atomic replace - visibility point.
            tmp.replace(self._target)
            self._tmp = None  # placed; do not unlink on subsequent errors

        except BaseException:
            # Cleanup on pre-visibility-point failure.
            if self._fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._fd)
                self._fd = None
            if self._tmp is not None:
                with contextlib.suppress(OSError):
                    self._tmp.unlink()
                self._tmp = None
            self._release_lock()
            raise

        self._committed = True

        # fsync parent dir (file is visible; warnings only).
        _fsync_dir(self._target.parent)

        # Write checksum sidecar if requested.
        if self._write_checksum:
            try:
                _write_checksum_sidecar(self._target, self._checksum_algo)
            except Exception:
                self._release_lock()
                raise

        self._release_lock()

    def abort(self) -> None:
        """Discard the in-progress write.

        Closes the tmp fd, unlinks the tmp file (suppresses OSError),
        and releases the lock. Safe to call multiple times or after
        :meth:`commit`.
        """
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
        if self._tmp is not None:
            with contextlib.suppress(OSError):
                self._tmp.unlink()
            self._tmp = None
        self._aborted = True
        self._release_lock()

    def _release_lock(self) -> None:
        """Release the cooperative lock if we hold one."""
        if self._concurrency == "lock":
            _release_lock_suppress(self._target)


# ---------------------------------------------------------------------------
# AtomicReader
# ---------------------------------------------------------------------------


class AtomicReader:
    """Context manager for streaming atomic reads.

    Opens the target file for reading. If ``check_checksum=True``, verifies
    the checksum sidecar *before* returning from ``__enter__``.

    The file is opened in binary mode internally.

    Usage::

        with AtomicReader("/var/lib/app/state.json") as r:
            content = r.read()

    Args:
        path: Source file path.
        check_checksum: Verify checksum sidecar on entry.
        checksum_algo: Hash algorithm. Defaults to ``"sha256"``.
        safety: Safety policy gate.
    """

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        check_checksum: bool = False,
        checksum_algo: str | _Unset = _UNSET,
        safety: SafetyPolicy = DEFAULT_SAFETY,
    ) -> None:
        # Resolve config at construction (explicit > ContextVar > default).
        _encoding, resolved_algo, _retries, _delay = resolve_config(
            encoding=_UNSET,
            checksum_algo=checksum_algo,
            retries=_UNSET,
            delay=_UNSET,
        )

        self._target = _resolve(path)
        self._check_checksum = check_checksum
        self._checksum_algo = resolved_algo
        self._safety = safety

        self._fobj: BinaryIO | None = None
        self._fd: int | None = None

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> AtomicReader:
        """Open the file for reading, verifying checksum if requested."""
        required = _REQUIRED_READ_CHECKSUM if self._check_checksum else _REQUIRED_READ
        _check_safety(self._target, required, self._safety, stacklevel=2)

        # Verify checksum before opening the fd so we fail fast.
        if self._check_checksum:
            self._verify_checksum_before_open()

        # Open in binary mode.
        fobj = self._target.open("rb")
        self._fobj = fobj
        self._fd = fobj.fileno()
        return self

    def _verify_checksum_before_open(self) -> None:
        """Verify the checksum sidecar before opening the file descriptor.

        Aligned with ``verify_checksum`` standalone and ``read_atomic``:
        a missing sidecar raises ``FileNotFoundError``; only a digest
        mismatch raises ``ChecksumMismatchError``.
        """
        from safeatomic._checksum import (  # noqa: PLC0415
            compute_hash_file,
            get_checksum_info,
        )
        from safeatomic._paths import checksum_path  # noqa: PLC0415

        info = get_checksum_info(self._target)
        if info is None:
            sidecar = checksum_path(self._target)
            msg = f"checksum sidecar not found: {sidecar}"
            raise FileNotFoundError(msg)
        actual = compute_hash_file(self._target, algo=self._checksum_algo)
        if actual != info.hash:
            raise ChecksumMismatchError(
                path=self._target,
                expected=info.hash,
                actual=actual,
            )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Close the file descriptor."""
        if self._fobj is not None:
            with contextlib.suppress(OSError):
                self._fobj.close()
        self._fobj = None
        self._fd = None

    # ------------------------------------------------------------------
    # Read surface
    # ------------------------------------------------------------------

    def read(self, n: int = -1) -> bytes:
        """Read and return up to *n* bytes, or the full file if *n* is -1.

        Raises:
            RuntimeError: If called outside the context manager.
            OSError: I/O failure.
        """
        if self._fobj is None:
            msg = "AtomicReader.read() called outside context manager"
            raise RuntimeError(msg)
        return self._fobj.read(n) or b""

    def readline(self) -> bytes:
        """Read and return one line including the newline.

        Raises:
            RuntimeError: If called outside the context manager.
            OSError: I/O failure.
        """
        if self._fobj is None:
            msg = "AtomicReader.readline() called outside context manager"
            raise RuntimeError(msg)
        return self._fobj.readline() or b""

    def __iter__(self) -> Iterator[bytes]:
        """Iterate over lines (bytes), each including its trailing newline."""
        if self._fobj is None:
            msg = "AtomicReader.__iter__() called outside context manager"
            raise RuntimeError(msg)
        yield from self._fobj

    @property
    def fileno(self) -> int:
        """Raw file descriptor.

        Raises:
            RuntimeError: If called outside the context manager.
        """
        if self._fd is None:
            msg = "AtomicReader.fileno accessed outside context manager"
            raise RuntimeError(msg)
        return self._fd
