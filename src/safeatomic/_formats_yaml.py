"""Atomic YAML read/write helpers for safeatomic v2.

# TODO(_io_core): import will resolve once _io_core lands

Provides two pairs of functions:

- :func:`atomic_yaml_dump` / :func:`atomic_yaml_load` — PyYAML safe
  serialisation (``yaml.safe_dump`` / ``yaml.safe_load``). PyYAML is a
  required dependency.
- :func:`atomic_yaml_dump_ruamel` / :func:`atomic_yaml_load_ruamel` —
  ruamel.yaml round-trip serialisation. ruamel is an optional extra;
  the functions raise :class:`ImportError` at call time if the package
  is not installed. The import is lazy (inside the function body) so
  that the module is importable without the ``ruamel`` extra installed.

Cross-refs:
- design/api-v2-proposal.md (atomic_yaml_* functions)
- design/implementation-discipline.md principle 10
- _checksum.py (checksum sidecar protocol)
- _constants.py (SafetyPolicy, DEFAULT_SAFETY, DEFAULT_CHECKSUM_ALGO)
"""

from __future__ import annotations

import io
from typing import IO, TYPE_CHECKING, Protocol

import yaml

from safeatomic._config import _UNSET, _Unset
from safeatomic._constants import DEFAULT_CONCURRENCY, DEFAULT_SAFETY
from safeatomic._io_core import read_atomic, write_atomic

if TYPE_CHECKING:
    import os

    from safeatomic._constants import ConcurrencyPolicy, SafetyPolicy


# ---------------------------------------------------------------------------
# Internal protocol for ruamel YAML instances
# ---------------------------------------------------------------------------
# ruamel.yaml has no shipped stubs. We declare only the methods and attributes
# we actually use, so mypy can type-check our code without Any or ignores.


class _RuamelYAML(Protocol):
    """Structural protocol for the subset of ruamel.yaml.YAML we use."""

    width: int | None

    def indent(self, *, mapping: int, sequence: int, offset: int) -> None:
        """Configure indentation."""
        ...

    def dump(self, data: object, stream: IO[str]) -> None:
        """Serialise ``data`` to ``stream``."""
        ...

    def load(self, stream: str) -> object:
        """Deserialise from ``stream``."""
        ...


# ---------------------------------------------------------------------------
# PyYAML safe dump/load
# ---------------------------------------------------------------------------


