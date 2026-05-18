"""Regression tests for the four pre-v2.0 source bugs fixed together.

Each test pins one of the four contracts that the bug fixes installed,
so a future change cannot silently re-introduce the drift.

The four bugs (all surfaced by Tier 3/4 test suites in batch 5460ad6):

1. AtomicWriter.__exit__ calling commit() after explicit abort()
   -> RuntimeError on clean exit. Fix: track ``_aborted`` flag and skip
   auto-commit when set.

2. move_atomic leaking raw OSError(EXDEV) when the pre-check could not
   resolve src/dst device (e.g. missing dst parent stat failing).
   Fix: wrap the final ``src_path.replace(dst_path)`` and translate
   EXDEV into CrossDeviceAtomicityError.

3. encoding asymmetry: atomic_json_dump / atomic_yaml_dump /
   atomic_yaml_dump_ruamel hardcoded ``encoding="utf-8"`` on the write
   path, ignoring ``safeatomic_config(encoding=...)``. Fix: add explicit
   ``encoding`` kwarg and propagate to write_atomic. (atomic_toml_dump
   intentionally untouched: TOML spec mandates utf-8.)

4. Sidecar-missing inconsistency: ``read_atomic(check_checksum=True)``
   with an absent sidecar raised ChecksumMismatchError(actual="(sidecar
   missing)"), while standalone ``verify_checksum`` raised
   FileNotFoundError for the same condition. Fix: align both surfaces
   on FileNotFoundError; ChecksumMismatchError is now reserved for
   genuine digest mismatches.
"""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from safeatomic import (
    AtomicWriter,
    CrossDeviceAtomicityError,
    atomic_json_dump,
    atomic_yaml_dump,
    atomic_yaml_dump_ruamel,
    move_atomic,
    read_atomic_bytes,
    safeatomic_config,
    write_atomic_bytes,
)

# ---------------------------------------------------------------------------
# Bug 1: AtomicWriter.__exit__ must not auto-commit after explicit abort()
# ---------------------------------------------------------------------------


def test_atomicwriter_explicit_abort_then_clean_exit_does_not_commit(
    tmp_path: Path,
) -> None:
    """Clean exit after abort() leaves the target untouched, no RuntimeError."""
    target = tmp_path / "abort-then-exit.bin"
    with AtomicWriter(target, concurrency="none") as w:
        w.write(b"provisional")
        w.abort()
        # Clean exit follows: __exit__ must observe _aborted=True and skip
        # the auto-commit. Before the fix, __exit__ would call commit() and
        # raise RuntimeError("AtomicWriter.commit() called before __enter__
        # or after abort").
    assert not target.exists(), "abort() must leave target untouched"


def test_atomicwriter_explicit_commit_then_clean_exit_does_not_double_commit(
    tmp_path: Path,
) -> None:
    """Clean exit after explicit commit() does not re-commit (no RuntimeError)."""
    target = tmp_path / "explicit-commit.bin"
    with AtomicWriter(target, concurrency="none") as w:
        w.write(b"payload")
        w.commit()
    # Should still be visible; clean exit must NOT re-commit.
    assert target.read_bytes() == b"payload"


# ---------------------------------------------------------------------------
# Bug 2: move_atomic must translate EXDEV into CrossDeviceAtomicityError
# even on the final os.replace path
# ---------------------------------------------------------------------------


def test_move_atomic_translates_late_exdev_to_cross_device_error(
    tmp_path: Path,
) -> None:
    """Contract: move_atomic NEVER leaks a raw OSError(EXDEV).

    We simulate the rare case where the pre-check passed (or could not
    determine st_dev) but the kernel still rejects the rename with
    EXDEV at the final ``src_path.replace(dst_path)`` step.
    """
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"payload")

    original_replace = Path.replace

    def fake_replace(self: Path, target: Path | str) -> None:
        if self == src:
            err = OSError(errno.EXDEV, "Invalid cross-device link")
            err.errno = errno.EXDEV
            raise err
        original_replace(self, target)

    with (
        patch.object(Path, "replace", fake_replace),
        pytest.raises(CrossDeviceAtomicityError) as excinfo,
    ):
        move_atomic(src, dst)

    assert excinfo.value.src == src
    assert excinfo.value.dst == dst
    # __cause__ must preserve the underlying OSError for diagnostics.
    assert isinstance(excinfo.value.__cause__, OSError)
    assert excinfo.value.__cause__.errno == errno.EXDEV


