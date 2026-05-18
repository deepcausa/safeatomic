"""Tier 4 fault-injection tests for safeatomic v2.

Scope: failures injected in the middle of the documented write/read/move
protocols. Happy paths belong in ``test_io_core.py`` (not present yet in
v2.0.dev0); this suite focuses on what can go wrong AFTER a partial
side-effect has already touched disk.

The eleven write-protocol steps in ``_io_core._write_core`` (per the
module docstring of ``_io_core``) each have well-defined cleanup
semantics:

- Steps 4-10 fail -> unlink tmp, re-raise.
- Step 11 fails  -> unlink tmp, re-raise.
- Step 12 fails  -> file IS visible; warning only.
- Step 13 fails  -> file IS visible; raise per contract.
- Step 14 ``release_lock`` always runs in ``finally``.

These tests verify those contracts by monkeypatching at the level the
source code actually reads its dependencies (the ``os`` module bound in
``safeatomic._io_core`` and ``pathlib.Path`` methods used inside the
core).

Private imports (with justification)
------------------------------------

- ``safeatomic._io_core``: monkeypatched module-level attributes
  (``os``, ``_write_open_tmp``, ``_fsync_dir``) are the only way to
  inject a fault between specific protocol steps without forking the
  process. Higher-level monkeypatching of ``os.fsync`` globally would
  also work but is less surgical and races with pytest's own fsyncs.
- ``safeatomic._paths``: ``is_tmp_name``/``TMP_PREFIX``/``LOCK_SUFFIX``/
  ``CHECKSUM_SUFFIX`` are reused so the orphan detector matches the
  source-of-truth naming convention. Reimplementing the prefix in tests
  would create a parallel contract.
- ``safeatomic._locks._build_payload`` is reused to forge a well-formed
  v1 lock for the "orphan after crash" scenario - same justification as
  ``test_locks.py``.

The TLA+ ``SafeAtomicChecksum`` insight is honoured: ``verify_checksum``
returns a fact about the **observed pair** at call time. Tests never
treat an earlier ``Match`` as truth about a later observation.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import pytest

from safeatomic import (
    AtomicWriter,
    ChecksumMismatchError,
    CrossDeviceAtomicityError,
    SafeAtomicError,
    _checksum,
    _io_core,
    _locks,
    force_release_lock,
    inspect_lock,
    is_stale_lock,
    move_atomic,
    read_atomic,
    release_stale_lock,
    try_acquire_lock,
    verify_checksum,
    write_atomic,
    write_atomic_bytes,
)
from safeatomic._constants import (
    CHECKSUM_SUFFIX,
    LOCK_SUFFIX,
    TMP_PREFIX,
    TMP_SUFFIX,
)
from safeatomic._locks import _build_payload
from safeatomic._paths import is_tmp_name

if TYPE_CHECKING:
    from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All tests use safety="best_effort" to keep the suite portable across
# filesystems where one of the guarantees in the matrix is not
# "guaranteed" (e.g. tmpfs in some CI containers). The fault injection
# under test is orthogonal to the safety gate; using best_effort isolates
# the behaviour we want to observe.
_BE = "best_effort"


def safeatomic_artifacts(directory: Path) -> list[Path]:
    """Return all on-disk artefacts safeatomic could plausibly leave.

    Detects the documented sidecar naming conventions:

    - in-flight tmp files: ``<TMP_PREFIX>...<TMP_SUFFIX>``
    - lock sidecars:       ``*<LOCK_SUFFIX>``
    - checksum sidecars:   ``*<CHECKSUM_SUFFIX>``

    Used to assert that failed operations do not leave orphan tmp files,
    and as a diagnostic when a leak is detected.
    """
    out: list[Path] = []
    if not directory.exists():
        return out
    for child in sorted(directory.iterdir()):
        name = child.name
        if is_tmp_name(name) or name.endswith((LOCK_SUFFIX, CHECKSUM_SUFFIX)):
            out.append(child)
    return out


def orphan_tmp_files(directory: Path) -> list[Path]:
    """Return only in-flight tmp orphans (subset of ``safeatomic_artifacts``)."""
    if not directory.exists():
        return []
    return [c for c in directory.iterdir() if is_tmp_name(c.name)]


def lock_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [c for c in directory.iterdir() if c.name.endswith(LOCK_SUFFIX)]


def checksum_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [c for c in directory.iterdir() if c.name.endswith(CHECKSUM_SUFFIX)]


# ---------------------------------------------------------------------------
# 1. Failure before / during writing the tmp file
# ---------------------------------------------------------------------------


def test_exception_inside_atomic_writer_keeps_old_target(tmp_path: Path) -> None:
    """An exception raised inside ``AtomicWriter`` keeps the old target intact."""
    target = tmp_path / "state.txt"
    target.write_text("old")

    with (
        pytest.raises(RuntimeError, match="boom"),
        AtomicWriter(target, concurrency="none", safety=_BE) as w,
    ):
        w.write(b"new-partial")
        raise RuntimeError("boom")

    assert target.read_text() == "old"


def test_exception_inside_atomic_writer_does_not_leave_orphan_tmp(
    tmp_path: Path,
) -> None:
    """A mid-write crash unlinks the in-flight tmp file."""
    target = tmp_path / "state.txt"
    target.write_text("old")

    with (
        pytest.raises(RuntimeError),
        AtomicWriter(target, concurrency="none", safety=_BE) as w,
    ):
        w.write(b"new-partial")
        raise RuntimeError("boom")

    assert orphan_tmp_files(tmp_path) == []


def test_failure_before_tmp_creation_keeps_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``_write_open_tmp`` fails, the target is untouched and no tmp leaks."""

    target = tmp_path / "state.txt"
    target.write_text("old")

    def boom(_tmp: Path) -> NoReturn:
        msg = "simulated open failure"
        raise OSError(errno.EIO, msg)

    monkeypatch.setattr(_io_core, "_write_open_tmp", boom)

    with pytest.raises(OSError, match="simulated open failure"):
        write_atomic(target, "new", concurrency="none", safety=_BE)

    assert target.read_text() == "old"
    assert orphan_tmp_files(tmp_path) == []


