"""Public exceptions and warnings for safeatomic v2.

The exception hierarchy is intentionally shallow. All exceptions inherit
from :class:`SafeAtomicError`, which inherits from :class:`Exception` (not
:class:`OSError`). Callers that want to catch operating-system errors
unchanged should not rely on ``except OSError`` catching safeatomic
exceptions; this is by design.

The library deliberately does NOT multi-inherit from :class:`OSError`.
:class:`CrossDeviceAtomicityError`, which wraps an underlying
``OSError(EXDEV)``, exposes the original via ``__cause__`` (set by
``raise ... from err``).

Reserved names ``LockTimeoutError`` and ``StaleLockError`` exist so that
future minor releases can introduce them without breaking ``isinstance``
checks against :class:`LockError`. They are NOT exported in v2.0
(no-dead-symbols policy, see ``adr/0005-public-api-surface.md``).

Cross-refs:
- design/failure-model.md
- design/guarantees-formalization.md §11 (UnsupportedEnvironmentWarning)
- design/implementation-discipline.md principle 5
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  # used at runtime in __init__


class SafeAtomicError(Exception):
    """Base class for all safeatomic errors.

    Inherits from :class:`Exception` directly, not :class:`OSError`. This
    choice is documented in ``design/failure-model.md``: safeatomic errors
    are protocol-level failures, not raw OS errors, and conflating them
    would force callers to disambiguate via message inspection.
    """


class UnsupportedEnvironmentError(SafeAtomicError):
    """Raised when the environment cannot provide the required guarantees.

    Under ``safety='strict'``, operations raise this BEFORE touching disk
    if the detected filesystem class does not support the guarantees the
    operation requires.

    Under ``safety='warn'``, an :class:`UnsupportedEnvironmentWarning` is
    issued instead and the operation proceeds. Under
    ``safety='best_effort'``, neither error nor warning is raised.
    """


class ChecksumMismatchError(SafeAtomicError):
    """Raised by ``read_atomic(check_checksum=True)`` on mismatch.

    The standalone :func:`safeatomic.verify_checksum` returns ``False`` on
    mismatch (a normal outcome to inspect). The read path is different:
    a mismatch there means the read result cannot be trusted, so it
    surfaces as an exception.

    Attributes:
        path: The data file whose checksum did not match.
        expected: The hex digest read from the sidecar.
        actual: The hex digest computed from the data file.
    """

    __slots__ = ("actual", "expected", "path")

    path: Path
    expected: str
    actual: str

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        super().__init__(
            f"checksum mismatch for {path}: expected {expected}, got {actual}"
        )
        self.path = path
        self.expected = expected
        self.actual = actual


class CrossDeviceAtomicityError(SafeAtomicError):
    """Raised by :func:`safeatomic.move_atomic` on EXDEV.

    ``move_atomic`` promises atomicity. Cross-device moves cannot be
    atomic at the filesystem level (they require copy+delete). Rather
    than silently degrade, safeatomic refuses and raises this error so
    the caller can decide whether to use :func:`shutil.move` (non-atomic)
    or restructure the layout to keep source and destination on the same
    device.

    This behaviour is unconditional: ``safety='best_effort'`` does NOT
    change it. The name ``move_atomic`` cannot lie.

    Attributes:
        src: The source path.
        dst: The destination path.

    The underlying :class:`OSError` with ``errno=EXDEV`` is available via
    ``__cause__`` when this error is raised with ``raise ... from err``.
    """

    __slots__ = ("dst", "src")

    src: Path
    dst: Path

    def __init__(self, src: Path, dst: Path) -> None:
        super().__init__(
            f"cross-device atomic move not supported: {src} -> {dst} "
            f"(different filesystems); use shutil.move for a non-atomic copy+delete"
        )
        self.src = src
        self.dst = dst


class LockError(SafeAtomicError):
    """Raised for lock-related failures other than ordinary contention.

    Ordinary contention (the lock is held by another writer) is reported
    by :func:`safeatomic.try_acquire_lock` returning ``False``, not by
    raising. This exception covers structural failures: the lock file
    cannot be written, the parent directory disappeared mid-acquisition,
    a release attempt found a lock owned by someone else, etc.
    """


# Reserved subclasses --------------------------------------------------------
#
# These exist so that future versions may export them without breaking
# isinstance checks. They are NOT in safeatomic.__all__ in v2.0.


class LockTimeoutError(LockError):
    """Reserved for a future ``acquire_lock(timeout=...)`` API.

    NOT exported in v2.0. Documented in
    ``design/adjacencies.md`` as deferred to v2.1.
    """


class StaleLockError(LockError):
    """Reserved for a future API that raises on stale lock encounter.

    NOT exported in v2.0. The current public surface uses
    :func:`safeatomic.release_stale_lock` (returns ``bool``) instead.
    """


class UnsupportedEnvironmentWarning(UserWarning):
    """Warning emitted under ``safety='warn'`` for unsupported environments.

    The library does NOT call :func:`warnings.simplefilter` to control
    the frequency of this warning. That decision belongs to the caller.
    By default, Python's warning system emits each distinct
    (message, category, module, lineno) combination once per process.
    """
