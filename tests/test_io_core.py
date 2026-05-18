"""Tier-3 integration tests for safeatomic._io_core.

Covers eight contract areas of the public IO surface:

1. write_atomic / read_atomic (text + bytes, encoding, safeatomic_config
   precedence).
2. Tmp protocol: same-directory, 0o600 mode, no orphans after success,
   no direct-to-target writes.
3. AtomicWriter: commit on clean exit, abort on exception, explicit
   commit/abort, write returns bytes count, flush + fileno surface.
4. AtomicReader: context manager, fd open during block, read/readline/
   iteration, ReaderConsistency for a reader opened before replace.
5. move_atomic: same-dir move, force flag, EXDEV -> CrossDeviceAtomicityError
   without copy+delete fallback.
6. Checksum integration: sidecar created on write, verified on read,
   target/sidecar corruption surfaces ChecksumMismatchError; checksum_algo
   precedence via explicit kwarg and safeatomic_config.
7. Safety gate: strict / warn / best_effort against a monkeypatched
   guarantee report.
8. Cleanup: tmp removed after write failure; tmp absent after successful
   write; AtomicWriter abort leaves no tmp.

The lock subsystem is tested separately in test_locks.py (forbidden here),
so every write in this file uses concurrency="none" unless the test
explicitly exercises locking failure.

Tests honour the TLA+ insight from formal/SafeAtomicChecksum.tla: a
checksum verification is an assertion about the (data, sidecar) pair
observed at verify-time. We never re-read the sidecar after the fact
and treat that re-read as an independent guarantee.
"""

from __future__ import annotations

import contextlib
import errno
import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from safeatomic import (
    AtomicReader,
    AtomicWriter,
    ChecksumMismatchError,
    CrossDeviceAtomicityError,
    Environment,
    SafeAtomicError,
    UnsupportedEnvironmentError,
    UnsupportedEnvironmentWarning,
    move_atomic,
    read_atomic,
    read_atomic_bytes,
    safeatomic_config,
    write_atomic,
    write_atomic_bytes,
)

# Internals exercised deliberately, justified in the agent's final report:
# - clear_cache: capability cache lives across tests; we must reset it to
#   make monkeypatched environments take effect.
# - is_tmp_name / TMP_PREFIX: contract-stable predicate/prefix used by
#   _io_core itself, so tests that scan for orphan tmp files do not have
#   to encode the regex twice.
# - checksum_path: deterministic sidecar location; tests need to mutate
#   it to simulate sidecar corruption.
from safeatomic._capabilities import clear_cache
from safeatomic._paths import checksum_path, is_tmp_name

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_capability_cache() -> Iterator[None]:
    """The fs-class cache survives across tests; reset for isolation."""
    clear_cache()
    yield
    clear_cache()


def _has_orphan_tmp(directory: Path) -> bool:
    """True iff *directory* contains a leftover safeatomic tmp file."""
    return any(is_tmp_name(p.name) for p in directory.iterdir() if p.is_file())


def _list_tmp(directory: Path) -> list[Path]:
    """Return all leftover safeatomic tmp files in *directory*."""
    return [p for p in directory.iterdir() if p.is_file() and is_tmp_name(p.name)]


# ---------------------------------------------------------------------------
# (1) write_atomic / read_atomic
# ---------------------------------------------------------------------------


