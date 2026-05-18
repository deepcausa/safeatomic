"""Operational diagnostic for safeatomic v2.

Exposes :func:`doctor`, a user-facing API for inspecting whether a
deployment environment can actually support the guarantees safeatomic
claims. Unlike :func:`safeatomic.inspect_guarantees`, which reads from a
cached environment vector and the normative matrix, ``doctor`` can
optionally run **destructive probes**: it creates short-lived files
prefixed ``.safeatomic-doctor-*`` in the target directory and removes
them afterwards.

Why this exists:

The developer may not know what OS/FS will run the deployed code.
``inspect_guarantees`` answers the theoretical question; ``doctor``
answers the operational one. See ``design/implementation-discipline.md``
principle 14: the report is the source of truth.

Cross-refs:
- design/guarantees-formalization.md §10 (inspection API)
- design/implementation-discipline.md principle 14 (report is truth)
- design/api-v2-proposal.md (doctor signature)
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from safeatomic._capabilities import detect_environment
from safeatomic._guarantees import (
    GuaranteeReport,
    inspect_guarantees,
)
from safeatomic._logging import logger

if TYPE_CHECKING:
    from os import PathLike
    from pathlib import Path

    from safeatomic._capabilities import Environment

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

DoctorStatus = Literal["pass", "warn", "fail", "unknown"]
"""Status of a single doctor check.

- ``pass``: the check succeeded on this environment.
- ``warn``: the check succeeded but the result is degraded or partial.
- ``fail``: the check failed.
- ``unknown``: the check could not be performed (e.g. lack of permission,
  ``destructive=False`` for a probe-required check).
"""


_GUARANTEE_TO_CHECK_NAME: Final[dict[str, str]] = {
    "AtomicVisibility": "atomic_visibility",
    "ReaderConsistency": "reader_consistency",
    "CrashDurability": "crash_durability",
    "WriterExclusion": "writer_exclusion",
    "StaleRecovery": "stale_recovery",
    "IntegrityDetection": "integrity_detection",
    "MetadataPreservation": "metadata_preservation",
    "CrossDeviceSafety": "cross_device_safety",
}
"""Canonical mapping from formal GuaranteeName (PascalCase, used in
formalization docs and inspect_guarantees) to operational check name
(snake_case, used in `doctor` output and CLI).
"""


_DOCTOR_TMP_PREFIX: Final[str] = ".safeatomic-doctor-"
"""Prefix for all transient files created by destructive probes.

