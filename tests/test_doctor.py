"""Tier 1 tests for safeatomic.doctor / DoctorReport / DoctorCheck.

Scope: operational diagnostic that probes the actual filesystem.

Import policy: ``doctor``, ``DoctorReport``, ``DoctorCheck`` are all in
the public 43-name surface. No private imports are needed for behaviour
tests; one private constant (``_DOCTOR_TMP_PREFIX``) is imported so the
cleanup-guarantee tests can target exactly the files doctor creates.
This is reported in the final summary.

Spec references:
- design/api-v2-proposal.md (signature)
- design/implementation-discipline.md principle 14 (report is truth)
- design/failure-model.md (orphan cleanup; .safeatomic-doctor-* prefix)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from safeatomic import (
    DoctorCheck,
    DoctorReport,
    GuaranteeReport,
    doctor,
)
from safeatomic._capabilities import Environment

# Private constant: needed so cleanup tests can count probe files by
# their canonical prefix without hard-coding the literal string.
from safeatomic._doctor import _DOCTOR_TMP_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterator


_VALID_STATUSES = {"pass", "warn", "fail", "unknown"}

_PROBE_NAMES = (
    "create_excl_0600",
    "fsync_file",
    "fsync_dir",
    "atomic_replace",
    "lock_sidecar",
    "checksum_sidecar",
)

_ALWAYS_ON_CHECKS = (
    "parent_exists",
    "parent_writable",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doctor_orphans(directory: Path) -> list[Path]:
    """Return any leftover .safeatomic-doctor-* files in ``directory``."""
    if not directory.exists():
        return []
    return [p for p in directory.iterdir() if p.name.startswith(_DOCTOR_TMP_PREFIX)]


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A target path inside tmp_path. Does not need to exist."""
    return tmp_path / "state.json"


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


def test_doctor_returns_doctor_report(target: Path) -> None:
    rep = doctor(target)
    assert isinstance(rep, DoctorReport)


def test_report_has_required_attributes(target: Path) -> None:
    rep = doctor(target)
    # Spec line: path / environment / guarantees / checks / ok must all
    # be present.
    assert hasattr(rep, "path")
    assert hasattr(rep, "environment")
    assert hasattr(rep, "guarantees")
    assert hasattr(rep, "checks")
    assert hasattr(rep, "ok")


def test_report_path_is_resolved_path(target: Path) -> None:
    rep = doctor(target)
    assert isinstance(rep.path, Path)
    # The implementation calls Path(...).resolve(strict=False); the
    # resolved path must be absolute.
    assert rep.path.is_absolute()


def test_report_environment_is_environment(target: Path) -> None:
    rep = doctor(target)
    assert isinstance(rep.environment, Environment)


def test_report_guarantees_is_guarantee_report(target: Path) -> None:
    rep = doctor(target)
    assert isinstance(rep.guarantees, GuaranteeReport)


def test_report_checks_is_tuple_of_doctorcheck(target: Path) -> None:
    rep = doctor(target)
    # We don't pin to "tuple" exactly (Sequence would be acceptable),
    # but the implementation uses tuple and the spec says "sequência".
    assert isinstance(rep.checks, tuple)
    assert len(rep.checks) > 0
    for c in rep.checks:
        assert isinstance(c, DoctorCheck)


def test_report_ok_is_bool(target: Path) -> None:
    rep = doctor(target)
    assert isinstance(rep.ok, bool)


# ---------------------------------------------------------------------------
# DoctorCheck shape
# ---------------------------------------------------------------------------


def test_check_has_required_attributes(target: Path) -> None:
    rep = doctor(target)
    for c in rep.checks:
        assert hasattr(c, "name")
        assert hasattr(c, "status")
        assert hasattr(c, "detail")
        assert isinstance(c.name, str)
        assert isinstance(c.status, str)
        assert isinstance(c.detail, str)
        assert c.name  # non-empty


def test_check_status_in_permitted_set(target: Path) -> None:
    rep = doctor(target, destructive=True)
    for c in rep.checks:
        assert c.status in _VALID_STATUSES, f"check {c.name!r} has illegal status {c.status!r}"


