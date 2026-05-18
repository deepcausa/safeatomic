"""Checksum computation and sidecar management for safeatomic v2.

Provides streaming hash computation, sidecar read/write, and verification
helpers. These functions are used internally by ``_io_core`` (write and read
paths) and are exposed publicly via ``safeatomic.__all__`` wrappers.

Sidecar file format (written by :func:`write_checksum_file`)::

    <hex_digest>  <basename>
    algo=<algo>
    timestamp=<iso8601_utc>

Line 1 is GNU coreutils-compatible (digest + two spaces + filename).
Lines 2 and 3 are safeatomic-specific metadata. Readers are tolerant of
missing metadata lines and extra content.

Cross-refs:
- design/failure-model.md (checksum sidecar contract)
- design/implementation-discipline.md principle 6 (sidecars are part of
  the protocol)
- _paths.py (checksum_path derivation)
- _constants.py (CHECKSUM_CHUNK_SIZE, DEFAULT_CHECKSUM_ALGO, CHECKSUM_SUFFIX)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from safeatomic._constants import (
    CHECKSUM_CHUNK_SIZE,
    DEFAULT_CHECKSUM_ALGO,
)
from safeatomic._logging import logger
from safeatomic._paths import checksum_path

if TYPE_CHECKING:
    import os


# ---------------------------------------------------------------------------
# ChecksumInfo dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChecksumInfo:
    """Metadata about a checksum sidecar file.

    Attributes:
        path: Path of the target file (not the sidecar).
        algo: Hash algorithm name (e.g. ``"sha256"``).
        hash: Hex digest as written in the sidecar (lowercase).
        timestamp: Sidecar creation time (tz-aware UTC).
    """

    path: Path
    algo: str
    hash: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------


def compute_hash_file(
    path: str | os.PathLike[str],
    *,
    algo: str = DEFAULT_CHECKSUM_ALGO,
) -> str:
    """Compute the hash of a file by streaming it in chunks.

    Args:
        path: Path to the file to hash. Must exist and be readable.
        algo: Hash algorithm name recognised by :func:`hashlib.new`.
            Defaults to ``"sha256"``.

    Returns:
        Lowercase hex digest of the file contents.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        PermissionError: If the file cannot be read.
        ValueError: If ``algo`` is not a valid algorithm name.

    Examples:
        >>> import tempfile, pathlib
        >>> with tempfile.NamedTemporaryFile(delete=False) as f:
        ...     _ = f.write(b"hello")
        ...     p = pathlib.Path(f.name)
        >>> compute_hash_file(p) == compute_hash_data(b"hello")
        True
    """
    p = Path(path)
    # hashlib.new raises ValueError for unknown algorithm names.
    h = hashlib.new(algo)
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHECKSUM_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_hash_data(
    data: bytes,
    *,
    algo: str = DEFAULT_CHECKSUM_ALGO,
) -> str:
    """Compute the hash of an in-memory bytes object.

    Args:
        data: Bytes to hash. For text, the caller must encode first
            (e.g. ``text.encode("utf-8")``).
        algo: Hash algorithm name recognised by :func:`hashlib.new`.
            Defaults to ``"sha256"``.

    Returns:
        Lowercase hex digest of ``data``.

    Raises:
        ValueError: If ``algo`` is not a valid algorithm name.

    Examples:
        >>> compute_hash_data(b"hello", algo="sha256")
        '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    """
    h = hashlib.new(algo)
    h.update(data)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_checksum(
    path: str | os.PathLike[str],
    expected: str | None = None,
    *,
    algo: str = DEFAULT_CHECKSUM_ALGO,
) -> bool:
    """Verify the hash of a file against an expected digest.

    If ``expected`` is ``None``, the expected digest is read from the
    checksum sidecar (``<path>.sha256``).

    A mismatch is a normal operational outcome: this function returns
    ``False`` rather than raising. It only raises for I/O errors.

    Note:
        :func:`verify_checksum` does NOT raise
        :class:`~safeatomic._exceptions.ChecksumMismatchError`. That
        exception is raised by ``read_atomic(check_checksum=True)``
        (in ``_io_core``) where a mismatch means the returned data
        cannot be trusted.

        The returned ``bool`` is a statement about the **observed
        pair** ``(data, expected_hash)`` at the moment of the call.
        If a concurrent writer updates either side after this call
        returns, that does not retroactively invalidate the result;
        it just means the next call may see something different. The
        TLA+ ``SafeAtomicChecksum`` model in
        ``safeatomic-project/formal/SafeAtomicChecksum.tla`` formalises
        this property: ``Match`` only ever holds for a consistent
        observed pair, never for an inconsistent or future-state pair.

    Args:
        path: Path to the file to verify.
        expected: Expected hex digest (case-insensitive). If ``None``,
            the digest is read from the sidecar file.
        algo: Hash algorithm to use when computing the current digest.
            Defaults to ``"sha256"``.

    Returns:
        ``True`` if the computed digest matches ``expected``,
        ``False`` on mismatch.

    Raises:
        FileNotFoundError: If ``path`` does not exist, or if
            ``expected`` is ``None`` and the sidecar does not exist.
        PermissionError: If the file cannot be read.
        ValueError: If ``algo`` is not a valid algorithm name.
    """
    p = Path(path)

    if expected is None:
        info = get_checksum_info(p)
        if info is None:
            sidecar = checksum_path(p)
            raise FileNotFoundError(  # noqa: TRY003  # stdlib exception; no safeatomic subclass for FileNotFoundError
                f"checksum sidecar not found: {sidecar}"
            )
        expected = info.hash

    actual = compute_hash_file(p, algo=algo)
    return actual.lower() == expected.lower()


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------


