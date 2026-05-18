"""Cooperative whole-file locking for safeatomic v2.

This module implements the lock primitives that provide
:guilabel:`WriterExclusion` and :guilabel:`StaleRecovery` (the latter
solely through :func:`release_stale_lock`).

Locks are **cooperative**: they exclude only callers that also use
``safeatomic`` lock APIs on the same target. A rogue writer that
ignores the lock can still race; that is by design and is documented
in ``design/guarantees-formalization.md`` §6.

On-disk payload
---------------

A lock sidecar is a UTF-8 JSON document with the following shape::

    {
        "version": 1,
        "pid": 12345,
        "hostname": "host.example.org",
        "session_hash": "<hex sha256 of session string>" | null,
        "timestamp": "2026-01-15T12:34:56.789012+00:00"
    }

The schema version is :data:`safeatomic._constants.LOCK_PAYLOAD_VERSION`.
Future protocol changes increase this. Readers reject payloads with an
unknown version by treating them as corrupt (``LockInfo(corrupt=True)``).

The ``session`` string is never written to disk. Only its SHA-256 digest
is recorded, so a caller can identify their own session without leaking
its content. The library never reverses the digest.

PID liveness
------------

v2.0 uses the POSIX standard ``os.kill(pid, 0)`` to probe whether a
process exists. This **cannot** detect PID reuse: if process A exits
and the kernel later allocates the same PID to process B, the lock
appears live.

PID liveness is therefore consulted only when the lock's recorded
``hostname`` equals the current host. On a different host, the local
kernel cannot answer the question; we report ``alive='unknown'`` and
refuse to declare the lock stale based on PID.

``release_stale_lock(max_age_s=...)`` may declare a lock stale on age
alone. This is an explicit **operator policy**, not a PID-reuse proof.
The docstrings and ``design/failure-model.md`` say so plainly.

A stronger liveness check that compares process start-time
(``/proc/<pid>/stat`` field 22 on Linux, ``kinfo_proc`` on BSD) is
recorded as future work in ``design/adjacencies.md`` for v2.1.

Cross-refs
----------

- design/guarantees-formalization.md §6 (WriterExclusion)
- design/guarantees-formalization.md §7 (StaleRecovery)
- design/failure-model.md (lock sidecar contract)
- design/implementation-discipline.md principles 6 and 7
- design/adjacencies.md (advanced PID liveness, v2.1)
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from safeatomic._constants import (
    DEFAULT_DELAY,
    DEFAULT_RETRIES,
    DEFAULT_SAFETY,
    LOCK_PAYLOAD_VERSION,
    SafetyPolicy,
)
from safeatomic._exceptions import LockError
from safeatomic._logging import logger
from safeatomic._paths import _as_path, lock_path

if TYPE_CHECKING:
    from os import PathLike
    from pathlib import Path


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

LivenessProbe = Literal["yes", "no", "unknown"]
"""Result of a PID-liveness probe.

- ``"yes"``: a process with that PID exists *on this host* right now.
  PID reuse is not detected; see module docstring.
- ``"no"``: no process with that PID exists on this host.
- ``"unknown"``: the question cannot be answered (e.g. the lock was
  taken on a different host, or the probe itself raised an
  unexpected error).
