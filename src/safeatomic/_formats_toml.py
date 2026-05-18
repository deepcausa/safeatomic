"""Atomic TOML read/write helpers for safeatomic v2.

# TODO(_io_core): import will resolve once _io_core lands

Thin wrappers around ``write_atomic`` / ``read_atomic`` (in ``_io_core``,
not yet available) that handle TOML serialisation and deserialisation.

Serialisation uses ``tomli_w`` (third-party, already in project
dependencies). Deserialisation uses ``tomllib`` from the standard library
(Python 3.11+).

Cross-refs:
- design/api-v2-proposal.md (atomic_toml_dump / atomic_toml_load)
- design/implementation-discipline.md principle 10
- _checksum.py (checksum sidecar protocol)
- _constants.py (SafetyPolicy, DEFAULT_SAFETY, DEFAULT_CHECKSUM_ALGO)
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import tomli_w

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
# TOML dump
# ---------------------------------------------------------------------------


def atomic_toml_dump(
    path: str | os.PathLike[str],
    obj: dict[str, object],
    *,
    concurrency: ConcurrencyPolicy = DEFAULT_CONCURRENCY,
    preserve_metadata: bool = True,
    write_checksum: bool = False,
    checksum_algo: str = DEFAULT_CHECKSUM_ALGO,
    retries: int = 0,
    delay: float = 0.1,
    session: str | None = None,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> None:
    """Serialise ``obj`` to TOML and write it atomically to ``path``.

    TOML requires a mapping at the top level; ``obj`` must be a
    ``dict[str, object]``. Serialisation is performed by
    :func:`tomli_w.dumps`. The resulting text is written via
    ``write_atomic`` (from ``_io_core``).

    Args:
        path: Destination file. The parent directory must exist.
        obj: TOML-serialisable mapping. Must be a ``dict`` with string
            keys; TOML does not support non-mapping top-level values.
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
        session: Optional lock session object. Defaults to ``None``.
        safety: Safety policy (``"strict"``, ``"warn"``,
            ``"best_effort"``). Defaults to ``"strict"``.

    Raises:
        TypeError: If ``obj`` contains values that ``tomli_w`` cannot
            serialise.
        SafeAtomicError: Propagated from ``write_atomic`` on protocol
            failures.
        UnsupportedEnvironmentError: Under ``safety='strict'`` if the
            filesystem does not support the required guarantees.
    """
    text = tomli_w.dumps(obj)
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
# TOML load
# ---------------------------------------------------------------------------


def atomic_toml_load(
    path: str | os.PathLike[str],
    *,
    check_checksum: bool = False,
    checksum_algo: str = DEFAULT_CHECKSUM_ALGO,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> dict[str, object]:
    """Read ``path`` atomically and deserialise the contents as TOML.

    The file is read via ``read_atomic`` (from ``_io_core``). Parsing is
    performed by :func:`tomllib.loads`. A :class:`tomllib.TOMLDecodeError`
    from parsing propagates without wrapping.

    Args:
        path: Source file to read.
        check_checksum: If ``True``, verify the ``.sha256`` sidecar before
            returning data. Raises
            :class:`~safeatomic._exceptions.ChecksumMismatchError` on
            mismatch. Defaults to ``False``.
        checksum_algo: Algorithm to use when verifying. Defaults to
            ``"sha256"``.
        safety: Safety policy. Defaults to ``"strict"``.

    Returns:
        The deserialised TOML document as a ``dict[str, object]``.
        TOML always has a mapping at the top level.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
        ChecksumMismatchError: If ``check_checksum=True`` and the digest
            does not match the sidecar.
        SafeAtomicError: Propagated from ``read_atomic`` on protocol
            failures.
    """
    text: str = read_atomic(
        path,
        encoding="utf-8",
        check_checksum=check_checksum,
        checksum_algo=checksum_algo,
        safety=safety,
    )
    return tomllib.loads(text)