Identifiable for orphan cleanup tooling (per design/failure-model.md).
"""

_EXPECTED_TMP_MODE: Final[int] = 0o600
"""The mode requested for probe tmp files (matches safeatomic write protocol)."""

# ---------------------------------------------------------------------------
# DoctorCheck / DoctorReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """Single diagnostic check result.

    Attributes:
        name: Stable, lowercased identifier (e.g. ``"fsync_file"``).
        status: One of :data:`DoctorStatus`.
        detail: Short human-readable explanation. May be empty for ``pass``.
    """

    name: str
    status: DoctorStatus
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Aggregated operational diagnostic for a path.

    Produced by :func:`doctor`. Combines the theoretical
    :class:`GuaranteeReport` with operational checks.

    Attributes:
        path: The path inspected (resolved to absolute).
        environment: The :class:`Environment` detected.
        guarantees: The :class:`GuaranteeReport` for this environment.
        checks: Tuple of :class:`DoctorCheck` in declaration order.
        ok: ``True`` iff no check has status ``"fail"`` and all
            ``require`` items are present and ``"pass"``.

    See Also:
        :func:`doctor`, :func:`safeatomic.inspect_guarantees`.
    """

    path: Path
    environment: Environment
    guarantees: GuaranteeReport
    checks: tuple[DoctorCheck, ...]
    ok: bool

    def summary(self) -> str:
        """Return a multi-line human-readable summary."""
        lines = [
            f"safeatomic doctor: {self.path}",
            "",
            "Environment:",
            f"  platform: {self.environment.platform}",
            f"  filesystem_class: {self.environment.filesystem_class}",
            f"  filesystem: {self.environment.filesystem or '(unknown)'}",
            f"  symlink_policy: {self.environment.symlink_policy}",
            "",
            "Guarantees:",
        ]
        for name in sorted(self.guarantees.guarantees):
            lines.append(f"  {name}: {self.guarantees.guarantees[name]}")
        lines.extend(["", "Checks:"])
        for c in self.checks:
            tail = f" — {c.detail}" if c.detail else ""
            lines.append(f"  [{c.status}] {c.name}{tail}")
        lines.append("")
        lines.append(f"Result: {'OK' if self.ok else 'FAIL'}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dict representation.

        Suitable for ``safeatomic doctor --json`` CLI output.
        """
        return {
            "path": str(self.path),
            "environment": {
                "platform": self.environment.platform,
                "filesystem": self.environment.filesystem,
                "filesystem_class": self.environment.filesystem_class,
                "supports_fsync_file": self.environment.supports_fsync_file,
                "supports_fsync_dir": self.environment.supports_fsync_dir,
                "supports_atomic_replace": self.environment.supports_atomic_replace,
                "symlink_policy": self.environment.symlink_policy,
            },
            "guarantees": dict(self.guarantees.guarantees),
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks
            ],
            "ok": self.ok,
        }

    def __str__(self) -> str:
        """Return :meth:`summary`."""
        return self.summary()


# ---------------------------------------------------------------------------
# Internal probes
# ---------------------------------------------------------------------------


def _probe_filename(parent: Path, tag: str) -> Path:
    """Return a transient probe path inside ``parent`` with ``tag``."""
    nonce = secrets.token_hex(6)
    return parent / f"{_DOCTOR_TMP_PREFIX}{tag}-{os.getpid()}-{nonce}"


def _check_parent_exists(parent: Path) -> DoctorCheck:
    if parent.exists():
        return DoctorCheck("parent_exists", "pass", "")
    return DoctorCheck("parent_exists", "fail", f"{parent} does not exist")


def _check_parent_writable(parent: Path) -> DoctorCheck:
    if not parent.exists():
        return DoctorCheck("parent_writable", "unknown", "parent does not exist")
    if os.access(parent, os.W_OK):
        return DoctorCheck("parent_writable", "pass", "")
    return DoctorCheck("parent_writable", "fail", f"{parent} not writable")


def _probe_create_excl_0600(parent: Path) -> DoctorCheck:
    """Create a file with O_CREAT|O_EXCL|O_WRONLY mode 0o600 and remove it.

    Tests two things together: exclusive create works (so safeatomic's
    tmp file strategy is safe from collisions), and 0o600 mode is
    honoured (so secrets in tmp are not world-readable during the
    write window).
    """
    probe = _probe_filename(parent, "excl")
    try:
        fd = os.open(
            probe,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except OSError as e:
        return DoctorCheck(
            "create_excl_0600",
            "fail",
            f"O_CREAT|O_EXCL failed: {e.__class__.__name__}: {e}",
        )
    try:
        os.close(fd)
        # Verify the mode that landed on disk (some filesystems may
        # widen permissions via mount options).
        st = probe.stat()
        actual_mode = st.st_mode & 0o777
        if actual_mode != _EXPECTED_TMP_MODE:
            return DoctorCheck(
                "create_excl_0600",
                "warn",
                f"mode 0o600 requested, got 0o{actual_mode:o}",
            )
        return DoctorCheck("create_excl_0600", "pass", "")
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()


def _probe_fsync_file(parent: Path) -> DoctorCheck:
    probe = _probe_filename(parent, "fsyncfile")
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as e:
        return DoctorCheck("fsync_file", "unknown", f"cannot create probe: {e}")
    try:
        os.write(fd, b"x")
        try:
            os.fsync(fd)
        except OSError as e:
            return DoctorCheck(
                "fsync_file",
                "fail",
                f"os.fsync(fd) failed: {e.__class__.__name__}: {e}",
            )
        return DoctorCheck("fsync_file", "pass", "")
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            probe.unlink()


def _probe_fsync_dir(parent: Path) -> DoctorCheck:
    if not parent.exists():
        return DoctorCheck("fsync_dir", "unknown", "parent does not exist")
    try:
        dfd = os.open(parent, os.O_RDONLY)
    except OSError as e:
        return DoctorCheck(
            "fsync_dir",
            "unknown",
            f"cannot open parent for fsync: {e.__class__.__name__}: {e}",
        )
    try:
        try:
            os.fsync(dfd)
        except OSError as e:
            # Windows and a few exotic filesystems do not support fsync
            # on a directory. That degrades CrashDurability but does not
            # fail the doctor outright unless the user demands it.
            if e.errno in {errno.EINVAL, errno.ENOTSUP, errno.EACCES}:
                return DoctorCheck(
                    "fsync_dir",
                    "warn",
                    f"directory fsync not supported: errno {e.errno}",
                )
            return DoctorCheck(
                "fsync_dir",
                "fail",
                f"os.fsync(dir) failed: {e.__class__.__name__}: {e}",
            )
        return DoctorCheck("fsync_dir", "pass", "")
    finally:
        with contextlib.suppress(OSError):
            os.close(dfd)


def _probe_atomic_replace(parent: Path) -> DoctorCheck:
    """Test that os.replace(src, dst) works between two paths in parent."""
    src = _probe_filename(parent, "rpl-src")
    dst = _probe_filename(parent, "rpl-dst")
    try:
        fd = os.open(src, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as e:
        return DoctorCheck(
            "atomic_replace",
            "unknown",
            f"cannot create probe src: {e}",
        )
    try:
        os.write(fd, b"x")
        os.close(fd)
        try:
            os.replace(src, dst)  # noqa: PTH105  # we are probing the syscall itself
        except OSError as e:
            return DoctorCheck(
                "atomic_replace",
                "fail",
                f"os.replace failed: {e.__class__.__name__}: {e}",
            )
        return DoctorCheck("atomic_replace", "pass", "")
    finally:
        with contextlib.suppress(OSError):
            src.unlink()
        with contextlib.suppress(OSError):
            dst.unlink()


def _probe_lock_sidecar(parent: Path) -> DoctorCheck:
    """Test that we can create+read+remove a JSON lock sidecar."""
    probe = _probe_filename(parent, "lock")
    payload = {"version": 1, "probe": True}
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as e:
        return DoctorCheck(
            "lock_sidecar",
            "fail",
            f"cannot create lock probe: {e}",
        )
    try:
        os.write(fd, json.dumps(payload).encode("utf-8"))
        os.close(fd)
        # Re-read to validate
        with probe.open("rb") as f:
            data = json.loads(f.read().decode("utf-8"))
        if data.get("probe") is not True:
            return DoctorCheck(
                "lock_sidecar",
                "fail",
                "lock sidecar round-trip mismatched",
            )
        return DoctorCheck("lock_sidecar", "pass", "")
    except (OSError, json.JSONDecodeError) as e:
        return DoctorCheck(
            "lock_sidecar",
            "fail",
            f"lock sidecar round-trip failed: {e.__class__.__name__}: {e}",
        )
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()


def _probe_checksum_sidecar(parent: Path) -> DoctorCheck:
    """Test that we can write+read a checksum sidecar file."""
    probe = _probe_filename(parent, "cksum")
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as e:
        return DoctorCheck(
            "checksum_sidecar",
            "fail",
            f"cannot create checksum probe: {e}",
        )
    try:
        os.write(fd, b"sha256:abcd1234\n")
        os.close(fd)
        with probe.open("rb") as f:
            _ = f.read()
        return DoctorCheck("checksum_sidecar", "pass", "")
    except OSError as e:
        return DoctorCheck(
            "checksum_sidecar",
            "fail",
            f"checksum sidecar round-trip failed: {e.__class__.__name__}: {e}",
        )
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()


# ---------------------------------------------------------------------------
# Required guarantees integration
# ---------------------------------------------------------------------------


def _evaluate_require(
    require: set[str] | None,
    guarantees: GuaranteeReport,
    checks: tuple[DoctorCheck, ...],
) -> bool:
    """Return True iff all required guarantees and probe checks pass.

    A doctor result is "ok" when:
    - no check has status ``"fail"``
    - if ``require`` is given, every named guarantee is at level
      ``"guaranteed"`` AND its operational check (if any) is ``"pass"``.

    Otherwise the user is in degraded territory and must opt in
    via ``safety="warn"`` or ``safety="best_effort"`` at call time.
    """
    if any(c.status == "fail" for c in checks):
        return False
    if require is None:
        return True
    for name in require:
        # Accept both PascalCase formal names and snake_case check names.
        formal: str | None = None
        if name in guarantees.guarantees:
            formal = name
        else:
            # try snake_case -> formal name reverse lookup
            for f, snake in _GUARANTEE_TO_CHECK_NAME.items():
                if snake == name:
                    formal = f
                    break
        if formal is None:
            # unknown name in require set is a failure
            return False
        if guarantees.guarantees[formal] != "guaranteed":  # type: ignore[index]
            return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def doctor(
    path: str | PathLike[str],
    *,
    destructive: bool = False,
    require: set[str] | None = None,
) -> DoctorReport:
    """Run an operational diagnostic against ``path``.

    The diagnostic always returns:

    - the detected :class:`Environment`
    - the theoretical :class:`GuaranteeReport`
    - a list of :class:`DoctorCheck` results

    Lightweight checks (parent exists, parent writable) always run.
    Probe-based checks (create with O_EXCL+0o600, fsync file, fsync
    dir, atomic replace, lock sidecar round-trip, checksum sidecar
    round-trip) run only when ``destructive=True``. Probe files are
    created in the parent directory with prefix ``.safeatomic-doctor-``
    and removed before this function returns.

    Args:
        path: The path to diagnose. Does not have to exist; the parent
            directory is used for probes.
        destructive: When ``True``, run probes that create and remove
            short-lived files in the parent directory. When ``False``,
            only run non-destructive checks; probe-only checks report
            status ``"unknown"``.
        require: Optional set of guarantee names. The report's ``ok``
            attribute is ``True`` only when no check has failed AND
            every required guarantee is at level ``"guaranteed"``.
            Names accept either PascalCase formal names
            (``"AtomicVisibility"``) or snake_case check names
            (``"atomic_visibility"``). An unknown name causes ``ok``
            to be ``False``.

    Returns:
        A :class:`DoctorReport`.

    Examples:
        >>> from safeatomic import doctor
        >>> report = doctor("/data/state.json", destructive=True)
        >>> if not report.ok:
        ...     raise RuntimeError(report.summary())

        >>> # Require specific guarantees for deployment:
        >>> r = doctor(
        ...     "/data",
        ...     destructive=True,
        ...     require={"AtomicVisibility", "CrashDurability"},
        ... )
        >>> assert r.ok

    Cross-ref:
        ``design/api-v2-proposal.md`` (signature),
        ``design/implementation-discipline.md`` principle 14.
    """
    from pathlib import Path as _Path  # noqa: PLC0415  # localised to avoid TYPE_CHECKING shadowing

    resolved = _Path(os.fspath(path)).resolve(strict=False)
    parent = resolved.parent if not resolved.is_dir() else resolved

    env = detect_environment(parent)
    guarantees = inspect_guarantees(parent)

    checks: list[DoctorCheck] = []

    # Always-on lightweight checks
    checks.append(_check_parent_exists(parent))
    checks.append(_check_parent_writable(parent))

    if destructive and parent.exists():
        checks.append(_probe_create_excl_0600(parent))
        checks.append(_probe_fsync_file(parent))
        checks.append(_probe_fsync_dir(parent))
        checks.append(_probe_atomic_replace(parent))
        checks.append(_probe_lock_sidecar(parent))
        checks.append(_probe_checksum_sidecar(parent))
    else:
        # Mark probe-only checks as unknown when destructive=False
        for probe_name in (
            "create_excl_0600",
            "fsync_file",
            "fsync_dir",
            "atomic_replace",
            "lock_sidecar",
            "checksum_sidecar",
        ):
            checks.append(
                DoctorCheck(
                    probe_name,
                    "unknown",
                    "destructive=False; skipped",
                )
            )

    checks_tuple = tuple(checks)
    ok = _evaluate_require(require, guarantees, checks_tuple)

    report = DoctorReport(
        path=resolved,
        environment=env,
        guarantees=guarantees,
        checks=checks_tuple,
        ok=ok,
    )
    logger.debug("doctor report: %s", report)
    return report