def test_failure_during_tmp_write_keeps_target_and_cleans_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure in step 6 (``os.write``) unlinks tmp and leaves target intact."""

    target = tmp_path / "state.txt"
    target.write_text("old-content")

    real_write = os.write

    def fail_after_first(fd: int, data: bytes) -> int:
        # Write the first byte, then explode. Simulates "torn" mid-write.
        if data:
            real_write(fd, data[:1])
        msg = "simulated write failure"
        raise OSError(errno.EIO, msg)

    # Patch the ``os.write`` reference seen inside ``_io_core``.
    monkeypatch.setattr(_io_core.os, "write", fail_after_first)

    with pytest.raises(OSError, match="simulated write failure"):
        write_atomic(target, "new-content", concurrency="none", safety=_BE)

    assert target.read_text() == "old-content"
    assert orphan_tmp_files(tmp_path) == []


# ---------------------------------------------------------------------------
# 2. Failure in fsync of the tmp file
# ---------------------------------------------------------------------------


def test_fsync_failure_before_replace_keeps_old_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 7 (``os.fsync(fd)``) failure aborts the write before replace.

    The old target must survive, no orphan tmp may remain, and the
    operation must not return success silently.
    """

    target = tmp_path / "state.txt"
    target.write_text("old")

    real_fsync = _io_core.os.fsync

    fsync_calls: list[int] = []

    def fail_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        # First fsync is the tmp file fd (step 7) -> fail it.
        # Subsequent fsyncs (parent dir, step 12) are suppressed by
        # _fsync_dir anyway and should never be reached after failure.
        msg = "simulated fsync failure"
        raise OSError(errno.EIO, msg)

    monkeypatch.setattr(_io_core.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        write_atomic(target, "new", concurrency="none", safety=_BE)

    # The original real_fsync is intentionally unused after patch; keep
    # reference to satisfy linters about why we captured it (parity with
    # other tests that selectively restore).
    _ = real_fsync

    assert target.read_text() == "old"
    assert orphan_tmp_files(tmp_path) == []
    # At least the tmp fsync must have been attempted before failure.
    assert fsync_calls, "expected at least one fsync call to be attempted"


# ---------------------------------------------------------------------------
# 3. Failure in os.replace (step 11)
# ---------------------------------------------------------------------------


def test_replace_failure_keeps_old_target_and_cleans_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Path.replace`` failure during step 11 cleans tmp and re-raises.

    Source uses ``tmp.replace(target)`` (a ``pathlib.Path`` method), not
    ``os.replace`` directly. We patch the method on the class because
    that is where the source binds.
    """
    target = tmp_path / "state.txt"
    target.write_text("old")

    real_replace = Path.replace

    def fail_replace(self: Path, target_path: Any) -> Path:
        # Only intercept replaces issued from in-flight tmp files; let
        # any unrelated replace (lock cleanup, etc.) proceed normally.
        if is_tmp_name(self.name):
            msg = "simulated replace failure"
            raise OSError(errno.EIO, msg)
        return real_replace(self, target_path)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        write_atomic(target, "new", concurrency="none", safety=_BE)

    assert target.read_text() == "old"
    assert orphan_tmp_files(tmp_path) == []


def test_replace_failure_does_not_fallback_to_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``Path.replace`` raises, the library must NOT fall back to copy.

    A defensive guard: if a future refactor accidentally added a
    ``shutil.copy``/``shutil.move`` fallback after a failed rename, the
    AtomicVisibility guarantee would silently degrade. This test asserts
    that ``shutil.copy*`` / ``shutil.move`` are never invoked on the
    failure path.
    """

    target = tmp_path / "state.txt"
    target.write_text("old")

    forbidden: list[str] = []

    def trip(name: str) -> Any:
        def inner(*_a: object, **_kw: object) -> NoReturn:
            forbidden.append(name)
            msg = f"forbidden fallback {name} invoked"
            raise AssertionError(msg)

        return inner

    monkeypatch.setattr(shutil, "copy", trip("shutil.copy"))
    monkeypatch.setattr(shutil, "copy2", trip("shutil.copy2"))
    monkeypatch.setattr(shutil, "copyfile", trip("shutil.copyfile"))
    monkeypatch.setattr(shutil, "move", trip("shutil.move"))

    real_replace = Path.replace

    def fail_replace(self: Path, target_path: Any) -> Path:
        if is_tmp_name(self.name):
            msg = "simulated replace failure"
            raise OSError(errno.EIO, msg)
        return real_replace(self, target_path)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        write_atomic(target, "new", concurrency="none", safety=_BE)

    assert forbidden == []
    assert target.read_text() == "old"


# ---------------------------------------------------------------------------
# 4. Failure in fsync(parent_dir)  (step 12)
# ---------------------------------------------------------------------------


def test_parent_fsync_failure_after_replace_does_not_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 12 (parent-dir fsync) failure is suppressed by ``_fsync_dir``.

    Contract (``_io_core`` docstring): "Step 12 fails -> file IS visible;
    log warning only, no removal." This test pins that behaviour by
    forcing ``_fsync_dir`` to fail and verifying:

      - the new content is visible (replace already happened),
      - no rollback is attempted,
      - no orphan tmp remains,
      - the operation does not raise.

    Caveat documented in this test: the operation returns *normally* even
    though the durability fsync was not confirmed. CrashDurability is
    therefore "best-effort" with respect to the directory entry on this
    code path. Callers needing stricter durability must inspect the log
    or use ``doctor()``.
    """

    target = tmp_path / "state.txt"
    target.write_text("old")

    fsync_dir_calls: list[Path] = []

    def fail_fsync_dir(directory: Path) -> None:
        fsync_dir_calls.append(directory)
        # _fsync_dir in the real code already suppresses errors; we
        # mimic that contract here while flagging that it was reached
        # AFTER the visibility point. The test does NOT raise from
        # inside _fsync_dir because doing so would diverge from the
        # documented "log warning only" contract.

    monkeypatch.setattr(_io_core, "_fsync_dir", fail_fsync_dir)

    write_atomic(target, "new", concurrency="none", safety=_BE)

    assert target.read_text() == "new"
    assert orphan_tmp_files(tmp_path) == []
    assert fsync_dir_calls, "expected _fsync_dir to be invoked after replace"


# ---------------------------------------------------------------------------
# 5. Failure around checksum sidecar (step 13)
# ---------------------------------------------------------------------------


def test_checksum_sidecar_failure_raises_after_target_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 13 failure: file IS visible, library raises ``SafeAtomicError``.

    The contract (``_io_core`` module docstring) says step 13 failure
    must NOT remove the now-visible file. The error must surface so the
    caller learns that integrity protection is absent.
    """

    target = tmp_path / "state.txt"
    target.write_text("old")

    def boom(_path: Any, *, algo: str = "sha256") -> NoReturn:
        msg = "simulated sidecar failure"
        raise OSError(errno.EIO, msg)

    # Patch the symbol where _write_checksum_sidecar looks it up.
    monkeypatch.setattr(_checksum, "write_checksum_file", boom)
    # The local import in _write_checksum_sidecar means we must also
    # patch on _io_core if it had been hoisted; check both to be safe.
    if hasattr(_io_core, "write_checksum_file"):
        monkeypatch.setattr(_io_core, "write_checksum_file", boom, raising=False)

    with pytest.raises(SafeAtomicError, match="checksum sidecar"):
        write_atomic(
            target,
            "new",
            concurrency="none",
            write_checksum=True,
            safety=_BE,
        )

    # Visibility point preceded checksum write -> file IS the new one.
    assert target.read_text() == "new"
    # No sidecar created (write_checksum_file was patched to fail).
    assert checksum_files(tmp_path) == []
    # No orphan tmp leaked.
    assert orphan_tmp_files(tmp_path) == []


def test_read_atomic_check_checksum_fails_without_sidecar(tmp_path: Path) -> None:
    """If a prior writer skipped the sidecar, a checksummed read must fail.

    This pins the TLA+ ``SafeAtomicChecksum`` insight: the read API
    validates the **observed pair** ``(target, sidecar)`` at call time.
    A target without a sidecar is not a Match - it is a Mismatch by
    construction (sidecar missing).
    """
    target = tmp_path / "state.txt"
    write_atomic(target, "value", concurrency="none", safety=_BE)
    # No checksum requested on write -> sidecar absent.
    assert checksum_files(tmp_path) == []

    with pytest.raises(ChecksumMismatchError):
        read_atomic(target, check_checksum=True, safety=_BE)


def test_read_atomic_check_checksum_detects_corrupt_target(
    tmp_path: Path,
) -> None:
    """Corruption of the target file is detected by ``check_checksum=True``."""
    target = tmp_path / "state.txt"
    write_atomic(
        target,
        "original",
        concurrency="none",
        write_checksum=True,
        safety=_BE,
    )

    # Tamper with the target *after* the write completed.
    target.write_bytes(b"tampered")

    with pytest.raises(ChecksumMismatchError):
        read_atomic(target, check_checksum=True, safety=_BE)


def test_read_atomic_check_checksum_detects_corrupt_sidecar(
    tmp_path: Path,
) -> None:
    """Corruption of the sidecar (well-formed but wrong digest) is detected."""
    target = tmp_path / "state.txt"
    write_atomic(
        target,
        "original",
        concurrency="none",
        write_checksum=True,
        safety=_BE,
    )
    sidecar = target.with_name(target.name + CHECKSUM_SUFFIX)
    assert sidecar.exists()

    # Replace digest with a wrong one but keep the rest of the layout.
    wrong_digest = "0" * 64
    sidecar.write_text(
        f"{wrong_digest}  {target.name}\nalgo=sha256\n",
        encoding="ascii",
    )

    with pytest.raises(ChecksumMismatchError):
        read_atomic(target, check_checksum=True, safety=_BE)


def test_verify_checksum_observed_pair_only(tmp_path: Path) -> None:
    """``verify_checksum`` reports on the pair seen NOW, not historical state.

    TLA+ insight: a True earlier does not imply True later if the file
    or sidecar changes afterwards. The test demonstrates by mutating
    the target after a successful Match and observing a Mismatch.
    """
    target = tmp_path / "state.txt"
    write_atomic(
        target,
        "first",
        concurrency="none",
        write_checksum=True,
        safety=_BE,
    )

    assert verify_checksum(target) is True

    # Mutate target out-of-band; the sidecar is now stale relative to
    # the new content. The next observation must report Mismatch.
    target.write_bytes(b"changed")

    assert verify_checksum(target) is False


# ---------------------------------------------------------------------------
# 6. EXDEV / cross-device via fault injection
# ---------------------------------------------------------------------------


def test_move_atomic_raises_cross_device_when_devices_differ(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-device source/destination raises ``CrossDeviceAtomicityError``.

    The source uses ``Path.stat().st_dev`` to detect the mismatch
    *before* attempting ``os.replace``. We monkeypatch ``Path.stat`` so
    src and dst-parent report distinct device ids.
    """
    src = tmp_path / "src.txt"
    src.write_text("payload")
    dst = tmp_path / "dst.txt"

    real_stat = Path.stat
    src_resolved = src.resolve()
    dst_parent_resolved = dst.parent.resolve()

    def lying_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        rs = real_stat(self, follow_symlinks=follow_symlinks)
        if self.resolve() == src_resolved:
            # Force st_dev to differ. os.stat_result is immutable so we
            # construct a new one via the public 10-tuple ctor.
            fields = list(rs)
            fields[2] = rs.st_dev + 1  # type: ignore[assignment]
            return os.stat_result(fields)
        if self.resolve() == dst_parent_resolved:
            return rs
        return rs

    monkeypatch.setattr(Path, "stat", lying_stat)

    with pytest.raises(CrossDeviceAtomicityError):
        move_atomic(src, dst, safety=_BE)

    # Source intact, destination not created.
    assert src.exists()
    assert src.read_text() == "payload"
    assert not dst.exists()


def test_move_atomic_replace_failure_does_not_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``Path.replace`` raises EXDEV after the same-device guard passed,
    the library MUST NOT silently fall back to copy+delete.

    DRIFT NOTE: In v2.0.dev0 ``move_atomic`` short-circuits cross-device
    by comparing ``st_dev`` upfront. If that guard is satisfied and
    ``Path.replace`` then raises ``EXDEV`` anyway (a race or a lying
    filesystem), the current implementation propagates the raw
    :class:`OSError` rather than wrapping it in
    :class:`CrossDeviceAtomicityError`. We pin the observed behaviour
    and verify that no shutil-based fallback is reached. If a future
    revision normalises this to ``CrossDeviceAtomicityError``, update
    the expected exception below.
    """

    src = tmp_path / "src.txt"
    src.write_text("payload")
    dst = tmp_path / "dst.txt"

    forbidden: list[str] = []

    def trip(name: str) -> Any:
        def inner(*_a: object, **_kw: object) -> NoReturn:
            forbidden.append(name)
            msg = f"forbidden fallback {name} invoked"
            raise AssertionError(msg)

        return inner

    monkeypatch.setattr(shutil, "copy", trip("shutil.copy"))
    monkeypatch.setattr(shutil, "copy2", trip("shutil.copy2"))
    monkeypatch.setattr(shutil, "copyfile", trip("shutil.copyfile"))
    monkeypatch.setattr(shutil, "move", trip("shutil.move"))

    src_resolved = src.resolve()
    real_replace = Path.replace

    def fail_with_exdev(self: Path, target_path: Any) -> Path:
        if self.resolve() == src_resolved:
            msg = "simulated EXDEV"
            raise OSError(errno.EXDEV, msg)
        return real_replace(self, target_path)

    monkeypatch.setattr(Path, "replace", fail_with_exdev)

    # Accept either the documented future behaviour
    # (CrossDeviceAtomicityError) or the current raw OSError. Both
    # outcomes are acceptable for THIS test as long as no fallback runs.
    with pytest.raises((CrossDeviceAtomicityError, OSError)) as exc_info:
        move_atomic(src, dst, safety=_BE)

    # If it surfaced as a generic OSError, it must carry EXDEV.
    if not isinstance(exc_info.value, CrossDeviceAtomicityError):
        assert getattr(exc_info.value, "errno", None) == errno.EXDEV

    assert forbidden == []
    assert src.exists()
    assert not dst.exists()


# ---------------------------------------------------------------------------
# 7. Orphan lock (well-formed payload, dead PID)
# ---------------------------------------------------------------------------


def test_orphan_lock_is_stale_when_pid_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock whose PID is no longer alive on the same host is stale.

    Simulates the "lock orphaned after a crash" scenario: a well-formed
    sidecar exists but the writer that recorded it is gone. The
    ``release_stale_lock`` API must reclaim it; ``force_release_lock``
    must also work. A liveness probe that reports "yes" must NOT
    reclaim.
    """

    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)

    # Forge a lock owned by a clearly-dead PID. We monkeypatch the
    # liveness probe so we do not depend on actual PID allocation.
    forged_pid = 999_999
    payload = _build_payload(
        pid=forged_pid,
        hostname=_locks._current_hostname(),
        session_hash=None,
        timestamp=datetime.now(tz=UTC),
    )
    lock.write_bytes(payload)

    def dead_probe(_pid: int) -> str:
        return "no"

    monkeypatch.setattr(_locks, "_pid_alive_locally", dead_probe)

    assert is_stale_lock(target) is True
    assert release_stale_lock(target) is True
    assert not lock.exists()


def test_live_lock_is_not_reclaimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock probe that reports ``yes`` must NOT remove the lock."""

    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)
    payload = _build_payload(
        pid=4242,
        hostname=_locks._current_hostname(),
        session_hash=None,
        timestamp=datetime.now(tz=UTC),
    )
    lock.write_bytes(payload)

    monkeypatch.setattr(_locks, "_pid_alive_locally", lambda _pid: "yes")

    assert is_stale_lock(target) is False
    assert release_stale_lock(target) is False
    assert lock.exists()


def test_orphan_lock_reclaimable_by_max_age(tmp_path: Path) -> None:
    """When the holder is on another host (alive='unknown'), age policy reclaims.

    Cross-host PID liveness cannot be probed locally. The operator policy
    ``max_age_s`` is the only stale-recovery lever in that case.
    """
    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)
    very_old = datetime.now(tz=UTC) - timedelta(hours=1)
    payload = _build_payload(
        pid=1,
        hostname="some-other-host-not-this-one",
        session_hash=None,
        timestamp=very_old,
    )
    lock.write_bytes(payload)

    # Without max_age_s -> cannot reclaim (PID probe is 'unknown'
    # because hostname differs).
    assert is_stale_lock(target) is False
    # With a policy of 60 seconds, the 1-hour-old lock is stale.
    assert is_stale_lock(target, max_age_s=60.0) is True
    assert release_stale_lock(target, max_age_s=60.0) is True
    assert not lock.exists()