def write_checksum_file(
    path: str | os.PathLike[str],
    *,
    algo: str = DEFAULT_CHECKSUM_ALGO,
) -> Path:
    """Write a checksum sidecar file for ``path``.

    The sidecar is placed at ``<path>.sha256`` and contains::

        <hex_digest>  <basename>
        algo=<algo>
        timestamp=<iso8601_utc>

    Line 1 uses the GNU coreutils format (digest + two spaces + filename)
    so the sidecar is compatible with ``sha256sum --check``.

    Args:
        path: Path to the data file. Must exist and be readable.
        algo: Hash algorithm to use. Defaults to ``"sha256"``.

    Returns:
        Path of the written sidecar file.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        PermissionError: If ``path`` cannot be read or the sidecar
            cannot be written.
        ValueError: If ``algo`` is not a valid algorithm name.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"target file not found: {p}")  # noqa: TRY003  # stdlib exception; no safeatomic subclass for FileNotFoundError

    digest = compute_hash_file(p, algo=algo)
    now = datetime.now(UTC)
    timestamp_str = now.isoformat()

    content = f"{digest}  {p.name}\nalgo={algo}\ntimestamp={timestamp_str}\n"

    sidecar = checksum_path(p)
    # TODO(_io_core): switch to write_atomic once available
    sidecar.write_text(content, encoding="ascii")
    return sidecar


def get_checksum_info(
    path: str | os.PathLike[str],
) -> ChecksumInfo | None:
    """Read and parse the checksum sidecar for ``path``.

    Returns ``None`` if the sidecar does not exist or cannot be parsed.
    Parse errors are tolerated and logged as warnings (not raised), because
    a corrupt sidecar is operationally recoverable: the caller can
    re-compute and re-write the sidecar.

    The parser accepts:

    - Missing ``algo=`` line (defaults to ``"sha256"``).
    - Missing ``timestamp=`` line (defaults to ``datetime.now(UTC)``).
    - Extra lines (ignored).

    Args:
        path: Path to the data file (not the sidecar).

    Returns:
        :class:`ChecksumInfo` if the sidecar exists and is parseable,
        ``None`` otherwise.

    Examples:
        >>> import tempfile, pathlib
        >>> with tempfile.NamedTemporaryFile(delete=False) as f:
        ...     _ = f.write(b"data")
        ...     p = pathlib.Path(f.name)
        >>> _ = write_checksum_file(p)
        >>> info = get_checksum_info(p)
        >>> info is not None
        True
    """
    p = Path(path)
    sidecar = checksum_path(p)

    if not sidecar.exists():
        return None

    try:
        raw = sidecar.read_text(encoding="ascii", errors="replace")
    except OSError:
        logger.warning("corrupt checksum sidecar at %s", sidecar)
        return None

    lines = raw.splitlines()
    if not lines:
        logger.warning("corrupt checksum sidecar at %s", sidecar)
        return None

    # Parse line 1: "<digest>  <basename>"
    first = lines[0].split()
    if len(first) < 1:
        logger.warning("corrupt checksum sidecar at %s", sidecar)
        return None
    digest = first[0].lower()

    # Parse remaining lines as key=value pairs (tolerant).
    kvs: dict[str, str] = {}
    for line in lines[1:]:
        if "=" in line:
            key, _, value = line.partition("=")
            kvs[key.strip()] = value.strip()

    algo = kvs.get("algo", DEFAULT_CHECKSUM_ALGO)

    timestamp_str = kvs.get("timestamp")
    if timestamp_str is not None:
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except ValueError:
            timestamp = datetime.now(UTC)
    else:
        timestamp = datetime.now(UTC)

    return ChecksumInfo(
        path=p,
        algo=algo,
        hash=digest,
        timestamp=timestamp,
    )
