"""Atomic JSON read/write helpers for safeatomic v2.

# TODO(_io_core): import will resolve once _io_core lands

Thin wrappers around ``write_atomic`` / ``read_atomic`` (in ``_io_core``,
not yet available) that handle JSON serialisation and deserialisation.
Until ``_io_core`` lands, the import below is guarded with
``# type: ignore[import-not-found]``.

Cross-refs:
- design/api-v2-proposal.md (atomic_json_dump / atomic_json_load)
- design/implementation-discipline.md principle 10 (invariants before
  performance; do not inline I/O logic here, delegate to _io_core)
- _checksum.py (checksum sidecar protocol used by read_atomic internally)
- _constants.py (SafetyPolicy, DEFAULT_SAFETY, DEFAULT_CHECKSUM_ALGO)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from safeatomic._constants import (
    DEFAULT_CHECKSUM_ALGO,
    DEFAULT_CONCURRENCY,
    DEFAULT_SAFETY,
)
from safeatomic._io_core import read_atomic, write_atomic

if TYPE_CHECKING:
    import os

    from safeatomic._constants import ConcurrencyPolicy, SafetyPolicy


# ---------------------------------------------------------------------------
# JSON dump
# ---------------------------------------------------------------------------


def atomic_json_dump(
    path: str | os.PathLike[str],
    obj: object,
    *,
    indent: int | None = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    concurrency: ConcurrencyPolicy = DEFAULT_CONCURRENCY,
    preserve_metadata: bool = True,
    write_checksum: bool = False,
    checksum_algo: str = DEFAULT_CHECKSUM_ALGO,
    retries: int = 0,
    delay: float = 0.1,
    session: str | None = None,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> None:
    """Serialise ``obj`` to JSON and write it atomically to ``path``.

    Serialisation is performed by :func:`json.dumps`. The resulting text is
    written via ``write_atomic`` (from ``_io_core``), which handles the
    tmp-rename protocol, optional locking, and optional checksum sidecar.

    Args:
        path: Destination file. The parent directory must exist.
        obj: JSON-serialisable object.
        indent: Indentation level for pretty-printing. ``None`` produces
            compact output. Defaults to ``2``.
        sort_keys: If ``True``, dictionary keys are sorted. Defaults to
            ``False``.
        ensure_ascii: If ``True``, non-ASCII characters are escaped.
            Defaults to ``False`` (UTF-8 passthrough).
        concurrency: Concurrency policy (``"lock"`` or ``"none"``).
            Defaults to ``"lock"``.
        preserve_metadata: If ``True``, copy mtime/permissions from the
            existing target before replacing it. Defaults to ``True``.
        write_checksum: If ``True``, write a ``.sha256`` sidecar after
            the atomic replace. Defaults to ``False``.
        checksum_algo: Algorithm for the checksum sidecar. Defaults to
            ``"sha256"``.
        retries: Number of lock-acquisition retries. Defaults to ``0``.
        delay: Delay in seconds between retries. Defaults to ``0.1``.
        session: Optional lock session object (passed through to
            ``write_atomic``). Defaults to ``None``.
        safety: Safety policy (``"strict"``, ``"warn"``,
            ``"best_effort"``). Defaults to ``"strict"``.

    Raises:
        TypeError: If ``obj`` is not JSON-serialisable.
        json.JSONDecodeError: Not raised here (only on load paths).
        SafeAtomicError: Propagated from ``write_atomic`` on protocol
            failures.
        UnsupportedEnvironmentError: Under ``safety='strict'`` if the
            filesystem does not support the required guarantees.
    """
    text = json.dumps(obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
    write_atomic(
        path,
        text,
        encoding="utf-8",
        concurrency=concurrency,
        preserve_metadata=preserve_metadata,
        write_checksum=write_checksum,
        checksum_algo=checksum_algo,
        retries=retries,
        delay=delay,
        session=session,
        safety=safety,
    )


# ---------------------------------------------------------------------------
# JSON load
# ---------------------------------------------------------------------------


def atomic_json_load(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
    check_checksum: bool = False,
    checksum_algo: str = DEFAULT_CHECKSUM_ALGO,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> object:
    """Read ``path`` atomically and deserialise the contents as JSON.

    The file is read via ``read_atomic`` (from ``_io_core``). Parsing is
    performed by :func:`json.loads`. A :class:`json.JSONDecodeError` from
    parsing propagates without wrapping; it is the caller's signal that the
    file content is not valid JSON.

    Args:
        path: Source file to read.
        encoding: Text encoding for reading the file. Defaults to
            ``"utf-8"``.
        check_checksum: If ``True``, verify the ``.sha256`` sidecar before
            returning data. Raises
            :class:`~safeatomic._exceptions.ChecksumMismatchError` on
            mismatch. Defaults to ``False``.
        checksum_algo: Algorithm to use when verifying. Defaults to
            ``"sha256"``.
        safety: Safety policy. Defaults to ``"strict"``.

    Returns:
        The deserialised JSON value. Type is ``object`` because JSON has
        no fixed top-level type (can be dict, list, str, int, …).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ChecksumMismatchError: If ``check_checksum=True`` and the digest
            does not match the sidecar.
        SafeAtomicError: Propagated from ``read_atomic`` on protocol
            failures.
    """
    text: str = read_atomic(
        path,
        encoding=encoding,
        check_checksum=check_checksum,
        checksum_algo=checksum_algo,
        safety=safety,
    )
    return json.loads(text)