def atomic_yaml_dump(
    path: str | os.PathLike[str],
    obj: object,
    *,
    default_flow_style: bool = False,
    sort_keys: bool = False,
    concurrency: ConcurrencyPolicy = DEFAULT_CONCURRENCY,
    preserve_metadata: bool = True,
    write_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    retries: int | _Unset = _UNSET,
    delay: float | _Unset = _UNSET,
    session: str | None = None,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> None:
    """Serialise ``obj`` to YAML and write it atomically to ``path``.

    Serialisation is performed by :func:`yaml.safe_dump` (PyYAML, a
    required dependency). The result is written via ``write_atomic``
    (from ``_io_core``).

    Args:
        path: Destination file. The parent directory must exist.
        obj: YAML-serialisable object.
        default_flow_style: If ``True``, use YAML flow style (inline
            sequences and mappings). Defaults to ``False`` (block style).
        sort_keys: If ``True``, dictionary keys are sorted. Defaults to
            ``False``.
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
        yaml.YAMLError: If ``obj`` is not safe-YAML-serialisable.
        SafeAtomicError: Propagated from ``write_atomic`` on protocol
            failures.
        UnsupportedEnvironmentError: Under ``safety='strict'`` if the
            filesystem does not support the required guarantees.
    """
    text: str = yaml.safe_dump(
        obj,
        default_flow_style=default_flow_style,
        sort_keys=sort_keys,
        allow_unicode=True,
    )
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


def atomic_yaml_load(
    path: str | os.PathLike[str],
    *,
    encoding: str | _Unset = _UNSET,
    check_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> object:
    """Read ``path`` atomically and deserialise the contents as YAML.

    Deserialisation is performed by :func:`yaml.safe_load` (PyYAML). A
    :class:`yaml.YAMLError` from parsing propagates without wrapping.

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
        The deserialised YAML value. Type is ``object`` because YAML
        supports arbitrary top-level values (mapping, sequence, scalar).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        yaml.YAMLError: If the file is not valid YAML.
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
    return yaml.safe_load(text)


# ---------------------------------------------------------------------------
# ruamel.yaml round-trip dump/load (lazy import — optional extra)
# ---------------------------------------------------------------------------

_RUAMEL_INSTALL_HINT = (
    "atomic_yaml_dump_ruamel requires the 'ruamel' extra. "
    "Install with: pip install safeatomic[ruamel]"
)


def atomic_yaml_dump_ruamel(
    path: str | os.PathLike[str],
    data: object,
    *,
    yaml_instance: _RuamelYAML | None = None,
    concurrency: ConcurrencyPolicy = DEFAULT_CONCURRENCY,
    preserve_metadata: bool = True,
    write_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    retries: int | _Unset = _UNSET,
    delay: float | _Unset = _UNSET,
    session: str | None = None,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> None:
    """Serialise ``data`` with ruamel.yaml and write it atomically to ``path``.

    Requires the ``ruamel`` optional extra (``pip install safeatomic[ruamel]``).
    The import is performed lazily inside the function so that this module is
    importable without the extra installed; the error surfaces only when the
    function is called.

    If ``yaml_instance`` is ``None``, a default :class:`ruamel.yaml.YAML`
    instance is created with indentation ``mapping=2, sequence=4, offset=2``
    and ``width=120``.

    Args:
        path: Destination file. The parent directory must exist.
        data: Object to serialise. Must be compatible with the ruamel
            YAML instance (e.g. CommentedMap for round-trip).
        yaml_instance: An existing ruamel.yaml YAML instance satisfying
            :class:`_RuamelYAML` to use for serialisation. If ``None``,
            a default instance is created. Defaults to ``None``.
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
        ImportError: If the ``ruamel.yaml`` package is not installed.
        SafeAtomicError: Propagated from ``write_atomic`` on protocol
            failures.
        UnsupportedEnvironmentError: Under ``safety='strict'`` if the
            filesystem does not support the required guarantees.
    """
    try:
        from ruamel.yaml import YAML  # noqa: PLC0415,I001  # intentional lazy import: ruamel is an optional extra
    except ImportError as err:
        raise ImportError(_RUAMEL_INSTALL_HINT) from err

    y: _RuamelYAML
    if yaml_instance is None:
        y = YAML()
        y.indent(mapping=2, sequence=4, offset=2)
        y.width = 120
    else:
        y = yaml_instance

    buf = io.StringIO()
    y.dump(data, buf)
    text = buf.getvalue()

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


def atomic_yaml_load_ruamel(
    path: str | os.PathLike[str],
    *,
    yaml_instance: _RuamelYAML | None = None,
    encoding: str | _Unset = _UNSET,
    check_checksum: bool = False,
    checksum_algo: str | _Unset = _UNSET,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> object:
    """Read ``path`` atomically and deserialise with ruamel.yaml.

    Requires the ``ruamel`` optional extra. The import is lazy; the error
    surfaces only when the function is called without the extra installed.

    If ``yaml_instance`` is ``None``, a default round-trip instance
    ``YAML(typ="rt")`` is created.

    Args:
        path: Source file to read.
        yaml_instance: An existing ruamel.yaml YAML instance satisfying
            :class:`_RuamelYAML` to use for deserialisation. If ``None``,
            a default round-trip instance is created. Defaults to ``None``.
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
        The deserialised YAML value (typically a
        :class:`ruamel.yaml.comments.CommentedMap` for round-trip).

    Raises:
        ImportError: If the ``ruamel.yaml`` package is not installed.
        FileNotFoundError: If ``path`` does not exist.
        ChecksumMismatchError: If ``check_checksum=True`` and the digest
            does not match the sidecar.
        SafeAtomicError: Propagated from ``read_atomic`` on protocol
            failures.
    """
    try:
        from ruamel.yaml import YAML  # noqa: PLC0415,I001  # intentional lazy import: ruamel is an optional extra
    except ImportError as err:
        raise ImportError(_RUAMEL_INSTALL_HINT) from err

    y: _RuamelYAML = YAML(typ="rt") if yaml_instance is None else yaml_instance

    text: str = read_atomic(
        path,
        encoding=encoding,
        check_checksum=check_checksum,
        checksum_algo=checksum_algo,
        safety=safety,
    )
    return y.load(text)