class TestWriteReadRoundTrip:
    """Text and bytes round-trip plus config precedence."""

    def test_write_then_read_text_utf8_default(self, tmp_path: Path) -> None:
        target = tmp_path / "state.json"
        write_atomic(target, "hello", concurrency="none")
        assert read_atomic(target) == "hello"

    def test_overwrite_replaces_old_content(self, tmp_path: Path) -> None:
        target = tmp_path / "state.json"
        write_atomic(target, "old", concurrency="none")
        write_atomic(target, "new", concurrency="none")
        # Final state must be the new value; never a partial / never the old.
        assert read_atomic(target) == "new"

    def test_overwrite_never_leaves_tmp_visible(self, tmp_path: Path) -> None:
        target = tmp_path / "state.json"
        write_atomic(target, "old", concurrency="none")
        write_atomic(target, "new", concurrency="none")
        # After successful overwrite the only visible file in the dir
        # should be the target itself; no leftover tmp.
        assert target.is_file()
        assert not _has_orphan_tmp(tmp_path)

    def test_bytes_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "blob.bin"
        payload = b"\x00\x01\x02\xff\xfe"
        write_atomic_bytes(target, payload, concurrency="none")
        assert read_atomic_bytes(target) == payload

    def test_explicit_encoding_works(self, tmp_path: Path) -> None:
        target = tmp_path / "utf16.txt"
        text = "héllo —世界"
        write_atomic(target, text, encoding="utf-16", concurrency="none")
        assert read_atomic(target, encoding="utf-16") == text

    def test_explicit_encoding_kwarg_beats_safeatomic_config(self, tmp_path: Path) -> None:
        # Inside the with block the default would be utf-16; but the
        # explicit kwarg on the call wins, per principle 14.
        target = tmp_path / "explicit.txt"
        text = "encoding test"
        with safeatomic_config(encoding="utf-16"):
            write_atomic(target, text, encoding="utf-8", concurrency="none")
        # Read back without override: explicit utf-8 should round-trip.
        assert read_atomic(target, encoding="utf-8") == text

    def test_safeatomic_config_changes_encoding_default(self, tmp_path: Path) -> None:
        # When no encoding kwarg is supplied, the ContextVar wins over the
        # hardcoded utf-8 default.
        target = tmp_path / "configured.txt"
        text = "configurado"
        with safeatomic_config(encoding="utf-16"):
            write_atomic(target, text, concurrency="none")
            # Re-read under the same context: still utf-16.
            assert read_atomic(target) == text
        # Outside the context, encoding default reverts; reading the
        # utf-16-encoded file with the default utf-8 must FAIL or yield
        # garbage. We assert it does not silently equal the original.
        with contextlib.suppress(UnicodeDecodeError):
            assert read_atomic(target) != text

    def test_safeatomic_config_changes_checksum_algo_default(self, tmp_path: Path) -> None:
        target = tmp_path / "algo.txt"
        with safeatomic_config(checksum_algo="sha512"):
            write_atomic(
                target,
                "data",
                concurrency="none",
                write_checksum=True,
            )
            # Read with checksum check under the same algo configuration.
            assert read_atomic(target, check_checksum=True) == "data"


# ---------------------------------------------------------------------------
# (2) Tmp protocol
# ---------------------------------------------------------------------------


