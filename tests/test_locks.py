"""Tier 2 tests for safeatomic._locks.

Scope: cooperative whole-file locking (WriterExclusion + StaleRecovery).

Public-API focus: the 8 lock callables and ``LockInfo`` are imported from
the package surface. A small number of private helpers are imported with
explicit justification:

- ``safeatomic._locks._pid_alive_locally`` and
  ``safeatomic._locks._current_hostname``: monkeypatched to obtain
  deterministic PID-liveness / hostname behaviour without relying on
  real processes or the test host's identity.
- ``safeatomic._locks._build_payload``: used to construct on-disk
  payloads that match the v1 schema exactly (timestamps, version,
  ordering). Reimplementing the format in tests would freeze a parallel
  contract; reusing the helper keeps the tests aligned with the schema.
- ``safeatomic._constants.LOCK_PAYLOAD_VERSION``: referenced when forging
  corrupt payloads with an unknown version.
- ``safeatomic._paths.lock_path``: used to compute the sidecar path the
  way the source does (``with_name`` semantics, not ``with_suffix``).

The four insights from the TLA+ ``SafeAtomicLock`` model that the suite
exercises explicitly:

1. PID liveness is only valid for the host that recorded the lock.
2. ``release_stale_lock`` is the ONLY surface providing StaleRecovery;
   ``force_release_lock`` is an administrative override and removes any
   lock unconditionally.
3. Acquisition resets all state; no metadata from a prior force-release
   contaminates a fresh acquisition (epoch insight).
4. ``release_lock`` is owner-scoped (PID+hostname), so the cooperative
   guarantee holds even though a narrow TOCTOU window remains (closed
   only by an epoch token in v2.1; see ``_locks.release_lock`` docstring
   and ``design/adjacencies.md``).

Cross-refs:
- formal/SafeAtomicLock.tla
- design/guarantees-formalization.md §6 §7
- design/failure-model.md (sidecar contract)
- design/adjacencies.md (v2.1 epoch token)
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from safeatomic import (
    LockError,
    LockInfo,
    force_release_lock,
    get_lock_age,
    inspect_lock,
    is_locked,
    is_stale_lock,
    release_lock,
    release_stale_lock,
    try_acquire_lock,
)
from safeatomic._constants import LOCK_PAYLOAD_VERSION
from safeatomic._locks import _build_payload
from safeatomic._paths import lock_path

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_payload(
    lf: Path,
    *,
    pid: int,
    hostname: str,
    session_hash: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    """Write a well-formed v1 lock payload directly to ``lf``.

    Uses the production payload builder so the byte layout matches the
    contract under test, including key order and trailing newline.
    """
    ts = timestamp if timestamp is not None else datetime.now(tz=UTC)
    lf.write_bytes(_build_payload(pid, hostname, session_hash, ts))


# ---------------------------------------------------------------------------
# 1. Basic acquisition / release
# ---------------------------------------------------------------------------


def test_acquire_on_free_path_creates_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert try_acquire_lock(target) is True
    assert lock_path(target).exists()


def test_acquire_duplicate_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert try_acquire_lock(target) is True
    # A second acquisition with no retries must return False (contention)
    # without raising. Same process, but the O_EXCL semantics still apply.
    assert try_acquire_lock(target) is False


def test_acquire_with_retries_still_false_when_held(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert try_acquire_lock(target) is True
    # retries=2, delay=0 so the test is fast. Still contended.
    assert try_acquire_lock(target, retries=2, delay=0.0) is False


def test_release_removes_lock_owned_by_current_process(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert try_acquire_lock(target) is True
    assert release_lock(target) is True
    assert not lock_path(target).exists()


def test_release_is_idempotent_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    # No lock has ever been acquired.
    assert release_lock(target) is False
    # Acquire + release once, then call release again on the empty slot.
    assert try_acquire_lock(target) is True
    assert release_lock(target) is True
    assert release_lock(target) is False


def test_force_release_removes_existing_lock(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert try_acquire_lock(target) is True
    assert force_release_lock(target) is True
    assert not lock_path(target).exists()


def test_force_release_returns_false_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert force_release_lock(target) is False


def test_is_locked_reflects_sidecar_existence(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert is_locked(target) is False
    assert try_acquire_lock(target) is True
    assert is_locked(target) is True
    assert release_lock(target) is True
    assert is_locked(target) is False


def test_try_acquire_lock_rejects_negative_retries(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    with pytest.raises(ValueError, match="retries"):
        try_acquire_lock(target, retries=-1)


def test_try_acquire_lock_rejects_negative_delay(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    with pytest.raises(ValueError, match="delay"):
        try_acquire_lock(target, delay=-0.1)


def test_try_acquire_lock_rejects_invalid_safety(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    with pytest.raises(ValueError, match="safety"):
        try_acquire_lock(target, safety="nope")  # type: ignore[arg-type]


def test_try_acquire_lock_raises_lockerror_when_parent_missing(tmp_path: Path) -> None:
    # Parent directory does not exist; this is a structural failure, not
    # ordinary contention.
    target = tmp_path / "missing-dir" / "data.json"
    with pytest.raises(LockError):
        try_acquire_lock(target)


# ---------------------------------------------------------------------------
# 2. LockInfo schema
# ---------------------------------------------------------------------------


def test_lockinfo_path_is_target_not_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    try_acquire_lock(target)
    info = inspect_lock(target)
    assert info.path == target
    assert info.lock_path == lock_path(target)
    assert info.path != info.lock_path
    # The sidecar path is the target's name with .lock appended.
    assert info.lock_path.name == "data.json.lock"


def test_lockinfo_timestamp_is_tz_aware_utc(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    try_acquire_lock(target)
    info = inspect_lock(target)
    assert info.timestamp is not None
    assert info.timestamp.tzinfo is not None
    # The library stores UTC; the parsed value must round-trip to UTC.
    assert info.timestamp.utcoffset() == timedelta(0)


def test_lockinfo_session_hash_never_exposes_raw_session(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    session_id = "my-session-id-do-not-leak-to-disk"
    try_acquire_lock(target, session=session_id)

    # Disk: the raw session must NOT appear in the payload bytes.
    raw = lock_path(target).read_bytes()
    assert session_id.encode("utf-8") not in raw

    # API: inspect returns the digest, not the raw string.
    info = inspect_lock(target)
    assert info.session_hash is not None
    assert info.session_hash != session_id
    assert len(info.session_hash) == 64  # sha256 hex
    int(info.session_hash, 16)  # valid hex


def test_lockinfo_session_hash_is_none_when_no_session(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    try_acquire_lock(target)
    info = inspect_lock(target)
    assert info.session_hash is None


def test_lockinfo_when_lock_absent_has_explicit_state(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    info = inspect_lock(target)
    assert isinstance(info, LockInfo)
    assert info.exists is False
    assert info.corrupt is False
    assert info.pid is None
    assert info.hostname is None
    assert info.session_hash is None
    assert info.timestamp is None
    assert info.alive is None
    assert info.raw is None
    # Path fields are populated even when the lock is absent.
    assert info.path == target
    assert info.lock_path == lock_path(target)


def test_lockinfo_str_distinct_for_absent_corrupt_live(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    # Absent
    absent_str = str(inspect_lock(target))
    assert "absent" in absent_str
    # Live
    try_acquire_lock(target)
    live_str = str(inspect_lock(target))
    assert "pid=" in live_str
    assert "host=" in live_str
    # Corrupt
    lock_path(target).write_bytes(b"definitely not json")
    corrupt_str = str(inspect_lock(target))
    assert "corrupt" in corrupt_str


def test_get_lock_age_positive_for_fresh_lock(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    try_acquire_lock(target)
    age = get_lock_age(target)
    assert age is not None
    # Fresh lock: age must be small and non-negative. Allow generous
    # upper bound to absorb scheduler jitter.
    assert age >= 0.0
    assert age < 5.0


def test_get_lock_age_returns_none_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert get_lock_age(target) is None


def test_get_lock_age_returns_none_when_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lock_path(target).write_bytes(b"garbage")
    assert get_lock_age(target) is None


# ---------------------------------------------------------------------------
# 3. Corrupt lock handling
# ---------------------------------------------------------------------------


def test_inspect_corrupt_lock_returns_corrupt_lockinfo(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lf = lock_path(target)
    lf.write_bytes(b"not valid json at all")

    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is True
    assert info.raw is not None
    assert "not valid json" in info.raw
    # Corrupt payloads do not populate parseable fields.
    assert info.pid is None
    assert info.hostname is None
    assert info.timestamp is None
    assert info.alive is None


def test_inspect_lock_with_unknown_version_is_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lf = lock_path(target)
    # Wrong schema version: must be rejected as corrupt.
    payload = {
        "version": LOCK_PAYLOAD_VERSION + 999,
        "pid": 1,
        "hostname": "h",
        "session_hash": None,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
    lf.write_bytes(json.dumps(payload).encode("utf-8"))

    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is True


def test_inspect_lock_with_missing_required_keys_is_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lf = lock_path(target)
    # Right version, but missing required keys (pid).
    payload = {
        "version": LOCK_PAYLOAD_VERSION,
        "hostname": "h",
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
    lf.write_bytes(json.dumps(payload).encode("utf-8"))

    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is True


def test_inspect_lock_with_non_dict_payload_is_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lock_path(target).write_bytes(b'["array","not","dict"]')

    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is True


def test_release_on_corrupt_lock_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lock_path(target).write_bytes(b"garbage")
    # release_lock must not remove a lock it cannot read as its own.
    assert release_lock(target) is False
    # But the corrupt file is still there.
    assert lock_path(target).exists()


def test_force_release_removes_corrupt_lock(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lock_path(target).write_bytes(b"garbage")
    # force_release is unconditional administrative override.
    assert force_release_lock(target) is True
    assert not lock_path(target).exists()


# ---------------------------------------------------------------------------
# 4. PID / hostname-aware staleness
# ---------------------------------------------------------------------------


def test_is_stale_lock_false_when_pid_alive_same_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="local-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", lambda _pid: "yes")
    assert is_stale_lock(target) is False


def test_is_stale_lock_true_when_pid_dead_same_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="local-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", lambda _pid: "no")
    assert is_stale_lock(target) is True


def test_is_stale_lock_false_when_pid_unknown_same_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="local-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", lambda _pid: "unknown")
    # "unknown" must NOT declare stale by PID. Conservative by design.
    assert is_stale_lock(target) is False


def test_is_stale_lock_never_stale_remote_host_without_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="remote-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    # Even if a hypothetical local PID 99999 were dead, the remote
    # hostname means the local kernel cannot answer the question.
    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", lambda _pid: "no")
    assert is_stale_lock(target) is False


def test_is_stale_lock_remote_host_with_max_age_uses_age_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    old_ts = datetime.now(tz=UTC) - timedelta(seconds=3600)
    _write_payload(
        lock_path(target),
        pid=99999,
        hostname="remote-host",
        timestamp=old_ts,
    )

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    # Remote host, but age policy is allowed to declare stale.
    assert is_stale_lock(target, max_age_s=60.0) is True


def test_is_stale_lock_remote_host_with_max_age_below_age_not_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    # Lock is 10s old; max_age_s=3600 keeps it fresh.
    recent_ts = datetime.now(tz=UTC) - timedelta(seconds=10)
    _write_payload(
        lock_path(target),
        pid=99999,
        hostname="remote-host",
        timestamp=recent_ts,
    )

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    assert is_stale_lock(target, max_age_s=3600.0) is False


def test_is_stale_lock_false_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert is_stale_lock(target) is False
    assert is_stale_lock(target, max_age_s=0.0) is False


def test_is_stale_lock_false_when_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lock_path(target).write_bytes(b"garbage")
    assert is_stale_lock(target) is False
    assert is_stale_lock(target, max_age_s=0.0) is False


def test_inspect_lock_remote_host_reports_alive_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="remote-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    # The probe must not be consulted at all for remote locks; but even
    # if it were, the result must be 'unknown' for cross-host PIDs.

    def _fail_if_called(_pid: int) -> str:  # pragma: no cover - safety net
        msg = "PID liveness probe must not be invoked for remote hosts"
        raise AssertionError(msg)

    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", _fail_if_called)
    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is False
    assert info.alive == "unknown"


# ---------------------------------------------------------------------------
# 5. release_stale_lock semantics
# ---------------------------------------------------------------------------


def test_release_stale_lock_removes_dead_local_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="local-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", lambda _pid: "no")

    assert release_stale_lock(target) is True
    assert not lock_path(target).exists()


def test_release_stale_lock_keeps_live_local_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="local-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", lambda _pid: "yes")

    assert release_stale_lock(target) is False
    assert lock_path(target).exists()


def test_release_stale_lock_keeps_remote_lock_without_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="remote-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    # Without max_age_s, a remote lock cannot be declared stale.
    assert release_stale_lock(target) is False
    assert lock_path(target).exists()


def test_release_stale_lock_removes_remote_lock_when_aged_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    old_ts = datetime.now(tz=UTC) - timedelta(seconds=3600)
    _write_payload(
        lock_path(target),
        pid=99999,
        hostname="remote-host",
        timestamp=old_ts,
    )

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    assert release_stale_lock(target, max_age_s=60.0) is True
    assert not lock_path(target).exists()


def test_release_stale_lock_returns_false_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert release_stale_lock(target) is False
    assert release_stale_lock(target, max_age_s=0.0) is False


def test_release_stale_lock_does_not_touch_corrupt_lock(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lock_path(target).write_bytes(b"garbage")
    # is_stale_lock returns False for corrupt sidecars, so
    # release_stale_lock must NOT remove them. Corrupt recovery is the
    # operator's job via force_release_lock.
    assert release_stale_lock(target, max_age_s=0.0) is False
    assert lock_path(target).exists()


# ---------------------------------------------------------------------------
# 6. TLA+ epoch insight: acquisition resets all state
# ---------------------------------------------------------------------------


def test_lock_release_metadata_resets_per_epoch(tmp_path: Path) -> None:
    """A new acquisition shows no trace of any prior release/force/stale.

    Operationally: previous force_release / stale / corrupt sidecar
    states must not contaminate the new lock's observable state. The
    sidecar is fully rewritten by the next ``try_acquire_lock`` (which
    only succeeds when the slot is empty), so ``inspect_lock`` on the
    new lock must report a fresh live record.

    See the TLA+ ``SafeAtomicLock`` model: every successful Acquire
    transition publishes a complete new payload; reset of release/stale
    metadata is implicit because the holder always writes the full doc.
    """
    target = tmp_path / "data.json"

    # Epoch 1: acquire, then force_release with old metadata.
    assert try_acquire_lock(target, session="epoch-1") is True
    info_epoch_1 = inspect_lock(target)
    assert info_epoch_1.exists is True
    epoch_1_hash = info_epoch_1.session_hash
    epoch_1_ts = info_epoch_1.timestamp
    assert epoch_1_hash is not None
    assert epoch_1_ts is not None

    assert force_release_lock(target) is True
    assert not lock_path(target).exists()

    # Simulate a stale corrupt file appearing in the meantime (an old
    # operator dropped garbage). This must not contaminate the next
    # acquisition because acquire uses O_CREAT|O_EXCL; we therefore
    # remove it first the same way an operator would.
    lock_path(target).write_bytes(b"old garbage from a prior life")
    assert force_release_lock(target) is True

    # Epoch 2: fresh acquisition with a different session string.
    assert try_acquire_lock(target, session="epoch-2") is True
    info_epoch_2 = inspect_lock(target)

    # No corruption.
    assert info_epoch_2.exists is True
    assert info_epoch_2.corrupt is False
    assert info_epoch_2.raw is None

    # All identifying fields belong to the new epoch.
    assert info_epoch_2.session_hash is not None
    assert info_epoch_2.session_hash != epoch_1_hash
    # PID matches the current process (we are still the same Python).
    assert info_epoch_2.pid == os.getpid()

    # Timestamp must be strictly greater than (or equal to, allowing
    # clock resolution) the previous epoch's. It must NOT have inherited
    # the old value.
    assert info_epoch_2.timestamp is not None
    assert info_epoch_2.timestamp >= epoch_1_ts
    # And it must be tz-aware UTC, freshly generated.
    assert info_epoch_2.timestamp.tzinfo is not None


def test_acquire_after_force_release_succeeds(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    assert try_acquire_lock(target) is True
    assert force_release_lock(target) is True
    # The slot is empty again; another acquisition must succeed.
    assert try_acquire_lock(target) is True
    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is False


def test_acquire_after_release_stale_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    _write_payload(lock_path(target), pid=99999, hostname="local-host")

    monkeypatch.setattr("safeatomic._locks._current_hostname", lambda: "local-host")
    monkeypatch.setattr("safeatomic._locks._pid_alive_locally", lambda _pid: "no")

    assert release_stale_lock(target) is True
    # Slot is now free. New acquisition must succeed and produce a clean
    # record owned by the current process.
    assert try_acquire_lock(target) is True
    info = inspect_lock(target)
    assert info.exists is True
    assert info.corrupt is False
    assert info.pid == os.getpid()


# ---------------------------------------------------------------------------
# 7. release_lock TOCTOU: owner-scoped behaviour
# ---------------------------------------------------------------------------
#
# v2.0 contract: release_lock removes the sidecar only when PID AND
# hostname match the current process. A v2.1 epoch token will close the
# narrow TOCTOU window between inspect and unlink (see
# ``_locks.release_lock`` docstring and ``design/adjacencies.md``). We
# test the owner-scoping conservatively here; we do NOT attempt to
# simulate the inspect/unlink interleaving, because closing it requires
# protocol changes that have not landed.


def test_release_lock_refuses_foreign_pid(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    # Forge a lock owned by some other PID on the SAME host.
    foreign_pid = os.getpid() + 1 if os.getpid() < 2 << 20 else 1
    if foreign_pid == os.getpid():
        foreign_pid += 1
    _write_payload(
        lock_path(target),
        pid=foreign_pid,
        hostname="any-host-will-do",
    )

    assert release_lock(target) is False
    assert lock_path(target).exists()


def test_release_lock_refuses_foreign_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    # Forge a lock that "this PID" supposedly holds but on a different
    # host. release_lock must still refuse: the hostname check fences
    # the owner-scoped contract.
    _write_payload(
        lock_path(target),
        pid=os.getpid(),
        hostname="some-other-host",
    )

    monkeypatch.setattr(
        "safeatomic._locks._current_hostname",
        lambda: "local-host",
    )

    assert release_lock(target) is False
    assert lock_path(target).exists()


def test_release_lock_accepts_own_pid_and_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.json"
    monkeypatch.setattr(
        "safeatomic._locks._current_hostname",
        lambda: "deterministic-host",
    )
    # Acquire under the patched hostname so the recorded host matches.
    assert try_acquire_lock(target) is True
    assert release_lock(target) is True
    assert not lock_path(target).exists()


def test_release_lock_refuses_corrupt_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    lock_path(target).write_bytes(b"garbage")
    # Cannot prove ownership of a corrupt record; refuse.
    assert release_lock(target) is False
    assert lock_path(target).exists()