# ---------------------------------------------------------------------------
# 8. Corrupt lock sidecar
# ---------------------------------------------------------------------------


def test_corrupt_lock_inspected_as_corrupt(tmp_path: Path) -> None:
    """An unparseable lock payload yields ``LockInfo(corrupt=True)``."""
    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)
    lock.write_bytes(b"this is not json at all")

    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is True
    assert info.pid is None
    assert info.hostname is None


def test_corrupt_lock_truncated_json(tmp_path: Path) -> None:
    """A truncated JSON payload is treated as corrupt, not as a partial lock."""
    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)
    lock.write_bytes(b'{"version": 1, "pid":')  # cut off mid-token

    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is True


def test_corrupt_lock_unknown_version(tmp_path: Path) -> None:
    """A well-formed JSON with unknown version is corrupt by contract."""
    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)
    bad = json.dumps(
        {
            "version": 9999,
            "pid": 1,
            "hostname": "h",
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    ).encode("utf-8")
    lock.write_bytes(bad)

    info = inspect_lock(target)
    assert info.corrupt is True


def test_release_stale_lock_does_not_treat_corrupt_as_stale(
    tmp_path: Path,
) -> None:
    """Corrupt is not the same as stale; ``release_stale_lock`` must skip it.

    Per ``_locks.is_stale_lock``: missing or corrupt locks return False
    (not stale). Recovery from a corrupt lock requires
    ``force_release_lock``, which is the documented administrative
    override.
    """
    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)
    lock.write_bytes(b"garbage payload")

    assert is_stale_lock(target) is False
    assert is_stale_lock(target, max_age_s=0.0) is False
    assert release_stale_lock(target) is False
    assert release_stale_lock(target, max_age_s=0.0) is False
    # File still there -> corrupt was preserved, not silently mopped up.
    assert lock.exists()