"""


# ---------------------------------------------------------------------------
# LockInfo dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LockInfo:
    """Snapshot of a lock sidecar's state.

    :func:`inspect_lock` always returns a :class:`LockInfo`, never
    ``None`` and never raising for a missing or corrupt lockfile.
    Callers branch on :attr:`exists` and :attr:`corrupt`.

    Attributes:
        path: The *locked* file (the target), not the lock sidecar.
        lock_path: The lock sidecar path itself
            (``target.name + ".lock"``).
        exists: Whether the lock sidecar exists at the moment of
            inspection. If ``False``, all other fields except
            :attr:`path` and :attr:`lock_path` are ``None`` /
            defaults.
        pid: PID recorded in the payload, or ``None`` if missing or
            unparseable.
        hostname: Hostname recorded in the payload, or ``None``.
        session_hash: Hex digest of the holder's session string, or
            ``None`` if the holder did not supply a session. Never the
            raw session value.
        timestamp: tz-aware UTC datetime when the lock was acquired, or
            ``None`` if missing or unparseable.
        alive: PID-liveness probe result. ``None`` when no probe was
            run (the lock does not exist or is corrupt). When set,
            see :data:`LivenessProbe`. ``"unknown"`` is also returned
            when :attr:`hostname` differs from the current host.
        corrupt: ``True`` if the sidecar exists but its payload could
            not be parsed as a well-formed safeatomic lock document.
        raw: Raw payload bytes (decoded as UTF-8 with errors replaced),
            populated only when :attr:`corrupt` is ``True``. Useful for
            human inspection; never parsed.
    """

    path: Path
    lock_path: Path
    exists: bool = False
    pid: int | None = None
    hostname: str | None = None
    session_hash: str | None = None
    timestamp: datetime | None = None
    alive: LivenessProbe | None = None
    corrupt: bool = False
    raw: str | None = field(default=None, repr=False)

    @property
    def age_s(self) -> float | None:
        """Age of the lock in seconds, or ``None`` if not computable.

        Returns ``None`` when the lock does not exist or has no parseable
        timestamp. The age is computed against the current wall clock;
        callers running on hosts with clock skew relative to the lock's
        host should treat this as approximate.
        """
        if self.timestamp is None:
            return None
        now = datetime.now(tz=UTC)
        return (now - self.timestamp).total_seconds()

    def __str__(self) -> str:
        """Compact one-line summary suitable for logs and CLI output.

        The format is intentionally human-readable. It is **not** a
        stable parse target; use the attributes for programmatic access.
        """
        if not self.exists:
            return f"LockInfo({self.lock_path}: absent)"
        if self.corrupt:
            return f"LockInfo({self.lock_path}: corrupt)"
        age = self.age_s
        age_str = f"{age:.1f}s" if age is not None else "?"
        return (
            f"LockInfo({self.lock_path}: "
            f"pid={self.pid} host={self.hostname} "
            f"alive={self.alive} age={age_str})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _current_hostname() -> str:
    """Return the current host's name.

    Uses :func:`socket.gethostname`. We do not attempt FQDN resolution;
    locks taken with a short hostname on one host must compare equal to
    inspections from the same host. Network identity is not the purpose.
    """
    return socket.gethostname()


def _session_to_hash(session: str | None) -> str | None:
    """Return the hex SHA-256 digest of ``session``, or ``None``.

    The raw session string is never written to disk and never returned.
    Callers that supply ``None`` (the default) get ``None`` back.
    """
    if session is None:
        return None
    return hashlib.sha256(session.encode("utf-8")).hexdigest()


def _pid_alive_locally(pid: int) -> LivenessProbe:
    """Probe whether ``pid`` corresponds to a running process on this host.

    Uses ``os.kill(pid, 0)``. This is the POSIX-standard way to test for
    process existence without sending a real signal.

    Caveats (see module docstring):

    - PID reuse is not detected.
    - Only valid on the local kernel. Callers must guard with a
      hostname check.

    Args:
        pid: Candidate PID. Must be non-negative.

    Returns:
        - ``"yes"`` if signal 0 succeeds or fails with
          :data:`errno.EPERM` (process exists but is not ours).
        - ``"no"`` if signal 0 raises :class:`ProcessLookupError`
          (a.k.a. :data:`errno.ESRCH`).
        - ``"unknown"`` for any other :class:`OSError`, or if the PID is
          obviously invalid (zero or negative).
    """
    if pid <= 0:
        return "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "no"
    except PermissionError:
        # Process exists but is owned by another user (EPERM). For the
        # purposes of lock liveness this is "yes" — somebody is there.
        return "yes"
    except OSError as exc:
        # Unexpected: log and treat as unknown rather than crash.
        logger.warning(
            "pid_alive probe failed for pid=%d errno=%s: %s",
            pid,
            errno.errorcode.get(exc.errno or 0, "?"),
            exc,
        )
        return "unknown"
    return "yes"


def _build_payload(
    pid: int,
    hostname: str,
    session_hash: str | None,
    timestamp: datetime,
) -> bytes:
    """Serialise a lock payload to deterministic UTF-8 JSON bytes.

    Keys are written in a fixed order to keep the on-disk representation
    stable for any future diff/audit tooling. The trailing newline is
    intentional and matches the convention used by configuration tools.
    """
    doc = {
        "version": LOCK_PAYLOAD_VERSION,
        "pid": pid,
        "hostname": hostname,
        "session_hash": session_hash,
        "timestamp": timestamp.isoformat(),
    }
    return (json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _parse_timestamp(value: object) -> datetime | None:
    """Parse a timestamp field into a tz-aware UTC datetime, or ``None``.

    Accepts only ISO 8601 strings. Naive datetimes (no tzinfo) are
    rejected because the payload contract requires UTC.
    """
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def _parse_lock_payload(raw_bytes: bytes) -> dict[str, object] | None:
    """Parse and validate a lock payload.

    Returns the decoded dict on success, or ``None`` if the payload is
    not a well-formed safeatomic lock document of a known version. This
    function does not raise.
    """
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        doc = json.loads(decoded)
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, dict):
        return None
    version = doc.get("version")
    if version != LOCK_PAYLOAD_VERSION:
        return None
    # Required keys must at least be present. Their types are validated
    # by the caller (in inspect_lock) so we can surface partial info.
    for key in ("pid", "hostname", "timestamp"):
        if key not in doc:
            return None
    return doc


def _decode_raw(raw_bytes: bytes) -> str:
    """Decode raw lock bytes for the ``raw`` field of a corrupt info.

    Errors are replaced rather than raised so callers can always see
    *something* in logs without crashing the inspection path.
    """
    return raw_bytes.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Safety policy
# ---------------------------------------------------------------------------
#
# Locks themselves do not require special environment capabilities beyond
# what every supported filesystem provides (atomic O_CREAT|O_EXCL on the
# same directory). We therefore do not gate the lock APIs on
# `inspect_guarantees`. The `safety` kwarg is accepted on
# `try_acquire_lock` for API uniformity and forward-compatibility; under
# the current implementation it has no effect. Future protocol changes
# (e.g. shared locks on network filesystems) may begin to consult it.
#
# Per design/implementation-discipline.md principle 13, we accept the
# kwarg today (so callers can write safety-uniform code) but document
# that it is a no-op at the lock layer in v2.0.


def _validate_safety(safety: SafetyPolicy) -> None:
    """Validate ``safety`` against the public literal alphabet.

    Raises :class:`ValueError` for unknown policies. This is a defensive
    check for callers who pass through ``str`` from untyped sources.
    """
    if safety not in ("strict", "warn", "best_effort"):
        msg = f"invalid safety policy: {safety!r}; expected one of 'strict', 'warn', 'best_effort'"
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def try_acquire_lock(
    path: str | PathLike[str],
    *,
    session: str | None = None,
    retries: int = DEFAULT_RETRIES,
    delay: float = DEFAULT_DELAY,
    safety: SafetyPolicy = DEFAULT_SAFETY,
) -> bool:
    """Attempt to acquire the cooperative lock for ``path``.

    Creates a lock sidecar at ``lock_path(path)`` using
    ``O_CREAT | O_EXCL | O_WRONLY``, which is the POSIX-atomic
    "create-if-absent" primitive. On success the function returns
    ``True``. On contention (the sidecar already exists) it retries up
    to ``retries`` times with ``delay`` seconds between attempts and
    returns ``False`` if all attempts fail.

    Args:
        path: The file to lock. The lock sidecar is a sibling of this
            path, not the path itself.
        session: Optional caller-supplied session identifier. Only its
            SHA-256 digest is stored; the raw value never reaches disk.
            Useful when one process holds multiple locks and wants to
            distinguish them at inspection time.
        retries: Number of additional attempts after the first failed
            acquisition. ``0`` (default) means try once and give up.
        delay: Seconds to sleep between attempts. Must be non-negative.
        safety: Safety policy. Accepted for API uniformity; has no
            effect at the lock layer in v2.0. Validated for early
            rejection of obviously wrong values.

    Returns:
        ``True`` if the lock was acquired; ``False`` if it is held by
        someone else after all attempts.

    Raises:
        LockError: For structural failures (parent directory missing,
            permission denied creating the sidecar, I/O error writing
            the payload). Ordinary contention is *not* a LockError; it
            returns ``False``.
        ValueError: If ``retries`` is negative, ``delay`` is negative,
            or ``safety`` is not a recognised policy.

    Notes:
        - This function does NOT block indefinitely. There is no
          ``timeout`` kwarg in v2.0; that surface is reserved for v2.1
          via :class:`LockTimeoutError`.
        - This function does NOT detect stale locks. Use
          :func:`is_stale_lock` and :func:`release_stale_lock` for
          recovery.
        - There is no ``force`` kwarg. Callers that genuinely need to
          break a lock must call :func:`force_release_lock` explicitly,
          which makes the override visible in code review.
    """
    _validate_safety(safety)
    if retries < 0:
        msg = f"retries must be non-negative, got {retries}"
        raise ValueError(msg)
    if delay < 0:
        msg = f"delay must be non-negative, got {delay}"
        raise ValueError(msg)

    target = _as_path(path)
    lf = lock_path(target)

    pid = os.getpid()
    host = _current_hostname()
    session_hash = _session_to_hash(session)

    attempts = retries + 1
    for attempt in range(attempts):
        timestamp = datetime.now(tz=UTC)
        payload = _build_payload(pid, host, session_hash, timestamp)
        try:
            fd = os.open(
                lf,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            # Ordinary contention: another holder beat us to it.
            if attempt < attempts - 1 and delay > 0:
                time.sleep(delay)
            continue
        except FileNotFoundError as exc:
            # Parent directory missing. This is a structural failure;
            # do not silently treat it as contention.
            msg = f"cannot create lock at {lf}: parent directory does not exist"
            raise LockError(msg) from exc
        except PermissionError as exc:
            msg = f"permission denied creating lock at {lf}"
            raise LockError(msg) from exc
        except OSError as exc:
            msg = f"failed to create lock at {lf}: {exc}"
            raise LockError(msg) from exc

        try:
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        except OSError as exc:
            # We created the sidecar but failed to write/close. Clean up
            # so we don't leave a zero-byte lock that looks corrupt to
            # every other process forever.
            with contextlib.suppress(OSError):
                lf.unlink()
            msg = f"failed to write lock payload at {lf}: {exc}"
            raise LockError(msg) from exc
        return True

    return False


def release_lock(path: str | PathLike[str]) -> bool:
    """Release the lock for ``path`` if held by the current PID and host.

    Idempotent: returns ``False`` (not an error) when the lock does not
    exist, is corrupt, or is held by someone else. Use
    :func:`force_release_lock` for administrative override.

    Args:
        path: The locked file (not the lock sidecar).

    Returns:
        ``True`` if a lock sidecar belonging to the current PID and
        hostname was removed; ``False`` otherwise.

    Raises:
        LockError: Only for structural I/O failures during unlink
            (rare). Ordinary "not yours" cases return ``False``.
    """
    info = inspect_lock(path)
    if not info.exists or info.corrupt:
        return False
    if info.pid != os.getpid():
        return False
    if info.hostname != _current_hostname():
        return False
    try:
        info.lock_path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        msg = f"failed to release lock at {info.lock_path}: {exc}"
        raise LockError(msg) from exc
    return True


def force_release_lock(path: str | PathLike[str]) -> bool:
    """Unconditionally remove the lock sidecar for ``path``.

    This is an **administrative override**, not stale recovery. It does
    not check PID, hostname, age, payload validity, or session. Use it
    only in operator/CLI contexts where the human has decided the lock
    must go. Application code should prefer :func:`release_stale_lock`
    with an explicit ``max_age_s`` policy.

    Args:
        path: The locked file (not the lock sidecar).

    Returns:
        ``True`` if a sidecar was removed; ``False`` if none existed.

    Raises:
        LockError: For structural I/O failures during unlink.
    """
    lf = lock_path(path)
    try:
        lf.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        msg = f"failed to force-release lock at {lf}: {exc}"
        raise LockError(msg) from exc
    return True


def is_locked(path: str | PathLike[str]) -> bool:
    """Return ``True`` if a lock sidecar currently exists for ``path``.

    This is a pure existence check. It does **not** probe liveness,
    validate the payload, or determine staleness. Use :func:`inspect_lock`
    when you need to distinguish "held by a live process" from "stale".

    Args:
        path: The locked file (not the lock sidecar).
    """
    return lock_path(path).exists()


def inspect_lock(path: str | PathLike[str]) -> LockInfo:
    """Return a :class:`LockInfo` snapshot for ``path``.

    Always returns a :class:`LockInfo`. Never raises for missing or
    corrupt lockfiles:

    - If the sidecar does not exist, returns
      ``LockInfo(exists=False)``.
    - If the sidecar exists but its payload cannot be parsed as a
      known-version safeatomic lock document, returns
      ``LockInfo(exists=True, corrupt=True, raw=<decoded bytes>)``.
    - On success, all fields are populated and :attr:`LockInfo.alive`
      is the result of a liveness probe (``"unknown"`` when the lock's
      hostname differs from the current host).

    Args:
        path: The locked file (not the lock sidecar).

    Returns:
        :class:`LockInfo` describing the current state.
    """
    target = _as_path(path)
    lf = lock_path(target)

    try:
        raw_bytes = lf.read_bytes()
    except FileNotFoundError:
        return LockInfo(path=target, lock_path=lf, exists=False)
    except PermissionError:
        # We cannot read the lock; treat as corrupt so callers do not
        # mistake it for absent. The raw field stays None because we
        # have no payload to record.
        return LockInfo(
            path=target,
            lock_path=lf,
            exists=True,
            corrupt=True,
            raw=None,
        )
    except OSError:
        return LockInfo(
            path=target,
            lock_path=lf,
            exists=True,
            corrupt=True,
            raw=None,
        )

    doc = _parse_lock_payload(raw_bytes)
    if doc is None:
        return LockInfo(
            path=target,
            lock_path=lf,
            exists=True,
            corrupt=True,
            raw=_decode_raw(raw_bytes),
        )

    # Soft-validate field types. Missing or wrong-typed fields degrade
    # the corresponding LockInfo attribute to None rather than poison
    # the whole record.
    pid_value = doc.get("pid")
    pid = pid_value if isinstance(pid_value, int) and not isinstance(pid_value, bool) else None

    host_value = doc.get("hostname")
    host = host_value if isinstance(host_value, str) else None

    session_hash_value = doc.get("session_hash")
    session_hash = session_hash_value if isinstance(session_hash_value, str) else None

    ts = _parse_timestamp(doc.get("timestamp"))

    alive: LivenessProbe
    if host is None or pid is None or host != _current_hostname():
        alive = "unknown"
    else:
        alive = _pid_alive_locally(pid)

    return LockInfo(
        path=target,
        lock_path=lf,
        exists=True,
        pid=pid,
        hostname=host,
        session_hash=session_hash,
        timestamp=ts,
        alive=alive,
        corrupt=False,
        raw=None,
    )


def get_lock_age(path: str | PathLike[str]) -> float | None:
    """Return the age in seconds of the lock for ``path``, or ``None``.

    Convenience wrapper around :func:`inspect_lock`. Returns ``None``
    when the lock does not exist, is corrupt, or has no parseable
    timestamp. See :attr:`LockInfo.age_s` for caveats about clock skew
    across hosts.

    Args:
        path: The locked file (not the lock sidecar).
    """
    info = inspect_lock(path)
    if not info.exists or info.corrupt:
        return None
    return info.age_s


def is_stale_lock(
    path: str | PathLike[str],
    *,
    max_age_s: float | None = None,
) -> bool:
    """Return ``True`` if the lock for ``path`` is judged stale.

    The judgement uses two criteria, applied in order:

    1. **Same-host liveness.** If the lock's recorded ``hostname``
       equals the current host and its PID is no longer running
       (probed via :func:`os.kill` signal 0), the lock is stale.
    2. **Age policy.** If ``max_age_s`` is supplied and the lock's
       age exceeds it, the lock is stale.

    Both conditions are conservative:

    - Cross-host locks never declare stale by PID. The local kernel
      cannot answer the question.
    - PID liveness cannot detect PID reuse; ``max_age_s`` is an
      operator policy, not a PID-reuse proof.

    Missing or corrupt locks return ``False`` from this function. Use
    :attr:`LockInfo.corrupt` to find them.

    Args:
        path: The locked file (not the lock sidecar).
        max_age_s: Optional maximum age in seconds. Locks older than
            this are declared stale regardless of liveness. ``None``
            disables age-based staleness.

    Returns:
        ``True`` if the lock is stale by one of the criteria above;
        ``False`` otherwise.
    """
    info = inspect_lock(path)
    if not info.exists or info.corrupt:
        return False
    if (
        info.hostname == _current_hostname()
        and info.pid is not None
        and _pid_alive_locally(info.pid) == "no"
    ):
        return True
    return max_age_s is not None and info.age_s is not None and info.age_s > max_age_s


def release_stale_lock(
    path: str | PathLike[str],
    *,
    max_age_s: float | None = None,
) -> bool:
    """Release ``path``'s lock if and only if it is stale.

    This is the sole public surface providing :guilabel:`StaleRecovery`.
    Internally it composes :func:`is_stale_lock` and
    :func:`force_release_lock`.

    Args:
        path: The locked file (not the lock sidecar).
        max_age_s: Forwarded to :func:`is_stale_lock`. Without it,
            staleness can only be declared by same-host PID liveness.

    Returns:
        ``True`` if a stale lock was found and removed. ``False`` if
        the lock is absent, live, on another host without an age
        policy, or younger than ``max_age_s``.

    Raises:
        LockError: For structural I/O failures during unlink.
    """
    if not is_stale_lock(path, max_age_s=max_age_s):
        return False
    return force_release_lock(path)