# ---------------------------------------------------------------------------
# Bug 3: encoding propagates on dump path (JSON / YAML / ruamel)
# ---------------------------------------------------------------------------


def test_atomic_json_dump_propagates_encoding_via_safeatomic_config(
    tmp_path: Path,
) -> None:
    """safeatomic_config(encoding='utf-16') reaches atomic_json_dump."""
    target = tmp_path / "utf16.json"
    with safeatomic_config(encoding="utf-16"):
        atomic_json_dump(target, {"k": "v"}, concurrency="none")
    raw = target.read_bytes()
    assert raw.startswith((b"\xff\xfe", b"\xfe\xff")), "utf-16 BOM expected"


def test_atomic_yaml_dump_propagates_encoding_via_explicit_kwarg(
    tmp_path: Path,
) -> None:
    """Explicit ``encoding`` kwarg on atomic_yaml_dump produces utf-16 bytes."""
    target = tmp_path / "utf16.yaml"
    atomic_yaml_dump(target, {"k": "v"}, encoding="utf-16", concurrency="none")
    raw = target.read_bytes()
    assert raw.startswith((b"\xff\xfe", b"\xfe\xff")), "utf-16 BOM expected"


def test_atomic_yaml_dump_ruamel_propagates_encoding_via_explicit_kwarg(
    tmp_path: Path,
) -> None:
    """Explicit ``encoding`` kwarg on atomic_yaml_dump_ruamel reaches write_atomic."""
    ruamel = pytest.importorskip("ruamel.yaml")
    del ruamel  # only needed for skip
    target = tmp_path / "utf16-ruamel.yaml"
    atomic_yaml_dump_ruamel(target, {"k": "v"}, encoding="utf-16", concurrency="none")
    raw = target.read_bytes()
    assert raw.startswith((b"\xff\xfe", b"\xfe\xff")), "utf-16 BOM expected"


def test_explicit_encoding_overrides_safeatomic_config_on_dump(
    tmp_path: Path,
) -> None:
    """Principle 14: explicit kwarg trumps safeatomic_config on the dump path."""
    target = tmp_path / "explicit-wins.json"
    with safeatomic_config(encoding="utf-16"):
        atomic_json_dump(target, {"k": "v"}, encoding="utf-8", concurrency="none")
    raw = target.read_bytes()
    # utf-8 has no BOM for this content; first bytes must be ASCII '{'.
    assert raw[:1] == b"{", "explicit encoding='utf-8' must override config"


# ---------------------------------------------------------------------------
# Bug 4: missing sidecar is FileNotFoundError, aligned with verify_checksum
# ---------------------------------------------------------------------------


def test_read_atomic_check_checksum_missing_sidecar_raises_filenotfounderror(
    tmp_path: Path,
) -> None:
    """``read_atomic(check_checksum=True)`` without sidecar raises FileNotFoundError.

    Previously this raised ``ChecksumMismatchError(actual="(sidecar
    missing)")``, while standalone ``verify_checksum`` raised
    ``FileNotFoundError`` for the same condition. The two surfaces are
    now aligned on ``FileNotFoundError``; ``ChecksumMismatchError`` is
    reserved for genuine digest mismatches.
    """
    target = tmp_path / "no-sidecar.bin"
    write_atomic_bytes(target, b"payload", concurrency="none")
    with pytest.raises(FileNotFoundError, match="checksum sidecar not found"):
        read_atomic_bytes(target, check_checksum=True)