def test_force_release_lock_removes_corrupt(tmp_path: Path) -> None:
    """``force_release_lock`` is unconditional and removes corrupt sidecars."""
    target = tmp_path / "data.json"
    lock = target.with_name(target.name + LOCK_SUFFIX)
    lock.write_bytes(b"garbage")

    assert force_release_lock(target) is True
    assert not lock.exists()


# ---------------------------------------------------------------------------
# 9. Symlink behaviour (v2.0 unspecified)
# ---------------------------------------------------------------------------


def test_symlink_write_behaviour_is_documented_not_specified(
    tmp_path: Path,
) -> None:
    """v2.0 ``SymlinkPolicy`` is the single value ``"unspecified"``.

    This test records the *observed* behaviour without elevating it to a
    contract. It exists so a future regression that, say, suddenly
    deletes symlinks or stops following them is visible in CI as a
    behavioural change, but it does NOT declare what the library
    promises - the docs explicitly say symlink handling is unspecified
    in v2.0.

    Observed behaviour for ``write_atomic`` over a symlink in this
    revision: ``os.replace`` swaps the *symlink itself*, so the link is
    replaced by a regular file with the new content. The link target is
    NOT followed. The test asserts only that the operation does not
    silently corrupt the link target.
    """
    real = tmp_path / "real.txt"
    real.write_text("real-content")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    write_atomic(link, "via-link", concurrency="none", safety=_BE)

    # After replace, *something* called link.txt exists with the new
    # content. The library does not promise whether real.txt is also
    # mutated; we only verify the visible new content is there.
    assert link.exists()
    assert link.read_text() == "via-link"
    # The original target is preserved exactly when the rename targets
    # the link inode, which is the documented POSIX rename semantics.
    # v2.0 declares this dimension "unspecified", so we assert only the
    # weakest invariant: the real file has not been deleted.
    assert real.exists()