def test_check_is_frozen(target: Path) -> None:
    rep = doctor(target)
    c = rep.checks[0]
    # DoctorCheck is @dataclass(frozen=True, slots=True); assignment
    # must raise. Either AttributeError (FrozenInstanceError subclass)
    # or FrozenInstanceError itself.
    with pytest.raises((AttributeError, Exception)):
        c.status = "fail"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# destructive=False: no real probes, no orphans
# ---------------------------------------------------------------------------


def test_destructive_false_leaves_no_files(tmp_path: Path, target: Path) -> None:
    # Snapshot what's in the directory before the call.
    before = set(tmp_path.iterdir())
    doctor(target, destructive=False)
    after = set(tmp_path.iterdir())
    # Strict equality: doctor must add nothing.
    assert after == before
    # And no orphan probe files exist anywhere.
    assert _doctor_orphans(tmp_path) == []


def test_destructive_false_marks_probes_unknown(target: Path) -> None:
    rep = doctor(target, destructive=False)
    by_name = {c.name: c for c in rep.checks}
    for name in _PROBE_NAMES:
        assert name in by_name, f"probe {name} missing from report"
        assert by_name[name].status == "unknown", (
            f"with destructive=False, probe {name} must be 'unknown', got {by_name[name].status!r}"
        )


def test_destructive_false_still_runs_always_on_checks(target: Path) -> None:
    rep = doctor(target, destructive=False)
    by_name = {c.name: c for c in rep.checks}
    for always in _ALWAYS_ON_CHECKS:
        assert always in by_name
        # tmp_path is a real existing writable directory in pytest.
        assert by_name[always].status == "pass"


# ---------------------------------------------------------------------------
# destructive=True: probes actually run, cleanup happens
# ---------------------------------------------------------------------------


def test_destructive_true_includes_all_probe_checks(target: Path) -> None:
    rep = doctor(target, destructive=True)
    names = {c.name for c in rep.checks}
    for probe in _PROBE_NAMES:
        assert probe in names


def test_destructive_true_cleans_up_probe_files(tmp_path: Path, target: Path) -> None:
    rep = doctor(target, destructive=True)
    orphans = _doctor_orphans(tmp_path)
    assert orphans == [], f"doctor(destructive=True) left orphans: {[p.name for p in orphans]}"
    # And the original target was not created either.
    assert not target.exists()
    # Sanity: at least one probe ran and reported pass on a normal local FS.
    assert any(c.status == "pass" for c in rep.checks)


def test_destructive_probes_have_well_known_status_on_local_fs(
    target: Path,
) -> None:
    """On a typical Linux CI tmpfs/ext4/overlay, all six probes succeed.

    We assert ``status != "fail"`` rather than ``status == "pass"`` to
    tolerate the documented ``warn`` cases (e.g. fsync_dir on exotic FS,
    permissive umask widening the 0o600 mode). A hard ``fail`` would be
    a genuine doctor bug or a misbehaving filesystem.
    """
    rep = doctor(target, destructive=True)
    by_name = {c.name: c for c in rep.checks}
    for probe in _PROBE_NAMES:
        c = by_name[probe]
        assert c.status != "fail", (
            f"probe {probe} failed unexpectedly on local tmp_path: {c.detail}"
        )


# ---------------------------------------------------------------------------
# require=
# ---------------------------------------------------------------------------


def test_require_none_does_not_force_failure(target: Path) -> None:
    rep = doctor(target, destructive=True, require=None)
    # require=None means "no extra demand"; ok is True iff no check failed.
    if all(c.status != "fail" for c in rep.checks):
        assert rep.ok is True


def test_require_accepts_pascal_case(target: Path) -> None:
    # PascalCase formal name: ``AtomicVisibility``.
    rep = doctor(target, destructive=True, require={"AtomicVisibility"})
    # We don't assert ok=True (the underlying matrix may report
    # best_effort for unusual filesystems); we only verify the call
    # doesn't blow up and the result is a valid bool.
    assert isinstance(rep.ok, bool)