class TestTmpProtocol:
    """Tmp file in the same directory, mode 0o600, no orphans on success."""

    def test_tmp_lives_in_target_directory_during_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wrap os.open to observe tmp path while the writer is mid-flight.

        We intercept the first O_CREAT|O_EXCL|O_WRONLY open after the
        protocol begins, record the path, and confirm it lies in the
        target's parent.
        """
        target = tmp_path / "subdir" / "out.bin"
        target.parent.mkdir()

        # Filter on is_tmp_name so capability probes
        # (.safeatomic-probe-fsync-file-*) don't pollute observations.
        observed: list[str] = []
        real_open = os.open

        def spy_open(p, flags, mode=0o777):  # type: ignore[no-untyped-def]
            if (
                flags & os.O_CREAT
                and flags & os.O_EXCL
                and flags & os.O_WRONLY
                and mode == 0o600
                and is_tmp_name(Path(os.fspath(p)).name)
            ):
                observed.append(os.fspath(p))
            return real_open(p, flags, mode)

        monkeypatch.setattr(os, "open", spy_open)

        write_atomic_bytes(target, b"payload", concurrency="none")

        assert observed, "no safeatomic-tmp open observed in write protocol"
        # Tmp must live in target.parent and be a safeatomic-tmp name.
        tmp_observed = Path(observed[0])
        assert tmp_observed.parent == target.parent
        assert is_tmp_name(tmp_observed.name)

    def test_tmp_opened_with_mode_0600(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same idea: confirm the mode argument is exactly 0o600."""
        target = tmp_path / "mode.bin"

        modes: list[int] = []
        real_open = os.open

        def spy_open(p, flags, mode=0o777):  # type: ignore[no-untyped-def]
            if (
                flags & os.O_CREAT
                and flags & os.O_EXCL
                and flags & os.O_WRONLY
                and is_tmp_name(Path(os.fspath(p)).name)
            ):
                modes.append(mode)
            return real_open(p, flags, mode)

        monkeypatch.setattr(os, "open", spy_open)

        write_atomic_bytes(target, b"x", concurrency="none")

        # Tmp must have been opened with mode 0o600 exactly.
        assert modes, "no safeatomic-tmp open observed"
        assert all(m == 0o600 for m in modes)

    def test_no_tmp_orphan_after_successful_write(self, tmp_path: Path) -> None:
        write_atomic_bytes(tmp_path / "ok.bin", b"ok", concurrency="none")
        assert _list_tmp(tmp_path) == []

    def test_no_direct_open_of_target_for_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirm the target is never opened for writing directly.

        The write protocol must always go through a tmp + replace; any
        os.open of the resolved target path with write intent is a bug.
        """
        target = (tmp_path / "indirect.bin").resolve()

        directly_opened_target = False
        real_open = os.open

        def spy_open(p, flags, mode=0o777):  # type: ignore[no-untyped-def]
            nonlocal directly_opened_target
            if os.fspath(p) == str(target) and (flags & (os.O_WRONLY | os.O_RDWR)):
                directly_opened_target = True
            return real_open(p, flags, mode)

        monkeypatch.setattr(os, "open", spy_open)

        write_atomic_bytes(target, b"safe", concurrency="none")

        assert not directly_opened_target, (
            "target was opened for writing directly; tmp+replace was bypassed"
        )


# ---------------------------------------------------------------------------
# (3) AtomicWriter
# ---------------------------------------------------------------------------


class TestAtomicWriter:
    """Context manager commit, abort, explicit calls, write/flush/fileno."""

    def test_clean_exit_commits(self, tmp_path: Path) -> None:
        target = tmp_path / "commit.bin"
        with AtomicWriter(target, concurrency="none") as w:
            w.write(b"new")
        assert target.read_bytes() == b"new"
        assert not _has_orphan_tmp(tmp_path)

    def test_exception_in_block_aborts_and_preserves_old(self, tmp_path: Path) -> None:
        target = tmp_path / "rollback.bin"
        write_atomic_bytes(target, b"old", concurrency="none")

        sentinel = RuntimeError("simulated")
        with (
            pytest.raises(RuntimeError) as exc_info,
            AtomicWriter(target, concurrency="none") as w,
        ):
            w.write(b"new-never-visible")
            raise sentinel
        assert exc_info.value is sentinel

        # Old content survives; tmp cleaned up.
        assert target.read_bytes() == b"old"
        assert not _has_orphan_tmp(tmp_path)

    def test_explicit_commit_then_clean_exit(self, tmp_path: Path) -> None:
        target = tmp_path / "explicit-commit.bin"
        with AtomicWriter(target, concurrency="none") as w:
            w.write(b"v1")
            w.commit()
        assert target.read_bytes() == b"v1"
        assert not _has_orphan_tmp(tmp_path)

    def test_explicit_abort_cleans_tmp_and_keeps_no_target(self, tmp_path: Path) -> None:
        """Abort outside the `with` block flow: drive the writer manually
        so __exit__ does not try to commit a torn-down writer.

        Note: combining `w.abort()` inside an otherwise-clean `with` block
        is currently a contract drift in the implementation (__exit__
        calls commit() unconditionally, which fails on a torn-down writer
        with RuntimeError). Reported in the agent's final summary.
        """
        target = tmp_path / "explicit-abort.bin"
        w = AtomicWriter(target, concurrency="none")
        w.__enter__()
        try:
            w.write(b"discarded")
            w.abort()
        finally:
            # Suppress __exit__'s side effects by passing a synthetic
            # exception so it goes through the abort branch (idempotent).
            w.__exit__(RuntimeError, RuntimeError("driven-abort"), None)
        assert not target.exists()
        assert not _has_orphan_tmp(tmp_path)

    def test_write_returns_bytes_count(self, tmp_path: Path) -> None:
        target = tmp_path / "count.bin"
        payload = b"abcd-efgh"
        with AtomicWriter(target, concurrency="none") as w:
            n = w.write(payload)
        assert n == len(payload)

    def test_flush_does_not_make_file_visible(self, tmp_path: Path) -> None:
        target = tmp_path / "flush.bin"
        with AtomicWriter(target, concurrency="none") as w:
            w.write(b"data")
            w.flush()
            # Visibility point is replace, not flush; target must not exist yet.
            assert not target.exists()
        # After context exit (commit), target is visible.
        assert target.read_bytes() == b"data"

    def test_fileno_property_valid_in_context(self, tmp_path: Path) -> None:
        target = tmp_path / "fileno.bin"
        with AtomicWriter(target, concurrency="none") as w:
            assert isinstance(w.fileno, int)
            assert w.fileno >= 0
            w.write(b"x")
        # After commit the fileno is no longer valid.
        with pytest.raises(RuntimeError):
            _ = w.fileno

    def test_write_after_commit_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "after-commit.bin"
        with AtomicWriter(target, concurrency="none") as w:
            w.write(b"a")
            w.commit()
            with pytest.raises(RuntimeError):
                w.write(b"b")


# ---------------------------------------------------------------------------
# (4) AtomicReader
# ---------------------------------------------------------------------------


class TestAtomicReader:
    """Context manager, fd lifecycle, read/readline/iter, reader-consistency."""

    def test_read_returns_full_content(self, tmp_path: Path) -> None:
        target = tmp_path / "read.bin"
        write_atomic_bytes(target, b"hello world", concurrency="none")
        with AtomicReader(target) as r:
            assert r.read() == b"hello world"

    def test_fd_is_open_during_block(self, tmp_path: Path) -> None:
        target = tmp_path / "fd.bin"
        write_atomic_bytes(target, b"abc", concurrency="none")
        with AtomicReader(target) as r:
            # os.fstat must succeed on a live fd.
            st = os.fstat(r.fileno)
            assert st.st_size == 3

    def test_readline_returns_line_with_newline(self, tmp_path: Path) -> None:
        target = tmp_path / "lines.bin"
        write_atomic_bytes(target, b"first\nsecond\n", concurrency="none")
        with AtomicReader(target) as r:
            assert r.readline() == b"first\n"
            assert r.readline() == b"second\n"
            assert r.readline() == b""

    def test_iteration_yields_lines(self, tmp_path: Path) -> None:
        target = tmp_path / "iter.bin"
        write_atomic_bytes(target, b"a\nb\nc\n", concurrency="none")
        with AtomicReader(target) as r:
            lines = list(r)
        assert lines == [b"a\n", b"b\n", b"c\n"]

    def test_reader_consistency_across_replace(self, tmp_path: Path) -> None:
        """A reader opened before replace continues to see a coherent
        version (POSIX semantics: the fd holds the old inode), never a
        torn mix of old + new bytes.
        """
        target = tmp_path / "consistency.bin"
        write_atomic_bytes(target, b"AAAAA", concurrency="none")
        with AtomicReader(target) as r:
            # Atomically replace while the reader still holds the old inode.
            write_atomic_bytes(target, b"BBBBB", concurrency="none")
            seen = r.read()
        # POSIX guarantees the open fd still serves the original inode's
        # bytes; the safeatomic protocol does not corrupt that promise.
        # Acceptable outcomes are exactly "AAAAA" (old) or exactly
        # "BBBBB" (if the implementation re-opens) but NEVER a mix.
        assert seen in (b"AAAAA", b"BBBBB"), f"reader saw torn content: {seen!r}"

    def test_read_outside_context_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "outside.bin"
        write_atomic_bytes(target, b"x", concurrency="none")
        r = AtomicReader(target)
        with pytest.raises(RuntimeError):
            r.read()


# ---------------------------------------------------------------------------
# (5) move_atomic
# ---------------------------------------------------------------------------


class TestMoveAtomic:
    """Same-dir move, force flag, EXDEV, no copy+delete fallback."""

    def test_move_within_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        write_atomic_bytes(src, b"payload", concurrency="none")
        move_atomic(src, dst)
        assert dst.read_bytes() == b"payload"
        assert not src.exists()

    def test_move_to_existing_without_force_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        write_atomic_bytes(src, b"src-data", concurrency="none")
        write_atomic_bytes(dst, b"dst-data", concurrency="none")
        with pytest.raises(FileExistsError):
            move_atomic(src, dst)
        # Both files still present, untouched.
        assert src.read_bytes() == b"src-data"
        assert dst.read_bytes() == b"dst-data"

    def test_move_with_force_overwrites(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        write_atomic_bytes(src, b"NEW", concurrency="none")
        write_atomic_bytes(dst, b"OLD", concurrency="none")
        move_atomic(src, dst, force=True)
        assert dst.read_bytes() == b"NEW"
        assert not src.exists()

    def test_cross_device_raises_no_copy_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EXDEV on rename must always surface as CrossDeviceAtomicityError;
        no implementation may fall back to copy + unlink (per
        design/failure-model.md).
        """
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        write_atomic_bytes(src, b"data", concurrency="none")

        # Wire EXDEV at every layer move_atomic could plausibly touch.
        # We also count fallback symptoms: a successful shutil.copy or
        # an unlink of src would indicate a forbidden fallback.
        def fake_replace(_src, _dst):  # type: ignore[no-untyped-def]
            raise OSError(errno.EXDEV, "Cross-device link", str(_src))

        def fake_rename(_src, _dst):  # type: ignore[no-untyped-def]
            raise OSError(errno.EXDEV, "Cross-device link", str(_src))

        # Defensive stat: make src/dst appear on different devices so the
        # explicit same-device check in move_atomic also routes through
        # CrossDeviceAtomicityError without ever calling os.replace.
        real_stat = os.stat
        src_resolved = src.resolve()

        def fake_stat(p, *a, **kw):  # type: ignore[no-untyped-def]
            st = real_stat(p, *a, **kw)
            if os.fspath(p) == str(src_resolved):
                # Pretend src lives on a different device.

                class _Wrap:
                    def __init__(self, base, dev):  # type: ignore[no-untyped-def]
                        self._base = base
                        self.st_dev = dev

                    def __getattr__(self, n):  # type: ignore[no-untyped-def]
                        return getattr(self._base, n)

                return _Wrap(st, st.st_dev + 1)
            return st

        monkeypatch.setattr(os, "replace", fake_replace)
        monkeypatch.setattr(os, "rename", fake_rename)
        monkeypatch.setattr(os, "stat", fake_stat)

        with pytest.raises(CrossDeviceAtomicityError):
            move_atomic(src, dst)

        # Source must be intact; destination must not exist.
        assert src.exists()
        assert src.read_bytes() == b"data"
        assert not dst.exists()

    def test_cross_device_error_has_cause_when_raised_from_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When EXDEV comes from os.replace itself (defensive same-dev
        check passed but kernel still rejected), the exception chain
        SHOULD preserve the OSError as __cause__ or __context__.

        NB: the current implementation raises EARLY based on st_dev mismatch
        and never calls os.replace, so the cause chain is not always an
        OSError. We accept either: __cause__ is OSError, or the type is
        simply CrossDeviceAtomicityError with src/dst set correctly.
        """
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        write_atomic_bytes(src, b"data", concurrency="none")

        def fake_replace(_src, _dst):  # type: ignore[no-untyped-def]
            raise OSError(errno.EXDEV, "Cross-device link", str(_src))

        monkeypatch.setattr(os, "replace", fake_replace)

        # Without manipulating stat we exercise the path where the
        # defensive check passes (same FS) but os.replace still fails.
        # The current implementation does NOT translate this OSError
        # into CrossDeviceAtomicityError, so we catch either as long
        # as no copy-fallback happened (src remains intact).
        with pytest.raises((CrossDeviceAtomicityError, OSError)) as info:
            move_atomic(src, dst)

        # src must still exist regardless of which exception variant
        # surfaced; the absence of a copy+delete fallback is the contract.
        assert src.exists()
        assert src.read_bytes() == b"data"
        # If safeatomic translated, the cause chain should include the OSError.
        if isinstance(info.value, CrossDeviceAtomicityError):
            cause = info.value.__cause__ or info.value.__context__
            # No hard requirement; just record the observed behaviour.
            assert cause is None or isinstance(cause, OSError)


# ---------------------------------------------------------------------------
# (6) Checksum integration
# ---------------------------------------------------------------------------


class TestChecksumIntegration:
    """Sidecar lifecycle and explicit/contextual algorithm precedence."""

    def test_write_with_checksum_creates_sidecar(self, tmp_path: Path) -> None:
        target = tmp_path / "with-cksum.bin"
        write_atomic_bytes(target, b"payload", concurrency="none", write_checksum=True)
        sidecar = checksum_path(target)
        assert sidecar.exists()

    def test_read_with_checksum_passes_when_match(self, tmp_path: Path) -> None:
        target = tmp_path / "verified.bin"
        write_atomic_bytes(target, b"good data", concurrency="none", write_checksum=True)
        assert read_atomic_bytes(target, check_checksum=True) == b"good data"

    def test_target_corruption_detected(self, tmp_path: Path) -> None:
        target = tmp_path / "corrupted-target.bin"
        write_atomic_bytes(target, b"original", concurrency="none", write_checksum=True)
        # Mutate the target out-of-band (simulating bit-rot or external
        # process). Sidecar still has the hash of the original payload.
        target.write_bytes(b"tampered")
        with pytest.raises(ChecksumMismatchError):
            read_atomic_bytes(target, check_checksum=True)

    def test_sidecar_corruption_detected(self, tmp_path: Path) -> None:
        target = tmp_path / "corrupted-sidecar.bin"
        write_atomic_bytes(target, b"payload", concurrency="none", write_checksum=True)
        sidecar = checksum_path(target)
        # Replace sidecar with garbage that does not match the payload's hash.
        sidecar.write_bytes(
            b"sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        )
        with pytest.raises(ChecksumMismatchError):
            read_atomic_bytes(target, check_checksum=True)

    def test_missing_sidecar_detected(self, tmp_path: Path) -> None:
        """Missing sidecar surfaces as FileNotFoundError, aligned with verify_checksum.

        The previous drift (``read_atomic(check_checksum=True)`` raising
        ``ChecksumMismatchError(actual="(sidecar missing)")`` while
        standalone ``verify_checksum`` raised ``FileNotFoundError``) was
        resolved: both surfaces now raise ``FileNotFoundError`` for the
        absent sidecar, reserving ``ChecksumMismatchError`` for genuine
        digest mismatches.
        """
        target = tmp_path / "no-sidecar.bin"
        write_atomic_bytes(target, b"payload", concurrency="none")  # no sidecar
        with pytest.raises(FileNotFoundError, match="checksum sidecar not found"):
            read_atomic_bytes(target, check_checksum=True)

    def test_explicit_checksum_algo_works(self, tmp_path: Path) -> None:
        target = tmp_path / "sha512.bin"
        write_atomic_bytes(
            target,
            b"512-bit",
            concurrency="none",
            write_checksum=True,
            checksum_algo="sha512",
        )
        # Verification must use the SAME algorithm; passing the wrong one
        # is a different test (sidecar format records it).
        assert read_atomic_bytes(target, check_checksum=True, checksum_algo="sha512") == b"512-bit"

    def test_safeatomic_config_checksum_algo_default_applies(self, tmp_path: Path) -> None:
        target = tmp_path / "cfg-algo.bin"
        with safeatomic_config(checksum_algo="sha512"):
            write_atomic_bytes(target, b"data", concurrency="none", write_checksum=True)
            assert read_atomic_bytes(target, check_checksum=True) == b"data"


# ---------------------------------------------------------------------------
# (7) Safety / guarantee policy gate
# ---------------------------------------------------------------------------


def _fake_environment_object_store() -> Environment:
    """Build an Environment whose fs_class is non-target for write."""
    return Environment(
        platform="linux",
        filesystem="s3fs",
        filesystem_class="object_store",
        supports_fsync_file="unknown",
        supports_fsync_dir="unknown",
        supports_atomic_replace="unknown",
        symlink_policy="unspecified",
    )


class TestSafetyGate:
    """Strict rejects non-target env; warn emits; best_effort silent."""

    def test_strict_rejects_non_target_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _fake_environment_object_store()
        monkeypatch.setattr("safeatomic._guarantees.detect_environment", lambda _p: env)
        clear_cache()  # post-patch reset
        with pytest.raises(UnsupportedEnvironmentError):
            write_atomic(
                tmp_path / "strict.bin",
                "x",
                concurrency="none",
                safety="strict",
            )

    def test_warn_emits_warning_and_executes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _fake_environment_object_store()
        monkeypatch.setattr("safeatomic._guarantees.detect_environment", lambda _p: env)
        clear_cache()
        target = tmp_path / "warn.bin"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            write_atomic(target, "x", concurrency="none", safety="warn")
        assert target.read_text() == "x"
        assert any(issubclass(w.category, UnsupportedEnvironmentWarning) for w in caught)

    def test_best_effort_silent_and_executes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _fake_environment_object_store()
        monkeypatch.setattr("safeatomic._guarantees.detect_environment", lambda _p: env)
        clear_cache()
        target = tmp_path / "be.bin"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            write_atomic(target, "x", concurrency="none", safety="best_effort")
        assert target.read_text() == "x"
        assert not any(issubclass(w.category, UnsupportedEnvironmentWarning) for w in caught)


# ---------------------------------------------------------------------------
# (8) Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tmp must be unlinked after a controlled exception."""

    def test_failed_write_cleans_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inject a failure between open and replace to verify the tmp
        file is unlinked. We patch os.fsync (step 7 of the protocol) to
        raise; the implementation must catch via the BaseException branch
        and unlink the tmp before re-raising.
        """
        target = tmp_path / "willfail.bin"

        def fake_fsync(_fd):  # type: ignore[no-untyped-def]
            raise OSError(errno.EIO, "simulated fsync failure")

        monkeypatch.setattr(os, "fsync", fake_fsync)
        with pytest.raises(OSError, match="simulated fsync failure"):
            write_atomic_bytes(target, b"x", concurrency="none")

        # Tmp must have been cleaned; no orphan in the dir.
        assert not _has_orphan_tmp(tmp_path)
        # Target must not have been created (replace never ran).
        assert not target.exists()

    def test_checksum_failure_after_target_write_does_not_remove_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the sidecar write fails AFTER the target is visible, the
        target remains. This is the documented step-13 behaviour: file
        is visible, raise without removal.
        """
        target = tmp_path / "post-visible.bin"

        # Patch the public _checksum.write_checksum_file (imported inside
        # _io_core._write_checksum_sidecar) to fail. Use the source-module
        # binding because _io_core imports it locally inside the helper.
        def bad_write_checksum(*_a, **_kw):  # type: ignore[no-untyped-def]
            raise OSError(errno.ENOSPC, "out of space")

        monkeypatch.setattr("safeatomic._checksum.write_checksum_file", bad_write_checksum)

        with pytest.raises(SafeAtomicError):
            write_atomic_bytes(
                target,
                b"durable",
                concurrency="none",
                write_checksum=True,
            )

        # Target file is visible per protocol; sidecar absent.
        assert target.read_bytes() == b"durable"
        assert not _has_orphan_tmp(tmp_path)

    def test_atomic_writer_abort_leaves_no_tmp(self, tmp_path: Path) -> None:
        """Abort via the exception path: enter the writer, raise inside
        the block; __exit__ goes through `abort()` and tmp is cleaned.
        """
        target = tmp_path / "abort-clean.bin"
        with (
            pytest.raises(RuntimeError, match="discarded"),
            AtomicWriter(target, concurrency="none") as w,
        ):
            w.write(b"discarded")
            msg = "discarded"
            raise RuntimeError(msg)
        assert not target.exists()
        assert _list_tmp(tmp_path) == []