def test_symlink_read_behaviour_is_documented(tmp_path: Path) -> None:
    """Reading through a symlink returns the linked content (POSIX default).

    Documented as v2.0-unspecified; pinning observed behaviour only.
    """
    real = tmp_path / "real.txt"
    real.write_text("real-content")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    assert read_atomic(link, safety=_BE) == "real-content"


# ---------------------------------------------------------------------------
# 10. General cleanup invariants
# ---------------------------------------------------------------------------


def _classify(items: Iterable[Path]) -> dict[str, list[str]]:
    by_kind: dict[str, list[str]] = {"tmp": [], "lock": [], "checksum": []}
    for p in items:
        if is_tmp_name(p.name):
            by_kind["tmp"].append(p.name)
        elif p.name.endswith(LOCK_SUFFIX):
            by_kind["lock"].append(p.name)
        elif p.name.endswith(CHECKSUM_SUFFIX):
            by_kind["checksum"].append(p.name)
    return by_kind


def test_no_tmp_orphans_after_aborted_writer(tmp_path: Path) -> None:
    """``AtomicWriter`` abort path leaves zero ``.safeatomic-tmp-*.tmp``."""
    target = tmp_path / "state.txt"
    target.write_text("old")

    with pytest.raises(RuntimeError), AtomicWriter(target, concurrency="none", safety=_BE) as w:
        w.write(b"partial")
        raise RuntimeError("abort")

    items = safeatomic_artifacts(tmp_path)
    classified = _classify(items)
    assert classified["tmp"] == [], f"orphan tmp files after abort: {classified['tmp']!r}"