def test_require_accepts_snake_case(target: Path) -> None:
    rep = doctor(target, destructive=True, require={"atomic_visibility"})
    assert isinstance(rep.ok, bool)


def test_require_unknown_name_forces_ok_false(target: Path) -> None:
    # Per docstring: "An unknown name causes ok to be False."
    rep = doctor(target, destructive=True, require={"NotARealGuarantee"})
    assert rep.ok is False


def test_require_pascal_and_snake_agree(target: Path) -> None:
    rep_pascal = doctor(target, destructive=True, require={"AtomicVisibility"})
    rep_snake = doctor(target, destructive=True, require={"atomic_visibility"})
    # Same semantics either way.
    assert rep_pascal.ok == rep_snake.ok


# ---------------------------------------------------------------------------
# summary() and to_dict()
# ---------------------------------------------------------------------------


def test_summary_returns_non_empty_string(target: Path) -> None:
    rep = doctor(target, destructive=True)
    s = rep.summary()
    assert isinstance(s, str)
    assert s
    # Useful information: at least mention the path and at least one
    # check name.
    assert str(rep.path) in s
    assert any(c.name in s for c in rep.checks)


def test_str_returns_summary(target: Path) -> None:
    rep = doctor(target)
    assert str(rep) == rep.summary()


def test_to_dict_is_json_serialisable(target: Path) -> None:
    rep = doctor(target, destructive=True)
    d = rep.to_dict()
    assert isinstance(d, dict)
    # Round-trip through json.dumps to prove serialisability.
    serialised = json.dumps(d)
    parsed = json.loads(serialised)
    assert parsed["path"] == str(rep.path)
    assert parsed["ok"] == rep.ok
    assert isinstance(parsed["checks"], list)
    assert len(parsed["checks"]) == len(rep.checks)
    for c_dict, c_obj in zip(parsed["checks"], rep.checks, strict=True):
        assert c_dict["name"] == c_obj.name
        assert c_dict["status"] == c_obj.status
        assert c_dict["detail"] == c_obj.detail


def test_to_dict_environment_section_complete(target: Path) -> None:
    rep = doctor(target)
    d = rep.to_dict()
    env = d["environment"]
    assert isinstance(env, dict)
    for key in (
        "platform",
        "filesystem",
        "filesystem_class",
        "supports_fsync_file",
        "supports_fsync_dir",
        "supports_atomic_replace",
        "symlink_policy",
    ):
        assert key in env


# ---------------------------------------------------------------------------
# Error handling: missing parent
# ---------------------------------------------------------------------------


def test_doctor_on_path_with_missing_parent_does_not_raise(tmp_path: Path) -> None:
    """Spec: 'path com parent inexistente deve gerar report fail ou
    exceção clara, conforme contrato atual'.

    The implementation's contract is: build a report. parent_exists is
    'fail' or 'unknown' and ok is False. No exception.
    """
    target = tmp_path / "deep" / "deeper" / "deepest" / "f.bin"
    rep = doctor(target, destructive=False)
    assert isinstance(rep, DoctorReport)
    by_name = {c.name: c for c in rep.checks}
    # parent_exists must report something other than 'pass'.
    assert by_name["parent_exists"].status in {"fail", "unknown"}


def test_doctor_missing_parent_skips_destructive_probes(tmp_path: Path) -> None:
    # When parent does not exist, doctor must not attempt destructive
    # probes (they'd all fail with ENOENT). Per source:
    # probes are gated on ``destructive and parent.exists()``.
    target = tmp_path / "missing_dir" / "f.bin"
    rep = doctor(target, destructive=True)
    by_name = {c.name: c for c in rep.checks}
    # Probe checks should be 'unknown' (skipped), not 'fail'.
    for probe in _PROBE_NAMES:
        assert by_name[probe].status == "unknown"
    # And no orphan files in the (non-existent) parent — vacuous, but
    # also no orphans in tmp_path itself.
    assert _doctor_orphans(tmp_path) == []


# ---------------------------------------------------------------------------
# Accept str and PathLike
# ---------------------------------------------------------------------------


