"""Sidecar path derivation for safeatomic v2.

This module is purely functional: it derives lock, tmp, and checksum sidecar
paths from a target path. It performs no I/O.

The naming convention is part of the on-disk protocol. Changing it is a
breaking change. See ``design/failure-model.md`` (sidecar contract) and
``design/implementation-discipline.md`` principle 6 (sidecars are part of
the protocol).

Path conventions:

Given a target ``/some/dir/file.json``:

- lock sidecar:    ``/some/dir/file.json.lock``
- checksum sidecar: ``/some/dir/file.json.sha256``
- in-flight tmp:    ``/some/dir/.safeatomic-tmp-<random>.tmp``

Lock and checksum sidecars are siblings of the target so that the parent
directory's permissions govern access to both target and sidecars
uniformly. Tmp files use a distinctive prefix so external orphan-cleanup
tooling can identify and reap them without false positives.

This module is NOT part of the public API. Path-helper symbols (e.g.
``get_lock_path``) were deliberately removed from the v2 public surface;
see ``adr/0005-public-api-surface.md``.

Cross-refs:
- design/api-v2-proposal.md (conventions)
- design/failure-model.md (sidecar contract)
- design/implementation-discipline.md principle 6
- adr/0005-public-api-surface.md
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from safeatomic._constants import (
    CHECKSUM_SUFFIX,
    LOCK_SUFFIX,
    TMP_PREFIX,
    TMP_SUFFIX,
)

if TYPE_CHECKING:
    from os import PathLike


# ---------------------------------------------------------------------------
# Tmp name randomness
# ---------------------------------------------------------------------------
#
# 16 hex chars = 64 bits of entropy from secrets.token_hex(8). Collision
# probability for distinct in-flight tmp files within the same directory is
# negligible for any realistic workload. We do not rely on the tmp name for
# security; we rely on it for orphan identification and intra-process
# uniqueness against concurrent writers in the same directory.

_TMP_TOKEN_BYTES = 8


def _as_path(target: str | PathLike[str]) -> Path:
    """Coerce a target argument to :class:`pathlib.Path`.

    The public API accepts ``str | os.PathLike[str]``. Internally we work
    only with :class:`Path`. This helper centralises the coercion so the
    contract is uniform.
    """
    return target if isinstance(target, Path) else Path(target)


# ---------------------------------------------------------------------------
# Public-ish derivations (used by other internal modules)
# ---------------------------------------------------------------------------


def lock_path(target: str | PathLike[str]) -> Path:
    """Return the lock sidecar path for ``target``.

    The lock file is a sibling of ``target`` obtained by appending
    :data:`safeatomic._constants.LOCK_SUFFIX` (``.lock``) to the target's
    name. The path is returned regardless of whether the target or the
    lock file exists; this function performs no I/O.

    Args:
        target: Target file path. May or may not exist on disk.

    Returns:
        Path of the lock sidecar.

    Examples:
        >>> from safeatomic._paths import lock_path
        >>> str(lock_path("/data/state.json"))
        '/data/state.json.lock'
    """
    p = _as_path(target)
    return p.with_name(p.name + LOCK_SUFFIX)


def checksum_path(target: str | PathLike[str]) -> Path:
    """Return the checksum sidecar path for ``target``.

    The checksum sidecar is a sibling of ``target`` obtained by appending
    :data:`safeatomic._constants.CHECKSUM_SUFFIX` (``.sha256``). The
    suffix is fixed even when a non-default algorithm is used; the
    algorithm is encoded inside the sidecar payload itself. See
    ``design/failure-model.md`` (checksum sidecar contract).

    Args:
        target: Target file path. May or may not exist on disk.

    Returns:
        Path of the checksum sidecar.

    Examples:
        >>> from safeatomic._paths import checksum_path
        >>> str(checksum_path("/data/state.json"))
        '/data/state.json.sha256'
    """
    p = _as_path(target)
    return p.with_name(p.name + CHECKSUM_SUFFIX)


def tmp_path_for(target: str | PathLike[str]) -> Path:
    """Return a fresh in-flight tmp path for ``target``.

    The tmp file is placed in ``target``'s parent directory (so that the
    final :func:`os.replace` is a same-directory rename, which is the
    operation POSIX defines as atomic). The name is::

        <parent>/<TMP_PREFIX><random-hex><TMP_SUFFIX>

    where ``TMP_PREFIX`` is ``.safeatomic-tmp-`` and ``TMP_SUFFIX`` is
    ``.tmp``.

    The random component uses :func:`secrets.token_hex` (64 bits of
    entropy) so concurrent writers in the same directory do not collide.
    Each call returns a new path; this function is **not** idempotent.

    The function performs no I/O. The caller is responsible for opening
    the file with ``O_CREAT | O_EXCL`` (or equivalent) to detect the
    rare collision.

    The tmp name intentionally does not encode the target's basename.
    This avoids accidentally exposing the target name through cleanup
    tooling and keeps tmp names of uniform shape regardless of target.

    Args:
        target: Target file path. The parent directory must exist when
            the tmp file is actually created; this function itself does
            not check.

    Returns:
        Fresh tmp path in ``target``'s parent directory.
    """
    p = _as_path(target)
    token = secrets.token_hex(_TMP_TOKEN_BYTES)
    return p.parent / f"{TMP_PREFIX}{token}{TMP_SUFFIX}"


def is_tmp_name(name: str) -> bool:
    """Return True if ``name`` matches the safeatomic tmp-file convention.

    Useful for orphan-cleanup tooling and tests. The check is purely
    lexical on the file's basename; no path resolution is performed.

    Args:
        name: File basename (not a full path). Callers that have a full
            path should pass ``Path(p).name``.

    Returns:
        True iff ``name`` starts with :data:`TMP_PREFIX` and ends with
        :data:`TMP_SUFFIX`.

    Examples:
        >>> from safeatomic._paths import is_tmp_name
        >>> is_tmp_name(".safeatomic-tmp-abc123.tmp")
        True
        >>> is_tmp_name("state.json")
        False
    """
    return name.startswith(TMP_PREFIX) and name.endswith(TMP_SUFFIX)