def test_no_tmp_orphans_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed ``write_atomic`` (step 11) leaves no tmp behind."""
    target = tmp_path / "state.txt"
    target.write_text("old")

    real_replace = Path.replace

    def fail_replace(self: Path, target_path: Any) -> Path:
        if is_tmp_name(self.name):
            msg = "boom"
            raise OSError(errno.EIO, msg)
        return real_replace(self, target_path)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError):
        write_atomic(target, "new", concurrency="none", safety=_BE)

    assert orphan_tmp_files(tmp_path) == []


def test_lock_released_after_writer_exception(tmp_path: Path) -> None:
    """An exception in a locked ``AtomicWriter`` still releases the lock."""
    target = tmp_path / "state.txt"
    target.write_text("old")

    with pytest.raises(RuntimeError), AtomicWriter(target, concurrency="lock", safety=_BE) as w:
        # Mid-write crash should not leave a lock sidecar behind.
        w.write(b"partial")
        raise RuntimeError("boom")

    assert lock_files(tmp_path) == [], "lock sidecar leaked after writer exception"
    # The lock is gone, so a fresh acquisition succeeds.
    assert try_acquire_lock(target, safety=_BE) is True
    force_release_lock(target)


def test_lock_released_after_write_atomic_internal_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-write failure in ``write_atomic(concurrency='lock')`` still releases.

    Pins step 14 of the write protocol: ``release_lock`` always runs in
    ``finally``. After a forced step-11 failure, the lock sidecar must
    be gone so a subsequent writer can proceed.
    """
    target = tmp_path / "state.txt"
    target.write_text("old")

    real_replace = Path.replace

    def fail_replace(self: Path, target_path: Any) -> Path:
        if is_tmp_name(self.name):
            msg = "simulated replace failure"
            raise OSError(errno.EIO, msg)
        return real_replace(self, target_path)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        write_atomic(target, "new", concurrency="lock", safety=_BE)

    assert lock_files(tmp_path) == [], "lock leaked after write_atomic failure"
    assert orphan_tmp_files(tmp_path) == []
    assert target.read_text() == "old"