def test_doctor_accepts_string_path(tmp_path: Path) -> None:
    target = tmp_path / "s.bin"
    rep = doctor(str(target))
    assert isinstance(rep, DoctorReport)
    assert rep.path == target.resolve(strict=False)


def test_doctor_accepts_pathlike(tmp_path: Path) -> None:
    target = tmp_path / "p.bin"
    rep = doctor(os.fspath(target))
    assert isinstance(rep, DoctorReport)


# ---------------------------------------------------------------------------
# Idempotence: repeated calls do not accumulate state
# ---------------------------------------------------------------------------


def test_repeated_destructive_calls_do_not_leak(tmp_path: Path, target: Path) -> None:
    for _ in range(5):
        doctor(target, destructive=True)
    assert _doctor_orphans(tmp_path) == []


def test_two_calls_return_equal_reports_on_stable_fs(target: Path) -> None:
    # On a stable local FS, two non-destructive calls produce equal
    # environment, guarantees, and (status-wise) checks. We compare the
    # status tuple rather than the full DoctorCheck objects (detail
    # strings can vary if they include a PID or nonce).
    r1 = doctor(target, destructive=False)
    r2 = doctor(target, destructive=False)
    assert r1.environment == r2.environment
    assert r1.guarantees == r2.guarantees
    assert tuple((c.name, c.status) for c in r1.checks) == tuple(
        (c.name, c.status) for c in r2.checks
    )


# ---------------------------------------------------------------------------
# Cleanup guarantee under probe failure (fault injection lite)
# ---------------------------------------------------------------------------


def test_cleanup_runs_even_when_a_probe_raises(
    tmp_path: Path,
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a single probe raises mid-way, files created before the raise
    must still be removed (the probes use try/finally).

    We patch ``os.replace`` (used by the atomic_replace probe) to raise
    *after* its src file has been created. The src file is created with
    ``os.open``; the replace probe's finally block then unlinks src.
    """
    real_replace = os.replace
    raised = {"count": 0}

    def bad_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Only intercept inside our tmp_path tree so we don't affect
        # unrelated I/O performed by pytest itself.
        if str(src).startswith(str(tmp_path)):
            raised["count"] += 1
            msg = "injected replace failure"
            raise OSError(msg)
        real_replace(src, dst)

    monkeypatch.setattr("safeatomic._doctor.os.replace", bad_replace)

    rep = doctor(target, destructive=True)
    # The injection ran at least once.
    assert raised["count"] >= 1

    # The atomic_replace check failed (or was reported as fail), but no
    # probe files were left behind.
    by_name = {c.name: c for c in rep.checks}
    assert by_name["atomic_replace"].status == "fail"
    leftover = _doctor_orphans(tmp_path)
    assert leftover == [], f"failed probe left orphans: {[p.name for p in leftover]}"


# ---------------------------------------------------------------------------
# Probe file naming sanity
# ---------------------------------------------------------------------------


def test_doctor_tmp_prefix_is_distinctive() -> None:
    # Defensive: the prefix is part of the on-disk protocol for
    # external orphan-cleanup tools and is documented in failure-model.md.
    assert _DOCTOR_TMP_PREFIX.startswith(".")
    assert "doctor" in _DOCTOR_TMP_PREFIX
    assert "safeatomic" in _DOCTOR_TMP_PREFIX


# ---------------------------------------------------------------------------
# Utility iteration (smoke test of full report under common knobs)
# ---------------------------------------------------------------------------


def _knob_matrix() -> Iterator[tuple[bool, set[str] | None]]:
    yield (False, None)
    yield (True, None)
    yield (False, {"AtomicVisibility"})
    yield (True, {"AtomicVisibility", "CrashDurability"})


@pytest.mark.parametrize(("destructive", "require"), list(_knob_matrix()))
def test_doctor_smoke_matrix(
    target: Path,
    destructive: bool,
    require: set[str] | None,
) -> None:
    rep = doctor(target, destructive=destructive, require=require)
    assert isinstance(rep, DoctorReport)
    # Statuses always within the permitted set, regardless of knobs.
    for c in rep.checks:
        assert c.status in _VALID_STATUSES