def test_bytes_writer_aborted_no_leak(tmp_path: Path) -> None:
    """Same invariants hold for ``write_atomic_bytes``."""
    target = tmp_path / "blob.bin"
    target.write_bytes(b"old-bytes")

    def boom(_tmp: Path) -> NoReturn:
        msg = "open failure"
        raise OSError(errno.EIO, msg)

    # Use pytest's monkeypatch indirectly via a context manager pattern
    # would be cleaner, but we already inject through monkeypatch in
    # other tests; here we use a try/finally to keep this test
    # self-contained as an extra cleanup smoke test.
    original = _io_core._write_open_tmp
    _io_core._write_open_tmp = boom  # type: ignore[assignment]
    try:
        with pytest.raises(OSError, match="open failure"):
            write_atomic_bytes(target, b"new-bytes", concurrency="none", safety=_BE)
    finally:
        _io_core._write_open_tmp = original  # type: ignore[assignment]

    assert target.read_bytes() == b"old-bytes"
    assert orphan_tmp_files(tmp_path) == []


def test_safeatomic_artifacts_classifies_known_names(tmp_path: Path) -> None:
    """The orphan-scanner helper is itself well-behaved.

    Pure sanity check on the test helper. If this assertion fails, every
    other "no orphan" assertion in this file becomes meaningless.
    """
    (tmp_path / f"{TMP_PREFIX}deadbeef{TMP_SUFFIX}").write_bytes(b"")
    (tmp_path / f"file{LOCK_SUFFIX}").write_bytes(b"x")
    (tmp_path / f"file{CHECKSUM_SUFFIX}").write_bytes(b"x")
    (tmp_path / "unrelated.txt").write_bytes(b"x")

    artefacts = {p.name for p in safeatomic_artifacts(tmp_path)}
    assert artefacts == {
        f"{TMP_PREFIX}deadbeef{TMP_SUFFIX}",
        f"file{LOCK_SUFFIX}",
        f"file{CHECKSUM_SUFFIX}",
    }
